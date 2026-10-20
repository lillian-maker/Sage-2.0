"""PRD examples; calculation tests do not imply live refund capability."""
import sys
import unittest
from datetime import datetime
from decimal import Decimal, localcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.billing_rules import month_boundary, annual_refund, credits_refund
from backend.billing_rules import (
    billing_policy, trial_transition, credit_consumption_order,
    annual_upgrade_credit_delta, renewal_failure_decision, payment_method_removal_decision,
)


class RulesTests(unittest.TestCase):
    def test_month_end_preserves_original_anchor(self):
        anchor = datetime.fromisoformat('2024-01-31T12:00:00+08:00')
        self.assertEqual(month_boundary(anchor, 1).isoformat(), '2024-02-29T12:00:00+08:00')
        self.assertEqual(month_boundary(anchor, 2).isoformat(), '2024-03-31T12:00:00+08:00')

    def test_annual_refund_only_future_whole_months(self):
        start = datetime.fromisoformat('2026-01-15T10:00:00+08:00')
        at = datetime.fromisoformat('2026-05-20T10:00:00+08:00')
        self.assertEqual(annual_refund(120000, start, at), {'months':7,'amount_minor':70000})

    def test_exact_boundary_counts_current_month_as_started(self):
        start = datetime.fromisoformat('2026-01-15T10:00:00+08:00')
        at = datetime.fromisoformat('2026-05-15T10:00:00+08:00')
        self.assertEqual(annual_refund(120000, start, at)['months'], 7)

    def test_expired_year_refund_is_zero(self):
        start = datetime.fromisoformat('2026-01-15T10:00:00+08:00')
        self.assertEqual(annual_refund(120000, start, month_boundary(start,12))['amount_minor'],0)

    def test_purchase_batch_refund_rounds_once_in_fen(self):
        self.assertEqual(credits_refund(10000, 300, 100), 3333)
        self.assertEqual(credits_refund(1, 2, 1), 1)
        self.assertEqual(credits_refund(10000, 300, 300), 10000)

    def test_rejects_unsettled_and_impossible_refunds(self):
        for args in [(10000,300,100,1), (10000,300,301,0),(-1,300,100,0),(100,0,0,0)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                credits_refund(*args)

    def test_rejects_refund_before_service_and_calendar_overflow(self):
        anchor = datetime.fromisoformat('2026-01-15T10:00:00+08:00')
        with self.assertRaisesRegex(ValueError, 'SERVICE_NOT_STARTED'):
            annual_refund(120000, anchor, datetime.fromisoformat('2026-01-14T10:00:00+08:00'))
        with self.assertRaisesRegex(ValueError, 'INVALID_MONTH_OFFSET'):
            month_boundary(anchor, 1000000)
        with self.assertRaisesRegex(ValueError, 'INVALID_REFUND_QUANTITY'):
            credits_refund(10000, 300, 100, 101)

    def test_timezone_and_boolean_amounts_are_not_accepted(self):
        anchor = datetime.fromisoformat('2026-01-15T10:00:00+08:00')
        for value in (None, '2026-01-15', datetime(2026, 1, 15)):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'TIMEZONE_REQUIRED'):
                annual_refund(10000, value, anchor)
        with self.assertRaisesRegex(ValueError, 'INVALID_AMOUNT'):
            annual_refund(True, anchor, anchor)


class ConfirmedPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.fromisoformat('2026-10-02T10:00:00+08:00')
        self.available = {'status': 'available', 'scenario': None, 'input_key': None}

    def locked(self):
        return trial_transition(self.available, 'start', scenario='voc',
                                input_key='normalized-brand', input_validated=True)

    def test_policy_records_confirmed_rules_not_payment_capability(self):
        policy = billing_policy()
        self.assertEqual(policy['currency'], 'CNY')
        self.assertEqual(policy['trial']['successful_runs'], 1)
        self.assertFalse(policy['renewal']['automatic_retry'])
        self.assertEqual(policy['renewal']['manual_payment_window_days'], 3)
        self.assertEqual(policy['credits']['purchased_valid_months'], 12)
        self.assertEqual(policy['credits']['business_consume_order'],
                         ['own_seat_included', 'business_shared'])
        self.assertEqual(policy['credits']['business_shared_sources'], ['subscription', 'purchased'])
        self.assertEqual(policy['credits']['enterprise_included_grant'], 'monthly_shared_by_purchased_seats')
        self.assertFalse(policy['credits']['business_uses_personal_credits'])
        self.assertFalse(policy['credits']['member_shared_limits'])
        self.assertFalse(policy['auto_topup']['default_enabled'])
        self.assertTrue(policy['auto_topup']['requires_actual_shared_demand'])
        self.assertNotIn('checkout_enabled', policy)

    def test_trial_input_validation_and_original_task_lock(self):
        with self.assertRaisesRegex(ValueError, 'TRIAL_INPUT_NOT_VALIDATED'):
            trial_transition(self.available, 'start', scenario='voc', input_key='brand')
        locked = self.locked()
        self.assertEqual(self.available['status'], 'available')
        for event in ('cancel', 'disconnect', 'retry', 'recoverable_failure'):
            self.assertEqual(trial_transition(locked, event), locked)
        with self.assertRaisesRegex(ValueError, 'TRIAL_ORIGINAL_TASK_REQUIRED'):
            trial_transition(locked, 'start', scenario='shopify_diagnosis',
                             input_key='other-store', input_validated=True)
        self.assertEqual(trial_transition(locked, 'start', scenario='voc',
                         input_key='normalized-brand', input_validated=True), locked)

    def test_trial_only_persisted_viewable_result_consumes_once(self):
        locked = self.locked()
        for persisted, viewable in ((False, False), (True, False), (False, True)):
            with self.assertRaisesRegex(ValueError, 'TRIAL_RESULT_NOT_READY'):
                trial_transition(locked, 'complete', result_persisted=persisted, result_viewable=viewable)
        consumed = trial_transition(locked, 'complete', result_persisted=True, result_viewable=True)
        self.assertEqual(consumed['status'], 'consumed')
        self.assertEqual(trial_transition(consumed, 'complete', result_persisted=True, result_viewable=True), consumed)
        with self.assertRaisesRegex(ValueError, 'TRIAL_ALREADY_USED'):
            trial_transition(consumed, 'start', scenario='voc', input_key='brand', input_validated=True)

    def test_only_confirmed_irrecoverable_failure_unlocks(self):
        self.assertEqual(trial_transition(self.locked(), 'confirmed_irrecoverable_system_failure'), self.available)
        for event in ('failed', 'network_timeout', 'unknown'):
            with self.subTest(event=event), self.assertRaisesRegex(ValueError, 'INVALID_TRIAL_EVENT'):
                trial_transition(self.locked(), event)
        consumed = trial_transition(self.locked(), 'complete', result_persisted=True, result_viewable=True)
        with self.assertRaisesRegex(ValueError, 'INVALID_TRIAL_TRANSITION'):
            trial_transition(consumed, 'confirmed_irrecoverable_system_failure')

    def test_trial_complete_rejects_explicit_wrong_task_identity(self):
        locked = self.locked()
        for identity in ({'scenario': 'shopify_diagnosis'}, {'input_key': 'another-brand'},
                         {'scenario': 'voc', 'input_key': 'another-brand'}):
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, 'TRIAL_ORIGINAL_TASK_REQUIRED'):
                trial_transition(locked, 'complete', result_persisted=True, result_viewable=True, **identity)
        self.assertEqual(locked['status'], 'locked')
        consumed = trial_transition(locked, 'complete', scenario='voc', input_key='normalized-brand',
                                    result_persisted=True, result_viewable=True)
        self.assertEqual(consumed['status'], 'consumed')
        with self.assertRaisesRegex(ValueError, 'TRIAL_ORIGINAL_TASK_REQUIRED'):
            trial_transition(consumed, 'complete', input_key='another-brand',
                             result_persisted=True, result_viewable=True)

    def test_credit_batches_earliest_expiry_then_subscription_no_mutation(self):
        def batch(key, source, expiry, available=10):
            return {'id': key, 'source': source, 'expires_at': datetime.fromisoformat(expiry), 'available': available}
        batches = [
            batch('late-sub', 'subscription', '2026-11-02T10:00:00+08:00'),
            batch('early-purchase', 'purchased', '2026-10-03T10:00:00+08:00'),
            batch('tie-purchase', 'purchased', '2026-10-04T10:00:00+08:00'),
            batch('tie-sub', 'subscription', '2026-10-04T10:00:00+08:00'),
            batch('expired', 'purchased', '2026-10-02T10:00:00+08:00'),
            batch('empty', 'subscription', '2026-10-03T10:00:00+08:00', 0),
        ]
        self.assertEqual([batch['id'] for batch in credit_consumption_order(batches, self.now)],
                         ['early-purchase', 'tie-sub', 'tie-purchase', 'late-sub'])
        self.assertEqual(len(batches), 6)

    def test_credits_unknown_promotion_policy_and_invalid_quantities_rejected(self):
        batch = {'id': 'a', 'source': 'purchased', 'expires_at': month_boundary(self.now, 1), 'available': 1}
        with self.assertRaisesRegex(ValueError, 'CREDITS_SOURCE_POLICY_PENDING'):
            credit_consumption_order([{**batch, 'source': 'gift'}], self.now)
        with self.assertRaisesRegex(ValueError, 'INVALID_CREDITS_BATCH'):
            credit_consumption_order([batch, batch], self.now)
        for value in (True, -1, float('nan'), Decimal('NaN'), Decimal('Infinity')):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'INVALID_CREDITS_QUANTITY'):
                credit_consumption_order([{**batch, 'available': value}], self.now)

    def test_annual_upgrade_grants_only_difference_and_does_not_choose_precision(self):
        self.assertEqual(annual_upgrade_credit_delta(1000, 2500), 1500)
        self.assertEqual(annual_upgrade_credit_delta(2500, 2500), 0)
        self.assertEqual(annual_upgrade_credit_delta(Decimal('1.2'), Decimal('2.3')), Decimal('1.1'))
        with self.assertRaisesRegex(ValueError, 'DOWNGRADE_NOT_ALLOWED'):
            annual_upgrade_credit_delta(2500, 1000)

    def test_failed_renewal_has_no_entitlement_grace_or_automatic_retry(self):
        end = datetime.fromisoformat('2026-10-02T10:00:00+08:00')
        deadline = datetime.fromisoformat('2026-10-05T10:00:00+08:00')
        decision = renewal_failure_decision('failed', end, deadline, end)
        self.assertFalse(decision['automatic_retry'])
        self.assertFalse(decision['subscription_period_active'])
        self.assertTrue(decision['allow_manual_payment'])
        self.assertTrue(decision['preserve_unexpired_purchased_credits'])
        self.assertFalse(decision['close_future_auto_renewal'])
        closed = renewal_failure_decision('failed', end, deadline, deadline)
        self.assertFalse(closed['allow_manual_payment'])
        self.assertTrue(closed['close_future_auto_renewal'])
        self.assertTrue(closed['new_subscription_requires_authorization'])
        self.assertTrue(closed['allow_new_subscription_payment'])
        self.assertFalse(decision['allow_new_subscription_payment'])

    def test_annual_upgrade_decimal_difference_is_exact_above_context_precision(self):
        old = Decimal('123456789012345678901234567890.123456789')
        new = Decimal('123456789012345678901234567891.123456790')
        with localcontext() as context:
            context.prec = 3
            self.assertEqual(annual_upgrade_credit_delta(old, new), Decimal('1.000000001'))
            self.assertEqual(context.prec, 3)
        self.assertEqual(annual_upgrade_credit_delta(0, Decimal('123456789012345678901234567890.123456789')),
                         Decimal('123456789012345678901234567890.123456789'))

    def test_unknown_renewal_never_opens_duplicate_payment(self):
        end, deadline = self.now, month_boundary(self.now, 1)
        for state in ('pending', 'unknown'):
            for at in (end, deadline):
                with self.subTest(state=state, at=at):
                    result = renewal_failure_decision(state, end, deadline, at)
                    self.assertTrue(result['query_original_payment'])
                    self.assertFalse(result['allow_manual_payment'])
                    self.assertFalse(result['automatic_retry'])
                    self.assertFalse(result['allow_new_subscription_payment'])

    def test_method_removal_waits_for_inflight_and_actual_revocation(self):
        for authorization, payment in (('active', 'failed'), ('unknown', 'succeeded'),
                                       ('revoked', 'pending'), ('revoked', 'unknown')):
            with self.subTest(authorization=authorization, payment=payment):
                result = payment_method_removal_decision([authorization], [payment])
                self.assertEqual(result['status'], 'removal_pending')
                self.assertTrue(result['block_new_charges'])
        complete = payment_method_removal_decision(['revoked'], ['succeeded', 'failed'])
        self.assertEqual(complete['status'], 'removed')
        self.assertEqual(complete['successful_inflight_payment_action'], 'fulfill_once')
        self.assertFalse(complete['automatic_refund'])
        self.assertTrue(complete['preserve_paid_entitlements'])

    def test_decision_helpers_reject_unsupported_states(self):
        with self.assertRaisesRegex(ValueError, 'INVALID_TRIAL_STATE'):
            trial_transition({'status': 'available'}, 'start')
        with self.assertRaisesRegex(ValueError, 'INVALID_RENEWAL_WINDOW'):
            renewal_failure_decision('failed', self.now, self.now, self.now)
        with self.assertRaisesRegex(ValueError, 'INVALID_PAYMENT_STATE'):
            renewal_failure_decision('succeeded', self.now, month_boundary(self.now, 1), self.now)
        with self.assertRaisesRegex(ValueError, 'INVALID_AUTHORIZATION_STATE'):
            payment_method_removal_decision(['deleted'], [])


class SubscriptionChangeAmountTests(unittest.TestCase):
    def calculate(self, **changes):
        from backend.billing_rules import subscription_change_amounts
        values=dict(current_price_minor=10000,actual_cash_minor=10000,
            starts_at=datetime.fromisoformat('2026-10-01T00:00:00+08:00'),
            ends_at=datetime.fromisoformat('2026-11-01T00:00:00+08:00'),
            quoted_at=datetime.fromisoformat('2026-10-16T12:00:00+08:00'),new_price_minor=20000)
        values.update(changes)
        return subscription_change_amounts(**values)

    def test_precise_seconds_cash_floor_and_final_half_up(self):
        self.assertEqual(self.calculate()['due_minor'],15000)
        result=self.calculate(current_price_minor=5000)
        self.assertEqual(result['due_minor'],15000)
        result=self.calculate(quoted_at=datetime.fromisoformat('2026-10-16T12:00:01+08:00'))
        self.assertNotEqual(result['remaining_ratio'],[1,2])

    def test_promotion_is_never_cash_and_prepaid_excess_is_refundable(self):
        result=self.calculate(current_price_minor=30000,actual_cash_minor=10000,prepaid_minor=10000)
        self.assertEqual((result['due_minor'],result['refund_minor'],result['cash_carried_minor'],result['promotion_minor']),(0,5000,10000,10000))

    def test_cash_source_rounding_conserves_refund_plus_carried_cash(self):
        result=self.calculate(current_price_minor=26,actual_cash_minor=14,new_price_minor=3,
            quoted_at=datetime.fromisoformat('2026-10-28T21:36:00+08:00'),prepaid_minor=1)
        self.assertEqual(result['refund_minor']+result['cash_carried_minor'],2)

    def test_invalid_or_excess_cash_requires_review(self):
        with self.assertRaisesRegex(ValueError,'CASH_CREDIT_REQUIRES_REVIEW'):self.calculate(new_price_minor=1)
        with self.assertRaisesRegex(ValueError,'INVALID_AMOUNT'):self.calculate(actual_cash_minor=True)


if __name__ == '__main__':
    unittest.main()
