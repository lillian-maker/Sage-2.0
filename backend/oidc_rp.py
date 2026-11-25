"""Website OIDC RP for Logto.

The browser talks to this module only through same-origin JSON endpoints. The
authorization code and ID token stay server-side; the Django session remains
the website session. Every failure is fail-closed and returns a fixed code.
"""
import base64
import hashlib
import json
import logging
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from google.auth import exceptions as google_exceptions
from google.auth import jwt as google_jwt
from django.conf import settings
from django.contrib.auth import login, logout as django_logout
from django.db import DatabaseError, transaction
from django.http import HttpResponse, JsonResponse
from django.utils.crypto import constant_time_compare

from .account import register_session, revoke_current_session
from .http import error
from .identity_link import IdentityLinkError, resolve_or_link
from .models import AuditEvent
from .views import digest, payload

logger = logging.getLogger('sage')

CALLBACK_PATH = '/api/auth/oidc/callback'
FLOW_SESSION_KEY = 'oidc_flow'
ID_TOKEN_SESSION_KEY = 'oidc_id_token'
HTTP_TIMEOUT_SECONDS = 10
MAX_DOCUMENT_BYTES = 65536
MAX_ID_TOKEN_BYTES = 16384
MAX_JWKS_KEYS = 20
SUBJECT_PATTERN = re.compile(r'^[\x21-\x7e]{1,255}$')
NAME_MAX_LENGTH = 150


class OidcFailure(Exception):
    """Named failure; never carries provider text or tokens."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _host_allowed(host):
    if not host:
        return False
    host = host.lower()
    for allowed in settings.ALLOWED_HOSTS:
        allowed = str(allowed).lower()
        if allowed == '*' or host == allowed or (allowed.startswith('.') and host.endswith(allowed)):
            return True
    return False


def _https_url(value, *, path=None):
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password:
        return False
    if parsed.query or parsed.fragment:
        return False
    if path is not None and parsed.path != path:
        return False
    return True


def ready():
    """Configuration is complete enough to start a login; no network is used."""
    if not getattr(settings, 'AUTH_OIDC_ENABLED', False):
        return False
    if not all((settings.OIDC_ISSUER, settings.OIDC_CLIENT_ID, settings.OIDC_CLIENT_SECRET,
                settings.OIDC_REDIRECT_URI, settings.OIDC_POST_LOGOUT_REDIRECT_URI)):
        return False
    if not _https_url(settings.OIDC_ISSUER):
        return False
    redirect = urlsplit(settings.OIDC_REDIRECT_URI)
    if not _https_url(settings.OIDC_REDIRECT_URI, path=CALLBACK_PATH):
        return False
    if not _host_allowed(redirect.hostname):
        return False
    post_logout = urlsplit(settings.OIDC_POST_LOGOUT_REDIRECT_URI)
    if not _https_url(settings.OIDC_POST_LOGOUT_REDIRECT_URI) or not _host_allowed(post_logout.hostname):
        return False
    scopes = set(settings.OIDC_SCOPE.split())
    return 'openid' in scopes and 'email' in scopes and 'offline_access' not in scopes


def _b64url_decode(value):
    if not isinstance(value, str):
        raise OidcFailure('OIDC_REJECTED')
    try:
        return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise OidcFailure('OIDC_REJECTED') from exc


def _jwk_pem(key):
    if not isinstance(key, dict) or key.get('use') not in (None, 'sig'):
        return None
    kid = key.get('kid')
    if not isinstance(kid, str) or not kid:
        return None
    try:
        if key.get('kty') == 'RSA':
            if key.get('alg') not in (None, 'RS256'):
                return None
            n = int.from_bytes(_b64url_decode(key.get('n')), 'big')
            e = int.from_bytes(_b64url_decode(key.get('e')), 'big')
            public = rsa.RSAPublicNumbers(e, n).public_key()
        elif key.get('kty') == 'EC':
            curves = {'P-256': (ec.SECP256R1, 'ES256'), 'P-384': (ec.SECP384R1, 'ES384')}
            curve_info = curves.get(key.get('crv'))
            if curve_info is None or key.get('alg') not in (None, curve_info[1]):
                return None
            x = int.from_bytes(_b64url_decode(key.get('x')), 'big')
            y = int.from_bytes(_b64url_decode(key.get('y')), 'big')
            public = ec.EllipticCurvePublicNumbers(x, y, curve_info[0]()).public_key()
        else:
            return None
        return kid, public.public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    except (ValueError, TypeError, OidcFailure):
        return None


def _jwt_header(raw):
    parts = raw.split('.')
    if len(parts) != 3:
        raise OidcFailure('OIDC_REJECTED')
    try:
        header = json.loads(_b64url_decode(parts[0]).decode('utf-8'))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OidcFailure('OIDC_REJECTED') from exc
    if not isinstance(header, dict):
        raise OidcFailure('OIDC_REJECTED')
    return header


def _fetch_json(url, *, max_bytes):
    try:
        response = requests.get(url, timeout=HTTP_TIMEOUT_SECONDS, allow_redirects=False)
        if response.status_code != 200 or len(response.content) > max_bytes:
            raise OidcFailure('OIDC_UNAVAILABLE')
        document = response.json()
    except OidcFailure:
        raise
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise OidcFailure('OIDC_UNAVAILABLE') from exc
    if not isinstance(document, dict):
        raise OidcFailure('OIDC_UNAVAILABLE')
    return document


def _discovery():
    base = settings.OIDC_ISSUER.rstrip('/')
    document = _fetch_json(base + '/.well-known/openid-configuration', max_bytes=MAX_DOCUMENT_BYTES)
    if document.get('issuer') != settings.OIDC_ISSUER:
        raise OidcFailure('OIDC_UNAVAILABLE')
    for key in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
        if not _https_url(document.get(key)):
            raise OidcFailure('OIDC_UNAVAILABLE')
    if 'end_session_endpoint' in document and not _https_url(document.get('end_session_endpoint')):
        raise OidcFailure('OIDC_UNAVAILABLE')
    return document


def _safe_return_to(value, language):
    from .google_auth import safe_return
    language = language if language in ('zh', 'en') else 'zh'
    return safe_return(value, language)


def _agreement_version(value):
    if value is True and settings.AUTH_REGISTRATION_TERMS_READY:
        version = settings.AUTH_REGISTRATION_TERMS_VERSION
        return version if isinstance(version, str) and 1 <= len(version) <= 128 else None
    if (isinstance(value, str) and settings.AUTH_REGISTRATION_TERMS_READY and
            value == settings.AUTH_REGISTRATION_TERMS_VERSION and value == value.strip() and
            1 <= len(value) <= 128):
        return value
    return None


def _flow_error(request, code, language='zh'):
    """One-shot failure redirect. The query carries only a fixed code."""
    path = '/pages/auth-en.html' if language == 'en' else '/pages/auth.html'
    response = HttpResponse(status=303)
    response['Location'] = path + '?oidc=' + code
    _clear_callback_query(request)
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response


def _clear_callback_query(request):
    request.META['QUERY_STRING'] = ''
    for key in ('RAW_URI', 'REQUEST_URI'):
        if key in request.META:
            request.META[key] = request.path


def _redirect(request, target):
    response = HttpResponse(status=303)
    response['Location'] = target
    _clear_callback_query(request)
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response


def start(request):
    if request.user.is_authenticated:
        return error(request, 'ALREADY_AUTHENTICATED', 409, '当前已登录。')
    data = payload(request, ('return_to', 'language'), optional=('intent', 'agreement_acceptance'))
    language = data.get('language')
    if language not in ('zh', 'en'):
        return error(request, 'INVALID_REQUEST', 400)
    intent = data.get('intent', 'login')
    if intent not in ('login', 'register'):
        return error(request, 'INVALID_REQUEST', 400)
    agreement_version = _agreement_version(data.get('agreement_acceptance'))
    if intent == 'register' and agreement_version is None:
        return error(request, 'TERMS_UNAVAILABLE', 422, '注册条款尚未就绪。')
    if not ready():
        return error(request, 'OIDC_UNAVAILABLE', 503, '统一登录尚未就绪。')

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    try:
        discovery = _discovery()
    except OidcFailure as exc:
        return error(request, exc.code, 503, '统一登录暂不可用。')

    request.session[FLOW_SESSION_KEY] = {
        'state_digest': digest(state),
        'nonce_digest': digest(nonce),
        'code_verifier': verifier,
        'return_to': _safe_return_to(data['return_to'], language),
        'language': language,
        'intent': intent,
        'agreement_version': agreement_version,
        'created_at': time.time(),
    }
    params = {
        'client_id': settings.OIDC_CLIENT_ID,
        'response_type': 'code',
        'redirect_uri': settings.OIDC_REDIRECT_URI,
        'scope': settings.OIDC_SCOPE,
        'state': state,
        'nonce': nonce,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
    }
    return JsonResponse({'authorization_url': discovery['authorization_endpoint'] + '?' + urlencode(params)},
                        status=201)


def _exchange_code(code, verifier, discovery):
    methods = discovery.get('token_endpoint_auth_methods_supported') or []
    basic = 'client_secret_basic' in methods and 'client_secret_post' not in methods
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': settings.OIDC_REDIRECT_URI,
        'client_id': settings.OIDC_CLIENT_ID,
        'code_verifier': verifier,
    }
    auth = None
    if basic:
        auth = (settings.OIDC_CLIENT_ID, settings.OIDC_CLIENT_SECRET)
    else:
        data['client_secret'] = settings.OIDC_CLIENT_SECRET
    try:
        response = requests.post(discovery['token_endpoint'], data=data, auth=auth,
            timeout=HTTP_TIMEOUT_SECONDS, allow_redirects=False)
        if response.status_code != 200 or len(response.content) > MAX_DOCUMENT_BYTES:
            raise OidcFailure('OIDC_REJECTED')
        document = response.json()
    except OidcFailure:
        raise
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise OidcFailure('OIDC_UNAVAILABLE') from exc
    if not isinstance(document, dict):
        raise OidcFailure('OIDC_REJECTED')
    token = document.get('id_token')
    if not isinstance(token, str) or not token or len(token) > MAX_ID_TOKEN_BYTES:
        raise OidcFailure('OIDC_REJECTED')
    return token


def _verify_id_token(raw, flow, discovery):
    header = _jwt_header(raw)
    if header.get('alg') not in ('RS256', 'ES256', 'ES384'):
        raise OidcFailure('OIDC_REJECTED')
    jwks = _fetch_json(discovery['jwks_uri'], max_bytes=MAX_DOCUMENT_BYTES)
    keys = jwks.get('keys')
    if not isinstance(keys, list) or not keys or len(keys) > MAX_JWKS_KEYS:
        raise OidcFailure('OIDC_REJECTED')
    certs = {}
    for key in keys:
        parsed = _jwk_pem(key)
        if parsed is not None:
            certs[parsed[0]] = parsed[1]
    if not certs:
        raise OidcFailure('OIDC_REJECTED')
    try:
        claims = google_jwt.decode(raw, certs=certs, audience=None, clock_skew_in_seconds=60)
    except (google_exceptions.GoogleAuthError, ValueError, TypeError) as exc:
        raise OidcFailure('OIDC_REJECTED') from exc
    if not isinstance(claims, dict):
        raise OidcFailure('OIDC_REJECTED')
    if not isinstance(claims.get('iss'), str) or not constant_time_compare(claims['iss'], settings.OIDC_ISSUER):
        raise OidcFailure('OIDC_REJECTED')
    audience = claims.get('aud')
    if isinstance(audience, str):
        audience_ok = constant_time_compare(audience, settings.OIDC_CLIENT_ID)
    elif isinstance(audience, list):
        audience_ok = settings.OIDC_CLIENT_ID in audience
    else:
        audience_ok = False
    if not audience_ok:
        raise OidcFailure('OIDC_REJECTED')
    if 'azp' in claims and (not isinstance(claims['azp'], str) or
            not constant_time_compare(claims['azp'], settings.OIDC_CLIENT_ID)):
        raise OidcFailure('OIDC_REJECTED')
    nonce = claims.get('nonce')
    if not isinstance(nonce, str) or not constant_time_compare(digest(nonce), flow['nonce_digest']):
        raise OidcFailure('OIDC_REJECTED')
    if not isinstance(claims.get('sub'), str) or not SUBJECT_PATTERN.fullmatch(claims['sub']):
        raise OidcFailure('OIDC_REJECTED')
    return claims


def _display_name(claims):
    name = claims.get('name')
    if not isinstance(name, str):
        given = claims.get('given_name') if isinstance(claims.get('given_name'), str) else ''
        family = claims.get('family_name') if isinstance(claims.get('family_name'), str) else ''
        name = (given + ' ' + family).strip()
    name = name.strip()
    if not name or len(name) > NAME_MAX_LENGTH or any(ord(char) < 32 or ord(char) == 127 for char in name):
        return None
    return name


def _link_error_code(code):
    if code == 'ambiguous-identity':
        return 'OIDC_IDENTITY_AMBIGUOUS'
    if code == 'account-disabled':
        return 'OIDC_ACCOUNT_DISABLED'
    if code in ('account-unavailable', 'account-inconsistent'):
        return 'OIDC_ACCOUNT_UNAVAILABLE'
    return 'OIDC_REJECTED'


def callback(request):
    for name in ('state', 'code', 'error'):
        if len(request.GET.getlist(name)) > 1:
            request.session.pop(FLOW_SESSION_KEY, None)
            return _flow_error(request, 'invalid_request')
    flow = request.session.pop(FLOW_SESSION_KEY, None)
    if not isinstance(flow, dict):
        return _flow_error(request, 'invalid_request')
    language = 'en' if flow.get('language') == 'en' else 'zh'
    if request.GET.get('error'):
        return _flow_error(request, 'provider_error', language)
    created_at = flow.get('created_at')
    if not isinstance(created_at, (int, float)) or time.time() - created_at > settings.AUTH_CODE_TTL_SECONDS:
        return _flow_error(request, 'invalid_request', language)
    state = request.GET.get('state', '')
    code = request.GET.get('code', '')
    if not isinstance(flow.get('state_digest'), str) or not isinstance(state, str) or not state or \
            not constant_time_compare(digest(state), flow['state_digest']):
        return _flow_error(request, 'invalid_request', language)
    if not isinstance(code, str) or not code or len(code) > 4096:
        return _flow_error(request, 'invalid_request', language)
    if not ready() or not isinstance(flow.get('code_verifier'), str):
        return _flow_error(request, 'unavailable', language)
    try:
        discovery = _discovery()
        raw_id_token = _exchange_code(code, flow['code_verifier'], discovery)
        claims = _verify_id_token(raw_id_token, flow, discovery)
    except OidcFailure as exc:
        code_name = 'unavailable' if exc.code == 'OIDC_UNAVAILABLE' else 'rejected'
        return _flow_error(request, code_name, language)

    verified_email = claims.get('email') if claims.get('email_verified') is True else None
    verified_phone = claims.get('phone_number') if claims.get('phone_number_verified') is True else None
    try:
        with transaction.atomic():
            account = resolve_or_link(
                issuer=settings.OIDC_ISSUER,
                subject=claims['sub'],
                verified_email=verified_email,
                verified_phone=verified_phone,
                display_name=_display_name(claims),
                agreement_version=flow.get('agreement_version'),
            )
            login(request, account.user, backend='django.contrib.auth.backends.ModelBackend')
            register_session(request)
            AuditEvent.objects.create(actor=account.user,
                action='auth.oidc_registered' if account.created else 'auth.oidc_verified',
                request_id=request.request_id)
            request.session[ID_TOKEN_SESSION_KEY] = raw_id_token
    except IdentityLinkError as exc:
        return _flow_error(request, _link_error_code(exc.code).lower().replace('oidc_', ''), language)
    except DatabaseError:
        django_logout(request)
        logger.error('oidc_callback_database_unavailable request_id=%s', request.request_id)
        return _flow_error(request, 'unavailable', language)
    return _redirect(request, flow.get('return_to') or _safe_return_to('/', language))


def logout(request):
    payload(request, ())
    id_token = request.session.get(ID_TOKEN_SESSION_KEY)
    revoke_current_session(request)
    django_logout(request)
    provider_signout = False
    logout_url = None
    if ready():
        try:
            discovery = _discovery()
            endpoint = discovery.get('end_session_endpoint')
            if isinstance(endpoint, str) and _https_url(endpoint):
                params = {
                    'client_id': settings.OIDC_CLIENT_ID,
                    'post_logout_redirect_uri': settings.OIDC_POST_LOGOUT_REDIRECT_URI,
                }
                if isinstance(id_token, str) and id_token:
                    params['id_token_hint'] = id_token
                logout_url = endpoint + '?' + urlencode(params)
                provider_signout = True
        except OidcFailure:
            pass
    return JsonResponse({'ok': True, 'provider_signout': provider_signout, 'logout_url': logout_url})
