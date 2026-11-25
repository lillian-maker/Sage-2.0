"""Billing arithmetic only. Does not issue refunds or mutate entitlements.

Amounts are integer fen. Annual calculation is for an unmodified annual line;
upgrades, prior refunds, expiry disputes and taxes need line-level settlement.
"""
from calendar import monthrange
from datetime import datetime, timedelta
from decimal import Decimal, DecimalException, Inexact, localcontext
from fractions import Fraction


def billing_policy():
    """Public confirmed rules only; this does not imply connected billing."""
    return {
        'version': '2026-11-25.1', 'currency': 'CNY', 'merchant_region': 'CN-Shenzhen',
        'trial': {'successful_runs': 1, 'choices': ['voc', 'shopify_diagnosis'],
                  'payment_method_required': False},
        'credits': {'name': 'Sage Credits', 'purchased_valid_months': 1,
                    'subscription_grant': 'monthly', 'rollover': False,
                    'consume_order': ['earliest_expiry', 'subscription_before_purchased_on_tie'],
                    'business_consume_order': ['own_seat_included', 'business_shared'],
                    'business_shared_sources': ['subscription', 'purchased'],
                    'enterprise_included_grant': 'monthly_shared_by_purchased_seats',
                    'business_uses_personal_credits': False,
                    'member_shared_limits': False},
        'renewal': {'automatic_retry': False, 'repayment_window_days': 0,
                    'charge_days_before_expiry': 1, 'reminder_days_before_expiry': 2,
                    'grace_entitlements': False},
        'orders': {'unpaid_ttl_minutes': 15, 'unpaid_per_kind': 1, 'user_cancel': True},
        'auto_topup': {'audience': 'business', 'default_enabled': False,
                       'separate_authorization': True, 'requires_actual_shared_demand': True},
        'payment_methods': {'independent_replacement': True,
                            'remove_blocks_new_charges': True,
                            'successful_inflight_payment_fulfills': True},
    }


def _aware(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError('TIMEZONE_REQUIRED')


def _quantity(value):
    # Units are supplied by the caller; this does not decide pending D03 precision.
    if type(value) not in (int, Decimal) or not Decimal(value).is_finite() or value < 0:
        raise ValueError('INVALID_CREDITS_QUANTITY')


def month_boundary(original_anchor, month_offset):
    """Always pass the original anchor, never a previously clamped boundary."""
    _aware(original_anchor)
    if type(month_offset) is not int or month_offset < 0:
        raise ValueError('INVALID_MONTH_OFFSET')
    year, month = divmod(original_anchor.year * 12 + original_anchor.month - 1 + month_offset, 12)
    month += 1
    if not 1 <= year <= 9999:
        raise ValueError('INVALID_MONTH_OFFSET')
    return original_anchor.replace(year=year, month=month,
                                   day=min(original_anchor.day, monthrange(year, month)[1]))


# An unpaid checkout order closes this long after it was created; one unpaid
# order per product kind may exist at a time.
UNPAID_ORDER_TTL = timedelta(minutes=15)

# Automatic renewal charges during the last day of a term; its reminder goes out
# one day before that. Intents are prepared a day earlier so the reminder can be.
RENEWAL_CHARGE_LEAD = timedelta(days=1)
RENEWAL_PREPARE_LEAD = timedelta(days=3)


# Periods sold under this calendar rule end at the first local midnight after the
# month boundary, so a term never stops in the middle of its last day.
NEXT_MIDNIGHT_RULE = 'next-midnight-v1'


def next_midnight(value):
    """First midnight after value on value's own wall clock."""
    _aware(value)
    return value.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


def credits_pack_expiry(paid_at_local):
    """One calendar month after payment, carried to the next local midnight."""
    return next_midnight(month_boundary(paid_at_local, 1))


def annual_refund(paid_minor, original_anchor, requested_at):
    if type(paid_minor) is not int or paid_minor < 0:
        raise ValueError('INVALID_AMOUNT')
    _aware(original_anchor)
    _aware(requested_at)
    if requested_at < original_anchor:
        raise ValueError('SERVICE_NOT_STARTED')
    months = sum(month_boundary(original_anchor, offset) > requested_at for offset in range(12))
    # Integer half-up rounding once, never float currency arithmetic.
    return {'months': months, 'amount_minor': (2 * paid_minor * months + 12) // 24}


def subscription_change_amounts(current_price_minor, actual_cash_minor, starts_at, ends_at,
                                quoted_at, new_price_minor, prepaid_minor=0, *, mode='annual_to_annual',
                                unused_cash_minor=None):
    """Confirmed upgrade rules: exact elapsed time, one half-up final fen.

    Current catalog credit above unused real cash is a discount, never cash.
    Future unopened prepayments offset the new price; only their excess is
    returned. This arithmetic creates no payment, entitlement or refund.
    """
    for value in (current_price_minor,actual_cash_minor,new_price_minor,prepaid_minor):
        if type(value) is not int or value < 0:
            raise ValueError('INVALID_AMOUNT')
    for value in (starts_at,ends_at,quoted_at):_aware(value)
    if not starts_at <= quoted_at < ends_at:
        raise ValueError('SERVICE_NOT_ACTIVE')
    def seconds(delta):
        return Fraction((delta.days*86400+delta.seconds)*1000000+delta.microseconds,1000000)
    ratio=seconds(ends_at-quoted_at)/seconds(ends_at-starts_at)
    unused_cash=Fraction(actual_cash_minor)*ratio if unused_cash_minor is None else unused_cash_minor
    if not isinstance(unused_cash, Fraction) or not 0 <= unused_cash <= actual_cash_minor:
        raise ValueError('INVALID_AMOUNT')
    if mode not in ('monthly_to_monthly','monthly_to_annual','annual_to_annual'):
        raise ValueError('UNSUPPORTED_PLAN_CHANGE')
    # The current term's unused value always offsets the new price; an upgrade
    # never refunds it. Only an unopened prepaid term's excess is returned.
    credit=unused_cash if mode!='annual_to_annual' else max(Fraction(current_price_minor)*ratio,unused_cash)
    if unused_cash>new_price_minor:
        raise ValueError('CASH_CREDIT_REQUIRES_REVIEW')
    prepaid_applied=min(Fraction(prepaid_minor),max(Fraction(0),new_price_minor-credit))
    half_up=lambda value:(2*value.numerator+value.denominator)//(2*value.denominator)
    due=half_up(max(Fraction(0),new_price_minor-credit-prepaid_minor))
    future_refund=half_up(prepaid_minor-prepaid_applied)
    current_refund=0
    refund=future_refund
    carried=min(new_price_minor-due,half_up(unused_cash)+prepaid_minor-future_refund)
    return {'gross_minor':new_price_minor,'discount_minor':new_price_minor-due,'due_minor':due,
        'refund_minor':refund,'cash_carried_minor':carried,
        'promotion_minor':max(0,new_price_minor-due-carried),
        'current_refund_minor':current_refund,'future_refund_minor':future_refund,'mode':mode,
        'remaining_ratio':[ratio.numerator,ratio.denominator]}


def credits_refund(paid_minor, purchased, unused, reserved=0):
    if any(type(value) is not int for value in (paid_minor, purchased, unused, reserved)):
        raise ValueError('INTEGER_REQUIRED')
    if paid_minor < 0 or purchased <= 0 or not 0 <= unused <= purchased or not 0 <= reserved <= unused:
        raise ValueError('INVALID_REFUND_QUANTITY')
    if reserved:
        raise ValueError('TASK_SETTLEMENT_REQUIRED')
    return (2 * paid_minor * unused + purchased) // (2 * purchased)


def trial_transition(state, event, *, scenario=None, input_key=None,
                     input_validated=False, result_persisted=False, result_viewable=False):
    """R08 decision for a trusted task service, not a browser mutation endpoint.

    Persistence, user ownership, task/result verification and compare-and-swap
    are required at the future call site. Returning a state does not save it.
    """
    choices = billing_policy()['trial']['choices']
    if not isinstance(state, dict) or set(state) != {'status', 'scenario', 'input_key'}:
        raise ValueError('INVALID_TRIAL_STATE')
    if state['status'] not in ('available', 'locked', 'consumed'):
        raise ValueError('INVALID_TRIAL_STATE')
    if state['status'] == 'available':
        if state['scenario'] is not None or state['input_key'] is not None:
            raise ValueError('INVALID_TRIAL_STATE')
    elif state['scenario'] not in choices or not isinstance(state['input_key'], str) or not state['input_key'].strip():
        raise ValueError('INVALID_TRIAL_STATE')
    if any(type(value) is not bool for value in (input_validated, result_persisted, result_viewable)):
        raise ValueError('INVALID_TRIAL_EVENT')
    if event == 'start':
        if scenario not in choices or not isinstance(input_key, str) or not input_key.strip() or not input_validated:
            raise ValueError('TRIAL_INPUT_NOT_VALIDATED')
        if state['status'] == 'consumed':
            raise ValueError('TRIAL_ALREADY_USED')
        if state['status'] == 'locked':
            if (scenario, input_key) != (state['scenario'], state['input_key']):
                raise ValueError('TRIAL_ORIGINAL_TASK_REQUIRED')
            return dict(state)
        return {'status': 'locked', 'scenario': scenario, 'input_key': input_key}
    if event == 'complete':
        if state['status'] == 'available' or not result_persisted or not result_viewable:
            raise ValueError('TRIAL_RESULT_NOT_READY')
        if ((scenario is not None and scenario != state['scenario']) or
                (input_key is not None and input_key != state['input_key'])):
            raise ValueError('TRIAL_ORIGINAL_TASK_REQUIRED')
        return {**state, 'status': 'consumed'}
    if state['status'] != 'locked':
        raise ValueError('INVALID_TRIAL_TRANSITION')
    if event in ('cancel', 'disconnect', 'retry', 'recoverable_failure'):
        return dict(state)
    if event == 'confirmed_irrecoverable_system_failure':
        return {'status': 'available', 'scenario': None, 'input_key': None}
    raise ValueError('INVALID_TRIAL_EVENT')


def credit_consumption_order(batches, now):
    """R09 order of already authorized, unreserved and unfrozen batch balances.

    Caller must filter account/member permissions and compute available amounts
    from the ledger. No reservation, expiry mutation or task billing occurs here.
    Gift/mixed batches remain blocked until D07 is specified.
    """
    _aware(now)
    if not isinstance(batches, list):
        raise ValueError('INVALID_CREDITS_BATCH')
    eligible, ids = [], set()
    for batch in batches:
        if not isinstance(batch, dict) or set(batch) != {'id', 'source', 'expires_at', 'available'}:
            raise ValueError('INVALID_CREDITS_BATCH')
        if not isinstance(batch['id'], str) or not batch['id'] or batch['id'] in ids:
            raise ValueError('INVALID_CREDITS_BATCH')
        if batch['source'] not in ('subscription', 'purchased'):
            raise ValueError('CREDITS_SOURCE_POLICY_PENDING')
        _aware(batch['expires_at'])
        _quantity(batch['available'])
        ids.add(batch['id'])
        if batch['expires_at'] > now and batch['available'] > 0:
            eligible.append(batch)
    return sorted(eligible, key=lambda batch: (
        batch['expires_at'], 0 if batch['source'] == 'subscription' else 1, batch['id']))


def annual_upgrade_credit_delta(old_monthly, new_monthly):
    """R19: add the monthly tier difference, never a full new grant.

    This does not reset usage/expiry/month anchor. Inputs use identical declared
    units, leaving the pending smallest-credit-unit policy unchanged.
    """
    _quantity(old_monthly)
    _quantity(new_monthly)
    if new_monthly < old_monthly:
        raise ValueError('DOWNGRADE_NOT_ALLOWED')
    if type(old_monthly) is int and type(new_monthly) is int:
        return new_monthly - old_monthly
    old_decimal, new_decimal = Decimal(old_monthly), Decimal(new_monthly)
    if old_decimal.is_zero():
        return new_decimal
    parts = (old_decimal.as_tuple(), new_decimal.as_tuple())
    exponent = min(part.exponent for part in parts)
    precision = max(len(part.digits) + part.exponent - exponent for part in parts) + 1
    try:
        with localcontext() as context:
            context.prec = max(context.prec, precision)
            context.traps[Inexact] = True
            return new_decimal - old_decimal
    except (DecimalException, ValueError) as error:
        raise ValueError('CREDITS_PRECISION_UNSUPPORTED') from error


def renewal_failure_decision(payment_state, period_end, now):
    """R18 decision after a failed/unknown renewal, without charging.

    There is no retry and no repayment window: an unrenewed term lapses at its
    end, after which only a new subscription purchase restores service.
    """
    for value in (period_end, now):
        _aware(value)
    if payment_state not in ('failed', 'pending', 'unknown'):
        raise ValueError('INVALID_PAYMENT_STATE')
    lapsed = now >= period_end
    return {
        'subscription_period_active': not lapsed,
        'automatic_retry': False,
        'query_original_payment': payment_state in ('pending', 'unknown'),
        'allow_new_subscription_payment': payment_state == 'failed' and lapsed,
        'close_future_auto_renewal': lapsed,
        'new_subscription_requires_authorization': lapsed,
        'preserve_unexpired_purchased_credits': True,
    }


def payment_method_removal_decision(authorizations, payments):
    """R24 snapshot decision after an authorized removal request.

    The future transaction must persist block_new_charges before external calls.
    Fulfillment remains idempotent; this function never refunds or deletes data.
    """
    if not isinstance(authorizations, list) or not isinstance(payments, list):
        raise ValueError('INVALID_REMOVAL_STATE')
    valid_authorizations = ('pending', 'active', 'revoking', 'revoked', 'failed', 'unknown')
    valid_payments = ('created', 'pending', 'succeeded', 'failed', 'canceled', 'unknown')
    if any(value not in valid_authorizations for value in authorizations):
        raise ValueError('INVALID_AUTHORIZATION_STATE')
    if any(value not in valid_payments for value in payments):
        raise ValueError('INVALID_PAYMENT_STATE')
    has_inflight = any(value in ('created', 'pending', 'unknown') for value in payments)
    all_revoked = all(value == 'revoked' for value in authorizations)
    return {
        'status': 'removed' if all_revoked and not has_inflight else 'removal_pending',
        'block_new_charges': True,
        'query_inflight_payments': has_inflight,
        'revoke_authorizations': not all_revoked,
        'preserve_paid_entitlements': True,
        'successful_inflight_payment_action': 'fulfill_once',
        'automatic_refund': False,
    }
