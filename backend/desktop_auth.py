"""Native bearer authentication for `/api/desktop/*` (BR-19).

The desktop app calls Django with a Logto **access token** held by Electron main.
This module is the resource-server face: it accepts only `Authorization: Bearer`,
verifies the token against cached JWKS, maps the exact `(issuer, subject)` to an
existing `ExternalIdentity`, and returns a minimal account DTO. It never creates a
mapping, never reads a Django session/cookie, and never logs token material.
"""
import base64
import json
import logging
import os
import threading
import time

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import ExternalIdentity

logger = logging.getLogger('sage')

DEFAULT_AUDIENCE = 'https://api.sagegogo.com'
DEFAULT_CLIENT_ID = 'sage-desktop'
DEFAULT_SCOPE = 'desktop.commerce.read'
JWKS_TTL_SECONDS = 300
JWKS_TIMEOUT_SECONDS = 5
MAX_JWKS_BYTES = 65536
MAX_JWKS_KEYS = 20
MAX_TOKEN_BYTES = 16384
CLOCK_SKEW_SECONDS = 60
ALLOWED_ALGORITHMS = {
    'RS256': (hashes.SHA256, 'RSA'),
    'RS384': (hashes.SHA384, 'RSA'),
    'RS512': (hashes.SHA512, 'RSA'),
    'ES256': (hashes.SHA256, 'P-256'),
    'ES384': (hashes.SHA384, 'P-384'),
    'ES512': (hashes.SHA512, 'P-521'),
}

_jwks_lock = threading.Lock()
_jwks_cache = {}


class DesktopAuthError(Exception):
    """Named, fail-closed refusal; the code is safe to return and audit."""

    def __init__(self, code, status):
        self.code = code
        self.status = status
        super().__init__(code)


def _config():
    issuer = os.environ.get('SAGE_DESKTOP_OIDC_ISSUER', '').strip()
    jwks_uri = os.environ.get('SAGE_DESKTOP_OIDC_JWKS_URI', '').strip()
    if issuer == '' or jwks_uri == '':
        raise DesktopAuthError('desktop-auth-unconfigured', 503)
    return {
        'issuer': issuer,
        'jwks_uri': jwks_uri,
        'audience': os.environ.get('SAGE_DESKTOP_OIDC_AUDIENCE', DEFAULT_AUDIENCE).strip() or DEFAULT_AUDIENCE,
        'client_id': os.environ.get('SAGE_DESKTOP_OIDC_CLIENT_ID', DEFAULT_CLIENT_ID).strip() or DEFAULT_CLIENT_ID,
        'scope': os.environ.get('SAGE_DESKTOP_OIDC_SCOPE', DEFAULT_SCOPE).strip() or DEFAULT_SCOPE,
    }


def reset_jwks_cache():
    with _jwks_lock:
        _jwks_cache.clear()


def _b64url(value):
    padding_needed = '=' * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding_needed)
    except (ValueError, TypeError) as error:
        raise DesktopAuthError('desktop-token-malformed', 401) from error


def _json_object(raw):
    try:
        parsed = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DesktopAuthError('desktop-token-malformed', 401) from error
    if not isinstance(parsed, dict):
        raise DesktopAuthError('desktop-token-malformed', 401)
    return parsed


def _fetch_jwks(uri):
    try:
        response = requests.get(uri, timeout=JWKS_TIMEOUT_SECONDS)
    except requests.RequestException as error:
        raise DesktopAuthError('desktop-jwks-unavailable', 503) from error
    if response.status_code != 200 or len(response.content) > MAX_JWKS_BYTES:
        raise DesktopAuthError('desktop-jwks-unavailable', 503)
    try:
        document = response.json()
    except ValueError as error:
        raise DesktopAuthError('desktop-jwks-unavailable', 503) from error
    keys = document.get('keys') if isinstance(document, dict) else None
    if not isinstance(keys, list) or not keys or len(keys) > MAX_JWKS_KEYS:
        raise DesktopAuthError('desktop-jwks-unavailable', 503)
    parsed = []
    for key in keys:
        if not isinstance(key, dict) or key.get('use', 'sig') != 'sig':
            continue
        if not isinstance(key.get('kid'), str) or key['kid'] == '':
            continue
        if key.get('kty') == 'RSA' and isinstance(key.get('n'), str) and isinstance(key.get('e'), str):
            parsed.append(key)
        elif key.get('kty') == 'EC' and isinstance(key.get('crv'), str) \
                and isinstance(key.get('x'), str) and isinstance(key.get('y'), str):
            parsed.append(key)
    if not parsed:
        raise DesktopAuthError('desktop-jwks-unavailable', 503)
    return parsed


def _load_jwks(uri, *, force=False):
    now = time.monotonic()
    with _jwks_lock:
        cached = _jwks_cache.get(uri)
        if cached is not None and not force and now - cached[0] < JWKS_TTL_SECONDS:
            return cached[1]
    keys = _fetch_jwks(uri)
    with _jwks_lock:
        _jwks_cache[uri] = (now, keys)
    return keys


def _public_key(key):
    if key['kty'] == 'RSA':
        n = int.from_bytes(_b64url(key['n']), 'big')
        e = int.from_bytes(_b64url(key['e']), 'big')
        return rsa.RSAPublicNumbers(e, n).public_key()
    curve = {'P-256': ec.SECP256R1(), 'P-384': ec.SECP384R1(), 'P-521': ec.SECP521R1()}.get(key['crv'])
    if curve is None:
        raise DesktopAuthError('desktop-token-unknown-key', 401)
    x = int.from_bytes(_b64url(key['x']), 'big')
    y = int.from_bytes(_b64url(key['y']), 'big')
    return ec.EllipticCurvePublicNumbers(x, y, curve).public_key()


def _verify(alg, key, signing_input, signature):
    if alg.startswith('RS'):
        key.verify(signature, signing_input, padding.PKCS1v15(), ALLOWED_ALGORITHMS[alg][0]())
        return
    if len(signature) % 2 != 0:
        raise DesktopAuthError('desktop-token-signature-invalid', 401)
    half = len(signature) // 2
    der = encode_dss_signature(
        int.from_bytes(signature[:half], 'big'),
        int.from_bytes(signature[half:], 'big'),
    )
    key.verify(der, signing_input, ec.ECDSA(ALLOWED_ALGORITHMS[alg][0]()))


def _token_from_request(request):
    header = request.META.get('HTTP_AUTHORIZATION', '')
    if not isinstance(header, str) or not header.startswith('Bearer '):
        raise DesktopAuthError('desktop-bearer-required', 401)
    token = header[len('Bearer '):].strip()
    if token == '' or len(token) > MAX_TOKEN_BYTES or any(char.isspace() for char in token):
        raise DesktopAuthError('desktop-token-malformed', 401)
    return token


def _validate_claims(payload, config, now):
    if payload.get('iss') != config['issuer']:
        raise DesktopAuthError('desktop-token-issuer-mismatch', 401)
    audience = payload.get('aud')
    audiences = audience if isinstance(audience, list) else [audience]
    if config['audience'] not in audiences:
        raise DesktopAuthError('desktop-token-audience-mismatch', 401)
    if payload.get('client_id') != config['client_id']:
        raise DesktopAuthError('desktop-token-client-mismatch', 401)
    subject = payload.get('sub')
    if not isinstance(subject, str) or subject == '' or len(subject) > 255:
        raise DesktopAuthError('desktop-token-malformed', 401)
    expires = payload.get('exp')
    if not isinstance(expires, (int, float)) or isinstance(expires, bool):
        raise DesktopAuthError('desktop-token-malformed', 401)
    if expires <= now - CLOCK_SKEW_SECONDS:
        raise DesktopAuthError('desktop-token-expired', 401)
    not_before = payload.get('nbf')
    if not_before is not None:
        if not isinstance(not_before, (int, float)) or isinstance(not_before, bool):
            raise DesktopAuthError('desktop-token-malformed', 401)
        if not_before > now + CLOCK_SKEW_SECONDS:
            raise DesktopAuthError('desktop-token-not-yet-valid', 401)
    scope = payload.get('scope')
    scopes = scope.split() if isinstance(scope, str) else []
    if config['scope'] not in scopes:
        raise DesktopAuthError('desktop-token-scope-denied', 403)
    return subject


def authenticate_desktop_request(request):
    """Return the mapped Django user or raise DesktopAuthError. Never creates mappings."""
    config = _config()
    token = _token_from_request(request)
    parts = token.split('.')
    if len(parts) != 3:
        raise DesktopAuthError('desktop-token-malformed', 401)
    header = _json_object(_b64url(parts[0]))
    alg = header.get('alg')
    kid = header.get('kid')
    if alg not in ALLOWED_ALGORITHMS or not isinstance(kid, str) or kid == '':
        raise DesktopAuthError('desktop-token-unsupported-algorithm', 401)
    keys = _load_jwks(config['jwks_uri'])
    key = next((candidate for candidate in keys if candidate.get('kid') == kid), None)
    if key is None:
        keys = _load_jwks(config['jwks_uri'], force=True)
        key = next((candidate for candidate in keys if candidate.get('kid') == kid), None)
    if key is None or key.get('alg', alg) != alg:
        raise DesktopAuthError('desktop-token-unknown-key', 401)
    if ALLOWED_ALGORITHMS[alg][1] == 'RSA' and key['kty'] != 'RSA':
        raise DesktopAuthError('desktop-token-unknown-key', 401)
    if ALLOWED_ALGORITHMS[alg][1] != 'RSA' and key.get('crv') != ALLOWED_ALGORITHMS[alg][1]:
        raise DesktopAuthError('desktop-token-unknown-key', 401)
    signing_input = f'{parts[0]}.{parts[1]}'.encode('ascii')
    try:
        public_key = _public_key(key)
    except (ValueError, TypeError) as error:
        raise DesktopAuthError('desktop-token-unknown-key', 401) from error
    try:
        _verify(alg, public_key, signing_input, _b64url(parts[2]))
    except InvalidSignature as error:
        raise DesktopAuthError('desktop-token-signature-invalid', 401) from error
    payload = _json_object(_b64url(parts[1]))
    subject = _validate_claims(payload, config, time.time())
    try:
        mapping = ExternalIdentity.objects.select_related('user').get(
            issuer=config['issuer'], subject=subject)
    except ExternalIdentity.DoesNotExist as error:
        raise DesktopAuthError('desktop-account-not-linked', 403) from error
    except ExternalIdentity.MultipleObjectsReturned as error:
        raise DesktopAuthError('desktop-account-ambiguous', 403) from error
    if not mapping.user.is_active:
        raise DesktopAuthError('desktop-account-disabled', 403)
    return mapping.user


@csrf_exempt
def desktop_account(request):
    if request.method != 'GET':
        return JsonResponse({'state': 'refused', 'code': 'desktop-method-not-allowed'}, status=405)
    try:
        user = authenticate_desktop_request(request)
    except DesktopAuthError as error:
        return JsonResponse({'state': 'refused', 'code': error.code}, status=error.status)
    return JsonResponse({
        'state': 'read',
        'account': {
            'id': str(user.pk),
            'displayName': user.first_name or user.username,
            'email': user.email,
        },
    })
