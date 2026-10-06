"""Isolated transaction-state tests. No channel SDK, request or real charge."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from .billing import (create_quote, create_order, prepare_payment_attempt,
    claim_payment_dispatch, record_attempt_event, order_detail, record_confirmed_payment)
from .models import PaymentAttempt, PaymentAttemptEvent, PaymentFact, Order, CreditBatch, BillingNotification
from .tests import FIXTURE_CHANNELS, fixture_account, fixture_product


@override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
class PaymentAttemptTests(TransactionTestCase):
    def setUp(self):
        self.user, self.account = fixture_account()
        self.other_user, self.other = fixture_account('other')
        self.product = fixture_product()
        self.now = timezone.now()
        self.deadline = self.now + timedelta(minutes=15)
        self.sequence = 0

    def order(self):
        self.sequence += 1
        quote = create_quote(self.account, self.user, self.product.pk, 1, self.now+timedelta(minutes=5), channel='fixture', buyer_region='CN')
        return create_order(self.account, self.user, {'quote_id':quote['id'], 'agreement_acceptance':'fixture-1'}, str(self.sequence))['id']

    def prepare(self, order_id=None, key='purchase-fixture'):
        return prepare_payment_attempt(self.account, self.user, order_id or self.order(), key, expires_at=self.deadline)

    def event(self, attempt, key, outcome, source='provider_query'):
        return record_attempt_event(self.account, attempt['id'], key, outcome, source=source,
            evidence_hash='' if source == 'local' else 'c'*64)

    def paid(self, order_id, reference='receipt'):
        return record_confirmed_payment(self.account, order_id, channel='fixture', merchant_reference='fixture-shenzhen',
            transaction_reference=reference, amount_minor=731, currency='CNY', paid_at=timezone.now(), evidence_hash='d'*64)

    def test_failure_notification_requires_applied_fact_and_is_atomic(self):
        attempt = self.prepare()
        claim_payment_dispatch(self.account,attempt['id'])
        self.event(attempt,'timeout','unknown','local')
        self.assertFalse(BillingNotification.objects.exists())
        with self.assertRaisesRegex(RuntimeError,'rollback'):
            with transaction.atomic():
                self.event(attempt,'failed','failed')
                self.assertEqual(BillingNotification.objects.get().type,'payment_failed')
                raise RuntimeError('rollback')
        self.assertFalse(BillingNotification.objects.exists())
        self.event(attempt,'failed','failed')
        self.event(attempt,'failed','failed')
        self.event(attempt,'failed-again','failed')
        self.assertEqual(BillingNotification.objects.count(),1)
        notice = BillingNotification.objects.get()
        self.assertEqual(str(notice.order_id),PaymentAttempt.objects.get().order_id.__str__())
        self.assertIsNone(notice.amount_minor)
        self.assertEqual(notice.currency,'')

    def test_paid_attempt_cannot_emit_stale_failure_notification(self):
        order_id = self.order()
        attempt = self.prepare(order_id)
        claim_payment_dispatch(self.account,attempt['id'])
        self.paid(order_id)
        self.event(attempt,'late-failure','failed')
        self.assertFalse(BillingNotification.objects.filter(type='payment_failed').exists())

    def test_one_attempt_per_intent_order_and_snapshot_replay(self):
        order_id = self.order()
        attempt = self.prepare(order_id)
        self.assertEqual(attempt, self.prepare(order_id))
        self.assertEqual(PaymentAttempt.objects.count(),1)
        for order, key in [(order_id,'changed-intent'), (self.order(),'purchase-fixture')]:
            with self.assertRaisesRegex(ValueError,'PAYMENT_INTENT_CONFLICT'):
                self.prepare(order,key)
        with self.assertRaisesRegex(ValueError,'PAYMENT_INTENT_CONFLICT'):
            prepare_payment_attempt(self.account,self.user,order_id,'purchase-fixture',expires_at=self.deadline+timedelta(seconds=1))

    def test_dispatch_is_durably_claimed_once_unknown_never_retries(self):
        attempt = self.prepare()
        with transaction.atomic():
            with self.assertRaisesRegex(ValueError,'DISPATCH_REQUIRES_AUTOCOMMIT'):
                claim_payment_dispatch(self.account,attempt['id'])
        first = claim_payment_dispatch(self.account,attempt['id'])
        self.assertTrue(first['dispatch'])
        self.assertEqual(first['request_reference'],attempt['id'])
        self.assertEqual(PaymentAttempt.objects.get().state,'pending')
        self.event(attempt,'network-timeout','unknown','local')
        self.assertFalse(claim_payment_dispatch(self.account,attempt['id'])['dispatch'])
        self.assertEqual(Order.objects.get().status,'pending_payment')
        with override_settings(BILLING_SALES_ENABLED=False):
            self.assertEqual(order_detail(self.account,PaymentAttempt.objects.get().order_id)['payment_attempt']['status'],'unknown')
            self.event(attempt,'query-after-shutdown','pending')
        self.assertFalse(claim_payment_dispatch(self.account,attempt['id'])['dispatch'])

    def test_gates_rechecked_before_new_dispatch_not_before_reconciliation(self):
        attempt = self.prepare()
        with override_settings(BILLING_SALES_ENABLED=False):
            with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
                claim_payment_dispatch(self.account,attempt['id'])
        self.assertIsNone(PaymentAttempt.objects.get().dispatched_at)
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            claim_payment_dispatch(self.other,attempt['id'])
        with self.assertRaisesRegex(ValueError,'FORBIDDEN'):
            prepare_payment_attempt(self.account,self.other_user,self.order(),'forbidden',expires_at=self.deadline)

    def test_expiry_before_dispatch_closes_locally_after_dispatch_requires_evidence(self):
        first = self.prepare()
        with patch('django.utils.timezone.now',return_value=self.deadline):
            self.assertFalse(claim_payment_dispatch(self.account,first['id'])['dispatch'])
        self.assertEqual(PaymentAttempt.objects.get(pk=first['id']).state,'expired')
        second = self.prepare(key='second')
        claim_payment_dispatch(self.account,second['id'])
        with patch('django.utils.timezone.now',return_value=self.deadline):
            self.assertFalse(claim_payment_dispatch(self.account,second['id'])['dispatch'])
            with self.assertRaisesRegex(ValueError,'PAYMENT_EVIDENCE_REQUIRED'):
                self.event(second,'local-expiry','expired','local')
        self.assertEqual(PaymentAttempt.objects.get(pk=second['id']).state,'pending')
        self.event(second,'query-close','expired')
        self.assertEqual(Order.objects.get(pk=PaymentAttempt.objects.get(pk=second['id']).order_id).status,'closed')

    def test_success_requires_persisted_money_and_stale_failure_never_unpays(self):
        order_id = self.order(); attempt = self.prepare(order_id)
        claim_payment_dispatch(self.account,attempt['id'])
        with self.assertRaisesRegex(ValueError,'PAYMENT_NOT_CONFIRMED'):
            self.event(attempt,'unproven-success','succeeded')
        self.paid(order_id)
        # Money was persisted even if the caller died before updating the attempt.
        self.assertEqual(order_detail(self.account,order_id)['payment_attempt']['status'],'succeeded')
        result = self.event(attempt,'verified-success','succeeded')
        self.assertEqual(result,self.event(attempt,'verified-success','succeeded'))
        self.event(attempt,'stale-pending','pending')
        self.event(attempt,'conflicting-failure','failed')
        self.assertEqual(PaymentAttempt.objects.get().state,'succeeded')
        self.assertEqual(Order.objects.get().status,'fulfilled')
        self.assertEqual(CreditBatch.objects.count(),1)
        self.assertTrue(PaymentAttempt.objects.get().needs_reconciliation)

    def test_event_idempotency_and_late_money_retained_for_review(self):
        order_id = self.order(); attempt = self.prepare(order_id)
        claim_payment_dispatch(self.account,attempt['id'])
        self.event(attempt,'failed','failed')
        self.event(attempt,'failed','failed')
        self.assertEqual(PaymentAttemptEvent.objects.count(),1)
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
            self.event(attempt,'failed','cancelled')
        self.assertFalse(claim_payment_dispatch(self.account,attempt['id'])['dispatch'])
        self.assertEqual(self.paid(order_id)['disposition'],'late_payment_review')
        self.event(attempt,'late-success','succeeded')
        self.assertEqual(PaymentAttempt.objects.get().state,'review')
        self.assertEqual(CreditBatch.objects.count(),0)

    def test_invalid_event_or_evidence_cannot_mutate_attempt(self):
        attempt = self.prepare()
        for outcome,source,evidence in [('failed','browser','c'*64),('succeeded','provider_query',''),('invalid','provider_query','c'*64)]:
            with self.assertRaises(ValueError):
                record_attempt_event(self.account,attempt['id'],'bad',outcome,source=source,evidence_hash=evidence)
        self.assertEqual(PaymentAttempt.objects.get().state,'created')
        self.assertEqual(PaymentAttemptEvent.objects.count(),0)

    def test_receipt_and_event_evidence_cannot_be_overwritten(self):
        order_id = self.order(); attempt = self.prepare(order_id)
        claim_payment_dispatch(self.account,attempt['id'])
        self.event(attempt,'unknown','unknown','local')
        event = PaymentAttemptEvent.objects.get()
        event.outcome = 'succeeded'
        with self.assertRaisesRegex(ValueError,'IMMUTABLE_BILLING_SNAPSHOT'):
            event.save()
        self.paid(order_id)
        fact = PaymentFact.objects.get()
        fact.amount_minor += 1
        with self.assertRaisesRegex(ValueError,'IMMUTABLE_BILLING_SNAPSHOT'):
            fact.save()


@override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
class PaymentAttemptConcurrencyTests(TransactionTestCase):
    setUp = PaymentAttemptTests.setUp
    order = PaymentAttemptTests.order
    prepare = PaymentAttemptTests.prepare

    def test_parallel_intent_and_dispatch_have_one_winner(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Real row-lock proof requires PostgreSQL')
        order_id = self.order()
        barrier = Barrier(2)
        def prepare():
            close_old_connections()
            try:
                barrier.wait()
                return self.prepare(order_id)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:prepare(),range(2)))
        self.assertEqual(results[0],results[1])
        barrier = Barrier(2)
        def claim():
            close_old_connections()
            try:
                barrier.wait()
                return claim_payment_dispatch(self.account,results[0]['id'])['dispatch']
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(lambda _:claim(),range(2))),[False,True])
        self.assertEqual(PaymentAttempt.objects.count(),1)
