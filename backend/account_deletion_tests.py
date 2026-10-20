"""Verified deletion fixtures; no real delivery, retention period or production side effect."""
import json
import re
from io import StringIO
import hashlib
from datetime import timedelta
from unittest.mock import patch
from unittest import skipUnless
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.sessions.models import Session
from django.core import mail
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.db import connection, close_old_connections, transaction
from django.utils import timezone

from .account import (cleanup_deletion_data, trial_identity_blocked,
                      update_deletion_request, _business_due)
from .models import (AccountDeletionChallenge, AccountDeletionRequest, AccountDeletionAudit,
    AccountProfile, AccountSession, BillingAccount, LoginChallenge, LoginIdentity, Membership,
    TrialAttempt, TrialIdentityMarker)


@override_settings(ACCOUNT_DELETION_ENABLED=True, ACCOUNT_DELETION_POLICY_VERSION='fixture-reviewed-v1',
    ACCOUNT_DELETION_POLICY_REVIEWED_AT='2026-01-01', ACCOUNT_DELETION_RETENTION_DAYS=13,
    ACCOUNT_DELETION_MARKER_RETENTION_DAYS=17, ACCOUNT_DELETION_MARKER_KEY='fixture-only-' * 6,
    AUTH_EMAIL_ENABLED=True, AUTH_EMAIL_TRANSPORT='smtp', DEFAULT_FROM_EMAIL='sage@example.invalid',
    EMAIL_HOST='fixture.invalid', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AccountDeletionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='deletion-fixture', first_name='Name', email='other@example.invalid')
        self.identity = LoginIdentity.objects.create(user=self.user, method='email', identifier='fixture@example.invalid')
        self.account = BillingAccount.objects.create(kind='personal', personal_owner=self.user, name='Personal')
        Membership.objects.create(account=self.account, user=self.user, role='owner')
        self.client.force_login(self.user)
        self.client.get('/api/account/profile')

    def post(self, path, data, client=None):
        return (client or self.client).post('/api/account/' + path, json.dumps(data), content_type='application/json')

    def challenge(self):
        response = self.post('deletion-challenges', {})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIn('Account deletion', mail.outbox[-1].subject)
        self.assertNotIn('code', response.json())
        return response.json()['challenge_id'], re.search(r'\b\d{6}\b', mail.outbox[-1].body).group()

    def confirm(self, challenge, code):
        return self.post('delete', {'challenge_id': challenge, 'code': code,
            'confirm': True, 'policy_version': 'fixture-reviewed-v1'})

    def test_simple_deletion_scrubs_identity_and_revokes_all_sessions(self):
        other = Client()
        other.force_login(self.user)
        other.get('/api/account/profile')
        AccountProfile.objects.filter(user=self.user).update(product_updates=True)
        challenge, code = self.challenge()
        response = self.confirm(challenge, code)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {'status': 'deleted'})
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertFalse(self.user.has_usable_password())
        self.assertEqual((self.user.first_name, self.user.last_name, self.user.email), ('', '', ''))
        self.assertFalse(LoginIdentity.objects.filter(user=self.user).exists())
        self.assertFalse(AccountDeletionChallenge.objects.filter(user=self.user).exists())
        self.assertFalse(Session.objects.filter(pk__in=AccountSession.objects.filter(user=self.user).values_list('session_key', flat=True)).exists())
        self.assertFalse(Membership.objects.get(user=self.user).is_active)
        self.assertFalse(AccountProfile.objects.get(user=self.user).product_updates)
        self.assertEqual(other.get('/api/account/profile').status_code, 401)
        new = get_user_model().objects.create_user(username='reregister-fixture')
        LoginIdentity.objects.create(user=new, method='email', identifier=self.identity.identifier)
        self.assertFalse(trial_identity_blocked(new))

    def test_trial_used_marker_blocks_new_identity_until_reviewed_expiry(self):
        TrialAttempt.objects.create(user=self.user, scenario='voc', input_value='Private input', input_key='key', request_key='fixture', task_state='completed', report={'private': 'report'})
        challenge, code = self.challenge()
        self.assertEqual(self.confirm(challenge, code).status_code, 200)
        attempt = TrialAttempt.objects.get(user=self.user)
        self.assertEqual((attempt.input_value, attempt.report), ('', None))
        new = get_user_model().objects.create_user(username='new-fixture')
        LoginIdentity.objects.create(user=new, method='email', identifier=self.identity.identifier)
        self.assertTrue(trial_identity_blocked(new))
        TrialIdentityMarker.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(trial_identity_blocked(new))

    def test_missing_marker_key_fails_closed_if_unexpired_records_exist(self):
        with override_settings(ACCOUNT_DELETION_MARKER_KEY=''):
            self.assertFalse(trial_identity_blocked(self.user))
            TrialIdentityMarker.objects.create(method='email', identifier_digest='fixture',
                expires_at=timezone.now()+timedelta(days=1))
            with self.assertRaisesMessage(ValueError, 'RETENTION_POLICY_PENDING'):
                trial_identity_blocked(self.user)

    def test_rotated_key_fails_closed_instead_of_reissuing_trial(self):
        TrialIdentityMarker.objects.create(method='email', identifier_digest='fixture',
            key_fingerprint=hashlib.sha256(('fixture-only-' * 6).encode()).hexdigest(),
            expires_at=timezone.now()+timedelta(days=1))
        with override_settings(ACCOUNT_DELETION_MARKER_KEY='different-fixture-key-' * 6):
            with self.assertRaisesMessage(ValueError, 'RETENTION_POLICY_PENDING'):
                trial_identity_blocked(self.user)

    def test_owner_application_preserves_login_and_enterprise_data(self):
        business = BillingAccount.objects.create(kind='business', name='Business')
        member = Membership.objects.create(account=business, user=self.user, role='owner')
        challenge, code = self.challenge()
        response = self.confirm(challenge, code)
        self.assertEqual(response.status_code, 202)
        self.user.refresh_from_db()
        member.refresh_from_db()
        self.assertTrue(self.user.is_active and member.is_active)
        self.assertEqual(business.name, 'Business')
        self.assertEqual(self.client.get('/api/account/profile').status_code, 200)
        row = AccountDeletionRequest.objects.get(user=self.user)
        self.assertEqual(row.blockers, ['BUSINESS_OWNER_RESPONSIBILITY'])
        self.assertEqual(self.confirm(challenge, code).status_code, 422)
        reviewer = get_user_model().objects.create_user(username='reviewer-fixture', is_staff=True)
        with self.assertRaisesMessage(ValidationError, 'FORBIDDEN'):
            update_deletion_request(row.pk, actor=reviewer, status='reviewing', progress_message='核验处理中')
        reviewer.user_permissions.add(Permission.objects.get(codename='manage_account_deletion'))
        reviewer = get_user_model().objects.get(pk=reviewer.pk)
        update_deletion_request(row.pk, actor=reviewer, status='reviewing', progress_message='核验处理中')
        row.refresh_from_db()
        self.assertIsNotNone(row.first_responded_at)
        with self.assertRaisesMessage(ValidationError, 'UNRESOLVED_ACCOUNT_RESPONSIBILITY'):
            update_deletion_request(row.pk, actor=reviewer, status='completed', progress_message='已核验')
        member.is_active = False
        member.save()
        update_deletion_request(row.pk, actor=reviewer, status='completed', progress_message='已完成')
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(BillingAccount.objects.get(pk=business.pk).name, 'Business')
        self.assertEqual(AccountDeletionAudit.objects.filter(request=row).count(), 3)

    def test_wrong_expired_undelivered_and_cross_session_codes_rejected(self):
        challenge, code = self.challenge()
        self.assertEqual(self.confirm(challenge, '000000' if code != '000000' else '111111').status_code, 422)
        other = Client()
        other.force_login(self.user)
        other.get('/api/account/profile')
        self.assertEqual(self.post('delete', {'challenge_id': challenge, 'code': code, 'confirm': True,
            'policy_version': 'fixture-reviewed-v1'}, other).status_code, 422)
        AccountDeletionChallenge.objects.filter(pk=challenge).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.confirm(challenge, code).status_code, 422)
        AccountDeletionChallenge.objects.filter(pk=challenge).update(expires_at=timezone.now()+timedelta(minutes=1), delivered=False)
        self.assertEqual(self.confirm(challenge, code).status_code, 422)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_login_code_cannot_delete_and_payload_consent_are_strict(self):
        challenge, code = self.challenge()
        login = LoginChallenge.objects.create(method='email', identifier=self.identity.identifier, code_digest='digest', session_digest='binding', delivered=True, expires_at=timezone.now()+timedelta(minutes=1))
        self.assertEqual(self.confirm(str(login.pk), code).status_code, 422)
        for data in ({}, {'challenge_id': challenge, 'code': code, 'confirm': 'true', 'policy_version': 'fixture-reviewed-v1'},
                     {'challenge_id': challenge, 'code': code, 'confirm': True, 'policy_version': 'old'}):
            self.assertEqual(self.post('delete', data).status_code, 422)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.user)
        self.assertEqual(self.post('deletion-challenges', {}, secure).status_code, 403)

    def test_retention_gate_and_delivery_failure_fail_closed(self):
        for config in ({'ACCOUNT_DELETION_ENABLED': False}, {'ACCOUNT_DELETION_RETENTION_DAYS': 0},
            {'ACCOUNT_DELETION_MARKER_RETENTION_DAYS': 0}, {'ACCOUNT_DELETION_POLICY_REVIEWED_AT': 'invalid'},
            {'ACCOUNT_DELETION_MARKER_KEY': ''}, {'AUTH_EMAIL_ENABLED': False}):
            with override_settings(**config):
                self.assertFalse(self.client.get('/api/account/deletion-eligibility').json()['available'])
                self.assertEqual(self.post('deletion-challenges', {}).status_code, 503)
        with patch('backend.account.send_mail', side_effect=OSError):
            self.assertEqual(self.post('deletion-challenges', {}).status_code, 503)
        self.assertFalse(AccountDeletionChallenge.objects.get().delivered)

    def test_retention_cleanup_is_dry_run_and_preserves_pending(self):
        challenge, code = self.challenge()
        self.confirm(challenge, code)
        row = AccountDeletionRequest.objects.get(user=self.user)
        row.retain_until = timezone.now()-timedelta(days=1)
        row.save()
        self.assertEqual(cleanup_deletion_data()['requests'], 1)
        self.assertTrue(AccountDeletionRequest.objects.filter(pk=row.pk).exists())
        cleanup_deletion_data(apply=True)
        self.assertFalse(AccountDeletionRequest.objects.filter(pk=row.pk).exists())
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_cleanup_command_defaults_to_dry_run(self):
        challenge, code = self.challenge()
        self.confirm(challenge, code)
        row = AccountDeletionRequest.objects.get(user=self.user)
        row.retain_until = timezone.now() - timedelta(days=1)
        row.save()
        output = StringIO()
        call_command('cleanup_account_deletions', stdout=output)
        self.assertEqual(json.loads(output.getvalue()), {'mode': 'dry-run', 'markers': 0, 'challenges': 0, 'requests': 1})
        self.assertTrue(AccountDeletionRequest.objects.filter(pk=row.pk).exists())
        call_command('cleanup_account_deletions', '--apply', stdout=StringIO())
        self.assertFalse(AccountDeletionRequest.objects.filter(pk=row.pk).exists())

    def test_business_days_include_official_adjusted_saturday(self):
        from datetime import datetime
        friday = datetime.fromisoformat('2026-10-09T10:00:00+08:00')
        self.assertEqual(_business_due(friday, 2).date().isoformat(), '2026-10-12')

    def test_deleting_personal_identity_preserves_business_inquiry_content(self):
        from .models import PurchaseInquiry
        business = BillingAccount.objects.create(kind='business', name='Business fixture')
        Membership.objects.create(account=business, user=self.user, role='member')
        inquiry = PurchaseInquiry.objects.create(account=business, created_by=self.user,
            company='Shared company fixture', seats=3, requirements='Shared business requirements',
            contact_email=self.identity.identifier, request_hash='fixture')
        challenge, code = self.challenge()
        self.assertEqual(self.confirm(challenge, code).status_code, 200)
        inquiry.refresh_from_db()
        self.assertEqual(inquiry.company, 'Shared company fixture')
        self.assertEqual(inquiry.requirements, 'Shared business requirements')
        self.assertEqual(inquiry.contact_email, '')

    def test_paid_credits_route_to_manual_without_balance_change(self):
        from .commerce.models import CreditBatch, CreditEntry
        batch = CreditBatch.objects.create(account=self.account, source='purchased', source_key='fixture-paid',
            initial_quantity='19', expires_at=timezone.now()+timedelta(days=10))
        CreditEntry.objects.create(batch=batch, kind='grant', quantity='19', event_key='grant')
        challenge, code = self.challenge()
        response = self.confirm(challenge, code)
        self.assertEqual(response.status_code, 202)
        self.assertIn('PAID_CREDITS_REMAINING', AccountDeletionRequest.objects.get(user=self.user).blockers)
        self.assertEqual(batch.entries.count(), 1)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_retry_throttle_failed_delivery_and_attempt_limit(self):
        challenge, code = self.challenge()
        self.assertEqual(self.post('deletion-challenges', {}).status_code, 429)
        AccountDeletionChallenge.objects.filter(pk=challenge).update(attempts=5)
        self.assertEqual(self.confirm(challenge, code).status_code, 422)

    def test_delays_require_explanation_and_future_date_and_pending_not_purged(self):
        business = BillingAccount.objects.create(kind='business', name='Business')
        Membership.objects.create(account=business, user=self.user, role='owner')
        challenge, code = self.challenge()
        self.confirm(challenge, code)
        row = AccountDeletionRequest.objects.get(user=self.user)
        reviewer = get_user_model().objects.create_user(username='reviewer-fixture', is_staff=True)
        reviewer.user_permissions.add(Permission.objects.get(codename='manage_account_deletion'))
        with self.assertRaisesMessage(ValidationError, 'DELAY_REASON_AND_DATE_REQUIRED'):
            update_deletion_request(row.pk, actor=reviewer, status='delayed', progress_message='等待移交')
        update_deletion_request(row.pk, actor=reviewer, status='delayed', progress_message='等待移交',
            delay_reason='企业所有权尚未移交', expected_completion_at=timezone.now()+timedelta(days=2))
        row.refresh_from_db()
        self.assertEqual(row.status, 'delayed')
        self.assertEqual(row.delay_reason, '企业所有权尚未移交')
        row.retain_until = timezone.now()-timedelta(days=1)
        row.save()
        cleanup_deletion_data(apply=True)
        self.assertTrue(AccountDeletionRequest.objects.filter(pk=row.pk).exists())


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row-lock concurrency')
@override_settings(ACCOUNT_DELETION_ENABLED=True, ACCOUNT_DELETION_POLICY_VERSION='fixture-reviewed-v1',
    ACCOUNT_DELETION_POLICY_REVIEWED_AT='2026-01-01', ACCOUNT_DELETION_RETENTION_DAYS=13,
    ACCOUNT_DELETION_MARKER_RETENTION_DAYS=17, ACCOUNT_DELETION_MARKER_KEY='fixture-only-' * 6,
    AUTH_EMAIL_ENABLED=True, AUTH_EMAIL_TRANSPORT='smtp', DEFAULT_FROM_EMAIL='sage@example.invalid',
    EMAIL_HOST='fixture.invalid', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AccountDeletionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='deletion-concurrency-fixture')
        LoginIdentity.objects.create(user=self.user, method='email', identifier='fixture@example.invalid')
        self.account = BillingAccount.objects.create(kind='personal', personal_owner=self.user, name='Personal')
        Membership.objects.create(account=self.account, user=self.user, role='owner')
        self.client.force_login(self.user)
        self.client.get('/api/account/profile')
        response = self.client.post('/api/account/deletion-challenges', '{}', content_type='application/json')
        self.assertEqual(response.status_code, 201)
        self.body = json.dumps({'challenge_id': response.json()['challenge_id'],
            'code': re.search(r'\b\d{6}\b', mail.outbox[-1].body).group(), 'confirm': True,
            'policy_version': 'fixture-reviewed-v1'})

    def delete(self):
        close_old_connections()
        try:
            return self.client.post('/api/account/delete', self.body, content_type='application/json').status_code
        finally:
            close_old_connections()

    def test_deletion_user_lock_prevents_concurrent_trial_start(self):
        from .account import _deletion_blockers
        from .trials import start_trial
        held, release, attempted = Event(), Event(), Event()

        def guarded(user):
            blockers = _deletion_blockers(user)
            held.set()
            if not release.wait(5):
                raise RuntimeError('Deletion test synchronization timed out')
            return blockers

        def start():
            close_old_connections()
            attempted.set()
            try:
                start_trial(get_user_model().objects.get(pk=self.user.pk), 'voc', 'fixture', 'concurrent')
                return 'started'
            except ValueError as exc:
                return str(exc)
            finally:
                close_old_connections()

        with patch('backend.account._deletion_blockers', side_effect=guarded), ThreadPoolExecutor(max_workers=2) as pool:
            deleting = pool.submit(self.delete)
            try:
                self.assertTrue(held.wait(5))
                starting = pool.submit(start)
                self.assertTrue(attempted.wait(5))
            finally:
                release.set()
            self.assertEqual(deleting.result(timeout=10), 200)
            self.assertEqual(starting.result(timeout=10), 'FORBIDDEN')
        self.assertFalse(TrialAttempt.objects.filter(user=self.user).exists())

    def test_committed_session_revocation_prevents_waiting_deletion(self):
        held, release, attempted = Event(), Event(), Event()
        key = self.client.session.session_key

        def revoke():
            close_old_connections()
            try:
                with transaction.atomic():
                    row = AccountSession.objects.select_for_update().get(session_key=key)
                    held.set()
                    if not release.wait(5):
                        raise RuntimeError('Revocation test synchronization timed out')
                    row.revoked_at = timezone.now()
                    row.save(update_fields=['revoked_at'])
                    Session.objects.filter(pk=key).delete()
            finally:
                close_old_connections()

        def waiting_delete():
            attempted.set()
            return self.delete()

        with ThreadPoolExecutor(max_workers=2) as pool:
            revoking = pool.submit(revoke)
            try:
                self.assertTrue(held.wait(5))
                deleting = pool.submit(waiting_delete)
                self.assertTrue(attempted.wait(5))
            finally:
                release.set()
            revoking.result(timeout=10)
            self.assertEqual(deleting.result(timeout=10), 401)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertFalse(AccountDeletionRequest.objects.filter(user=self.user).exists())
