"""App passwordless e-mail code login: POST auth/challenges + auth/verify (C-D-1, C-D-2)."""
import json
import re
import smtplib
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from backend.authentication_tests import AUTH_FIXTURE
from backend.models import AccountProfile, AccountSession, AuthFlow, BillingAccount, LoginChallenge, LoginIdentity
from backend.workbench.models import AccountPolicy
from backend.workbench.tests.base import make_business, make_user


@override_settings(**AUTH_FIXTURE, AUTH_EMAIL_CODE_LOGIN_ENABLED=True)
class EmailCodeLoginTests(TestCase):
    def setUp(self):
        self.user = make_user('lillian@example.com', 'Lillian', 'Li')
        self.client = Client(enforce_csrf_checks=True)
        self.token = self.client.get('/api/auth/session').json()['csrf_token']

    def post(self, path, body, client=None, **extra):
        client = client or self.client
        token = self.token if client is self.client else client.get('/api/auth/session').json()['csrf_token']
        return client.post('/api/auth/' + path, json.dumps(body), content_type='application/json',
                           HTTP_X_CSRFTOKEN=token, **extra)

    def challenge(self, identifier='lillian@example.com', client=None):
        response = self.post('challenges', {'method': 'email', 'identifier': identifier}, client)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def code(self):
        return re.search(r'\b\d{6}\b', mail.outbox[-1].body).group()

    def test_disabled_by_default_keeps_legacy_403(self):
        with override_settings(AUTH_EMAIL_CODE_LOGIN_ENABLED=False):
            self.assertIs(self.client.get('/api/auth/session').json()['capabilities']['email_login'], False)
            for path, body in (('challenges', {'method': 'email', 'identifier': 'lillian@example.com'}),
                               ('verify', {'challenge_id': 'x', 'code': '123456'})):
                response = self.post(path, body)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()['error']['code'], 'AUTH_FLOW_REQUIRED')
        self.assertEqual(len(mail.outbox), 0)
        self.assertIs(self.client.get('/api/auth/session').json()['capabilities']['email_login'], True)

    def test_challenge_response_shape_and_mail(self):
        body = self.challenge()
        for key in ('flow_id', 'challenge_id', 'expires_at', 'resend_available_at'):
            self.assertIn(key, body)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('登录验证码', mail.outbox[0].subject)
        self.assertNotIn(self.code(), json.dumps(body))
        flow = AuthFlow.objects.get(pk=body['flow_id'])
        self.assertEqual((flow.purpose, flow.stage, flow.user_id), ('email_code', 'code', self.user.pk))
        self.assertEqual(LoginChallenge.objects.get(pk=body['challenge_id']).purpose, 'login')

    def test_verify_logs_in_with_session_shape(self):
        body = self.challenge()
        response = self.post('verify', {'challenge_id': body['challenge_id'], 'code': self.code()},
                             HTTP_X_SAGE_CLIENT='MacBook Pro; 0.1.0')
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertTrue(data['authenticated'])
        self.assertEqual(data['user']['display_name'], 'Lillian')
        self.assertIsNone(data['user']['onboarding_completed_at'])
        self.assertIs(data['capabilities']['byok'], False)
        self.assertTrue(self.client.get('/api/auth/session').json()['authenticated'])
        row = AccountSession.objects.get(user=self.user)
        self.assertEqual((row.device_name, row.app_version), ('MacBook Pro', '0.1.0'))
        self.assertIsNotNone(row.last_seen_at)
        self.assertIsNotNone(LoginChallenge.objects.get(pk=body['challenge_id']).consumed_at)

    def test_code_cannot_be_reused(self):
        body = self.challenge()
        code = self.code()
        self.assertEqual(self.post('verify', {'challenge_id': body['challenge_id'], 'code': code}).status_code, 200)
        self.client.post('/api/auth/logout', '{}', content_type='application/json',
                         HTTP_X_CSRFTOKEN=self.client.cookies['csrftoken'].value)
        self.token = self.client.get('/api/auth/session').json()['csrf_token']
        response = self.post('verify', {'challenge_id': body['challenge_id'], 'code': code})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['error']['code'], 'INVALID_CODE')

    def test_wrong_code_attempt_limit(self):
        body = self.challenge()
        code = self.code()
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(5):
            response = self.post('verify', {'challenge_id': body['challenge_id'], 'code': wrong})
            self.assertEqual(response.json()['error']['code'], 'INVALID_CODE')
        self.assertEqual(self.post('verify', {'challenge_id': body['challenge_id'], 'code': code}).status_code, 422)
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])

    def test_expired_code_and_bad_format(self):
        body = self.challenge()
        LoginChallenge.objects.filter(pk=body['challenge_id']).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.post('verify', {'challenge_id': body['challenge_id'], 'code': self.code()})
                         .json()['error']['code'], 'INVALID_CODE')
        for code in ('12345', 123456, 'abcdef'):
            self.assertEqual(self.post('verify', {'challenge_id': body['challenge_id'], 'code': code})
                             .json()['error']['code'], 'INVALID_CODE')
        self.assertEqual(self.post('verify', {'challenge_id': 'not-a-uuid', 'code': '123456'})
                         .json()['error']['code'], 'INVALID_CODE')

    def test_unregistered_email_gets_same_response_without_mail_or_signup(self):
        body = self.challenge('stranger@example.com')
        self.assertEqual(set(body) >= {'flow_id', 'challenge_id', 'expires_at', 'resend_available_at'}, True)
        self.assertEqual(len(mail.outbox), 0)
        response = self.post('verify', {'challenge_id': body['challenge_id'], 'code': '123456'})
        self.assertEqual(response.json()['error']['code'], 'INVALID_CODE')
        self.assertFalse(LoginIdentity.objects.filter(identifier='stranger@example.com').exists())
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_inactive_user_is_treated_as_unknown(self):
        self.user.is_active = False
        self.user.save()
        self.challenge()
        self.assertEqual(len(mail.outbox), 0)

    def test_non_email_methods_stay_closed(self):
        response = self.post('challenges', {'method': 'phone', 'identifier': '+8613800000000'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'AUTH_FLOW_REQUIRED')
        self.assertFalse(LoginChallenge.objects.exists())

    def test_invalid_identifier(self):
        response = self.post('challenges', {'method': 'email', 'identifier': 'not-an-email'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['error']['code'], 'INVALID_IDENTIFIER')

    def test_unknown_keys_rejected(self):
        response = self.post('challenges', {'method': 'email', 'identifier': 'lillian@example.com', 'x': 1})
        self.assertEqual(response.json()['error']['code'], 'INVALID_REQUEST')

    def test_channel_unavailable(self):
        with override_settings(AUTH_EMAIL_ENABLED=False):
            response = self.post('challenges', {'method': 'email', 'identifier': 'lillian@example.com'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'CHANNEL_UNAVAILABLE')
        self.assertFalse(AuthFlow.objects.exists())

    def test_delivery_failed(self):
        with patch('backend.authentication.send_mail', side_effect=smtplib.SMTPException('down')):
            response = self.post('challenges', {'method': 'email', 'identifier': 'lillian@example.com'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'DELIVERY_FAILED')

    def test_rate_limited_until_resend_window(self):
        self.challenge()
        response = self.post('challenges', {'method': 'email', 'identifier': 'lillian@example.com'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()['error']['code'], 'RATE_LIMITED')

    @override_settings(AUTH_RESEND_SECONDS=0)
    def test_resend_replaces_code(self):
        body = self.challenge()
        old = self.code()
        response = self.post('resend', {'flow_id': body['flow_id']})
        self.assertEqual(response.status_code, 201, response.content)
        fresh = response.json()
        self.assertIn('resend_available_at', fresh)
        self.assertNotEqual(fresh['challenge_id'], body['challenge_id'])
        self.assertEqual(self.post('verify', {'challenge_id': body['challenge_id'], 'code': old}).status_code, 422)
        self.assertEqual(self.post('verify', {'challenge_id': fresh['challenge_id'], 'code': self.code()})
                         .status_code, 200)

    def test_cancel_invalidates_flow(self):
        body = self.challenge()
        self.assertEqual(self.post('cancel', {'flow_id': body['flow_id']}).status_code, 200)
        response = self.post('verify', {'challenge_id': body['challenge_id'], 'code': self.code()})
        self.assertEqual(response.status_code, 422)
        self.assertIn(response.json()['error']['code'], ('INVALID_FLOW', 'INVALID_CODE'))

    def test_code_bound_to_requesting_browser(self):
        body = self.challenge()
        other = Client(enforce_csrf_checks=True)
        response = self.post('verify', {'challenge_id': body['challenge_id'], 'code': self.code()}, other)
        self.assertEqual(response.json()['error']['code'], 'INVALID_CODE')
        self.assertFalse(other.get('/api/auth/session').json()['authenticated'])

    def test_authenticated_caller_rejected(self):
        self.client.force_login(self.user)
        self.token = self.client.get('/api/auth/session').json()['csrf_token']
        response = self.post('challenges', {'method': 'email', 'identifier': 'lillian@example.com'})
        self.assertEqual(response.json()['error']['code'], 'INVALID_REQUEST')


class SessionExtraKeysTests(TestCase):
    def setUp(self):
        self.user = make_user('lillian@example.com', 'Lillian', 'Li')
        self.business = make_business(members=((self.user, 'owner'),))
        self.client.force_login(self.user)

    def select(self, account):
        session = self.client.session
        session['active_account_id'] = str(account.pk)
        session.save()

    def test_anonymous_has_byok_false(self):
        data = Client().get('/api/auth/session').json()
        self.assertIs(data['capabilities']['byok'], False)
        self.assertIsNone(data['user'])

    def test_onboarding_and_byok(self):
        data = self.client.get('/api/auth/session').json()
        self.assertIsNone(data['user']['onboarding_completed_at'])
        self.assertIs(data['capabilities']['byok'], False)
        now = timezone.now()
        AccountProfile.objects.update_or_create(user=self.user, defaults={'onboarding_completed_at': now})
        self.assertEqual(self.client.get('/api/auth/session').json()['user']['onboarding_completed_at'],
                         now.isoformat())
        AccountPolicy.objects.create(account=self.business, expert_share_max_scope='account', byok_enabled=True)
        self.select(self.business)
        self.assertIs(self.client.get('/api/auth/session').json()['capabilities']['byok'], True)
        self.select(BillingAccount.objects.get(personal_owner=self.user))
        self.assertIs(self.client.get('/api/auth/session').json()['capabilities']['byok'], False)
