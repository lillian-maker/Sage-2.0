"""BR-20 tests: personal commerce DTOs stay inside the authenticated account."""
import os
from datetime import timedelta
from unittest import mock

from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from . import desktop_auth
from .commerce.models import CreditBatch, CreditEntry, Order, ProductVersion, Quote, Subscription
from .desktop_auth_tests import AUDIENCE, CLIENT_ID, ISSUER, JWKS_URI, SCOPE, public_jwk, sign
from .models import BillingAccount, ExternalIdentity, InvoiceRequest


class DesktopBillingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        User = get_user_model()
        cls.user = User.objects.create_user(username='br20-a', first_name='A', email='a@example.test')
        cls.other = User.objects.create_user(username='br20-b', first_name='B', email='b@example.test')
        cls.unprovisioned = User.objects.create_user(username='br20-c', first_name='C', email='c@example.test')
        ExternalIdentity.objects.create(issuer=ISSUER, subject='subject-a', user=cls.user)
        ExternalIdentity.objects.create(issuer=ISSUER, subject='subject-c', user=cls.unprovisioned)
        cls.account = BillingAccount.objects.create(kind='personal', personal_owner=cls.user, name='A')
        cls.other_account = BillingAccount.objects.create(kind='personal', personal_owner=cls.other, name='B')
        cls.product = ProductVersion.objects.create(
            code='personal-monthly', version=1, kind='subscription', audience='personal',
            billing_cycle='monthly', currency='CNY', unit_amount_minor=9900,
            credits_quantity='100', entitlements={}, state='draft')
        now = timezone.now()
        cls.orders = [cls.make_order(cls.account, cls.user, index) for index in range(55)]
        cls.other_order = cls.make_order(cls.other_account, cls.other, 1)
        cls.subscription = Subscription.objects.create(
            account=cls.account, initial_order=cls.orders[0], original_anchor=now,
            billing_timezone='Asia/Shanghai', calendar_rule_version='v1',
            billing_cycle='monthly', mandate={})
        cls.batch = CreditBatch.objects.create(
            account=cls.account, source='subscription', source_key='br20-month-1',
            initial_quantity='100', expires_at=now + timedelta(days=30))
        CreditEntry.objects.create(batch=cls.batch, kind='grant', quantity='100', event_key='grant:1')
        InvoiceRequest.objects.create(
            account=cls.account, order=cls.orders[0], created_by=cls.user,
            recipient_email='a@example.test', title='BR20', status='issued',
            request_key='br20-invoice', request_hash='a' * 64)

    @classmethod
    def make_order(cls, account, user, index):
        quote = Quote.objects.create(
            account=account, product=cls.product, snapshot={}, amount_minor=9900,
            currency='CNY', expires_at=timezone.now() + timedelta(days=1))
        return Order.objects.create(
            account=account, created_by=user, quote=quote, idempotency_key=f'br20-{account.pk}-{index}',
            request_hash='b' * 64, snapshot={'code': 'personal-monthly', 'billing_cycle': 'monthly', 'seat_quantity': 1},
            amount_minor=9900, currency='CNY', status='fulfilled')

    def setUp(self):
        desktop_auth.reset_jwks_cache()
        self.keys = [public_jwk(self.rsa_key, 'rsa-1', 'RS256')]
        self.fetch = mock.patch.object(desktop_auth, '_fetch_jwks', side_effect=lambda uri: self.keys)
        self.fetch_mock = self.fetch.start()
        self.addCleanup(self.fetch.stop)
        self.env = mock.patch.dict(os.environ, {
            'SAGE_DESKTOP_OIDC_ISSUER': ISSUER, 'SAGE_DESKTOP_OIDC_JWKS_URI': JWKS_URI,
            'SAGE_DESKTOP_OIDC_AUDIENCE': AUDIENCE, 'SAGE_DESKTOP_OIDC_CLIENT_ID': CLIENT_ID,
            'SAGE_DESKTOP_OIDC_SCOPE': SCOPE,
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def token(self, subject='subject-a'):
        now = int(timezone.now().timestamp())
        return sign({'iss': ISSUER, 'aud': [AUDIENCE], 'sub': subject, 'client_id': CLIENT_ID,
                     'scope': SCOPE, 'exp': now + 300, 'nbf': now - 10}, self.rsa_key, 'rsa-1')

    def get(self, path, subject='subject-a'):
        return self.client.get(path, HTTP_AUTHORIZATION=f'Bearer {self.token(subject)}')

    def test_billing_summary_is_personal_credits_and_subscriptions(self):
        response = self.get('/api/desktop/billing')
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body['state'], 'read')
        self.assertEqual(body['source'], 'django-commerce')
        self.assertEqual(body['account']['id'], str(self.account.pk))
        self.assertEqual(body['credits']['available'], '100')
        self.assertEqual([item['id'] for item in body['subscriptions']], [str(self.subscription.pk)])
        self.assertTrue(body['capabilities']['subscription'])

    def test_orders_paginate_and_never_cross_accounts(self):
        first = self.get('/api/desktop/orders')
        self.assertEqual(first.status_code, 200, first.content)
        page = first.json()
        self.assertEqual(len(page['items']), 50)
        self.assertEqual(page['next_offset'], 50)
        self.assertTrue(all(item['account_id'] == str(self.account.pk) for item in page['items']))
        second = self.get('/api/desktop/orders?offset=50')
        self.assertEqual(len(second.json()['items']), 5)
        self.assertIsNone(second.json()['next_offset'])
        self.assertNotIn(str(self.other_order.pk), [item['id'] for item in page['items']])

    def test_order_detail_carries_invoice_status_and_hides_foreign_orders(self):
        detail = self.get(f'/api/desktop/orders/{self.orders[0].pk}')
        self.assertEqual(detail.status_code, 200, detail.content)
        body = detail.json()
        self.assertEqual(body['invoice'], {'status': 'issued', 'updatedAt': body['invoice']['updatedAt']})
        self.assertNotIn('document', body['invoice'])
        foreign = self.get(f'/api/desktop/orders/{self.other_order.pk}')
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(foreign.json()['code'], 'desktop-order-not-found')

    def test_usage_is_paginated_and_scoped(self):
        body = self.get('/api/desktop/usage').json()
        self.assertEqual(body['total'], 1)
        self.assertEqual(body['items'][0]['kind'], 'grant')
        self.assertEqual(body['items'][0]['quantity'], '100')
        self.assertIsNone(body['next_offset'])

    def test_subscriptions_are_scoped(self):
        body = self.get('/api/desktop/subscriptions').json()
        self.assertEqual([item['id'] for item in body['items']], [str(self.subscription.pk)])

    def test_account_parameter_cannot_select_another_ledger(self):
        response = self.get(f'/api/desktop/orders?account_id={self.other_account.pk}')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'desktop-request-invalid')

    def test_unprovisioned_identity_is_named(self):
        response = self.get('/api/desktop/billing', subject='subject-c')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'desktop-account-not-provisioned')

    def test_bearer_is_required_and_method_is_get_only(self):
        self.assertEqual(self.client.get('/api/desktop/billing').status_code, 401)
        response = self.client.post('/api/desktop/billing', HTTP_AUTHORIZATION=f'Bearer {self.token()}')
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()['code'], 'desktop-method-not-allowed')
