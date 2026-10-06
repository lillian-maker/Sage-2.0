"""Billing writes must serialize with server-side device revocation."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import close_old_connections, connection, transaction
from django.test import Client, RequestFactory, TransactionTestCase
from django.utils import timezone

from backend.models import AccountSession, BillingAccount, Membership, PurchaseInquiry
from backend.views import api


class BillingSessionRevocationTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='billing-session-fixture')
        self.account = BillingAccount.objects.create(kind='personal', name='Fixture', personal_owner=self.user)
        Membership.objects.create(user=self.user, account=self.account, role='owner')
        client = Client()
        client.force_login(self.user)
        session = client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        self.session_key = session.session_key
        self.metadata = AccountSession.objects.create(user=self.user, session_key=self.session_key, client='Fixture')

    def request(self, path, data):
        request = RequestFactory().post('/api/' + path, json.dumps(data), content_type='application/json',
            HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        request.user = self.user
        request.session = SessionStore(session_key=self.session_key)
        request.session.load()
        request.request_id = uuid.uuid4()
        return request

    def test_revoked_inflight_billing_write_is_denied(self):
        request = self.request('billing/purchase-inquiries', {'company': 'Fixture', 'seats': 2,
            'requirements': 'Fixture requirements', 'contact_email': 'fixture@example.invalid'})
        AccountSession.objects.filter(pk=self.metadata.pk).update(revoked_at=timezone.now())
        Session.objects.filter(pk=self.session_key).delete()
        response = api(request, 'billing/purchase-inquiries')
        self.assertEqual(response.status_code, 401)
        self.assertFalse(PurchaseInquiry.objects.exists())

    def test_revoke_commits_while_billing_write_waits_for_lock(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Actual row-lock waiting requires PostgreSQL.')
        waiting = Event()
        request = self.request('billing/purchase-inquiries', {'company': 'Fixture', 'seats': 2,
            'requirements': 'Fixture requirements', 'contact_email': 'fixture@example.invalid'})
        def write():
            close_old_connections()
            def signal(execute, sql, params, many, context):
                if 'FOR UPDATE' in sql and 'backend_accountsession' in sql:
                    waiting.set()
                return execute(sql, params, many, context)
            try:
                with connection.execute_wrapper(signal):
                    return api(request, 'billing/purchase-inquiries').status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                AccountSession.objects.select_for_update().get(pk=self.metadata.pk)
                future = pool.submit(write)
                self.assertTrue(waiting.wait(timeout=5), 'Billing write must attempt session row lock.')
                AccountSession.objects.filter(pk=self.metadata.pk).update(revoked_at=timezone.now())
                Session.objects.filter(pk=self.session_key).delete()
            self.assertEqual(future.result(timeout=10), 401)
        self.assertFalse(PurchaseInquiry.objects.exists())
