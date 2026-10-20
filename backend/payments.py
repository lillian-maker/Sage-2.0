"""Closed payment callbacks and explicitly gated local test reconciliation."""
import base64
import binascii
import hashlib
import json
import logging
import math
import re
import uuid
from datetime import datetime, timedelta, timezone as datetime_timezone
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

from alipay.aop.api.AlipayClientConfig import AlipayClientConfig
from alipay.aop.api.DefaultAlipayClient import DefaultAlipayClient
from alipay.aop.api.request.AlipayTradePagePayRequest import AlipayTradePagePayRequest
from alipay.aop.api.request.AlipayTradeQueryRequest import AlipayTradeQueryRequest
from alipay.aop.api.request.AlipayTradeCloseRequest import AlipayTradeCloseRequest
from alipay.aop.api.request.AlipayTradeRefundRequest import AlipayTradeRefundRequest
from alipay.aop.api.request.AlipayTradeFastpayRefundQueryRequest import AlipayTradeFastpayRefundQueryRequest
from alipay.aop.api.util.SignatureUtils import get_sign_content, sign_with_rsa2
import requests
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .commerce.models import Order, PaymentAttempt, PaymentFact
from .http import error


def start_payment(request, order_id):
    """Called after the billing API's session/account locks and authorization."""
    from .views import payload
    if request.GET:
        raise ValueError('INVALID_REQUEST')
    payload(request, ())
    order = Order.objects.select_related('account', 'quote__product').filter(pk=order_id,
        account_id=request.session.get('active_account_id')).first()
    if not order:
        raise ValueError('NOT_FOUND')
    wechat = order.snapshot.get('channel') == 'wechat'
    existing = PaymentAttempt.objects.filter(order=order).first()
    configuration = (_wechat_native_configuration(order, reconciliation=bool(existing and existing.dispatched_at))
        if wechat else _alipay_sandbox_configuration(order))
    if configuration is None:
        return error(request, 'CHANNEL_UNAVAILABLE', 503)
    from .commerce.billing import prepare_payment_attempt
    if wechat and existing and existing.dispatched_at:
        return {'payment_query': str(existing.pk), 'account': order.account}
    expires_at = existing.expires_at if existing else (
        order.created_at+timedelta(seconds=configuration['payment_ttl_seconds']))
    prepared = prepare_payment_attempt(order.account, request.user, order.pk,
        'checkout:'+str(order.pk), expires_at=expires_at)
    # Only api consumes this internal result, after its authorization/prepare
    # transaction commits. No URL or SDK operation occurs under those locks.
    return {'payment_dispatch': prepared['id'], 'account': order.account}


def _alipay_sandbox_configuration(order, *, reconciliation=False):
    """Explicit test-only permission; catalog approval alone cannot sign a URL."""
    from .commerce.billing import _sale_gate
    try:
        configuration = settings.BILLING_ALIPAY_SANDBOX
        if type(configuration) is not dict:
            return None
        configuration = dict(configuration)
        if (settings.ENVIRONMENT != 'test' or type(configuration) is not dict or
                set(configuration) != {'enabled', 'app_id', 'seller_id', 'merchant_entity',
                    'private_key', 'public_key', 'payment_ttl_seconds'} or
                configuration['enabled'] is not True or
                type(configuration['payment_ttl_seconds']) is not int or
                configuration['payment_ttl_seconds'] <= 0 or
                order.snapshot.get('kind') != 'credits' or order.quote.product.kind != 'credits' or
                order.snapshot.get('channel') != 'alipay' or order.currency != 'CNY' or
                order.quote.currency != 'CNY' or order.quote.account_id != order.account_id or
                order.snapshot != order.quote.snapshot or
                order.amount_minor != order.quote.amount_minor or
                type(order.snapshot.get('amount_minor')) is not int or
                order.snapshot.get('amount_minor') != order.amount_minor or
                order.snapshot.get('currency') != 'CNY' or
                order.snapshot.get('merchant_entity') != configuration['merchant_entity']):
            return None
        for field in ('app_id', 'seller_id'):
            if not isinstance(configuration[field], str) or not re.fullmatch(r'[0-9]{1,64}', configuration[field]):
                return None
        private_data = configuration['private_key']
        private_data = private_data.encode('ascii') if isinstance(private_data, str) else private_data
        public_data = configuration['public_key']
        public_data = public_data.encode('ascii') if isinstance(public_data, str) else public_data
        if (type(private_data) is not bytes or len(private_data) > 8192 or
                not private_data.startswith(b'-----BEGIN RSA PRIVATE KEY-----') or
                type(public_data) is not bytes or len(public_data) > 8192 or
                not public_data.startswith(b'-----BEGIN PUBLIC KEY-----')):
            return None
        private = serialization.load_pem_private_key(private_data, password=None)
        public = serialization.load_pem_public_key(public_data)
        if (not isinstance(private, rsa.RSAPrivateKey) or private.key_size < 2048 or
                not isinstance(public, rsa.RSAPublicKey) or public.key_size < 2048):
            return None
        # Evaluate overflow without inventing an expiry fallback.
        order.created_at+timedelta(seconds=configuration['payment_ttl_seconds'])
        alipay_amount(order.amount_minor)
        if not reconciliation:
            _sale_gate(order.quote.product, order.account, timezone.now(), 'alipay',
                order.snapshot.get('buyer_region'), configuration['merchant_entity'])
        return configuration
    except (ValueError, TypeError, UnicodeError, OverflowError, UnsupportedAlgorithm):
        return None


def _alipay_sandbox_identity(configuration):
    return {'channel': 'alipay', 'environment': 'SANDBOX',
        **{key: configuration[key] for key in ('app_id', 'seller_id', 'merchant_entity')}}


def payment_start_capability(account, order_id):
    """Read-only order-bound browser capability, never merchant configuration."""
    order = Order.objects.select_related('account', 'quote__product').filter(account=account, pk=order_id).first()
    if not order or order.status != 'pending_payment' or order.paid_at:
        return None
    wechat = order.snapshot.get('channel') == 'wechat'
    configuration = _wechat_native_configuration(order) if wechat else _alipay_sandbox_configuration(order)
    if configuration is None:
        return None
    attempt = PaymentAttempt.objects.filter(order=order).first()
    if attempt and (attempt.state != 'created' or attempt.dispatched_at):
        return None
    expires_at = attempt.expires_at if attempt else order.created_at+timedelta(seconds=configuration['payment_ttl_seconds'])
    if expires_at <= timezone.now():
        return None
    return {'environment': 'LOCAL_TEST' if wechat else 'SANDBOX',
        'channel': 'wechat' if wechat else 'alipay', 'account_id': str(account.pk),
        'order_id': str(order.pk), 'available': True}


def finish_payment(request, prepared):
    """Claim in a second short transaction before signing or channel I/O."""
    from .commerce.billing import claim_payment_dispatch, record_attempt_event
    account, attempt_id = prepared['account'], prepared['payment_dispatch']
    order = Order.objects.select_related('account', 'quote__product').get(paymentattempt__pk=attempt_id, account=account)
    if order.snapshot.get('channel') == 'wechat':
        return _finish_wechat_payment(request, prepared, order)
    configuration = _alipay_sandbox_configuration(order)
    if configuration is None:
        return error(request, 'CHANNEL_UNAVAILABLE', 503)
    try:
        claimed = claim_payment_dispatch(account, attempt_id, request=request,
            dispatch_context=_alipay_sandbox_identity(configuration))
    except ValueError as exc:
        code = str(exc)
        status = {'AUTH_REQUIRED': 401, 'ACCOUNT_CHANGED': 409, 'FORBIDDEN': 403,
            'NOT_FOUND': 404, 'CONFIG_INCOMPLETE': 503, 'CREDITS_SUBSCRIPTION_REQUIRED': 422}.get(code)
        if status is None:
            raise
        return error(request, code, status)
    data = {'payment_attempt': {key: claimed[key] for key in (
        'id', 'status', 'needs_reconciliation', 'expires_at')}}
    if claimed['dispatch']:
        attempt = PaymentAttempt.objects.get(pk=attempt_id, account=account)
        try:
            data['payment_url'] = alipay_sandbox_page_url(attempt.pk, order.amount_minor,
                app_id=configuration['app_id'], private_key=configuration['private_key'],
                expires_at=attempt.expires_at)
        except Exception:
            # Do not include SDK exceptions: they may contain key/request material.
            record_attempt_event(account, attempt_id, 'sandbox-signing-unknown', 'unknown',
                source='local', evidence_hash='')
            return error(request, 'CHANNEL_UNAVAILABLE', 503)
    return JsonResponse(data)


def _wechat_native_configuration(order=None, *, reconciliation=False):
    """Sage local-test permission for the real protocol, never a WeChat sandbox."""
    from .wechat import build_wechat_request
    from .commerce.billing import _sale_gate
    try:
        configuration = settings.BILLING_WECHAT_NATIVE
        if (settings.ENVIRONMENT != 'test' or type(configuration) is not dict or
                set(configuration) != {'enabled', 'app_id', 'mch_id', 'merchant_entity',
                    'merchant_serial', 'private_key', 'public_key', 'public_key_id',
                    'api_v3_key', 'notify_origin', 'payment_ttl_seconds'} or
                configuration['enabled'] is not True or
                type(configuration['payment_ttl_seconds']) is not int or
                not 60 <= configuration['payment_ttl_seconds'] <= 7200 or
                type(configuration['api_v3_key']) is not bytes or len(configuration['api_v3_key']) != 32 or
                type(configuration['merchant_entity']) is not str or
                not 1 <= len(configuration['merchant_entity']) <= 128 or
                not configuration['merchant_entity'].strip() or
                any(ord(char) < 32 for char in configuration['merchant_entity'])):
            return None
        configuration = dict(configuration)
        public_data = configuration['public_key']
        public_data = public_data.encode('ascii') if type(public_data) is str else public_data
        if (type(public_data) is not bytes or len(public_data) > 8192 or
                not public_data.startswith(b'-----BEGIN PUBLIC KEY-----')):
            return None
        key = serialization.load_pem_public_key(public_data)
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
            return None
        # Reuse protocol validation, including HTTPS callback origin and RSA key.
        # The TTL bound above is a Sage test policy, not a provider claim.
        build_wechat_request('native', '0'*32, mchid=configuration['mch_id'],
            merchant_serial=configuration['merchant_serial'], private_key=configuration['private_key'],
            public_key_id=configuration['public_key_id'], timestamp=1, nonce='configuration',
            appid=configuration['app_id'], amount_minor=1, description='Sage local test',
            notify_origin=configuration['notify_origin'], time_expire='2026-01-01T00:00:00+00:00')
        if order is not None:
            snapshot = order.snapshot
            if (snapshot.get('kind') != 'credits' or order.quote.product.kind != 'credits' or
                    snapshot.get('channel') != 'wechat' or snapshot.get('billing_cycle') != '' or
                    order.currency != 'CNY' or order.quote.currency != 'CNY' or
                    order.quote.account_id != order.account_id or snapshot != order.quote.snapshot or
                    order.amount_minor != order.quote.amount_minor or
                    type(snapshot.get('amount_minor')) is not int or
                    snapshot.get('amount_minor') != order.amount_minor or
                    not 1 <= order.amount_minor <= 2147483647 or snapshot.get('currency') != 'CNY' or
                    snapshot.get('merchant_entity') != configuration['merchant_entity']):
                return None
            order.created_at+timedelta(seconds=configuration['payment_ttl_seconds'])
            if not reconciliation:
                _sale_gate(order.quote.product, order.account, timezone.now(), 'wechat',
                    snapshot.get('buyer_region'), configuration['merchant_entity'],
                    public_purchase=bool(snapshot.get('terms_document_digest')))
        return configuration
    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, UnsupportedAlgorithm):
        return None


def _wechat_identity(configuration):
    return {'channel':'wechat', 'environment':'LIVE', **{key:configuration[key]
        for key in ('app_id', 'mch_id', 'merchant_entity')}}


def _wechat_context(account, attempt_id):
    if transaction.get_connection().in_atomic_block:
        raise ValueError('DISPATCH_REQUIRES_AUTOCOMMIT')
    attempt = PaymentAttempt.objects.select_related('account', 'order__account',
        'order__quote__product').filter(account=account, pk=attempt_id).first()
    if not attempt:
        raise ValueError('NOT_FOUND')
    configuration = _wechat_native_configuration(attempt.order, reconciliation=True)
    if (configuration is None or attempt.order.account_id != account.pk or
            not attempt.dispatched_at or attempt.subscription_id or
            attempt.recovery_of_id or attempt.dispatch_authorization != _wechat_identity(configuration)):
        raise ValueError('CHANNEL_UNAVAILABLE')
    return attempt, configuration


def _wechat_call(configuration, operation, attempt):
    from .wechat import call_wechat_api
    now = int(timezone.now().timestamp())
    fields = {'mchid':configuration['mch_id'], 'merchant_serial':configuration['merchant_serial'],
        'private_key':configuration['private_key'], 'public_key_id':configuration['public_key_id'],
        'public_key':configuration['public_key'], 'now':now, 'timestamp':now, 'nonce':uuid.uuid4().hex}
    if operation == 'native':
        fields.update(appid=configuration['app_id'], amount_minor=attempt.order.amount_minor,
            description='Sage order '+str(attempt.order_id), notify_origin=configuration['notify_origin'],
            time_expire=attempt.expires_at.isoformat())
    return call_wechat_api(operation, attempt.pk, **fields)


def payment_qr(account, order_id):
    """Read-only recovery from this account's original persisted Native payload."""
    from .wechat import wechat_qr_image
    attempt = PaymentAttempt.objects.select_related('order__account', 'order__quote__product').filter(
        account=account, order_id=order_id, state='pending', dispatched_at__isnull=False,
        wechat_code_url__isnull=False).first()
    if (not attempt or attempt.expires_at <= timezone.now() or attempt.needs_reconciliation or
            attempt.order.status != 'pending_payment' or attempt.order.paid_at or
            attempt.order.payment_facts.exists()):
        return None
    configuration = _wechat_native_configuration(attempt.order, reconciliation=True)
    if configuration is None or attempt.dispatch_authorization != _wechat_identity(configuration):
        return None
    return {'channel':'wechat', 'account_id':str(account.pk), 'order_id':str(order_id),
        'attempt_id':str(attempt.pk), 'expires_at':attempt.expires_at.isoformat(),
        'image_svg_base64':wechat_qr_image(attempt.wechat_code_url)}


def _wechat_order_result(account, order_id):
    from .commerce.billing import order_detail
    data = order_detail(account, order_id)
    qr = payment_qr(account, order_id)
    if qr:
        data['payment_qr'] = qr
    return data


def _finish_wechat_payment(request, prepared, order):
    from .commerce.billing import _lock_account, _attempt_dict, claim_payment_dispatch, record_attempt_event
    configuration = _wechat_native_configuration(order)
    if configuration is None:
        return error(request, 'CHANNEL_UNAVAILABLE', 503)
    account, attempt_id = prepared['account'], prepared['payment_dispatch']
    try:
        claimed = claim_payment_dispatch(account, attempt_id, request=request,
            dispatch_context=_wechat_identity(configuration))
    except ValueError as exc:
        code = str(exc)
        status = {'AUTH_REQUIRED':401, 'ACCOUNT_CHANGED':409, 'FORBIDDEN':403,
            'NOT_FOUND':404, 'CONFIG_INCOMPLETE':503, 'CREDITS_SUBSCRIPTION_REQUIRED':422}.get(code)
        if status is None:
            raise
        return error(request, code, status)
    if not claimed['dispatch']:
        if PaymentAttempt.objects.filter(pk=attempt_id, dispatched_at__isnull=False).exists():
            return finish_payment_query(request, {'payment_query':attempt_id, 'account':account})
    else:
        attempt, configuration = _wechat_context(account, attempt_id)
        try:
            fields, _ = _wechat_call(configuration, 'native', attempt)
        except ValueError:
            record_attempt_event(account, attempt_id, 'wechat-native-unknown', 'unknown',
                source='local', evidence_hash='')
            return error(request, 'CHANNEL_UNAVAILABLE', 503)
        # A late Native reply cannot replace another QR or resurrect paid money.
        with transaction.atomic():
            account = _lock_account(account)
            current = PaymentAttempt.objects.select_for_update().get(pk=attempt_id, account=account)
            current_order = Order.objects.select_for_update().get(pk=order.pk, account=account)
            if (current.dispatch_authorization == _wechat_identity(configuration) and
                    current.wechat_code_url is None and current.state == 'pending' and
                    current_order.status == 'pending_payment' and not current_order.paid_at):
                current.wechat_code_url = fields['code_url']
                current.save(update_fields=['wechat_code_url'])
    current = PaymentAttempt.objects.select_related('order').get(account=account, pk=attempt_id)
    data = {'payment_attempt':_attempt_dict(current, current.order)}
    qr = payment_qr(account, order.pk)
    if qr:
        data['payment_qr'] = qr
    return JsonResponse(data)


def start_payment_query(request, order_id):
    from .views import payload
    if request.GET:
        raise ValueError('INVALID_REQUEST')
    payload(request, ())
    order = Order.objects.select_related('account', 'quote__product').filter(pk=order_id,
        account_id=request.session.get('active_account_id')).first()
    if not order:
        raise ValueError('NOT_FOUND')
    if _wechat_native_configuration(order, reconciliation=True) is None:
        return error(request, 'CHANNEL_UNAVAILABLE', 503)
    attempt = PaymentAttempt.objects.filter(order=order, dispatched_at__isnull=False).first()
    if not attempt:
        return error(request, 'CHANNEL_UNAVAILABLE', 503)
    return {'payment_query':str(attempt.pk), 'account':order.account}


def finish_payment_query(request, prepared):
    """Recheck the live financial session in a short transaction before query I/O."""
    from .account import _lock_current
    from .commerce.billing import _lock_account, _authorize_purchase
    from .models import Membership
    account = prepared['account']
    try:
        with transaction.atomic():
            _lock_current(request)
            selected = str(account.pk)
            if (request.session.load().get('active_account_id') != selected or
                    request.headers.get('X-Billing-Account') != selected):
                raise ValueError('ACCOUNT_CHANGED')
            account = _lock_account(account)
            if not Membership.objects.filter(account=account, user=request.user,
                    is_active=True, role__in=('owner','billing_admin')).exists():
                raise ValueError('FORBIDDEN')
            _authorize_purchase(account, request.user)
        query_wechat_payment(account, prepared['payment_query'])
        return JsonResponse(_wechat_order_result(account,
            PaymentAttempt.objects.get(account=account, pk=prepared['payment_query']).order_id))
    except Exception as exc:
        code = str(exc) if isinstance(exc, ValueError) else ''
        status = {'CHANNEL_UNAVAILABLE':503, 'AUTH_REQUIRED':401, 'FORBIDDEN':403,
            'ACCOUNT_CHANGED':409, 'NOT_FOUND':404}.get(code)
        if status is not None:
            return error(request, code, status)
        logging.getLogger('sage').error('wechat_query_failed request_id=%s', request.request_id)
        return error(request, 'PAYMENT_QUERY_FAILED', 500)


def _wechat_query_result(account, attempt, configuration):
    from .wechat import _rfc3339
    from .commerce.billing import record_confirmed_payment, record_attempt_event
    try:
        fields, evidence = _wechat_call(configuration, 'query', attempt)
    except ValueError:
        record_attempt_event(account, attempt.pk, 'wechat-query:'+uuid.uuid4().hex,
            'unknown', source='local', evidence_hash='')
        return 'unknown'
    outcome, fact = 'unknown', None
    try:
        if (type(fields) is not dict or any(fields.get(key) != value for key,value in (
                ('appid',configuration['app_id']), ('mchid',configuration['mch_id']),
                ('out_trade_no',attempt.pk.hex), ('trade_type','NATIVE'))) or
                type(fields.get('amount')) is not dict or
                type(fields['amount'].get('total')) is not int or
                fields['amount']['total'] != attempt.order.amount_minor or
                fields['amount'].get('currency') != 'CNY'):
            raise ValueError
        state = fields.get('trade_state')
        if state == 'SUCCESS':
            reference = fields['transaction_id']
            if type(reference) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', reference):
                raise ValueError
            paid_at = _rfc3339(fields['success_time'])
            if not attempt.dispatched_at.replace(microsecond=0) <= paid_at <= timezone.now():
                raise ValueError
            fact = dict(channel='wechat', merchant_reference=configuration['merchant_entity'],
                transaction_reference=reference, amount_minor=attempt.order.amount_minor,
                currency='CNY', paid_at=paid_at, evidence_hash=evidence)
            outcome = 'succeeded'
        elif state == 'NOTPAY' and not fields.get('success_time'):
            outcome = ('unknown' if attempt.events.filter(event_key='wechat-close-claim').exists()
                else 'pending')
        elif state == 'CLOSED' and not fields.get('success_time') and not fields.get('transaction_id'):
            # CLOSED from an independent authenticated query is terminal evidence.
            outcome = 'cancelled'
    except (ValueError, TypeError, KeyError, UnicodeError):
        outcome = 'unknown'
    if fact is not None:
        record_confirmed_payment(account, attempt.order_id, **fact)
    record_attempt_event(account, attempt.pk, 'wechat-query:'+evidence+':'+outcome,
        outcome, source='provider_query', evidence_hash=evidence)
    return outcome


def query_wechat_payment(account, attempt_id):
    """Only reconcile the original transaction; an expired QR is never reissued."""
    from .commerce.billing import _lock_account, record_attempt_event
    attempt, configuration = _wechat_context(account, attempt_id)
    outcome = _wechat_query_result(account, attempt, configuration)
    if outcome != 'pending' or attempt.expires_at > timezone.now():
        return
    with transaction.atomic():
        account = _lock_account(account)
        current = PaymentAttempt.objects.select_for_update().get(pk=attempt.pk, account=account)
        order = Order.objects.select_for_update().get(pk=current.order_id, account=account)
        dispatch_close = (not order.paid_at and not order.payment_facts.exists() and
            current.state == 'pending' and not current.events.filter(event_key='wechat-close-claim').exists())
        if dispatch_close:
            record_attempt_event(account, attempt.pk, 'wechat-close-claim', 'unknown', source='local', evidence_hash='')
    if dispatch_close:
        try:
            fields, evidence = _wechat_call(configuration, 'close', attempt)
            if fields is not None:
                raise ValueError
        except ValueError:
            record_attempt_event(account, attempt.pk, 'wechat-close-unknown', 'unknown', source='local', evidence_hash='')
        else:
            record_attempt_event(account, attempt.pk, 'wechat-close-accepted', 'unknown',
                source='provider_close', evidence_hash=evidence)
        _wechat_query_result(account, attempt, configuration)


@csrf_exempt
def wechat_native_webhook(request):
    """Exact test-only callback: authenticate raw bytes, persist funds, deliver, ACK."""
    from .wechat import verify_wechat_notification, wechat_payment_fact
    configuration = _wechat_native_configuration()
    if configuration is None:
        return error(request, 'CHANNEL_UNAVAILABLE', 503)
    if request.method != 'POST':
        response = HttpResponse(status=405)
        response['Allow'] = 'POST'
        return response
    if (request.META.get('QUERY_STRING') or request.content_type != 'application/json' or
            set(request.content_params)-{'charset'} or
            request.content_params.get('charset','utf-8').lower() != 'utf-8'):
        return HttpResponse(status=400)
    try:
        body = request.read(65537)
        now = int(timezone.now().timestamp())
        verify = dict(public_key=configuration['public_key'], public_key_id=configuration['public_key_id'],
            api_v3_key=configuration['api_v3_key'], now=now)
        fields = verify_wechat_notification(body, request.headers, **verify)
        reference = fields['out_trade_no']
        if type(reference) is not str or not re.fullmatch(r'[0-9a-f]{32}', reference):
            raise ValueError
        attempt = PaymentAttempt.objects.select_related('account').get(pk=uuid.UUID(hex=reference))
        attempt, original_configuration = _wechat_context(attempt.account, attempt.pk)
        if original_configuration != configuration:
            raise ValueError
        fact = wechat_payment_fact(body, request.headers, **verify, notification=True,
            appid=configuration['app_id'], mchid=configuration['mch_id'], reference=attempt.pk,
            amount_minor=attempt.order.amount_minor, merchant_reference=configuration['merchant_entity'])
        if not attempt.dispatched_at.replace(microsecond=0) <= fact['paid_at'] <= timezone.now():
            raise ValueError
    except (ValueError, TypeError, KeyError, UnicodeError, ValidationError, PaymentAttempt.DoesNotExist):
        return HttpResponse(status=400)
    except Exception:
        logging.getLogger('sage').error('wechat_callback_failed request_id=%s', request.request_id)
        return error(request, 'PAYMENT_CALLBACK_FAILED', 500)
    from .commerce.billing import record_attempt_event, record_confirmed_payment
    try:
        record_confirmed_payment(attempt.account, attempt.order_id, **fact)
        record_attempt_event(attempt.account, attempt.pk, 'wechat-callback:'+fact['evidence_hash'],
            'succeeded', source='verified_callback', evidence_hash=fact['evidence_hash'])
    except Exception:
        logging.getLogger('sage').error('wechat_callback_failed request_id=%s', request.request_id)
        return error(request, 'PAYMENT_CALLBACK_FAILED', 500)
    return HttpResponse(status=204)


def alipay_amount(amount_minor):
    """Integer fen to yuan within the official PC payment amount range."""
    if type(amount_minor) is not int or not 1 <= amount_minor <= 10000000000:
        raise ValueError('INVALID_AMOUNT')
    return f'{amount_minor // 100}.{amount_minor % 100:02d}'


def _alipay_reference(reference):
    if isinstance(reference, uuid.UUID):
        reference = str(reference)
    if not isinstance(reference, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', reference):
        raise ValueError('INVALID_REFERENCE')
    return reference


def build_alipay_request(operation, reference, *, amount_minor=None, refund_reference=None):
    """Build an official SDK request from stable server-owned references only."""
    content = {'out_trade_no': _alipay_reference(reference)}
    if operation == 'page':
        if refund_reference is not None:
            raise ValueError('INVALID_REQUEST')
        request = AlipayTradePagePayRequest()
        content.update(total_amount=alipay_amount(amount_minor),
            subject='Sage order '+content['out_trade_no'], product_code='FAST_INSTANT_TRADE_PAY')
    elif operation in ('refund', 'refund_query'):
        content['out_request_no'] = _alipay_reference(refund_reference)
        if operation == 'refund':
            request = AlipayTradeRefundRequest()
            content['refund_amount'] = alipay_amount(amount_minor)
        else:
            if amount_minor is not None:
                raise ValueError('INVALID_REQUEST')
            request = AlipayTradeFastpayRefundQueryRequest()
    elif operation in ('query', 'close'):
        if amount_minor is not None or refund_reference is not None:
            raise ValueError('INVALID_REQUEST')
        request = AlipayTradeQueryRequest() if operation == 'query' else AlipayTradeCloseRequest()
    else:
        raise ValueError('INVALID_REQUEST')
    request.biz_content = content
    return request


def alipay_sandbox_page_url(reference, amount_minor, *, app_id, private_key, expires_at=None):
    """Offline signed URL only in isolated tests; never enables website payment."""
    if settings.ENVIRONMENT != 'test':
        raise ValueError('CHANNEL_UNAVAILABLE')
    request = build_alipay_request('page', reference, amount_minor=amount_minor)
    try:
        if expires_at is not None:
            if not isinstance(expires_at, datetime) or timezone.is_naive(expires_at):
                raise ValueError
            content = request.biz_content.to_alipay_dict()
            content['time_expire'] = expires_at.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
            request.biz_content = content
        # Internal input caps, not claims about the provider's app-id length.
        if not isinstance(app_id, str) or not re.fullmatch(r'[0-9]{1,64}', app_id):
            raise ValueError
        key_data = private_key.encode('ascii') if isinstance(private_key, str) else private_key
        if (type(key_data) is not bytes or len(key_data) > 8192 or
                not key_data.startswith(b'-----BEGIN RSA PRIVATE KEY-----')):
            raise ValueError
        key = serialization.load_pem_private_key(key_data, password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
            raise ValueError
        config = AlipayClientConfig(sandbox_debug=True)
        config.app_id, config.app_private_key = app_id, key_data.decode('ascii')
        return DefaultAlipayClient(config, logger=None).page_execute(request, http_method='GET')
    except Exception:
        # The SDK includes request/material details in some exceptions.
        raise ValueError('ALIPAY_SIGNING_FAILED') from None


def _alipay_nonempty(value):
    # Java Character.isWhitespace excludes NBSP, figure space and narrow NBSP.
    # Only test emptiness; never trim a nonempty signed key or value.
    return bool(value) and not re.fullmatch(
        r'[\x09-\x0d\x1c-\x20\u1680\u2000-\u2006\u2008-\u200a\u2028-\u2029\u205f\u3000]*', value)


def _verify_alipay_signature(content, signature, public_key):
    signature = base64.b64decode(signature, validate=True)
    key_data = public_key.encode('ascii') if isinstance(public_key, str) else public_key
    if (type(key_data) is not bytes or len(key_data) > 8192 or
            not key_data.startswith(b'-----BEGIN PUBLIC KEY-----')):
        raise ValueError
    key = serialization.load_pem_public_key(key_data)
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
        raise ValueError
    key.verify(signature, content, padding.PKCS1v15(), hashes.SHA256())


def verify_alipay_notification(body, public_key):
    """Return signed fields only, without asserting identity, payment or funds."""
    try:
        if type(body) is not bytes or not 0 < len(body) <= 65536:
            raise ValueError
        text = body.decode('utf-8')
        if re.search(r'%(?![0-9A-Fa-f]{2})', text):
            raise ValueError
        pairs = parse_qsl(text, keep_blank_values=True, strict_parsing=True,
                          encoding='utf-8', errors='strict', max_num_fields=128)
        fields = dict(pairs)
        if len(pairs) != len(fields) or fields.get('sign_type') != 'RSA2':
            raise ValueError
        if fields.get('charset', 'utf-8').lower() != 'utf-8':
            raise ValueError
        signature = fields.pop('sign')
        fields.pop('sign_type')
        fields = {key: value for key, value in fields.items()
                  if _alipay_nonempty(key) and _alipay_nonempty(value)}
        content = '&'.join(f'{key}={fields[key]}' for key in sorted(fields)).encode('utf-8')
        _verify_alipay_signature(content, signature, public_key)
        return fields
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error, InvalidSignature, UnsupportedAlgorithm):
        raise ValueError('ALIPAY_NOTIFICATION_INVALID') from None


def verify_alipay_response(body, public_key, *, method):
    """Verify the original signed JSON slice; no identity or payment assertion.

    Official SDK execute permits SHA1 verification even with RSA2 configured.
    Never consume its decoded output instead of these raw transport bytes.
    """
    try:
        if (method not in ('alipay.trade.query', 'alipay.trade.close',
                'alipay.trade.refund', 'alipay.trade.fastpay.refund.query') or
                type(body) is not bytes or not 0 < len(body) <= 65536):
            raise ValueError
        text = body.decode('utf-8')

        def object_fields(pairs):
            fields = dict(pairs)
            if len(fields) != len(pairs):
                raise ValueError
            return fields

        def invalid_constant(value):
            raise ValueError

        def finite_float(value):
            result = float(value)
            if not math.isfinite(result):
                raise ValueError
            return result

        decoder = json.JSONDecoder(object_pairs_hook=object_fields,
            parse_constant=invalid_constant, parse_float=finite_float)
        envelope = decoder.decode(text)
        response_key = method.replace('.', '_')+'_response'
        if (type(envelope) is not dict or
                set(envelope) - {response_key, 'sign', 'sign_type'} or
                response_key not in envelope or type(envelope[response_key]) is not dict or
                type(envelope.get('sign')) is not str or
                envelope.get('sign_type', 'RSA2') != 'RSA2'):
            raise ValueError
        # Walk top-level members with the JSON parser, preserving the value's
        # exact source span. A response-name inside a string is never a node.
        cursor = text.index('{')+1
        signed_content = None
        whitespace = re.compile(r'[ \t\r\n]*')
        while True:
            cursor = whitespace.match(text, cursor).end()
            name, cursor = decoder.raw_decode(text, cursor)
            cursor = whitespace.match(text, cursor).end()+1
            cursor = whitespace.match(text, cursor).end()
            start = cursor
            _, cursor = decoder.raw_decode(text, cursor)
            if name == response_key:
                signed_content = text[start:cursor].encode('utf-8')
            cursor = whitespace.match(text, cursor).end()
            if text[cursor] == '}':
                break
            cursor += 1
        _verify_alipay_signature(signed_content, envelope['sign'], public_key)
        return envelope[response_key]
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error,
            InvalidSignature, UnsupportedAlgorithm, RecursionError):
        raise ValueError('ALIPAY_RESPONSE_INVALID') from None


def alipay_notification_fact(account, attempt_id, body, *, sandbox=False):
    """Map signed PC-payment notification evidence to the existing ledger input.

    This mapper never writes. Query JSON alone lacks signed app /
    seller identity and must not enter this notification boundary. An original
    dispatch claim and explicitly approved merchant identity/key are required.
    """
    from .commerce.billing import _identifier, _payment_snapshot
    try:
        attempt = None
        if sandbox:
            attempt = PaymentAttempt.objects.select_related('order__quote__product', 'order__account').filter(
                account=account, pk=attempt_id).first()
            if not attempt:
                raise ValueError
            configuration = _alipay_sandbox_configuration(attempt.order, reconciliation=True)
            if configuration is None or attempt.dispatch_authorization != _alipay_sandbox_identity(configuration):
                raise ValueError
            configuration = {**configuration, 'currency': 'CNY'}
        else:
            channels = settings.BILLING_APPROVED_CHANNELS
            configuration = channels.get('alipay') if isinstance(channels, dict) else None
        if (not isinstance(configuration, dict) or configuration.get('enabled') is not True or
                configuration.get('currency') != 'CNY'):
            raise ValueError
        for field in ('app_id', 'seller_id'):
            if (not isinstance(configuration.get(field), str) or
                    not re.fullmatch(r'[0-9]{1,64}', configuration[field])):
                raise ValueError
        merchant_reference = _identifier(configuration['merchant_entity'])
        fields = verify_alipay_notification(body, configuration['public_key'])
        if attempt is None:
            attempt = PaymentAttempt.objects.select_related('order__quote').filter(
                account=account, pk=attempt_id).first()
        if not attempt or not attempt.dispatched_at:
            raise ValueError
        identity = attempt.dispatch_authorization
        if identity is not None and not attempt.subscription_id:
            if (settings.ENVIRONMENT != 'test' or type(identity) is not dict or set(identity) != {
                    'channel', 'environment', 'app_id', 'seller_id', 'merchant_entity'} or
                    identity != _alipay_sandbox_identity(configuration) or
                    fields.get('app_id') != identity['app_id'] or
                    fields.get('seller_id') != identity['seller_id']):
                raise ValueError
        order = attempt.order
        snapshot = _payment_snapshot(order)
        if (order.account_id != account.pk or order.quote.account_id != account.pk or
                snapshot.get('channel') != 'alipay' or
                snapshot.get('merchant_entity') != merchant_reference or
                order.snapshot != order.quote.snapshot or
                type(snapshot.get('amount_minor')) is not int or
                snapshot['amount_minor'] != order.amount_minor or
                order.quote.amount_minor != order.amount_minor or
                order.currency != 'CNY' or order.quote.currency != 'CNY' or
                snapshot.get('currency') != 'CNY' or
                fields.get('app_id') != configuration['app_id'] or
                fields.get('seller_id') != configuration['seller_id'] or
                fields.get('out_trade_no') != str(attempt.pk) or
                fields.get('notify_type') != 'trade_status_sync' or
                fields.get('trade_status') not in ('TRADE_SUCCESS', 'TRADE_FINISHED') or
                fields.get('total_amount') != alipay_amount(order.amount_minor)):
            raise ValueError
        # Domestic PC payments bind CNY through the approved commercial terms.
        # Do not invent a mandatory currency callback field; if supplied, check it.
        if any(field in fields and fields[field] != 'CNY'
               for field in ('currency', 'trans_currency')):
            raise ValueError
        reference = _alipay_reference(fields['trade_no'])
        payment_time = fields['gmt_payment']
        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}', payment_time):
            raise ValueError
        # Official AlipayConstants: yyyy-MM-dd HH:mm:ss, GMT+8. Missing payer
        # time is insufficient evidence here; receipt time is not a substitute.
        paid_at = datetime.strptime(payment_time, '%Y-%m-%d %H:%M:%S').replace(
            tzinfo=datetime_timezone(timedelta(hours=8))).astimezone(datetime_timezone.utc)
        if not attempt.dispatched_at.replace(microsecond=0) <= paid_at <= timezone.now():
            raise ValueError
        return {'channel': 'alipay', 'merchant_reference': merchant_reference,
            'transaction_reference': reference, 'amount_minor': order.amount_minor, 'currency': 'CNY',
            'paid_at': paid_at, 'evidence_hash': hashlib.sha256(body).hexdigest()}
    except (ValueError, TypeError, KeyError, UnicodeError, ValidationError):
        raise ValueError('ALIPAY_PAYMENT_INVALID') from None


@csrf_exempt
def alipay_sandbox_webhook(request):
    """Exact test-only signed-form callback; never enabled by production gates.

    The provider's literal success acknowledgment follows receipt persistence,
    delivery and event recording. There is no session or outbound operation.
    """
    if request.method != 'POST':
        response = HttpResponse('failure', status=405, content_type='text/plain')
        response['Allow'] = 'POST'
        return response
    configuration = settings.BILLING_ALIPAY_SANDBOX
    if (settings.ENVIRONMENT != 'test' or type(configuration) is not dict or
            set(configuration) != {'enabled', 'app_id', 'seller_id', 'merchant_entity',
                'private_key', 'public_key', 'payment_ttl_seconds'} or
            configuration.get('enabled') is not True):
        return error(request, 'CHANNEL_UNAVAILABLE', 503)
    if (request.META.get('QUERY_STRING') or request.content_type != 'application/x-www-form-urlencoded' or
            set(request.content_params)-{'charset'} or
            request.content_params.get('charset', 'utf-8').lower() != 'utf-8'):
        return HttpResponse('failure', status=400, content_type='text/plain')
    try:
        # Do not let Django's form parser normalize duplicate fields or consume
        # an unlimited body. Signature verification uses these original bytes.
        body = request.read(65537)
        fields = verify_alipay_notification(body, configuration['public_key'])
        reference = uuid.UUID(fields['out_trade_no'])
        attempt = PaymentAttempt.objects.select_related('account').get(pk=reference)
        account = attempt.account
        _alipay_reconciliation_context(account, attempt.pk)
        fact = alipay_notification_fact(account, attempt.pk, body, sandbox=True)
    except (ValueError, TypeError, KeyError, UnicodeError, ValidationError, PaymentAttempt.DoesNotExist):
        return HttpResponse('failure', status=400, content_type='text/plain')
    from .commerce.billing import record_attempt_event, record_confirmed_payment
    try:
        record_confirmed_payment(account, attempt.order_id, **fact)
        record_attempt_event(account, attempt.pk, 'sandbox-callback:'+fact['evidence_hash'],
            'succeeded', source='verified_callback', evidence_hash=fact['evidence_hash'])
    except Exception:
        # A fixed HTTP failure requests a retry without exposing key, request,
        # payer or exception details; committed receipt evidence survives.
        logging.getLogger('sage').error('alipay_callback_failed request_id=%s', request.request_id)
        return error(request, 'PAYMENT_CALLBACK_FAILED', 500)
    return HttpResponse('success', content_type='text/plain; charset=utf-8')


def _alipay_reconciliation_context(account, attempt_id):
    if transaction.get_connection().in_atomic_block:
        raise ValueError('QUERY_REQUIRES_AUTOCOMMIT')
    try:
        attempt = PaymentAttempt.objects.select_related('order__quote__product', 'order__account').get(
            account=account, pk=attempt_id)
    except (PaymentAttempt.DoesNotExist, ValidationError, ValueError):
        raise ValueError('NOT_FOUND') from None
    if (not attempt.dispatched_at or attempt.subscription_id or attempt.recovery_of_id or
            type(attempt.dispatch_authorization) is not dict):
        raise ValueError('ALIPAY_QUERY_UNAVAILABLE')
    configuration = _alipay_sandbox_configuration(attempt.order, reconciliation=True)
    if configuration is None or attempt.dispatch_authorization != _alipay_sandbox_identity(configuration):
        raise ValueError('CHANNEL_UNAVAILABLE')
    return attempt, configuration


def _alipay_sandbox_call(configuration, operation, reference, *, amount_minor=None, refund_reference=None):
    """One TLS call, raw RSA2 verification; callers supply frozen server context.

    Public official SDK request/signature functions preserve its AOP contract.
    All signed parameters stay in the form body, away from URL access logs.
    """
    request = build_alipay_request(operation, reference, amount_minor=amount_minor,
        refund_reference=refund_reference)
    config = AlipayClientConfig(sandbox_debug=True)
    params = request.get_params()
    params.update(app_id=configuration['app_id'], charset=config.charset,
        format=config.format, sign_type='RSA2', timestamp=timezone.now().astimezone(
            ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S'))
    method = params['method']
    try:
        private_key = configuration['private_key']
        if isinstance(private_key, bytes):
            private_key = private_key.decode('ascii')
        params['sign'] = sign_with_rsa2(private_key, get_sign_content(params), config.charset)
    except Exception:
        raise ValueError('ALIPAY_SIGNING_FAILED') from None
    def reject_status(response, **kwargs):
        # A response hook runs before Session.send prepares Response.next and
        # eagerly reads redirect bodies even when allow_redirects is False.
        if response.status_code != 200:
            try:
                response.close()
            finally:
                raise ValueError
    try:
        with requests.Session() as session:
            session.trust_env = False
            with session.post(config.server_url, data=params, timeout=(5, 15),
                    verify=True, allow_redirects=False, stream=True, hooks={'response':reject_status}) as response:
                if response.status_code != 200:
                    raise ValueError
                chunks, length = [], 0
                for chunk in response.iter_content(chunk_size=8192):
                    length += len(chunk)
                    if length > 65536:
                        raise ValueError
                    chunks.append(chunk)
                body = b''.join(chunks)
        fields = verify_alipay_response(body, configuration['public_key'], method=method)
        return fields, hashlib.sha256(body).hexdigest()
    except (requests.RequestException, OSError, ValueError, UnicodeError):
        # No raw SDK/HTTP exception, body, signed form or payer data is logged.
        raise ValueError('ALIPAY_QUERY_UNKNOWN') from None


def _reconcile_alipay_payment(account, attempt, configuration, *, close_evidence=None):
    from .commerce.billing import record_attempt_event, record_confirmed_payment
    try:
        fields, evidence = _alipay_sandbox_call(configuration, 'query', attempt.pk)
    except ValueError:
        return record_attempt_event(account, attempt.pk, 'sandbox-query:'+uuid.uuid4().hex,
            'unknown', source='local', evidence_hash='')
    outcome, fact = 'unknown', None
    try:
        order = attempt.order
        if (fields.get('code') != '10000' or fields.get('out_trade_no') != str(attempt.pk) or
                fields.get('total_amount') != alipay_amount(order.amount_minor) or
                any(key in fields and fields[key] != 'CNY' for key in ('currency', 'trans_currency')) or
                any(key in fields and fields[key] != configuration[key] for key in ('app_id', 'seller_id'))):
            raise ValueError
        status = fields.get('trade_status')
        if status == 'WAIT_BUYER_PAY':
            outcome = 'pending'
        elif status in ('TRADE_SUCCESS', 'TRADE_FINISHED'):
            reference = _alipay_reference(fields['trade_no'])
            payment_time = fields['send_pay_date']
            if not isinstance(payment_time, str) or not re.fullmatch(
                    r'[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}', payment_time):
                raise ValueError
            seller_paid_at = datetime.strptime(payment_time, '%Y-%m-%d %H:%M:%S').replace(
                tzinfo=datetime_timezone(timedelta(hours=8))).astimezone(datetime_timezone.utc)
            if not attempt.dispatched_at.replace(microsecond=0) <= seller_paid_at <= timezone.now():
                raise ValueError
            # send_pay_date is the seller's settlement time, not the buyer's
            # canonical gmt_payment. A query cannot invent a new paid_at. Only
            # reuse an immutable, exactly matching receipt already in the ledger.
            existing = PaymentFact.objects.filter(channel='alipay',
                merchant_reference=configuration['merchant_entity'], transaction_reference=reference).first()
            if (not existing or existing.order_id != order.pk or
                    existing.amount_minor != order.amount_minor or existing.currency != 'CNY'):
                raise ValueError
            fact = {'channel': 'alipay', 'merchant_reference': configuration['merchant_entity'],
                'transaction_reference': reference, 'amount_minor': order.amount_minor,
                'currency': 'CNY', 'paid_at': existing.paid_at, 'evidence_hash': evidence}
            outcome = 'succeeded'
        elif status == 'TRADE_CLOSED' and close_evidence and not fields.get('send_pay_date'):
            # CLOSED alone can also mean a fully refunded payment. Require the
            # verified successful close of this original transaction as well.
            outcome = 'cancelled'
            evidence = hashlib.sha256((close_evidence+':'+evidence).encode('ascii')).hexdigest()
    except (ValueError, TypeError, KeyError, UnicodeError):
        outcome = 'unknown'
    if fact is not None:
        # Delivery exceptions propagate only AFTER the actual receipt commits.
        record_confirmed_payment(account, attempt.order_id, **fact)
    return record_attempt_event(account, attempt.pk, 'sandbox-query:'+evidence+':'+outcome,
        outcome, source='provider_query', evidence_hash=evidence)


def query_alipay_payment(account, attempt_id):
    """Query only a previously claimed sandbox transaction; never create a charge.

    Query JSON has no required signed app/seller identity. Trust comes from this
    original authenticated request with the dispatch's frozen merchant context,
    followed by raw RSA2 response verification and exact transaction matching.
    First receipt creation awaits canonical gmt_payment evidence; seller payment
    time from a query can only support reconciliation of an existing receipt.
    """
    attempt, configuration = _alipay_reconciliation_context(account, attempt_id)
    return _reconcile_alipay_payment(account, attempt, configuration)


def close_alipay_payment(account, attempt_id):
    """Close the original sandbox transaction, then independently query its state."""
    from .commerce.billing import _lock_account, record_attempt_event
    attempt, configuration = _alipay_reconciliation_context(account, attempt_id)
    # A crash/timeout after this committed claim may only query, never reclose.
    with transaction.atomic():
        account = _lock_account(account)
        current = PaymentAttempt.objects.select_for_update().get(pk=attempt.pk, account=account)
        order = Order.objects.select_for_update().get(pk=current.order_id, account=account)
        accepted = current.events.filter(event_key='sandbox-close-accepted',
            source='provider_close', outcome='unknown').first()
        close_evidence = accepted.evidence_hash if accepted else None
        dispatch_close = (not order.paid_at and current.state not in (
            'succeeded', 'cancelled', 'expired', 'review') and
            not current.events.filter(event_key='sandbox-close-claim').exists())
        if dispatch_close:
            record_attempt_event(account, attempt.pk, 'sandbox-close-claim',
                'unknown', source='local', evidence_hash='')
    if dispatch_close:
        try:
            fields, evidence = _alipay_sandbox_call(configuration, 'close', attempt.pk)
            if (fields.get('code') == '10000' and fields.get('out_trade_no') == str(attempt.pk) and
                    not any(key in fields and fields[key] != configuration[key] for key in ('app_id', 'seller_id'))):
                close_evidence = evidence
        except ValueError:
            evidence = None
        if close_evidence:
            record_attempt_event(account, attempt.pk, 'sandbox-close-accepted',
                'unknown', source='provider_close', evidence_hash=close_evidence)
        else:
            # Persist uncertainty before querying; never retry the close here.
            record_attempt_event(account, attempt.pk, 'sandbox-close:'+uuid.uuid4().hex,
                'unknown', source='provider_close' if evidence else 'local', evidence_hash=evidence or '')
    return _reconcile_alipay_payment(account, attempt, configuration, close_evidence=close_evidence)


def _alipay_refund_context(account, refund_id):
    """Read the original refund bindings, also rechecked under the account lock."""
    from .commerce.models import CreditRefund
    try:
        refund = CreditRefund.objects.select_related('payment__order__quote__product',
            'payment__order__account', 'freeze__batch').get(
            payment__order__account=account, pk=refund_id)
    except (CreditRefund.DoesNotExist, ValidationError, ValueError):
        raise ValueError('NOT_FOUND') from None
    payment, hold = refund.payment, refund.freeze
    if (refund.state not in ('reserved', 'refunded') or refund.review_state != 'approved' or
            not isinstance(refund.review_evidence_hash, str) or
            not re.fullmatch(r'[a-f0-9]{64}', refund.review_evidence_hash) or
            payment.disposition != 'accepted' or payment.channel != 'alipay' or payment.currency != 'CNY' or
            payment.amount_minor != payment.order.amount_minor or not 0 < refund.amount_minor <= payment.amount_minor or
            hold.batch.account_id != account.pk or hold.batch.order_id != payment.order_id or
            hold.batch.source != 'purchased' or
            hold.request_key != 'money-refund:'+hashlib.sha256(refund.request_key.encode()).hexdigest() or
            hold.state != ('frozen' if refund.state == 'reserved' else 'refunded')):
        raise ValueError('ALIPAY_REFUND_QUERY_UNAVAILABLE')
    attempt = PaymentAttempt.objects.filter(account=account, order_id=payment.order_id).first()
    if (not attempt or not attempt.dispatched_at or attempt.subscription_id or attempt.recovery_of_id or
            type(attempt.dispatch_authorization) is not dict):
        raise ValueError('ALIPAY_REFUND_QUERY_UNAVAILABLE')
    configuration = _alipay_sandbox_configuration(payment.order, reconciliation=True)
    if configuration is None or attempt.dispatch_authorization != _alipay_sandbox_identity(configuration):
        raise ValueError('CHANNEL_UNAVAILABLE')
    if payment.merchant_reference != configuration['merchant_entity']:
        raise ValueError('ALIPAY_REFUND_QUERY_UNAVAILABLE')
    return refund, attempt, configuration


def submit_alipay_refund(account, refund_id):
    """Claim one test-only sandbox refund before I/O; every reentry only queries.

    The claim survives signing failures, timeouts and process crashes. A refund
    response never resolves credits: only independently verified REFUND_SUCCESS
    from the original refund query may do that.
    """
    from .commerce.billing import _credit_refund_dict, _lock_account, _balances, _quantity, record_attempt_event
    from .commerce.models import CreditRefund
    if transaction.get_connection().in_atomic_block:
        raise ValueError('QUERY_REQUIRES_AUTOCOMMIT')
    with transaction.atomic():
        account = _lock_account(account)
        refund, attempt, configuration = _alipay_refund_context(account, refund_id)
        if refund.state == 'refunded':
            return _credit_refund_dict(refund)
        claim_key = 'sandbox-refund-claim:'+str(refund.pk)
        dispatch = not attempt.events.filter(event_key=claim_key).exists()
        if dispatch:
            payment, hold = refund.payment, refund.freeze
            committed = sum(CreditRefund.objects.filter(payment=payment).exclude(state='released')
                .values_list('amount_minor', flat=True))
            if (attempt.state != 'succeeded' or attempt.needs_reconciliation or
                    payment.order.status != 'fulfilled' or not payment.order.paid_at or
                    payment.order.payment_facts.count() != 1 or committed > payment.amount_minor or
                    _balances(hold.batch)['frozen'] < _quantity(hold.quantity, positive=True)):
                raise ValueError('ALIPAY_REFUND_QUERY_UNAVAILABLE')
            record_attempt_event(account, attempt.pk, claim_key, 'unknown', source='local', evidence_hash='')
    if dispatch:
        try:
            _alipay_sandbox_call(configuration, 'refund', attempt.pk,
                amount_minor=refund.amount_minor, refund_reference=refund.pk)
        except ValueError:
            # The durable claim forbids resubmission even if signing failed.
            record_attempt_event(account, attempt.pk,
                'sandbox-refund-submit-unknown:'+str(refund.pk), 'unknown', source='local', evidence_hash='')
    return query_alipay_refund(account, refund.pk)


def query_alipay_refund(account, refund_id):
    """Query a reviewed frozen refund; this function cannot submit a refund.

    The persistent refund UUID is its only out_request_no. Missing or uncertain
    provider status leaves the allocation frozen, never released or retried.
    """
    from .commerce.billing import _credit_refund_dict, _lock_account, resolve_credit_refund
    from .commerce.models import CreditRefund
    if transaction.get_connection().in_atomic_block:
        raise ValueError('QUERY_REQUIRES_AUTOCOMMIT')
    refund, attempt, configuration = _alipay_refund_context(account, refund_id)
    payment = refund.payment
    if refund.state == 'refunded':
        return _credit_refund_dict(refund)
    try:
        fields, evidence = _alipay_sandbox_call(configuration, 'refund_query', attempt.pk,
            refund_reference=refund.pk)
        if (fields.get('code') != '10000' or fields.get('refund_status') != 'REFUND_SUCCESS' or
                fields.get('out_trade_no') != str(attempt.pk) or
                fields.get('out_request_no') != str(refund.pk) or
                fields.get('trade_no') != payment.transaction_reference or
                fields.get('total_amount') != alipay_amount(payment.amount_minor) or
                fields.get('refund_amount') != alipay_amount(refund.amount_minor) or
                fields.get('error_code') or
                any(key in fields and fields[key] != 'CNY' for key in ('currency', 'trans_currency')) or
                any(key in fields and fields[key] != configuration[key] for key in ('app_id', 'seller_id'))):
            raise ValueError
    except ValueError:
        return {**_credit_refund_dict(refund), 'query_status': 'unknown'}
    # Only DB reconciliation holds locks; the completed TLS query never does.
    # A competing verified query can have a different raw response hash.
    with transaction.atomic():
        account = _lock_account(account)
        latest = CreditRefund.objects.select_for_update().get(pk=refund.pk)
        if latest.state == 'refunded':
            return _credit_refund_dict(latest)
        return resolve_credit_refund(account, refund.pk, 'refunded', evidence_hash=evidence)
