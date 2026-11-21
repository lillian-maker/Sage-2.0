"""Contract tests: POST app/billing/handoff and GET auth/handoff (65 屏，D31).

The App issues a one-time hand-off credential bound to the signed-in user and the active ledger; the website
redeems it once within 120 seconds, signs the browser in as that user and selects that ledger. Only the SHA-256
digest of the token is stored.
"""
import hashlib
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from django.utils import timezone

from backend.models import AuditEvent, Membership
from backend.workbench.models import BillingHandoff
from backend.workbench.tests.base import AppTestCase

REDEEM = '/api/auth/handoff'


class HandoffIssueTests(AppTestCase):
    def issue(self, account=None, purpose='topup'):
        self.login(self.lillian, account)
        return self.ok(self.post('app/billing/handoff', {'purpose': purpose}), 201)

    def test_issue_shape_and_digest_only(self):
        body = self.issue()
        self.assertEqual(set(body), {'token', 'purpose', 'expires_at', 'expires_in', 'redeem_path', 'account_id'})
        self.assertEqual(body['expires_in'], 120)
        self.assertEqual(body['purpose'], 'topup')
        self.assertEqual(body['account_id'], str(self.personal.pk))
        self.assertTrue(body['redeem_path'].startswith(REDEEM + '?token='))
        row = BillingHandoff.objects.get()
        self.assertEqual(row.token_digest, hashlib.sha256(body['token'].encode()).hexdigest())
        self.assertNotIn(body['token'], row.token_digest)
        self.assertEqual(row.user, self.lillian)
        self.assertEqual(row.account, self.personal)
        self.assertLessEqual(row.expires_at, timezone.now() + timedelta(seconds=121))

    def test_business_ledger_is_bound(self):
        body = self.issue(self.business)
        self.assertEqual(body['account_id'], str(self.business.pk))
        self.assertEqual(BillingHandoff.objects.get().account, self.business)

    def test_unknown_purpose(self):
        self.login(self.lillian)
        self.assert_error(self.post('app/billing/handoff', {'purpose': 'refund'}), 422, 'VALIDATION_FAILED')

    def test_ledger_header_must_match(self):
        self.login(self.lillian)
        self.assert_error(self.post('app/billing/handoff', {'purpose': 'topup'}, account=self.business),
                          409, 'ACCOUNT_CHANGED')

    def test_requires_login(self):
        self.assert_error(self.post('app/billing/handoff', {'purpose': 'topup'}, account=False), 401, 'AUTH_REQUIRED')


class HandoffRedeemTests(AppTestCase):
    def setUp(self):
        self.app = self.client_class()
        self.login(self.lillian, self.personal, client=self.app)
        self.body = self.ok(self.post('app/billing/handoff', {'purpose': 'topup'}, client=self.app), 201)
        self.site = self.client_class()

    def redeem(self, token=None, client=None):
        return (client or self.site).get(REDEEM, {'token': self.body['token'] if token is None else token})

    def assert_redirect(self, response, ok):
        self.assertEqual(response.status_code, 302, response.content[:300])
        location = urlparse(response['Location'])
        self.assertEqual(location.path, '/pages/billing.html')
        self.assertEqual(location.fragment, 'credits')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        query = parse_qs(location.query)
        if ok:
            self.assertNotIn('handoff', query)
        else:
            self.assertEqual(query.get('handoff'), ['invalid'])
        return location

    def site_session(self, client=None):
        return self.ok((client or self.site).get('/api/auth/session'))

    def test_redeem_signs_in_and_selects_ledger(self):
        self.assert_redirect(self.redeem(), ok=True)
        session = self.site_session()
        self.assertTrue(session['authenticated'])
        self.assertEqual(session['user']['id'], str(self.lillian.pk))
        self.assertEqual(session['active_account_id'], str(self.personal.pk))
        self.assertIsNotNone(BillingHandoff.objects.get().consumed_at)
        self.assertTrue(AuditEvent.objects.filter(actor=self.lillian, action='auth.handoff_redeemed').exists())

    def test_reuse_is_refused(self):
        self.assert_redirect(self.redeem(), ok=True)
        other = self.client_class()
        self.assert_redirect(self.redeem(client=other), ok=False)
        self.assertFalse(self.site_session(other)['authenticated'])

    def test_expired_is_refused(self):
        BillingHandoff.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assert_redirect(self.redeem(), ok=False)
        self.assertFalse(self.site_session()['authenticated'])

    def test_bound_ledger_wins_after_app_switches(self):
        # The App switched to the business ledger after issuing; the website still opens the issuing ledger.
        session = self.app.session
        session['active_account_id'] = str(self.business.pk)
        session.save()
        self.assert_redirect(self.redeem(), ok=True)
        self.assertEqual(self.site_session()['active_account_id'], str(self.personal.pk))

    def test_ledger_membership_revoked_is_refused(self):
        self.login(self.lillian, self.business, client=self.app)
        body = self.ok(self.post('app/billing/handoff', {'purpose': 'topup'}, client=self.app), 201)
        Membership.objects.filter(user=self.lillian, account=self.business).update(is_active=False)
        self.assert_redirect(self.redeem(body['token']), ok=False)
        self.assertFalse(self.site_session()['authenticated'])

    def test_other_signed_in_user_is_refused_and_token_burned(self):
        chen = self.client_class()
        chen.force_login(self.chen)
        self.assert_redirect(self.redeem(client=chen), ok=False)
        session = self.site_session(chen)
        self.assertEqual(session['user']['id'], str(self.chen.pk))
        self.assert_redirect(self.redeem(), ok=False)
        self.assertFalse(self.site_session()['authenticated'])

    def test_same_user_already_signed_in_switches_ledger(self):
        site = self.client_class()
        self.login(self.lillian, self.business, client=site)
        self.assert_redirect(self.redeem(client=site), ok=True)
        self.assertEqual(self.site_session(site)['active_account_id'], str(self.personal.pk))

    def test_unknown_or_malformed_token(self):
        for token in ('', 'x' * 43, 'not a token'):
            with self.subTest(token=token):
                self.assert_redirect(self.redeem(token), ok=False)
        self.assertIsNone(BillingHandoff.objects.get().consumed_at)

    def test_only_get(self):
        response = self.site.post(REDEEM + '?token=' + self.body['token'], '{}', content_type='application/json')
        self.assertEqual(response.status_code, 405)
        self.assertIsNone(BillingHandoff.objects.get().consumed_at)

    def test_unknown_query_keys_refused(self):
        response = self.site.get(REDEEM, {'token': self.body['token'], 'next': 'https://evil.example'})
        self.assert_redirect(response, ok=False)
        self.assertIsNone(BillingHandoff.objects.get().consumed_at)
