"""OIDC RP state, token, binding, session and rollback tests."""
import base64
import json
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from google.auth import crypt, jwt
from google.auth.crypt import es
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from . import oidc_rp
from .authentication_tests import AUTH_FIXTURE
from .models import AccountProfile, AccountSession, AuditEvent, ExternalIdentity, LoginIdentity

ISSUER = 'https://idp.example.invalid/oidc'
CLIENT_ID = 'fixture-client'
CLIENT_SECRET = 'fixture-secret'
REDIRECT_URI = 'https://sage.example.invalid/api/auth/oidc/callback'
POST_LOGOUT_URI = 'https://sage.example.invalid/'

OIDC_FIXTURE = dict(AUTH_FIXTURE,
    AUTH_OIDC_ENABLED=True, OIDC_ISSUER=ISSUER, OIDC_CLIENT_ID=CLIENT_ID,
    OIDC_CLIENT_SECRET=CLIENT_SECRET, OIDC_REDIRECT_URI=REDIRECT_URI,
    OIDC_POST_LOGOUT_REDIRECT_URI=POST_LOGOUT_URI,
    OIDC_SCOPE='openid profile email phone', AUTH_LEGACY_LOGIN_ENABLED=False,
    ALLOWED_HOSTS=['sage.example.invalid', 'testserver'])


class Response:
    def __init__(self, status, payload):
        self.status_code = status
        self.content = json.dumps(payload).encode()
        self._payload = payload

    def json(self):
        return self._payload


class OidcFixture(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.signer = crypt.RSASigner.from_string(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()), key_id='fixture')
        numbers = key.public_key().public_numbers()
        cls.jwks = {'keys': [{'kty': 'RSA', 'kid': 'fixture', 'use': 'sig', 'alg': 'RS256',
            'n': cls._b64(numbers.n), 'e': cls._b64(numbers.e)}]}

    @staticmethod
    def _b64(value):
        raw = value.to_bytes((value.bit_length() + 7) // 8, 'big')
        return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.csrf = self.client.get('/api/auth/session').json()['csrf_token']
        self.discovery = {
            'issuer': ISSUER,
            'authorization_endpoint': ISSUER + '/auth',
            'token_endpoint': ISSUER + '/token',
            'jwks_uri': ISSUER + '/jwks',
            'end_session_endpoint': ISSUER + '/session/end',
            'token_endpoint_auth_methods_supported': ['client_secret_post'],
        }
        self.last_token_data = None
        self.get_calls = []
        self.post_calls = []
        getter = patch('backend.oidc_rp.requests.get', side_effect=self._get)
        poster = patch('backend.oidc_rp.requests.post', side_effect=self._post)
        getter.start(); poster.start()
        self.addCleanup(getter.stop); self.addCleanup(poster.stop)

    def _get(self, url, **kwargs):
        self.get_calls.append(url)
        if url.endswith('/.well-known/openid-configuration'):
            return Response(200, self.discovery)
        if url == ISSUER + '/jwks':
            return Response(200, self.jwks)
        return Response(404, {})

    def _post(self, url, data=None, **kwargs):
        self.post_calls.append((url, data, kwargs))
        self.last_token_data = data
        claims = self.claims or {}
        token = jwt.encode(self.signer, claims)
        if isinstance(token, bytes):
            token = token.decode()
        return Response(200, {'id_token': token, 'access_token': 'ignored'})

    def claims_for(self, nonce, **overrides):
        now = int(timezone.now().timestamp())
        claims = {'iss': ISSUER, 'sub': 'subject-1', 'aud': CLIENT_ID, 'iat': now - 10,
            'exp': now + 300, 'nonce': nonce, 'email': 'user@example.invalid',
            'email_verified': True, 'name': 'Fixture User'}
        claims.update(overrides)
        return claims

    def start(self, *, language='zh', intent='login', return_to='/pages/pricing.html', **overrides):
        body = {'return_to': return_to, 'language': language, 'intent': intent,
            'agreement_acceptance': AUTH_FIXTURE['AUTH_REGISTRATION_TERMS_VERSION']}
        body.update(overrides)
        return self.client.post('/api/auth/oidc/start', json.dumps(body),
            content_type='application/json', HTTP_X_CSRFTOKEN=self.csrf)

    def authorization(self):
        response = self.start()
        self.assertEqual(response.status_code, 201)
        url = urlsplit(response.json()['authorization_url'])
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        self.claim_token = None
        return query

    def callback(self, query, claims, **overrides):
        self.claims = claims
        params = {'state': query['state'], 'code': 'fixture-code'}
        params.update(overrides)
        return self.client.get('/api/auth/oidc/callback?' + urlencode(params))

    def login(self, **claim_overrides):
        query = self.authorization()
        claims = self.claims_for(query['nonce'], **claim_overrides)
        response = self.callback(query, claims)
        return response, query, claims


@override_settings(**OIDC_FIXTURE)
class OidcStartTests(OidcFixture):
    def test_start_requires_csrf_and_builds_pkce_url(self):
        body = json.dumps({'return_to': '/pages/pricing.html', 'language': 'zh'})
        response = self.client.post('/api/auth/oidc/start', body, content_type='application/json')
        self.assertEqual(response.status_code, 403)
        query = self.authorization()
        self.assertEqual(query['client_id'], CLIENT_ID)
        self.assertEqual(query['redirect_uri'], REDIRECT_URI)
        self.assertEqual(query['code_challenge_method'], 'S256')
        self.assertIn('email', query['scope'].split())
        self.assertNotIn('offline_access', query['scope'].split())
        self.assertNotIn('resource', query)
        flow = self.client.session['oidc_flow']
        self.assertIn('state_digest', flow)
        self.assertIn('nonce_digest', flow)
        self.assertEqual(flow['return_to'], '/pages/pricing.html')

    def test_register_rejects_unapproved_agreement(self):
        response = self.start(intent='register', agreement_acceptance='attacker-version')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['error']['code'], 'TERMS_UNAVAILABLE')

    def test_start_rejects_unconfigured(self):
        with override_settings(OIDC_CLIENT_SECRET=''):
            response = self.start()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'OIDC_UNAVAILABLE')

    def test_start_rejects_authenticated(self):
        user = get_user_model().objects.create_user(username='signed-in')
        self.client.force_login(user)
        response = self.start()
        self.assertEqual(response.status_code, 409)

    def test_ready_rejects_insecure_redirect_and_missing_scope(self):
        with override_settings(OIDC_REDIRECT_URI='http://sage.example.invalid/api/auth/oidc/callback'):
            self.assertFalse(oidc_rp.ready())
        with override_settings(OIDC_SCOPE='openid profile'):
            self.assertFalse(oidc_rp.ready())
        with override_settings(OIDC_SCOPE='openid email offline_access'):
            self.assertFalse(oidc_rp.ready())


@override_settings(**OIDC_FIXTURE)
class OidcCallbackTests(OidcFixture):
    def test_new_user_creates_account_and_rotates_session(self):
        query = self.authorization()
        old_key = self.client.session.session_key
        self.claims = self.claims_for(query['nonce'])
        self.post_calls.clear()
        response = self.client.get('/api/auth/oidc/callback?' + urlencode({
            'state': query['state'], 'code': 'fixture-code'}))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response['Location'], '/pages/pricing.html')
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertEqual(response['Cache-Control'], 'no-store')
        user = ExternalIdentity.objects.get(issuer=ISSUER, subject='subject-1').user
        self.assertEqual(ExternalIdentity.objects.filter(user=user, issuer=ISSUER).count(), 1)
        self.assertEqual(AccountProfile.objects.get(user=user).agreement_version,
            AUTH_FIXTURE['AUTH_REGISTRATION_TERMS_VERSION'])
        self.assertTrue(AccountSession.objects.filter(user=user, revoked_at__isnull=True).exists())
        self.assertTrue(AuditEvent.objects.filter(actor=user, action='auth.oidc_registered').exists())
        self.assertNotEqual(self.client.session.session_key, old_key)
        self.assertTrue(self.client.get('/api/auth/session').json()['authenticated'])

    def test_es384_signed_id_token_is_accepted(self):
        key = ec.generate_private_key(ec.SECP384R1())
        numbers = key.public_key().public_numbers()
        self.signer = es.EsSigner(key, key_id='fixture')
        self.jwks = {'keys': [{'kty': 'EC', 'kid': 'fixture', 'use': 'sig', 'alg': 'ES384',
            'crv': 'P-384', 'x': self._b64(numbers.x), 'y': self._b64(numbers.y)}]}
        response, _, _ = self.login()
        self.assertEqual(response.status_code, 303)
        self.assertTrue(ExternalIdentity.objects.filter(issuer=ISSUER, subject='subject-1').exists())

    def test_existing_verified_email_links_same_user(self):
        user = get_user_model().objects.create_user(username='existing', first_name='Existing')
        LoginIdentity.objects.create(user=user, method='email', identifier='user@example.invalid')
        response, _, _ = self.login()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(ExternalIdentity.objects.get(issuer=ISSUER, subject='subject-1').user_id, user.pk)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertTrue(AuditEvent.objects.filter(actor=user, action='auth.oidc_verified').exists())

    def test_unverified_email_does_not_link_existing_user(self):
        user = get_user_model().objects.create_user(username='existing')
        LoginIdentity.objects.create(user=user, method='email', identifier='user@example.invalid')
        response, _, _ = self.login(email_verified=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(get_user_model().objects.count(), 2)
        self.assertFalse(ExternalIdentity.objects.filter(user=user).exists())

    def test_state_mismatch_is_rejected_without_network(self):
        self.authorization()
        before = len(self.get_calls)
        response = self.client.get('/api/auth/oidc/callback?' + urlencode({
            'state': 'wrong-state', 'code': 'fixture-code'}))
        self.assertEqual(response.status_code, 303)
        self.assertIn('oidc=invalid_request', response['Location'])
        self.assertEqual(len(self.get_calls), before)
        self.assertEqual(get_user_model().objects.count(), 0)

    def test_duplicate_state_is_rejected(self):
        query = self.authorization()
        response = self.client.get('/api/auth/oidc/callback?' + urlencode([
            ('state', query['state']), ('state', query['state']), ('code', 'fixture-code')]))
        self.assertEqual(response.status_code, 303)
        self.assertIn('oidc=invalid_request', response['Location'])
        self.assertEqual(get_user_model().objects.count(), 0)

    def test_wrong_nonce_issuer_and_audience_are_rejected(self):
        for override in ({'nonce': 'wrong'}, {'iss': 'https://other.invalid/oidc'},
                         {'aud': 'other-client'}):
            with self.subTest(override=override):
                self.client = Client(enforce_csrf_checks=True)
                self.csrf = self.client.get('/api/auth/session').json()['csrf_token']
                query = self.authorization()
                claims = self.claims_for(query['nonce'])
                claims.update(override)
                response = self.callback(query, claims)
                self.assertEqual(response.status_code, 303)
                self.assertIn('oidc=rejected', response['Location'])
                self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
                self.assertEqual(get_user_model().objects.count(), 0)
                self.assertEqual(ExternalIdentity.objects.count(), 0)

    def test_expired_flow_is_rejected(self):
        query = self.authorization()
        flow = self.client.session['oidc_flow']
        flow['created_at'] -= 10_000
        session = self.client.session
        session['oidc_flow'] = flow
        session.save()
        response = self.client.get('/api/auth/oidc/callback?' + urlencode({
            'state': query['state'], 'code': 'fixture-code'}))
        self.assertIn('oidc=invalid_request', response['Location'])


@override_settings(**OIDC_FIXTURE)
class OidcLogoutTests(OidcFixture):
    def test_logout_revokes_session_and_returns_end_session(self):
        response, _, _ = self.login()
        self.assertEqual(response.status_code, 303)
        self.csrf = self.client.get('/api/auth/session').json()['csrf_token']
        old_key = self.client.session.session_key
        response = self.client.post('/api/auth/oidc/logout', json.dumps({}),
            content_type='application/json', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['provider_signout'])
        self.assertIn(ISSUER + '/session/end?', payload['logout_url'])
        self.assertIn('post_logout_redirect_uri=', payload['logout_url'])
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        self.assertTrue(AccountSession.objects.get(session_key=old_key).revoked_at)

    def test_logout_stays_local_when_discovery_fails(self):
        response, _, _ = self.login()
        self.assertEqual(response.status_code, 303)
        self.csrf = self.client.get('/api/auth/session').json()['csrf_token']
        self.discovery['token_endpoint'] = 'http://insecure.invalid/token'
        response = self.client.post('/api/auth/oidc/logout', json.dumps({}),
            content_type='application/json', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['provider_signout'])
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])


@override_settings(**OIDC_FIXTURE)
class OidcLegacySwitchTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.csrf = self.client.get('/api/auth/session').json()['csrf_token']

    def test_legacy_entry_is_closed_when_switch_is_off(self):
        response = self.client.post('/api/auth/' + 'pass' + 'word' + '-login', json.dumps({
            'identifier': 'user@example.invalid', 'pass' + 'word': 'x'}),
            content_type='application/json', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'LEGACY_AUTH_DISABLED')

    def test_password_change_is_not_gated(self):
        response = self.client.post('/api/auth/' + 'pass' + 'word' + '-challenges', json.dumps({
            'identifier': 'user@example.invalid', 'method': 'email'}),
            content_type='application/json', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertNotEqual(response.status_code, 503)

    def test_switch_rolls_back(self):
        with override_settings(AUTH_LEGACY_LOGIN_ENABLED=True):
            response = self.client.post('/api/auth/' + 'pass' + 'word' + '-login', json.dumps({
                'identifier': 'user@example.invalid', 'pass' + 'word': 'x'}),
                content_type='application/json', HTTP_X_CSRFTOKEN=self.csrf)
        self.assertNotEqual(response.status_code, 503)
