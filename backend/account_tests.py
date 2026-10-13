"""Account centre tests: isolated fixtures; no external location/image service."""
import json
import tempfile
from io import BytesIO
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.backends.base import UpdateError
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from unittest.mock import patch

from backend.models import AccountProfile, AccountSession, BillingAccount, LoginIdentity, Membership
from backend.account import cleanup_avatar_files, session_is_active


class AccountCentreTests(TestCase):
    def setUp(self):
        media=tempfile.TemporaryDirectory(prefix='sage-account-fixture-');self.addCleanup(media.cleanup)
        isolated=override_settings(MEDIA_ROOT=media.name,AVATAR_LOCAL_FLOCK_VERIFIED=True)
        isolated.enable();self.addCleanup(isolated.disable)
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
        self.assertTrue(self.client.get('/api/account/preferences').json()['product_updates'])
        self.assertEqual(self.write('preferences', {'product_updates': True}).status_code, 200)
        self.assertTrue(AccountProfile.objects.get(user=self.user).product_updates)
        self.assertEqual(self.write('preferences', {'product_updates': 'false'}).status_code, 422)
        self.assertEqual(self.write('preferences', {'product_updates': False}).status_code, 200)
        self.assertFalse(AccountProfile.objects.get(user=self.user).product_updates)
        self.assertFalse(self.client.get('/api/account/preferences').json()['product_updates'])

    def test_unconfigured_legacy_notification_uses_default_without_erasing_explicit_choice(self):
        AccountProfile.objects.create(user=self.user, product_updates=False)
        self.assertTrue(self.client.get('/api/account/preferences').json()['product_updates'])
        self.assertEqual(self.write('preferences', {'product_updates': False}).status_code, 200)
        self.assertFalse(self.client.get('/api/account/preferences').json()['product_updates'])

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
        default_storage.save('avatars/old.jpg', ContentFile(b'old-fixture'))
        image = BytesIO()
        Image.new('RGB', (2, 2)).save(image, 'PNG')
        upload = SimpleUploadedFile('avatar.png', image.getvalue(), content_type='image/png')
        with patch('backend.account.default_storage.save', side_effect=OSError('fixture storage outage')):
            result = self.client.post('/api/account/avatar', {'avatar': upload})
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json()['error']['code'], 'STORAGE_UNAVAILABLE')
        profile.refresh_from_db()
        self.assertEqual(profile.avatar_name, 'avatars/old.jpg')
        with default_storage.open('avatars/old.jpg') as old:
            self.assertEqual(old.read(), b'old-fixture')
        self.assertEqual(len(profile.pending_avatar_deletions), 1)
        self.assertRegex(profile.pending_avatar_deletions[0], r'^avatars/[0-9a-f]{32}/$')
        self.assertTrue(default_storage.exists(profile.pending_avatar_deletions[0]+'.lock'))
        self.assertFalse(default_storage.exists(profile.pending_avatar_deletions[0]+'avatar.jpg'))
        self.assertEqual(cleanup_avatar_files(self.user.pk), 0)
        self.assertTrue(default_storage.exists('avatars/old.jpg'))

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
        self.assertIn('BUSINESS_OWNER_RESPONSIBILITY', self.client.get('/api/account/deletion-eligibility').json()['blockers'])
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


class LegacySessionPasswordResetTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='legacy-session-fixture')
        LoginIdentity.objects.create(user=self.user, method='email', identifier='fixture@example.invalid')
        self.client.force_login(self.user)

    def write(self, path, data, client=None):
        return (client or self.client).patch('/api/account/' + path, json.dumps(data),
            content_type='application/json')

    def test_password_reset_rejects_inflight_legacy_profile_write(self):
        import uuid
        from django.contrib.auth import get_user
        from django.test import RequestFactory
        from backend.authentication import change_password
        from backend.models import AuditEvent
        from backend.views import api
        request = RequestFactory().patch('/api/account/profile',
            json.dumps({'display_name': 'Stale request'}), content_type='application/json')
        request.session = SessionStore(session_key=self.client.session.session_key)
        request.user = get_user(request)  # Independent snapshot loaded before reset.
        request.request_id = uuid.uuid4()
        self.assertTrue(request.user.is_authenticated)
        self.assertFalse(AccountSession.objects.exists())
        change_password(request, get_user_model().objects.get(pk=self.user.pk),
            'fixture@example.invalid', 'Canyon!42', expires_at=timezone.now()+timedelta(minutes=5))
        self.assertTrue(Session.objects.filter(pk=request.session.session_key).exists())
        response = api(request, 'account/profile')
        self.user.refresh_from_db()
        self.assertEqual((response.status_code, self.user.first_name,
            AuditEvent.objects.filter(action='profile.name_updated').count()), (401, '', 0))

    def test_password_reset_rejects_inflight_legacy_billing_write(self):
        import uuid
        from django.contrib.auth import get_user
        from django.test import RequestFactory
        from backend.authentication import change_password
        from backend.models import AuditEvent, PurchaseInquiry
        from backend.views import api
        account = BillingAccount.objects.create(kind='personal', name='Fixture', personal_owner=self.user)
        Membership.objects.create(user=self.user, account=account, role='owner')
        session = self.client.session
        session['active_account_id'] = str(account.pk)
        session.save()
        request = RequestFactory().post('/api/billing/purchase-inquiries', json.dumps({
            'company': 'Fixture', 'seats': 2, 'requirements': 'Fixture requirements',
            'contact_email': 'fixture@example.invalid'}), content_type='application/json',
            HTTP_X_BILLING_ACCOUNT=str(account.pk))
        request.session = SessionStore(session_key=session.session_key)
        request.user = get_user(request)
        request.request_id = uuid.uuid4()
        self.assertTrue(request.user.is_authenticated)
        self.assertFalse(AccountSession.objects.exists())
        change_password(request, get_user_model().objects.get(pk=self.user.pk),
            'fixture@example.invalid', 'Canyon!42', expires_at=timezone.now()+timedelta(minutes=5))
        response = api(request, 'billing/purchase-inquiries')
        self.assertEqual((response.status_code, PurchaseInquiry.objects.count(),
            AuditEvent.objects.filter(action='procurement.received').count()), (401, 0, 0))

    def test_fresh_login_after_password_reset_can_write(self):
        import uuid
        from django.test import RequestFactory
        from backend.authentication import change_password
        request = RequestFactory().post('/api/auth/recovery/complete')
        request.request_id = uuid.uuid4()
        change_password(request, self.user, 'fixture@example.invalid', 'Canyon!42',
            expires_at=timezone.now()+timedelta(minutes=5))
        fresh = Client()
        self.assertTrue(fresh.login(username=self.user.username, password='Canyon!42'))
        response = self.write('profile', {'display_name': 'Fresh login'}, client=fresh)
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Fresh login')

    def test_password_hash_upgrade_login_can_write(self):
        with override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher']):
            self.user.set_password('Canyon!42')
            self.user.save(update_fields=['password'])
        fresh = Client()
        with override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.PBKDF2PasswordHasher',
                'django.contrib.auth.hashers.MD5PasswordHasher']):
            self.assertTrue(fresh.login(username=self.user.username, password='Canyon!42'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.password.startswith('pbkdf2_sha256$'))
        self.assertEqual(self.write('profile', {'display_name': 'Upgraded login'}, client=fresh).status_code, 200)

    def test_secret_key_fallback_session_can_write(self):
        previous_secret = 'nonproduction-session-fallback-fixture-' * 2
        fresh = Client()
        with override_settings(SECRET_KEY=previous_secret):
            fresh.force_login(self.user)
        with override_settings(SECRET_KEY_FALLBACKS=[previous_secret]):
            self.assertEqual(self.write('profile', {'display_name': 'Fallback login'}, client=fresh).status_code, 200)


@skipUnlessDBFeature('has_select_for_update')
class AvatarTransactionTests(TransactionTestCase):
    def setUp(self):
        import os
        from PIL import Image
        media=tempfile.TemporaryDirectory(prefix='sage-avatar-transaction-');self.addCleanup(media.cleanup)
        self.media=media.name
        isolated=override_settings(MEDIA_ROOT=self.media,AVATAR_LOCAL_FLOCK_VERIFIED=True)
        isolated.enable();self.addCleanup(isolated.disable)
        self.assertEqual(default_storage.location,self.media)
        self.user=get_user_model().objects.create_user(username='avatar-transaction-fixture')
        self.client.force_login(self.user);self.client.get('/api/account/profile')
        self.old='avatars/old.jpg';default_storage.save(self.old,ContentFile(b'old-fixture'))
        AccountProfile.objects.filter(user=self.user).update(avatar_name=self.old)
        image=BytesIO();Image.new('RGB',(2,2),'navy').save(image,'PNG');self.image=image.getvalue()
        self.original_save=default_storage.save

    def upload(self):
        return self.client.post('/api/account/avatar',{'avatar':SimpleUploadedFile('fixture.png',self.image,content_type='image/png')})

    def observe(self, action):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import close_old_connections,connections
        def read():
            close_old_connections()
            try:return action()
            finally:connections['default'].close()
        with ThreadPoolExecutor(max_workers=1) as pool:return pool.submit(read).result(timeout=8)

    def token(self):
        queue=AccountProfile.objects.get(user=self.user).pending_avatar_deletions
        self.assertEqual(len(queue),1);self.assertRegex(queue[0],r'^avatars/[0-9a-f]{32}/$')
        return queue[0]

    def test_preregistration_committed_visible_from_independent_connection_before_save(self):
        def save(name,content):
            queue=self.observe(lambda:AccountProfile.objects.get(user=self.user).pending_avatar_deletions)
            self.assertEqual(queue,[name.rsplit('/',1)[0]+'/'])
            return self.original_save(name,content)
        with patch('backend.account.default_storage.save',side_effect=save) as storage:
            self.assertEqual(self.upload().status_code,200)
        self.assertEqual(storage.call_count,1);self.assertEqual(AccountProfile.objects.get(user=self.user).pending_avatar_deletions,[])

    def test_ordinary_outer_transaction_refuses_before_any_media_write(self):
        import os
        from django.db import transaction
        before=set(os.listdir(self.media))
        with patch('backend.account.default_storage.save',wraps=self.original_save) as storage,transaction.atomic():
            with self.assertRaisesMessage(RuntimeError,'durable atomic block'):self.upload()
            storage.assert_not_called()
        self.assertEqual(set(os.listdir(self.media)),before)

    def test_save_writes_then_raises_keeps_committed_namespace_and_cleaner_recovers(self):
        import os
        def fault(name,content):self.original_save(name,content);raise OSError('fixture after file write')
        with patch('backend.account.default_storage.save',side_effect=fault):self.assertEqual(self.upload().status_code,503)
        token=self.token();directory=default_storage.path(token);marker=os.path.join(directory,'.lock')
        inode=(os.stat(marker).st_dev,os.stat(marker).st_ino)
        self.assertEqual(AccountProfile.objects.get(user=self.user).avatar_name,self.old)
        self.assertEqual(set(os.listdir(directory)),{'.lock','avatar.jpg'})
        self.assertEqual(cleanup_avatar_files(self.user.pk),0)
        self.assertTrue(default_storage.exists(self.old));self.assertEqual(os.listdir(directory),['.lock'])
        self.assertEqual((os.stat(marker).st_dev,os.stat(marker).st_ino),inode)

    def test_final_database_failure_never_requires_same_database_compensation(self):
        import os
        from django.db import connection,DatabaseError
        cursor=connection.cursor
        def fault(*args,**kwargs):
            def unavailable(*args,**kwargs):raise DatabaseError('fixture database remains unavailable')
            connection.cursor=unavailable
            raise DatabaseError('fixture final SQL failure')
        try:
            with patch('backend.account._audit',side_effect=fault):self.assertEqual(self.upload().status_code,503)
        finally:connection.cursor=cursor
        token=self.token();self.assertEqual(AccountProfile.objects.get(user=self.user).avatar_name,self.old)
        self.assertTrue(default_storage.exists(token+'avatar.jpg'));self.assertEqual(cleanup_avatar_files(self.user.pk),0)
        self.assertTrue(default_storage.exists(self.old))

    def test_real_commit_before_and_after_ack_exception_have_different_cleanup_results(self):
        from django.db import connection,DatabaseError
        from backend.models import AuditEvent
        for committed in (False,True):
            with self.subTest(committed=committed):
                commit=connection.commit;seen=False
                def fault():
                    nonlocal seen
                    if not seen and AuditEvent.objects.filter(action='profile.avatar_updated').exists():
                        seen=True
                        if committed:commit()
                        raise DatabaseError('fixture final commit acknowledgment')
                    commit()
                with patch.object(connection,'commit',side_effect=fault):self.assertEqual(self.upload().status_code,503)
                self.assertTrue(seen)
                profile=self.observe(lambda:AccountProfile.objects.get(user=self.user))
                if committed:
                    self.assertNotEqual(profile.avatar_name,self.old);self.assertNotIn(profile.avatar_name.rsplit('/',1)[0]+'/',profile.pending_avatar_deletions)
                    self.assertEqual(AuditEvent.objects.filter(action='profile.avatar_updated').count(),1)
                    self.assertTrue(default_storage.exists(profile.avatar_name));self.assertEqual(cleanup_avatar_files(self.user.pk),0)
                    self.assertTrue(default_storage.exists(profile.avatar_name))
                else:
                    self.assertEqual(profile.avatar_name,self.old);self.token();self.assertEqual(AuditEvent.objects.filter(action='profile.avatar_updated').count(),0)
                    self.assertEqual(cleanup_avatar_files(self.user.pk),0)

    def test_cleaner_or_revoker_between_transactions_prevents_storage_write(self):
        import os
        from django.db import connection
        for revoke in (False,True):
            with self.subTest(revoke=revoke):
                commit=connection.commit;first=True
                def intervene():
                    nonlocal first
                    commit()
                    if first:
                        first=False
                        if revoke:self.observe(lambda:AccountSession.objects.filter(user=self.user).update(revoked_at=timezone.now()))
                        else:self.observe(lambda:cleanup_avatar_files(self.user.pk))
                with patch.object(connection,'commit',side_effect=intervene),patch('backend.account.default_storage.save',wraps=self.original_save) as storage:
                    self.assertIn(self.upload().status_code,(401,422));storage.assert_not_called()
                self.assertEqual(AccountProfile.objects.get(user=self.user).avatar_name,self.old)
                if revoke:AccountSession.objects.filter(user=self.user).update(revoked_at=None)

    def test_owned_backend_termination_releases_sql_lock_but_not_namespace_flock(self):
        import os,fcntl
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from django.db import connection,connections,close_old_connections,transaction
        written,release=Event(),Event();pid=[]
        def store(name,content):
            result=self.original_save(name,content)
            with connection.cursor() as cursor:cursor.execute('SELECT pg_backend_pid()');pid.append(cursor.fetchone()[0])
            written.set();self.assertTrue(release.wait(timeout=8));return result
        def worker():
            close_old_connections()
            try:
                with patch('backend.account.default_storage.save',side_effect=store):return self.upload().status_code
            finally:connections['default'].close()
        with ThreadPoolExecutor(max_workers=1) as pool:
            job=pool.submit(worker)
            try:
                self.assertTrue(written.wait(timeout=8));token=self.token();marker=default_storage.path(token+'.lock')
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()');self.assertNotEqual(pid[0],cursor.fetchone()[0])
                    cursor.execute('SELECT pg_terminate_backend(%s)',[pid[0]]);self.assertTrue(cursor.fetchone()[0])
                with transaction.atomic():AccountProfile.objects.select_for_update(nowait=True).get(user=self.user)
                self.assertEqual(cleanup_avatar_files(self.user.pk),1)
                self.assertTrue(default_storage.exists(token+'avatar.jpg'));self.assertEqual(self.token(),token)
                with open(marker,'rb') as fd:
                    with self.assertRaises(BlockingIOError):fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally:release.set()
            self.assertEqual(job.result(timeout=10),503)
        self.assertEqual(cleanup_avatar_files(self.user.pk),0);self.assertTrue(default_storage.exists(self.old))

    def test_preregistration_failure_has_zero_media_writes(self):
        import os
        from django.db import DatabaseError
        before=os.listdir(default_storage.path('avatars'))
        with patch.object(AccountProfile,'save',side_effect=DatabaseError('fixture preregistration SQL failure')),patch(
                'backend.account.default_storage.save',wraps=self.original_save) as save:
            self.assertEqual(self.upload().status_code,503);save.assert_not_called()
        self.assertEqual(os.listdir(default_storage.path('avatars')),before)
        self.assertEqual(self.observe(lambda:AccountProfile.objects.get(user=self.user).pending_avatar_deletions),[])

    def test_flock_survives_actual_commit_and_rollback_exit(self):
        import os,fcntl
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from django.db import connection,connections,close_old_connections,DatabaseError
        from backend.models import AuditEvent
        for rollback in (False,True):
            with self.subTest(rollback=rollback):
                waiting,release=Event(),Event();token=[]
                def worker():
                    close_old_connections();commit=connection.commit;undo=connection.rollback
                    def pause(original):
                        waiting.set();self.assertTrue(release.wait(timeout=8));original()
                    def commit_final():
                        if AuditEvent.objects.filter(action='profile.avatar_updated').exists():pause(commit)
                        else:commit()
                    def fail(*args):raise DatabaseError('fixture final rollback')
                    def store(name,content):token.append(name.rsplit('/',1)[0]+'/');return self.original_save(name,content)
                    try:
                        with patch('backend.account.default_storage.save',side_effect=store),patch.object(
                                connection,'commit',side_effect=commit_final),patch.object(connection,'rollback',side_effect=lambda:pause(undo)):
                            if rollback:
                                with patch('backend.account._audit',side_effect=fail):return self.upload().status_code
                            return self.upload().status_code
                    finally:connections['default'].close()
                # Remove the previous success audit so the next prereg commit isn't paused.
                AuditEvent.objects.all().delete()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    job=pool.submit(worker)
                    try:
                        self.assertTrue(waiting.wait(timeout=8));marker=default_storage.path(token[0]+'.lock')
                        with open(marker,'rb') as fd:
                            with self.assertRaises(BlockingIOError):fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    finally:release.set()
                    self.assertEqual(job.result(timeout=10),503 if rollback else 200)
                self.assertEqual(cleanup_avatar_files(self.user.pk),0)

    def test_hard_exit_after_actual_save_keeps_independently_committed_token(self):
        import os,subprocess,sys
        from django.conf import settings
        from django.db import connection
        self.assertEqual(connection.settings_dict['HOST'],'127.0.0.1')
        # Only this runner's synthetic loopback DB settings enter the owned child.
        env={key:os.environ[key] for key in ('PATH','SAGE_ENV') if key in os.environ}
        for suffix,key in [('NAME','NAME'),('USER','USER'),('PASSWORD','PASSWORD'),('HOST','HOST'),('PORT','PORT')]:
            env['SAGE_DB_'+suffix]=str(connection.settings_dict[key])
        env['DJANGO_SETTINGS_MODULE']='backend.settings';env['SAGE_MEDIA_ROOT']=self.media;env['SAGE_AVATAR_LOCAL_FLOCK_VERIFIED']='1'
        code='''import os,django
django.setup()
from django.test import Client
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from io import BytesIO
from PIL import Image
from unittest.mock import patch
c=Client();c.force_login(get_user_model().objects.get(pk=USER_ID));c.get('/api/account/profile')
i=BytesIO();Image.new('RGB',(2,2)).save(i,'PNG');save=default_storage.save
def exit_after_write(name,content):
    save(name,content);os._exit(23)
with patch('backend.account.default_storage.save',side_effect=exit_after_write):
    c.post('/api/account/avatar',{'avatar':SimpleUploadedFile('fixture.png',i.getvalue(),content_type='image/png')})
'''.replace('USER_ID',str(self.user.pk))
        child=subprocess.run([sys.executable,'-c',code],cwd=settings.BASE_DIR,env=env,capture_output=True,timeout=15)
        self.assertEqual(child.returncode,23)
        token=self.token();marker=default_storage.path(token+'.lock');inode=os.stat(marker).st_ino
        self.assertTrue(default_storage.exists(token+'avatar.jpg'))
        self.assertEqual(self.observe(lambda:AccountProfile.objects.get(user=self.user).avatar_name),self.old)
        self.assertEqual(cleanup_avatar_files(self.user.pk),0)
        self.assertEqual(os.stat(marker).st_ino,inode);self.assertTrue(default_storage.exists(self.old))

    def test_default_off_blocks_upload_and_namespace_apply_without_writes(self):
        import os
        before=os.listdir(default_storage.path('avatars'))
        with override_settings(AVATAR_LOCAL_FLOCK_VERIFIED=False),patch('backend.account.default_storage.save',wraps=self.original_save) as save:
            self.assertEqual(self.upload().status_code,503);save.assert_not_called()
        self.assertEqual(os.listdir(default_storage.path('avatars')),before)
        self.assertEqual(AccountProfile.objects.get(user=self.user).pending_avatar_deletions,[])
        token='avatars/'+'a'*32+'/'
        AccountProfile.objects.filter(user=self.user).update(pending_avatar_deletions=[token])
        with override_settings(AVATAR_LOCAL_FLOCK_VERIFIED=False):self.assertEqual(cleanup_avatar_files(self.user.pk),1)
        self.assertEqual(self.token(),token);self.assertEqual(os.listdir(default_storage.path('avatars')),before)

    def test_unknown_storage_and_symlinked_parent_fail_closed(self):
        import os
        from pathlib import Path
        from django.core.files.storage import InMemoryStorage
        with override_settings(STORAGES={'default':{'BACKEND':'django.core.files.storage.InMemoryStorage'}}):
            self.assertIs(default_storage.__class__,InMemoryStorage)
            with patch('backend.account.default_storage.save') as save:
                self.assertEqual(self.upload().status_code,503);save.assert_not_called()
        self.assertEqual(default_storage.location,self.media)
        with tempfile.TemporaryDirectory(prefix='sage-avatar-symlink-root-') as linked, tempfile.TemporaryDirectory(prefix='sage-avatar-outside-') as outside:
            sentinel=Path(outside,'sentinel.jpg');sentinel.write_bytes(b'outside-fixture');before=sentinel.stat()
            Path(linked,'avatars').symlink_to(outside,target_is_directory=True)
            token='avatars/'+'b'*32+'/'
            AccountProfile.objects.filter(user=self.user).update(pending_avatar_deletions=[token,'avatars/sentinel.jpg'])
            with override_settings(MEDIA_ROOT=linked):
                self.assertEqual(cleanup_avatar_files(self.user.pk),2)
                with patch('backend.account.default_storage.save') as save:
                    self.assertEqual(self.upload().status_code,503);save.assert_not_called()
                # Both original suspicious entries remain; only preregistration was added.
                queue=AccountProfile.objects.get(user=self.user).pending_avatar_deletions
                self.assertEqual(queue[:2],[token,'avatars/sentinel.jpg']);self.assertEqual(len(queue),3)
            self.assertEqual(sentinel.read_bytes(),b'outside-fixture');after=sentinel.stat()
            self.assertEqual((before.st_ino,before.st_mtime_ns),(after.st_ino,after.st_mtime_ns))

    def test_actual_returned_name_is_used_and_invalid_results_remain_tracked(self):
        import os
        with patch.object(default_storage,'get_available_name',side_effect=lambda name,max_length=None:name.replace('avatar.jpg','alternate.jpg')):
            self.assertEqual(self.upload().status_code,200)
        current=AccountProfile.objects.get(user=self.user).avatar_name
        self.assertTrue(current.endswith('/alternate.jpg'));self.assertTrue(default_storage.exists(current))
        for mode in ('outside','marker','extra','wrong_contents'):
            with self.subTest(mode=mode):
                def fault(name,content):
                    result=self.original_save(name,content)
                    if mode=='extra':self.original_save(name.replace('avatar.jpg','extra.jpg'),ContentFile(b'fixture'))
                    if mode=='wrong_contents':
                        with open(default_storage.path(result),'wb') as file:file.write(b'fixture')
                    return 'avatars/outside.jpg' if mode=='outside' else name.replace('avatar.jpg','.lock') if mode=='marker' else result
                with patch('backend.account.default_storage.save',side_effect=fault):self.assertEqual(self.upload().status_code,503)
                self.assertEqual(AccountProfile.objects.get(user=self.user).avatar_name,current)
                queue=AccountProfile.objects.get(user=self.user).pending_avatar_deletions
                self.assertTrue(queue);self.assertTrue(all(name.endswith('/') for name in queue))
        self.assertTrue(default_storage.exists(current))

    def test_cleanup_rejects_suspicious_queue_paths_and_contents_preserves_sentinel(self):
        import os
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix='sage-avatar-outside-') as outside:
            sentinel=Path(outside,'sentinel.jpg');sentinel.write_bytes(b'outside-fixture');before=sentinel.stat()
            values=[None,12,{},'../sentinel.jpg',str(sentinel),'avatars/../sentinel.jpg','avatars/x\\y.jpg','avatars/not-hex/']
            for value in values:
                with self.subTest(value_type=type(value).__name__):
                    AccountProfile.objects.filter(user=self.user).update(pending_avatar_deletions=[value])
                    self.assertEqual(cleanup_avatar_files(self.user.pk),1)
                    self.assertEqual(AccountProfile.objects.get(user=self.user).pending_avatar_deletions,[value])
            # PostgreSQL jsonb itself rejects U+0000; test the path guard directly
            # rather than pretending that impossible JSON fixture was persisted.
            from backend.account import _avatar_exact_file
            with self.assertRaises(OSError):_avatar_exact_file('avatars/x\0.jpg')
            for value in ('avatars/old.jpg',{'unexpected':'queue'}):
                AccountProfile.objects.filter(user=self.user).update(pending_avatar_deletions=value)
                self.assertEqual(cleanup_avatar_files(self.user.pk),1)
                self.assertEqual(AccountProfile.objects.get(user=self.user).pending_avatar_deletions,value)
            for mode in ('file_symlink','directory_symlink','marker_symlink','extra','subdirectory'):
                with self.subTest(mode=mode):
                    token='avatars/'+__import__('uuid').uuid4().hex+'/';directory=Path(default_storage.path(token))
                    if mode=='directory_symlink':directory.symlink_to(outside,target_is_directory=True)
                    else:
                        directory.mkdir()
                        if mode=='marker_symlink':(directory/'.lock').symlink_to(sentinel)
                        else:(directory/'.lock').touch()
                        if mode=='file_symlink':(directory/'avatar.jpg').symlink_to(sentinel)
                        if mode=='extra':(directory/'unexpected.txt').write_bytes(b'fixture')
                        if mode=='subdirectory':(directory/'nested').mkdir()
                    AccountProfile.objects.filter(user=self.user).update(pending_avatar_deletions=[token])
                    self.assertEqual(cleanup_avatar_files(self.user.pk),1);self.assertEqual(self.token(),token)
            self.assertEqual(sentinel.read_bytes(),b'outside-fixture')
            after=sentinel.stat();self.assertEqual((before.st_ino,before.st_mtime_ns,before.st_size),(after.st_ino,after.st_mtime_ns,after.st_size))

    def test_namespace_current_and_dry_run_keep_tree_queue_and_stable_inode(self):
        import os,fcntl
        from pathlib import Path
        from django.core.management import call_command
        from io import StringIO
        self.assertEqual(self.upload().status_code,200)
        profile=AccountProfile.objects.get(user=self.user);token=profile.avatar_name.rsplit('/',1)[0]+'/'
        AccountProfile.objects.filter(user=self.user).update(pending_avatar_deletions=[token])
        def snapshot():return {str(p.relative_to(self.media)):(p.stat().st_ino,p.stat().st_mtime_ns,p.stat().st_size) for p in Path(self.media).rglob('*')}
        before=snapshot();call_command('cleanup_avatars',stdout=StringIO());self.assertEqual(snapshot(),before)
        self.assertEqual(cleanup_avatar_files(self.user.pk),1);self.assertTrue(default_storage.exists(profile.avatar_name))
        marker=default_storage.path(token+'.lock')
        with open(marker,'rb') as fd:
            original=os.fstat(fd.fileno());fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            # Make namespace non-current; cleaner must respect the old open FD.
            AccountProfile.objects.filter(user=self.user).update(avatar_name='')
            self.assertEqual(cleanup_avatar_files(self.user.pk),1)
            fcntl.flock(fd,fcntl.LOCK_UN)
            self.assertEqual(cleanup_avatar_files(self.user.pk),0)
            self.assertEqual(os.stat(marker).st_ino,original.st_ino)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.assertEqual(os.listdir(default_storage.path(token)),['.lock'])


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
