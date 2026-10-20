"""Explicit isolated fixtures, not real prices, mandates or payments."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from backend.models import AuditEvent, Membership
from backend.billing_rules import month_boundary
from .billing import create_quote, create_order, record_confirmed_payment, credits_summary, grant_credits
from .models import Subscription, SubscriptionPeriod, CreditBatch, CreditEntry, Order, PaymentFact, ProductVersion, PaymentAttempt
from .tests import fixture_account, FIXTURE_CHANNELS
from .subscriptions import (activate_subscription, append_paid_period, grant_due_credits,
                            cancel_subscription, confirm_cancellation, list_subscriptions)


def sent_renewal_notice_fixture(account, attempt_id):
    """Explicit synthetic send acknowledgement, never a provider or a mock gate."""
    from backend.models import NotificationDelivery
    from .models import BillingNotification
    from backend.notifications import prepare_renewal_notice
    attempt=PaymentAttempt.objects.select_related('subscription').get(account=account,pk=attempt_id)
    if not attempt.subscription_id:raise AssertionError('Renewal fixture requires a renewal intent')
    notice=BillingNotification.objects.filter(account=account,
        source_event=f'renewal:{attempt.subscription_id}:{attempt.renewal_cycle_index}:reminder').first()
    if not notice:
        notice=prepare_renewal_notice(account,attempt.subscription_id,attempt.renewal_cycle_index,
                                     charge_at=attempt.dispatch_not_before)
    for member in Membership.objects.filter(account=account,is_active=True,user__is_active=True,
            role__in=['owner','billing_admin']):
        NotificationDelivery.objects.get_or_create(notification=notice,user=member.user,channel='email',defaults={
            'state':'sent','provider_reference':f'fixture-renewal-send:{attempt.pk}:{member.user_id}',
            'confirmed_at':notice.occurred_at})


@override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS,
    BILLING_TERMS_DOCUMENTS={'fixture-1':{'approved':True,
        'zh':{'title':'测试','paragraphs':['仅供本地测试']},
        'en':{'title':'Fixture','paragraphs':['Local test only']}}})
class SeatPurchaseTests(TransactionTestCase):
    """Synthetic funds and internal delivery only; public extra-seat sales stay closed."""
    def setUp(self):
        SubscriptionTests.setUp(self)
        from backend.models import BillingAccount
        self.account = BillingAccount.objects.create(kind='business', name='Seat purchase fixture')
        self.member = Membership.objects.create(account=self.account, user=self.user, role='owner')

    def source(self, cycle='annual', mode='seat_included', seats=1, credits='100', price=120000):
        self.product = ProductVersion.objects.create(code='seat-original', version=1, kind='subscription',
            audience='business', billing_cycle=cycle, minimum_seats=10 if mode == 'shared_included' else 1,
            unit_amount_minor=price, credits_quantity=credits,
            entitlements={'resource_mode':mode, 'upgrade_rank':1}, entitlement_version='fixture-1',
            credit_rule_version='fixture-1', terms_version='fixture-1', state='published',
            effective_at=self.anchor-timedelta(days=1))
        order = self.base_order(seats)
        sub = activate_subscription(self.account, order['id'], activated_at=self.anchor,
            billing_timezone='Asia/Shanghai', calendar_rule_version='fixture-calendar-v1', mandate=self.mandate)
        self.subscription = Subscription.objects.get(pk=sub['id'])
        return self.subscription

    def base_order(self, seats, key='base'):
        quote = create_quote(self.account, self.user, self.product.pk, seats,
            timezone.now()+timedelta(minutes=5), channel='fixture', buyer_region='CN')
        order = create_order(self.account, self.user, {'quote_id':quote['id'], 'agreement_acceptance':'fixture-1'}, key)
        self.receipt(order['id'], int(order['amount_minor']), key)
        return order

    def receipt(self, order_id, amount, key='extra', paid_at=None):
        return record_confirmed_payment(self.account, order_id, channel='fixture',
            merchant_reference='fixture-shenzhen', transaction_reference=key, amount_minor=amount,
            currency='CNY', paid_at=paid_at or timezone.now(), evidence_hash='b'*64)

    def quote(self, quantity=1, key='extra-quote'):
        from .subscriptions import seat_purchase_quote
        return seat_purchase_quote(self.account, self.user, self.subscription.pk, quantity, request_key=key)

    def confirm(self, quote, key='extra-order'):
        from .subscriptions import confirm_seat_purchase
        return confirm_seat_purchase(self.account, self.user, self.subscription.pk, quote['id'],
            agreement_acceptance='fixture-1', request_key=key)

    def purchase(self, quantity=1):
        row = self.confirm(self.quote(quantity))
        self.receipt(row['order_id'], int(row['amount_minor']))
        from .models import SeatPurchaseChange
        return SeatPurchaseChange.objects.get(pk=row['id'])

    def _assert_cross_mode_rejected(self, mode, other):
        from .subscriptions import subscription_change_options, subscription_change_quote
        self.source(mode=mode, seats=10)
        target = ProductVersion.objects.create(code='cross-'+mode, version=1, kind='subscription',
            audience='business', billing_cycle='annual', minimum_seats=10 if other == 'shared_included' else 1, unit_amount_minor=240000,
            credits_quantity='100', entitlements={'resource_mode':other, 'upgrade_rank':2},
            entitlement_version='fixture-1', credit_rule_version='fixture-1', terms_version='fixture-1',
            state='published', effective_at=self.anchor-timedelta(days=1))
        options = subscription_change_options(self.account, self.user, self.subscription.pk)
        self.assertNotIn(str(target.pk), [row['product_id'] for row in options['items']])
        with self.assertRaisesRegex(ValueError, 'BUSINESS_PLAN_SWITCH_NOT_SUPPORTED'):
            subscription_change_quote(self.account, self.user, self.subscription.pk, target.pk, request_key='cross-'+mode)

    def test_teams_to_enterprise_is_not_offered_or_quoted(self):
        self._assert_cross_mode_rejected('seat_included', 'shared_included')

    def test_enterprise_to_teams_is_not_offered_or_quoted(self):
        self._assert_cross_mode_rejected('shared_included', 'seat_included')

    def test_historical_cross_mode_quote_cannot_confirm_or_activate_paid_order(self):
        from .subscriptions import subscription_change_quote, confirm_subscription_change, activate_subscription_change
        from .models import SubscriptionChange
        self.source(seats=10)
        target = ProductVersion.objects.create(code='cross-historical', version=1, kind='subscription',
            audience='business', billing_cycle='annual', minimum_seats=10, unit_amount_minor=240000,
            credits_quantity='100', entitlements={'resource_mode':'shared_included', 'upgrade_rank':2},
            entitlement_version='fixture-1', credit_rule_version='fixture-1', terms_version='fixture-1',
            state='published', effective_at=self.anchor-timedelta(days=1))
        with patch('backend.commerce.subscriptions._business_plan_switch_allowed', return_value=True):
            quote = subscription_change_quote(self.account, self.user, self.subscription.pk, target.pk, request_key='old-cross')
        with self.assertRaisesRegex(ValueError, 'BUSINESS_PLAN_SWITCH_NOT_SUPPORTED'):
            confirm_subscription_change(self.account, self.user, self.subscription.pk, quote['id'],
                agreement_acceptance='fixture-1', request_key='old-cross-order')
        self.assertIsNone(SubscriptionChange.objects.get(pk=quote['id']).order_id)
        with patch('backend.commerce.subscriptions._business_plan_switch_allowed', return_value=True):
            order = confirm_subscription_change(self.account, self.user, self.subscription.pk, quote['id'],
                agreement_acceptance='fixture-1', request_key='old-cross-order')
        self.receipt(order['order_id'], int(order['due_minor']), 'historical-cross-paid')
        with self.assertRaisesRegex(ValueError, 'BUSINESS_PLAN_SWITCH_NOT_SUPPORTED'):
            activate_subscription_change(self.account, quote['id'], activated_at=self.anchor,
                mandate={**self.mandate, 'agreement_reference':'cross-mandate'}, old_authorization_evidence_hash='c'*64)
        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.superseded_at)
        self.assertEqual(PaymentFact.objects.filter(order_id=order['order_id'], disposition='accepted').count(), 1)

    def test_payment_grants_once_and_preserves_original_order_and_period(self):
        from .subscriptions import fulfill_seat_purchase, organization_summary
        from .models import PaidSeat, SeatPurchaseChange, BillingNotification
        sub = self.source(cycle='monthly')
        original = list(SubscriptionPeriod.objects.values('id', 'snapshot', 'starts_at', 'ends_at'))
        original_order = dict(sub.initial_order.snapshot)
        self.now.return_value = self.anchor+(month_boundary(self.anchor, 1)-self.anchor)/2
        quote = self.quote(2)
        self.assertEqual((quote['amount_minor'], quote['first_month_credits_per_seat'],
            quote['first_month_credits_total']), ('120000', '50', '100'))
        self.assertEqual(self.quote(2), quote)
        order = self.confirm(quote)
        self.assertEqual(self.confirm(quote), order)
        self.assertEqual(PaidSeat.objects.count(), 1)
        self.receipt(order['order_id'], 120000)
        change = SeatPurchaseChange.objects.get(pk=quote['id'])
        first = fulfill_seat_purchase(self.account, change.pk)
        self.assertEqual(first, fulfill_seat_purchase(self.account, change.pk))
        self.assertEqual((first['status'], first['credits_estimated']), ('activated', False))
        self.assertEqual(PaidSeat.objects.count(), 3)
        self.assertEqual(list(CreditBatch.objects.filter(seat__purchase_change=change).values_list('initial_quantity', flat=True)), ['50', '50'])
        self.assertEqual(organization_summary(self.account, admin_actor=self.user)['seat_count'], 3)
        self.assertEqual(BillingNotification.objects.filter(type='seats_purchased').count(), 1)
        self.assertEqual(original, list(SubscriptionPeriod.objects.values('id', 'snapshot', 'starts_at', 'ends_at')))
        sub.initial_order.refresh_from_db(); self.assertEqual(sub.initial_order.snapshot, original_order)

    def test_cross_month_delayed_payment_uses_actual_month_and_rounds_per_seat(self):
        from .models import SeatPurchaseChange
        self.source(mode='shared_included', seats=10, credits='1')
        end = month_boundary(self.anchor, 1)
        self.now.return_value = end-timedelta(seconds=5)
        row = self.confirm(self.quote(3))
        self.assertEqual(datetime.fromisoformat(row['expires_at']), end)
        self.now.return_value = end+(month_boundary(self.anchor, 2)-end)/3
        self.receipt(row['order_id'], int(row['amount_minor']))
        change = SeatPurchaseChange.objects.get(pk=row['id'])
        extra = CreditBatch.objects.filter(source_key__startswith='seat-purchase:')
        self.assertEqual(list(extra.values_list('initial_quantity', 'service_month', 'seat_id')), [('2.01', 1, None)])
        self.assertEqual(change.granted_months, 1)
        self.now.return_value = month_boundary(self.anchor, 2)
        grant_due_credits(self.account); grant_due_credits(self.account)
        self.assertEqual(list(extra.order_by('service_month').values_list('initial_quantity', flat=True)), ['2.01', '3'])

    def test_late_funds_are_retained_for_reconciliation_without_seats(self):
        from .models import SeatPurchaseChange, PaidSeat, BillingIssue
        self.source(cycle='monthly')
        end = month_boundary(self.anchor, 1)
        self.now.return_value = end-timedelta(minutes=1)
        row = self.confirm(self.quote())
        self.now.return_value = end
        self.receipt(row['order_id'], int(row['amount_minor']))
        change = SeatPurchaseChange.objects.get(pk=row['id'])
        self.assertEqual(change.state, 'payment_review')
        self.assertEqual(PaidSeat.objects.count(), 1)
        self.assertTrue(PaymentFact.objects.filter(order_id=row['order_id'], disposition='accepted').exists())
        self.assertTrue(BillingIssue.objects.filter(code='SEAT_PURCHASE_PAYMENT_REVIEW', object_id=change.pk).exists())
        self.assertEqual(SubscriptionPeriod.objects.count(), 1)

    def test_unknown_payment_keeps_original_order_and_blocks_another_purchase(self):
        from .billing import prepare_payment_attempt, claim_payment_dispatch
        self.source(); row = self.confirm(self.quote())
        attempt = prepare_payment_attempt(self.account, self.user, row['order_id'], 'extra-payment',
            expires_at=datetime.fromisoformat(row['expires_at']))
        claim_payment_dispatch(self.account, attempt['id'])
        self.now.return_value += timedelta(minutes=11)
        with self.assertRaisesMessage(ValueError, 'PAYMENT_RECONCILIATION_REQUIRED'):
            self.quote(key='second-purchase')
        self.assertEqual(self.confirm(self.quote())['order_id'], row['order_id'])
        self.assertEqual(Order.objects.count(), 2)

    def test_expired_undispatched_order_can_be_closed_without_new_money(self):
        self.source(); first = self.confirm(self.quote())
        self.now.return_value += timedelta(minutes=10)
        second = self.quote(key='refreshed')
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(Order.objects.get(pk=first['order_id']).status, 'closed')
        self.assertEqual(self.confirm(first)['status'], 'expired')

    def test_quote_cannot_be_confirmed_after_boundary_or_via_generic_order(self):
        from .models import SeatPurchaseChange
        self.source(); row = self.quote()
        change = SeatPurchaseChange.objects.get(pk=row['id'])
        with self.assertRaisesMessage(ValueError, 'INVALID_REQUEST'):
            create_order(self.account, self.user, {'quote_id':str(change.quote_id), 'agreement_acceptance':'fixture-1'}, 'bypass')
        self.now.return_value += timedelta(minutes=10)
        with self.assertRaisesMessage(ValueError, 'QUOTE_EXPIRED'):
            self.confirm(row)

    def test_next_unpaid_period_freezes_new_capacity_and_delivers_all_seats(self):
        from .subscriptions import prepare_renewal_attempt, _renewal_snapshot, fulfill_renewal_attempt
        from .billing import claim_payment_dispatch
        self.source(cycle='monthly'); change = self.purchase(2)
        end = month_boundary(self.anchor, 1)
        snapshot = _renewal_snapshot(self.subscription)
        self.assertEqual((snapshot['seat_quantity'], snapshot['amount_minor']), (3, 360000))
        self.assertEqual(set(snapshot['seat_ids']), set(str(pk) for pk in self.subscription.seats.values_list('pk', flat=True)))
        attempt = prepare_renewal_attempt(self.account, self.subscription.pk, cycle_index=1,
            dispatch_not_before=end-timedelta(days=1), expires_at=end)
        order = PaymentAttempt.objects.get(pk=attempt['id']).order
        self.assertEqual(order.snapshot, snapshot)
        self.assertEqual(change.period.ends_at, end)
        sent_renewal_notice_fixture(self.account, attempt['id'])
        self.now.return_value = end-timedelta(days=1)
        with override_settings(BILLING_RENEWAL_CHARGING_ENABLED=True):
            self.assertTrue(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        self.receipt(order.pk, order.amount_minor, 'renewal')
        fulfill_renewal_attempt(self.account, attempt['id'])
        future = self.subscription.periods.get(cycle_index=1)
        self.assertEqual(future.snapshot, snapshot)
        self.now.return_value = end
        grant_due_credits(self.account); grant_due_credits(self.account)
        self.assertEqual(CreditBatch.objects.filter(subscription_period=future).count(), 3)

    def test_prepaid_lower_capacity_pauses_only_extra_seat_and_keeps_member(self):
        from .subscriptions import assign_seat, organization_summary, record_expired_notifications
        from .billing import _usable_business_seats, start_shared_task, consume_shared_task
        from .models import PaidSeat, BillingNotification
        self.source(cycle='monthly')
        future = self.base_order(1, 'future')
        append_paid_period(self.account, self.subscription.pk, future['id'], cycle_index=1)
        self.now.return_value += timedelta(days=1)
        change = self.purchase()
        original = PaidSeat.objects.get(purchase_change=None)
        extra = change.seats.get()
        other = Membership.objects.create(account=self.account, user=self.other_user, role='member')
        assign_seat(self.account, self.user, original.pk, self.member.pk, expected_version=0, request_key='owner-seat')
        assign_seat(self.account, self.user, extra.pk, other.pk, expected_version=0, request_key='extra-seat')
        old_task = start_shared_task(self.account, other.pk, 'extra-task', 'fixture')
        grant_credits(self.account, 'purchased', 'company-points', '8', month_boundary(self.anchor, 3))
        grant_credits(self.other, 'purchased', 'private-points', '9', month_boundary(self.anchor, 3))
        record_expired_notifications(self.account)
        self.assertFalse(BillingNotification.objects.filter(type='seat_capacity_ending').exists())
        self.now.return_value = month_boundary(self.anchor, 1)-timedelta(days=1)
        record_expired_notifications(self.account); record_expired_notifications(self.account)
        self.assertEqual(BillingNotification.objects.filter(type='seat_capacity_ending').count(), 1)
        self.now.return_value = month_boundary(self.anchor, 1)
        grant_due_credits(self.account)
        self.assertFalse(_usable_business_seats(self.account, other.pk, timezone.now()).exists())
        self.assertTrue(_usable_business_seats(self.account, self.member.pk, timezone.now()).exists())
        self.assertEqual(organization_summary(self.account, admin_actor=self.user)['seat_count'], 1)
        other.refresh_from_db(); self.assertTrue(other.is_active)
        result = consume_shared_task(self.account, old_task['id'], 'after-expiry', '1')
        self.assertFalse(result['accepted'])
        self.assertEqual(credits_summary(self.other)['available'], '9')
        self.assertEqual(PaidSeat.objects.count(), 2)

    def test_natural_expiry_keeps_extra_seat_access_to_unexpired_shared_points(self):
        from .subscriptions import assign_seat
        from .billing import _usable_business_seats
        self.source(cycle='monthly'); change = self.purchase()
        seat = change.seats.get()
        assign_seat(self.account, self.user, seat.pk, self.member.pk, expected_version=0, request_key='seat')
        self.now.return_value = month_boundary(self.anchor, 1)
        self.assertTrue(_usable_business_seats(self.account, self.member.pk, timezone.now()).exists())

    def test_annual_extra_refund_uses_own_seconds_and_preserves_original_sources(self):
        from .refunds import service_refund_assessment, request_service_refund, record_service_refund_receipt
        from .models import ServiceRefund
        self.anchor = datetime(2026, 1, 1, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.now.return_value = self.anchor
        self.source()
        self.now.return_value = datetime(2026, 8, 1, tzinfo=self.anchor.tzinfo)
        change = self.purchase()
        self.assertEqual(change.order.amount_minor, 50301)
        self.now.return_value = datetime(2026, 8, 15, tzinfo=self.anchor.tzinfo)
        assessment = service_refund_assessment(self.account, self.user, self.subscription.pk)
        self.assertTrue(assessment['eligible'], assessment)
        self.assertTrue(assessment['automatic_review'])
        # Base annual order still refunds 4/12; the August extra purchase refunds 122/153.
        self.assertEqual({item['order_id']:item['amount_minor'] for item in assessment['items']},
            {str(self.subscription.initial_order_id):'40000', str(change.order_id):'40109'})
        self.assertEqual(assessment['amount_minor'], '80109')
        result = request_service_refund(self.account, self.user, self.subscription.pk, 'refund')
        self.assertEqual(request_service_refund(self.account, self.user, self.subscription.pk, 'refund'), result)
        with self.assertRaisesMessage(ValueError, 'REFUND_ALREADY_REQUESTED'):
            request_service_refund(self.account, self.user, self.subscription.pk, 'second-refund')
        refund = ServiceRefund.objects.get(pk=result['id'])
        self.assertEqual(refund.pending_from, datetime(2026, 9, 1, tzinfo=self.anchor.tzinfo))
        for index, allocation in enumerate(refund.allocations):
            receipt = dict(event_key=f'paid-back-{index}', outcome='refunded', amount_minor=allocation['amount_minor'],
                provider_reference=f'fixture-return-{index}', evidence_hash='d'*64)
            confirmed = record_service_refund_receipt(self.account, refund.pk, allocation['payment_id'], **receipt)
            self.assertEqual(record_service_refund_receipt(self.account, refund.pk, allocation['payment_id'], **receipt), confirmed)
        refund.refresh_from_db(); self.assertEqual(refund.status, 'refunded')
        self.assertEqual(refund.receipts.count(), 2)
        self.now.return_value = refund.pending_from
        grant_due_credits(self.account)
        self.assertFalse(CreditBatch.objects.filter(subscription_period=change.period, service_month__gte=8).exists())

    def test_annual_extra_refund_rounds_total_once_for_different_purchase_dates(self):
        from .refunds import service_refund_assessment
        self.anchor = datetime(2026, 1, 1, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.now.return_value = self.anchor
        self.source()
        self.now.return_value = datetime(2026, 8, 1, tzinfo=self.anchor.tzinfo)
        first = self.purchase()
        self.now.return_value = datetime(2026, 8, 5, tzinfo=self.anchor.tzinfo)
        second = self.confirm(self.quote(key='second-quote'), key='second-order')
        self.receipt(second['order_id'], int(second['amount_minor']), 'second-payment')
        self.assertEqual((first.order.amount_minor, second['amount_minor']), (50301, '48986'))
        self.now.return_value = datetime(2026, 8, 15, tzinfo=self.anchor.tzinfo)
        assessment = service_refund_assessment(self.account, self.user, self.subscription.pk)
        self.assertTrue(assessment['eligible'], assessment)
        # Rounding each extra source first gives 80218; their exact sum rounds to 80219.
        self.assertEqual(assessment['amount_minor'], '120219')
        self.assertEqual(len(assessment['items']), 3)
        self.assertEqual(sum(int(item['amount_minor']) for item in assessment['items']), 120219)

    def test_annual_extra_refund_uses_actual_payment_second_not_quote_time(self):
        from .refunds import service_refund_assessment
        self.anchor = datetime(2026, 1, 1, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.now.return_value = self.anchor
        self.source()
        self.now.return_value = datetime(2026, 8, 1, tzinfo=self.anchor.tzinfo)
        order = self.confirm(self.quote())
        self.now.return_value = datetime(2026, 8, 2, 12, 34, 56, tzinfo=self.anchor.tzinfo)
        self.receipt(order['order_id'], int(order['amount_minor']))
        self.now.return_value = datetime(2026, 8, 15, tzinfo=self.anchor.tzinfo)
        assessment = service_refund_assessment(self.account, self.user, self.subscription.pk)
        self.assertTrue(assessment['eligible'], assessment)
        self.assertEqual(next(item['amount_minor'] for item in assessment['items'] if item['order_id']==order['order_id']), '40513')

    def test_annual_extra_refund_keeps_prepaid_next_year_at_full_actual_price(self):
        from .refunds import service_refund_assessment
        self.anchor = datetime(2026, 1, 1, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.now.return_value = self.anchor
        self.source()
        next_order = self.base_order(1, 'next-year')
        append_paid_period(self.account, self.subscription.pk, next_order['id'], cycle_index=1)
        self.now.return_value = datetime(2026, 8, 1, tzinfo=self.anchor.tzinfo)
        change = self.purchase()
        self.now.return_value = datetime(2026, 8, 15, tzinfo=self.anchor.tzinfo)
        assessment = service_refund_assessment(self.account, self.user, self.subscription.pk)
        self.assertTrue(assessment['eligible'], assessment)
        self.assertEqual(assessment['amount_minor'], '200109')
        self.assertEqual({item['order_id']:item['amount_minor'] for item in assessment['items']},
            {str(self.subscription.initial_order_id):'40000', str(change.order_id):'40109', next_order['id']:'120000'})

    def test_issued_invoice_for_extra_purchase_requires_existing_manual_review(self):
        from backend.models import InvoiceRequest
        from .refunds import service_refund_assessment, request_service_refund
        self.source()
        change = self.purchase()
        InvoiceRequest.objects.create(account=self.account, order=change.order, created_by=self.user,
            recipient_email='fixture@example.invalid', title='Fixture', status='issued',
            request_key='extra-invoice', request_hash='c'*64)
        assessment = service_refund_assessment(self.account, self.user, self.subscription.pk)
        self.assertTrue(assessment['eligible'], assessment)
        self.assertFalse(assessment['automatic_review'])
        self.assertEqual(request_service_refund(self.account, self.user, self.subscription.pk, 'refund')['status'], 'review')

    def test_annual_refund_waits_for_successful_renewal_delivery_instead_of_omitting_cash(self):
        from .refunds import service_refund_assessment, request_service_refund
        from .subscriptions import fulfill_renewal_attempt
        from .models import ServiceRefund
        self.source(); self.purchase()
        attempt = self.pending_paid_renewal()
        assessment = service_refund_assessment(self.account, self.user, self.subscription.pk)
        self.assertFalse(assessment['eligible'], assessment)
        self.assertEqual(assessment['reason'], 'PAYMENT_RECONCILIATION_REQUIRED')
        with self.assertRaisesMessage(ValueError, 'PAYMENT_RECONCILIATION_REQUIRED'):
            request_service_refund(self.account, self.user, self.subscription.pk, 'refund')
        self.assertFalse(ServiceRefund.objects.exists())
        self.subscription.refresh_from_db(); self.assertIsNone(self.subscription.refund_cutoff_at)
        fulfill_renewal_attempt(self.account, attempt['id'])
        order = PaymentAttempt.objects.get(pk=attempt['id']).order
        assessment = service_refund_assessment(self.account, self.user, self.subscription.pk)
        self.assertTrue(assessment['eligible'], assessment)
        self.assertEqual(next(item['amount_minor'] for item in assessment['items'] if item['order_id']==str(order.pk)), str(order.amount_minor))

    def test_upgrade_carries_each_payment_for_its_own_interval_once(self):
        from .subscriptions import subscription_change_quote, confirm_subscription_change, activate_subscription_change
        from .refunds import service_refund_assessment, request_service_refund
        from .models import SubscriptionChange, ServiceRefund
        self.anchor = datetime(2026, 1, 1, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.now.return_value = self.anchor
        self.source()
        self.now.return_value = datetime(2026, 8, 1, tzinfo=self.anchor.tzinfo)
        change = self.purchase()
        self.assertEqual(change.order.amount_minor, 50301)
        ProductVersion.objects.create(code='seat-original', version=2, kind='subscription', audience='business',
            billing_cycle='annual', minimum_seats=1, unit_amount_minor=140000, credits_quantity='100',
            entitlements={'resource_mode':'seat_included', 'upgrade_rank':1}, entitlement_version='fixture-1',
            credit_rule_version='fixture-1', terms_version='fixture-1', state='published', effective_at=self.anchor)
        target = ProductVersion.objects.create(code='seat-upgrade', version=1, kind='subscription', audience='business',
            billing_cycle='annual', minimum_seats=1, unit_amount_minor=150000, credits_quantity='200',
            entitlements={'resource_mode':'seat_included', 'upgrade_rank':2}, entitlement_version='fixture-1',
            credit_rule_version='fixture-1', terms_version='fixture-1', state='published', effective_at=self.anchor)
        self.now.return_value = datetime(2026, 9, 1, tzinfo=self.anchor.tzinfo)
        row = subscription_change_quote(self.account, self.user, self.subscription.pk, target.pk, request_key='upgrade')
        plan_change = SubscriptionChange.objects.get(pk=row['id'])
        self.assertEqual((row['due_minor'], plan_change.snapshot['cash_carried_minor'], plan_change.snapshot['promotion_minor']),
            ('206411', 80219, 13370))
        self.assertEqual(sum(item['amount_minor'] for item in plan_change.cash_sources), 80219)
        self.assertEqual(len(plan_change.cash_sources), 2)
        confirmed = confirm_subscription_change(self.account, self.user, self.subscription.pk, row['id'],
            agreement_acceptance='fixture-1', request_key='upgrade-order')
        self.receipt(confirmed['order_id'], int(confirmed['due_minor']), 'upgrade-cash')
        activated = activate_subscription_change(self.account, row['id'], activated_at=timezone.now(),
            mandate={**self.mandate, 'agreement_reference':'upgraded-agreement'}, old_authorization_evidence_hash='c'*64)
        plan_change.refresh_from_db()
        assessment = service_refund_assessment(self.account, self.user, plan_change.new_subscription_id)
        self.assertEqual(assessment['amount_minor'], '262744')
        self.assertEqual(len(assessment['items']), 3)
        result = request_service_refund(self.account, self.user, plan_change.new_subscription_id, 'future-refund')
        self.assertEqual(result, request_service_refund(self.account, self.user, plan_change.new_subscription_id, 'future-refund'))
        self.assertEqual(ServiceRefund.objects.count(), 1)
        with self.assertRaisesMessage(ValueError, 'REFUND_ALREADY_REQUESTED'):
            request_service_refund(self.account, self.user, plan_change.new_subscription_id, 'another-refund')
        self.assertEqual(service_refund_assessment(self.account, self.user, self.subscription.pk)['reason'], 'SUBSCRIPTION_ALREADY_CHANGED')

    def test_http_disabled_and_enabled_permissions_payload_sensitive_confirmation(self):
        self.source()
        client = Client(); client.force_login(self.user)
        session = client.session; session['active_account_id'] = str(self.account.pk); session.save()
        endpoint = f'/api/billing/subscriptions/{self.subscription.pk}/seat-quotes'
        headers = {'HTTP_X_BILLING_ACCOUNT':str(self.account.pk), 'HTTP_IDEMPOTENCY_KEY':'http'}
        response = client.post(endpoint, json.dumps({'quantity':1}), content_type='application/json', **headers)
        self.assertEqual(response.status_code, 503)
        with override_settings(BILLING_SEAT_PURCHASE_ENABLED=True):
            changed_headers = {**headers, 'HTTP_X_BILLING_ACCOUNT':str(self.other.pk)}
            self.assertEqual(client.post(endpoint, json.dumps({'quantity':1}), content_type='application/json',
                **changed_headers).status_code, 409)
            response = client.post(endpoint, json.dumps({'quantity':1}), content_type='application/json', **headers)
            self.assertEqual(response.status_code, 201)
            row = response.json()
            before = (Order.objects.count(), CreditBatch.objects.count())
            self.assertEqual(client.get(endpoint+'/'+row['id'], HTTP_X_BILLING_ACCOUNT=str(self.account.pk)).json(), row)
            self.assertEqual(before, (Order.objects.count(), CreditBatch.objects.count()))
            response = client.post(endpoint.replace('seat-quotes', 'seat-purchases'),
                json.dumps({'quote_id':row['id'], 'agreement_acceptance':'fixture-1'}), content_type='application/json', **headers)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()['error']['code'], 'SENSITIVE_CONFIRMATION_REQUIRED')
            self.assertEqual(client.post(endpoint.replace('seat-quotes', 'seat-purchases'),
                json.dumps({'quote_id':row['id'], 'agreement_acceptance':'fixture-1'}), content_type='application/json',
                **changed_headers).status_code, 409)
            from backend.models import LoginIdentity
            from backend.security_tests import PHONE_SETTINGS
            self.user.set_password('SeatFixture!82A'); self.user.save(update_fields=['password'])
            client.force_login(self.user)
            session = client.session; session['active_account_id'] = str(self.account.pk); session.save()
            LoginIdentity.objects.create(user=self.user, method='phone', identifier='+8613800000001')
            with override_settings(**PHONE_SETTINGS):
                with patch('backend.sms.send_code', return_value=1) as sender:
                    challenge = client.post('/api/account/sensitive-challenges', json.dumps({
                        'account_id':str(self.account.pk), 'password':'SeatFixture!82A'}), content_type='application/json')
                self.assertEqual(challenge.status_code, 201, challenge.content)
                verified = client.post('/api/account/sensitive-verify', json.dumps({
                    'challenge_id':challenge.json()['challenge_id'], 'code':sender.call_args.args[1]}),
                    content_type='application/json')
                self.assertEqual(verified.status_code, 200)
                with patch('backend.payments.start_payment') as start, patch('backend.payments.finish_payment') as finish:
                    confirmed = client.post(endpoint.replace('seat-quotes', 'seat-purchases'),
                        json.dumps({'quote_id':row['id'], 'agreement_acceptance':'fixture-1'}), content_type='application/json', **headers)
                start.assert_not_called(); finish.assert_not_called()
                self.assertEqual(confirmed.status_code, 201, confirmed.content)
                self.assertEqual(confirmed.json()['status'], 'pending_payment')
                self.assertIsNotNone(confirmed.json()['order_id'])
            self.assertEqual(client.get(endpoint, **headers).status_code, 405)
            response = client.post(endpoint, json.dumps({'quantity':True}), content_type='application/json', **headers)
            self.assertEqual(response.status_code, 422)

    def test_renewal_seat_ids_skip_an_expired_middle_seat(self):
        from .subscriptions import _renewal_snapshot, _period_seats
        self.source(cycle='monthly')
        original_period = self.subscription.periods.get()
        future_order = self.base_order(1, 'future')
        append_paid_period(self.account, self.subscription.pk, future_order['id'], cycle_index=1)
        old_addition = self.purchase()
        self.now.return_value = month_boundary(self.anchor, 1)+timedelta(seconds=1)
        quote = self.quote(key='next-month-addition')
        order = self.confirm(quote, key='next-month-order')
        self.receipt(order['order_id'], int(order['amount_minor']), 'next-month-payment')
        ids = _renewal_snapshot(self.subscription)['seat_ids']
        self.assertEqual(list(self.subscription.seats.filter(pk__in=ids).order_by('position').values_list('position', flat=True)), [1, 3])
        self.assertNotIn(str(old_addition.seats.get().pk), ids)
        self.assertEqual(list(_period_seats(original_period).values_list('position', flat=True)), [1, 2])

    def test_invalid_roles_accounts_quantities_and_read_calls_cannot_buy(self):
        from .subscriptions import seat_purchase_quote, seat_purchase_detail
        self.source()
        for quantity in (False, 0, -1, '1', 10001):
            with self.subTest(quantity=quantity), self.assertRaisesMessage(ValueError, 'INVALID_SEATS'):
                self.quote(quantity)
        row = self.quote()
        member = Membership.objects.create(account=self.account, user=self.other_user, role='member')
        with self.assertRaisesMessage(ValueError, 'FORBIDDEN'):
            seat_purchase_quote(self.account, member.user, self.subscription.pk, 1, request_key='ordinary')
        with self.assertRaisesMessage(ValueError, 'NOT_FOUND'):
            seat_purchase_detail(self.other, self.other_user, self.subscription.pk, row['id'])
        with self.assertRaisesMessage(ValueError, 'IDEMPOTENCY_CONFLICT'):
            self.quote(2)
        Membership.objects.filter(pk=member.pk).update(role='billing_admin')
        self.assertEqual(seat_purchase_quote(self.account, member.user, self.subscription.pk, 1,
            request_key='administrator')['added_seats'], 1)

    def test_multiple_quotes_cannot_sell_using_a_stale_capacity_snapshot(self):
        self.source()
        first = self.quote(key='first'); second = self.quote(key='second')
        order = self.confirm(first)
        with self.assertRaisesMessage(ValueError, 'PAYMENT_RECONCILIATION_REQUIRED'):
            self.confirm(second, key='other-confirmation')
        self.receipt(order['order_id'], int(order['amount_minor']))
        with self.assertRaisesMessage(ValueError, 'SOURCE_SUBSCRIPTION_CHANGED'):
            self.confirm(second, key='other-confirmation')

    def test_paid_but_undelivered_renewal_blocks_extra_purchase_until_delivery(self):
        from .subscriptions import prepare_renewal_attempt, fulfill_renewal_attempt
        from .billing import claim_payment_dispatch, record_attempt_event
        self.source(cycle='monthly')
        end = month_boundary(self.anchor, 1)
        self.now.return_value = end-timedelta(days=1)
        stale = self.quote(key='before-renewal')
        attempt = prepare_renewal_attempt(self.account, self.subscription.pk, cycle_index=1,
            dispatch_not_before=timezone.now(), expires_at=end)
        sent_renewal_notice_fixture(self.account, attempt['id'])
        with override_settings(BILLING_RENEWAL_CHARGING_ENABLED=True):
            claim_payment_dispatch(self.account, attempt['id'])
        order = PaymentAttempt.objects.get(pk=attempt['id']).order
        self.receipt(order.pk, order.amount_minor, 'renewal')
        record_attempt_event(self.account, attempt['id'], 'success', 'succeeded', source='verified_callback', evidence_hash='c'*64)
        with self.assertRaisesMessage(ValueError, 'PAYMENT_RECONCILIATION_REQUIRED'):
            self.quote()
        with self.assertRaisesMessage(ValueError, 'PAYMENT_RECONCILIATION_REQUIRED'):
            self.confirm(stale)
        fulfill_renewal_attempt(self.account, attempt['id'])
        self.assertEqual(self.quote()['next_paid_seats'], 1)
        self.purchase()
        self.assertEqual(self.subscription.periods.get(cycle_index=1).snapshot['seat_quantity'], 1)

    def pending_paid_renewal(self):
        from .subscriptions import prepare_renewal_attempt
        from .billing import claim_payment_dispatch, record_attempt_event
        end = month_boundary(self.anchor, 1)
        attempt = prepare_renewal_attempt(self.account, self.subscription.pk, cycle_index=1,
            dispatch_not_before=timezone.now(), expires_at=end)
        sent_renewal_notice_fixture(self.account, attempt['id'])
        with override_settings(BILLING_RENEWAL_CHARGING_ENABLED=True):
            claim_payment_dispatch(self.account, attempt['id'])
        order = PaymentAttempt.objects.get(pk=attempt['id']).order
        self.receipt(order.pk, order.amount_minor, 'pending-renewal')
        record_attempt_event(self.account, attempt['id'], 'success', 'succeeded', source='verified_callback', evidence_hash='c'*64)
        return attempt

    def monthly_upgrade_target(self):
        return ProductVersion.objects.create(code='monthly-upgrade', version=1, kind='subscription', audience='business',
            billing_cycle='monthly', minimum_seats=1, unit_amount_minor=240000, credits_quantity='200',
            entitlements={'resource_mode':'seat_included', 'upgrade_rank':2}, entitlement_version='fixture-1',
            credit_rule_version='fixture-1', terms_version='fixture-1', state='published', effective_at=self.anchor)

    def test_upgrade_quote_waits_for_already_paid_renewal_delivery(self):
        from .subscriptions import subscription_change_quote
        self.source(cycle='monthly'); target = self.monthly_upgrade_target()
        self.pending_paid_renewal()
        with self.assertRaisesMessage(ValueError, 'PAYMENT_RECONCILIATION_REQUIRED'):
            subscription_change_quote(self.account, self.user, self.subscription.pk, target.pk, request_key='upgrade')

    def test_upgrade_confirmation_waits_for_already_paid_renewal_delivery(self):
        from .subscriptions import subscription_change_quote, confirm_subscription_change
        self.source(cycle='monthly'); target = self.monthly_upgrade_target()
        quote = subscription_change_quote(self.account, self.user, self.subscription.pk, target.pk, request_key='upgrade')
        self.pending_paid_renewal()
        with self.assertRaisesMessage(ValueError, 'PAYMENT_RECONCILIATION_REQUIRED'):
            confirm_subscription_change(self.account, self.user, self.subscription.pk, quote['id'],
                agreement_acceptance='fixture-1', request_key='upgrade-order')

    def test_upgrade_activation_waits_for_already_paid_renewal_delivery(self):
        from .subscriptions import subscription_change_quote, confirm_subscription_change, activate_subscription_change
        self.source(cycle='monthly'); target = self.monthly_upgrade_target()
        quote = subscription_change_quote(self.account, self.user, self.subscription.pk, target.pk, request_key='upgrade')
        order = confirm_subscription_change(self.account, self.user, self.subscription.pk, quote['id'],
            agreement_acceptance='fixture-1', request_key='upgrade-order')
        self.receipt(order['order_id'], int(order['due_minor']), 'upgrade-payment')
        self.pending_paid_renewal()
        with self.assertRaisesMessage(ValueError, 'PAYMENT_RECONCILIATION_REQUIRED'):
            activate_subscription_change(self.account, quote['id'], activated_at=timezone.now(),
                mandate={**self.mandate, 'agreement_reference':'upgrade-agreement'}, old_authorization_evidence_hash='c'*64)

    def test_historical_extra_purchase_does_not_block_later_full_year_refund(self):
        from .refunds import service_refund_assessment
        self.source()
        future = self.base_order(1, 'future')
        append_paid_period(self.account, self.subscription.pk, future['id'], cycle_index=1)
        self.purchase()
        self.now.return_value = month_boundary(self.anchor, 13)
        assessment = service_refund_assessment(self.account, self.user, self.subscription.pk)
        self.assertTrue(assessment['eligible'], assessment)
        self.assertEqual(assessment['amount_minor'], '100000')

    def test_payment_attempt_deadline_cannot_extend_quote(self):
        from .billing import prepare_payment_attempt, claim_payment_dispatch
        self.source(); row = self.confirm(self.quote())
        deadline = datetime.fromisoformat(row['expires_at'])
        with self.assertRaisesMessage(ValueError, 'QUOTE_EXPIRED'):
            prepare_payment_attempt(self.account, self.user, row['order_id'], 'too-late', expires_at=deadline+timedelta(seconds=1))
        attempt = prepare_payment_attempt(self.account, self.user, row['order_id'], 'on-time', expires_at=deadline)
        self.now.return_value = deadline
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])

    def test_second_payment_does_not_add_more_seats(self):
        from .models import PaidSeat
        self.source(); change = self.purchase()
        self.receipt(change.order_id, change.order.amount_minor, 'duplicate-money')
        self.assertEqual(PaidSeat.objects.count(), 2)
        self.assertEqual(change.order.payment_facts.count(), 2)
        self.assertEqual(CreditBatch.objects.filter(purchase_change=change).count(), 1)

    def test_parallel_quote_confirmation_prevents_two_orders_for_same_capacity(self):
        self.source()
        first = self.quote(key='one'); second = self.quote(key='two')
        results = SubscriptionConcurrencyTests.parallel(self, [lambda:self.confirm(first, key='first'),
            lambda:self.confirm(second, key='second')])
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn('PAYMENT_RECONCILIATION_REQUIRED', results)

    def test_migration_rollback_preserves_extra_pooled_credit_history_on_conflict(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL transactional schema rollback required')
        from django.db import IntegrityError
        from django.db.migrations.executor import MigrationExecutor
        self.source(mode='shared_included', seats=10)
        change = self.purchase()
        batches = list(CreditBatch.objects.order_by('pk').values('id', 'initial_quantity', 'purchase_change_id'))
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            with self.assertRaises(IntegrityError):
                executor.migrate([('commerce', '0031_seat_purchase_changes')])
            self.assertEqual(list(CreditBatch.objects.order_by('pk').values('id', 'initial_quantity', 'purchase_change_id')), batches)
            self.assertEqual(CreditBatch.objects.filter(purchase_change=change).count(), 1)
            self.assertIn(('commerce', '0032_seat_purchase_grants'), MigrationExecutor(connection).loader.applied_migrations)
        finally:
            MigrationExecutor(connection).migrate(latest)

    def test_parallel_confirmation_and_fulfillment_do_not_duplicate_seats_or_points(self):
        self.source(); row = self.quote()
        results = SubscriptionConcurrencyTests.parallel(self, [lambda:self.confirm(row)]*2)
        self.assertEqual(results[0], results[1])
        order = results[0]
        from .billing import _persist_confirmed_payment
        from .subscriptions import fulfill_seat_purchase
        _persist_confirmed_payment(self.account, order['order_id'], channel='fixture', merchant_reference='fixture-shenzhen',
            transaction_reference='parallel-receipt', amount_minor=int(order['amount_minor']), currency='CNY',
            paid_at=timezone.now(), evidence_hash='b'*64)
        results = SubscriptionConcurrencyTests.parallel(self, [lambda:fulfill_seat_purchase(self.account, row['id'])]*2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.subscription.seats.count(), 2)
        self.assertEqual(CreditBatch.objects.filter(source_key__startswith='seat-purchase:').count(), 1)


class MemberRemovalTests(TransactionTestCase):
    """Offboarding is immediate access revocation, not global account deletion."""
    def setUp(self):
        from django.contrib.auth import get_user_model
        from backend.models import BillingAccount
        self.owner = get_user_model().objects.create_user(username='removal-owner',password='RemovalFixture!82A')
        self.employee = get_user_model().objects.create_user(username='removal-employee')
        self.account = BillingAccount.objects.create(kind='business', name='Removal fixture')
        self.owner_member = Membership.objects.create(account=self.account,user=self.owner,role='owner')
        self.employee_member = Membership.objects.create(account=self.account,user=self.employee,role='billing_admin')
        self.other = BillingAccount.objects.create(kind='business', name='Other fixture')
        self.other_member = Membership.objects.create(account=self.other,user=self.employee,role='owner')
        self.personal = BillingAccount.objects.create(kind='personal',name='Personal fixture',personal_owner=self.employee)
        Membership.objects.create(account=self.personal,user=self.employee,role='owner')

    def remove(self, **changes):
        from .subscriptions import remove_member
        values = dict(account=self.account,actor=self.owner,membership_id=self.employee_member.pk,request_key='removal-fixture')
        values.update(changes)
        return remove_member(**values)

    def login_client(self, user, account=None, **options):
        client = Client(**options)
        client.force_login(user)
        session = client.session
        session['active_account_id'] = str((account or self.account).pk)
        session.save()
        return client

    def test_removal_is_idempotent_and_preserves_other_memberships_and_user(self):
        from backend.models import MemberRemoval
        result = self.remove()
        self.assertEqual(result,self.remove())
        self.employee_member.refresh_from_db()
        self.employee.refresh_from_db()
        self.other_member.refresh_from_db()
        self.assertFalse(self.employee_member.is_active)
        self.assertTrue(self.employee.is_active)
        self.assertTrue(self.other_member.is_active)
        self.assertEqual(MemberRemoval.objects.count(),1)
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
            self.remove(membership_id=self.owner_member.pk)
        with self.assertRaisesRegex(ValueError,'MEMBER_ALREADY_REMOVED'):
            self.remove(request_key='another-request')

    def test_owner_only_self_owner_cross_account_and_invalid_key_denied(self):
        for changes, code in (({'actor':self.employee},'FORBIDDEN'),
                ({'membership_id':self.owner_member.pk},'OWNER_REMOVAL_FORBIDDEN'),
                ({'membership_id':self.other_member.pk},'NOT_FOUND'),
                ({'request_key':''},'INVALID_IDEMPOTENCY_KEY')):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError,code):
                self.remove(**changes)
        self.employee_member.refresh_from_db()
        self.assertTrue(self.employee_member.is_active)

    def test_owner_cannot_remove_another_owner_and_audit_failure_rolls_back(self):
        from backend.models import MemberRemoval
        self.employee_member.role = 'owner'
        self.employee_member.save(update_fields=['role'])
        with self.assertRaisesRegex(ValueError,'OWNER_REMOVAL_FORBIDDEN'):
            self.remove()
        self.employee_member.role = 'member'
        self.employee_member.save(update_fields=['role'])
        with patch('backend.models.MemberRemoval.objects.create',side_effect=RuntimeError('fixture audit failure')):
            with self.assertRaisesRegex(RuntimeError,'fixture audit failure'):
                self.remove()
        self.employee_member.refresh_from_db()
        self.assertTrue(self.employee_member.is_active)
        self.assertFalse(MemberRemoval.objects.exists())

    def test_removal_audit_cannot_be_changed_or_deleted(self):
        from backend.models import MemberRemoval
        record = MemberRemoval.objects.get(pk=self.remove()['removal_id'])
        record.handoff_pending = True
        with self.assertRaisesRegex(ValueError,'IMMUTABLE_MEMBER_REMOVAL'):
            record.save()
        with self.assertRaisesRegex(ValueError,'IMMUTABLE_MEMBER_REMOVAL'):
            record.delete()
        with self.assertRaisesRegex(ValueError,'IMMUTABLE_MEMBER_REMOVAL'):
            MemberRemoval.objects.filter(pk=record.pk).update(handoff_pending=True)
        with self.assertRaisesRegex(ValueError,'IMMUTABLE_MEMBER_REMOVAL'):
            MemberRemoval.objects.filter(pk=record.pk).delete()
        with self.assertRaisesRegex(ValueError,'IMMUTABLE_MEMBER_REMOVAL'):
            MemberRemoval.objects.bulk_update([record],['handoff_pending'])
        record.refresh_from_db()
        self.assertFalse(record.handoff_pending)

    def test_reserved_tasks_do_not_delay_removal_or_mutate_company_assets(self):
        from .models import Reservation
        from backend.models import MemberRemoval
        now = timezone.now()
        reservation = Reservation.objects.create(account=self.account,task_key='handoff-fixture',
            membership=self.employee_member,shared_period_start=now,shared_period_end=now+timedelta(days=1),quantity='2')
        result = self.remove()
        self.assertTrue(result['handoff_pending'])
        self.assertFalse(result['executor_connected'])
        record = MemberRemoval.objects.get(pk=result['removal_id'])
        self.assertEqual(record.pending_reservation_ids,[str(reservation.pk)])
        reservation.refresh_from_db()
        self.assertEqual((reservation.account_id,reservation.membership_id,reservation.state),
            (self.account.pk,self.employee_member.pk,'reserved'))

    def test_api_strict_payload_csrf_account_and_removed_session(self):
        client = self.login_client(self.owner)
        endpoint = '/api/billing/organization/members/remove'
        data = {'membership_id':self.employee_member.pk,'request_key':'api-fixture','confirm':True}
        self.assertEqual(client.post(endpoint,json.dumps(data),content_type='application/json').status_code,409)
        for bad in ({**data,'confirm':False},{**data,'extra':True},{**data,'membership_id':True}):
            self.assertEqual(client.post(endpoint,json.dumps(bad),content_type='application/json',
                HTTP_X_BILLING_ACCOUNT=str(self.account.pk)).status_code,422)
        strict = self.login_client(self.owner,enforce_csrf_checks=True)
        self.assertEqual(strict.post(endpoint,json.dumps(data),content_type='application/json',
            HTTP_X_BILLING_ACCOUNT=str(self.account.pk)).status_code,403)
        employee = self.login_client(self.employee)
        unconfirmed = client.post(endpoint,json.dumps(data),content_type='application/json',
            HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        self.assertEqual(unconfirmed.status_code,403)
        self.assertEqual(unconfirmed.json()['error']['code'],'SENSITIVE_CONFIRMATION_REQUIRED')
        from backend.models import LoginIdentity, LoginChallenge
        from backend.security_tests import PHONE_SETTINGS
        LoginIdentity.objects.create(user=self.owner,method='phone',identifier='+8613800000001')
        # Exercise the actual password + bound-phone verification endpoints.
        # Only the SMS transport is synthetic; the confirmation gate is real.
        with override_settings(**PHONE_SETTINGS):
            with patch('backend.sms.send_code',return_value=1) as sender:
                challenge=client.post('/api/account/sensitive-challenges',json.dumps({
                    'account_id':str(self.account.pk),'password':'RemovalFixture!82A'}),content_type='application/json')
            self.assertEqual(challenge.status_code,201,challenge.content)
            code=sender.call_args.args[1]
            confirmed=client.post('/api/account/sensitive-verify',json.dumps({
                'challenge_id':challenge.json()['challenge_id'],'code':code}),content_type='application/json')
            self.assertEqual(confirmed.status_code,200,confirmed.content)
            self.assertIsNotNone(LoginChallenge.objects.get(pk=challenge.json()['challenge_id']).consumed_at)
            response = client.post(endpoint,json.dumps(data),content_type='application/json',
                HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        self.assertEqual(response.status_code,200,response.content)
        for path in ('billing/account','billing/organization','billing/my-usage','billing/subscriptions','billing/orders'):
            self.assertEqual(employee.get('/api/'+path).status_code,403,path)
        inquiry = {'company':'Fixture','seats':2,'requirements':'Fixture','contact_email':'fixture@example.invalid'}
        self.assertEqual(employee.post('/api/billing/purchase-inquiries',json.dumps(inquiry),
            content_type='application/json',HTTP_X_BILLING_ACCOUNT=str(self.account.pk)).status_code,403)
        self.assertEqual(employee.post('/api/billing/accounts/select',json.dumps({'account_id':str(self.other.pk)}),
            content_type='application/json',HTTP_X_BILLING_ACCOUNT=str(self.account.pk)).status_code,200)
        self.assertEqual(employee.get('/api/billing/account').status_code,200)

    def test_organization_owner_capability_and_history(self):
        owner = self.login_client(self.owner)
        admin = self.login_client(self.employee)
        self.assertTrue(owner.get('/api/billing/organization').json()['capabilities']['member_removal'])
        self.assertFalse(admin.get('/api/billing/organization').json()['capabilities']['member_removal'])
        self.remove()
        data = owner.get('/api/billing/organization').json()
        removed = next(row for row in data['members'] if row['id'] == self.employee_member.pk)
        self.assertFalse(removed['can_remove'])
        self.assertIsNotNone(removed['removed_at'])
        history = owner.get('/api/billing/organization-history').json()
        self.assertEqual(history['items'][0]['type'],'member_removal')

    def test_financial_write_waiting_for_account_lock_rechecks_removed_member(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Actual row-lock waiting requires PostgreSQL.')
        import uuid
        from threading import Event
        from django.contrib.sessions.backends.db import SessionStore
        from django.db import transaction
        from django.test import RequestFactory
        from backend.account import register_session
        from backend.models import BillingAccount, PurchaseInquiry
        from backend.views import api
        client = self.login_client(self.employee)
        request = RequestFactory().post('/api/billing/purchase-inquiries',json.dumps({
            'company':'Fixture','seats':2,'requirements':'Fixture','contact_email':'fixture@example.invalid'}),
            content_type='application/json',HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        request.user = self.employee
        request.session = SessionStore(session_key=client.session.session_key)
        request.session.load()
        request.request_id = uuid.uuid4()
        register_session(request)
        waiting = Event()
        def write():
            close_old_connections()
            def signal(execute,sql,params,many,context):
                if 'FOR UPDATE' in sql and 'backend_billingaccount' in sql:
                    waiting.set()
                return execute(sql,params,many,context)
            try:
                with connection.execute_wrapper(signal):
                    return api(request,'billing/purchase-inquiries').status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                BillingAccount.objects.select_for_update().get(pk=self.account.pk)
                future = pool.submit(write)
                self.assertTrue(waiting.wait(timeout=5),'Write must lock company before authorizing member.')
                self.remove()
            self.assertEqual(future.result(timeout=10),403)
        self.assertFalse(PurchaseInquiry.objects.exists())

    def test_cached_account_after_completed_switch_rejects_old_write(self):
        import uuid
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import RequestFactory
        from backend.account import register_session
        from backend.models import PurchaseInquiry
        from backend.views import api
        client = self.login_client(self.employee)
        request = RequestFactory().post('/api/billing/purchase-inquiries',json.dumps({
            'company':'Fixture','seats':2,'requirements':'Fixture','contact_email':'fixture@example.invalid'}),
            content_type='application/json',HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        request.user = self.employee
        request.session = SessionStore(session_key=client.session.session_key)
        self.assertEqual(request.session.get('active_account_id'),str(self.account.pk))
        request.request_id = uuid.uuid4()
        register_session(request)
        switched = client.post('/api/billing/accounts/select',json.dumps({'account_id':str(self.other.pk)}),
            content_type='application/json')
        self.assertEqual(switched.status_code,200)
        self.assertEqual(client.session.get('active_account_id'),str(self.other.pk))
        response = api(request,'billing/purchase-inquiries')
        self.assertEqual(response.status_code,409)
        self.assertEqual(json.loads(response.content)['error']['code'],'ACCOUNT_CHANGED')
        self.assertFalse(PurchaseInquiry.objects.exists())

    def test_account_switch_holds_transaction_through_session_save(self):
        from django.contrib.sessions.middleware import SessionMiddleware
        client = self.login_client(self.employee)
        process_response = SessionMiddleware.process_response
        def save(middleware,request,response):
            self.assertTrue(connection.in_atomic_block,'Account selection must commit with its session save.')
            self.assertEqual(request.session.load()['active_account_id'],str(self.account.pk))
            result = process_response(middleware,request,response)
            self.assertEqual(request.session.load()['active_account_id'],str(self.other.pk))
            return result
        with patch.object(SessionMiddleware,'process_response',save):
            response = client.post('/api/billing/accounts/select',json.dumps({'account_id':str(self.other.pk)}),
                content_type='application/json')
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.cookies)
        self.assertEqual(client.session.get('active_account_id'),str(self.other.pk))
        response = client.post('/api/billing/purchase-inquiries',json.dumps({
            'company':'Fixture','seats':2,'requirements':'Fixture','contact_email':'fixture@example.invalid'}),
            content_type='application/json',HTTP_X_BILLING_ACCOUNT=str(self.other.pk))
        self.assertEqual(response.status_code,201)

    def test_account_switch_session_save_failure_rolls_back(self):
        from django.db import DatabaseError
        from django.contrib.sessions.backends.db import SessionStore
        from backend.models import AccountSession
        client = self.login_client(self.employee,raise_request_exception=False)
        with patch.object(SessionStore,'save',side_effect=DatabaseError('fixture session storage failure')):
            response = client.post('/api/billing/accounts/select',json.dumps({'account_id':str(self.other.pk)}),
                content_type='application/json')
        self.assertGreaterEqual(response.status_code,500)
        self.assertEqual(client.session.get('active_account_id'),str(self.account.pk))
        self.assertFalse(AccountSession.objects.exists())

    def test_account_switch_saved_session_rolls_back_on_server_error(self):
        from django.contrib.sessions.middleware import SessionMiddleware
        from backend.models import AccountSession
        client = self.login_client(self.employee)
        process_response = SessionMiddleware.process_response
        def fail_after_save(middleware,request,response):
            result = process_response(middleware,request,response)
            self.assertEqual(request.session.load()['active_account_id'],str(self.other.pk))
            result.status_code = 500
            return result
        with patch.object(SessionMiddleware,'process_response',fail_after_save):
            response = client.post('/api/billing/accounts/select',json.dumps({'account_id':str(self.other.pk)}),
                content_type='application/json')
        self.assertEqual(response.status_code,500)
        self.assertEqual(client.session.get('active_account_id'),str(self.account.pk))
        self.assertFalse(AccountSession.objects.exists())

    def test_cached_write_waits_for_account_switch_session_save(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Actual row-lock waiting requires PostgreSQL.')
        import uuid
        from threading import Event
        from django.contrib.sessions.backends.db import SessionStore
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory
        from backend.account import register_session
        from backend.models import PurchaseInquiry
        from backend.views import api
        client = self.login_client(self.employee)
        request = RequestFactory().post('/api/billing/purchase-inquiries',json.dumps({
            'company':'Fixture','seats':2,'requirements':'Fixture','contact_email':'fixture@example.invalid'}),
            content_type='application/json',HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        request.user = self.employee
        request.session = SessionStore(session_key=client.session.session_key)
        self.assertEqual(request.session.get('active_account_id'),str(self.account.pk))
        request.request_id = uuid.uuid4()
        register_session(request)
        saving, release, waiting = Event(), Event(), Event()
        process_response = SessionMiddleware.process_response
        def pause_save(middleware,select_request,response):
            saving.set()
            if not release.wait(timeout=5):
                raise AssertionError('Account selection session save was not released.')
            return process_response(middleware,select_request,response)
        def select():
            close_old_connections()
            try:
                return client.post('/api/billing/accounts/select',json.dumps({'account_id':str(self.other.pk)}),
                    content_type='application/json').status_code
            finally:
                close_old_connections()
        def write():
            close_old_connections()
            def signal(execute,sql,params,many,context):
                if 'FOR UPDATE' in sql and 'backend_accountsession' in sql:
                    waiting.set()
                return execute(sql,params,many,context)
            try:
                with connection.execute_wrapper(signal):
                    response = api(request,'billing/purchase-inquiries')
                    return response.status_code,json.loads(response.content)
            finally:
                close_old_connections()
        with patch.object(SessionMiddleware,'process_response',pause_save), ThreadPoolExecutor(max_workers=2) as pool:
            selection = pool.submit(select)
            try:
                self.assertTrue(saving.wait(timeout=5),'Selection must reach its session save.')
                mutation = pool.submit(write)
                self.assertTrue(waiting.wait(timeout=5),'Write must attempt the shared session-row lock.')
                with self.assertRaises(TimeoutError):
                    mutation.result(timeout=0.2)
            finally:
                release.set()
            self.assertEqual(selection.result(timeout=10),200)
            status,data = mutation.result(timeout=10)
        self.assertEqual(status,409)
        self.assertEqual(data['error']['code'],'ACCOUNT_CHANGED')
        self.assertEqual(client.session.get('active_account_id'),str(self.other.pk))
        self.assertFalse(PurchaseInquiry.objects.exists())


@override_settings(BILLING_SALES_ENABLED=True, BILLING_RENEWAL_CHARGING_ENABLED=True,
                   BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
class RenewalIntentTests(TransactionTestCase):
    def setUp(self):
        SubscriptionTests.setUp(self)

    def paid(self):
        return SubscriptionTests.paid(self)

    def activate(self, *args, **kwargs):
        return SubscriptionTests.activate(self, *args, **kwargs)

    def prepare(self, subscription_id, *, notice_sent=False, **changes):
        from .subscriptions import prepare_renewal_attempt
        values = dict(cycle_index=1, dispatch_not_before=month_boundary(self.anchor, 12)-timedelta(days=1),
                      expires_at=month_boundary(self.anchor, 12))
        values.update(changes)
        attempt=prepare_renewal_attempt(self.account, subscription_id, **values)
        if notice_sent:sent_renewal_notice_fixture(self.account,attempt['id'])
        return attempt

    def test_new_renewal_claim_requires_confirmed_notice_at_least_one_day_before(self):
        from backend.models import NotificationDelivery
        from .billing import claim_payment_dispatch
        sub=self.activate()
        attempt=self.prepare(sub['id'])
        self.now.return_value=month_boundary(self.anchor,12)-timedelta(hours=1)
        with self.assertRaisesRegex(ValueError,'RENEWAL_NOTICE_REQUIRED'):
            claim_payment_dispatch(self.account,attempt['id'])
        sent_renewal_notice_fixture(self.account,attempt['id'])
        delivery=NotificationDelivery.objects.get()
        timely=delivery.confirmed_at
        for state,confirmed_at in (('pending',timely),('failed',timely),('unknown',timely),
                ('sent',None),('sent',timely+timedelta(seconds=1))):
            delivery.state=state;delivery.confirmed_at=confirmed_at
            delivery.save(update_fields=['state','confirmed_at'])
            with self.subTest(state=state,confirmed_at=confirmed_at),self.assertRaisesRegex(ValueError,'RENEWAL_NOTICE_REQUIRED'):
                claim_payment_dispatch(self.account,attempt['id'])
            row=PaymentAttempt.objects.get(pk=attempt['id'])
            self.assertEqual(row.state,'created');self.assertIsNone(row.dispatched_at)
            self.assertIsNone(row.dispatch_authorization)
        delivery.state='sent';delivery.confirmed_at=timely
        delivery.save(update_fields=['state','confirmed_at'])
        self.assertTrue(claim_payment_dispatch(self.account,attempt['id'])['dispatch'])
        self.assertFalse(claim_payment_dispatch(self.account,attempt['id'])['dispatch'])

    def test_renewal_stop_preserves_intent_and_does_not_stop_purchase_or_receipts(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        from .subscriptions import fulfill_renewal_attempt
        sub = self.activate()
        attempt = self.prepare(sub['id'], notice_sent=True)
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        with override_settings(BILLING_RENEWAL_CHARGING_ENABLED=False):
            with self.assertRaisesRegex(ValueError, 'RENEWAL_CHARGING_DISABLED'):
                claim_payment_dispatch(self.account, attempt['id'])
            row = PaymentAttempt.objects.get(pk=attempt['id'])
            self.assertEqual(row.state, 'created')
            self.assertIsNone(row.dispatched_at)
            self.assertIsNone(row.dispatch_authorization)
            self.assertEqual(Order.objects.get(pk=row.order_id).status, 'pending_payment')
            # The renewal-specific switch does not block a separate new purchase.
            from .billing import prepare_payment_attempt
            # Use a fresh unpaid order; paid orders must not be dispatched again.
            quote = create_quote(self.account, self.user, self.product.pk, 1,
                timezone.now()+timedelta(minutes=5), channel='fixture', buyer_region='CN')
            purchase_order = create_order(self.account, self.user,
                {'quote_id':quote['id'], 'agreement_acceptance':'fixture-1'}, 'stop-purchase')
            purchase = prepare_payment_attempt(self.account, self.user, purchase_order['id'],
                'stop-purchase', expires_at=timezone.now()+timedelta(minutes=15))
            self.assertTrue(claim_payment_dispatch(self.account, purchase['id'])['dispatch'])
        with override_settings(BILLING_RENEWAL_CHARGING_ENABLED=True):
            self.assertTrue(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        with override_settings(BILLING_SALES_ENABLED=False, BILLING_RENEWAL_CHARGING_ENABLED=False):
            self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
            record_attempt_event(self.account, attempt['id'], 'stop-unknown', 'unknown',
                source='local', evidence_hash='')
            record_confirmed_payment(self.account, row.order_id, channel='fixture',
                merchant_reference='fixture-shenzhen', transaction_reference='stop-receipt',
                amount_minor=12345, currency='CNY', paid_at=timezone.now(), evidence_hash='b'*64)
            result = fulfill_renewal_attempt(self.account, attempt['id'])
            self.assertEqual(result, fulfill_renewal_attempt(self.account, attempt['id']))
            self.assertEqual(SubscriptionPeriod.objects.filter(subscription_id=sub['id']).count(), 2)
            self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])

    def test_payment_method_removal_stops_dispatch_and_waits_for_revocation(self):
        from .billing import claim_payment_dispatch
        from .subscriptions import (register_verified_payment_method, payment_method_impact,
            request_payment_method_removal, finish_payment_method_removal)
        method = register_verified_payment_method(self.account, channel='fixture',
            merchant_reference='fixture-shenzhen', fingerprint='c'*64, evidence_hash='d'*64)
        sub = self.activate(payment_method_id=method)
        attempt = self.prepare(sub['id'])
        impact = payment_method_impact(self.account, self.user, method)
        result = request_payment_method_removal(self.account, self.user, method,
            expected_impact=impact['confirmation_hash'])
        self.assertEqual(result['state'], 'removing')
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        self.assertEqual(credits_summary(self.account)['available'], '17.25')
        with self.assertRaisesRegex(ValueError, 'REMOVAL_PENDING'):
            finish_payment_method_removal(self.account, method, evidence_hash='e'*64)
        confirm_cancellation(self.account, sub['id'], agreement_reference='fixture-agreement', evidence_hash='f'*64)
        self.assertEqual(finish_payment_method_removal(self.account, method, evidence_hash='e'*64)['state'], 'removed')
        self.assertEqual(finish_payment_method_removal(self.account, method, evidence_hash='e'*64)['state'], 'removed')
        with self.assertRaisesRegex(ValueError, 'IDEMPOTENCY_CONFLICT'):
            finish_payment_method_removal(self.account, method, evidence_hash='a'*64)

    def topup_fixture(self):
        from .subscriptions import register_verified_payment_method
        order = SubscriptionTests.business_paid(self,'shared_only')
        self.activate(order)
        method = register_verified_payment_method(self.account,channel='fixture',merchant_reference='fixture-shenzhen',
            fingerprint='c'*64,evidence_hash='d'*64)
        pack = ProductVersion.objects.create(code='fixture_business_pack',version=1,kind='credits',audience='business',
            unit_amount_minor=731,credits_quantity='10',entitlements={'fixture':True},entitlement_version='fixture-1',
            credit_rule_version='fixture-1',terms_version='fixture-1',state='published',effective_at=self.anchor)
        args = dict(payment_method_id=method,product_id=pack.pk,threshold='2',monthly_cap_minor=1462,
            mandate={**self.mandate,'agreement_reference':'independent-topup'},terms_version='topup-fixture-1',
            consent_evidence_hash='e'*64,expected_version=0,request_key='topup-consent')
        return args

    def test_enterprise_topup_notifications_are_distinct_atomic_and_idempotent(self):
        from django.db import transaction
        from .models import BillingNotification
        from .subscriptions import (record_topup_authorization, pause_topup_authorization,
                                    request_topup_revocation, confirm_topup_revocation)
        from .billing import notifications_page
        args = self.topup_fixture()
        before = BillingNotification.objects.count()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            with self.assertRaisesRegex(ValueError,'ROLLBACK'):
                with transaction.atomic():
                    record_topup_authorization(self.account,self.user,**args)
                    raise ValueError('ROLLBACK')
            self.assertEqual(BillingNotification.objects.count(),before)
            result = record_topup_authorization(self.account,self.user,**args)
            self.assertEqual(result,record_topup_authorization(self.account,self.user,**args))
        with self.assertRaisesRegex(ValueError,'ROLLBACK'):
            with transaction.atomic():
                pause_topup_authorization(self.account,reason='user_requested')
                raise ValueError('ROLLBACK')
        self.assertFalse(BillingNotification.objects.filter(type='topup_authorization_paused').exists())
        pause_topup_authorization(self.account,reason='user_requested')
        pause_topup_authorization(self.account,reason='user_requested')
        with self.assertRaisesRegex(ValueError,'ROLLBACK'):
            with transaction.atomic():
                request_topup_revocation(self.account,self.user,result['id'])
                raise ValueError('ROLLBACK')
        self.assertFalse(BillingNotification.objects.filter(type='topup_revocation_processing').exists())
        request_topup_revocation(self.account,self.user,result['id'])
        request_topup_revocation(self.account,self.user,result['id'])
        values = dict(agreement_reference=args['mandate']['agreement_reference'],evidence_hash='f'*64)
        with self.assertRaisesRegex(ValueError,'ROLLBACK'):
            with transaction.atomic():
                confirm_topup_revocation(self.account,result['id'],**values)
                raise ValueError('ROLLBACK')
        self.assertFalse(BillingNotification.objects.filter(type='topup_revocation_confirmed').exists())
        confirm_topup_revocation(self.account,result['id'],**values)
        confirm_topup_revocation(self.account,result['id'],**values)
        rows = [row for row in notifications_page(self.account)['items'] if row['type'].startswith('topup_')]
        self.assertEqual({row['type'] for row in rows},{'topup_authorization_recorded','topup_authorization_paused',
            'topup_revocation_processing','topup_revocation_confirmed'})
        self.assertEqual(len(rows),4)
        self.assertTrue(all(row['amount_minor'] is None and row['currency'] is None for row in rows))
        self.assertTrue(all(set(row)=={'id','type','order_id','subscription_id','refund_id','amount_minor','currency','occurred_at'} for row in rows))

    def test_topup_consent_is_independent_versioned_and_refund_pauses(self):
        from .subscriptions import record_topup_authorization
        from .billing import freeze_credits, resolve_credit_freeze
        from .models import TopupAuthorization
        args = self.topup_fixture()
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            record_topup_authorization(self.account,self.user,**args)
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            with self.assertRaisesRegex(ValueError,'INDEPENDENT_AUTHORIZATION_REQUIRED'):
                record_topup_authorization(self.account,self.user,**{**args,'mandate':self.mandate})
            result = record_topup_authorization(self.account,self.user,**args)
            self.assertFalse(result['charging_enabled'])
            self.assertEqual(record_topup_authorization(self.account,self.user,**args),result)
            with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
                record_topup_authorization(self.account,self.user,**{**args,'threshold':'3'})
            with self.assertRaisesRegex(ValueError,'AUTHORIZATION_VERSION_CHANGED'):
                record_topup_authorization(self.account,self.user,**{**args,'request_key':'another'})
        self.assertEqual(Order.objects.count(),1)
        self.assertEqual(TopupAuthorization.objects.count(),1)
        grant_credits(self.account,'purchased','fixture-refund','10',self.anchor+timedelta(days=10))
        batch = CreditBatch.objects.get(source='purchased')
        hold = freeze_credits(self.account,batch.pk,'refund','2')
        consent = TopupAuthorization.objects.get(pk=result['id'])
        consent.refresh_from_db()
        self.assertEqual((consent.state,consent.pause_reason),('paused','credits_refund'))
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            with self.assertRaisesRegex(ValueError,'REFUND_PENDING'):
                record_topup_authorization(self.account,self.user,**{**args,'request_key':'during-refund',
                    'expected_version':1,'mandate':{**args['mandate'],'agreement_reference':'fresh-topup'}})
        resolve_credit_freeze(self.account,hold['id'],'released',evidence_hash='f'*64)
        consent.refresh_from_db()
        self.assertEqual(consent.state,'paused')
        self.assertEqual(AuditEvent.objects.filter(action='topup.authorization_paused').count(),1)

    def test_topup_expiry_pauses_cancelled_subscription_and_never_resumes_on_replay(self):
        from .subscriptions import record_topup_authorization, close_expired_renewals
        from .models import TopupAuthorization
        args = self.topup_fixture()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            result = record_topup_authorization(self.account,self.user,**args)
            cancel_subscription(self.account,self.user,Subscription.objects.get(account=self.account).pk)
            self.now.return_value = month_boundary(self.anchor,12)
            close_expired_renewals(self.account)
            record = TopupAuthorization.objects.get(pk=result['id'])
            self.assertEqual((record.state,record.pause_reason),('paused','subscription_expired'))
            self.assertEqual(record_topup_authorization(self.account,self.user,**args),result)
            record.refresh_from_db()
            self.assertEqual(record.state,'paused')
            close_expired_renewals(self.account)
            self.assertEqual(AuditEvent.objects.filter(action='topup.authorization_paused').count(),1)

    def test_topup_receipt_notification_survives_delivery_failure_and_manual_purchase_is_separate(self):
        from .subscriptions import record_topup_authorization, prepare_topup_attempt
        from .billing import claim_payment_dispatch, notifications_page
        from .models import BillingNotification
        args = self.topup_fixture()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            auth = record_topup_authorization(self.account,self.user,**args)
            attempt = prepare_topup_attempt(self.account,auth['id'],trigger_key='notice-receipt',
                starts_at=self.anchor,ends_at=self.anchor+timedelta(days=30),policy_version='fixture-window',
                expires_at=self.anchor+timedelta(hours=1))
            with self.assertRaisesRegex(ValueError,'TOPUP_CHARGING_DISABLED'):
                claim_payment_dispatch(self.account,attempt['id'])
            order = PaymentAttempt.objects.get(pk=attempt['id']).order
            values = dict(channel='fixture',merchant_reference='fixture-shenzhen',transaction_reference='notice-topup-receipt',
                amount_minor=731,currency='CNY',paid_at=self.anchor,evidence_hash='b'*64)
            with patch('backend.commerce.billing.fulfill_paid_order',side_effect=ValueError('DELIVERY_FAILED')):
                with self.assertRaisesRegex(ValueError,'DELIVERY_FAILED'):
                    record_confirmed_payment(self.account,order.pk,**values)
            row = BillingNotification.objects.get(order=order)
            self.assertEqual((row.type,row.amount_minor,row.currency),('topup_payment_received',731,'CNY'))
            self.assertEqual(Order.objects.get(pk=order.pk).status,'paid_pending_entitlement')
            self.assertFalse(CreditBatch.objects.filter(order=order).exists())
            record_confirmed_payment(self.account,order.pk,**values)
            record_confirmed_payment(self.account,order.pk,**values)
            self.assertEqual(BillingNotification.objects.filter(order=order).count(),1)
            public = next(item for item in notifications_page(self.account)['items'] if item['order_id']==str(order.pk))
            self.assertEqual((public['type'],public['amount_minor'],public['currency']),('topup_payment_received','731','CNY'))
            quote = create_quote(self.account,self.user,args['product_id'],1,self.anchor+timedelta(hours=1),channel='fixture',buyer_region='CN')
            manual = create_order(self.account,self.user,{'quote_id':quote['id'],'agreement_acceptance':'fixture-1'},'topup:manual-not-budget')
            record_confirmed_payment(self.account,manual['id'],**{**values,'transaction_reference':'notice-manual-receipt'})
            self.assertEqual(BillingNotification.objects.get(order_id=manual['id']).type,'payment_received')

    def test_topup_failed_notice_requires_applied_failure_and_rolls_back_with_event(self):
        from django.db import transaction
        from .subscriptions import record_topup_authorization, prepare_topup_attempt
        from .billing import record_attempt_event, claim_payment_dispatch
        from .models import BillingNotification, PaymentAttemptEvent
        args = self.topup_fixture()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            auth = record_topup_authorization(self.account,self.user,**args)
            def prepare(key):
                return prepare_topup_attempt(self.account,auth['id'],trigger_key=key,
                    starts_at=self.anchor,ends_at=self.anchor+timedelta(days=30),policy_version='fixture-window',
                    expires_at=self.anchor+timedelta(hours=1))
            first = prepare('notice-failure')
            with self.assertRaisesRegex(ValueError,'TOPUP_CHARGING_DISABLED'):
                claim_payment_dispatch(self.account,first['id'])
            # Isolated fixture of a prior unknown PSP dispatch; the real gate stays off.
            PaymentAttempt.objects.filter(pk=first['id']).update(state='unknown',dispatched_at=self.anchor)
            record_attempt_event(self.account,first['id'],'unknown','unknown',source='local',evidence_hash='')
            self.assertFalse(BillingNotification.objects.filter(type='topup_payment_failed').exists())
            with self.assertRaisesRegex(ValueError,'ROLLBACK'):
                with transaction.atomic():
                    record_attempt_event(self.account,first['id'],'failed','failed',source='provider_query',evidence_hash='c'*64)
                    raise ValueError('ROLLBACK')
            self.assertFalse(PaymentAttemptEvent.objects.filter(attempt_id=first['id'],event_key='failed').exists())
            self.assertFalse(BillingNotification.objects.filter(type='topup_payment_failed').exists())
            record_attempt_event(self.account,first['id'],'failed','failed',source='provider_query',evidence_hash='c'*64)
            record_attempt_event(self.account,first['id'],'failed','failed',source='provider_query',evidence_hash='c'*64)
            record_attempt_event(self.account,first['id'],'failed-again','failed',source='provider_query',evidence_hash='d'*64)
            row = BillingNotification.objects.get(order_id=PaymentAttempt.objects.get(pk=first['id']).order_id)
            self.assertEqual(row.type,'topup_payment_failed')
            self.assertEqual((row.amount_minor,row.currency),(None,''))
            second = prepare('notice-paid-late-failure')
            order = PaymentAttempt.objects.get(pk=second['id']).order
            record_confirmed_payment(self.account,order.pk,channel='fixture',merchant_reference='fixture-shenzhen',
                transaction_reference='notice-paid-before-failure',amount_minor=731,currency='CNY',paid_at=self.anchor,evidence_hash='b'*64)
            record_attempt_event(self.account,second['id'],'late-failed','failed',source='provider_query',evidence_hash='e'*64)
            self.assertFalse(PaymentAttemptEvent.objects.get(attempt_id=second['id'],event_key='late-failed').applied)
            self.assertEqual(BillingNotification.objects.filter(type='topup_payment_failed').count(),1)
            self.assertFalse(BillingNotification.objects.filter(order=order,type='topup_payment_failed').exists())

    def test_topup_budget_binds_intent_and_counts_success_and_inflight_across_versions(self):
        from .subscriptions import record_topup_authorization, prepare_topup_attempt, topup_budget_summary
        from .billing import claim_payment_dispatch, record_attempt_event
        from .models import TopupBudget
        args = self.topup_fixture()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        window = dict(starts_at=self.anchor,ends_at=self.anchor+timedelta(days=30),policy_version='fixture-window')
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            auth = record_topup_authorization(self.account,self.user,**args)
            def prepare(key,authorization=auth['id']):
                return prepare_topup_attempt(self.account,authorization,trigger_key=key,
                    expires_at=self.anchor+timedelta(hours=1),**window)
            first = prepare('one')
            self.assertEqual(prepare('one'),first)
            self.assertEqual(TopupBudget.objects.count(),1)
            with self.assertRaisesRegex(ValueError,'TOPUP_CHARGING_DISABLED'):
                claim_payment_dispatch(self.account,first['id'])
            with self.assertRaisesRegex(ValueError,'TOPUP_PENDING'):
                prepare('two')
            # Explicit isolated fixture of a previously dispatched unknown PSP result.
            PaymentAttempt.objects.filter(pk=first['id']).update(state='unknown',dispatched_at=self.anchor)
            self.assertEqual(topup_budget_summary(self.account,**window)['reserved_minor'],731)
            from .subscriptions import automatic_topup_summary
            summary = automatic_topup_summary(self.account)
            self.assertFalse(summary['charging_enabled'])
            self.assertEqual(summary['budget']['reserved_minor'],'731')
            self.assertEqual(summary['budget']['remaining_minor'],'731')
            self.assertNotIn('independent-topup',json.dumps(summary))
            record_attempt_event(self.account,first['id'],'failed','failed',source='provider_query',evidence_hash='b'*64)
            self.assertEqual(topup_budget_summary(self.account,**window)['reserved_minor'],0)
            second = prepare('two')
            order = PaymentAttempt.objects.get(pk=second['id']).order
            record_confirmed_payment(self.account,order.pk,channel='fixture',merchant_reference='fixture-shenzhen',
                transaction_reference='topup-success',amount_minor=731,currency='CNY',paid_at=self.anchor,evidence_hash='b'*64)
            self.assertEqual(topup_budget_summary(self.account,**window)['spent_minor'],731)
            new = record_topup_authorization(self.account,self.user,**{**args,'request_key':'new-consent',
                'expected_version':1,'mandate':{**args['mandate'],'agreement_reference':'new-topup'}})
            third = prepare('three',new['id'])
            totals = topup_budget_summary(self.account,**window)
            self.assertEqual((totals['spent_minor'],totals['reserved_minor']),(731,731))
            self.assertEqual(PaymentAttempt.objects.get(pk=third['id']).dispatch_authorization,None)
            with self.assertRaisesRegex(ValueError,'TOPUP_PENDING'):
                prepare('four',new['id'])
            third_order = PaymentAttempt.objects.get(pk=third['id']).order
            record_confirmed_payment(self.account,third_order.pk,channel='fixture',merchant_reference='fixture-shenzhen',
                transaction_reference='topup-success-three',amount_minor=731,currency='CNY',paid_at=self.anchor,evidence_hash='b'*64)
            with self.assertRaisesRegex(ValueError,'TOPUP_BUDGET_EXCEEDED'):
                prepare('four',new['id'])
            with self.assertRaisesRegex(ValueError,'BUDGET_WINDOW_CONFLICT'):
                prepare_topup_attempt(self.account,new['id'],trigger_key='shift-window',expires_at=self.anchor+timedelta(hours=1),
                    **{**window,'ends_at':self.anchor+timedelta(days=31)})
            self.assertEqual(TopupBudget.objects.count(),3)

    def test_topup_removal_waits_for_separate_revocation_and_preserves_paid_subscription(self):
        from .subscriptions import (record_topup_authorization, confirm_topup_revocation, payment_method_impact,
            request_payment_method_removal,finish_payment_method_removal)
        from .models import TopupAuthorization
        args = self.topup_fixture()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            result = record_topup_authorization(self.account,self.user,**args)
        evidence = dict(agreement_reference='independent-topup',evidence_hash='f'*64)
        with self.assertRaisesRegex(ValueError,'REVOCATION_NOT_REQUESTED'):
            confirm_topup_revocation(self.account,result['id'],**evidence)
        method = args['payment_method_id']
        impact = payment_method_impact(self.account,self.user,method)
        request_payment_method_removal(self.account,self.user,method,expected_impact=impact['confirmation_hash'])
        self.assertEqual(TopupAuthorization.objects.get(pk=result['id']).state,'revoking')
        with self.assertRaisesRegex(ValueError,'REMOVAL_PENDING'):
            finish_payment_method_removal(self.account,method,evidence_hash='a'*64)
        confirm_topup_revocation(self.account,result['id'],**evidence)
        confirm_topup_revocation(self.account,result['id'],**evidence)
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
            confirm_topup_revocation(self.account,result['id'],**{**evidence,'evidence_hash':'b'*64})
        finish_payment_method_removal(self.account,method,evidence_hash='a'*64)
        self.assertTrue(Subscription.objects.get(account=self.account).auto_renew)
        self.assertEqual(AuditEvent.objects.filter(action='topup.authorization_revoked').count(),1)

    def test_authorization_change_versions_dispatch_and_retired_revocation(self):
        from .subscriptions import (register_verified_payment_method, replace_subscription_authorization,
            confirm_retired_authorization, payment_method_impact, request_payment_method_removal,
            finish_payment_method_removal)
        from .billing import claim_payment_dispatch
        old = register_verified_payment_method(self.account, channel='fixture',
            merchant_reference='fixture-shenzhen', fingerprint='c'*64, evidence_hash='d'*64)
        new = register_verified_payment_method(self.account, channel='fixture',
            merchant_reference='fixture-shenzhen', fingerprint='e'*64, evidence_hash='f'*64)
        order = self.paid()
        sub = self.activate(order, payment_method_id=old)
        attempt = self.prepare(sub['id'], notice_sent=True)
        mandate = {**self.mandate, 'agreement_reference': 'replacement-agreement'}
        args = dict(payment_method_id=new, mandate=mandate, expected_version=0, request_key='replace-one')
        counts = (Order.objects.count(), SubscriptionPeriod.objects.count(), CreditBatch.objects.count())
        result = replace_subscription_authorization(self.account, self.user, sub['id'], **args)
        self.assertEqual(result['version'], 1)
        self.assertEqual(replace_subscription_authorization(self.account, self.user, sub['id'], **args), result)
        self.assertEqual((Order.objects.count(), SubscriptionPeriod.objects.count(), CreditBatch.objects.count()), counts)
        self.activate(order, payment_method_id=old)  # Original activation replay remains idempotent.
        with self.assertRaisesRegex(ValueError, 'AUTHORIZATION_VERSION_CHANGED'):
            replace_subscription_authorization(self.account, self.user, sub['id'], **{**args, 'request_key': 'stale'})
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        self.assertTrue(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        row = PaymentAttempt.objects.get(pk=attempt['id'])
        self.assertEqual(row.dispatch_authorization['mandate'], mandate)
        self.assertEqual(row.dispatch_authorization['authorization_version'], 1)
        impact = payment_method_impact(self.account, self.user, old)
        self.assertEqual(len(impact['retired_authorizations']), 1)
        from .subscriptions import payment_methods_summary
        public = next(item for item in payment_methods_summary(self.account)['items'] if item['id'] == old)
        self.assertEqual(public['retired_authorizations'], impact['retired_authorizations'])
        self.assertNotIn('fixture-agreement', json.dumps(public))
        request_payment_method_removal(self.account, self.user, old, expected_impact=impact['confirmation_hash'])
        with self.assertRaisesRegex(ValueError, 'REMOVAL_PENDING'):
            finish_payment_method_removal(self.account, old, evidence_hash='a'*64)
        confirm_retired_authorization(self.account, result['id'], agreement_reference='fixture-agreement', evidence_hash='b'*64)
        confirm_retired_authorization(self.account, result['id'], agreement_reference='fixture-agreement', evidence_hash='b'*64)
        finish_payment_method_removal(self.account, old, evidence_hash='a'*64)
        current = Subscription.objects.get(pk=sub['id'])
        self.assertTrue(current.auto_renew)
        self.assertEqual(str(current.payment_method_id), new)

    def test_authorization_change_keeps_inflight_and_rolls_back_failed_audit(self):
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization
        from .billing import claim_payment_dispatch
        from .models import AuthorizationChange
        old = register_verified_payment_method(self.account, channel='fixture',
            merchant_reference='fixture-shenzhen', fingerprint='c'*64, evidence_hash='d'*64)
        new = register_verified_payment_method(self.account, channel='fixture',
            merchant_reference='fixture-shenzhen', fingerprint='e'*64, evidence_hash='f'*64)
        sub = self.activate(payment_method_id=old)
        attempt = self.prepare(sub['id'], notice_sent=True)
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        claim_payment_dispatch(self.account, attempt['id'])
        snapshot = PaymentAttempt.objects.get(pk=attempt['id']).dispatch_authorization
        args = dict(payment_method_id=new, mandate={**self.mandate, 'agreement_reference':'replacement'},
            expected_version=0, request_key='replacement')
        with patch('backend.commerce.subscriptions.AuditEvent.objects.create', side_effect=RuntimeError('audit failure')):
            with self.assertRaisesRegex(RuntimeError, 'audit failure'):
                replace_subscription_authorization(self.account, self.user, sub['id'], **args)
        self.assertEqual(AuthorizationChange.objects.count(), 0)
        self.assertEqual(Subscription.objects.get(pk=sub['id']).authorization_version, 0)
        with self.assertRaisesRegex(ValueError, 'FORBIDDEN'):
            replace_subscription_authorization(self.account, self.other_user, sub['id'], **args)
        with self.assertRaisesRegex(ValueError, 'MANDATE_NOT_CONFIRMED'):
            replace_subscription_authorization(self.account, self.user, sub['id'], **{**args, 'mandate':{}})
        replace_subscription_authorization(self.account, self.user, sub['id'], **args)
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        self.assertEqual(PaymentAttempt.objects.get(pk=attempt['id']).dispatch_authorization, snapshot)
        with self.assertRaisesRegex(ValueError, 'IDEMPOTENCY_CONFLICT'):
            replace_subscription_authorization(self.account, self.user, sub['id'], **{**args, 'payment_method_id':old})

    @override_settings(BILLING_TERMS_DOCUMENTS={'fixture-1':{'approved':True,
        'zh':{'title':'测试','paragraphs':['测试协议']},'en':{'title':'Fixture','paragraphs':['Fixture terms']}}})
    def test_cross_channel_authorization_preserves_inflight_and_future_commercial_snapshot(self):
        from .subscriptions import (register_verified_payment_method, replace_subscription_authorization, prepare_renewal_attempt,
            fulfill_renewal_attempt, confirm_retired_authorization, payment_method_impact,
            request_payment_method_removal, finish_payment_method_removal)
        from .billing import claim_payment_dispatch, purchase_terms
        channels = {**FIXTURE_CHANNELS, 'replacement': dict(FIXTURE_CHANNELS['fixture'])}
        old = register_verified_payment_method(self.account, channel='fixture', merchant_reference='fixture-shenzhen', fingerprint='c'*64, evidence_hash='d'*64)
        new = register_verified_payment_method(self.account, channel='replacement', merchant_reference='fixture-shenzhen', fingerprint='e'*64, evidence_hash='f'*64)
        digest = purchase_terms('fixture-1')['digest']
        quote = create_quote(self.account,self.user,self.product.pk,1,timezone.now()+timedelta(minutes=5),channel='fixture',buyer_region='CN',terms_digest=digest)
        order = create_order(self.account,self.user,{'quote_id':quote['id'],'agreement_acceptance':'fixture-1'},'public-original')
        record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',transaction_reference='public-original',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
        sub = self.activate(order['id'],payment_method_id=old)
        initial = Subscription.objects.get(pk=sub['id']).initial_order
        original_snapshot = dict(initial.snapshot)
        first = self.prepare(sub['id'], notice_sent=True)
        self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
        claim_payment_dispatch(self.account,first['id'])
        old_dispatch = PaymentAttempt.objects.get(pk=first['id']).dispatch_authorization
        mandate = {**self.mandate,'channel':'replacement','agreement_reference':'new-channel-agreement'}
        args = dict(payment_method_id=new,mandate=mandate,expected_version=0,request_key='cross-channel')
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            result = replace_subscription_authorization(self.account,self.user,sub['id'],**args)
            self.assertEqual(replace_subscription_authorization(self.account,self.user,sub['id'],**args),result)
            self.assertFalse(claim_payment_dispatch(self.account,first['id'])['dispatch'])
            first_row = PaymentAttempt.objects.get(pk=first['id'])
            self.assertEqual(first_row.dispatch_authorization,old_dispatch)
            record_confirmed_payment(self.account,first_row.order_id,channel='fixture',merchant_reference='fixture-shenzhen',
                transaction_reference='old-channel-inflight',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
            # Removing an already-reconciled old instrument does not erase its
            # historical dispatch identity or prevent outstanding delivery.
            confirm_retired_authorization(self.account,result['id'],agreement_reference='fixture-agreement',evidence_hash='c'*64)
            impact = payment_method_impact(self.account,self.user,old)
            request_payment_method_removal(self.account,self.user,old,expected_impact=impact['confirmation_hash'])
            finish_payment_method_removal(self.account,old,evidence_hash='d'*64)
            fulfill_renewal_attempt(self.account,first['id'])
            second = prepare_renewal_attempt(self.account,sub['id'],cycle_index=2,
                dispatch_not_before=month_boundary(self.anchor,24)-timedelta(days=1),expires_at=month_boundary(self.anchor,24))
            second_row = PaymentAttempt.objects.get(pk=second['id'])
            sent_renewal_notice_fixture(self.account,second['id'])
            self.assertEqual(second_row.order.snapshot,{**original_snapshot,'channel':'replacement'})
            self.assertEqual(second_row.order.snapshot['terms_document_digest'],digest)
            self.now.return_value = month_boundary(self.anchor,24)-timedelta(hours=1)
            # Simulated stored corruption cannot expand this transport-only change.
            saved_snapshot = second_row.order.snapshot
            Order.objects.filter(pk=second_row.order_id).update(snapshot={**saved_snapshot,'entitlements':{'unexpected':True}})
            with self.assertRaisesRegex(ValueError,'RENEWAL_SNAPSHOT_CONFLICT'):
                claim_payment_dispatch(self.account,second['id'])
            self.assertIsNone(PaymentAttempt.objects.get(pk=second['id']).dispatched_at)
            Order.objects.filter(pk=second_row.order_id).update(snapshot=saved_snapshot)
            self.assertTrue(claim_payment_dispatch(self.account,second['id'])['dispatch'])
            second_row.refresh_from_db()
            self.assertEqual(second_row.dispatch_authorization['mandate'],mandate)
            record_confirmed_payment(self.account,second_row.order_id,channel='replacement',merchant_reference='fixture-shenzhen',
                transaction_reference='new-channel-next-cycle',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
            Order.objects.filter(pk=second_row.order_id).update(snapshot={**saved_snapshot,'terms_version':'changed'})
            with self.assertRaisesRegex(ValueError,'SUBSCRIPTION_CHANGE_POLICY_REQUIRED'):
                fulfill_renewal_attempt(self.account,second['id'])
            Order.objects.filter(pk=second_row.order_id).update(snapshot=saved_snapshot)
            with override_settings(BILLING_SALES_ENABLED=False,BILLING_RENEWAL_CHARGING_ENABLED=False,BILLING_APPROVED_CHANNELS={}):
                fulfill_renewal_attempt(self.account,second['id'])
            self.assertEqual(SubscriptionPeriod.objects.filter(subscription_id=sub['id']).count(),3)
        initial.refresh_from_db()
        self.assertEqual(initial.snapshot,original_snapshot)

    def test_cross_channel_rejects_unapproved_or_invalid_authorization_without_changing_binding(self):
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization
        from .models import AuthorizationChange
        sub = self.activate()
        new = register_verified_payment_method(self.account, channel='replacement', merchant_reference='fixture-shenzhen', fingerprint='e'*64, evidence_hash='f'*64)
        args = dict(payment_method_id=new,mandate={**self.mandate,'channel':'replacement','agreement_reference':'replacement'},expected_version=0,request_key='cross')
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            replace_subscription_authorization(self.account,self.user,sub['id'],**args)
        channels = {**FIXTURE_CHANNELS,'replacement':dict(FIXTURE_CHANNELS['fixture'])}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            for invalid in ({'automatic_retry':True},{'merchant_reference':'different-merchant'},{'executor':'provider'}):
                with self.assertRaises(ValueError):
                    replace_subscription_authorization(self.account,self.user,sub['id'],**{**args,'mandate':{**args['mandate'],**invalid}})
        self.assertEqual(AuthorizationChange.objects.count(),0)
        self.assertEqual(Subscription.objects.get(pk=sub['id']).mandate,self.mandate)

    def test_undispatched_channel_changes_preserve_intent_and_pin_latest_transport(self):
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization, fulfill_renewal_attempt
        from .billing import claim_payment_dispatch, fulfill_paid_order, order_detail
        sub = self.activate()
        pending = self.prepare(sub['id'], notice_sent=True)
        before = PaymentAttempt.objects.get(pk=pending['id'])
        order_before = Order.objects.values().get(pk=before.order_id)
        quote_before = dict(before.order.quote.snapshot)
        channels = {**FIXTURE_CHANNELS, **{key:dict(FIXTURE_CHANNELS['fixture']) for key in ('replacement','third')}}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            for version, channel in enumerate(('replacement','third')):
                method = register_verified_payment_method(self.account,channel=channel,
                    merchant_reference='fixture-shenzhen',fingerprint=str(version+1)*64,evidence_hash='d'*64)
                mandate = {**self.mandate,'channel':channel,'agreement_reference':channel}
                args = dict(payment_method_id=method,mandate=mandate,expected_version=version,request_key=channel)
                changed = replace_subscription_authorization(self.account,self.user,sub['id'],**args)
                self.assertEqual(replace_subscription_authorization(self.account,self.user,sub['id'],**args),changed)
                self.assertEqual(self.prepare(sub['id'], notice_sent=True),pending)
                self.assertEqual(Order.objects.values().get(pk=before.order_id),order_before)
                row = PaymentAttempt.objects.get(pk=pending['id'])
                self.assertEqual(row.order.quote.snapshot,quote_before)
                self.assertIsNone(row.dispatch_authorization)
            self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
            claimed = claim_payment_dispatch(self.account,pending['id'])
            self.assertTrue(claimed['dispatch'])
            self.assertEqual(claimed['channel'],'third')
            row.refresh_from_db()
            self.assertEqual(row.dispatch_authorization['authorization_version'],2)
            frozen = dict(row.dispatch_authorization)
            self.assertFalse(claim_payment_dispatch(self.account,row.pk)['dispatch'])
            # Later authorization changes cannot rewrite this transaction's receipt identity.
            replace_subscription_authorization(self.account,self.user,sub['id'],payment_method_id=method,
                mandate={**mandate,'agreement_reference':'after-dispatch'},expected_version=2,request_key='after-dispatch')
            wrong = record_confirmed_payment(self.account,row.order_id,channel='fixture',
                merchant_reference='fixture-shenzhen',transaction_reference='wrong-original-channel',
                amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
            self.assertEqual(wrong['disposition'],'identity_mismatch_review')
            received = record_confirmed_payment(self.account,row.order_id,channel='third',
                merchant_reference='fixture-shenzhen',transaction_reference='migrated-channel',
                amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
            self.assertEqual(received['disposition'],'accepted')
            fulfill_paid_order(self.account,row.order_id)
            result = fulfill_renewal_attempt(self.account,row.pk)
            self.assertEqual(fulfill_renewal_attempt(self.account,row.pk),result)
            row.refresh_from_db()
            self.assertEqual(row.dispatch_authorization,frozen)
            self.assertEqual(row.order.snapshot,order_before['snapshot'])
            self.assertEqual(SubscriptionPeriod.objects.filter(subscription_id=sub['id']).count(),2)
            self.assertEqual(PaymentAttempt.objects.count(),1)
            self.assertTrue(order_detail(self.account,row.order_id)['needs_review'])

    def test_cross_channel_subscription_change_never_changes_independent_topup(self):
        from .subscriptions import record_topup_authorization, register_verified_payment_method, replace_subscription_authorization
        from .models import TopupAuthorization
        args = self.topup_fixture()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']},
                    'replacement':dict(FIXTURE_CHANNELS['fixture'])}
        new = register_verified_payment_method(self.account,channel='replacement',merchant_reference='fixture-shenzhen',fingerprint='e'*64,evidence_hash='f'*64)
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            topup = record_topup_authorization(self.account,self.user,**args)
            before = TopupAuthorization.objects.values().get(pk=topup['id'])
            sub = Subscription.objects.get(account=self.account)
            replace_subscription_authorization(self.account,self.user,sub.pk,payment_method_id=new,
                mandate={**self.mandate,'channel':'replacement','agreement_reference':'replacement'},expected_version=0,request_key='cross-channel')
            self.assertEqual(TopupAuthorization.objects.values().get(pk=topup['id']),before)

    def test_undispatched_migration_rejects_unsafe_intents_and_rolls_back_authorization(self):
        from django.db import transaction
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization
        from .models import AuthorizationChange, PaymentAttemptEvent
        from .billing import _persist_confirmed_payment, claim_payment_dispatch
        sub = self.activate()
        pending = self.prepare(sub['id'], notice_sent=True)
        row = PaymentAttempt.objects.get(pk=pending['id'])
        channels = {**FIXTURE_CHANNELS,'replacement':dict(FIXTURE_CHANNELS['fixture'])}
        method = register_verified_payment_method(self.account,channel='replacement',merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        args = dict(payment_method_id=method,mandate={**self.mandate,'channel':'replacement','agreement_reference':'replacement'},expected_version=0,request_key='migration')
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            for label in ('reconciliation','frozen','event','funds','expired','closed','commercial'):
                with self.subTest(label=label), transaction.atomic():
                    if label == 'reconciliation':
                        PaymentAttempt.objects.filter(pk=row.pk).update(needs_reconciliation=True)
                    elif label == 'frozen':
                        PaymentAttempt.objects.filter(pk=row.pk).update(dispatch_authorization={})
                    elif label == 'event':
                        PaymentAttemptEvent.objects.create(attempt=row,event_key='unexpected',outcome='unknown',source='local',evidence_hash='',applied=False)
                    elif label == 'funds':
                        _persist_confirmed_payment(self.account,row.order_id,channel='fixture',merchant_reference='other',transaction_reference='unexpected',amount_minor=1,currency='CNY',paid_at=timezone.now(),evidence_hash='c'*64)
                    elif label == 'expired':
                        self.now.return_value = month_boundary(self.anchor,12)
                    elif label == 'closed':
                        Order.objects.filter(pk=row.order_id).update(status='closed')
                    else:
                        Order.objects.filter(pk=row.order_id).update(snapshot={**row.order.snapshot,'amount_minor':1})
                    with self.assertRaisesRegex(ValueError,'RENEWAL_INTENT_PENDING|RENEWAL_NOT_AUTHORIZED'):
                        replace_subscription_authorization(self.account,self.user,sub['id'],**args)
                    self.assertFalse(AuthorizationChange.objects.exists())
                    transaction.set_rollback(True)
                self.now.return_value = self.anchor
            with patch('backend.commerce.subscriptions.AuditEvent.objects.create',side_effect=RuntimeError('audit failure')):
                with self.assertRaisesRegex(RuntimeError,'audit failure'):
                    replace_subscription_authorization(self.account,self.user,sub['id'],**args)
            self.assertFalse(AuthorizationChange.objects.exists())
            self.assertEqual(Subscription.objects.get(pk=sub['id']).authorization_version,0)
            replace_subscription_authorization(self.account,self.user,sub['id'],**args)
            self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
            with patch('backend.commerce.models.PaymentAttempt.save',side_effect=RuntimeError('dispatch failure')):
                with self.assertRaisesRegex(RuntimeError,'dispatch failure'):
                    claim_payment_dispatch(self.account,row.pk)
            row.refresh_from_db()
            self.assertEqual(row.state,'created')
            self.assertIsNone(row.dispatched_at)
            self.assertIsNone(row.dispatch_authorization)
            self.assertEqual(Subscription.objects.get(pk=sub['id']).authorization_version,1)

    def test_migrated_dispatch_rejects_forged_authorization_but_preserves_received_funds(self):
        from django.db import transaction
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization, fulfill_renewal_attempt
        from .billing import claim_payment_dispatch, _persist_confirmed_payment
        sub = self.activate()
        pending = self.prepare(sub['id'], notice_sent=True)
        method = register_verified_payment_method(self.account,channel='replacement',merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        channels = {**FIXTURE_CHANNELS,'replacement':dict(FIXTURE_CHANNELS['fixture'])}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            replace_subscription_authorization(self.account,self.user,sub['id'],payment_method_id=method,
                mandate={**self.mandate,'channel':'replacement','agreement_reference':'replacement'},expected_version=0,request_key='migration')
            self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
            claim_payment_dispatch(self.account,pending['id'])
        row = PaymentAttempt.objects.get(pk=pending['id'])
        frozen = row.dispatch_authorization
        for label, auth in (('missing',None),('no-history',{**frozen,'authorization_version':12}),
            ('boolean-version',{**frozen,'authorization_version':True}),('wrong-sub',{**frozen,'subscription_id':'other'}),
            ('wrong-mandate',{**frozen,'mandate':{**frozen['mandate'],'agreement_reference':'forged'}})):
            with self.subTest(label=label), transaction.atomic():
                PaymentAttempt.objects.filter(pk=row.pk).update(dispatch_authorization=auth)
                fact = _persist_confirmed_payment(self.account,row.order_id,channel='replacement',merchant_reference='fixture-shenzhen',
                    transaction_reference=label,amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
                self.assertEqual(fact['disposition'],'identity_mismatch_review')
                self.assertTrue(PaymentFact.objects.filter(pk=fact['id']).exists())
                self.assertIsNone(Order.objects.get(pk=row.order_id).paid_at)
                with self.assertRaises(ValueError):
                    fulfill_renewal_attempt(self.account,row.pk)
                self.assertEqual(SubscriptionPeriod.objects.count(),1)
                transaction.set_rollback(True)

    def test_channel_replacement_and_dispatch_serialize_on_one_intent(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Row-lock assertion requires PostgreSQL')
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization, fulfill_renewal_attempt
        from .billing import claim_payment_dispatch
        sub = self.activate()
        pending = self.prepare(sub['id'], notice_sent=True)
        method = register_verified_payment_method(self.account,channel='replacement',merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        channels = {**FIXTURE_CHANNELS,'replacement':dict(FIXTURE_CHANNELS['fixture'])}
        self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
        barrier = Barrier(2)
        def work(replace):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                if replace:
                    return replace_subscription_authorization(self.account,self.user,sub['id'],payment_method_id=method,
                        mandate={**self.mandate,'channel':'replacement','agreement_reference':'replacement'},expected_version=0,request_key='migration')
                return claim_payment_dispatch(self.account,pending['id'])
            finally:
                close_old_connections()
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(work,(True,False)))
            row = PaymentAttempt.objects.get(pk=pending['id'])
            self.assertTrue(results[1]['dispatch'])
            self.assertEqual(row.dispatch_authorization['authorization_version'],0 if results[1]['channel']=='fixture' else 1)
            self.assertEqual(row.dispatch_authorization['mandate']['channel'],results[1]['channel'])
            record_confirmed_payment(self.account,row.order_id,channel=results[1]['channel'],merchant_reference='fixture-shenzhen',
                transaction_reference='serialized',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
            fulfill_renewal_attempt(self.account,row.pk)
            self.assertFalse(claim_payment_dispatch(self.account,row.pk)['dispatch'])
            self.assertEqual(PaymentAttempt.objects.count(),1)
            self.assertEqual(Order.objects.count(),2)
            self.assertEqual(SubscriptionPeriod.objects.count(),2)

    def test_undispatched_expired_renewal_recovery_can_dispatch_and_activate(self):
        from .billing import claim_payment_dispatch
        from .subscriptions import activate_recovery_subscription
        sub = self.activate()
        original = self.prepare(sub['id'])
        self.now.return_value = month_boundary(self.anchor,12)
        self.assertEqual(claim_payment_dispatch(self.account,original['id'])['status'],'expired')
        recovery = self.recover(original)
        row = PaymentAttempt.objects.get(pk=recovery['id'])
        self.assertTrue(claim_payment_dispatch(self.account,row.pk)['dispatch'])
        record_confirmed_payment(self.account,row.order_id,channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='undispatched-recovery',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
        cancel_subscription(self.account,self.user,sub['id'])
        confirm_cancellation(self.account,sub['id'],agreement_reference='fixture-agreement',evidence_hash='c'*64)
        args = dict(activated_at=timezone.now(),mandate={**self.mandate,'agreement_reference':'new-recovery'})
        recovered = activate_recovery_subscription(self.account,row.pk,**args)
        self.assertEqual(activate_recovery_subscription(self.account,row.pk,**args),recovered)
        self.assertEqual(Subscription.objects.filter(auto_renew=True).count(),1)
        self.assertEqual(PaymentAttempt.objects.count(),2)

    def test_unknown_or_future_creation_channel_cannot_be_erased_by_migration(self):
        from django.db import transaction
        from .models import Quote
        from .billing import claim_payment_dispatch
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization
        sub = self.activate()
        pending = self.prepare(sub['id'])
        row = PaymentAttempt.objects.get(pk=pending['id'])
        channels = {**FIXTURE_CHANNELS,'replacement':dict(FIXTURE_CHANNELS['fixture'])}
        method = register_verified_payment_method(self.account,channel='replacement',merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        # The replacement did not exist at intent creation; simulate a damaged
        # stored channel in both immutable rows, not a client attack route.
        self.now.return_value = self.anchor+timedelta(days=1)
        args = dict(payment_method_id=method,mandate={**self.mandate,'channel':'replacement','agreement_reference':'replacement'},expected_version=0,request_key='migration')
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            for channel in (None,'unknown','replacement'):
                with self.subTest(channel=channel), transaction.atomic():
                    corrupted = {**row.order.snapshot,'channel':channel}
                    Order.objects.filter(pk=row.order_id).update(snapshot=corrupted)
                    Quote.objects.filter(pk=row.order.quote_id).update(snapshot=corrupted)
                    with self.assertRaisesRegex(ValueError,'RENEWAL_SNAPSHOT_CONFLICT'):
                        replace_subscription_authorization(self.account,self.user,sub['id'],**args)
                    transaction.set_rollback(True)
            replace_subscription_authorization(self.account,self.user,sub['id'],**args)
            self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
            for channel in (None,'unknown','replacement'):
                with self.subTest(dispatch_channel=channel):
                    corrupted = {**row.order.snapshot,'channel':channel}
                    Order.objects.filter(pk=row.order_id).update(snapshot=corrupted)
                    Quote.objects.filter(pk=row.order.quote_id).update(snapshot=corrupted)
                    try:
                        with self.assertRaisesRegex(ValueError,'RENEWAL_SNAPSHOT_CONFLICT'):
                            claim_payment_dispatch(self.account,row.pk)
                        self.assertIsNone(PaymentAttempt.objects.get(pk=row.pk).dispatched_at)
                    finally:
                        Order.objects.filter(pk=row.order_id).update(snapshot=row.order.snapshot)
                        Quote.objects.filter(pk=row.order.quote_id).update(snapshot=row.order.snapshot)

    def test_renewal_preserves_original_accepted_terms_digest_without_public_new_purchase(self):
        from .billing import purchase_terms
        terms = {'fixture-1':{'approved':True,'zh':{'title':'测试','paragraphs':['测试协议']},'en':{'title':'Fixture','paragraphs':['Fixture terms']}}}
        with override_settings(BILLING_TERMS_DOCUMENTS=terms):
            digest = purchase_terms('fixture-1')['digest']
            quote = create_quote(self.account,self.user,self.product.pk,1,timezone.now()+timedelta(minutes=5),channel='fixture',buyer_region='CN',terms_digest=digest)
            order = create_order(self.account,self.user,{'quote_id':quote['id'],'agreement_acceptance':'fixture-1'},'public-initial')
            record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',transaction_reference='public-initial',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
            sub = self.activate(order['id'])
            attempt = self.prepare(sub['id'])
            self.assertEqual(PaymentAttempt.objects.get(pk=attempt['id']).order.snapshot,Order.objects.get(pk=order['id']).snapshot)
            self.assertEqual(PaymentAttempt.objects.get(pk=attempt['id']).order.snapshot['terms_document_digest'],digest)

    def test_payment_method_unknown_inflight_blocks_removal_but_success_still_delivers(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        from .subscriptions import (register_verified_payment_method, payment_method_impact,
            request_payment_method_removal, finish_payment_method_removal, fulfill_renewal_attempt)
        method = register_verified_payment_method(self.account, channel='fixture',
            merchant_reference='fixture-shenzhen', fingerprint='c'*64, evidence_hash='d'*64)
        sub = self.activate(payment_method_id=method)
        attempt = self.prepare(sub['id'], notice_sent=True)
        old_impact = payment_method_impact(self.account, self.user, method)
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        self.assertTrue(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        with self.assertRaisesRegex(ValueError, 'PAYMENT_METHOD_IMPACT_CHANGED'):
            request_payment_method_removal(self.account, self.user, method,
                expected_impact=old_impact['confirmation_hash'])
        impact = payment_method_impact(self.account, self.user, method)
        self.assertEqual(len(impact['unresolved_payments']), 1)
        request_payment_method_removal(self.account, self.user, method, expected_impact=impact['confirmation_hash'])
        confirm_cancellation(self.account, sub['id'], agreement_reference='fixture-agreement', evidence_hash='f'*64)
        record_attempt_event(self.account, attempt['id'], 'unknown', 'unknown', source='local', evidence_hash='')
        with self.assertRaisesRegex(ValueError, 'REMOVAL_PENDING'):
            finish_payment_method_removal(self.account, method, evidence_hash='e'*64)
        row = PaymentAttempt.objects.get(pk=attempt['id'])
        record_confirmed_payment(self.account, row.order_id, channel='fixture', merchant_reference='fixture-shenzhen',
            transaction_reference='method-inflight', amount_minor=12345, currency='CNY',
            paid_at=timezone.now(), evidence_hash='b'*64)
        fulfill_renewal_attempt(self.account, attempt['id'])
        self.assertEqual(SubscriptionPeriod.objects.count(), 2)
        self.assertEqual(finish_payment_method_removal(self.account, method, evidence_hash='e'*64)['state'], 'removed')
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])

    def test_duplicate_receipt_blocks_method_removal_without_regranting(self):
        from .billing import claim_payment_dispatch
        from .subscriptions import (register_verified_payment_method, payment_method_impact,
            request_payment_method_removal, finish_payment_method_removal, fulfill_renewal_attempt)
        method = register_verified_payment_method(self.account, channel='fixture',
            merchant_reference='fixture-shenzhen', fingerprint='c'*64, evidence_hash='d'*64)
        sub = self.activate(payment_method_id=method)
        attempt = self.prepare(sub['id'], notice_sent=True)
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        claim_payment_dispatch(self.account, attempt['id'])
        row = PaymentAttempt.objects.get(pk=attempt['id'])
        for reference in ('original-receipt', 'duplicate-receipt'):
            record_confirmed_payment(self.account, row.order_id, channel='fixture',
                merchant_reference='fixture-shenzhen', transaction_reference=reference,
                amount_minor=12345, currency='CNY', paid_at=timezone.now(), evidence_hash='b'*64)
        fulfill_renewal_attempt(self.account, attempt['id'])
        self.assertEqual(SubscriptionPeriod.objects.count(), 2)
        self.assertEqual(PaymentFact.objects.filter(order=row.order).count(), 2)
        impact = payment_method_impact(self.account, self.user, method)
        self.assertEqual(impact['unresolved_payments'], [{'id': attempt['id'], 'status': 'review'}])
        request_payment_method_removal(self.account, self.user, method, expected_impact=impact['confirmation_hash'])
        confirm_cancellation(self.account, sub['id'], agreement_reference='fixture-agreement', evidence_hash='f'*64)
        with self.assertRaisesRegex(ValueError, 'REMOVAL_PENDING'):
            finish_payment_method_removal(self.account, method, evidence_hash='e'*64)

    def test_payment_method_scope_replay_removed_binding_and_atomic_failure(self):
        from .subscriptions import (register_verified_payment_method, payment_method_impact,
            request_payment_method_removal, finish_payment_method_removal)
        from .models import PaymentMethod
        args = dict(channel='fixture', merchant_reference='fixture-shenzhen', fingerprint='c'*64, evidence_hash='d'*64)
        method = register_verified_payment_method(self.account, **args)
        other = register_verified_payment_method(self.other, **args)
        self.assertNotEqual(method, other)
        self.assertEqual(register_verified_payment_method(self.account, **args), method)
        with self.assertRaisesRegex(ValueError, 'FORBIDDEN'):
            payment_method_impact(self.account, self.other_user, method)
        with self.assertRaisesRegex(ValueError, 'NOT_FOUND'):
            payment_method_impact(self.other, self.other_user, method)
        sub = self.activate(payment_method_id=method)
        impact = payment_method_impact(self.account, self.user, method)
        with patch('backend.commerce.subscriptions.AuditEvent.objects.create', side_effect=RuntimeError('audit-failed')):
            with self.assertRaisesRegex(RuntimeError, 'audit-failed'):
                request_payment_method_removal(self.account, self.user, method, expected_impact=impact['confirmation_hash'])
        self.assertEqual(PaymentMethod.objects.get(pk=method).state, 'active')
        self.assertTrue(Subscription.objects.get(pk=sub['id']).auto_renew)
        request_payment_method_removal(self.account, self.user, method, expected_impact=impact['confirmation_hash'])
        request_payment_method_removal(self.account, self.user, method, expected_impact=impact['confirmation_hash'])
        self.assertEqual(AuditEvent.objects.filter(action='payment_method.remove_requested').count(), 1)
        self.assertEqual(PaymentMethod.objects.get(pk=other).state, 'active')
        confirm_cancellation(self.account, sub['id'], agreement_reference='fixture-agreement', evidence_hash='f'*64)
        finish_payment_method_removal(self.account, method, evidence_hash='e'*64)
        self.now.return_value = month_boundary(self.anchor, 12)
        new_order = self.paid()
        with self.assertRaisesRegex(ValueError, 'PAYMENT_METHOD_BINDING_REQUIRED'):
            activate_subscription(self.account, new_order, activated_at=timezone.now(), billing_timezone='Asia/Shanghai',
                calendar_rule_version='fixture-calendar-v1', mandate=self.mandate)
        with self.assertRaisesRegex(ValueError, 'PAYMENT_METHOD_NOT_AVAILABLE'):
            activate_subscription(self.account, new_order, activated_at=timezone.now(), billing_timezone='Asia/Shanghai',
                calendar_rule_version='fixture-calendar-v1', mandate=self.mandate, payment_method_id=method)

    @override_settings(BILLING_APPROVED_CHANNELS={'fixture':{**FIXTURE_CHANNELS['fixture'],
        'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}})
    def test_payment_methods_show_independent_topup_history_without_writes_or_secrets(self):
        from django.test.utils import CaptureQueriesContext
        from .models import TopupAuthorization
        from .subscriptions import (record_topup_authorization, payment_methods_summary,
            request_topup_revocation, confirm_topup_revocation, register_verified_payment_method)
        args = self.topup_fixture()
        first = record_topup_authorization(self.account,self.user,**args)
        second = record_topup_authorization(self.account,self.user,**{**args,
            'expected_version':1,'request_key':'second-topup',
            'mandate':{**args['mandate'],'agreement_reference':'second-independent'}})
        method = args['payment_method_id']
        public = next(item for item in payment_methods_summary(self.account)['items'] if item['id']==method)
        self.assertEqual([item['state'] for item in public['topup_authorizations']],['superseded','authorized'])
        self.assertEqual([item['version'] for item in public['topup_authorizations']],[1,2])
        request_topup_revocation(self.account,self.user,first['id'])
        before = list(TopupAuthorization.objects.values())
        with CaptureQueriesContext(connection) as queries:
            data = payment_methods_summary(self.account)
            self.assertEqual(data,payment_methods_summary(self.account))
        for query in queries.captured_queries:
            self.assertRegex(query['sql'],r'^(SELECT|BEGIN|COMMIT|SAVEPOINT|RELEASE SAVEPOINT)\b')
        self.assertEqual(before,list(TopupAuthorization.objects.values()))
        self.assertFalse(data['can_edit'])
        self.assertFalse(data['automatic_topup_supported'])
        public = next(item for item in data['items'] if item['id']==method)
        timestamp = self.anchor.astimezone(ZoneInfo('UTC')).isoformat()
        self.assertEqual(public['topup_authorizations'][0],{
            'id':first['id'],'state':'revoking','version':1,'created_at':timestamp,
            'revocation_requested_at':timestamp,'revoked_at':None})
        self.assertEqual(public['topup_authorizations'][1]['id'],second['id'])
        self.assertIsNone(public['topup_authorizations'][1]['revocation_requested_at'])
        for secret in ('mandate','agreement_reference','evidence_hash','fingerprint','configuration','request_key','actor'):
            self.assertNotIn(secret,json.dumps(data))
        confirm_topup_revocation(self.account,first['id'],agreement_reference=args['mandate']['agreement_reference'],evidence_hash='f'*64)
        public = next(item for item in payment_methods_summary(self.account)['items'] if item['id']==method)
        self.assertEqual(public['topup_authorizations'][0]['state'],'revoked')
        self.assertEqual(public['topup_authorizations'][0]['revoked_at'],timestamp)
        self.assertEqual(public['topup_authorizations'][1]['state'],'authorized')
        other_method = register_verified_payment_method(self.other,channel='fixture',
            merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        self.assertEqual(payment_methods_summary(self.other)['items'][0]['id'],other_method)
        self.assertEqual(payment_methods_summary(self.other)['items'][0]['topup_authorizations'],[])

    @override_settings(BILLING_APPROVED_CHANNELS={'fixture':{**FIXTURE_CHANNELS['fixture'],
        'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}})
    def test_payment_methods_topup_projection_http_requires_financial_account(self):
        from .subscriptions import record_topup_authorization
        args = self.topup_fixture()
        record = record_topup_authorization(self.account,self.user,**args)
        client = Client()
        self.assertEqual(client.get('/api/billing/payment-methods').status_code,401)
        client.force_login(self.user)
        state=client.session;state['active_account_id']=str(self.account.pk);state.save()
        response=client.get('/api/billing/payment-methods',HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['items'][0]['topup_authorizations'][0]['id'],record['id'])
        self.assertEqual(client.get('/api/billing/payment-methods',HTTP_X_BILLING_ACCOUNT=str(self.other.pk)).status_code,409)
        self.assertEqual(client.post('/api/billing/payment-methods',{},content_type='application/json').status_code,405)
        client.force_login(self.other_user)
        state=client.session;state['active_account_id']=str(self.account.pk);state.save()
        self.assertEqual(client.get('/api/billing/payment-methods').status_code,403)
        Membership.objects.create(account=self.account,user=self.other_user,role='member')
        self.assertEqual(client.get('/api/billing/payment-methods').status_code,403)

    def test_canonical_intent_replay_and_changed_window(self):
        sub = self.activate()
        attempt = self.prepare(sub['id'])
        self.assertEqual(attempt, self.prepare(sub['id']))
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(Order.objects.count(), 2)
        with self.assertRaisesRegex(ValueError, 'PAYMENT_INTENT_CONFLICT'):
            self.prepare(sub['id'], dispatch_not_before=month_boundary(self.anchor, 12)-timedelta(hours=1))
        with self.assertRaisesRegex(ValueError, 'RENEWAL_CYCLE_CONFLICT'):
            self.prepare(sub['id'], cycle_index=2)

    def test_renewal_dispatch_pins_authorization_once_and_keeps_it_private(self):
        from .billing import claim_payment_dispatch, order_detail
        from .subscriptions import register_verified_payment_method
        method = register_verified_payment_method(self.account,channel='fixture',merchant_reference='fixture-shenzhen',
            fingerprint='c'*64,evidence_hash='d'*64)
        sub = self.activate(payment_method_id=method)
        attempt = self.prepare(sub['id'], notice_sent=True)
        row = PaymentAttempt.objects.get(pk=attempt['id'])
        self.assertIsNone(row.dispatch_authorization)
        self.assertFalse(claim_payment_dispatch(self.account,attempt['id'])['dispatch'])
        row.refresh_from_db()
        self.assertIsNone(row.dispatch_authorization)
        self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
        self.assertTrue(claim_payment_dispatch(self.account,attempt['id'])['dispatch'])
        row.refresh_from_db()
        expected = {'subscription_id':sub['id'],'payment_method_id':method,'mandate':self.mandate,
                    'authorization_version':0}
        self.assertEqual(row.dispatch_authorization,expected)
        self.assertNotIn('agreement_reference',json.dumps(order_detail(self.account,row.order_id)))
        cancel_subscription(self.account,self.user,sub['id'])
        self.assertFalse(claim_payment_dispatch(self.account,attempt['id'])['dispatch'])
        row.refresh_from_db()
        self.assertEqual(row.dispatch_authorization,expected)
        # Simulate a later binding revision without rewriting the in-flight fact.
        Subscription.objects.filter(pk=sub['id']).update(payment_method=None)
        from .subscriptions import payment_method_impact
        self.assertEqual(len(payment_method_impact(self.account,self.user,method)['unresolved_payments']),1)
        row.dispatch_authorization = None
        with self.assertRaisesRegex(ValueError,'IMMUTABLE_BILLING_SNAPSHOT'):
            row.save(update_fields=['dispatch_authorization'])

    def failed_renewal(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        sub = self.activate()
        attempt = self.prepare(sub['id'], notice_sent=True)
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        claim_payment_dispatch(self.account, attempt['id'])
        record_attempt_event(self.account, attempt['id'], 'decline', 'failed', source='provider_query', evidence_hash='c'*64)
        self.now.return_value = month_boundary(self.anchor, 12)
        return sub, attempt

    def test_renewal_failure_notification_is_not_a_receipt_or_duplicate(self):
        from .models import BillingNotification
        from .billing import record_attempt_event
        sub, attempt = self.failed_renewal()
        notice = BillingNotification.objects.get(type='renewal_failed')
        self.assertEqual(str(notice.subscription_id),sub['id'])
        self.assertIsNone(notice.amount_minor)
        self.assertEqual(notice.currency,'')
        record_attempt_event(self.account,attempt['id'],'another-decline','failed',
            source='verified_callback',evidence_hash='d'*64)
        self.assertEqual(BillingNotification.objects.filter(type='renewal_failed').count(),1)

    def test_renewal_receipt_is_classified_before_delivery_and_replays_once(self):
        from .models import BillingNotification
        from .billing import claim_payment_dispatch, record_attempt_event
        sub = self.activate()
        attempt = self.prepare(sub['id'], notice_sent=True)
        self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
        claim_payment_dispatch(self.account,attempt['id'])
        order_id = PaymentAttempt.objects.get(pk=attempt['id']).order_id
        values = dict(channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='renewal-notice',amount_minor=12345,currency='CNY',
            paid_at=timezone.now(),evidence_hash='b'*64)
        with patch('backend.commerce.billing.fulfill_paid_order',side_effect=ValueError('DELIVERY_FAILED')):
            with self.assertRaisesRegex(ValueError,'DELIVERY_FAILED'):
                record_confirmed_payment(self.account,order_id,**values)
        row = BillingNotification.objects.get(order_id=order_id)
        self.assertEqual(row.type,'renewal_payment_received')
        self.assertEqual(str(row.subscription_id),sub['id'])
        self.assertEqual(row.amount_minor,12345)
        self.assertEqual(Order.objects.get(pk=order_id).status,'paid_pending_entitlement')
        record_confirmed_payment(self.account,order_id,**values)
        record_confirmed_payment(self.account,order_id,**{**values,'transaction_reference':'renewal-duplicate'})
        record_attempt_event(self.account,attempt['id'],'stale-failure','failed',
            source='provider_query',evidence_hash='c'*64)
        self.assertEqual(BillingNotification.objects.filter(order_id=order_id).count(),1)
        self.assertFalse(BillingNotification.objects.filter(order_id=order_id,type='renewal_failed').exists())

    def test_actual_expiry_notices_are_atomic_per_period_not_account_shutdown(self):
        from django.db import transaction
        from .models import BillingNotification
        from .subscriptions import record_expired_notifications
        sub = self.activate()
        self.assertEqual(record_expired_notifications(self.account),0)
        self.now.return_value = month_boundary(self.anchor,1)
        with self.assertRaisesRegex(RuntimeError,'rollback'):
            with transaction.atomic():
                self.assertEqual(record_expired_notifications(self.account),1)
                raise RuntimeError('rollback')
        self.assertFalse(BillingNotification.objects.filter(type='credit_batch_expired').exists())
        self.assertEqual(record_expired_notifications(self.account),1)
        self.assertEqual(record_expired_notifications(self.account),0)
        self.now.return_value = month_boundary(self.anchor,12)
        self.assertEqual(record_expired_notifications(self.account),1)
        notice = BillingNotification.objects.get(type='service_period_ended')
        self.assertEqual(str(notice.subscription_id),sub['id'])
        self.assertEqual(notice.occurred_at,month_boundary(self.anchor,12))
        self.assertIsNone(notice.amount_minor)
        self.assertEqual(record_expired_notifications(self.account),0)
        self.assertEqual(CreditBatch.objects.count(),1)
        self.assertEqual(SubscriptionPeriod.objects.count(),1)

    def test_delivery_command_records_expiry_without_payment_or_repeat(self):
        from io import StringIO
        from django.core.management import call_command
        from .models import BillingNotification
        self.activate()
        self.now.return_value = month_boundary(self.anchor,12)
        output = StringIO()
        call_command('grant_subscription_credits',stdout=output)
        self.assertIn('payment attempts created: 0',output.getvalue())
        self.assertEqual(BillingNotification.objects.filter(type='credit_batch_expired').count(),12)
        self.assertEqual(BillingNotification.objects.filter(type='service_period_ended').count(),1)
        count = BillingNotification.objects.count()
        call_command('grant_subscription_credits',stdout=StringIO())
        self.assertEqual(BillingNotification.objects.count(),count)
        self.assertFalse(PaymentAttempt.objects.exists())
        self.assertEqual(PaymentFact.objects.count(),1)

    def recover(self, attempt, **changes):
        from .subscriptions import prepare_recovery_attempt
        values = dict(agreement_acceptance='fixture-1', expires_at=month_boundary(self.anchor,12)+timedelta(hours=1))
        values.update(changes)
        return prepare_recovery_attempt(self.account, self.user, attempt['id'], **values)

    def test_renewal_read_flags_extra_receipts_without_blocking_accepted_delivery(self):
        from django.db import transaction
        from .billing import claim_payment_dispatch, _persist_confirmed_payment
        from .subscriptions import fulfill_renewal_attempt
        sub=self.activate()
        attempt=self.prepare(sub['id'], notice_sent=True)
        self.now.return_value=month_boundary(self.anchor,12)-timedelta(hours=1)
        claim_payment_dispatch(self.account,attempt['id'])
        row=PaymentAttempt.objects.get(pk=attempt['id'])
        values=dict(channel='fixture',merchant_reference='fixture-shenzhen',amount_minor=12345,
            currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
        record_confirmed_payment(self.account,row.order_id,transaction_reference='normal-renewal',**values)
        self.assertEqual(list_subscriptions(self.account)[0]['renewal']['status'],'succeeded')
        client=Client();client.force_login(self.user)
        state=client.session;state['active_account_id']=str(self.account.pk);state.save()
        for label, changes in (('duplicate',{}),('amount',{'amount_minor':1}),('identity',{'merchant_reference':'other-fixture'})):
            with self.subTest(label=label):
                self.assertEqual(list_subscriptions(self.account)[0]['renewal']['status'],'succeeded')
                with transaction.atomic():
                    # The public receipt wrapper requires autocommit. Exercise
                    # its exact persistence path inside an isolated savepoint.
                    with transaction.atomic():
                        fact=_persist_confirmed_payment(self.account,row.order_id,transaction_reference=label,**{**values,**changes})
                        self.assertEqual(fact['disposition'],{'duplicate':'duplicate_payment_review',
                            'amount':'amount_mismatch_review','identity':'identity_mismatch_review'}[label])
                        data=client.get('/api/billing/subscriptions',HTTP_X_BILLING_ACCOUNT=str(self.account.pk)).json()['items'][0]['renewal']
                        self.assertEqual(data['status'],'review')
                        self.assertTrue(data['query_original_payment'])
                        self.assertFalse(data['manual_payment_eligible'])
                        self.assertFalse(data['new_subscription_payment_eligible'])
                        transaction.set_rollback(True)
                self.assertEqual(list_subscriptions(self.account)[0]['renewal']['status'],'succeeded')
        record_confirmed_payment(self.account,row.order_id,transaction_reference='delivery-duplicate',**values)
        self.now.return_value=month_boundary(self.anchor,12)+timedelta(days=4)
        from .subscriptions import close_expired_renewals
        result=list_subscriptions(self.account)[0]['renewal']
        self.assertEqual(result['status'],'review')
        self.assertTrue(result['query_original_payment'])
        self.assertFalse(result['reauthorization_required'])
        self.assertFalse(result['close_future_auto_renewal'])
        self.assertEqual(close_expired_renewals(self.account),0)
        self.assertTrue(Subscription.objects.get(pk=sub['id']).auto_renew)
        row.refresh_from_db();self.assertFalse(row.needs_reconciliation)
        first=fulfill_renewal_attempt(self.account,row.pk)
        self.assertEqual(first,fulfill_renewal_attempt(self.account,row.pk))
        self.assertEqual(SubscriptionPeriod.objects.filter(subscription_id=sub['id']).count(),2)
        self.assertEqual(PaymentAttempt.objects.count(),1)

    def test_recovery_extra_receipts_remain_visible_after_single_delivery(self):
        from .billing import claim_payment_dispatch
        from .subscriptions import activate_recovery_subscription, register_verified_payment_method
        sub,original=self.failed_renewal()
        recovery=self.recover(original)
        claim_payment_dispatch(self.account,recovery['id'])
        row=PaymentAttempt.objects.get(pk=recovery['id'])
        values=dict(channel='fixture',merchant_reference='fixture-shenzhen',amount_minor=12345,
            currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
        record_confirmed_payment(self.account,row.order_id,transaction_reference='recovery-accepted',**values)
        self.assertEqual(list_subscriptions(self.account)[0]['renewal']['status'],'succeeded')
        record_confirmed_payment(self.account,row.order_id,transaction_reference='recovery-duplicate',**values)
        result=list_subscriptions(self.account)[0]['renewal']
        self.assertEqual(result['status'],'review')
        self.assertTrue(result['query_original_payment'])
        self.now.return_value=month_boundary(self.anchor,12)+timedelta(days=4)
        from .subscriptions import close_expired_renewals
        result=list_subscriptions(self.account)[0]['renewal']
        self.assertFalse(result['reauthorization_required'])
        self.assertFalse(result['close_future_auto_renewal'])
        self.assertEqual(close_expired_renewals(self.account),0)
        self.assertTrue(Subscription.objects.get(pk=sub['id']).auto_renew)
        cancel_subscription(self.account,self.user,sub['id'])
        confirm_cancellation(self.account,sub['id'],agreement_reference=self.mandate['agreement_reference'],evidence_hash='c'*64)
        method=register_verified_payment_method(self.account,channel='fixture',merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        args=dict(activated_at=timezone.now(),mandate={**self.mandate,'agreement_reference':'recovery-new'},payment_method_id=method)
        new=activate_recovery_subscription(self.account,row.pk,**args)
        self.assertEqual(new,activate_recovery_subscription(self.account,row.pk,**args))
        old=next(item for item in list_subscriptions(self.account) if item['id']==sub['id'])
        self.assertEqual(old['renewal']['status'],'review')
        self.assertTrue(old['renewal']['query_original_payment'])
        self.assertFalse(old['renewal']['manual_payment_eligible'])
        self.assertFalse(old['renewal']['reauthorization_required'])
        self.assertFalse(old['renewal']['close_future_auto_renewal'])
        self.assertEqual(close_expired_renewals(self.account),0)
        self.assertTrue(Subscription.objects.get(pk=new['id']).auto_renew)
        self.assertEqual(CreditBatch.objects.filter(subscription_period__subscription_id=new['id']).count(),1)
        self.assertEqual(PaymentAttempt.objects.count(),2)

    def test_manual_recovery_has_unique_order_and_requires_terms(self):
        sub, original = self.failed_renewal()
        with self.assertRaisesRegex(ValueError, 'TERMS_MISMATCH'):
            self.recover(original, agreement_acceptance='not-accepted')
        self.assertEqual(Order.objects.count(),2)
        recovery = self.recover(original)
        self.assertEqual(recovery,self.recover(original))
        self.assertEqual(Order.objects.count(),3)
        self.assertEqual(PaymentAttempt.objects.get(pk=recovery['id']).recovery_of_id, PaymentAttempt.objects.get(pk=original['id']).pk)
        with self.assertRaisesRegex(ValueError,'PAYMENT_INTENT_CONFLICT'):
            self.recover(original,expires_at=month_boundary(self.anchor,12)+timedelta(hours=2))
        self.assertFalse(list_subscriptions(self.account)[0]['renewal']['manual_payment_eligible'])

    def test_recovery_rechecks_original_money_before_dispatch(self):
        from .billing import claim_payment_dispatch
        sub, original = self.failed_renewal()
        recovery = self.recover(original)
        row = PaymentAttempt.objects.get(pk=original['id'])
        record_confirmed_payment(self.account,row.order_id,channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='late-original',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
        self.assertFalse(claim_payment_dispatch(self.account,recovery['id'])['dispatch'])
        self.assertIsNone(PaymentAttempt.objects.get(pk=recovery['id']).dispatched_at)
        self.assertEqual(PaymentFact.objects.count(),2)

    def test_recovery_claims_once_and_unknown_never_retries(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        sub, original = self.failed_renewal()
        recovery = self.recover(original)
        self.assertTrue(claim_payment_dispatch(self.account,recovery['id'])['dispatch'])
        record_attempt_event(self.account,recovery['id'],'timeout','unknown',source='local',evidence_hash='')
        self.assertFalse(claim_payment_dispatch(self.account,recovery['id'])['dispatch'])
        self.assertEqual(self.recover(original)['id'],recovery['id'])
        result = list_subscriptions(self.account)[0]['renewal']
        self.assertTrue(result['query_original_payment'])
        self.assertFalse(result['manual_payment_eligible'])

    def test_recovery_outside_window_rejected(self):
        sub, original = self.failed_renewal()
        self.now.return_value = month_boundary(self.anchor,12)+timedelta(days=3)
        with self.assertRaisesRegex(ValueError,'RECOVERY_NOT_ELIGIBLE'):
            self.recover(original)
        self.assertEqual(Order.objects.count(),2)

    def test_dispatched_expiry_is_not_manual_recovery_eligible(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        sub = self.activate()
        original = self.prepare(sub['id'], notice_sent=True)
        self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
        claim_payment_dispatch(self.account,original['id'])
        record_attempt_event(self.account,original['id'],'expired','expired',source='provider_query',evidence_hash='c'*64)
        self.now.return_value = month_boundary(self.anchor,12)
        self.assertFalse(list_subscriptions(self.account)[0]['renewal']['manual_payment_eligible'])
        with self.assertRaisesRegex(ValueError,'RECOVERY_NOT_ELIGIBLE'):
            self.recover(original)

    @override_settings(BILLING_TERMS_DOCUMENTS={'fixture-1':{'approved':True,
        'zh':{'title':'测试','paragraphs':['测试协议']},'en':{'title':'Fixture','paragraphs':['Fixture terms']}}})
    def test_cross_channel_failed_renewal_recovery_preserves_terms_and_delivers_once(self):
        from .billing import claim_payment_dispatch, record_attempt_event, purchase_terms
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization, activate_recovery_subscription
        digest = purchase_terms('fixture-1')['digest']
        quote = create_quote(self.account,self.user,self.product.pk,1,timezone.now()+timedelta(minutes=5),channel='fixture',buyer_region='CN',terms_digest=digest)
        order = create_order(self.account,self.user,{'quote_id':quote['id'],'agreement_acceptance':'fixture-1'},'public-recovery-initial')
        record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',transaction_reference='public-recovery-initial',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
        sub = self.activate(order['id'])
        method = register_verified_payment_method(self.account,channel='replacement',merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        mandate = {**self.mandate,'channel':'replacement','agreement_reference':'replacement'}
        channels = {**FIXTURE_CHANNELS,'replacement':dict(FIXTURE_CHANNELS['fixture'])}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            original = self.prepare(sub['id'], notice_sent=True)
            replace_subscription_authorization(self.account,self.user,sub['id'],payment_method_id=method,
                mandate=mandate,expected_version=0,request_key='replacement')
            self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
            claim_payment_dispatch(self.account,original['id'])
            record_attempt_event(self.account,original['id'],'declined','failed',source='provider_query',evidence_hash='c'*64)
            self.now.return_value = month_boundary(self.anchor,12)
            recovery = self.recover(original)
            self.assertEqual(self.recover(original),recovery)
            row = PaymentAttempt.objects.get(pk=recovery['id'])
            original_snapshot = Order.objects.get(pk=order['id']).snapshot
            self.assertEqual(row.order.snapshot,{**original_snapshot,'channel':'replacement'})
            self.assertEqual(row.order.snapshot['terms_document_digest'],digest)
            self.assertTrue(claim_payment_dispatch(self.account,recovery['id'])['dispatch'])
            self.assertFalse(claim_payment_dispatch(self.account,recovery['id'])['dispatch'])
            record_confirmed_payment(self.account,row.order_id,channel='replacement',merchant_reference='fixture-shenzhen',transaction_reference='recovery-new-channel',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
            cancel_subscription(self.account,self.user,sub['id'])
            confirm_cancellation(self.account,sub['id'],agreement_reference='replacement',evidence_hash='c'*64)
            values = dict(activated_at=timezone.now(),mandate={**mandate,'agreement_reference':'recovery-fresh'},payment_method_id=method)
            new = activate_recovery_subscription(self.account,recovery['id'],**values)
            self.assertEqual(activate_recovery_subscription(self.account,recovery['id'],**values),new)
            self.assertEqual(CreditBatch.objects.filter(subscription_period__subscription_id=new['id']).count(),1)
            self.assertEqual(Subscription.objects.filter(auto_renew=True).count(),1)
            self.assertEqual(Order.objects.get(pk=row.order_id).status,'fulfilled')
            self.assertEqual(Order.objects.get(pk=order['id']).snapshot,original_snapshot)

    def test_old_unknown_or_replaced_authorization_cannot_create_recovery(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization
        sub = self.activate()
        original = self.prepare(sub['id'], notice_sent=True)
        self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
        claim_payment_dispatch(self.account,original['id'])
        record_attempt_event(self.account,original['id'],'unknown','unknown',source='local',evidence_hash='')
        method = register_verified_payment_method(self.account,channel='fixture',merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        replace_subscription_authorization(self.account,self.user,sub['id'],payment_method_id=method,
            mandate={**self.mandate,'agreement_reference':'replacement'},expected_version=0,request_key='replacement')
        self.now.return_value = month_boundary(self.anchor,12)
        with self.assertRaisesRegex(ValueError,'RECOVERY_NOT_ELIGIBLE'):
            self.recover(original)
        record_attempt_event(self.account,original['id'],'declined','failed',source='provider_query',evidence_hash='c'*64)
        with self.assertRaisesRegex(ValueError,'RECOVERY_NOT_ELIGIBLE'):
            self.recover(original)
        self.assertEqual(PaymentAttempt.objects.count(),1)
        self.assertEqual(Order.objects.count(),2)

    def test_recovery_activation_uses_new_confirmed_authorization_and_actual_start(self):
        from .billing import claim_payment_dispatch
        from .subscriptions import activate_recovery_subscription
        sub, original = self.failed_renewal()
        recovery = self.recover(original)
        claim_payment_dispatch(self.account,recovery['id'])
        row = PaymentAttempt.objects.get(pk=recovery['id'])
        record_confirmed_payment(self.account,row.order_id,channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='recovery-paid',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
        self.now.return_value += timedelta(minutes=10)
        activated_at = timezone.now()
        new_mandate = {**self.mandate,'agreement_reference':'fixture-reauthorized','evidence_hash':'d'*64}
        with self.assertRaisesRegex(ValueError,'RECOVERY_AUTHORIZATION_REQUIRED'):
            activate_recovery_subscription(self.account,recovery['id'],activated_at=activated_at,mandate=new_mandate)
        self.assertEqual(PaymentFact.objects.count(),2)
        cancel_subscription(self.account,self.user,sub['id'])
        confirm_cancellation(self.account,sub['id'],agreement_reference=self.mandate['agreement_reference'],evidence_hash='c'*64)
        with self.assertRaisesRegex(ValueError,'RECOVERY_AUTHORIZATION_REQUIRED'):
            activate_recovery_subscription(self.account,recovery['id'],activated_at=activated_at,mandate=self.mandate)
        from .subscriptions import register_verified_payment_method
        method = register_verified_payment_method(self.account,channel='fixture',merchant_reference='fixture-shenzhen',
            fingerprint='c'*64,evidence_hash='d'*64)
        with self.assertRaisesRegex(ValueError,'PAYMENT_METHOD_BINDING_REQUIRED'):
            activate_recovery_subscription(self.account,recovery['id'],activated_at=activated_at,mandate=new_mandate)
        result = activate_recovery_subscription(self.account,recovery['id'],activated_at=activated_at,mandate=new_mandate,payment_method_id=method)
        self.assertEqual(result,activate_recovery_subscription(self.account,recovery['id'],activated_at=activated_at,mandate=new_mandate,payment_method_id=method))
        self.assertEqual(str(Subscription.objects.get(pk=result['id']).payment_method_id),method)
        self.assertEqual(Subscription.objects.get(pk=result['id']).original_anchor,activated_at)
        self.assertEqual(Subscription.objects.get(pk=sub['id']).original_anchor,self.anchor)
        self.assertEqual(Subscription.objects.filter(auto_renew=True).count(),1)
        self.assertEqual(CreditBatch.objects.filter(subscription_period__subscription_id=result['id']).count(),1)
        self.assertEqual(credits_summary(self.account)['available'],'17.25')
        self.assertEqual(Order.objects.get(pk=row.order_id).status,'fulfilled')
        old = next(item for item in list_subscriptions(self.account) if item['id'] == sub['id'])
        self.assertIsNone(old['renewal'])
        original_row = PaymentAttempt.objects.get(pk=original['id'])
        record_confirmed_payment(self.account,original_row.order_id,channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='original-paid-after-recovery',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='e'*64)
        old = next(item for item in list_subscriptions(self.account) if item['id'] == sub['id'])
        self.assertEqual(old['renewal']['status'],'review')
        self.assertTrue(old['renewal']['query_original_payment'])
        self.assertFalse(old['renewal']['manual_payment_eligible'])
        self.assertEqual(CreditBatch.objects.filter(subscription_period__subscription_id=result['id']).count(),1)

    def test_recovery_cancel_before_claim_and_cross_account(self):
        from .billing import claim_payment_dispatch
        from .subscriptions import prepare_recovery_attempt
        sub, original = self.failed_renewal()
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            prepare_recovery_attempt(self.other,self.other_user,original['id'],agreement_acceptance='fixture-1',expires_at=timezone.now()+timedelta(hours=1))
        recovery = self.recover(original)
        cancel_subscription(self.account,self.user,sub['id'])
        self.assertFalse(claim_payment_dispatch(self.account,recovery['id'])['dispatch'])
        self.assertIsNone(PaymentAttempt.objects.get(pk=recovery['id']).dispatched_at)

    def test_unknown_original_and_recovery_chaining_never_create_orders(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        sub = self.activate()
        original = self.prepare(sub['id'], notice_sent=True)
        self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
        claim_payment_dispatch(self.account,original['id'])
        record_attempt_event(self.account,original['id'],'timeout','unknown',source='local',evidence_hash='')
        self.now.return_value = month_boundary(self.anchor,12)
        with self.assertRaisesRegex(ValueError,'RECOVERY_NOT_ELIGIBLE'):
            self.recover(original)
        self.assertEqual(Order.objects.count(),2)
        record_attempt_event(self.account,original['id'],'resolved-failed','failed',source='provider_query',evidence_hash='d'*64)
        recovery = self.recover(original)
        with self.assertRaisesRegex(ValueError,'RECOVERY_NOT_ELIGIBLE'):
            self.recover(recovery)
        self.assertEqual(Order.objects.count(),3)

    def test_recovery_sales_gate_and_unstarted_expiry(self):
        from .billing import claim_payment_dispatch
        _, original = self.failed_renewal()
        with override_settings(BILLING_SALES_ENABLED=False):
            with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
                self.recover(original)
        self.assertEqual(Order.objects.count(),2)
        recovery = self.recover(original)
        with override_settings(BILLING_SALES_ENABLED=False):
            with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
                claim_payment_dispatch(self.account,recovery['id'])
        self.now.return_value += timedelta(hours=1)
        result = claim_payment_dispatch(self.account,recovery['id'])
        self.assertFalse(result['dispatch'])
        self.assertEqual(result['status'],'expired')
        self.assertEqual(PaymentFact.objects.count(),1)

    def test_cancel_before_prepare_or_dispatch_never_claims(self):
        from .billing import claim_payment_dispatch
        sub = self.activate()
        attempt = self.prepare(sub['id'])
        cancel_subscription(self.account, self.user, sub['id'])
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        row = PaymentAttempt.objects.get(pk=attempt['id'])
        self.assertEqual(row.state, 'cancelled')
        self.assertIsNone(row.dispatched_at)
        self.assertEqual(row.order.status, 'closed')
        self.assertEqual(self.prepare(sub['id'])['id'], attempt['id'])
        self.assertEqual(Order.objects.count(), 2)

    def test_cancelled_subscription_cannot_create_sage_charge(self):
        sub = self.activate()
        cancel_subscription(self.account, self.user, sub['id'])
        with self.assertRaisesRegex(ValueError, 'RENEWAL_NOT_AUTHORIZED'):
            self.prepare(sub['id'])
        self.assertEqual(Order.objects.count(), 1)

    def test_provider_executor_is_not_charged_by_sage(self):
        self.mandate['executor'] = 'provider'
        sub = self.activate()
        with self.assertRaisesRegex(ValueError, 'RENEWAL_EXECUTOR_CONFLICT'):
            self.prepare(sub['id'])
        self.assertEqual(PaymentAttempt.objects.count(), 0)

    def test_expiry_and_confirmed_failure_never_retry(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        sub = self.activate()
        attempt = self.prepare(sub['id'], notice_sent=True)
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        claim_payment_dispatch(self.account, attempt['id'])
        record_attempt_event(self.account, attempt['id'], 'declined', 'failed', source='provider_query', evidence_hash='c'*64)
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        self.assertEqual(self.prepare(sub['id'], notice_sent=True)['id'], attempt['id'])
        self.now.return_value = month_boundary(self.anchor, 12)
        self.assertEqual(list_subscriptions(self.account)[0]['status'], 'expired')
        self.assertEqual(SubscriptionPeriod.objects.count(), 1)
        self.assertEqual(PaymentFact.objects.count(), 1)

    def test_cross_account_and_public_reserved_key_are_rejected(self):
        from .subscriptions import prepare_renewal_attempt
        sub = self.activate()
        with self.assertRaisesRegex(ValueError, 'NOT_FOUND'):
            prepare_renewal_attempt(self.other, sub['id'], cycle_index=1,
                dispatch_not_before=self.anchor, expires_at=month_boundary(self.anchor, 12))
        client = Client()
        client.force_login(self.user)
        session = client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        for key in (f"renewal:{sub['id']}:1",f"recovery:{sub['id']}"):
            response = client.post('/api/billing/orders', json.dumps({'quote_id': str(self.product.pk),
                'agreement_acceptance': 'fixture-1'}), content_type='application/json',
                HTTP_X_BILLING_ACCOUNT=str(self.account.pk), HTTP_IDEMPOTENCY_KEY=key)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()['error']['code'], 'INVALID_IDEMPOTENCY_KEY')
        self.assertEqual(Order.objects.count(), 1)

    def test_window_and_unknown_do_not_create_another_charge(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        sub = self.activate()
        attempt = self.prepare(sub['id'], notice_sent=True)
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        self.assertTrue(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        record_attempt_event(self.account, attempt['id'], 'timeout', 'unknown', source='local', evidence_hash='')
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        self.assertEqual(self.prepare(sub['id'], notice_sent=True)['id'], attempt['id'])
        self.assertEqual(PaymentAttempt.objects.count(), 1)

    def test_failed_renewal_window_has_no_free_service_and_closes_once(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        from .subscriptions import close_expired_renewals
        sub = self.activate()
        attempt = self.prepare(sub['id'], notice_sent=True)
        end = month_boundary(self.anchor, 12)
        self.now.return_value = end-timedelta(hours=1)
        claim_payment_dispatch(self.account, attempt['id'])
        record_attempt_event(self.account, attempt['id'], 'failure', 'failed', source='provider_query', evidence_hash='c'*64)
        before = list_subscriptions(self.account)[0]
        self.assertEqual(before['status'], 'active')
        self.assertFalse(before['renewal']['manual_payment_eligible'])
        self.now.return_value = end
        expired = list_subscriptions(self.account)[0]
        self.assertEqual(expired['status'], 'expired')
        self.assertTrue(expired['renewal']['manual_payment_eligible'])
        self.assertFalse(expired['capabilities']['manual_payment'])
        self.assertEqual(credits_summary(self.account)['available'], '0')
        self.assertEqual(close_expired_renewals(self.account), 0)
        self.now.return_value = end+timedelta(days=3)
        self.assertEqual(close_expired_renewals(self.account), 1)
        self.assertEqual(close_expired_renewals(self.account), 0)
        closed = list_subscriptions(self.account)[0]
        self.assertFalse(closed['auto_renew'])
        self.assertFalse(closed['renewal']['manual_payment_eligible'])
        self.assertTrue(closed['renewal']['reauthorization_required'])
        self.assertEqual(closed['cancellation_state'], 'processing')
        self.assertEqual(AuditEvent.objects.filter(action='subscription.recovery_expired').count(), 1)

    def test_unknown_receipt_blocks_new_payment_even_after_recovery_deadline(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        from .subscriptions import close_expired_renewals
        sub = self.activate()
        attempt = self.prepare(sub['id'], notice_sent=True)
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        claim_payment_dispatch(self.account, attempt['id'])
        record_attempt_event(self.account, attempt['id'], 'timeout', 'unknown', source='local', evidence_hash='')
        self.now.return_value = month_boundary(self.anchor, 12)+timedelta(days=3)
        close_expired_renewals(self.account)
        result = list_subscriptions(self.account)[0]['renewal']
        self.assertTrue(result['query_original_payment'])
        self.assertFalse(result['manual_payment_eligible'])
        self.assertFalse(result['new_subscription_payment_eligible'])
        self.assertEqual(Order.objects.count(), 2)

    def test_missed_scheduler_still_closes_authorization_without_inventing_failure(self):
        from .subscriptions import close_expired_renewals
        self.activate()
        self.now.return_value = month_boundary(self.anchor, 12)+timedelta(days=3)
        self.assertEqual(close_expired_renewals(self.account), 1)
        self.assertEqual(close_expired_renewals(self.account), 0)
        result = list_subscriptions(self.account)[0]
        self.assertFalse(result['auto_renew'])
        self.assertEqual(result['renewal']['status'], 'missing')
        self.assertFalse(result['renewal']['new_subscription_payment_eligible'])
        self.assertFalse(result['renewal']['manual_payment_eligible'])
        self.assertEqual(PaymentAttempt.objects.count(), 0)

    def test_inflight_payment_after_cancel_delivers_once(self):
        from .billing import claim_payment_dispatch
        from .subscriptions import fulfill_renewal_attempt
        sub = self.activate()
        attempt = self.prepare(sub['id'], notice_sent=True)
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        self.assertTrue(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        cancel_subscription(self.account, self.user, sub['id'])
        row = PaymentAttempt.objects.get(pk=attempt['id'])
        record_confirmed_payment(self.account, row.order_id, channel='fixture', merchant_reference='fixture-shenzhen',
            transaction_reference='renewal', amount_minor=12345, currency='CNY', paid_at=timezone.now(), evidence_hash='b'*64)
        result = fulfill_renewal_attempt(self.account, attempt['id'])
        self.assertEqual(result, fulfill_renewal_attempt(self.account, attempt['id']))
        self.assertFalse(result['auto_renew'])
        self.assertEqual(SubscriptionPeriod.objects.count(), 2)
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])


@override_settings(BILLING_SALES_ENABLED=True, BILLING_RENEWAL_CHARGING_ENABLED=True,
                   BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
class SubscriptionTests(TransactionTestCase):
    def setUp(self):
        self.user, self.account = fixture_account()
        self.other_user, self.other = fixture_account('other')
        Membership.objects.create(account=self.account, user=self.user, role='owner')
        Membership.objects.create(account=self.other, user=self.other_user, role='owner')
        self.anchor = datetime(2028, 1, 31, 1, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.clock = patch('django.utils.timezone.now', return_value=self.anchor)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.product = ProductVersion.objects.create(code='fixture_plan', version=1, kind='subscription',
            audience='personal', billing_cycle='annual', unit_amount_minor=12345, credits_quantity='17.25',
            entitlements={'fixture': True}, entitlement_version='fixture-1', credit_rule_version='fixture-1',
            terms_version='fixture-1', state='published', effective_at=self.anchor-timedelta(days=1))
        self.mandate = dict(channel='fixture', merchant_reference='fixture-shenzhen',
            agreement_reference='fixture-agreement', evidence_hash='a'*64, executor='sage', automatic_retry=False)
        self.sequence = 0

    def paid(self):
        self.sequence += 1
        quote = create_quote(self.account, self.user, self.product.pk, 1, timezone.now()+timedelta(minutes=5),
                             channel='fixture', buyer_region='CN')
        order = create_order(self.account, self.user, {'quote_id':quote['id'], 'agreement_acceptance':'fixture-1'}, str(self.sequence))
        record_confirmed_payment(self.account, order['id'], channel='fixture', merchant_reference='fixture-shenzhen',
            transaction_reference=str(self.sequence), amount_minor=12345, currency='CNY', paid_at=timezone.now(), evidence_hash='b'*64)
        return order['id']

    def activate(self, order_id=None, **kwargs):
        return activate_subscription(self.account, order_id or self.paid(), activated_at=self.anchor,
            billing_timezone='Asia/Shanghai', calendar_rule_version='fixture-calendar-v1', mandate=self.mandate, **kwargs)

    def business_paid(self, resource_mode, billing_cycle='annual'):
        """shared_only builds a historical order, never a new catalog purchase."""
        from backend.models import BillingAccount
        self.account = BillingAccount.objects.create(kind='business',name='fixture-business')
        Membership.objects.create(account=self.account,user=self.user,role='owner')
        self.product = ProductVersion.objects.create(code='fixture_business',version=1 if billing_cycle == 'annual' else 2,kind='subscription',
            audience='business',billing_cycle=billing_cycle,minimum_seats=2,unit_amount_minor=12345,
            credits_quantity='0' if resource_mode == 'shared_only' else '17.25',
            entitlements={'resource_mode':resource_mode},entitlement_version='fixture-1',credit_rule_version='fixture-1',
            terms_version='fixture-1',state='published',effective_at=self.anchor-timedelta(days=1))
        if resource_mode == 'shared_only':
            from .models import Quote
            snapshot={name:getattr(self.product,name) for name in ('code','version','kind','audience','billing_cycle',
                'currency','unit_amount_minor','credits_quantity','entitlements','entitlement_version',
                'credit_rule_version','terms_version')}
            snapshot.update(product_id=str(self.product.pk),seat_quantity=2,amount_minor=24690,
                channel='fixture',buyer_region='CN',merchant_entity='fixture-shenzhen')
            quote=Quote.objects.create(account=self.account,product=self.product,snapshot=snapshot,
                amount_minor=24690,currency='CNY',expires_at=self.anchor+timedelta(minutes=5))
            order={'id':str(Order.objects.create(account=self.account,created_by=self.user,quote=quote,
                idempotency_key='business-first',request_hash='historical-fixture',snapshot=snapshot,
                amount_minor=24690,currency='CNY').pk)}
        else:
            quote = create_quote(self.account,self.user,self.product.pk,2,self.anchor+timedelta(minutes=5),channel='fixture',buyer_region='CN')
            order = create_order(self.account,self.user,{'quote_id':quote['id'],'agreement_acceptance':'fixture-1'},'business-first')
        record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='business-paid' if billing_cycle == 'annual' else 'business-paid-monthly',amount_minor=24690,currency='CNY',paid_at=self.anchor,evidence_hash='b'*64)
        return order['id']

    def test_historical_zero_credit_enterprise_has_no_next_grant_for_paid_periods(self):
        for cycle in ('annual','monthly'):
            with self.subTest(cycle=cycle):
                sub=self.activate(self.business_paid('shared_only',cycle))
                self.assertIsNone(sub['next_credit_grant_at'])
                client=Client();client.force_login(self.user)
                state=client.session;state['active_account_id']=str(self.account.pk);state.save()
                response=client.get('/api/billing/subscriptions',HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
                self.assertEqual(response.status_code,200)
                self.assertIsNone(response.json()['items'][0]['next_credit_grant_at'])

    def test_positive_historical_credit_grants_keep_anchor_after_cancellation(self):
        sub=self.activate(self.business_paid('seat_included'))
        # Withdrawing the current catalog must not replace the paid period terms.
        ProductVersion.objects.filter(pk=self.product.pk).update(state='withdrawn')
        self.now.return_value=month_boundary(self.anchor,1)
        cancel_subscription(self.account,self.user,sub['id'])
        current=list_subscriptions(self.account)[0]
        self.assertEqual(current['next_credit_grant_at'],month_boundary(self.anchor,2).isoformat())
        self.assertEqual(current['cancellation_state'],'processing')
        self.now.return_value=month_boundary(self.anchor,12)
        self.assertIsNone(list_subscriptions(self.account)[0]['next_credit_grant_at'])

    def test_unknown_period_credit_quantity_is_not_treated_as_zero(self):
        sub=self.activate(self.business_paid('shared_only'))
        period=SubscriptionPeriod.objects.get(subscription_id=sub['id'])
        for value in (None,True,'unknown','-1'):
            with self.subTest(value=value):
                SubscriptionPeriod.objects.filter(pk=period.pk).update(snapshot={**period.snapshot,'credits_quantity':value})
                with self.assertRaisesMessage(ValueError,'INVALID_CREDITS'):
                    list_subscriptions(self.account)

    def test_annual_refund_scope_uses_original_service_month_boundaries(self):
        from .billing import credit_refund_assessment
        sub = Subscription.objects.get(pk=self.activate()['id'])
        for instant, current, count in (
            (self.anchor, 0, 11),
            (month_boundary(self.anchor,1)-timedelta(microseconds=1),0,11),
            (month_boundary(self.anchor,1),1,10),
            (month_boundary(self.anchor,2),2,9),
            (month_boundary(self.anchor,11),11,0)):
            with self.subTest(instant=instant):
                self.now.return_value = instant
                value = credit_refund_assessment(self.account,sub.initial_order_id)
                self.assertEqual(value['status'],'scope_ready')
                self.assertIs(value['can_submit'],False)
                self.assertEqual(value['future_month_count'],count)
                self.assertEqual(value['current_service_month'],{
                    'starts_at':month_boundary(self.anchor,current).isoformat(),
                    'ends_at':month_boundary(self.anchor,current+1).isoformat()})
                self.assertEqual(value['future_service_months'],[{
                    'starts_at':month_boundary(self.anchor,i).isoformat(),
                    'ends_at':month_boundary(self.anchor,i+1).isoformat()} for i in range(current+1,12)])
                self.assertFalse({'reference_minor','paid_minor','mandate','evidence_hash','channel'} & value.keys())
        for instant, reason in ((self.anchor-timedelta(microseconds=1),'annual_service_not_started'),
                                (month_boundary(self.anchor,12),'annual_service_ended')):
            self.now.return_value = instant
            value=credit_refund_assessment(self.account,sub.initial_order_id)
            self.assertEqual(value['reason'],reason)
            self.assertNotIn('future_month_count',value)

    def test_annual_refund_scope_rejects_incomplete_or_complex_sources(self):
        from .billing import credit_refund_assessment
        from .models import Quote
        sub=Subscription.objects.get(pk=self.activate()['id'])
        period=sub.periods.get()
        order=sub.initial_order
        SubscriptionPeriod.objects.filter(pk=period.pk).update(granted_months=7)
        self.assertEqual(credit_refund_assessment(self.account,order.pk)['future_month_count'],11)
        Subscription.objects.filter(pk=sub.pk).update(billing_timezone='invalid-zone')
        self.assertEqual(credit_refund_assessment(self.account,order.pk)['reason'],'annual_source_review_required')
        Subscription.objects.filter(pk=sub.pk).update(billing_timezone='Asia/Shanghai')
        for change in ({'ends_at':period.ends_at-timedelta(days=1)},
                       {'cycle_index':1}, {'snapshot':{**period.snapshot,'offset_minor':1}}):
            with self.subTest(change=change):
                SubscriptionPeriod.objects.filter(pk=period.pk).update(**change)
                self.assertEqual(credit_refund_assessment(self.account,order.pk)['reason'],'annual_source_review_required')
                SubscriptionPeriod.objects.filter(pk=period.pk).update(ends_at=period.ends_at,
                    cycle_index=period.cycle_index,snapshot=period.snapshot)
        for change in ({'terms_document_digest':'z'*64},{'terms_version':''},{'code':'not-the-source'}):
            with self.subTest(change=change):
                altered={**order.snapshot,**change}
                for model, pk in ((Order,order.pk),(Quote,order.quote_id),(SubscriptionPeriod,period.pk)):
                    model.objects.filter(pk=pk).update(snapshot=altered)
                self.assertEqual(credit_refund_assessment(self.account,order.pk)['reason'],'annual_source_review_required')
        altered={**order.snapshot,'offset_minor':1}
        for model, pk in ((Order,order.pk),(Quote,order.quote_id),(SubscriptionPeriod,period.pk)):
            model.objects.filter(pk=pk).update(snapshot=altered)
        self.assertEqual(credit_refund_assessment(self.account,order.pk)['reason'],'annual_source_review_required')

    def test_annual_refund_scope_preserves_digest_and_refuses_multiple_periods(self):
        from .billing import credit_refund_assessment
        from .models import Quote
        sub=Subscription.objects.get(pk=self.activate()['id'])
        order=sub.initial_order
        period=sub.periods.get()
        snapshot={**order.snapshot,'terms_document_digest':'e'*64}
        for model, pk in ((Order,order.pk),(Quote,order.quote_id),(SubscriptionPeriod,period.pk)):
            model.objects.filter(pk=pk).update(snapshot=snapshot)
        self.assertEqual(credit_refund_assessment(self.account,order.pk)['status'],'scope_ready')
        self.assertEqual(Order.objects.get(pk=order.pk).snapshot,snapshot)
        second=Order.objects.get(pk=self.paid())
        SubscriptionPeriod.objects.create(subscription=sub,order=second,cycle_index=1,
            starts_at=period.ends_at,ends_at=month_boundary(self.anchor,24),snapshot=second.snapshot)
        self.assertEqual(credit_refund_assessment(self.account,order.pk)['reason'],'annual_source_review_required')
        self.assertEqual(credit_refund_assessment(self.account,second.pk)['reason'],'payment_not_fulfilled')

    def test_annual_refund_scope_http_is_account_scoped_and_read_only(self):
        from django.test.utils import CaptureQueriesContext
        sub=Subscription.objects.get(pk=self.activate()['id'])
        path=f'/api/billing/orders/{sub.initial_order_id}/refund-assessment'
        client=Client()
        self.assertEqual(client.get(path).status_code,401)
        client.force_login(self.user)
        state=client.session;state['active_account_id']=str(self.account.pk);state.save()
        with CaptureQueriesContext(connection) as queries:
            first=client.get(path,HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
            second=client.get(path,HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json()['status'],'scope_ready')
        self.assertEqual(first.json(),second.json())
        for query in queries:
            self.assertNotRegex(query['sql'].lstrip().upper(),r'^(INSERT|UPDATE|DELETE)\b')
        self.assertEqual(client.post(path).status_code,405)
        Membership.objects.filter(account=self.account,user=self.user).update(role='member')
        self.assertEqual(client.get(path,HTTP_X_BILLING_ACCOUNT=str(self.account.pk)).status_code,403)
        client.force_login(self.other_user)
        state=client.session;state['active_account_id']=str(self.other.pk);state.save()
        self.assertEqual(client.get(path,HTTP_X_BILLING_ACCOUNT=str(self.other.pk)).status_code,404)

    def test_business_teams_monthly_grants_are_bound_to_paid_seats(self):
        from .models import PaidSeat
        order = self.business_paid('seat_included')
        result = self.activate(order)
        self.assertEqual(self.activate(order)['id'],result['id'])
        self.assertEqual(PaidSeat.objects.filter(subscription_id=result['id']).count(),2)
        self.assertEqual(CreditBatch.objects.count(),2)
        self.assertEqual(CreditBatch.objects.filter(seat__isnull=True).count(),0)
        self.assertEqual(credits_summary(self.account)['available'],'34.5')
        self.now.return_value = month_boundary(self.anchor,1)
        grant_due_credits(self.account)
        grant_due_credits(self.account)
        self.assertEqual(CreditBatch.objects.count(),4)
        self.assertEqual(credits_summary(self.account)['available'],'34.5')

    def test_historical_enterprise_seats_have_no_included_credit_grants(self):
        from .models import PaidSeat
        result = self.activate(self.business_paid('shared_only'))
        self.assertEqual(PaidSeat.objects.filter(subscription_id=result['id']).count(),2)
        self.assertEqual(CreditBatch.objects.count(),0)

    def test_business_unknown_resource_mode_does_not_deliver(self):
        order = self.business_paid('seat_included')
        # Simulate a legacy corrupt snapshot, bypassing the model deliberately:
        # the normal quote path now rejects this before any payment can start.
        record = Order.objects.get(pk=order)
        snapshot = dict(record.snapshot)
        snapshot['entitlements'] = {'resource_mode': 'unknown'}
        Order.objects.filter(pk=order).update(snapshot=snapshot)
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            self.activate(order)
        self.assertEqual(Subscription.objects.count(),0)

    def _seat_lifecycle_fixture(self, mode='seat_included', cycle='annual'):
        from django.contrib.auth import get_user_model
        from .models import PaidSeat
        from .subscriptions import assign_seat
        subscription = self.activate(self.business_paid(mode, cycle))
        members = [Membership.objects.create(account=self.account,
            user=get_user_model().objects.create_user(username=f'seat-{index}-{self.account.pk}'),role='member')
            for index in range(2)]
        seat = PaidSeat.objects.filter(subscription_id=subscription['id']).order_by('position').first()
        assign_seat(self.account,self.user,seat.pk,members[0].pk,expected_version=0,request_key='initial-seat')
        return subscription, seat, *members

    def _assert_used_seat_locks_until_service_month(self, cycle):
        from .billing import reserve_credits, settle_credits
        from .subscriptions import assign_seat, organization_summary
        subscription, seat, original, replacement = self._seat_lifecycle_fixture(cycle=cycle)
        debit = reserve_credits(self.account,'used-included','1',membership_id=original.pk)
        settle_credits(self.account,debit['id'],'1')
        month_end = month_boundary(self.anchor,1)
        self.now.return_value = self.anchor+timedelta(days=1)
        with self.assertRaisesMessage(ValueError,'SEAT_LOCKED_UNTIL_NEXT_SERVICE_MONTH'):
            assign_seat(self.account,self.user,seat.pk,replacement.pk,expected_version=1,request_key='too-early')
        assign_seat(self.account,self.user,seat.pk,None,expected_version=1,request_key='unbind-used')
        batch = CreditBatch.objects.get(seat=seat,service_month=0)
        self.assertEqual(batch.expires_at,timezone.now())
        summary = organization_summary(self.account)
        self.assertEqual((summary['available_seats'],summary['locked_seats']),(1,1))
        self.now.return_value = month_end-timedelta(microseconds=1)
        with self.assertRaisesMessage(ValueError,'SEAT_LOCKED_UNTIL_NEXT_SERVICE_MONTH'):
            assign_seat(self.account,self.user,seat.pk,replacement.pk,expected_version=2,request_key='still-too-early')
        if cycle == 'monthly':
            quote=create_quote(self.account,self.user,self.product.pk,2,month_end+timedelta(minutes=5),
                channel='fixture',buyer_region='CN')
            order=create_order(self.account,self.user,{'quote_id':quote['id'],'agreement_acceptance':'fixture-1'},'seat-renewal')
            record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',
                transaction_reference='seat-renewal',amount_minor=24690,currency='CNY',paid_at=timezone.now(),evidence_hash='c'*64)
            append_paid_period(self.account,subscription['id'],order['id'],cycle_index=1)
        self.now.return_value = month_end
        grant_due_credits(self.account)
        assign_seat(self.account,self.user,seat.pk,replacement.pk,expected_version=2,request_key='next-month')
        self.assertEqual(CreditBatch.objects.filter(seat=seat,expires_at__gt=month_end).count(),1)
        self.assertEqual(CreditBatch.objects.get(seat=seat,expires_at__gt=month_end).initial_quantity,'17.25')
        self.assertEqual(batch.entries.filter(kind='settle').get().quantity,'1')

    def test_used_annual_teams_seat_reopens_next_service_month(self):
        self._assert_used_seat_locks_until_service_month('annual')

    def test_used_monthly_teams_seat_reopens_at_renewed_service_month(self):
        self._assert_used_seat_locks_until_service_month('monthly')

    def test_removed_unused_seat_reuses_original_batch_without_regrant(self):
        from .subscriptions import assign_seat, remove_member, organization_summary
        _, seat, original, replacement = self._seat_lifecycle_fixture()
        before=list(CreditBatch.objects.filter(seat=seat).values_list('pk','initial_quantity','expires_at'))
        remove_member(self.account,self.user,original.pk,request_key='remove-unused')
        seat.refresh_from_db();original.refresh_from_db()
        self.assertFalse(original.is_active)
        self.assertIsNone(seat.membership_id)
        assign_seat(self.account,self.user,seat.pk,replacement.pk,expected_version=2,request_key='reuse-unused')
        self.assertEqual(before,list(CreditBatch.objects.filter(seat=seat).values_list('pk','initial_quantity','expires_at')))
        self.assertEqual(CreditEntry.objects.filter(batch__seat=seat,kind='grant').count(),1)
        row=next(item for item in organization_summary(self.account)['members'] if item['id']==original.pk)
        self.assertFalse(row['can_use_credits'])

    def test_removed_used_seat_ends_balance_and_preserves_month_lock(self):
        from .billing import reserve_credits, settle_credits
        from .subscriptions import assign_seat, remove_member, organization_summary
        _, seat, original, replacement = self._seat_lifecycle_fixture()
        debit=reserve_credits(self.account,'used-before-removal','1',membership_id=original.pk)
        settle_credits(self.account,debit['id'],'1')
        self.now.return_value=self.anchor+timedelta(days=1)
        with patch('backend.commerce.models.SeatChange.save',side_effect=RuntimeError('seat-audit-failed')):
            with self.assertRaisesMessage(RuntimeError,'seat-audit-failed'):
                remove_member(self.account,self.user,original.pk,request_key='remove-used')
        seat.refresh_from_db();original.refresh_from_db()
        self.assertTrue(original.is_active)
        self.assertEqual(seat.membership_id,original.pk)
        self.assertEqual(CreditBatch.objects.get(seat=seat).expires_at,month_boundary(self.anchor,1))
        removed=remove_member(self.account,self.user,original.pk,request_key='remove-used')
        self.assertEqual(removed,remove_member(self.account,self.user,original.pk,request_key='remove-used'))
        seat.refresh_from_db()
        self.assertIsNone(seat.membership_id)
        self.assertEqual(CreditBatch.objects.get(seat=seat).expires_at,timezone.now())
        with self.assertRaisesMessage(ValueError,'SEAT_LOCKED_UNTIL_NEXT_SERVICE_MONTH'):
            assign_seat(self.account,self.user,seat.pk,replacement.pk,expected_version=2,request_key='replace-used')
        self.assertEqual(organization_summary(self.account)['locked_seats'],1)

    def test_removed_seat_with_old_reservation_waits_for_settlement(self):
        from .billing import reserve_credits, settle_credits
        from .subscriptions import assign_seat, remove_member, organization_summary
        _, seat, original, replacement = self._seat_lifecycle_fixture()
        debit=reserve_credits(self.account,'old-reserved-seat','2',membership_id=original.pk)
        before=CreditBatch.objects.get(seat=seat).expires_at
        self.now.return_value=self.anchor+timedelta(days=1)
        result=remove_member(self.account,self.user,original.pk,request_key='remove-reserved')
        self.assertTrue(result['handoff_pending'])
        seat.refresh_from_db();original.refresh_from_db()
        self.assertEqual(seat.membership_id,original.pk)
        self.assertFalse(original.is_active)
        self.assertEqual(CreditBatch.objects.get(seat=seat).expires_at,before)
        for target in (None,replacement.pk):
            with self.assertRaisesMessage(ValueError,'TASK_SETTLEMENT_REQUIRED'):
                assign_seat(self.account,self.user,seat.pk,target,expected_version=1,request_key='cannot-move')
        summary=organization_summary(self.account)
        self.assertEqual(summary['pending_settlement_seats'],1)
        row=next(item for item in summary['members'] if item['id']==original.pk)
        self.assertFalse(row['can_use_credits'])
        self.assertTrue(row['seat_release_pending'])
        settle_credits(self.account,debit['id'],'1')
        assign_seat(self.account,self.user,seat.pk,None,expected_version=1,request_key='settled-release')
        self.assertEqual(CreditBatch.objects.get(seat=seat).expires_at,timezone.now())

    def test_enterprise_removal_releases_seat_and_summary_separates_free_roles(self):
        from .subscriptions import assign_seat, remove_member, organization_summary
        _, seat, original, replacement = self._seat_lifecycle_fixture(mode='shared_only')
        rows={item['id']:item for item in organization_summary(self.account)['members']}
        owner=Membership.objects.get(account=self.account,user=self.user)
        self.assertEqual(rows[owner.pk]['usage_role'],'config_admin')
        self.assertFalse(rows[owner.pk]['can_use_credits'])
        self.assertEqual(rows[original.pk]['usage_role'],'member')
        self.assertTrue(rows[original.pk]['can_use_credits'])
        self.assertEqual(rows[replacement.pk]['usage_role'],'directory_member')
        remove_member(self.account,self.user,original.pk,request_key='remove-enterprise')
        assign_seat(self.account,self.user,seat.pk,replacement.pk,expected_version=2,request_key='reuse-enterprise')
        self.assertFalse(CreditBatch.objects.filter(seat=seat).exists())
        assign_seat(self.account,self.user,seat.pk,owner.pk,expected_version=3,request_key='paid-admin')
        rows={item['id']:item for item in organization_summary(self.account)['members']}
        self.assertEqual(rows[owner.pk]['usage_role'],'admin')
        self.now.return_value=month_boundary(self.anchor,12)
        summary=organization_summary(self.account)
        self.assertFalse(summary['subscription_active'])
        self.assertTrue(next(item for item in summary['members'] if item['id']==owner.pk)['can_use_credits'])
        for field in ('refund_cutoff_at','superseded_at'):
            Subscription.objects.filter(pk=seat.subscription_id).update(
                **{'refund_cutoff_at':None,'superseded_at':None,field:timezone.now()})
            row=next(item for item in organization_summary(self.account)['members'] if item['id']==owner.pk)
            self.assertEqual(row['usage_role'],'config_admin')
            self.assertFalse(row['can_use_credits'])

    def test_seat_reassignment_keeps_credits_and_rejects_stale_changes(self):
        from .subscriptions import assign_seat
        from .models import PaidSeat, SeatChange
        from django.contrib.auth import get_user_model
        self.activate(self.business_paid('seat_included'))
        first = Membership.objects.get(account=self.account,user=self.user)
        second = Membership.objects.create(account=self.account,user=get_user_model().objects.create_user(username='seat-two'),role='member')
        seat = PaidSeat.objects.order_by('position').first()
        batches = list(CreditBatch.objects.values_list('id','initial_quantity','expires_at'))
        result = assign_seat(self.account,self.user,seat.pk,first.pk,expected_version=0,request_key='assign-first')
        self.assertEqual(result,assign_seat(self.account,self.user,seat.pk,first.pk,expected_version=0,request_key='assign-first'))
        result = assign_seat(self.account,self.user,seat.pk,second.pk,expected_version=1,request_key='replace')
        self.assertEqual(result['version'],2)
        with self.assertRaisesRegex(ValueError,'SEAT_CHANGED'):
            assign_seat(self.account,self.user,seat.pk,first.pk,expected_version=1,request_key='stale')
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
            assign_seat(self.account,self.user,seat.pk,first.pk,expected_version=1,request_key='replace')
        self.assertEqual(list(CreditBatch.objects.values_list('id','initial_quantity','expires_at')),batches)
        self.assertEqual(SeatChange.objects.count(),2)
        change = SeatChange.objects.order_by('-id').first()
        self.assertEqual((change.before_membership_id,change.after_membership_id),(first.pk,second.pk))

    def test_seat_assignment_rejects_unauthorized_duplicate_and_foreign_member(self):
        from .subscriptions import assign_seat
        from .models import PaidSeat
        self.activate(self.business_paid('seat_included'))
        member = Membership.objects.get(account=self.account,user=self.user)
        seats = list(PaidSeat.objects.order_by('position'))
        with self.assertRaisesRegex(ValueError,'FORBIDDEN'):
            assign_seat(self.account,self.other_user,seats[0].pk,member.pk,expected_version=0,request_key='unauthorized')
        foreign = Membership.objects.get(account=self.other,user=self.other_user)
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            assign_seat(self.account,self.user,seats[0].pk,foreign.pk,expected_version=0,request_key='foreign')
        assign_seat(self.account,self.user,seats[0].pk,member.pk,expected_version=0,request_key='first')
        with self.assertRaisesRegex(ValueError,'MEMBER_ALREADY_SEATED'):
            assign_seat(self.account,self.user,seats[1].pk,member.pk,expected_version=0,request_key='duplicate')

    def test_expired_subscription_allows_unassignment_not_replacement(self):
        from .subscriptions import assign_seat
        from .models import PaidSeat
        self.activate(self.business_paid('shared_only'))
        member = Membership.objects.get(account=self.account,user=self.user)
        seat = PaidSeat.objects.first()
        assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=0,request_key='first')
        self.now.return_value = month_boundary(self.anchor,12)
        assign_seat(self.account,self.user,seat.pk,None,expected_version=1,request_key='remove')
        with self.assertRaisesRegex(ValueError,'SUBSCRIPTION_EXPIRED'):
            assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=2,request_key='again')
        self.assertEqual(PaidSeat.objects.count(),2)

    def test_seat_change_audit_failure_rolls_back_assignment(self):
        from .subscriptions import assign_seat
        from .models import PaidSeat, SeatChange
        self.activate(self.business_paid('seat_included'))
        member = Membership.objects.get(account=self.account,user=self.user)
        seat = PaidSeat.objects.first()
        with patch('backend.commerce.models.SeatChange.save',side_effect=RuntimeError('audit-failed')):
            with self.assertRaisesRegex(RuntimeError,'audit-failed'):
                assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=0,request_key='assign')
        seat.refresh_from_db()
        self.assertIsNone(seat.membership_id)
        self.assertEqual(seat.version,0)
        self.assertEqual(SeatChange.objects.count(),0)

    def business_member(self, mode='seat_included'):
        from .models import PaidSeat
        from .subscriptions import assign_seat
        self.activate(self.business_paid(mode))
        member = Membership.objects.get(account=self.account,user=self.user)
        seat = PaidSeat.objects.order_by('position').first()
        assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=0,request_key='member-first')
        grant_credits(self.account,'purchased','shared-fixture','10',self.anchor+timedelta(days=2))
        return member

    def test_enterprise_seat_and_shared_limit_notifications_follow_versions(self):
        from django.db import transaction
        from .billing import set_shared_limit, notifications_page
        from .subscriptions import assign_seat
        from .models import PaidSeat, BillingNotification
        member = self.business_member('shared_only')
        seat = PaidSeat.objects.get(membership=member)
        self.assertEqual(BillingNotification.objects.filter(type='seat_assignment_changed').count(),1)
        with self.assertRaisesRegex(ValueError,'ROLLBACK'):
            with transaction.atomic():
                assign_seat(self.account,self.user,seat.pk,None,expected_version=1,request_key='clear')
                set_shared_limit(self.account,self.user,member.pk,'2',expected_version=0,request_key='cap')
                raise ValueError('ROLLBACK')
        self.assertEqual(BillingNotification.objects.filter(type='seat_assignment_changed').count(),1)
        self.assertFalse(BillingNotification.objects.filter(type='shared_limit_changed').exists())
        assign_seat(self.account,self.user,seat.pk,None,expected_version=1,request_key='clear')
        assign_seat(self.account,self.user,seat.pk,None,expected_version=1,request_key='clear')
        set_shared_limit(self.account,self.user,member.pk,'2',expected_version=0,request_key='cap')
        set_shared_limit(self.account,self.user,member.pk,'2',expected_version=0,request_key='cap')
        set_shared_limit(self.account,self.user,member.pk,None,expected_version=1,request_key='uncap')
        rows = [row for row in notifications_page(self.account)['items'] if row['type'] in ('seat_assignment_changed','shared_limit_changed')]
        self.assertEqual(len(rows),4)
        self.assertTrue(all(row['amount_minor'] is None and row['currency'] is None for row in rows))
        self.assertEqual(BillingNotification.objects.filter(type='seat_assignment_changed',subscription=seat.subscription).count(),2)

    def test_business_own_included_then_shared_ignores_legacy_member_cap(self):
        from .billing import set_shared_limit, start_shared_task, consume_shared_task
        member = self.business_member()
        set_shared_limit(self.account,self.user,member.pk,'3',expected_version=0,request_key='cap')
        task = start_shared_task(self.account,member.pk,'shared-all-seats','voc')
        self.assertTrue(consume_shared_task(self.account,task['id'],'actual','27.25')['accepted'])
        entries = CreditEntry.objects.filter(reservation__shared_task_id=task['id'],kind='settle')
        self.assertEqual(entries.get(batch__source='purchased').quantity,'10')
        self.assertEqual(entries.filter(batch__source='subscription').count(),1)
        self.assertEqual(entries.get(batch__source='subscription').batch.seat.membership_id,member.pk)
        self.assertEqual(credits_summary(self.account)['reserved'],'0')
        over = start_shared_task(self.account,member.pk,'over-total-balance','voc')
        self.assertFalse(consume_shared_task(self.account,over['id'],'actual','5')['accepted'])
        self.assertEqual(credits_summary(self.account)['consumed'],'27.25')
        self.assertEqual(credits_summary(self.account)['available'],'17.25')

    def test_legacy_limit_history_is_versioned_but_does_not_limit_actual_shared_usage(self):
        from .billing import set_shared_limit, start_shared_task, consume_shared_task
        member = self.business_member('shared_only')
        initial = set_shared_limit(self.account,self.user,member.pk,'5',expected_version=0,request_key='cap')
        self.assertEqual(initial,set_shared_limit(self.account,self.user,member.pk,'5',expected_version=0,request_key='cap'))
        task = start_shared_task(self.account,member.pk,'actual-use','voc')
        consume_shared_task(self.account,task['id'],'first','4')
        set_shared_limit(self.account,self.user,member.pk,'2',expected_version=1,request_key='reduce')
        self.assertTrue(consume_shared_task(self.account,task['id'],'after-legacy-reduction','1')['accepted'])
        self.assertEqual(credits_summary(self.account)['consumed'],'5')
        with self.assertRaisesRegex(ValueError,'LIMIT_CHANGED'):
            set_shared_limit(self.account,self.user,member.pk,None,expected_version=1,request_key='stale')
        with self.assertRaisesRegex(ValueError,'FORBIDDEN'):
            set_shared_limit(self.account,self.other_user,member.pk,'5',expected_version=2,request_key='foreign')

    def test_legacy_hold_still_rejects_wrong_member_and_cross_month_settlement(self):
        from .billing import reserve_credits, settle_credits
        member = self.business_member('shared_only')
        foreign = Membership.objects.get(account=self.other,user=self.other_user)
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            reserve_credits(self.account,'foreign','1',membership_id=foreign.pk)
        grant_credits(self.account,'purchased','long-shared','10',self.anchor+timedelta(days=100))
        self.now.return_value = self.anchor+timedelta(days=3)
        task = reserve_credits(self.account,'cross-period','1',membership_id=member.pk)
        self.now.return_value = month_boundary(self.anchor,1)
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            settle_credits(self.account,task['id'],'1')

    def test_actual_shared_task_crosses_service_month_without_member_limit_or_long_hold(self):
        from .billing import set_shared_limit, start_shared_task, consume_shared_task
        member = self.business_member('shared_only')
        grant_credits(self.account,'purchased','long-shared','10',self.anchor+timedelta(days=100))
        set_shared_limit(self.account,self.user,member.pk,'2',expected_version=0,request_key='cap')
        task = start_shared_task(self.account,member.pk,'cross-month-actual','voc')
        self.assertTrue(consume_shared_task(self.account,task['id'],'first','2')['accepted'])
        self.assertTrue(consume_shared_task(self.account,task['id'],'same-month-over-old-cap','1')['accepted'])
        self.assertEqual(credits_summary(self.account)['reserved'],'0')
        self.now.return_value = month_boundary(self.anchor,1)
        self.assertTrue(consume_shared_task(self.account,task['id'],'next-month','2')['accepted'])
        self.assertEqual(credits_summary(self.account)['consumed'],'5')
        self.assertEqual(credits_summary(self.account)['reserved'],'0')

    def test_organization_summary_reads_actual_total_without_editable_member_limit(self):
        from .billing import set_shared_limit, start_shared_task, consume_shared_task
        from .subscriptions import organization_summary
        member = self.business_member('shared_only')
        set_shared_limit(self.account,self.user,member.pk,'5',expected_version=0,request_key='cap')
        task=start_shared_task(self.account,member.pk,'read-actual','voc')
        consume_shared_task(self.account,task['id'],'actual','2')
        result = organization_summary(self.account)
        self.assertEqual((result['seat_count'],result['assigned_seats']),(2,1))
        row = result['members'][0]
        self.assertEqual(row['total_credits'],'2')
        self.assertEqual(row['shared_reserved'],'0')
        self.assertFalse(result['can_edit'])

    def test_business_renewal_preserves_seat_quantity_and_original_slots(self):
        from .subscriptions import prepare_renewal_attempt, fulfill_renewal_attempt
        from .billing import claim_payment_dispatch
        from .models import PaidSeat
        result = self.activate(self.business_paid('seat_included'))
        seats = set(PaidSeat.objects.values_list('id',flat=True))
        end = month_boundary(self.anchor,12)
        self.now.return_value = end-timedelta(hours=1)
        attempt = prepare_renewal_attempt(self.account,result['id'],cycle_index=1,
            dispatch_not_before=end-timedelta(days=1),expires_at=end)
        row = PaymentAttempt.objects.get(pk=attempt['id'])
        self.assertEqual(row.order.snapshot['seat_quantity'],2)
        self.assertEqual(row.order.amount_minor,24690)
        sent_renewal_notice_fixture(self.account,attempt['id'])
        claim_payment_dispatch(self.account,attempt['id'])
        record_confirmed_payment(self.account,row.order_id,channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='business-renewal',amount_minor=24690,currency='CNY',paid_at=timezone.now(),evidence_hash='c'*64)
        fulfill_renewal_attempt(self.account,attempt['id'])
        self.now.return_value = end
        grant_due_credits(self.account)
        self.assertEqual(set(PaidSeat.objects.values_list('id',flat=True)),seats)
        self.assertEqual(CreditBatch.objects.filter(expires_at__gt=end).count(),2)
        self.assertEqual(credits_summary(self.account)['available'],'34.5')

    def test_requires_accepted_payment_and_valid_mandate(self):
        order_id = self.paid()
        for mandate in ({}, {**self.mandate,'automatic_retry':True}, {**self.mandate,'channel':'other'},
                        {**self.mandate,'merchant_reference':'other'}):
            with self.assertRaises(ValueError):
                activate_subscription(self.account, order_id, activated_at=self.anchor,
                    billing_timezone='Asia/Shanghai', calendar_rule_version='fixture', mandate=mandate)
        with self.assertRaisesRegex(ValueError, 'NOT_FOUND'):
            activate_subscription(self.other, order_id, activated_at=self.anchor,
                billing_timezone='Asia/Shanghai', calendar_rule_version='fixture', mandate=self.mandate)
        PaymentFact.objects.all().delete()
        with self.assertRaisesRegex(ValueError, 'PAYMENT_NOT_CONFIRMED'):
            self.activate(order_id)
        self.assertEqual(Subscription.objects.count(), 0)

    def test_confirmed_renewal_setup_failure_keeps_full_paid_service_from_actual_activation(self):
        from .billing import fulfill_paid_order
        from .models import ServiceRefund,CreditRefund
        order_id=self.paid()
        self.now.return_value=self.anchor+timedelta(days=2)
        proof={'status':'confirmed_failed','evidence_hash':'f'*64}
        values=dict(activated_at=timezone.now(),billing_timezone='Asia/Shanghai',
            calendar_rule_version='fixture-calendar-v1',mandate=None,renewal_setup_failure=proof)
        result=activate_subscription(self.account,order_id,**values)
        self.assertEqual(activate_subscription(self.account,order_id,**values),result)
        self.assertEqual(fulfill_paid_order(self.account,order_id)['status'],'fulfilled')
        subscription=Subscription.objects.get(initial_order_id=order_id)
        self.assertFalse(subscription.auto_renew)
        self.assertEqual(subscription.cancellation_state,'none')
        self.assertIsNone(subscription.cancel_requested_at)
        self.assertIsNone(subscription.cancel_confirmed_at)
        self.assertNotIn('agreement_reference',subscription.mandate)
        self.assertEqual(subscription.mandate,{'renewal_setup_failure':proof})
        self.assertEqual(subscription.original_anchor,timezone.now())
        self.assertEqual(subscription.periods.get().ends_at,month_boundary(timezone.now(),12))
        self.assertEqual(credits_summary(self.account)['available'],'17.25')
        self.assertEqual(result['renewal_setup']['status'],'failed')
        self.assertTrue(result['renewal_setup']['separate_authorization_required'])
        self.assertNotIn('evidence_hash',str(result))
        self.assertEqual((Order.objects.count(),PaymentFact.objects.count(),CreditBatch.objects.count()),(1,1,1))
        self.assertFalse(ServiceRefund.objects.exists());self.assertFalse(CreditRefund.objects.exists())
        self.assertEqual(AuditEvent.objects.filter(action='subscription.activated_without_renewal').count(),1)
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
            activate_subscription(self.account,order_id,**{**values,'renewal_setup_failure':{**proof,'evidence_hash':'e'*64}})

    def test_missing_unknown_or_conflicting_renewal_setup_evidence_never_grants_service(self):
        order_id=self.paid()
        for proof in (None,{}, {'status':'unknown','evidence_hash':'f'*64},
                {'status':'confirmed_failed','evidence_hash':''},
                {'status':'confirmed_failed','evidence_hash':'f'*64,'agreement_reference':'fabricated'}):
            with self.subTest(proof=proof),self.assertRaisesRegex(ValueError,'MANDATE_NOT_CONFIRMED'):
                activate_subscription(self.account,order_id,activated_at=self.anchor,billing_timezone='Asia/Shanghai',
                    calendar_rule_version='fixture',mandate=None,renewal_setup_failure=proof)
        with self.assertRaisesRegex(ValueError,'MANDATE_NOT_CONFIRMED'):
            activate_subscription(self.account,order_id,activated_at=self.anchor,billing_timezone='Asia/Shanghai',
                calendar_rule_version='fixture',mandate=self.mandate,
                renewal_setup_failure={'status':'confirmed_failed','evidence_hash':'f'*64})
        self.assertFalse(Subscription.objects.exists());self.assertFalse(CreditBatch.objects.exists())
        self.assertEqual(Order.objects.get(pk=order_id).status,'paid_pending_entitlement')
        PaymentFact.objects.all().delete()
        with self.assertRaisesRegex(ValueError,'PAYMENT_NOT_CONFIRMED'):
            activate_subscription(self.account,order_id,activated_at=self.anchor,billing_timezone='Asia/Shanghai',
                calendar_rule_version='fixture',mandate=None,
                renewal_setup_failure={'status':'confirmed_failed','evidence_hash':'f'*64})

    def test_paid_service_without_renewal_never_creates_a_renewal_charge_or_fake_cancellation(self):
        from .subscriptions import prepare_renewal_attempt,confirm_cancellation,subscription_change_options
        order_id=self.paid()
        result=activate_subscription(self.account,order_id,activated_at=self.anchor,billing_timezone='Asia/Shanghai',
            calendar_rule_version='fixture',mandate=None,
            renewal_setup_failure={'status':'confirmed_failed','evidence_hash':'f'*64})
        with self.assertRaisesRegex(ValueError,'RENEWAL_NOT_AUTHORIZED'):
            prepare_renewal_attempt(self.account,result['id'],cycle_index=1,
                dispatch_not_before=month_boundary(self.anchor,12)-timedelta(days=1),expires_at=month_boundary(self.anchor,12))
        self.assertEqual(cancel_subscription(self.account,self.user,result['id']),result)
        self.assertEqual(subscription_change_options(self.account,self.user,result['id'])['reason'],'RENEWAL_SETUP_REQUIRED')
        with self.assertRaisesRegex(ValueError,'INVALID_CANCELLATION_EVIDENCE'):
            confirm_cancellation(self.account,result['id'],agreement_reference=None,evidence_hash='e'*64)
        self.assertFalse(PaymentAttempt.objects.exists())
        self.assertEqual(Order.objects.count(),1)
        self.now.return_value=month_boundary(self.anchor,1)
        grant_due_credits(self.account)
        self.assertEqual(credits_summary(self.account)['available'],'17.25')

    def test_business_monthly_signing_failure_keeps_all_paid_seats_and_full_first_month(self):
        from .models import PaidSeat
        order_id=self.business_paid('seat_included',billing_cycle='monthly')
        self.now.return_value=self.anchor+timedelta(days=2)
        with override_settings(BILLING_SALES_ENABLED=False):
            result=activate_subscription(self.account,order_id,activated_at=timezone.now(),billing_timezone='Asia/Shanghai',
                calendar_rule_version='fixture',mandate=None,
                renewal_setup_failure={'status':'confirmed_failed','evidence_hash':'f'*64})
        self.assertFalse(result['auto_renew'])
        self.assertEqual(PaidSeat.objects.filter(subscription_id=result['id']).count(),2)
        self.assertEqual(credits_summary(self.account)['available'],'34.5')
        self.assertEqual(SubscriptionPeriod.objects.get(subscription_id=result['id']).ends_at,
            month_boundary(timezone.now(),1))
        self.assertEqual((Order.objects.count(),PaymentFact.objects.count()),(1,1))

    def test_paid_service_without_renewal_refunds_future_months_without_fabricated_cancellation(self):
        from .refunds import request_service_refund, record_service_refund_receipt
        from .models import ServiceRefund
        order_id=self.paid()
        result=activate_subscription(self.account,order_id,activated_at=self.anchor,billing_timezone='Asia/Shanghai',
            calendar_rule_version='fixture',mandate=None,
            renewal_setup_failure={'status':'confirmed_failed','evidence_hash':'f'*64})
        self.now.return_value=self.anchor+timedelta(days=2)
        request=request_service_refund(self.account,self.user,result['id'],'no-renewal-refund')
        self.assertEqual(request['status'],'approved')
        refund=ServiceRefund.objects.get(pk=request['id'])
        self.assertEqual(refund.amount_minor,11316)
        self.assertEqual(request_service_refund(self.account,self.user,result['id'],'no-renewal-refund'),request)
        receipt=record_service_refund_receipt(self.account,refund.pk,refund.allocations[0]['payment_id'],
            event_key='confirmed-no-renewal-refund',outcome='refunded',amount_minor=refund.amount_minor,
            provider_reference='fixture-refund',evidence_hash='e'*64)
        self.assertEqual(receipt['status'],'refunded')
        subscription=Subscription.objects.get(pk=result['id'])
        self.assertFalse(subscription.auto_renew)
        self.assertEqual(subscription.cancellation_state,'none')
        self.assertIsNone(subscription.cancel_requested_at)
        self.assertIsNone(subscription.cancel_confirmed_at)
        self.assertEqual(subscription.cancellation_evidence_hash,'')
        self.now.return_value=month_boundary(self.anchor,1)
        self.assertEqual(grant_due_credits(self.account)['created_batches'],0)
        from .subscriptions import active_subscription_periods
        self.assertFalse(active_subscription_periods(self.account).exists())

    def test_terminated_refund_restores_paid_service_without_enabling_failed_renewal(self):
        from .refunds import request_service_refund, record_service_refund_receipt
        from .models import ServiceRefund
        order_id=self.paid()
        proof={'status':'confirmed_failed','evidence_hash':'f'*64}
        result=activate_subscription(self.account,order_id,activated_at=self.anchor,billing_timezone='Asia/Shanghai',
            calendar_rule_version='fixture',mandate=None,renewal_setup_failure=proof)
        request=request_service_refund(self.account,self.user,result['id'],'stop-no-renewal-refund')
        refund=ServiceRefund.objects.get(pk=request['id'])
        values=dict(account=self.account,refund_id=refund.pk,payment_id=refund.allocations[0]['payment_id'],
            amount_minor=refund.amount_minor,provider_reference='',evidence_hash='e'*64)
        self.assertEqual(record_service_refund_receipt(**values,event_key='unknown-refund',outcome='unknown')['status'],'unknown')
        self.assertEqual(record_service_refund_receipt(**values,event_key='terminated-refund',outcome='terminated')['status'],'terminated')
        subscription=Subscription.objects.get(pk=result['id'])
        self.assertIsNone(subscription.refund_cutoff_at)
        self.assertFalse(subscription.auto_renew)
        self.assertEqual(subscription.cancellation_state,'none')
        self.assertEqual(subscription.mandate,{'renewal_setup_failure':proof})
        self.now.return_value=month_boundary(self.anchor,1)
        self.assertEqual(grant_due_credits(self.account)['created_batches'],1)
        self.assertEqual(credits_summary(self.account)['available'],'17.25')

    def test_annual_grants_only_current_month_and_replay_does_not_reset(self):
        order_id = self.paid()
        result = self.activate(order_id)
        self.assertEqual(self.activate(order_id), result)
        self.assertEqual((Subscription.objects.count(),SubscriptionPeriod.objects.count(),CreditBatch.objects.count()),(1,1,1))
        self.assertEqual(credits_summary(self.account)['available'],'17.25')
        self.assertEqual(Order.objects.get(pk=order_id).status,'fulfilled')
        self.assertNotIn('mandate', result)
        self.assertEqual(result['status'],'active')
        self.assertEqual(result['next_credit_grant_at'],month_boundary(self.anchor,1).isoformat())

    def test_calendar_roundtrip_month_end_leap_year_and_no_thirteenth_grant(self):
        self.activate()
        for month in range(1,12):
            self.now.return_value = month_boundary(self.anchor,month)
            self.assertEqual(grant_due_credits(self.account)['created_batches'],1)
            self.assertEqual(grant_due_credits(self.account)['created_batches'],0)
            batch = CreditBatch.objects.get(service_month=month)
            self.assertEqual(batch.expires_at,month_boundary(self.anchor,month+1))
            self.assertEqual(credits_summary(self.account)['available'],'17.25')
        self.now.return_value = month_boundary(self.anchor,12)
        self.assertEqual(grant_due_credits(self.account)['created_batches'],0)
        self.assertEqual(credits_summary(self.account)['available'],'0')
        self.assertEqual(CreditBatch.objects.count(),12)
        self.assertEqual((Order.objects.count(),PaymentFact.objects.count()),(1,1))

    def test_cancel_retains_annual_grants_and_requires_channel_confirmation(self):
        sub = self.activate()
        result = cancel_subscription(self.account,self.user,sub['id'])
        self.assertFalse(result['auto_renew'])
        self.assertEqual(result['cancellation_state'],'processing')
        self.assertEqual(result,cancel_subscription(self.account,self.user,sub['id']))
        self.now.return_value = month_boundary(self.anchor,1)
        grant_due_credits(self.account)
        self.assertEqual(credits_summary(self.account)['available'],'17.25')
        with self.assertRaises(ValueError):
            confirm_cancellation(self.account,sub['id'],agreement_reference='wrong',evidence_hash='c'*64)
        confirmed = confirm_cancellation(self.account,sub['id'],agreement_reference='fixture-agreement',evidence_hash='c'*64)
        self.assertEqual(confirmed['cancellation_state'],'completed')
        self.assertEqual(confirmed,confirm_cancellation(self.account,sub['id'],agreement_reference='fixture-agreement',evidence_hash='c'*64))
        self.assertEqual(AuditEvent.objects.filter(action='subscription.cancel_requested').count(),1)

    def test_early_paid_renewal_is_not_spendable_until_its_start(self):
        sub = self.activate()
        self.now.return_value = month_boundary(self.anchor,11)
        grant_due_credits(self.account)
        order_id = self.paid()
        result = append_paid_period(self.account,sub['id'],order_id,cycle_index=1)
        self.assertEqual(credits_summary(self.account)['available'],'17.25')
        self.assertEqual(CreditBatch.objects.filter(subscription_period__order_id=order_id).count(),0)
        self.assertEqual(result,append_paid_period(self.account,sub['id'],order_id,cycle_index=1))
        self.now.return_value = month_boundary(self.anchor,12)
        grant_due_credits(self.account)
        self.assertEqual(credits_summary(self.account)['available'],'17.25')
        self.assertEqual(CreditBatch.objects.filter(subscription_period__order_id=order_id).count(),1)

    def test_cancel_does_not_swallow_already_inflight_paid_period(self):
        sub = self.activate()
        self.now.return_value = month_boundary(self.anchor,11)
        order_id = self.paid()
        cancel_subscription(self.account,self.user,sub['id'])
        append_paid_period(self.account,sub['id'],order_id,cycle_index=1)
        self.now.return_value = month_boundary(self.anchor,12)
        grant_due_credits(self.account)
        self.assertEqual(credits_summary(self.account)['available'],'17.25')

    def test_missed_months_expire_not_rollover_and_original_snapshot_survives(self):
        self.activate()
        ProductVersion.objects.filter(pk=self.product.pk).update(credits_quantity='999',state='retired')
        self.now.return_value = month_boundary(self.anchor,5)
        grant_due_credits(self.account)
        self.assertEqual(credits_summary(self.account)['available'],'17.25')
        self.assertEqual(credits_summary(self.account)['expired'],'86.25')
        self.assertEqual(CreditBatch.objects.count(),6)

    def test_overlapping_subscription_and_duplicate_period_refuse_without_losing_money(self):
        sub = self.activate()
        second = self.paid()
        with self.assertRaisesRegex(ValueError,'SUBSCRIPTION_CONFLICT'):
            self.activate(second)
        append_paid_period(self.account,sub['id'],second,cycle_index=1)
        third = self.paid()
        with self.assertRaisesRegex(ValueError,'SUBSCRIPTION_CONFLICT'):
            append_paid_period(self.account,sub['id'],third,cycle_index=1)
        self.assertEqual(PaymentFact.objects.count(),3)
        self.assertEqual(Order.objects.get(pk=third).status,'paid_pending_entitlement')

    def test_late_renewal_unknown_policy_rejected_and_purchase_balance_retained(self):
        ProductVersion.objects.filter(pk=self.product.pk).update(billing_cycle='monthly')
        sub = self.activate()
        grant_credits(self.account,'purchased','independent','2',month_boundary(self.anchor,12))
        self.now.return_value = month_boundary(self.anchor,1)+timedelta(seconds=1)
        late = self.paid()
        with self.assertRaisesRegex(ValueError,'LATE_RENEWAL_REQUIRES_ACTIVATION'):
            append_paid_period(self.account,sub['id'],late,cycle_index=1)
        grant_due_credits(self.account)
        self.assertEqual(credits_summary(self.account)['available'],'2')
        self.assertEqual(list_subscriptions(self.account)[0]['status'],'expired')

    def test_rollback_delivery_keeps_money_and_can_repair(self):
        order_id = self.paid()
        with patch('backend.commerce.subscriptions.grant_credits',side_effect=RuntimeError('fixture failure')):
            with self.assertRaises(RuntimeError): self.activate(order_id)
        self.assertEqual(Subscription.objects.count(),0)
        self.assertEqual(PaymentFact.objects.count(),1)
        self.activate(order_id)
        self.assertEqual(CreditEntry.objects.count(),1)

    def test_invalid_time_inputs_and_changed_replay_are_rejected(self):
        order_id = self.paid()
        for moment, zone in ((self.anchor.replace(tzinfo=None),'Asia/Shanghai'),(self.anchor,'Not/AZone'),
                            (self.anchor+timedelta(days=1),'Asia/Shanghai')):
            with self.assertRaises(ValueError):
                activate_subscription(self.account,order_id,activated_at=moment,billing_timezone=zone,
                                      calendar_rule_version='fixture',mandate=self.mandate)
        self.activate(order_id)
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
            activate_subscription(self.account,order_id,activated_at=self.anchor,billing_timezone='UTC',
                calendar_rule_version='fixture-calendar-v1',mandate=self.mandate)

    def test_read_cancel_api_csrf_account_isolation_and_empty_list(self):
        sub = self.activate()
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get('/api/billing/subscriptions').status_code,401)
        client.force_login(self.user)
        token = client.get('/api/auth/session').json()['csrf_token']
        self.assertEqual(client.get('/api/billing/subscriptions').json()['items'][0]['id'],sub['id'])
        path = '/api/billing/subscriptions/'+sub['id']+'/cancel'
        self.assertEqual(client.post(path,'{}',content_type='application/json').status_code,403)
        self.assertEqual(client.post(path,'{}',content_type='application/json',HTTP_X_CSRFTOKEN=token).status_code,409)
        response = client.post(path,'{}',content_type='application/json',HTTP_X_CSRFTOKEN=token,HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['subscription']['cancellation_state'],'processing')
        self.assertEqual(list_subscriptions(self.other),[])
        with self.assertRaisesRegex(ValueError,'FORBIDDEN'):
            cancel_subscription(self.account,self.other_user,sub['id'])
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            cancel_subscription(self.other,self.other_user,sub['id'])


@override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
class OrganizationHistoryTests(TransactionTestCase):
    """Existing organization audit rows only, never a management write endpoint."""

    def setUp(self):
        from backend.models import LoginIdentity
        from .models import PaidSeat
        SubscriptionTests.setUp(self)
        self.personal = self.account
        order_id = SubscriptionTests.business_paid(self, 'shared_only')
        SubscriptionTests.activate(self, order_id)
        self.owner = Membership.objects.get(account=self.account,user=self.user)
        self.member = Membership.objects.create(account=self.account,user=self.other_user,role='member')
        self.seat = PaidSeat.objects.filter(subscription__account=self.account).order_by('position').first()
        LoginIdentity.objects.create(user=self.user,method='email',identifier='finance-history@example.invalid')

    def history(self, **options):
        from .subscriptions import organization_history
        return organization_history(self.account, **options)

    def seed_changes(self):
        from .subscriptions import assign_seat
        from .billing import set_shared_limit
        assign_seat(self.account,self.user,self.seat.pk,self.owner.pk,expected_version=0,request_key='private-seat-one')
        assign_seat(self.account,self.user,self.seat.pk,self.member.pk,expected_version=1,request_key='private-seat-two')
        set_shared_limit(self.account,self.user,self.member.pk,'9007199254740993.125',expected_version=0,request_key='private-limit-one')
        set_shared_limit(self.account,self.user,self.member.pk,'0',expected_version=1,request_key='private-limit-two')

    def test_history_recovers_versions_before_after_and_exact_quantities(self):
        self.seed_changes()
        Membership.objects.filter(pk=self.owner.pk).update(is_active=False)
        result = self.history()
        self.assertEqual(result['account_id'],str(self.account.pk))
        self.assertEqual(result['total'],4)
        self.assertIsNone(result['next_offset'])
        seats = {row['version']:row for row in result['items'] if row['type'] == 'seat_assignment'}
        limits = {row['version']:row for row in result['items'] if row['type'] == 'shared_limit'}
        self.assertIsNone(seats[1]['before_membership_id'])
        self.assertEqual(seats[2]['before_membership_id'],self.owner.pk)
        self.assertEqual(seats[2]['after_membership_id'],self.member.pk)
        self.assertEqual(seats[2]['before_version'],1)
        self.assertEqual(seats[2]['seat_position'],1)
        self.assertEqual(limits[1]['quantity'],'9007199254740993.125')
        self.assertTrue(limits[1]['previous_known'])
        self.assertIsNone(limits[1]['previous_quantity'])
        self.assertEqual(limits[2]['previous_quantity'],'9007199254740993.125')
        self.assertEqual(limits[2]['quantity'],'0')
        for item in result['items']:
            self.assertEqual(item['actor_masked_identifier'],'f***@example.invalid')
        rendered = json.dumps(result)
        for excluded in ('finance-history', 'private-', 'request_key', 'actor_id', 'evidence_hash', 'agreement_reference'):
            self.assertNotIn(excluded,rendered)

    def test_mixed_history_is_stably_paginated_without_gaps_at_identical_times(self):
        from .models import SharedLimitChange
        from .subscriptions import assign_seat
        assign_seat(self.account,self.user,self.seat.pk,self.owner.pk,expected_version=0,request_key='one-seat')
        SharedLimitChange.objects.bulk_create([SharedLimitChange(membership=self.member,
            actor=self.user,quantity=str(number),version=number,request_key=f'page-{number}')
            for number in range(1,52)])
        first,second = self.history(),self.history(offset=50)
        self.assertEqual(first['total'],52)
        self.assertEqual(first['next_offset'],50)
        self.assertEqual(len(first['items']),50)
        self.assertEqual(len(second['items']),2)
        self.assertIsNone(second['next_offset'])
        identifiers = [item['id'] for item in first['items']+second['items']]
        self.assertEqual(len(set(identifiers)),52)
        self.assertEqual(self.history()['items'],first['items'])
        self.assertEqual(self.history(offset=999999999)['items'],[])

    def test_missing_previous_limit_is_unknown_not_unlimited(self):
        from .models import SharedLimitChange
        SharedLimitChange.objects.create(membership=self.member,actor=self.user,
            quantity='0',version=3,request_key='missing-earlier-version')
        item = self.history()['items'][0]
        self.assertFalse(item['previous_known'])
        self.assertIsNone(item['previous_quantity'])
        self.assertEqual(item['quantity'],'0')

    def test_actor_phone_is_masked_and_missing_identity_does_not_expose_username(self):
        from backend.models import LoginIdentity
        self.seed_changes()
        LoginIdentity.objects.create(user=self.user,method='phone',identifier='13800138000')
        self.assertEqual(self.history()['items'][0]['actor_masked_identifier'],'138****8000')
        LoginIdentity.objects.filter(user=self.user).delete()
        self.assertEqual(self.history()['items'][0]['actor_masked_identifier'],'')

    def test_other_business_has_no_history_and_personal_account_is_rejected(self):
        from backend.models import BillingAccount
        from .subscriptions import organization_history
        self.seed_changes()
        other_business = BillingAccount.objects.create(kind='business',name='Other history fixture')
        self.assertEqual(organization_history(other_business)['items'],[])
        with self.assertRaisesRegex(ValueError,'FORBIDDEN'):
            organization_history(self.personal)
        for offset in (-1,True,1.5,'1',1000000000):
            with self.subTest(offset=offset),self.assertRaisesRegex(ValueError,'INVALID_REQUEST'):
                self.history(offset=offset)

    def test_financial_api_checks_account_role_and_strict_single_ascii_offset(self):
        self.seed_changes()
        client = Client()
        path = '/api/billing/organization-history'
        self.assertEqual(client.get(path).status_code,401)
        client.force_login(self.user)
        session = client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        self.assertEqual(client.get(path).json()['total'],4)
        for query in ('?offset=-1','?offset=1.5','?offset=+1','?offset=１２',
                      '?offset=1&offset=2','?offset=','?offset=1000000000','?unexpected=1'):
            with self.subTest(query=query):
                self.assertEqual(client.get(path+query).status_code,422)
        self.assertEqual(client.get(path,HTTP_X_BILLING_ACCOUNT=str(self.other.pk)).status_code,409)
        client.force_login(self.other_user)
        session = client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        self.assertEqual(client.get(path).status_code,403)
        Membership.objects.filter(pk=self.member.pk).update(role='billing_admin')
        self.assertEqual(client.get(path).status_code,200)
        self.assertEqual(client.post(path, '{}', content_type='application/json').status_code,405)

    def test_reads_are_repeatable_and_do_not_write_or_lock_rows(self):
        from django.test.utils import CaptureQueriesContext
        self.seed_changes()
        with CaptureQueriesContext(connection) as queries:
            first,second = self.history(),self.history()
        self.assertEqual(first,second)
        self.assertTrue(queries.captured_queries)
        for row in queries.captured_queries:
            self.assertRegex(row['sql'],r'^\(?SELECT\b')
            self.assertNotRegex(row['sql'],r'(?i);|\b(?:INSERT|UPDATE|DELETE|INTO|LOCK)\b')


@override_settings(BILLING_SALES_ENABLED=True, BILLING_RENEWAL_CHARGING_ENABLED=True,
                   BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
class SubscriptionConcurrencyTests(TransactionTestCase):
    setUp = SubscriptionTests.setUp
    paid = SubscriptionTests.paid
    activate = SubscriptionTests.activate
    prepare = RenewalIntentTests.prepare
    failed_renewal = RenewalIntentTests.failed_renewal
    recover = RenewalIntentTests.recover
    business_paid = SubscriptionTests.business_paid
    business_member = SubscriptionTests.business_member
    topup_fixture = RenewalIntentTests.topup_fixture
    def parallel(self, operations):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row locks required')
        barrier = Barrier(len(operations))
        def run(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operation()
            except ValueError as exc:
                return str(exc)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            return list(pool.map(run,operations))

    def test_parallel_activation_grant_and_cancel(self):
        order_id = self.paid()
        results = self.parallel([lambda:self.activate(order_id)]*2)
        self.assertEqual(results[0],results[1])
        self.assertEqual(Subscription.objects.count(),1)
        self.now.return_value = month_boundary(self.anchor,1)
        self.parallel([lambda:grant_due_credits(self.account)]*2)
        self.assertEqual(CreditBatch.objects.count(),2)
        self.parallel([lambda:cancel_subscription(self.account,self.user,results[0]['id'])]*2)
        self.assertEqual(AuditEvent.objects.filter(action='subscription.cancel_requested').count(),1)

    def test_parallel_topup_consents_have_one_version_winner(self):
        from .subscriptions import record_topup_authorization
        from .models import TopupAuthorization
        args = self.topup_fixture()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        def record(key):
            return record_topup_authorization(self.account,self.user,**{**args,'request_key':key,
                'mandate':{**args['mandate'],'agreement_reference':key}})
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            results = self.parallel([lambda:record('one'),lambda:record('two')])
        self.assertEqual(sum(isinstance(result,dict) for result in results),1)
        self.assertIn('AUTHORIZATION_VERSION_CHANGED',results)
        self.assertEqual(TopupAuthorization.objects.count(),1)
        self.assertEqual(Order.objects.count(),1)

    def test_parallel_topup_budget_reserves_only_one_inflight_intent(self):
        from .subscriptions import record_topup_authorization, prepare_topup_attempt
        from .models import TopupBudget
        args = self.topup_fixture()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            auth = record_topup_authorization(self.account,self.user,**args)
            def prepare(key):
                return prepare_topup_attempt(self.account,auth['id'],trigger_key=key,
                    starts_at=self.anchor,ends_at=self.anchor+timedelta(days=30),policy_version='fixture-window',
                    expires_at=self.anchor+timedelta(hours=1))
            results = self.parallel([lambda:prepare('one'),lambda:prepare('two')])
        self.assertEqual(sum(isinstance(result,dict) for result in results),1)
        self.assertIn('TOPUP_PENDING',results)
        self.assertEqual(TopupBudget.objects.count(),1)
        self.assertEqual(Order.objects.count(),2)

    def test_parallel_authorization_change_has_one_version_winner(self):
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization
        from .models import AuthorizationChange
        method = register_verified_payment_method(self.account,channel='fixture',merchant_reference='fixture-shenzhen',
            fingerprint='c'*64,evidence_hash='d'*64)
        sub = self.activate(payment_method_id=method)
        def replace(key):
            return replace_subscription_authorization(self.account,self.user,sub['id'],payment_method_id=method,
                mandate={**self.mandate,'agreement_reference':key},expected_version=0,request_key=key)
        results = self.parallel([lambda:replace('one'), lambda:replace('two')])
        self.assertEqual(sum(isinstance(result,dict) for result in results),1)
        self.assertIn('AUTHORIZATION_VERSION_CHANGED',results)
        self.assertEqual(AuthorizationChange.objects.count(),1)
        self.assertEqual(Subscription.objects.get(pk=sub['id']).authorization_version,1)

    def test_parallel_cross_channel_change_has_one_winner_without_new_orders(self):
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization
        from .models import AuthorizationChange
        sub = self.activate()
        channels = {**FIXTURE_CHANNELS,'replacement':dict(FIXTURE_CHANNELS['fixture'])}
        method = register_verified_payment_method(self.account,channel='replacement',merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        def replace(key):
            return replace_subscription_authorization(self.account,self.user,sub['id'],payment_method_id=method,
                mandate={**self.mandate,'channel':'replacement','agreement_reference':key},expected_version=0,request_key=key)
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            results = self.parallel([lambda:replace('one'),lambda:replace('two')])
        self.assertEqual(sum(isinstance(result,dict) for result in results),1)
        self.assertIn('AUTHORIZATION_VERSION_CHANGED',results)
        self.assertEqual(AuthorizationChange.objects.count(),1)
        self.assertEqual(Subscription.objects.get(pk=sub['id']).authorization_version,1)
        self.assertEqual(Order.objects.count(),1)

    def test_parallel_seat_assignment_has_one_winner_and_one_audit(self):
        from .subscriptions import assign_seat
        from .models import PaidSeat, SeatChange
        self.activate(self.business_paid('seat_included'))
        member = Membership.objects.get(account=self.account,user=self.user)
        seat = PaidSeat.objects.first()
        results = self.parallel([
            lambda:assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=0,request_key='one'),
            lambda:assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=0,request_key='two')])
        self.assertEqual(sum(isinstance(item,dict) for item in results),1)
        self.assertIn('SEAT_CHANGED',results)
        self.assertEqual(SeatChange.objects.count(),1)
        self.assertEqual(CreditBatch.objects.count(),2)

    def test_parallel_actual_shared_consumption_uses_total_pool_and_cannot_overspend(self):
        from .billing import set_shared_limit, start_shared_task, consume_shared_task
        member = self.business_member('shared_only')
        set_shared_limit(self.account,self.user,member.pk,'5',expected_version=0,request_key='cap')
        first=start_shared_task(self.account,member.pk,'first-parallel','voc')
        second=start_shared_task(self.account,member.pk,'second-parallel','voc')
        results = self.parallel([
            lambda:consume_shared_task(self.account,first['id'],'actual','6'),
            lambda:consume_shared_task(self.account,second['id'],'actual','6')])
        self.assertEqual(sum(item['accepted'] for item in results),1)
        self.assertEqual(credits_summary(self.account)['consumed'],'6')
        self.assertEqual(credits_summary(self.account)['reserved'],'0')

    def test_parallel_method_removal_and_dispatch_preserve_one_path(self):
        from .billing import claim_payment_dispatch
        from .subscriptions import (register_verified_payment_method, payment_method_impact,
            request_payment_method_removal)
        method = register_verified_payment_method(self.account, channel='fixture',
            merchant_reference='fixture-shenzhen', fingerprint='c'*64, evidence_hash='d'*64)
        sub = self.activate(payment_method_id=method)
        attempt = self.prepare(sub['id'], notice_sent=True)
        impact = payment_method_impact(self.account, self.user, method)
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        results = self.parallel([
            lambda:request_payment_method_removal(self.account, self.user, method, expected_impact=impact['confirmation_hash']),
            lambda:claim_payment_dispatch(self.account, attempt['id'])])
        row = PaymentAttempt.objects.get(pk=attempt['id'])
        if row.dispatched_at:
            self.assertIn('PAYMENT_METHOD_IMPACT_CHANGED', results)
            fresh = payment_method_impact(self.account, self.user, method)
            self.assertEqual(len(fresh['unresolved_payments']), 1)
            request_payment_method_removal(self.account, self.user, method, expected_impact=fresh['confirmation_hash'])
        else:
            self.assertEqual(row.state, 'cancelled')
        self.assertFalse(Subscription.objects.get(pk=sub['id']).auto_renew)
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        self.assertEqual(AuditEvent.objects.filter(action='payment_method.remove_requested').count(), 1)

    def test_parallel_renewal_creation_and_cancel_dispatch(self):
        from .billing import claim_payment_dispatch
        sub = self.activate()
        results = self.parallel([lambda: self.prepare(sub['id'])]*2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(Order.objects.count(), 2)
        sent_renewal_notice_fixture(self.account,results[0]['id'])
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        self.parallel([lambda: cancel_subscription(self.account, self.user, sub['id']),
                       lambda: claim_payment_dispatch(self.account, results[0]['id'])])
        attempt = PaymentAttempt.objects.get()
        self.assertIn(attempt.state, ('pending', 'cancelled'))
        self.assertEqual(attempt.dispatched_at is not None, attempt.state == 'pending')
        self.assertFalse(Subscription.objects.get().auto_renew)
        self.assertFalse(claim_payment_dispatch(self.account, attempt.pk)['dispatch'])

    def test_parallel_recovery_creates_and_dispatches_once(self):
        from .billing import claim_payment_dispatch
        _, original = self.failed_renewal()
        results = self.parallel([lambda:self.recover(original)]*2)
        self.assertEqual(results[0],results[1])
        self.assertEqual(Order.objects.count(),3)
        self.assertEqual(PaymentAttempt.objects.filter(recovery_of__isnull=False).count(),1)
        claims = self.parallel([lambda:claim_payment_dispatch(self.account,results[0]['id'])]*2)
        self.assertEqual(sum(item['dispatch'] for item in claims),1)


@override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
class SharedTaskRulesTests(TransactionTestCase):
    setUp = SubscriptionTests.setUp
    paid = SubscriptionTests.paid
    activate = SubscriptionTests.activate
    business_paid = SubscriptionTests.business_paid
    business_member = SubscriptionTests.business_member
    parallel = SubscriptionConcurrencyTests.parallel

    def task(self, key='actual-task', mode='seat_included'):
        from .billing import start_shared_task
        self.member = self.business_member(mode)
        return start_shared_task(self.account,self.member.pk,key,'voc')

    def test_shared_usage_ignores_legacy_member_limit_but_preserves_other_seat_ownership(self):
        from .billing import consume_shared_task, set_shared_limit
        task = self.task()
        set_shared_limit(self.account,self.user,self.member.pk,'0',expected_version=0,request_key='legacy-cap')
        result = consume_shared_task(self.account,task['id'],'actual-use','27.25')
        self.assertTrue(result['accepted'])
        self.assertEqual(result['credits'],'27.25')
        self.assertEqual(credits_summary(self.account)['reserved'],'0')
        self.assertFalse(consume_shared_task(self.account,task['id'],'cannot-use-other-seat','0.01')['accepted'])
        self.assertEqual(credits_summary(self.account)['available'],'17.25')

    def test_exact_task_total_rounds_once_and_duplicate_events_do_not_accumulate(self):
        from .billing import consume_shared_task, complete_shared_task
        for index, amount in enumerate(('0.004','0.005')):
            from .billing import start_shared_task
            if index == 0:
                task=self.task()
            else:
                task=start_shared_task(self.account,self.member.pk,'second-task','voc')
            consume_shared_task(self.account,task['id'],'first',amount)
            result=consume_shared_task(self.account,task['id'],'second',amount)
            self.assertEqual(result['credits'],'0.01')
            self.assertEqual(consume_shared_task(self.account,task['id'],'second',amount),result)
            complete_shared_task(self.account,task['id'],result_persisted=True)
            self.assertEqual(consume_shared_task(self.account,task['id'],'second',amount)['credits'],'0.01')
            with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
                consume_shared_task(self.account,task['id'],'second','0.006')

    def test_insufficient_actual_usage_survives_pause_and_only_manual_retry_settles(self):
        from .billing import consume_shared_task, resume_shared_task
        from .models import SharedTask, Reservation
        task=self.task(mode='shared_only')
        result=consume_shared_task(self.account,task['id'],'actual-over-balance','11.004')
        self.assertFalse(result['accepted']);self.assertEqual(result['status'],'paused')
        self.assertEqual(SharedTask.objects.get(pk=task['id']).exact_quantity,'11.004')
        self.assertEqual(Reservation.objects.get(shared_task_id=task['id']).state,'pending')
        grant_credits(self.account,'purchased','manual-replenish','2',self.anchor+timedelta(days=2))
        self.assertFalse(consume_shared_task(self.account,task['id'],'actual-over-balance','11.004')['accepted'])
        resume_shared_task(self.account,self.user,task['id'])
        result=consume_shared_task(self.account,task['id'],'actual-over-balance','11.004')
        self.assertTrue(result['accepted']);self.assertEqual(result['credits'],'11')
        self.assertEqual(SharedTask.objects.get(pk=task['id']).exact_quantity,'11.004')
        self.assertEqual(credits_summary(self.account)['reserved'],'0')

    def test_month_boundary_and_long_running_tasks_do_not_hold_expired_points(self):
        from .billing import consume_shared_task
        task=self.task(mode='shared_only')
        self.assertEqual(credits_summary(self.account)['reserved'],'0')
        self.now.return_value=month_boundary(self.anchor,1)
        result=consume_shared_task(self.account,task['id'],'after-expiry','1')
        self.assertFalse(result['accepted']);self.assertEqual(result['status'],'paused')
        self.assertEqual(result['credits'],'0')

    def test_removed_member_running_task_continues_but_paused_task_never_resumes(self):
        from django.contrib.auth import get_user_model
        from .billing import start_shared_task, consume_shared_task, resume_shared_task, complete_shared_task
        from .subscriptions import remove_member, assign_seat
        from .models import PaidSeat
        self.member=self.business_member('shared_only')
        employee=get_user_model().objects.create_user(username='actual-employee')
        member=Membership.objects.create(account=self.account,user=employee,role='member')
        seat=PaidSeat.objects.filter(membership__isnull=True).first()
        assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=0,request_key='employee-seat')
        running=start_shared_task(self.account,member.pk,'employee-running','voc')
        paused=start_shared_task(self.account,member.pk,'employee-paused','voc')
        consume_shared_task(self.account,paused['id'],'cost','11')
        remove_member(self.account,self.user,member.pk,request_key='offboard')
        self.assertTrue(consume_shared_task(self.account,running['id'],'cost','1')['accepted'])
        complete_shared_task(self.account,running['id'],result_persisted=True)
        grant_credits(self.account,'purchased','after-removal','20',self.anchor+timedelta(days=2))
        with self.assertRaisesRegex(ValueError,'TASK_RESUME_FORBIDDEN'):
            resume_shared_task(self.account,self.user,paused['id'])

    def test_metadata_projection_has_no_contents_and_cannot_cross_account(self):
        from .billing import consume_shared_task, shared_task_usage
        from .subscriptions import organization_summary
        task=self.task();consume_shared_task(self.account,task['id'],'cost','1.25')
        result=shared_task_usage(self.account,self.user,membership_id=self.member.pk)
        self.assertEqual(set(result['items'][0]),{'id','occurred_at','task_type','status','credits'})
        self.assertEqual(result['items'][0]['credits'],'1.25')
        self.assertEqual(organization_summary(self.account,admin_actor=self.user)['members'][0]['total_credits'],'1.25')
        with self.assertRaisesRegex(ValueError,'FORBIDDEN'):
            shared_task_usage(self.account,self.other_user,membership_id=self.member.pk)
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            consume_shared_task(self.other,task['id'],'cost','1')

    def test_unknown_actual_amount_is_not_silently_zero(self):
        from .billing import consume_shared_task
        from .models import Reservation
        task=self.task()
        with self.assertRaises(ValueError):consume_shared_task(self.account,task['id'],'unknown',None)
        self.assertFalse(Reservation.objects.filter(shared_task_id=task['id']).exists())

    def test_parallel_actual_events_cannot_double_count_or_double_debit(self):
        from .billing import consume_shared_task
        from .models import SharedTask
        task=self.task(mode='shared_only')
        self.parallel([lambda:consume_shared_task(self.account,task['id'],'same-event','0.004')]*2)
        self.assertEqual(SharedTask.objects.get(pk=task['id']).exact_quantity,'0.004')
        self.assertEqual(SharedTask.objects.get(pk=task['id']).debits.count(),1)
        self.parallel([lambda:consume_shared_task(self.account,task['id'],'second','0.004'),
                       lambda:consume_shared_task(self.account,task['id'],'third','0.004')])
        self.assertEqual(credits_summary(self.account)['consumed'],'0.01')


@override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS={
    'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}})
class FixedTopupRulesTests(TransactionTestCase):
    setUp = SubscriptionTests.setUp
    paid = SubscriptionTests.paid
    activate = SubscriptionTests.activate
    topup_fixture = RenewalIntentTests.topup_fixture
    parallel = SubscriptionConcurrencyTests.parallel

    def test_current_admin_can_stop_departed_admin_topup_without_claiming_provider_revocation(self):
        from django.contrib.auth import get_user_model
        from .models import TopupAuthorization
        auth = self.authorize()
        previous = self.user
        successor = get_user_model().objects.create_user(username='topup-successor')
        Membership.objects.filter(account=self.account,user=previous).update(is_active=False)
        Membership.objects.create(account=self.account,user=successor,role='owner')
        client = Client(enforce_csrf_checks=True)
        client.force_login(successor)
        session = client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        token = client.get('/api/auth/session').json()['csrf_token']
        route = '/api/billing/automatic-topup/disable'
        data = json.dumps({'authorization_id':auth['id'],'confirm':True})
        self.assertEqual(client.post(route,data,content_type='application/json').status_code,403)
        self.assertEqual(client.post(route,data,content_type='application/json',HTTP_X_CSRFTOKEN=token).status_code,409)
        response = client.post(route,data,content_type='application/json',HTTP_X_CSRFTOKEN=token,
            HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['authorization']['state'],'revoking')
        row = TopupAuthorization.objects.get(pk=auth['id'])
        self.assertIsNone(row.revoked_at)
        self.assertEqual(client.post(route,data,content_type='application/json',HTTP_X_CSRFTOKEN=token,
            HTTP_X_BILLING_ACCOUNT=str(self.account.pk)).status_code,200)
        self.assertEqual(AuditEvent.objects.filter(action='topup.revocation_requested').count(),1)
        Membership.objects.filter(account=self.account,user=successor).update(role='member')
        self.assertEqual(client.post(route,data,content_type='application/json',HTTP_X_CSRFTOKEN=token,
            HTTP_X_BILLING_ACCOUNT=str(self.account.pk)).status_code,403)
        client.force_login(previous)
        self.assertEqual(client.get('/api/billing/automatic-topup').status_code,403)

    def authorize(self, *, with_demand=True):
        from .subscriptions import authorize_topup_preset
        args=self.topup_fixture()
        self.preset={'approved':True,'product_id':str(args['product_id']),'threshold':'2',
            'monthly_cap_minor':1462,'terms_version':'topup-fixture-1','policy_version':'service-month-v1'}
        settings=override_settings(BILLING_TOPUP_PRESETS={'approved':self.preset});settings.enable();self.addCleanup(settings.disable)
        self.authorization=authorize_topup_preset(self.account,self.user,preset_key='approved',
            **{key:args[key] for key in ('payment_method_id','mandate','consent_evidence_hash','expected_version','request_key')})
        if with_demand:
            from .billing import start_shared_task, consume_shared_task
            from .models import PaidSeat
            from .subscriptions import assign_seat
            member=Membership.objects.get(account=self.account,user=self.user)
            seat=PaidSeat.objects.filter(subscription__account=self.account).first()
            assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=0,request_key='demand-seat')
            grant_credits(self.account,'purchased','demand-fixture','1',self.anchor+timedelta(days=2))
            self.demand_task=start_shared_task(self.account,member.pk,'topup-demand','voc')
            self.assertTrue(consume_shared_task(self.account,self.demand_task['id'],'actual','1')['accepted'])
        return self.authorization

    def test_low_shared_balance_without_actual_task_demand_never_creates_order(self):
        from .subscriptions import evaluate_automatic_topup
        from .billing import start_shared_task
        from .models import PaidSeat, TopupBudget
        from .subscriptions import assign_seat
        self.authorize(with_demand=False)
        before=Order.objects.count()
        self.assertEqual(evaluate_automatic_topup(self.account)['reason'],'NO_SHARED_DEMAND')
        member=Membership.objects.get(account=self.account,user=self.user)
        seat=PaidSeat.objects.filter(subscription__account=self.account).first()
        assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=0,request_key='empty-task-seat')
        start_shared_task(self.account,member.pk,'not-yet-used','voc')
        self.assertEqual(evaluate_automatic_topup(self.account)['reason'],'NO_SHARED_DEMAND')
        self.assertEqual(Order.objects.count(),before)
        self.assertFalse(TopupBudget.objects.exists())

    def test_active_member_losing_paid_seat_does_not_trigger_from_running_task(self):
        from .subscriptions import evaluate_automatic_topup, assign_seat
        from .models import PaidSeat, TopupBudget
        self.authorize()
        seat=PaidSeat.objects.get(membership__user=self.user,subscription__account=self.account)
        assign_seat(self.account,self.user,seat.pk,None,expected_version=seat.version,request_key='management-only')
        self.assertEqual(evaluate_automatic_topup(self.account)['reason'],'NO_SHARED_DEMAND')
        self.assertFalse(TopupBudget.objects.exists())

    def test_demand_arriving_after_low_balance_triggers_without_new_crossing(self):
        from .subscriptions import evaluate_automatic_topup, assign_seat
        from .billing import start_shared_task, consume_shared_task
        from .models import PaidSeat
        self.authorize(with_demand=False)
        self.assertFalse(evaluate_automatic_topup(self.account)['triggered'])
        member=Membership.objects.get(account=self.account,user=self.user)
        seat=PaidSeat.objects.filter(subscription__account=self.account).first()
        assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=0,request_key='late-demand-seat')
        task=start_shared_task(self.account,member.pk,'late-demand','voc')
        self.assertFalse(consume_shared_task(self.account,task['id'],'actual','1')['accepted'])
        self.assertTrue(evaluate_automatic_topup(self.account)['triggered'])

    def test_finished_task_or_removed_paused_task_cannot_cause_charge(self):
        from .subscriptions import evaluate_automatic_topup, claim_automatic_topup, topup_budget_summary
        from .billing import complete_shared_task, start_shared_task, consume_shared_task
        from .models import SharedTask, TopupBudget, TopupAuthorization
        self.authorize()
        attempt=evaluate_automatic_topup(self.account)['attempt']
        complete_shared_task(self.account,self.demand_task['id'],result_persisted=True)
        result=claim_automatic_topup(self.account,attempt['id'])
        self.assertEqual((result['dispatch'],result['status'],result['reason']),(False,'cancelled','TOPUP_NOT_NEEDED'))
        row=PaymentAttempt.objects.select_related('order').get(pk=attempt['id'])
        self.assertEqual(row.order.status,'closed')
        self.assertIsNone(row.dispatched_at)
        self.assertFalse(row.events.exists());self.assertFalse(row.order.payment_facts.exists())
        self.assertTrue(TopupAuthorization.objects.get(pk=self.authorization['id']).trigger_armed)
        budget=TopupBudget.objects.get(attempt=row)
        self.assertEqual(topup_budget_summary(self.account,starts_at=budget.starts_at,ends_at=budget.ends_at,
            policy_version=budget.policy_version)['reserved_minor'],0)
        self.assertFalse(claim_automatic_topup(self.account,row.pk)['dispatch'])
        self.assertEqual(AuditEvent.objects.filter(action='topup.intent_cancelled',request_id=row.pk).count(),1)
        SharedTask.objects.filter(pk=self.demand_task['id']).update(state='paused',resume_blocked=True)
        self.assertEqual(evaluate_automatic_topup(self.account)['reason'],'NO_SHARED_DEMAND')
        member=Membership.objects.get(account=self.account,user=self.user)
        task=start_shared_task(self.account,member.pk,'replacement-demand','voc')
        self.assertFalse(consume_shared_task(self.account,task['id'],'actual','1')['accepted'])
        fresh=evaluate_automatic_topup(self.account)
        self.assertTrue(fresh['triggered'])
        self.assertNotEqual(fresh['attempt']['id'],str(row.pk))

    def test_expired_unsubmitted_topup_claim_closes_locally_at_ten_minutes(self):
        from .subscriptions import evaluate_automatic_topup, claim_automatic_topup
        from .models import TopupAuthorization
        self.authorize();attempt=evaluate_automatic_topup(self.account)['attempt']
        self.now.return_value=self.anchor+timedelta(minutes=10)
        result=claim_automatic_topup(self.account,attempt['id'])
        self.assertEqual((result['dispatch'],result['status'],result['reason']),(False,'expired','PAYMENT_EXPIRED'))
        row=PaymentAttempt.objects.select_related('order').get(pk=attempt['id'])
        self.assertEqual(row.order.status,'closed');self.assertIsNone(row.dispatched_at)
        self.assertFalse(row.events.exists());self.assertFalse(row.order.payment_facts.exists())
        self.assertEqual(AuditEvent.objects.filter(action='topup.intent_expired',request_id=row.pk).count(),1)
        self.assertTrue(TopupAuthorization.objects.get(pk=self.authorization['id']).trigger_armed)
        self.assertTrue(evaluate_automatic_topup(self.account)['triggered'])

    def test_scheduler_closes_unclaimed_expiry_before_building_replacement(self):
        from .subscriptions import evaluate_automatic_topup, topup_budget_summary
        from .models import TopupBudget
        self.authorize();attempt=evaluate_automatic_topup(self.account)['attempt']
        self.now.return_value=self.anchor+timedelta(minutes=11)
        self.assertEqual(evaluate_automatic_topup(self.account),{'triggered':False,'reason':'TOPUP_INTENT_EXPIRED'})
        row=PaymentAttempt.objects.select_related('order').get(pk=attempt['id'])
        self.assertEqual((row.state,row.order.status),('expired','closed'))
        budget=TopupBudget.objects.get(attempt=row)
        totals=topup_budget_summary(self.account,starts_at=budget.starts_at,ends_at=budget.ends_at,
            policy_version=budget.policy_version)
        self.assertEqual((totals['reserved_minor'],totals['spent_minor']),(0,0))
        self.assertEqual(TopupBudget.objects.count(),1)
        fresh=evaluate_automatic_topup(self.account)
        self.assertTrue(fresh['triggered']);self.assertNotEqual(fresh['attempt']['id'],attempt['id'])

    def test_expiry_cleanup_survives_subsequent_preset_error(self):
        from .subscriptions import evaluate_automatic_topup
        self.authorize();attempt=evaluate_automatic_topup(self.account)['attempt']
        self.now.return_value=self.anchor+timedelta(minutes=11)
        with override_settings(BILLING_TOPUP_PRESETS={'approved':{**self.preset,'monthly_cap_minor':2193}}):
            self.assertEqual(evaluate_automatic_topup(self.account)['reason'],'TOPUP_INTENT_EXPIRED')
            with self.assertRaisesRegex(ValueError,'TOPUP_CONFIGURATION_CHANGED'):
                evaluate_automatic_topup(self.account)
        row=PaymentAttempt.objects.select_related('order').get(pk=attempt['id'])
        self.assertEqual((row.state,row.order.status),('expired','closed'))
        self.assertEqual(AuditEvent.objects.filter(action='topup.intent_expired',request_id=row.pk).count(),1)

    def test_expired_unknown_attempt_is_never_closed_or_replaced(self):
        from .subscriptions import evaluate_automatic_topup, claim_automatic_topup
        from .models import TopupBudget
        self.authorize();attempt=evaluate_automatic_topup(self.account)['attempt']
        self.assertTrue(claim_automatic_topup(self.account,attempt['id'])['dispatch'])
        PaymentAttempt.objects.filter(pk=attempt['id']).update(state='unknown')
        self.now.return_value=self.anchor+timedelta(minutes=11)
        self.assertFalse(evaluate_automatic_topup(self.account)['triggered'])
        self.assertFalse(claim_automatic_topup(self.account,attempt['id'])['dispatch'])
        row=PaymentAttempt.objects.select_related('order').get(pk=attempt['id'])
        self.assertEqual((row.state,row.order.status),('unknown','pending_payment'))
        self.assertEqual(TopupBudget.objects.count(),1)
        self.assertFalse(AuditEvent.objects.filter(action__startswith='topup.intent_',request_id=row.pk).exists())

    def test_created_attempt_with_reconciliation_or_dispatch_evidence_cannot_be_closed(self):
        from .subscriptions import evaluate_automatic_topup, claim_automatic_topup
        self.authorize();attempt=evaluate_automatic_topup(self.account)['attempt']
        self.now.return_value=self.anchor+timedelta(minutes=11)
        for evidence in ({'needs_reconciliation':True},{'dispatch_authorization':{'fixture':'claimed'}}):
            with self.subTest(evidence=next(iter(evidence))):
                PaymentAttempt.objects.filter(pk=attempt['id']).update(**evidence)
                self.assertFalse(evaluate_automatic_topup(self.account)['triggered'])
                result=claim_automatic_topup(self.account,attempt['id'])
                self.assertFalse(result['dispatch'])
                row=PaymentAttempt.objects.select_related('order').get(pk=attempt['id'])
                self.assertEqual((row.state,row.order.status),('created','pending_payment'))
                PaymentAttempt.objects.filter(pk=row.pk).update(needs_reconciliation=False,dispatch_authorization=None)
        self.assertFalse(AuditEvent.objects.filter(action__startswith='topup.intent_',request_id=row.pk).exists())

    def test_created_attempt_with_provider_event_or_payment_fact_cannot_be_closed(self):
        from .subscriptions import evaluate_automatic_topup, claim_automatic_topup
        from .models import PaymentAttemptEvent
        self.authorize();attempt=evaluate_automatic_topup(self.account)['attempt']
        row=PaymentAttempt.objects.get(pk=attempt['id'])
        PaymentAttemptEvent.objects.create(attempt=row,event_key='synthetic-receipt',source='query',
            outcome='unknown',request_hash='a'*64,evidence_hash='b'*64,applied=False)
        self.now.return_value=self.anchor+timedelta(minutes=11)
        self.assertFalse(evaluate_automatic_topup(self.account)['triggered'])
        self.assertFalse(claim_automatic_topup(self.account,row.pk)['dispatch'])
        row.refresh_from_db();self.assertEqual(row.state,'created')
        # Remove only the synthetic event to independently test the funds guard.
        PaymentAttemptEvent.objects.filter(attempt=row).delete()
        PaymentFact.objects.create(order=row.order,channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='unresolved-fixture-payment',amount_minor=731,currency='CNY',
            evidence_hash='c'*64,disposition='review',paid_at=self.anchor)
        self.assertFalse(evaluate_automatic_topup(self.account)['triggered'])
        self.assertFalse(claim_automatic_topup(self.account,row.pk)['dispatch'])
        row.refresh_from_db();self.assertEqual((row.state,row.order.status),('created','pending_payment'))
        self.assertFalse(AuditEvent.objects.filter(action__startswith='topup.intent_',request_id=row.pk).exists())

    def test_scheduler_closes_unsubmitted_intent_when_task_finishes(self):
        from .subscriptions import evaluate_automatic_topup
        from .billing import complete_shared_task
        from .models import TopupAuthorization
        self.authorize();attempt=evaluate_automatic_topup(self.account)['attempt']
        complete_shared_task(self.account,self.demand_task['id'],result_persisted=True)
        self.assertEqual(evaluate_automatic_topup(self.account)['reason'],'NO_SHARED_DEMAND')
        row=PaymentAttempt.objects.select_related('order').get(pk=attempt['id'])
        self.assertEqual((row.state,row.order.status),('cancelled','closed'))
        self.assertIsNone(row.dispatched_at)
        self.assertTrue(TopupAuthorization.objects.get(pk=self.authorization['id']).trigger_armed)
        self.assertEqual(AuditEvent.objects.filter(action='topup.intent_cancelled',request_id=row.pk).count(),1)

    def test_replenishment_before_dispatch_closes_old_intent_in_scheduler_and_claim(self):
        from .subscriptions import evaluate_automatic_topup,claim_automatic_topup
        self.authorize();first=evaluate_automatic_topup(self.account)['attempt']
        grant_credits(self.account,'purchased','manual-before-evaluate','2',self.anchor+timedelta(days=1))
        self.assertEqual(evaluate_automatic_topup(self.account)['reason'],'ABOVE_THRESHOLD')
        self.assertEqual(PaymentAttempt.objects.get(pk=first['id']).state,'cancelled')
        self.lower_balance();second=evaluate_automatic_topup(self.account)['attempt']
        grant_credits(self.account,'purchased','manual-before-claim','2',self.anchor+timedelta(days=1))
        result=claim_automatic_topup(self.account,second['id'])
        self.assertEqual((result['dispatch'],result['status'],result['reason']),(False,'cancelled','TOPUP_NOT_NEEDED'))
        for attempt in (first,second):
            row=PaymentAttempt.objects.select_related('order').get(pk=attempt['id'])
            self.assertEqual(row.order.status,'closed');self.assertIsNone(row.dispatched_at)
            self.assertFalse(row.events.exists());self.assertFalse(row.order.payment_facts.exists())

    def test_local_closure_audit_failure_rolls_back_attempt_order_and_trigger(self):
        from .subscriptions import evaluate_automatic_topup,claim_automatic_topup
        from .models import TopupAuthorization
        self.authorize();attempt=evaluate_automatic_topup(self.account)['attempt']
        self.now.return_value=self.anchor+timedelta(minutes=11)
        with patch('backend.commerce.subscriptions.AuditEvent.objects.create',side_effect=RuntimeError('audit-unavailable')):
            with self.assertRaisesRegex(RuntimeError,'audit-unavailable'):
                claim_automatic_topup(self.account,attempt['id'])
        row=PaymentAttempt.objects.select_related('order').get(pk=attempt['id'])
        self.assertEqual((row.state,row.order.status),('created','pending_payment'))
        self.assertFalse(TopupAuthorization.objects.get(pk=self.authorization['id']).trigger_armed)
        self.assertFalse(claim_automatic_topup(self.account,attempt['id'])['dispatch'])
        self.assertEqual(AuditEvent.objects.filter(action='topup.intent_expired',request_id=row.pk).count(),1)

    def test_paused_task_with_refunded_seat_does_not_create_shared_demand(self):
        from .subscriptions import evaluate_automatic_topup
        from .billing import consume_shared_task
        self.authorize()
        self.assertFalse(consume_shared_task(self.account,self.demand_task['id'],'pending','1')['accepted'])
        Subscription.objects.filter(account=self.account).update(refund_cutoff_at=self.anchor)
        count=Order.objects.count()
        self.assertEqual(evaluate_automatic_topup(self.account)['reason'],'NO_SHARED_DEMAND')
        self.assertEqual(Order.objects.count(),count)

    def test_teams_waits_for_shared_demand_and_other_seat_credits_do_not_mask_it(self):
        from .subscriptions import register_verified_payment_method, evaluate_automatic_topup
        from .billing import start_shared_task, consume_shared_task
        from .tests import fixture_product
        self.business_paid=SubscriptionTests.business_paid.__get__(self)
        member=SubscriptionTests.business_member(self,'seat_included')
        method=register_verified_payment_method(self.account,channel='fixture',merchant_reference='fixture-shenzhen',
            fingerprint='c'*64,evidence_hash='d'*64)
        pack=fixture_product(audience='business')
        args=dict(payment_method_id=method,product_id=pack.pk,
            mandate={**self.mandate,'agreement_reference':'teams-topup'},consent_evidence_hash='e'*64,
            expected_version=0,request_key='teams-consent')
        self.topup_fixture=lambda:args
        self.authorize(with_demand=False)
        # The existing purchased batch expires; both seats still have monthly Credits.
        self.now.return_value=self.anchor+timedelta(days=3)
        task=start_shared_task(self.account,member.pk,'teams-demand','voc')
        self.assertTrue(consume_shared_task(self.account,task['id'],'only-included','1')['accepted'])
        self.assertEqual(evaluate_automatic_topup(self.account)['reason'],'NO_SHARED_DEMAND')
        self.assertFalse(consume_shared_task(self.account,task['id'],'requires-shared','20')['accepted'])
        self.assertTrue(evaluate_automatic_topup(self.account)['triggered'])

    def pay(self, attempt, reference):
        order_id=PaymentAttempt.objects.get(pk=attempt['id']).order_id
        record_confirmed_payment(self.account,order_id,channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference=reference,amount_minor=731,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)

    def lower_balance(self):
        from .models import PaidSeat
        from .subscriptions import assign_seat
        from .billing import reserve_credits, settle_credits
        member=Membership.objects.get(account=self.account,user=self.user)
        seat=PaidSeat.objects.filter(subscription__account=self.account,membership__isnull=True).first()
        if seat and not PaidSeat.objects.filter(subscription__account=self.account,membership=member).exists():
            assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=0,request_key='topup-seat')
        amount=credits_summary(self.account)['available']
        reservation=reserve_credits(self.account,'consume-before-'+str(PaymentAttempt.objects.count()),amount,membership_id=member.pk)
        settle_credits(self.account,reservation['id'],amount)

    def test_unconfigured_presets_and_pack_not_above_threshold_fail_closed(self):
        from .subscriptions import _topup_preset, authorize_topup_preset
        with self.assertRaisesRegex(ValueError,'TOPUP_PRESET_REQUIRED'):_topup_preset('missing')
        auth=self.authorize()
        self.preset['threshold']='10'
        with override_settings(BILLING_TOPUP_PRESETS={'approved':self.preset}):
            from .models import TopupAuthorization
            previous=TopupAuthorization.objects.get(pk=auth['id'])
            with self.assertRaisesRegex(ValueError,'TOPUP_PRESET_MISMATCH'):
                authorize_topup_preset(self.account,self.user,preset_key='approved',payment_method_id=previous.payment_method_id,
                    mandate={**previous.mandate,'agreement_reference':'new'},consent_evidence_hash='f'*64,
                    expected_version=1,request_key='invalid-large-threshold')

    def test_one_pack_per_crossing_unknown_does_not_duplicate_and_budget_is_not_refunded(self):
        from .subscriptions import evaluate_automatic_topup
        from .models import TopupBudget
        self.authorize()
        first=evaluate_automatic_topup(self.account)
        self.assertTrue(first['triggered'])
        self.assertFalse(evaluate_automatic_topup(self.account)['triggered'])
        PaymentAttempt.objects.filter(pk=first['attempt']['id']).update(state='unknown',dispatched_at=self.anchor)
        self.assertFalse(evaluate_automatic_topup(self.account)['triggered'])
        self.assertEqual(TopupBudget.objects.count(),1)
        self.pay(first['attempt'],'topup-first')
        self.assertEqual(evaluate_automatic_topup(self.account)['reason'],'ABOVE_THRESHOLD')
        self.lower_balance()
        second=evaluate_automatic_topup(self.account)
        self.assertTrue(second['triggered']);self.pay(second['attempt'],'topup-second')
        self.lower_balance()
        self.assertEqual(evaluate_automatic_topup(self.account)['reason'],'TOPUP_BUDGET_EXCEEDED')
        self.assertEqual(TopupBudget.objects.count(),2)

    def test_authorizers_removal_does_not_cancel_consent_current_admin_can_trigger(self):
        from django.contrib.auth import get_user_model
        from .subscriptions import evaluate_automatic_topup, remove_member
        self.authorize()
        original=Membership.objects.get(account=self.account,user=self.user)
        original.role='billing_admin';original.save(update_fields=['role'])
        current=get_user_model().objects.create_user(username='new-topup-owner')
        Membership.objects.create(account=self.account,user=current,role='owner')
        remove_member(self.account,current,original.pk,request_key='authorizer-left')
        result=evaluate_automatic_topup(self.account)
        self.assertTrue(result['triggered'])
        self.assertEqual(PaymentAttempt.objects.get(pk=result['attempt']['id']).order.created_by_id,current.pk)
        from .subscriptions import claim_automatic_topup, prepare_renewal_attempt
        self.assertTrue(claim_automatic_topup(self.account,result['attempt']['id'])['dispatch'])
        self.assertFalse(claim_automatic_topup(self.account,result['attempt']['id'])['dispatch'])
        subscription=Subscription.objects.get(account=self.account)
        end=subscription.periods.get().ends_at
        charge_at=end-timedelta(minutes=30)
        self.now.return_value=charge_at
        renewal=prepare_renewal_attempt(self.account,subscription.pk,cycle_index=1,dispatch_not_before=charge_at,expires_at=end)
        self.assertEqual(PaymentAttempt.objects.get(pk=renewal['id']).order.created_by_id,current.pk)
        from backend.notifications import prepare_renewal_notice
        from backend.models import NotificationDelivery
        notice=prepare_renewal_notice(self.account,subscription.pk,1,charge_at=charge_at)
        NotificationDelivery.objects.create(notification=notice,user=current,channel='email',state='sent',
            provider_reference='fixture-current-manager-notice',confirmed_at=charge_at-timedelta(days=1))
        from .billing import claim_payment_dispatch
        with override_settings(BILLING_RENEWAL_CHARGING_ENABLED=True):
            self.assertTrue(claim_payment_dispatch(self.account,renewal['id'])['dispatch'])
        self.assertEqual(subscription.initial_order.created_by_id,self.user.pk)

    def test_changed_preset_never_silently_changes_existing_consent(self):
        from .subscriptions import evaluate_automatic_topup
        self.authorize()
        with override_settings(BILLING_TOPUP_PRESETS={'approved':{**self.preset,'monthly_cap_minor':2193}}):
            with self.assertRaisesRegex(ValueError,'TOPUP_CONFIGURATION_CHANGED'):
                evaluate_automatic_topup(self.account)

    def test_threshold_equality_does_not_trigger_and_unknown_payment_blocks_rearmed_crossing(self):
        from .subscriptions import evaluate_automatic_topup
        from .models import TopupBudget
        self.authorize()
        grant_credits(self.account,'purchased','at-threshold','2',self.anchor+timedelta(days=1))
        self.assertEqual(evaluate_automatic_topup(self.account)['reason'],'ABOVE_THRESHOLD')
        self.lower_balance()
        first=evaluate_automatic_topup(self.account)
        PaymentAttempt.objects.filter(pk=first['attempt']['id']).update(state='unknown',dispatched_at=self.anchor)
        grant_credits(self.account,'purchased','cross-up','2',self.anchor+timedelta(days=1))
        evaluate_automatic_topup(self.account)
        self.lower_balance()
        self.assertEqual(evaluate_automatic_topup(self.account)['reason'],'TOPUP_PENDING')
        self.assertEqual(TopupBudget.objects.count(),1)

    def test_overlapping_service_month_carries_spent_budget_across_new_anchor(self):
        from .subscriptions import evaluate_automatic_topup, topup_budget_summary
        self.authorize();first=evaluate_automatic_topup(self.account);self.pay(first['attempt'],'before-upgrade')
        window=dict(starts_at=self.anchor+timedelta(days=15),ends_at=self.anchor+timedelta(days=45),
                    policy_version='new-service-month')
        self.assertEqual(topup_budget_summary(self.account,**window)['spent_minor'],0)
        self.assertEqual(topup_budget_summary(self.account,**window,include_overlapping=True)['spent_minor'],731)

    def test_refunding_pack_does_not_restore_service_month_money_budget(self):
        from .subscriptions import evaluate_automatic_topup, topup_budget_summary
        from .billing import reserve_credit_refund, resolve_credit_refund
        from .models import TopupBudget
        self.authorize();first=evaluate_automatic_topup(self.account);self.pay(first['attempt'],'refunded-topup')
        attempt=PaymentAttempt.objects.get(pk=first['attempt']['id'])
        refund=reserve_credit_refund(self.account,attempt.order_id,'pack-refund',review_evidence_hash='d'*64)
        resolve_credit_refund(self.account,refund['id'],'refunded',evidence_hash='e'*64)
        budget=TopupBudget.objects.get(attempt=attempt)
        self.assertEqual(topup_budget_summary(self.account,starts_at=budget.starts_at,ends_at=budget.ends_at,
            policy_version=budget.policy_version)['spent_minor'],731)

    def test_parallel_below_threshold_checks_create_only_one_intent(self):
        from .subscriptions import evaluate_automatic_topup
        from .models import TopupBudget
        self.authorize()
        results=self.parallel([lambda:evaluate_automatic_topup(self.account)]*2)
        self.assertEqual(sum(item['triggered'] for item in results),1)
        self.assertEqual(TopupBudget.objects.count(),1)


@override_settings(BILLING_SALES_ENABLED=True,BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS,
    BILLING_TERMS_DOCUMENTS={'fixture-1':{'approved':True,'zh':{'title':'测试','paragraphs':['仅供本地测试']},
                                       'en':{'title':'Fixture','paragraphs':['Local test only']}}})
class ApprovedPlanChangeTests(TransactionTestCase):
    setUp=SubscriptionTests.setUp
    paid=SubscriptionTests.paid
    activate=SubscriptionTests.activate
    parallel=SubscriptionConcurrencyTests.parallel

    def source(self, cycle='monthly'):
        self.product=ProductVersion.objects.create(code='ranked-original',version=1,kind='subscription',audience='personal',
            billing_cycle=cycle,unit_amount_minor=12345,credits_quantity='17.25',entitlements={'upgrade_rank':1},
            entitlement_version='fixture-1',credit_rule_version='fixture-1',terms_version='fixture-1',
            state='published',effective_at=self.anchor-timedelta(days=1))
        self.subscription=self.activate(self.paid())
        return Subscription.objects.get(pk=self.subscription['id'])

    def target(self, cycle='monthly', amount=24690):
        return ProductVersion.objects.create(code='ranked-new-'+cycle,version=1,kind='subscription',audience='personal',
            billing_cycle=cycle,unit_amount_minor=amount,credits_quantity='35',entitlements={'upgrade_rank':2},
            entitlement_version='fixture-1',credit_rule_version='fixture-1',terms_version='fixture-1',
            state='published',effective_at=self.anchor-timedelta(days=1))

    def quote(self, source, target):
        from .subscriptions import subscription_change_quote
        return subscription_change_quote(self.account,self.user,source.pk,target.pk,request_key='quote-change')

    def confirm_and_activate(self, source, quote):
        from .subscriptions import confirm_subscription_change, activate_subscription_change
        change=confirm_subscription_change(self.account,self.user,source.pk,quote['id'],
            agreement_acceptance='fixture-1',request_key='confirm-change')
        if int(change['due_minor']):
            record_confirmed_payment(self.account,change['order_id'],channel='fixture',merchant_reference='fixture-shenzhen',
                transaction_reference='new-change-payment',amount_minor=int(change['due_minor']),currency='CNY',
                paid_at=timezone.now(),evidence_hash='c'*64)
        result=activate_subscription_change(self.account,change['id'],activated_at=timezone.now(),
            mandate={**self.mandate,'agreement_reference':'new-upgrade-mandate'},old_authorization_evidence_hash='d'*64)
        return result

    def test_month_to_month_collects_new_full_price_and_refunds_old_unused_cash_separately(self):
        source=self.source();target=self.target()
        self.now.return_value=self.anchor+(month_boundary(self.anchor,1)-self.anchor)/2
        quote=self.quote(source,target)
        self.assertEqual((quote['due_minor'],quote['discount_minor'],quote['refund_minor']),('24690','0','6173'))
        result=self.confirm_and_activate(source,quote)
        from .models import SubscriptionChange, ServiceRefund
        change=SubscriptionChange.objects.get(pk=result['id'])
        self.assertEqual(change.cash_sources,[])
        self.assertEqual(ServiceRefund.objects.get(subscription=source).amount_minor,6173)
        self.assertEqual(credits_summary(self.account)['available'],'35')
        new=change.new_subscription
        self.assertEqual(new.original_anchor,timezone.now())
        self.assertEqual(new.periods.get().ends_at,month_boundary(timezone.now(),1))

    def test_month_to_year_uses_only_unused_actual_cash_even_if_current_price_increases(self):
        source=self.source();target=self.target('annual',148140)
        ProductVersion.objects.create(code='ranked-original',version=2,kind='subscription',audience='personal',
            billing_cycle='monthly',unit_amount_minor=24690,credits_quantity='17.25',entitlements={'upgrade_rank':1},
            entitlement_version='fixture-1',credit_rule_version='fixture-1',terms_version='fixture-1',
            state='published',effective_at=self.anchor)
        self.now.return_value=self.anchor+(month_boundary(self.anchor,1)-self.anchor)/2
        quote=self.quote(source,target)
        self.assertEqual(quote['due_minor'],'141968')
        self.assertEqual(quote['refund_minor'],'0')
        result=self.confirm_and_activate(source,quote)
        from .models import SubscriptionChange
        new=SubscriptionChange.objects.get(pk=result['id']).new_subscription
        self.assertEqual(new.periods.get().ends_at,month_boundary(timezone.now(),12))
        self.assertEqual(credits_summary(self.account)['available'],'35')

    def test_year_to_year_uses_current_price_floor_cash_and_never_refunds_promotion(self):
        source=self.source('annual');target=self.target('annual')
        ProductVersion.objects.create(code='ranked-original',version=2,kind='subscription',audience='personal',
            billing_cycle='annual',unit_amount_minor=37035,credits_quantity='17.25',entitlements={'upgrade_rank':1},
            entitlement_version='fixture-1',credit_rule_version='fixture-1',terms_version='fixture-1',
            state='published',effective_at=self.anchor)
        end=month_boundary(self.anchor,12)
        self.now.return_value=self.anchor+(end-self.anchor)/2
        quote=self.quote(source,target)
        self.assertEqual((quote['due_minor'],quote['refund_minor']),('6173','0'))
        self.confirm_and_activate(source,quote)
        from .models import SubscriptionChange, ServiceRefund
        change=SubscriptionChange.objects.get(subscription=source)
        self.assertLess(sum(row['amount_minor'] for row in change.cash_sources),int(quote['discount_minor']))
        self.assertFalse(ServiceRefund.objects.exists())
        self.assertEqual(credits_summary(self.account)['available'],'35')

    def test_pending_unpaid_cannot_activate_and_quote_retry_is_identical(self):
        source=self.source();target=self.target()
        quote=self.quote(source,target)
        self.assertEqual(self.quote(source,target),quote)
        from .subscriptions import confirm_subscription_change, activate_subscription_change
        change=confirm_subscription_change(self.account,self.user,source.pk,quote['id'],agreement_acceptance='fixture-1',request_key='confirm-change')
        with self.assertRaisesRegex(ValueError,'PAYMENT_NOT_CONFIRMED'):
            activate_subscription_change(self.account,change['id'],activated_at=timezone.now(),mandate=self.mandate,
                old_authorization_evidence_hash='d'*64)
        source.refresh_from_db();self.assertIsNone(source.superseded_at)

    def test_zero_due_change_uses_credit_evidence_not_fake_payment_fact(self):
        source=self.source('annual');target=self.target('annual',13000)
        ProductVersion.objects.create(code='ranked-original',version=2,kind='subscription',audience='personal',
            billing_cycle='annual',unit_amount_minor=26000,credits_quantity='17.25',entitlements={'upgrade_rank':1},
            entitlement_version='fixture-1',credit_rule_version='fixture-1',terms_version='fixture-1',
            state='published',effective_at=self.anchor)
        quote=self.quote(source,target);self.assertEqual(quote['due_minor'],'0')
        result=self.confirm_and_activate(source,quote)
        self.assertFalse(PaymentFact.objects.filter(order_id=result['order_id']).exists())
        self.assertEqual(result['status'],'activated')

    def test_quote_expires_at_ten_minutes_and_wrong_account_is_denied(self):
        source=self.source();target=self.target();quote=self.quote(source,target)
        self.assertEqual(datetime.fromisoformat(quote['expires_at']),self.anchor+timedelta(minutes=10))
        from .subscriptions import confirm_subscription_change, subscription_change_detail
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            subscription_change_detail(self.other,self.other_user,source.pk,quote['id'])
        self.now.return_value=self.anchor+timedelta(minutes=10)
        with self.assertRaisesRegex(ValueError,'QUOTE_EXPIRED'):
            confirm_subscription_change(self.account,self.user,source.pk,quote['id'],agreement_acceptance='fixture-1',request_key='confirm-change')

    def test_future_prepaid_offsets_once_and_excess_is_a_cash_refund_intent(self):
        source=self.source()
        future_order=self.paid()
        append_paid_period(self.account,source.pk,future_order,cycle_index=1)
        target=self.target(amount=10000)
        self.now.return_value=self.anchor+(month_boundary(self.anchor,1)-self.anchor)/2
        quote=self.quote(source,target)
        self.assertEqual((quote['due_minor'],quote['refund_minor']),('0','8518'))
        result=self.confirm_and_activate(source,quote)
        from .models import SubscriptionChange, ServiceRefund
        change=SubscriptionChange.objects.get(pk=result['id'])
        self.assertEqual(sum(row['amount_minor'] for row in change.cash_sources),10000)
        refund=ServiceRefund.objects.get(subscription=source)
        self.assertEqual(sum(row['amount_minor'] for row in refund.allocations),8518)
        self.assertEqual(source.periods.count(),2)
        self.now.return_value=month_boundary(self.anchor,1)
        grant_due_credits(self.account)
        self.assertEqual(CreditBatch.objects.filter(subscription_period__subscription=source,service_month=0).count(),1)

    def test_discounted_new_term_renews_at_full_product_price(self):
        source=self.source('annual');target=self.target('annual')
        self.now.return_value=self.anchor+(month_boundary(self.anchor,12)-self.anchor)/2
        quote=self.quote(source,target)
        result=self.confirm_and_activate(source,quote)
        from .models import SubscriptionChange
        from .subscriptions import prepare_renewal_attempt
        new=SubscriptionChange.objects.get(pk=result['id']).new_subscription
        end=new.periods.get().ends_at
        renewal=prepare_renewal_attempt(self.account,new.pk,cycle_index=1,dispatch_not_before=end-timedelta(minutes=30),expires_at=end)
        order=PaymentAttempt.objects.get(pk=renewal['id']).order
        self.assertEqual(order.amount_minor,target.unit_amount_minor)
        self.assertNotEqual(order.amount_minor,int(quote['due_minor']))
        self.assertNotIn('subscription_change_id',order.snapshot)
        from backend.notifications import prepare_renewal_notice
        notice=prepare_renewal_notice(self.account,new.pk,1,charge_at=end-timedelta(minutes=30))
        self.assertEqual(notice.amount_minor,target.unit_amount_minor)

    def test_enterprise_running_task_survives_switch_without_recharging_and_purchased_points_remain(self):
        from backend.models import BillingAccount
        from .models import PaidSeat
        from .billing import start_shared_task,consume_shared_task
        from .subscriptions import assign_seat
        self.account=BillingAccount.objects.create(kind='business',name='change-business-fixture')
        member=Membership.objects.create(account=self.account,user=self.user,role='owner')
        product=ProductVersion.objects.create(code='ranked-business',version=1,kind='subscription',audience='business',
            billing_cycle='monthly',minimum_seats=2,unit_amount_minor=12345,credits_quantity='17.25',
            entitlements={'upgrade_rank':1,'resource_mode':'seat_included'},entitlement_version='fixture-1',
            credit_rule_version='fixture-1',terms_version='fixture-1',state='published',effective_at=self.anchor-timedelta(days=1))
        quote=create_quote(self.account,self.user,product.pk,2,self.anchor+timedelta(minutes=5),channel='fixture',buyer_region='CN')
        order=create_order(self.account,self.user,{'quote_id':quote['id'],'agreement_acceptance':'fixture-1'},'business-source')
        record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='business-source',amount_minor=24690,currency='CNY',paid_at=self.anchor,evidence_hash='b'*64)
        source=Subscription.objects.get(pk=self.activate(order['id'])['id'])
        seat=PaidSeat.objects.filter(subscription=source).first()
        assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=0,request_key='business-change-seat')
        grant_credits(self.account,'purchased','change-retained-pack','10',self.anchor+timedelta(days=60))
        task=start_shared_task(self.account,member.pk,'surviving-task','voc')
        consume_shared_task(self.account,task['id'],'before','2')
        target=ProductVersion.objects.create(code='ranked-business-next',version=1,kind='subscription',audience='business',
            billing_cycle='monthly',minimum_seats=2,unit_amount_minor=24690,credits_quantity='35',
            entitlements={'upgrade_rank':2,'resource_mode':'seat_included'},entitlement_version='fixture-1',
            credit_rule_version='fixture-1',terms_version='fixture-1',state='published',effective_at=self.anchor-timedelta(days=1))
        self.now.return_value=self.anchor+timedelta(days=1)
        self.confirm_and_activate(source,self.quote(source,target))
        self.assertEqual(credits_summary(self.account)['available'],'80')
        result=consume_shared_task(self.account,task['id'],'after','3')
        self.assertEqual((result['status'],result['credits']),('running','5'))
        self.assertEqual(credits_summary(self.account)['reserved'],'0')
        self.assertEqual(credits_summary(self.account)['available'],'77')

    def test_parallel_same_quote_confirmation_creates_one_order(self):
        from .subscriptions import confirm_subscription_change
        source=self.source();target=self.target();quote=self.quote(source,target)
        results=self.parallel([lambda:confirm_subscription_change(self.account,self.user,source.pk,quote['id'],
            agreement_acceptance='fixture-1',request_key='parallel-confirm')]*2)
        self.assertEqual(results[0],results[1])
        self.assertEqual(Order.objects.count(),2)


@override_settings(BILLING_SALES_ENABLED=True, BILLING_RENEWAL_CHARGING_ENABLED=True,
                   BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
class BusinessInvitationTests(TransactionTestCase):
    paid = SubscriptionTests.paid
    activate = SubscriptionTests.activate
    business_paid = SubscriptionTests.business_paid
    parallel = SubscriptionConcurrencyTests.parallel

    def setUp(self):
        from django.contrib.auth import get_user_model
        from backend.models import LoginIdentity
        SubscriptionTests.setUp(self)
        self.subscription = Subscription.objects.get(pk=self.activate(self.business_paid('seat_included'))['id'])
        self.recipient = get_user_model().objects.create_user(username='invited-recipient')
        LoginIdentity.objects.create(user=self.recipient, method='email', identifier='invited@example.invalid')
        self.owner_member = Membership.objects.get(account=self.account, user=self.user)

    def invite(self, **changes):
        from .subscriptions import create_business_invitation
        values = dict(account=self.account, actor=self.user, method='email', identifier='invited@example.invalid',
            role='member', paid=True, request_key='invite-one')
        values.update(changes)
        return create_business_invitation(**values)

    def accept(self, invitation, user=None):
        from .subscriptions import accept_business_invitation
        return accept_business_invitation(user or self.recipient, invitation['id'])

    def summary(self):
        from .subscriptions import organization_summary
        return organization_summary(self.account, admin_actor=self.user)

    def client_for(self, user, account=None, **options):
        client = Client(**options)
        client.force_login(user)
        state = client.session
        if account:
            state['active_account_id'] = str(account.pk)
        state.save()
        return client

    def test_invitation_reserves_existing_seat_exactly_24_hours(self):
        row = self.invite()
        self.assertEqual(row['delivery'], 'not_sent')
        self.assertEqual(datetime.fromisoformat(row['expires_at'])-datetime.fromisoformat(row['created_at']), timedelta(hours=24))
        self.assertEqual((self.summary()['available_seats'], self.summary()['reserved_seats']), (1, 1))
        self.now.return_value = self.anchor+timedelta(hours=24, microseconds=-1)
        self.assertEqual(self.summary()['reserved_seats'], 1)
        self.now.return_value = self.anchor+timedelta(hours=24)
        self.assertEqual((self.summary()['available_seats'], self.summary()['reserved_seats']), (2, 0))
        with self.assertRaisesMessage(ValueError, 'INVITATION_UNAVAILABLE'):
            self.accept(row)

    def test_invitation_replay_and_payload_conflict(self):
        from .models import BusinessInvitation
        row = self.invite()
        self.assertEqual(self.invite(), row)
        with self.assertRaisesMessage(ValueError, 'IDEMPOTENCY_CONFLICT'):
            self.invite(paid=False)
        with self.assertRaisesMessage(ValueError, 'INVITATION_ALREADY_PENDING'):
            self.invite(request_key='duplicate-target')
        self.assertEqual(BusinessInvitation.objects.count(), 1)

    def test_acceptance_is_idempotent_and_never_grants_points(self):
        from .models import PaidSeat, SeatChange
        row = self.invite()
        before = list(CreditBatch.objects.values())
        result = self.accept(row)
        self.assertEqual(self.accept(row), result)
        self.assertEqual(Membership.objects.filter(account=self.account, user=self.recipient).count(), 1)
        self.assertEqual(PaidSeat.objects.filter(membership_id=result['membership_id']).count(), 1)
        self.assertEqual(SeatChange.objects.count(), 1)
        self.assertEqual(list(CreditBatch.objects.values()), before)
        self.assertEqual((self.summary()['available_seats'], self.summary()['reserved_seats']), (1, 0))

    def test_free_members_and_free_admins_do_not_reserve_or_receive_seats(self):
        from .models import PaidSeat
        for role in ('member', 'billing_admin'):
            with self.subTest(role=role):
                row = self.invite(role=role, paid=False, request_key='free-'+role)
                self.assertIsNone(row['seat_position'])
                self.assertEqual(self.summary()['available_seats'], 2)
                result = self.accept(row)
                self.assertFalse(PaidSeat.objects.filter(membership_id=result['membership_id']).exists())
                member = Membership.objects.get(pk=result['membership_id'])
                self.assertEqual(member.role, role)
                # Separate synthetic recipient for the next invitation, never delete membership history.
                if role == 'member':
                    from django.contrib.auth import get_user_model
                    from backend.models import LoginIdentity
                    LoginIdentity.objects.filter(user=self.recipient).update(identifier='old@example.invalid')
                    self.recipient = get_user_model().objects.create_user(username='free-admin-recipient')
                    LoginIdentity.objects.create(user=self.recipient, method='email', identifier='invited@example.invalid')

    def test_only_owner_can_invite_an_administrator(self):
        from .subscriptions import organization_summary
        Membership.objects.filter(pk=self.owner_member.pk).update(role='billing_admin')
        self.assertEqual(organization_summary(self.account, admin_actor=self.user)['capabilities']['invitation_roles'], ['member'])
        for paid in (True, False):
            with self.assertRaisesMessage(ValueError, 'FORBIDDEN'):
                self.invite(role='billing_admin', paid=paid)
        self.assertEqual(self.invite()['status'], 'pending')

    def test_ordinary_member_cannot_invite_or_revoke(self):
        from .subscriptions import revoke_business_invitation
        row = self.invite()
        member = Membership.objects.create(account=self.account, user=self.recipient, role='member')
        with self.assertRaisesMessage(ValueError, 'FORBIDDEN'):
            self.invite(actor=member.user, identifier='someone@example.invalid', request_key='ordinary')
        with self.assertRaisesMessage(ValueError, 'FORBIDDEN'):
            revoke_business_invitation(self.account, member.user, row['id'])

    def test_revoke_releases_seat_and_does_not_resurrect_on_create_replay(self):
        from .subscriptions import revoke_business_invitation
        row = self.invite()
        first = revoke_business_invitation(self.account, self.user, row['id'])
        self.assertEqual(first, revoke_business_invitation(self.account, self.user, row['id']))
        self.assertEqual(self.invite()['status'], 'revoked')
        self.assertEqual(self.summary()['available_seats'], 2)
        with self.assertRaisesMessage(ValueError, 'INVITATION_UNAVAILABLE'):
            self.accept(row)

    def test_reserved_seat_cannot_be_assigned_by_another_path(self):
        from .models import BusinessInvitation
        from .subscriptions import assign_seat
        row = self.invite()
        seat = BusinessInvitation.objects.get(pk=row['id']).seat
        with self.assertRaisesMessage(ValueError, 'SEAT_RESERVED_FOR_INVITATION'):
            assign_seat(self.account, self.user, seat.pk, self.owner_member.pk, expected_version=0, request_key='steal-reservation')
        seat.refresh_from_db()
        self.assertIsNone(seat.membership_id)
        self.assertEqual(seat.version, 0)

    def test_all_reserved_capacity_rejects_another_paid_invitation(self):
        self.invite()
        self.invite(identifier='second@example.invalid', request_key='second')
        with self.assertRaisesMessage(ValueError, 'NO_AVAILABLE_SEAT'):
            self.invite(identifier='third@example.invalid', request_key='third')
        self.assertEqual(self.summary()['available_seats'], 0)
        self.assertFalse(self.invite(identifier='free@example.invalid', request_key='free', paid=False)['paid'])

    def test_creator_demotion_save_and_restore_never_revives_pending_invitation(self):
        row = self.invite()
        self.owner_member.role = 'member'
        self.owner_member.save(update_fields=['role'])
        self.owner_member.role = 'owner'
        self.owner_member.save(update_fields=['role'])
        self.assertEqual(self.invite()['status'], 'invalidated')
        self.assertEqual(self.summary()['available_seats'], 2)
        with self.assertRaisesMessage(ValueError, 'INVITATION_UNAVAILABLE'):
            self.accept(row)

    def test_creator_queryset_deactivation_and_restore_never_revives_invitation(self):
        row = self.invite()
        Membership.objects.filter(pk=self.owner_member.pk).update(is_active=False)
        Membership.objects.filter(pk=self.owner_member.pk).update(is_active=True)
        self.assertEqual(self.invite()['status'], 'invalidated')
        self.assertEqual(self.summary()['available_seats'], 2)
        with self.assertRaisesMessage(ValueError, 'INVITATION_UNAVAILABLE'):
            self.accept(row)

    def test_creator_bulk_demotion_and_restore_never_revives_invitation(self):
        self.invite()
        self.owner_member.role = 'member'
        Membership.objects.bulk_update([self.owner_member], ['role'])
        self.owner_member.role = 'owner'
        Membership.objects.bulk_update([self.owner_member], ['role'])
        self.assertEqual(self.invite()['status'], 'invalidated')

    def test_owner_to_admin_revokes_only_admin_invitations(self):
        self.invite(role='billing_admin')
        ordinary = self.invite(identifier='ordinary@example.invalid', request_key='ordinary')
        Membership.objects.filter(pk=self.owner_member.pk).update(role='billing_admin')
        Membership.objects.filter(pk=self.owner_member.pk).update(role='owner')
        self.assertEqual(self.invite(role='billing_admin')['status'], 'invalidated')
        from .models import BusinessInvitation
        self.assertEqual(BusinessInvitation.objects.get(pk=ordinary['id']).state, 'pending')

    def test_remove_creator_invalidates_invites_and_releases_reservation(self):
        from .subscriptions import remove_member
        Membership.objects.create(account=self.account, user=self.other_user, role='billing_admin')
        row = self.invite(actor=self.other_user)
        target = Membership.objects.get(account=self.account, user=self.other_user)
        remove_member(self.account, self.user, target.pk, request_key='remove-inviter')
        self.assertEqual(self.summary()['available_seats'], 2)
        with self.assertRaisesMessage(ValueError, 'INVITATION_UNAVAILABLE'):
            self.accept(row)

    def test_wrong_verified_identity_and_unverified_user_email_cannot_accept(self):
        from backend.models import LoginIdentity
        row = self.invite()
        with self.assertRaisesMessage(ValueError, 'FORBIDDEN'):
            self.accept(row, self.other_user)
        LoginIdentity.objects.filter(user=self.recipient).delete()
        self.recipient.email = 'invited@example.invalid'
        self.recipient.save(update_fields=['email'])
        with self.assertRaisesMessage(ValueError, 'FORBIDDEN'):
            self.accept(row)

    def test_verified_google_email_and_verified_phone_can_accept(self):
        from backend.models import LoginIdentity
        LoginIdentity.objects.filter(user=self.recipient).delete()
        LoginIdentity.objects.create(user=self.recipient, method='google', identifier='fixture-google-sub',
            provider_email='invited@example.invalid')
        from .subscriptions import business_invitations
        row = self.invite(paid=False)
        self.assertEqual(business_invitations(recipient=self.recipient)['items'][0]['id'], row['id'])
        self.accept(row)
        LoginIdentity.objects.create(user=self.other_user, method='phone', identifier='+8613800000099')
        phone = self.invite(method='phone', identifier='+8613800000099', request_key='phone', paid=False)
        self.assertEqual(self.accept(phone, self.other_user)['invitation']['status'], 'accepted')

    def test_existing_member_cannot_receive_or_accept_another_invitation(self):
        from backend.models import LoginIdentity
        LoginIdentity.objects.create(user=self.user, method='email', identifier='owner@example.invalid')
        with self.assertRaisesMessage(ValueError, 'MEMBER_ALREADY_EXISTS'):
            self.invite(identifier='owner@example.invalid')
        row = self.invite()
        Membership.objects.create(account=self.account, user=self.recipient, role='member')
        with self.assertRaisesMessage(ValueError, 'MEMBER_ALREADY_EXISTS'):
            self.accept(row)

    def test_expired_or_superseded_subscription_invalidates_invitation(self):
        row = self.invite()
        Subscription.objects.filter(pk=self.subscription.pk).update(superseded_at=self.anchor)
        with self.assertRaisesMessage(ValueError, 'INVITATION_UNAVAILABLE'):
            self.accept(row)
        with self.assertRaisesMessage(ValueError, 'SUBSCRIPTION_EXPIRED'):
            self.invite(identifier='other@example.invalid', request_key='other')
        self.assertEqual(self.summary()['reserved_seats'], 0)

    def test_invitation_creation_and_acceptance_roll_back_on_audit_failure(self):
        from .models import BusinessInvitation, PaidSeat
        with patch('backend.models.AuditEvent.objects.create', side_effect=RuntimeError('fixture audit failure')):
            with self.assertRaises(RuntimeError):
                self.invite()
        self.assertFalse(BusinessInvitation.objects.exists())
        row = self.invite()
        with patch('backend.models.AuditEvent.objects.create', side_effect=RuntimeError('fixture audit failure')):
            with self.assertRaises(RuntimeError):
                self.accept(row)
        self.assertEqual(BusinessInvitation.objects.get(pk=row['id']).state, 'pending')
        self.assertFalse(Membership.objects.filter(account=self.account, user=self.recipient).exists())
        self.assertFalse(PaidSeat.objects.filter(subscription=self.subscription, membership__isnull=False).exists())

    def test_get_lists_are_read_only_private_and_paginated(self):
        from django.test.utils import CaptureQueriesContext
        from .subscriptions import business_invitations
        row = self.invite()
        self.now.return_value = self.anchor+timedelta(hours=24)
        with CaptureQueriesContext(connection) as queries:
            own = business_invitations(recipient=self.recipient)
            admin = business_invitations(self.account, actor=self.user)
            summary = self.summary()
        self.assertEqual(own['items'][0]['status'], 'expired')
        self.assertEqual(admin['items'][0]['id'], row['id'])
        self.assertEqual(summary['reserved_seats'], 0)
        self.assertNotIn('identifier', own['items'][0])
        self.assertEqual(own['user_id'], self.recipient.pk)
        self.assertEqual(business_invitations(recipient=self.other_user)['items'], [])
        self.assertEqual(business_invitations(recipient=self.recipient, offset=50)['items'], [])
        for query in queries:
            self.assertNotRegex(query['sql'].lstrip().upper(), r'^(INSERT|UPDATE|DELETE)\b')

    def test_http_account_scope_sensitive_gate_and_fresh_recipient_session(self):
        from backend.models import AccountSession, LoginIdentity
        from backend.security_tests import PHONE_SETTINGS
        client = self.client_for(self.user, self.account)
        endpoint = '/api/billing/organization/invitations'
        data = dict(method='email', identifier='invited@example.invalid', role='member', paid=True, request_key='http')
        self.assertEqual(Client().get('/api/account/business-invitations').status_code, 401)
        self.assertEqual(client.post(endpoint, json.dumps(data), content_type='application/json').status_code, 409)
        headers = dict(HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        self.assertEqual(client.post(endpoint, json.dumps(data), content_type='application/json', **headers).json()['error']['code'], 'SENSITIVE_CONFIRMATION_REQUIRED')
        self.user.set_password('InvitationFixture!82A')
        self.user.save(update_fields=['password'])
        client = self.client_for(self.user, self.account)
        LoginIdentity.objects.create(user=self.user, method='phone', identifier='+8613800000001')
        with override_settings(**PHONE_SETTINGS):
            with patch('backend.sms.send_code', return_value=1) as sender:
                challenge = client.post('/api/account/sensitive-challenges', json.dumps({
                    'account_id':str(self.account.pk), 'password':'InvitationFixture!82A'}), content_type='application/json')
            self.assertEqual(challenge.status_code, 201)
            confirmed = client.post('/api/account/sensitive-verify', json.dumps({
                'challenge_id':challenge.json()['challenge_id'], 'code':sender.call_args.args[1]}), content_type='application/json')
            self.assertEqual(confirmed.status_code, 200)
            response = client.post(endpoint, json.dumps(data), content_type='application/json', **headers)
            self.assertEqual(response.status_code, 201, response.content)
        row = response.json()
        receiver = self.client_for(self.recipient, self.other)
        result = receiver.post('/api/account/business-invitations/accept', json.dumps({
            'invitation_id':row['id'], 'confirm':True}), content_type='application/json')
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(receiver.session['active_account_id'], str(self.other.pk))
        AccountSession.objects.filter(user=self.recipient).update(revoked_at=timezone.now())
        denied = receiver.post('/api/account/business-invitations/accept', json.dumps({
            'invitation_id':row['id'], 'confirm':True}), content_type='application/json')
        self.assertEqual(denied.status_code, 401)

    def test_http_rejects_csrf_extra_fields_other_account_and_wrong_identity(self):
        row = self.invite()
        data = {'invitation_id':row['id'], 'confirm':True}
        path = '/api/account/business-invitations/accept'
        strict = self.client_for(self.recipient, enforce_csrf_checks=True)
        self.assertEqual(strict.post(path, json.dumps(data), content_type='application/json').status_code, 403)
        client = self.client_for(self.recipient)
        self.assertEqual(client.post(path, json.dumps({**data, 'extra':True}), content_type='application/json').status_code, 422)
        self.assertEqual(client.get('/api/account/business-invitations?account_id='+str(self.account.pk)).status_code, 422)
        other = self.client_for(self.other_user)
        self.assertEqual(other.post(path, json.dumps(data), content_type='application/json').status_code, 403)
        from .subscriptions import revoke_business_invitation
        with self.assertRaisesMessage(ValueError, 'NOT_FOUND'):
            revoke_business_invitation(self.other, self.other_user, row['id'])

    def test_parallel_creation_does_not_overbook_last_seat(self):
        self.invite()
        results = self.parallel([lambda:self.invite(identifier='last-a@example.invalid', request_key='last-a'),
            lambda:self.invite(identifier='last-b@example.invalid', request_key='last-b')])
        self.assertEqual(sum(isinstance(value, dict) for value in results), 1)
        self.assertIn('NO_AVAILABLE_SEAT', results)
        self.assertEqual(self.summary()['reserved_seats'], 2)

    def test_parallel_acceptance_creates_one_member_and_one_assignment(self):
        from .models import SeatChange
        row = self.invite()
        results = self.parallel([lambda:self.accept(row)]*2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(Membership.objects.filter(account=self.account, user=self.recipient).count(), 1)
        self.assertEqual(SeatChange.objects.count(), 1)

    def test_parallel_acceptance_and_revoke_have_one_terminal_result(self):
        from .subscriptions import revoke_business_invitation
        from .models import BusinessInvitation
        row = self.invite()
        self.parallel([lambda:self.accept(row), lambda:revoke_business_invitation(self.account, self.user, row['id'])])
        record = BusinessInvitation.objects.get(pk=row['id'])
        self.assertIn(record.state, ('accepted', 'revoked'))
        self.assertEqual(Membership.objects.filter(account=self.account, user=self.recipient).count(), int(record.state == 'accepted'))

    def test_http_gets_do_not_write_or_expose_another_recipients_invitation(self):
        from django.test.utils import CaptureQueriesContext
        self.invite()
        self.invite(identifier='other-target@example.invalid', request_key='other-target')
        recipient = self.client_for(self.recipient)
        owner = self.client_for(self.user, self.account)
        with CaptureQueriesContext(connection) as queries:
            received = recipient.get('/api/account/business-invitations')
            sent = owner.get('/api/billing/organization/invitations')
        self.assertEqual(received.status_code, 200)
        self.assertEqual(sent.status_code, 200)
        self.assertEqual(received.json()['total'], 1)
        self.assertEqual(sent.json()['total'], 2)
        self.assertNotIn('other-target', json.dumps(received.json()))
        for query in queries:
            self.assertNotRegex(query['sql'].lstrip().upper(), r'^(INSERT|UPDATE|DELETE)\b')

    def test_demotion_rollback_does_not_leave_valid_admin_invitation_revoked(self):
        from django.db import transaction
        from .models import BusinessInvitation
        row = self.invite()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                Membership.objects.filter(pk=self.owner_member.pk).update(role='member')
                raise RuntimeError('fixture rollback')
        self.owner_member.refresh_from_db()
        self.assertEqual(self.owner_member.role, 'owner')
        self.assertEqual(BusinessInvitation.objects.get(pk=row['id']).state, 'pending')
        self.assertEqual(self.summary()['reserved_seats'], 1)

    def test_parallel_create_and_direct_assignment_cannot_share_a_seat(self):
        from .models import PaidSeat, BusinessInvitation
        from .subscriptions import assign_seat
        self.invite()
        reserved = BusinessInvitation.objects.get(request_key='invite-one').seat_id
        seat = PaidSeat.objects.filter(subscription=self.subscription).exclude(pk=reserved).get()
        results = self.parallel([lambda:self.invite(identifier='competing@example.invalid', request_key='competing'),
            lambda:assign_seat(self.account, self.user, seat.pk, self.owner_member.pk,
                expected_version=0, request_key='competing-assignment')])
        self.assertEqual(sum(isinstance(value, dict) for value in results), 1)
        self.assertTrue('NO_AVAILABLE_SEAT' in results or 'SEAT_RESERVED_FOR_INVITATION' in results)
        seat.refresh_from_db()
        reserved_now = BusinessInvitation.objects.filter(seat=seat, state='pending').exists()
        self.assertNotEqual(bool(seat.membership_id), reserved_now)

    def test_parallel_invitation_and_creator_demotion_cannot_leave_valid_reservation(self):
        from .models import BusinessInvitation
        self.parallel([lambda:self.invite(),
            lambda:Membership.objects.filter(pk=self.owner_member.pk).update(role='member')])
        Membership.objects.filter(pk=self.owner_member.pk).update(role='owner')
        self.assertFalse(BusinessInvitation.objects.filter(state='pending').exists())
        self.assertEqual(self.summary()['available_seats'], 2)

    def test_removed_member_requires_new_invitation_and_keeps_task_and_removal_history(self):
        from backend.models import MemberRemoval
        from .models import SharedTask, SeatChange
        from .subscriptions import remove_member
        from .billing import resume_shared_task
        first = self.invite()
        joined = self.accept(first)
        member = Membership.objects.get(pk=joined['membership_id'])
        task = SharedTask.objects.create(account=self.account, membership=member, task_key='before-removal',
            task_type='fixture', state='paused', exact_quantity='0')
        remove_member(self.account, self.user, member.pk, request_key='remove-before-reinvite')
        removal = list(MemberRemoval.objects.filter(membership=member).values())
        credits = list(CreditBatch.objects.values())
        with self.assertRaisesMessage(ValueError, 'INVITATION_UNAVAILABLE'):
            self.accept(first)
        second = self.invite(request_key='return-with-new-invitation', role='billing_admin')
        returned = self.accept(second)
        self.assertEqual(returned['membership_id'], member.pk)
        self.assertEqual(self.accept(second), returned)
        self.assertEqual(Membership.objects.filter(account=self.account, user=self.recipient).count(), 1)
        member.refresh_from_db()
        task.refresh_from_db()
        self.assertTrue(member.is_active)
        self.assertEqual(member.role, 'billing_admin')
        self.assertTrue(task.resume_blocked)
        self.assertEqual(task.state, 'paused')
        self.assertEqual(list(MemberRemoval.objects.filter(membership=member).values()), removal)
        self.assertEqual(list(CreditBatch.objects.values()), credits)
        self.assertEqual(SeatChange.objects.count(), 3)
        with self.assertRaisesMessage(ValueError, 'TASK_RESUME_FORBIDDEN'):
            resume_shared_task(self.account, self.recipient, task.pk)
        self.now.return_value = self.anchor+timedelta(minutes=1)
        removed_again = remove_member(self.account, self.user, member.pk, request_key='remove-after-reinvite')
        self.assertEqual(MemberRemoval.objects.filter(membership=member).count(), 2)
        self.assertNotEqual(str(removal[0]['id']), removed_again['removal_id'])
        with self.assertRaisesMessage(ValueError, 'INVITATION_UNAVAILABLE'):
            self.accept(second)
        shown = next(item for item in self.summary()['members'] if item['id'] == member.pk)
        self.assertEqual(datetime.fromisoformat(shown['removed_at']), timezone.now())

    def test_rejoining_cannot_restore_a_seat_held_by_unsettled_work(self):
        from .models import Reservation, PaidSeat, BusinessInvitation
        from .subscriptions import remove_member
        first = self.invite()
        joined = self.accept(first)
        member = Membership.objects.get(pk=joined['membership_id'])
        Reservation.objects.create(account=self.account, membership=member, task_key='pending-before-removal',
            quantity='1', shared_period_start=self.anchor, shared_period_end=self.anchor+timedelta(days=1))
        remove_member(self.account, self.user, member.pk, request_key='remove-with-pending-work')
        self.assertTrue(PaidSeat.objects.filter(subscription=self.subscription, membership=member).exists())
        for paid in (False, True):
            invitation = self.invite(request_key='rejoin-pending-'+str(paid), paid=paid)
            with self.assertRaisesMessage(ValueError, 'TASK_SETTLEMENT_REQUIRED'):
                self.accept(invitation)
            member.refresh_from_db()
            self.assertFalse(member.is_active)
            self.assertEqual(BusinessInvitation.objects.get(pk=invitation['id']).state, 'pending')
            from .subscriptions import revoke_business_invitation
            revoke_business_invitation(self.account, self.user, invitation['id'])

    def test_removal_history_reverse_migration_refuses_to_discard_repeated_history(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL transactional schema rollback required')
        from django.db import IntegrityError
        from django.db.migrations.executor import MigrationExecutor
        from django.db.migrations.recorder import MigrationRecorder
        from backend.models import MemberRemoval
        from .models import PaidSeat
        self.test_removed_member_requires_new_invitation_and_keeps_task_and_removal_history()
        before = list(MemberRemoval.objects.order_by('pk').values())
        seats = list(PaidSeat.objects.order_by('pk').values('id', 'purchase_change_id'))
        batches = list(CreditBatch.objects.order_by('pk').values('id', 'purchase_change_id'))
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            with self.assertRaises(IntegrityError):
                executor.migrate([('backend', '0014_phone_binding_google_reauthentication')])
            self.assertEqual(list(MemberRemoval.objects.order_by('pk').values()), before)
            self.assertTrue(MigrationRecorder.Migration.objects.filter(app='backend', name='0015_member_removal_history').exists())
        finally:
            # Only the failing migration rolls back. Earlier dependent reversals
            # may have committed; restore the complete current graph before ORM use.
            MigrationExecutor(connection).migrate(latest)
        self.assertTrue(set(latest).issubset(MigrationExecutor(connection).loader.applied_migrations))
        self.assertEqual(list(PaidSeat.objects.order_by('pk').values('id', 'purchase_change_id')), seats)
        self.assertEqual(list(CreditBatch.objects.order_by('pk').values('id', 'purchase_change_id')), batches)
        # Restored current models must still support another admission/removal.
        from .subscriptions import remove_member
        third = self.invite(request_key='third-admission')
        joined = self.accept(third)
        remove_member(self.account, self.user, joined['membership_id'], request_key='third-removal')
        self.assertEqual(MemberRemoval.objects.count(), 3)


@override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS,
    BILLING_TERMS_DOCUMENTS={'fixture-1':{'approved':True,
        'zh':{'title':'测试','paragraphs':['仅供本地测试']},
        'en':{'title':'Fixture','paragraphs':['Local test only']}}})
class EnterpriseManagementTests(TransactionTestCase):
    setUp = SeatPurchaseTests.setUp
    source = SeatPurchaseTests.source
    base_order = SeatPurchaseTests.base_order
    receipt = SeatPurchaseTests.receipt

    def employee(self, name='employee', role='member'):
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user(username='management-'+name)
        return Membership.objects.create(account=self.account, user=user, role=role)

    def test_role_change_replay_permissions_owner_and_invitation_revocation(self):
        from .subscriptions import change_member_role, create_business_invitation, organization_summary
        from .models import MembershipRoleChange, BusinessInvitation
        self.source(seats=2)
        member = self.employee()
        first = change_member_role(self.account, self.user, member.pk, 'billing_admin', expected_version=0, request_key='promote')
        self.assertEqual(first, change_member_role(self.account, self.user, member.pk, 'billing_admin', expected_version=0, request_key='promote'))
        invite = create_business_invitation(self.account, member.user, method='email', identifier='local@example.test',
            role='member', paid=True, request_key='employee-invite')
        change_member_role(self.account, self.user, member.pk, 'member', expected_version=1, request_key='demote')
        self.assertEqual(BusinessInvitation.objects.get(pk=invite['id']).state, 'invalidated')
        change_member_role(self.account, self.user, member.pk, 'billing_admin', expected_version=2, request_key='promote-again')
        self.assertEqual(BusinessInvitation.objects.get(pk=invite['id']).state, 'invalidated')
        self.assertEqual(MembershipRoleChange.objects.count(), 3)
        with self.assertRaisesRegex(ValueError, 'FORBIDDEN'):
            change_member_role(self.account, member.user, member.pk, 'member', expected_version=3, request_key='self-demote')
        with self.assertRaisesRegex(ValueError, 'OWNER_ROLE_PROTECTED'):
            change_member_role(self.account, self.user, self.member.pk, 'member', expected_version=0, request_key='owner')
        with self.assertRaisesRegex(ValueError, 'MEMBER_ROLE_CHANGED'):
            change_member_role(self.account, self.user, member.pk, 'member', expected_version=0, request_key='stale')
        with self.assertRaisesRegex(ValueError, 'IDEMPOTENCY_CONFLICT'):
            change_member_role(self.account, self.user, member.pk, 'member', expected_version=0, request_key='promote')
        row = next(row for row in organization_summary(self.account, admin_actor=self.user)['members'] if row['id'] == member.pk)
        self.assertEqual(row['role_version'], 3)
        from .subscriptions import organization_history
        history = organization_history(self.account)
        self.assertEqual([row['type'] for row in history['items']], ['member_role']*3)
        self.assertEqual([row['version'] for row in history['items']], [3, 2, 1])

    def test_assignment_retains_membership_and_credit_batch_on_reclaim_and_replay(self):
        from .subscriptions import assign_seat, organization_summary
        from .models import PaidSeat, SeatChange
        self.source(seats=2)
        member = self.employee()
        seat = PaidSeat.objects.filter(subscription=self.subscription, membership__isnull=True).first()
        before = list(CreditBatch.objects.values('id', 'initial_quantity', 'expires_at'))
        result = assign_seat(self.account, self.user, seat.pk, member.pk, expected_version=0, request_key='assign')
        self.assertEqual(result, assign_seat(self.account, self.user, seat.pk, member.pk, expected_version=0, request_key='assign'))
        data = organization_summary(self.account, admin_actor=self.user)
        row = next(row for row in data['members'] if row['id'] == member.pk)
        self.assertTrue(row['can_release_seat']); self.assertEqual(row['seat_id'], str(seat.pk))
        assign_seat(self.account, self.user, seat.pk, None, expected_version=1, request_key='release')
        member.refresh_from_db(); self.assertTrue(member.is_active)
        self.assertEqual(list(CreditBatch.objects.values('id', 'initial_quantity', 'expires_at')), before)
        self.assertEqual(SeatChange.objects.filter(seat=seat).count(), 2)
        self.now.return_value = self.subscription.periods.get().ends_at
        with self.assertRaisesRegex(ValueError, 'SUBSCRIPTION_EXPIRED'):
            assign_seat(self.account, self.user, seat.pk, member.pk, expected_version=2, request_key='expired')

    def sensitive_client(self):
        from backend.models import LoginIdentity
        from backend.security_tests import PHONE_SETTINGS
        phone_settings = override_settings(**PHONE_SETTINGS)
        phone_settings.enable(); self.addCleanup(phone_settings.disable)
        self.user.set_password('ManagementFixture!82A'); self.user.save(update_fields=['password'])
        client = Client(); client.force_login(self.user)
        session = client.session; session['active_account_id'] = str(self.account.pk); session.save()
        LoginIdentity.objects.create(user=self.user, method='phone', identifier='+8613800000001')
        with override_settings(**PHONE_SETTINGS):
            with patch('backend.sms.send_code', return_value=1) as sender:
                challenge = client.post('/api/account/sensitive-challenges', json.dumps({
                    'account_id':str(self.account.pk), 'password':'ManagementFixture!82A'}), content_type='application/json')
            self.assertEqual(challenge.status_code, 201, challenge.content)
            response = client.post('/api/account/sensitive-verify', json.dumps({
                'challenge_id':challenge.json()['challenge_id'], 'code':sender.call_args.args[1]}), content_type='application/json')
            self.assertEqual(response.status_code, 200, response.content)
        return client

    def test_http_member_changes_require_sensitive_account_and_fresh_role(self):
        from .models import PaidSeat
        self.source(seats=2); member = self.employee()
        client = Client(); client.force_login(self.user)
        session = client.session; session['active_account_id'] = str(self.account.pk); session.save()
        endpoint = '/api/billing/organization/members/role'
        body = {'membership_id':member.pk, 'role':'billing_admin', 'expected_version':0, 'request_key':'http-role', 'confirm':True}
        headers = {'HTTP_X_BILLING_ACCOUNT':str(self.account.pk)}
        response = client.post(endpoint, json.dumps(body), content_type='application/json', **headers)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'SENSITIVE_CONFIRMATION_REQUIRED')
        client = self.sensitive_client()
        self.assertEqual(client.post(endpoint, json.dumps(body), content_type='application/json',
            HTTP_X_BILLING_ACCOUNT=str(self.other.pk)).status_code, 409)
        response = client.post(endpoint, json.dumps(body), content_type='application/json', **headers)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(client.post(endpoint, json.dumps(body), content_type='application/json', **headers).json(), response.json())
        seat = PaidSeat.objects.filter(subscription=self.subscription, membership__isnull=True).first()
        endpoint = '/api/billing/organization/seats/assignment'
        body = {'seat_id':str(seat.pk), 'membership_id':member.pk, 'expected_version':0, 'request_key':'http-assign', 'confirm':True}
        self.assertEqual(client.post(endpoint, json.dumps(body), content_type='application/json', **headers).status_code, 200)
        self.now.return_value += timedelta(minutes=15, seconds=1)
        response = client.post(endpoint, json.dumps(body), content_type='application/json', **headers)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'SENSITIVE_CONFIRMATION_REQUIRED')

    def reduce(self, seats, key='reduce'):
        from .subscriptions import seat_reduction_quote, confirm_seat_reduction
        quote = seat_reduction_quote(self.account, self.user, self.subscription.pk,
            [str(seat.pk) for seat in seats], request_key=key+'-quote')
        return confirm_seat_reduction(self.account, self.user, self.subscription.pk, quote['id'], request_key=key+'-confirm')

    def refund_receipts(self, reduction, outcome='refunded'):
        from .models import ServiceRefund
        from .refunds import record_service_refund_receipt
        refund = ServiceRefund.objects.get(pk=reduction['refund']['id'])
        for i, item in enumerate(refund.allocations):
            result = record_service_refund_receipt(self.account, refund.pk, item['payment_id'],
                event_key=f'{outcome}:{i}', outcome=outcome, amount_minor=item['amount_minor'],
                provider_reference='local-receipt', evidence_hash='d'*64)
        return result

    def test_teams_reduction_changes_current_capacity_only_and_refunds_original_cash(self):
        from .subscriptions import _period_seats, _renewal_snapshot, confirm_seat_reduction
        from .models import PaidSeat, ServiceRefund
        self.source(cycle='monthly', seats=3, price=10000)
        original = list(self.subscription.periods.values('snapshot', 'starts_at', 'ends_at'))
        period = self.subscription.periods.get()
        self.now.return_value = self.anchor+(period.ends_at-self.anchor)/2
        seat = PaidSeat.objects.filter(subscription=self.subscription).last()
        change = self.reduce([seat])
        self.assertEqual(change['amount_minor'], '5000')
        self.assertEqual(_period_seats(period).count(), 2)
        self.assertEqual(_renewal_snapshot(self.subscription)['seat_quantity'], 2)
        refund = ServiceRefund.objects.get(pk=change['refund']['id'])
        self.assertEqual(refund.purpose, 'seat_reduction')
        self.assertEqual(refund.allocations[0]['payment_id'], str(period.order.payment_facts.get().pk))
        self.assertEqual(self.refund_receipts(change)['status'], 'refunded')
        self.assertEqual(self.refund_receipts(change)['status'], 'refunded')
        self.assertEqual(change['id'], confirm_seat_reduction(self.account, self.user, self.subscription.pk,
            change['id'], request_key='reduce-confirm')['id'])
        self.subscription.refresh_from_db(); self.assertTrue(self.subscription.auto_renew)
        self.assertIsNone(self.subscription.refund_cutoff_at)
        self.assertEqual(list(self.subscription.periods.values('snapshot', 'starts_at', 'ends_at')), original)
        self.assertEqual(PaidSeat.objects.filter(subscription=self.subscription).count(), 3)

    def test_enterprise_monthly_reduction_keeps_pool_and_paid_next_period(self):
        from .subscriptions import _period_seats, _renewal_snapshot
        from .models import PaidSeat
        self.source(cycle='monthly', mode='shared_included', seats=12, price=10000)
        future_order = self.base_order(12, 'future')
        append_paid_period(self.account, self.subscription.pk, future_order['id'], cycle_index=1)
        current = self.subscription.periods.get(cycle_index=0)
        future = self.subscription.periods.get(cycle_index=1)
        before = list(CreditBatch.objects.values('id', 'initial_quantity', 'expires_at'))
        change = self.reduce(list(PaidSeat.objects.filter(subscription=self.subscription).order_by('-position')[:2]))
        self.assertEqual(change['amount_minor'], '0'); self.assertIsNone(change['refund'])
        self.assertEqual((_period_seats(current).count(), _period_seats(future).count()), (10, 12))
        self.assertEqual(list(CreditBatch.objects.values('id', 'initial_quantity', 'expires_at')), before)
        self.assertEqual(_renewal_snapshot(self.subscription)['seat_quantity'], 12)
        self.now.return_value = future.starts_at
        grant_due_credits(self.account)
        self.assertEqual(CreditBatch.objects.get(subscription_period=future).initial_quantity, '1200')

    def test_annual_enterprise_reduction_then_refund_excludes_removed_seat_cash_and_grants(self):
        from .subscriptions import _period_seats, _renewal_snapshot
        from .refunds import service_refund_assessment
        from .models import PaidSeat
        self.source(mode='shared_included', seats=12, price=120000)
        self.now.return_value = month_boundary(self.anchor, 2)+timedelta(days=4)
        grant_due_credits(self.account)
        before = list(CreditBatch.objects.values('id', 'initial_quantity', 'expires_at'))
        change = self.reduce(list(PaidSeat.objects.filter(subscription=self.subscription).order_by('-position')[:2]))
        self.assertEqual(change['amount_minor'], '180000')
        self.assertEqual(list(CreditBatch.objects.values('id', 'initial_quantity', 'expires_at')), before)
        self.assertEqual(self.refund_receipts(change)['status'], 'refunded')
        self.assertEqual(_renewal_snapshot(self.subscription)['seat_quantity'], 10)
        assessment = service_refund_assessment(self.account, self.user, self.subscription.pk)
        self.assertEqual(assessment['amount_minor'], '900000')
        self.now.return_value = month_boundary(self.anchor, 3)
        grant_due_credits(self.account); grant_due_credits(self.account)
        self.assertEqual(CreditBatch.objects.get(subscription_period=self.subscription.periods.get(), service_month=3).initial_quantity, '1000')
        self.assertEqual(_period_seats(self.subscription.periods.get()).count(), 10)

    def test_reduction_rejects_assigned_reserved_minimum_and_stale_seat_version(self):
        from .subscriptions import seat_reduction_quote, confirm_seat_reduction, assign_seat, create_business_invitation
        from .models import PaidSeat, SeatReduction
        self.source(cycle='monthly', seats=2)
        seats = list(PaidSeat.objects.filter(subscription=self.subscription).order_by('position'))
        with self.assertRaisesRegex(ValueError, 'MINIMUM_SEATS_REQUIRED'):
            seat_reduction_quote(self.account, self.user, self.subscription.pk, [str(seat.pk) for seat in seats], request_key='minimum')
        with self.assertRaisesRegex(ValueError, 'INVALID_SEATS'):
            seat_reduction_quote(self.account, self.user, self.subscription.pk, [str(seats[0].pk)]*2, request_key='duplicate')
        quote = seat_reduction_quote(self.account, self.user, self.subscription.pk, [str(seats[0].pk)], request_key='stale-quote')
        assign_seat(self.account, self.user, seats[0].pk, self.member.pk, expected_version=0, request_key='assign-before-reduce')
        with self.assertRaisesRegex(ValueError, 'SEAT_NOT_RELEASABLE'):
            confirm_seat_reduction(self.account, self.user, self.subscription.pk, quote['id'], request_key='stale-confirm')
        assign_seat(self.account, self.user, seats[0].pk, None, expected_version=1, request_key='release-before-reduce')
        with self.assertRaisesRegex(ValueError, 'SEAT_CHANGED'):
            confirm_seat_reduction(self.account, self.user, self.subscription.pk, quote['id'], request_key='stale-confirm')
        create_business_invitation(self.account, self.user, method='email', identifier='pending@example.test',
            role='member', paid=True, request_key='reserved')
        with self.assertRaisesRegex(ValueError, 'SEAT_RESERVED_FOR_INVITATION'):
            seat_reduction_quote(self.account, self.user, self.subscription.pk, [str(seats[0].pk)], request_key='reserved-reduce')
        self.assertFalse(SeatReduction.objects.exclude(state='quoted').exists())

    def next_purchase(self, quantity, covers, key='next'):
        from .subscriptions import seat_purchase_quote, confirm_seat_purchase
        quote = seat_purchase_quote(self.account, self.user, self.subscription.pk, quantity,
            scope='next_paid', cover_seat_ids=[str(seat.pk) for seat in covers], request_key=key+'-quote')
        result = confirm_seat_purchase(self.account, self.user, self.subscription.pk, quote['id'],
            agreement_acceptance='fixture-1', request_key=key+'-order')
        self.receipt(result['order_id'], int(result['amount_minor']), key+'-payment')
        from .models import SeatPurchaseChange
        return SeatPurchaseChange.objects.get(pk=result['id'])

    def test_next_purchase_covers_only_selected_seats_and_waits_for_period_to_grant(self):
        from .subscriptions import _period_seats, assign_seat, fulfill_seat_purchase
        from .billing import _usable_business_seats
        from .models import PaidSeat
        self.source(cycle='monthly', seats=1, price=10000)
        future_order = self.base_order(1, 'future')
        append_paid_period(self.account, self.subscription.pk, future_order['id'], cycle_index=1)
        SeatPurchaseTests.quote(self, 2); current_change = SeatPurchaseTests.confirm(self, SeatPurchaseTests.quote(self, 2))
        self.receipt(current_change['order_id'], int(current_change['amount_minor']))
        extra = list(PaidSeat.objects.filter(purchase_change_id=current_change['id']).order_by('position'))
        employee = self.employee(); other = self.employee('other')
        for seat, member in zip(extra, [employee, other]):
            assign_seat(self.account, self.user, seat.pk, member.pk, expected_version=0, request_key='seat-'+str(member.pk))
        periods = list(self.subscription.periods.values('id', 'snapshot', 'starts_at', 'ends_at'))
        before = list(CreditBatch.objects.values('id', 'initial_quantity', 'expires_at'))
        addition = self.next_purchase(2, [extra[0]])
        self.assertEqual(addition.quote.amount_minor, 20000)
        self.assertEqual(list(CreditBatch.objects.values('id', 'initial_quantity', 'expires_at')), before)
        fulfill_seat_purchase(self.account, addition.pk)
        current, future = self.subscription.periods.get(cycle_index=0), self.subscription.periods.get(cycle_index=1)
        self.assertEqual((_period_seats(current).count(), _period_seats(future).count()), (3, 3))
        self.now.return_value = future.starts_at
        grant_due_credits(self.account); grant_due_credits(self.account)
        self.assertIn(extra[0].pk, [seat.pk for seat in _usable_business_seats(self.account, employee.pk, timezone.now())])
        self.assertNotIn(extra[1].pk, [seat.pk for seat in _usable_business_seats(self.account, other.pk, timezone.now())])
        self.assertTrue(Membership.objects.get(pk=other.pk).is_active)
        self.assertEqual(CreditBatch.objects.filter(subscription_period=future).count(), 3)
        self.assertEqual(list(self.subscription.periods.values('id', 'snapshot', 'starts_at', 'ends_at')), periods)

    def test_next_purchase_refunds_full_future_cash_once_without_touching_current(self):
        from .refunds import service_refund_assessment, request_service_refund
        self.source(seats=1, price=120000)
        future_order = self.base_order(1, 'future')
        append_paid_period(self.account, self.subscription.pk, future_order['id'], cycle_index=1)
        addition = self.next_purchase(2, [])
        result = service_refund_assessment(self.account, self.user, self.subscription.pk)
        self.assertEqual(result['amount_minor'], '470000')
        requested = request_service_refund(self.account, self.user, self.subscription.pk, 'full-refund')
        self.assertEqual(requested['amount_minor'], '470000')
        self.assertEqual(requested, request_service_refund(self.account, self.user, self.subscription.pk, 'full-refund'))
        self.assertEqual(CreditBatch.objects.filter(purchase_change=addition).count(), 0)

    def test_unknown_reduction_refund_blocks_new_money_and_termination_restores_capacity(self):
        from .subscriptions import seat_purchase_quote, _period_seats
        from .models import PaidSeat
        self.source(cycle='monthly', seats=2, price=10000)
        change = self.reduce([PaidSeat.objects.filter(subscription=self.subscription).last()])
        self.assertEqual(self.refund_receipts(change, 'unknown')['status'], 'unknown')
        with self.assertRaisesRegex(ValueError, 'REFUND_PENDING'):
            seat_purchase_quote(self.account, self.user, self.subscription.pk, 1, request_key='pending-refund-add')
        self.assertEqual(_period_seats(self.subscription.periods.get()).count(), 1)
        self.assertEqual(self.refund_receipts(change, 'terminated')['status'], 'terminated')
        self.assertEqual(self.refund_receipts(change, 'terminated')['status'], 'terminated')
        self.assertEqual(_period_seats(self.subscription.periods.get()).count(), 2)

    def test_next_full_annual_purchase_uses_month_rule_after_it_starts(self):
        from .refunds import service_refund_assessment
        self.source(seats=1, price=120000)
        future_order = self.base_order(1, 'future')
        append_paid_period(self.account, self.subscription.pk, future_order['id'], cycle_index=1)
        self.next_purchase(1, [])
        self.now.return_value = self.subscription.periods.get(cycle_index=1).starts_at+timedelta(days=10)
        grant_due_credits(self.account)
        result = service_refund_assessment(self.account, self.user, self.subscription.pk)
        self.assertEqual(result['amount_minor'], '220000')

    def test_reduction_termination_after_later_month_grant_requires_review_without_rewriting_grants(self):
        from .models import PaidSeat, BillingIssue, SeatReduction
        from .subscriptions import _period_seats
        self.source(mode='shared_included', seats=11)
        change = self.reduce([PaidSeat.objects.filter(subscription=self.subscription).last()])
        self.now.return_value = month_boundary(self.anchor, 1)
        grant_due_credits(self.account)
        before = list(CreditBatch.objects.values('id', 'initial_quantity', 'expires_at'))
        result = self.refund_receipts(change, 'terminated')
        self.assertEqual(result['status'], 'review')
        self.assertEqual(SeatReduction.objects.get(pk=change['id']).state, 'applied')
        self.assertEqual(_period_seats(self.subscription.periods.get()).count(), 10)
        self.assertTrue(BillingIssue.objects.filter(code='SEAT_REFUND_RESTORE_REVIEW').exists())
        self.assertEqual(list(CreditBatch.objects.values('id', 'initial_quantity', 'expires_at')), before)

    def test_mixed_base_and_partial_extra_reduction_uses_each_cash_coverage(self):
        from .models import PaidSeat
        from .refunds import service_refund_assessment
        self.anchor = datetime(2026, 1, 1, tzinfo=ZoneInfo('Asia/Shanghai')); self.now.return_value = self.anchor
        self.source(mode='shared_included', seats=11, price=120000)
        self.now.return_value = datetime(2026, 8, 1, tzinfo=ZoneInfo('Asia/Shanghai'))
        quote = SeatPurchaseTests.quote(self, 1)
        added = SeatPurchaseTests.confirm(self, quote)
        self.receipt(added['order_id'], int(added['amount_minor']))
        self.assertEqual(added['amount_minor'], '50301')
        self.now.return_value = datetime(2026, 8, 15, tzinfo=ZoneInfo('Asia/Shanghai'))
        base = PaidSeat.objects.filter(subscription=self.subscription, purchase_change=None).last()
        extra = PaidSeat.objects.get(purchase_change_id=added['id'])
        change = self.reduce([base, extra])
        self.assertEqual(change['amount_minor'], '80109')
        self.assertEqual(self.refund_receipts(change)['status'], 'refunded')
        result = service_refund_assessment(self.account, self.user, self.subscription.pk)
        self.assertEqual(result['amount_minor'], '400000')
        self.now.return_value = datetime(2026, 9, 1, tzinfo=ZoneInfo('Asia/Shanghai'))
        grant_due_credits(self.account)
        self.assertEqual(list(CreditBatch.objects.filter(service_month=8).values_list('initial_quantity', flat=True)), ['1000'])

    def test_reduction_then_upgrade_carries_only_remaining_active_capacity_cash(self):
        from fractions import Fraction
        from .models import PaidSeat
        from .subscriptions import subscription_change_quote, _duration
        from .refunds import _round
        self.source(seats=2, price=120000)
        self.now.return_value = month_boundary(self.anchor, 5)+timedelta(days=3)
        change = self.reduce([PaidSeat.objects.filter(subscription=self.subscription).last()])
        self.refund_receipts(change)
        target = ProductVersion.objects.create(code='management-upgrade', version=1, kind='subscription',
            audience='business', billing_cycle='annual', minimum_seats=1, unit_amount_minor=240000,
            credits_quantity='100', entitlements={'resource_mode':'seat_included', 'upgrade_rank':2},
            entitlement_version='fixture-1', credit_rule_version='fixture-1', terms_version='fixture-1',
            state='published', effective_at=self.anchor-timedelta(days=1))
        result = subscription_change_quote(self.account, self.user, self.subscription.pk, target.pk, request_key='after-reduction-upgrade')
        period = self.subscription.periods.get()
        expected = _round(Fraction(120000)*_duration(period.ends_at-timezone.now())/_duration(period.ends_at-period.starts_at))
        from .models import SubscriptionChange
        row = SubscriptionChange.objects.get(pk=result['id'])
        self.assertEqual(row.snapshot['cash_carried_minor'], expected)
        self.assertEqual(row.quote.snapshot['seat_quantity'], 1)

    def test_financial_http_gates_binding_sensitive_and_original_operations(self):
        from .models import PaidSeat, SeatReduction
        self.source(cycle='monthly', mode='shared_included', seats=12, price=10000)
        future_order = self.base_order(12, 'future')
        append_paid_period(self.account, self.subscription.pk, future_order['id'], cycle_index=1)
        seat = PaidSeat.objects.filter(subscription=self.subscription).last()
        client = self.sensitive_client()
        base = f'/api/billing/subscriptions/{self.subscription.pk}'
        headers = {'HTTP_X_BILLING_ACCOUNT':str(self.account.pk), 'HTTP_IDEMPOTENCY_KEY':'http-reduce'}
        body = {'seat_ids':[str(seat.pk)]}
        self.assertEqual(client.post(base+'/seat-reduction-quotes', json.dumps(body), content_type='application/json', **headers).status_code, 503)
        with override_settings(BILLING_SEAT_PURCHASE_ENABLED=True):
            response = client.post(base+'/seat-reduction-quotes', json.dumps(body), content_type='application/json', **headers)
            self.assertEqual(response.status_code, 201, response.content); quote = response.json()
            count = SeatReduction.objects.count()
            self.assertEqual(client.get(base+'/seat-reduction-quotes/'+quote['id'], **headers).json(), quote)
            self.assertEqual(SeatReduction.objects.count(), count)
            payload = {'quote_id':quote['id'], 'confirm':True}
            self.assertEqual(client.post(base+'/seat-reductions', json.dumps(payload), content_type='application/json',
                **{**headers, 'HTTP_X_BILLING_ACCOUNT':str(self.other.pk)}).status_code, 409)
            response = client.post(base+'/seat-reductions', json.dumps(payload), content_type='application/json', **headers)
            self.assertEqual(response.status_code, 201, response.content)
            self.assertEqual(response.json()['status'], 'applied')
            response = client.post(base+'/seat-quotes', json.dumps({'quantity':1, 'scope':'next_paid', 'cover_seat_ids':[]}),
                content_type='application/json', **{**headers, 'HTTP_IDEMPOTENCY_KEY':'http-next'})
            self.assertEqual(response.status_code, 201, response.content)
            quoted = response.json(); self.assertEqual(quoted['scope'], 'next_paid')
            with patch('backend.payments.start_payment') as start, patch('backend.payments.finish_payment') as finish:
                response = client.post(base+'/seat-purchases', json.dumps({'quote_id':quoted['id'], 'agreement_acceptance':'fixture-1'}),
                    content_type='application/json', **{**headers, 'HTTP_IDEMPOTENCY_KEY':'http-next-confirm'})
            start.assert_not_called(); finish.assert_not_called()
            self.assertEqual(response.status_code, 201, response.content)
            self.assertEqual(response.json()['status'], 'pending_payment')

    parallel = SubscriptionConcurrencyTests.parallel

    def test_concurrent_role_versions_and_seat_reduction_assignment_have_one_winner(self):
        from .subscriptions import change_member_role, seat_reduction_quote, confirm_seat_reduction, assign_seat
        from .models import PaidSeat, MembershipRoleChange, SeatReduction
        self.source(cycle='monthly', seats=2, price=10000); member = self.employee()
        results = self.parallel([lambda:change_member_role(self.account, self.user, member.pk, 'billing_admin', expected_version=0, request_key='role-a'),
            lambda:change_member_role(self.account, self.user, member.pk, 'member', expected_version=0, request_key='role-b')])
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn('MEMBER_ROLE_CHANGED', results); self.assertEqual(MembershipRoleChange.objects.count(), 1)
        seat = PaidSeat.objects.filter(subscription=self.subscription).last()
        quote = seat_reduction_quote(self.account, self.user, self.subscription.pk, [str(seat.pk)], request_key='race-quote')
        results = self.parallel([lambda:confirm_seat_reduction(self.account, self.user, self.subscription.pk, quote['id'], request_key='race-confirm'),
            lambda:assign_seat(self.account, self.user, seat.pk, member.pk, expected_version=0, request_key='race-assign')])
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        seat.refresh_from_db()
        self.assertFalse(seat.membership_id and SeatReduction.objects.filter(pk=quote['id'], state='applied').exists())

    def test_concurrent_next_purchase_confirmation_and_delivery_do_not_duplicate_capacity(self):
        from .subscriptions import seat_purchase_quote, confirm_seat_purchase, fulfill_seat_purchase, _period_seats
        from .models import SeatPurchaseChange, PaidSeat
        self.source(cycle='monthly', seats=1, price=10000)
        future_order = self.base_order(1, 'future'); append_paid_period(self.account, self.subscription.pk, future_order['id'], cycle_index=1)
        quote = seat_purchase_quote(self.account, self.user, self.subscription.pk, 2, scope='next_paid', cover_seat_ids=[], request_key='race-next')
        results = self.parallel([lambda:confirm_seat_purchase(self.account, self.user, self.subscription.pk, quote['id'],
            agreement_acceptance='fixture-1', request_key='race-next-order')]*2)
        self.assertEqual(results[0], results[1])
        self.receipt(results[0]['order_id'], int(results[0]['amount_minor']), 'race-next-payment')
        self.parallel([lambda:fulfill_seat_purchase(self.account, quote['id'])]*2)
        self.assertEqual(_period_seats(self.subscription.periods.get(cycle_index=1)).count(), 3)
        self.assertEqual(PaidSeat.objects.filter(subscription=self.subscription).count(), 3)
        self.assertEqual(SeatPurchaseChange.objects.count(), 1)

    def test_enterprise_change_history_blocks_destructive_reverse_migration(self):
        from django.db.migrations.executor import MigrationExecutor
        from django.db.migrations.exceptions import IrreversibleError
        from django.db.migrations.recorder import MigrationRecorder
        from .models import MembershipRoleChange, PaidSeat, SeatReduction, ServiceRefund
        from .subscriptions import change_member_role
        self.source(cycle='monthly', seats=2, price=10000)
        employee = self.employee()
        change_member_role(self.account, self.user, employee.pk, 'billing_admin', expected_version=0, request_key='history')
        reduction = self.reduce([PaidSeat.objects.filter(subscription=self.subscription).last()])
        before = (list(MembershipRoleChange.objects.values()), list(SeatReduction.objects.values()), list(ServiceRefund.objects.values()))
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            with self.assertRaisesRegex(IrreversibleError, 'Enterprise change history must be retained'):
                executor.migrate([('commerce', '0032_seat_purchase_grants')])
            self.assertTrue(MigrationRecorder.Migration.objects.filter(app='commerce', name='0035_protect_enterprise_change_history').exists())
            self.assertEqual(before, (list(MembershipRoleChange.objects.values()), list(SeatReduction.objects.values()), list(ServiceRefund.objects.values())))
        finally:
            MigrationExecutor(connection).migrate(latest)
        self.assertEqual(ServiceRefund.objects.get(pk=reduction['refund']['id']).purpose, 'seat_reduction')

    def test_next_purchase_rejects_duplicate_cross_period_and_late_payment(self):
        from .subscriptions import seat_purchase_quote, confirm_seat_purchase, _period_seats
        from .models import PaidSeat, SeatPurchaseChange, BillingIssue
        self.source(cycle='monthly', seats=1, price=10000)
        future_order = self.base_order(1, 'future'); append_paid_period(self.account, self.subscription.pk, future_order['id'], cycle_index=1)
        base = PaidSeat.objects.get(subscription=self.subscription)
        for selected, code in [([str(base.pk)], 'SEAT_COVERAGE_CHANGED'), ([str(base.pk)]*2, 'INVALID_SEATS')]:
            with self.assertRaisesRegex(ValueError, code):
                seat_purchase_quote(self.account, self.user, self.subscription.pk, 2, scope='next_paid',
                    cover_seat_ids=selected, request_key=code)
        quote = seat_purchase_quote(self.account, self.user, self.subscription.pk, 1, scope='next_paid', cover_seat_ids=[], request_key='late-next')
        confirmed = confirm_seat_purchase(self.account, self.user, self.subscription.pk, quote['id'],
            agreement_acceptance='fixture-1', request_key='late-next-order')
        future = self.subscription.periods.get(cycle_index=1)
        self.now.return_value = future.starts_at
        self.receipt(confirmed['order_id'], int(confirmed['amount_minor']), 'late-next-payment')
        self.assertEqual(SeatPurchaseChange.objects.get(pk=quote['id']).state, 'payment_review')
        self.assertEqual(_period_seats(future).count(), 1)
        self.assertTrue(BillingIssue.objects.filter(code='SEAT_PURCHASE_PAYMENT_REVIEW').exists())
        self.assertEqual(PaymentFact.objects.filter(order_id=confirmed['order_id'], disposition='accepted').count(), 1)
