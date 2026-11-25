"""Offline tests with generated keys and temporary files only."""
import json
import io
import logging
import os
import runpy
import tempfile
import traceback
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import django
from django.test import override_settings
from django.views.debug import ExceptionReporter

from backend.alipay_credentials import load_alipay_credentials


class AlipayCredentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.config = {'enabled': True, 'app_id': '2026000000000001',
            'seller_id': '2088000000000001', 'merchant_entity': 'fixture',
            'private_key': key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption()).decode('ascii'),
            'public_key': key.public_key().public_bytes(serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo).decode('ascii'),
            'payment_ttl_seconds': 900, 'notify_origin': 'https://example.test'}

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'credentials.json'
        self.write(self.config)

    def write(self, config):
        self.path.write_text(json.dumps(config), encoding='utf-8')
        self.path.chmod(0o400)

    def assert_rejected(self, path=None):
        try:
            load_alipay_credentials(path or self.path)
        except ValueError as error:
            report = ''.join(traceback.format_exception(error))
            self.assertEqual(str(error), 'Alipay credential file is invalid, unreadable or incomplete.')
            self.assertIsNone(error.__context__)
            self.assertNotIn(self.config['private_key'], report)
            self.assertNotIn('synthetic-secret-marker', report)
        else:
            self.fail('Invalid credentials were accepted')

    def test_loads_generated_keys_without_mutating_environment(self):
        environment = dict(os.environ)
        self.assertEqual(load_alipay_credentials(self.path), self.config)
        self.assertEqual(dict(os.environ), environment)

    def test_missing_symlink_and_broad_permissions_fail_closed(self):
        self.assert_rejected(self.path.parent / 'missing')
        symlink = self.path.parent / 'link'
        symlink.symlink_to(self.path)
        self.assert_rejected(symlink)
        self.path.chmod(0o440)
        self.assert_rejected()
        self.path.chmod(0o400)
        with patch('backend.alipay_credentials.os.geteuid', return_value=os.geteuid()+1):
            self.assert_rejected()

    def test_bad_schema_and_keys_fail_closed(self):
        for change in ({'private_key': 'synthetic-secret-marker'}, {'seller_id': ''},
                       {'enabled': False}, {'payment_ttl_seconds': True},
                       {'notify_origin': 'http://example.test'},
                       {'unexpected': 'synthetic-secret-marker'}):
            with self.subTest(fields=tuple(change)):
                self.path.chmod(0o600)
                self.write({**self.config, **change})
                self.assert_rejected()

    def test_malformed_duplicate_and_oversized_input_is_not_reported(self):
        for content in ('{"private_key":"synthetic-secret-marker",',
                        '{"enabled":true,"enabled":false}', 'x' * 32769):
            with self.subTest(length=len(content)):
                self.path.chmod(0o600)
                self.path.write_text(content, encoding='utf-8')
                self.path.chmod(0o400)
                self.assert_rejected()

    def test_startup_only_loads_explicit_production_configuration(self):
        settings_file = str(Path(__file__).with_name('settings.py'))
        for environment, enabled in (('production', ''), ('test', '1'), ('development', '1')):
            with self.subTest(environment=environment), patch.dict(os.environ, {
                    'SAGE_ENV': environment, 'SAGE_SECRET_KEY': 'x' * 64,
                    'SAGE_ALLOWED_HOSTS': 'example.test', 'SAGE_DB_NAME': 'fixture',
                    'SAGE_ALIPAY_CREDENTIALS_ENABLED': enabled}, clear=True), patch(
                    'backend.alipay_credentials.load_alipay_credentials') as load:
                result = runpy.run_path(settings_file)
                load.assert_not_called()
                self.assertEqual(result['BILLING_ALIPAY_LIVE'], {})
                self.assertFalse(result['BILLING_ALIPAY_LIVE_ENABLED'])
                self.assertFalse(result['BILLING_SALES_ENABLED'])
                self.assertEqual(result['BILLING_ALIPAY_SANDBOX'], {})
                self.assertEqual(result['BILLING_WECHAT_NATIVE'], {})

        with patch.dict(os.environ, {
                'SAGE_ENV': 'production', 'SAGE_SECRET_KEY': 'x' * 64,
                'SAGE_ALLOWED_HOSTS': 'example.test', 'SAGE_DB_NAME': 'fixture',
                'SAGE_ALIPAY_CREDENTIALS_ENABLED': '1'}, clear=True), patch(
                'backend.alipay_credentials.load_alipay_credentials',
                side_effect=lambda path: load_alipay_credentials(self.path)) as load:
            result = runpy.run_path(settings_file)
            load.assert_called_once_with('/run/secrets/alipay_live.json')
            self.assertTrue(result['BILLING_ALIPAY_LIVE'] == self.config)
            self.assertFalse(result['BILLING_ALIPAY_LIVE_ENABLED'])
            self.assertFalse(result['BILLING_ALIPAY_LIVE_REFUND_ENABLED'])
            self.assertFalse(result['BILLING_SALES_ENABLED'])
            self.assertEqual(result['BILLING_ALIPAY_SANDBOX'], {})
            self.assertEqual(result['BILLING_WECHAT_NATIVE'], {})

    def test_production_startup_fails_closed_on_bad_mount(self):
        settings_file = str(Path(__file__).with_name('settings.py'))
        with patch.dict(os.environ, {
                'SAGE_ENV': 'production', 'SAGE_SECRET_KEY': 'x' * 64,
                'SAGE_ALLOWED_HOSTS': 'example.test', 'SAGE_DB_NAME': 'fixture',
                'SAGE_ALIPAY_CREDENTIALS_ENABLED': '1'}, clear=True), patch(
                'backend.alipay_credentials.load_alipay_credentials',
                side_effect=lambda path: load_alipay_credentials(self.path)):
            self.path.chmod(0o440)
            with self.assertRaisesRegex(ValueError, '^Alipay credential file is invalid, unreadable or incomplete\\.$'):
                runpy.run_path(settings_file)
            self.path.chmod(0o600)
            self.path.write_text('{"synthetic":', encoding='utf-8')
            self.path.chmod(0o400)
            with self.assertRaises(ValueError) as raised:
                runpy.run_path(settings_file)
            self.assertIsNone(raised.exception.__context__)
            self.path.unlink()
            with self.assertRaises(ValueError):
                runpy.run_path(settings_file)

    def test_default_django_error_report_hides_settings_and_parser_locals(self):
        django.setup()
        self.path.chmod(0o600)
        self.path.write_text('{"private_key":"' + ('synthetic' + '-secret-marker') + '",', encoding='utf-8')
        self.path.chmod(0o400)
        with override_settings(DEBUG=False, BILLING_ALIPAY_LIVE=self.config):
            try:
                load_alipay_credentials(self.path)
            except ValueError as error:
                reporter = ExceptionReporter(None, type(error), error, error.__traceback__)
                reports = [reporter.get_traceback_text(), reporter.get_traceback_html()]
                self.assertEqual(reporter.filter.get_safe_settings()['BILLING_ALIPAY_LIVE']['private_key'],
                                 reporter.filter.cleansed_substitute)
            else:
                self.fail('Invalid credentials were accepted')
        for report in reports:
            self.assertFalse(self.config['private_key'] in report, 'Private key appeared in a Django error report')
            self.assertFalse(self.config['private_key'].splitlines()[1] in report,
                             'Private key material appeared in a Django error report')
            self.assertFalse(('synthetic' + '-secret-marker') in report, 'Parser data appeared in a Django error report')

    def test_payment_error_report_hides_passed_private_key(self):
        django.setup()
        from backend import payments
        with override_settings(DEBUG=False, ENVIRONMENT='test'), patch.object(
                payments, 'build_alipay_request', side_effect=RuntimeError('synthetic failure')):
            try:
                payments._alipay_sandbox_call(self.config, 'query', '0' * 32)
            except Exception as error:
                reporter = ExceptionReporter(None, type(error), error, error.__traceback__)
                report = reporter.get_traceback_html()
            else:
                self.fail('Synthetic failure did not propagate')
        self.assertFalse(self.config['private_key'].splitlines()[1] in report,
                         'Private key material appeared in a payment error report')

    def test_payment_dispatch_error_report_hides_loaded_configuration(self):
        django.setup()
        from backend import payments
        with (override_settings(DEBUG=False, ENVIRONMENT='production'),
              patch.object(payments.Order.objects, 'select_related') as select,
              patch.object(payments, '_alipay_configuration', return_value=self.config),
              patch('backend.commerce.billing.claim_payment_dispatch', side_effect=RuntimeError('synthetic failure'))):
            select.return_value.get.return_value = SimpleNamespace(snapshot={'channel': 'alipay'})
            try:
                payments.finish_payment(None, {'account': object(), 'payment_dispatch': 'fixture'})
            except Exception as error:
                report = ExceptionReporter(None, type(error), error, error.__traceback__).get_traceback_html()
            else:
                self.fail('Synthetic failure did not propagate')
        self.assertFalse(self.config['private_key'].splitlines()[1] in report,
                         'Private key material appeared in a dispatch error report')

    def test_payment_error_report_hides_key_in_exception_message(self):
        django.setup()
        from backend import payments
        cases = (
            ('backend.payments.build_alipay_request', 'ALIPAY_QUERY_UNKNOWN'),
            ('backend.payments.sign_with_rsa2', 'ALIPAY_SIGNING_FAILED'),
            ('requests.Session.post', 'ALIPAY_QUERY_UNKNOWN'),
        )
        for target, expected in cases:
            with self.subTest(target=target):
                output = io.StringIO()
                handler = logging.StreamHandler(output)
                logging.getLogger().addHandler(handler)
                logging.getLogger('sage').addHandler(handler)
                try:
                    with override_settings(DEBUG=False, ENVIRONMENT='test'), patch(
                            target, side_effect=RuntimeError(self.config['private_key'])):
                        try:
                            payments._alipay_sandbox_call(self.config, 'query', '0' * 32)
                        except Exception as error:
                            self.assertEqual(str(error), expected)
                            reporter = ExceptionReporter(None, type(error), error, error.__traceback__)
                            reports = [reporter.get_traceback_html(), reporter.get_traceback_text(),
                                       ''.join(traceback.format_exception(error))]
                            current = error
                            while current is not None:
                                self.assertFalse(self.config['private_key'].splitlines()[1] in str(current),
                                                 'Private key material remained in exception chain')
                                current = current.__cause__ or current.__context__
                        else:
                            self.fail('Synthetic failure did not propagate')
                finally:
                    logging.getLogger().removeHandler(handler)
                    logging.getLogger('sage').removeHandler(handler)
                reports.append(output.getvalue())
                for report in reports:
                    self.assertFalse(self.config['private_key'].splitlines()[1] in report,
                                     'Private key material appeared in an error report or log')

    def test_dispatch_db_exception_does_not_expose_key(self):
        django.setup()
        from backend import payments
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        logging.getLogger().addHandler(handler)
        logging.getLogger('sage').addHandler(handler)
        try:
            with (override_settings(DEBUG=False, ENVIRONMENT='production'),
                  patch.object(payments.Order.objects, 'select_related') as select,
                  patch.object(payments, '_alipay_configuration', return_value=self.config),
                  patch('backend.commerce.billing.claim_payment_dispatch',
                        side_effect=RuntimeError(self.config['private_key']))):
                select.return_value.get.return_value = SimpleNamespace(snapshot={'channel': 'alipay'})
                try:
                    payments.finish_payment(None, {'account': object(), 'payment_dispatch': 'fixture'})
                except RuntimeError as error:
                    self.assertEqual(str(error), 'PAYMENT_INTERNAL_ERROR')
                    reporter = ExceptionReporter(None, type(error), error, error.__traceback__)
                    reports = [reporter.get_traceback_text(), reporter.get_traceback_html(),
                               ''.join(traceback.format_exception(error))]
                    current = error
                    while current is not None:
                        self.assertFalse(self.config['private_key'].splitlines()[1] in str(current),
                                         'Private key material remained in exception chain')
                        current = current.__cause__ or current.__context__
                else:
                    self.fail('Synthetic failure did not propagate')
        finally:
            logging.getLogger().removeHandler(handler)
            logging.getLogger('sage').removeHandler(handler)
        reports.append(output.getvalue())
        for report in reports:
            self.assertFalse(self.config['private_key'].splitlines()[1] in report,
                             'Private key material appeared in a dispatch report or log')

    def assert_safe_public_failure(self, operation):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        logging.getLogger().addHandler(handler)
        logging.getLogger('sage').addHandler(handler)
        try:
            try:
                operation()
            except RuntimeError as error:
                self.assertEqual(str(error), 'PAYMENT_INTERNAL_ERROR')
                reporter = ExceptionReporter(None, type(error), error, error.__traceback__)
                reports = [reporter.get_traceback_text(), reporter.get_traceback_html(),
                           ''.join(traceback.format_exception(error))]
                current = error
                while current is not None:
                    self.assertFalse(self.config['private_key'].splitlines()[1] in str(current),
                                     'Private key material remained in exception chain')
                    current = current.__cause__ or current.__context__
            else:
                self.fail('Synthetic failure did not propagate')
        finally:
            logging.getLogger().removeHandler(handler)
            logging.getLogger('sage').removeHandler(handler)
        reports.append(output.getvalue())
        for report in reports:
            self.assertFalse(self.config['private_key'].splitlines()[1] in report,
                             'Private key material appeared in a report or log')

    def test_notification_exception_does_not_expose_key(self):
        django.setup()
        from backend import payments
        request = SimpleNamespace(method='POST', META={}, content_type='application/x-www-form-urlencoded',
                                  content_params={}, read=Mock(side_effect=RuntimeError(self.config['private_key'])))
        with (override_settings(DEBUG=False, ENVIRONMENT='production'),
              patch.object(payments, '_alipay_configuration', return_value=self.config)):
            self.assert_safe_public_failure(lambda: payments.alipay_sandbox_webhook(request))

    def test_refund_exception_does_not_expose_key(self):
        django.setup()
        from backend import payments
        with (override_settings(DEBUG=False, ENVIRONMENT='production'),
              patch.object(payments.transaction, 'get_connection', return_value=SimpleNamespace(in_atomic_block=False)),
              patch.object(payments.transaction, 'atomic', side_effect=nullcontext),
              patch('backend.commerce.billing._lock_account', return_value=object()),
              patch.object(payments, '_alipay_refund_context',
                           return_value=(SimpleNamespace(state='refunded'), object(), self.config)),
              patch('backend.commerce.billing._credit_refund_dict',
                    side_effect=RuntimeError(self.config['private_key']))):
            self.assert_safe_public_failure(lambda: payments.submit_alipay_refund(object(), 'fixture'))
