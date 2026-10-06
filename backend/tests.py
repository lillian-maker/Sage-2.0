"""Authentication/ownership integration tests: isolated DB and in-memory email only."""
import json
import uuid
import re
from unittest.mock import patch
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from backend.models import BillingAccount, LoginChallenge, LoginIdentity, Membership, PurchaseInquiry
from backend.apps import deployment_configuration


class BillingLookupTests(TestCase):
    """Internal CLI fixtures only; no channel, customer data or real funds."""

    def setUp(self):
        from backend.commerce.models import (BillingIssue, CreditBatch, CreditFreeze,
            CreditRefund, Order, PaymentAttempt, PaymentFact, ProductVersion, Quote)
        self.user = get_user_model().objects.create_user(username='lookup-fixture')
        self.account = BillingAccount.objects.create(kind='personal', name='Lookup fixture',
                                                     personal_owner=self.user)
        other_user = get_user_model().objects.create_user(username='lookup-other-fixture')
        self.other = BillingAccount.objects.create(kind='personal', name='Other fixture',
                                                   personal_owner=other_user)
        self.now = timezone.now()
        self.amount = 9007199254740993
        product = ProductVersion.objects.create(code='lookup-fixture', version=1,
            kind='credits', audience='personal', unit_amount_minor=self.amount)
        quote = Quote.objects.create(account=self.account, product=product,
            snapshot={'private_contact': 'private-fixture@example.invalid'},
            amount_minor=self.amount, currency='CNY', expires_at=self.now+timedelta(hours=1))
        self.order = Order.objects.create(account=self.account, created_by=self.user,
            quote=quote, idempotency_key='private-request-key', request_hash='a'*64,
            snapshot={'private_contact': 'private-fixture@example.invalid'},
            amount_minor=self.amount, currency='CNY', status='fulfilled', paid_at=self.now)
        self.payment = PaymentFact.objects.create(order=self.order, channel='fixture',
            merchant_reference='private-merchant-reference', transaction_reference='lookup-receipt',
            amount_minor=self.amount, currency='CNY', evidence_hash='b'*64,
            disposition='accepted', paid_at=self.now)
        self.attempt = PaymentAttempt.objects.create(account=self.account, order=self.order,
            intent_key='private-intent-key', expires_at=self.now+timedelta(hours=1),
            state='succeeded', dispatch_authorization={'private': 'private-authorization'})
        batch = CreditBatch.objects.create(account=self.account, order=self.order,
            source='purchased', source_key='lookup-batch', initial_quantity='10',
            expires_at=self.now+timedelta(days=30))
        hold = CreditFreeze.objects.create(batch=batch, request_key='private-freeze-key', quantity='1')
        self.refund = CreditRefund.objects.create(payment=self.payment, freeze=hold,
            request_key='private-refund-key', amount_minor=731, review_evidence_hash='c'*64)
        self.issue = BillingIssue.objects.create(account=self.account,
            code='PAYMENT_REVIEW_REQUIRED', object_type='payment', object_id=self.payment.pk,
            last_seen_at=self.now)
        BillingIssue.objects.create(account=self.other, code='OTHER_ACCOUNT_ISSUE',
            object_type='payment', object_id=self.payment.pk, last_seen_at=self.now)

    def lookup(self, **options):
        from io import StringIO
        from django.core.management import call_command
        output = StringIO()
        call_command('report_billing_facts', account=str(self.account.pk), stdout=output, **options)
        return json.loads(output.getvalue())

    def test_three_references_recover_the_same_account_scoped_facts(self):
        for options in ({'order': str(self.order.pk)},
                        {'transaction_reference': 'lookup-receipt'}, {'refund': str(self.refund.pk)}):
            with self.subTest(options=options):
                result = self.lookup(**options)
                self.assertEqual(result['scope'], 'internal_billing_lookup_not_channel_verification')
                self.assertEqual(result['account_id'], str(self.account.pk))
                self.assertEqual(result['order']['id'], str(self.order.pk))
                self.assertEqual(result['order']['amount_minor'], str(self.amount))
                self.assertEqual(result['payments'][0]['amount_minor'], str(self.amount))
                self.assertEqual(result['refunds'][0]['amount_minor'], '731')
                self.assertEqual(result['refunds'][0]['state'], 'reserved')
                self.assertEqual(result['refunds'][0]['id'], str(self.refund.pk))
                self.assertEqual(result['payment_attempt']['state'], 'succeeded')
                self.assertEqual(result['delivery']['status'], 'completed')
                self.assertEqual([item['id'] for item in result['issues']], [str(self.issue.pk)])

    def test_missing_and_other_account_references_are_indistinguishable(self):
        from django.core.management import call_command, CommandError
        from io import StringIO
        for selector, value in (('order', str(self.order.pk)),
                                ('refund', str(self.refund.pk)),
                                ('transaction_reference', 'lookup-receipt')):
            with self.subTest(selector=selector):
                for account, reference in ((self.other.pk,value),
                    (self.account.pk,str(uuid.uuid4()) if selector != 'transaction_reference' else 'missing')):
                    output = StringIO()
                    with self.assertRaisesMessage(CommandError, 'LOOKUP_NOT_FOUND'):
                        call_command('report_billing_facts', account=str(account),
                                     stdout=output, **{selector:reference})
                    self.assertEqual(output.getvalue(), '')

    def test_transaction_ambiguity_is_rejected_even_when_order_is_the_same(self):
        from django.core.management import CommandError
        from backend.commerce.models import PaymentFact
        PaymentFact.objects.create(order=self.order, channel='other-fixture',
            merchant_reference='private-other-merchant', transaction_reference='lookup-receipt',
            amount_minor=731, currency='CNY', evidence_hash='d'*64,
            disposition='duplicate_payment_review', paid_at=self.now)
        with self.assertRaisesMessage(CommandError, 'AMBIGUOUS_TRANSACTION_REFERENCE'):
            self.lookup(transaction_reference='lookup-receipt')
        self.assertEqual(len(self.lookup(order=str(self.order.pk))['payments']), 2)

    def test_output_is_allowlisted_and_never_leaks_evidence_or_account_identity(self):
        result = self.lookup(order=str(self.order.pk))
        self.assertEqual(set(result['order']), {'id','status','amount_minor','currency','paid_at','created_at'})
        self.assertEqual(set(result['payments'][0]),
            {'id','channel','transaction_reference','amount_minor','currency','disposition','paid_at'})
        self.assertEqual(set(result['refunds'][0]),
            {'id','payment_id','state','amount_minor','created_at','resolved_at'})
        self.assertEqual(set(result['payment_attempt']), {'id','state','needs_reconciliation','expires_at'})
        rendered = json.dumps(result)
        for excluded in ('private-', 'evidence_hash', 'request_hash', 'request_key',
                         'dispatch_authorization', 'merchant_reference', 'created_by', 'personal_owner'):
            self.assertNotIn(excluded, rendered)

    def test_lookup_modes_reject_invalid_or_mixed_arguments(self):
        from django.core.management import CommandError
        for options in ({'order':'not-uuid'}, {'refund':'not-uuid'},
            {'transaction_reference':''}, {'transaction_reference':'x'*129},
            {'transaction_reference':'bad\nreference'},
            {'order':str(self.order.pk),'refund':str(self.refund.pk)},
            {'order':str(self.order.pk),'include_age':True},
            {'order':str(self.order.pk),'start':self.now.isoformat(),'end':self.now.isoformat()},
            {'order':str(self.order.pk),'start':self.now.isoformat()}):
            with self.subTest(options=options), self.assertRaises(CommandError):
                self.lookup(**options)

    def test_lookup_repetition_is_readonly_and_preserves_pending_delivery(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from backend.commerce.models import Order
        Order.objects.filter(pk=self.order.pk).update(status='paid_pending_entitlement')
        with CaptureQueriesContext(connection) as queries:
            first = self.lookup(order=str(self.order.pk))
            second = self.lookup(refund=str(self.refund.pk))
        self.assertEqual(first, second)
        self.assertEqual(first['delivery']['status'], 'pending')
        self.assertTrue(queries.captured_queries)
        for query in queries.captured_queries:
            self.assertRegex(query['sql'], r'^SELECT\b')
            self.assertNotRegex(query['sql'], r'(?i);|\b(?:INSERT|UPDATE|DELETE|INTO|LOCK)\b')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'paid_pending_entitlement')


class BillingAgeReportTests(TestCase):
    """Current backlog observations, not historical as-of or threshold alerts."""

    def setUp(self):
        from backend.commerce.models import Order, PaymentAttempt, ProductVersion, Quote
        self.user = get_user_model().objects.create_user(username='age-fixture')
        self.account = BillingAccount.objects.create(kind='personal', name='Age fixture', personal_owner=self.user)
        self.now = timezone.now()
        product = ProductVersion.objects.create(code='age-fixture', version=1, kind='credits', audience='personal')
        quote = Quote.objects.create(account=self.account, product=product, snapshot={},
            amount_minor=731, currency='CNY', expires_at=self.now+timedelta(days=1))
        self.order = Order.objects.create(account=self.account, created_by=self.user, quote=quote,
            idempotency_key='age-order', request_hash='a'*64, snapshot={}, amount_minor=731,
            currency='CNY', status='pending_payment')
        self.attempt = PaymentAttempt.objects.create(account=self.account, order=self.order,
            intent_key='age-attempt', expires_at=self.now+timedelta(days=1), state='unknown')

    def report(self, **options):
        from io import StringIO
        from django.core.management import call_command
        output = StringIO()
        with patch('backend.commerce.management.commands.report_billing_facts.timezone.now', return_value=self.now):
            call_command('report_billing_facts', account=str(self.account.pk),
                start=(self.now-timedelta(seconds=1)).isoformat(),
                end=(self.now+timedelta(seconds=1)).isoformat(), include_age=True,
                stdout=output, **options)
        return json.loads(output.getvalue())

    def event(self, outcome, at, applied=True):
        from backend.commerce.models import PaymentAttemptEvent
        item = PaymentAttemptEvent.objects.create(attempt=self.attempt,
            event_key=str(uuid.uuid4()), source='fixture', outcome=outcome,
            request_hash='a'*64, evidence_hash='b'*64, applied=applied)
        PaymentAttemptEvent.objects.filter(pk=item.pk).update(created_at=at)

    def test_unknown_age_uses_first_unknown_after_last_applied_nonunknown(self):
        self.event('unknown', self.now-timedelta(days=5))
        self.event('pending', self.now-timedelta(days=4))
        self.event('unknown', self.now-timedelta(seconds=2, microseconds=1))
        self.event('unknown', self.now-timedelta(seconds=1))
        self.event('succeeded', self.now, applied=False)
        result = self.report()['current_backlog_age']
        self.assertEqual(result['scope'], 'current_account_state_not_windowed_or_historical')
        self.assertEqual(result['observed_at'], self.now.isoformat())
        group = result['unknown_payment_attempts']
        self.assertEqual(group['count'], 1)
        self.assertEqual(group['known_start_count'], 1)
        self.assertEqual(group['oldest_start_at'], (self.now-timedelta(seconds=2, microseconds=1)).isoformat())
        self.assertEqual(group['max_age_microseconds'], '2000001')

    def test_missing_unknown_transition_never_substitutes_attempt_created_at(self):
        group = self.report()['current_backlog_age']['unknown_payment_attempts']
        self.assertEqual(group['unknown_start_count'], 1)
        self.assertIsNone(group['oldest_start_at'])
        self.assertIsNone(group['max_age_microseconds'])
        self.event('unknown', self.now-timedelta(days=2))
        self.event('pending', self.now-timedelta(days=1))
        self.assertEqual(self.report()['current_backlog_age']['unknown_payment_attempts'], group)

    def test_delivery_freeze_issue_ages_are_current_backlog_not_window_cohort(self):
        from backend.commerce.models import BillingIssue, CreditBatch, CreditFreeze, Order
        start = self.now-timedelta(days=2, seconds=3, microseconds=4)
        Order.objects.filter(pk=self.order.pk).update(status='paid_pending_entitlement', paid_at=start)
        batch = CreditBatch.objects.create(account=self.account, order=self.order, source='purchased',
            source_key='age-batch', initial_quantity='10', expires_at=self.now+timedelta(days=1))
        hold = CreditFreeze.objects.create(batch=batch, request_key='age-freeze', quantity='1')
        CreditFreeze.objects.filter(pk=hold.pk).update(created_at=start)
        issue = BillingIssue.objects.create(account=self.account, code='PAID_DELIVERY_PENDING',
            object_type='order', object_id=self.order.pk, last_seen_at=self.now)
        BillingIssue.objects.filter(pk=issue.pk).update(first_seen_at=start)
        result = self.report()['current_backlog_age']
        for key in ('paid_pending_delivery_orders', 'frozen_credit_allocations', 'unresolved_issues'):
            self.assertEqual(result[key]['count'], 1)
            self.assertEqual(result[key]['max_age_microseconds'], '172803000004')
            self.assertEqual(result[key]['oldest_start_at'], start.isoformat())
        Order.objects.filter(pk=self.order.pk).update(status='fulfilled')
        CreditFreeze.objects.filter(pk=hold.pk).update(state='released')
        BillingIssue.objects.filter(pk=issue.pk).update(state='resolved')
        result = self.report()['current_backlog_age']
        for key in ('paid_pending_delivery_orders', 'frozen_credit_allocations', 'unresolved_issues'):
            self.assertEqual(result[key]['count'], 0)
            self.assertIsNone(result[key]['max_age_microseconds'])

    def test_future_start_is_anomaly_never_negative_age(self):
        future = self.now+timedelta(microseconds=1)
        self.event('unknown', future)
        group = self.report()['current_backlog_age']['unknown_payment_attempts']
        self.assertEqual(group['future_start_count'], 1)
        self.assertEqual(group['earliest_future_start_at'], future.isoformat())
        self.assertEqual(group['known_start_count'], 0)
        self.assertIsNone(group['max_age_microseconds'])

    def test_applied_event_sequence_not_clock_sort_determines_current_unknown_episode(self):
        self.event('unknown', self.now-timedelta(days=2))
        self.event('pending', self.now+timedelta(days=1))
        self.event('unknown', self.now-timedelta(microseconds=1))
        group = self.report()['current_backlog_age']['unknown_payment_attempts']
        self.assertEqual(group['future_start_count'], 0)
        self.assertEqual(group['max_age_microseconds'], '1')

    def test_zero_age_is_zero_and_missing_paid_timestamp_is_unknown(self):
        from backend.commerce.models import Order
        self.event('unknown', self.now)
        Order.objects.filter(pk=self.order.pk).update(status='paid_pending_entitlement')
        result = self.report()['current_backlog_age']
        self.assertEqual(result['unknown_payment_attempts']['max_age_microseconds'], '0')
        self.assertEqual(result['paid_pending_delivery_orders']['unknown_start_count'], 1)
        self.assertIsNone(result['paid_pending_delivery_orders']['max_age_microseconds'])

    def test_age_report_is_repeatable_readonly_and_empty_for_other_account(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from backend.commerce.tests import BillingFactsTests
        self.event('unknown', self.now-timedelta(seconds=1))
        with CaptureQueriesContext(connection) as queries:
            first, second = self.report(), self.report()
        self.assertEqual(first, second)
        self.assertTrue(queries.captured_queries)
        BillingFactsTests.assert_readonly_queries(self, [query['sql'] for query in queries.captured_queries])
        other_user = get_user_model().objects.create_user(username='age-other-fixture')
        self.account = BillingAccount.objects.create(kind='personal', name='Empty fixture', personal_owner=other_user)
        result = self.report()['current_backlog_age']
        for key in ('unknown_payment_attempts','paid_pending_delivery_orders','frozen_credit_allocations','unresolved_issues'):
            self.assertEqual(result[key]['count'], 0)
            self.assertIsNone(result[key]['oldest_start_at'])


class HealthTests(TestCase):
    def test_liveness_never_accesses_database(self):
        with patch('django.db.backends.utils.CursorWrapper.execute', side_effect=AssertionError('DB accessed')):
            response = self.client.get('/health/live')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'alive'})
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_readiness_checks_database_and_schema_without_business_writes(self):
        response = self.client.get('/health/ready')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ready'})
        self.assertFalse(BillingAccount.objects.exists())
        self.assertEqual(self.client.head('/health/ready').content, b'')
        self.assertEqual(self.client.post('/health/ready').status_code, 405)

    def test_unavailable_database_returns_generic_503(self):
        from django.db import OperationalError
        with patch('backend.views.connection.cursor', side_effect=OperationalError('sensitive connection details')):
            response = self.client.get('/health/ready')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'not_ready'})
        self.assertNotIn(b'sensitive', response.content)

    def test_pending_migrations_are_not_ready(self):
        with patch('backend.views.MigrationExecutor') as executor:
            executor.return_value.migration_plan.return_value = [object()]
            response = self.client.get('/health/ready')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'not_ready'})


class DeploymentConfigurationTests(TestCase):
    @override_settings(LOCAL=False,
                       ALLOWED_HOSTS=['sage.example.invalid'], AUTH_EMAIL_ENABLED=False,
                       BILLING_SALES_ENABLED=False)
    def test_disabled_capabilities_are_warnings_not_silently_ready(self):
        with patch('backend.apps.settings.DATABASES', {'default': {'ENGINE': 'django.db.backends.postgresql'}}):
            self.assertEqual({item.id for item in deployment_configuration(None)}, {'sage.W001', 'sage.W002'})

    def test_local_configuration_is_rejected_for_deployment(self):
        self.assertIn('sage.E001', {item.id for item in deployment_configuration(None)})

    @override_settings(ALLOWED_HOSTS=['https://sage.example.invalid', '.example.invalid'])
    def test_broad_or_url_hosts_are_rejected(self):
        self.assertIn('sage.E003', {item.id for item in deployment_configuration(None)})

    @override_settings(AUTH_EMAIL_ENABLED=True, EMAIL_HOST='', DEFAULT_FROM_EMAIL='invalid')
    def test_incomplete_enabled_email_is_rejected(self):
        self.assertIn('sage.E004', {item.id for item in deployment_configuration(None)})

    @override_settings(AUTH_EMAIL_ENABLED=True, EMAIL_HOST='smtp.example.invalid',
                       DEFAULT_FROM_EMAIL='sender@example.invalid', EMAIL_USE_TLS=True, EMAIL_PORT=587,
                       EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend')
    def test_configured_smtp_does_not_claim_actual_delivery(self):
        self.assertNotIn('sage.E004', {item.id for item in deployment_configuration(None)})


@override_settings(AUTH_EMAIL_ENABLED=True, EMAIL_HOST='fixture.invalid',
                   DEFAULT_FROM_EMAIL='no-reply@example.invalid',
                   BILLING_CREDITS_PURCHASE_ENABLED=True,
                   BILLING_TERMS_DOCUMENTS={'fixture-1': {'approved': True,
                       'zh': {'title': '隔离测试条款', 'paragraphs': ['仅用于测试，不是实际合同。']},
                       'en': {'title': 'Isolated test terms', 'paragraphs': ['Fixture only, not a real contract.']}}},
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AccountTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.session()

    def session(self, client=None):
        client = client or self.client
        response = client.get('/api/auth/session')
        self.assertEqual(response.status_code, 200)
        result = response.json()
        client.csrf_token = result['csrf_token']
        return result

    def post(self, path, data, client=None):
        client = client or self.client
        return client.post(path, json.dumps(data), content_type='application/json',
                           HTTP_X_CSRFTOKEN=client.csrf_token,
                           HTTP_X_BILLING_ACCOUNT=client.session.get('active_account_id', '') or '')

    def challenge(self, identifier='owner@example.invalid', client=None):
        response = self.post('/api/auth/challenges', {'method': 'email', 'identifier': identifier}, client)
        self.assertEqual(response.status_code, 201, response.content)
        code = re.search(r'\b\d{6}\b', mail.outbox[-1].body).group()
        self.assertNotIn(code, response.content.decode())
        return response.json()['challenge_id'], code

    def sign_in(self, identifier='owner@example.invalid', client=None):
        client = client or self.client
        challenge, code = self.challenge(identifier, client)
        response = self.post('/api/auth/verify', {'challenge_id': challenge, 'code': code}, client)
        self.assertEqual(response.status_code, 200, response.content)
        client.csrf_token = response.json()['csrf_token']
        return response.json()

    def purchase_product(self):
        from backend.commerce.models import ProductVersion
        return ProductVersion.objects.create(
            code='fixture_subscription', version=1, kind='subscription', audience='personal',
            billing_cycle='monthly', unit_amount_minor=731, credits_quantity='10',
            entitlements={'fixture': True, 'benefits': {'zh': ['隔离测试权益，不对外销售。'],
                          'en': ['Isolated test benefits, not for sale.']}},
            entitlement_version='fixture-1', credit_rule_version='fixture-1',
            terms_version='fixture-1', state='published',
            effective_at=timezone.now() - timedelta(days=1))

    def test_verified_identity_creates_one_private_account_and_logout_revokes(self):
        data = self.sign_in()
        self.assertTrue(data['authenticated'])
        self.assertEqual(len(data['accounts']), 1)
        self.assertEqual(BillingAccount.objects.count(), 1)
        summary = self.client.get('/api/billing/account')
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()['credits']['available'], '0')
        self.assertEqual(summary.json()['orders'], [])
        self.assertEqual(self.post('/api/auth/logout', {}).status_code, 200)
        self.assertEqual(self.client.get('/api/billing/account').status_code, 401)

    def test_writes_require_csrf(self):
        response = self.client.post('/api/auth/challenges', json.dumps({
            'method': 'email', 'identifier': 'csrf@example.invalid'}), content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'CSRF_FAILED')
        self.assertFalse(LoginChallenge.objects.exists())

    def test_purchase_quote_order_lifecycle_is_account_scoped_and_idempotent(self):
        from backend.commerce.tests import FIXTURE_CHANNELS
        from backend.commerce.models import Order, Quote
        self.sign_in()
        product = self.purchase_product()
        with override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS,
                               BILLING_QUOTE_TTL_SECONDS=300):
            response = self.post('/api/billing/purchase-quotes', {
                'product_id': str(product.pk), 'seat_quantity': 1, 'channel': 'fixture', 'buyer_region': 'CN'})
            self.assertEqual(response.status_code, 201, response.content)
            data = response.json()
            self.assertEqual(data['amount_minor'], 731)
            self.assertEqual(data['snapshot']['terms_version'], 'fixture-1')
            self.assertNotIn('merchant_entity', data['snapshot'])
            self.assertTrue(data['can_order'])
            self.assertEqual(self.client.get('/api/billing/purchase-quotes/' + data['id']).json(), data)
            body = {'quote_id': data['id'], 'agreement_acceptance': 'fixture-1'}
            wrong = self.post('/api/billing/orders', {**body, 'agreement_acceptance': 'wrong'})
            self.assertEqual(wrong.status_code, 422)
            kwargs = {'content_type': 'application/json', 'HTTP_X_CSRFTOKEN': self.client.csrf_token,
                      'HTTP_X_BILLING_ACCOUNT': self.client.session['active_account_id'],
                      'HTTP_IDEMPOTENCY_KEY': 'customer-order-1'}
            first = self.client.post('/api/billing/orders', json.dumps(body), **kwargs)
            second = self.client.post('/api/billing/orders', json.dumps(body), **kwargs)
            self.assertEqual(first.status_code, 201, first.content)
            self.assertEqual(second.json()['id'], first.json()['id'])
            self.assertEqual(Order.objects.count(), 1)
            conflict = self.client.post('/api/billing/orders', json.dumps({**body, 'agreement_acceptance': 'changed'}), **kwargs)
            self.assertEqual(conflict.status_code, 409)
            result = self.client.get('/api/billing/purchase-quotes/' + data['id']).json()
            self.assertFalse(result['can_order'])
            self.assertEqual(result['order_id'], first.json()['id'])
            self.assertEqual(Quote.objects.count(), 1)
        other = Client(enforce_csrf_checks=True)
        self.session(other)
        self.sign_in('other@example.invalid', other)
        self.assertEqual(other.get('/api/billing/purchase-quotes/' + data['id']).status_code, 404)

    def test_purchase_quote_requires_configuration_and_rejects_browser_prices(self):
        from backend.commerce.tests import FIXTURE_CHANNELS
        from backend.commerce.models import Quote
        self.sign_in()
        body = {'product_id': str(self.purchase_product().pk), 'seat_quantity': 1,
                'channel': 'fixture', 'buyer_region': 'CN'}
        self.assertEqual(self.post('/api/billing/purchase-quotes', body).status_code, 503)
        with override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS):
            self.assertEqual(self.post('/api/billing/purchase-quotes', body).status_code, 503)
        with override_settings(BILLING_QUOTE_TTL_SECONDS=300):
            self.assertEqual(self.post('/api/billing/purchase-quotes', {**body, 'amount_minor': 1}).status_code, 422)
        self.assertFalse(Quote.objects.exists())

    def test_same_quote_different_key_recovers_conflict_before_new_sale_gates(self):
        from backend.commerce.tests import FIXTURE_CHANNELS
        from backend.commerce.models import Order, Quote
        self.sign_in()
        with override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS,
                               BILLING_QUOTE_TTL_SECONDS=300):
            product = self.purchase_product()
            quote = self.post('/api/billing/purchase-quotes', {'product_id':str(product.pk),
                'seat_quantity':1, 'channel':'fixture', 'buyer_region':'CN'}).json()
            body = {'quote_id':quote['id'], 'agreement_acceptance':'fixture-1'}
            headers = {'content_type':'application/json', 'HTTP_X_CSRFTOKEN':self.client.csrf_token,
                       'HTTP_X_BILLING_ACCOUNT':self.client.session['active_account_id'],
                       'HTTP_IDEMPOTENCY_KEY':'first-tab'}
            first = self.client.post('/api/billing/orders', json.dumps(body), **headers)
            self.assertEqual(first.status_code,201)
            second = self.client.post('/api/billing/orders', json.dumps(body),
                **{**headers,'HTTP_IDEMPOTENCY_KEY':'second-tab'})
            self.assertEqual(second.status_code,409)
            self.assertEqual(second.json()['error']['code'],'QUOTE_ALREADY_ORDERED')
            Quote.objects.filter(pk=quote['id']).update(expires_at=timezone.now()-timedelta(seconds=1))
            with override_settings(BILLING_SALES_ENABLED=False, BILLING_TERMS_DOCUMENTS={}):
                replay = self.client.post('/api/billing/orders', json.dumps(body), **headers)
                self.assertEqual(replay.status_code,201)
                self.assertEqual(replay.json()['id'],first.json()['id'])
                conflict = self.client.post('/api/billing/orders', json.dumps(body),
                    **{**headers,'HTTP_IDEMPOTENCY_KEY':'third-tab'})
                self.assertEqual(conflict.status_code,409)
                self.assertEqual(conflict.json()['error']['code'],'QUOTE_ALREADY_ORDERED')
                altered = self.client.post('/api/billing/orders',
                    json.dumps({**body,'agreement_acceptance':'changed'}), **headers)
                self.assertEqual(altered.status_code,409)
                self.assertEqual(altered.json()['error']['code'],'IDEMPOTENCY_CONFLICT')
                detail = self.client.get('/api/billing/purchase-quotes/'+quote['id']).json()
                self.assertEqual(detail['order_id'],first.json()['id'])
            self.assertEqual(Order.objects.count(),1)
        other = Client(enforce_csrf_checks=True)
        self.session(other)
        self.sign_in('conflict-other@example.invalid',other)
        hidden = other.post('/api/billing/orders',json.dumps(body),content_type='application/json',
            HTTP_X_CSRFTOKEN=other.csrf_token, HTTP_X_BILLING_ACCOUNT=other.session['active_account_id'],
            HTTP_IDEMPOTENCY_KEY='second-tab')
        self.assertEqual(hidden.status_code,404)
        self.assertNotIn(first.json()['id'],hidden.content.decode())

    def test_order_pages_are_bounded_stable_and_account_scoped(self):
        from backend.commerce.models import Order, Quote
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.sign_in()
        account = BillingAccount.objects.get(pk=self.client.session['active_account_id'])
        other = BillingAccount.objects.create(kind='business',name='Isolated other account')
        product = self.purchase_product()
        owned, foreign = [], None
        for index in range(52):
            target = account if index < 51 else other
            quote = Quote.objects.create(account=target,product=product,snapshot={},
                amount_minor=731,currency='CNY',expires_at=timezone.now()+timedelta(minutes=5))
            order = Order.objects.create(account=target,quote=quote,created_by_id=account.personal_owner_id,
                idempotency_key=f'page-{index}',request_hash='a'*64,snapshot={},amount_minor=731,currency='CNY')
            if target == account:
                owned.append(str(order.pk))
            else:
                foreign = str(order.pk)
        Order.objects.update(created_at=timezone.now())
        expected = sorted(owned,reverse=True)
        first = self.client.get('/api/billing/orders').json()
        self.assertEqual(set(first),{'account_id','items','next_offset'})
        self.assertEqual(first['account_id'],str(account.pk))
        self.assertEqual([item['id'] for item in first['items']],expected[:50])
        self.assertEqual(first['next_offset'],50)
        self.assertEqual(self.client.get('/api/billing/orders?offset=0').json(),first)
        second = self.client.get('/api/billing/orders?offset=50').json()
        self.assertEqual([item['id'] for item in second['items']],expected[50:])
        self.assertIsNone(second['next_offset'])
        empty = self.client.get('/api/billing/orders?offset=51').json()
        self.assertEqual(empty['items'],[])
        self.assertIsNone(empty['next_offset'])
        self.assertEqual(self.client.get('/api/billing/orders/'+foreign).status_code,404)
        with CaptureQueriesContext(connection) as queries:
            summary = self.client.get('/api/billing/account').json()
        self.assertEqual([item['id'] for item in summary['orders']],expected[:50])
        order_reads = [item['sql'] for item in queries.captured_queries if 'FROM "commerce_order"' in item['sql']]
        self.assertTrue(order_reads)
        self.assertTrue(all('LIMIT 51' in sql for sql in order_reads))

    def test_existing_credit_order_recovery_survives_purchase_switch_off(self):
        import hashlib
        from backend.commerce.models import Order, Quote
        from backend.commerce.tests import fixture_product
        self.sign_in()
        account = BillingAccount.objects.get(pk=self.client.session['active_account_id'])
        product = fixture_product()
        # Stored historical purchase fixtures, not a bypass for new purchases.
        snapshot = {'kind':'credits','terms_document_digest':'a'*64,'terms_version':'fixture-1',
                    'amount_minor':731,'currency':'CNY'}
        quote = Quote.objects.create(account=account,product=product,snapshot=snapshot,
            amount_minor=731,currency='CNY',expires_at=timezone.now()-timedelta(days=1))
        body = {'quote_id':str(quote.pk),'agreement_acceptance':'fixture-1'}
        order = Order.objects.create(account=account,quote=quote,created_by_id=account.personal_owner_id,
            idempotency_key='stored-credit-order',snapshot=snapshot,amount_minor=731,currency='CNY',
            request_hash=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest())
        headers = {'content_type':'application/json','HTTP_X_CSRFTOKEN':self.client.csrf_token,
                   'HTTP_X_BILLING_ACCOUNT':str(account.pk)}
        with override_settings(BILLING_CREDITS_PURCHASE_ENABLED=False, BILLING_SALES_ENABLED=False):
            replay = self.client.post('/api/billing/orders',json.dumps(body),
                HTTP_IDEMPOTENCY_KEY='stored-credit-order',**headers)
            self.assertEqual(replay.status_code,201)
            self.assertEqual(replay.json()['id'],str(order.pk))
            duplicate = self.client.post('/api/billing/orders',json.dumps(body),
                HTTP_IDEMPOTENCY_KEY='other-tab',**headers)
            self.assertEqual(duplicate.status_code,409)
            self.assertEqual(duplicate.json()['error']['code'],'QUOTE_ALREADY_ORDERED')
            fresh = Quote.objects.create(account=account,product=product,snapshot=snapshot,
                amount_minor=731,currency='CNY',expires_at=timezone.now()+timedelta(minutes=5))
            unavailable = self.client.post('/api/billing/orders',
                json.dumps({**body,'quote_id':str(fresh.pk)}),HTTP_IDEMPOTENCY_KEY='new-intent',**headers)
            self.assertEqual(unavailable.status_code,503)
            self.assertEqual(unavailable.json()['error']['code'],'CONFIG_INCOMPLETE')
        self.assertEqual(Order.objects.count(),1)

    def test_order_page_query_rejects_unknown_repeated_or_invalid_offset(self):
        self.sign_in()
        for query in ('offset=-1','offset=1.0','offset=','offset=abc','offset=1000000000',
                      'offset=1&offset=2','offset=0&account_id=other','limit=50'):
            with self.subTest(query=query):
                response = self.client.get('/api/billing/orders?'+query)
                self.assertEqual(response.status_code,422)
                self.assertEqual(response.json()['error']['code'],'INVALID_REQUEST')
        result = self.client.get('/api/billing/orders?offset=999999999')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['items'],[])
        self.assertIsNone(result.json()['next_offset'])

    def test_purchase_quote_read_expiry_and_closed_sales_never_create_new_order(self):
        from backend.commerce.tests import FIXTURE_CHANNELS
        from backend.commerce.models import Quote, Order
        self.sign_in()
        with override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS,
                               BILLING_QUOTE_TTL_SECONDS=300):
            data = self.post('/api/billing/purchase-quotes', {'product_id': str(self.purchase_product().pk),
                'seat_quantity': 1, 'channel': 'fixture', 'buyer_region': 'CN'}).json()
        url = '/api/billing/purchase-quotes/' + data['id']
        result = self.client.get(url).json()
        self.assertFalse(result['can_order'])
        self.assertEqual(result['unavailable_reason'], 'CONFIG_INCOMPLETE')
        Quote.objects.filter(pk=data['id']).update(expires_at=timezone.now()-timedelta(seconds=1))
        result = self.client.get(url).json()
        self.assertEqual(result['unavailable_reason'], 'QUOTE_EXPIRED')
        self.assertEqual(self.client.get(url+'?account_id=anything').status_code, 422)
        self.assertEqual(self.post(url, {}).status_code, 405)
        self.assertFalse(Order.objects.exists())

    def test_purchase_quote_writes_require_csrf_current_account_and_finance_role(self):
        from backend.commerce.tests import FIXTURE_CHANNELS
        from backend.commerce.models import Quote
        self.sign_in()
        body = {'product_id': str(self.purchase_product().pk), 'seat_quantity': 1,
                'channel': 'fixture', 'buyer_region': 'CN'}
        with override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS,
                               BILLING_QUOTE_TTL_SECONDS=300):
            self.assertEqual(self.client.post('/api/billing/purchase-quotes', json.dumps(body),
                content_type='application/json').status_code, 403)
            self.assertEqual(self.client.post('/api/billing/purchase-quotes', json.dumps(body),
                content_type='application/json', HTTP_X_CSRFTOKEN=self.client.csrf_token,
                HTTP_X_BILLING_ACCOUNT=str(uuid.uuid4())).status_code, 409)
            self.assertEqual(self.post('/api/billing/purchase-quotes?amount_minor=1', body).status_code, 422)
            account = BillingAccount.objects.create(name='fixture business', kind='business')
            Membership.objects.create(account=account, user=get_user_model().objects.get(pk=self.client.session['_auth_user_id']), role='member')
            self.post('/api/billing/accounts/select', {'account_id': str(account.pk)})
            self.assertEqual(self.post('/api/billing/purchase-quotes', body).status_code, 403)
        self.assertFalse(Quote.objects.exists())

    def test_purchase_quote_refuses_unapproved_products_invalid_seats_and_expiry(self):
        from backend.commerce.tests import FIXTURE_CHANNELS
        from backend.commerce.models import Quote, ProductVersion
        self.sign_in()
        product = self.purchase_product()
        body = {'product_id': str(product.pk), 'seat_quantity': 1, 'channel': 'fixture', 'buyer_region': 'CN'}
        with override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS,
                               BILLING_QUOTE_TTL_SECONDS=300):
            for value in (True, '1', 0, 2):
                self.assertEqual(self.post('/api/billing/purchase-quotes', {**body, 'seat_quantity': value}).status_code, 422)
            ProductVersion.objects.filter(pk=product.pk).update(state='draft')
            self.assertEqual(self.post('/api/billing/purchase-quotes', body).status_code, 503)
            for ttl in (None, True, 0, -1, '300', 10**100):
                with override_settings(BILLING_QUOTE_TTL_SECONDS=ttl):
                    self.assertEqual(self.post('/api/billing/purchase-quotes', body).status_code, 503)
        self.assertFalse(Quote.objects.exists())

    def test_purchase_catalog_exposes_only_owned_published_versions_and_approved_terms(self):
        from backend.commerce.tests import FIXTURE_CHANNELS
        from backend.commerce.models import ProductVersion
        self.sign_in()
        product = self.purchase_product()
        ProductVersion.objects.create(code='hidden', version=1, kind='credits', audience='personal')
        url = '/api/billing/purchase-catalog'
        with override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS,
                               BILLING_QUOTE_TTL_SECONDS=300):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, response.content)
            data = response.json()
            self.assertEqual(data['account_id'], self.client.session['active_account_id'])
            self.assertEqual(len(data['items']), 1)
            item = data['items'][0]
            self.assertEqual(item['id'], str(product.pk))
            self.assertTrue(item['quote_available'])
            self.assertEqual(item['unit_amount_minor'], '731')
            self.assertEqual(item['purchase_options'], [{'channel': 'fixture', 'buyer_region': 'CN'}])
            self.assertNotIn('fixture-shenzhen', response.content.decode())
            with override_settings(BILLING_TERMS_DOCUMENTS={}):
                item = self.client.get(url).json()['items'][0]
                self.assertFalse(item['quote_available'])
                self.assertEqual(item['unavailable_reason'], 'TERMS_UNAVAILABLE')
        self.assertEqual(self.post(url, {}).status_code, 405)
        self.assertEqual(self.client.get(url+'?account_id=other').status_code, 422)

    def test_public_purchase_rejects_amounts_outside_browser_integer_precision(self):
        from backend.commerce.tests import FIXTURE_CHANNELS
        from backend.commerce.models import ProductVersion, Quote
        self.sign_in()
        product = self.purchase_product()
        with override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS,
                               BILLING_QUOTE_TTL_SECONDS=300):
            ProductVersion.objects.filter(pk=product.pk).update(unit_amount_minor=9007199254740992)
            item = self.client.get('/api/billing/purchase-catalog').json()['items'][0]
            self.assertFalse(item['quote_available'])
            response = self.post('/api/billing/purchase-quotes', {'product_id': str(product.pk),
                'seat_quantity': 1, 'channel': 'fixture', 'buyer_region': 'CN'})
            self.assertEqual(response.status_code, 503)
            self.assertFalse(Quote.objects.exists())
            ProductVersion.objects.filter(pk=product.pk).update(unit_amount_minor=9007199254740991)
            response = self.post('/api/billing/purchase-quotes', {'product_id': str(product.pk),
                'seat_quantity': 1, 'channel': 'fixture', 'buyer_region': 'CN'})
            self.assertEqual(response.status_code, 201, response.content)
            self.assertEqual(response.json()['amount_minor'], 9007199254740991)
            account = BillingAccount.objects.create(name='Precision fixture', kind='business')
            Membership.objects.create(account=account, user_id=self.client.session['_auth_user_id'], role='owner')
            self.assertEqual(self.post('/api/billing/accounts/select', {'account_id': str(account.pk)}).status_code, 200)
            ProductVersion.objects.filter(pk=product.pk).update(audience='business', minimum_seats=2,
                unit_amount_minor=4503599627370496,
                entitlements={'resource_mode': 'seat_included', 'benefits': {'zh': ['测试'], 'en': ['Fixture']}})
            count = Quote.objects.count()
            response = self.post('/api/billing/purchase-quotes', {'product_id': str(product.pk),
                'seat_quantity': 2, 'channel': 'fixture', 'buyer_region': 'CN'})
            self.assertEqual(response.status_code, 503, response.content)
            self.assertEqual(Quote.objects.count(), count)

    def test_quote_terms_withdrawal_or_changes_prevent_new_order(self):
        from backend.commerce.tests import FIXTURE_CHANNELS
        from backend.commerce.models import Order
        self.sign_in()
        with override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS,
                               BILLING_QUOTE_TTL_SECONDS=300):
            data = self.post('/api/billing/purchase-quotes', {'product_id': str(self.purchase_product().pk),
                'seat_quantity': 1, 'channel': 'fixture', 'buyer_region': 'CN'}).json()
            self.assertIn('digest', data['terms'])
            with override_settings(BILLING_TERMS_DOCUMENTS={}):
                detail = self.client.get('/api/billing/purchase-quotes/'+data['id']).json()
                self.assertFalse(detail['can_order'])
                self.assertIsNone(detail['terms'])
                self.assertEqual(detail['unavailable_reason'], 'TERMS_UNAVAILABLE')
                response = self.client.post('/api/billing/orders', json.dumps({'quote_id': data['id'],
                    'agreement_acceptance': 'fixture-1'}), content_type='application/json',
                    HTTP_X_CSRFTOKEN=self.client.csrf_token,
                    HTTP_X_BILLING_ACCOUNT=self.client.session['active_account_id'],
                    HTTP_IDEMPOTENCY_KEY='withdrawn-terms')
                self.assertEqual(response.status_code, 503)
            changed = {'fixture-1': {'approved': True, 'zh': {'title': 'Changed', 'paragraphs': ['Changed']},
                'en': {'title': 'Changed', 'paragraphs': ['Changed']}}}
            with override_settings(BILLING_TERMS_DOCUMENTS=changed):
                detail = self.client.get('/api/billing/purchase-quotes/'+data['id']).json()
                self.assertFalse(detail['can_order'])
                self.assertEqual(detail['unavailable_reason'], 'TERMS_MISMATCH')
        self.assertFalse(Order.objects.exists())

    def test_public_credits_purchase_requires_an_effective_paid_subscription(self):
        from backend.commerce.tests import fixture_product, FIXTURE_CHANNELS
        from backend.commerce.models import Order, Quote
        self.sign_in()
        product = fixture_product()
        body = {'product_id': str(product.pk), 'seat_quantity': 1,
                'channel': 'fixture', 'buyer_region': 'CN'}
        with override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS,
                               BILLING_QUOTE_TTL_SECONDS=300):
            for enabled in (True, False):
                with override_settings(BILLING_CREDITS_PURCHASE_ENABLED=enabled):
                    item = self.client.get('/api/billing/purchase-catalog').json()['items'][0]
                    self.assertEqual(item['id'], str(product.pk))
                    self.assertFalse(item['quote_available'])
                    self.assertEqual(item['unavailable_reason'], 'CONFIG_INCOMPLETE')
                    response = self.post('/api/billing/purchase-quotes', body)
                    self.assertEqual(response.status_code, 503, response.content)
                    self.assertEqual(response.json()['error']['code'], 'CONFIG_INCOMPLETE')
        self.assertFalse(Quote.objects.exists())
        self.assertFalse(Order.objects.exists())

    def test_challenge_is_browser_bound_one_use_and_stores_no_plain_code(self):
        challenge, code = self.challenge()
        record = LoginChallenge.objects.get(pk=challenge)
        self.assertNotEqual(record.code_digest, code)
        foreign = Client(enforce_csrf_checks=True)
        self.session(foreign)
        response = self.post('/api/auth/verify', {'challenge_id': challenge, 'code': code}, foreign)
        self.assertEqual(response.status_code, 422)
        data = {'challenge_id': challenge, 'code': code}
        good = self.post('/api/auth/verify', data)
        self.assertEqual(good.status_code, 200)
        self.client.csrf_token = good.json()['csrf_token']
        self.assertEqual(self.post('/api/auth/verify', data).status_code, 422)
        self.assertEqual(LoginIdentity.objects.count(), 1)

    def test_wrong_attempts_persist_and_exhaust_challenge(self):
        challenge, code = self.challenge()
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(5):
            self.assertEqual(self.post('/api/auth/verify', {'challenge_id': challenge, 'code': wrong}).status_code, 422)
        self.assertEqual(LoginChallenge.objects.get(pk=challenge).attempts, 5)
        self.assertEqual(self.post('/api/auth/verify', {'challenge_id': challenge, 'code': code}).status_code, 422)
        self.assertFalse(LoginIdentity.objects.exists())

    def test_expired_challenge(self):
        challenge, code = self.challenge()
        LoginChallenge.objects.filter(pk=challenge).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.post('/api/auth/verify', {'challenge_id': challenge, 'code': code}).status_code, 422)

    def test_resend_throttled_and_delivery_off_does_not_store_identifier(self):
        self.challenge()
        response = self.post('/api/auth/challenges', {'method': 'email', 'identifier': 'owner@example.invalid'})
        self.assertEqual(response.status_code, 429)
        with override_settings(AUTH_EMAIL_ENABLED=False):
            response = self.post('/api/auth/challenges', {'method': 'email', 'identifier': 'off@example.invalid'})
            self.assertEqual(response.status_code, 503)
        self.assertFalse(LoginChallenge.objects.filter(identifier='off@example.invalid').exists())

    def test_phone_not_advertised_until_real_delivery_is_selected(self):
        self.assertFalse(self.session()['capabilities']['phone_login'])
        response = self.post('/api/auth/challenges', {'method': 'phone', 'identifier': '+8613800000000'})
        self.assertEqual(response.status_code, 503)

    def test_membership_is_checked_on_every_request(self):
        data = self.sign_in()
        foreign_user = get_user_model().objects.create_user(username='foreign')
        other = BillingAccount.objects.create(kind='personal', name='Other', personal_owner=foreign_user)
        response = self.post('/api/billing/accounts/select', {'account_id': str(other.pk)})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.session()['active_account_id'], data['active_account_id'])
        Membership.objects.filter(user_id=data['user']['id']).update(is_active=False)
        self.assertEqual(self.client.get('/api/billing/account').status_code, 403)

    def test_purchase_inquiry_is_persisted_scoped_and_duplicate_safe(self):
        self.sign_in()
        body = {'company': 'Fixture Company', 'seats': 2, 'requirements': '合同采购需求',
                'contact_email': 'procurement@example.invalid'}
        one = self.post('/api/billing/purchase-inquiries', body)
        two = self.post('/api/billing/purchase-inquiries', body)
        self.assertEqual(one.status_code, 201)
        self.assertEqual(one.json()['id'], two.json()['id'])
        self.assertEqual(PurchaseInquiry.objects.count(), 1)
        self.assertEqual(len(self.client.get('/api/billing/purchase-inquiries').json()['items']), 1)
        stranger = Client(enforce_csrf_checks=True)
        self.session(stranger)
        self.sign_in('stranger@example.invalid', stranger)
        self.assertEqual(stranger.get('/api/billing/purchase-inquiries').json()['items'], [])

    def test_purchase_inquiry_pagination_and_input_boundaries(self):
        data = self.sign_in()
        account_id, user_id = data['active_account_id'], data['user']['id']
        rows = [PurchaseInquiry.objects.create(account_id=account_id, created_by_id=user_id,
            company=f'Company {index}', seats=2, requirements='<script>plain text</script>',
            contact_email='fixture@example.invalid', request_hash=str(index)) for index in range(101)]
        PurchaseInquiry.objects.filter(account_id=account_id).update(created_at=timezone.now())
        expected = sorted((str(row.pk) for row in rows), reverse=True)
        seen = []
        for offset in (0, 50, 100):
            response = self.client.get('/api/billing/purchase-inquiries', {'offset': offset}).json()
            self.assertEqual(response['account_id'], account_id)
            self.assertEqual(response['offset'], offset)
            self.assertEqual(response['total'], 101)
            self.assertEqual(response['next_offset'], offset+50 if offset < 100 else None)
            seen.extend(item['id'] for item in response['items'])
        self.assertEqual(seen, expected)
        for query in ('offset=-1', 'offset=1.5', 'offset=1000000000', 'offset=0&offset=50', 'account_id=x'):
            self.assertEqual(self.client.get('/api/billing/purchase-inquiries?'+query).status_code, 422)
        body = {'company':'a'*160, 'requirements':'b'*2000, 'seats':2,
                'contact_email':'fixture@example.invalid'}
        self.assertEqual(self.post('/api/billing/purchase-inquiries', body).status_code, 201)
        for field in ('company', 'requirements'):
            self.assertEqual(self.post('/api/billing/purchase-inquiries', {**body,field:body[field]+'x'}).status_code, 422)

    def test_member_cannot_read_financial_data_or_submit_procurement(self):
        data = self.sign_in()
        account = BillingAccount.objects.create(kind='business', name='Fixture business')
        Membership.objects.create(account=account, user_id=data['user']['id'], role='member')
        self.assertEqual(self.post('/api/billing/accounts/select', {'account_id': str(account.id)}).status_code, 200)
        self.assertEqual(self.client.get('/api/billing/account').status_code, 403)
        self.assertEqual(self.client.get('/api/billing/purchase-inquiries').status_code, 403)
        self.assertEqual(self.client.get('/api/billing/organization').status_code,403)
        self.assertEqual(self.client.get('/api/billing/payment-methods').status_code,403)

    def test_organization_read_is_masked_scoped_paginated_and_read_only(self):
        self.assertEqual(self.client.get('/api/billing/organization').status_code,401)
        data = self.sign_in()
        account = BillingAccount.objects.create(kind='business',name='Fixture business')
        Membership.objects.create(account=account,user_id=data['user']['id'],role='owner')
        self.post('/api/billing/accounts/select',{'account_id':str(account.pk)})
        response = self.client.get('/api/billing/organization')
        self.assertEqual(response.status_code,200)
        body = response.json()
        self.assertEqual(body['total_members'],1)
        self.assertEqual(body['members'][0]['masked_identifier'],'o***@example.invalid')
        self.assertNotIn('owner@example.invalid',response.content.decode())
        self.assertFalse(body['can_edit'])
        self.assertIsNone(body['members'][0]['shared_used'])
        self.assertEqual(self.client.get('/api/billing/organization?offset=1').json()['members'],[])
        self.assertEqual(self.client.get('/api/billing/organization?offset=-1').status_code,422)
        self.assertEqual(self.client.get('/api/billing/organization',HTTP_X_BILLING_ACCOUNT=str(uuid.uuid4())).status_code,409)
        self.assertEqual(self.post('/api/billing/organization',{}).status_code,405)

    def test_member_can_read_only_own_limits_without_financial_access(self):
        self.assertEqual(self.client.get('/api/billing/my-usage').status_code,401)
        self.sign_in()
        user = get_user_model().objects.get(pk=self.client.session['_auth_user_id'])
        business = BillingAccount.objects.create(kind='business',name='Fixture company')
        member = Membership.objects.create(account=business,user=user,role='member')
        self.post('/api/billing/accounts/select',{'account_id':str(business.pk)})
        response = self.client.get('/api/billing/my-usage')
        self.assertEqual(response.status_code,200)
        data = response.json()
        self.assertEqual(set(data),{'account_id','member','period_start','period_end','subscription_active','can_edit'})
        self.assertEqual(data['member']['id'],member.pk)
        self.assertIsNone(data['member']['shared_used'])
        self.assertFalse(data['can_edit'])
        self.assertEqual(self.client.get('/api/billing/account').status_code,403)
        self.assertEqual(self.client.get('/api/billing/organization').status_code,403)
        self.assertEqual(self.client.get('/api/billing/my-usage?user_id=999').status_code,422)
        self.assertEqual(self.client.get('/api/billing/my-usage',HTTP_X_BILLING_ACCOUNT=str(uuid.uuid4())).status_code,409)
        self.assertEqual(self.post('/api/billing/my-usage',{}).status_code,405)
        member.is_active = False
        member.save(update_fields=['is_active'])
        self.assertEqual(self.client.get('/api/billing/my-usage').status_code,403)

    def test_payment_method_read_is_scoped_redacted_and_read_only(self):
        from .commerce.subscriptions import register_verified_payment_method
        self.assertEqual(self.client.get('/api/billing/payment-methods').status_code,401)
        data = self.sign_in()
        account = BillingAccount.objects.get(pk=data['active_account_id'])
        method = register_verified_payment_method(account,channel='alipay',merchant_reference='private-merchant',
            fingerprint='c'*64,evidence_hash='d'*64)
        response = self.client.get('/api/billing/payment-methods')
        self.assertEqual(response.status_code,200)
        body = response.json()
        self.assertEqual(body['account_id'],str(account.pk))
        self.assertEqual(body['items'][0]['id'],method)
        self.assertFalse(body['can_edit'])
        self.assertEqual(body['items'][0]['state'],'active')
        for value in ('private-merchant','c'*64,'d'*64,'confirmation_hash'):
            self.assertNotIn(value,response.content.decode())
        self.assertEqual(self.client.get('/api/billing/payment-methods?offset=1').json()['items'],[])
        self.assertEqual(self.client.get('/api/billing/payment-methods?offset=-1').status_code,422)
        self.assertEqual(self.client.get('/api/billing/payment-methods',HTTP_X_BILLING_ACCOUNT=str(uuid.uuid4())).status_code,409)
        self.assertEqual(self.post('/api/billing/payment-methods',{}).status_code,405)

    def test_automatic_topup_read_requires_business_finance_context(self):
        self.assertEqual(self.client.get('/api/billing/automatic-topup').status_code,401)
        data = self.sign_in()
        self.assertEqual(self.client.get('/api/billing/automatic-topup').status_code,403)
        owner = BillingAccount.objects.get(pk=data['active_account_id']).personal_owner
        business = BillingAccount.objects.create(kind='business',name='fixture-topup')
        Membership.objects.create(account=business,user=owner,role='owner')
        session = self.client.session
        session['active_account_id'] = str(business.pk)
        session.save()
        result = self.client.get('/api/billing/automatic-topup')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json(),{'account_id':str(business.pk),'charging_enabled':False,'authorization':None,'budget':None})
        self.assertEqual(self.post('/api/billing/automatic-topup',{}).status_code,405)
        self.assertEqual(self.client.get('/api/billing/automatic-topup',HTTP_X_BILLING_ACCOUNT=str(uuid.uuid4())).status_code,409)
        Membership.objects.filter(account=business,user=owner).update(role='member')
        self.assertEqual(self.client.get('/api/billing/automatic-topup').status_code,403)

    def test_stale_tab_cannot_write_to_changed_account(self):
        data = self.sign_in()
        business = BillingAccount.objects.create(kind='business', name='New context')
        Membership.objects.create(account=business, user_id=data['user']['id'], role='owner')
        self.assertEqual(self.post('/api/billing/accounts/select', {'account_id': str(business.pk)}).status_code, 200)
        body = {'company': 'Old context', 'seats': 2, 'requirements': 'Do not misattribute',
                'contact_email': 'context@example.invalid'}
        for headers in ({}, {'HTTP_X_BILLING_ACCOUNT': data['active_account_id']}):
            result = self.client.post('/api/billing/purchase-inquiries', json.dumps(body),
                content_type='application/json', HTTP_X_CSRFTOKEN=self.client.csrf_token, **headers)
            self.assertEqual(result.status_code, 409)
            self.assertEqual(result.json()['error']['code'], 'ACCOUNT_CHANGED')
        self.assertFalse(PurchaseInquiry.objects.exists())

    def test_payload_and_method_boundaries(self):
        self.assertEqual(self.post('/api/auth/logout', {'user_id': 1}).status_code, 422)
        self.assertEqual(self.client.get('/api/auth/verify').status_code, 405)
        self.assertEqual(self.client.post('/api/auth/challenges', '[]', content_type='application/json',
                                         HTTP_X_CSRFTOKEN=self.client.csrf_token).status_code, 422)

    def test_extreme_json_integer_is_client_error_not_server_error(self):
        body = '{"method":"email","identifier":' + '1' * 5000 + '}'
        response = self.client.post('/api/auth/challenges', body, content_type='application/json',
                                    HTTP_X_CSRFTOKEN=self.client.csrf_token)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['error']['code'], 'INVALID_JSON')

    def test_no_financial_write_or_webhook_can_be_faked(self):
        self.sign_in()
        for path in ('/api/billing/credits', '/api/billing/refunds', '/api/payments/webhooks/alipay'):
            self.assertEqual(self.post(path, {}).status_code, 503)
        self.assertEqual(self.post('/api/billing/orders', {'amount_minor': 1}).status_code, 422)

    def test_session_and_responses_are_not_publicly_cacheable(self):
        response = self.client.get('/api/auth/session')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(response['X-Frame-Options'], 'DENY')
        self.assertIn('X-Request-ID', response)

    def test_delivery_failure_does_not_create_valid_code_or_user(self):
        from unittest.mock import patch
        with patch('backend.views.send_mail', side_effect=OSError('fixture delivery failure')):
            result = self.post('/api/auth/challenges', {'method': 'email', 'identifier': 'fail@example.invalid'})
        self.assertEqual(result.status_code, 503)
        self.assertFalse(LoginChallenge.objects.get(identifier='fail@example.invalid').delivered)
        self.assertFalse(LoginIdentity.objects.exists())

    def test_same_verified_email_returns_same_account(self):
        data = self.sign_in()
        other = Client(enforce_csrf_checks=True)
        self.session(other)
        with override_settings(AUTH_RESEND_SECONDS=0):
            second = self.sign_in(client=other)
        self.assertEqual(data['user']['id'], second['user']['id'])
        self.assertEqual(data['active_account_id'], second['active_account_id'])
        self.assertEqual(LoginIdentity.objects.count(), 1)


@skipUnlessDBFeature('has_select_for_update')
@override_settings(AUTH_EMAIL_ENABLED=True, EMAIL_HOST='fixture.invalid',
                   DEFAULT_FROM_EMAIL='no-reply@example.invalid', AUTH_RESEND_SECONDS=0,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AccountConcurrencyTests(TransactionTestCase):
    def test_verification_locks_identity_before_challenge(self):
        from django.db import connection
        client = Client(enforce_csrf_checks=True)
        token = client.get('/api/auth/session').json()['csrf_token']
        result = client.post('/api/auth/challenges', json.dumps({
            'method': 'email', 'identifier': 'lock-order@example.invalid'}),
            content_type='application/json', HTTP_X_CSRFTOKEN=token)
        code = re.search(r'\b\d{6}\b', mail.outbox[-1].body).group()
        locks = []

        def record(execute, sql, params, many, context):
            if 'FOR UPDATE' in sql:
                locks.append(sql)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(record):
            response = client.post('/api/auth/verify', json.dumps({
                'challenge_id': result.json()['challenge_id'], 'code': code}),
                content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertIn('backend_auththrottle', locks[0])
        self.assertIn('backend_loginchallenge', locks[1])

    def test_two_verified_challenges_create_one_identity_account(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        clients, submissions = [], []
        for _ in range(2):
            client = Client(enforce_csrf_checks=True)
            token = client.get('/api/auth/session').json()['csrf_token']
            result = client.post('/api/auth/challenges', json.dumps({
                'method': 'email', 'identifier': 'concurrent@example.invalid'}),
                content_type='application/json', HTTP_X_CSRFTOKEN=token)
            self.assertEqual(result.status_code, 201)
            code = re.search(r'\b\d{6}\b', mail.outbox[-1].body).group()
            clients.append((client, token))
            submissions.append({'challenge_id': result.json()['challenge_id'], 'code': code})
        barrier = Barrier(2)

        def verify(index):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                client, token = clients[index]
                response = client.post('/api/auth/verify', json.dumps(submissions[index]),
                                       content_type='application/json', HTTP_X_CSRFTOKEN=token)
                return response.status_code, response.json()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(verify, [0, 1]))
        self.assertEqual([item[0] for item in results], [200, 200])
        self.assertEqual(results[0][1]['user']['id'], results[1][1]['user']['id'])
        self.assertEqual(LoginIdentity.objects.count(), 1)
        self.assertEqual(BillingAccount.objects.count(), 1)
