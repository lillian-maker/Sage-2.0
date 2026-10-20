"""Synthetic RSA2 responses at the real requests boundary; no PSP network I/O."""
import base64
import hashlib
import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as datetime_timezone
from threading import Barrier, Event
from time import monotonic, sleep
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import requests
from urllib3.exceptions import ProtocolError
from urllib3.response import HTTPResponse
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from backend import payments
from backend.commerce import billing
from backend.commerce.models import CreditBatch, CreditRefund, Order, PaymentAttempt, PaymentFact, Subscription
from backend.commerce.tests import fixture_account, fixture_product


@override_settings(ENVIRONMENT='test', BILLING_SALES_ENABLED=True,
    BILLING_APPROVED_CHANNELS={'alipay': {'enabled': True, 'currency': 'CNY',
        'merchant_entity': 'fixture-shenzhen', 'regions': ['CN'], 'capabilities': ['one_time']}})
class AlipayTransportTests(TransactionTestCase):
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
        self.enterContext(override_settings(BILLING_ALIPAY_SANDBOX=self.configuration))
        self.context = {'channel': 'alipay', 'environment': 'SANDBOX',
            **{key: self.configuration[key] for key in ('app_id', 'seller_id', 'merchant_entity')}}
        self.user, self.account = fixture_account('alipay-transport-fixture')
        product = fixture_product()
        quote = billing.create_quote(self.account, self.user, product.pk, 1,
            self.now+timedelta(minutes=5), channel='alipay', buyer_region='CN')
        order = billing.create_order(self.account, self.user, {'quote_id': quote['id'],
            'agreement_acceptance': 'fixture-1'}, 'alipay-transport-order')
        self.order = Order.objects.get(pk=order['id'])
        prepared = billing.prepare_payment_attempt(self.account, self.user, self.order.pk,
            'alipay-transport-intent', expires_at=self.now+timedelta(minutes=10))
        self.attempt = PaymentAttempt.objects.get(pk=prepared['id'])
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            self.enterContext(patch(target, side_effect=AssertionError('PSP network is forbidden')))

    def claim(self, *, context=True):
        with patch('django.utils.timezone.now', return_value=self.now-timedelta(minutes=2)):
            kwargs = {'dispatch_context': self.context} if context else {}
            result = billing.claim_payment_dispatch(self.account, self.attempt.pk, **kwargs)
        self.attempt.refresh_from_db()
        return result

    def signed(self, *, operation='query', algorithm=None, **changes):
        fields = {'code': '10000', 'out_trade_no': str(self.attempt.pk),
            'total_amount': '7.31', 'trade_status': 'WAIT_BUYER_PAY'}
        if operation == 'close':
            fields = {'code': '10000', 'out_trade_no': str(self.attempt.pk)}
        fields.update(changes)
        for key in [key for key, value in fields.items() if value is None]:
            fields.pop(key)
        content = json.dumps(fields, separators=(',', ':'), ensure_ascii=False).encode()
        signature = base64.b64encode(self.private.sign(content, padding.PKCS1v15(),
            algorithm or hashes.SHA256()))
        return b'{"alipay_trade_'+operation.encode()+b'_response":'+content+b',"sign":"'+signature+b'"}'

    def transport(self, *bodies, statuses=None):
        self.sent = []
        replies = iter(zip(bodies, statuses or [200]*len(bodies)))

        def send(session, request, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            self.assertFalse(session.trust_env)
            self.assertTrue(kwargs['verify'])
            self.assertFalse(kwargs['allow_redirects'])
            self.assertEqual(kwargs['timeout'], (5, 15))
            self.assertEqual(kwargs['proxies'], {})
            self.assertTrue(kwargs['stream'])
            self.assertEqual(session.get_adapter(request.url).max_retries.total, 0)
            parsed = urlsplit(request.url)
            self.assertEqual((parsed.scheme, parsed.netloc, parsed.path, parsed.query),
                ('https', 'openapi.alipaydev.com', '/gateway.do', ''))
            self.assertEqual(request.method, 'POST')
            self.assertTrue(request.headers['Content-Type'].startswith('application/x-www-form-urlencoded'))
            form = {key: values[0] for key, values in parse_qs(request.body).items()}
            signature = base64.b64decode(form.pop('sign'), validate=True)
            canonical = '&'.join(f'{key}={form[key]}' for key in sorted(form)).encode()
            self.private.public_key().verify(signature, canonical, padding.PKCS1v15(), hashes.SHA256())
            self.assertEqual(form['app_id'], self.context['app_id'])
            self.assertEqual(form['sign_type'], 'RSA2')
            self.assertEqual(form['charset'], 'utf-8')
            self.assertEqual(form['version'], '1.0')
            self.assertEqual(form['format'], 'json')
            self.assertEqual(form['timestamp'], '2026-10-16 17:00:00')
            expected = {'out_trade_no': str(self.attempt.pk)}
            if form['method'] in ('alipay.trade.refund', 'alipay.trade.fastpay.refund.query'):
                expected['out_request_no'] = str(self.refund.pk)
            if form['method'] == 'alipay.trade.refund':
                expected['refund_amount'] = '7.31'
            self.assertEqual(json.loads(form['biz_content']), expected)
            self.assertNotIn('notify_url', form)
            self.assertNotIn('return_url', form)
            self.sent.append(form['method'])
            body, status = next(replies)
            if callable(body):
                body = body(form['method'])
            if isinstance(body, BaseException):
                raise body
            response = requests.Response()
            response.status_code, response.raw = status, io.BytesIO(body)
            response.url = request.url
            return response

        return patch.object(requests.Session, 'send', autospec=True, side_effect=send)

    def query(self):
        return payments.query_alipay_payment(self.account, self.attempt.pk)

    def notification_fact(self, *, reference='fixture_transaction', payment_time='2026-10-16 16:58:30'):
        from backend.payment_channel_tests import AlipayOfflineProtocolTests
        body = AlipayOfflineProtocolTests.signed_notification(self, additions={
            'out_trade_no': str(self.attempt.pk), 'seller_id': self.context['seller_id'],
            'notify_type': 'trade_status_sync', 'gmt_payment': payment_time, 'trade_no': reference})
        approved = {'enabled': True, 'currency': 'CNY', 'app_id': self.context['app_id'],
            'seller_id': self.context['seller_id'], 'merchant_entity': self.context['merchant_entity'],
            'public_key': self.public_pem}
        with override_settings(BILLING_APPROVED_CHANNELS={'alipay': approved}):
            return payments.alipay_notification_fact(self.account, self.attempt.pk, body)

    def assert_no_funds(self):
        self.assertFalse(PaymentFact.objects.exists())
        self.assertFalse(CreditBatch.objects.exists())

    def test_claim_freezes_only_nonsecret_identity_atomically_and_cannot_change_it(self):
        result = self.claim()
        self.assertTrue(result['dispatch'])
        self.assertEqual(self.attempt.dispatch_authorization, self.context)
        self.attempt.dispatch_authorization = {**self.context, 'seller_id': '1'}
        with self.assertRaisesRegex(ValueError, '^IMMUTABLE_BILLING_SNAPSHOT$'):
            self.attempt.save(update_fields=['dispatch_authorization'])
        with patch.object(PaymentAttempt, 'save', side_effect=RuntimeError('synthetic save failure')):
            # A new attempt is unnecessary: reset only this synthetic undispatched fixture.
            PaymentAttempt.objects.filter(pk=self.attempt.pk).update(state='created',
                dispatched_at=None, dispatch_authorization=None)
            with self.assertRaisesRegex(RuntimeError, 'synthetic save failure'):
                self.claim()
        self.attempt.refresh_from_db()
        self.assertIsNone(self.attempt.dispatched_at)
        self.assertIsNone(self.attempt.dispatch_authorization)

    def test_claim_rejects_arbitrary_context_before_dispatch(self):
        for change in ({'channel': 'wechat'}, {'environment': 'production'}, {'app_id': 'invalid'},
                {'seller_id': ''}, {'merchant_entity': 'other'}, {'private_key': 'forbidden'}):
            with self.subTest(fields=list(change)):
                with self.assertRaisesRegex(ValueError, '^INVALID_DISPATCH_CONTEXT$'):
                    billing.claim_payment_dispatch(self.account, self.attempt.pk,
                        dispatch_context={**self.context, **change})
                self.attempt.refresh_from_db()
                self.assertIsNone(self.attempt.dispatched_at)
                self.assertIsNone(self.attempt.dispatch_authorization)

    def test_first_payment_identity_cannot_replace_subscription_authorization(self):
        subscription = Subscription.objects.create(account=self.account, initial_order=self.order,
            original_anchor=self.now, billing_timezone='Asia/Shanghai', calendar_rule_version='fixture-1',
            billing_cycle='monthly', mandate={})
        PaymentAttempt.objects.filter(pk=self.attempt.pk).update(subscription=subscription,
            renewal_cycle_index=1, dispatch_not_before=self.now-timedelta(minutes=1))
        with self.assertRaisesRegex(ValueError, '^INVALID_DISPATCH_CONTEXT$'):
            self.claim()
        self.attempt.refresh_from_db()
        self.assertIsNone(self.attempt.dispatched_at)
        self.assertIsNone(self.attempt.dispatch_authorization)

    def test_postgres_concurrent_dispatch_freezes_one_original_identity(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row locks required')
        barrier = Barrier(2)

        def claim():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return billing.claim_payment_dispatch(self.account, self.attempt.pk,
                    dispatch_context=dict(self.context))['dispatch']
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.dispatch_authorization, self.context)

    def test_postgres_concurrent_close_commits_one_claim_before_transport(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row locks required')
        self.claim()
        barrier = Barrier(2)

        def close():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return payments.close_alipay_payment(self.account, self.attempt.pk)['status']
            finally:
                close_old_connections()

        def response(method):
            return self.signed(operation='close') if method == 'alipay.trade.close' else self.signed(trade_status='TRADE_CLOSED')

        with self.transport(response, response, response), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: close(), range(2)))
        self.assertEqual(self.sent.count('alipay.trade.close'), 1)
        self.assertEqual(self.sent.count('alipay.trade.query'), 2)
        self.assertIn('cancelled', results)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.state, 'cancelled')
        self.assertEqual(self.attempt.events.filter(event_key='sandbox-close-claim').count(), 1)
        self.assert_no_funds()

    def test_real_prepared_form_is_signed_and_pending_queries_never_redispatch(self):
        self.claim()
        with self.transport(self.signed(), self.signed()), self.assertNoLogs(level='DEBUG'):
            self.assertEqual(self.query()['status'], 'pending')
            self.assertEqual(self.query()['status'], 'pending')
        self.assertEqual(self.sent, ['alipay.trade.query']*2)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assert_no_funds()

    def test_query_continues_after_new_sales_are_stopped(self):
        self.claim()
        with override_settings(BILLING_SALES_ENABLED=False, BILLING_APPROVED_CHANNELS={}), self.transport(self.signed()):
            self.assertEqual(self.query()['status'], 'pending')

    def test_missing_rotated_or_invalid_configuration_never_sends(self):
        self.claim()
        for config in ({}, {**self.configuration, 'enabled': False},
                {**self.configuration, 'app_id': '1'}, {**self.configuration, 'seller_id': '1'},
                {**self.configuration, 'merchant_entity': 'other'},
                {**self.configuration, 'gateway': 'https://example.invalid'}):
            with self.subTest(fields=sorted(config)), override_settings(BILLING_ALIPAY_SANDBOX=config), \
                    patch.object(requests.Session, 'send') as send:
                with self.assertRaisesRegex(ValueError, '^CHANNEL_UNAVAILABLE$'):
                    self.query()
                send.assert_not_called()
        for environment in ('production', 'development'):
            with override_settings(ENVIRONMENT=environment), patch.object(requests.Session, 'send') as send:
                with self.assertRaisesRegex(ValueError, '^CHANNEL_UNAVAILABLE$'):
                    self.query()
                send.assert_not_called()
        self.assert_no_funds()

    def test_new_notification_rejects_approved_config_that_differs_from_frozen_identity(self):
        from backend.payment_channel_tests import AlipayOfflineProtocolTests
        self.claim()
        fields = {'out_trade_no': str(self.attempt.pk), 'seller_id': '2088000000000001',
            'notify_type': 'trade_status_sync', 'gmt_payment': '2026-10-16 16:59:00'}
        notification = AlipayOfflineProtocolTests.signed_notification(self, additions=fields)
        approved = {'enabled': True, 'currency': 'CNY', 'app_id': self.context['app_id'],
            'seller_id': self.context['seller_id'], 'merchant_entity': self.context['merchant_entity'],
            'public_key': self.public_pem}
        with override_settings(BILLING_APPROVED_CHANNELS={'alipay': approved}):
            self.assertEqual(payments.alipay_notification_fact(self.account, self.attempt.pk,
                notification)['merchant_reference'], self.context['merchant_entity'])
        for key in ('app_id', 'seller_id'):
            with override_settings(BILLING_APPROVED_CHANNELS={'alipay': {**approved, key: '1'}}):
                with self.assertRaisesRegex(ValueError, '^ALIPAY_PAYMENT_INVALID$'):
                    payments.alipay_notification_fact(self.account, self.attempt.pk, notification)
        self.assert_no_funds()

    def test_legacy_or_undispatched_attempt_cannot_be_retroactively_bound(self):
        for dispatched in (False, True):
            if dispatched:
                self.claim(context=False)
            with patch.object(requests.Session, 'send') as send:
                with self.assertRaisesRegex(ValueError, '^ALIPAY_QUERY_UNAVAILABLE$'):
                    self.query()
                send.assert_not_called()
            self.attempt.refresh_from_db()
            self.assertIsNone(self.attempt.dispatch_authorization)

    def test_cross_account_missing_attempt_and_atomic_call_never_send(self):
        self.claim()
        _, other = fixture_account('alipay-other-account')
        with patch.object(requests.Session, 'send') as send:
            for account, reference in ((other, self.attempt.pk), (self.account, uuid.uuid4())):
                with self.assertRaisesRegex(ValueError, '^NOT_FOUND$'):
                    payments.query_alipay_payment(account, reference)
            with transaction.atomic(), self.assertRaisesRegex(ValueError, '^QUERY_REQUIRES_AUTOCOMMIT$'):
                self.query()
            send.assert_not_called()

    def test_unknown_transport_and_bad_signed_responses_never_become_money(self):
        self.claim()
        cases = [requests.Timeout('SENSITIVE_SYNTHETIC'), b'{}', b'x'*65537,
            self.signed(algorithm=hashes.SHA1()), self.signed(out_trade_no='other'),
            self.signed(total_amount='7.32'), self.signed(total_amount=7.31),
            self.signed(trans_currency='USD'), self.signed(code='40004', sub_code='ACQ.TRADE_NOT_EXIST'),
            self.signed(app_id='1'), self.signed(seller_id='1'),
            self.signed(trade_status='unexpected'), self.signed(trade_status='TRADE_SUCCESS'),
            self.signed(trade_status='TRADE_SUCCESS', trade_no='transaction', send_pay_date='2026-10-16 16:00:00')]
        for body in cases:
            with self.subTest(length=len(body) if isinstance(body, bytes) else 0), self.transport(body), \
                    self.assertNoLogs(level='DEBUG'):
                self.assertEqual(self.query()['status'], 'unknown')
                self.assertEqual(self.sent, ['alipay.trade.query'])
            self.assert_no_funds()
        self.assertEqual(PaymentAttempt.objects.count(), 1)

    def test_http_redirect_and_error_are_unknown_without_following(self):
        self.claim()
        for status in (302, 500):
            with self.transport(self.signed(), statuses=[status]):
                self.assertEqual(self.query()['status'], 'unknown')
                self.assertEqual(self.sent, ['alipay.trade.query'])
        self.assert_no_funds()

    def test_adapter_redirect_is_closed_before_session_reads_body_or_stream_error(self):
        for stream_error in (False, True):
            reads, sends = [], []
            raw = HTTPResponse(body=io.BytesIO(b'RAW_RESPONSE_MUST_NOT_LEAK'*50000), preload_content=False)
            original_read = raw.read
            def read(*args, **kwargs):
                reads.append(1)
                if stream_error:
                    raise ProtocolError('RAW_EXCEPTION_MUST_NOT_LEAK')
                return original_read(*args, **kwargs)
            raw.read = read
            response = requests.Response()
            response.status_code, response.raw = 302, raw
            response.headers['Location'] = 'https://untrusted.example/redirect'
            def send(adapter, prepared, **kwargs):
                sends.append(prepared)
                response.request, response.url = prepared, prepared.url
                self.assertTrue(kwargs['stream'])
                self.assertIs(kwargs['verify'], True)
                self.assertEqual(adapter.max_retries.total, 0)
                return response
            with self.subTest(stream_error=stream_error), \
                    patch.object(requests.adapters.HTTPAdapter, 'send', autospec=True, side_effect=send), \
                    self.assertNoLogs():
                try:
                    with self.assertRaisesRegex(ValueError, '^ALIPAY_QUERY_UNKNOWN$'):
                        payments._alipay_sandbox_call(self.configuration, 'query', self.attempt.pk)
                    self.assertEqual(reads, [], 'Session redirect preparation must never consume the body')
                    self.assertTrue(raw.closed)
                    self.assertEqual(len(sends), 1)
                finally:
                    raw.close()

    def test_transport_oserror_has_fixed_error_without_retry_or_exception_logging(self):
        with self.transport(OSError('RAW_EXCEPTION_MUST_NOT_LEAK')), self.assertNoLogs():
            with self.assertRaisesRegex(ValueError, '^ALIPAY_QUERY_UNKNOWN$'):
                payments._alipay_sandbox_call(self.configuration, 'query', self.attempt.pk)
            self.assertEqual(self.sent, ['alipay.trade.query'])

    def test_query_first_waits_for_notification_then_reuses_canonical_payment_time(self):
        self.claim()
        body = self.signed(trade_status='TRADE_SUCCESS', trade_no='fixture_transaction',
            send_pay_date='2026-10-16 16:59:00')
        with self.transport(body):
            self.assertEqual(self.query()['status'], 'unknown')
        self.assert_no_funds()
        verified = self.notification_fact()
        billing.record_confirmed_payment(self.account, self.order.pk, **verified)
        with self.transport(body, body):
            self.assertEqual(self.query()['status'], 'succeeded')
            self.assertEqual(self.query()['status'], 'succeeded')
        fact = PaymentFact.objects.get()
        self.assertEqual(fact.paid_at, verified['paid_at'])
        self.assertEqual(fact.evidence_hash, verified['evidence_hash'])
        self.assertEqual(PaymentFact.objects.count(), 1)
        self.assertEqual(CreditBatch.objects.count(), 1)
        self.assertEqual(self.sent, ['alipay.trade.query']*2)

    def test_notification_then_query_preserves_different_buyer_payment_time(self):
        self.claim()
        verified = self.notification_fact()
        billing.record_confirmed_payment(self.account, self.order.pk, **verified)
        body = self.signed(trade_status='TRADE_SUCCESS', trade_no='fixture_transaction',
            send_pay_date='2026-10-16 16:59:00')
        with self.transport(body):
            self.assertEqual(self.query()['status'], 'succeeded')
        self.assertEqual(PaymentFact.objects.get().paid_at, verified['paid_at'])
        self.assertEqual(PaymentFact.objects.count(), 1)
        self.assertEqual(CreditBatch.objects.count(), 1)

    def test_notification_delivery_failure_is_repaired_by_query_with_different_seller_payment_time(self):
        self.claim()
        verified = self.notification_fact()
        with patch('backend.commerce.billing.fulfill_paid_order', side_effect=RuntimeError('synthetic delivery failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic delivery failure'):
                billing.record_confirmed_payment(self.account, self.order.pk, **verified)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'paid_pending_entitlement')
        fact = PaymentFact.objects.get()
        self.assertEqual(fact.evidence_hash, verified['evidence_hash'])
        self.assertEqual(fact.amount_minor, 731)
        body = self.signed(trade_status='TRADE_SUCCESS', trade_no='fixture_transaction',
            send_pay_date='2026-10-16 16:59:00')
        with self.transport(body, body):
            self.assertEqual(self.query()['status'], 'succeeded')
            self.assertEqual(self.query()['status'], 'succeeded')
        self.assertEqual(PaymentFact.objects.count(), 1)
        self.assertEqual(CreditBatch.objects.count(), 1)
        self.assertEqual(self.sent, ['alipay.trade.query']*2)

    def test_query_cannot_reuse_conflicting_existing_receipt_order_amount_or_currency(self):
        self.claim()
        _, other_account = fixture_account('conflicting-receipt-account')
        other_order = Order.objects.create(account=other_account, created_by=self.user,
            quote=type(self.order.quote).objects.create(account=other_account, product=self.order.quote.product,
                snapshot=self.order.snapshot, amount_minor=731, currency='CNY', expires_at=self.now+timedelta(minutes=5)),
            snapshot=self.order.snapshot, amount_minor=731, currency='CNY',
            idempotency_key='conflicting-receipt-order', request_hash='c'*64)
        body = self.signed(trade_status='TRADE_SUCCESS', trade_no='fixture_transaction',
            send_pay_date='2026-10-16 16:59:00')
        for changes in ({'order': other_order}, {'amount_minor': 732}, {'currency': 'USD'}):
            values = {'order': self.order, 'channel': 'alipay', 'merchant_reference': self.context['merchant_entity'],
                'transaction_reference': 'fixture_transaction', 'amount_minor': 731, 'currency': 'CNY',
                'paid_at': self.now-timedelta(seconds=90), 'evidence_hash': 'c'*64, 'disposition': 'accepted'}
            existing = PaymentFact.objects.create(**{**values, **changes})
            with self.transport(body), patch('backend.commerce.billing.record_confirmed_payment') as persist:
                self.assertIn(self.query()['status'], ('unknown', 'review'))
                persist.assert_not_called()
            self.assertEqual(PaymentFact.objects.count(), 1)
            self.assertFalse(CreditBatch.objects.exists())
            existing.delete()

    def test_standalone_closed_query_cannot_claim_unpaid_cancellation(self):
        self.claim()
        with self.transport(self.signed(trade_status='TRADE_CLOSED')):
            self.assertEqual(self.query()['status'], 'unknown')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'pending_payment')

    def test_close_requires_signed_acceptance_and_separate_closed_query(self):
        self.claim()
        with self.transport(self.signed(operation='close'), self.signed(trade_status='TRADE_CLOSED')):
            result = payments.close_alipay_payment(self.account, self.attempt.pk)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(self.sent, ['alipay.trade.close', 'alipay.trade.query'])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'closed')
        self.assert_no_funds()

    def test_close_unknown_never_resends_and_always_queries_original_trade(self):
        self.claim()
        with self.transport(requests.Timeout('synthetic close timeout'), self.signed(trade_status='TRADE_CLOSED')):
            self.assertEqual(payments.close_alipay_payment(self.account, self.attempt.pk)['status'], 'unknown')
            self.assertEqual(self.sent, ['alipay.trade.close', 'alipay.trade.query'])
        with self.transport(self.signed(trade_status='TRADE_CLOSED')):
            self.assertEqual(payments.close_alipay_payment(self.account, self.attempt.pk)['status'], 'unknown')
            self.assertEqual(self.sent, ['alipay.trade.query'])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'pending_payment')

    def test_close_mismatched_acceptance_cannot_prove_cancellation(self):
        self.claim()
        with self.transport(self.signed(operation='close', out_trade_no='other'),
                self.signed(trade_status='TRADE_CLOSED')):
            self.assertEqual(payments.close_alipay_payment(self.account, self.attempt.pk)['status'], 'unknown')
        self.assert_no_funds()

    def test_close_accepted_then_query_timeout_can_reconcile_without_reclosing(self):
        self.claim()
        with self.transport(self.signed(operation='close'), requests.Timeout('synthetic query timeout')):
            self.assertEqual(payments.close_alipay_payment(self.account, self.attempt.pk)['status'], 'unknown')
        accepted = self.attempt.events.get(event_key='sandbox-close-accepted')
        self.assertEqual((accepted.source, accepted.outcome), ('provider_close', 'unknown'))
        with self.transport(self.signed(trade_status='TRADE_CLOSED')):
            self.assertEqual(payments.close_alipay_payment(self.account, self.attempt.pk)['status'], 'cancelled')
            self.assertEqual(self.sent, ['alipay.trade.query'])

    def test_close_response_evidence_cannot_directly_set_payment_or_close_terminal(self):
        self.claim()
        for outcome in ('pending', 'succeeded', 'failed', 'cancelled', 'expired'):
            with self.subTest(outcome=outcome), self.assertRaisesRegex(ValueError, '^INVALID_PAYMENT_EVENT$'):
                billing.record_attempt_event(self.account, self.attempt.pk, 'invalid-close-proof', outcome,
                    source='provider_close', evidence_hash='a'*64)
        self.assertFalse(self.attempt.events.exists())
        self.assert_no_funds()

    def test_close_success_followed_by_closed_paid_time_stays_unknown(self):
        self.claim()
        with self.transport(self.signed(operation='close'), self.signed(trade_status='TRADE_CLOSED',
                send_pay_date='2026-10-16 16:59:00')):
            self.assertEqual(payments.close_alipay_payment(self.account, self.attempt.pk)['status'], 'unknown')
        self.assert_no_funds()

    def test_close_paid_race_waits_for_canonical_fact_and_never_cancels_paid_order(self):
        self.claim()
        paid = self.signed(trade_status='TRADE_SUCCESS', trade_no='paid_close_race',
            send_pay_date='2026-10-16 16:59:00')
        with self.transport(self.signed(operation='close'), paid):
            self.assertEqual(payments.close_alipay_payment(self.account, self.attempt.pk)['status'], 'unknown')
        self.assert_no_funds()
        verified = self.notification_fact(reference='paid_close_race')
        billing.record_confirmed_payment(self.account, self.order.pk, **verified)
        with self.transport(paid):
            self.assertEqual(payments.close_alipay_payment(self.account, self.attempt.pk)['status'], 'succeeded')
            self.assertEqual(self.sent, ['alipay.trade.query'])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'fulfilled')
        self.assertEqual(PaymentFact.objects.count(), 1)
        with self.transport(self.signed(trade_status='TRADE_CLOSED', send_pay_date='2026-10-16 16:59:00')):
            self.assertEqual(payments.close_alipay_payment(self.account, self.attempt.pk)['status'], 'succeeded')
            self.assertEqual(self.sent, ['alipay.trade.query'])

    def prepare_refund(self):
        self.claim()
        billing.record_confirmed_payment(self.account, self.order.pk,
            **self.notification_fact(reference='fixture_refund_payment'))
        with self.transport(self.signed(trade_status='TRADE_SUCCESS', trade_no='fixture_refund_payment',
                send_pay_date='2026-10-16 16:59:00')):
            self.query()
        reserved = billing.reserve_credit_refund(self.account, self.order.pk, 'reviewed-fixture-refund',
            review_evidence_hash='b'*64)
        self.refund = CreditRefund.objects.get(pk=reserved['id'])

    def refund_response(self, **changes):
        return self.signed(operation='fastpay_refund_query', trade_status=None,
            **{'out_request_no': str(self.refund.pk), 'trade_no': 'fixture_refund_payment',
                'refund_amount': payments.alipay_amount(self.refund.amount_minor),
                'refund_status': 'REFUND_SUCCESS', **changes})

    def test_refund_query_requires_reviewed_freeze_and_exact_original_refs(self):
        self.prepare_refund()
        invalid = [self.refund_response(refund_status=None), self.refund_response(refund_status='PROCESSING'),
            self.refund_response(code='40004'), self.refund_response(out_request_no='other'),
            self.refund_response(out_trade_no='other'), self.refund_response(trade_no='other'),
            self.refund_response(refund_amount='7.32'), self.refund_response(total_amount='7.32'),
            self.refund_response(trans_currency='USD'), b'{}', requests.Timeout('synthetic refund-query timeout')]
        for body in invalid:
            with self.transport(body):
                result = payments.query_alipay_refund(self.account, self.refund.pk)
                self.assertEqual(result['state'], 'reserved')
                self.assertEqual(result['query_status'], 'unknown')
                self.assertEqual(self.sent, ['alipay.trade.fastpay.refund.query'])
            self.refund.refresh_from_db()
            self.assertEqual(self.refund.state, 'reserved')
            self.assertEqual(self.refund.freeze.state, 'frozen')

    def test_refund_query_confirmed_success_resolves_once_without_refund_submission(self):
        self.prepare_refund()
        body = self.refund_response()
        with self.transport(body):
            result = payments.query_alipay_refund(self.account, self.refund.pk)
        self.assertEqual(result['state'], 'refunded')
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.evidence_hash, hashlib.sha256(body).hexdigest())
        self.assertEqual(self.refund.freeze.state, 'refunded')
        self.assertEqual(self.sent, ['alipay.trade.fastpay.refund.query'])
        with patch.object(requests.Session, 'send') as send:
            self.assertEqual(payments.query_alipay_refund(self.account, self.refund.pk)['state'], 'refunded')
            send.assert_not_called()

    def test_refund_query_cross_account_unreviewed_or_released_is_rejected_offline(self):
        self.prepare_refund()
        _, other = fixture_account('refund-other-account')
        with patch.object(requests.Session, 'send') as send:
            with self.assertRaisesRegex(ValueError, '^NOT_FOUND$'):
                payments.query_alipay_refund(other, self.refund.pk)
            for changes in ({'review_evidence_hash': ''}, {'state': 'released'}):
                CreditRefund.objects.filter(pk=self.refund.pk).update(**changes)
                with self.assertRaisesRegex(ValueError, '^ALIPAY_REFUND_QUERY_UNAVAILABLE$'):
                    payments.query_alipay_refund(self.account, self.refund.pk)
                CreditRefund.objects.filter(pk=self.refund.pk).update(state='reserved', review_evidence_hash='b'*64)
            send.assert_not_called()

    def test_refund_query_review_state_blocks_transport_and_preserves_row(self):
        self.prepare_refund()
        CreditRefund.objects.filter(pk=self.refund.pk).update(review_state='review')
        original = CreditRefund.objects.values().get(pk=self.refund.pk)
        with self.transport(self.refund_response()):
            with self.assertRaisesRegex(ValueError, '^ALIPAY_REFUND_QUERY_UNAVAILABLE$'):
                payments.query_alipay_refund(self.account, self.refund.pk)
            self.assertEqual(self.sent, [])
        self.assertEqual(CreditRefund.objects.values().get(pk=self.refund.pk), original)

    def test_refund_claim_blocks_release_and_keeps_unknown_frozen(self):
        self.prepare_refund()
        billing.record_attempt_event(self.account, self.attempt.pk,
            'sandbox-refund-claim:'+str(self.refund.pk), 'unknown', source='local', evidence_hash='')
        with self.assertRaisesRegex(ValueError, '^REFUND_RECONCILIATION_REQUIRED$'):
            billing.resolve_credit_refund(self.account, self.refund.pk, 'released', evidence_hash='c'*64)
        self.refund.refresh_from_db()
        self.assertEqual((self.refund.state, self.refund.freeze.state), ('reserved', 'frozen'))

    def submit_refund(self):
        submit = getattr(payments, 'submit_alipay_refund', None)
        self.assertTrue(callable(submit), 'A committed sandbox refund dispatcher is required')
        return submit(self.account, self.refund.pk)

    def test_refund_submission_success_requires_query_and_replay_never_resubmits(self):
        self.prepare_refund()
        other_user, other_account = fixture_account('refund-unrelated-payment')
        quote = billing.create_quote(other_account, other_user, self.order.quote.product_id, 1,
            self.now+timedelta(minutes=5), channel='alipay', buyer_region='CN')
        other = billing.create_order(other_account, other_user, {'quote_id': quote['id'],
            'agreement_acceptance': 'fixture-1'}, 'refund-unrelated-order')
        billing.record_confirmed_payment(other_account, other['id'], channel='alipay',
            merchant_reference='fixture-shenzhen', transaction_reference='unrelated-refund-payment',
            amount_minor=731, currency='CNY', paid_at=self.now, evidence_hash='d'*64)
        original = list(PaymentFact.objects.values())
        self.assertEqual(len(original), 2)
        def accepted(method):
            self.refund.refresh_from_db()
            self.assertEqual((self.refund.state, self.refund.freeze.state), ('reserved', 'frozen'))
            self.assertTrue(self.attempt.events.filter(
                event_key='sandbox-refund-claim:'+str(self.refund.pk)).exists())
            return self.signed(operation='refund', trade_status=None, refund_fee='7.31')
        with self.transport(accepted, self.refund_response()):
            self.assertEqual(self.submit_refund()['state'], 'refunded')
            self.assertEqual(self.sent, ['alipay.trade.refund', 'alipay.trade.fastpay.refund.query'])
        with patch.object(requests.Session, 'send') as send:
            self.assertEqual(self.submit_refund()['state'], 'refunded')
            send.assert_not_called()
        self.assertEqual(list(PaymentFact.objects.values()), original)
        self.assertEqual(self.attempt.events.filter(event_key='sandbox-refund-claim:'+str(self.refund.pk)).count(), 1)
        self.assertFalse(self.attempt.events.filter(event_key='sandbox-refund-submit-unknown:'+str(self.refund.pk)).exists())
        self.refund.refresh_from_db()
        self.assertEqual(self.refund.freeze.state, 'refunded')

    def assert_refund_unknown_event(self):
        events = self.attempt.events.filter(event_key='sandbox-refund-submit-unknown:'+str(self.refund.pk))
        self.assertEqual(events.count(), 1, 'Unknown refund submission must leave one durable result event')
        event = events.get()
        self.assertEqual((event.source, event.outcome, event.evidence_hash), ('local', 'unknown', ''))
        recorded = str(list(self.attempt.events.values()))
        for forbidden in ('RAW_EXCEPTION_MUST_NOT_PERSIST', 'RAW_RESPONSE_MUST_NOT_PERSIST', self.private_pem):
            self.assertNotIn(forbidden, recorded)

    def assert_refund_unknown_no_resend(self, failed):
        with self.transport(failed, self.refund_response(refund_status='PROCESSING')):
            self.assertEqual(self.submit_refund()['query_status'], 'unknown')
            self.assertEqual(self.sent, ['alipay.trade.refund', 'alipay.trade.fastpay.refund.query'])
        self.refund.refresh_from_db()
        self.assertEqual((self.refund.state, self.refund.freeze.state), ('reserved', 'frozen'))
        self.assert_refund_unknown_event()
        with self.transport(self.refund_response(refund_status='PROCESSING')):
            self.assertEqual(self.submit_refund()['query_status'], 'unknown')
            self.assertEqual(self.sent, ['alipay.trade.fastpay.refund.query'])
        with self.transport(self.refund_response()):
            self.assertEqual(self.submit_refund()['state'], 'refunded')
            self.assertEqual(self.sent, ['alipay.trade.fastpay.refund.query'])
        self.assert_refund_unknown_event()

    def test_refund_submit_timeout_never_authorizes_resubmission(self):
        self.prepare_refund()
        self.assert_refund_unknown_no_resend(requests.Timeout('RAW_EXCEPTION_MUST_NOT_PERSIST'))

    def test_refund_submit_bad_signature_never_authorizes_resubmission(self):
        self.prepare_refund()
        self.assert_refund_unknown_no_resend(self.signed(operation='refund', algorithm=hashes.SHA1(),
            sub_msg='RAW_RESPONSE_MUST_NOT_PERSIST'))

    def test_refund_crash_after_committed_claim_recovers_by_query_only(self):
        self.prepare_refund()
        with self.transport(SystemExit('synthetic crash after claim')):
            with self.assertRaises(SystemExit):
                self.submit_refund()
        self.assertTrue(self.attempt.events.filter(event_key='sandbox-refund-claim:'+str(self.refund.pk)).exists())
        with self.transport(self.refund_response()):
            self.assertEqual(self.submit_refund()['state'], 'refunded')
            self.assertEqual(self.sent, ['alipay.trade.fastpay.refund.query'])

    def test_refund_signing_failure_after_claim_never_retries_refund(self):
        self.prepare_refund()
        with patch('backend.payments.sign_with_rsa2', side_effect=ValueError('RAW_EXCEPTION_MUST_NOT_PERSIST')), \
                patch.object(requests.Session, 'send') as send:
            self.assertEqual(self.submit_refund()['query_status'], 'unknown')
            send.assert_not_called()
        self.refund.refresh_from_db()
        self.assertEqual((self.refund.state, self.refund.freeze.state), ('reserved', 'frozen'))
        self.assert_refund_unknown_event()
        with self.transport(self.refund_response()):
            self.assertEqual(self.submit_refund()['state'], 'refunded')
            self.assertEqual(self.sent, ['alipay.trade.fastpay.refund.query'])
        self.assert_refund_unknown_event()

    def test_refund_submission_rechecks_review_payment_amount_and_attempt_before_claim(self):
        from backend.commerce.models import CreditFreeze
        self.prepare_refund()
        cases = [(CreditRefund, {'review_state': 'review'}), (CreditRefund, {'amount_minor': 732}),
            (CreditRefund, {'review_evidence_hash': ''}), (CreditRefund, {'state': 'released'}),
            (PaymentFact, {'disposition': 'review'}), (PaymentFact, {'currency': 'USD'}),
            (PaymentFact, {'amount_minor': 732}), (PaymentFact, {'channel': 'wechat'}),
            (PaymentFact, {'merchant_reference': 'other'}), (PaymentAttempt, {'needs_reconciliation': True}),
            (PaymentAttempt, {'state': 'unknown'}), (PaymentAttempt, {'dispatched_at': None}),
            (PaymentAttempt, {'dispatch_authorization': {**self.context, 'seller_id': '1'}}),
            (CreditFreeze, {'state': 'released'}), (CreditFreeze, {'request_key': 'wrong-binding'}),
            (CreditFreeze, {'quantity': '0'}), (CreditBatch, {'source': 'subscription'}),
            (Order, {'status': 'paid_pending_entitlement'})]
        for model, changes in cases:
            row_id = {CreditRefund: self.refund.pk, PaymentFact: self.refund.payment_id, PaymentAttempt: self.attempt.pk,
                CreditFreeze: self.refund.freeze_id, CreditBatch: self.refund.freeze.batch_id, Order: self.order.pk}[model]
            original = model.objects.values(*changes).get(pk=row_id)
            model.objects.filter(pk=row_id).update(**changes)
            with self.subTest(model=model.__name__, fields=list(changes)), patch.object(requests.Session, 'send') as send:
                with self.assertRaises(ValueError):
                    self.submit_refund()
                send.assert_not_called()
                self.assertFalse(self.attempt.events.filter(event_key='sandbox-refund-claim:'+str(self.refund.pk)).exists())
            model.objects.filter(pk=row_id).update(**original)

    def test_refund_submission_configuration_and_outer_transaction_never_send(self):
        self.prepare_refund()
        for values in ({'BILLING_ALIPAY_SANDBOX': {}}, {'ENVIRONMENT': 'production'}, {'ENVIRONMENT': 'development'}):
            with override_settings(**values), patch.object(requests.Session, 'send') as send:
                with self.assertRaises(ValueError):
                    self.submit_refund()
                send.assert_not_called()
        with transaction.atomic(), patch.object(requests.Session, 'send') as send:
            with self.assertRaisesRegex(ValueError, '^QUERY_REQUIRES_AUTOCOMMIT$'):
                self.submit_refund()
            send.assert_not_called()

    def test_refund_submission_checks_other_allocations_against_original_payment(self):
        from backend.commerce.models import CreditFreeze
        self.prepare_refund()
        hold = CreditFreeze.objects.create(batch=self.refund.freeze.batch, request_key='other-refund', quantity='1')
        CreditRefund.objects.create(payment=self.refund.payment, freeze=hold, request_key='other-refund',
            amount_minor=1, review_evidence_hash='d'*64)
        with patch.object(requests.Session, 'send') as send:
            with self.assertRaisesRegex(ValueError, '^ALIPAY_REFUND_QUERY_UNAVAILABLE$'):
                self.submit_refund()
            send.assert_not_called()
        self.assertFalse(self.attempt.events.filter(event_key='sandbox-refund-claim:'+str(self.refund.pk)).exists())
        self.refund.refresh_from_db()
        self.assertEqual((self.refund.state, self.refund.freeze.state), ('reserved', 'frozen'))

    def test_refund_existing_claim_queries_even_when_attempt_requires_reconciliation(self):
        self.prepare_refund()
        billing.record_attempt_event(self.account, self.attempt.pk,
            'sandbox-refund-claim:'+str(self.refund.pk), 'unknown', source='local', evidence_hash='')
        PaymentAttempt.objects.filter(pk=self.attempt.pk).update(state='unknown', needs_reconciliation=True)
        with self.transport(self.refund_response()):
            self.assertEqual(self.submit_refund()['state'], 'refunded')
            self.assertEqual(self.sent, ['alipay.trade.fastpay.refund.query'])

    def test_postgres_concurrent_refund_submissions_dispatch_once_and_query_twice(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row locks required')
        self.prepare_refund()
        barrier = Barrier(2)

        def submit():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return payments.submit_alipay_refund(self.account, self.refund.pk)['state']
            finally:
                close_old_connections()

        def response(method):
            # A query can legitimately precede completion of the claimed refund.
            # Keeping both unknown ensures both callers independently query.
            return self.signed(operation='refund', trade_status=None) if method == 'alipay.trade.refund' else \
                self.refund_response(refund_status='PROCESSING')

        with self.transport(response, response, response), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual(results, ['reserved', 'reserved'])
        self.assertEqual(self.sent.count('alipay.trade.refund'), 1)
        self.assertEqual(self.sent.count('alipay.trade.fastpay.refund.query'), 2)
        self.assertEqual(self.attempt.events.filter(event_key='sandbox-refund-claim:'+str(self.refund.pk)).count(), 1)
        with self.transport(self.refund_response()):
            self.assertEqual(self.submit_refund()['state'], 'refunded')
            self.assertEqual(self.sent, ['alipay.trade.fastpay.refund.query'])

    def assert_postgres_lock_wait(self, ready, worker_pid):
        self.assertTrue(ready.wait(timeout=10))
        deadline = monotonic()+10
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_stat_clear_snapshot()')
                cursor.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s', [worker_pid[0]])
                row = cursor.fetchone()
            if row and row[0] == 'Lock':
                return
            sleep(.01)
        self.fail('Competing operation did not wait on the held account row lock')

    def test_postgres_release_commits_before_claim_blocks_submission_without_io(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row locks required')
        self.prepare_refund()
        ready, worker_pid = Event(), []

        def submit():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    worker_pid.append(cursor.fetchone()[0])
                ready.set()
                try:
                    payments.submit_alipay_refund(self.account, self.refund.pk)
                except ValueError as exc:
                    return str(exc)
            finally:
                close_old_connections()

        with patch.object(requests.Session, 'send') as send, ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                billing._lock_account(self.account)
                future = pool.submit(submit)
                self.assert_postgres_lock_wait(ready, worker_pid)
                billing.resolve_credit_refund(self.account, self.refund.pk, 'released', evidence_hash='d'*64)
            self.assertEqual(future.result(timeout=10), 'ALIPAY_REFUND_QUERY_UNAVAILABLE')
            send.assert_not_called()
        self.refund.refresh_from_db()
        self.assertEqual((self.refund.state, self.refund.freeze.state), ('released', 'released'))
        self.assertFalse(self.attempt.events.filter(event_key='sandbox-refund-claim:'+str(self.refund.pk)).exists())

    def test_postgres_claim_commits_before_release_preserves_freeze(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row locks required')
        self.prepare_refund()
        ready, worker_pid = Event(), []

        def release():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    worker_pid.append(cursor.fetchone()[0])
                ready.set()
                try:
                    billing.resolve_credit_refund(self.account, self.refund.pk, 'released', evidence_hash='d'*64)
                except ValueError as exc:
                    return str(exc)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                billing._lock_account(self.account)
                billing.record_attempt_event(self.account, self.attempt.pk,
                    'sandbox-refund-claim:'+str(self.refund.pk), 'unknown', source='local', evidence_hash='')
                future = pool.submit(release)
                self.assert_postgres_lock_wait(ready, worker_pid)
            self.assertEqual(future.result(timeout=10), 'REFUND_RECONCILIATION_REQUIRED')
        self.refund.refresh_from_db()
        self.assertEqual((self.refund.state, self.refund.freeze.state), ('reserved', 'frozen'))
