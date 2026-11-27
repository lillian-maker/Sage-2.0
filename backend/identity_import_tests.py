"""G2: Django-to-Logto import payload, dry-run, and apply counting."""
import json
import os
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .management.commands.import_logto_users import (
    build_user_payload,
    import_users,
    legacy_password_digest,
)
from .models import LoginIdentity


def new_user(username, password='Sage-G2-Fixture-Password-1'):
    return get_user_model().objects.create_user(username=username, password=password)


class ImportPayloadTests(TestCase):
    def test_pbkdf2_digest_is_logto_legacy_shape(self):
        digest = legacy_password_digest(make_password('Sage-G2-Fixture-Password-1'))
        self.assertIsNotNone(digest)
        parsed = json.loads(digest)
        self.assertEqual(parsed[0], 'pbkdf2')
        self.assertEqual(parsed[1][3], 'sha256')
        self.assertEqual(parsed[1][4], '@')
        self.assertEqual(len(bytes.fromhex(parsed[2])), 32)

    def test_non_pbkdf2_digest_is_omitted(self):
        self.assertIsNone(legacy_password_digest('bcrypt$whatever'))

    def test_payload_uses_verified_login_email_only(self):
        user = new_user('payload-email')
        user.email = 'unverified@example.com'
        user.save(update_fields=['email'])
        LoginIdentity.objects.create(user=user, method='email', identifier='verified@example.com')
        payload, has_password = build_user_payload(user)
        self.assertEqual(payload['primaryEmail'], 'verified@example.com')
        self.assertTrue(has_password)
        self.assertEqual(payload['passwordAlgorithm'], 'Legacy')

    def test_payload_omits_primary_email_without_email_identity(self):
        user = new_user('payload-phone')
        LoginIdentity.objects.create(user=user, method='phone', identifier='+15550100')
        payload, _ = build_user_payload(user)
        self.assertNotIn('primaryEmail', payload)

    def test_payload_omits_unusable_passwords(self):
        user = new_user('payload-unusable', password=None)
        payload, has_password = build_user_payload(user)
        self.assertFalse(has_password)
        self.assertNotIn('passwordAlgorithm', payload)


class ImportRunTests(TestCase):
    def test_dry_run_counts_without_network(self):
        new_user('dry-a')
        new_user('dry-b', password=None)
        result = import_users(get_user_model().objects.order_by('pk'), apply=False)
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['created'], 2)
        self.assertEqual(result['passwords_skipped'], 1)
        self.assertEqual(result['failed'], 0)

    def test_apply_counts_created_exists_and_failed(self):
        first = new_user('apply-a')
        second = new_user('apply-b')
        calls = []

        def post(endpoint, token, payload):
            calls.append(payload['id'])
            if payload['id'] == str(first.pk):
                return 201, 'created'
            if payload['id'] == str(second.pk):
                return 422, 'exists'
            return 500, 'http-error'

        result = import_users([first, second], apply=True, endpoint='https://auth.example',
                              token='fixture', post=post, sleep=lambda _: None)
        self.assertEqual(result['created'], 1)
        self.assertEqual(result['exists'], 1)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(len(calls), 2)

    def test_command_dry_run_and_limit(self):
        new_user('cmd-a')
        new_user('cmd-b')
        staff = new_user('cmd-staff')
        staff.is_staff = True
        staff.save(update_fields=['is_staff'])
        out = StringIO()
        call_command('import_logto_users', '--limit', '1', stdout=out)
        result = json.loads(out.getvalue())
        self.assertEqual(result['mode'], 'dry-run')
        self.assertEqual(result['total'], 1)

    def test_command_apply_requires_management_env(self):
        with patch.dict(os.environ, {'LOGTO_MANAGEMENT_ENDPOINT': '', 'LOGTO_MANAGEMENT_TOKEN': ''}):
            with self.assertRaises(CommandError):
                call_command('import_logto_users', '--apply', stdout=StringIO())
