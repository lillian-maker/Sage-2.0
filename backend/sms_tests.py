"""SMS provider contract fixtures; never use real credentials or send externally."""
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from .sms import phone_ready, send_login_code
import json
from django.test import TestCase
from .models import LoginChallenge, LoginIdentity


@override_settings(AUTH_PHONE_ENABLED=True, SMS_TEMPLATE_APPROVED=True,
    SMS_SECRET_ID='fixture-id', SMS_SECRET_KEY='fixture-key', SMS_REGION='ap-guangzhou',
    SMS_SDK_APP_ID='1400000000', SMS_SIGN='Fixture', SMS_TEMPLATE_ID='123',
    SMS_ALLOWED_COUNTRY_CODES=('86',), AUTH_CODE_TTL_SECONDS=300)
class SmsTransportTests(SimpleTestCase):
    @override_settings(SMS_BINDING_TEMPLATE_ID='129',SMS_BINDING_TEMPLATE_APPROVED=True)
    def test_phone_binding_uses_only_its_dedicated_template(self):
        from .sms import send_code
        with patch('tencentcloud.sms.v20210111.sms_client.SmsClient') as client:
            client.return_value.SendSms.return_value=Mock(SendStatusSet=[
                Mock(Code='Ok',SerialNo='fixture-binding',PhoneNumber='+8613800000000')])
            self.assertEqual(send_code('+8613800000000','123456',purpose='phone_binding'),1)
            self.assertEqual(client.return_value.SendSms.call_args.args[0].TemplateId,'129')
            self.assertEqual(client.return_value.SendSms.call_args.args[0].TemplateParamSet,['123456','5'])
            with override_settings(SMS_BINDING_TEMPLATE_APPROVED=False):
                with self.assertRaisesMessage(OSError,'SMS_CHANNEL_UNAVAILABLE'):
                    send_code('+8613800000000','123456',purpose='phone_binding')
            self.assertEqual(client.return_value.SendSms.call_count,1)

    def test_disabled_or_missing_approval_or_config_is_not_ready(self):
        self.assertTrue(phone_ready())
        for settings in ({'AUTH_PHONE_ENABLED': False}, {'SMS_TEMPLATE_APPROVED': False},
                         {'SMS_SECRET_KEY': ''}, {'SMS_ALLOWED_COUNTRY_CODES': ()},
                         {'SMS_ALLOWED_COUNTRY_CODES': ('+86',)}, {'AUTH_CODE_TTL_SECONDS': 301}):
            with self.subTest(setting=tuple(settings)), override_settings(**settings):
                self.assertFalse(phone_ready())
                with self.assertRaisesMessage(OSError, 'SMS_CHANNEL_UNAVAILABLE'):
                    send_login_code('+8613800000000', '123456')

    def test_international_numbers_require_explicit_country_approval(self):
        self.assertTrue(phone_ready('+8613800000000'))
        self.assertFalse(phone_ready('+12025550100'))
        self.assertFalse(phone_ready('13800000000'))
        with override_settings(SMS_ALLOWED_COUNTRY_CODES=('1',)):
            self.assertTrue(phone_ready('+12025550100'))
            self.assertFalse(phone_ready('+8613800000000'))

    def test_otp_and_ttl_template_single_call_without_retry(self):
        with patch('tencentcloud.sms.v20210111.sms_client.SmsClient') as client:
            client.return_value.SendSms.return_value = Mock(SendStatusSet=[
                Mock(Code='Ok', SerialNo='fixture-serial', PhoneNumber='+8613800000000')])
            self.assertEqual(send_login_code('+8613800000000', '123456'), 1)
            profile = client.call_args.args[2]
            self.assertEqual(profile.httpProfile.reqTimeout, 10)
            self.assertEqual(profile.httpProfile.endpoint, 'sms.tencentcloudapi.com')
            self.assertIsNone(profile.retryer)
            self.assertTrue(profile.disable_region_breaker)
            request = client.return_value.SendSms.call_args.args[0]
            self.assertEqual(request.PhoneNumberSet, ['+8613800000000'])
            self.assertEqual(request.TemplateParamSet, ['123456', '5'])
            self.assertEqual(request.TemplateId, '123')
            self.assertEqual(request.SmsSdkAppId, '1400000000')
            self.assertEqual(client.return_value.SendSms.call_count, 1)

    def test_rejection_and_unknown_or_mismatched_receipts_are_not_accepted(self):
        from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
        with patch('tencentcloud.sms.v20210111.sms_client.SmsClient') as client:
            client.return_value.SendSms.side_effect = TencentCloudSDKException(
                'RequestLimitExceeded', 'private fixture provider body')
            with self.assertRaisesMessage(OSError, 'SMS_DELIVERY_FAILED'):
                send_login_code('+8613800000000', '123456')
            self.assertEqual(client.return_value.SendSms.call_count, 1)
            client.return_value.SendSms.side_effect = None
            for statuses in ([], [Mock(Code='Unknown', SerialNo='', PhoneNumber='+8613800000000')],
                             [Mock(Code='Ok', SerialNo='', PhoneNumber='+8613800000000')],
                             [Mock(Code='Ok', SerialNo='fixture', PhoneNumber='+12025550100')]):
                client.return_value.SendSms.return_value = Mock(SendStatusSet=statuses)
                self.assertEqual(send_login_code('+8613800000000', '123456'), 0)

    def test_invalid_otp_cannot_reach_sdk(self):
        with patch('tencentcloud.sms.v20210111.sms_client.SmsClient') as client:
            with self.assertRaisesMessage(ValueError, 'INVALID_CODE'):
                send_login_code('+8613800000000', '12345x')
            client.assert_not_called()

    def test_region_appid_and_template_format_require_real_config(self):
        for changed in ({'SMS_REGION':'invalid'},{'SMS_SDK_APP_ID':'abc'},{'SMS_TEMPLATE_ID':'0'}):
            with self.subTest(changed=changed),override_settings(**changed):self.assertFalse(phone_ready())

    @override_settings(SMS_REGISTRATION_TEMPLATE_ID='124',SMS_REGISTRATION_TEMPLATE_APPROVED=True,
        SMS_PASSWORD_TEMPLATE_ID='125',SMS_PASSWORD_TEMPLATE_APPROVED=True,
        SMS_PASSWORD_CHANGED_TEMPLATE_ID='126',SMS_PASSWORD_CHANGED_TEMPLATE_APPROVED=True)
    def test_api_access_regions_do_not_authorize_sms_product_region(self):
        from .sms import send_code
        for region in ('ap-shanghai','ap-hongkong','ap-singapore','ap-chengdu','ap-chongqing',
                'ap-jakarta','ap-bangkok','ap-seoul','ap-tokyo','na-ashburn','na-siliconvalley'):
            for purpose in ('login','registration','password_reset','password_changed'):
                with self.subTest(region=region,purpose=purpose),override_settings(SMS_REGION=region),patch('tencentcloud.sms.v20210111.sms_client.SmsClient') as client:
                    self.assertFalse(phone_ready('+8613800000000',purpose))
                    with self.assertRaisesMessage(OSError,'SMS_CHANNEL_UNAVAILABLE'):
                        send_code('+8613800000000',None if purpose=='password_changed' else '123456',purpose=purpose)
                    client.assert_not_called()

    @override_settings(SMS_REGISTRATION_TEMPLATE_ID='124',SMS_REGISTRATION_TEMPLATE_APPROVED=True,
        SMS_PASSWORD_TEMPLATE_ID='125',SMS_PASSWORD_TEMPLATE_APPROVED=True,
        SMS_PASSWORD_CHANGED_TEMPLATE_ID='126',SMS_PASSWORD_CHANGED_TEMPLATE_APPROVED=True)
    def test_all_three_product_regions_accept_each_approved_purpose(self):
        from .sms import send_code
        with patch('tencentcloud.sms.v20210111.sms_client.SmsClient') as client:
            client.return_value.SendSms.return_value=Mock(SendStatusSet=[Mock(Code='Ok',SerialNo='fixture',PhoneNumber='+8613800000000')])
            for region in ('ap-beijing','ap-guangzhou','ap-nanjing'):
                for purpose in ('login','registration','password_reset','password_changed'):
                    with self.subTest(region=region,purpose=purpose),override_settings(SMS_REGION=region):
                        self.assertTrue(phone_ready('+8613800000000',purpose))
                        self.assertEqual(send_code('+8613800000000',None if purpose=='password_changed' else '123456',purpose=purpose),1)
                        self.assertEqual(client.call_args.args[1],region)
            self.assertEqual(client.return_value.SendSms.call_count,12)

    @override_settings(SMS_REGISTRATION_TEMPLATE_ID='124',SMS_REGISTRATION_TEMPLATE_APPROVED=True,
        SMS_PASSWORD_TEMPLATE_ID='125',SMS_PASSWORD_TEMPLATE_APPROVED=True,
        SMS_PASSWORD_CHANGED_TEMPLATE_ID='126',SMS_PASSWORD_CHANGED_TEMPLATE_APPROVED=True)
    def test_each_purpose_selects_approved_template_and_exact_network_receipt(self):
        from .sms import send_code
        from tencentcloud.sms.v20210111.models import SendSmsResponse
        def receipt(client,request):
            self.assertEqual(request.PhoneNumberSet,['+8613800000000'])
            self.assertEqual(request.TemplateParamSet,[] if request.TemplateId=='126' else ['123456','5'])
            result=SendSmsResponse();result.from_json_string(json.dumps({'SendStatusSet':[
                {'Code':'Ok','SerialNo':'fixture','PhoneNumber':'+8613800000000'}]}));return result
        with patch('tencentcloud.sms.v20210111.sms_client.SmsClient.SendSms',autospec=True,side_effect=receipt) as network:
            for purpose,template in (('registration','124'),('password_reset','125'),('password_changed','126')):
                self.assertTrue(phone_ready('+8613800000000',purpose))
                self.assertFalse(phone_ready('+12025550100',purpose))
                self.assertEqual(send_code('+8613800000000',None if purpose=='password_changed' else '123456',purpose=purpose),1)
                self.assertEqual(network.call_args.args[1].TemplateId,template)
            for result in (None,Mock(SendStatusSet=None),Mock(SendStatusSet=[Mock(Code='Ok',SerialNo=1,PhoneNumber='+8613800000000')]),
                    Mock(SendStatusSet=[Mock(),Mock()])):
                network.side_effect=None;network.return_value=result
                self.assertEqual(send_code('+8613800000000','123456',purpose='registration'),0)


@override_settings(AUTH_PHONE_ENABLED=True, SMS_TEMPLATE_APPROVED=True,
    SMS_SECRET_ID='fixture-id', SMS_SECRET_KEY='fixture-key', SMS_REGION='ap-guangzhou',
    SMS_SDK_APP_ID='1400000000', SMS_SIGN='Fixture', SMS_TEMPLATE_ID='123',
    SMS_ALLOWED_COUNTRY_CODES=('86',), AUTH_CODE_TTL_SECONDS=300)
class PhoneLoginTests(TestCase):
    def setUp(self):
        self.client.get('/api/auth/session')

    def post(self, path, data):
        return self.client.post('/api/auth/' + path, json.dumps(data), content_type='application/json')

    def test_approved_phone_cannot_bypass_password_and_captcha(self):
        with patch('backend.sms.send_login_code', return_value=1) as send:
            response = self.post('challenges', {'method': 'phone', 'identifier': '+8613800000000'})
        self.assertEqual(response.status_code, 403)
        send.assert_not_called()
        self.assertEqual(response.json()['error']['code'],'AUTH_FLOW_REQUIRED')
        self.assertFalse(LoginIdentity.objects.exists())
        self.assertFalse(LoginChallenge.objects.exists())
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])

    def test_legacy_phone_challenge_never_reaches_supplier(self):
        with patch('backend.sms.send_login_code', side_effect=OSError('SMS_DELIVERY_FAILED')) as send:
            response = self.post('challenges', {'method': 'phone', 'identifier': '+8613800000000'})
        self.assertEqual(response.status_code, 403)
        send.assert_not_called()
        self.assertFalse(LoginChallenge.objects.exists())

    def test_unapproved_country_is_rejected_before_delivery(self):
        with patch('backend.sms.send_login_code') as send:
            self.assertEqual(self.post('challenges', {'method': 'phone', 'identifier': '+12025550100'}).status_code, 403)
        send.assert_not_called()
        self.assertFalse(LoginChallenge.objects.exists())


@override_settings(AUTH_PHONE_ENABLED=True,SMS_REAL_DELIVERY_VERIFIED=True,
    SMS_SECRET_ID='fixture-id',SMS_SECRET_KEY='fixture-key',SMS_REGION='ap-guangzhou',
    SMS_SDK_APP_ID='1400000000',SMS_SIGN='Fixture',SMS_ALLOWED_COUNTRY_CODES=('86',))
class SmsNoticeTransportTests(SimpleTestCase):
    def test_notice_requires_complete_transport_and_real_delivery_declaration(self):
        from .sms import send_notice
        for field,value in (('SMS_REAL_DELIVERY_VERIFIED',False),('SMS_SECRET_KEY',''),
                ('SMS_REGION','ap-shanghai'),('SMS_ALLOWED_COUNTRY_CODES',()),('SMS_SDK_APP_ID','bad')):
            with self.subTest(field=field),override_settings(**{field:value}),patch(
                    'tencentcloud.sms.v20210111.sms_client.SmsClient') as client:
                with self.assertRaisesMessage(OSError,'SMS_CHANNEL_UNAVAILABLE'):
                    send_notice('+8613800000000','123',['Synthetic'])
                client.assert_not_called()

    def test_notice_returns_only_matching_provider_acceptance_reference(self):
        from .sms import send_notice
        with patch('tencentcloud.sms.v20210111.sms_client.SmsClient') as client:
            client.return_value.SendSms.return_value=Mock(SendStatusSet=[
                Mock(Code='Ok',SerialNo='synthetic-reference',PhoneNumber='+8613800000000')])
            self.assertEqual(send_notice('+8613800000000','123',['Synthetic']),'synthetic-reference')
            request=client.return_value.SendSms.call_args.args[0]
            self.assertEqual(request.TemplateParamSet,['Synthetic'])
            self.assertIsNone(client.call_args.args[2].retryer)
            for status in (Mock(Code='Unknown',SerialNo='',PhoneNumber='+8613800000000'),
                    Mock(Code='Ok',SerialNo='synthetic-reference',PhoneNumber='+8613900000000')):
                client.return_value.SendSms.return_value=Mock(SendStatusSet=[status])
                self.assertEqual(send_notice('+8613800000000','123',['Synthetic']),'')

    def test_notice_unapproved_shape_urls_or_controls_never_reach_sdk(self):
        from .sms import send_notice
        for template,parameters in (('bad',[]),('123',['https://example.invalid']),
                ('123',['www.example.invalid']),('123',['line\nline']),('123',['x'*101])):
            with self.subTest(template=template),patch('tencentcloud.sms.v20210111.sms_client.SmsClient') as client:
                with self.assertRaisesMessage(ValueError,'INVALID_NOTICE'):
                    send_notice('+8613800000000',template,parameters)
                client.assert_not_called()

    def test_notice_provider_error_is_redacted_without_automatic_retry(self):
        from .sms import send_notice
        from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
        with patch('tencentcloud.sms.v20210111.sms_client.SmsClient') as client:
            client.return_value.SendSms.side_effect=TencentCloudSDKException('Unknown','synthetic-private-provider-body')
            with self.assertRaisesMessage(OSError,'SMS_DELIVERY_FAILED'):
                send_notice('+8613800000000','123',['Synthetic'])
            self.assertEqual(client.return_value.SendSms.call_count,1)
