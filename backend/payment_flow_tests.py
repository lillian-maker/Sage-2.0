"""Real HTTP payment flows, isolated test configuration and dispatch boundaries."""
import json
import base64
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as datetime_timezone
from threading import Event, Barrier
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.apps import apps
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import close_old_connections, connection, transaction
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from backend import payments
from backend.commerce import billing as commerce
from backend.commerce.models import CreditBatch, Order, PaymentAttempt, PaymentFact, Quote
from backend.commerce.tests import fixture_account, fixture_product
from backend.models import AccountSession, BillingAccount, Membership
from backend.views import api


@override_settings(ENVIRONMENT='test', BILLING_WALLET_TESTS={
    'google_pay': {'enabled': True}, 'apple_pay': {'enabled': True}})
class WalletTestConfigurationTests(TestCase):
    def setUp(self):
        self.user, self.account = fixture_account('wallet-http-fixture')
        self.member = Membership.objects.create(user=self.user, account=self.account, role='owner')
        product = fixture_product()
        snapshot = {'channel': 'alipay', 'merchant_entity': 'fixture-shenzhen',
                    'amount_minor': 731, 'currency': 'CNY', 'billing_cycle': 'monthly'}
        quote = Quote.objects.create(account=self.account, product=product, snapshot=snapshot,
            amount_minor=731, currency='CNY', expires_at=timezone.now()+timedelta(minutes=5))
        self.order = Order.objects.create(account=self.account, created_by=self.user, quote=quote,
            idempotency_key='wallet-fixture', request_hash='a'*64,
            snapshot=snapshot, amount_minor=731, currency='CNY')
        self.url = f'/api/billing/orders/{self.order.pk}/wallet-test'
        self.client.force_login(self.user)
        session = self.client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            guard = patch(target, side_effect=AssertionError('Wallet configuration must be offline'))
            guard.start()
            self.addCleanup(guard.stop)

    def get(self, *, header=True, suffix=''):
        before = {model._meta.label: list(model.objects.order_by('pk').values())
                  for model in apps.get_app_config('commerce').get_models()}
        headers = {'HTTP_X_BILLING_ACCOUNT': str(self.account.pk)} if header else {}
        response = self.client.get(self.url+suffix, **headers)
        after = {model._meta.label: list(model.objects.order_by('pk').values())
                 for model in apps.get_app_config('commerce').get_models()}
        self.assertEqual(before, after)
        self.assertEqual(response['Cache-Control'], 'no-store')
        return response

    def test_explicit_test_permission_returns_only_order_bound_public_configuration(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        common = {'environment': 'TEST', 'currency': 'CNY', 'amount_minor': '731',
            'billing_cycle': 'monthly', 'account_id': str(self.account.pk), 'order_id': str(self.order.pk)}
        self.assertEqual(response.json(), {'wallet_tests': [
            {**common, 'channel': 'google_pay'}, {**common, 'channel': 'apple_pay'}]})

    def test_default_development_and_production_cannot_be_enabled_by_browser(self):
        for environment, configuration in (('test', {}), ('development', {'google_pay': {'enabled': True}}),
                ('production', {'google_pay': {'enabled': True}})):
            with self.subTest(environment=environment), override_settings(
                    ENVIRONMENT=environment, BILLING_WALLET_TESTS=configuration):
                self.assertEqual(self.get().status_code, 503)
        self.assertEqual(self.get(suffix='?wallet_test=1').status_code, 422)

    def test_configuration_never_returns_merchant_material_or_invents_country(self):
        with override_settings(BILLING_WALLET_TESTS={'apple_pay': {'enabled': True,
                'country_code': 'US', 'private_material': 'synthetic-not-returned'},
                'other_gateway': {'enabled': True}}):
            response = self.get()
        self.assertEqual(response.status_code, 200)
        data = response.json()['wallet_tests']
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['country_code'], 'US')
        self.assertNotIn('private_material', data[0])
        self.assertNotIn('other_gateway', json.dumps(data))

    def test_missing_context_or_financial_permission_is_denied(self):
        self.assertEqual(self.get(header=False).status_code, 409)
        Membership.objects.filter(pk=self.member.pk).update(role='member')
        self.assertEqual(self.get().status_code, 403)
        self.client.logout()
        self.assertEqual(self.get().status_code, 401)

    def test_cross_account_order_is_hidden(self):
        _, other = fixture_account('wallet-other')
        Membership.objects.create(user=self.user, account=other, role='owner')
        session = self.client.session
        session['active_account_id'] = str(other.pk)
        session.save()
        response = self.client.get(self.url, HTTP_X_BILLING_ACCOUNT=str(other.pk))
        self.assertEqual(response.status_code, 404)
        self.assertNotIn('wallet_tests', response.json())

    def test_paid_closed_or_dispatched_orders_cannot_launch_test_sheet(self):
        for status in ('fulfilled', 'closed'):
            Order.objects.filter(pk=self.order.pk).update(status=status)
            self.assertEqual(self.get().status_code, 503)
        Order.objects.filter(pk=self.order.pk).update(status='pending_payment')
        PaymentAttempt.objects.create(account=self.account, order=self.order, intent_key='wallet-existing',
            expires_at=timezone.now()+timedelta(minutes=5), state='unknown', dispatched_at=timezone.now())
        self.assertEqual(self.get().status_code, 503)

    def test_wallet_test_is_read_only_get(self):
        response = self.client.post(self.url, '{}', content_type='application/json',
            HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        self.assertEqual(response.status_code, 405)


@override_settings(ENVIRONMENT='test', BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS={
    'alipay': {'enabled': True, 'currency': 'CNY', 'merchant_entity': 'fixture-shenzhen',
        'regions': ['CN'], 'capabilities': ['one_time']}})
class AlipaySandboxHTTPTests(TransactionTestCase):
    """Catch missing commit, unsafe gating, client amounts and duplicate signing."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_pem = cls.private.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode('ascii')
        cls.public_pem = cls.private.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)

    def setUp(self):
        self.configuration = {'enabled': True, 'app_id': '2026000000000001',
            'seller_id': '2088000000000001', 'merchant_entity': 'fixture-shenzhen',
            'private_key': self.private_pem, 'public_key': self.public_pem,
            'payment_ttl_seconds': 900}
        self.enterContext(override_settings(BILLING_ALIPAY_SANDBOX=self.configuration))
        self.user, self.account = fixture_account('sandbox-http-fixture')
        self.member = Membership.objects.create(user=self.user, account=self.account, role='owner')
        self.product = fixture_product()
        quote = commerce.create_quote(self.account, self.user, self.product.pk, 1,
            timezone.now()+timedelta(minutes=5), channel='alipay', buyer_region='CN')
        order = commerce.create_order(self.account, self.user,
            {'quote_id': quote['id'], 'agreement_acceptance': 'fixture-1'}, 'sandbox-http-order')
        self.order = Order.objects.get(pk=order['id'])
        self.url = f'/api/billing/orders/{self.order.pk}/pay'
        self.client.force_login(self.user)
        session = self.client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        self.session_key = session.session_key
        self.metadata = AccountSession.objects.create(user=self.user, session_key=self.session_key, client='Fixture')
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            self.enterContext(patch(target, side_effect=AssertionError('Payment signing must stay offline')))

    def post(self, body='{}'):
        response = self.client.post(self.url, body, content_type='application/json',
            HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        self.assertEqual(response['Cache-Control'], 'no-store')
        return response

    def request(self):
        request = RequestFactory().post(self.url, '{}', content_type='application/json',
            HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        request.user, request.request_id = self.user, uuid.uuid4()
        request.session = SessionStore(session_key=self.session_key)
        request.session['active_account_id']  # Cache before any concurrent selection.
        return request

    def assert_no_funds(self):
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'pending_payment')
        self.assertIsNone(self.order.paid_at)
        for name in ('PaymentFact', 'CreditBatch', 'CreditEntry', 'Subscription', 'SubscriptionPeriod'):
            self.assertFalse(apps.get_model('commerce', name).objects.exists())

    def test_http_signs_real_sdk_sandbox_url_from_frozen_order_and_committed_attempt(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        attempt = PaymentAttempt.objects.get(order=self.order)
        self.assertEqual(attempt.intent_key, 'checkout:'+str(self.order.pk))
        self.assertEqual(attempt.state, 'pending')
        self.assertIsNotNone(attempt.dispatched_at)
        self.assertEqual(attempt.expires_at, self.order.created_at+timedelta(seconds=900))
        self.assertEqual(data['payment_attempt']['id'], str(attempt.pk))
        url = urlsplit(data['payment_url'])
        self.assertEqual((url.scheme, url.netloc, url.path),
            ('https', 'openapi.alipaydev.com', '/gateway.do'))
        params = {key: value[0] for key, value in parse_qs(url.query).items()}
        biz = json.loads(params['biz_content'])
        self.assertEqual(biz['out_trade_no'], str(attempt.pk))
        self.assertEqual(biz['total_amount'], '7.31')
        self.assertEqual(biz['product_code'], 'FAST_INSTANT_TRADE_PAY')
        self.assertEqual(biz['time_expire'], timezone.localtime(attempt.expires_at,
            timezone.get_fixed_timezone(480)).strftime('%Y-%m-%d %H:%M:%S'))
        self.assertNotIn('notify_url', params)
        self.assertNotIn('return_url', params)
        self.assertEqual(params['app_id'], '2026000000000001')
        signature = base64.b64decode(params.pop('sign'))
        signed = '&'.join(f'{key}={params[key]}' for key in sorted(params)).encode('utf-8')
        self.private.public_key().verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        self.assert_no_funds()

    def test_duplicate_pending_and_unknown_keep_reference_expiry_and_never_sign_again(self):
        first = self.post()
        self.assertEqual(first.status_code, 200)
        original = list(PaymentAttempt.objects.values())[0]
        with patch.object(payments, 'alipay_sandbox_page_url', side_effect=AssertionError('No second signing')):
            with override_settings(BILLING_ALIPAY_SANDBOX={**self.configuration, 'payment_ttl_seconds': 1800}):
                repeated = self.post()
            self.assertEqual(repeated.status_code, 200)
            self.assertNotIn('payment_url', repeated.json())
            self.assertEqual(repeated.json()['payment_attempt']['status'], 'pending')
            self.assertEqual(list(PaymentAttempt.objects.values())[0], original)
            commerce.record_attempt_event(self.account, original['id'], 'fixture-timeout', 'unknown',
                source='local', evidence_hash='')
            unknown = self.post()
            self.assertEqual(unknown.status_code, 200)
            self.assertNotIn('payment_url', unknown.json())
            self.assertEqual(unknown.json()['payment_attempt']['status'], 'unknown')
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assert_no_funds()

    def test_missing_configuration_fields_disable_without_creating_attempt(self):
        for field in self.configuration:
            with self.subTest(field=field), override_settings(BILLING_ALIPAY_SANDBOX={
                    key: value for key, value in self.configuration.items() if key != field}):
                self.assertEqual(self.post().status_code, 503)
                self.assertFalse(PaymentAttempt.objects.exists())

    def test_configuration_is_strict_and_default_development_production_remain_closed(self):
        configurations = ({}, {**self.configuration, 'enabled': 1},
            {**self.configuration, 'payment_ttl_seconds': True},
            {**self.configuration, 'payment_ttl_seconds': 0},
            {**self.configuration, 'app_id': 'bad'}, {**self.configuration, 'seller_id': ''},
            {**self.configuration, 'merchant_entity': 'another-entity'},
            {**self.configuration, 'private_key': 'invalid'},
            {**self.configuration, 'public_key': 'invalid'},
            {**self.configuration, 'gateway': 'https://example.invalid'},
            {**self.configuration, 'notify_url': 'https://example.invalid'},
            {**self.configuration, 'return_url': 'https://example.invalid'})
        for configuration in configurations:
            with self.subTest(configuration_keys=sorted(configuration)), override_settings(BILLING_ALIPAY_SANDBOX=configuration):
                self.assertEqual(self.post().status_code, 503)
                self.assertFalse(PaymentAttempt.objects.exists())
        for environment in ('development', 'production'):
            with self.subTest(environment=environment), override_settings(ENVIRONMENT=environment):
                self.assertEqual(self.post().status_code, 503)
                self.assertFalse(PaymentAttempt.objects.exists())

    def test_subscription_page_payment_cannot_be_mistaken_for_recurring_authorization(self):
        snapshot = {**self.order.snapshot, 'kind': 'subscription', 'billing_cycle': 'monthly'}
        Order.objects.filter(pk=self.order.pk).update(snapshot=snapshot)
        Quote.objects.filter(pk=self.order.quote_id).update(snapshot=snapshot)
        self.assertEqual(self.post().status_code, 503)
        self.assertFalse(PaymentAttempt.objects.exists())
        self.assert_no_funds()

    def test_tampering_is_rejected_even_when_sandbox_is_enabled(self):
        for field in ('amount_minor', 'currency', 'channel', 'merchant_entity', 'expires_at', 'intent_key'):
            with self.subTest(field=field):
                self.assertEqual(self.post(json.dumps({field: 'untrusted'})).status_code, 422)
                self.assertFalse(PaymentAttempt.objects.exists())

    def test_expired_policy_does_not_create_new_attempt_or_extend_order_deadline(self):
        Order.objects.filter(pk=self.order.pk).update(created_at=timezone.now()-timedelta(minutes=16))
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertFalse(PaymentAttempt.objects.exists())

    def test_signing_failure_preserves_claim_as_unknown_and_next_click_does_not_retry(self):
        with patch.object(payments, 'alipay_sandbox_page_url', side_effect=ValueError('ALIPAY_SIGNING_FAILED')):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'CHANNEL_UNAVAILABLE')
        self.assertTrue(PaymentAttempt.objects.filter(order=self.order).exists())
        attempt = PaymentAttempt.objects.get(order=self.order)
        self.assertEqual(attempt.state, 'unknown')
        self.assertIsNotNone(attempt.dispatched_at)
        with patch.object(payments, 'alipay_sandbox_page_url', side_effect=AssertionError('No retry')):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['payment_attempt']['status'], 'unknown')
        self.assertNotIn('payment_url', response.json())
        self.assert_no_funds()

    def test_sdk_sees_both_committed_stages_and_holds_no_transaction(self):
        original = payments.alipay_sandbox_page_url
        def signing(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            with ThreadPoolExecutor(max_workers=1) as pool:
                def read():
                    close_old_connections()
                    try:
                        attempt = PaymentAttempt.objects.get(order=self.order)
                        return attempt.state, attempt.dispatched_at is not None
                    finally:
                        close_old_connections()
                self.assertEqual(pool.submit(read).result(timeout=10), ('pending', True))
            return original(*args, **kwargs)
        with patch.object(payments, 'alipay_sandbox_page_url', side_effect=signing):
            self.assertEqual(self.post().status_code, 200)

    def test_order_detail_exposes_only_safe_read_only_payment_start_capability(self):
        before = {model._meta.label: list(model.objects.order_by('pk').values())
                  for model in apps.get_app_config('commerce').get_models()}
        response = self.client.get(self.url.removesuffix('/pay'),
            HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get('payment_start'), {'environment': 'SANDBOX',
            'channel': 'alipay', 'account_id': str(self.account.pk),
            'order_id': str(self.order.pk), 'available': True})
        after = {model._meta.label: list(model.objects.order_by('pk').values())
                 for model in apps.get_app_config('commerce').get_models()}
        self.assertEqual(before, after)
        for configuration in ({}, {**self.configuration, 'public_key': 'invalid'}):
            with override_settings(BILLING_ALIPAY_SANDBOX=configuration):
                self.assertNotIn('payment_start', self.client.get(self.url.removesuffix('/pay')).json())
        self.assertEqual(self.post().status_code, 200)
        self.assertNotIn('payment_start', self.client.get(self.url.removesuffix('/pay')).json())

    def test_order_detail_does_not_advertise_expired_payment_policy(self):
        Order.objects.filter(pk=self.order.pk).update(created_at=timezone.now()-timedelta(minutes=16))
        self.assertNotIn('payment_start', self.client.get(self.url.removesuffix('/pay')).json())

    def test_raw_response_rejects_signed_overflow_numbers_at_any_depth(self):
        for content in (b'{"amount":1e999}', b'{"amount":-1e999}',
                b'{"nested":{"values":[1,1e999]}}', b'{"nested":[{"value":-1e999}]}'):
            signature = base64.b64encode(self.private.sign(content, padding.PKCS1v15(), hashes.SHA256()))
            response = b'{"alipay_trade_query_response":'+content+b',"sign":"'+signature+b'"}'
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, '^ALIPAY_RESPONSE_INVALID$'):
                payments.verify_alipay_response(response, self.public_pem, method='alipay.trade.query')
        content = b'{"amount":7.31,"nested":[-1.25,1e2]}'
        signature = base64.b64encode(self.private.sign(content, padding.PKCS1v15(), hashes.SHA256()))
        response = b'{"alipay_trade_query_response":'+content+b',"sign":"'+signature+b'"}'
        self.assertEqual(payments.verify_alipay_response(response, self.public_pem,
            method='alipay.trade.query'), {'amount': 7.31, 'nested': [-1.25, 100.0]})

    def test_signed_payer_currency_does_not_change_cny_order_fact(self):
        from backend.payment_channel_tests import AlipayOfflineProtocolTests
        self.assertEqual(self.post().status_code, 200)
        attempt = PaymentAttempt.objects.get(order=self.order)
        fields = {'out_trade_no': str(attempt.pk), 'seller_id': '2088000000000001',
            'gmt_payment': timezone.localtime(timezone.now(), timezone.get_fixed_timezone(480)).strftime('%Y-%m-%d %H:%M:%S'),
            'notify_type': 'trade_status_sync', 'currency': 'CNY', 'trans_currency': 'CNY', 'pay_currency': 'HKD'}
        body = AlipayOfflineProtocolTests.signed_notification(self, additions=fields)
        with override_settings(BILLING_APPROVED_CHANNELS={'alipay': {**self.configuration,
                'currency': 'CNY', 'regions': ['CN'], 'capabilities': ['one_time']}}):
            fact = payments.alipay_notification_fact(self.account, attempt.pk, body)
        self.assertEqual((fact['amount_minor'], fact['currency']), (731, 'CNY'))
        self.assert_no_funds()

    def _dispatch_race(self, change, expected, *, account_lock=False):
        if connection.vendor != 'postgresql':
            self.skipTest('Dispatch row-lock races require PostgreSQL; SQLite is not evidence.')
        ready, release, waiting = Event(), Event(), Event()
        original = payments.finish_payment
        request = self.request()
        lock_table = 'backend_billingaccount' if account_lock else 'backend_accountsession'
        def finish(request, prepared):
            ready.set()
            if not release.wait(timeout=10):
                raise AssertionError('Revocation transaction did not release dispatch')
            return original(request, prepared)
        def write():
            close_old_connections()
            def signal(execute, sql, params, many, context):
                if ready.is_set() and 'FOR UPDATE' in sql and lock_table in sql:
                    waiting.set()
                return execute(sql, params, many, context)
            try:
                with connection.execute_wrapper(signal):
                    return api(request, self.url.removeprefix('/api/'))
            finally:
                close_old_connections()
        with patch.object(payments, 'finish_payment', side_effect=finish), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(write)
            self.assertTrue(ready.wait(timeout=10), 'Prepare must commit before competing authorization change')
            self.assertEqual(PaymentAttempt.objects.get(order=self.order).state, 'created')
            with transaction.atomic():
                if account_lock:
                    BillingAccount.objects.select_for_update().get(pk=self.account.pk)
                else:
                    AccountSession.objects.select_for_update().get(pk=self.metadata.pk)
                release.set()
                self.assertTrue(waiting.wait(timeout=5), 'Dispatch must reacquire '+lock_table+' lock')
                change()
            response = future.result(timeout=10)
        self.assertEqual(response.status_code, expected)
        self.assertNotIn('payment_url', json.loads(response.content))
        attempt = PaymentAttempt.objects.get(order=self.order)
        self.assertEqual(attempt.state, 'created')
        self.assertIsNone(attempt.dispatched_at)
        self.assert_no_funds()

    def test_pg_revocation_committing_while_dispatch_waits_prevents_dispatch(self):
        def revoke():
            AccountSession.objects.filter(pk=self.metadata.pk).update(revoked_at=timezone.now())
            Session.objects.filter(pk=self.session_key).delete()
        self._dispatch_race(revoke, 401)

    def test_pg_account_switch_committing_while_dispatch_waits_prevents_dispatch(self):
        _, other = fixture_account('sandbox-switched')
        Membership.objects.create(user=self.user, account=other, role='owner')
        def switch():
            session = SessionStore(session_key=self.session_key)
            session['active_account_id'] = str(other.pk)
            session.save()
        self._dispatch_race(switch, 409)

    def test_pg_offboarding_committing_while_dispatch_waits_prevents_dispatch(self):
        BillingAccount.objects.filter(pk=self.account.pk).update(kind='business', personal_owner=None)
        # Explicit isolated fixture shape, never a production product update.
        type(self.product).objects.filter(pk=self.product.pk).update(audience='business')
        def offboard():
            BillingAccount.objects.select_for_update().get(pk=self.account.pk)
            Membership.objects.filter(pk=self.member.pk).update(is_active=False)
        self._dispatch_race(offboard, 403, account_lock=True)

    def test_pg_double_click_dispatches_only_one_committed_attempt(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Dispatch row-lock races require PostgreSQL; SQLite is not evidence.')
        boundary = Barrier(2)
        original = payments.finish_payment
        requests = [self.request(), self.request()]
        def finish(request, prepared):
            boundary.wait(timeout=10)
            return original(request, prepared)
        def write(request):
            close_old_connections()
            try:
                response = api(request, self.url.removeprefix('/api/'))
                return response.status_code, json.loads(response.content)
            finally:
                close_old_connections()
        with patch.object(payments, 'finish_payment', side_effect=finish), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, requests))
        self.assertEqual([status for status, _ in results], [200, 200])
        self.assertEqual(sum('payment_url' in data for _, data in results), 1)
        self.assertEqual(len({data['payment_attempt']['id'] for _, data in results}), 1)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(PaymentAttempt.objects.get().state, 'pending')
        self.assert_no_funds()

@override_settings(ENVIRONMENT='test', BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS={
    'alipay': {'enabled': True, 'currency': 'CNY', 'merchant_entity': 'fixture-shenzhen',
        'regions': ['CN'], 'capabilities': ['one_time']}})
class AlipayCallbackHTTPTests(TransactionTestCase):
    """The exact callback route consumes real synthetic RSA2 form bytes."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_pem = cls.private.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode('ascii')
        cls.public_pem = cls.private.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)

    def setUp(self):
        self.now = datetime(2026, 10, 16, 9, 0, tzinfo=datetime_timezone.utc)
        self.enterContext(patch('django.utils.timezone.now', return_value=self.now))
        self.configuration = {'enabled': True, 'app_id': '2026000000000001',
            'seller_id': '2088000000000001', 'merchant_entity': 'fixture-shenzhen',
            'private_key': self.private_pem, 'public_key': self.public_pem,
            'payment_ttl_seconds': 900}
        self.context = {'channel': 'alipay', 'environment': 'SANDBOX',
            'app_id': '2026000000000001', 'seller_id': '2088000000000001',
            'merchant_entity': 'fixture-shenzhen'}
        self.enterContext(override_settings(BILLING_ALIPAY_SANDBOX=self.configuration))
        self.user, self.account = fixture_account('callback-http-fixture')
        self.product = fixture_product()
        quote = commerce.create_quote(self.account, self.user, self.product.pk, 1,
            self.now+timedelta(minutes=5), channel='alipay', buyer_region='CN')
        order = commerce.create_order(self.account, self.user,
            {'quote_id': quote['id'], 'agreement_acceptance': 'fixture-1'}, 'callback-http-order')
        self.order = Order.objects.get(pk=order['id'])
        prepared = commerce.prepare_payment_attempt(self.account, self.user, self.order.pk,
            'callback-http-intent', expires_at=self.now+timedelta(minutes=10))
        self.attempt = PaymentAttempt.objects.get(pk=prepared['id'])
        with patch('django.utils.timezone.now', return_value=self.now-timedelta(minutes=2)):
            commerce.claim_payment_dispatch(self.account, self.attempt.pk, dispatch_context=self.context)
        self.client = Client(enforce_csrf_checks=True)
        self.url = '/api/payments/webhooks/alipay'
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            self.enterContext(patch(target, side_effect=AssertionError('Callbacks must never call a PSP')))

    def notification(self, **changes):
        from backend.payment_channel_tests import AlipayOfflineProtocolTests
        return AlipayOfflineProtocolTests.signed_notification(self, additions={
            'out_trade_no': str(self.attempt.pk), 'seller_id': self.context['seller_id'],
            'notify_type': 'trade_status_sync', 'gmt_payment': '2026-10-16 16:58:30',
            'trade_no': 'callback_fixture_transaction', **changes})

    def all_rows(self):
        # Inspect every actual persisted column, including tables outside the
        # payment models, without relying on unrelated in-progress ORM fields.
        result = {}
        with connection.cursor() as cursor:
            for table in connection.introspection.table_names(cursor):
                cursor.execute('SELECT * FROM '+connection.ops.quote_name(table))
                result[table] = sorted(cursor.fetchall(), key=repr)
        return result

    def post(self, body=None, *, content_type='application/x-www-form-urlencoded', suffix=''):
        response = self.client.post(self.url+suffix, self.notification() if body is None else body,
            content_type=content_type)
        self.assertEqual(response['Cache-Control'], 'no-store')
        return response

    def reject_unchanged(self, body=None, **kwargs):
        before = self.all_rows()
        response = self.post(body, **kwargs)
        self.assertGreaterEqual(response.status_code, 400)
        self.assertNotEqual(response.content, b'success')
        self.assertEqual(self.all_rows(), before)
        return response

    def signed_query(self):
        content = json.dumps({'code': '10000', 'out_trade_no': str(self.attempt.pk),
            'trade_status': 'TRADE_SUCCESS', 'trade_no': 'callback_fixture_transaction',
            'total_amount': '7.31', 'send_pay_date': '2026-10-16 16:59:00'}, separators=(',', ':')).encode()
        signature = base64.b64encode(self.private.sign(content, padding.PKCS1v15(), hashes.SHA256()))
        return b'{"alipay_trade_query_response":'+content+b',"sign":"'+signature+b'"}'

    def test_exact_callback_accepts_anonymous_signed_form_without_session_or_csrf(self):
        sessions = Session.objects.count()
        with self.assertNoLogs(level='DEBUG'):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'success')
        self.assertTrue(response['Content-Type'].startswith('text/plain'))
        self.assertFalse(response.cookies)
        self.assertEqual(Session.objects.count(), sessions)
        fact = PaymentFact.objects.get()
        self.assertEqual(fact.paid_at, self.now-timedelta(seconds=90))
        self.assertEqual(fact.amount_minor, 731)
        self.assertEqual(CreditBatch.objects.count(), 1)
        self.assertEqual(self.attempt.events.get().source, 'verified_callback')

    def test_duplicate_and_finished_notifications_deliver_one_receipt(self):
        body = self.notification()
        self.assertEqual(self.post(body).content, b'success')
        before = self.all_rows()
        self.assertEqual(self.post(body).content, b'success')
        self.assertEqual(self.all_rows(), before)
        self.assertEqual(self.post(self.notification(trade_status='TRADE_FINISHED')).content, b'success')
        self.assertEqual(PaymentFact.objects.count(), 1)
        self.assertEqual(CreditBatch.objects.count(), 1)
        self.assertEqual(self.attempt.events.count(), 2)

    def test_production_development_missing_or_incomplete_config_never_write(self):
        for environment, config in (('production', self.configuration), ('development', self.configuration),
                ('test', {}), ('test', {**self.configuration, 'enabled': False}),
                ('test', {**self.configuration, 'gateway': 'https://example.invalid'}),
                ('test', {key: value for key, value in self.configuration.items() if key != 'private_key'})):
            with self.subTest(environment=environment, keys=sorted(config)), override_settings(
                    ENVIRONMENT=environment, BILLING_ALIPAY_SANDBOX=config):
                self.reject_unchanged()

    def test_stopping_new_sales_does_not_drop_a_valid_inflight_notification(self):
        with override_settings(BILLING_SALES_ENABLED=False, BILLING_APPROVED_CHANNELS={}):
            self.assertEqual(self.post().content, b'success')
        self.assertEqual(PaymentFact.objects.count(), 1)

    def test_bad_signature_money_identity_time_and_duplicate_fields_leave_all_rows_unchanged(self):
        bodies = [self.notification().replace(b'7.31', b'7.32'), self.notification(total_amount='7.32'),
            self.notification(app_id='1'), self.notification(seller_id='1'), self.notification(currency='USD'),
            self.notification(gmt_payment=None), self.notification(gmt_payment='invalid'),
            self.notification(out_trade_no=str(uuid.uuid4())), self.notification(trade_status='WAIT_BUYER_PAY'),
            self.notification()+b'&total_amount=7.31', self.notification()+b'&out_trade_no=other',
            self.notification()+b'&sign=duplicate', b'{}', b'bad=%XX', b'\xff']
        for body in bodies:
            with self.subTest(length=len(body)):
                self.reject_unchanged(body)

    def test_undispatched_unfrozen_or_rotated_merchant_cannot_receive_money(self):
        for values in ({'dispatched_at': None}, {'dispatch_authorization': None},
                {'dispatch_authorization': {**self.context, 'seller_id': '1'}}):
            PaymentAttempt.objects.filter(pk=self.attempt.pk).update(**values)
            self.reject_unchanged()
            PaymentAttempt.objects.filter(pk=self.attempt.pk).update(
                dispatched_at=self.now-timedelta(minutes=2), dispatch_authorization=self.context)

    def test_signed_other_attempt_cannot_bypass_its_frozen_merchant_identity(self):
        _, other = fixture_account('callback-other-merchant')
        quote = Quote.objects.create(account=other, product=self.product, snapshot=self.order.snapshot,
            amount_minor=731, currency='CNY', expires_at=self.now+timedelta(minutes=5))
        order = Order.objects.create(account=other, created_by=self.user, quote=quote,
            snapshot=self.order.snapshot, amount_minor=731, currency='CNY',
            idempotency_key='callback-other-order', request_hash='d'*64)
        attempt = PaymentAttempt.objects.create(account=other, order=order, intent_key='other-callback-intent',
            expires_at=self.now+timedelta(minutes=5), state='pending', dispatched_at=self.now-timedelta(minutes=2),
            dispatch_authorization={**self.context, 'seller_id': '1'})
        self.reject_unchanged(self.notification(out_trade_no=str(attempt.pk)))

    def test_callback_request_contract_and_exact_csrf_exception(self):
        for content_type in ('application/json', 'text/plain', 'multipart/form-data',
                'application/x-www-form-urlencoded; charset=iso-8859-1'):
            self.reject_unchanged(content_type=content_type)
        self.reject_unchanged(suffix='?account_id=other')
        self.reject_unchanged(b'x'*65537)
        before = self.all_rows()
        for method in ('get', 'put', 'delete', 'options'):
            response = getattr(self.client, method)(self.url)
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response['Allow'], 'POST')
        for path in (f'/api/billing/orders/{self.order.pk}/pay',
                '/api/payments/webhooks/wechat/extra', self.url+'/extra'):
            response = self.client.post(path, '{}', content_type='application/json')
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()['error']['code'], 'CSRF_FAILED')
        with override_settings(BILLING_WECHAT_NATIVE={}):
            response = self.client.post('/api/payments/webhooks/wechat', '{}',
                content_type='application/json')
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()['error']['code'], 'CHANNEL_UNAVAILABLE')
        self.assertEqual(self.all_rows(), before)

    def test_delivery_failure_returns_500_preserves_money_and_retry_repairs(self):
        body = self.notification()
        with self.assertLogs('sage', level='ERROR') as captured, patch(
                'backend.commerce.billing.fulfill_paid_order', side_effect=RuntimeError('synthetic delivery failure')):
            response = self.post(body)
        self.assertRegex(captured.output[0], r'^ERROR:sage:alipay_callback_failed request_id=[0-9a-f-]{36}$')
        self.assertNotIn('synthetic delivery failure', ''.join(captured.output))
        self.assertEqual(len(captured.output), 1)
        self.assertEqual(response.status_code, 500)
        self.assertNotEqual(response.content, b'success')
        self.assertNotIn(b'synthetic delivery failure', response.content)
        self.assertEqual(PaymentFact.objects.count(), 1)
        self.assertFalse(CreditBatch.objects.exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'paid_pending_entitlement')
        self.assertFalse(self.attempt.events.exists())
        self.assertEqual(self.post(body).content, b'success')
        self.assertEqual(PaymentFact.objects.count(), 1)
        self.assertEqual(CreditBatch.objects.count(), 1)

    def test_conflicting_canonical_payment_time_cannot_change_an_existing_receipt(self):
        self.assertEqual(self.post().content, b'success')
        response = self.reject_unchanged(self.notification(gmt_payment='2026-10-16 16:59:00'))
        self.assertEqual(response.status_code, 500)

    def test_query_first_then_http_notification_then_query_preserves_canonical_time(self):
        from backend.alipay_transport_tests import AlipayTransportTests
        with AlipayTransportTests.transport(self, self.signed_query()):
            self.assertEqual(payments.query_alipay_payment(self.account, self.attempt.pk)['status'], 'unknown')
        self.assertFalse(PaymentFact.objects.exists())
        self.assertEqual(self.post().content, b'success')
        with AlipayTransportTests.transport(self, self.signed_query()):
            self.assertEqual(payments.query_alipay_payment(self.account, self.attempt.pk)['status'], 'succeeded')
        self.assertEqual(PaymentFact.objects.get().paid_at, self.now-timedelta(seconds=90))
        self.assertEqual(CreditBatch.objects.count(), 1)

    def test_http_notification_delivery_failure_then_query_repairs_without_recharging(self):
        from backend.alipay_transport_tests import AlipayTransportTests
        with patch('backend.commerce.billing.fulfill_paid_order', side_effect=RuntimeError('synthetic delivery failure')):
            self.assertEqual(self.post().status_code, 500)
        with AlipayTransportTests.transport(self, self.signed_query()):
            self.assertEqual(payments.query_alipay_payment(self.account, self.attempt.pk)['status'], 'succeeded')
        self.assertEqual(self.post().content, b'success')
        self.assertEqual(PaymentFact.objects.count(), 1)
        self.assertEqual(CreditBatch.objects.count(), 1)
