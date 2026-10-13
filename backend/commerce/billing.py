"""Transactional billing functions. HTTP/auth and PSP verification live outside here.

No function trusts browser-supplied amounts or declares a PSP payment successful.
record_confirmed_payment and the task ledger functions have no HTTP routes: their
callers must be trusted provider reconciliation / server-side task code.
"""

import calendar
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone as datetime_timezone
from fractions import Fraction

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from backend.models import BillingAccount, Membership
from .models import (CreditBatch, CreditEntry, Order, PaymentFact, ProductVersion, Quote, Reservation,
                     PaymentAttempt, PaymentAttemptEvent)


def _quantity(value, *, positive=False):
    # Exact plain decimal storage is technical representation, not a D03 unit or
    # rounding decision. In particular we never truncate to Decimal's 28 digits.
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("INVALID_CREDITS")
    text = str(value)
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        raise ValueError("INVALID_CREDITS")
    whole, _, fraction = text.partition(".")
    try:
        result = Fraction(int(whole + fraction), 10 ** len(fraction))
    except ValueError as exc:
        raise ValueError("CREDITS_STORAGE_PRECISION_UNSUPPORTED") from exc
    if positive and result == 0:
        raise ValueError("INVALID_CREDITS")
    return result


def _text(value):
    if value < 0:
        raise ValueError("LEDGER_INCONSISTENT")
    denominator, scale = value.denominator, 0
    while denominator != 1:
        denominator //= 2 if denominator % 2 == 0 else 5
        scale += 1
    # scale above is a safe upper bound for factors 2 and 5, not a precision cap.
    integer = value.numerator * (10 ** scale // value.denominator)
    digits = str(integer).rjust(scale + 1, "0")
    return (digits[:-scale] + "." + digits[-scale:]).rstrip("0").rstrip(".") if scale else digits


def _datetime(value):
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise ValueError("INVALID_DATETIME")
    return value


def _identifier(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("INVALID_IDENTIFIER")
    return value


def _get(queryset, **kwargs):
    try:
        return queryset.get(**kwargs)
    except (queryset.model.DoesNotExist, ValidationError, ValueError) as exc:
        raise ValueError("NOT_FOUND") from exc


def _lock_account(account):
    return _get(BillingAccount.objects.select_for_update(), pk=account.pk)


def _authorize_purchase(account, user):
    if not user or not user.is_authenticated or not user.is_active:
        raise ValueError("FORBIDDEN")
    if account.kind == "personal" and account.personal_owner_id == user.pk:
        return
    if account.kind == "business" and Membership.objects.filter(
        account=account, user=user, role__in=["owner", "billing_admin"], is_active=True,
    ).exists():
        return
    raise ValueError("FORBIDDEN")


def _sale_gate(product, account, now, channel, buyer_region, merchant_entity=None, *, public_purchase=False):
    if not getattr(settings, "BILLING_SALES_ENABLED", False) or not getattr(settings, "BILLING_APPROVED_CHANNELS", {}):
        raise ValueError("CONFIG_INCOMPLETE")
    if (product.state != "published" or product.currency != "CNY"
            or type(product.unit_amount_minor) is not int or product.unit_amount_minor <= 0
            or not isinstance(product.entitlements, dict) or not product.entitlements or not product.entitlement_version
            or not product.credit_rule_version or not product.terms_version
            or not product.effective_at or product.effective_at > now
            or product.audience != account.kind or product.kind not in {"credits", "subscription"}
            or product.minimum_seats < (2 if account.kind == "business" and product.kind == "subscription" else 1)):
        raise ValueError("CONFIG_INCOMPLETE")
    if product.kind == "subscription" and product.billing_cycle not in {"monthly", "annual"}:
        raise ValueError("CONFIG_INCOMPLETE")
    if product.kind == "credits" and product.billing_cycle:
        raise ValueError("CONFIG_INCOMPLETE")
    if public_purchase:
        # Public quote/order amounts are JSON numbers consumed by browser clients.
        if product.unit_amount_minor > 9007199254740991:
            raise ValueError('CONFIG_INCOMPLETE')
        from .models import Subscription, SubscriptionPeriod
        periods = SubscriptionPeriod.objects.filter(subscription__account=account)
        if product.kind == 'subscription':
            # A public new purchase is not an upgrade, conversion or recovery.
            # Recheck under the account lock before accepting a new order.
            if (Subscription.objects.filter(account=account, auto_renew=True).exists() or
                    periods.filter(ends_at__gt=now).exists() or
                    Order.objects.filter(account=account,snapshot__kind='subscription')
                        .exclude(status__in=['fulfilled','closed']).exists() or
                    PaymentFact.objects.filter(order__account=account,order__snapshot__kind='subscription')
                        .exclude(disposition='accepted').exists()):
                raise ValueError('CONFIG_INCOMPLETE')
        elif not periods.filter(starts_at__lte=now,ends_at__gt=now).exists():
            # D10 does not permit inferring new purchases from retained balances.
            raise ValueError('CONFIG_INCOMPLETE')
    try:
        quantity = _quantity(product.credits_quantity, positive=product.kind == "credits")
    except ValueError as exc:
        raise ValueError("CONFIG_INCOMPLETE") from exc
    if product.kind == 'subscription' and account.kind == 'business':
        mode = product.entitlements.get('resource_mode')
        if mode not in ('seat_included', 'shared_only') or (mode == 'shared_only' and quantity != 0):
            raise ValueError('CONFIG_INCOMPLETE')
    channels = settings.BILLING_APPROVED_CHANNELS
    configuration = channels.get(channel) if isinstance(channels, dict) and isinstance(channel, str) else None
    capability = f"recurring_{product.billing_cycle}" if product.kind == "subscription" else "one_time"
    regions = configuration.get("regions") if isinstance(configuration, dict) else None
    capabilities = configuration.get("capabilities") if isinstance(configuration, dict) else None
    if (not isinstance(configuration, dict) or configuration.get("enabled") is not True
            or configuration.get("currency") != "CNY" or not configuration.get("merchant_entity")
            or not isinstance(regions, list) or not isinstance(capabilities, list)
            or not isinstance(buyer_region, str) or buyer_region not in regions
            or capability not in capabilities
            or (merchant_entity is not None and merchant_entity != configuration["merchant_entity"])):
        raise ValueError("CONFIG_INCOMPLETE")
    return configuration["merchant_entity"]


@transaction.atomic
def create_quote(account, user, product_id, seat_quantity, expires_at, *, channel, buyer_region, terms_digest=None):
    """Trusted catalog caller supplies expiry; no invented D03 quote duration."""
    account = _lock_account(account)
    _authorize_purchase(account, user)
    now = timezone.now()
    product = _get(ProductVersion.objects.select_for_update(), pk=product_id)
    merchant_entity = _sale_gate(product, account, now, channel, buyer_region,
                                public_purchase=terms_digest is not None)
    if type(seat_quantity) is not int or seat_quantity < product.minimum_seats:
        raise ValueError("INVALID_SEATS")
    if (account.kind == "personal" or product.kind == "credits") and seat_quantity != 1:
        raise ValueError("INVALID_SEATS")
    if _datetime(expires_at) <= now:
        raise ValueError("QUOTE_EXPIRED")
    amount = product.unit_amount_minor * seat_quantity
    if terms_digest is not None and amount > 9007199254740991:
        raise ValueError('CONFIG_INCOMPLETE')
    if amount > 9223372036854775807:
        raise ValueError("AMOUNT_STORAGE_OVERFLOW")
    snapshot = {
        "product_id": str(product.pk), "code": product.code, "version": product.version,
        "kind": product.kind, "audience": product.audience, "billing_cycle": product.billing_cycle,
        "seat_quantity": seat_quantity, "currency": product.currency,
        "unit_amount_minor": product.unit_amount_minor, "amount_minor": amount,
        "credits_quantity": _text(_quantity(product.credits_quantity)),
        "entitlements": product.entitlements, "entitlement_version": product.entitlement_version,
        "credit_rule_version": product.credit_rule_version, "terms_version": product.terms_version,
        "channel": channel, "buyer_region": buyer_region, "merchant_entity": merchant_entity,
    }
    if terms_digest is not None:
        if not isinstance(terms_digest, str) or not re.fullmatch(r'[a-f0-9]{64}', terms_digest):
            raise ValueError('TERMS_UNAVAILABLE')
        snapshot['terms_document_digest'] = terms_digest
    quote = Quote.objects.create(account=account, product=product, snapshot=snapshot, amount_minor=amount, currency="CNY", expires_at=expires_at)
    return {"id": str(quote.pk), "amount_minor": amount, "currency": "CNY", "expires_at": expires_at.isoformat()}


def _order_dict(order):
    return {
        "id": str(order.pk), "account_id": str(order.account_id), "status": order.status,
        "amount_minor": order.amount_minor, "currency": order.currency,
        "snapshot": order.snapshot, "created_at": order.created_at.isoformat(),
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "plan_code": order.snapshot.get("code"), "billing_cycle": order.snapshot.get("billing_cycle"),
        "seat_quantity": order.snapshot.get("seat_quantity"),
    }


def purchase_terms(version):
    documents = settings.BILLING_TERMS_DOCUMENTS
    item = documents.get(version) if isinstance(documents, dict) and isinstance(version, str) else None
    if not isinstance(item, dict) or item.get('approved') is not True:
        raise ValueError('TERMS_UNAVAILABLE')
    document = {'version': version}
    for language in ('zh', 'en'):
        content = item.get(language)
        if (not isinstance(content, dict) or not isinstance(content.get('title'), str) or
                not content['title'].strip() or not isinstance(content.get('paragraphs'), list) or
                not content['paragraphs'] or any(not isinstance(text, str) or not text.strip()
                                                for text in content['paragraphs'])):
            raise ValueError('TERMS_UNAVAILABLE')
        document[language] = {'title': content['title'], 'paragraphs': content['paragraphs']}
    document['digest'] = hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False,
                                                  separators=(',', ':')).encode()).hexdigest()
    return document


def purchase_benefits(product):
    benefits = product.entitlements.get('benefits') if isinstance(product.entitlements, dict) else None
    if (not isinstance(benefits, dict) or any(not isinstance(benefits.get(language), list) or
            not benefits[language] or any(not isinstance(text, str) or not text.strip()
                                        for text in benefits[language]) for language in ('zh', 'en'))):
        raise ValueError('CONFIG_INCOMPLETE')
    return benefits


def purchase_catalog(account):
    """Approved product versions only; reference prices remain a separate catalog."""
    now, seen, items = timezone.now(), set(), []
    for product in ProductVersion.objects.filter(audience=account.kind, state='published',
            effective_at__lte=now).order_by('code', 'billing_cycle', '-version'):
        key = (product.code, product.billing_cycle)
        if key in seen:
            continue
        seen.add(key)
        options, reason = [], 'CONFIG_INCOMPLETE'
        ttl = settings.BILLING_QUOTE_TTL_SECONDS
        try:
            if product.kind == 'credits' and not settings.BILLING_CREDITS_PURCHASE_ENABLED:
                raise ValueError('CONFIG_INCOMPLETE')
            purchase_benefits(product)
            document = purchase_terms(product.terms_version)
            if type(ttl) is not int or ttl <= 0:
                raise ValueError('CONFIG_INCOMPLETE')
            try:
                now + timedelta(seconds=ttl)
            except OverflowError as exc:
                raise ValueError('CONFIG_INCOMPLETE') from exc
            channels = settings.BILLING_APPROVED_CHANNELS
            if isinstance(channels, dict):
                for channel, configuration in channels.items():
                    regions = configuration.get('regions') if isinstance(configuration, dict) else None
                    if not isinstance(channel, str) or not isinstance(regions, list):
                        continue
                    for region in regions:
                        try:
                            _sale_gate(product, account, now, channel, region, public_purchase=True)
                        except ValueError:
                            continue
                        options.append({'channel': channel, 'buyer_region': region})
            reason = None if options else 'CONFIG_INCOMPLETE'
        except ValueError as exc:
            reason = str(exc)
            document = None
        items.append({'id': str(product.pk), 'code': product.code, 'version': product.version,
            'kind': product.kind, 'audience': product.audience, 'billing_cycle': product.billing_cycle,
            'currency': product.currency, 'unit_amount_minor': str(product.unit_amount_minor) if product.unit_amount_minor is not None else None,
            'minimum_seats': product.minimum_seats, 'credits_quantity': product.credits_quantity,
            'entitlements': product.entitlements, 'terms_version': product.terms_version,
            'quote_available': bool(options) and reason is None, 'unavailable_reason': reason,
            'purchase_options': options, 'terms': document})
    return {'account_id': str(account.pk), 'items': items, 'currency': 'CNY'}


def purchase_quote_detail(account, quote_id):
    """Owned confirmation snapshot; exclude merchant routing identifiers."""
    quote = _get(Quote.objects.select_related('product'), account=account, pk=quote_id)
    order = Order.objects.filter(quote=quote).first()
    reason = 'QUOTE_ALREADY_ORDERED' if order else 'QUOTE_EXPIRED' if quote.expires_at <= timezone.now() else None
    document = None
    if quote.snapshot.get('terms_document_digest'):
        try:
            document = purchase_terms(quote.snapshot.get('terms_version'))
            if document['digest'] != quote.snapshot['terms_document_digest']:
                reason = reason or 'TERMS_MISMATCH'
                document = None
        except ValueError:
            reason = reason or 'TERMS_UNAVAILABLE'
    if reason is None:
        try:
            if quote.product.kind == 'credits' and not settings.BILLING_CREDITS_PURCHASE_ENABLED:
                raise ValueError('CONFIG_INCOMPLETE')
            _sale_gate(quote.product, account, timezone.now(), quote.snapshot.get('channel'),
                       quote.snapshot.get('buyer_region'), quote.snapshot.get('merchant_entity'),
                       public_purchase=bool(quote.snapshot.get('terms_document_digest')))
        except ValueError as exc:
            if str(exc) != 'CONFIG_INCOMPLETE':
                raise
            reason = 'CONFIG_INCOMPLETE'
    return {'id': str(quote.pk), 'account_id': str(account.pk),
            'amount_minor': quote.amount_minor, 'currency': quote.currency,
            'expires_at': quote.expires_at.isoformat(),
            'snapshot': {key: value for key, value in quote.snapshot.items() if key != 'merchant_entity'},
            'can_order': reason is None, 'unavailable_reason': reason,
            'order_id': str(order.pk) if order else None, 'terms': document}


@transaction.atomic
def create_order(account, user, payload, idempotency_key):
    account = _lock_account(account)
    _authorize_purchase(account, user)
    _identifier(idempotency_key)
    if not isinstance(payload, dict) or set(payload) != {"quote_id", "agreement_acceptance"} or not all(isinstance(value, str) for value in payload.values()):
        raise ValueError("INVALID_PAYLOAD")
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    existing = Order.objects.filter(account=account, idempotency_key=idempotency_key).first()
    if existing:
        if existing.request_hash != fingerprint:
            raise ValueError("IDEMPOTENCY_CONFLICT")
        return _order_dict(existing)
    quote = _get(Quote.objects.select_for_update().select_related("product"), account=account, pk=payload["quote_id"])
    if Order.objects.filter(account=account, quote=quote).exists():
        raise ValueError("QUOTE_ALREADY_ORDERED")
    _sale_gate(quote.product, account, timezone.now(), quote.snapshot.get("channel"), quote.snapshot.get("buyer_region"), quote.snapshot.get("merchant_entity"),
               public_purchase=bool(quote.snapshot.get('terms_document_digest')))
    if quote.expires_at <= timezone.now():
        raise ValueError("QUOTE_EXPIRED")
    if payload["agreement_acceptance"] != quote.snapshot.get("terms_version"):
        raise ValueError("TERMS_MISMATCH")
    if quote.snapshot.get('terms_document_digest'):
        document = purchase_terms(quote.snapshot['terms_version'])
        if document['digest'] != quote.snapshot['terms_document_digest']:
            raise ValueError('TERMS_MISMATCH')
    if quote.currency != "CNY" or quote.amount_minor != quote.snapshot.get("amount_minor") or quote.amount_minor <= 0:
        raise ValueError("CONFIG_INCOMPLETE")
    order = Order.objects.create(account=account, created_by=user, quote=quote, idempotency_key=idempotency_key, request_hash=fingerprint, snapshot=quote.snapshot, amount_minor=quote.amount_minor, currency=quote.currency)
    return _order_dict(order)


def list_orders(account):
    return orders_page(account)['items']


def orders_page(account, offset=0):
    rows = list(Order.objects.filter(account=account).order_by('-created_at', '-id')[offset:offset+51])
    return {'account_id': str(account.pk), 'items': [_order_dict(order) for order in rows[:50]],
            'next_offset': offset+50 if len(rows) > 50 else None}


def credit_transactions_page(account, offset=0):
    """Read persisted Credits movements; no synthetic expiry or running balance."""
    if type(offset) is not int or not 0 <= offset <= 999999999:
        raise ValueError('INVALID_REQUEST')
    query = CreditEntry.objects.filter(batch__account=account)
    rows = list(query.select_related('batch__order','batch__seat__subscription','reservation')
        .order_by('-id')[offset:offset+51])
    items = []
    kinds = ('grant','reserve','settle','release','freeze','unfreeze','refund')
    for row in rows[:50]:
        batch = row.batch
        if ((row.reservation_id and row.reservation.account_id != account.pk) or
            (batch.order_id and batch.order.account_id != account.pk) or
            (batch.seat_id and batch.seat.subscription.account_id != account.pk)):
            raise ValueError('LEDGER_INCONSISTENT')
        known = row.kind in kinds
        items.append({'id':str(row.pk),'kind':row.kind if known else 'unknown',
            'quantity':_text(_quantity(row.quantity)) if known else None,
            'batch_id':str(batch.pk),'source':batch.source if batch.source in ('subscription','purchased') else 'unknown',
            'order_id':str(batch.order_id) if batch.order_id else None,
            'reservation_id':str(row.reservation_id) if row.reservation_id else None,
            'seat_id':str(batch.seat_id) if batch.seat_id else None,'occurred_at':row.created_at.isoformat()})
    return {'account_id':str(account.pk),'items':items,'offset':offset,
            'next_offset':offset+50 if len(rows)>50 else None,'total':query.count()}


def notifications_page(account, offset=0):
    """Financial-account facts, never arbitrary channel text or credentials."""
    from .models import BillingNotification
    if type(offset) is not int or not 0 <= offset <= 999999999:
        raise ValueError('INVALID_REQUEST')
    query = BillingNotification.objects.filter(account=account)
    rows = list(query.order_by('-occurred_at','-id')[offset:offset+51])
    items = []
    for row in rows[:50]:
        known = row.type in BillingNotification.TYPES
        items.append({'id':str(row.pk),'type':row.type if known else 'unknown',
            'order_id':str(row.order_id) if known and row.order_id else None,
            'subscription_id':str(row.subscription_id) if known and row.subscription_id else None,
            'refund_id':str(row.refund_id) if known and row.refund_id else None,
            'amount_minor':str(row.amount_minor) if known and row.currency == 'CNY' and row.amount_minor is not None else None,
            'currency':'CNY' if known and row.currency == 'CNY' and row.amount_minor is not None else None,
            'occurred_at':row.occurred_at.isoformat()})
    return {'account_id':str(account.pk),'items':items,'offset':offset,
            'next_offset':offset+50 if len(rows)>50 else None,'total':query.count()}


@transaction.atomic
def order_detail(account, order_id):
    from .models import CreditRefund
    account = _lock_account(account)
    order = _get(Order.objects.filter(account=account), pk=order_id)
    review_states = {'duplicate_payment_review', 'identity_mismatch_review',
                     'amount_mismatch_review', 'late_payment_review'}
    payments = [{"id": str(fact.pk), "status": fact.disposition if fact.disposition in review_states | {'accepted'} else 'payment_review_required', "amount_minor": fact.amount_minor,
                 "currency": fact.currency, "paid_at": fact.paid_at.isoformat()}
                for fact in order.payment_facts.all().order_by("created_at", "id")]
    delivery = "completed" if order.status == "fulfilled" else "pending" if order.paid_at else "not_started"
    attempt = PaymentAttempt.objects.filter(order=order).first()
    attempt_data = None
    if attempt:
        attempt_data = {'status': _effective_attempt_state(attempt, order),
                        'needs_reconciliation': attempt.needs_reconciliation,
                        'expires_at': attempt.expires_at.isoformat()}
    review_reasons = {payment['status'] for payment in payments if payment['status'] != 'accepted'}
    if attempt and attempt.needs_reconciliation:
        review_reasons.add('payment_reconciliation_required')
    refunds = [{**_credit_refund_dict(item),'created_at':item.created_at.isoformat(),
                'resolved_at':item.resolved_at.isoformat() if item.resolved_at else None}
               for item in CreditRefund.objects.filter(payment__order=order).order_by('created_at','id')]
    return {"order": _order_dict(order), "payments": payments, "delivery": {"status": delivery},
            "payment_attempt": attempt_data, "refunds":refunds,
            "needs_review": bool(review_reasons), "review_reasons": sorted(review_reasons)}


@transaction.atomic
def credit_refund_assessment(account, order_id):
    """Read-only calculation, not a refund quote, approval or money movement.

    Assess the complete unused paid Credits batch or the unstarted complete
    service months of an unchanged original annual order. Annual scope has no
    amount; rounding/invoice/contract policies remain separate in both cases.
    """
    account = _lock_account(account)
    order = _get(Order.objects.select_for_update(),account=account,pk=order_id)
    result = {'order_id':str(order.pk),'account_id':str(account.pk),'can_submit':False,
        'status':'unavailable','reason':'unsupported_order','currency':'CNY'}
    if not isinstance(order.snapshot,dict):
        return result
    annual = order.snapshot.get('kind') == 'subscription' and order.snapshot.get('billing_cycle') == 'annual'
    if order.snapshot.get('kind') != 'credits' and not annual:
        return result
    if order.status != 'fulfilled' or not order.paid_at:
        return {**result,'reason':'payment_not_fulfilled'}
    facts = list(order.payment_facts.all())
    try:
        transport = _payment_snapshot(order)
    except ValueError:
        return {**result,'reason':'payment_review_required'}
    if (len(facts) != 1 or facts[0].disposition != 'accepted' or facts[0].amount_minor != order.amount_minor or
        facts[0].currency != 'CNY' or facts[0].channel != transport.get('channel') or
        facts[0].merchant_reference != order.snapshot.get('merchant_entity') or
        PaymentAttempt.objects.filter(order=order,needs_reconciliation=True).exists()):
        return {**result,'reason':'payment_review_required'}
    if annual:
        from .models import SubscriptionPeriod, CreditRefund
        from .subscriptions import _boundary, _paid_order
        unavailable = {**result,'reason':'annual_source_review_required'}
        periods = list(SubscriptionPeriod.objects.filter(order=order).select_related('subscription'))
        if len(periods) != 1:
            return unavailable
        period = periods[0]
        subscription = period.subscription
        snapshot = order.snapshot
        required = {'product_id','code','version','kind','audience','billing_cycle','seat_quantity',
            'currency','unit_amount_minor','amount_minor','credits_quantity','entitlements',
            'entitlement_version','credit_rule_version','terms_version','channel','buyer_region','merchant_entity'}
        if (not required <= snapshot.keys() or set(snapshot) - required - {'terms_document_digest'} or
            ('terms_document_digest' in snapshot and (not isinstance(snapshot['terms_document_digest'],str) or
             not re.fullmatch(r'[a-f0-9]{64}',snapshot['terms_document_digest']))) or
            subscription.account_id != account.pk or subscription.initial_order_id != order.pk or
            subscription.billing_cycle != 'annual' or subscription.periods.count() != 1 or period.cycle_index != 0 or
            snapshot != period.snapshot or snapshot != order.quote.snapshot or order.quote.account_id != account.pk or
            snapshot['product_id'] != str(order.quote.product_id) or snapshot['currency'] != 'CNY' or
            order.currency != 'CNY' or order.quote.currency != 'CNY' or
            type(snapshot['amount_minor']) is not int or snapshot['amount_minor'] != order.amount_minor or
            order.quote.amount_minor != order.amount_minor or order.amount_minor <= 0 or
            type(snapshot['unit_amount_minor']) is not int or snapshot['unit_amount_minor'] <= 0 or
            type(snapshot['seat_quantity']) is not int or
            snapshot['unit_amount_minor'] * snapshot['seat_quantity'] != order.amount_minor or
            CreditRefund.objects.filter(payment__order=order).exists()):
            return unavailable
        try:
            _paid_order(account,order.pk)
            product = order.quote.product
            if (any(snapshot[key] != getattr(product,key) for key in (
                'code','version','kind','audience','billing_cycle','currency','unit_amount_minor',
                'entitlements','entitlement_version','credit_rule_version','terms_version')) or
                _quantity(snapshot['credits_quantity']) != _quantity(product.credits_quantity)):
                return unavailable
            boundaries = [_boundary(subscription,i) for i in range(13)]
        except (ValueError, TypeError, OverflowError):
            return unavailable
        if (period.starts_at != boundaries[0] or period.ends_at != boundaries[12] or
            facts[0].paid_at > period.starts_at or not subscription.calendar_rule_version):
            return unavailable
        now = timezone.now()
        if now < boundaries[0]:
            return {**result,'reason':'annual_service_not_started'}
        if now >= boundaries[12]:
            return {**result,'reason':'annual_service_ended'}
        current = next(i for i in range(12) if boundaries[i] <= now < boundaries[i+1])
        intervals = [{'starts_at':boundaries[i].isoformat(),'ends_at':boundaries[i+1].isoformat()} for i in range(12)]
        future = intervals[current+1:]
        return {**result,'status':'scope_ready','scope':'annual_future_service_months',
            'reason':'annual_refund_policy_review_required' if future else 'annual_no_future_months',
            'service_period':{'starts_at':boundaries[0].isoformat(),'ends_at':boundaries[12].isoformat()},
            'current_service_month':intervals[current], 'future_service_months':future,'future_month_count':len(future)}
    batches = list(CreditBatch.objects.filter(account=account,order=order))
    if (len(batches) != 1 or batches[0].source != 'purchased' or
        _quantity(batches[0].initial_quantity) != _quantity(order.snapshot.get('credits_quantity'))):
        return {**result,'reason':'ledger_review_required'}
    batch = batches[0]
    if batch.expires_at <= timezone.now():
        return {**result,'reason':'credits_expired'}
    balance = _balances(batch)
    if balance['reserved']:
        return {**result,'reason':'task_settlement_required'}
    if balance['frozen'] or balance['refunded']:
        return {**result,'reason':'refund_review_required'}
    if not balance['available']:
        return {**result,'reason':'no_unused_credits'}
    purchased = _quantity(batch.initial_quantity,positive=True)
    exact = Fraction(facts[0].amount_minor)*balance['available']/purchased
    return {**result,'status':'assessment_ready','reason':'policy_review_required' if exact.denominator == 1 else 'rounding_policy_pending',
        'scope':'all_unused_paid_credits','unused_quantity':_text(balance['available']),
        'purchased_quantity':_text(purchased),'paid_minor':str(facts[0].amount_minor),
        'reference_minor':str(exact.numerator) if exact.denominator == 1 else None,
        'unrounded_minor':{'numerator':str(exact.numerator),'denominator':str(exact.denominator)}}


def _balances(batch):
    values = {key: Fraction(0) for key in ('available', 'reserved', 'consumed', 'frozen', 'refunded')}
    granted = Fraction(0)
    for entry in batch.entries.all().order_by("id"):
        quantity = _quantity(entry.quantity, positive=True)
        if entry.kind == "grant":
            values["available"] += quantity
            granted += quantity
        elif entry.kind == "reserve":
            values["available"] -= quantity
            values["reserved"] += quantity
        elif entry.kind == "settle":
            values["reserved"] -= quantity
            values["consumed"] += quantity
        elif entry.kind == "release":
            values["reserved"] -= quantity
            values["available"] += quantity
        elif entry.kind == 'freeze':
            values['available'] -= quantity
            values['frozen'] += quantity
        elif entry.kind == 'unfreeze':
            values['frozen'] -= quantity
            values['available'] += quantity
        elif entry.kind == 'refund':
            values['frozen'] -= quantity
            values['refunded'] += quantity
        else:
            raise ValueError("LEDGER_INCONSISTENT")
        if min(values.values()) < 0:
            raise ValueError("LEDGER_INCONSISTENT")
    if granted != _quantity(batch.initial_quantity) or sum(values.values()) != granted:
        raise ValueError("LEDGER_INCONSISTENT")
    return values


@transaction.atomic
def credits_summary(account):
    account = _lock_account(account)
    totals = {key: Fraction(0) for key in ("available", "reserved", "consumed", "expired", "frozen", "refunded")}
    batches = []
    now = timezone.now()
    for batch in CreditBatch.objects.filter(account=account).order_by("expires_at", "id"):
        balance = _balances(batch)
        balance["expired"] = balance["available"] if batch.expires_at <= now else Fraction(0)
        if batch.expires_at <= now:
            balance["available"] = Fraction(0)
        for key, value in balance.items():
            totals[key] += value
        batches.append({"id": str(batch.pk), "source": batch.source, "expires_at": batch.expires_at.isoformat(), **{key: _text(value) for key, value in balance.items()}})
    return {**{key: _text(value) for key, value in totals.items()}, "batches": batches, "refunds_available": False}


@transaction.atomic
def freeze_credits(account, batch_id, request_key, quantity):
    """Trusted refund orchestration only; quote/eligibility must precede this.

    One request can allocate several batches in an outer atomic transaction.
    No public caller may choose arbitrary quantities or bypass refund rules.
    """
    from .models import CreditFreeze
    account = _lock_account(account)
    batch = _get(CreditBatch.objects.select_for_update(), account=account, pk=batch_id)
    _identifier(request_key)
    amount = _quantity(quantity, positive=True)
    existing = CreditFreeze.objects.filter(batch=batch, request_key=request_key).first()
    if existing:
        if _quantity(existing.quantity) != amount:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return {'id': str(existing.pk), 'state': existing.state, 'quantity': existing.quantity}
    if batch.expires_at <= timezone.now():
        raise ValueError('CREDITS_EXPIRED')
    balance = _balances(batch)
    if balance['reserved']:
        raise ValueError('TASK_SETTLEMENT_REQUIRED')
    if amount > balance['available']:
        raise ValueError('INSUFFICIENT_CREDITS')
    hold = CreditFreeze.objects.create(batch=batch, request_key=request_key, quantity=_text(amount))
    CreditEntry.objects.create(batch=batch, kind='freeze', quantity=hold.quantity, event_key=f'freeze:{hold.pk}')
    if account.kind == 'business' and batch.source == 'purchased':
        from .subscriptions import pause_topup_authorization
        pause_topup_authorization(account, reason='credits_refund')
    return {'id': str(hold.pk), 'state': hold.state, 'quantity': hold.quantity}


@transaction.atomic
def resolve_credit_freeze(account, freeze_id, outcome, *, evidence_hash):
    from .models import CreditRefund, CreditFreeze
    account = _lock_account(account)
    _get(CreditFreeze.objects.all(),pk=freeze_id,batch__account=account)
    if CreditRefund.objects.filter(freeze_id=freeze_id).exists():
        raise ValueError('REFUND_FUNDS_RESOLUTION_REQUIRED')
    return _resolve_credit_freeze(account,freeze_id,outcome,evidence_hash=evidence_hash)


def _resolve_credit_freeze(account, freeze_id, outcome, *, evidence_hash):
    """Trusted confirmed refund success OR confirmed-unpaid terminated request.

    Failed/unknown channel outcomes never release the freeze. A digest is an
    audit reference, not channel verification; no HTTP writer is exposed.
    """
    from .models import CreditFreeze
    account = _lock_account(account)
    hold = _get(CreditFreeze.objects.select_for_update().select_related('batch'), batch__account=account, pk=freeze_id)
    if outcome not in ('released', 'refunded') or not isinstance(evidence_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', evidence_hash):
        raise ValueError('REFUND_EVIDENCE_REQUIRED')
    if hold.state != 'frozen':
        if hold.state != outcome or hold.evidence_hash != evidence_hash:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return {'id': str(hold.pk), 'state': hold.state, 'quantity': hold.quantity}
    _balances(hold.batch)
    CreditEntry.objects.create(batch=hold.batch, kind='refund' if outcome == 'refunded' else 'unfreeze',
                               quantity=hold.quantity, event_key=f'resolve-freeze:{hold.pk}')
    hold.state, hold.evidence_hash = outcome, evidence_hash
    hold.save(update_fields=['state', 'evidence_hash'])
    _balances(hold.batch)
    return {'id': str(hold.pk), 'state': hold.state, 'quantity': hold.quantity}


def _credit_refund_dict(item):
    return {'id':str(item.pk),'state':item.state,'amount_minor':str(item.amount_minor),'currency':'CNY'}


@transaction.atomic
def reserve_credit_refund(account, order_id, request_key, *, review_evidence_hash):
    """Trusted finance-reviewed allocation only. No HTTP or channel dispatch.

    Caller must have completed invoice/contract/exception review. A hash records
    that evidence; it does not perform the review or grant a user permission.
    Non-integer minor amounts remain blocked until rounding is approved.
    """
    from .models import CreditRefund
    account = _lock_account(account)
    order = _get(Order.objects.select_for_update(),account=account,pk=order_id)
    _identifier(request_key)
    if not isinstance(review_evidence_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',review_evidence_hash):
        raise ValueError('REFUND_REVIEW_REQUIRED')
    existing = CreditRefund.objects.filter(payment__order=order,request_key=request_key).first()
    if existing:
        if existing.review_evidence_hash != review_evidence_hash:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return _credit_refund_dict(existing)
    assessment = credit_refund_assessment(account,order.pk)
    if assessment['status'] != 'assessment_ready':
        raise ValueError('REFUND_NOT_ELIGIBLE')
    if assessment['reference_minor'] is None:
        raise ValueError('ROUNDING_POLICY_REQUIRED')
    amount = int(assessment['reference_minor'])
    payment = order.payment_facts.get(disposition='accepted')
    committed = sum(item.amount_minor for item in CreditRefund.objects.filter(payment=payment).exclude(state='released'))
    if amount <= 0 or committed + amount > payment.amount_minor:
        raise ValueError('REFUND_EXCEEDS_PAYMENT')
    batch = CreditBatch.objects.get(account=account,order=order)
    hold = freeze_credits(account,batch.pk,'money-refund:'+hashlib.sha256(request_key.encode()).hexdigest(),assessment['unused_quantity'])
    item = CreditRefund.objects.create(payment=payment,freeze_id=hold['id'],request_key=request_key,
        amount_minor=amount,review_evidence_hash=review_evidence_hash)
    from .models import BillingNotification
    BillingNotification.objects.create(account=account,type='credit_refund_reserved',
        source_event=f'refund:{item.pk}:reserved',order=order,refund=item,amount_minor=item.amount_minor,
        currency=payment.currency,occurred_at=item.created_at)
    return _credit_refund_dict(item)


@transaction.atomic
def resolve_credit_refund(account, refund_id, outcome, *, evidence_hash):
    """Trusted verified success or confirmed-unpaid termination; not PSP verification."""
    from .models import CreditRefund
    account = _lock_account(account)
    item = _get(CreditRefund.objects.select_for_update(),payment__order__account=account,pk=refund_id)
    if outcome not in ('refunded','released') or not isinstance(evidence_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',evidence_hash):
        raise ValueError('REFUND_EVIDENCE_REQUIRED')
    if item.state != 'reserved':
        if item.state != outcome or item.evidence_hash != evidence_hash:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return _credit_refund_dict(item)
    _resolve_credit_freeze(account,item.freeze_id,outcome,evidence_hash=evidence_hash)
    item.state,item.evidence_hash,item.resolved_at = outcome,evidence_hash,timezone.now()
    item.save(update_fields=['state','evidence_hash','resolved_at'])
    from .models import BillingNotification
    BillingNotification.objects.create(account=account,type='credit_refund_'+outcome,
        source_event=f'refund:{item.pk}:{outcome}',order=item.payment.order,refund=item,
        amount_minor=item.amount_minor,currency=item.payment.currency,occurred_at=item.resolved_at)
    return _credit_refund_dict(item)


def account_summary(account):
    return {"credits": credits_summary(account), "orders": list_orders(account), "capabilities": {"subscription": True, "invoices": False, "payment_methods": False, "refunds": False}}


@transaction.atomic
def check_billing_integrity(account):
    """Internal read-only consistency report, not channel/settlement reconciliation."""
    from .models import CreditRefund
    account = _lock_account(account)
    findings = []
    def report(code, kind, pk):
        findings.append({'code':code,'object_type':kind,'object_id':str(pk)})
    for order in Order.objects.filter(account=account).order_by('id'):
        if order.paid_at and order.status != 'fulfilled':
            report('PAID_DELIVERY_PENDING','order',order.pk)
        for fact in order.payment_facts.all():
            if fact.disposition != 'accepted':
                report('PAYMENT_REVIEW_REQUIRED','payment',fact.pk)
            allocated = sum(item.amount_minor for item in CreditRefund.objects.filter(payment=fact).exclude(state='released'))
            if allocated > fact.amount_minor:
                report('REFUND_EXCEEDS_PAYMENT','payment',fact.pk)
    for attempt in PaymentAttempt.objects.filter(account=account,needs_reconciliation=True).order_by('id'):
        report('PAYMENT_RECONCILIATION_REQUIRED','payment_attempt',attempt.pk)
    for batch in CreditBatch.objects.filter(account=account).order_by('id'):
        try:
            _balances(batch)
        except ValueError as exc:
            if str(exc) not in ('LEDGER_INCONSISTENT','INVALID_CREDITS','CREDITS_STORAGE_PRECISION_UNSUPPORTED'):
                raise
            report('CREDIT_LEDGER_INCONSISTENT','credit_batch',batch.pk)
    expected = {'reserved':'frozen','released':'released','refunded':'refunded'}
    for item in CreditRefund.objects.filter(payment__order__account=account).select_related('freeze__batch','payment').order_by('id'):
        hold = item.freeze
        frozen_entry = CreditEntry.objects.filter(batch=hold.batch,event_key=f'freeze:{hold.pk}',kind='freeze',quantity=hold.quantity).exists()
        resolution = CreditEntry.objects.filter(batch=hold.batch,event_key=f'resolve-freeze:{hold.pk}')
        resolved_entry = (not resolution.exists() if item.state == 'reserved' else
            resolution.filter(kind='refund' if item.state == 'refunded' else 'unfreeze',quantity=hold.quantity).exists())
        if (hold.batch.account_id != account.pk or hold.batch.order_id != item.payment.order_id or
                not frozen_entry or not resolved_entry or hold.state != expected.get(item.state) or
                (item.state != 'reserved' and (not item.resolved_at or item.evidence_hash != hold.evidence_hash))):
            report('REFUND_LEDGER_MISMATCH','credit_refund',item.pk)
    return {'account_id':str(account.pk),'scope':'internal_ledger_only','findings':findings}


@transaction.atomic
def record_billing_findings(account):
    """Explicit internal write: persist detector output, never mutate money or close issues."""
    from .models import BillingIssue
    account = _lock_account(account)
    report = check_billing_integrity(account)
    now = timezone.now()
    created = 0
    for finding in report['findings']:
        item, is_new = BillingIssue.objects.get_or_create(account=account,**finding,defaults={'last_seen_at':now})
        if is_new:
            created += 1
        else:
            item.last_seen_at = max(item.last_seen_at,now)
            item.save(update_fields=['last_seen_at'])
    return {**report,'created':created,'observed':len(report['findings'])}


@transaction.atomic
def grant_credits(account, source, source_key, quantity, expires_at, *, order=None):
    """Internal entitlement delivery, not a route for arbitrary balance edits."""
    account = _lock_account(account)
    amount = _quantity(quantity, positive=True)
    _identifier(source_key)
    _datetime(expires_at)
    if source not in {"subscription", "purchased"} or (order and order.account_id != account.pk):
        raise ValueError("INVALID_GRANT")
    existing = CreditBatch.objects.filter(account=account, source_key=source_key).first()
    if existing:
        if (_quantity(existing.initial_quantity) != amount or existing.source != source
                or existing.expires_at != expires_at or existing.order_id != (order.pk if order else None)):
            raise ValueError("IDEMPOTENCY_CONFLICT")
        return {"id": str(existing.pk)}
    batch = CreditBatch.objects.create(account=account, source=source, source_key=source_key, order=order, initial_quantity=_text(amount), expires_at=expires_at)
    CreditEntry.objects.create(batch=batch, kind="grant", quantity=_text(amount), event_key="grant")
    return {"id": str(batch.pk)}


def _reservation_dict(reservation):
    return {"id": str(reservation.pk), "task_key": reservation.task_key, "state": reservation.state, "quantity": reservation.quantity, "settled_quantity": reservation.settled_quantity}


@transaction.atomic
def record_task_cost(account, reservation_id, *, source, event_key, resource, usage,
                     retry_index, outcome, cost_amount, currency, occurred_at, evidence_hash,
                     billing_category=None):
    """Trusted final per-operation telemetry, not a rate conversion or debit.

    Source/event keys must identify one operation (including retry attempts),
    not cumulative snapshots. Late facts may arrive after task settlement.
    Corrections need a separate reviewed workflow; replay cannot rewrite costs.
    The trusted executor classifies task usage versus Sage-caused extra work;
    omitted classification stays unknown, never an implicit customer charge.
    """
    from .models import TaskCostEvent
    account = _lock_account(account)
    reservation = _get(Reservation.objects, account=account, pk=reservation_id)
    for value in (source,event_key,resource):
        _identifier(value)
    _datetime(occurred_at)
    if (not isinstance(usage,dict) or not usage or
        set(usage) - {'input_tokens','output_tokens','cached_input_tokens','api_calls'} or
        type(retry_index) is not int or not 0 <= retry_index <= 2147483647 or
        outcome not in ('succeeded','failed','cancelled') or
        billing_category not in (None,'task_usage','sage_fault') or
        not isinstance(evidence_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',evidence_hash)):
        raise ValueError('INVALID_COST_EVENT')
    normalized_usage = {key:_text(_quantity(value)) for key,value in usage.items()}
    if cost_amount is None:
        if currency is not None:
            raise ValueError('INVALID_COST_EVENT')
        amount = None
    else:
        if not isinstance(currency,str) or not re.fullmatch(r'[A-Z]{3}',currency):
            raise ValueError('INVALID_COST_EVENT')
        amount = _text(_quantity(cost_amount))
    payload = {'version':1,'resource':resource,'usage':normalized_usage,'retry_index':retry_index,
        'outcome':outcome,'cost_amount':amount,'currency':currency,
        'occurred_at':occurred_at.astimezone(datetime_timezone.utc).isoformat(),'evidence_hash':evidence_hash}
    # Omission preserves historical fingerprints and replay compatibility.
    if billing_category is not None:
        payload['billing_category'] = billing_category
    digest = hashlib.sha256(json.dumps([str(reservation.pk),payload],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    existing = TaskCostEvent.objects.filter(account=account,source=source,event_key=event_key).first()
    if existing:
        if existing.request_hash != digest:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return {'id':str(existing.pk)}
    event = TaskCostEvent.objects.create(account=account,reservation=reservation,source=source,
        event_key=event_key,payload=payload,request_hash=digest)
    return {'id':str(event.pk)}


@transaction.atomic
def complete_task_cost(account, event_id, *, source, request_key, cost_amount,
                       currency, occurred_at, evidence_hash):
    """Internal trusted-source receipt; never change an event or debit Credits.

    Only the original telemetry source may complete an unknown cost. Callers
    must authenticate that source before invoking this non-public function;
    an evidence hash is a retained receipt reference, not authentication.
    Known-cost corrections require a separate reviewed workflow.
    """
    from .models import TaskCostEvent, TaskCostCompletion
    account = _lock_account(account)
    event = _get(TaskCostEvent.objects, account=account, reservation__account=account, pk=event_id)
    _identifier(source)
    _identifier(request_key)
    _datetime(occurred_at)
    if (not isinstance(currency,str) or not re.fullmatch(r'[A-Z]{3}',currency) or
        not isinstance(evidence_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',evidence_hash)):
        raise ValueError('INVALID_COST_EVENT')
    amount = _text(_quantity(cost_amount))
    if source != event.source:
        raise ValueError('COST_SOURCE_MISMATCH')
    payload = [str(event.pk),amount,currency,
        occurred_at.astimezone(datetime_timezone.utc).isoformat(),evidence_hash]
    digest = hashlib.sha256(json.dumps(payload,separators=(',',':')).encode()).hexdigest()
    existing = TaskCostCompletion.objects.filter(account=account,source=source,request_key=request_key).first()
    if existing:
        if existing.request_hash != digest:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return {'id':str(existing.pk)}
    if event.payload['cost_amount'] is not None:
        raise ValueError('COST_ALREADY_KNOWN')
    if TaskCostCompletion.objects.filter(event=event).exists():
        raise ValueError('IDEMPOTENCY_CONFLICT')
    completion = TaskCostCompletion.objects.create(account=account,event=event,source=source,
        request_key=request_key,cost_amount=amount,currency=currency,occurred_at=occurred_at,
        evidence_hash=evidence_hash,request_hash=digest)
    return {'id':str(completion.pk)}


@transaction.atomic
def task_cost_summary(account, reservation_id):
    """Internal audit read; classifications are not rates or Credits debits."""
    account = _lock_account(account)
    return task_cost_observation(account, reservation_id)


def task_cost_observation(account, reservation_id):
    """Current internal costs without a transaction or locks, not an as-of snapshot."""
    reservation = _get(Reservation.objects, account=account, pk=reservation_id)
    costs, unknown, count = {}, 0, 0
    breakdown = {key:{'event_count':0,'unknown_cost_events':0,'costs':{}}
                 for key in ('task_usage','sage_fault','unclassified')}
    from .models import TaskCostCompletion
    completions = {row.event_id:row for row in TaskCostCompletion.objects.filter(account=account,event__reservation=reservation)}
    for event in reservation.cost_events.order_by('created_at','id'):
        category = event.payload.get('billing_category','unclassified')
        if not isinstance(category,str) or category not in breakdown:
            raise ValueError('INVALID_COST_EVENT')
        bucket = breakdown[category]
        bucket['event_count'] += 1
        count += 1
        amount, currency = event.payload['cost_amount'], event.payload['currency']
        if amount is None and event.pk in completions:
            completion = completions[event.pk]
            amount, currency = completion.cost_amount, completion.currency
        if amount is None:
            unknown += 1
            bucket['unknown_cost_events'] += 1
        else:
            costs[currency] = costs.get(currency,Fraction(0)) + _quantity(amount)
            bucket['costs'][currency] = bucket['costs'].get(currency,Fraction(0)) + _quantity(amount)
    for bucket in breakdown.values():
        bucket['costs'] = {key:_text(value) for key,value in sorted(bucket['costs'].items())}
    return {'event_count':count,'unknown_cost_events':unknown,
            'costs':{key:_text(value) for key,value in sorted(costs.items())},
            'billing_breakdown':breakdown}


@transaction.atomic
def set_shared_limit(account, actor, membership_id, quantity, *, expected_version, request_key):
    from .models import SharedLimitChange, SubscriptionPeriod
    account = _lock_account(account)
    _authorize_purchase(account,actor)
    if account.kind != 'business':
        raise ValueError('FORBIDDEN')
    if type(membership_id) is not int or membership_id < 1:
        raise ValueError('NOT_FOUND')
    member = _get(Membership.objects,account=account,pk=membership_id,is_active=True,user__is_active=True)
    _identifier(request_key)
    if type(expected_version) is not int or expected_version < 0:
        raise ValueError('INVALID_LIMIT_VERSION')
    limit = None if quantity is None else _text(_quantity(quantity))
    previous = SharedLimitChange.objects.filter(membership=member,request_key=request_key).first()
    if previous:
        if previous.actor_id != actor.pk or previous.quantity != limit or previous.version != expected_version+1:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return {'version':previous.version,'quantity':previous.quantity}
    latest = member.shared_limit_changes.order_by('-version').first()
    if (latest.version if latest else 0) != expected_version:
        raise ValueError('LIMIT_CHANGED')
    now = timezone.now()
    if not SubscriptionPeriod.objects.filter(subscription__account=account,starts_at__lte=now,ends_at__gt=now).exists():
        old = latest.quantity if latest else None
        if limit is None or (old is not None and _quantity(limit) > _quantity(old)):
            raise ValueError('SUBSCRIPTION_EXPIRED')
    change = SharedLimitChange.objects.create(membership=member,actor=actor,quantity=limit,
                                              version=expected_version+1,request_key=request_key)
    from .models import BillingNotification
    BillingNotification.objects.create(account=account,type='shared_limit_changed',
        source_event=f'shared-limit:{change.pk}',occurred_at=change.created_at)
    return {'version':change.version,'quantity':change.quantity}


def _business_credit_scope(account, membership_id):
    from .models import PaidSeat
    from .subscriptions import _boundary, _calendar_zone
    if membership_id is None:
        raise ValueError('CONFIG_INCOMPLETE')
    if type(membership_id) is not int or membership_id < 1:
        raise ValueError('NOT_FOUND')
    member = _get(Membership.objects,account=account,pk=membership_id,is_active=True,user__is_active=True)
    now = timezone.now()
    seat = _get(PaidSeat.objects.select_related('subscription'),membership=member,subscription__account=account,
        subscription__periods__starts_at__lte=now,subscription__periods__ends_at__gt=now)
    subscription = seat.subscription
    local = now.astimezone(_calendar_zone(subscription.billing_timezone))
    anchor = subscription.original_anchor.astimezone(_calendar_zone(subscription.billing_timezone))
    index = (local.year-anchor.year)*12 + local.month-anchor.month
    if _boundary(subscription,index) > now:
        index -= 1
    start,end = _boundary(subscription,index),_boundary(subscription,index+1)
    latest = member.shared_limit_changes.order_by('-version').first()
    remaining = None
    if latest and latest.quantity is not None:
        used = Fraction(0)
        entries = CreditEntry.objects.filter(batch__account=account,batch__source='purchased',batch__seat__isnull=True,
            reservation__membership=member,reservation__shared_period_start=start).select_related('reservation')
        for entry in entries:
            if (entry.kind == 'reserve' and entry.reservation.state == 'reserved') or entry.kind == 'settle':
                used += _quantity(entry.quantity)
        remaining = max(Fraction(0),_quantity(latest.quantity)-used)
    return member,seat,start,end,remaining


@transaction.atomic
def reserve_credits(account, task_key, quantity, *, membership_id=None):
    account = _lock_account(account)
    member,seat,start,end,shared_remaining = None,None,None,None,None
    if account.kind == 'business':
        member,seat,start,end,shared_remaining = _business_credit_scope(account,membership_id)
    elif membership_id is not None:
        raise ValueError('FORBIDDEN')
    _identifier(task_key)
    amount = _quantity(quantity, positive=True)
    existing = Reservation.objects.filter(account=account, task_key=task_key).first()
    if existing:
        if _quantity(existing.quantity) != amount or existing.membership_id != (member.pk if member else None):
            raise ValueError("IDEMPOTENCY_CONFLICT")
        return _reservation_dict(existing)
    query = CreditBatch.objects.filter(account=account, expires_at__gt=timezone.now())
    if member:
        query = query.filter(Q(seat=seat)|Q(source='purchased',seat__isnull=True))
    batches = list(query)
    batches.sort(key=lambda batch: (batch.expires_at, batch.source != "subscription", str(batch.pk)))
    available = []
    for batch in batches:
        value = _balances(batch)['available']
        if member and batch.seat_id is None and shared_remaining is not None:
            value = min(value,shared_remaining)
            shared_remaining -= value
        available.append((batch,value))
    if sum(value for _, value in available) < amount:
        raise ValueError("INSUFFICIENT_CREDITS")
    reservation = Reservation.objects.create(account=account, task_key=task_key, quantity=_text(amount),
        membership=member,shared_period_start=start,shared_period_end=end)
    remaining = amount
    for batch, value in available:
        take = min(remaining, value)
        if take:
            CreditEntry.objects.create(batch=batch, reservation=reservation, kind="reserve", quantity=_text(take), event_key=f"reserve:{reservation.pk}")
            remaining -= take
        if remaining == 0:
            break
    return _reservation_dict(reservation)


@transaction.atomic
def settle_credits(account, reservation_id, actual_quantity):
    account = _lock_account(account)
    reservation = _get(Reservation.objects.select_for_update(), account=account, pk=reservation_id)
    actual = _quantity(actual_quantity)
    if reservation.state != "reserved":
        if reservation.state == "settled" and _quantity(reservation.settled_quantity) == actual:
            return _reservation_dict(reservation)
        raise ValueError("IDEMPOTENCY_CONFLICT")
    if actual > _quantity(reservation.quantity):
        raise ValueError("OVER_RESERVATION")
    if reservation.shared_period_end and timezone.now() >= reservation.shared_period_end:
        raise ValueError('CONFIG_INCOMPLETE')
    allocations = list(reservation.entries.filter(kind="reserve").select_related("batch").order_by("id"))
    if sum(_quantity(entry.quantity) for entry in allocations) != _quantity(reservation.quantity):
        raise ValueError("LEDGER_INCONSISTENT")
    if any(entry.batch.expires_at <= timezone.now() for entry in allocations):
        # D11: do not guess whether an expired task allocation may be settled.
        raise ValueError("CONFIG_INCOMPLETE")
    remaining = actual
    for entry in allocations:
        take = min(remaining, _quantity(entry.quantity))
        unused = _quantity(entry.quantity) - take
        if take:
            CreditEntry.objects.create(batch=entry.batch, reservation=reservation, kind="settle", quantity=_text(take), event_key=f"settle:{reservation.pk}")
        if unused:
            CreditEntry.objects.create(batch=entry.batch, reservation=reservation, kind="release", quantity=_text(unused), event_key=f"release:{reservation.pk}")
        remaining -= take
    reservation.state = "settled"
    reservation.settled_quantity = _text(actual)
    reservation.save(update_fields=["state", "settled_quantity"])
    return _reservation_dict(reservation)


@transaction.atomic
def release_credits(account, reservation_id):
    account = _lock_account(account)
    reservation = _get(Reservation.objects.select_for_update(), account=account, pk=reservation_id)
    if reservation.state == "released":
        return _reservation_dict(reservation)
    if reservation.state != "reserved":
        raise ValueError("IDEMPOTENCY_CONFLICT")
    result = settle_credits(account, reservation_id, "0")
    reservation.refresh_from_db()
    reservation.state = "released"
    reservation.save(update_fields=["state"])
    return {**result, "state": "released"}


@transaction.atomic
def _persist_confirmed_payment(account, order_id, *, channel, merchant_reference, transaction_reference, amount_minor, currency, paid_at, evidence_hash):
    """Accept verified server-side facts only, never browser redirects/callback JSON.

    Future PSP code must verify merchant, signature AND amount/query evidence
    before calling this. Current application deliberately exposes no such route.
    Receipt exceptions are persisted for reconciliation, not silently discarded.
    """
    account = _lock_account(account)
    order = _get(Order.objects.select_for_update(), account=account, pk=order_id)
    for value in (channel, merchant_reference, transaction_reference):
        _identifier(value)
    if type(amount_minor) is not int or amount_minor <= 0 or amount_minor > 9223372036854775807 or not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("INVALID_PAYMENT_FACT")
    if not isinstance(evidence_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", evidence_hash):
        raise ValueError("INVALID_PAYMENT_EVIDENCE")
    _datetime(paid_at)
    receipt_key = {"channel": channel, "merchant_reference": merchant_reference, "transaction_reference": transaction_reference}
    existing = PaymentFact.objects.filter(**receipt_key).first()
    if existing:
        if (existing.order_id != order.pk or existing.amount_minor != amount_minor or existing.currency != currency or existing.paid_at != paid_at):
            raise ValueError("RECEIPT_CONFLICT")
        return {"id": str(existing.pk), "disposition": existing.disposition}
    try:
        snapshot = _payment_snapshot(order)
    except ValueError:
        # Preserve verified actual funds even when dispatch evidence is damaged.
        snapshot = None
    if snapshot is None or channel != snapshot.get('channel') or merchant_reference != snapshot.get('merchant_entity'):
        disposition = "identity_mismatch_review"
    elif amount_minor != order.amount_minor or currency != order.currency:
        disposition = "amount_mismatch_review"
    elif order.paid_at:
        disposition = "duplicate_payment_review"
    elif order.status != "pending_payment":
        disposition = "late_payment_review"
    else:
        disposition = "accepted"
    try:
        with transaction.atomic():
            fact = PaymentFact.objects.create(order=order, **receipt_key, amount_minor=amount_minor, currency=currency, paid_at=paid_at, evidence_hash=evidence_hash, disposition=disposition)
    except IntegrityError as exc:
        # Different account locks cannot allow one provider receipt on two orders.
        raise ValueError("RECEIPT_CONFLICT") from exc
    if disposition == "accepted":
        order.paid_at = paid_at
        order.status = "paid_pending_entitlement"
        order.save(update_fields=["paid_at", "status"])
        from .models import BillingNotification, TopupBudget
        attempt = PaymentAttempt.objects.filter(account=account,order=order).first()
        receipt_type = 'payment_received'
        if attempt and TopupBudget.objects.filter(account=account,attempt=attempt).exists():
            receipt_type = 'topup_payment_received'
        elif attempt and attempt.subscription_id:
            receipt_type = 'renewal_payment_received'
        BillingNotification.objects.create(account=account,type=receipt_type,
            source_event=f'payment:{fact.pk}:accepted',order=order,amount_minor=fact.amount_minor,
            subscription_id=attempt.subscription_id if attempt else None,
            currency=fact.currency,occurred_at=fact.paid_at)
    return {"id": str(fact.pk), "disposition": fact.disposition}


def record_confirmed_payment(account, order_id, **verified_fact):
    """Persist the trusted fact before delivery so a delivery error cannot erase it.

    An actual PSP integration is not yet available; no public route invokes this.
    """
    if transaction.get_connection().in_atomic_block:
        raise ValueError("PAYMENT_FACT_REQUIRES_AUTOCOMMIT")
    fact = _persist_confirmed_payment(account, order_id, **verified_fact)
    if fact["disposition"] == "accepted":
        fulfill_paid_order(account, order_id)
    return fact


@transaction.atomic
def fulfill_paid_order(account, order_id):
    """Idempotent local delivery/repair from persisted accepted payment evidence."""
    account = _lock_account(account)
    order = _get(Order.objects.select_for_update(), account=account, pk=order_id)
    snapshot = _payment_snapshot(order)
    if not order.paid_at or not order.payment_facts.filter(disposition="accepted", amount_minor=order.amount_minor,
        currency=order.currency, channel=snapshot.get('channel'),
        merchant_reference=order.snapshot.get('merchant_entity')).exists():
        raise ValueError("PAYMENT_NOT_CONFIRMED")
    if order.status == "fulfilled":
        return _order_dict(order)
    if order.status != "paid_pending_entitlement":
        raise ValueError("ORDER_STATE_CONFLICT")
    if order.snapshot.get("kind") == "credits":
        try:
            quantity = _quantity(order.snapshot["credits_quantity"], positive=True)
        except (KeyError, ValueError) as exc:
            raise ValueError("CONFIG_INCOMPLETE") from exc
        paid_at = timezone.localtime(order.paid_at)
        year = paid_at.year + 1
        day = min(paid_at.day, calendar.monthrange(year, paid_at.month)[1])
        expires_at = paid_at.replace(year=year, day=day)
        grant_credits(account, "purchased", f"order:{order.pk}", _text(quantity), expires_at, order=order)
        order.status = "fulfilled"
        order.save(update_fields=["status"])
    # Subscription activation requires separately confirmed mandate/calendar
    # inputs in subscriptions.py; a paid receipt alone is not enough.
    return _order_dict(order)


def _payment_snapshot(order):
    """Immutable commercial terms plus proven dispatch transport, never current routing."""
    attempt = PaymentAttempt.objects.filter(order=order,subscription__isnull=False).select_related('subscription').first()
    if not attempt:
        return dict(order.snapshot)
    from .subscriptions import _mandate, _renewal_snapshot
    sub, auth = attempt.subscription, attempt.dispatch_authorization
    if (not attempt.dispatched_at or not isinstance(auth,dict) or
        set(auth) != {'subscription_id','authorization_version','payment_method_id','mandate'} or
        auth['subscription_id'] != str(sub.pk) or sub.account_id != order.account_id or
        type(auth['authorization_version']) is not int or auth['authorization_version'] < 0 or
        not isinstance(auth['mandate'],dict)):
        raise ValueError('PAYMENT_AUTHORIZATION_UNKNOWN')
    version = auth['authorization_version']
    if version:
        change = sub.authorization_changes.filter(version=version).first()
        expected = (change.new_mandate,change.new_method_id) if change else (None,None)
    else:
        first = sub.authorization_changes.order_by('version').first()
        expected = ((first.old_mandate,first.old_method_id) if first else
                    (sub.mandate,sub.payment_method_id) if sub.authorization_version == 0 else (None,None))
    if auth['mandate'] != expected[0] or auth['payment_method_id'] != (str(expected[1]) if expected[1] else None):
        raise ValueError('PAYMENT_AUTHORIZATION_UNKNOWN')
    _renewal_snapshot(sub,auth['mandate'],source_attempt=attempt)
    snapshot = {**order.snapshot,'channel':auth['mandate'].get('channel')}
    _mandate(auth['mandate'],snapshot)
    if auth['mandate']['executor'] != 'sage' or order.currency != 'CNY':
        raise ValueError('PAYMENT_AUTHORIZATION_UNKNOWN')
    return snapshot


def _effective_attempt_state(attempt, order):
    # Persisted money wins over a delayed status event, never over missing money.
    if order.paid_at and order.payment_facts.filter(disposition='accepted').exists():
        return 'succeeded'
    if order.payment_facts.exists():
        return 'review'
    return attempt.state


def _attempt_dict(attempt, order):
    return {'id': str(attempt.pk), 'status': _effective_attempt_state(attempt, order),
            'needs_reconciliation': attempt.needs_reconciliation,
            'expires_at': attempt.expires_at.astimezone(datetime_timezone.utc).isoformat()}


@transaction.atomic
def prepare_payment_attempt(account, user, order_id, intent_key, *, expires_at):
    """Internal only. Caller supplies canonical business intent, not browser keys.

    No payment channel is invoked. The provider-specific expiry policy and a
    trusted intent builder must be configured before exposing payable checkout.
    """
    account = _lock_account(account)
    _authorize_purchase(account, user)
    _identifier(intent_key)
    if intent_key.startswith(('renewal:', 'recovery:', 'topup:')):
        raise ValueError('RENEWAL_BUILDER_REQUIRED')
    _datetime(expires_at)
    order = _get(Order.objects.select_for_update().select_related('quote__product'), account=account, pk=order_id)
    existing = PaymentAttempt.objects.filter(account=account, intent_key=intent_key).first()
    if existing:
        if existing.order_id != order.pk or existing.expires_at != expires_at:
            raise ValueError('PAYMENT_INTENT_CONFLICT')
        return _attempt_dict(existing, order)
    if PaymentAttempt.objects.filter(order=order).exists():
        raise ValueError('PAYMENT_INTENT_CONFLICT')
    if order.status != 'pending_payment' or order.paid_at:
        raise ValueError('ORDER_STATE_CONFLICT')
    if expires_at <= timezone.now():
        raise ValueError('PAYMENT_EXPIRED')
    _sale_gate(order.quote.product, account, timezone.now(), order.snapshot.get('channel'),
               order.snapshot.get('buyer_region'), order.snapshot.get('merchant_entity'))
    attempt = PaymentAttempt.objects.create(account=account, order=order, intent_key=intent_key, expires_at=expires_at)
    return _attempt_dict(attempt, order)


def claim_payment_dispatch(account, attempt_id):
    """Commit one dispatch claim BEFORE network I/O, never blindly resend it.

    A caller crash before/after sending must query the PSP using this attempt ID.
    The future channel adapter must use it as its idempotent merchant request ID.
    This function neither calls a PSP nor claims exactly-once network delivery.
    """
    if transaction.get_connection().in_atomic_block:
        raise ValueError('DISPATCH_REQUIRES_AUTOCOMMIT')
    with transaction.atomic():
        account = _lock_account(account)
        from .models import TopupBudget
        if TopupBudget.objects.filter(account=account,attempt_id=attempt_id).exists():
            raise ValueError('TOPUP_CHARGING_DISABLED')
        attempt = _get(PaymentAttempt.objects.select_for_update(), account=account, pk=attempt_id)
        order = _get(Order.objects.select_for_update().select_related('quote__product'), pk=attempt.order_id, account=account)
        result = {**_attempt_dict(attempt, order), 'dispatch': False, 'request_reference': str(attempt.pk)}
        if attempt.state != 'created' or result['status'] != 'created' or order.status != 'pending_payment':
            return result
        now = timezone.now()
        snapshot = dict(order.snapshot)
        if attempt.recovery_of_id:
            from .subscriptions import _recovery_context
            original = _get(PaymentAttempt.objects.select_for_update(), account=account, pk=attempt.recovery_of_id)
            try:
                _, deadline = _recovery_context(account, original, now)
            except ValueError as exc:
                if str(exc) != 'RECOVERY_NOT_ELIGIBLE':
                    raise
                attempt.state = 'cancelled'
                attempt.save(update_fields=['state'])
                order.status = 'closed'
                order.save(update_fields=['status'])
                return {**result, 'status': 'cancelled'}
            source = _payment_snapshot(original.order) if original.dispatched_at else original.order.snapshot
            if attempt.expires_at > deadline or order.snapshot != source:
                raise ValueError('RECOVERY_INTENT_CONFLICT')
        if attempt.subscription_id:
            from .subscriptions import _mandate, _boundary, _months, _renewal_snapshot
            from .models import Subscription
            subscription = _get(Subscription.objects.select_for_update(), account=account, pk=attempt.subscription_id)
            if not subscription.auto_renew or subscription.cancellation_state != 'none':
                attempt.state = 'cancelled'
                attempt.save(update_fields=['state'])
                order.status = 'closed'
                order.save(update_fields=['status'])
                return {**result, 'status': 'cancelled'}
            snapshot = _renewal_snapshot(subscription,source_attempt=attempt)
            _mandate(subscription.mandate, snapshot)
            if subscription.mandate['executor'] != 'sage':
                raise ValueError('RENEWAL_EXECUTOR_CONFLICT')
            last = subscription.periods.order_by('-cycle_index').first()
            if not last or attempt.renewal_cycle_index != last.cycle_index + 1:
                raise ValueError('RENEWAL_CYCLE_CONFLICT')
            if ({**order.snapshot,'channel':snapshot['channel']} != snapshot or order.snapshot != order.quote.snapshot or
                attempt.needs_reconciliation or attempt.dispatch_authorization is not None or attempt.dispatched_at or
                attempt.events.exists() or order.payment_facts.exists()):
                raise ValueError('RENEWAL_SNAPSHOT_CONFLICT')
            if attempt.expires_at > _boundary(subscription, attempt.renewal_cycle_index * _months(subscription)):
                raise ValueError('INVALID_RENEWAL_WINDOW')
            if now < attempt.dispatch_not_before:
                return result
        if attempt.expires_at <= now:
            # No dispatch was ever claimed: no external transaction to close.
            attempt.state = 'expired'
            attempt.save(update_fields=['state'])
            order.status = 'closed'
            order.save(update_fields=['status'])
            return {**result, 'status': 'expired'}
        if attempt.subscription_id and not getattr(settings, 'BILLING_RENEWAL_CHARGING_ENABLED', False):
            raise ValueError('RENEWAL_CHARGING_DISABLED')
        _sale_gate(order.quote.product, account, now, snapshot.get('channel'),
                   order.snapshot.get('buyer_region'), order.snapshot.get('merchant_entity'))
        if attempt.subscription_id:
            if subscription.payment_method_id:
                from .models import PaymentMethod
                method = _get(PaymentMethod.objects.select_for_update(),account=account,pk=subscription.payment_method_id)
                if (method.state != 'active' or method.channel != subscription.mandate['channel'] or
                    method.merchant_reference != subscription.mandate['merchant_reference']):
                    raise ValueError('PAYMENT_METHOD_NOT_AVAILABLE')
            # Frozen with the dispatch claim before any provider call. Workers
            # must query this transaction using this snapshot, never a newer mandate.
            attempt.dispatch_authorization = {'subscription_id':str(subscription.pk),
                'authorization_version': subscription.authorization_version,
                'payment_method_id':str(subscription.payment_method_id) if subscription.payment_method_id else None,
                'mandate':dict(subscription.mandate)}
        attempt.state, attempt.dispatched_at = 'pending', now
        attempt.save(update_fields=['state', 'dispatched_at', 'dispatch_authorization'])
        result.update(status='pending', dispatch=True, channel=snapshot['channel'])
    return result


@transaction.atomic
def record_attempt_event(account, attempt_id, event_key, outcome, *, source, evidence_hash):
    """Trusted reconciliation boundary; hashes do not replace PSP verification.

    A success fact must be stored separately first. Unknown/local timeout can
    never authorize re-dispatch, close a live order or open product benefits.
    """
    account = _lock_account(account)
    attempt = _get(PaymentAttempt.objects.select_for_update(), account=account, pk=attempt_id)
    order = _get(Order.objects.select_for_update(), pk=attempt.order_id, account=account)
    _identifier(event_key)
    if (source not in ('local', 'provider_query', 'verified_callback') or
        outcome not in ('pending', 'unknown', 'succeeded', 'failed', 'cancelled', 'expired')):
        raise ValueError('INVALID_PAYMENT_EVENT')
    if ((source == 'local' and (outcome != 'unknown' or evidence_hash != '')) or
        (source != 'local' and (not isinstance(evidence_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', evidence_hash)))):
        raise ValueError('PAYMENT_EVIDENCE_REQUIRED')
    fingerprint = hashlib.sha256(json.dumps([source,outcome,evidence_hash], separators=(',', ':')).encode()).hexdigest()
    previous = PaymentAttemptEvent.objects.filter(attempt=attempt, event_key=event_key).first()
    if previous:
        if previous.request_hash != fingerprint:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return _attempt_dict(attempt, order)
    if attempt.dispatched_at is None and outcome in ('pending', 'unknown'):
        raise ValueError('PAYMENT_NOT_DISPATCHED')
    effective = _effective_attempt_state(attempt, order)
    if outcome == 'succeeded' and effective not in ('succeeded', 'review'):
        raise ValueError('PAYMENT_NOT_CONFIRMED')
    terminals = ('succeeded', 'failed', 'cancelled', 'expired', 'review')
    applied = effective not in terminals or outcome == effective
    if effective in terminals:
        attempt.state = effective
        if outcome in terminals and outcome != effective:
            attempt.needs_reconciliation = True
    else:
        attempt.state = outcome
    if outcome == 'succeeded' and effective == 'review':
        attempt.needs_reconciliation = True
    if applied and attempt.state in ('failed', 'cancelled', 'expired') and not order.paid_at:
        order.status = 'closed'
        order.save(update_fields=['status'])
    PaymentAttemptEvent.objects.create(attempt=attempt, event_key=event_key, source=source,
        outcome=outcome, request_hash=fingerprint, evidence_hash=evidence_hash, applied=applied)
    attempt.save(update_fields=['state', 'needs_reconciliation'])
    if applied and effective != 'failed' and attempt.state == 'failed' and not order.paid_at:
        from .models import BillingNotification, TopupBudget
        failure_type = 'renewal_failed' if attempt.subscription_id else 'payment_failed'
        if TopupBudget.objects.filter(account=account,attempt=attempt).exists():
            failure_type = 'topup_payment_failed'
        BillingNotification.objects.create(account=account,
            type=failure_type,
            source_event=f'attempt:{attempt.pk}:failed',order=order,
            subscription_id=attempt.subscription_id,occurred_at=timezone.now())
    return _attempt_dict(attempt, order)
