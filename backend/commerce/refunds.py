"""Annual future-service refund requests and original-payment receipts."""
import hashlib
import json
import re
import uuid
from fractions import Fraction
from datetime import timezone as datetime_timezone

from django.db import transaction
from django.utils import timezone

from backend.models import AuditEvent, InvoiceRequest
from .billing import _authorize_purchase, _get, _identifier, _lock_account
from .models import (BillingIssue, BillingNotification, CreditBatch, CreditRefund, PaymentFact, PaymentAttempt,
    ServiceRefund, ServiceRefundReceipt, Subscription, SubscriptionChange, Order)
from .subscriptions import _boundary, _duration, _months, cancel_subscription


def _round(value):
    return (2*value.numerator+value.denominator)//(2*value.denominator)


def _remaining_cash(payment):
    reserved = sum(CreditRefund.objects.filter(payment=payment).exclude(state='released')
                   .values_list('amount_minor', flat=True))
    for refund in ServiceRefund.objects.filter(account=payment.order.account).exclude(status='terminated'):
        reserved += sum(item['amount_minor'] for item in refund.allocations if item['payment_id'] == str(payment.pk))
    if reserved > payment.amount_minor:
        raise ValueError('REFUND_CASH_INCONSISTENT')
    return payment.amount_minor-reserved


def _period_cash(period):
    order = period.order
    payments = list(order.payment_facts.filter(disposition='accepted'))
    if sum(payment.amount_minor for payment in payments) != order.amount_minor:
        raise ValueError('PAYMENT_NOT_CONFIRMED')
    sources = {str(payment.pk): payment.amount_minor for payment in payments}
    change = SubscriptionChange.objects.filter(new_subscription=period.subscription,
        order=period.order, state='activated').first()
    if change:
        for item in change.cash_sources:
            payment = _get(PaymentFact.objects, order__account=period.subscription.account, pk=item['payment_id'])
            amount = item['amount_minor']
            if type(amount) is not int or not 0 <= amount <= payment.amount_minor or payment.disposition != 'accepted':
                raise ValueError('REFUND_CASH_INCONSISTENT')
            sources[str(payment.pk)] = sources.get(str(payment.pk), 0)+amount
    return sources


def _period_cash_weights(period, cutoff, *, policy='seconds', seat_ids=None):
    """Exact unused cash by original payment and the seats it actually covered."""
    from .subscriptions import _period_seats, _purchase_seats
    subscription = period.subscription
    base = _period_cash(period)
    business = subscription.account.kind == 'business'
    current_seats = _period_seats(period, timezone.now()) if business else None
    base_seats = _period_seats(period, timezone.now(), include_additions=False) if business else None
    if seat_ids is not None:
        if not business:
            raise ValueError('FORBIDDEN')
        base_seats = base_seats.filter(pk__in=seat_ids)
    fraction = Fraction(base_seats.count(), period.snapshot['seat_quantity']) if business else Fraction(1)
    if cutoff <= period.starts_at:
        ratio = Fraction(1)
    elif cutoff >= period.ends_at:
        ratio = Fraction()
    elif policy == 'future_months':
        months = _months(subscription)
        remaining = sum(_boundary(subscription, period.cycle_index*months+month) >= cutoff for month in range(months))
        ratio = Fraction(remaining, months)
    else:
        ratio = _duration(period.ends_at-cutoff)/_duration(period.ends_at-period.starts_at)
    weights = {key:amount*fraction*ratio for key, amount in base.items()}
    period_ratio = ratio
    sources = dict(base)
    for addition in subscription.seat_purchases.filter(period=period, state='activated').select_related('order'):
        order = addition.order
        start = period.starts_at if addition.snapshot.get('scope') == 'next_paid' else addition.activated_at
        if (not order or not start or addition.account_id != subscription.account_id or
                order.account_id != subscription.account_id or addition.activated_at != order.paid_at or
                not period.starts_at <= start < period.ends_at):
            raise ValueError('REFUND_CASH_INCONSISTENT')
        payments = list(order.payment_facts.filter(disposition='accepted'))
        if sum(payment.amount_minor for payment in payments) != order.amount_minor:
            raise ValueError('PAYMENT_NOT_CONFIRMED')
        seats = _purchase_seats(addition).filter(pk__in=current_seats.values('pk'))
        if seat_ids is not None:
            seats = seats.filter(pk__in=seat_ids)
        share = Fraction(seats.count(), addition.quantity)
        ratio = (period_ratio if addition.snapshot.get('scope') == 'next_paid' and policy == 'future_months' else
            min(Fraction(1), max(Fraction(), _duration(period.ends_at-cutoff)/_duration(period.ends_at-start))))
        for payment in payments:
            key = str(payment.pk)
            sources[key] = sources.get(key, 0)+payment.amount_minor
            weights[key] = weights.get(key, Fraction())+payment.amount_minor*share*ratio
    return {key:value for key, value in weights.items() if value > 0}, sources


def _assessment(account, subscription):
    from .subscriptions import _seat_purchase_pending, _renewal_delivery_pending
    if subscription.billing_cycle != 'annual':
        raise ValueError('ANNUAL_REFUND_ONLY')
    if subscription.superseded_at:
        raise ValueError('SUBSCRIPTION_ALREADY_CHANGED')
    if subscription.service_refunds.filter(purpose='service').exclude(status='terminated').exists():
        raise ValueError('REFUND_ALREADY_REQUESTED')
    if subscription.service_refunds.filter(purpose='seat_reduction').exclude(status__in=['refunded', 'terminated']).exists():
        raise ValueError('REFUND_PENDING')
    if (_seat_purchase_pending(subscription) or
            SubscriptionChange.objects.filter(subscription=subscription, state='pending_payment').exists() or
            _renewal_delivery_pending(subscription)):
        raise ValueError('PAYMENT_RECONCILIATION_REQUIRED')
    now = timezone.now()
    periods = list(subscription.periods.select_related('order').order_by('starts_at'))
    current = next((period for period in periods if period.starts_at <= now < period.ends_at), None)
    future = [period for period in periods if period.starts_at > now]
    if not current and not future:
        raise ValueError('NO_REFUNDABLE_SERVICE')
    desired, pending_from, order_ids = {}, None, set()
    for period in ([current] if current else [])+future:
        start_month = period.cycle_index*12
        started = sum(_boundary(subscription, start_month+month) <= now for month in range(12))
        remaining = 12-started
        if not remaining:
            continue
        boundary = _boundary(subscription, start_month+started)
        pending_from = min(pending_from, boundary) if pending_from else boundary
        # Future credits must not already be usable; do not refund over them.
        if CreditBatch.objects.filter(subscription_period=period, service_month__gte=started).exists():
            raise ValueError('FUTURE_CREDITS_REQUIRE_REVIEW')
        weights, _ = _period_cash_weights(period, boundary, policy='future_months')
        for payment_id, value in weights.items():
            desired[payment_id] = desired.get(payment_id, Fraction())+value
        order_ids.add(period.order_id)
    if not desired or pending_from is None:
        raise ValueError('NO_REFUNDABLE_SERVICE')
    amounts = {}
    for payment_id, value in desired.items():
        payment = _get(PaymentFact.objects, order__account=account, pk=payment_id, disposition='accepted')
        amounts[payment_id] = min(value, Fraction(_remaining_cash(payment)))
    total = _round(sum(amounts.values(), Fraction()))
    if not total:
        raise ValueError('NO_REFUNDABLE_SERVICE')
    # Allocate the once-rounded total without rounding every source separately.
    whole = {key: value.numerator//value.denominator for key, value in amounts.items()}
    extra = total-sum(whole.values())
    for key in sorted(amounts, key=lambda item: (-(amounts[item]-whole[item]), item))[:extra]:
        whole[key] += 1
    allocations = [{'payment_id': key, 'amount_minor': amount} for key, amount in sorted(whole.items()) if amount]
    source_orders = set(PaymentFact.objects.filter(pk__in=whole).values_list('order_id', flat=True))
    issued = InvoiceRequest.objects.filter(order_id__in=source_orders,
        status__in=('issued', 'sending', 'sent', 'failed', 'unknown')).exists()
    abnormal = (PaymentFact.objects.filter(order_id__in=source_orders).exclude(disposition='accepted').exists() or
        PaymentAttempt.objects.filter(order_id__in=source_orders, needs_reconciliation=True).exists())
    contract = any(period.snapshot.get('contract_id') or period.snapshot.get('contract') or
        period.snapshot.get('entitlements', {}).get('contract') for period in periods if period.order_id in order_ids)
    return {'subscription_id': str(subscription.pk), 'eligible': True, 'amount_minor': str(total),
        'currency': 'CNY', 'current_service_ends_at': pending_from.astimezone(datetime_timezone.utc).isoformat(),
        'automatic_review': not (issued or abnormal or contract),
        'items': [{'order_id': str(_get(PaymentFact.objects, pk=item['payment_id']).order_id),
            'amount_minor': str(item['amount_minor'])} for item in allocations],
        '_allocations': allocations, '_pending_from': pending_from}


def service_refund_assessment(account, user, subscription_id):
    _authorize_purchase(account, user)
    subscription = _get(Subscription.objects, account=account, pk=subscription_id)
    try:
        result = _assessment(account, subscription)
    except ValueError as exc:
        result = {'subscription_id': str(subscription.pk), 'eligible': False,
            'reason': str(exc), 'amount_minor': None, 'currency': 'CNY',
            'automatic_review': False, 'items': []}
        if str(exc) == 'REFUND_ALREADY_REQUESTED':
            previous = subscription.service_refunds.exclude(status='terminated').order_by('-created_at', '-id').first()
            if previous:
                result['existing_refund'] = refund_data(previous)
        return result
    return {key: value for key, value in result.items() if not key.startswith('_')}


def refund_data(refund):
    return {'id': str(refund.pk), 'subscription_id': str(refund.subscription_id),
        'status': refund.status, 'amount_minor': str(refund.amount_minor), 'currency': 'CNY',
        'current_service_ends_at': refund.pending_from.astimezone(datetime_timezone.utc).isoformat(),
        'purpose':refund.purpose, 'auto_renew': refund.subscription.auto_renew if refund.purpose == 'seat_reduction' else False}


def credit_refund_data(item):
    return {'id': str(item.pk), 'status': 'refunded' if item.state == 'refunded' else
        'terminated' if item.state == 'released' else item.review_state,
        'amount_minor': str(item.amount_minor), 'currency': 'CNY'}


@transaction.atomic
def request_credit_refund(account, user, order_id, request_key):
    from .billing import reserve_credit_refund
    account = _lock_account(account)
    _authorize_purchase(account, user)
    _identifier(request_key)
    order = _get(Order.objects, account=account, pk=order_id)
    evidence = hashlib.sha256(f'credit-refund-20261016:{order.pk}:{user.pk}:{request_key}'.encode()).hexdigest()
    previous = CreditRefund.objects.filter(payment__order=order, request_key=request_key).first()
    if previous:
        if previous.review_evidence_hash != evidence:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return credit_refund_data(previous)
    result = reserve_credit_refund(account, order.pk, request_key, review_evidence_hash=evidence)
    item = CreditRefund.objects.select_for_update().get(pk=result['id'])
    if item.state == 'reserved':
        issued = InvoiceRequest.objects.filter(order=order,
            status__in=('issued', 'sending', 'sent', 'failed', 'unknown')).exists()
        contract = order.snapshot.get('contract_id') or order.snapshot.get('contract') or order.snapshot.get('entitlements', {}).get('contract')
        desired = 'review' if issued or contract else 'approved'
        if item.review_state != desired:
            item.review_state = desired
            item.save(update_fields=['review_state'])
    return credit_refund_data(item)


@transaction.atomic
def request_service_refund(account, user, subscription_id, request_key):
    account = _lock_account(account)
    _authorize_purchase(account, user)
    _identifier(request_key)
    if len(request_key) > 64:
        raise ValueError('INVALID_REQUEST')
    subscription = _get(Subscription.objects.select_for_update(), account=account, pk=subscription_id)
    fingerprint = hashlib.sha256(json.dumps({'subscription_id': str(subscription.pk),
        'actor_id': str(user.pk), 'policy': 'future-service-20261016'}, sort_keys=True).encode()).hexdigest()
    previous = ServiceRefund.objects.filter(account=account, request_key=request_key).first()
    if previous:
        if previous.request_hash != fingerprint:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return refund_data(previous)
    assessment = _assessment(account, subscription)
    cancel_subscription(account, user, subscription.pk)
    subscription.refresh_from_db()
    subscription.refund_cutoff_at = assessment['_pending_from']
    subscription.save(update_fields=['refund_cutoff_at'])
    refund = ServiceRefund.objects.create(account=account, subscription=subscription, created_by=user,
        request_key=request_key, request_hash=fingerprint,
        status='approved' if assessment['automatic_review'] else 'review',
        amount_minor=int(assessment['amount_minor']), allocations=assessment['_allocations'],
        pending_from=assessment['_pending_from'])
    AuditEvent.objects.create(actor=user, account=account, action='service_refund.requested', request_id=refund.pk)
    BillingNotification.objects.create(account=account, subscription=subscription,
        type='service_refund_requested', source_event=f'service-refund:{refund.pk}:requested',
        amount_minor=refund.amount_minor, currency='CNY', occurred_at=refund.created_at)
    return refund_data(refund)


@transaction.atomic
def approve_service_refund(account, refund_id, actor, *, evidence_hash):
    account = _lock_account(account)
    if (not actor.is_active or not actor.is_staff or not actor.has_perm('commerce.change_servicerefund') or
            not isinstance(evidence_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', evidence_hash)):
        raise ValueError('FORBIDDEN')
    refund = _get(ServiceRefund.objects.select_for_update(), account=account, pk=refund_id)
    if refund.status not in ('review', 'approved'):
        raise ValueError('REFUND_STATE_CONFLICT')
    if refund.status == 'review':
        refund.status, refund.evidence_hash = 'approved', evidence_hash
        refund.save(update_fields=['status', 'evidence_hash', 'updated_at'])
        AuditEvent.objects.create(actor=actor, account=account, action='service_refund.approved', request_id=refund.pk)
    return refund_data(refund)


@transaction.atomic
def record_service_refund_receipt(account, refund_id, payment_id, *, event_key, outcome,
                                  amount_minor, provider_reference, evidence_hash):
    """Trusted verified channel boundary only. No browser can assert a refund."""
    account = _lock_account(account)
    refund = _get(ServiceRefund.objects.select_for_update(), account=account, pk=refund_id)
    _identifier(event_key)
    if (refund.status == 'review' or outcome not in ('refunded', 'failed', 'unknown', 'terminated') or
            type(amount_minor) is not int or amount_minor <= 0 or
            not isinstance(evidence_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', evidence_hash) or
            not isinstance(provider_reference, str) or len(provider_reference) > 128 or
            (outcome == 'refunded' and not provider_reference)):
        raise ValueError('INVALID_REFUND_EVIDENCE')
    allocation = next((item for item in refund.allocations if item['payment_id'] == str(payment_id)), None)
    if not allocation:
        raise ValueError('NOT_FOUND')
    payment = _get(PaymentFact.objects, order__account=account, pk=payment_id)
    existing = refund.receipts.filter(payment=payment, event_key=event_key).first()
    if existing:
        if (existing.outcome, existing.amount_minor, existing.provider_reference, existing.evidence_hash) != (
                outcome, amount_minor, provider_reference, evidence_hash):
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return refund_data(refund)
    if refund.status in ('refunded', 'terminated'):
        raise ValueError('REFUND_STATE_CONFLICT')
    if amount_minor != allocation['amount_minor']:
        raise ValueError('REFUND_AMOUNT_MISMATCH')
    if outcome == 'refunded' and refund.receipts.filter(payment=payment, outcome='refunded').exists():
        raise ValueError('REFUND_ALREADY_CONFIRMED')
    if outcome == 'terminated' and refund.receipts.filter(payment=payment, outcome='refunded').exists():
        raise ValueError('REFUND_ALREADY_CONFIRMED')
    ServiceRefundReceipt.objects.create(refund=refund, payment=payment, event_key=event_key,
        outcome=outcome, amount_minor=amount_minor, provider_reference=provider_reference, evidence_hash=evidence_hash)
    succeeded = set(refund.receipts.filter(outcome='refunded').values_list('payment_id', flat=True))
    terminated = set(refund.receipts.filter(outcome='terminated').values_list('payment_id', flat=True))
    required = {uuid.UUID(item['payment_id']) for item in refund.allocations}
    if succeeded == required:
        refund.status = 'refunded'
    elif terminated == required and not succeeded:
        refund.status = 'terminated'
        subscription = _get(Subscription.objects.select_for_update(), account=account, pk=refund.subscription_id)
        if refund.purpose == 'seat_reduction':
            reduction = refund.seat_reduction
            from .subscriptions import _service_month
            month, _, _ = _service_month(subscription, reduction.period, reduction.applied_at)
            conflict = (subscription.superseded_at or subscription.refund_cutoff_at or reduction.period.ends_at <= timezone.now() or
                reduction.period.granted_months > month+1 or
                subscription.seat_reductions.filter(period=reduction.period, state='applied', applied_at__gt=reduction.applied_at).exists())
            if conflict:
                refund.status = 'review'
                BillingIssue.objects.update_or_create(account=account, code='SEAT_REFUND_RESTORE_REVIEW',
                    object_type='service_refund', object_id=refund.pk, defaults={'last_seen_at':timezone.now()})
            else:
                reduction.state = 'reverted'; reduction.save(update_fields=['state'])
        else:
            subscription.refund_cutoff_at = None
            subscription.save(update_fields=['refund_cutoff_at'])
        # Seat refunds preserve renewal; whole-service refunds keep it off.
    else:
        refund.status = 'unknown' if outcome in ('failed', 'unknown') else 'processing'
    refund.evidence_hash = evidence_hash
    refund.save(update_fields=['status', 'evidence_hash', 'updated_at'])
    if outcome in ('failed', 'unknown'):
        BillingIssue.objects.update_or_create(account=account, code='service_refund_unconfirmed',
            object_type='service_refund', object_id=refund.pk, defaults={'last_seen_at': timezone.now()})
    if refund.status == 'refunded':
        BillingNotification.objects.get_or_create(account=account,
            source_event=f'service-refund:{refund.pk}:refunded', defaults={
                'type': 'service_refund_refunded', 'subscription': refund.subscription,
                'amount_minor': refund.amount_minor, 'currency': 'CNY', 'occurred_at': timezone.now()})
    return refund_data(refund)
