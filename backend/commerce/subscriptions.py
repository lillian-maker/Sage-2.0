"""Paid service periods, monthly delivery and local cancellation.

Activation and channel confirmation are trusted internal operations, not HTTP
payment endpoints. They do not charge, sign a mandate or verify a PSP callback.
"""
import re
import uuid
import hashlib
import json
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from backend.billing_rules import month_boundary, renewal_failure_decision
from backend.models import AuditEvent
from .billing import (_authorize_purchase, _datetime, _get, _identifier, _lock_account,
                      _quantity, _attempt_dict, _effective_attempt_state, _payment_snapshot, _sale_gate, create_quote, create_order, grant_credits)
from .models import CreditBatch, Quote, Order, Subscription, SubscriptionPeriod, PaymentAttempt, PaymentMethod, AuthorizationChange


@transaction.atomic
def record_expired_notifications(account):
    """Trusted internal scan only. A period ending is not account-wide expiry.

    Records existing endpoints without changing balances, dispatching payments
    or sending messages. GET handlers must never invoke this write operation.
    """
    from .models import BillingNotification
    account = _lock_account(account)
    now, count = timezone.now(), 0
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
                               expected_version, request_key):
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
    if not SubscriptionPeriod.objects.filter(subscription__account=account,starts_at__lte=now,ends_at__gt=now).exists():
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


def _topup_budget_state(row):
    """Read canonical payment facts; never maintain a second money ledger."""
    attempt, order = row.attempt, row.attempt.order
    if attempt.needs_reconciliation or order.payment_facts.exclude(disposition='accepted').exists():
        return 'review'
    return _effective_attempt_state(attempt,order)


@transaction.atomic
def topup_budget_summary(account, *, starts_at, ends_at, policy_version):
    from .models import TopupBudget
    account = _lock_account(account)
    _datetime(starts_at)
    _datetime(ends_at)
    _identifier(policy_version)
    if ends_at <= starts_at:
        raise ValueError('INVALID_BUDGET_WINDOW')
    rows = TopupBudget.objects.filter(account=account,starts_at=starts_at,ends_at=ends_at,policy_version=policy_version)
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
    result['authorization'] = {'state':auth.state,'version':auth.version,'pause_reason':auth.pause_reason,
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
def prepare_topup_attempt(account, authorization_id, *, trigger_key, starts_at, ends_at, policy_version, expires_at):
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
    if not starts_at <= now < expires_at <= ends_at:
        raise ValueError('INVALID_BUDGET_WINDOW')
    _authorize_purchase(account,auth.actor)
    if (auth.state != 'authorized' or auth.payment_method.state != 'active' or
        not SubscriptionPeriod.objects.filter(subscription__account=account,starts_at__lte=now,ends_at__gt=now).exists()):
        raise ValueError('TOPUP_NOT_AUTHORIZED')
    if CreditFreeze.objects.filter(batch__account=account,batch__source='purchased',state='frozen').exists():
        raise ValueError('REFUND_PENDING')
    rows = TopupBudget.objects.filter(account=account)
    if rows.filter(starts_at__lt=ends_at,ends_at__gt=starts_at).exclude(
            starts_at=starts_at,ends_at=ends_at,policy_version=policy_version).exists():
        raise ValueError('BUDGET_WINDOW_CONFLICT')
    if any(_topup_budget_state(row) not in ('succeeded','failed','cancelled','expired')
           for row in rows.select_related('attempt__order')):
        raise ValueError('TOPUP_PENDING')
    totals = topup_budget_summary(account,starts_at=starts_at,ends_at=ends_at,policy_version=policy_version)
    amount = auth.configuration['unit_amount_minor']
    if totals['spent_minor'] + totals['reserved_minor'] + amount > auth.configuration['monthly_cap_minor']:
        raise ValueError('TOPUP_BUDGET_EXCEEDED')
    channel = auth.mandate['channel']
    config = settings.BILLING_APPROVED_CHANNELS.get(channel,{})
    if 'automatic_topup' not in config.get('capabilities',[]):
        raise ValueError('CONFIG_INCOMPLETE')
    key = f'topup:{account.pk}:{trigger_key}'
    _identifier(key)
    quote = create_quote(account,auth.actor,auth.product_id,1,expires_at,channel=channel,buyer_region='CN')
    order_data = create_order(account,auth.actor,{'quote_id':quote['id'],
        'agreement_acceptance':auth.product.terms_version},key)
    order = _get(Order.objects.select_for_update(),account=account,pk=order_data['id'])
    if (order.amount_minor != amount or _quantity(order.snapshot['credits_quantity']) != _quantity(auth.configuration['credits_quantity']) or
        order.snapshot['merchant_entity'] != auth.mandate['merchant_reference']):
        raise ValueError('TOPUP_CONFIGURATION_CHANGED')
    attempt = PaymentAttempt.objects.create(account=account,order=order,intent_key=key,expires_at=expires_at)
    TopupBudget.objects.create(account=account,authorization=auth,attempt=attempt,trigger_key=trigger_key,
        starts_at=starts_at,ends_at=ends_at,policy_version=policy_version)
    return _attempt_dict(attempt,order)


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


@transaction.atomic
def organization_summary(account, *, offset=0, viewer=None):
    """Admin roster or authenticated viewer's own limits; never returns funds."""
    from fractions import Fraction
    from backend.models import Membership
    from .models import PaidSeat, CreditEntry
    from .billing import _text
    account = _lock_account(account)
    if account.kind != 'business':
        raise ValueError('FORBIDDEN')
    if type(offset) is not int or offset < 0:
        raise ValueError('INVALID_REQUEST')
    if viewer is not None and offset:
        raise ValueError('INVALID_REQUEST')
    now = timezone.now()
    period = SubscriptionPeriod.objects.filter(subscription__account=account,starts_at__lte=now,ends_at__gt=now).select_related('subscription').first()
    subscription = period.subscription if period else Subscription.objects.filter(account=account).order_by('-created_at').first()
    start = end = None
    if period:
        local = now.astimezone(_calendar_zone(subscription.billing_timezone))
        anchor = subscription.original_anchor.astimezone(_calendar_zone(subscription.billing_timezone))
        index = (local.year-anchor.year)*12 + local.month-anchor.month
        if _boundary(subscription,index) > now:
            index -= 1
        start,end = _boundary(subscription,index),_boundary(subscription,index+1)
    seats = PaidSeat.objects.filter(subscription=subscription) if subscription else PaidSeat.objects.none()
    members = Membership.objects.filter(account=account).order_by('id')
    if viewer is not None:
        members = members.filter(user=viewer,is_active=True)
        if not viewer.is_active or not members.exists():
            raise ValueError('FORBIDDEN')
    total = members.count()
    items = []
    for member in members[offset:offset+50]:
        seat = seats.filter(membership=member).first()
        limit = member.shared_limit_changes.order_by('-version').first()
        used,reserved = Fraction(0),Fraction(0)
        if start:
            entries = CreditEntry.objects.filter(batch__account=account,batch__source='purchased',batch__seat__isnull=True,
                reservation__membership=member,reservation__shared_period_start=start).select_related('reservation')
            for entry in entries:
                if entry.kind == 'settle':
                    used += _quantity(entry.quantity)
                elif entry.kind == 'reserve' and entry.reservation.state == 'reserved':
                    reserved += _quantity(entry.quantity)
        items.append({'id':member.pk,'user_id':member.user_id,'role':member.role,'active':member.is_active,
            'seat_position':seat.position if seat else None,'shared_limit':limit.quantity if limit else None,
            'shared_used':_text(used) if start else None,'shared_reserved':_text(reserved) if start else None})
    if viewer is not None:
        return {'account_id':str(account.pk),'member':items[0],
            'subscription_active':bool(period),'period_start':start.isoformat() if start else None,
            'period_end':end.isoformat() if end else None,'can_edit':False}
    return {'account_id':str(account.pk),'members':items,'total_members':total,'offset':offset,
        'next_offset':offset+50 if offset+50<total else None,'seat_count':seats.count(),
        'assigned_seats':seats.filter(membership__isnull=False).count(),'subscription_active':bool(period),
        'period_start':start.isoformat() if start else None,'period_end':end.isoformat() if end else None,
        'can_edit':False}


def organization_history(account, *, offset=0):
    """Read existing audit versions; HTTP applies the existing financial role gate."""
    from django.db.models import F, Value
    from backend.models import LoginIdentity, Membership
    from .models import SeatChange, SharedLimitChange
    if account.kind != 'business':
        raise ValueError('FORBIDDEN')
    if type(offset) is not int or not 0 <= offset <= 999999999:
        raise ValueError('INVALID_REQUEST')
    seats = SeatChange.objects.filter(seat__subscription__account=account)
    limits = SharedLimitChange.objects.filter(membership__account=account)
    keys = seats.annotate(history_type=Value('seat_assignment'),event_id=F('id')).values(
        'history_type','event_id','created_at').union(limits.annotate(
            history_type=Value('shared_limit'),event_id=F('id')).values(
                'history_type','event_id','created_at'),all=True)
    page = list(keys.order_by('-created_at','-history_type','-event_id')[offset:offset+51])
    seat_rows = {row.pk:row for row in seats.filter(pk__in=[item['event_id'] for item in page[:50]
        if item['history_type'] == 'seat_assignment']).select_related('seat')}
    limit_rows = {row.pk:row for row in limits.filter(pk__in=[item['event_id'] for item in page[:50]
        if item['history_type'] == 'shared_limit'])}
    rows = list(seat_rows.values()) + list(limit_rows.values())
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
    seat = _get(PaidSeat.objects.select_for_update(), subscription__account=account, pk=seat_id)
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
        if not seat.subscription.periods.filter(starts_at__lte=now,ends_at__gt=now).exists():
            raise ValueError('SUBSCRIPTION_EXPIRED')
        _get(Membership.objects,pk=membership_id,account=account,is_active=True,user__is_active=True)
        if PaidSeat.objects.filter(subscription=seat.subscription,membership_id=membership_id).exclude(pk=seat.pk).exists():
            raise ValueError('MEMBER_ALREADY_SEATED')
    if membership_id != seat.membership_id and CreditBatch.objects.filter(
        seat=seat,entries__reservation__state='reserved').exists():
        raise ValueError('TASK_SETTLEMENT_REQUIRED')
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
    if not order.paid_at or not order.payment_facts.filter(disposition='accepted',
        amount_minor=order.amount_minor, currency='CNY', channel=transport.get('channel'),
        merchant_reference=order.snapshot.get('merchant_entity')).exists():
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
        if (type(snapshot.get('seat_quantity')) is not int or snapshot['seat_quantity'] < 2 or
            mode not in ('seat_included','shared_only') or (mode == 'shared_only' and quantity != 0)):
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
        not sub.periods.filter(starts_at__lte=now, ends_at__gt=now).exists()):
        raise ValueError('RENEWAL_NOT_AUTHORIZED')
    if sub.mandate['executor'] != 'sage' or mandate['executor'] != 'sage':
        raise ValueError('RENEWAL_EXECUTOR_CONFLICT')
    for channel in {sub.mandate['channel'], mandate['channel']}:
        _sale_gate(sub.initial_order.quote.product, account, now, channel,
                   snapshot['buyer_region'], snapshot['merchant_entity'])
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
    _authorize_purchase(account, initial.created_by)
    _sale_gate(initial.quote.product, account, timezone.now(), snapshot['channel'],
               snapshot['buyer_region'], snapshot['merchant_entity'])
    if (initial.currency != 'CNY' or initial.amount_minor != snapshot.get('amount_minor') or
            initial.amount_minor <= 0):
        raise ValueError('RENEWAL_SNAPSHOT_CONFLICT')
    if Order.objects.filter(account=account, idempotency_key=key).exists():
        raise ValueError('PAYMENT_INTENT_CONFLICT')
    # This is a previously authorized renewal, not a public new purchase.
    # In particular preserve the accepted terms digest, even if the public
    # catalog/terms have since changed; do not reconstruct commercial fields.
    quote = Quote.objects.create(account=account, product=initial.quote.product,
        snapshot=snapshot, amount_minor=initial.amount_minor, currency='CNY', expires_at=expires_at)
    payload = {'quote_id':str(quote.pk),'agreement_acceptance':snapshot['terms_version']}
    fingerprint = hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    order = Order.objects.create(account=account,created_by=initial.created_by,quote=quote,
        idempotency_key=key,request_hash=fingerprint,snapshot=snapshot,
        amount_minor=initial.amount_minor,currency='CNY')
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
               snapshot['buyer_region'], snapshot['merchant_entity'])
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
    if not SubscriptionPeriod.objects.filter(subscription__account=account,starts_at__lte=now,ends_at__gt=now).exists():
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
    current = next((period for period in periods if period.starts_at <= now < period.ends_at), None)
    future = next((period for period in periods if period.starts_at > now), None)
    shown = current or future or (periods[-1] if periods else None)
    next_grant = None
    for period in periods:
        if _quantity(period.snapshot.get('credits_quantity')) == 0:
            continue
        for month in range(_months(subscription)):
            boundary = _boundary(subscription, period.cycle_index * _months(subscription) + month)
            if boundary > now and (next_grant is None or boundary < next_grant):
                next_grant = boundary
    return {
        'id': str(subscription.pk), 'plan_code': shown.snapshot['code'] if shown else '',
        'billing_cycle': subscription.billing_cycle,
        'status': 'active' if current else 'scheduled' if future else 'expired',
        'period_start': shown.starts_at.isoformat() if shown else None,
        'period_end': shown.ends_at.isoformat() if shown else None,
        'next_credit_grant_at': next_grant.isoformat() if next_grant else None,
        'prepaid_through': max((period.ends_at for period in periods), default=None).isoformat() if periods else None,
        'auto_renew': subscription.auto_renew, 'cancellation_state': subscription.cancellation_state,
        'can_cancel': subscription.auto_renew and subscription.cancellation_state == 'none',
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
            if _quantity(period.snapshot['credits_quantity']) > 0:
                seats = list(subscription.seats.order_by('position')) if account.kind == 'business' else [None]
                if account.kind == 'business' and len(seats) != period.snapshot['seat_quantity']:
                    raise ValueError('LEDGER_INCONSISTENT')
                for seat in seats:
                    key = f'subscription:{period.pk}:{index}' + (f':{seat.pk}' if seat else '')
                    result = grant_credits(account, 'subscription', key,
                        period.snapshot['credits_quantity'], _boundary(subscription, offset + 1))
                    batch = CreditBatch.objects.get(pk=result['id'])
                    if batch.subscription_period_id not in (None, period.pk) or batch.service_month not in (None, index):
                        raise ValueError('LEDGER_INCONSISTENT')
                    batch.subscription_period, batch.service_month, batch.seat = period, index, seat
                    batch.save(update_fields=['subscription_period', 'service_month', 'seat'])
                    count += 1
            period.granted_months += 1
            period.save(update_fields=['granted_months'])
    return {'created_batches': count}


@transaction.atomic
def activate_subscription(account, order_id, *, activated_at, billing_timezone, calendar_rule_version, mandate, payment_method_id=None):
    account = _lock_account(account)
    order = _paid_order(account, order_id)
    _datetime(activated_at)
    _calendar_zone(billing_timezone)
    if not isinstance(calendar_rule_version, str) or not calendar_rule_version.strip() or len(calendar_rule_version) > 80:
        raise ValueError('CALENDAR_POLICY_REQUIRED')
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
    if payment_method_id is None and PaymentMethod.objects.filter(account=account,
            channel=mandate['channel'], merchant_reference=mandate['merchant_reference']).exists():
        raise ValueError('PAYMENT_METHOD_BINDING_REQUIRED')
    if payment_method_id is not None:
        method = _get(PaymentMethod.objects.select_for_update(), account=account, pk=payment_method_id)
        if (method.state != 'active' or method.channel != mandate['channel'] or
            method.merchant_reference != mandate['merchant_reference']):
            raise ValueError('PAYMENT_METHOD_NOT_AVAILABLE')
    if activated_at < order.paid_at or activated_at > timezone.now():
        raise ValueError('INVALID_ACTIVATION_TIME')
    if (Subscription.objects.filter(account=account, auto_renew=True).exists() or
        SubscriptionPeriod.objects.filter(subscription__account=account, ends_at__gt=activated_at).exists() or
        SubscriptionPeriod.objects.filter(order=order).exists()):
        raise ValueError('SUBSCRIPTION_CONFLICT')
    subscription = Subscription.objects.create(account=account, initial_order=order,
        original_anchor=activated_at, billing_timezone=billing_timezone, calendar_rule_version=calendar_rule_version,
        billing_cycle=order.snapshot['billing_cycle'], mandate=mandate, payment_method=method)
    if account.kind == 'business':
        from .models import PaidSeat
        PaidSeat.objects.bulk_create([PaidSeat(subscription=subscription,position=position)
                                     for position in range(1,order.snapshot['seat_quantity']+1)])
    SubscriptionPeriod.objects.create(subscription=subscription, order=order, cycle_index=0,
        starts_at=activated_at, ends_at=_boundary(subscription, _months(subscription)), snapshot=order.snapshot)
    grant_due_credits(account)
    order.status = 'fulfilled'
    order.save(update_fields=['status'])
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
    if SubscriptionPeriod.objects.filter(subscription__account=account, starts_at__lt=end, ends_at__gt=start).exists():
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
    if subscription.cancellation_state == 'none':
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
    if (agreement_reference != subscription.mandate.get('agreement_reference') or
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
