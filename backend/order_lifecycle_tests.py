"""Unpaid checkout orders: one per kind, buyer cancellation and timed closing."""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from backend import payments
from backend.commerce import billing as commerce
from backend.commerce.models import CreditBatch, Order, PaymentAttempt, PaymentFact, ProductVersion
from backend.commerce.tests import fixture_account
from backend.models import AccountSession, Membership


TERMS = {'fixture-1': {'approved': True,
    'zh': {'title': '测试条款', 'paragraphs': ['仅限隔离测试。']},
    'en': {'title': 'Test terms', 'paragraphs': ['Isolated fixture only.']}}}


@override_settings(ENVIRONMENT='test', BILLING_SALES_ENABLED=True, BILLING_QUOTE_TTL_SECONDS=300,
    BILLING_TERMS_DOCUMENTS=TERMS, BILLING_APPROVED_CHANNELS={'alipay': {'enabled': True,
        'currency': 'CNY', 'merchant_entity': 'fixture-shenzhen', 'regions': ['CN'],
        'capabilities': ['one_time', 'term_monthly']}})
class UnpaidOrderLifecycleTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_pem = key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode('ascii')
        cls.public_pem = key.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)

    def setUp(self):
        self.configuration = {'enabled': True, 'app_id': '2026000000000001',
            'seller_id': '2088000000000001', 'merchant_entity': 'fixture-shenzhen',
            'private_key': self.private_pem, 'public_key': self.public_pem, 'payment_ttl_seconds': 900}
        self.enterContext(override_settings(BILLING_ALIPAY_SANDBOX=self.configuration))
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            self.enterContext(patch(target, side_effect=AssertionError('PSP network is forbidden')))
        self.user, self.account = fixture_account('unpaid-lifecycle')
        self.member = Membership.objects.create(user=self.user, account=self.account, role='owner')
        self.product = ProductVersion.objects.create(code='pro', version=1, kind='subscription',
            audience='personal', billing_cycle='monthly', unit_amount_minor=731, credits_quantity='10',
            entitlements={'benefits': {'zh': ['测试权益'], 'en': ['Test benefit']}},
            entitlement_version='fixture-1', credit_rule_version='fixture-1', terms_version='fixture-1',
            state='published', effective_at=timezone.now()-timedelta(days=1))
        self.client.force_login(self.user)
        session = self.client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        AccountSession.objects.create(user=self.user, session_key=session.session_key, client='Fixture')
        self.sequence = 0

    def order(self):
        self.sequence += 1
        quote = commerce.create_quote(self.account, self.user, self.product.pk, 1,
            timezone.now()+timedelta(minutes=5), channel='alipay', buyer_region='CN',
            terms_digest=commerce.purchase_terms('fixture-1')['digest'])
        return commerce.create_order(self.account, self.user,
            {'quote_id': quote['id'], 'agreement_acceptance': 'fixture-1'}, f'lifecycle-{self.sequence}')

    def post(self, path, body='{}'):
        response = self.client.post('/api/'+path, body, content_type='application/json',
            HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        self.assertEqual(response['Cache-Control'], 'no-store')
        return response

    def channel(self, *answers):
        """Signed channel answers in call order: (operation, fields)."""
        calls = []
        replies = iter(answers)

        def call(configuration, operation, reference, **kwargs):
            expected, fields = next(replies)
            self.assertEqual(operation, expected)
            calls.append(operation)
            if isinstance(fields, BaseException):
                raise fields
            return {'out_trade_no': str(reference), **fields}, (str(len(calls))*64)[:64]
        return patch.object(payments, '_alipay_sandbox_call', side_effect=call), calls

    def test_second_unpaid_order_of_same_kind_is_refused_with_its_order(self):
        first = self.order()
        with self.assertRaisesRegex(ValueError, '^UNPAID_ORDER_EXISTS$'):
            self.order()
        catalog = commerce.purchase_catalog(self.account)['items'][0]
        self.assertEqual((catalog['unavailable_reason'], catalog['pending_order_id']),
            ('UNPAID_ORDER_EXISTS', first['id']))
        self.assertEqual(Order.objects.count(), 1)

    def test_http_create_reports_unpaid_order_conflict(self):
        self.order()
        quote = self.client.post('/api/billing/purchase-quotes', {'product_id': str(self.product.pk),
            'seat_quantity': 1, 'channel': 'alipay', 'buyer_region': 'CN'}, content_type='application/json',
            HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        self.assertEqual(quote.status_code, 409, quote.content)
        self.assertEqual(quote.json()['error']['code'], 'UNPAID_ORDER_EXISTS')

    def test_buyer_cancels_undispatched_order_and_can_order_again(self):
        first = self.order()
        detail = self.client.get(f'/api/billing/orders/{first["id"]}', HTTP_X_BILLING_ACCOUNT=str(self.account.pk)).json()
        self.assertTrue(detail['cancelable'])
        created = Order.objects.get(pk=first['id']).created_at
        self.assertEqual(detail['payment_deadline'], (created+timedelta(minutes=15)).isoformat())
        response = self.post(f'billing/orders/{first["id"]}/cancel')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['cancel_status'], 'cancelled')
        self.assertEqual(response.json()['order']['status'], 'closed')
        self.assertFalse(response.json()['cancelable'])
        self.assertEqual(self.post(f'billing/orders/{first["id"]}/cancel').status_code, 200)
        self.assertNotEqual(self.order()['id'], first['id'])

    def test_cancel_requires_post_account_header_and_financial_role(self):
        first = self.order()
        url = f'/api/billing/orders/{first["id"]}/cancel'
        self.assertEqual(self.client.get(url, HTTP_X_BILLING_ACCOUNT=str(self.account.pk)).status_code, 405)
        self.assertEqual(self.client.post(url, '{}', content_type='application/json').status_code, 409)
        Membership.objects.filter(pk=self.member.pk).update(role='member')
        self.assertEqual(self.post(f'billing/orders/{first["id"]}/cancel').status_code, 403)
        self.assertEqual(Order.objects.get(pk=first['id']).status, 'pending_payment')

    def test_cancel_closes_dispatched_payment_only_on_verified_channel_answer(self):
        first = self.order()
        self.assertEqual(self.post(f'billing/orders/{first["id"]}/pay').status_code, 200)
        attempt = PaymentAttempt.objects.get(order_id=first['id'])
        self.assertEqual(attempt.state, 'pending')
        unknown, calls = self.channel(('close', ValueError('ALIPAY_QUERY_UNKNOWN')),
            ('query', {'code': '10000', 'total_amount': '7.31', 'trade_status': 'WAIT_BUYER_PAY'}))
        with unknown:
            response = self.post(f'billing/orders/{first["id"]}/cancel')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['cancel_status'], 'pending')
        self.assertEqual(Order.objects.get(pk=first['id']).status, 'pending_payment')
        closed, calls = self.channel(('query', {'code': '10000', 'total_amount': '7.31', 'trade_status': 'TRADE_CLOSED'}))
        with closed:
            response = self.post(f'billing/orders/{first["id"]}/cancel')
        # The close was claimed once; a later cancel only queries and still lacks close proof.
        self.assertEqual(calls, ['query'])
        self.assertEqual(response.json()['cancel_status'], 'pending')

    def test_cancel_of_dispatched_trade_closes_order_after_signed_close_and_query(self):
        first = self.order()
        self.post(f'billing/orders/{first["id"]}/pay')
        answers, calls = self.channel(('close', {'code': '10000'}),
            ('query', {'code': '10000', 'total_amount': '7.31', 'trade_status': 'TRADE_CLOSED'}))
        with answers:
            response = self.post(f'billing/orders/{first["id"]}/cancel')
        self.assertEqual(calls, ['close', 'query'])
        self.assertEqual(response.json()['cancel_status'], 'cancelled')
        self.assertEqual(Order.objects.get(pk=first['id']).status, 'closed')
        self.assertEqual(PaymentAttempt.objects.get(order_id=first['id']).state, 'cancelled')

    def test_cancel_racing_payment_delivers_the_paid_order(self):
        first = self.order()
        self.post(f'billing/orders/{first["id"]}/pay')
        paid_at = timezone.now().astimezone(timezone.get_fixed_timezone(480)).strftime('%Y-%m-%d %H:%M:%S')
        answers, _ = self.channel(('close', {'code': '40004', 'sub_code': 'ACQ.TRADE_STATUS_ERROR'}),
            ('query', {'code': '10000', 'total_amount': '7.31', 'trade_status': 'TRADE_SUCCESS',
                       'trade_no': 'race_paid', 'send_pay_date': paid_at}))
        with answers:
            response = self.post(f'billing/orders/{first["id"]}/cancel')
        self.assertEqual(response.json()['cancel_status'], 'paid')
        self.assertEqual(Order.objects.get(pk=first['id']).status, 'fulfilled')
        self.assertEqual(PaymentFact.objects.get().disposition, 'accepted')
        self.assertEqual(self.post(f'billing/orders/{first["id"]}/cancel').json()['error']['code'],
            'ORDER_STATE_CONFLICT')

    def test_overdue_undispatched_order_no_longer_blocks_a_new_one(self):
        first = self.order()
        later = timezone.now()+timedelta(minutes=15, seconds=1)
        with patch('django.utils.timezone.now', return_value=later):
            second = self.order()
        self.assertEqual(Order.objects.get(pk=first['id']).status, 'closed')
        self.assertEqual(Order.objects.get(pk=second['id']).status, 'pending_payment')

    def test_overdue_dispatched_order_keeps_blocking_until_channel_closes_it(self):
        first = self.order()
        self.post(f'billing/orders/{first["id"]}/pay')
        later = timezone.now()+timedelta(minutes=16)
        with patch('django.utils.timezone.now', return_value=later):
            with self.assertRaisesRegex(ValueError, '^UNPAID_ORDER_EXISTS$'):
                self.order()

    def test_sweeper_closes_overdue_orders_and_leaves_fresh_ones(self):
        undispatched = self.order()
        Order.objects.filter(pk=undispatched['id']).update(created_at=timezone.now()-timedelta(minutes=20))
        self.product.code, self.product.pk = 'pro_second', None
        self.product._state.adding = True
        self.product.save()
        self.product = ProductVersion.objects.get(code='pro_second')
        Order.objects.filter(pk=undispatched['id']).update(status='closed')
        dispatched = self.order()
        self.post(f'billing/orders/{dispatched["id"]}/pay')
        Order.objects.filter(pk=undispatched['id']).update(status='pending_payment')
        PaymentAttempt.objects.filter(order_id=dispatched['id']).update(
            expires_at=timezone.now()-timedelta(minutes=6))
        Order.objects.filter(pk=dispatched['id']).update(created_at=timezone.now()-timedelta(minutes=21))
        missing = {'code': '40004', 'sub_code': 'ACQ.TRADE_NOT_EXIST'}
        answers, calls = self.channel(('close', missing), ('query', missing))
        out = StringIO()
        with answers:
            call_command('close_unpaid_orders', stdout=out)
        self.assertEqual(calls, ['close', 'query'])
        self.assertIn('expired: 1; channel_closed: 1; paid: 0; still_open: 0', out.getvalue())
        self.assertEqual(Order.objects.get(pk=undispatched['id']).status, 'closed')
        self.assertEqual(Order.objects.get(pk=dispatched['id']).status, 'closed')
        fresh = self.order()
        with self.channel()[0]:
            call_command('close_unpaid_orders', stdout=StringIO())
        self.assertEqual(Order.objects.get(pk=fresh['id']).status, 'pending_payment')
        self.assertFalse(CreditBatch.objects.exists())

    def test_sweeper_delivers_an_overdue_order_that_was_actually_paid(self):
        first = self.order()
        self.post(f'billing/orders/{first["id"]}/pay')
        attempt = PaymentAttempt.objects.get(order_id=first['id'])
        paid_at = (attempt.dispatched_at+timedelta(seconds=30)).astimezone(
            timezone.get_fixed_timezone(480)).strftime('%Y-%m-%d %H:%M:%S')
        Order.objects.filter(pk=first['id']).update(created_at=timezone.now()-timedelta(minutes=20))
        PaymentAttempt.objects.filter(pk=attempt.pk).update(expires_at=timezone.now()-timedelta(minutes=5))
        answers, _ = self.channel(('close', {'code': '40004', 'sub_code': 'ACQ.TRADE_STATUS_ERROR'}),
            ('query', {'code': '10000', 'total_amount': '7.31', 'trade_status': 'TRADE_SUCCESS',
                       'trade_no': 'late_sweep', 'send_pay_date': paid_at}))
        out = StringIO()
        with answers, patch('django.utils.timezone.now', return_value=timezone.now()+timedelta(seconds=40)):
            call_command('close_unpaid_orders', stdout=out)
        self.assertIn('paid: 1', out.getvalue())
        self.assertEqual(Order.objects.get(pk=first['id']).status, 'fulfilled')
