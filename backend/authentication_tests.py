"""Real auth state transitions with only the supplier network boundary mocked."""
import json
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from backend.models import BillingAccount, LoginIdentity
from django.test import override_settings
from unittest.mock import patch
from django.core import mail
from django.utils import timezone
from datetime import timedelta
import re
import uuid
from backend.models import (AccountProfile, AccountSession, AuthFlow, AuthThrottle,
    LoginChallenge, Membership, InvitationCode, RegistrationAttribution)

AUTH_FIXTURE = dict(AUTH_CAPTCHA_ENABLED=True, CAPTCHA_SECRET_ID='fixture-id',
    CAPTCHA_SECRET_KEY='fixture-key', CAPTCHA_APP_ID='123', CAPTCHA_APP_SECRET_KEY='fixture-app-key',
    AUTH_EMAIL_ENABLED=True, AUTH_EMAIL_TRANSPORT='smtp', EMAIL_HOST='fixture.invalid',
    DEFAULT_FROM_EMAIL='fixture@example.invalid', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    AUTH_REGISTRATION_ENABLED=True, AUTH_REGISTRATION_TERMS_READY=True,
    AUTH_REGISTRATION_TERMS_VERSION='NONPRODUCTION-FIXTURE-ONLY',
    AUTH_REGISTRATION_TERMS_URL='/legal/nonproduction-terms',
    AUTH_REGISTRATION_PRIVACY_URL='/legal/nonproduction-privacy')

PHONE_FIXTURE = dict(AUTH_FIXTURE, AUTH_PHONE_ENABLED=True, SMS_TEMPLATE_APPROVED=True,
    SMS_SECRET_ID='fixture-id', SMS_SECRET_KEY='fixture-key', SMS_REGION='ap-guangzhou',
    SMS_SDK_APP_ID='1400000000', SMS_SIGN='Fixture', SMS_TEMPLATE_ID='123',
    SMS_ALLOWED_COUNTRY_CODES=('86',), SMS_REGISTRATION_TEMPLATE_ID='124',
    SMS_REGISTRATION_TEMPLATE_APPROVED=True, SMS_PASSWORD_TEMPLATE_ID='125',
    SMS_PASSWORD_TEMPLATE_APPROVED=True, SMS_PASSWORD_CHANGED_TEMPLATE_ID='126',
    SMS_PASSWORD_CHANGED_TEMPLATE_APPROVED=True)


class PhoneFixture:
    def setUp(self):
        from unittest.mock import Mock
        self.client = Client(enforce_csrf_checks=True)
        self.token = self.client.get('/api/auth/session').json()['csrf_token']
        self.codes = []
        def receipt(client, request):
            if request.TemplateId != '126':
                self.codes.append(request.TemplateParamSet[0])
            return Mock(SendStatusSet=[Mock(Code='Ok', SerialNo='fixture',
                PhoneNumber=request.PhoneNumberSet[0])])
        supplier = patch('tencentcloud.sms.v20210111.sms_client.SmsClient.SendSms', autospec=True, side_effect=receipt)
        self.sms = supplier.start(); self.addCleanup(supplier.stop)
        captcha = patch('backend.authentication.CommonClient.call_json', return_value={'Response':{'CaptchaCode':1}})
        self.captcha = captcha.start(); self.addCleanup(captcha.stop)

    def post(self, path, body, client=None):
        return (client or self.client).post('/api/auth/'+path, json.dumps(body),
            content_type='application/json', HTTP_X_CSRFTOKEN=self.token)

    def start(self, **values):
        body = dict(method='phone', first_name='Alice',last_name='',identifier='+8613800000000',
            password='Silver!864',agreement_acceptance='NONPRODUCTION-FIXTURE-ONLY',invitation_code='')
        body.update(values)
        return self.post('register/start',body)

    def prove(self, flow, ticket=None):
        return self.post('captcha',dict(flow_id=flow['flow_id'],ticket=ticket or uuid.uuid4().hex,randstr='fixture'))

    def code(self):
        start=self.start(); self.assertEqual(start.status_code,201,start.content)
        code=self.prove(start.json()); self.assertEqual(code.status_code,201,code.content)
        return dict(flow_id=code.json()['flow_id'],challenge_id=code.json()['challenge_id'],code=self.codes[-1])

    def account(self,password='Old historical password with spaces'):
        user=get_user_model().objects.create_user(username=uuid.uuid4().hex,password=password)
        LoginIdentity.objects.create(user=user,method='phone',identifier='+8613800000000')
        account=BillingAccount.objects.create(kind='personal',personal_owner=user,name='Fixture')
        Membership.objects.create(user=user,account=account,role='owner')
        return user


@override_settings(**PHONE_FIXTURE)
class PhoneAuthenticationTests(PhoneFixture,TestCase):
    def test_registration_phone_identity_password_consent_and_account_no_email(self):
        body=self.code();self.assertFalse(get_user_model().objects.exists())
        response=self.post('register/verify',dict(body,invitation_code=''))
        self.assertEqual(response.status_code,200,response.content)
        user=get_user_model().objects.get();self.assertTrue(user.check_password('Silver!864'))
        self.assertEqual(user.email,'');self.assertEqual(LoginIdentity.objects.get().method,'phone')
        self.assertEqual(AccountProfile.objects.get().agreement_version,'NONPRODUCTION-FIXTURE-ONLY')
        self.assertEqual(Membership.objects.get().role,'owner')
        self.assertEqual(self.client.get('/api/account/profile').json()['phone'],'+8613800000000')
        self.assertEqual(AuthFlow.objects.get().password_hash,'')

    def test_phone_password_login_requires_captcha_but_not_sms_config(self):
        self.account()
        with override_settings(SMS_SECRET_KEY='',SMS_PASSWORD_TEMPLATE_APPROVED=False):
            caps=self.client.get('/api/auth/session').json()['capabilities']
            self.assertTrue(caps['phone_password_login']);self.assertFalse(caps['phone_password_reset'])
            result=self.post('password-login',dict(method='phone',identifier='+8613800000000',password='Old historical password with spaces'))
            self.assertEqual(result.status_code,201,result.content)
            self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
            self.assertEqual(self.prove(result.json()).status_code,200)
        self.sms.assert_not_called()

    def test_phone_settings_original_identity_reset_revokes_all_sessions(self):
        user=self.account();other=Client();other.force_login(user);self.client.force_login(user)
        self.token=self.client.get('/api/auth/session').json()['csrf_token']
        self.assertTrue(self.client.get('/api/auth/session').json()['capabilities']['phone_password_reset'])
        sent=self.post('password-challenges',dict(method='phone',identifier='+8613800000000'))
        self.assertEqual(sent.status_code,201,sent.content)
        with self.captureOnCommitCallbacks(execute=True):
            result=self.post('password-reset',dict(challenge_id=sent.json()['challenge_id'],code=self.codes[-1],password='Copper!975'))
        self.assertEqual(result.status_code,200,result.content)
        self.assertFalse(other.get('/api/auth/session').json()['authenticated'])
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        user.refresh_from_db();self.assertEqual(user.email,'');self.assertTrue(user.check_password('Copper!975'))
        self.assertEqual(self.sms.call_args.args[1].TemplateId,'126')

    def test_recovery_original_phone_no_autologin_preserves_user_and_account(self):
        user=self.account(password=None)
        start=self.post('recovery/start',dict(method='phone',identifier='+8613800000000'))
        self.assertEqual(start.status_code,201,start.content)
        code=self.prove(start.json());self.assertEqual(code.status_code,201,code.content)
        verify=self.post('recovery/verify',dict(flow_id=start.json()['flow_id'],challenge_id=code.json()['challenge_id'],code=self.codes[-1]))
        self.assertEqual(verify.status_code,200,verify.content)
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        self.assertEqual(self.post('recovery/complete',dict(flow_id=start.json()['flow_id'],password='Copper!975')).status_code,200)
        user.refresh_from_db();self.assertTrue(user.check_password('Copper!975'))
        self.assertEqual(get_user_model().objects.count(),1);self.assertEqual(BillingAccount.objects.get().personal_owner_id,user.pk)

    def test_invalid_ascii_country_length_or_missing_password_never_supplier(self):
        for identifier in ('13800000000','+12025550100','+861380000000','+86138000000000','+861３800000000','+8613800000000\n'):
            with self.subTest(identifier=identifier):self.assertEqual(self.start(identifier=identifier).status_code,422)
        self.assertEqual(self.start(password='').status_code,422)
        self.assertFalse(AuthFlow.objects.exists());self.sms.assert_not_called()

    def test_google_only_display_claim_cannot_restore_phone_or_settings_email(self):
        user=get_user_model().objects.create_user(username='google',password=None)
        LoginIdentity.objects.create(user=user,method='google',identifier='fixture-sub',provider_email='claim@example.invalid')
        start=self.post('recovery/start',dict(method='phone',identifier='+8613800000000'))
        self.assertEqual(start.status_code,201,start.content)
        code=self.prove(start.json());self.assertEqual(code.status_code,201,code.content)
        self.sms.assert_not_called();self.assertFalse(LoginChallenge.objects.get().delivered)
        self.client.force_login(user);self.token=self.client.get('/api/auth/session').json()['csrf_token']
        self.assertFalse(self.client.get('/api/auth/session').json()['capabilities']['phone_password_reset'])
        self.assertEqual(self.post('password-challenges',dict(method='phone',identifier='+8613800000000')).status_code,403)

    def test_channel_gate_defaultoff_and_every_required_config_stops_before_hash_or_supplier(self):
        from django.contrib.auth.hashers import make_password
        for changed in ({'AUTH_PHONE_ENABLED':False},{'SMS_REGISTRATION_TEMPLATE_APPROVED':False},
                {'SMS_REGISTRATION_TEMPLATE_ID':''},{'SMS_REGISTRATION_TEMPLATE_ID':'abc'},
                {'SMS_REGION':'invalid-region'},{'SMS_SDK_APP_ID':'abc'},
                {'SMS_ALLOWED_COUNTRY_CODES':('1',)},{'SMS_SECRET_KEY':''},
                {'SMS_TEMPLATE_APPROVED':False},{'AUTH_REGISTRATION_TERMS_READY':False}):
            with self.subTest(config=tuple(changed)),override_settings(**changed),patch('backend.authentication.make_password',wraps=make_password) as hashed:
                self.assertEqual(self.start().status_code,503)
                self.assertFalse(self.client.get('/api/auth/session').json()['capabilities']['phone_registration'])
                hashed.assert_not_called()
        self.sms.assert_not_called();self.assertFalse(AuthFlow.objects.exists())

    def test_method_restart_cannot_reuse_phone_otp_or_global_ticket_in_email_flow(self):
        phone=self.start().json();ticket=uuid.uuid4().hex;code=self.prove(phone,ticket).json()
        email=self.post('register/start',dict(first_name='Alice',last_name='',identifier='new@example.invalid',password='Silver!864',agreement_acceptance='NONPRODUCTION-FIXTURE-ONLY',invitation_code=''))
        self.assertEqual(email.status_code,201,email.content)
        row=AuthFlow.objects.get(pk=phone['flow_id']);self.assertIsNotNone(row.consumed_at);self.assertEqual(row.password_hash,'')
        self.assertEqual(self.prove(email.json(),ticket).status_code,422)
        self.assertEqual(self.post('register/verify',dict(flow_id=phone['flow_id'],challenge_id=code['challenge_id'],code=self.codes[-1],invitation_code='')).status_code,422)
        self.assertFalse(get_user_model().objects.exists())

    def test_unsupported_sms_region_stops_registration_recovery_before_hash_or_flow(self):
        from django.contrib.auth.hashers import make_password
        for region in ('ap-shanghai','ap-hongkong','ap-singapore'):
            with self.subTest(region=region),override_settings(SMS_REGION=region),patch('backend.authentication.make_password',wraps=make_password) as hashed:
                self.assertEqual(self.start().status_code,503)
                self.assertEqual(self.post('recovery/start',dict(method='phone',identifier='+8613800000000')).status_code,503)
                caps=self.client.get('/api/auth/session').json()['capabilities']
                self.assertFalse(caps['phone_registration']);self.assertFalse(caps['phone_password_reset'])
                self.assertTrue(caps['phone_password_login'])
                hashed.assert_not_called();self.sms.assert_not_called();self.captcha.assert_not_called()
                self.assertFalse(AuthFlow.objects.exists());self.assertFalse(LoginChallenge.objects.exists())

    def test_unsupported_sms_region_notification_is_unavailable_without_sdk(self):
        from backend.authentication import _password_notification
        for region in ('ap-shanghai','ap-hongkong','ap-singapore'):
            with self.subTest(region=region),override_settings(SMS_REGION=region):
                with self.assertLogs('sage',level='WARNING') as logs:
                    _password_notification('+8613800000000','fixture','phone')
                self.assertIn('password_change_notification_unavailable',logs.output[0])
        self.sms.assert_not_called();self.assertEqual(len(mail.outbox),0)

    def test_phone_password_login_remains_ready_with_unsupported_sms_region(self):
        self.account()
        for region in ('ap-shanghai','ap-hongkong','ap-singapore'):
            with self.subTest(region=region),override_settings(SMS_REGION=region):
                self.client=Client(enforce_csrf_checks=True)
                self.token=self.client.get('/api/auth/session').json()['csrf_token']
                self.assertTrue(self.client.get('/api/auth/session').json()['capabilities']['phone_password_login'])
                started=self.post('password-login',dict(method='phone',identifier='+8613800000000',password='Old historical password with spaces'))
                self.assertEqual(started.status_code,201,started.content)
                self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
                self.assertEqual(self.prove(started.json()).status_code,200)
                self.assertTrue(self.client.get('/api/auth/session').json()['authenticated'])
        self.sms.assert_not_called()

    def test_challenge_exact_method_and_purpose_cannot_be_swapped(self):
        body=self.code()
        for changes in ({'method':'email'},{'purpose':'password_reset'},{'identifier':'+8613900000000'}):
            with self.subTest(changes=changes):
                LoginChallenge.objects.filter(pk=body['challenge_id']).update(**changes)
                self.assertEqual(self.post('register/verify',dict(body,invitation_code='')).status_code,422)
                LoginChallenge.objects.filter(pk=body['challenge_id']).update(method='phone',purpose='registration',identifier='+8613800000000')

    def test_wrong_code_limit_resend_invalidates_old_code_and_expired_grant(self):
        body=self.code();old=body['code']
        with override_settings(AUTH_RESEND_SECONDS=0):
            result=self.post('resend',{'flow_id':body['flow_id']})
        self.assertEqual(result.status_code,201,result.content)
        self.assertIsNotNone(LoginChallenge.objects.get(pk=body['challenge_id']).consumed_at)
        wrong=dict(flow_id=body['flow_id'],challenge_id=result.json()['challenge_id'],code='999999' if self.codes[-1]!='999999' else '000000',invitation_code='')
        for _ in range(5):self.assertEqual(self.post('register/verify',wrong).status_code,422)
        wrong['code']=self.codes[-1];self.assertEqual(self.post('register/verify',wrong).status_code,422)
        self.assertFalse(get_user_model().objects.exists())

    def test_phone_attempt_and_captcha_tombstone_survive_downstream_database_failure(self):
        from django.db import DatabaseError
        body=self.code()
        with patch('backend.authentication.BillingAccount.objects.create',side_effect=DatabaseError('fixture')):
            self.assertEqual(self.post('register/verify',dict(body,invitation_code='')).status_code,503)
        self.assertEqual(LoginChallenge.objects.get().attempts,1)
        self.assertFalse(get_user_model().objects.exists());self.assertFalse(Membership.objects.exists())
        self.assertIsNotNone(AuthFlow.objects.get().captcha_digest)

    def test_phone_invitation_final_recheck_failure_then_clear_has_no_reward(self):
        InvitationCode.objects.create(code='FIXTURE8',blogger='Fixture')
        with override_settings(INVITATION_CODES_READY=True):
            body=self.code();InvitationCode.objects.filter(pk='FIXTURE8').update(is_active=False)
            response=self.post('register/verify',dict(body,invitation_code='FIXTURE8'))
            self.assertEqual(response.status_code,422);self.assertFalse(get_user_model().objects.exists())
            self.assertEqual(self.post('register/verify',dict(body,invitation_code='')).status_code,200)
        self.assertFalse(RegistrationAttribution.objects.exists())
        from backend.commerce.models import CreditBatch
        self.assertFalse(CreditBatch.objects.exists())

    def test_duplicate_registration_never_overwrites_or_enumerates_before_proof(self):
        user=self.account();before=user.password;body=self.code()
        result=self.post('register/verify',dict(body,invitation_code=''))
        self.assertEqual(result.json()['error']['code'],'REGISTRATION_FAILED')
        user.refresh_from_db();self.assertEqual(user.password,before);self.assertEqual(get_user_model().objects.count(),1)

    def test_phone_notification_missing_and_network_failure_observable_no_email(self):
        from backend.authentication import _password_notification
        from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
        with override_settings(SMS_PASSWORD_CHANGED_TEMPLATE_APPROVED=False),self.assertLogs('sage',level='WARNING') as logs:
            _password_notification('+8613800000000','fixture','phone')
        self.assertIn('password_change_notification_unavailable',logs.output[0]);self.sms.assert_not_called()
        self.sms.side_effect=TencentCloudSDKException('NetworkError','private body')
        with self.assertLogs('sage',level='WARNING') as logs:_password_notification('+8613800000000','fixture','phone')
        self.assertIn('password_change_notification_failed',logs.output[0]);self.assertNotIn('private body',''.join(logs.output))
        self.assertEqual(len(mail.outbox),0)

    def test_disabled_user_unknown_phone_no_sms_and_cannot_register_over_identity(self):
        user=self.account();user.is_active=False;user.save(update_fields=['is_active'])
        result=self.post('recovery/start',dict(method='phone',identifier='+8613800000000'))
        self.assertEqual(self.prove(result.json()).status_code,201);self.sms.assert_not_called()
        self.assertEqual(self.post('password-login',dict(method='phone',identifier='+8613800000000',password='Old historical password with spaces')).status_code,401)

    def test_settings_does_not_accept_other_phone_or_google_display_email(self):
        user=self.account();self.client.force_login(user);self.token=self.client.get('/api/auth/session').json()['csrf_token']
        self.assertEqual(self.post('password-challenges',dict(method='phone',identifier='+8613900000000')).status_code,403)
        self.assertEqual(self.post('password-challenges',dict(identifier='display@example.invalid')).status_code,403)
        self.assertFalse(self.client.get('/api/account/deletion-eligibility').json()['available'])
        self.sms.assert_not_called()

    def test_phone_settings_last_write_deadline_rolls_back_password_session_and_audit(self):
        from backend.models import AuditEvent
        from django.contrib.sessions.models import Session
        user=self.account();original=user.password;self.client.force_login(user)
        self.token=self.client.get('/api/auth/session').json()['csrf_token']
        self.client.get('/api/account/sessions')
        sent=self.post('password-challenges',dict(method='phone',identifier='+8613800000000'))
        item=LoginChallenge.objects.get(pk=sent.json()['challenge_id']);session_key=self.client.session.session_key
        create=AuditEvent.objects.create
        with patch('backend.authentication.timezone.now',return_value=timezone.now()) as now:
            def expire(*args,**kwargs):
                row=create(*args,**kwargs);now.return_value=item.expires_at+timedelta(seconds=1);return row
            with patch('backend.authentication.AuditEvent.objects.create',side_effect=expire):
                result=self.post('password-reset',dict(challenge_id=str(item.pk),code=self.codes[-1],password='Copper!975'))
        self.assertEqual(result.status_code,422);user.refresh_from_db();self.assertEqual(user.password,original)
        self.assertTrue(Session.objects.filter(pk=session_key).exists());self.assertFalse(AuditEvent.objects.filter(action='auth.password_reset').exists())
        item.refresh_from_db();self.assertEqual(item.attempts,1);self.assertIsNone(item.consumed_at)

    def test_phone_delivery_unknown_cannot_set_delivered(self):
        from unittest.mock import Mock
        flow=self.start().json();self.sms.side_effect=None;self.sms.return_value=Mock(SendStatusSet=[])
        self.assertEqual(self.prove(flow).status_code,503);self.assertFalse(LoginChallenge.objects.get().delivered)
        self.assertFalse(get_user_model().objects.exists());self.assertEqual(AuthFlow.objects.get().attempts,1)
        self.assertEqual(self.sms.call_count,1)

    def test_phone_supplier_latency_returns_remaining_ttl_and_expiry_never_delivered(self):
        flow=self.start().json();deadline=AuthFlow.objects.get().expires_at
        receipt=self.sms.side_effect
        with patch('backend.authentication.timezone.now',return_value=deadline-timedelta(seconds=300)) as now:
            def delayed(*args,**kwargs):
                result=receipt(*args,**kwargs);now.return_value=deadline-timedelta(seconds=270);return result
            self.sms.side_effect=delayed
            result=self.prove(flow)
        self.assertEqual(result.status_code,201,result.content);self.assertEqual(result.json()['expires_in'],270)
        self.post('cancel',dict(flow_id=flow['flow_id']));flow=self.start().json();deadline=AuthFlow.objects.get(pk=flow['flow_id']).expires_at
        with patch('backend.authentication.timezone.now',return_value=deadline-timedelta(seconds=300)) as now:
            def expired(*args,**kwargs):
                result=receipt(*args,**kwargs);now.return_value=deadline+timedelta(seconds=1);return result
            self.sms.side_effect=expired
            with override_settings(AUTH_RESEND_SECONDS=0):result=self.prove(flow)
        self.assertEqual(result.status_code,422,result.content)
        self.assertFalse(LoginChallenge.objects.filter(authflow__pk=flow['flow_id'],delivered=True).exists())
        self.assertEqual(AuthFlow.objects.get(pk=flow['flow_id']).stage,'captcha')

    def test_phone_settings_scrubs_other_method_user_flows_without_using_display_email(self):
        user=self.account();self.client.force_login(user);self.token=self.client.get('/api/auth/session').json()['csrf_token']
        other=AuthFlow.objects.create(method='email',purpose='login',identifier='original@example.invalid',user=user,
            session_digest='other-fixture',password_hash=user.password,expires_at=timezone.now()+timedelta(seconds=300))
        sent=self.post('password-challenges',dict(method='phone',identifier='+8613800000000'))
        self.assertEqual(self.post('password-reset',dict(challenge_id=sent.json()['challenge_id'],code=self.codes[-1],password='Copper!975')).status_code,200)
        other.refresh_from_db();self.assertIsNotNone(other.consumed_at);self.assertEqual(other.password_hash,'')

    def test_deleted_user_phone_flow_cannot_restore_password_or_send(self):
        user=self.account();start=self.post('recovery/start',dict(method='phone',identifier='+8613800000000'))
        # Simulate the already-reviewed erasure's exact identity/user result; no SMS deletion added.
        LoginIdentity.objects.filter(user=user).delete();user.is_active=False;user.set_unusable_password();user.save()
        code=self.prove(start.json());self.assertEqual(code.status_code,201,code.content);self.sms.assert_not_called()
        self.assertEqual(self.post('recovery/verify',dict(flow_id=start.json()['flow_id'],challenge_id=code.json()['challenge_id'],code='123456')).status_code,422)
        self.assertFalse(user.has_usable_password());self.assertFalse(LoginIdentity.objects.exists())


@override_settings(**PHONE_FIXTURE)
@skipUnlessDBFeature('has_select_for_update')
class PhoneConcurrencyTests(PhoneFixture,TransactionTestCase):
    def worker(self,action):
        from django.db import close_old_connections,connections,connection
        close_old_connections()
        try:
            with connection.cursor() as cursor:cursor.execute("SET lock_timeout = '5s'")
            return action()
        finally:connections['default'].close()

    def test_duplicate_phone_registration_is_one_identity_account_and_user(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        first=self.code();second=Client(enforce_csrf_checks=True)
        token=second.get('/api/auth/session').json()['csrf_token']
        started=second.post('/api/auth/register/start',json.dumps(dict(method='phone',first_name='Other',last_name='',identifier='+8613800000000',password='Silver!864',agreement_acceptance='NONPRODUCTION-FIXTURE-ONLY',invitation_code='')),content_type='application/json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(started.status_code,201,started.content)
        with override_settings(AUTH_RESEND_SECONDS=0):
            sent=second.post('/api/auth/captcha',json.dumps(dict(flow_id=started.json()['flow_id'],ticket=uuid.uuid4().hex,randstr='fixture')),content_type='application/json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(sent.status_code,201,sent.content)
        other=dict(flow_id=sent.json()['flow_id'],challenge_id=sent.json()['challenge_id'],code=self.codes[-1],invitation_code='')
        barrier=Barrier(2)
        def submit(client,csrf,body):
            barrier.wait(timeout=5)
            return client.post('/api/auth/register/verify',json.dumps(body),content_type='application/json',HTTP_X_CSRFTOKEN=csrf).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs=[pool.submit(self.worker,lambda:submit(self.client,self.token,dict(first,invitation_code=''))),pool.submit(self.worker,lambda:submit(second,token,other))]
            self.assertEqual(sorted(job.result(timeout=10) for job in jobs),[200,422])
        self.assertEqual(get_user_model().objects.count(),1);self.assertEqual(LoginIdentity.objects.count(),1)
        self.assertEqual(BillingAccount.objects.count(),1);self.assertEqual(Membership.objects.count(),1)

    def test_captcha_network_cancel_keeps_committed_ticket_and_never_sends_sms(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from django.db import connection
        flow=self.start().json();entered,release=Event(),Event();ticket=uuid.uuid4().hex
        def supplier(*args,**kwargs):
            self.assertFalse(connection.in_atomic_block);entered.set();self.assertTrue(release.wait(timeout=5))
            return {'Response':{'CaptchaCode':1}}
        with patch('backend.authentication.CommonClient.call_json',side_effect=supplier),ThreadPoolExecutor(max_workers=1) as pool:
            job=pool.submit(self.worker,lambda:self.prove(flow,ticket).status_code)
            try:
                self.assertTrue(entered.wait(timeout=5));self.assertEqual(AuthFlow.objects.get().attempts,1)
                self.assertEqual(self.post('cancel',{'flow_id':flow['flow_id']}).status_code,200)
            finally:release.set()
            self.assertEqual(job.result(timeout=10),422)
        self.sms.assert_not_called();self.assertTrue(AuthThrottle.objects.filter(key=__import__('backend.views',fromlist=['digest']).digest('captcha-ticket:'+ticket)).exists())

    def test_settings_attempt_visible_after_commit_even_when_business_audit_fails(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import DatabaseError
        user=self.account();original=user.password;self.client.force_login(user)
        self.token=self.client.get('/api/auth/session').json()['csrf_token']
        sent=self.post('password-challenges',dict(method='phone',identifier='+8613800000000'))
        self.assertEqual(sent.status_code,201,sent.content)
        body=dict(challenge_id=sent.json()['challenge_id'],code=self.codes[-1],password='Copper!975')
        def fault(*args,**kwargs):
            with ThreadPoolExecutor(max_workers=1) as pool:
                observed=pool.submit(self.worker,lambda:(LoginChallenge.objects.get(pk=body['challenge_id']).attempts,get_user_model().objects.get(pk=user.pk).password)).result(timeout=5)
            self.assertEqual(observed,(1,original));raise DatabaseError('fixture downstream failure')
        with patch('backend.authentication.AuditEvent.objects.create',side_effect=fault):
            self.assertEqual(self.post('password-reset',body).status_code,503)
        user.refresh_from_db();self.assertEqual(user.password,original);self.assertEqual(LoginChallenge.objects.get().attempts,1)
        self.assertTrue(self.client.get('/api/auth/session').json()['authenticated'])

    def test_phone_cleanup_skips_locked_flow_and_its_otp_then_scrubs(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import transaction
        from backend.authentication import cleanup_auth_data
        body=self.code();AuthFlow.objects.filter(pk=body['flow_id']).update(expires_at=timezone.now()-timedelta(seconds=1))
        LoginChallenge.objects.filter(pk=body['challenge_id']).update(expires_at=timezone.now()-timedelta(seconds=1))
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                AuthFlow.objects.select_for_update().get(pk=body['flow_id'])
                self.assertEqual(pool.submit(self.worker,lambda:cleanup_auth_data(apply=True)).result(timeout=5),{'flows':0,'challenges':0})
                self.assertTrue(LoginChallenge.objects.get().code_digest)
        self.assertEqual(cleanup_auth_data(apply=True),{'flows':1,'challenges':1})
        self.assertEqual(AuthFlow.objects.get().password_hash,'');self.assertEqual(LoginChallenge.objects.get().code_digest,'')
        self.assertTrue(AuthThrottle.objects.filter(key=AuthFlow.objects.get().captcha_digest).exists())

    def test_invitation_real_lock_wait_expiry_keeps_attempt_and_no_registration(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from django.db import transaction,connection
        import time
        InvitationCode.objects.create(code='FIXTURE8',blogger='Fixture');body=self.code()
        flow=AuthFlow.objects.get(pk=body['flow_id']);waiting=Event();pid=[]
        def verify():
            def signal(execute,sql,params,many,context):
                if 'FOR UPDATE' in sql and 'backend_invitationcode' in sql:
                    with connection.cursor() as cursor:cursor.execute('SELECT pg_backend_pid()');pid.append(cursor.fetchone()[0])
                    waiting.set()
                return execute(sql,params,many,context)
            with connection.execute_wrapper(signal):return self.post('register/verify',dict(body,invitation_code='FIXTURE8')).status_code
        with override_settings(INVITATION_CODES_READY=True),patch('backend.authentication.timezone.now',return_value=timezone.now()) as now,ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                InvitationCode.objects.select_for_update().get(pk='FIXTURE8')
                job=pool.submit(self.worker,verify);self.assertTrue(waiting.wait(timeout=5))
                blocked=False;until=time.monotonic()+4
                while not blocked and time.monotonic()<until:
                    with connection.cursor() as cursor:cursor.execute('SELECT cardinality(pg_blocking_pids(%s))',pid);blocked=cursor.fetchone()[0]>0
                    if not blocked:time.sleep(.02)
                self.assertTrue(blocked,'Actual PostgreSQL invite lock wait required')
                self.assertEqual(LoginChallenge.objects.get().attempts,1)
                now.return_value=flow.expires_at+timedelta(seconds=1)
            self.assertEqual(job.result(timeout=10),422)
        self.assertFalse(get_user_model().objects.exists());self.assertFalse(RegistrationAttribution.objects.exists())
        self.assertEqual(LoginChallenge.objects.get().attempts,1)


class AuthenticationBypassTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.token = self.client.get('/api/auth/session').json()['csrf_token']

    def post(self, path, body):
        return self.client.post('/api/auth/' + path, json.dumps(body),
            content_type='application/json', HTTP_X_CSRFTOKEN=self.token)

    def test_legacy_otp_endpoints_never_grant_session_or_signup(self):
        from django.test import override_settings
        from django.core import mail
        import re
        with override_settings(AUTH_EMAIL_ENABLED=True, EMAIL_HOST='fixture.invalid',
                DEFAULT_FROM_EMAIL='fixture@example.invalid',
                EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            response = self.post('challenges', {'method':'email','identifier':'new@example.invalid'})
            if response.status_code == 201:
                code = re.search(r'\b\d{6}\b', mail.outbox[-1].body).group()
                self.post('verify', {'challenge_id':response.json()['challenge_id'],'code':code})
        self.assertFalse(get_user_model().objects.exists())
        self.assertFalse(BillingAccount.objects.exists())
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])

    def test_password_cannot_log_in_before_captcha(self):
        user = get_user_model().objects.create_user(username='fixture', password='Silver!864')
        LoginIdentity.objects.create(user=user, method='email', identifier='fixture@example.invalid')
        self.post('password-login', {'identifier':'fixture@example.invalid','password':'Silver!864'})
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])

    def test_anonymous_settings_reset_cannot_skip_captcha(self):
        response = self.post('password-challenges', {'identifier':'fixture@example.invalid'})
        self.assertEqual(response.status_code, 403)


@override_settings(**AUTH_FIXTURE)
class EmailAuthenticationTests(AuthenticationBypassTests):
    def setUp(self):
        super().setUp()
        self.supplier = patch('backend.authentication.CommonClient.call_json', return_value={'Response':{'CaptchaCode':1}})
        self.network = self.supplier.start()
        self.addCleanup(self.supplier.stop)

    def start(self, **values):
        body = dict(first_name='Alice', last_name='', identifier='new@example.invalid',
            password='Silver!864', agreement_acceptance='NONPRODUCTION-FIXTURE-ONLY', invitation_code='')
        body.update(values)
        return self.post('register/start', body)

    def prove(self, flow_id, **values):
        body = {'flow_id':flow_id, 'ticket':uuid.uuid4().hex, 'randstr':'fixture-random'}
        body.update(values)
        return self.post('captcha', body)

    def code(self, start=None):
        start = start or self.start()
        self.assertEqual(start.status_code, 201, start.content)
        result = self.prove(start.json()['flow_id'])
        self.assertEqual(result.status_code, 201, result.content)
        body = {key:result.json()[key] for key in ('flow_id','challenge_id')}
        body['code'] = re.search(r'\b[0-9]{6}\b', mail.outbox[-1].body).group()
        return body

    def finish(self, body, invitation_code=''):
        return self.post('register/verify', dict(body, invitation_code=invitation_code))

    def account(self, password=None, identifier='original@example.invalid'):
        user = get_user_model().objects.create_user(username=uuid.uuid4().hex, password=password)
        LoginIdentity.objects.create(user=user, method='email', identifier=identifier)
        account = BillingAccount.objects.create(kind='personal', personal_owner=user, name='Fixture')
        Membership.objects.create(user=user, account=account, role='owner')
        return user, account

    def test_complete_registration_is_atomic_password_identity_consent_account_and_session(self):
        body = self.code()
        flow = AuthFlow.objects.get(pk=body['flow_id'])
        self.assertNotIn('Silver!864', flow.password_hash)
        self.assertTrue(flow.password_hash.startswith('pbkdf2_sha256$'))
        self.assertFalse(get_user_model().objects.exists())
        result = self.finish(body)
        self.assertEqual(result.status_code, 200, result.content)
        user = get_user_model().objects.get()
        self.assertTrue(user.check_password('Silver!864'))
        self.assertEqual(user.first_name, 'Alice')
        self.assertEqual(Membership.objects.get().role, 'owner')
        self.assertEqual(LoginIdentity.objects.get().user_id, user.pk)
        self.assertEqual(AccountProfile.objects.get().agreement_version, 'NONPRODUCTION-FIXTURE-ONLY')
        self.assertIsNotNone(AccountProfile.objects.get().agreement_accepted_at)
        self.assertTrue(result.json()['authenticated'])
        self.assertEqual(AccountSession.objects.count(), 1)
        flow.refresh_from_db()
        self.assertEqual(flow.password_hash, '')
        self.token = result.json()['csrf_token']
        self.assertEqual(self.finish(body).status_code, 422)

    def test_invalid_profile_agreement_and_password_never_create_flow(self):
        for fields in ({'first_name':''}, {'first_name':'Alice\n'}, {'last_name':1},
                {'agreement_acceptance':''}, {'agreement_acceptance':True}, {'password':'weak'},
                {'password':'Silver!864 '}, {'identifier':'invalid'}):
            self.assertEqual(self.start(**fields).status_code, 422, fields)
        self.assertFalse(AuthFlow.objects.exists())
        self.assertFalse(get_user_model().objects.exists())

    def test_registration_config_terms_captcha_and_delivery_default_closed(self):
        for field, value in (('AUTH_CAPTCHA_ENABLED',False), ('CAPTCHA_APP_SECRET_KEY',''),
                ('AUTH_REGISTRATION_ENABLED',False), ('AUTH_REGISTRATION_TERMS_READY',False),
                ('AUTH_REGISTRATION_TERMS_VERSION',''), ('AUTH_EMAIL_ENABLED',False)):
            with override_settings(**{field:value}):
                self.assertEqual(self.start().status_code, 503)
                self.assertFalse(self.client.get('/api/auth/session').json()['capabilities']['email_registration'])
        self.assertFalse(AuthFlow.objects.exists())

    def test_registration_ses_requires_its_own_template(self):
        with override_settings(AUTH_EMAIL_TRANSPORT='ses', SES_SECRET_ID='fixture', SES_SECRET_KEY='fixture',
                SES_REGION='ap-hongkong', SES_TEMPLATE_ID='123', AUTH_REGISTRATION_SES_TEMPLATE_ID=''):
            self.assertEqual(self.start().status_code, 503)

    def test_start_restart_cancel_expiry_and_no_skip(self):
        first = self.start().json()['flow_id']
        self.assertEqual(self.post('register/verify', dict(flow_id=first,challenge_id=str(uuid.uuid4()),
            code='123456',invitation_code='')).status_code, 422)
        second = self.start().json()['flow_id']
        self.assertEqual(self.prove(first).status_code, 422)
        self.assertEqual(self.post('cancel', {'flow_id':second}).status_code, 200)
        self.assertEqual(self.prove(second).status_code, 422)
        third = self.start().json()['flow_id']
        AuthFlow.objects.filter(pk=third).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.prove(third).status_code, 422)
        self.assertFalse(get_user_model().objects.exists())

    def test_cross_browser_and_purpose_are_rejected(self):
        body = self.code()
        other = Client(enforce_csrf_checks=True)
        token = other.get('/api/auth/session').json()['csrf_token']
        response = other.post('/api/auth/register/verify', json.dumps(dict(body,invitation_code='')),
            content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.post('recovery/verify',body).status_code, 422)
        self.assertEqual(self.post('recovery/complete', {'flow_id':body['flow_id'],'password':'Canyon!42'}).status_code,422)
        self.assertEqual(LoginChallenge.objects.get().attempts,0)
        self.assertFalse(get_user_model().objects.exists())

    def test_captcha_rejection_timeout_fallback_attempts_and_no_mail_or_session(self):
        flow = self.start().json()['flow_id']
        self.assertEqual(self.prove(flow,ticket='trerror_fixture').status_code,422)
        self.network.return_value = {'Response':{'CaptchaCode':9}}
        self.assertEqual(self.prove(flow).status_code,422)
        self.network.side_effect = OSError('fixture timeout')
        self.assertEqual(self.prove(flow).status_code,503)
        self.network.side_effect = None
        self.network.return_value = {'Response':{'CaptchaCode':True}}
        self.assertEqual(self.prove(flow).status_code,422)
        self.assertEqual(AuthFlow.objects.get().attempts,4)
        self.assertFalse(LoginChallenge.objects.exists())
        self.assertFalse(get_user_model().objects.exists())
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])

    def test_captcha_ticket_is_globally_single_use_even_after_failed_retry_and_restart(self):
        first = self.start().json()['flow_id']
        self.network.return_value = {'Response':{'CaptchaCode':9}}
        self.assertEqual(self.prove(first,ticket='single-use-ticket').status_code,422)
        self.assertEqual(self.prove(first,ticket='another-ticket').status_code,422)
        second = self.start().json()['flow_id']
        self.network.return_value = {'Response':{'CaptchaCode':1}}
        before = self.network.call_count
        self.assertEqual(self.prove(second,ticket='single-use-ticket').status_code,422)
        self.assertEqual(self.network.call_count,before)

    def test_captcha_supplier_contract_uses_actual_client_ip_and_fixed_application(self):
        flow = self.start().json()['flow_id']
        self.prove(flow,ticket='normal-ticket',randstr='normal-randstr')
        args = self.network.call_args.args
        self.assertEqual(args[0],'DescribeCaptchaResult')
        self.assertEqual(args[1],dict(CaptchaType=9,Ticket='normal-ticket',Randstr='normal-randstr',
            CaptchaAppId=123,AppSecretKey='fixture-app-key',UserIp='127.0.0.1'))
        result = self.client.get('/api/auth/session').json()
        self.assertNotIn('fixture-app-key',json.dumps(result))

    def test_wrong_code_persists_attempts_exhaustion_and_expiry(self):
        body = self.code()
        wrong = dict(body,code='000000' if body['code']!='000000' else '111111')
        for _ in range(5):
            self.assertEqual(self.finish(wrong).status_code,422)
        self.assertEqual(LoginChallenge.objects.get().attempts,5)
        self.assertEqual(self.finish(body).status_code,422)
        self.assertFalse(get_user_model().objects.exists())

    def test_resend_cooldown_old_challenge_and_flow_deadline(self):
        body = self.code()
        deadline = AuthFlow.objects.get().expires_at
        self.assertEqual(self.post('resend',{'flow_id':body['flow_id']}).status_code,429)
        with override_settings(AUTH_RESEND_SECONDS=0):
            result = self.post('resend',{'flow_id':body['flow_id']})
        self.assertEqual(result.status_code,201)
        self.assertEqual(AuthFlow.objects.get().expires_at,deadline)
        self.assertEqual(self.finish(body).status_code,422)

    def test_invitation_final_stop_can_correct_or_clear_without_partial_rows(self):
        with override_settings(INVITATION_CODES_READY=True):
            InvitationCode.objects.create(code='VALID123',blogger='Fixture')
            body = self.code(self.start(invitation_code='VALID123'))
            InvitationCode.objects.filter(pk='VALID123').update(is_active=False)
            failed = self.finish(body,'VALID123')
            self.assertEqual(failed.status_code,422)
            self.assertEqual(failed.json()['error']['message'],'无效邀请码，请检查')
            self.assertFalse(get_user_model().objects.exists())
            self.assertFalse(BillingAccount.objects.exists())
            self.assertFalse(RegistrationAttribution.objects.exists())
            result = self.finish(body,'')
            self.assertEqual(result.status_code,200,result.content)
            self.assertFalse(RegistrationAttribution.objects.exists())

    def test_invitation_source_off_is_distinct_and_blank_registration_still_works(self):
        body = self.code()
        with override_settings(INVITATION_CODES_READY=False):
            self.assertEqual(self.finish(body,'VALID123').status_code,503)
            self.assertFalse(get_user_model().objects.exists())
            self.assertEqual(self.finish(body,'').status_code,200)

    def test_valid_invitation_commits_with_complete_account(self):
        with override_settings(INVITATION_CODES_READY=True):
            InvitationCode.objects.create(code='VALID123',blogger='Fixture')
            result = self.finish(self.code(),' valid123 ')
        self.assertEqual(result.status_code,200)
        attribution = RegistrationAttribution.objects.get()
        self.assertEqual(attribution.registered_at,attribution.user.date_joined)
        self.assertEqual(attribution.invitation_code_id,'VALID123')

    def test_transaction_failure_rolls_back_every_registration_row(self):
        from django.db import DatabaseError
        body = self.code()
        with patch('backend.authentication.Membership.objects.create',side_effect=DatabaseError('fixture')):
            self.assertEqual(self.finish(body).status_code,503)
        for model in (get_user_model(),BillingAccount,Membership,LoginIdentity,AccountProfile,RegistrationAttribution):
            self.assertFalse(model.objects.exists(),model)
        self.assertIsNone(AuthFlow.objects.get().consumed_at)
        self.assertEqual(self.finish(body).status_code,200)

    def test_existing_identifier_not_exposed_at_start_and_never_overwritten(self):
        user, account = self.account(password='Canyon!42',identifier='new@example.invalid')
        before = user.password
        body = self.code()
        result = self.finish(body)
        self.assertEqual(result.status_code,422)
        self.assertEqual(result.json()['error']['code'],'REGISTRATION_FAILED')
        user.refresh_from_db()
        self.assertEqual(user.password,before)
        self.assertEqual(BillingAccount.objects.get().pk,account.pk)
        self.assertEqual(get_user_model().objects.count(),1)

    def test_password_login_pending_then_captcha_with_historical_password(self):
        password = '  合作伙伴原密码保留  '+ 'x'*40
        user, _ = self.account(password=password)
        result = self.post('password-login',{'identifier':'original@example.invalid','password':password})
        self.assertEqual(result.status_code,201,result.content)
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        signed = self.prove(result.json()['flow_id'])
        self.assertEqual(signed.status_code,200,signed.content)
        self.assertEqual(signed.json()['user']['id'],str(user.pk))
        self.assertFalse(LoginChallenge.objects.exists())

    def test_pending_login_password_changed_or_deleted_cannot_grant_session(self):
        user, _ = self.account(password='Silver!864')
        flow = self.post('password-login',{'identifier':'original@example.invalid','password':'Silver!864'}).json()['flow_id']
        user.set_password('Canyon!42')
        user.save(update_fields=['password'])
        self.assertEqual(self.prove(flow).status_code,401)
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])

    def test_recovery_sets_legacy_password_preserves_ids_and_revokes_all_sessions_without_login(self):
        user, account = self.account()
        other = Client()
        other.force_login(user)
        other.get('/api/account/sessions')
        unregistered = Client()
        unregistered.force_login(user)
        body = self.code(self.post('recovery/start',{'identifier':'original@example.invalid'}))
        result = self.post('recovery/verify',body)
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['stage'],'password')
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        self.assertFalse(user.has_usable_password())
        result = self.post('recovery/complete',{'flow_id':body['flow_id'],'password':'Canyon!42'})
        self.assertEqual(result.status_code,200,result.content)
        user.refresh_from_db()
        self.assertTrue(user.check_password('Canyon!42'))
        self.assertEqual(user.personal_account.pk,account.pk)
        self.assertEqual(LoginIdentity.objects.get().user_id,user.pk)
        self.assertEqual(Membership.objects.get().account_id,account.pk)
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        self.assertEqual(other.get('/api/account/profile').status_code,401)
        self.assertEqual(unregistered.get('/api/account/profile').status_code,401)
        self.assertEqual(self.post('recovery/complete',{'flow_id':body['flow_id'],'password':'River!864'}).status_code,422)

    def test_unknown_google_only_and_disabled_recovery_cannot_create_password_or_identity(self):
        google = get_user_model().objects.create_user(username='google-only')
        LoginIdentity.objects.create(user=google,method='google',identifier='fixture-subject')
        for identifier in ('missing@example.invalid','google@example.invalid'):
            body = self.code(self.post('recovery/start',{'identifier':identifier}))
            self.assertEqual(self.post('recovery/verify',body).status_code,422)
        google.refresh_from_db()
        self.assertFalse(google.has_usable_password())
        self.assertEqual(LoginIdentity.objects.count(),1)

    def test_csrf_same_origin_methods_and_strict_fields(self):
        self.assertEqual(self.client.post('/api/auth/register/start','{}',content_type='application/json').status_code,403)
        self.assertEqual(self.client.get('/api/auth/register/start').status_code,405)
        self.assertEqual(self.post('register/start',{}).status_code,422)
        self.assertEqual(self.client.post('/api/auth/recovery/start',json.dumps({'identifier':'new@example.invalid'}),
            content_type='application/json',HTTP_X_CSRFTOKEN=self.token,HTTP_ORIGIN='https://attacker.invalid').status_code,403)

    def test_authenticated_settings_reset_stays_original_identity_bound_and_revokes_session(self):
        user, _ = self.account(password='Silver!864')
        self.client.force_login(user)
        self.token = self.client.get('/api/auth/session').json()['csrf_token']
        self.client.get('/api/account/sessions')
        self.assertEqual(self.post('password-challenges',{'identifier':'foreign@example.invalid'}).status_code,403)
        result = self.post('password-challenges',{'identifier':'original@example.invalid'})
        self.assertEqual(result.status_code,201,result.content)
        body = {'challenge_id':result.json()['challenge_id'],
            'code':re.search(r'\b[0-9]{6}\b',mail.outbox[-1].body).group(),'password':'Canyon!42'}
        self.assertEqual(self.post('password-reset',body).status_code,200)
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        user.refresh_from_db()
        self.assertTrue(user.check_password('Canyon!42'))
        self.network.assert_not_called()

    def test_correct_password_cannot_sign_in_without_captcha_configuration(self):
        self.account(password='Silver!864')
        with override_settings(CAPTCHA_APP_ID=''):
            result = self.post('password-login',{'identifier':'original@example.invalid','password':'Silver!864'})
        self.assertEqual(result.status_code,503)
        self.assertFalse(AuthFlow.objects.exists())
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])

    def test_identity_and_ip_send_limits_persist(self):
        with override_settings(AUTH_IDENTIFIER_LIMIT=1,AUTH_RESEND_SECONDS=0):
            body = self.code()
            self.assertEqual(self.post('resend',{'flow_id':body['flow_id']}).status_code,429)
        AuthThrottle.objects.all().delete()
        with override_settings(AUTH_IP_LIMIT=1):
            flow = self.start(identifier='second@example.invalid').json()['flow_id']
            self.assertEqual(self.prove(flow).status_code,429)
        self.assertFalse(get_user_model().objects.exists())

    def test_cleanup_dry_run_then_apply_scrubs_expired_flow_without_account_or_ticket_mutation(self):
        from django.core.management import call_command
        from io import StringIO
        body = self.code()
        flow = AuthFlow.objects.get(pk=body['flow_id'])
        ticket = flow.captcha_digest
        before = AuthThrottle.objects.get(pk=ticket).count
        AuthFlow.objects.filter(pk=flow.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        LoginChallenge.objects.filter(pk=body['challenge_id']).update(expires_at=timezone.now()-timedelta(seconds=1))
        output = StringIO()
        call_command('cleanup_auth_flows',stdout=output)
        self.assertEqual(json.loads(output.getvalue()),{'mode':'dry-run','flows':1,'challenges':1})
        flow.refresh_from_db()
        self.assertTrue(flow.password_hash)
        self.assertTrue(flow.identifier)
        output = StringIO()
        call_command('cleanup_auth_flows','--apply',stdout=output)
        self.assertEqual(json.loads(output.getvalue()),{'mode':'apply','flows':1,'challenges':1})
        flow.refresh_from_db()
        self.assertEqual((flow.password_hash,flow.identifier,flow.first_name,flow.last_name),('','','',''))
        self.assertIsNotNone(flow.consumed_at)
        challenge = LoginChallenge.objects.get(pk=body['challenge_id'])
        self.assertEqual((challenge.identifier,challenge.code_digest),('',''))
        self.assertEqual(AuthThrottle.objects.get(pk=ticket).count,before)
        self.assertFalse(get_user_model().objects.exists())
        self.assertEqual(self.finish(body).status_code,422)
        output = StringIO()
        call_command('cleanup_auth_flows','--apply',stdout=output)
        self.assertEqual(json.loads(output.getvalue()),{'mode':'apply','flows':0,'challenges':0})

    def test_cleanup_preserves_live_flow_and_completed_user_consent_and_rights(self):
        from backend.authentication import cleanup_auth_data
        body = self.code()
        self.assertEqual(cleanup_auth_data(apply=True),{'flows':0,'challenges':0})
        self.assertEqual(self.finish(body).status_code,200)
        user = get_user_model().objects.get()
        password_hash, account = user.password, user.personal_account.pk
        consent = AccountProfile.objects.get().agreement_accepted_at
        self.assertEqual(cleanup_auth_data(apply=True),{'flows':1,'challenges':1})
        user.refresh_from_db()
        self.assertEqual(user.password,password_hash)
        self.assertEqual(user.personal_account.pk,account)
        self.assertEqual(AccountProfile.objects.get().agreement_accepted_at,consent)
        self.assertEqual(LoginIdentity.objects.get().user_id,user.pk)
        self.assertEqual(Membership.objects.get().account_id,account)
        self.assertTrue(self.client.get('/api/auth/session').json()['authenticated'])

    def test_cleanup_cannot_make_submitted_ticket_reusable(self):
        from backend.authentication import cleanup_auth_data
        flow = self.start().json()['flow_id']
        self.network.return_value = {'Response':{'CaptchaCode':9}}
        self.assertEqual(self.prove(flow,ticket='retained-ticket').status_code,422)
        self.assertEqual(self.post('cancel',{'flow_id':flow}).status_code,200)
        cleanup_auth_data(apply=True)
        next_flow = self.start().json()['flow_id']
        self.network.return_value = {'Response':{'CaptchaCode':1}}
        calls = self.network.call_count
        self.assertEqual(self.prove(next_flow,ticket='retained-ticket').status_code,422)
        self.assertEqual(self.network.call_count,calls)

    def test_r1_supplier_latency_past_deadline_cannot_grant_login(self):
        self.account(password='Silver!864')
        flow_id = self.post('password-login',{'identifier':'original@example.invalid','password':'Silver!864'}).json()['flow_id']
        flow = AuthFlow.objects.get(pk=flow_id)
        with patch('backend.authentication.timezone.now',return_value=timezone.now()) as now:
            def accepted_after_expiry(*args,**kwargs):
                now.return_value = flow.expires_at+timedelta(seconds=1)
                return {'Response':{'CaptchaCode':1}}
            self.network.side_effect = accepted_after_expiry
            response = self.prove(flow_id)
            self.assertEqual(response.status_code,422,response.content)
            self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        self.assertFalse(AccountSession.objects.exists())

    def test_r1_accepted_ticket_and_attempt_survive_later_database_failure(self):
        from django.db import DatabaseError
        flow_id = self.start().json()['flow_id']
        ticket = 'persisted-supplier-submission'
        with patch('backend.authentication.LoginChallenge.objects.create',side_effect=DatabaseError('fixture')):
            response = self.prove(flow_id,ticket=ticket)
        self.assertEqual(response.status_code,503,response.content)
        flow = AuthFlow.objects.get(pk=flow_id)
        self.assertEqual(flow.attempts,1)
        self.assertTrue(AuthThrottle.objects.filter(pk=flow.captcha_digest).exists())
        self.assertEqual(self.prove(flow_id,ticket=ticket).status_code,422)
        self.assertEqual(self.network.call_count,1)
        self.assertFalse(LoginChallenge.objects.exists())

    def test_r1_invitation_lock_wait_past_deadline_cannot_create_user(self):
        from backend.invitations import validate_code
        with override_settings(INVITATION_CODES_READY=True):
            InvitationCode.objects.create(code='VALID123',blogger='Fixture')
            body = self.code()
            flow = AuthFlow.objects.get(pk=body['flow_id'])
            with patch('backend.authentication.timezone.now',return_value=timezone.now()) as now:
                def expired_after_lock(value,*,lock=False):
                    result = validate_code(value,lock=lock)
                    if lock:
                        now.return_value = flow.expires_at+timedelta(seconds=1)
                    return result
                with patch('backend.invitations.validate_code',side_effect=expired_after_lock):
                    response = self.finish(body,'VALID123')
                self.assertEqual(response.status_code,422,response.content)
            self.assertFalse(get_user_model().objects.exists())
            self.assertFalse(BillingAccount.objects.exists())
            self.assertFalse(RegistrationAttribution.objects.exists())

    @override_settings(AUTH_IP_LIMIT=0)
    def test_r1_rate_limited_registration_does_not_hash_password(self):
        from django.contrib.auth.hashers import make_password
        with patch('backend.authentication.make_password',wraps=make_password) as hashed:
            self.assertEqual(self.start().status_code,429)
        hashed.assert_not_called()

    def test_r1_terms_and_privacy_urls_are_required_for_registration_readiness(self):
        for field in ('AUTH_REGISTRATION_TERMS_URL','AUTH_REGISTRATION_PRIVACY_URL'):
            for value in ('','http://example.invalid/legal','//evil.invalid/legal','javascript:alert(1)',
                    '/\\evil.invalid/legal','https://user:password@example.invalid/legal'):
                with override_settings(**{field:value}):
                    self.assertEqual(self.start().status_code,503,(field,value))
                    capabilities = self.client.get('/api/auth/session').json()['capabilities']
                    self.assertFalse(capabilities['email_registration'])

    def test_r1_otp_attempt_survives_account_database_failure(self):
        from django.db import DatabaseError
        body = self.code()
        with patch('backend.authentication.BillingAccount.objects.create',side_effect=DatabaseError('fixture')):
            self.assertEqual(self.finish(body).status_code,503)
        self.assertEqual(LoginChallenge.objects.get(pk=body['challenge_id']).attempts,1)
        self.assertFalse(get_user_model().objects.exists())
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])

    def test_r1_legal_urls_are_exposed_only_with_complete_readiness(self):
        with override_settings(AUTH_REGISTRATION_TERMS_URL='https://example.invalid:8443/terms'):
            capabilities = self.client.get('/api/auth/session').json()['capabilities']
            self.assertTrue(capabilities['email_registration'])
            self.assertEqual(capabilities['registration_terms_url'],'https://example.invalid:8443/terms')
            self.assertEqual(capabilities['registration_privacy_url'],'/legal/nonproduction-privacy')
        with override_settings(AUTH_REGISTRATION_PRIVACY_URL=''):
            capabilities = self.client.get('/api/auth/session').json()['capabilities']
            self.assertIsNone(capabilities['registration_terms_url'])
            self.assertIsNone(capabilities['registration_privacy_url'])

    def test_r1_final_login_write_deadline_rolls_back_session_grant(self):
        from backend.models import AuditEvent
        self.account(password='Silver!864')
        flow_id = self.post('password-login',{'identifier':'original@example.invalid','password':'Silver!864'}).json()['flow_id']
        flow = AuthFlow.objects.get(pk=flow_id)
        create = AuditEvent.objects.create
        with patch('backend.authentication.timezone.now',return_value=timezone.now()) as now:
            def expire_after_write(*args,**kwargs):
                event = create(*args,**kwargs)
                now.return_value = flow.expires_at+timedelta(seconds=1)
                return event
            with patch('backend.authentication.AuditEvent.objects.create',side_effect=expire_after_write):
                self.assertEqual(self.prove(flow_id).status_code,422)
            self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        self.assertFalse(AccountSession.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(action='auth.password_verified').exists())
        self.assertEqual(AuthFlow.objects.get(pk=flow_id).attempts,1)

    def test_r1_final_registration_write_deadline_rolls_back_user_and_session(self):
        from backend.models import AuditEvent
        body = self.code()
        flow = AuthFlow.objects.get(pk=body['flow_id'])
        create = AuditEvent.objects.create
        with patch('backend.authentication.timezone.now',return_value=timezone.now()) as now:
            def expire_after_write(*args,**kwargs):
                event = create(*args,**kwargs)
                now.return_value = flow.expires_at+timedelta(seconds=1)
                return event
            with patch('backend.authentication.AuditEvent.objects.create',side_effect=expire_after_write):
                self.assertEqual(self.finish(body).status_code,422)
            self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        self.assertFalse(get_user_model().objects.exists())
        self.assertFalse(AccountSession.objects.exists())
        self.assertEqual(LoginChallenge.objects.get(pk=body['challenge_id']).attempts,1)

    def settings_reset_fixture(self):
        user,_ = self.account(password='Silver!864')
        self.client.force_login(user)
        self.token = self.client.get('/api/auth/session').json()['csrf_token']
        self.assertEqual(self.client.get('/api/account/sessions').status_code,200)
        response = self.post('password-challenges',{'identifier':'original@example.invalid'})
        self.assertEqual(response.status_code,201,response.content)
        return user,{'challenge_id':response.json()['challenge_id'],
            'code':re.search(r'\b[0-9]{6}\b',mail.outbox[-1].body).group(),'password':'Violet!431'}

    def test_r2_settings_reset_deadline_after_audit_rolls_back_all_business_writes(self):
        from backend.models import AuditEvent
        from django.contrib.sessions.models import Session
        user,body = self.settings_reset_fixture()
        before_hash,session_key = user.password,self.client.session.session_key
        item = LoginChallenge.objects.get(pk=body['challenge_id'])
        create = AuditEvent.objects.create
        with patch('backend.authentication.timezone.now',return_value=timezone.now()) as now:
            def expired_after_audit(*args,**kwargs):
                event = create(*args,**kwargs)
                now.return_value = item.expires_at+timedelta(seconds=1)
                return event
            with patch('backend.authentication.AuditEvent.objects.create',side_effect=expired_after_audit):
                response = self.post('password-reset',body)
            self.assertEqual(response.status_code,422,response.content)
        user.refresh_from_db()
        self.assertEqual(user.password,before_hash)
        self.assertTrue(Session.objects.filter(pk=session_key).exists())
        self.assertIsNone(AccountSession.objects.get(session_key=session_key).revoked_at)
        self.assertFalse(AuditEvent.objects.filter(action='auth.password_reset').exists())
        item.refresh_from_db()
        self.assertEqual(item.attempts,1)
        self.assertIsNone(item.consumed_at)

    def test_r2_settings_reset_attempt_survives_business_database_failure(self):
        from django.db import DatabaseError
        from backend.models import AuditEvent
        user,body = self.settings_reset_fixture()
        before_hash = user.password
        with patch('backend.authentication.AuditEvent.objects.create',side_effect=DatabaseError('fixture')):
            self.assertEqual(self.post('password-reset',body).status_code,503)
        item = LoginChallenge.objects.get(pk=body['challenge_id'])
        self.assertEqual(item.attempts,1)
        self.assertIsNone(item.consumed_at)
        user.refresh_from_db()
        self.assertEqual(user.password,before_hash)
        self.assertFalse(AuditEvent.objects.filter(action='auth.password_reset').exists())
        self.assertTrue(self.client.get('/api/auth/session').json()['authenticated'])

    def test_r2_restart_invalidates_flow_before_challenge(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.code()
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.start().status_code,201)
        writes = [query['sql'] for query in queries if query['sql'].startswith('UPDATE ') and
            ('backend_authflow' in query['sql'] or 'backend_loginchallenge' in query['sql'])]
        self.assertTrue(writes[0].startswith('UPDATE "backend_authflow"'),writes[0])
        self.assertTrue(writes[1].startswith('UPDATE "backend_loginchallenge"'),writes[1])
