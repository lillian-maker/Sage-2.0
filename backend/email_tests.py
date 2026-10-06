"""Provider contracts only: no real credentials or external sends."""
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings, RequestFactory

from backend import views
from backend.apps import deployment_configuration


class EmailTransportTests(SimpleTestCase):
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
