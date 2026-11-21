"""Contract tests: keys added to existing billing/* responses (C-C-1, C-C-10..12, C-C-14)."""
from datetime import timedelta

from django.utils import timezone

from backend.commerce import billing as ledger
from backend.commerce.models import (BillingNotification, Order, PaymentFact, ProductVersion, Quote, Subscription,
                                     SubscriptionPeriod)
from backend.workbench.tests.base import AppTestCase
from backend.workbench.tests.test_billing_fixtures import credits, spend

SNAPSHOT = {'kind': 'credits', 'code': 'credits_5000', 'channel': 'fixture', 'merchant_entity': 'fixture-shenzhen',
            'credits_quantity': '5000', 'unit_amount_minor': 44900, 'currency': 'CNY', 'seat_quantity': 1}


def order(account, user, amount=44900, snapshot=None, key='order-1'):
    product, _ = ProductVersion.objects.get_or_create(
        code='credits_5000', version=1,
        defaults={'kind': 'credits', 'audience': 'personal', 'unit_amount_minor': amount, 'credits_quantity': '5000'})
    quote = Quote.objects.create(account=account, product=product, snapshot=snapshot or SNAPSHOT, amount_minor=amount,
                                 currency='CNY', expires_at=timezone.now() + timedelta(hours=1))
    return Order.objects.create(account=account, created_by=user, quote=quote, idempotency_key=key, request_hash='0' * 64,
                                snapshot=snapshot or SNAPSHOT, amount_minor=amount, currency='CNY')


def pay(account, item, reference='TX0000123456'):
    return ledger._persist_confirmed_payment(
        account, item.pk, channel='fixture', merchant_reference='fixture-shenzhen', transaction_reference=reference,
        amount_minor=item.amount_minor, currency='CNY', paid_at=timezone.now(), evidence_hash='a' * 64)


class OrderKeyTests(AppTestCase):
    def setUp(self):
        self.account = self.login(self.lillian)
        self.order = order(self.account, self.lillian)

    def test_display_number_assigned_on_payment(self):
        self.assertIsNone(self.order.display_number)
        pay(self.account, self.order)
        self.order.refresh_from_db()
        prefix = timezone.localtime(self.order.paid_at).strftime('SG-%Y-%m-')
        self.assertEqual(self.order.display_number, prefix + '0001')
        other = order(self.account, self.lillian, key='order-2')
        pay(self.account, other, reference='TX0000999999')
        other.refresh_from_db()
        self.assertEqual(other.display_number, prefix + '0002')

    def test_display_number_immutable_once_assigned(self):
        pay(self.account, self.order)
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.display_number)
        self.order.display_number = 'SG-1999-01-0001'
        with self.assertRaisesMessage(ValueError, 'IMMUTABLE_BILLING_SNAPSHOT'):
            self.order.save()

    def test_orders_page_keys(self):
        pay(self.account, self.order)
        body = self.ok(self.get('billing/orders'))
        # offset/total are not added: backend.tests pins the exact page key set.
        self.assertEqual(set(body), {'account_id', 'items', 'next_offset'})
        row = body['items'][0]
        for key in ('display_number', 'service_period', 'needs_review', 'invoice_state'):
            self.assertIn(key, row)
        self.assertIsNone(row['service_period'])
        self.assertFalse(row['needs_review'])

    def test_order_detail_keys(self):
        pay(self.account, self.order)
        ledger.fulfill_paid_order(self.account, self.order.pk)
        body = self.ok(self.get('billing/orders/%s' % self.order.pk))
        self.assertTrue(body['order']['display_number'].startswith('SG-'))
        self.assertIsNone(body['order']['discount'])
        self.assertEqual(body['delivery']['credit_batch']['quantity'], '5000')
        payment = body['payments'][0]
        self.assertEqual(payment['channel'], 'fixture')
        self.assertEqual(payment['transaction_reference_masked'], '****3456')
        self.assertIsNotNone(payment['confirmed_at'])
        self.assertEqual(PaymentFact.objects.count(), 1)


class SubscriptionKeyTests(AppTestCase):
    def setUp(self):
        self.account = self.login(self.lillian)
        snapshot = {**SNAPSHOT, 'kind': 'subscription', 'code': 'pro', 'unit_amount_minor': 49900,
                    'billing_cycle': 'monthly'}
        paid = order(self.account, self.lillian, amount=49900, snapshot=snapshot)
        now = timezone.now()
        self.subscription = Subscription.objects.create(
            account=self.account, initial_order=paid, original_anchor=now - timedelta(days=3),
            billing_timezone='Asia/Shanghai', calendar_rule_version='fixture', billing_cycle='monthly', mandate={})
        self.period = SubscriptionPeriod.objects.create(subscription=self.subscription, order=paid, cycle_index=0,
                                                        starts_at=now - timedelta(days=3),
                                                        ends_at=now + timedelta(days=27), snapshot=snapshot)

    def test_unit_amount_and_currency(self):
        item = self.ok(self.get('billing/subscriptions'))['items'][0]
        self.assertEqual((item['unit_amount_minor'], item['currency']), (49900, 'CNY'))

    def test_usage_25_notification_once(self):
        credits(self.account, '1000')
        spend(self.account, 'k-1', '100')
        self.assertFalse(BillingNotification.objects.filter(type='credits_usage_25').exists())
        spend(self.account, 'k-2', '200')
        spend(self.account, 'k-3', '10')
        rows = BillingNotification.objects.filter(type='credits_usage_25')
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows[0].source_event, 'credits_usage:%s:25' % self.period.pk)
