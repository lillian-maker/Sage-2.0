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


@override_settings(AUTH_PHONE_ENABLED=True, SMS_TEMPLATE_APPROVED=True,
    SMS_SECRET_ID='fixture-id', SMS_SECRET_KEY='fixture-key', SMS_REGION='ap-guangzhou',
    SMS_SDK_APP_ID='1400000000', SMS_SIGN='Fixture', SMS_TEMPLATE_ID='123',
    SMS_ALLOWED_COUNTRY_CODES=('86',), AUTH_CODE_TTL_SECONDS=300)
class PhoneLoginTests(TestCase):
    def setUp(self):
        self.client.get('/api/auth/session')

    def post(self, path, data):
        return self.client.post('/api/auth/' + path, json.dumps(data), content_type='application/json')

    def test_approved_phone_verifies_once_and_creates_separate_identity(self):
        with patch('backend.sms.send_login_code', return_value=1) as send:
            response = self.post('challenges', {'method': 'phone', 'identifier': '+8613800000000'})
        self.assertEqual(response.status_code, 201)
        code = send.call_args.args[1]
        data = {'challenge_id': response.json()['challenge_id'], 'code': code}
        self.assertEqual(self.post('verify', data).status_code, 200)
        self.assertEqual(LoginIdentity.objects.filter(method='phone').count(), 1)
        self.assertFalse(LoginIdentity.objects.filter(method='email').exists())
        self.assertEqual(self.post('verify', data).status_code, 422)

    def test_failed_delivery_never_produces_verifiable_challenge(self):
        with patch('backend.sms.send_login_code', side_effect=OSError('SMS_DELIVERY_FAILED')):
            response = self.post('challenges', {'method': 'phone', 'identifier': '+8613800000000'})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(LoginChallenge.objects.get().delivered)

    def test_unapproved_country_is_rejected_before_delivery(self):
        with patch('backend.sms.send_login_code') as send:
            self.assertEqual(self.post('challenges', {'method': 'phone', 'identifier': '+12025550100'}).status_code, 503)
        send.assert_not_called()
        self.assertFalse(LoginChallenge.objects.exists())
