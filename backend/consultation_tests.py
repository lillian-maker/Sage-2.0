"""Isolated sales intake/admin/retention tests; fixtures never contact sales."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, close_old_connections, transaction
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from .consultations import normalized_fields, purge_record, submit, update_sales
from .models import Consultation, ConsultationAudit


class ConsultationTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.session = self.client.get('/api/auth/session').json()
        self.fields = {'company': 'Fixture brand', 'phone': '+86 138-0000-0000',
                       'email': 'sales-fixture@example.invalid', 'question': 'Fixture question'}
        self.sales = get_user_model().objects.create_user(username='sales-fixture', is_staff=True)
        self.sales.user_permissions.add(*Permission.objects.filter(codename__in=(
            'manage_consultation', 'purge_consultation'), content_type__app_label='backend'))

    def submit(self, fields=None, **headers):
        return self.client.post('/api/consultations', json.dumps(fields or self.fields),
            content_type='application/json', HTTP_X_CSRFTOKEN=self.session['csrf_token'],
            HTTP_IDEMPOTENCY_KEY='consultation-fixture-01', **headers)

    def record(self):
        response = self.submit()
        self.assertEqual(response.status_code, 201, response.content)
        return Consultation.objects.get(pk=response.json()['receipt_id'])

    def test_anonymous_receipt_and_retry(self):
        record = self.record()
        self.assertEqual(record.phone, '+8613800000000')
        self.assertEqual(self.submit().json()['receipt_id'], str(record.pk))
        self.assertEqual(Consultation.objects.count(), 1)
        self.assertEqual(ConsultationAudit.objects.count(), 1)
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])

    def test_different_content_same_key_conflicts(self):
        self.record()
        fields = dict(self.fields, question='Changed question')
        self.assertEqual(self.submit(fields).status_code, 409)
        self.assertEqual(Consultation.objects.count(), 1)

    def test_csrf_and_cross_origin_are_required(self):
        missing = self.client.post('/api/consultations', json.dumps(self.fields), content_type='application/json')
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(self.submit(HTTP_ORIGIN='https://attacker.invalid').status_code, 403)
        self.assertEqual(Consultation.objects.count(), 0)

    def test_bad_input_and_public_query_are_rejected(self):
        for update in ({'phone': '13800000000'}, {'email': 'invalid'}, {'question': 'x' * 2001},
                       {'company': ''}, {'question': ['invalid']}):
            self.assertEqual(self.submit(dict(self.fields, **update)).status_code, 400)
        self.assertEqual(self.client.get('/api/consultations').status_code, 405)
        self.assertEqual(Consultation.objects.count(), 0)

    def test_optional_email_and_international_phone(self):
        self.assertEqual(normalized_fields(dict(self.fields, phone='+1 (202) 555-0100', email=''))['phone'],
                         '+12025550100')

    def test_rate_limit_and_database_failure(self):
        for _ in range(10):
            self.assertEqual(self.submit().status_code, 201)
        self.assertEqual(self.submit().status_code, 429)
        with patch('backend.consultations.throttle', side_effect=DatabaseError('fixture failure')):
            response = self.submit()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('fixture failure', response.content.decode())

    def test_sales_permission_owner_and_stale_edit(self):
        record = self.record()
        ordinary = get_user_model().objects.create_user(username='ordinary-fixture')
        with self.assertRaisesMessage(ValidationError, 'CONSULTATION_FORBIDDEN'):
            update_sales(record.pk, actor=ordinary, version=0, owner_id=None, status='completed', notes='')
        with self.assertRaisesMessage(ValidationError, 'CONSULTATION_INVALID_OWNER'):
            update_sales(record.pk, actor=self.sales, version=0, owner_id=ordinary.pk,
                         status='completed', notes='')
        updated = update_sales(record.pk, actor=self.sales, version=0, owner_id=self.sales.pk,
                               status='completed', notes='Private fixture note')
        self.assertEqual(updated.purge_at, updated.completed_at + timedelta(days=90))
        with self.assertRaisesMessage(ValidationError, 'CONSULTATION_EDIT_CONFLICT'):
            update_sales(record.pk, actor=self.sales, version=0, owner_id=None, status='pending', notes='')
        self.assertNotIn('Private fixture note', str(list(ConsultationAudit.objects.values())))

    def test_retention_boundary_dryrun_apply_and_replay(self):
        record = self.record()
        now = timezone.now()
        Consultation.objects.filter(pk=record.pk).update(status='completed',
            completed_at=now-timedelta(days=90), purge_at=now)
        self.assertFalse(purge_record(record.pk, scheduled=True, now=now-timedelta(seconds=1)))
        output = StringIO()
        with patch('backend.management.commands.purge_consultations.timezone.now', return_value=now):
            call_command('purge_consultations', stdout=output)
        self.assertEqual(json.loads(output.getvalue()), {'mode': 'dry-run', 'eligible': 1, 'purged': 0})
        record.refresh_from_db()
        self.assertTrue(record.question)
        self.assertTrue(purge_record(record.pk, scheduled=True, now=now))
        self.assertFalse(purge_record(record.pk, scheduled=True, now=now))
        record.refresh_from_db()
        self.assertEqual((record.company, record.phone, record.email, record.question, record.notes,
                          record.request_hash), ('', '', '', '', '', ''))
        self.assertEqual(self.submit().status_code, 409)

    def test_early_purge_permission_and_reopen_cannot_revive(self):
        record = self.record()
        with self.assertRaisesMessage(ValidationError, 'CONSULTATION_FORBIDDEN'):
            purge_record(record.pk, actor=get_user_model().objects.create_user(username='purge-denied'))
        self.assertTrue(purge_record(record.pk, actor=self.sales))
        with self.assertRaisesMessage(ValidationError, 'CONSULTATION_ALREADY_PURGED'):
            update_sales(record.pk, actor=self.sales, version=1, owner_id=None, status='pending', notes='')

    def test_reopen_before_cleanup_resets_deadline_and_command_apply(self):
        record = self.record()
        update_sales(record.pk, actor=self.sales, version=0, owner_id=None, status='completed', notes='')
        reopened = update_sales(record.pk, actor=self.sales, version=1, owner_id=None,
                                status='in_progress', notes='')
        self.assertIsNone(reopened.purge_at)
        output = StringIO()
        call_command('purge_consultations', apply=True, stdout=output)
        self.assertEqual(json.loads(output.getvalue())['purged'], 0)
        update_sales(record.pk, actor=self.sales, version=reopened.version, owner_id=None,
                     status='completed', notes='')
        now = timezone.now()
        Consultation.objects.filter(pk=record.pk).update(completed_at=now-timedelta(days=90), purge_at=now)
        output = StringIO()
        call_command('purge_consultations', apply=True, stdout=output)
        self.assertEqual(json.loads(output.getvalue())['purged'], 1)

    def test_database_constraints_reject_invalid_status_and_deadlines(self):
        record = self.record()
        for fields in ({'status': 'unknown'}, {'status': 'completed'},
                       {'purge_at': timezone.now()}, {'completed_at': timezone.now()}):
            with self.subTest(fields=tuple(fields)):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Consultation.objects.filter(pk=record.pk).update(**fields)
        record.refresh_from_db()
        self.assertEqual(record.status, 'pending')
        self.assertIsNone(record.completed_at)
        self.assertIsNone(record.purge_at)

    def test_admin_access_and_purge_confirmation(self):
        record = self.record()
        model_admin = admin.site._registry[Consultation]
        request = RequestFactory().post('/sales/backend/consultation/')
        request.user = self.sales
        self.assertTrue(model_admin.has_view_permission(request))
        confirmation = model_admin.purge_personal_information(request, Consultation.objects.all())
        self.assertContains(confirmation, 'confirm_purge')
        record.refresh_from_db()
        self.assertIsNone(record.purged_at)
        request.user = get_user_model().objects.create_user(username='admin-denied', is_staff=True)
        self.assertFalse(model_admin.has_view_permission(request))
        self.assertEqual(model_admin.purge_personal_information(request, Consultation.objects.all()).status_code, 403)

    def test_sales_admin_form_saves_and_detects_stale_browser(self):
        record = self.record()
        client = Client()
        client.force_login(self.sales)
        path = f'/sales/backend/consultation/{record.pk}/change/'
        self.assertEqual(client.get(path).status_code, 200)
        response = client.post(path, {'owner': self.sales.pk, 'status': 'completed',
                                     'notes': 'Internal fixture', 'edit_version': 0, '_save': 'Save'})
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.status, 'completed')
        response = client.post(path, {'owner': '', 'status': 'pending', 'notes': '',
                                     'edit_version': 0, '_save': 'Save'})
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.status, 'completed')
        self.assertEqual(record.version, 1)


class ConsultationConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_same_session_retry_from_two_ips_creates_one_record(self):
        from django.contrib.sessions.backends.db import SessionStore
        session = SessionStore()
        session['fixture'] = True
        session.save()
        fields = {'company': 'Concurrent fixture', 'phone': '+12025550100',
                  'email': '', 'question': 'Concurrent fixture'}

        def worker(ip):
            close_old_connections()
            try:
                request = RequestFactory().post('/api/consultations', json.dumps(fields),
                    content_type='application/json', HTTP_IDEMPOTENCY_KEY='concurrent-fixture-key',
                    REMOTE_ADDR=ip)
                request.session = SessionStore(session_key=session.session_key)
                request.request_id = uuid.uuid4()
                response = submit(request)
                return response.status_code, json.loads(response.content)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(worker, ('127.0.0.1', '127.0.0.2')))
        self.assertEqual([item[0] for item in responses], [201, 201])
        self.assertEqual(responses[0][1]['receipt_id'], responses[1][1]['receipt_id'])
        self.assertEqual(Consultation.objects.count(), 1)
        self.assertEqual(ConsultationAudit.objects.count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_sales_edits_do_not_overwrite(self):
        user = get_user_model().objects.create_user(username='concurrent-sales', is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename='manage_consultation',
                                                        content_type__app_label='backend'))
        record = Consultation.objects.create(company='Concurrent fixture', phone='+12025550100',
            email='', question='Concurrent fixture', request_key='a' * 64, request_hash='b' * 64)

        def worker(status):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=user.pk)
                try:
                    update_sales(record.pk, actor=actor, version=0, owner_id=None, status=status, notes='')
                    return 'saved'
                except ValidationError as exc:
                    return exc.messages[0]
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(worker, ('in_progress', 'completed')))
        self.assertCountEqual(outcomes, ['saved', 'CONSULTATION_EDIT_CONFLICT'])
        self.assertEqual(ConsultationAudit.objects.count(), 1)
