"""G2: verified external subjects map to one Django account, fail closed otherwise."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from .account import _erase_account

from .identity_link import IdentityLinkError, resolve_or_link
from .models import (AccountDeletionRequest, AccountProfile, BillingAccount, ExternalIdentity,
                     LoginIdentity, Membership)

ISSUER = 'https://auth.staging.sagegogo.com/oidc'
terms = 'terms-2026-11-25'


def new_user(username='g2-user'):
    return get_user_model().objects.create_user(username=username)


class IdentityLinkTests(TestCase):
    def test_new_subject_provisions_personal_account_and_terms(self):
        result = resolve_or_link(
            issuer=ISSUER, subject='sub-new', verified_email='new@example.com',
            display_name='New Person', agreement_version=terms)
        self.assertTrue(result.created)
        self.assertEqual(ExternalIdentity.objects.filter(user=result.user).count(), 1)
        account = BillingAccount.objects.get(personal_owner=result.user)
        self.assertEqual(account.kind, 'personal')
        self.assertTrue(Membership.objects.filter(account=account, user=result.user, role='owner').exists())
        profile = AccountProfile.objects.get(user=result.user)
        self.assertEqual(profile.agreement_version, terms)
        self.assertIsNotNone(profile.agreement_accepted_at)

    def test_verified_email_links_existing_user(self):
        user = new_user()
        LoginIdentity.objects.create(user=user, method='email', identifier='Case@Example.com')
        result = resolve_or_link(issuer=ISSUER, subject='sub-email', verified_email='case@example.com')
        self.assertFalse(result.created)
        self.assertEqual(result.user, user)
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_google_provider_email_links_existing_user(self):
        user = new_user()
        LoginIdentity.objects.create(user=user, method='google', identifier='google-sub',
                                     provider_email='google@example.com')
        result = resolve_or_link(issuer=ISSUER, subject='sub-google', verified_email='google@example.com')
        self.assertFalse(result.created)
        self.assertEqual(result.user, user)

    def test_phone_links_existing_user(self):
        user = new_user()
        LoginIdentity.objects.create(user=user, method='phone', identifier='+15550100')
        result = resolve_or_link(issuer=ISSUER, subject='sub-phone', verified_phone='+15550100')
        self.assertFalse(result.created)
        self.assertEqual(result.user, user)

    def test_ambiguous_email_and_phone_fails_closed(self):
        first = new_user('first')
        second = new_user('second')
        LoginIdentity.objects.create(user=first, method='email', identifier='both@example.com')
        LoginIdentity.objects.create(user=second, method='phone', identifier='+15550101')
        with self.assertRaises(IdentityLinkError) as raised:
            resolve_or_link(issuer=ISSUER, subject='sub-ambiguous',
                            verified_email='both@example.com', verified_phone='+15550101')
        self.assertEqual(raised.exception.code, 'ambiguous-identity')
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_same_subject_is_idempotent(self):
        first = resolve_or_link(issuer=ISSUER, subject='sub-same', verified_email='same@example.com')
        second = resolve_or_link(issuer=ISSUER, subject='sub-same', verified_email='same@example.com')
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.user, second.user)
        self.assertEqual(ExternalIdentity.objects.count(), 1)

    def test_existing_user_without_account_gets_personal_account(self):
        user = new_user()
        LoginIdentity.objects.create(user=user, method='email', identifier='bare@example.com')
        result = resolve_or_link(issuer=ISSUER, subject='sub-bare', verified_email='bare@example.com')
        self.assertEqual(result.user, user)
        self.assertTrue(BillingAccount.objects.filter(personal_owner=user, kind='personal').exists())

    def test_inactive_account_fails_closed(self):
        user = new_user()
        user.is_active = False
        user.save(update_fields=['is_active'])
        LoginIdentity.objects.create(user=user, method='email', identifier='inactive@example.com')
        with self.assertRaises(IdentityLinkError) as raised:
            resolve_or_link(issuer=ISSUER, subject='sub-inactive', verified_email='inactive@example.com')
        self.assertEqual(raised.exception.code, 'account-disabled')
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_unverified_user_email_is_not_a_link_key(self):
        user = new_user('unverified')
        user.email = 'unverified@example.com'
        user.save(update_fields=['email'])
        result = resolve_or_link(issuer=ISSUER, subject='sub-unverified',
                                 verified_email='unverified@example.com')
        self.assertTrue(result.created)
        self.assertNotEqual(result.user, user)

    def test_existing_profile_terms_are_updated(self):
        user = new_user()
        LoginIdentity.objects.create(user=user, method='email', identifier='terms@example.com')
        AccountProfile.objects.create(user=user, agreement_version='old', agreement_accepted_at=timezone.now())
        resolve_or_link(issuer=ISSUER, subject='sub-terms', verified_email='terms@example.com',
                        agreement_version=terms)
        profile = AccountProfile.objects.get(user=user)
        self.assertEqual(profile.agreement_version, terms)

    def test_account_deletion_removes_external_identity(self):
        result = resolve_or_link(issuer=ISSUER, subject='sub-delete',
                                 verified_email='delete@example.com')
        row = AccountDeletionRequest.objects.create(
            user=result.user, status='requested', policy_version='fixture',
            retain_until=timezone.now() + timedelta(days=30))
        _erase_account(result.user, row)
        self.assertFalse(ExternalIdentity.objects.filter(user=result.user).exists())

    def test_invalid_issuer_and_subject_fail_closed(self):
        for issuer, subject, code in [
            ('http://auth.example.com/oidc', 'sub', 'invalid-issuer'),
            (ISSUER, '', 'invalid-subject'),
            (ISSUER, 'x' * 256, 'invalid-subject'),
        ]:
            with self.assertRaises(IdentityLinkError) as raised:
                resolve_or_link(issuer=issuer, subject=subject)
            self.assertEqual(raised.exception.code, code)
