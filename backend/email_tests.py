"""Provider contracts only: no real credentials or external sends."""
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings, RequestFactory

from backend import views
from backend.apps import deployment_configuration


@override_settings(AUTH_EMAIL_ENABLED=True, AUTH_EMAIL_TRANSPORT='ses',
    SES_SECRET_ID='fixture-id', SES_SECRET_KEY='fixture-key', SES_REGION='ap-hongkong',
    DEFAULT_FROM_EMAIL='sender@example.invalid', SES_TEMPLATE_ID='',
    AUTH_REGISTRATION_SES_TEMPLATE_ID='31', AUTH_PASSWORD_SES_TEMPLATE_ID='21',
    AUTH_PASSWORD_CHANGED_SES_TEMPLATE_ID='41', AUTH_GOOGLE_BIND_SES_TEMPLATE_ID='51',
    ACCOUNT_DELETION_SES_TEMPLATE_ID='19', AUTH_REGISTRATION_ENABLED=True,
    AUTH_REGISTRATION_TERMS_READY=True, AUTH_REGISTRATION_TERMS_VERSION='fixture-v1',
    AUTH_REGISTRATION_TERMS_URL='/fixture-terms', AUTH_REGISTRATION_PRIVACY_URL='/fixture-privacy',
    AUTH_CAPTCHA_ENABLED=True, CAPTCHA_APP_ID='123', CAPTCHA_APP_SECRET_KEY='fixture-app',
    CAPTCHA_SECRET_ID='fixture-id', CAPTCHA_SECRET_KEY='fixture-key',
    ACCOUNT_DELETION_ENABLED=True, ACCOUNT_DELETION_POLICY_VERSION='fixture-reviewed-v1',
    ACCOUNT_DELETION_POLICY_REVIEWED_AT='2026-01-01', ACCOUNT_DELETION_RETENTION_DAYS=13,
    ACCOUNT_DELETION_MARKER_RETENTION_DAYS=17, ACCOUNT_DELETION_MARKER_KEY='fixture-only-' * 6)
class EmailPurposeReadinessTests(SimpleTestCase):
    invalid_ids = (None, True, False, 0, '0', '', 'invalid', '²', '１２', '١٢', '9' * 5000)

    def test_invalid_captcha_id_closes_capabilities_without_exception_or_config_leak(self):
        from django.contrib.auth.models import AnonymousUser
        from backend.authentication import captcha_ready
        for index, value in enumerate(self.invalid_ids):
            with self.subTest(case=index), override_settings(CAPTCHA_APP_ID=value):
                self.assertFalse(captcha_ready())
                request = RequestFactory().get('/api/auth/session')
                request.user, request.session = AnonymousUser(), {}
                data = views.session_data(request)
                for capability in ('captcha', 'password_login', 'password_reset', 'email_registration'):
                    self.assertFalse(data['capabilities'][capability])
                self.assertIsNone(data['capabilities']['captcha_app_id'])
                for private_value in ('fixture-id', 'fixture-key', 'fixture-app'):
                    self.assertNotIn(private_value, str(data))

    def test_invalid_purpose_template_closes_readiness_without_session_failure(self):
        from django.contrib.auth.models import AnonymousUser
        from backend.authentication import registration_ready
        from backend.google_auth import bind_email_ready
        for setting, ready, capability in (
                ('AUTH_REGISTRATION_SES_TEMPLATE_ID', registration_ready, 'email_registration'),
                ('AUTH_PASSWORD_SES_TEMPLATE_ID', views.password_reset_ready, 'password_reset'),
                ('AUTH_GOOGLE_BIND_SES_TEMPLATE_ID', bind_email_ready, None)):
            for index, value in enumerate(self.invalid_ids):
                with self.subTest(setting=setting, case=index), override_settings(**{setting: value}):
                    self.assertFalse(ready())
                    request = RequestFactory().get('/api/auth/session')
                    request.user, request.session = AnonymousUser(), {}
                    data = views.session_data(request)
                    self.assertTrue(data['capabilities']['password_login'])
                    if capability:
                        self.assertFalse(data['capabilities'][capability])
                    for private_value in ('fixture-id', 'fixture-key', 'fixture-app'):
                        self.assertNotIn(private_value, str(data))

    def test_invalid_password_notification_template_never_calls_delivery(self):
        from backend.authentication import _password_notification
        for index, value in enumerate(self.invalid_ids):
            with self.subTest(case=index), override_settings(AUTH_PASSWORD_CHANGED_SES_TEMPLATE_ID=value), \
                    patch('backend.email.send_password_changed') as send, self.assertLogs('sage', level='WARNING') as logs:
                _password_notification('receiver@example.invalid', 'fixture-request')
                send.assert_not_called()
                self.assertIn('password_change_notification_unavailable', logs.output[0])
                self.assertNotIn('receiver', logs.output[0])

    def test_positive_ascii_ids_preserve_existing_purpose_readiness_and_notification(self):
        from backend.authentication import captcha_ready, registration_ready, _password_notification
        from backend.google_auth import bind_email_ready
        for value in ('12', 12, '0012'):
            with self.subTest(value=value), override_settings(CAPTCHA_APP_ID=value,
                    AUTH_REGISTRATION_SES_TEMPLATE_ID=value, AUTH_PASSWORD_SES_TEMPLATE_ID=value,
                    AUTH_GOOGLE_BIND_SES_TEMPLATE_ID=value, AUTH_PASSWORD_CHANGED_SES_TEMPLATE_ID=value), \
                    patch('backend.email.send_password_changed', return_value=1) as send:
                for ready in (captcha_ready, registration_ready, views.password_reset_ready, bind_email_ready):
                    self.assertTrue(ready())
                _password_notification('receiver@example.invalid', 'fixture-request')
                send.assert_called_once_with('receiver@example.invalid')

    def test_explicit_invalid_deletion_template_never_falls_back_to_legacy(self):
        from backend.account import deletion_ready
        for template in (None, True, False, '²', '¹²', '１２', '١٢', '0', 'invalid', '9' * 5000):
            with self.subTest(template_type=type(template).__name__), \
                    override_settings(SES_TEMPLATE_ID='12', ACCOUNT_DELETION_SES_TEMPLATE_ID=template):
                self.assertTrue(views.email_ready())
                self.assertFalse(views.email_ready(template))
                self.assertFalse(deletion_ready())

    def test_five_purpose_configuration_does_not_require_retired_login_template(self):
        from backend.account import deletion_ready
        from backend.authentication import registration_ready
        from backend.google_auth import bind_email_ready
        self.assertFalse(views.email_ready())
        self.assertTrue(registration_ready())
        self.assertTrue(views.password_reset_ready())
        self.assertTrue(bind_email_ready())
        self.assertTrue(deletion_ready())

    def test_deployment_checks_transport_not_retired_login_or_purpose_availability(self):
        self.assertNotIn('sage.E004', {item.id for item in deployment_configuration(None)})
        with override_settings(AUTH_REGISTRATION_SES_TEMPLATE_ID='', AUTH_PASSWORD_SES_TEMPLATE_ID='',
                AUTH_PASSWORD_CHANGED_SES_TEMPLATE_ID='', AUTH_GOOGLE_BIND_SES_TEMPLATE_ID='',
                ACCOUNT_DELETION_SES_TEMPLATE_ID=''):
            self.assertNotIn('sage.E004', {item.id for item in deployment_configuration(None)})
            from backend.account import deletion_ready
            from backend.authentication import registration_ready
            from backend.google_auth import bind_email_ready
            self.assertFalse(registration_ready())
            self.assertFalse(views.password_reset_ready())
            self.assertFalse(bind_email_ready())
            self.assertFalse(deletion_ready())

    def test_missing_deletion_purpose_fails_before_sdk_or_database(self):
        from backend.account import deletion_challenge, deletion_ready
        for legacy in ('', '12'):
            for template in ('', '0', 'invalid'):
                with self.subTest(legacy_present=bool(legacy), template=template), \
                        override_settings(SES_TEMPLATE_ID=legacy, ACCOUNT_DELETION_SES_TEMPLATE_ID=template), \
                        patch('backend.email.SesClient') as client:
                    self.assertFalse(deletion_ready())
                    request = RequestFactory().post('/api/account/deletion-challenges', '{}',
                        content_type='application/json')
                    request.request_id = 'fixture-request'
                    self.assertEqual(deletion_challenge(request).status_code, 503)
                    client.assert_not_called()

    def test_bad_transport_still_rejects_deployment_and_all_purposes(self):
        from backend.account import deletion_ready
        from backend.authentication import registration_ready
        from backend.google_auth import bind_email_ready
        for changes in ({'SES_SECRET_ID':''}, {'SES_SECRET_KEY':''}, {'SES_REGION':'invalid'},
                {'DEFAULT_FROM_EMAIL':'invalid'}):
            with self.subTest(fields=tuple(changes)), override_settings(**changes):
                self.assertIn('sage.E004', {item.id for item in deployment_configuration(None)})
                for ready in (registration_ready, views.password_reset_ready, bind_email_ready, deletion_ready):
                    self.assertFalse(ready())

    def test_deletion_policy_gates_remain_independent(self):
        from backend.account import deletion_ready
        for changes in ({'ACCOUNT_DELETION_ENABLED':False}, {'ACCOUNT_DELETION_POLICY_VERSION':''},
                {'ACCOUNT_DELETION_POLICY_REVIEWED_AT':'invalid'}, {'ACCOUNT_DELETION_RETENTION_DAYS':0},
                {'ACCOUNT_DELETION_MARKER_RETENTION_DAYS':0}, {'ACCOUNT_DELETION_MARKER_KEY':''},
                {'AUTH_EMAIL_ENABLED':False}):
            with self.subTest(fields=tuple(changes)), override_settings(**changes):
                self.assertFalse(deletion_ready())


class EmailTransportTests(SimpleTestCase):
    @override_settings(SES_SECRET_ID='fixture-id', SES_SECRET_KEY='fixture-key',
        SES_REGION='ap-hongkong', SES_TEMPLATE_ID='12', AUTH_REGISTRATION_SES_TEMPLATE_ID='31',
        AUTH_PASSWORD_CHANGED_SES_TEMPLATE_ID='41', DEFAULT_FROM_EMAIL='sender@example.invalid')
    def test_registration_and_password_change_use_distinct_templates(self):
        from backend.email import send_registration_code, send_password_changed
        with patch('backend.email.SesClient') as client:
            client.return_value.SendEmail.return_value = Mock(MessageId='fixture-message')
            self.assertEqual(send_registration_code('receiver@example.invalid','123456'),1)
            request = client.return_value.SendEmail.call_args.args[0]
            self.assertEqual(request.Template.TemplateID,31)
            self.assertIn('Registration',request.Subject)
            self.assertEqual(send_password_changed('receiver@example.invalid'),1)
            request = client.return_value.SendEmail.call_args.args[0]
            self.assertEqual(request.Template.TemplateID,41)
            self.assertEqual(request.Template.TemplateData,'{}')
            self.assertIn('Password changed',request.Subject)

    def test_password_change_notification_absent_configuration_is_observable_no_send(self):
        from backend.authentication import _password_notification
        with patch('backend.email.send_password_changed') as send, self.assertLogs('sage',level='WARNING') as logs:
            _password_notification('receiver@example.invalid','fixture-request')
        send.assert_not_called()
        self.assertIn('password_change_notification_unavailable',logs.output[0])
        self.assertNotIn('receiver',logs.output[0])

    @override_settings(AUTH_EMAIL_ENABLED=True, AUTH_EMAIL_TRANSPORT='ses',
        SES_SECRET_ID='fixture-id',SES_SECRET_KEY='fixture-key',SES_REGION='ap-hongkong',
        DEFAULT_FROM_EMAIL='sender@example.invalid',AUTH_PASSWORD_SES_TEMPLATE_ID='21',
        AUTH_PASSWORD_CHANGED_SES_TEMPLATE_ID='41')
    def test_password_change_notification_failure_is_observable(self):
        from backend.authentication import _password_notification
        with patch('backend.email.send_password_changed',side_effect=OSError('private-provider-body')), self.assertLogs('sage',level='WARNING') as logs:
            _password_notification('receiver@example.invalid','fixture-request')
        self.assertIn('password_change_notification_failed',logs.output[0])
        self.assertNotIn('private-provider-body',logs.output[0])

    @override_settings(SES_SECRET_ID='fixture-id', SES_SECRET_KEY='fixture-key',
        SES_REGION='ap-hongkong', SES_TEMPLATE_ID='12', AUTH_PASSWORD_SES_TEMPLATE_ID='21',
        DEFAULT_FROM_EMAIL='sender@example.invalid')
    def test_password_code_uses_its_own_template(self):
        from backend.email import send_password_code
        with patch('backend.email.SesClient') as client:
            client.return_value.SendEmail.return_value = Mock(MessageId='fixture-message')
            self.assertEqual(send_password_code('receiver@example.invalid', '123456'), 1)
            request = client.return_value.SendEmail.call_args.args[0]
            self.assertEqual(request.Template.TemplateID, 21)
            self.assertIn('Password reset', request.Subject)
            self.assertEqual(client.return_value.SendEmail.call_count, 1)

    @override_settings(AUTH_EMAIL_ENABLED=True, AUTH_EMAIL_TRANSPORT='ses',
        SES_SECRET_ID='fixture-id', SES_SECRET_KEY='fixture-key', SES_REGION='ap-hongkong',
        SES_TEMPLATE_ID='12', AUTH_PASSWORD_SES_TEMPLATE_ID='', DEFAULT_FROM_EMAIL='sender@example.invalid')
    def test_password_reset_does_not_reuse_login_template(self):
        self.assertTrue(views.email_ready())
        self.assertFalse(views.password_reset_ready())

    @override_settings(SES_SECRET_ID='fixture-id', SES_SECRET_KEY='fixture-key',
        SES_REGION='ap-hongkong', SES_TEMPLATE_ID='12', ACCOUNT_DELETION_SES_TEMPLATE_ID='19',
        DEFAULT_FROM_EMAIL='sender@example.invalid')
    def test_deletion_code_uses_separate_template_and_explicit_subject(self):
        from backend.email import send_deletion_code
        with patch('backend.email.SesClient') as client:
            client.return_value.SendEmail.return_value = Mock(MessageId='fixture-message')
            self.assertEqual(send_deletion_code('receiver@example.invalid', '123456'), 1)
            request = client.return_value.SendEmail.call_args.args[0]
            self.assertEqual(request.Template.TemplateID, 19)
            self.assertIn('注销', request.Subject)
            self.assertNotIn('Sign-in', request.Subject)
            self.assertEqual(client.return_value.SendEmail.call_count, 1)

    @override_settings(SES_SECRET_ID='fixture-id', SES_SECRET_KEY='fixture-key',
        SES_REGION='ap-hongkong', SES_TEMPLATE_ID='12', DEFAULT_FROM_EMAIL='sender@example.invalid')
    def test_provider_rejection_and_missing_message_are_not_success(self):
        from backend.email import send_login_code
        from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
        with patch('backend.email.SesClient') as client:
            client.return_value.SendEmail.side_effect = TencentCloudSDKException(
                'RequestLimitExceeded', 'private-fixture-body')
            with self.assertRaisesMessage(OSError, 'EMAIL_DELIVERY_FAILED'):
                send_login_code('receiver@example.invalid', '123456')
            self.assertEqual(client.return_value.SendEmail.call_count, 1)
            client.return_value.SendEmail.side_effect = None
            client.return_value.SendEmail.return_value = Mock(MessageId=None)
            self.assertEqual(send_login_code('receiver@example.invalid', '123456'), 0)

    @override_settings(AUTH_EMAIL_ENABLED=True, AUTH_EMAIL_TRANSPORT='ses',
        SES_SECRET_ID='fixture-id', SES_SECRET_KEY='fixture-key', SES_REGION='ap-hongkong',
        SES_TEMPLATE_ID='12', DEFAULT_FROM_EMAIL='sender@example.invalid', EMAIL_HOST='')
    def test_ses_template_and_timeout_without_retry_or_logging_pii(self):
        self.assertTrue(views.email_ready())
        from backend.email import send_login_code
        with patch('backend.email.SesClient') as client:
            client.return_value.SendEmail.return_value = Mock(MessageId='fixture-message')
            self.assertEqual(send_login_code('receiver@example.invalid', '123456'), 1)
            args = client.call_args.args
            self.assertEqual(args[1], 'ap-hongkong')
            self.assertEqual(args[2].httpProfile.reqTimeout, 10)
            self.assertIsNone(args[2].retryer)
            request = client.return_value.SendEmail.call_args.args[0]
            self.assertEqual(request.Template.TemplateID, 12)
            self.assertEqual(request.Template.TemplateData, '{"code": "123456"}')
            self.assertEqual(request.TriggerType, 1)
            self.assertEqual(request.Destination, ['receiver@example.invalid'])
            self.assertEqual(client.return_value.SendEmail.call_count, 1)

    @override_settings(AUTH_EMAIL_ENABLED=True, AUTH_EMAIL_TRANSPORT='ses',
        SES_SECRET_ID='', SES_SECRET_KEY='', SES_REGION='ap-hongkong',
        SES_TEMPLATE_ID='12', DEFAULT_FROM_EMAIL='sender@example.invalid')
    def test_incomplete_ses_is_not_ready(self):
        self.assertFalse(views.email_ready())
        self.assertIn('sage.E004', {x.id for x in deployment_configuration(None)})

    @override_settings(AUTH_EMAIL_ENABLED=True, AUTH_EMAIL_TRANSPORT='unknown', EMAIL_HOST='fixture',
        DEFAULT_FROM_EMAIL='sender@example.invalid')
    def test_unknown_transport_does_not_fall_back_to_smtp(self):
        self.assertFalse(views.email_ready())

    @override_settings(TRUSTED_PROXY_IPS=('127.0.0.1',))
    def test_only_exact_proxy_peer_can_supply_single_client_ip(self):
        from backend.http import client_ip
        factory = RequestFactory()
        self.assertEqual(client_ip(factory.get('/', REMOTE_ADDR='198.51.100.1',
            HTTP_X_REAL_IP='192.0.2.1')), '198.51.100.1')
        self.assertEqual(client_ip(factory.get('/', REMOTE_ADDR='127.0.0.1',
            HTTP_X_REAL_IP='192.0.2.1')), '192.0.2.1')
        self.assertEqual(client_ip(factory.get('/', REMOTE_ADDR='127.0.0.1',
            HTTP_X_REAL_IP='192.0.2.1,192.0.2.2')), '127.0.0.1')
