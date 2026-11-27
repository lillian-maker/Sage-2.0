"""Resolve a verified external IdP subject to one Django account.

The caller must only pass email or phone values that the IdP has verified.
This module never accepts an unverified identifier as a linking key.
"""
import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import AccountProfile, BillingAccount, ExternalIdentity, LoginIdentity, Membership

ISSUER_MAX_LENGTH = 512
SUBJECT_MAX_LENGTH = 255
EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
PHONE_PATTERN = re.compile(r'^\+[0-9]{7,20}$')


class IdentityLinkError(ValueError):
    """A named, fail-closed reason why an external subject cannot be linked."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class LinkedAccount:
    user: object
    created: bool


def _clean_text(value, name, max_length, *, allow_empty=False):
    if not isinstance(value, str):
        raise IdentityLinkError('invalid-' + name)
    if value != value.strip() or len(value) > max_length:
        raise IdentityLinkError('invalid-' + name)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise IdentityLinkError('invalid-' + name)
    if not value and not allow_empty:
        raise IdentityLinkError('invalid-' + name)
    return value


def _validate_issuer(value):
    issuer = _clean_text(value, 'issuer', ISSUER_MAX_LENGTH)
    parsed = urlsplit(issuer)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.query or parsed.fragment:
        raise IdentityLinkError('invalid-issuer')
    return issuer


def _validate_subject(value):
    return _clean_text(value, 'subject', SUBJECT_MAX_LENGTH)


def _normalize_email(value):
    if value is None or value == '':
        return ''
    email = _clean_text(value, 'email', 254).lower()
    if not EMAIL_PATTERN.fullmatch(email):
        raise IdentityLinkError('invalid-email')
    return email


def _normalize_phone(value):
    if value is None or value == '':
        return ''
    if not isinstance(value, str):
        raise IdentityLinkError('invalid-phone')
    phone = re.sub(r'[\s()\-]', '', value)
    if not PHONE_PATTERN.fullmatch(phone):
        raise IdentityLinkError('invalid-phone')
    return phone


def _matching_user_ids(email, phone):
    user_ids = set()
    if email:
        user_ids.update(LoginIdentity.objects.filter(
            method='email', identifier__iexact=email).values_list('user_id', flat=True))
        # Google provider_email is set only after Google verified email_verified=true.
        user_ids.update(LoginIdentity.objects.filter(
            method='google', provider_email__iexact=email).values_list('user_id', flat=True))
    if phone:
        user_ids.update(LoginIdentity.objects.filter(
            method='phone', identifier=phone).values_list('user_id', flat=True))
    return user_ids


def _ensure_personal_account(user):
    account, _ = BillingAccount.objects.get_or_create(
        personal_owner=user,
        defaults={'kind': 'personal', 'name': '个人账户'},
    )
    if account.kind != 'personal':
        raise IdentityLinkError('account-inconsistent')
    Membership.objects.get_or_create(account=account, user=user, defaults={'role': 'owner'})
    return account


def _record_agreement(user, version, accepted_at):
    if version is None or version == '':
        return None
    version = _clean_text(version, 'agreement-version', 128)
    profile, _ = AccountProfile.objects.get_or_create(user=user)
    accepted = accepted_at or timezone.now()
    if profile.agreement_version != version or profile.agreement_accepted_at is None:
        profile.agreement_version = version
        profile.agreement_accepted_at = accepted
        profile.save(update_fields=['agreement_version', 'agreement_accepted_at'])
    return profile


def _provision_user(display_name, agreement_version, accepted_at):
    name = ''
    if display_name is not None:
        name = _clean_text(display_name, 'display-name', 150, allow_empty=True)
    user = get_user_model().objects.create_user(username=uuid.uuid4().hex, first_name=name)
    _ensure_personal_account(user)
    _record_agreement(user, agreement_version, accepted_at)
    return user


def resolve_or_link(*, issuer, subject, verified_email=None, verified_phone=None,
                    display_name=None, agreement_version=None, accepted_at=None):
    """Return the account for a verified external subject, linking it if needed.

    Raises IdentityLinkError with a stable code when the link is ambiguous,
    the account is unavailable, or the identifiers are malformed. The caller
    must treat every error as fail closed and must not retry with a weaker key.
    """
    issuer = _validate_issuer(issuer)
    subject = _validate_subject(subject)
    email = _normalize_email(verified_email)
    phone = _normalize_phone(verified_phone)
    accepted = accepted_at or timezone.now()

    with transaction.atomic():
        existing = ExternalIdentity.objects.select_for_update().filter(
            issuer=issuer, subject=subject).first()
        if existing is not None:
            if not existing.user.is_active:
                raise IdentityLinkError('account-disabled')
            _ensure_personal_account(existing.user)
            _record_agreement(existing.user, agreement_version, accepted)
            return LinkedAccount(existing.user, False)

        user_ids = _matching_user_ids(email, phone)
        if len(user_ids) > 1:
            raise IdentityLinkError('ambiguous-identity')
        if user_ids:
            user = get_user_model().objects.filter(pk=next(iter(user_ids))).first()
            if user is None:
                raise IdentityLinkError('account-unavailable')
            if not user.is_active:
                raise IdentityLinkError('account-disabled')
            created = False
        else:
            user = _provision_user(display_name, agreement_version, accepted)
            created = True

        try:
            with transaction.atomic():
                ExternalIdentity.objects.create(issuer=issuer, subject=subject, user=user)
        except IntegrityError:
            # A concurrent first login won the unique (issuer, subject) race.
            existing = ExternalIdentity.objects.select_for_update().get(
                issuer=issuer, subject=subject)
            if not existing.user.is_active:
                raise IdentityLinkError('account-disabled')
            _ensure_personal_account(existing.user)
            _record_agreement(existing.user, agreement_version, accepted)
            return LinkedAccount(existing.user, False)

        _ensure_personal_account(user)
        _record_agreement(user, agreement_version, accepted)
        return LinkedAccount(user, created)
