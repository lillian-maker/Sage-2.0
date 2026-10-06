"""Billing arithmetic only. Does not issue refunds or mutate entitlements.

Amounts are integer fen. Annual calculation is for an unmodified annual line;
upgrades, prior refunds, expiry disputes and taxes need line-level settlement.
"""
from calendar import monthrange
from datetime import datetime
from decimal import Decimal, DecimalException, Inexact, localcontext


def billing_policy():
    """Public confirmed rules only; this does not imply connected billing."""
    return {
        'version': '2026-10-02', 'currency': 'CNY', 'merchant_region': 'CN-Shenzhen',
        'trial': {'successful_runs': 1, 'choices': ['voc', 'shopify_diagnosis'],
                  'payment_method_required': False},
        'credits': {'name': 'Sage Credits', 'purchased_valid_months': 12,
                    'subscription_grant': 'monthly', 'rollover': False,
                    'consume_order': ['earliest_expiry', 'subscription_before_purchased_on_tie']},
        'renewal': {'automatic_retry': False, 'manual_payment_window_days': 3,
                    'grace_entitlements': False},
        'auto_topup': {'audience': 'business', 'default_enabled': False,
                       'separate_authorization': True},
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


def renewal_failure_decision(payment_state, period_end, manual_payment_deadline, now):
    """R18 decision after a failed/unknown renewal, without charging.

    Caller supplies the three-day deadline calculated under the approved
    timezone/boundary policy; D03 is not silently decided here.
    """
    for value in (period_end, manual_payment_deadline, now):
        _aware(value)
    if manual_payment_deadline <= period_end:
        raise ValueError('INVALID_RENEWAL_WINDOW')
    if payment_state not in ('failed', 'pending', 'unknown'):
        raise ValueError('INVALID_PAYMENT_STATE')
    period_active = now < period_end
    window_closed = now >= manual_payment_deadline
    return {
        'subscription_period_active': period_active,
        'automatic_retry': False,
        'query_original_payment': payment_state in ('pending', 'unknown'),
        'allow_manual_payment': payment_state == 'failed' and not period_active and not window_closed,
        'allow_new_subscription_payment': payment_state == 'failed' and window_closed,
        'close_future_auto_renewal': window_closed,
        'new_subscription_requires_authorization': window_closed,
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
