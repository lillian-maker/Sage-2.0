"""BR-19 tests: native bearer resource authentication for `/api/desktop/*`."""
import base64
import json
import os
import time
from unittest import mock

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from django.contrib.auth import get_user_model
from django.test import TestCase

from . import desktop_auth
from .models import ExternalIdentity

ISSUER = 'https://auth.example.test/oidc'
JWKS_URI = 'https://auth.example.test/oidc/jwks'
AUDIENCE = 'https://api.sagegogo.com'
CLIENT_ID = 'sage-desktop'
SCOPE = 'desktop.commerce.read'


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def public_jwk(private_key, kid, alg):
    if isinstance(private_key, rsa.RSAPrivateKey):
        numbers = private_key.public_key().public_numbers()
        return {'kty': 'RSA', 'kid': kid, 'alg': alg, 'use': 'sig',
                'n': b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, 'big')),
                'e': b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, 'big'))}
    numbers = private_key.public_key().public_numbers()
    size = (numbers.curve.key_size + 7) // 8
    curve = {'secp256r1': 'P-256', 'secp384r1': 'P-384', 'secp521r1': 'P-521'}[numbers.curve.name]
    return {'kty': 'EC', 'kid': kid, 'alg': alg, 'use': 'sig', 'crv': curve,
            'x': b64(numbers.x.to_bytes(size, 'big')), 'y': b64(numbers.y.to_bytes(size, 'big'))}


def sign(claims, private_key, kid, alg='RS256'):
    header = b64(json.dumps({'alg': alg, 'kid': kid, 'typ': 'JWT'}).encode('utf-8'))
    body = b64(json.dumps(claims).encode('utf-8'))
    signing_input = f'{header}.{body}'.encode('ascii')
    if alg.startswith('RS'):
        digest = {'RS256': hashes.SHA256(), 'RS384': hashes.SHA384(), 'RS512': hashes.SHA512()}[alg]
        signature = private_key.sign(signing_input, padding.PKCS1v15(), digest)
    else:
        digest = {'ES256': hashes.SHA256(), 'ES384': hashes.SHA384(), 'ES512': hashes.SHA512()}[alg]
        der = private_key.sign(signing_input, ec.ECDSA(digest))
        r, s = decode_dss_signature(der)
        size = (private_key.curve.key_size + 7) // 8
        signature = r.to_bytes(size, 'big') + s.to_bytes(size, 'big')
    return f'{header}.{body}.{b64(signature)}'


class DesktopBearerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.ec_key = ec.generate_private_key(ec.SECP256R1())
        cls.user = get_user_model().objects.create_user(
            username='br19-user', first_name='BR19', email='br19@example.test')
        ExternalIdentity.objects.create(issuer=ISSUER, subject='subject-1', user=cls.user)

    def setUp(self):
        desktop_auth.reset_jwks_cache()
        self.keys = [public_jwk(self.rsa_key, 'rsa-1', 'RS256'), public_jwk(self.ec_key, 'ec-1', 'ES256')]
        self.fetch = mock.patch.object(desktop_auth, '_fetch_jwks', side_effect=lambda uri: self.keys)
        self.fetch_mock = self.fetch.start()
        self.addCleanup(self.fetch.stop)
        self.env = mock.patch.dict(os.environ, {
            'SAGE_DESKTOP_OIDC_ISSUER': ISSUER,
            'SAGE_DESKTOP_OIDC_JWKS_URI': JWKS_URI,
            'SAGE_DESKTOP_OIDC_AUDIENCE': AUDIENCE,
            'SAGE_DESKTOP_OIDC_CLIENT_ID': CLIENT_ID,
            'SAGE_DESKTOP_OIDC_SCOPE': SCOPE,
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def claims(self, **overrides):
        now = int(time.time())
        payload = {'iss': ISSUER, 'aud': [AUDIENCE], 'sub': 'subject-1', 'client_id': CLIENT_ID,
                   'scope': SCOPE, 'exp': now + 300, 'nbf': now - 10}
        payload.update(overrides)
        return payload

    def token(self, claims=None, **overrides):
        return sign(claims if claims is not None else self.claims(**overrides), self.rsa_key, 'rsa-1')

    def get(self, authorization=None, **extra):
        headers = {} if authorization is None else {'HTTP_AUTHORIZATION': authorization}
        headers.update(extra)
        return self.client.get('/api/desktop/account', **headers)

    def assertRefused(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json(), {'state': 'refused', 'code': code})

    def test_valid_rs256_token_reads_the_minimal_account(self):
        response = self.get(f'Bearer {self.token()}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'state': 'read', 'account': {
            'id': str(self.user.pk), 'displayName': 'BR19', 'email': 'br19@example.test'}})

    def test_valid_es256_token_is_accepted(self):
        response = self.get(f'Bearer {sign(self.claims(), self.ec_key, "ec-1", "ES256")}')
        self.assertEqual(response.status_code, 200, response.content)

    def test_issuer_audience_client_and_time_are_enforced(self):
        cases = [
            ({'iss': 'https://other.example/oidc'}, 'desktop-token-issuer-mismatch'),
            ({'aud': ['https://other.example/api']}, 'desktop-token-audience-mismatch'),
            ({'client_id': 'sage-web'}, 'desktop-token-client-mismatch'),
            ({'exp': int(time.time()) - 3600}, 'desktop-token-expired'),
            ({'nbf': int(time.time()) + 3600}, 'desktop-token-not-yet-valid'),
        ]
        for overrides, code in cases:
            with self.subTest(code=code):
                self.assertRefused(self.get(f'Bearer {self.token(**overrides)}'), 401, code)

    def test_scope_is_required(self):
        self.assertRefused(self.get(f'Bearer {self.token(scope="openid profile")}'), 403, 'desktop-token-scope-denied')

    def test_unknown_kid_refreshes_once_then_refuses(self):
        token = sign(self.claims(), self.rsa_key, 'missing-kid')
        self.assertRefused(self.get(f'Bearer {token}'), 401, 'desktop-token-unknown-key')
        self.assertEqual(self.fetch_mock.call_count, 2)
        self.assertRefused(self.get(f'Bearer {token}'), 401, 'desktop-token-unknown-key')
        self.assertEqual(self.fetch_mock.call_count, 3)

    def test_tampered_signature_is_refused(self):
        token = self.token()
        tampered = token[:-4] + ('AAAA' if not token.endswith('AAAA') else 'BBBB')
        self.assertRefused(self.get(f'Bearer {tampered}'), 401, 'desktop-token-signature-invalid')

    def test_cookie_query_and_body_tokens_do_not_authenticate(self):
        token = self.token()
        response = self.client.get('/api/desktop/account', HTTP_COOKIE=f'sessionid={token}')
        self.assertRefused(response, 401, 'desktop-bearer-required')
        response = self.client.get(f'/api/desktop/account?access_token={token}')
        self.assertRefused(response, 401, 'desktop-bearer-required')
        response = self.client.generic('POST', '/api/desktop/account',
                                       data=json.dumps({'access_token': token}), content_type='application/json')
        self.assertRefused(response, 405, 'desktop-method-not-allowed')

    def test_unmapped_subject_is_refused_without_creating_a_mapping(self):
        before = ExternalIdentity.objects.count()
        self.assertRefused(self.get(f'Bearer {self.token(sub="subject-unknown")}'), 403, 'desktop-account-not-linked')
        self.assertEqual(ExternalIdentity.objects.count(), before)

    def test_disabled_account_is_refused(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertRefused(self.get(f'Bearer {self.token()}'), 403, 'desktop-account-disabled')

    def test_missing_or_malformed_authorization_is_refused(self):
        self.assertRefused(self.get(), 401, 'desktop-bearer-required')
        self.assertRefused(self.get('Basic abc'), 401, 'desktop-bearer-required')
        self.assertRefused(self.get('Bearer not-a-jwt'), 401, 'desktop-token-malformed')

    def test_unconfigured_or_unavailable_jwks_fails_closed(self):
        with mock.patch.dict(os.environ, {'SAGE_DESKTOP_OIDC_ISSUER': '', 'SAGE_DESKTOP_OIDC_JWKS_URI': ''}):
            self.assertRefused(self.get(f'Bearer {self.token()}'), 503, 'desktop-auth-unconfigured')
        self.fetch_mock.side_effect = desktop_auth.DesktopAuthError('desktop-jwks-unavailable', 503)
        desktop_auth.reset_jwks_cache()
        self.assertRefused(self.get(f'Bearer {self.token()}'), 503, 'desktop-jwks-unavailable')

    def test_jwks_is_cached_between_requests(self):
        token = self.token()
        self.assertEqual(self.get(f'Bearer {token}').status_code, 200)
        self.assertEqual(self.get(f'Bearer {token}').status_code, 200)
        self.assertEqual(self.fetch_mock.call_count, 1)
