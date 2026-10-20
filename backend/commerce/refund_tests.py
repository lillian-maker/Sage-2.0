"""Annual cash allocation, future-service freeze and receipt replay."""
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase, Client, TransactionTestCase, override_settings

from backend.billing_rules import month_boundary
from backend.models import BillingAccount, Membership, InvoiceRequest
from .models import (Order, Quote, ProductVersion, PaymentFact, PaymentAttempt, Subscription, SubscriptionPeriod,
    SubscriptionChange, ServiceRefund, CreditBatch, CreditRefund)
from .refunds import (request_service_refund, service_refund_assessment, record_service_refund_receipt)
from .subscriptions import grant_due_credits, active_subscription_periods


class AnnualServiceRefundTests(TestCase):
    def setUp(self):
        self.anchor = datetime(2026, 1, 31, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.now = datetime(2026, 7, 15, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.clock = patch('django.utils.timezone.now', return_value=self.now)
        self.clock_mock = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.user = get_user_model().objects.create_user(username='annual-refund-owner')
        self.account = BillingAccount.objects.create(kind='personal', name='Fixture', personal_owner=self.user)
        Membership.objects.create(account=self.account, user=self.user, role='owner')
        self.product = ProductVersion.objects.create(code='fixture', version=1, kind='subscription',
            audience='personal', billing_cycle='annual', unit_amount_minor=120000, credits_quantity='10',
            entitlements={'fixture': True}, entitlement_version='f', credit_rule_version='f',
            terms_version='f', state='published', effective_at=self.anchor)
        self.sequence = 0
        self.order, self.payment = self.paid_order(120000)
        self.sub = Subscription.objects.create(account=self.account, initial_order=self.order,
            original_anchor=self.anchor, billing_timezone='Asia/Shanghai', calendar_rule_version='fixture',
            billing_cycle='annual', mandate={})
        self.period = self.period_for(self.sub, self.order, 0)

    def paid_order(self, amount):
        self.sequence += 1
        snapshot = {'code': 'fixture', 'credits_quantity': '10', 'seat_quantity': 1,
            'entitlements': {'fixture': True}, 'billing_cycle': 'annual', 'kind': 'subscription',
            'audience': 'personal', 'currency': 'CNY', 'amount_minor': amount}
        quote = Quote.objects.create(account=self.account, product=self.product, snapshot=snapshot,
            amount_minor=amount, currency='CNY', expires_at=self.now+timedelta(minutes=10))
        order = Order.objects.create(account=self.account, quote=quote, created_by=self.user,
            idempotency_key=f'fixture-{self.sequence}', request_hash='a'*64, snapshot=snapshot,
            amount_minor=amount, currency='CNY', status='fulfilled', paid_at=self.now)
        payment = PaymentFact.objects.create(order=order, channel='fixture', merchant_reference='fixture-merchant',
            transaction_reference=f'fixture-{self.sequence}', amount_minor=amount, currency='CNY',
            evidence_hash='a'*64, disposition='accepted', paid_at=self.now)
        return order, payment

    def period_for(self, sub, order, cycle):
        return SubscriptionPeriod.objects.create(subscription=sub, order=order, cycle_index=cycle,
            starts_at=month_boundary(sub.original_anchor, cycle*12),
            ends_at=month_boundary(sub.original_anchor, (cycle+1)*12), snapshot=order.snapshot)

    def apply(self, key='refund-fixture'):
        return request_service_refund(self.account, self.user, self.sub.pk, key)

    def receipt(self, row, payment=None, outcome='refunded', key='receipt', amount=None):
        payment = payment or self.payment
        part = next(item for item in row.allocations if item['payment_id'] == str(payment.pk))
        return record_service_refund_receipt(self.account, row.pk, payment.pk, event_key=key,
            outcome=outcome, amount_minor=part['amount_minor'] if amount is None else amount,
            provider_reference=f'fixture-{key}' if outcome == 'refunded' else '', evidence_hash='b'*64)

    def test_ordinary_request_is_automatic_and_stops_renewal_immediately(self):
        result = self.apply()
        self.assertEqual((result['status'], result['amount_minor']), ('approved', '60000'))
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.auto_renew)
        self.assertEqual(self.sub.cancellation_state, 'processing')
        self.assertEqual(self.sub.refund_cutoff_at, month_boundary(self.anchor, 6))
        self.assertEqual(active_subscription_periods(self.account).count(), 1)

    def test_prepaid_next_year_is_in_same_request_at_full_paid_amount(self):
        future, future_payment = self.paid_order(130001)
        self.period_for(self.sub, future, 1)
        result = self.apply()
        row = ServiceRefund.objects.get(pk=result['id'])
        self.assertEqual(row.amount_minor, 190001)
        self.assertEqual({item['payment_id']: item['amount_minor'] for item in row.allocations},
            {str(self.payment.pk): 60000, str(future_payment.pk): 130001})

    def test_request_replay_does_not_duplicate_and_new_key_cannot_double_refund(self):
        first = self.apply()
        self.assertEqual(self.apply(), first)
        self.assertEqual(ServiceRefund.objects.count(), 1)
        with self.assertRaisesRegex(ValueError, 'REFUND_ALREADY_REQUESTED'):
            self.apply('another-key')

    def test_paid_pending_plan_change_blocks_refund_without_freezing_or_losing_new_cash(self):
        change_order, change_payment = self.paid_order(45000)
        SubscriptionChange.objects.create(account=self.account, subscription=self.sub, actor=self.user,
            quote=change_order.quote, order=change_order, request_key='pending-paid-change',
            request_hash='d'*64, snapshot={}, state='pending_payment')
        self.assertEqual(service_refund_assessment(self.account, self.user, self.sub.pk)['reason'],
            'PAYMENT_RECONCILIATION_REQUIRED')
        with self.assertRaisesRegex(ValueError, 'PAYMENT_RECONCILIATION_REQUIRED'):
            self.apply()
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.auto_renew)
        self.assertIsNone(self.sub.refund_cutoff_at)
        self.assertFalse(ServiceRefund.objects.exists())
        self.assertTrue(PaymentFact.objects.filter(pk=change_payment.pk, disposition='accepted').exists())

    def test_unresolved_renewal_blocks_new_refund_for_every_inflight_state(self):
        from django.db import transaction
        for state in ('created', 'pending', 'unknown', 'review'):
            with self.subTest(state=state), transaction.atomic():
                quote=Quote.objects.create(account=self.account, product=self.product, snapshot=self.order.snapshot,
                    amount_minor=self.order.amount_minor, currency='CNY', expires_at=self.now+timedelta(minutes=10))
                order=Order.objects.create(account=self.account, quote=quote, created_by=self.user,
                    idempotency_key='unresolved-'+state, request_hash='a'*64, snapshot=self.order.snapshot,
                    amount_minor=self.order.amount_minor, currency='CNY')
                PaymentAttempt.objects.create(account=self.account, order=order, subscription=self.sub,
                    renewal_cycle_index=1, intent_key='unresolved-'+state, state=state,
                    dispatched_at=None if state == 'created' else self.now,
                    dispatch_not_before=self.now, expires_at=self.now+timedelta(minutes=10))
                with self.assertRaisesRegex(ValueError, 'PAYMENT_RECONCILIATION_REQUIRED'):
                    self.apply('renewal-'+state)
                self.sub.refresh_from_db()
                self.assertTrue(self.sub.auto_renew)
                self.assertIsNone(self.sub.refund_cutoff_at)
                self.assertFalse(ServiceRefund.objects.exists())
                transaction.set_rollback(True)

    def test_reconciliation_flagged_source_payment_requires_manual_review_and_stays_frozen(self):
        from .billing import record_attempt_event
        attempt=PaymentAttempt.objects.create(account=self.account, order=self.order, intent_key='original-cash-review',
            state='pending', dispatched_at=self.now, expires_at=self.now+timedelta(minutes=10))
        record_attempt_event(self.account, attempt.pk, 'contradictory-provider-query', 'failed',
            source='provider_query', evidence_hash='b'*64)
        attempt.refresh_from_db();self.assertTrue(attempt.needs_reconciliation)
        self.assertFalse(service_refund_assessment(self.account, self.user, self.sub.pk)['automatic_review'])
        self.assertEqual(self.apply()['status'], 'review')
        self.sub.refresh_from_db();self.assertIsNotNone(self.sub.refund_cutoff_at)
        with self.assertRaisesRegex(ValueError, 'INVALID_REFUND_EVIDENCE'):
            self.receipt(ServiceRefund.objects.get())

    def test_existing_refund_is_rediscovered_with_current_status_after_reload(self):
        original=self.apply()
        client=Client();client.force_login(self.user)
        session=client.session;session['active_account_id']=str(self.account.pk);session.save()
        endpoint=f'/api/billing/subscriptions/{self.sub.pk}/refund-assessment'
        for outcome in (None, 'unknown', 'refunded'):
            if outcome:
                self.receipt(ServiceRefund.objects.get(), outcome=outcome, key='reload-'+outcome)
            response=client.get(endpoint)
            self.assertEqual(response.status_code,200)
            data=response.json()
            self.assertFalse(data['eligible'])
            self.assertEqual(data['reason'],'REFUND_ALREADY_REQUESTED')
            self.assertEqual(data['existing_refund']['id'],original['id'])
            self.assertEqual(data['existing_refund']['status'],outcome or 'approved')

    def test_refund_lookup_rejects_invalid_and_other_account_ids(self):
        original=self.apply()
        client=Client();client.force_login(self.user)
        session=client.session;session['active_account_id']=str(self.account.pk);session.save()
        for value in ('not-a-uuid', '123', str(uuid.uuid4())):
            with self.subTest(value=value):
                response=client.get('/api/billing/refunds/'+value)
                self.assertEqual(response.status_code,404)
                self.assertEqual(response.json()['error']['code'],'NOT_FOUND')
        outsider=get_user_model().objects.create_user(username='other-refund-viewer')
        other=BillingAccount.objects.create(kind='personal', name='Other fixture', personal_owner=outsider)
        Membership.objects.create(account=other, user=outsider, role='owner')
        client.force_login(outsider)
        session=client.session;session['active_account_id']=str(other.pk);session.save()
        self.assertEqual(client.get('/api/billing/refunds/'+original['id']).status_code,404)

    def test_issued_invoice_routes_to_human_review(self):
        InvoiceRequest.objects.create(account=self.account, order=self.order, created_by=self.user,
            recipient_email='fixture@example.invalid', title='Fixture', status='issued',
            request_key='invoice-fixture', request_hash='c'*64)
        self.assertEqual(self.apply()['status'], 'review')
        with self.assertRaisesRegex(ValueError, 'INVALID_REFUND_EVIDENCE'):
            self.receipt(ServiceRefund.objects.first())

    def test_unknown_and_failed_keep_future_service_frozen_and_renewal_off(self):
        self.apply()
        row = ServiceRefund.objects.get()
        self.assertEqual(self.receipt(row, outcome='unknown')['status'], 'unknown')
        self.assertEqual(self.receipt(row, outcome='failed', key='failed')['status'], 'unknown')
        self.sub.refresh_from_db()
        self.assertIsNotNone(self.sub.refund_cutoff_at)
        self.assertFalse(self.sub.auto_renew)

    def test_verified_termination_restores_service_but_does_not_restart_renewal(self):
        self.apply()
        row = ServiceRefund.objects.get()
        self.assertEqual(self.receipt(row, outcome='terminated')['status'], 'terminated')
        self.sub.refresh_from_db()
        self.assertIsNone(self.sub.refund_cutoff_at)
        self.assertFalse(self.sub.auto_renew)

    def test_future_credits_are_not_granted_during_or_after_successful_refund(self):
        self.apply()
        self.clock_mock.return_value = month_boundary(self.anchor, 7)
        grant_due_credits(self.account)
        self.assertFalse(CreditBatch.objects.filter(subscription_period=self.period, service_month__gte=6).exists())
        self.assertEqual(active_subscription_periods(self.account).count(), 0)
        self.receipt(ServiceRefund.objects.get())
        grant_due_credits(self.account)
        self.assertFalse(CreditBatch.objects.filter(subscription_period=self.period, service_month__gte=6).exists())

    def test_partial_multi_payment_results_wait_for_every_original_payment(self):
        future, future_payment = self.paid_order(130001)
        self.period_for(self.sub, future, 1)
        self.apply()
        row = ServiceRefund.objects.get()
        self.assertEqual(self.receipt(row)['status'], 'processing')
        self.assertEqual(self.receipt(row, future_payment, key='future')['status'], 'refunded')
        self.assertEqual(self.receipt(row, future_payment, key='future')['status'], 'refunded')
        self.assertEqual(row.receipts.count(), 2)

    def test_receipt_amount_scope_and_replay_conflicts_are_rejected(self):
        self.apply()
        row = ServiceRefund.objects.get()
        with self.assertRaisesRegex(ValueError, 'REFUND_AMOUNT_MISMATCH'):
            self.receipt(row, amount=60001)
        outsider = BillingAccount.objects.create(kind='business', name='Other')
        with self.assertRaisesRegex(ValueError, 'NOT_FOUND'):
            record_service_refund_receipt(outsider, row.pk, self.payment.pk, event_key='bad',
                outcome='refunded', amount_minor=60000, provider_reference='fixture', evidence_hash='b'*64)
        self.assertEqual(row.receipts.count(), 0)
        self.receipt(row)
        with self.assertRaisesRegex(ValueError, 'IDEMPOTENCY_CONFLICT'):
            self.receipt(row, outcome='failed')

    def test_cash_carry_is_refundable_but_upgrade_discount_is_not_cash(self):
        old, old_payment = self.paid_order(120000)
        old_sub = Subscription.objects.create(account=self.account, initial_order=old, original_anchor=self.anchor,
            billing_timezone='Asia/Shanghai', calendar_rule_version='fixture', billing_cycle='annual',
            mandate={}, auto_renew=False, cancellation_state='completed')
        SubscriptionChange.objects.create(account=self.account, subscription=old_sub, new_subscription=self.sub,
            actor=self.user, quote=self.order.quote, order=self.order, request_key='change-fixture',
            request_hash='d'*64, snapshot={'discount_minor': 45000}, state='activated',
            cash_sources=[{'payment_id': str(old_payment.pk), 'amount_minor': 75000}])
        self.assertEqual(self.apply()['amount_minor'], '97500')

    def test_single_final_rounding_across_cash_sources(self):
        Order.objects.filter(pk=self.order.pk).update(amount_minor=1)
        PaymentFact.objects.filter(pk=self.payment.pk).update(amount_minor=1)
        self.order.refresh_from_db()
        old, old_payment = self.paid_order(1)
        old_sub = Subscription.objects.create(account=self.account, initial_order=old, original_anchor=self.anchor,
            billing_timezone='Asia/Shanghai', calendar_rule_version='fixture', billing_cycle='annual',
            mandate={}, auto_renew=False, cancellation_state='completed')
        SubscriptionChange.objects.create(account=self.account, subscription=old_sub, new_subscription=self.sub,
            actor=self.user, quote=self.order.quote, order=self.order, request_key='round-fixture',
            request_hash='e'*64, snapshot={}, state='activated',
            cash_sources=[{'payment_id': str(old_payment.pk), 'amount_minor': 1}])
        result = self.apply()
        self.assertEqual(result['amount_minor'], '1')
        self.assertEqual(sum(item['amount_minor'] for item in ServiceRefund.objects.get().allocations), 1)

    def test_replaced_subscription_and_non_admin_are_ineligible(self):
        self.sub.superseded_at = self.now
        self.sub.save(update_fields=['superseded_at'])
        self.assertEqual(service_refund_assessment(self.account, self.user, self.sub.pk)['reason'], 'SUBSCRIPTION_ALREADY_CHANGED')
        outsider = get_user_model().objects.create_user(username='annual-refund-outsider')
        with self.assertRaisesRegex(ValueError, 'FORBIDDEN'):
            request_service_refund(self.account, outsider, self.sub.pk, 'forbidden')

    def test_http_request_uses_account_header_and_rechecks_original_id(self):
        client = Client()
        client.force_login(self.user)
        session = client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        endpoint = f'/api/billing/subscriptions/{self.sub.pk}/refunds'
        body = {'confirm': True, 'request_key': 'http-refund'}
        self.assertEqual(client.post(endpoint, body, content_type='application/json').status_code, 409)
        result = client.post(endpoint, body, content_type='application/json',
            HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.json()['status'], 'approved')
        self.assertEqual(client.get('/api/billing/refunds/'+result.json()['id']).json(), result.json())
        self.assertEqual(ServiceRefund.objects.get().receipts.count(), 0)


from .tests import FIXTURE_CHANNELS, fixture_account, fixture_product


@override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
class CreditRefundRequestTests(TransactionTestCase):
    def setUp(self):
        from .billing import create_quote, create_order, record_confirmed_payment
        from django.utils import timezone
        self.user, self.account = fixture_account('credit-refund-request')
        Membership.objects.create(account=self.account, user=self.user, role='owner')
        now = timezone.now()
        self.product = ProductVersion.objects.create(code='fixture_refund_pack', version=1, kind='credits',
            audience='personal', unit_amount_minor=731, credits_quantity='2', entitlements={'fixture': True},
            entitlement_version='fixture-1', credit_rule_version='fixture-1', terms_version='fixture-1',
            state='published', effective_at=now-timedelta(days=1))
        quote = create_quote(self.account, self.user, self.product.pk, 1, now+timedelta(minutes=10),
            channel='fixture', buyer_region='CN')
        order = create_order(self.account, self.user, {'quote_id': quote['id'], 'agreement_acceptance': 'fixture-1'}, 'credit-refund-order')
        record_confirmed_payment(self.account, order['id'], channel='fixture', merchant_reference='fixture-shenzhen',
            transaction_reference='credit-refund-paid', amount_minor=731, currency='CNY', paid_at=now, evidence_hash='a'*64)
        self.order = Order.objects.get(pk=order['id'])

    def test_ordinary_unused_refund_is_auto_approved_and_final_half_fen_rounds_once(self):
        from .billing import reserve_credits, settle_credits, credits_summary
        from .refunds import request_credit_refund
        reservation = reserve_credits(self.account, 'one-used', '1')
        settle_credits(self.account, reservation['id'], '1')
        result = request_credit_refund(self.account, self.user, self.order.pk, 'normal-refund')
        self.assertEqual((result['status'], result['amount_minor']), ('approved', '366'))
        self.assertEqual(request_credit_refund(self.account, self.user, self.order.pk, 'normal-refund'), result)
        self.assertEqual(credits_summary(self.account)['frozen'], '1')
        self.assertEqual(CreditRefund.objects.count(), 1)

    def test_issued_invoice_freezes_but_does_not_auto_approve_or_confirm_cash_refund(self):
        from .refunds import request_credit_refund
        from .billing import resolve_credit_refund, credits_summary
        InvoiceRequest.objects.create(account=self.account, order=self.order, created_by=self.user,
            recipient_email='fixture@example.invalid', title='Fixture', status='issued',
            request_key='invoice-fixture', request_hash='c'*64)
        result = request_credit_refund(self.account, self.user, self.order.pk, 'invoiced-refund')
        self.assertEqual(result['status'], 'review')
        self.assertEqual(credits_summary(self.account)['frozen'], '2')
        with self.assertRaisesRegex(ValueError, 'REFUND_REVIEW_REQUIRED'):
            resolve_credit_refund(self.account, result['id'], 'refunded', evidence_hash='b'*64)
