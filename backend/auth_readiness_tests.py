"""Configuration-only launch checks; no database, browser or provider requests."""
import io
import json
from unittest.mock import patch

from django.core.management import call_command, CommandError
from django.test import SimpleTestCase, override_settings


@override_settings(
    ALLOWED_HOSTS=['sage.example.invalid'], AUTH_CAPTCHA_ENABLED=True,
    CAPTCHA_APP_ID='123', CAPTCHA_APP_SECRET_KEY='fixture-app-secret',
    CAPTCHA_SECRET_ID='fixture-captcha-id', CAPTCHA_SECRET_KEY='fixture-captcha-secret',
    AUTH_EMAIL_ENABLED=True, AUTH_EMAIL_TRANSPORT='ses',
    SES_SECRET_ID='fixture-ses-id', SES_SECRET_KEY='fixture-ses-secret',
    SES_REGION='ap-hongkong', DEFAULT_FROM_EMAIL='sender@example.invalid',
    SES_TEMPLATE_ID='', AUTH_REGISTRATION_SES_TEMPLATE_ID='31',
    AUTH_PASSWORD_SES_TEMPLATE_ID='32', AUTH_PASSWORD_CHANGED_SES_TEMPLATE_ID='33',
    AUTH_GOOGLE_BIND_SES_TEMPLATE_ID='34', AUTH_REGISTRATION_ENABLED=True,
    AUTH_REGISTRATION_TERMS_READY=True, AUTH_REGISTRATION_TERMS_VERSION='fixture-v1',
    AUTH_REGISTRATION_TERMS_URL='/fixture-terms', AUTH_REGISTRATION_PRIVACY_URL='/fixture-privacy',
    AUTH_GOOGLE_ENABLED=True, GOOGLE_BRAND_READY=True,
    GOOGLE_CLIENT_ID='fixture.apps.googleusercontent.com', GOOGLE_CLIENT_SECRET='fixture-google-secret',
    GOOGLE_REDIRECT_URI='https://sage.example.invalid/api/auth/google/callback',
    AUTH_PHONE_ENABLED=False, ACCOUNT_DELETION_ENABLED=False, BILLING_SALES_ENABLED=False,
)
class AuthReadinessTests(SimpleTestCase):
    def report(self, **options):
        output = io.StringIO()
        with patch('requests.sessions.Session.request', side_effect=AssertionError('No HTTP allowed')), \
                patch('backend.email.SesClient.SendEmail', side_effect=AssertionError('No email allowed')), \
                patch('backend.authentication.CommonClient.call_json', side_effect=AssertionError('No CAPTCHA allowed')), \
                patch('socket.create_connection', side_effect=AssertionError('No socket allowed')), \
                patch('socket.socket.connect', side_effect=AssertionError('No socket allowed')), \
                patch('socket.getaddrinfo', side_effect=AssertionError('No DNS allowed')), \
                patch('django.db.backends.base.base.BaseDatabaseWrapper.connect', side_effect=AssertionError('No DB allowed')), \
                patch('django.db.backends.base.base.BaseDatabaseWrapper.cursor', side_effect=AssertionError('No DB allowed')):
            call_command('check_auth_readiness', stdout=output, **options)
        return json.loads(output.getvalue())

    def test_complete_config_is_not_real_delivery_or_legal_approval_evidence(self):
        report = self.report(strict=True)
        self.assertTrue(report['configuration_ready'])
        self.assertEqual(report['evidence'], 'configuration_only')
        self.assertFalse(report['ready_for_public_launch'])
        self.assertEqual(report['external_requests'], 0)
        self.assertFalse(report['database_access'])
        self.assertEqual(report['blockers'], [])

    def test_missing_registration_template_is_a_specific_blocker(self):
        with override_settings(AUTH_REGISTRATION_SES_TEMPLATE_ID=''):
            report = self.report()
        self.assertEqual(report['blockers'], ['email_registration'])
        self.assertTrue(report['checks']['password_recovery'])
        self.assertTrue(report['checks']['google_login'])

    def test_missing_purpose_templates_fail_only_their_dependent_checks(self):
        for setting, blockers in (
                ('AUTH_PASSWORD_SES_TEMPLATE_ID', ['password_recovery', 'password_change_notification']),
                ('AUTH_PASSWORD_CHANGED_SES_TEMPLATE_ID', ['password_change_notification']),
                ('AUTH_GOOGLE_BIND_SES_TEMPLATE_ID', ['google_bind_email'])):
            with self.subTest(setting=setting), override_settings(**{setting: ''}):
                self.assertEqual(self.report()['blockers'], blockers)

    def test_closed_gates_override_otherwise_complete_configuration(self):
        for setting, blockers in (
                ('AUTH_GOOGLE_ENABLED', ['google_login', 'google_registration', 'google_callback_host']),
                ('AUTH_REGISTRATION_ENABLED', ['legal_approval_declared', 'email_registration', 'google_registration']),
                ('AUTH_EMAIL_ENABLED', ['email_transport', 'email_registration', 'password_recovery',
                                        'password_change_notification', 'google_bind_email']),
                ('AUTH_CAPTCHA_ENABLED', ['captcha', 'email_registration', 'password_login', 'password_recovery'])):
            with self.subTest(setting=setting), override_settings(**{setting: False}):
                self.assertEqual(self.report()['blockers'], blockers)

    def test_strict_failure_does_not_poison_a_later_complete_check(self):
        with override_settings(GOOGLE_REDIRECT_URI='https://other.example.invalid/api/auth/google/callback'):
            with self.assertRaisesMessage(CommandError, 'Authentication configuration is incomplete'):
                self.report(strict=True)
        self.assertEqual(self.report(strict=True)['blockers'], [])

    def test_strict_mode_fails_without_echoing_configuration(self):
        output = io.StringIO()
        with override_settings(GOOGLE_CLIENT_SECRET=''), self.assertRaisesMessage(
                CommandError, 'Authentication configuration is incomplete'):
            call_command('check_auth_readiness', strict=True, stdout=output)
        report = json.loads(output.getvalue())
        self.assertIn('google_login', report['blockers'])
        self.assertFalse(report['configuration_ready'])

    def test_callback_must_match_exact_allowed_host(self):
        for uri in ('https://other.example.invalid/api/auth/google/callback',
                    'https://sage.example.invalid.attacker.invalid/api/auth/google/callback',
                    'https://[bad/api/auth/google/callback'):
            with self.subTest(uri=uri), override_settings(GOOGLE_REDIRECT_URI=uri):
                report = self.report()
                self.assertIn('google_callback_host', report['blockers'])

    def test_declaration_and_reachable_approved_legal_documents_are_distinct(self):
        with override_settings(AUTH_REGISTRATION_TERMS_READY=False):
            report = self.report()
        self.assertIn('legal_approval_declared', report['blockers'])
        self.assertIn('email_registration', report['blockers'])
        self.assertIn('google_registration', report['blockers'])
        self.assertTrue(report['checks']['google_login'])
        self.assertIn('legal_content_and_approval', report['unverified'])

    def test_malformed_numeric_config_is_reported_not_raised(self):
        for value in ('²', '１２', '9' * 5000, None, True):
            with self.subTest(kind=type(value).__name__), override_settings(
                    CAPTCHA_APP_ID=value, AUTH_REGISTRATION_SES_TEMPLATE_ID=value,
                    AUTH_PASSWORD_SES_TEMPLATE_ID=value, AUTH_GOOGLE_BIND_SES_TEMPLATE_ID=value,
                    AUTH_PASSWORD_CHANGED_SES_TEMPLATE_ID=value):
                report = self.report()
                self.assertFalse(report['configuration_ready'])
                self.assertIn('captcha', report['blockers'])
                self.assertIn('password_change_notification', report['blockers'])

    def test_report_contains_no_credentials_addresses_or_config_values(self):
        output = json.dumps(self.report())
        for value in ('fixture', 'example.invalid', 'sender@', 'captcha-secret', 'google-secret'):
            self.assertNotIn(value, output)
        self.assertNotIn('sms', output)
        self.assertNotIn('account_deletion', output)

    def test_smtp_cannot_claim_ses_password_change_notice_is_available(self):
        with override_settings(AUTH_EMAIL_TRANSPORT='smtp', EMAIL_HOST='fixture.invalid'):
            report = self.report()
        self.assertIn('password_change_notification', report['blockers'])
