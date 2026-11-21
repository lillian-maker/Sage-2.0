"""Isolated generated WeChat credentials and failure boundaries; no network."""
import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import override_settings
from django.views.decorators.debug import sensitive_variables
from django.views.debug import ExceptionReporter

from backend.wechat_credentials import load_wechat_credentials


class WechatCredentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.config = {'enabled': True, 'app_id': 'wx_fixture', 'mch_id': '12345678',
            'merchant_entity': 'fixture', 'merchant_serial': 'AABB1234',
            'private_key': key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode('ascii'),
            'public_key': key.public_key().public_bytes(serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo).decode('ascii'),
            'public_key_id': 'PUB_KEY_ID_123', 'api_v3_key': 'x' * 32,
            'notify_origin': 'https://example.test', 'payment_ttl_seconds': 900}

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'credentials.json'
        self.write(self.config)

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding='utf-8')
        self.path.chmod(0o400)

    def assert_rejected(self, path=None):
        with self.assertRaises(ValueError) as raised:
            load_wechat_credentials(path or self.path)
        self.assertEqual(str(raised.exception), 'WeChat credential file is invalid, unreadable or incomplete.')
        self.assertIsNone(raised.exception.__context__)

    def test_loads_and_converts_api_key_without_network(self):
        with patch('requests.Session.send', side_effect=AssertionError('unexpected network')):
            loaded = load_wechat_credentials(self.path)
        self.assertTrue(loaded == {**self.config, 'api_v3_key': b'x' * 32})

    def test_missing_symlink_permission_and_owner_fail_closed(self):
        self.assert_rejected(self.path.parent / 'missing')
        link = self.path.parent / 'link'
        link.symlink_to(self.path)
        self.assert_rejected(link)
        self.path.chmod(0o440)
        self.assert_rejected()
        self.path.chmod(0o400)
        with patch('backend.wechat_credentials.os.geteuid', return_value=os.geteuid()+1):
            self.assert_rejected()

    def test_schema_and_key_validation(self):
        for change in ({'api_v3_key': 'x' * 31}, {'api_v3_key': '中' * 32},
                       {'enabled': False}, {'payment_ttl_seconds': True},
                       {'notify_origin': 'http://example.test'}, {'private_key': 'invalid'},
                       {'public_key': 'invalid'}, {'app_id': 'bad space'},
                       {'public_key_id': 'invalid'}, {'unexpected': 'value'}):
            with self.subTest(fields=tuple(change)):
                self.path.chmod(0o600)
                self.write({**self.config, **change})
                self.assert_rejected()

    def test_duplicate_malformed_and_oversized_fail_closed(self):
        for data in ('{"api_v3_key":"synthetic-secret-marker",',
                     '{"enabled":true,"enabled":false}', 'x' * 32769):
            with self.subTest(length=len(data)):
                self.path.chmod(0o600)
                self.path.write_text(data, encoding='utf-8')
                self.path.chmod(0o400)
                self.assert_rejected()

    def test_startup_requires_explicit_production_opt_in_and_keeps_gates_closed(self):
        settings_file = str(Path(__file__).with_name('settings.py'))
        for environment, enabled in (('production', ''), ('test', '1'), ('development', '1'), ('production', '1')):
            with self.subTest(environment=environment, enabled=enabled), patch.dict(os.environ, {
                    'SAGE_ENV': environment, 'SAGE_SECRET_KEY': 'x' * 64,
                    'SAGE_ALLOWED_HOSTS': 'example.test', 'SAGE_DB_NAME': 'fixture',
                    'SAGE_WECHAT_CREDENTIALS_ENABLED': enabled}, clear=True), patch(
                    'backend.wechat_credentials.load_wechat_credentials',
                    side_effect=lambda path: load_wechat_credentials(self.path)) as load:
                result = runpy.run_path(settings_file)
                if environment == 'production' and enabled == '1':
                    load.assert_called_once_with('/run/secrets/wechat_native.json')
                    self.assertTrue(result['BILLING_WECHAT_NATIVE'] == {**self.config, 'api_v3_key': b'x' * 32})
                else:
                    load.assert_not_called()
                    self.assertTrue(result['BILLING_WECHAT_NATIVE'] == {})
                self.assertFalse(result['BILLING_WECHAT_LIVE_ENABLED'])
                self.assertFalse(result['BILLING_SALES_ENABLED'])
                self.assertEqual(result['BILLING_APPROVED_CHANNELS'], {})

    @sensitive_variables()
    def test_django_report_redacts_parser_and_settings(self):
        marker = 'synthetic' + '-secret-marker'
        self.path.chmod(0o600)
        self.path.write_text('{"private_key":"' + marker + '",', encoding='utf-8')
        self.path.chmod(0o400)
        with override_settings(DEBUG=False, BILLING_WECHAT_NATIVE=self.config):
            try:
                load_wechat_credentials(self.path)
            except ValueError as error:
                reporter = ExceptionReporter(None, type(error), error, error.__traceback__)
                reports = [reporter.get_traceback_text(), reporter.get_traceback_html()]
                safe = reporter.filter.get_safe_settings()['BILLING_WECHAT_NATIVE']
                self.assertNotIn(self.config['private_key'].splitlines()[1], str(safe))
                self.assertNotIn(self.config['api_v3_key'], str(safe))
            else:
                self.fail('Malformed input accepted')
        for report in reports:
            self.assertNotIn(marker, report)
            self.assertNotIn(self.config['private_key'].splitlines()[1], report)
