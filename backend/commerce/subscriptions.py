"""Paid service periods, monthly delivery and local cancellation.

Activation and channel confirmation are trusted internal operations, not HTTP
payment endpoints. They do not charge, sign a mandate or verify a PSP callback.
"""
import re
import uuid
import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from backend.billing_rules import month_boundary, renewal_failure_decision
from backend.models import AuditEvent
from .billing import (_authorize_purchase, _datetime, _get, _identifier, _lock_account,
                      _quantity, _text, _attempt_dict, _effective_attempt_state, _payment_snapshot, _sale_gate, create_quote, create_order, grant_credits)
from .models import CreditBatch, Quote, Order, Subscription, SubscriptionPeriod, PaymentAttempt, PaymentMethod, AuthorizationChange


def active_subscription_periods(account, at=None):
    """One service cutoff rule shared by upgrades and future-month refunds."""
    at=at or timezone.now()
    return SubscriptionPeriod.objects.filter(subscription__account=account,starts_at__lte=at,ends_at__gt=at).filter(
        Q(subscription__superseded_at__isnull=True)|Q(subscription__superseded_at__gt=at)).filter(
        Q(subscription__refund_cutoff_at__isnull=True)|Q(subscription__refund_cutoff_at__gt=at))


def _purchase_seats(change):
    from .models import PaidSeat
    if not change.covered_seat_ids:
        return change.seats.order_by('position')
    ids = change.covered_seat_ids
    if (not isinstance(ids, list) or len(ids) != change.quantity or len(set(ids)) != len(ids) or
            any(not isinstance(value, str) for value in ids)):
        raise ValueError('LEDGER_INCONSISTENT')
    seats = PaidSeat.objects.filter(subscription=change.subscription, pk__in=ids).order_by('position')
    if seats.count() != change.quantity:
        raise ValueError('LEDGER_INCONSISTENT')
    return seats


def _period_seats(period, at=None, *, include_additions=True):
    """Immutable period capacity, plus purchases covering this exact period."""
    from .models import PaidSeat
    ids = period.snapshot.get('seat_ids')
    if ids is None:
        base = Q(position__lte=period.snapshot['seat_quantity'])
    else:
        if (not isinstance(ids, list) or len(ids) != period.snapshot['seat_quantity'] or
                any(not isinstance(value, str) for value in ids) or len(set(ids)) != len(ids)):
            raise ValueError('LEDGER_INCONSISTENT')
        base = Q(pk__in=ids)
    rows = PaidSeat.objects.filter(subscription=period.subscription)
    if rows.filter(base).count() != period.snapshot['seat_quantity']:
        raise ValueError('LEDGER_INCONSISTENT')
    if include_additions:
        additions = period.subscription.seat_purchases.filter(period=period, state='activated')
        if at is not None:
            additions = additions.filter(activated_at__lte=at)
        for change in additions:
            base |= Q(pk__in=_purchase_seats(change).values('pk'))
    reductions = period.subscription.seat_reductions.filter(period=period, state='applied')
    if at is not None:
        reductions = reductions.filter(applied_at__lte=at)
    removed = [value for reduction in reductions for value in reduction.seat_ids]
    return rows.filter(base).exclude(pk__in=removed).order_by('position')


def _service_month(subscription, period, at):
    for index in range(_months(subscription)):
        offset = period.cycle_index*_months(subscription)+index
        start, end = _boundary(subscription, offset), _boundary(subscription, offset+1)
        if start <= at < end:
            return index, start, end
    raise ValueError('SERVICE_NOT_ACTIVE')


def _duration(delta):
    from fractions import Fraction
    return Fraction((delta.days*86400+delta.seconds)*1000000+delta.microseconds, 1000000)


def _seat_purchase_credits(change, at):
    from fractions import Fraction
    if change.snapshot.get('scope') == 'next_paid':
        return 0, _boundary(change.subscription, change.period.cycle_index*_months(change.subscription)+1), _quantity(change.snapshot['credits_quantity'])
    index, start, end = _service_month(change.subscription, change.period, at)
    quantity = _quantity(change.snapshot['credits_quantity'])*_duration(end-at)/_duration(end-start)
    cents = quantity*100
    per_seat = Fraction((2*cents.numerator+cents.denominator)//(2*cents.denominator), 100)
    return index, end, per_seat


def _seat_purchase_dict(change):
    from .billing import purchase_terms
    at = change.activated_at or change.quote.created_at
    _, _, quantity = _seat_purchase_credits(change, at)
    future = change.period if change.snapshot.get('scope') == 'next_paid' else change.subscription.periods.filter(starts_at__gte=change.period.ends_at).order_by('starts_at').first()
    status = change.state
    if status == 'quoted' and change.quote.expires_at <= timezone.now():
        status = 'expired'
    return {'id':str(change.pk), 'subscription_id':str(change.subscription_id), 'added_seats':change.quantity,
        'scope':change.snapshot.get('scope', 'current'), 'cover_seat_ids':change.snapshot.get('cover_seat_ids', []),
        'service_start':change.period.starts_at.astimezone(ZoneInfo('UTC')).isoformat() if change.snapshot.get('scope') == 'next_paid' else at.astimezone(ZoneInfo('UTC')).isoformat(),
        'seat_count_before':change.snapshot['seat_count_before'], 'seat_count_after':change.snapshot['seat_count_after'],
        'amount_minor':str(change.quote.amount_minor), 'currency':'CNY',
        'expires_at':change.quote.expires_at.astimezone(ZoneInfo('UTC')).isoformat(),
        'service_end':change.period.ends_at.astimezone(ZoneInfo('UTC')).isoformat(),
        'first_month_credits_per_seat':_text(quantity), 'first_month_credits_total':_text(quantity*change.quantity),
        'credits_estimated':change.activated_at is None,
        'next_paid_seats':_period_seats(future).count() if future else None,
        'next_paid_starts_at':future.starts_at.astimezone(ZoneInfo('UTC')).isoformat() if future else None,
        'terms':purchase_terms(change.quote.snapshot['terms_version']), 'status':status,
        'order_id':str(change.order_id) if change.order_id else None}


def _seat_purchase_pending(subscription, *, exclude=None):
    from .models import SeatPurchaseChange
    return SeatPurchaseChange.objects.filter(subscription=subscription,
        state__in=('pending_payment', 'payment_review')).exclude(pk=exclude).exists()


def _renewal_delivery_pending(subscription):
    # A terminal payment status is not proof that the frozen paid period exists.
    return PaymentAttempt.objects.filter(subscription=subscription).filter(
        Q(state__in=('created', 'pending', 'unknown', 'review')) |
        Q(order__payment_facts__disposition='accepted', order__subscriptionperiod__isnull=True)).exists()


def _service_refund_pending(subscription):
    from .models import ServiceRefund
    return ServiceRefund.objects.filter(subscription=subscription).filter(
        Q(purpose='service') & ~Q(status='terminated') |
        Q(purpose='seat_reduction') & ~Q(status__in=['refunded', 'terminated'])).exists()


def _seat_ids(values, *, allow_empty=False):
    if not isinstance(values, list) or len(values) > 10000 or (not values and not allow_empty):
        raise ValueError('INVALID_SEATS')
    try:
        ids = [str(uuid.UUID(value)) for value in values if isinstance(value, str)]
    except (ValueError, AttributeError) as exc:
        raise ValueError('INVALID_SEATS') from exc
    if len(ids) != len(values) or len(set(ids)) != len(ids):
        raise ValueError('INVALID_SEATS')
    return sorted(ids)


@transaction.atomic
def seat_purchase_quote(account, actor, subscription_id, quantity, *, request_key, scope='current', cover_seat_ids=None):
    from .models import SeatPurchaseChange, SubscriptionChange, ServiceRefund
    from .billing import purchase_terms
    account = _lock_account(account)
    _authorize_purchase(account, actor)
    if account.kind != 'business':
        raise ValueError('FORBIDDEN')
    if type(quantity) is not int or not 1 <= quantity <= 10000:
        raise ValueError('INVALID_SEATS')
    cover_seat_ids = _seat_ids([] if cover_seat_ids is None else cover_seat_ids, allow_empty=True)
    if scope not in ('current', 'next_paid') or (scope == 'current' and cover_seat_ids) or len(cover_seat_ids) > quantity:
        raise ValueError('INVALID_SEATS')
    if not isinstance(request_key, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,64}', request_key):
        raise ValueError('INVALID_IDEMPOTENCY_KEY')
    fingerprint_data = {'subscription_id':str(subscription_id), 'quantity':quantity}
    if scope != 'current':
        fingerprint_data.update(scope=scope, cover_seat_ids=cover_seat_ids)
    fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True).encode()).hexdigest()
    existing = SeatPurchaseChange.objects.filter(account=account, request_key=request_key).first()
    if existing:
        if existing.actor_id != actor.pk or existing.request_hash != fingerprint:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return _seat_purchase_dict(existing)
    subscription = _get(Subscription.objects.select_for_update(), account=account, pk=subscription_id)
    now = timezone.now()
    period = active_subscription_periods(account, now).filter(subscription=subscription).select_related('order__quote__product').first()
    if not period:
        raise ValueError('SERVICE_NOT_ACTIVE')
    current = period
    if scope == 'next_paid':
        period = subscription.periods.filter(starts_at__gt=now).select_related('order__quote__product').order_by('starts_at').first()
        if not period:
            raise ValueError('NEXT_PAID_PERIOD_REQUIRED')
        eligible = _period_seats(current, now).exclude(pk__in=_period_seats(period).values('pk'))
        if eligible.filter(pk__in=cover_seat_ids).count() != len(cover_seat_ids):
            raise ValueError('SEAT_COVERAGE_CHANGED')
    if subscription.refund_cutoff_at or _service_refund_pending(subscription):
        raise ValueError('REFUND_PENDING')
    # Only a never-dispatched expired order can be closed without asking a payment provider.
    for pending in SeatPurchaseChange.objects.filter(subscription=subscription, state='pending_payment',
            quote__expires_at__lte=now).select_related('order'):
        if pending.order and not pending.order.paid_at and not pending.order.payment_facts.exists() and not PaymentAttempt.objects.filter(order=pending.order).exists():
            pending.order.status = 'closed';pending.order.save(update_fields=['status'])
            pending.state = 'expired';pending.save(update_fields=['state'])
    if (_seat_purchase_pending(subscription) or SubscriptionChange.objects.filter(subscription=subscription, state='pending_payment').exists() or
            _renewal_delivery_pending(subscription)):
        raise ValueError('PAYMENT_RECONCILIATION_REQUIRED')
    product = period.order.quote.product
    _sale_gate(product, account, now, period.snapshot['channel'], period.snapshot['buyer_region'], period.snapshot['merchant_entity'])
    count = _period_seats(period, now).count()
    if count+quantity > 10000:
        raise ValueError('INVALID_SEATS')
    _, _, month_end = _service_month(subscription, current, now)
    amount = period.snapshot['unit_amount_minor']*quantity*(_duration(period.ends_at-now)/_duration(period.ends_at-period.starts_at) if scope == 'current' else 1)
    from fractions import Fraction
    amount = Fraction(amount)
    due = (2*amount.numerator+amount.denominator)//(2*amount.denominator)
    if due > 9007199254740991:
        raise ValueError('CONFIG_INCOMPLETE')
    document = purchase_terms(period.snapshot['terms_version'])
    identifier = uuid.uuid4()
    snapshot = {**period.snapshot, 'seat_quantity':count+quantity, 'amount_minor':due,
        'terms_document_digest':document['digest'], 'seat_purchase_change_id':str(identifier)}
    snapshot.pop('seat_ids', None)
    snapshot.pop('subscription_change_id', None)
    quote = Quote.objects.create(account=account, product=product, snapshot=snapshot, amount_minor=due,
        currency='CNY', expires_at=min(now+timedelta(minutes=10), month_end, period.starts_at if scope == 'next_paid' else period.ends_at))
    change = SeatPurchaseChange.objects.create(id=identifier, account=account, subscription=subscription,
        period=period, actor=actor, quote=quote, quantity=quantity, request_key=request_key, request_hash=fingerprint,
        snapshot={'seat_count_before':count, 'seat_count_after':count+quantity,
            'scope':scope, 'cover_seat_ids':cover_seat_ids,
            'credits_quantity':period.snapshot['credits_quantity'], 'source_fingerprint':_change_source_fingerprint(subscription)})
    return _seat_purchase_dict(change)


def seat_purchase_detail(account, actor, subscription_id, quote_id):
    from .models import SeatPurchaseChange
    _authorize_purchase(account, actor)
    return _seat_purchase_dict(_get(SeatPurchaseChange.objects, account=account, subscription_id=subscription_id, pk=quote_id))


@transaction.atomic
def confirm_seat_purchase(account, actor, subscription_id, quote_id, *, agreement_acceptance, request_key):
    from .models import SeatPurchaseChange, SubscriptionChange, ServiceRefund
    from .billing import purchase_terms
    account = _lock_account(account)
    _authorize_purchase(account, actor);_identifier(request_key)
    change = _get(SeatPurchaseChange.objects.select_for_update(of=('self',)).select_related('quote__product', 'subscription', 'period'),
        account=account, subscription_id=subscription_id, pk=quote_id)
    if (agreement_acceptance != change.quote.snapshot['terms_version'] or
            purchase_terms(agreement_acceptance)['digest'] != change.quote.snapshot['terms_document_digest']):
        raise ValueError('TERMS_MISMATCH')
    if change.order_id:
        if change.order.idempotency_key != request_key or change.order.created_by_id != actor.pk:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return _seat_purchase_dict(change)
    now = timezone.now()
    if change.quote.expires_at <= now:
        raise ValueError('QUOTE_EXPIRED')
    if change.snapshot['source_fingerprint'] != _change_source_fingerprint(change.subscription):
        raise ValueError('SOURCE_SUBSCRIPTION_CHANGED')
    future_purchase = change.snapshot.get('scope') == 'next_paid'
    if (change.subscription.superseded_at or change.subscription.refund_cutoff_at or
            (not future_purchase and not active_subscription_periods(account, now).filter(pk=change.period_id).exists()) or
            (future_purchase and change.period.starts_at <= now)):
        raise ValueError('SOURCE_SUBSCRIPTION_CHANGED')
    if _service_refund_pending(change.subscription):
        raise ValueError('REFUND_PENDING')
    if (_seat_purchase_pending(change.subscription, exclude=change.pk) or
            SubscriptionChange.objects.filter(subscription=change.subscription, state='pending_payment').exists() or
            _renewal_delivery_pending(change.subscription)):
        raise ValueError('PAYMENT_RECONCILIATION_REQUIRED')
    _sale_gate(change.quote.product, account, now, change.quote.snapshot['channel'],
        change.quote.snapshot['buyer_region'], change.quote.snapshot['merchant_entity'])
    if Order.objects.filter(account=account, idempotency_key=request_key).exists():
        raise ValueError('IDEMPOTENCY_CONFLICT')
    fingerprint = hashlib.sha256(json.dumps({'seat_quote_id':str(change.pk), 'agreement_acceptance':agreement_acceptance}, sort_keys=True).encode()).hexdigest()
    paid = change.quote.amount_minor == 0
    change.order = Order.objects.create(account=account, created_by=actor, quote=change.quote, idempotency_key=request_key,
        request_hash=fingerprint, snapshot=change.quote.snapshot, amount_minor=change.quote.amount_minor, currency='CNY',
        status='paid_pending_entitlement' if paid else 'pending_payment', paid_at=now if paid else None)
    change.state = 'pending_payment';change.save(update_fields=['order', 'state'])
    if paid:
        fulfill_seat_purchase(account, change.pk)
        change.refresh_from_db()
    return _seat_purchase_dict(change)


@transaction.atomic
def fulfill_seat_purchase(account, change_id):
    from django.db.models import Max
    from .models import SeatPurchaseChange, PaidSeat, BillingNotification, BillingIssue
    account = _lock_account(account)
    change = _get(SeatPurchaseChange.objects.select_for_update(of=('self',)).select_related('order', 'subscription', 'period'),
        account=account, pk=change_id)
    if change.state == 'activated':
        return _seat_purchase_dict(change)
    if not change.order_id or not change.order.paid_at:
        raise ValueError('PAYMENT_NOT_CONFIRMED')
    if change.order.amount_minor and not change.order.payment_facts.filter(disposition='accepted',
            amount_minor=change.order.amount_minor, currency='CNY').exists():
        raise ValueError('PAYMENT_NOT_CONFIRMED')
    if (change.order.paid_at < change.quote.created_at or change.order.paid_at > timezone.now() or
            change.order.paid_at >= (change.period.starts_at if change.snapshot.get('scope') == 'next_paid' else change.period.ends_at) or
            change.subscription.superseded_at or change.subscription.refund_cutoff_at):
        change.state = 'payment_review';change.save(update_fields=['state'])
        BillingIssue.objects.get_or_create(account=account, code='SEAT_PURCHASE_PAYMENT_REVIEW',
            object_type='seat_purchase', object_id=change.pk, defaults={'last_seen_at':timezone.now()})
        return _seat_purchase_dict(change)
    if change.state != 'pending_payment':
        raise ValueError('ORDER_STATE_CONFLICT')
    position = PaidSeat.objects.filter(subscription=change.subscription).aggregate(value=Max('position'))['value'] or 0
    covered = change.snapshot.get('cover_seat_ids', [])
    if _period_seats(change.period).filter(pk__in=covered).exists():
        raise ValueError('SEAT_COVERAGE_CHANGED')
    new_seats = PaidSeat.objects.bulk_create([PaidSeat(subscription=change.subscription, position=position+index+1, purchase_change=change)
        for index in range(change.quantity-len(covered))])
    change.covered_seat_ids = covered+[str(seat.pk) for seat in new_seats]
    change.state, change.activated_at = 'activated', change.order.paid_at
    change.save(update_fields=['state', 'activated_at', 'covered_seat_ids'])
    grant_due_credits(account)
    change.order.status = 'fulfilled';change.order.save(update_fields=['status'])
    BillingNotification.objects.create(account=account,
        type='next_seats_purchased' if change.snapshot.get('scope') == 'next_paid' else 'seats_purchased', source_event=f'seat-purchase:{change.pk}',
        subscription=change.subscription, order=change.order, amount_minor=change.order.amount_minor,
        currency='CNY', occurred_at=change.activated_at)
    return _seat_purchase_dict(change)


def _eligible_reduction_seats(account, period, seat_ids, at):
    seats = _period_seats(period, at).filter(pk__in=seat_ids)
    if seats.count() != len(seat_ids) or seats.filter(membership__isnull=False).exists():
        raise ValueError('SEAT_NOT_RELEASABLE')
    reserved = {str(row.seat_id) for row in _pending_invitations(account, at) if row.seat_id}
    if reserved.intersection(seat_ids):
        raise ValueError('SEAT_RESERVED_FOR_INVITATION')
    for seat in seats:
        pending, _, locked = _seat_usage_state(seat, at)
        if pending:
            raise ValueError('TASK_SETTLEMENT_REQUIRED')
        if locked:
            raise ValueError('SEAT_LOCKED_UNTIL_NEXT_SERVICE_MONTH')
    if _period_seats(period, at).count()-len(seat_ids) < period.order.quote.product.minimum_seats:
        raise ValueError('MINIMUM_SEATS_REQUIRED')
    return list(seats)


def _seat_reduction_dict(change):
    from .refunds import refund_data
    values = change.snapshot
    status = 'expired' if change.state == 'quoted' and change.expires_at <= timezone.now() else change.state
    return {'id':str(change.pk), 'subscription_id':str(change.subscription_id),
        'seat_ids':change.seat_ids, 'seat_positions':values['seat_positions'], 'removed_seats':len(change.seat_ids),
        'seat_count_before':values['seat_count_before'], 'seat_count_after':values['seat_count_after'],
        'amount_minor':str(values['amount_minor']), 'currency':'CNY', 'expires_at':change.expires_at.astimezone(ZoneInfo('UTC')).isoformat(),
        'service_end':change.period.ends_at.astimezone(ZoneInfo('UTC')).isoformat(), 'refund_from':values['refund_from'], 'policy':values['policy'],
        'next_paid_seats':values['next_paid_seats'], 'next_paid_starts_at':values['next_paid_starts_at'],
        'status':status, 'refund':refund_data(change.refund) if change.refund_id else None}


@transaction.atomic
def seat_reduction_quote(account, actor, subscription_id, seat_ids, *, request_key):
    from fractions import Fraction
    from backend.models import InvoiceRequest
    from .models import SeatReduction, SubscriptionChange, PaymentFact
    from .refunds import _period_cash_weights, _remaining_cash, _round
    account = _lock_account(account); _authorize_purchase(account, actor); _identifier(request_key)
    if account.kind != 'business':
        raise ValueError('FORBIDDEN')
    if len(request_key) > 64:
        raise ValueError('INVALID_IDEMPOTENCY_KEY')
    seat_ids = _seat_ids(seat_ids)
    fingerprint = hashlib.sha256(json.dumps({'subscription_id':str(subscription_id), 'seat_ids':seat_ids}, sort_keys=True).encode()).hexdigest()
    existing = SeatReduction.objects.filter(account=account, request_key=request_key).first()
    if existing:
        if existing.actor_id != actor.pk or existing.request_hash != fingerprint:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return _seat_reduction_dict(existing)
    subscription = _get(Subscription.objects.select_for_update(), account=account, pk=subscription_id)
    now = timezone.now()
    period = active_subscription_periods(account, now).filter(subscription=subscription).select_related('order__quote__product').first()
    if not period:
        raise ValueError('SERVICE_NOT_ACTIVE')
    if subscription.refund_cutoff_at or _service_refund_pending(subscription):
        raise ValueError('REFUND_PENDING')
    if (_seat_purchase_pending(subscription) or _renewal_delivery_pending(subscription) or
            SubscriptionChange.objects.filter(subscription=subscription, state='pending_payment').exists()):
        raise ValueError('PAYMENT_RECONCILIATION_REQUIRED')
    seats = _eligible_reduction_seats(account, period, seat_ids, now)
    _, _, month_end = _service_month(subscription, period, now)
    teams = period.snapshot['entitlements'].get('resource_mode') == 'seat_included'
    cutoff = now if teams else month_end
    weights, _ = _period_cash_weights(period, cutoff, policy='seconds' if teams else 'future_months', seat_ids=seat_ids)
    for key, value in weights.items():
        weights[key] = min(value, Fraction(_remaining_cash(_get(PaymentFact.objects, order__account=account, pk=key))))
    amount = _round(sum(weights.values(), Fraction()))
    allocations = _allocate_change_cash(weights, amount)
    orders = set(PaymentFact.objects.filter(pk__in=weights).values_list('order_id', flat=True))
    review = (InvoiceRequest.objects.filter(order_id__in=orders, status__in=['issued', 'sending', 'sent', 'failed', 'unknown']).exists() or
        PaymentFact.objects.filter(order_id__in=orders).exclude(disposition='accepted').exists() or
        PaymentAttempt.objects.filter(order_id__in=orders, needs_reconciliation=True).exists() or
        bool(period.snapshot.get('contract_id') or period.snapshot.get('contract') or period.snapshot.get('entitlements', {}).get('contract')))
    count = _period_seats(period, now).count()
    future = subscription.periods.filter(starts_at__gt=now).order_by('starts_at').first()
    change = SeatReduction.objects.create(account=account, subscription=subscription, period=period, actor=actor,
        seat_ids=seat_ids, request_key=request_key, request_hash=fingerprint,
        expires_at=min(now+timedelta(minutes=10), month_end, period.ends_at), snapshot={
            'seat_count_before':count, 'seat_count_after':count-len(seats), 'seat_positions':[seat.position for seat in seats],
            'seat_versions':{str(seat.pk):seat.version for seat in seats}, 'amount_minor':amount,
            'allocations':allocations, 'automatic_review':not review, 'refund_from':cutoff.isoformat(),
            'policy':'teams_remaining_time' if teams else 'enterprise_future_months',
            'next_paid_seats':_period_seats(future).count() if future else None,
            'next_paid_starts_at':future.starts_at.isoformat() if future else None,
            'source_fingerprint':_change_source_fingerprint(subscription)})
    return _seat_reduction_dict(change)


def seat_reduction_detail(account, actor, subscription_id, quote_id):
    from .models import SeatReduction
    _authorize_purchase(account, actor)
    return _seat_reduction_dict(_get(SeatReduction.objects, account=account, subscription_id=subscription_id, pk=quote_id))


@transaction.atomic
def confirm_seat_reduction(account, actor, subscription_id, quote_id, *, request_key):
    from .models import SeatReduction, ServiceRefund, SubscriptionChange, BillingNotification, PaymentFact
    from .refunds import _remaining_cash
    account = _lock_account(account); _authorize_purchase(account, actor); _identifier(request_key)
    if len(request_key) > 64:
        raise ValueError('INVALID_IDEMPOTENCY_KEY')
    change = _get(SeatReduction.objects.select_for_update(of=('self',)).select_related('period__order__quote__product', 'subscription'),
        account=account, subscription_id=subscription_id, pk=quote_id)
    if change.confirmation_key:
        if change.confirmation_key != request_key or change.actor_id != actor.pk:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return _seat_reduction_dict(change)
    if change.actor_id != actor.pk or SeatReduction.objects.filter(account=account, confirmation_key=request_key).exists():
        raise ValueError('IDEMPOTENCY_CONFLICT')
    now = timezone.now()
    if change.expires_at <= now:
        raise ValueError('QUOTE_EXPIRED')
    if (change.snapshot['source_fingerprint'] != _change_source_fingerprint(change.subscription) or
            not active_subscription_periods(account, now).filter(pk=change.period_id).exists()):
        raise ValueError('SOURCE_SUBSCRIPTION_CHANGED')
    if _service_refund_pending(change.subscription):
        raise ValueError('REFUND_PENDING')
    if (_seat_purchase_pending(change.subscription) or _renewal_delivery_pending(change.subscription) or
            SubscriptionChange.objects.filter(subscription=change.subscription, state='pending_payment').exists()):
        raise ValueError('PAYMENT_RECONCILIATION_REQUIRED')
    seats = _eligible_reduction_seats(account, change.period, change.seat_ids, now)
    if any(change.snapshot['seat_versions'][str(seat.pk)] != seat.version for seat in seats):
        raise ValueError('SEAT_CHANGED')
    for row in change.snapshot['allocations']:
        if row['amount_minor'] > _remaining_cash(_get(PaymentFact.objects, order__account=account, pk=row['payment_id'])):
            raise ValueError('REFUND_CASH_INCONSISTENT')
    if change.snapshot['amount_minor']:
        change.refund = ServiceRefund.objects.create(account=account, subscription=change.subscription, created_by=actor,
            request_key='seat-reduction:'+str(change.pk), request_hash=change.request_hash, purpose='seat_reduction',
            status='approved' if change.snapshot['automatic_review'] else 'review',
            amount_minor=change.snapshot['amount_minor'], allocations=change.snapshot['allocations'],
            pending_from=datetime.fromisoformat(change.snapshot['refund_from']))
    change.state, change.applied_at, change.confirmation_key = 'applied', now, request_key
    change.save(update_fields=['refund', 'state', 'applied_at', 'confirmation_key'])
    AuditEvent.objects.create(actor=actor, account=account, action='seats.reduced', request_id=change.pk)
    BillingNotification.objects.create(account=account, type='seats_reduced', source_event=f'seat-reduction:{change.pk}',
        subscription=change.subscription, amount_minor=change.snapshot['amount_minor'], currency='CNY', occurred_at=now)
    return _seat_reduction_dict(change)


def _billing_operator(account, original):
    """Enterprise mandates belong to the company, not the employee's access."""
    if account.kind!='business':
        _authorize_purchase(account,original)
        return original
    from backend.models import Membership
    member=Membership.objects.filter(account=account,is_active=True,user__is_active=True,
        role__in=['owner','billing_admin']).select_related('user').order_by('id').first()
    if not member:raise ValueError('CURRENT_ADMIN_REQUIRED')
    return member.user


def _business_plan_switch_allowed(source, target):
    if source.get('audience') != 'business':
        return True
    before, after = source.get('entitlements', {}).get('resource_mode'), target.entitlements.get('resource_mode')
    return (before == after and before in ('seat_included', 'shared_included', 'shared_only') and
        not ({source.get('code'), target.code} == {'teams', 'enterprise'}))


def _check_business_plan_switch(change):
    period = change.subscription.periods.filter(starts_at__lte=change.quote.created_at,
        ends_at__gt=change.quote.created_at).first()
    if not period or not _business_plan_switch_allowed(period.snapshot, change.quote.product):
        raise ValueError('BUSINESS_PLAN_SWITCH_NOT_SUPPORTED')


def subscription_change_options(account, actor, subscription_id):
    from .models import ProductVersion
    _authorize_purchase(account,actor)
    subscription=_get(Subscription.objects,account=account,pk=subscription_id)
    period=active_subscription_periods(account).filter(subscription=subscription).first()
    if not period or subscription.refund_cutoff_at:
        return {'subscription_id':str(subscription.pk),'items':[],'available':False,'reason':'SERVICE_NOT_ACTIVE'}
    if subscription.mandate.get('renewal_setup_failure'):
        # The current switch flow requires proof of revoking an old agreement.
        # Do not collect an upgrade payment for an agreement that never existed.
        return {'subscription_id':str(subscription.pk),'items':[],'available':False,'reason':'RENEWAL_SETUP_REQUIRED'}
    rank=period.snapshot.get('entitlements',{}).get('upgrade_rank')
    seats = _period_seats(period, timezone.now()).count() if account.kind == 'business' else 1
    items=[]
    if type(rank) is int:
        for product in ProductVersion.objects.filter(audience=account.kind,kind='subscription',state='published',effective_at__lte=timezone.now()):
            if not _business_plan_switch_allowed(period.snapshot, product):
                continue
            target=product.entitlements.get('upgrade_rank')
            if (type(target) is not int or target < rank or
                    (subscription.billing_cycle=='annual' and product.billing_cycle!='annual') or
                    (product.billing_cycle==subscription.billing_cycle and target==rank) or
                    product.minimum_seats>seats):continue
            try:
                _sale_gate(product,account,timezone.now(),period.snapshot['channel'],period.snapshot['buyer_region'],period.snapshot['merchant_entity'])
                from .billing import purchase_terms
                purchase_terms(product.terms_version)
            except ValueError:continue
            items.append({'product_id':str(product.pk),'code':product.code,'billing_cycle':product.billing_cycle,
                'amount_minor':str(product.unit_amount_minor*seats),
                'credits_quantity':product.credits_quantity,'currency':product.currency})
    return {'subscription_id':str(subscription.pk),'items':items,'available':bool(items),
            'reason':None if items else 'APPROVED_UPGRADE_PRODUCTS_REQUIRED'}


def _change_source_fingerprint(subscription):
    periods=[]
    for period in subscription.periods.select_related('order').order_by('cycle_index'):
        periods.append({'id':str(period.pk),'order_id':str(period.order_id),'starts_at':period.starts_at.isoformat(),
            'ends_at':period.ends_at.isoformat(),'payments':list(period.order.payment_facts.order_by('pk').values(
                'id','amount_minor','disposition'))})
    from .models import SeatPurchaseChange
    additions = list(SeatPurchaseChange.objects.filter(subscription=subscription, state='activated').order_by('pk').values(
        'id', 'period_id', 'order_id', 'quantity', 'activated_at', 'covered_seat_ids'))
    reductions = list(subscription.seat_reductions.exclude(state='quoted').order_by('pk').values(
        'id', 'period_id', 'seat_ids', 'state', 'applied_at', 'refund__status'))
    return hashlib.sha256(json.dumps({'periods':periods,'seat_purchases':additions,'seat_reductions':reductions,'authorization_version':subscription.authorization_version},
        sort_keys=True,default=str).encode()).hexdigest()


def _allocate_change_cash(weights, total):
    """Allocate one already rounded total deterministically to real cash sources."""
    allocations={key:value.numerator//value.denominator for key,value in weights.items()}
    left=total-sum(allocations.values())
    if left<0 or left>len(allocations):raise ValueError('CASH_ALLOCATION_INCONSISTENT')
    order=sorted(weights,key=lambda key:(-(weights[key]-allocations[key]),key))
    for key in order[:left]:allocations[key]+=1
    return [{'payment_id':key,'amount_minor':amount} for key,amount in sorted(allocations.items()) if amount]


def _change_dict(change):
    from .billing import purchase_terms
    data={key:str(change.snapshot[key]) for key in ('gross_minor','discount_minor','due_minor','refund_minor')}
    return {**data,'id':str(change.pk),'subscription_id':str(change.subscription_id),
        'product_id':str(change.quote.product_id),'expires_at':change.quote.expires_at.astimezone(ZoneInfo('UTC')).isoformat(),
        'currency':'CNY','billing_cycle':change.quote.snapshot['billing_cycle'],
        'credits_quantity':change.quote.snapshot['credits_quantity'],'period_rule':'new_full_period_on_activation',
        'status':change.state,'order_id':str(change.order_id) if change.order_id else None,
        'payment_required':change.snapshot['due_minor']>0,
        'activated_at':change.activated_at.astimezone(ZoneInfo('UTC')).isoformat() if change.activated_at else None,
        'terms':purchase_terms(change.quote.snapshot['terms_version'])}


@transaction.atomic
def subscription_change_quote(account, actor, subscription_id, product_id, *, request_key):
    from fractions import Fraction
    from backend.billing_rules import subscription_change_amounts
    from .models import SubscriptionChange, ProductVersion, ServiceRefund
    from .billing import purchase_terms
    account=_lock_account(account);_authorize_purchase(account,actor);_identifier(request_key)
    if len(request_key)>64:raise ValueError('INVALID_IDEMPOTENCY_KEY')
    fingerprint=hashlib.sha256(json.dumps({'subscription_id':str(subscription_id),'product_id':str(product_id)},sort_keys=True).encode()).hexdigest()
    previous=SubscriptionChange.objects.filter(account=account,request_key=request_key).select_related('quote').first()
    if previous:
        if previous.actor_id!=actor.pk or previous.request_hash!=fingerprint:raise ValueError('IDEMPOTENCY_CONFLICT')
        return _change_dict(previous)
    subscription=_get(Subscription.objects.select_for_update(),account=account,pk=subscription_id)
    period=active_subscription_periods(account).filter(subscription=subscription).select_related('order').first()
    if not period:raise ValueError('SERVICE_NOT_ACTIVE')
    if (subscription.refund_cutoff_at or _service_refund_pending(subscription)):
        raise ValueError('REFUND_PENDING')
    if (_seat_purchase_pending(subscription) or
            _renewal_delivery_pending(subscription) or
            SubscriptionChange.objects.filter(subscription=subscription,state='pending_payment').exists()):
        raise ValueError('PAYMENT_RECONCILIATION_REQUIRED')
    from .models import Reservation
    if Reservation.objects.filter(account=account,state='reserved',shared_task__isnull=True).exists():
        raise ValueError('LEGACY_RESERVATION_REVIEW_REQUIRED')
    product=_get(ProductVersion.objects.select_for_update(),pk=product_id)
    if not _business_plan_switch_allowed(period.snapshot, product):
        raise ValueError('BUSINESS_PLAN_SWITCH_NOT_SUPPORTED')
    options=subscription_change_options(account,actor,subscription.pk)
    if str(product.pk) not in {item['product_id'] for item in options['items']}:raise ValueError('UPGRADE_NOT_AVAILABLE')
    current=ProductVersion.objects.filter(code=period.snapshot['code'],audience=account.kind,kind='subscription',
        billing_cycle=subscription.billing_cycle,state='published',effective_at__lte=timezone.now()).order_by('-version').first()
    if not current or type(current.unit_amount_minor) is not int or current.unit_amount_minor<=0:
        raise ValueError('CURRENT_PRICE_REQUIRED')
    from .refunds import _period_cash_weights, _remaining_cash
    from .models import PaymentFact
    now = timezone.now()
    own_weights, own_cash = _period_cash_weights(period, now)
    future = list(subscription.periods.filter(starts_at__gt=now).select_related('order').order_by('starts_at'))
    future_cash = []
    for item in future:
        weights, _ = _period_cash_weights(item, item.starts_at)
        if any(value.denominator != 1 for value in weights.values()):
            raise ValueError('REFUND_CASH_INCONSISTENT')
        future_cash.append((item, {key:int(value) for key, value in weights.items()}))
    prepaid = sum(sum(values.values()) for _, values in future_cash)
    seats = _period_seats(period, now).count() if account.kind == 'business' else 1
    for key, weight in own_weights.items():
        own_weights[key] = min(weight, _remaining_cash(_get(PaymentFact.objects, order__account=account, pk=key)))
    amounts=subscription_change_amounts(current.unit_amount_minor*seats,sum(own_cash.values()),
        period.starts_at,period.ends_at,now,product.unit_amount_minor*seats,prepaid,
        mode=subscription.billing_cycle+'_to_'+product.billing_cycle,
        unused_cash_minor=sum(own_weights.values(), Fraction()))
    weights={} if amounts['mode']=='monthly_to_monthly' else dict(own_weights)
    applied=Fraction(prepaid-amounts['future_refund_minor'])
    refund_allocations=(_allocate_change_cash(own_weights,amounts['current_refund_minor'])
                        if amounts['current_refund_minor'] else [])
    for _,sources in future_cash:
        for key,value in sources.items():
            take=min(applied,Fraction(value));applied-=take
            weights[key]=weights.get(key,Fraction(0))+take
            remaining=value-int(take)
            if remaining:refund_allocations.append({'payment_id':key,'amount_minor':remaining})
    cash_sources=_allocate_change_cash(weights,amounts['cash_carried_minor'])
    document=purchase_terms(product.terms_version)
    identifier=uuid.uuid4();now=timezone.now()
    snapshot={'product_id':str(product.pk),'code':product.code,'version':product.version,'kind':'subscription',
        'audience':product.audience,'billing_cycle':product.billing_cycle,'seat_quantity':seats,'currency':'CNY',
        'unit_amount_minor':product.unit_amount_minor,'amount_minor':amounts['due_minor'],
        'credits_quantity':product.credits_quantity,'entitlements':product.entitlements,'entitlement_version':product.entitlement_version,
        'credit_rule_version':product.credit_rule_version,'terms_version':product.terms_version,
        'terms_document_digest':document['digest'],'channel':period.snapshot['channel'],
        'buyer_region':period.snapshot['buyer_region'],'merchant_entity':period.snapshot['merchant_entity'],
        'subscription_change_id':str(identifier)}
    quote=Quote.objects.create(account=account,product=product,snapshot=snapshot,amount_minor=amounts['due_minor'],
        currency='CNY',expires_at=now+timedelta(minutes=10))
    change=SubscriptionChange.objects.create(id=identifier,account=account,subscription=subscription,actor=actor,
        quote=quote,request_key=request_key,request_hash=fingerprint,cash_sources=cash_sources,
        snapshot={**amounts,'source_fingerprint':_change_source_fingerprint(subscription),'refund_allocations':refund_allocations})
    return _change_dict(change)


@transaction.atomic
def subscription_change_detail(account, actor, subscription_id, quote_id):
    from .models import SubscriptionChange
    account=_lock_account(account);_authorize_purchase(account,actor)
    change=_get(SubscriptionChange.objects.select_related('quote'),account=account,subscription_id=subscription_id,pk=quote_id)
    return _change_dict(change)


@transaction.atomic
def confirm_subscription_change(account, actor, subscription_id, quote_id, *, agreement_acceptance, request_key):
    from .models import SubscriptionChange
    from .billing import purchase_terms
    account=_lock_account(account);_authorize_purchase(account,actor);_identifier(request_key)
    change=_get(SubscriptionChange.objects.select_for_update().select_related('quote__product','subscription'),
        account=account,subscription_id=subscription_id,pk=quote_id)
    _check_business_plan_switch(change)
    if (agreement_acceptance!=change.quote.snapshot['terms_version'] or
            purchase_terms(agreement_acceptance)['digest']!=change.quote.snapshot['terms_document_digest']):
        raise ValueError('TERMS_MISMATCH')
    if change.order_id:
        if change.order.idempotency_key!=request_key or change.order.created_by_id!=actor.pk:raise ValueError('IDEMPOTENCY_CONFLICT')
        return _change_dict(change)
    if change.quote.expires_at<=timezone.now():raise ValueError('QUOTE_EXPIRED')
    if change.subscription.superseded_at or change.subscription.refund_cutoff_at:raise ValueError('SOURCE_SUBSCRIPTION_CHANGED')
    if (_seat_purchase_pending(change.subscription) or
            _renewal_delivery_pending(change.subscription)):
        raise ValueError('PAYMENT_RECONCILIATION_REQUIRED')
    if _change_source_fingerprint(change.subscription)!=change.snapshot['source_fingerprint']:raise ValueError('SOURCE_SUBSCRIPTION_CHANGED')
    if SubscriptionChange.objects.filter(subscription=change.subscription,state='pending_payment').exclude(pk=change.pk).exists():
        raise ValueError('PAYMENT_RECONCILIATION_REQUIRED')
    _sale_gate(change.quote.product,account,timezone.now(),change.quote.snapshot['channel'],change.quote.snapshot['buyer_region'],change.quote.snapshot['merchant_entity'])
    if Order.objects.filter(account=account,idempotency_key=request_key).exists():raise ValueError('IDEMPOTENCY_CONFLICT')
    fingerprint=hashlib.sha256(json.dumps({'quote_id':str(change.pk),'agreement_acceptance':agreement_acceptance},sort_keys=True).encode()).hexdigest()
    paid=change.quote.amount_minor==0
    change.order=Order.objects.create(account=account,created_by=actor,quote=change.quote,idempotency_key=request_key,
        request_hash=fingerprint,snapshot=change.quote.snapshot,amount_minor=change.quote.amount_minor,currency='CNY',
        status='paid_pending_entitlement' if paid else 'pending_payment',paid_at=timezone.now() if paid else None)
    change.state='pending_payment';change.save(update_fields=['order','state'])
    return _change_dict(change)


@transaction.atomic
def activate_subscription_change(account, change_id, *, activated_at, mandate,
                                  old_authorization_evidence_hash, payment_method_id=None):
    from .models import SubscriptionChange, PaidSeat, ServiceRefund, BillingNotification
    account=_lock_account(account)
    change=_get(SubscriptionChange.objects.select_for_update(of=('self',)).select_related('quote','order','subscription'),
        account=account,pk=change_id)
    _check_business_plan_switch(change)
    if change.state=='activated':
        new=change.new_subscription
        if (change.activated_at!=activated_at or new.mandate!=mandate or
                str(new.payment_method_id or '')!=str(payment_method_id or '') or
                change.subscription.cancellation_evidence_hash!=old_authorization_evidence_hash):
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return _change_dict(change)
    if change.state!='pending_payment' or not change.order_id:raise ValueError('CHANGE_NOT_CONFIRMED')
    # The account lock serializes billing writes; lock these concrete rows too,
    # without asking PostgreSQL to lock the nullable joined order.
    change.order=_get(Order.objects.select_for_update(),account=account,pk=change.order_id)
    source=_get(Subscription.objects.select_for_update(),account=account,pk=change.subscription_id)
    _datetime(activated_at)
    if not change.order.paid_at or activated_at<change.order.paid_at:raise ValueError('PAYMENT_NOT_CONFIRMED')
    if activated_at>timezone.now():raise ValueError('INVALID_ACTIVATION_TIME')
    if (source.superseded_at or source.refund_cutoff_at or
            _change_source_fingerprint(source)!=change.snapshot['source_fingerprint']):raise ValueError('SOURCE_SUBSCRIPTION_CHANGED')
    if _seat_purchase_pending(source) or _renewal_delivery_pending(source):
        raise ValueError('PAYMENT_RECONCILIATION_REQUIRED')
    if (not isinstance(mandate,dict) or not isinstance(old_authorization_evidence_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',old_authorization_evidence_hash) or
            mandate.get('agreement_reference')==source.mandate.get('agreement_reference')):raise ValueError('INDEPENDENT_AUTHORIZATION_REQUIRED')
    _paid_order(account,change.order_id)
    source.superseded_at=activated_at
    source.save(update_fields=['superseded_at'])
    cancel_subscription(account,change.actor,source.pk)
    confirm_cancellation(account,source.pk,agreement_reference=source.mandate['agreement_reference'],evidence_hash=old_authorization_evidence_hash)
    CreditBatch.objects.filter(subscription_period__subscription=source,expires_at__gt=activated_at).update(expires_at=activated_at)
    old_period = source.periods.filter(starts_at__lte=activated_at, ends_at__gt=activated_at).first()
    seats = ({index:seat.membership_id for index,seat in enumerate(_period_seats(old_period, activated_at), 1)}
        if account.kind == 'business' and old_period else {})
    result=activate_subscription(account,change.order_id,activated_at=activated_at,billing_timezone=source.billing_timezone,
        calendar_rule_version=source.calendar_rule_version,mandate=mandate,payment_method_id=payment_method_id)
    change.new_subscription_id=result['id'];change.activated_at=activated_at;change.state='activated'
    change.save(update_fields=['new_subscription','activated_at','state'])
    for seat in PaidSeat.objects.filter(subscription_id=result['id']):
        seat.membership_id=seats.get(seat.position);seat.save(update_fields=['membership'])
    if change.snapshot['refund_minor']:
        ServiceRefund.objects.create(account=account,subscription=source,created_by=change.actor,
            request_key='change:'+str(change.pk),request_hash=hashlib.sha256(json.dumps(change.snapshot['refund_allocations'],sort_keys=True).encode()).hexdigest(),
            amount_minor=change.snapshot['refund_minor'],allocations=change.snapshot['refund_allocations'],pending_from=activated_at)
    BillingNotification.objects.create(account=account,type='plan_changed',source_event=f'plan-change:{change.pk}',
        subscription_id=result['id'],order=change.order,amount_minor=change.order.amount_minor,currency='CNY',occurred_at=activated_at)
    return _change_dict(change)


@transaction.atomic
def record_expired_notifications(account):
    """Trusted internal scan only. A period ending is not account-wide expiry.

    Records existing endpoints without changing balances, dispatching payments
    or sending messages. GET handlers must never invoke this write operation.
    """
    from .models import BillingNotification, SeatPurchaseChange
    account = _lock_account(account)
    now, count = timezone.now(), 0
    for change in SeatPurchaseChange.objects.filter(account=account, state='activated',
            period__ends_at__gt=now, period__ends_at__lte=now+timedelta(days=1)).select_related('period', 'subscription'):
        future = change.subscription.periods.filter(starts_at=change.period.ends_at).first()
        if not future or not _purchase_seats(change).exclude(pk__in=_period_seats(future).values('pk')).exists():
            continue
        _, created = BillingNotification.objects.get_or_create(account=account,
            source_event=f'seat-purchase:{change.pk}:ending', defaults={
                'type':'seat_capacity_ending', 'order_id':change.order_id,
                'subscription':change.subscription, 'occurred_at':now})
        count += int(created)
    for batch in CreditBatch.objects.filter(account=account,expires_at__lte=now).select_related('subscription_period'):
        _, created = BillingNotification.objects.get_or_create(account=account,
            source_event=f'credit-batch:{batch.pk}:expired',defaults={
                'type':'credit_batch_expired','order_id':batch.order_id,
                'subscription_id':batch.subscription_period.subscription_id if batch.subscription_period_id else None,
                'occurred_at':batch.expires_at})
        count += int(created)
    for period in SubscriptionPeriod.objects.filter(subscription__account=account,ends_at__lte=now):
        _, created = BillingNotification.objects.get_or_create(account=account,
            source_event=f'service-period:{period.pk}:ended',defaults={
                'type':'service_period_ended','order_id':period.order_id,
                'subscription_id':period.subscription_id,'occurred_at':period.ends_at})
        count += int(created)
    return count


@transaction.atomic
def record_topup_authorization(account, user, *, payment_method_id, product_id, threshold,
                               monthly_cap_minor, mandate, terms_version, consent_evidence_hash,
                               expected_version, request_key, preset_key=None):
    """Trusted independently verified consent only; no scheduler or debit here.

    Budget calendar/trigger policy is deliberately absent until D09 is approved.
    Recording authorization alone never enables automatic charging.
    """
    from django.conf import settings
    from .models import TopupAuthorization, ProductVersion, CreditFreeze
    from .billing import _sale_gate, _text
    account = _lock_account(account)
    _authorize_purchase(account, user)
    if account.kind != 'business':
        raise ValueError('BUSINESS_ONLY')
    _identifier(request_key)
    _identifier(terms_version)
    quantity = _text(_quantity(threshold, positive=True))
    if (type(expected_version) is not int or expected_version < 0 or type(monthly_cap_minor) is not int or
        not 0 < monthly_cap_minor <= 9223372036854775807 or not isinstance(consent_evidence_hash,str) or
        not re.fullmatch(r'[a-f0-9]{64}',consent_evidence_hash)):
        raise ValueError('INVALID_TOPUP_CONSENT')
    method = _get(PaymentMethod.objects.select_for_update(),account=account,pk=payment_method_id)
    product = _get(ProductVersion.objects.all(),pk=product_id)
    _mandate(mandate, {'channel':method.channel,'merchant_entity':method.merchant_reference})
    configuration = {'threshold':quantity,'monthly_cap_minor':monthly_cap_minor,
        'product_id':str(product.pk),'unit_amount_minor':product.unit_amount_minor,
        'credits_quantity':product.credits_quantity,'currency':product.currency,
        'terms_version':terms_version,'consent_evidence_hash':consent_evidence_hash}
    if preset_key is not None:
        preset = _topup_preset(preset_key)
        if (str(product.pk) != preset['product_id'] or quantity != preset['threshold'] or
                monthly_cap_minor != preset['monthly_cap_minor'] or terms_version != preset['terms_version'] or
                _quantity(product.credits_quantity) <= _quantity(quantity)):
            raise ValueError('TOPUP_PRESET_MISMATCH')
        configuration.update(preset_key=preset_key,policy_version=preset['policy_version'])
    previous = TopupAuthorization.objects.filter(account=account,request_key=request_key).first()
    if previous:
        if (previous.version != expected_version+1 or previous.actor_id != user.pk or
            previous.payment_method_id != method.pk or previous.configuration != configuration or previous.mandate != mandate):
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return {'id':str(previous.pk),'version':previous.version,'charging_enabled':False}
    latest = TopupAuthorization.objects.filter(account=account).order_by('-version').first()
    if (latest.version if latest else 0) != expected_version:
        raise ValueError('AUTHORIZATION_VERSION_CHANGED')
    if CreditFreeze.objects.filter(batch__account=account,batch__source='purchased',state='frozen').exists():
        raise ValueError('REFUND_PENDING')
    now = timezone.now()
    if not active_subscription_periods(account,now).exists():
        raise ValueError('ACTIVE_SUBSCRIPTION_REQUIRED')
    if method.state != 'active' or product.kind != 'credits' or mandate['executor'] != 'sage':
        raise ValueError('INVALID_TOPUP_CONSENT')
    _sale_gate(product,account,now,method.channel,'CN',method.merchant_reference)
    if ('automatic_topup' not in settings.BILLING_APPROVED_CHANNELS[method.channel]['capabilities'] or
        monthly_cap_minor < product.unit_amount_minor):
        raise ValueError('CONFIG_INCOMPLETE')
    agreement = mandate['agreement_reference']
    if (Subscription.objects.filter(account=account,mandate__agreement_reference=agreement).exists() or
        AuthorizationChange.objects.filter(subscription__account=account).filter(
            Q(old_mandate__agreement_reference=agreement)|Q(new_mandate__agreement_reference=agreement)).exists() or
        TopupAuthorization.objects.filter(account=account,mandate__agreement_reference=agreement).exists()):
        raise ValueError('INDEPENDENT_AUTHORIZATION_REQUIRED')
    if latest and latest.state in ('authorized','paused'):
        latest.state = 'superseded'
        latest.save(update_fields=['state'])
    record = TopupAuthorization.objects.create(account=account,actor=user,payment_method=method,product=product,
        version=expected_version+1,request_key=request_key,configuration=configuration,mandate=mandate)
    AuditEvent.objects.create(actor=user,account=account,action='topup.authorization_recorded',request_id=record.pk)
    from .models import BillingNotification
    BillingNotification.objects.create(account=account,type='topup_authorization_recorded',
        source_event=f'topup:{record.pk}:authorization_recorded',occurred_at=record.created_at)
    return {'id':str(record.pk),'version':record.version,'charging_enabled':False}


def _topup_preset(key):
    from django.conf import settings
    from .billing import _text
    presets = getattr(settings,'BILLING_TOPUP_PRESETS',{})
    preset = presets.get(key) if isinstance(presets,dict) and isinstance(key,str) else None
    if (not isinstance(preset,dict) or preset.get('approved') is not True or
            set(preset) != {'approved','product_id','threshold','monthly_cap_minor','terms_version','policy_version'} or
            type(preset.get('monthly_cap_minor')) is not int or preset['monthly_cap_minor'] <= 0):
        raise ValueError('TOPUP_PRESET_REQUIRED')
    try:
        product_id = str(uuid.UUID(str(preset['product_id'])))
        threshold = _text(_quantity(preset['threshold'],positive=True))
        _identifier(preset['terms_version'])
        _identifier(preset['policy_version'])
    except (ValueError,TypeError,AttributeError):
        raise ValueError('TOPUP_PRESET_REQUIRED') from None
    return {**preset,'product_id':product_id,'threshold':threshold}


@transaction.atomic
def authorize_topup_preset(account, user, *, preset_key, payment_method_id, mandate,
                            consent_evidence_hash, expected_version, request_key):
    preset = _topup_preset(preset_key)
    return record_topup_authorization(account,user,payment_method_id=payment_method_id,
        product_id=preset['product_id'],threshold=preset['threshold'],monthly_cap_minor=preset['monthly_cap_minor'],
        mandate=mandate,terms_version=preset['terms_version'],consent_evidence_hash=consent_evidence_hash,
        expected_version=expected_version,request_key=request_key,preset_key=preset_key)


def topup_presets(account):
    from django.conf import settings
    from .models import ProductVersion
    if account.kind != 'business':
        raise ValueError('FORBIDDEN')
    result=[]
    for key in getattr(settings,'BILLING_TOPUP_PRESETS',{}):
        preset=_topup_preset(key)
        product=ProductVersion.objects.filter(pk=preset['product_id'],audience='business',kind='credits',state='published').first()
        if product and product.unit_amount_minor and _quantity(product.credits_quantity)>_quantity(preset['threshold']):
            result.append({'key':key,'threshold':preset['threshold'],'monthly_cap_minor':str(preset['monthly_cap_minor']),
                'pack_credits':product.credits_quantity,'pack_amount_minor':str(product.unit_amount_minor),'currency':product.currency})
    return result


def _topup_budget_state(row):
    """Read canonical payment facts; never maintain a second money ledger."""
    attempt, order = row.attempt, row.attempt.order
    if attempt.needs_reconciliation or order.payment_facts.exclude(disposition='accepted').exists():
        return 'review'
    return _effective_attempt_state(attempt,order)


@transaction.atomic
def topup_budget_summary(account, *, starts_at, ends_at, policy_version, include_overlapping=False):
    from .models import TopupBudget
    account = _lock_account(account)
    _datetime(starts_at)
    _datetime(ends_at)
    _identifier(policy_version)
    if ends_at <= starts_at:
        raise ValueError('INVALID_BUDGET_WINDOW')
    rows = TopupBudget.objects.filter(account=account)
    rows = (rows.filter(starts_at__lt=ends_at,ends_at__gt=starts_at) if include_overlapping else
            rows.filter(starts_at=starts_at,ends_at=ends_at,policy_version=policy_version))
    spent, reserved, review = 0, 0, []
    for row in rows.select_related('attempt__order'):
        state = _topup_budget_state(row)
        if state == 'succeeded':
            spent += row.attempt.order.amount_minor
        elif state not in ('failed','cancelled','expired'):
            reserved += row.attempt.order.amount_minor
            if state == 'review':
                review.append(str(row.attempt_id))
    return {'spent_minor':spent,'reserved_minor':reserved,'review_attempts':review}


@transaction.atomic
def automatic_topup_summary(account):
    """Read-only business projection; no agreements, evidence or payer identity."""
    from .models import TopupAuthorization, TopupBudget
    account = _lock_account(account)
    if account.kind != 'business':
        raise ValueError('FORBIDDEN')
    auth = TopupAuthorization.objects.filter(account=account).order_by('-version').first()
    result = {'account_id':str(account.pk),'charging_enabled':False,'authorization':None,'budget':None}
    if not auth:
        return result
    result['authorization'] = {'id':str(auth.pk),'state':auth.state,'version':auth.version,'pause_reason':auth.pause_reason,
        'threshold':auth.configuration['threshold'],'pack_credits':auth.configuration['credits_quantity'],
        'pack_amount_minor':str(auth.configuration['unit_amount_minor']),
        'monthly_cap_minor':str(auth.configuration['monthly_cap_minor']),'currency':'CNY'}
    now = timezone.now()
    window = TopupBudget.objects.filter(account=account,starts_at__lte=now,ends_at__gt=now).order_by('-starts_at').first()
    if window:
        totals = topup_budget_summary(account,starts_at=window.starts_at,ends_at=window.ends_at,policy_version=window.policy_version)
        result['budget'] = {'starts_at':window.starts_at.isoformat(),'ends_at':window.ends_at.isoformat(),
            'spent_minor':str(totals['spent_minor']),'reserved_minor':str(totals['reserved_minor']),
            'needs_review':bool(totals['review_attempts']),
            'remaining_minor':None if totals['review_attempts'] else str(max(0,
                auth.configuration['monthly_cap_minor']-totals['spent_minor']-totals['reserved_minor']))}
    return result


@transaction.atomic
def prepare_topup_attempt(account, authorization_id, *, trigger_key, starts_at, ends_at, policy_version, expires_at,
                          _preset_trigger=False, actor=None):
    """Trusted intent builder with explicit budget window, not a trigger policy.

    No threshold evaluation, automatic retry or debit is performed. D09/D15
    must be approved before any scheduler or dispatch can use these intents.
    """
    from django.conf import settings
    from .models import TopupAuthorization, TopupBudget, CreditFreeze
    account = _lock_account(account)
    _identifier(trigger_key)
    _identifier(policy_version)
    if len(policy_version) > 80:
        raise ValueError('INVALID_BUDGET_WINDOW')
    for value in (starts_at,ends_at,expires_at):
        _datetime(value)
    auth = _get(TopupAuthorization.objects.select_for_update(),account=account,pk=authorization_id)
    existing = TopupBudget.objects.filter(account=account,trigger_key=trigger_key).select_related('attempt__order').first()
    if existing:
        if (existing.authorization_id != auth.pk or existing.starts_at != starts_at or existing.ends_at != ends_at or
            existing.policy_version != policy_version or existing.attempt.expires_at != expires_at):
            raise ValueError('PAYMENT_INTENT_CONFLICT')
        return _attempt_dict(existing.attempt,existing.attempt.order)
    now = timezone.now()
    if not (starts_at <= now < ends_at and now < expires_at and (_preset_trigger or expires_at <= ends_at)):
        raise ValueError('INVALID_BUDGET_WINDOW')
    buyer = actor if _preset_trigger else _billing_operator(account,auth.actor)
    _authorize_purchase(account,buyer)
    if (auth.state != 'authorized' or auth.payment_method.state != 'active' or
        not active_subscription_periods(account,now).exists()):
        raise ValueError('TOPUP_NOT_AUTHORIZED')
    if CreditFreeze.objects.filter(batch__account=account,batch__source='purchased',state='frozen').exists():
        raise ValueError('REFUND_PENDING')
    rows = TopupBudget.objects.filter(account=account)
    if not _preset_trigger and rows.filter(starts_at__lt=ends_at,ends_at__gt=starts_at).exclude(
            starts_at=starts_at,ends_at=ends_at,policy_version=policy_version).exists():
        raise ValueError('BUDGET_WINDOW_CONFLICT')
    if any(_topup_budget_state(row) not in ('succeeded','failed','cancelled','expired')
           for row in rows.select_related('attempt__order')):
        raise ValueError('TOPUP_PENDING')
    totals = topup_budget_summary(account,starts_at=starts_at,ends_at=ends_at,policy_version=policy_version,
                                 include_overlapping=_preset_trigger)
    amount = auth.configuration['unit_amount_minor']
    if totals['spent_minor'] + totals['reserved_minor'] + amount > auth.configuration['monthly_cap_minor']:
        raise ValueError('TOPUP_BUDGET_EXCEEDED')
    channel = auth.mandate['channel']
    config = settings.BILLING_APPROVED_CHANNELS.get(channel,{})
    if 'automatic_topup' not in config.get('capabilities',[]):
        raise ValueError('CONFIG_INCOMPLETE')
    key = f'topup:{account.pk}:{trigger_key}'
    _identifier(key)
    quote = create_quote(account,buyer,auth.product_id,1,expires_at,channel=channel,buyer_region='CN')
    order_data = create_order(account,buyer,{'quote_id':quote['id'],
        'agreement_acceptance':auth.product.terms_version},key)
    order = _get(Order.objects.select_for_update(),account=account,pk=order_data['id'])
    if (order.amount_minor != amount or _quantity(order.snapshot['credits_quantity']) != _quantity(auth.configuration['credits_quantity']) or
        order.snapshot['merchant_entity'] != auth.mandate['merchant_reference']):
        raise ValueError('TOPUP_CONFIGURATION_CHANGED')
    attempt = PaymentAttempt.objects.create(account=account,order=order,intent_key=key,expires_at=expires_at)
    TopupBudget.objects.create(account=account,authorization=auth,attempt=attempt,trigger_key=trigger_key,
        starts_at=starts_at,ends_at=ends_at,policy_version=policy_version)
    return _attempt_dict(attempt,order)


def _has_shared_task_demand(account):
    """Require persisted actual use, not an empty task or a low balance alone."""
    from fractions import Fraction
    from .models import SharedTask, PaidSeat
    from .billing import _business_task_credit_scope
    now=timezone.now()
    for task in SharedTask.objects.filter(account=account,state__in=('running','paused')).select_related('membership__user'):
        if task.state == 'paused' and (task.resume_blocked or not task.membership.is_active or
                not task.membership.user.is_active or not PaidSeat.objects.filter(
                    membership_id=task.membership_id,subscription__account=account).filter(
                    Q(subscription__superseded_at__isnull=True)|Q(subscription__superseded_at__gt=now)).filter(
                    Q(subscription__refund_cutoff_at__isnull=True)|Q(subscription__refund_cutoff_at__gt=now)).exists()):
            continue
        scope = _business_task_credit_scope(account,task)
        if not scope['can_consume']:
            continue
        outstanding = sum((_quantity(value) for value in task.debits.filter(state='pending').values_list(
            'quantity',flat=True)),Fraction(0))
        if outstanding > scope['included_available']:
            return True
        if task.state == 'running' and scope['included_available'] == 0 and task.debits.filter(
                Q(entries__batch__source='purchased')|Q(entries__batch__source='subscription',
                    entries__batch__subscription_period__snapshot__entitlements__resource_mode='shared_included'),
                state='settled',entries__kind='settle',entries__batch__account=account,
                entries__batch__seat__isnull=True).exists():
            return True
    return False


def _close_unused_topup(budget, *, expired=False):
    """Close only a provably unsubmitted intent while holding its account lock."""
    attempt,order=budget.attempt,budget.attempt.order
    if (attempt.state!='created' or attempt.dispatched_at is not None or
            attempt.dispatch_authorization is not None or attempt.needs_reconciliation or
            order.status!='pending_payment' or order.paid_at is not None or
            order.payment_facts.exists() or attempt.events.exists()):
        return False
    attempt.state='expired' if expired else 'cancelled'
    attempt.save(update_fields=['state'])
    order.status='closed';order.save(update_fields=['status'])
    auth=budget.authorization
    if auth.state=='authorized' and not auth.trigger_armed:
        auth.trigger_armed=True;auth.save(update_fields=['trigger_armed'])
    AuditEvent.objects.create(account_id=budget.account_id,action='topup.intent_'+attempt.state,
                              request_id=attempt.pk)
    return True


@transaction.atomic
def evaluate_automatic_topup(account):
    """Trusted scheduler hook: at most one intent per downward threshold crossing.

    Never calls a channel. Unknown payments block a new intent, including after
    a plan change or removal of the person who originally gave consent.
    """
    from .models import TopupAuthorization, TopupBudget
    from .billing import _business_task_credit_scope
    from backend.models import Membership
    account=_lock_account(account)
    if account.kind!='business':
        raise ValueError('FORBIDDEN')
    now=timezone.now()
    unsubmitted=list(TopupBudget.objects.filter(account=account,attempt__state='created').select_related(
        'attempt__order','authorization'))
    closed=False
    for budget in unsubmitted:
        if budget.attempt.expires_at<=now or budget.ends_at<=now:
            closed=_close_unused_topup(budget,expired=True) or closed
    if closed:
        # Commit cleanup before a later tick validates configuration or builds
        # another intent; those failures must not resurrect expired local work.
        return {'triggered':False,'reason':'TOPUP_INTENT_EXPIRED'}
    auth=TopupAuthorization.objects.filter(account=account).order_by('-version').first()
    if not auth or auth.state!='authorized':
        return {'triggered':False,'reason':'TOPUP_NOT_AUTHORIZED'}
    preset=_topup_preset(auth.configuration.get('preset_key'))
    if any(auth.configuration.get(key)!=preset[key] for key in ('product_id','threshold','monthly_cap_minor','terms_version','policy_version')):
        raise ValueError('TOPUP_CONFIGURATION_CHANGED')
    available=_business_task_credit_scope(account)['shared_available']
    if available>=_quantity(preset['threshold']):
        for budget in unsubmitted:
            _close_unused_topup(budget)
        if not auth.trigger_armed:
            auth.trigger_armed=True;auth.save(update_fields=['trigger_armed'])
        return {'triggered':False,'reason':'ABOVE_THRESHOLD'}
    if not _has_shared_task_demand(account):
        for budget in unsubmitted:
            _close_unused_topup(budget)
        return {'triggered':False,'reason':'NO_SHARED_DEMAND'}
    if not auth.trigger_armed:
        return {'triggered':False,'reason':'WAIT_FOR_NEXT_CROSSING'}
    if any(_topup_budget_state(row) not in ('succeeded','failed','cancelled','expired')
           for row in TopupBudget.objects.filter(account=account).select_related('attempt__order')):
        return {'triggered':False,'reason':'TOPUP_PENDING'}
    now=timezone.now()
    period=active_subscription_periods(account,now).select_related('subscription').first()
    if not period:
        pause_topup_authorization(account,reason='subscription_expired')
        return {'triggered':False,'reason':'ACTIVE_SUBSCRIPTION_REQUIRED'}
    subscription=period.subscription
    local=now.astimezone(_calendar_zone(subscription.billing_timezone))
    anchor=subscription.original_anchor.astimezone(_calendar_zone(subscription.billing_timezone))
    index=(local.year-anchor.year)*12+local.month-anchor.month
    if _boundary(subscription,index)>now:index-=1
    start,end=_boundary(subscription,index),_boundary(subscription,index+1)
    member=Membership.objects.filter(account=account,is_active=True,user__is_active=True,
        role__in=('owner','billing_admin')).select_related('user').order_by('id').first()
    if not member:
        return {'triggered':False,'reason':'CURRENT_ADMIN_REQUIRED'}
    expiry=now+timedelta(minutes=10)
    try:
        attempt=prepare_topup_attempt(account,auth.pk,trigger_key=f'cross:{auth.pk}:{auth.trigger_sequence+1}',
            starts_at=start,ends_at=end,policy_version=preset['policy_version'],expires_at=expiry,
            _preset_trigger=True,actor=member.user)
    except ValueError as exc:
        if str(exc) in ('TOPUP_BUDGET_EXCEEDED','TOPUP_PENDING','REFUND_PENDING'):
            return {'triggered':False,'reason':str(exc)}
        raise
    auth.trigger_armed=False;auth.trigger_sequence+=1
    auth.save(update_fields=['trigger_armed','trigger_sequence'])
    return {'triggered':True,'attempt':attempt}


def claim_automatic_topup(account, attempt_id):
    """Trusted worker claim only; commit once before any external I/O.

    Legacy consents without a currently approved fixed preset stay closed.
    No channel call is made, and already claimed/unknown work is query-only.
    """
    from .models import TopupBudget, TopupAuthorization, CreditFreeze
    from .billing import _business_task_credit_scope
    if transaction.get_connection().in_atomic_block:raise ValueError('DISPATCH_REQUIRES_AUTOCOMMIT')
    with transaction.atomic():
        account=_lock_account(account)
        budget=_get(TopupBudget.objects.select_related('authorization','attempt__order__quote__product'),
                    account=account,attempt_id=attempt_id)
        auth=budget.authorization;attempt=budget.attempt;order=attempt.order
        result={**_attempt_dict(attempt,order),'dispatch':False,'request_reference':str(attempt.pk)}
        if attempt.dispatched_at or attempt.state!='created' or result['status']!='created':return result
        if (attempt.dispatch_authorization is not None or attempt.needs_reconciliation or
                order.status!='pending_payment' or order.paid_at is not None or
                order.payment_facts.exists() or attempt.events.exists()):
            return {**result,'reason':'TOPUP_RECONCILIATION_REQUIRED'}
        now=timezone.now()
        if attempt.expires_at<=now or budget.ends_at<=now:
            _close_unused_topup(budget,expired=True)
            return {**result,'status':attempt.state,'reason':'PAYMENT_EXPIRED'}
        if not _has_shared_task_demand(account):
            _close_unused_topup(budget)
            return {**result,'status':attempt.state,'reason':'TOPUP_NOT_NEEDED'}
        preset=_topup_preset(auth.configuration.get('preset_key'))
        if any(auth.configuration.get(key)!=preset[key] for key in ('product_id','threshold','monthly_cap_minor','terms_version','policy_version')):
            raise ValueError('TOPUP_CONFIGURATION_CHANGED')
        if auth.state!='authorized' or auth.payment_method.state!='active':raise ValueError('TOPUP_NOT_AUTHORIZED')
        _billing_operator(account,auth.actor)
        if not active_subscription_periods(account).exists():raise ValueError('ACTIVE_SUBSCRIPTION_REQUIRED')
        if CreditFreeze.objects.filter(batch__account=account,batch__source='purchased',state='frozen').exists():raise ValueError('REFUND_PENDING')
        if _business_task_credit_scope(account)['shared_available']>=_quantity(preset['threshold']):
            _close_unused_topup(budget)
            return {**result,'status':attempt.state,'reason':'TOPUP_NOT_NEEDED'}
        now=timezone.now()
        if not budget.starts_at<=now<budget.ends_at:raise ValueError('INVALID_BUDGET_WINDOW')
        totals=topup_budget_summary(account,starts_at=budget.starts_at,ends_at=budget.ends_at,
            policy_version=budget.policy_version,include_overlapping=True)
        if totals['spent_minor']+totals['reserved_minor']>preset['monthly_cap_minor']:raise ValueError('TOPUP_BUDGET_EXCEEDED')
        if any(_topup_budget_state(row) not in ('succeeded','failed','cancelled','expired')
               for row in TopupBudget.objects.filter(account=account).exclude(pk=budget.pk).select_related('attempt__order')):
            raise ValueError('TOPUP_PENDING')
        _sale_gate(order.quote.product,account,now,auth.mandate['channel'],order.snapshot['buyer_region'],auth.mandate['merchant_reference'])
        from django.conf import settings
        if ('automatic_topup' not in settings.BILLING_APPROVED_CHANNELS.get(auth.mandate['channel'],{}).get('capabilities',[]) or
                auth.mandate['executor']!='sage' or order.amount_minor!=auth.configuration['unit_amount_minor'] or
                order.snapshot!=order.quote.snapshot or order.payment_facts.exists() or attempt.events.exists()):
            raise ValueError('TOPUP_CONFIGURATION_CHANGED')
        attempt.state,attempt.dispatched_at='pending',now
        attempt.dispatch_authorization={'topup_authorization_id':str(auth.pk),'version':auth.version,
            'payment_method_id':str(auth.payment_method_id),'mandate':dict(auth.mandate)}
        attempt.save(update_fields=['state','dispatched_at','dispatch_authorization'])
        return {**result,'status':'pending','dispatch':True,'channel':auth.mandate['channel']}


@transaction.atomic
def pause_topup_authorization(account, *, reason):
    """Trusted lifecycle hook. Resuming requires new explicit consent, never a timer."""
    from .models import TopupAuthorization
    account = _lock_account(account)
    if reason not in ('subscription_expired','credits_refund','user_requested','method_removal'):
        raise ValueError('INVALID_PAUSE_REASON')
    record = TopupAuthorization.objects.filter(account=account).order_by('-version').first()
    if not record or record.state != 'authorized':
        return
    record.state, record.pause_reason = 'paused', reason
    record.save(update_fields=['state','pause_reason'])
    AuditEvent.objects.create(account=account,action='topup.authorization_paused',request_id=record.pk)
    from .models import BillingNotification
    BillingNotification.objects.create(account=account,type='topup_authorization_paused',
        source_event=f'topup:{record.pk}:authorization_paused',occurred_at=timezone.now())


@transaction.atomic
def request_topup_revocation(account, user, authorization_id):
    """Stop local use now; only a verified provider result completes revocation."""
    from .models import TopupAuthorization
    account = _lock_account(account)
    _authorize_purchase(account,user)
    record = _get(TopupAuthorization.objects.select_for_update(),account=account,pk=authorization_id)
    if record.state in ('revoking','revoked'):
        return
    record.state, record.revocation_requested_at = 'revoking', timezone.now()
    record.save(update_fields=['state','revocation_requested_at'])
    AuditEvent.objects.create(actor=user,account=account,action='topup.revocation_requested',request_id=record.pk)
    from .models import BillingNotification
    BillingNotification.objects.create(account=account,type='topup_revocation_processing',
        source_event=f'topup:{record.pk}:revocation_processing',occurred_at=record.revocation_requested_at)


@transaction.atomic
def confirm_topup_revocation(account, authorization_id, *, agreement_reference, evidence_hash):
    """Trusted verified channel result; neither initiates revocation nor refunds."""
    from .models import TopupAuthorization
    account = _lock_account(account)
    record = _get(TopupAuthorization.objects.select_for_update(),account=account,pk=authorization_id)
    if (record.mandate['agreement_reference'] != agreement_reference or not isinstance(evidence_hash,str) or
        not re.fullmatch(r'[a-f0-9]{64}',evidence_hash)):
        raise ValueError('INVALID_REVOCATION_EVIDENCE')
    if record.state == 'revoked':
        if record.revocation_evidence_hash != evidence_hash:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return
    if record.state != 'revoking':
        raise ValueError('REVOCATION_NOT_REQUESTED')
    record.state, record.revoked_at, record.revocation_evidence_hash = 'revoked',timezone.now(),evidence_hash
    record.save(update_fields=['state','revoked_at','revocation_evidence_hash'])
    AuditEvent.objects.create(account=account,action='topup.authorization_revoked',request_id=record.pk)
    from .models import BillingNotification
    BillingNotification.objects.create(account=account,type='topup_revocation_confirmed',
        source_event=f'topup:{record.pk}:revocation_confirmed',occurred_at=record.revoked_at)


@transaction.atomic
def register_verified_payment_method(account, *, channel, merchant_reference, fingerprint, evidence_hash):
    """Trusted verifier supplies a non-chargeable instrument fingerprint, not a token.

    This does not verify a PSP response. No browser route calls this function.
    Re-registration never reactivates a removed instrument.
    """
    account = _lock_account(account)
    _identifier(channel)
    _identifier(merchant_reference)
    if any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value)
           for value in (fingerprint, evidence_hash)):
        raise ValueError('INVALID_METHOD_EVIDENCE')
    method, created = PaymentMethod.objects.get_or_create(account=account, channel=channel,
        merchant_reference=merchant_reference, fingerprint=fingerprint, defaults={'evidence_hash': evidence_hash})
    if not created and method.evidence_hash != evidence_hash:
        raise ValueError('IDEMPOTENCY_CONFLICT')
    return str(method.pk)


def _method_impact(method):
    """Under the account lock; include retired authorizations and frozen dispatches."""
    from .models import TopupAuthorization
    subscriptions = list(Subscription.objects.filter(account=method.account, payment_method=method).order_by('id'))
    ids = [sub.pk for sub in subscriptions]
    attempts = PaymentAttempt.objects.filter(account=method.account).filter(
        Q(subscription_id__in=ids) | Q(order_id__in=[sub.initial_order_id for sub in subscriptions]) |
        Q(recovery_of__subscription_id__in=ids) |
        Q(topup_budget__authorization__payment_method=method) |
        Q(dispatch_authorization__payment_method_id=str(method.pk))).select_related('order').order_by('id')
    pending = []
    for attempt in attempts:
        state = _effective_attempt_state(attempt, attempt.order)
        # Accepted money still delivers, but does not resolve additional receipts.
        # Inspect the facts themselves: legacy/direct receipt ingestion may not
        # have a corresponding attempt event or reconciliation flag.
        receipt_review = attempt.order.payment_facts.exclude(disposition='accepted').exists()
        if receipt_review:
            state = 'review'
        if receipt_review or attempt.needs_reconciliation or (attempt.dispatched_at and state not in ('succeeded', 'failed', 'cancelled', 'expired')):
            pending.append({'id': str(attempt.pk), 'status': state})
    data = {'id': str(method.pk), 'state': method.state,
        'subscriptions': [{'id': str(sub.pk), 'cancellation_state': sub.cancellation_state} for sub in subscriptions],
        'unresolved_payments': pending,
        'topup_authorizations': [{'id':str(record.pk),'state':record.state,'version':record.version,
            'created_at':record.created_at.isoformat(),
            'revocation_requested_at':record.revocation_requested_at.isoformat() if record.revocation_requested_at else None,
            'revoked_at':record.revoked_at.isoformat() if record.revoked_at else None}
            for record in TopupAuthorization.objects.filter(account=method.account,payment_method=method).order_by('version')],
        'retired_authorizations': [{'id': str(change.pk), 'subscription_id': str(change.subscription_id),
            'state': 'completed' if change.revoked_at else 'processing'}
            for change in AuthorizationChange.objects.filter(subscription__account=method.account,
                old_method=method).order_by('id')],
        # Authorization records are readable; automatic charging remains disabled.
        'automatic_topup_supported': False}
    data['confirmation_hash'] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return data


@transaction.atomic
def payment_method_impact(account, user, method_id):
    account = _lock_account(account)
    _authorize_purchase(account, user)
    method = _get(PaymentMethod.objects.select_for_update(), account=account, pk=method_id)
    return _method_impact(method)


@transaction.atomic
def payment_methods_summary(account, *, offset=0):
    """Read-only HTTP projection; caller enforces financial membership."""
    account = _lock_account(account)
    if type(offset) is not int or offset < 0:
        raise ValueError('INVALID_REQUEST')
    methods = PaymentMethod.objects.filter(account=account).order_by('-created_at', 'id')
    total = methods.count()
    items = []
    for method in methods[offset:offset+50]:
        impact = _method_impact(method)
        items.append({'id': str(method.pk), 'channel': method.channel, 'state': method.state,
            'subscriptions': impact['subscriptions'], 'unresolved_payments': impact['unresolved_payments'],
            'retired_authorizations': impact['retired_authorizations'],
            'topup_authorizations': impact['topup_authorizations'],
            'removal_requested_at': method.removal_requested_at.isoformat() if method.removal_requested_at else None,
            'removed_at': method.removed_at.isoformat() if method.removed_at else None})
    return {'account_id': str(account.pk), 'items': items, 'total': total, 'offset': offset,
        'next_offset': offset+50 if offset+50 < total else None, 'can_edit': False,
        'has_unbound_authorizations': Subscription.objects.filter(account=account, payment_method__isnull=True)
            .exclude(cancellation_state='completed').exists(), 'automatic_topup_supported': False}


@transaction.atomic
def request_payment_method_removal(account, user, method_id, *, expected_impact):
    """Internal only until the confirmed high-risk authentication boundary exists."""
    account = _lock_account(account)
    _authorize_purchase(account, user)
    method = _get(PaymentMethod.objects.select_for_update(), account=account, pk=method_id)
    impact = _method_impact(method)
    if method.state != 'active':
        return impact
    if expected_impact != impact['confirmation_hash']:
        raise ValueError('PAYMENT_METHOD_IMPACT_CHANGED')
    method.state = 'removing'
    method.removal_requested_at = timezone.now()
    method.save(update_fields=['state', 'removal_requested_at'])
    for sub in impact['subscriptions']:
        cancel_subscription(account, user, sub['id'])
    from .models import TopupAuthorization
    for record in TopupAuthorization.objects.filter(account=account,payment_method=method).exclude(state='revoked'):
        request_topup_revocation(account,user,record.pk)
    AuditEvent.objects.create(actor=user, account=account, action='payment_method.remove_requested', request_id=method.pk)
    return _method_impact(method)


@transaction.atomic
def finish_payment_method_removal(account, method_id, *, evidence_hash):
    """Trusted channel worker only, after instrument invalidation is confirmed.

    Revocation failures/unknown results do not call this function. A digest is
    retained evidence, never a substitute for provider verification.
    """
    account = _lock_account(account)
    method = _get(PaymentMethod.objects.select_for_update(), account=account, pk=method_id)
    if not isinstance(evidence_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', evidence_hash):
        raise ValueError('INVALID_METHOD_EVIDENCE')
    impact = _method_impact(method)
    if method.state == 'removed':
        if method.removal_evidence_hash != evidence_hash:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return impact
    if (method.state != 'removing' or impact['unresolved_payments'] or
        any(record['state'] != 'revoked' for record in impact['topup_authorizations']) or
        any(change['state'] != 'completed' for change in impact['retired_authorizations']) or
        any(sub['cancellation_state'] != 'completed' for sub in impact['subscriptions'])):
        raise ValueError('REMOVAL_PENDING')
    method.state, method.removed_at, method.removal_evidence_hash = 'removed', timezone.now(), evidence_hash
    method.save(update_fields=['state', 'removed_at', 'removal_evidence_hash'])
    AuditEvent.objects.create(account=account, action='payment_method.removed', request_id=method.pk)
    return _method_impact(method)


def _seat_usage_state(seat, now):
    """Derive a seat's handoff guard from its ledger, never its shortened expiry."""
    from .models import Reservation
    pending = CreditBatch.objects.filter(seat=seat,entries__reservation__state='reserved').exists()
    if seat.membership_id:
        pending = pending or Reservation.objects.filter(account=seat.subscription.account,
            membership_id=seat.membership_id,state='reserved').exists()
    used, locked_until = [], None
    batches = CreditBatch.objects.filter(seat=seat,source='subscription',
        subscription_period__starts_at__lte=now,subscription_period__ends_at__gt=now).select_related('subscription_period')
    for batch in batches:
        period = batch.subscription_period
        if batch.service_month is None or period.subscription_id != seat.subscription_id:
            raise ValueError('LEDGER_INCONSISTENT')
        offset = period.cycle_index*_months(seat.subscription)+batch.service_month
        start,end = _boundary(seat.subscription,offset),_boundary(seat.subscription,offset+1)
        if start <= now < end and any(_quantity(value,positive=True) for value in
                batch.entries.filter(kind='settle').values_list('quantity',flat=True)):
            used.append(batch)
            locked_until = max(locked_until,end) if locked_until else end
    return pending, used, locked_until


def _invitation_status(invitation, now):
    if invitation.state != 'pending':
        return invitation.state
    if invitation.expires_at <= now:
        return 'expired'
    creator = invitation.creator
    if (not creator.is_active or not creator.user.is_active or
            creator.role not in ('owner', 'billing_admin') or
            (invitation.role == 'billing_admin' and creator.role != 'owner') or
            not active_subscription_periods(invitation.account, now).filter(
                subscription_id=invitation.subscription_id).exists()):
        return 'invalidated'
    if invitation.seat_id:
        period = active_subscription_periods(invitation.account, now).filter(subscription_id=invitation.subscription_id).first()
        if invitation.seat.membership_id is not None or not period or not _period_seats(period, now).filter(pk=invitation.seat_id).exists():
            return 'invalidated'
    return 'pending'


def _pending_invitations(account, now):
    from .models import BusinessInvitation
    return [row for row in BusinessInvitation.objects.filter(account=account, state='pending',
        expires_at__gt=now).select_related('creator__user', 'seat', 'subscription', 'account')
        if _invitation_status(row, now) == 'pending']


def _invitation_dict(row, *, recipient=None, manager=False, now=None):
    status = _invitation_status(row, now or timezone.now())
    if row.method == 'email':
        local, domain = row.identifier.rsplit('@', 1)
        masked = local[:1] + '***@' + domain
    else:
        masked = row.identifier[:3] + '****' + row.identifier[-4:]
    return {'id':str(row.pk), 'account_id':str(row.account_id), 'account_name':row.account.name,
        'method':row.method, 'masked_identifier':masked, 'role':row.role, 'paid':bool(row.seat_id),
        'seat_position':row.seat.position if row.seat_id else None, 'status':status,
        'created_at':row.created_at.astimezone(ZoneInfo('UTC')).isoformat(),
        'expires_at':row.expires_at.astimezone(ZoneInfo('UTC')).isoformat(),
        'accepted_at':row.accepted_at.astimezone(ZoneInfo('UTC')).isoformat() if row.accepted_at else None,
        'can_accept':bool(recipient and status == 'pending'),
        'can_revoke':bool(manager and status == 'pending'), 'delivery':'not_sent'}


def business_invitations(account=None, *, actor=None, recipient=None, offset=0):
    """Read-only even when expiry or an administrator's loss of access is observed."""
    from backend.models import LoginIdentity
    from .models import BusinessInvitation
    if type(offset) is not int or not 0 <= offset <= 999999999:
        raise ValueError('INVALID_REQUEST')
    rows = BusinessInvitation.objects.select_related('account', 'creator__user', 'seat', 'subscription')
    if recipient is not None:
        if not recipient.is_active:
            raise ValueError('FORBIDDEN')
        match = Q(pk__in=[])
        for identity in LoginIdentity.objects.filter(user=recipient):
            if identity.method in ('email', 'phone'):
                match |= Q(method=identity.method, identifier=identity.identifier)
            elif identity.method == 'google' and identity.provider_email:
                match |= Q(method='email', identifier=identity.provider_email)
        rows = rows.filter(match)
    else:
        _authorize_purchase(account, actor)
        if account.kind != 'business':
            raise ValueError('FORBIDDEN')
        rows = rows.filter(account=account)
    total = rows.count()
    now = timezone.now()
    return {'account_id':str(account.pk) if account else None, 'user_id':recipient.pk if recipient else None,
        'items':[_invitation_dict(row, recipient=recipient, manager=recipient is None, now=now)
            for row in rows.order_by('-created_at', '-pk')[offset:offset+50]],
        'total':total, 'offset':offset, 'next_offset':offset+50 if offset+50 < total else None}


@transaction.atomic
def create_business_invitation(account, actor, *, method, identifier, role, paid, request_key):
    from backend.models import LoginIdentity, Membership
    from backend.views import normalized_identifier
    from .models import BusinessInvitation, PaidSeat
    account = _lock_account(account)
    _authorize_purchase(account, actor)
    if account.kind != 'business':
        raise ValueError('FORBIDDEN')
    creator = _get(Membership.objects, account=account, user=actor, is_active=True, user__is_active=True)
    if role not in ('member', 'billing_admin') or type(paid) is not bool or method not in ('email', 'phone'):
        raise ValueError('INVALID_REQUEST')
    if role == 'billing_admin' and creator.role != 'owner':
        raise ValueError('FORBIDDEN')
    identifier = normalized_identifier(method, identifier)
    if not isinstance(request_key, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,64}', request_key):
        raise ValueError('INVALID_IDEMPOTENCY_KEY')
    existing = BusinessInvitation.objects.filter(account=account, request_key=request_key).first()
    if existing:
        if (existing.creator_id != creator.pk or existing.method != method or existing.identifier != identifier or
                existing.role != role or bool(existing.seat_id) != paid):
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return _invitation_dict(existing, manager=True)
    now = timezone.now()
    period = active_subscription_periods(account, now).select_related('subscription').first()
    if not period:
        raise ValueError('SUBSCRIPTION_EXPIRED')
    identity_match = Q(method=method, identifier=identifier)
    if method == 'email':
        identity_match |= Q(method='google', provider_email=identifier)
    if Membership.objects.filter(account=account, is_active=True, user_id__in=LoginIdentity.objects.filter(
            identity_match).values('user_id')).exists():
        raise ValueError('MEMBER_ALREADY_EXISTS')
    pending = _pending_invitations(account, now)
    if any(row.method == method and row.identifier == identifier for row in pending):
        raise ValueError('INVITATION_ALREADY_PENDING')
    seat = None
    if paid:
        reserved = {row.seat_id for row in pending if row.seat_id}
        for candidate in _period_seats(period, now).select_for_update(of=('self',)).filter(membership__isnull=True):
            waiting, _, locked = _seat_usage_state(candidate, now)
            if candidate.pk not in reserved and not waiting and not locked:
                seat = candidate
                break
        if seat is None:
            raise ValueError('NO_AVAILABLE_SEAT')
    row = BusinessInvitation.objects.create(account=account, creator=creator, subscription=period.subscription,
        seat=seat, method=method, identifier=identifier, role=role, request_key=request_key,
        expires_at=now+timedelta(hours=24))
    AuditEvent.objects.create(account=account, actor=actor, action='business_invitation.created', request_id=row.pk)
    return _invitation_dict(row, manager=True)


@transaction.atomic
def revoke_business_invitation(account, actor, invitation_id):
    from .models import BusinessInvitation
    account = _lock_account(account)
    _authorize_purchase(account, actor)
    row = _get(BusinessInvitation.objects.select_for_update(), account=account, pk=invitation_id)
    if row.state == 'revoked':
        return _invitation_dict(row, manager=True)
    if row.state != 'pending':
        raise ValueError('INVITATION_UNAVAILABLE')
    row.state = 'revoked'
    row.save(update_fields=['state'])
    AuditEvent.objects.create(account=account, actor=actor, action='business_invitation.revoked', request_id=row.pk)
    return _invitation_dict(row, manager=True)


@transaction.atomic
def accept_business_invitation(user, invitation_id):
    from django.contrib.auth import get_user_model
    from backend.models import LoginIdentity, Membership
    from .models import BusinessInvitation
    hint = _get(BusinessInvitation.objects.select_related('account'), pk=invitation_id)
    account = _lock_account(hint.account)
    user = _get(get_user_model().objects.select_for_update(), pk=user.pk, is_active=True)
    row = _get(BusinessInvitation.objects.select_for_update(of=('self',)).select_related('creator__user', 'seat'),
        pk=invitation_id, account=account)
    identity_match = Q(method=row.method, identifier=row.identifier)
    if row.method == 'email':
        identity_match |= Q(method='google', provider_email=row.identifier)
    if not LoginIdentity.objects.filter(identity_match, user=user).exists():
        raise ValueError('FORBIDDEN')
    if row.state == 'accepted':
        member = row.accepted_membership
        if not member or member.user_id != user.pk or not member.is_active:
            raise ValueError('INVITATION_UNAVAILABLE')
        return {'invitation':_invitation_dict(row, recipient=user), 'membership_id':member.pk, 'account_id':str(account.pk)}
    if _invitation_status(row, timezone.now()) != 'pending':
        raise ValueError('INVITATION_UNAVAILABLE')
    member = Membership.objects.select_for_update().filter(account=account, user=user).first()
    if member and member.is_active:
        raise ValueError('MEMBER_ALREADY_EXISTS')
    if member:
        from .models import PaidSeat
        # Offboarding may retain a seat until an in-flight settlement finishes.
        # Reactivation, including a free role, must not restore that old access.
        if PaidSeat.objects.filter(subscription__account=account, membership=member).exists():
            raise ValueError('TASK_SETTLEMENT_REQUIRED')
        member.is_active, member.role = True, row.role
        member.save(update_fields=['is_active', 'role'])
    else:
        member = Membership.objects.create(account=account, user=user, role=row.role)
    row.state, row.accepted_membership, row.accepted_at = 'accepted', member, timezone.now()
    row.save(update_fields=['state', 'accepted_membership', 'accepted_at'])
    if row.seat_id:
        assign_seat(account, row.creator.user, row.seat_id, member.pk,
            expected_version=row.seat.version, request_key=f'invitation:{row.pk}')
    AuditEvent.objects.create(account=account, actor=user, action='business_invitation.accepted', request_id=row.pk)
    return {'invitation':_invitation_dict(row, recipient=user), 'membership_id':member.pk, 'account_id':str(account.pk)}


@transaction.atomic
def organization_summary(account, *, offset=0, viewer=None, admin_actor=None):
    """Admin roster or authenticated viewer's own limits; never returns funds."""
    from fractions import Fraction
    from backend.models import Membership, MemberRemoval
    from .models import PaidSeat, CreditEntry
    from .billing import _text
    account = _lock_account(account)
    if account.kind != 'business':
        raise ValueError('FORBIDDEN')
    can_remove = False
    if admin_actor is not None:
        _authorize_purchase(account,admin_actor)
        can_remove = Membership.objects.filter(account=account,user=admin_actor,role='owner',is_active=True).exists()
    if type(offset) is not int or offset < 0:
        raise ValueError('INVALID_REQUEST')
    if viewer is not None and offset:
        raise ValueError('INVALID_REQUEST')
    now = timezone.now()
    reserved_seats = {row.seat_id for row in _pending_invitations(account, now) if row.seat_id}
    period = active_subscription_periods(account,now).select_related('subscription').first()
    subscription = period.subscription if period else Subscription.objects.filter(account=account).order_by('-created_at').first()
    start = end = None
    if period:
        local = now.astimezone(_calendar_zone(subscription.billing_timezone))
        anchor = subscription.original_anchor.astimezone(_calendar_zone(subscription.billing_timezone))
        index = (local.year-anchor.year)*12 + local.month-anchor.month
        if _boundary(subscription,index) > now:
            index -= 1
        start,end = _boundary(subscription,index),_boundary(subscription,index+1)
    shown_period = period or (subscription.periods.filter(starts_at__lte=now).order_by('-starts_at').first() if subscription else None)
    seats = _period_seats(shown_period, now) if shown_period else PaidSeat.objects.none()
    next_period = subscription.periods.filter(starts_at__gt=now).order_by('starts_at').first() if subscription else None
    seat_states = {seat.pk:_seat_usage_state(seat,now) for seat in seats.select_related('subscription')}
    members = Membership.objects.filter(account=account).select_related('user').order_by('id')
    if viewer is not None:
        members = members.filter(user=viewer,is_active=True)
        if not viewer.is_active or not members.exists():
            raise ValueError('FORBIDDEN')
    total = members.count()
    items = []
    page_members = list(members[offset:offset+50])
    removals = {row.membership_id:row for row in MemberRemoval.objects.filter(account=account,
        membership_id__in=[member.pk for member in page_members]).order_by('created_at', 'pk')}
    for member in page_members:
        seat = seats.filter(membership=member).first()
        pending,_,locked_until = seat_states[seat.pk] if seat else (False,[],None)
        paid_user = bool(seat and member.is_active and member.user.is_active and
            member.role in ('owner','billing_admin','member') and
            (subscription.superseded_at is None or subscription.superseded_at > now) and
            (subscription.refund_cutoff_at is None or subscription.refund_cutoff_at > now))
        administrator = member.role in ('owner','billing_admin')
        limit = member.shared_limit_changes.order_by('-version').first()
        used,reserved = Fraction(0),Fraction(0)
        if start:
            entries = CreditEntry.objects.filter(batch__account=account,batch__seat__isnull=True,
                reservation__membership=member).filter(
                Q(batch__source='purchased')|Q(batch__source='subscription',
                    batch__subscription_period__snapshot__entitlements__resource_mode='shared_included')).filter(
                Q(reservation__shared_task__isnull=True,reservation__shared_period_start=start)|
                Q(reservation__shared_task__isnull=False,created_at__gte=start,created_at__lt=end)
            ).select_related('reservation')
            for entry in entries:
                if entry.kind == 'settle':
                    used += _quantity(entry.quantity)
                elif entry.kind == 'reserve' and entry.reservation.state == 'reserved':
                    reserved += _quantity(entry.quantity)
        total_credits = sum((_quantity(value) for value in member.reservation_set.filter(
            account=account,shared_task__isnull=False,state='settled').values_list('settled_quantity',flat=True)),Fraction(0))
        items.append({'id':member.pk,'user_id':member.user_id,'role':member.role,'active':member.is_active,
            'role_version':member.role_changes.count(),
            'seat_id':str(seat.pk) if seat else None, 'seat_version':seat.version if seat else None,
            'can_change_role':bool(can_remove and member.is_active and member.role != 'owner'),
            'can_assign_seat':bool(admin_actor and period and member.is_active and member.user.is_active and not seat),
            'can_release_seat':bool(admin_actor and seat and not pending),
            'usage_role':('admin' if paid_user else 'config_admin') if administrator else
                ('member' if paid_user else 'directory_member'),
            # Permission only: natural expiry still permits unexpired purchased
            # Credits, while the actual task must separately check its balance.
            'can_use_credits':paid_user,
            'seat_locked_until':locked_until.isoformat() if locked_until else None,
            'seat_release_pending':bool(pending and not member.is_active),
            'total_credits':_text(total_credits),
            'seat_position':seat.position if seat else None,'shared_limit':limit.quantity if limit else None,
            'shared_used':_text(used) if start else None,'shared_reserved':_text(reserved) if start else None,
            'can_remove':bool(can_remove and member.is_active and member.role != 'owner' and member.user_id != admin_actor.pk),
            'removed_at':removals[member.pk].created_at.isoformat() if member.pk in removals else None,
            'handoff_pending':removals[member.pk].handoff_pending if member.pk in removals else False})
    if viewer is not None:
        return {'account_id':str(account.pk),'member':items[0],
            'subscription_active':bool(period),'period_start':start.isoformat() if start else None,
            'period_end':end.isoformat() if end else None,'can_edit':False}
    return {'account_id':str(account.pk),'members':items,'total_members':total,'offset':offset,
        'next_paid_period':{'id':str(next_period.pk), 'starts_at':next_period.starts_at.isoformat(),
            'ends_at':next_period.ends_at.isoformat(), 'seat_count':_period_seats(next_period).count(),
            'uncovered_seat_items':[{'id':str(seat.pk), 'position':seat.position, 'membership_id':seat.membership_id}
                for seat in seats.exclude(pk__in=_period_seats(next_period).values('pk'))]} if next_period else None,
        'available_seat_items':[{'id':str(seat.pk), 'position':seat.position, 'version':seat.version}
            for seat in seats.filter(membership__isnull=True) if period and seat.pk not in reserved_seats
            and not seat_states[seat.pk][0] and not seat_states[seat.pk][2]],
        'next_offset':offset+50 if offset+50<total else None,'seat_count':seats.count(),
        'reserved_seats':len(reserved_seats),
        'available_seats':sum(bool(period) and seat.pk not in reserved_seats and not seat_states[seat.pk][0] and not seat_states[seat.pk][2]
            for seat in seats.filter(membership__isnull=True)),
        'locked_seats':sum(bool(seat_states[seat.pk][2]) for seat in seats.filter(membership__isnull=True)),
        'pending_settlement_seats':sum(state[0] for state in seat_states.values()),
        'assigned_seats':seats.filter(membership__isnull=False).count(),'subscription_active':bool(period),
        'subscription_id':str(subscription.pk) if subscription else None,
        'period_start':start.isoformat() if start else None,'period_end':end.isoformat() if end else None,
        'can_edit':False,'capabilities':{'member_removal':can_remove,
            'member_role_management':can_remove, 'seat_assignment':bool(admin_actor),
            'seat_reduction':bool(admin_actor and period and settings.BILLING_SEAT_PURCHASE_ENABLED),
            'next_seat_purchase':bool(admin_actor and period and next_period and settings.BILLING_SEAT_PURCHASE_ENABLED),
            'invitation_management':bool(admin_actor and period),
            'seat_purchase':bool(admin_actor and period and settings.BILLING_SEAT_PURCHASE_ENABLED),
            'invitation_roles':['member', 'billing_admin'] if can_remove else ['member']},'executor_connected':False}


@transaction.atomic
def change_member_role(account, actor, membership_id, role, *, expected_version, request_key):
    from backend.models import Membership
    from .models import MembershipRoleChange, BillingNotification
    account = _lock_account(account)
    _authorize_purchase(account, actor)
    if account.kind != 'business' or not Membership.objects.filter(
            account=account, user=actor, is_active=True, role='owner').exists():
        raise ValueError('FORBIDDEN')
    if (type(membership_id) is not int or membership_id < 1 or role not in ('member', 'billing_admin') or
            type(expected_version) is not int or not 0 <= expected_version < 2147483647):
        raise ValueError('INVALID_REQUEST')
    _identifier(request_key)
    if len(request_key) > 64:
        raise ValueError('INVALID_IDEMPOTENCY_KEY')
    member = _get(Membership.objects.select_for_update(), account=account, pk=membership_id,
        is_active=True, user__is_active=True)
    if member.role == 'owner':
        raise ValueError('OWNER_ROLE_PROTECTED')
    previous = MembershipRoleChange.objects.filter(account=account, request_key=request_key).first()
    if previous:
        if (previous.actor_id, previous.membership_id, previous.after_role, previous.expected_version) != (
                actor.pk, membership_id, role, expected_version):
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return {'account_id':str(account.pk), 'membership_id':membership_id,
            'role':previous.after_role, 'version':previous.resulting_version}
    if member.role_changes.count() != expected_version:
        raise ValueError('MEMBER_ROLE_CHANGED')
    before, member.role = member.role, role
    member.save(update_fields=['role'])
    change = MembershipRoleChange.objects.create(account=account, membership=member, actor=actor,
        before_role=before, after_role=role, request_key=request_key,
        expected_version=expected_version, resulting_version=expected_version+1)
    AuditEvent.objects.create(actor=actor, account=account, action='membership.role_changed', request_id=uuid.uuid4())
    BillingNotification.objects.create(account=account, type='member_role_changed',
        source_event=f'member-role:{change.pk}', occurred_at=change.created_at)
    return {'account_id':str(account.pk), 'membership_id':membership_id, 'role':role, 'version':expected_version+1}


def _member_removal_dict(record):
    return {'removal_id':str(record.pk),'membership_id':record.membership_id,'removed':True,
        'handoff_pending':record.handoff_pending,'executor_connected':False}


@transaction.atomic
def remove_member(account, actor, membership_id, *, request_key, request_id=None):
    """Revoke access immediately; release only seats without unsettled holds."""
    from backend.models import Membership, MemberRemoval
    from .models import Reservation, SharedTask
    account = _lock_account(account)
    if (account.kind != 'business' or not actor or not actor.is_active or
            not Membership.objects.filter(account=account,user=actor,role='owner',is_active=True,
                user__is_active=True).exists()):
        raise ValueError('FORBIDDEN')
    if not isinstance(request_key,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,64}',request_key):
        raise ValueError('INVALID_IDEMPOTENCY_KEY')
    if type(membership_id) is not int or membership_id < 1:
        raise ValueError('INVALID_REQUEST')
    existing = MemberRemoval.objects.filter(account=account,request_key=request_key).first()
    if existing:
        if existing.membership_id != membership_id or existing.actor_id != actor.pk:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return _member_removal_dict(existing)
    target = _get(Membership.objects.select_for_update(),account=account,pk=membership_id)
    if target.user_id == actor.pk or target.role == 'owner':
        raise ValueError('OWNER_REMOVAL_FORBIDDEN')
    if not target.is_active:
        raise ValueError('MEMBER_ALREADY_REMOVED')
    reservations = [str(pk) for pk in Reservation.objects.filter(account=account,membership=target,
        state='reserved').order_by('id').values_list('id',flat=True)]
    target.is_active = False
    target.save(update_fields=['is_active'])
    from .models import BusinessInvitation
    BusinessInvitation.objects.filter(creator=target, state='pending').update(state='invalidated')
    SharedTask.objects.filter(account=account,membership=target).update(resume_blocked=True)
    record = MemberRemoval.objects.create(account=account,membership=target,actor=actor,request_key=request_key,
        pending_reservation_ids=reservations,handoff_pending=bool(reservations))
    from .models import BillingNotification, PaidSeat
    for seat in PaidSeat.objects.filter(subscription__account=account,membership=target).select_related('subscription'):
        if not _seat_usage_state(seat,timezone.now())[0]:
            assign_seat(account,actor,seat.pk,None,expected_version=seat.version,
                request_key=f'member-removal:{record.pk}:{seat.pk}')
    for recipient in (target.user_id,None):
        BillingNotification.objects.create(account=account,type='member_removed',recipient_user_id=recipient,
            source_event=f'member-removal:{record.pk}:{recipient or "administrators"}',occurred_at=record.created_at)
    AuditEvent.objects.create(account=account,actor=actor,action='membership.removed',request_id=request_id or uuid.uuid4())
    return _member_removal_dict(record)


def organization_history(account, *, offset=0):
    """Read existing audit versions; HTTP applies the existing financial role gate."""
    from django.db.models import F, Value
    from backend.models import LoginIdentity, Membership, MemberRemoval
    from .models import SeatChange, SharedLimitChange, MembershipRoleChange
    if account.kind != 'business':
        raise ValueError('FORBIDDEN')
    if type(offset) is not int or not 0 <= offset <= 999999999:
        raise ValueError('INVALID_REQUEST')
    seats = SeatChange.objects.filter(seat__subscription__account=account)
    limits = SharedLimitChange.objects.filter(membership__account=account)
    removals = MemberRemoval.objects.filter(account=account)
    roles = MembershipRoleChange.objects.filter(account=account)
    keys = seats.annotate(history_type=Value('seat_assignment'),event_id=F('id')).values(
        'history_type','event_id','created_at').union(limits.annotate(
            history_type=Value('shared_limit'),event_id=F('id')).values(
                'history_type','event_id','created_at'),removals.annotate(
                    history_type=Value('member_removal'),event_id=F('id')).values(
                        'history_type','event_id','created_at'),roles.annotate(
                    history_type=Value('member_role'),event_id=F('id')).values(
                        'history_type','event_id','created_at'),all=True)
    page = list(keys.order_by('-created_at','-history_type','-event_id')[offset:offset+51])
    seat_rows = {row.pk:row for row in seats.filter(pk__in=[item['event_id'] for item in page[:50]
        if item['history_type'] == 'seat_assignment']).select_related('seat')}
    limit_rows = {row.pk:row for row in limits.filter(pk__in=[item['event_id'] for item in page[:50]
        if item['history_type'] == 'shared_limit'])}
    removal_rows = {row.pk:row for row in removals.filter(pk__in=[item['event_id'] for item in page[:50]
        if item['history_type'] == 'member_removal'])}
    role_rows = {row.pk:row for row in roles.filter(pk__in=[item['event_id'] for item in page[:50]
        if item['history_type'] == 'member_role'])}
    rows = list(seat_rows.values()) + list(limit_rows.values()) + list(removal_rows.values()) + list(role_rows.values())
    actor_ids = list(Membership.objects.filter(account=account,
        user_id__in=[row.actor_id for row in rows]).values_list('user_id',flat=True))
    actor_labels = {}
    for identity in LoginIdentity.objects.filter(user_id__in=actor_ids).order_by('-id'):
        if identity.user_id in actor_labels:
            continue
        masked = ''
        if identity.method == 'email' and '@' in identity.identifier:
            local,domain = identity.identifier.rsplit('@',1)
            masked = local[:1]+'***@'+domain
        elif identity.method == 'phone' and len(identity.identifier) >= 7:
            masked = identity.identifier[:3]+'****'+identity.identifier[-4:]
        actor_labels[identity.user_id] = masked
    previous_query = Q(pk__in=[])
    for row in limit_rows.values():
        if row.version > 1:
            previous_query |= Q(membership_id=row.membership_id,version=row.version-1)
    previous = {(row.membership_id,row.version):row.quantity for row in limits.filter(previous_query)}
    items = []
    for key in page[:50]:
        if key['history_type'] == 'member_role':
            row = role_rows[key['event_id']]
            items.append({'id':f'role:{row.pk}', 'type':'member_role', 'created_at':row.created_at.isoformat(),
                'actor_masked_identifier':actor_labels.get(row.actor_id, ''), 'membership_id':row.membership_id,
                'before_role':row.before_role, 'after_role':row.after_role,
                'before_version':row.expected_version, 'version':row.resulting_version})
            continue
        if key['history_type'] == 'member_removal':
            row = removal_rows[key['event_id']]
            items.append({'id':f'removal:{row.pk}','type':'member_removal','created_at':row.created_at.isoformat(),
                'actor_masked_identifier':actor_labels.get(row.actor_id,''),'membership_id':row.membership_id,
                'handoff_pending':row.handoff_pending,'pending_task_count':len(row.pending_reservation_ids),
                'executor_connected':False})
            continue
        seat_change = key['history_type'] == 'seat_assignment'
        row = (seat_rows if seat_change else limit_rows)[key['event_id']]
        item = {'id':f'{"seat" if seat_change else "limit"}:{row.pk}',
            'type':key['history_type'],'created_at':row.created_at.isoformat(),
            'actor_masked_identifier':actor_labels.get(row.actor_id,'')}
        if seat_change:
            item.update(seat_id=str(row.seat_id),seat_position=row.seat.position,
                before_membership_id=row.before_membership_id,after_membership_id=row.after_membership_id,
                before_version=row.expected_version,version=row.resulting_version)
        else:
            prior_key = (row.membership_id,row.version-1)
            item.update(membership_id=row.membership_id,quantity=row.quantity,version=row.version,
                previous_known=row.version == 1 or prior_key in previous,
                previous_quantity=previous.get(prior_key))
        items.append(item)
    return {'account_id':str(account.pk),'items':items,'offset':offset,
        'next_offset':offset+50 if len(page)>50 else None,'total':keys.count()}


@transaction.atomic
def assign_seat(account, actor, seat_id, membership_id, *, expected_version, request_key):
    """Existing members only; no invitation, new membership, or free seat.

    Internal until public high-risk verification and organization UI are wired.
    Replay returns the recorded operation, not permission to overwrite newer work.
    """
    from backend.models import Membership
    from .models import PaidSeat, SeatChange
    account = _lock_account(account)
    _authorize_purchase(account, actor)
    if account.kind != 'business':
        raise ValueError('FORBIDDEN')
    _identifier(request_key)
    if type(expected_version) is not int or not 0 <= expected_version < 2147483647:
        raise ValueError('INVALID_SEAT_VERSION')
    if membership_id is not None and (type(membership_id) is not int or membership_id < 1):
        raise ValueError('NOT_FOUND')
    seat = _get(PaidSeat.objects.select_for_update().select_related('subscription'), subscription__account=account, pk=seat_id)
    existing = SeatChange.objects.filter(seat=seat,request_key=request_key).first()
    if existing:
        if (existing.actor_id != actor.pk or existing.expected_version != expected_version or
            existing.after_membership_id != membership_id):
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return {'seat_id':str(seat.pk),'membership_id':existing.after_membership_id,'version':existing.resulting_version}
    if seat.version != expected_version:
        raise ValueError('SEAT_CHANGED')
    now = timezone.now()
    if membership_id is not None:
        if any(row.seat_id == seat.pk for row in _pending_invitations(account, now)):
            raise ValueError('SEAT_RESERVED_FOR_INVITATION')
        period = active_subscription_periods(account,now).filter(subscription=seat.subscription).first()
        if not period or not _period_seats(period, now).filter(pk=seat.pk).exists():
            raise ValueError('SUBSCRIPTION_EXPIRED')
        _get(Membership.objects,pk=membership_id,account=account,is_active=True,user__is_active=True)
        previous = PaidSeat.objects.filter(subscription=seat.subscription,membership_id=membership_id).exclude(pk=seat.pk).first()
        if previous:
            if _period_seats(period, now).filter(pk=previous.pk).exists():
                raise ValueError('MEMBER_ALREADY_SEATED')
            assign_seat(account, actor, previous.pk, None, expected_version=previous.version,
                request_key='move-expired-seat:'+hashlib.sha256(f'{seat.pk}:{request_key}'.encode()).hexdigest())
    if membership_id != seat.membership_id:
        pending,used,locked_until = _seat_usage_state(seat,now)
        if pending:
            raise ValueError('TASK_SETTLEMENT_REQUIRED')
        if membership_id is not None and locked_until:
            raise ValueError('SEAT_LOCKED_UNTIL_NEXT_SERVICE_MONTH')
        if seat.membership_id is not None:
            # Unused seat batches remain reusable; consumed ones retain their
            # ledger but their remaining allowance ends when the member leaves.
            for batch in used:
                if batch.expires_at > now:
                    batch.expires_at = now
                    batch.save(update_fields=['expires_at'])
    before = seat.membership_id
    seat.membership_id = membership_id
    seat.version += 1
    seat.save(update_fields=['membership','version'])
    change = SeatChange.objects.create(seat=seat,actor=actor,before_membership_id=before,after_membership_id=membership_id,
        request_key=request_key,expected_version=expected_version,resulting_version=seat.version)
    from .models import BillingNotification
    BillingNotification.objects.create(account=account,type='seat_assignment_changed',
        source_event=f'seat-change:{change.pk}',subscription=seat.subscription,occurred_at=change.created_at)
    return {'seat_id':str(seat.pk),'membership_id':membership_id,'version':seat.version}


def _calendar_zone(name):
    if not isinstance(name, str) or len(name) > 64:
        raise ValueError('CALENDAR_POLICY_REQUIRED')
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError('CALENDAR_POLICY_REQUIRED') from exc


def _boundary(subscription, offset):
    return month_boundary(subscription.original_anchor.astimezone(
        _calendar_zone(subscription.billing_timezone)), offset)


def _months(subscription):
    return 12 if subscription.billing_cycle == 'annual' else 1


def _paid_order(account, order_id):
    order = _get(Order.objects.select_for_update(), account=account, pk=order_id)
    transport = _payment_snapshot(order)
    from .models import SubscriptionChange
    credited_change = (order.amount_minor==0 and SubscriptionChange.objects.filter(account=account,order=order,
        pk=order.snapshot.get('subscription_change_id'),state__in=['pending_payment','activated'],quote__amount_minor=0).exists())
    if not order.paid_at or (not credited_change and not order.payment_facts.filter(disposition='accepted',
        amount_minor=order.amount_minor, currency='CNY', channel=transport.get('channel'),
        merchant_reference=order.snapshot.get('merchant_entity')).exists()):
        raise ValueError('PAYMENT_NOT_CONFIRMED')
    if order.status not in ('paid_pending_entitlement', 'fulfilled'):
        raise ValueError('ORDER_STATE_CONFLICT')
    snapshot = order.snapshot
    if (snapshot.get('kind') != 'subscription' or snapshot.get('audience') != account.kind or
        snapshot.get('billing_cycle') not in ('monthly', 'annual') or
        not isinstance(snapshot.get('entitlements'), dict) or not snapshot['entitlements']):
        raise ValueError('CONFIG_INCOMPLETE')
    quantity = _quantity(snapshot.get('credits_quantity'))
    if account.kind == 'personal':
        if snapshot.get('seat_quantity') != 1:
            raise ValueError('CONFIG_INCOMPLETE')
    elif account.kind == 'business':
        mode = snapshot['entitlements'].get('resource_mode')
        if (type(snapshot.get('seat_quantity')) is not int or snapshot['seat_quantity'] < 1 or
            mode not in ('seat_included','shared_included','shared_only') or
            (mode == 'shared_only' and (quantity != 0 or snapshot['seat_quantity'] < 2)) or
            (mode == 'shared_included' and (quantity <= 0 or snapshot['seat_quantity'] < 10))):
            raise ValueError('CONFIG_INCOMPLETE')
    else:
        raise ValueError('CONFIG_INCOMPLETE')
    return order


def _mandate(value, snapshot):
    keys = {'channel', 'merchant_reference', 'agreement_reference', 'evidence_hash', 'executor', 'automatic_retry'}
    if (not isinstance(value, dict) or set(value) != keys or
        value['channel'] != snapshot.get('channel') or value['merchant_reference'] != snapshot.get('merchant_entity') or
        value['executor'] not in ('sage', 'provider') or value['automatic_retry'] is not False or
        not isinstance(value['evidence_hash'], str) or not re.fullmatch(r'[a-f0-9]{64}', value['evidence_hash'])):
        raise ValueError('MANDATE_NOT_CONFIRMED')
    _identifier(value['agreement_reference'])


def _renewal_snapshot(subscription, mandate=None, *, source_attempt=None):
    """Change only transport; every accepted commercial/terms field is retained."""
    mandate = subscription.mandate if mandate is None else mandate
    if not isinstance(mandate, dict):
        raise ValueError('MANDATE_NOT_CONFIRMED')
    if source_attempt is not None:
        channel = source_attempt.order.snapshot.get('channel')
        if (channel != subscription.initial_order.snapshot.get('channel') and
            not subscription.authorization_changes.filter(new_mandate__channel=channel,
                created_at__lte=source_attempt.created_at).exists()):
            raise ValueError('RENEWAL_SNAPSHOT_CONFLICT')
    snapshot = {**subscription.initial_order.snapshot, 'channel': mandate.get('channel')}
    if snapshot.get('subscription_change_id'):
        from .models import SubscriptionChange
        change=_get(SubscriptionChange.objects,account=subscription.account,new_subscription=subscription,state='activated')
        snapshot['amount_minor']=change.snapshot['gross_minor']
        snapshot.pop('subscription_change_id')
    if subscription.seat_purchases.filter(state='activated').exists() or subscription.seat_reductions.filter(state='applied').exists():
        last = subscription.periods.order_by('-cycle_index').first()
        seats = list(_period_seats(last))
        snapshot.update(seat_ids=[str(seat.pk) for seat in seats], seat_quantity=len(seats),
            amount_minor=snapshot['unit_amount_minor']*len(seats))
    _mandate(mandate, snapshot)
    if snapshot.get('currency') != 'CNY' or mandate['executor'] != 'sage':
        raise ValueError('RENEWAL_EXECUTOR_CONFLICT')
    return snapshot


@transaction.atomic
def replace_subscription_authorization(account, user, subscription_id, *, payment_method_id,
                                       mandate, expected_version, request_key):
    """Trusted verified-mandate boundary, not a public signing endpoint.

    Same merchant/CNY and Sage-executed renewals only. Existing intents retain
    commercial snapshots; dispatch freezes the confirmed transport. No PSP call.
    """
    account = _lock_account(account)
    _authorize_purchase(account, user)
    _identifier(request_key)
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError('AUTHORIZATION_VERSION_CHANGED')
    sub = _get(Subscription.objects.select_for_update(), account=account, pk=subscription_id)
    snapshot = _renewal_snapshot(sub, mandate)
    existing = sub.authorization_changes.filter(request_key=request_key).first()
    if existing:
        if (existing.version != expected_version+1 or existing.new_mandate != mandate or
            str(existing.new_method_id) != str(payment_method_id) or existing.actor_id != user.pk):
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return {'id': str(existing.pk), 'version': existing.version}
    if sub.authorization_version != expected_version:
        raise ValueError('AUTHORIZATION_VERSION_CHANGED')
    now = timezone.now()
    if (not sub.auto_renew or sub.cancellation_state != 'none' or
        not active_subscription_periods(account,now).filter(subscription=sub).exists()):
        raise ValueError('RENEWAL_NOT_AUTHORIZED')
    if sub.mandate['executor'] != 'sage' or mandate['executor'] != 'sage':
        raise ValueError('RENEWAL_EXECUTOR_CONFLICT')
    for channel in {sub.mandate['channel'], mandate['channel']}:
        _sale_gate(sub.initial_order.quote.product, account, now, channel,
                   snapshot['buyer_region'], snapshot['merchant_entity'],historical_subscription=True)
    for pending in PaymentAttempt.objects.filter(subscription=sub,state='created',dispatched_at__isnull=True).select_related('order__quote'):
        _renewal_snapshot(sub,mandate,source_attempt=pending)
        if (pending.dispatch_authorization is not None or pending.needs_reconciliation or pending.expires_at <= now or
            pending.events.exists() or pending.order.payment_facts.exists() or pending.order.paid_at or
            pending.order.status != 'pending_payment' or pending.order.snapshot != pending.order.quote.snapshot or
            {**pending.order.snapshot,'channel':snapshot['channel']} != snapshot):
            raise ValueError('RENEWAL_INTENT_PENDING')
    method = _get(PaymentMethod.objects.select_for_update(), account=account, pk=payment_method_id)
    if (method.state != 'active' or method.channel != mandate['channel'] or
        method.merchant_reference != mandate['merchant_reference']):
        raise ValueError('PAYMENT_METHOD_NOT_AVAILABLE')
    agreement = mandate['agreement_reference']
    if (agreement == sub.mandate['agreement_reference'] or
        sub.authorization_changes.filter(old_mandate__agreement_reference=agreement).exists()):
        raise ValueError('NEW_AUTHORIZATION_REQUIRED')
    change = AuthorizationChange.objects.create(subscription=sub, version=expected_version+1,
        request_key=request_key, actor=user, old_mandate=sub.mandate, new_mandate=mandate,
        old_method_id=sub.payment_method_id, new_method=method)
    # Only this locked transition bypasses the model's immutable save guard.
    # History and binding commit together; no order or entitlement is created.
    changed = Subscription.objects.filter(pk=sub.pk, authorization_version=expected_version).update(
        mandate=mandate, payment_method=method, authorization_version=expected_version+1)
    if changed != 1:
        raise ValueError('AUTHORIZATION_VERSION_CHANGED')
    AuditEvent.objects.create(actor=user, account=account, action='subscription.authorization_changed', request_id=change.pk)
    return {'id': str(change.pk), 'version': change.version}


@transaction.atomic
def confirm_retired_authorization(account, change_id, *, agreement_reference, evidence_hash):
    """Trusted provider revocation result; never cancels the new authorization."""
    account = _lock_account(account)
    change = _get(AuthorizationChange.objects.select_for_update(), subscription__account=account, pk=change_id)
    if (agreement_reference != change.old_mandate['agreement_reference'] or
        not isinstance(evidence_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', evidence_hash)):
        raise ValueError('INVALID_REVOCATION_EVIDENCE')
    if change.revoked_at:
        if change.revocation_evidence_hash != evidence_hash:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return
    change.revoked_at, change.revocation_evidence_hash = timezone.now(), evidence_hash
    change.save(update_fields=['revoked_at', 'revocation_evidence_hash'])
    AuditEvent.objects.create(account=account, action='subscription.retired_authorization_revoked', request_id=change.pk)


@transaction.atomic
def prepare_renewal_attempt(account, subscription_id, *, cycle_index, dispatch_not_before, expires_at):
    """Trusted scheduler boundary, with an explicitly supplied approved window.

    Never derives a new intent from a client key, retries a failed charge, or
    chooses a channel's advance-debit policy. No PSP is called here.
    """
    account = _lock_account(account)
    subscription = _get(Subscription.objects.select_for_update().select_related('initial_order'),
                        account=account, pk=subscription_id)
    if type(cycle_index) is not int or cycle_index < 1:
        raise ValueError('RENEWAL_CYCLE_CONFLICT')
    _datetime(dispatch_not_before)
    _datetime(expires_at)
    existing = PaymentAttempt.objects.filter(subscription=subscription, renewal_cycle_index=cycle_index).first()
    if existing:
        if existing.dispatch_not_before != dispatch_not_before or existing.expires_at != expires_at:
            raise ValueError('PAYMENT_INTENT_CONFLICT')
        return _attempt_dict(existing, existing.order)
    if not subscription.auto_renew or subscription.cancellation_state != 'none':
        raise ValueError('RENEWAL_NOT_AUTHORIZED')
    if _service_refund_pending(subscription):
        raise ValueError('REFUND_PENDING')
    if _seat_purchase_pending(subscription):
        raise ValueError('PAYMENT_RECONCILIATION_REQUIRED')
    initial = subscription.initial_order
    snapshot = _renewal_snapshot(subscription)
    if subscription.mandate['executor'] != 'sage':
        raise ValueError('RENEWAL_EXECUTOR_CONFLICT')
    last = subscription.periods.order_by('-cycle_index').first()
    if not last or cycle_index != last.cycle_index + 1:
        raise ValueError('RENEWAL_CYCLE_CONFLICT')
    start = _boundary(subscription, cycle_index * _months(subscription))
    if not dispatch_not_before < expires_at <= start or expires_at <= timezone.now():
        raise ValueError('INVALID_RENEWAL_WINDOW')
    key = f'renewal:{subscription.pk}:{cycle_index}'
    buyer=_billing_operator(account,initial.created_by)
    _sale_gate(initial.quote.product, account, timezone.now(), snapshot['channel'],
               snapshot['buyer_region'], snapshot['merchant_entity'],historical_subscription=True)
    if (initial.currency != 'CNY' or type(snapshot.get('amount_minor')) is not int or snapshot['amount_minor'] <= 0 or
            (not initial.snapshot.get('subscription_change_id') and not snapshot.get('seat_ids') and
                initial.amount_minor!=snapshot['amount_minor'])):
        raise ValueError('RENEWAL_SNAPSHOT_CONFLICT')
    if Order.objects.filter(account=account, idempotency_key=key).exists():
        raise ValueError('PAYMENT_INTENT_CONFLICT')
    # This is a previously authorized renewal, not a public new purchase.
    # In particular preserve the accepted terms digest, even if the public
    # catalog/terms have since changed; do not reconstruct commercial fields.
    quote = Quote.objects.create(account=account, product=initial.quote.product,
        snapshot=snapshot, amount_minor=snapshot['amount_minor'], currency='CNY', expires_at=expires_at)
    payload = {'quote_id':str(quote.pk),'agreement_acceptance':snapshot['terms_version']}
    fingerprint = hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    order = Order.objects.create(account=account,created_by=buyer,quote=quote,
        idempotency_key=key,request_hash=fingerprint,snapshot=snapshot,
        amount_minor=snapshot['amount_minor'],currency='CNY')
    attempt = PaymentAttempt.objects.create(account=account, order=order, intent_key=key, expires_at=expires_at,
        subscription=subscription, renewal_cycle_index=cycle_index, dispatch_not_before=dispatch_not_before)
    return _attempt_dict(attempt, order)


@transaction.atomic
def fulfill_renewal_attempt(account, attempt_id):
    """Deliver a bound, dispatched renewal only after an accepted money fact.

    Cancellation does not discard an already dispatched payment. Late receipts
    remain recorded and require the separately specified recovery policy.
    """
    account = _lock_account(account)
    attempt = _get(PaymentAttempt.objects.select_for_update(), account=account, pk=attempt_id)
    if not attempt.subscription_id or not attempt.dispatched_at:
        raise ValueError('RENEWAL_NOT_DISPATCHED')
    return append_paid_period(account, attempt.subscription_id, attempt.order_id,
                              cycle_index=attempt.renewal_cycle_index)


def _recovery_context(account, original, now):
    """Called under the account lock, both at preparation and before dispatch."""
    if original.account_id != account.pk or not original.subscription_id or original.recovery_of_id:
        raise ValueError('RECOVERY_NOT_ELIGIBLE')
    subscription = _get(Subscription.objects.select_for_update(), account=account, pk=original.subscription_id)
    last = subscription.periods.order_by('-cycle_index').first()
    if (not last or original.renewal_cycle_index != last.cycle_index + 1 or
        not subscription.auto_renew or subscription.cancellation_state != 'none'):
        raise ValueError('RECOVERY_NOT_ELIGIBLE')
    deadline = last.ends_at.astimezone(_calendar_zone(subscription.billing_timezone)) + timedelta(days=3)
    state = _effective_attempt_state(original, original.order)
    if (not last.ends_at <= now < deadline or original.needs_reconciliation or original.order.payment_facts.exists() or
        not (state == 'failed' or (state == 'expired' and original.dispatched_at is None))):
        raise ValueError('RECOVERY_NOT_ELIGIBLE')
    if original.dispatched_at:
        authorization = original.dispatch_authorization
        if (not isinstance(authorization,dict) or authorization.get('subscription_id') != str(subscription.pk) or
                authorization.get('authorization_version') != subscription.authorization_version or
                authorization.get('mandate') != subscription.mandate):
            # A changed mandate is not permission to recharge an older intent.
            raise ValueError('RECOVERY_NOT_ELIGIBLE')
    snapshot = _payment_snapshot(original.order) if original.dispatched_at else original.order.snapshot
    _mandate(subscription.mandate, snapshot)
    if subscription.mandate['executor'] != 'sage' or account.kind != 'personal':
        raise ValueError('RECOVERY_NOT_ELIGIBLE')
    if snapshot != _renewal_snapshot(subscription):
        raise ValueError('RENEWAL_SNAPSHOT_CONFLICT')
    return subscription, deadline


@transaction.atomic
def prepare_recovery_attempt(account, user, original_attempt_id, *, agreement_acceptance, expires_at):
    """Explicit user-confirmed recovery intent, not automatic renewal retry.

    Internal only until channel/activation policy is ready. Repeated submission
    returns the existing attempt; it never manufactures a new charge after an
    unknown result. Subsequent manual retries require their own reviewed policy.
    """
    account = _lock_account(account)
    _authorize_purchase(account, user)
    _datetime(expires_at)
    original = _get(PaymentAttempt.objects.select_for_update(), account=account, pk=original_attempt_id)
    if agreement_acceptance != original.order.snapshot.get('terms_version'):
        raise ValueError('TERMS_MISMATCH')
    existing = PaymentAttempt.objects.filter(recovery_of=original).first()
    if existing:
        if existing.expires_at != expires_at:
            raise ValueError('PAYMENT_INTENT_CONFLICT')
        return _attempt_dict(existing, existing.order)
    subscription, deadline = _recovery_context(account, original, timezone.now())
    if not timezone.now() < expires_at <= deadline:
        raise ValueError('INVALID_RECOVERY_WINDOW')
    source = original.order
    snapshot = _payment_snapshot(source) if original.dispatched_at else source.snapshot
    _sale_gate(source.quote.product, account, timezone.now(), snapshot['channel'],
               snapshot['buyer_region'], snapshot['merchant_entity'],historical_subscription=True)
    key = f'recovery:{original.pk}'
    if source.currency != 'CNY' or source.amount_minor != snapshot.get('amount_minor') or source.amount_minor <= 0:
        raise ValueError('RENEWAL_SNAPSHOT_CONFLICT')
    if Order.objects.filter(account=account,idempotency_key=key).exists():
        raise ValueError('PAYMENT_INTENT_CONFLICT')
    # Recovery retains the failed intent's commercial terms and transport,
    # not the initial purchase's channel or a newer public catalog version.
    quote = Quote.objects.create(account=account,product=source.quote.product,snapshot=snapshot,
        amount_minor=source.amount_minor,currency='CNY',expires_at=expires_at)
    payload = {'quote_id':str(quote.pk),'agreement_acceptance':agreement_acceptance}
    fingerprint = hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    order = Order.objects.create(account=account,created_by=user,quote=quote,idempotency_key=key,
        request_hash=fingerprint,snapshot=snapshot,amount_minor=source.amount_minor,currency='CNY')
    attempt = PaymentAttempt.objects.create(account=account, order=order, intent_key=key,
        expires_at=expires_at, recovery_of=original)
    return _attempt_dict(attempt, order)


@transaction.atomic
def activate_recovery_subscription(account, attempt_id, *, activated_at, mandate, payment_method_id=None):
    """New-authorisation branch only. Never silently reuses/revokes a mandate.

    A trusted caller must already have confirmed the old cancellation and new
    mandate with its PSP. Reusing an old agreement with a changed schedule is
    a separate channel capability, not inferred by this function.
    """
    account = _lock_account(account)
    attempt = _get(PaymentAttempt.objects.select_for_update(), account=account, pk=attempt_id)
    if not attempt.recovery_of_id or not attempt.dispatched_at or attempt.needs_reconciliation:
        raise ValueError('RECOVERY_NOT_CONFIRMED')
    original = _get(PaymentAttempt.objects.select_for_update(), account=account, pk=attempt.recovery_of_id)
    old = _get(Subscription.objects.select_for_update(), account=account, pk=original.subscription_id)
    if original.order.payment_facts.exists() or original.needs_reconciliation:
        raise ValueError('RECOVERY_RECONCILIATION_REQUIRED')
    order = _paid_order(account, attempt.order_id)
    last = old.periods.order_by('-cycle_index').first()
    source = _payment_snapshot(original.order) if original.dispatched_at else original.order.snapshot
    if not last or original.renewal_cycle_index != last.cycle_index + 1 or order.snapshot != source:
        raise ValueError('RECOVERY_INTENT_CONFLICT')
    deadline = last.ends_at.astimezone(_calendar_zone(old.billing_timezone)) + timedelta(days=3)
    if not last.ends_at <= order.paid_at < min(deadline, attempt.expires_at):
        raise ValueError('RECOVERY_PAYMENT_OUTSIDE_WINDOW')
    _mandate(mandate, order.snapshot)
    if (old.auto_renew or old.cancellation_state != 'completed' or not old.cancel_confirmed_at or
        not old.cancellation_evidence_hash or mandate['agreement_reference'] == old.mandate['agreement_reference']):
        raise ValueError('RECOVERY_AUTHORIZATION_REQUIRED')
    return activate_subscription(account, order.pk, activated_at=activated_at, billing_timezone=old.billing_timezone,
                                 calendar_rule_version=old.calendar_rule_version, mandate=mandate,
                                 payment_method_id=payment_method_id)


def _renewal_state(subscription, now):
    last = subscription.periods.order_by('-cycle_index').first()
    if not last:
        return None
    attempt = PaymentAttempt.objects.filter(subscription=subscription,
        renewal_cycle_index=last.cycle_index + 1).select_related('order').first()
    if not attempt and now < last.ends_at:
        return None
    state = _effective_attempt_state(attempt, attempt.order) if attempt else 'missing'
    recovery = PaymentAttempt.objects.filter(recovery_of=attempt).select_related('order').first() if attempt else None
    # Accepted money still delivers; an additional receipt remains a separate
    # review even when ingestion did not create an attempt reconciliation flag.
    receipt_review = bool(
        (attempt and attempt.order.payment_facts.exclude(disposition='accepted').exists()) or
        (recovery and recovery.order.payment_facts.exclude(disposition='accepted').exists()))
    if recovery and Subscription.objects.filter(initial_order=recovery.order).exists():
        if attempt.needs_reconciliation or attempt.order.payment_facts.exists():
            state = 'review'
        elif receipt_review:
            state = _effective_attempt_state(recovery, recovery.order)
        else:
            return None
    if recovery and state in ('failed', 'expired') and not attempt.needs_reconciliation:
        state = _effective_attempt_state(recovery, recovery.order)
        if recovery.needs_reconciliation:
            state = 'review'
    # A review-only receipt must not turn accepted, pending delivery into a
    # failed renewal or change the expiry worker's authorization decision.
    policy_state = state
    if receipt_review:
        state = 'review'
    # The subscription already carries its trusted calendar/timezone policy.
    # Three calendar days here never grants three days of service or credit.
    deadline = last.ends_at.astimezone(_calendar_zone(subscription.billing_timezone)) + timedelta(days=3)
    decision = renewal_failure_decision('failed' if policy_state in ('failed', 'expired', 'cancelled') else 'unknown',
                                        last.ends_at, deadline, now)
    resolved_failure = bool(attempt and not attempt.needs_reconciliation and
        (state == 'failed' or (state == 'expired' and attempt.dispatched_at is None)))
    unresolved = state in ('pending', 'unknown', 'review') or bool(attempt and attempt.needs_reconciliation)
    return {'status': state, 'manual_payment_deadline': deadline.isoformat(),
        'manual_payment_eligible': bool(not recovery and resolved_failure and subscription.auto_renew and decision['allow_manual_payment']),
        'new_subscription_payment_eligible': bool(not recovery and resolved_failure and decision['allow_new_subscription_payment']),
        'query_original_payment': bool(unresolved),
        'reauthorization_required': bool(decision['new_subscription_requires_authorization'] and policy_state != 'succeeded'),
        'close_future_auto_renewal': bool(decision['close_future_auto_renewal'] and policy_state != 'succeeded')}


@transaction.atomic
def close_expired_renewals(account):
    """Persist local debit blocking; PSP revocation remains separately pending."""
    account = _lock_account(account)
    now, count = timezone.now(), 0
    # A cancelled renewal still retains service until its paid term ends.
    # Top-up pauses at that boundary, not after the renewal recovery window.
    if not active_subscription_periods(account,now).exists():
        pause_topup_authorization(account,reason='subscription_expired')
    for subscription in Subscription.objects.select_for_update().filter(account=account, auto_renew=True):
        state = _renewal_state(subscription, now)
        if state and state['close_future_auto_renewal']:
            subscription.auto_renew = False
            subscription.cancellation_state = 'processing'
            subscription.cancel_requested_at = now
            subscription.save(update_fields=['auto_renew', 'cancellation_state', 'cancel_requested_at'])
            AuditEvent.objects.create(account=account, action='subscription.recovery_expired', request_id=uuid.uuid4())
            count += 1
    return count


def _describe(subscription, now):
    periods = list(subscription.periods.order_by('starts_at'))
    cutoff=min((value for value in (subscription.superseded_at,subscription.refund_cutoff_at) if value),default=None)
    current = next((period for period in periods if period.starts_at <= now < period.ends_at and (not cutoff or now < cutoff)), None)
    future = next((period for period in periods if period.starts_at > now and (not cutoff or period.starts_at < cutoff)), None)
    shown = current or future or (periods[-1] if periods else None)
    next_grant = None
    for period in periods:
        if _quantity(period.snapshot.get('credits_quantity')) == 0:
            continue
        for month in range(_months(subscription)):
            boundary = _boundary(subscription, period.cycle_index * _months(subscription) + month)
            if boundary > now and (not cutoff or boundary < cutoff) and (next_grant is None or boundary < next_grant):
                next_grant = boundary
    return {
        'id': str(subscription.pk), 'plan_code': shown.snapshot['code'] if shown else '',
        'billing_cycle': subscription.billing_cycle,
        'status': 'active' if current else 'scheduled' if future else 'expired',
        'period_start': shown.starts_at.isoformat() if shown else None,
        'period_end': min(shown.ends_at,cutoff).isoformat() if shown and cutoff else shown.ends_at.isoformat() if shown else None,
        'next_credit_grant_at': next_grant.isoformat() if next_grant else None,
        'prepaid_through': min(max(period.ends_at for period in periods),cutoff).isoformat() if periods and cutoff else max((period.ends_at for period in periods), default=None).isoformat() if periods else None,
        'auto_renew': subscription.auto_renew, 'cancellation_state': subscription.cancellation_state,
        'can_cancel': subscription.auto_renew and subscription.cancellation_state == 'none',
        'renewal_setup': {'status': 'failed', 'separate_authorization_required': True}
            if subscription.mandate.get('renewal_setup_failure') else None,
        'renewal': _renewal_state(subscription, now),
        'capabilities': {'cancel': True, 'upgrade': False, 'payment_method': False, 'manual_payment': False},
    }


@transaction.atomic
def list_subscriptions(account):
    account = _lock_account(account)
    now = timezone.now()
    return [_describe(item, now) for item in Subscription.objects.filter(account=account).order_by('-created_at', '-id')]


@transaction.atomic
def grant_due_credits(account):
    """Idempotent delivery only; never creates a payment or extends expiry.

    Late runs record historical expired months separately, not as current credit
    or a compensation promise. Pending cross-expiry reservations stay untouched.
    """
    account = _lock_account(account)
    now, count = timezone.now(), 0
    periods = SubscriptionPeriod.objects.filter(subscription__account=account, starts_at__lte=now).select_related('subscription')
    for period in periods:
        subscription = period.subscription
        months = _months(subscription)
        while period.granted_months < months:
            index = period.granted_months
            offset = period.cycle_index * months + index
            if _boundary(subscription, offset) > now:
                break
            cutoff=min((value for value in (subscription.superseded_at,subscription.refund_cutoff_at) if value),default=None)
            if cutoff and _boundary(subscription,offset) >= cutoff:
                break
            quantity = _quantity(period.snapshot['credits_quantity'])
            if quantity > 0:
                seats = list(_period_seats(period, _boundary(subscription, offset), include_additions=False)) if account.kind == 'business' else [None]
                if account.kind == 'business' and period.snapshot['entitlements'].get('resource_mode') == 'shared_included':
                    quantity *= len(seats)
                    seats = [None] if quantity > 0 else []
                for seat in seats:
                    key = f'subscription:{period.pk}:{index}' + (f':{seat.pk}' if seat else '')
                    expiry=_boundary(subscription,offset+1)
                    if cutoff:expiry=min(expiry,cutoff)
                    result = grant_credits(account, 'subscription', key,
                        _text(quantity), expiry)
                    batch = CreditBatch.objects.get(pk=result['id'])
                    if batch.subscription_period_id not in (None, period.pk) or batch.service_month not in (None, index):
                        raise ValueError('LEDGER_INCONSISTENT')
                    batch.subscription_period, batch.service_month, batch.seat = period, index, seat
                    batch.save(update_fields=['subscription_period', 'service_month', 'seat'])
                    count += 1
            period.granted_months += 1
            period.save(update_fields=['granted_months'])
    from .models import SeatPurchaseChange
    for change in SeatPurchaseChange.objects.filter(account=account, state='activated').select_related('subscription', 'period'):
        first, _, first_quantity = _seat_purchase_credits(change, change.activated_at)
        subscription, period = change.subscription, change.period
        months = _months(subscription)
        while first+change.granted_months < months:
            index = first+change.granted_months
            offset = period.cycle_index*months+index
            if _boundary(subscription, offset) > now:
                break
            cutoff = min((value for value in (subscription.superseded_at, subscription.refund_cutoff_at) if value), default=None)
            if cutoff and _boundary(subscription, offset) >= cutoff:
                break
            quantity = first_quantity if index == first else _quantity(change.snapshot['credits_quantity'])
            all_seats = _purchase_seats(change)
            if all_seats.count() != change.quantity:
                raise ValueError('LEDGER_INCONSISTENT')
            seats = list(all_seats.filter(pk__in=_period_seats(period, max(change.activated_at, _boundary(subscription, offset))).values('pk')))
            if period.snapshot['entitlements'].get('resource_mode') == 'shared_included':
                quantity *= len(seats)
                seats = [None]
            if quantity > 0:
                for seat in seats:
                    key = f'seat-purchase:{change.pk}:{index}'+(f':{seat.pk}' if seat else '')
                    expiry = _boundary(subscription, offset+1)
                    if cutoff:
                        expiry = min(expiry, cutoff)
                    result = grant_credits(account, 'subscription', key, _text(quantity), expiry)
                    batch = CreditBatch.objects.get(pk=result['id'])
                    batch.purchase_change = change
                    batch.subscription_period, batch.service_month, batch.seat = period, index, seat
                    batch.save(update_fields=['purchase_change', 'subscription_period', 'service_month', 'seat'])
                    count += 1
            change.granted_months += 1
            change.save(update_fields=['granted_months'])
    return {'created_batches': count}


@transaction.atomic
def activate_subscription(account, order_id, *, activated_at, billing_timezone, calendar_rule_version, mandate,
                          payment_method_id=None, renewal_setup_failure=None):
    """Trusted delivery from accepted money and a verified renewal setup outcome.

    A confirmed setup failure grants the paid term without any debit authority.
    Unknown signing results stay pending; this input has no browser route.
    """
    account = _lock_account(account)
    order = _paid_order(account, order_id)
    if order.snapshot.get('seat_purchase_change_id'):
        raise ValueError('INVALID_REQUEST')
    _datetime(activated_at)
    _calendar_zone(billing_timezone)
    if not isinstance(calendar_rule_version, str) or not calendar_rule_version.strip() or len(calendar_rule_version) > 80:
        raise ValueError('CALENDAR_POLICY_REQUIRED')
    without_renewal = renewal_setup_failure is not None
    if without_renewal:
        if (mandate is not None or payment_method_id is not None or
                not isinstance(renewal_setup_failure, dict) or
                set(renewal_setup_failure) != {'status', 'evidence_hash'} or
                renewal_setup_failure['status'] != 'confirmed_failed' or
                not isinstance(renewal_setup_failure['evidence_hash'], str) or
                not re.fullmatch(r'[a-f0-9]{64}', renewal_setup_failure['evidence_hash'])):
            raise ValueError('MANDATE_NOT_CONFIRMED')
        # A failure record is deliberately not a mandate: no agreement reference,
        # executor or authority to debit exists. The immutable JSON retains proof.
        mandate = {'renewal_setup_failure': dict(renewal_setup_failure)}
    else:
        _mandate(mandate, order.snapshot)
    existing = Subscription.objects.filter(initial_order=order).first()
    if existing:
        first_change = existing.authorization_changes.order_by('version').first()
        original_mandate = first_change.old_mandate if first_change else existing.mandate
        original_method = first_change.old_method_id if first_change else existing.payment_method_id
        if (existing.original_anchor != activated_at or existing.billing_timezone != billing_timezone or
            existing.calendar_rule_version != calendar_rule_version or original_mandate != mandate or
            str(original_method or '') != str(payment_method_id or '')):
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return _describe(existing, timezone.now())
    method = None
    if not without_renewal and payment_method_id is None and PaymentMethod.objects.filter(account=account,
            channel=mandate['channel'], merchant_reference=mandate['merchant_reference']).exists():
        raise ValueError('PAYMENT_METHOD_BINDING_REQUIRED')
    if payment_method_id is not None:
        method = _get(PaymentMethod.objects.select_for_update(), account=account, pk=payment_method_id)
        if (method.state != 'active' or method.channel != mandate['channel'] or
            method.merchant_reference != mandate['merchant_reference']):
            raise ValueError('PAYMENT_METHOD_NOT_AVAILABLE')
    if activated_at < order.paid_at or activated_at > timezone.now():
        raise ValueError('INVALID_ACTIVATION_TIME')
    conflicting=SubscriptionPeriod.objects.filter(subscription__account=account,ends_at__gt=activated_at).filter(
        Q(subscription__superseded_at__isnull=True)|Q(subscription__superseded_at__gt=activated_at)).filter(
        Q(subscription__refund_cutoff_at__isnull=True)|Q(subscription__refund_cutoff_at__gt=activated_at))
    if (Subscription.objects.filter(account=account, auto_renew=True).exists() or conflicting.exists() or
        SubscriptionPeriod.objects.filter(order=order).exists()):
        raise ValueError('SUBSCRIPTION_CONFLICT')
    subscription = Subscription.objects.create(account=account, initial_order=order,
        original_anchor=activated_at, billing_timezone=billing_timezone, calendar_rule_version=calendar_rule_version,
        billing_cycle=order.snapshot['billing_cycle'], mandate=mandate, payment_method=method,
        auto_renew=not without_renewal)
    if account.kind == 'business':
        from .models import PaidSeat
        PaidSeat.objects.bulk_create([PaidSeat(subscription=subscription,position=position)
                                     for position in range(1,order.snapshot['seat_quantity']+1)])
    SubscriptionPeriod.objects.create(subscription=subscription, order=order, cycle_index=0,
        starts_at=activated_at, ends_at=_boundary(subscription, _months(subscription)), snapshot=order.snapshot)
    grant_due_credits(account)
    order.status = 'fulfilled'
    order.save(update_fields=['status'])
    if without_renewal:
        AuditEvent.objects.create(account=account, action='subscription.activated_without_renewal', request_id=subscription.pk)
    return _describe(subscription, timezone.now())


@transaction.atomic
def append_paid_period(account, subscription_id, order_id, *, cycle_index):
    """Attach a verified on-time renewal; never initiate another charge.

    Already in-flight money is preserved even if the user has since cancelled.
    Late/manual recovery and price/plan changes need their separate D03/D04 path.
    """
    account = _lock_account(account)
    subscription = _get(Subscription.objects.select_for_update(), account=account, pk=subscription_id)
    order = _paid_order(account, order_id)
    if type(cycle_index) is not int or cycle_index < 1:
        raise ValueError('INVALID_RENEWAL_PERIOD')
    existing = SubscriptionPeriod.objects.filter(order=order).first()
    if existing:
        if existing.subscription_id != subscription.pk or existing.cycle_index != cycle_index:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return _describe(subscription, timezone.now())
    initial = subscription.periods.get(cycle_index=0)
    if order.snapshot != initial.snapshot or PaymentAttempt.objects.filter(order=order,subscription=subscription).exists():
        attempt = PaymentAttempt.objects.filter(order=order,subscription=subscription,
            renewal_cycle_index=cycle_index,dispatched_at__isnull=False).first()
        authorization = attempt.dispatch_authorization if attempt else None
        if (not isinstance(authorization,dict) or authorization.get('subscription_id') != str(subscription.pk) or
                not isinstance(authorization.get('mandate'),dict) or
                _payment_snapshot(order) != _renewal_snapshot(subscription,authorization['mandate'])):
            raise ValueError('SUBSCRIPTION_CHANGE_POLICY_REQUIRED')
    if subscription.periods.filter(cycle_index=cycle_index).exists() or not subscription.periods.filter(cycle_index=cycle_index-1).exists():
        raise ValueError('SUBSCRIPTION_CONFLICT')
    start = _boundary(subscription, cycle_index * _months(subscription))
    end = _boundary(subscription, (cycle_index + 1) * _months(subscription))
    if order.paid_at > start:
        raise ValueError('LATE_RENEWAL_REQUIRES_ACTIVATION')
    if SubscriptionPeriod.objects.filter(subscription__account=account,subscription__superseded_at__isnull=True,starts_at__lt=end,ends_at__gt=start).exists():
        raise ValueError('SUBSCRIPTION_CONFLICT')
    SubscriptionPeriod.objects.create(subscription=subscription, order=order, cycle_index=cycle_index,
        starts_at=start, ends_at=end, snapshot=order.snapshot)
    grant_due_credits(account)
    order.status = 'fulfilled'
    order.save(update_fields=['status'])
    return _describe(subscription, timezone.now())


@transaction.atomic
def cancel_subscription(account, user, subscription_id):
    account = _lock_account(account)
    _authorize_purchase(account, user)
    subscription = _get(Subscription.objects.select_for_update(), account=account, pk=subscription_id)
    if subscription.cancellation_state == 'none' and subscription.auto_renew:
        subscription.auto_renew = False
        subscription.cancellation_state = 'processing'
        subscription.cancel_requested_at = timezone.now()
        subscription.save(update_fields=['auto_renew', 'cancellation_state', 'cancel_requested_at'])
        AuditEvent.objects.create(actor=user, account=account, action='subscription.cancel_requested', request_id=uuid.uuid4())
        from .models import BillingNotification
        BillingNotification.objects.create(account=account,type='cancellation_processing',
            source_event=f'subscription:{subscription.pk}:cancellation_processing',
            subscription=subscription,occurred_at=subscription.cancel_requested_at)
    # No external call inside this transaction. A future channel worker must
    # query/revoke and then call confirm_cancellation; failures remain pending.
    return _describe(subscription, timezone.now())


@transaction.atomic
def confirm_cancellation(account, subscription_id, *, agreement_reference, evidence_hash):
    account = _lock_account(account)
    subscription = _get(Subscription.objects.select_for_update(), account=account, pk=subscription_id)
    if (not subscription.mandate.get('agreement_reference') or
        agreement_reference != subscription.mandate.get('agreement_reference') or
        not isinstance(evidence_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', evidence_hash)):
        raise ValueError('INVALID_CANCELLATION_EVIDENCE')
    if subscription.cancellation_state != 'completed':
        subscription.auto_renew = False
        subscription.cancellation_state = 'completed'
        subscription.cancel_confirmed_at = timezone.now()
        subscription.cancellation_evidence_hash = evidence_hash
        subscription.save(update_fields=['auto_renew', 'cancellation_state', 'cancel_confirmed_at', 'cancellation_evidence_hash'])
        AuditEvent.objects.create(account=account, action='subscription.cancel_confirmed', request_id=uuid.uuid4())
        from .models import BillingNotification
        BillingNotification.objects.create(account=account,type='cancellation_confirmed',
            source_event=f'subscription:{subscription.pk}:cancellation_confirmed',
            subscription=subscription,occurred_at=subscription.cancel_confirmed_at)
    return _describe(subscription, timezone.now())
