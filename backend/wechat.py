"""WeChat Pay APIv3 Native protocol and bounded transport; no ledger writes.

Fixed WeChat public-key mode only. Configuration and frozen order expectations
must come from the server; these functions grant no merchant/product authority.
"""
import base64
import binascii
import hashlib
import json
import math
import re
import uuid
from datetime import datetime
from urllib.parse import quote, unquote, urlsplit

import requests
from cryptography.exceptions import InvalidSignature, InvalidTag, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _text(value, pattern):
    if type(value) is not str or not re.fullmatch(pattern, value):
        raise ValueError
    return value


def _reference(value, *, refund=False):
    if isinstance(value, uuid.UUID):
        value = value.hex
    return _text(value, r'[A-Za-z0-9_|*@-]{1,64}' if refund else r'[A-Za-z0-9_|*-]{6,32}')


def _amount(value):
    # Bound the API's integer field; bool, float and yuan strings are not fen.
    if type(value) is not int or not 1 <= value <= 2147483647:
        raise ValueError
    return value


def _rfc3339(value):
    _text(value, r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
        r'(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def build_wechat_request(operation, reference, *, mchid, merchant_serial, private_key,
        public_key_id, timestamp, nonce, appid=None, amount_minor=None, description=None,
        notify_origin=None, time_expire=None, refund_reference=None, refund_minor=None):
    """Return method/url/headers/body, never send. Send body via data, not json.

    Merchant certificate serial signs Authorization; Wechatpay-Serial selects
    the separately configured WeChat public key. UUID references use stable hex.
    notify_origin is trusted server configuration, never a browser-supplied URL.
    """
    try:
        _text(mchid, r'[0-9]{1,32}')
        _text(merchant_serial, r'[0-9A-Fa-f]{1,64}')
        _text(public_key_id, r'PUB_KEY_ID_[0-9]{1,64}')
        _text(nonce, r'[A-Za-z0-9_-]{1,32}')
        if type(timestamp) is not int or not 0 < timestamp < 10**12:
            raise ValueError
        reference = _reference(reference)
        path = '/v3/pay/transactions/out-trade-no/'+quote(reference, safe='')
        method, content = 'POST', None
        if operation == 'native':
            if refund_reference is not None or refund_minor is not None:
                raise ValueError
            _text(appid, r'[A-Za-z0-9_-]{1,32}')
            if type(description) is not str or not 1 <= len(description) <= 127:
                raise ValueError
            if any(ord(char) < 32 for char in description):
                raise ValueError
            if type(notify_origin) is not str:
                raise ValueError
            origin = urlsplit(notify_origin)
            if (origin.scheme != 'https' or not origin.hostname or origin.username is not None or
                    origin.password is not None or origin.path or origin.query or origin.fragment or
                    not re.fullmatch(r'https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?', notify_origin)):
                raise ValueError
            notify_url = notify_origin+'/api/payments/webhooks/wechat'
            if len(notify_url) > 255 or (origin.port is not None and not 1 <= origin.port <= 65535):
                raise ValueError
            _rfc3339(time_expire)
            path = '/v3/pay/transactions/native'
            content = dict(appid=appid, mchid=mchid, description=description,
                out_trade_no=reference, notify_url=notify_url,
                amount=dict(total=_amount(amount_minor), currency='CNY'), time_expire=time_expire)
        elif operation in ('query', 'close', 'refund', 'refund_query'):
            if any(value is not None for value in (appid, description, notify_origin, time_expire)):
                raise ValueError
            if operation in ('query', 'close'):
                if any(value is not None for value in (amount_minor, refund_reference, refund_minor)):
                    raise ValueError
                if operation == 'query':
                    method, path = 'GET', path+'?mchid='+mchid
                else:
                    path, content = path+'/close', dict(mchid=mchid)
            else:
                refund_reference = _reference(refund_reference, refund=True)
                path = '/v3/refund/domestic/refunds'
                if operation == 'refund_query':
                    if amount_minor is not None or refund_minor is not None:
                        raise ValueError
                    method, path = 'GET', path+'/'+quote(refund_reference, safe='')
                else:
                    if _amount(refund_minor) > _amount(amount_minor):
                        raise ValueError
                    content = dict(out_trade_no=reference, out_refund_no=refund_reference,
                        amount=dict(refund=refund_minor, total=amount_minor, currency='CNY'))
        else:
            raise ValueError
        body = b'' if content is None else json.dumps(content, ensure_ascii=False,
            separators=(',', ':'), allow_nan=False).encode('utf-8')
        key_data = private_key.encode('ascii') if type(private_key) is str else private_key
        if type(key_data) is not bytes or not 0 < len(key_data) <= 8192:
            raise ValueError
        key = serialization.load_pem_private_key(key_data, password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
            raise ValueError
        canonical = f'{method}\n{path}\n{timestamp}\n{nonce}\n'.encode()+body+b'\n'
        signature = base64.b64encode(key.sign(canonical, padding.PKCS1v15(), hashes.SHA256())).decode()
        authorization = (f'WECHATPAY2-SHA256-RSA2048 mchid="{mchid}",nonce_str="{nonce}",'
            f'signature="{signature}",timestamp="{timestamp}",serial_no="{merchant_serial}"')
        return dict(method=method, url='https://api.mch.weixin.qq.com'+path,
            headers={'Authorization':authorization, 'Wechatpay-Serial':public_key_id,
                'Accept':'application/json', 'Content-Type':'application/json'}, body=body)
    except (ValueError, TypeError, UnicodeError, UnsupportedAlgorithm):
        raise ValueError('WECHAT_REQUEST_INVALID') from None


def _native_code_url(code_url):
    _text(code_url, r'[\x21-\x7e]{1,2048}')
    decoded = unquote(code_url)
    parsed = urlsplit(code_url)
    # Project-local safety policy, not an official query-parameter contract.
    # Never turn the scan payload into a webpage redirect.
    if (not code_url.startswith('weixin://') or parsed.netloc != 'wxpay' or
            not parsed.path.startswith('/') or len(parsed.path) <= 1 or '#' in code_url or
            '\\' in decoded or not re.fullmatch(r'[\x21-\x7e]+', decoded)):
        raise ValueError
    return code_url


def wechat_qr_image(code_url):
    """Render a local Native scan payload; this creates no payment fact."""
    import qrcode
    from qrcode.exceptions import DataOverflowError
    from qrcode.image.svg import SvgPathFillImage

    try:
        payload = _native_code_url(code_url)
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
        qr.add_data(payload.encode('ascii'))
        qr.make(fit=True)
        image = qr.make_image(image_factory=SvgPathFillImage)
        image.get_image().set('width', '320')
        image.get_image().set('height', '320')
        return base64.b64encode(image.to_string()).decode('ascii')
    except (ValueError, TypeError, UnicodeError, DataOverflowError):
        raise ValueError('WECHAT_QR_INVALID') from None


def call_wechat_api(operation, reference, *, public_key, now, **request_fields):
    """One authenticated APIv3 call from trusted server fields, never a retry.

    The caller must durably claim money operations before calling and reconcile
    unknown results using original IDs. Native is wired only for local tests.
    Accepted Native/refund responses are not payment/refund confirmation.
    """
    try:
        if type(now) is not int:
            raise ValueError
        request = build_wechat_request(operation, reference, **request_fields)
    except (TypeError, ValueError):
        raise ValueError('WECHAT_REQUEST_INVALID') from None
    def reject_status(response, **kwargs):
        # Session.send prepares Response.next even with redirects disabled;
        # reject before that preparation can consume an unbounded error body.
        if not 200 <= response.status_code < 300:
            try:
                response.close()
            finally:
                raise ValueError
    try:
        with requests.Session() as session:
            session.trust_env = False
            with session.request(request['method'], request['url'], headers=request['headers'],
                    data=request['body'], timeout=(5, 15), verify=True,
                    allow_redirects=False, stream=True, hooks={'response':reject_status}) as response:
                if not 200 <= response.status_code < 300:
                    raise ValueError
                chunks, length = [], 0
                for chunk in response.iter_content(chunk_size=8192):
                    length += len(chunk)
                    if length > 65536:
                        raise ValueError
                    chunks.append(chunk)
                body = b''.join(chunks)
                # requests' response.headers merges duplicates. Preserve raw
                # urllib3 header pairs so duplicate signature fields are denied.
                fields = verify_wechat_response(body, response.raw.headers.items(),
                    public_key=public_key, public_key_id=request_fields['public_key_id'], now=now)
        if operation == 'native':
            _native_code_url(fields.get('code_url') if type(fields) is dict else None)
        return fields, hashlib.sha256(body).hexdigest()
    except (requests.RequestException, OSError, ValueError, UnicodeError):
        # Never expose response bodies, request Authorization or key material.
        raise ValueError('WECHAT_CALL_UNKNOWN') from None


def _json_object(body):
    def object_fields(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError
        return result

    def invalid_constant(value):
        raise ValueError

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError
        return result

    if type(body) is not bytes or not 0 < len(body) <= 65536:
        raise ValueError
    result = json.loads(body.decode('utf-8'), object_pairs_hook=object_fields,
        parse_constant=invalid_constant, parse_float=finite_float)
    if type(result) is not dict:
        raise ValueError
    return result


def verify_wechat_response(body, headers, *, public_key, public_key_id, now):
    """Authenticate raw bytes before strict JSON parsing; signed empty 204 is None.

    Pass raw header pairs where available so duplicates remain detectable.
    No certificate fallback, automatic key download, or unsigned error exception.
    """
    try:
        if type(body) is not bytes or len(body) > 65536 or type(now) is not int:
            raise ValueError
        _text(public_key_id, r'PUB_KEY_ID_[0-9]{1,64}')
        fields = {}
        for name, value in headers.items() if hasattr(headers, 'items') else headers:
            if type(name) is not str:
                raise ValueError
            name = name.lower()
            if name not in ('wechatpay-timestamp', 'wechatpay-nonce', 'wechatpay-serial', 'wechatpay-signature'):
                continue
            if name in fields or type(value) is not str or not value or len(value) > 1024:
                raise ValueError
            fields[name] = value
        timestamp = _text(fields['wechatpay-timestamp'], r'[0-9]{1,12}')
        nonce = _text(fields['wechatpay-nonce'], r'[A-Za-z0-9_-]{1,128}')
        if fields['wechatpay-serial'] != public_key_id or abs(now-int(timestamp)) >= 300:
            raise ValueError
        signature = base64.b64decode(fields['wechatpay-signature'], validate=True)
        key_data = public_key.encode('ascii') if type(public_key) is str else public_key
        if (type(key_data) is not bytes or len(key_data) > 8192 or
                not key_data.startswith(b'-----BEGIN PUBLIC KEY-----')):
            raise ValueError
        key = serialization.load_pem_public_key(key_data)
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
            raise ValueError
        key.verify(signature, timestamp.encode()+b'\n'+nonce.encode()+b'\n'+body+b'\n',
            padding.PKCS1v15(), hashes.SHA256())
        return _json_object(body) if body else None
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error,
            InvalidSignature, UnsupportedAlgorithm, RecursionError):
        raise ValueError('WECHAT_RESPONSE_INVALID') from None


def verify_wechat_notification(body, headers, *, public_key, public_key_id, now, api_v3_key):
    """Authenticate and decrypt only; the result alone is not a payment fact."""
    try:
        envelope = verify_wechat_response(body, headers,
            public_key=public_key, public_key_id=public_key_id, now=now)
        if (type(envelope) is not dict or envelope.get('resource_type') != 'encrypt-resource' or
                envelope.get('event_type') != 'TRANSACTION.SUCCESS' or
                type(api_v3_key) is not bytes or len(api_v3_key) != 32):
            raise ValueError
        resource = envelope['resource']
        if type(resource) is not dict or resource.get('algorithm') != 'AEAD_AES_256_GCM':
            raise ValueError
        nonce, aad = resource['nonce'], resource.get('associated_data', '')
        if type(nonce) is not str or len(nonce.encode('utf-8')) != 12 or type(aad) is not str:
            raise ValueError
        ciphertext = base64.b64decode(resource['ciphertext'], validate=True)
        plaintext = AESGCM(api_v3_key).decrypt(nonce.encode('utf-8'), ciphertext, aad.encode('utf-8'))
        return _json_object(plaintext)
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error, InvalidTag, RecursionError):
        raise ValueError('WECHAT_NOTIFICATION_INVALID') from None


def wechat_payment_fact(body, headers, *, public_key, public_key_id, now,
        appid, mchid, reference, amount_minor, merchant_reference,
        notification=False, api_v3_key=None):
    """Verify then map a Native SUCCESS to existing ledger fields; never persist.

    Identity/reference/amount are frozen server order expectations. mchid is the
    platform merchant; merchant_reference is the existing legal-entity mapping.
    The caller must still reconcile the original order/attempt before recording.
    """
    try:
        _text(appid, r'[A-Za-z0-9_-]{1,32}')
        _text(mchid, r'[0-9]{1,32}')
        reference, amount_minor = _reference(reference), _amount(amount_minor)
        if (type(merchant_reference) is not str or not 1 <= len(merchant_reference) <= 128 or
                not merchant_reference.strip() or any(ord(char) < 32 for char in merchant_reference) or
                type(notification) is not bool):
            raise ValueError
        verify_args = dict(public_key=public_key, public_key_id=public_key_id, now=now)
        transaction = (verify_wechat_notification(body, headers, api_v3_key=api_v3_key, **verify_args)
            if notification else verify_wechat_response(body, headers, **verify_args))
        if type(transaction) is not dict or any(transaction.get(field) != expected for field, expected in (
                ('appid', appid), ('mchid', mchid), ('out_trade_no', reference),
                ('trade_type', 'NATIVE'), ('trade_state', 'SUCCESS'))):
            raise ValueError
        amount = transaction['amount']
        if (type(amount) is not dict or type(amount.get('total')) is not int or
                amount['total'] != amount_minor or amount.get('currency') != 'CNY'):
            raise ValueError
        transaction_reference = _text(transaction['transaction_id'], r'[A-Za-z0-9_-]{1,32}')
        paid_at = _rfc3339(transaction['success_time'])
        return dict(channel='wechat', merchant_reference=merchant_reference,
            transaction_reference=transaction_reference, amount_minor=amount_minor,
            currency='CNY', paid_at=paid_at, evidence_hash=hashlib.sha256(body).hexdigest())
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError('WECHAT_PAYMENT_INVALID') from None
