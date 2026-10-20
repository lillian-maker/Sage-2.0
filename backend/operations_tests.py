"""Recovery age and fault notification decisions; all network calls are mocked."""
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from .operations import backup_status, site_status, billing_status, send_fault_alert


class OperationsTests(SimpleTestCase):
    def setUp(self):
        self.now = timezone.now()
        self.manifest = {'source_region': 'ap-shanghai', 'destination_region': 'ap-guangzhou',
            'destination_country': 'CN', 'retention_days': 30,
            'recoverable_through': (self.now-timedelta(minutes=14)).isoformat(),
            'restore_verified_at': (self.now-timedelta(days=1)).isoformat()}

    def test_recovery_point_boundary_and_clock_skew(self):
        for seconds, expected in ((899, True), (900, True), (901, False), (-1, False)):
            data = {**self.manifest, 'recoverable_through': (self.now-timedelta(seconds=seconds)).isoformat()}
            self.assertEqual(backup_status(data, now=self.now)['ok'], expected)

    def test_separate_mainland_region_and_thirty_day_retention_are_required(self):
        for values in ({'destination_region': 'ap-shanghai'}, {'destination_country': 'US'},
                {'retention_days': 7}, {'retention_days': True}, {'source_region': ''}):
            self.assertFalse(backup_status({**self.manifest, **values}, now=self.now)['ok'])
        self.assertTrue(backup_status(self.manifest, now=self.now)['ok'])

    def test_missing_unknown_time_and_unverified_restore_fail(self):
        for values in (None, {}, {**self.manifest, 'restore_verified_at': ''},
                {**self.manifest, 'recoverable_through': '2026-10-16T12:00:00'},
                {**self.manifest, 'restore_verified_at': (self.now-timedelta(days=31)).isoformat()}):
            self.assertFalse(backup_status(values, now=self.now)['ok'])

    def test_retention_must_match_approved_thirty_days(self):
        for days, code in ((29, 'BACKUP_RETENTION_TOO_SHORT'),
                (30, 'BACKUP_FRESH'), (31, 'BACKUP_RETENTION_TOO_LONG'),
                (True, 'BACKUP_RETENTION_TOO_SHORT'), (False, 'BACKUP_RETENTION_TOO_SHORT')):
            with self.subTest(retention_days=days):
                result = backup_status({**self.manifest, 'retention_days': days}, now=self.now)
                self.assertEqual(result['ok'], code == 'BACKUP_FRESH')
                self.assertEqual(result['code'], code)

    def test_probe_rejects_redirect_html_and_broken_tls(self):
        with patch('backend.operations.build_opener') as opener:
            opener.return_value.open.side_effect = OSError
            self.assertFalse(site_status('https://site.example.invalid')['ok'])
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.geturl.return_value = 'https://site.example.invalid/health/ready'
        response.read.return_value = b'{"status":"ready"}'
        with patch('backend.operations.build_opener') as opener:
            opener.return_value.open.return_value = response
            self.assertTrue(site_status('https://site.example.invalid')['ok'])
            response.geturl.return_value = 'https://other.example.invalid/login'
            self.assertFalse(site_status('https://site.example.invalid')['ok'])
            response.geturl.return_value = 'https://site.example.invalid/health/ready'
            response.read.return_value = b'<html>login</html>'
            self.assertFalse(site_status('https://site.example.invalid')['ok'])

    def test_no_plain_http_credentials_or_query_probe(self):
        with patch('backend.operations.build_opener') as network:
            for origin in ('http://site.example.invalid', 'https://user:secret@site.example.invalid',
                    'https://site.example.invalid/?token=fixture', 'https://site.example.invalid:bad'):
                self.assertFalse(site_status(origin)['ok'])
            network.assert_not_called()

    def test_alert_disabled_is_not_reported_as_sent(self):
        with self.assertRaisesRegex(ValueError, 'OPERATIONS_ALERTS_DISABLED'):
            send_fault_alert('SITE_UNAVAILABLE', serious=True)

    def test_financial_issue_or_database_failure_never_reports_clear(self):
        from django.db import DatabaseError
        with patch('backend.commerce.models.BillingIssue.objects.exclude') as query:
            query.return_value.count.return_value = 2
            self.assertEqual(billing_status(), {'ok': False, 'code': 'BILLING_ISSUES_OPEN', 'open_issues': 2})
            query.side_effect = DatabaseError
            self.assertEqual(billing_status()['code'], 'BILLING_CHECK_UNAVAILABLE')

    @override_settings(OPERATIONS_ALERTS_ENABLED=True,
        OPERATIONS_EMAIL_TEMPLATE_APPROVED=True, OPERATIONS_EMAIL_TEMPLATE_ID='123',
        OPERATIONS_EMAIL_RECIPIENT='fixture@example.invalid', OPERATIONS_SMS_TEMPLATE_APPROVED=True,
        OPERATIONS_SMS_TEMPLATE_ID='124', OPERATIONS_SMS_RECIPIENT='+8613800138000')
    def test_serious_alerts_include_sms_even_at_night_and_unknown_stays_unknown(self):
        with patch('backend.email.send_notice', return_value='fixture-mail') as email, \
                patch('backend.sms.send_notice', side_effect=OSError) as sms:
            self.assertEqual(send_fault_alert('SITE_UNAVAILABLE', serious=True), {'email': 'sent', 'sms': 'unknown'})
            self.assertEqual(send_fault_alert('GENERAL_WARNING', serious=False), {'email': 'sent'})
            self.assertEqual(sms.call_count, 1)
            self.assertEqual(email.call_count, 2)
