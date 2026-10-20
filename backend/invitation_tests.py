"""Invitation fixtures stay in isolated databases; no registration API is opened."""
import csv
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from pathlib import Path
from threading import Barrier, Event, local
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, IntegrityError, close_old_connections, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from .invitations import create_registered_user, normalize_code, validate_code
from .models import AccountSession, BillingAccount, InvitationCode, InvitationCodeQuerySet, RegistrationAttribution, TrialAttempt


@override_settings(INVITATION_CODES_READY=True)
class InvitationTests(TestCase):
    def setUp(self):
        self.code = InvitationCode.objects.create(code='FIXTURE1', blogger='Fixture blogger')

    def test_ascii_format_and_normalization(self):
        for raw in ('ABC123', 'a' * 16, ' \tfixture1\n'):
            self.assertEqual(normalize_code(raw), raw.strip().upper())
        for raw in ('abc12', 'x' * 17, 'ＡBC123', 'ABC_123', 'ABC 123', 123, None, [], 'éabc123'):
            with self.subTest(raw=raw), self.assertRaisesMessage(ValueError, 'INVALID_INVITATION_CODE'):
                normalize_code(raw)
        self.assertEqual(normalize_code(' \t '), '')

    @override_settings(INVITATION_CODES_READY=False)
    def test_blank_does_not_require_ready_list_or_read_codes(self):
        with patch('backend.invitations.InvitationCode.objects.filter', side_effect=DatabaseError):
            self.assertIsNone(validate_code(' '))
            user = create_registered_user(username='blank-fixture', invitation_code='')
        self.assertFalse(RegistrationAttribution.objects.filter(user=user).exists())
        with self.assertRaisesMessage(ValueError, 'INVITATION_CODES_UNAVAILABLE'):
            validate_code('FIXTURE1')

    def test_unknown_inactive_and_final_recheck(self):
        self.assertEqual(validate_code(' fixture1 ').pk, self.code.pk)
        self.code.is_active = False
        self.code.save(update_fields=['is_active'])
        for value in ('FIXTURE1', 'UNKNOWN1'):
            with self.assertRaisesMessage(ValueError, 'INVALID_INVITATION_CODE'):
                create_registered_user(username='rejected-fixture', invitation_code=value)
        self.assertFalse(get_user_model().objects.filter(username='rejected-fixture').exists())

    def test_reusable_code_new_users_only_and_atomic_failure(self):
        for username in ('first-fixture', 'second-fixture'):
            user = create_registered_user(username=username, invitation_code=' fixture1 ')
            self.assertEqual(user.registrationattribution.invitation_code_id, 'FIXTURE1')
            self.assertEqual(user.registrationattribution.registered_at, user.date_joined)
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_registered_user(username='first-fixture', invitation_code='FIXTURE1')
        with patch('backend.invitations.RegistrationAttribution.objects.create', side_effect=RuntimeError('fixture failure')):
            with self.assertRaises(RuntimeError):
                create_registered_user(username='rollback-fixture', invitation_code='FIXTURE1')
        self.assertFalse(get_user_model().objects.filter(username='rollback-fixture').exists())
        self.assertEqual(RegistrationAttribution.objects.count(), 2)
        self.assertFalse(BillingAccount.objects.exists())
        self.assertFalse(TrialAttempt.objects.exists())

    def test_primary_key_inputs_cannot_overwrite_existing_user_or_attribute_it(self):
        user_model = get_user_model()
        old = user_model.objects.create_user(username='preserved-fixture',
            email='preserved@example.invalid', password='Fixture123!')
        session_client = Client()
        session_client.force_login(old)
        session_key = session_client.session.session_key
        AccountSession.objects.create(user=old, session_key=session_key, client='Fixture browser')
        old.refresh_from_db()
        before_user = user_model.objects.filter(pk=old.pk).values().get()
        before_sessions = list(Session.objects.order_by('pk').values())
        before_account_sessions = list(AccountSession.objects.order_by('pk').values())
        before_codes = list(InvitationCode.objects.order_by('pk').values())
        for code in ('FIXTURE1', ''):
            for key in sorted({'pk', user_model._meta.pk.name, user_model._meta.pk.attname}):
                with self.subTest(invitation_code=code, primary_key_field=key), transaction.atomic():
                    with self.assertRaisesMessage(ValueError, 'INVALID_REGISTRATION_FIELDS'):
                        create_registered_user(invitation_code=code, username='replacement-fixture',
                            email='replacement@example.invalid', password='Changed123!', **{key: old.pk})
                    self.assertEqual(user_model.objects.filter(pk=old.pk).values().get(), before_user)
                    self.assertEqual(user_model.objects.count(), 1)
                    self.assertFalse(RegistrationAttribution.objects.exists())
                    self.assertEqual(list(Session.objects.order_by('pk').values()), before_sessions)
                    self.assertEqual(list(AccountSession.objects.order_by('pk').values()), before_account_sessions)
                    self.assertEqual(list(InvitationCode.objects.order_by('pk').values()), before_codes)
                    self.assertEqual(int(session_client.session['_auth_user_id']), old.pk)
                    self.assertEqual(session_client.session['_auth_user_hash'], old.get_session_auth_hash())

    def test_code_primary_key_and_bulk_mapping_cannot_change(self):
        self.code.code = 'NEWCODE1'
        with self.assertRaisesMessage(ValueError, 'IMMUTABLE_INVITATION_MAPPING'):
            self.code.save()
        self.assertFalse(InvitationCode.objects.filter(pk='NEWCODE1').exists())
        self.code = InvitationCode.objects.get(pk='FIXTURE1')
        self.code.blogger = 'Other'
        with self.assertRaisesMessage(ValueError, 'IMMUTABLE_INVITATION_MAPPING'):
            InvitationCode.objects.bulk_update([self.code], ['blogger'])
        with self.assertRaisesMessage(ValueError, 'IMMUTABLE_INVITATION_MAPPING'):
            InvitationCode.objects.bulk_create([InvitationCode(code='NEWCODE1', blogger='Other')])
        self.code.refresh_from_db()
        self.assertEqual(self.code.blogger, 'Fixture blogger')

    def test_database_rejects_noncanonical_code_and_duplicate_user(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            # Bypass only the model guard to verify the actual database check.
            from django.db.models import Model
            Model.save(InvitationCode(code='lowercase', blogger='Fixture'), force_insert=True)
        user = create_registered_user(username='unique-fixture', invitation_code='FIXTURE1')
        with self.assertRaises(IntegrityError), transaction.atomic():
            RegistrationAttribution.objects.create(user=user, invitation_code=self.code, registered_at=user.date_joined)

    def test_new_instance_cannot_upsert_after_a_stale_mapping_read(self):
        with patch('backend.models.InvitationCodeQuerySet.first', return_value=None):
            with self.assertRaises(IntegrityError), transaction.atomic():
                InvitationCode(code='FIXTURE1', blogger='Other').save()
        self.code.refresh_from_db()
        self.assertEqual(self.code.blogger, 'Fixture blogger')

    def test_new_attribution_cannot_upsert_after_a_stale_primary_key_read(self):
        first = create_registered_user(username='first-attribution-fixture', invitation_code='FIXTURE1')
        other = get_user_model().objects.create_user(username='other-attribution-fixture')
        record = first.registrationattribution
        with patch('backend.models.RegistrationAttributionQuerySet.exists', return_value=False):
            with self.assertRaises(IntegrityError), transaction.atomic():
                RegistrationAttribution(pk=record.pk, user=other, invitation_code=self.code,
                    registered_at=other.date_joined).save()
        record.refresh_from_db()
        self.assertEqual(record.user_id, first.pk)
        self.assertFalse(RegistrationAttribution.objects.filter(user=other).exists())

    def test_permanent_mapping_and_immutable_attribution(self):
        user = create_registered_user(username='immutable-fixture', invitation_code='FIXTURE1')
        record = user.registrationattribution
        for operation in (
            lambda: RegistrationAttribution.objects.filter(pk=record.pk).update(registered_at=timezone.now()),
            lambda: RegistrationAttribution.objects.filter(pk=record.pk).delete(),
            lambda: RegistrationAttribution.objects.bulk_update([record], ['registered_at']),
            lambda: RegistrationAttribution.objects.bulk_create([record], update_conflicts=True,
                update_fields=['registered_at'], unique_fields=['user']),
            lambda: record.delete(),
            lambda: InvitationCode.objects.filter(pk=self.code.pk).update(blogger='Other'),
            lambda: InvitationCode.objects.filter(pk=self.code.pk).delete(),
            lambda: self.code.delete(),
        ):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation()
        record.registered_at += timedelta(seconds=1)
        with self.assertRaisesMessage(ValueError, 'IMMUTABLE_REGISTRATION_ATTRIBUTION'):
            record.save()
        with self.assertRaisesMessage(ValueError, 'IMMUTABLE_INVITATION_MAPPING'):
            InvitationCode(code='fixture1', blogger='Other').save()
        self.code.is_active = False
        self.code.save()
        self.assertEqual(RegistrationAttribution.objects.count(), 1)


class InvitationApiTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.token = self.client.get('/api/auth/session').json()['csrf_token']

    def submit(self, code='', data=None, **headers):
        return self.client.post('/api/auth/invitation-check', json.dumps(
            {'invitation_code': code} if data is None else data), content_type='application/json',
            HTTP_X_CSRFTOKEN=self.token, **headers)

    def test_unready_blank_and_safe_public_response(self):
        self.assertEqual(self.submit().json(), {'valid': True})
        self.assertEqual(self.submit('FIXTURE1').status_code, 503)
        self.assertEqual(self.submit('bad').status_code, 422)
        with override_settings(INVITATION_CODES_READY=True):
            InvitationCode.objects.create(code='FIXTURE1', blogger='Private fixture blogger')
            result = self.submit(' fixture1 ')
            self.assertEqual(result.json(), {'valid': True})
            self.assertEqual(self.submit('UNKNOWN1').json()['error']['message'], '无效邀请码，请检查')
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        self.assertEqual(RegistrationAttribution.objects.count(), 0)

    def test_csrf_origin_strict_payload_methods_and_no_write_endpoints(self):
        self.assertEqual(self.client.post('/api/auth/invitation-check', '{}', content_type='application/json').status_code, 403)
        self.assertEqual(self.submit(HTTP_ORIGIN='https://attacker.invalid').status_code, 403)
        for data in ({}, {'invitation_code': '', 'user_id': 1}, {'invitation_code': None}, []):
            self.assertEqual(self.submit(data=data).status_code, 422)
        self.assertEqual(self.client.get('/api/auth/invitation-check').status_code, 405)
        self.assertNotEqual(self.submit(data={'invitation_code': '', 'username': 'evil'}).status_code, 200)
        self.assertEqual(self.client.post('/api/auth/registration-attributions', '{}',
            content_type='application/json', HTTP_X_CSRFTOKEN=self.token).status_code, 401)

    def test_rate_limit_and_database_fault(self):
        for _ in range(20):
            self.assertEqual(self.submit().status_code, 200)
        self.assertEqual(self.submit().status_code, 429)
        with patch('backend.views.throttle', side_effect=DatabaseError('private fixture failure')):
            response = self.submit()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private fixture failure', response.content.decode())

    @override_settings(INVITATION_CODES_READY=True)
    def test_code_store_fault_is_service_failure(self):
        with patch('backend.invitations.InvitationCode.objects.filter', side_effect=DatabaseError):
            self.assertEqual(self.submit('FIXTURE1').status_code, 503)

    def test_old_verify_rejects_invitation_parameter(self):
        response = self.client.post('/api/auth/verify', json.dumps({
            'challenge_id': 'fixture', 'code': '123456', 'invitation_code': ''}),
            content_type='application/json', HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['error']['code'], 'INVALID_REQUEST')


class InvitationImportTests(TestCase):
    def import_rows(self, rows, apply=False):
        with tempfile.TemporaryDirectory(prefix='sage-invitation-fixture-') as directory:
            path = Path(directory) / 'codes.csv'
            with path.open('w', encoding='utf-8', newline='') as stream:
                csv.writer(stream).writerows(rows)
            output = StringIO()
            call_command('import_invitation_codes', str(path), apply=apply, stdout=output)
            return output.getvalue()

    def test_dry_run_apply_and_counts_only(self):
        rows = [('邀请码', '博主', '状态'), (' fixture1 ', 'Private fixture blogger', '启用')]
        output = self.import_rows(rows)
        self.assertEqual(InvitationCode.objects.count(), 0)
        self.assertNotIn('FIXTURE1', output)
        self.assertNotIn('Private fixture blogger', output)
        self.assertEqual(json.loads(output)['mode'], 'dry-run')
        self.import_rows(rows, True)
        self.assertTrue(InvitationCode.objects.get(pk='FIXTURE1').is_active)
        from django.conf import settings
        self.assertFalse(settings.INVITATION_CODES_READY)

    def test_header_encoding_and_unreadable_file_fail_safely(self):
        for rows in ([], [('code', 'blogger', 'status')], [('邀请码', '博主', '状态'), ('FIXTURE1', 'Fixture\tname', '启用')]):
            with self.subTest(rows=rows), self.assertRaisesMessage(CommandError, 'INVITATION_IMPORT_FAILED'):
                self.import_rows(rows, True)
        with self.assertRaisesMessage(CommandError, 'INVITATION_IMPORT_FAILED'):
            call_command('import_invitation_codes', '/private/tmp/sage-nonexistent-fixture-codes.csv', stdout=StringIO())
        self.assertFalse(InvitationCode.objects.exists())

    def test_batch_invalid_duplicate_and_mapping_change_have_no_partial_writes(self):
        header = ('邀请码', '博主', '状态')
        for bad in (('BAD', 'Fixture', '启用'), ('OTHER123', '', '启用'),
                    ('OTHER123', 'Fixture', 'active'), ('OTHER123', 'Fixture', '启用', 'extra')):
            with self.subTest(bad=bad), self.assertRaises(CommandError):
                self.import_rows([header, ('FIXTURE1', 'Fixture', '启用'), bad], True)
            self.assertFalse(InvitationCode.objects.exists())
        with self.assertRaises(CommandError):
            self.import_rows([header, ('FIXTURE1', 'Fixture', '启用'), ('fixture1', 'Fixture', '停用')], True)
        self.assertFalse(InvitationCode.objects.exists())
        InvitationCode.objects.create(code='FIXTURE1', blogger='Original')
        with self.assertRaises(CommandError):
            self.import_rows([header, ('OTHER123', 'Other', '启用'), ('FIXTURE1', 'Changed', '停用')], True)
        self.assertEqual(InvitationCode.objects.count(), 1)
        self.assertTrue(InvitationCode.objects.get(pk='FIXTURE1').is_active)

    @override_settings(INVITATION_CODES_READY=True)
    def test_disable_preserves_history_missing_row_and_atomic_database_error(self):
        InvitationCode.objects.create(code='FIXTURE1', blogger='Original')
        InvitationCode.objects.create(code='OMITTED1', blogger='Other')
        create_registered_user(username='history-fixture', invitation_code='FIXTURE1')
        self.import_rows([('邀请码', '博主', '状态'), ('FIXTURE1', 'Original', '停用')], True)
        self.assertFalse(InvitationCode.objects.get(pk='FIXTURE1').is_active)
        self.assertTrue(InvitationCode.objects.get(pk='OMITTED1').is_active)
        self.assertEqual(RegistrationAttribution.objects.count(), 1)
        with patch('backend.invitations.InvitationCode.objects.create', side_effect=DatabaseError):
            with self.assertRaises(CommandError):
                self.import_rows([('邀请码', '博主', '状态'), ('FIXTURE1', 'Original', '启用'),
                    ('NEWCODE1', 'New', '启用')], True)
        self.assertFalse(InvitationCode.objects.get(pk='FIXTURE1').is_active)


@override_settings(INVITATION_CODES_READY=True)
@override_settings(PASSKEY_RP_ID='sage.example.invalid',PASSKEY_ORIGIN='https://sage.example.invalid')
class InvitationAdminTests(TestCase):
    base = '/sales/backend/registrationattribution/'

    def setUp(self):
        InvitationCode.objects.create(code='FIXTURE1', blogger='=HYPERLINK("fixture")')
        InvitationCode.objects.create(code='FIXTURE2', blogger='Other fixture')
        for index in range(31):
            create_registered_user(username=f'private-user-{index}', email=f'private-{index}@example.invalid',
                invitation_code='FIXTURE1' if index < 30 else 'FIXTURE2')
        self.staff = get_user_model().objects.create_user(username='viewer-fixture', is_staff=True)
        self.client.force_login(self.staff)

    def grant(self, *codes):
        self.staff.user_permissions.add(*Permission.objects.filter(content_type__app_label='backend', codename__in=codes))
        # Fixture proof state only; standard WebAuthn signatures are exercised in security_tests.
        from .models import AccountPasskey,AccountSession
        AccountPasskey.objects.get_or_create(user=self.staff,credential_id='admin-fixture-'+str(self.staff.pk),
            defaults={'public_key':'fixture','label':'Synthetic admin'})
        self.client.get('/api/account/profile')
        AccountSession.objects.filter(user=self.staff,session_key=self.client.session.session_key).update(
            sensitive_verified_at=timezone.now(),sensitive_method='passkey')

    def test_export_requires_fresh_device_confirmation_in_addition_to_permissions(self):
        from .models import AccountSession
        self.grant('view_registration_attribution','export_registration_attribution')
        AccountSession.objects.filter(user=self.staff).update(sensitive_verified_at=None)
        self.assertEqual(self.client.get(self.base+'export/').status_code,403)
        AccountSession.objects.filter(user=self.staff).update(sensitive_verified_at=timezone.now()-timedelta(minutes=15))
        self.assertEqual(self.client.get(self.base+'export/').status_code,403)

    def test_independent_view_and_export_permissions(self):
        self.grant('export_registration_attribution')
        self.assertEqual(self.client.get(self.base + 'export/').status_code, 403)
        self.staff.user_permissions.clear()
        self.grant('manage_consultation')
        self.assertEqual(self.client.get(self.base).status_code, 403)
        self.assertEqual(self.client.get(self.base + 'export/').status_code, 403)
        self.grant('view_registration_attribution')
        self.assertEqual(self.client.get(self.base).status_code, 200)
        self.assertEqual(self.client.get(self.base + 'export/').status_code, 403)
        self.grant('export_registration_attribution')
        self.assertEqual(self.client.get(self.base + 'export/').status_code, 200)
        self.staff.is_staff = False
        self.staff.save()
        self.assertEqual(self.client.get(self.base).status_code, 302)

    def test_all_formula_prefixes_are_escaped_in_four_column_csv(self):
        self.grant('view_registration_attribution', 'export_registration_attribution')
        prefixes = ('=', '+', '-', '@', '  =')
        for index, prefix in enumerate(prefixes):
            code = f'FORMULA{index}'
            InvitationCode.objects.create(code=code, blogger=prefix + 'fixture')
            create_registered_user(username=f'formula-fixture-{index}', invitation_code=code)
            response = self.client.get(self.base + 'export/', {'invitation_code__code': code})
            row = list(csv.reader(StringIO(response.content.decode('utf-8-sig'))))[1]
            self.assertEqual(row[2], "'" + prefix + 'fixture')
            self.assertEqual(len(row), 4)

    def test_list_details_only_four_columns_and_no_mutations(self):
        self.grant('view_registration_attribution', 'export_registration_attribution')
        self.assertNotIn(InvitationCode, admin.site._registry)
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'private-user-')
        self.assertNotContains(response, '@example.invalid')
        record = RegistrationAttribution.objects.first()
        detail = self.client.get(f'{self.base}{record.pk}/change/')
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, 'private-user-')
        self.assertNotContains(detail, '@example.invalid')
        for url in ('add/', f'{record.pk}/change/', f'{record.pk}/delete/', 'export/', ''):
            self.assertIn(self.client.post(self.base + url, {'action': 'delete_selected', '_selected_action': record.pk}).status_code, (403, 405))
        self.assertEqual(RegistrationAttribution.objects.count(), 31)

    def test_full_filtered_csv_dates_and_formula_escaping(self):
        self.grant('view_registration_attribution', 'export_registration_attribution')
        filters = {'invitation_code__code': 'FIXTURE1', 'invitation_code__blogger': '=HYPERLINK("fixture")',
            'registered_at__gte': (timezone.now() - timedelta(days=1)).isoformat(),
            'registered_at__lt': (timezone.now() + timedelta(days=1)).isoformat()}
        listing = self.client.get(self.base, filters)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.context['cl'].result_count, 30)
        response = self.client.get(self.base + 'export/', dict(filters, p='1'))
        rows = list(csv.reader(StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(rows[0], ['用户编号', '邀请码', '对应博主', '注册时间'])
        self.assertEqual(len(rows), 31)
        self.assertTrue(all(len(row) == 4 and row[2].startswith("'=") for row in rows[1:]))
        for query in ({'registered_at__gte': 'bad'}, {'unknown': 'bad'},
                      {'invitation_code__code': ['FIXTURE1', 'FIXTURE2']}, {'user__email': 'private@example.invalid'}):
            self.assertEqual(self.client.get(self.base + 'export/', query).status_code, 400)
        empty = self.client.get(self.base + 'export/', {'invitation_code__code': 'UNKNOWN1'})
        self.assertEqual(len(list(csv.reader(StringIO(empty.content.decode('utf-8-sig'))))), 1)


@override_settings(INVITATION_CODES_READY=True)
class InvitationConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_first_saves_cannot_reassign_permanent_blogger(self):
        both_read_missing, winner_committed, thread = Barrier(2), Event(), local()
        original_first = InvitationCodeQuerySet.first

        def synchronized_first(query):
            value = original_first(query)
            if value is None:
                both_read_missing.wait(timeout=5)
                if thread.loser and not winner_committed.wait(5):
                    raise RuntimeError('fixture winner timeout')
            return value

        def save(blogger, loser):
            close_old_connections()
            thread.loser = loser
            try:
                InvitationCode(code='RACECODE', blogger=blogger).save()
                return 'created'
            except IntegrityError:
                return 'duplicate'
            finally:
                if not loser:
                    winner_committed.set()
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(
                InvitationCodeQuerySet, 'first', synchronized_first):
            winner = pool.submit(save, 'Original fixture', False)
            loser = pool.submit(save, 'Other fixture', True)
            self.assertEqual(winner.result(timeout=10), 'created')
            self.assertEqual(loser.result(timeout=10), 'duplicate')
        self.assertEqual(InvitationCode.objects.get(pk='RACECODE').blogger, 'Original fixture')

    @skipUnlessDBFeature('has_select_for_update')
    def test_final_validation_waits_for_committed_disable(self):
        code = InvitationCode.objects.create(code='FIXTURE1', blogger='Fixture')
        self.assertEqual(validate_code(code.pk).pk, code.pk)
        entered = Event()

        def register():
            close_old_connections()
            entered.set()
            try:
                create_registered_user(username='wait-fixture', invitation_code=code.pk)
            except ValueError as exc:
                return str(exc)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                locked = InvitationCode.objects.select_for_update().get(pk=code.pk)
                future = pool.submit(register)
                self.assertTrue(entered.wait(5))
                self.assertFalse(future.done())
                locked.is_active = False
                locked.save(update_fields=['is_active'])
            self.assertEqual(future.result(timeout=10), 'INVALID_INVITATION_CODE')
        self.assertFalse(RegistrationAttribution.objects.exists())
        self.assertFalse(get_user_model().objects.filter(username='wait-fixture').exists())

    @skipUnlessDBFeature('has_select_for_update')
    def test_registration_holds_code_lock_through_attribution_commit(self):
        InvitationCode.objects.create(code='FIXTURE1', blogger='Fixture')
        reached_write, release, disabled = Event(), Event(), Event()
        original_create = RegistrationAttribution.objects.create

        def blocking_create(**kwargs):
            reached_write.set()
            if not release.wait(10):
                raise RuntimeError('fixture lock timeout')
            return original_create(**kwargs)

        def register():
            close_old_connections()
            try:
                return create_registered_user(username='locked-fixture', invitation_code='FIXTURE1').pk
            finally:
                close_old_connections()

        def disable():
            close_old_connections()
            try:
                with transaction.atomic():
                    code = InvitationCode.objects.select_for_update().get(pk='FIXTURE1')
                    code.is_active = False
                    code.save(update_fields=['is_active'])
                disabled.set()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool, patch(
                'backend.invitations.RegistrationAttribution.objects.create', side_effect=blocking_create):
            registration = pool.submit(register)
            try:
                self.assertTrue(reached_write.wait(5))
                deactivation = pool.submit(disable)
                self.assertFalse(disabled.wait(.2))
            finally:
                release.set()
            user_id = registration.result(timeout=10)
            deactivation.result(timeout=10)
        self.assertTrue(RegistrationAttribution.objects.filter(user_id=user_id).exists())
        self.assertFalse(InvitationCode.objects.get(pk='FIXTURE1').is_active)
