"""Account centre tests: isolated fixtures; no external location/image service."""
import json
import tempfile
from io import BytesIO
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.backends.base import UpdateError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import default_storage
from django.utils import timezone
from unittest.mock import patch

from backend.models import AccountProfile, AccountSession, BillingAccount, LoginIdentity, Membership
from backend.account import cleanup_avatar_files, session_is_active


class AccountCentreTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='account-fixture')
        LoginIdentity.objects.create(user=self.user, method='email', identifier='fixture@example.invalid')
        self.client.force_login(self.user)

    def write(self, path, data, method='patch', client=None):
        return getattr(client or self.client, method)('/api/account/' + path,
            json.dumps(data), content_type='application/json')

    def test_profile_persist_and_read_only_email(self):
        result = self.write('profile', {'display_name': '  Example Name  '})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['display_name'], 'Example Name')
        self.assertEqual(self.client.get('/api/account/profile').json()['email'], 'fixture@example.invalid')
        self.assertEqual(self.write('profile', {'email': 'other@example.invalid'}).status_code, 422)
        for value in ['', 'x' * 81, 12, 'name\nline']:
            self.assertEqual(self.write('profile', {'display_name': value}).status_code, 422)

    def test_preferences_default_and_strict_boolean(self):
        self.assertFalse(self.client.get('/api/account/preferences').json()['product_updates'])
        self.assertEqual(self.write('preferences', {'product_updates': True}).status_code, 200)
        self.assertTrue(AccountProfile.objects.get(user=self.user).product_updates)
        self.assertEqual(self.write('preferences', {'product_updates': 'false'}).status_code, 422)
        self.assertEqual(self.write('preferences', {'product_updates': False}).status_code, 200)
        self.assertFalse(AccountProfile.objects.get(user=self.user).product_updates)

    def test_other_sessions_revocation_is_real_preserves_current(self):
        other = Client()
        other.force_login(self.user)
        other.get('/api/account/sessions', HTTP_USER_AGENT='Mozilla/5.0 Firefox/122')
        current = self.client.get('/api/account/sessions').json()['items']
        self.assertEqual(len(current), 2)
        self.assertEqual(sum(row['is_current'] for row in current), 1)
        self.assertNotIn('session_key', current[0])
        self.assertEqual(current[0]['location'], '未知')
        result = self.write('sessions/revoke', {'other_sessions': True}, 'post')
        self.assertEqual(result.json()['revoked_count'], 1)
        self.assertEqual(other.get('/api/account/profile').status_code, 401)
        self.assertEqual(self.client.get('/api/account/profile').status_code, 200)
        self.assertEqual(self.write('sessions/revoke', {'other_sessions': True}, 'post').json()['revoked_count'], 0)

    def test_current_and_foreign_session_cannot_be_revoked(self):
        current = self.client.get('/api/account/sessions').json()['items'][0]['id']
        self.assertEqual(self.write('sessions/revoke', {'session_id': current}, 'post').status_code, 409)
        foreign = get_user_model().objects.create_user(username='foreign-fixture')
        session = AccountSession.objects.create(user=foreign, session_key='foreign-key', client='Browser')
        self.assertEqual(self.write('sessions/revoke', {'session_id': str(session.pk)}, 'post').status_code, 404)
        self.assertIsNone(AccountSession.objects.get(pk=session.pk).revoked_at)

    def test_expired_sessions_not_listed_and_pagination_validated(self):
        self.client.get('/api/account/sessions')
        expired = Session.objects.create(session_key='expired-key', session_data='',
                                         expire_date=timezone.now()-timedelta(seconds=1))
        AccountSession.objects.create(user=self.user, session_key=expired.pk, client='Browser')
        self.assertEqual(len(self.client.get('/api/account/sessions').json()['items']), 1)
        self.assertEqual(self.client.get('/api/account/sessions?offset=-1').status_code, 422)

    def test_auth_and_csrf_required(self):
        self.assertEqual(Client().get('/api/account/profile').status_code, 401)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.user)
        self.assertEqual(self.write('profile', {'display_name': 'Name'}, client=secure).status_code, 403)

    def test_deletion_closed_until_retention_policy_approved(self):
        result = self.client.get('/api/account/deletion-eligibility')
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()['eligible'])
        self.assertIn('RETENTION_POLICY_PENDING', result.json()['blockers'])
        self.assertEqual(self.write('delete', {}, 'post').status_code, 503)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_avatar_reencodes_private_square_and_removes(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            image = BytesIO()
            Image.new('RGB', (40, 20), 'navy').save(image, 'PNG')
            upload = SimpleUploadedFile('untrusted-path.png', image.getvalue(), content_type='image/png')
            result = self.client.post('/api/account/avatar', {'avatar': upload})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['avatar_url'], '/api/account/avatar-image')
            name = AccountProfile.objects.get(user=self.user).avatar_name
            self.assertTrue(name.startswith('avatars/'))
            with default_storage.open(name) as saved, Image.open(saved) as decoded:
                self.assertEqual(decoded.format, 'JPEG')
                self.assertEqual(decoded.size, (512, 512))
                self.assertFalse(decoded.getexif())
            response = self.client.get('/api/account/avatar-image')
            self.assertEqual(response.status_code, 200)
            # Consume Django's wrapped stream, which disconnects the production
            # connection-cleanup signal while closing this test response. Calling
            # close() directly bypasses that wrapper and closes the PG test transaction.
            content = b''.join(response.streaming_content)
            self.assertTrue(content.startswith(b'\xff\xd8'))
            with Image.open(BytesIO(content)) as delivered:
                self.assertEqual(delivered.format, 'JPEG')
                self.assertEqual(delivered.size, (512, 512))
            self.assertEqual(Client().get('/api/account/avatar-image').status_code, 401)
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.write('avatar', {}, 'delete').status_code, 200)
            self.assertFalse(default_storage.exists(name))
            self.assertIsNone(self.client.get('/api/account/profile').json()['avatar_url'])

    def test_invalid_avatar_does_not_change_profile(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            images = [(b'<svg/>', 'image/svg+xml'), (b'not-an-image', 'image/png'),
                      (b'x' * (2 * 1024 * 1024 + 1), 'image/png')]
            png = BytesIO()
            Image.new('RGB', (2, 2)).save(png, 'PNG')
            images.append((png.getvalue(), 'image/jpeg'))
            animated = BytesIO()
            Image.new('RGB', (2, 2), 'red').save(animated, 'PNG', save_all=True,
                append_images=[Image.new('RGB', (2, 2), 'blue')])
            images.append((animated.getvalue(), 'image/png'))
            for content, mime in images:
                upload = SimpleUploadedFile('avatar.png', content, content_type=mime)
                result = self.client.post('/api/account/avatar', {'avatar': upload})
                self.assertIn(result.status_code, (413, 422))
            self.assertFalse(AccountProfile.objects.filter(user=self.user).exists())

    def test_revoked_metadata_cannot_revive_live_cookie(self):
        self.client.get('/api/account/sessions')
        AccountSession.objects.filter(user=self.user).update(revoked_at=timezone.now())
        self.assertEqual(self.client.get('/api/account/profile').status_code, 401)
        self.assertEqual(self.write('profile', {'display_name': 'Forbidden'}).status_code, 401)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, '')

    def test_avatar_storage_failure_keeps_old_record(self):
        from PIL import Image
        profile = AccountProfile.objects.create(user=self.user, avatar_name='avatars/old.jpg')
        image = BytesIO()
        Image.new('RGB', (2, 2)).save(image, 'PNG')
        upload = SimpleUploadedFile('avatar.png', image.getvalue(), content_type='image/png')
        with patch('backend.account.default_storage.save', side_effect=OSError('fixture storage outage')):
            result = self.client.post('/api/account/avatar', {'avatar': upload})
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json()['error']['code'], 'STORAGE_UNAVAILABLE')
        profile.refresh_from_db()
        self.assertEqual(profile.avatar_name, 'avatars/old.jpg')

    def test_late_session_save_cannot_resurrect_revoked_device(self):
        other = Client()
        other.force_login(self.user)
        other.get('/api/account/sessions')
        key = other.session.session_key
        in_flight = SessionStore(session_key=key)
        # Read before revocation, exactly as a concurrent request would.
        self.assertIn('_auth_user_id', in_flight)
        in_flight['late_response'] = True
        result = self.write('sessions/revoke', {'other_sessions': True}, 'post')
        self.assertEqual(result.json()['revoked_count'], 1)
        with self.assertRaises(UpdateError):
            in_flight.save()
        self.assertFalse(Session.objects.filter(pk=key).exists())
        self.assertIsNotNone(AccountSession.objects.get(session_key=key).revoked_at)
        self.assertEqual(other.get('/api/account/profile').status_code, 401)

    def test_sole_business_administrator_blocks_deletion(self):
        business = BillingAccount.objects.create(kind='business', name='Fixture business')
        Membership.objects.create(account=business, user=self.user, role='owner')
        self.assertIn('SOLE_BUSINESS_ADMIN', self.client.get('/api/account/deletion-eligibility').json()['blockers'])
        colleague = get_user_model().objects.create_user(username='colleague-fixture')
        Membership.objects.create(account=business, user=colleague, role='billing_admin')
        self.assertNotIn('SOLE_BUSINESS_ADMIN', self.client.get('/api/account/deletion-eligibility').json()['blockers'])

    def test_ambiguous_session_revoke_body_rejected(self):
        result = self.write('sessions/revoke', {'session_id': 'ignored', 'other_sessions': True}, 'post')
        self.assertEqual(result.status_code, 422)

    def test_cleanup_failure_durable_and_retry_does_not_delete_new_avatar(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            image = BytesIO()
            Image.new('RGB', (2, 2), 'navy').save(image, 'PNG')
            def upload():
                return self.client.post('/api/account/avatar', {'avatar': SimpleUploadedFile(
                    'avatar.png', image.getvalue(), content_type='image/png')})
            self.assertEqual(upload().status_code, 200)
            old_name = AccountProfile.objects.get(user=self.user).avatar_name
            with patch('backend.account.default_storage.delete', side_effect=OSError('fixture outage')):
                with self.captureOnCommitCallbacks(execute=True):
                    self.assertEqual(upload().status_code, 200)
            profile = AccountProfile.objects.get(user=self.user)
            self.assertEqual(profile.pending_avatar_deletions, [old_name])
            self.assertNotEqual(profile.avatar_name, old_name)
            self.assertEqual(cleanup_avatar_files(self.user.pk), 0)
            profile.refresh_from_db()
            self.assertEqual(profile.pending_avatar_deletions, [])
            self.assertTrue(default_storage.exists(profile.avatar_name))
            self.assertFalse(default_storage.exists(old_name))

    def test_cleanup_cli_dry_run_is_read_only_and_apply_retries(self):
        from django.core.management import call_command
        AccountProfile.objects.create(user=self.user, pending_avatar_deletions=['avatars/old.jpg'])
        from io import StringIO
        with patch('backend.account.default_storage.delete') as delete:
            call_command('cleanup_avatars', stdout=StringIO())
            delete.assert_not_called()
            self.assertEqual(AccountProfile.objects.get(user=self.user).pending_avatar_deletions, ['avatars/old.jpg'])
            call_command('cleanup_avatars', apply=True, stdout=StringIO())
            delete.assert_called_once_with('avatars/old.jpg')
        self.assertEqual(AccountProfile.objects.get(user=self.user).pending_avatar_deletions, [])

    def test_cleanup_never_removes_current_avatar_and_reports_pending(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from io import StringIO
        AccountProfile.objects.create(user=self.user, avatar_name='avatars/current.jpg',
            pending_avatar_deletions=['avatars/current.jpg'])
        with patch('backend.account.default_storage.delete') as delete:
            with self.assertRaisesMessage(CommandError, 'AVATAR_CLEANUP_PENDING'):
                call_command('cleanup_avatars', apply=True, stdout=StringIO())
            delete.assert_not_called()
        self.assertEqual(AccountProfile.objects.get(user=self.user).avatar_name, 'avatars/current.jpg')

    def test_legacy_session_guard_does_not_write_metadata(self):
        from django.test import RequestFactory
        request = RequestFactory().get('/api/billing/account')
        request.user = self.user
        request.session = self.client.session
        self.assertTrue(session_is_active(request))
        self.assertFalse(AccountSession.objects.exists())


class AccountSessionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='session-race-fixture')
        self.clients = [Client(), Client()]
        for client in self.clients:
            client.force_login(self.user)
            client.get('/api/account/sessions')

    def parallel(self, operations):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections, connection
        if connection.vendor != 'postgresql':
            self.skipTest('Row-lock concurrency requires PostgreSQL; SQLite is not evidence.')
        barrier = Barrier(2)
        def run(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operations[index]()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(run, [0, 1]))

    def test_cross_device_revocations_do_not_deadlock_or_revoke_winner(self):
        def revoke(client):
            return client.post('/api/account/sessions/revoke', json.dumps({'other_sessions': True}),
                content_type='application/json').status_code
        statuses = self.parallel([lambda: revoke(self.clients[0]), lambda: revoke(self.clients[1])])
        self.assertEqual(sorted(statuses), [200, 401])
        self.assertEqual(AccountSession.objects.filter(revoked_at__isnull=True).count(), 1)
        self.assertEqual(Session.objects.count(), 1)

    def test_sensitive_write_serializes_with_revocation(self):
        statuses = self.parallel([
            lambda: self.clients[0].patch('/api/account/profile', json.dumps({'display_name': 'Race Name'}),
                content_type='application/json').status_code,
            lambda: self.clients[1].post('/api/account/sessions/revoke', json.dumps({'other_sessions': True}),
                content_type='application/json').status_code,
        ])
        self.assertEqual(statuses[1], 200)
        self.assertIn(statuses[0], (200, 401))
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Race Name' if statuses[0] == 200 else '')
        self.assertEqual(self.clients[0].patch('/api/account/profile', json.dumps({'display_name': 'After revoke'}),
            content_type='application/json').status_code, 401)
