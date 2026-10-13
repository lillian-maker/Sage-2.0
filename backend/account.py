"""Small, same-origin account endpoints; reuse Django identity and sessions."""
import re
import uuid
import logging
import os
import stat
from contextlib import contextmanager, ExitStack
if os.name == 'posix':
    import fcntl
else:
    fcntl = None
from io import BytesIO
import warnings
import hashlib
import hmac
import secrets
import smtplib
from datetime import date, timedelta

from django.contrib.sessions.models import Session
from django.conf import settings
from django.contrib.auth import get_user, get_user_model, logout
from django.core.mail import send_mail
from django.utils.crypto import constant_time_compare
from django.views.decorators.debug import sensitive_variables
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage, FileSystemStorage
from django.db import models, transaction
from django.http import FileResponse, JsonResponse
from django.utils import timezone

from .http import client_ip, error
from .models import AccountProfile, AccountSession, AuditEvent, LoginIdentity, Membership
from .models import (AccountDeletionChallenge, AccountDeletionRequest, AccountDeletionAudit,
                     TrialIdentityMarker, TrialAttempt, BillingAccount)

logger = logging.getLogger('sage')


def register_session(request):
    """Register successful login or a legacy authenticated session, never revive one."""
    if not request.user.is_authenticated:
        return None
    if not request.session.session_key:
        request.session.save()
    agent = request.headers.get('User-Agent', '')
    browser = next((label for token, label in [('Edg/', 'Edge'), ('Firefox/', 'Firefox'),
        ('Chrome/', 'Chrome'), ('Safari/', 'Safari')] if token in agent), 'Browser')
    os = next((label for token, label in [('Android', 'Android'), ('iPhone', 'iOS'),
        ('iPad', 'iPadOS'), ('Windows', 'Windows'), ('Macintosh', 'macOS'),
        ('Linux', 'Linux')] if token in agent), '')
    address = client_ip(request) or None
    record, _ = AccountSession.objects.get_or_create(session_key=request.session.session_key,
        defaults={'user': request.user, 'client': browser + (' / ' + os if os else ''), 'ip': address})
    return record


def revoke_current_session(request):
    if request.user.is_authenticated:
        AccountSession.objects.filter(user=request.user, session_key=request.session.session_key,
            revoked_at__isnull=True).update(revoked_at=timezone.now())


def session_is_active(request):
    if not request.user.is_authenticated:
        return False
    row = AccountSession.objects.filter(session_key=request.session.session_key).first()
    # Billing GET contracts are genuinely read-only. Legacy sessions acquire
    # metadata only when entering account settings, never on arbitrary reads.
    return row is None or (row.user_id == request.user.pk and row.revoked_at is None)


def avatar_url(profile):
    return '/api/account/avatar-image' if profile and profile.avatar_name else None


def profile_data(user):
    profile, _ = AccountProfile.objects.get_or_create(user=user)
    identity = LoginIdentity.objects.filter(user=user, method='email').order_by('id').first()
    google = LoginIdentity.objects.filter(user=user,method='google').order_by('id').first() if identity is None else None
    phone = LoginIdentity.objects.filter(user=user,method='phone').order_by('id').first()
    return {'display_name': user.first_name, 'phone': phone.identifier if phone else '',
            'password_reset_method': 'email' if identity else 'phone' if phone else None,
            'email': identity.identifier if identity else google.provider_email if google else '',
            'avatar_url': avatar_url(profile), 'email_read_only': True}


def _lock_current(request):
    record = AccountSession.objects.select_for_update().get(session_key=request.session.session_key)
    if record.revoked_at or record.user_id != request.user.pk or not request.user.is_active:
        raise ValueError('AUTH_REQUIRED')
    # request.user may have been cached before a password reset committed.
    # Reuse Django's fresh DB/auth-hash check, including secret-key fallbacks.
    user = get_user(request)
    if not user.is_authenticated or user.pk != record.user_id:
        raise ValueError('AUTH_REQUIRED')
    # A revoker holds this same record lock until the actual Session row is deleted.
    if not Session.objects.filter(pk=record.session_key, expire_date__gt=timezone.now()).exists():
        raise ValueError('AUTH_REQUIRED')


def _audit(request, action):
    AuditEvent.objects.create(actor=request.user, action=action, request_id=request.request_id)


def _avatar_storage_ready():
    # An operator must first verify the actual mount and shared-host lock semantics.
    if (not settings.AVATAR_LOCAL_FLOCK_VERIFIED or os.name != 'posix' or
            default_storage.__class__ is not FileSystemStorage):
        raise OSError('AVATAR_STORAGE_UNAVAILABLE')


@contextmanager
def _avatar_namespace_lock(token, create=False):
    """Stable local marker; callers retain this context through the SQL commit."""
    _avatar_storage_ready()
    if not isinstance(token, str) or not re.fullmatch(r'avatars/[0-9a-f]{32}/', token):
        raise OSError('AVATAR_NAMESPACE_INVALID')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as descriptors:
        root = os.open(default_storage.location, flags)
        descriptors.callback(os.close, root)
        if create:
            try:
                os.mkdir('avatars', 0o700, dir_fd=root)
            except FileExistsError:
                pass
        try:
            parent = os.open('avatars', flags, dir_fd=root)
        except FileNotFoundError:
            if create:
                raise
            yield None
            return
        descriptors.callback(os.close, parent)
        basename = token.split('/')[1]
        if create:
            os.mkdir(basename, 0o700, dir_fd=parent)
        try:
            directory = os.open(basename, flags, dir_fd=parent)
        except FileNotFoundError:
            if create:
                raise
            yield None
            return
        descriptors.callback(os.close, directory)
        marker = os.open('.lock', os.O_RDWR | os.O_NOFOLLOW |
            (os.O_CREAT | os.O_EXCL if create else 0), 0o600, dir_fd=directory)
        descriptors.callback(os.close, marker)
        info = os.fstat(marker)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size:
            raise OSError('AVATAR_MARKER_INVALID')
        fcntl.flock(marker, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Never unlink directories/markers: old open descriptors must keep the same lock.
        yield directory, marker


def _avatar_namespace_files(token, handles):
    directory, marker = handles
    # Re-open the full path without following any untrusted component.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as descriptors:
        fd = os.open(default_storage.location, flags)
        descriptors.callback(os.close, fd)
        for part in token.rstrip('/').split('/'):
            fd = os.open(part, flags, dir_fd=fd)
            descriptors.callback(os.close, fd)
        before, current = os.fstat(directory), os.fstat(fd)
        if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
            raise OSError('AVATAR_NAMESPACE_CHANGED')
    names = os.listdir(directory)
    if '.lock' not in names or len(names) > 2:
        raise OSError('AVATAR_NAMESPACE_CONTENTS_INVALID')
    result = []
    for name in names:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError('AVATAR_NAMESPACE_CONTENTS_INVALID')
        if name == '.lock':
            locked = os.fstat(marker)
            if info.st_size or (info.st_dev, info.st_ino) != (locked.st_dev, locked.st_ino):
                raise OSError('AVATAR_MARKER_INVALID')
        elif re.fullmatch(r'[A-Za-z0-9_-]{1,120}\.jpg', name):
            result.append(name)
        else:
            raise OSError('AVATAR_NAMESPACE_CONTENTS_INVALID')
    return result


def _avatar_exact_file(name):
    if default_storage.__class__ is not FileSystemStorage:
        raise OSError('AVATAR_STORAGE_UNAVAILABLE')
    if not isinstance(name, str) or not re.fullmatch(
            r'avatars/(?:[0-9a-f]{32}/)?[A-Za-z0-9_-]{1,120}\.jpg', name):
        raise OSError('AVATAR_FILE_INVALID')
    with ExitStack() as descriptors:
        fd = os.open(default_storage.location, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.callback(os.close, fd)
        try:
            for part in name.split('/')[:-1]:
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                descriptors.callback(os.close, fd)
            info = os.stat(name.split('/')[-1], dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError('AVATAR_FILE_INVALID')


def cleanup_avatar_files(user_id):
    """Retry durable old-file cleanup, serialized with uploads for this profile."""
    with ExitStack() as locks, transaction.atomic():
        profile = AccountProfile.objects.select_for_update().filter(user_id=user_id).first()
        if not profile:
            return 0
        if not isinstance(profile.pending_avatar_deletions, list):
            logger.error('avatar_cleanup_queue_invalid')
            return 1
        remaining = []
        for name in profile.pending_avatar_deletions:
            if name == profile.avatar_name or (isinstance(name, str) and name.endswith('/') and
                    profile.avatar_name.startswith(name)):
                # Never erase the live avatar even if a malformed queue contains it.
                remaining.append(name)
                logger.error('avatar_cleanup_current_file_protected')
                continue
            try:
                if isinstance(name, str) and name.endswith('/'):
                    directory = locks.enter_context(_avatar_namespace_lock(name))
                    if directory is not None:
                        for child in _avatar_namespace_files(name, directory):
                            default_storage.delete(name + child)
                        if _avatar_namespace_files(name, directory):
                            raise OSError('AVATAR_CLEANUP_INCOMPLETE')
                else:
                    _avatar_exact_file(name)
                    if name.count('/') == 2:
                        token = name.rsplit('/', 1)[0] + '/'
                        directory = locks.enter_context(_avatar_namespace_lock(token))
                        if directory is not None:
                            _avatar_namespace_files(token, directory)
                    default_storage.delete(name)
                    if default_storage.exists(name):
                        raise OSError('AVATAR_CLEANUP_INCOMPLETE')
            except OSError:
                remaining.append(name)
                logger.error('avatar_cleanup_storage_failed')
        profile.pending_avatar_deletions = remaining
        profile.save(update_fields=['pending_avatar_deletions'])
        return len(remaining)


def _avatar_bytes(upload):
    from PIL import Image, ImageOps, UnidentifiedImageError
    if upload.size > 2 * 1024 * 1024:
        raise ValueError('AVATAR_TOO_LARGE')
    if upload.content_type not in ('image/jpeg', 'image/png', 'image/webp'):
        raise ValueError('INVALID_AVATAR')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(upload) as image:
                formats = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp'}
                if (image.format not in formats or formats[image.format] != upload.content_type or
                        image.width * image.height > 16_000_000 or getattr(image, 'n_frames', 1) != 1):
                    raise ValueError('INVALID_AVATAR')
                image.load()
                image = ImageOps.exif_transpose(image)
                fitted = ImageOps.fit(image.convert('RGBA'), (512, 512))
                opaque = Image.new('RGB', fitted.size, 'white')
                opaque.paste(fitted, mask=fitted.getchannel('A'))
                encoded = BytesIO()
                opaque.save(encoded, format='JPEG', quality=88)
                return encoded.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValueError('INVALID_AVATAR') from exc


def deletion_ready():
    from .views import email_ready
    try:
        reviewed = date.fromisoformat(settings.ACCOUNT_DELETION_POLICY_REVIEWED_AT)
    except (TypeError, ValueError):
        return False
    return bool(settings.ACCOUNT_DELETION_ENABLED and
        settings.ACCOUNT_DELETION_POLICY_VERSION and reviewed <= timezone.localdate() and
        settings.ACCOUNT_DELETION_RETENTION_DAYS > 0 and
        settings.ACCOUNT_DELETION_MARKER_RETENTION_DAYS > 0 and
        len(settings.ACCOUNT_DELETION_MARKER_KEY) >= 50 and
        email_ready(settings.ACCOUNT_DELETION_SES_TEMPLATE_ID))


def trial_identifier_digest(method, identifier):
    if len(settings.ACCOUNT_DELETION_MARKER_KEY) < 50:
        raise ValueError('RETENTION_POLICY_PENDING')
    return hmac.new(settings.ACCOUNT_DELETION_MARKER_KEY.encode(),
        (method + '\0' + identifier).encode(), hashlib.sha256).hexdigest()


def trial_identity_blocked(user):
    if len(settings.ACCOUNT_DELETION_MARKER_KEY) < 50:
        if TrialIdentityMarker.objects.filter(expires_at__gt=timezone.now()).exists():
            raise ValueError('RETENTION_POLICY_PENDING')
        return False
    if TrialIdentityMarker.objects.filter(expires_at__gt=timezone.now()).exclude(
            key_fingerprint=hashlib.sha256(settings.ACCOUNT_DELETION_MARKER_KEY.encode()).hexdigest()).exists():
        raise ValueError('RETENTION_POLICY_PENDING')
    return any(TrialIdentityMarker.objects.filter(method=row.method,
        identifier_digest=trial_identifier_digest(row.method, row.identifier),
        expires_at__gt=timezone.now()).exists() for row in LoginIdentity.objects.filter(user=user))


def _deletion_blockers(user):
    from .commerce.models import Order, Subscription, SubscriptionPeriod
    from .commerce.models import CreditBatch, Reservation
    from .commerce.billing import _balances
    accounts = BillingAccount.objects.filter(personal_owner=user).values_list('pk', flat=True)
    now = timezone.now()
    blockers = []
    if Order.objects.filter(account_id__in=accounts).exists():
        blockers.append('TRANSACTION_HISTORY')
    if (Subscription.objects.filter(account_id__in=accounts, auto_renew=True).exists() or
            SubscriptionPeriod.objects.filter(subscription__account_id__in=accounts,
                starts_at__lte=now, ends_at__gt=now).exists()):
        blockers.append('ACTIVE_SUBSCRIPTION')
    if Order.objects.filter(account_id__in=accounts).exclude(status__in=[
            'fulfilled', 'cancelled', 'expired', 'refunded']).exists():
        blockers.append('UNSETTLED_ORDER')
    if Membership.objects.filter(user=user, is_active=True, account__kind='business', role='owner').exists():
        blockers.append('BUSINESS_OWNER_RESPONSIBILITY')
    if TrialAttempt.objects.filter(user=user, task_state='running').exists() or Reservation.objects.filter(
            models.Q(account_id__in=accounts) | models.Q(membership__user=user), state='reserved').exists():
        blockers.append('RUNNING_TASK')
    if any(any(_balances(batch)[kind] > 0 for kind in ('available', 'reserved', 'frozen')) for batch in CreditBatch.objects.filter(
            account_id__in=accounts, source='purchased', expires_at__gt=now)):
        blockers.append('PAID_CREDITS_REMAINING')
    return blockers


def deletion_request_data(row):
    if not row:
        return None
    return {field: getattr(row, field).isoformat() if hasattr(getattr(row, field), 'isoformat')
        else str(getattr(row, field)) if field == 'id' else getattr(row, field)
        for field in ('id', 'status', 'created_at', 'first_response_due_at', 'completion_due_at',
                      'first_responded_at', 'completed_at', 'delay_reason', 'expected_completion_at', 'progress_message')}


def deletion_eligibility(user):
    blockers = _deletion_blockers(user)
    available = deletion_ready()
    if not available:
        blockers.insert(0, 'RETENTION_POLICY_PENDING')
    if not LoginIdentity.objects.filter(user=user, method='email').exists():
        blockers.append('EMAIL_VERIFICATION_REQUIRED')
        available = False
    return {'eligible': available and not blockers, 'available': available, 'blockers': blockers,
            'policy_version': settings.ACCOUNT_DELETION_POLICY_VERSION,
            'request': deletion_request_data(AccountDeletionRequest.objects.filter(user=user).first()),
            'retained_data': ['necessary_billing_records'],
            'message': '复杂账号提交申请后由专人核验，2个工作日内首次回复，原则上7个工作日内完成。' if available else '账号注销尚未开放，等待保留规则审定及真实邮箱验证接通。'}


def _business_due(now, days):
    # Weekdays only; statutory holidays require an approved calendar before launch.
    while days:
        now += timedelta(days=1)
        if now.weekday() < 5:
            days -= 1
    return now


def _lock_deletion_user(user):
    list(AccountSession.objects.select_for_update().filter(user=user).order_by('pk'))
    ids = list(Membership.objects.filter(user=user).values_list('account_id', flat=True))
    list(BillingAccount.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
    locked = get_user_model().objects.select_for_update().get(pk=user.pk)
    if not locked.is_active:
        raise ValueError('AUTH_REQUIRED')
    return locked


def _erase_account(user, row):
    now = timezone.now()
    if TrialAttempt.objects.filter(user=user).exclude(task_state='irrecoverable_failure').exists():
        for identity in LoginIdentity.objects.filter(user=user):
            TrialIdentityMarker.objects.update_or_create(method=identity.method,
                identifier_digest=trial_identifier_digest(identity.method, identity.identifier),
                defaults={'expires_at': now + timedelta(days=settings.ACCOUNT_DELETION_MARKER_RETENTION_DAYS),
                    'key_fingerprint': hashlib.sha256(settings.ACCOUNT_DELETION_MARKER_KEY.encode()).hexdigest()})
    from .models import AuthFlow, LoginChallenge, PurchaseInquiry
    identities = list(LoginIdentity.objects.filter(user=user))
    # Google sub and asserted email differ; erase only this user's bound flows.
    AuthFlow.objects.filter(user=user).delete()
    for identity in identities:
        AuthFlow.objects.filter(method=identity.method,identifier=identity.identifier).exclude(purpose='google').delete()
        LoginChallenge.objects.filter(method=identity.method, identifier=identity.identifier).delete()
    LoginIdentity.objects.filter(user=user).delete()
    AccountDeletionChallenge.objects.filter(user=user).delete()
    TrialAttempt.objects.filter(user=user).update(input_value='', input_key='', report=None)
    PurchaseInquiry.objects.filter(created_by=user, account__kind='personal').update(company='', requirements='')
    PurchaseInquiry.objects.filter(created_by=user).update(contact_email='')
    profile, _ = AccountProfile.objects.select_for_update().get_or_create(user=user)
    if profile.avatar_name and profile.avatar_name not in profile.pending_avatar_deletions:
        profile.pending_avatar_deletions.append(profile.avatar_name)
    profile.avatar_name, profile.product_updates, profile.preference_changed_at = '', False, None
    profile.save()
    keys = list(AccountSession.objects.filter(user=user).values_list('session_key', flat=True))
    Session.objects.filter(pk__in=keys).delete()
    AccountSession.objects.filter(user=user).update(revoked_at=now, ip=None, client='')
    Membership.objects.filter(user=user).update(is_active=False)
    BillingAccount.objects.filter(personal_owner=user).update(name='已注销个人账户')
    user.first_name = user.last_name = user.email = ''
    user.username, user.is_active = 'deleted-' + uuid.uuid4().hex, False
    user.set_unusable_password()
    user.save()
    row.status, row.completed_at = 'completed', now
    row.retain_until = now + timedelta(days=settings.ACCOUNT_DELETION_RETENTION_DAYS)
    row.delay_reason, row.progress_message = '', ''
    row.save()
    transaction.on_commit(lambda: cleanup_avatar_files(user.pk))


@sensitive_variables()
def deletion_challenge(request):
    from .views import payload, digest, throttle
    payload(request, ())
    if not deletion_ready():
        return error(request, 'RETENTION_POLICY_PENDING', 503)
    identity = LoginIdentity.objects.filter(user=request.user, method='email').first()
    if not identity:
        return error(request, 'EMAIL_VERIFICATION_REQUIRED', 409)
    code, now, challenge_id = f'{secrets.randbelow(1000000):06d}', timezone.now(), uuid.uuid4()
    with transaction.atomic():
        _lock_current(request)
        if not throttle('deletion-ip:' + client_ip(request), settings.AUTH_IP_LIMIT) or not throttle(
                'deletion-user:' + str(request.user.pk), settings.AUTH_IDENTIFIER_LIMIT, settings.AUTH_RESEND_SECONDS):
            return error(request, 'RATE_LIMITED', 429)
        AccountDeletionChallenge.objects.filter(user=request.user, consumed_at__isnull=True).update(consumed_at=now)
        row = AccountDeletionChallenge.objects.create(id=challenge_id, user=request.user,
            identifier_digest=trial_identifier_digest('email', identity.identifier),
            session_digest=digest(request.session.session_key), code_digest=digest(str(challenge_id) + ':' + code),
            expires_at=now + timedelta(seconds=settings.AUTH_CODE_TTL_SECONDS))
    try:
        if settings.AUTH_EMAIL_TRANSPORT == 'ses':
            from .email import send_deletion_code
            sent = send_deletion_code(identity.identifier, code)
        else:
            sent = send_mail('Sage 账号注销验证码 / Account deletion code',
                f'账号注销验证码 / Account deletion code: {code}\n仅用于永久注销账号，请勿分享。',
                settings.DEFAULT_FROM_EMAIL, [identity.identifier], fail_silently=False)
    except (OSError, smtplib.SMTPException):
        logger.warning('deletion_delivery_failed request_id=%s', request.request_id)
        return error(request, 'DELIVERY_FAILED', 503)
    if sent != 1:
        return error(request, 'DELIVERY_FAILED', 503)
    AccountDeletionChallenge.objects.filter(pk=row.pk).update(delivered=True)
    return JsonResponse({'challenge_id': str(row.pk), 'expires_in': settings.AUTH_CODE_TTL_SECONDS,
                         'resend_after': settings.AUTH_RESEND_SECONDS}, status=201)


@sensitive_variables()
def delete_account(request):
    from .views import payload, digest
    if not deletion_ready():
        return error(request, 'RETENTION_POLICY_PENDING', 503)
    data = payload(request, ('challenge_id', 'code', 'confirm', 'policy_version'))
    if data['confirm'] is not True or data['policy_version'] != settings.ACCOUNT_DELETION_POLICY_VERSION:
        raise ValueError('INVALID_REQUEST')
    try:
        challenge_id = uuid.UUID(data['challenge_id'])
    except (ValueError, TypeError, AttributeError):
        raise ValueError('INVALID_REQUEST') from None
    if not isinstance(data['code'], str) or not re.fullmatch(r'\d{6}', data['code']):
        raise ValueError('INVALID_REQUEST')
    with transaction.atomic():
        user = _lock_deletion_user(request.user)
        _lock_current(request)
        challenge = AccountDeletionChallenge.objects.select_for_update().filter(pk=challenge_id, user=user).first()
        identity = LoginIdentity.objects.filter(user=user, method='email').first()
        now = timezone.now()
        if (not challenge or not identity or not challenge.delivered or challenge.consumed_at or
                challenge.expires_at <= now or challenge.attempts >= settings.AUTH_CODE_MAX_ATTEMPTS or
                not constant_time_compare(challenge.session_digest, digest(request.session.session_key)) or
                not constant_time_compare(challenge.identifier_digest, trial_identifier_digest('email', identity.identifier))):
            return error(request, 'INVALID_CODE', 422)
        challenge.attempts += 1
        challenge.save(update_fields=['attempts'])
        if not constant_time_compare(challenge.code_digest, digest(str(challenge.pk) + ':' + data['code'])):
            return error(request, 'INVALID_CODE', 422)
        challenge.consumed_at = now
        challenge.save(update_fields=['consumed_at'])
        row = AccountDeletionRequest.objects.filter(user=user).first()
        if row:
            return JsonResponse({'status': row.status, 'request': deletion_request_data(row)})
        blockers = _deletion_blockers(user)
        row = AccountDeletionRequest.objects.create(user=user, policy_version=data['policy_version'], blockers=blockers,
            first_response_due_at=_business_due(now, 2), completion_due_at=_business_due(now, 7),
            retain_until=now + timedelta(days=settings.ACCOUNT_DELETION_RETENTION_DAYS))
        AccountDeletionAudit.objects.create(request=row, actor=user, action='verified_request')
        if not blockers:
            _erase_account(user, row)
            AccountDeletionAudit.objects.create(request=row, actor=user, action='automatic_completion')
            logout(request)
            return JsonResponse({'status': 'deleted'})
        return JsonResponse({'status': 'requested', 'request': deletion_request_data(row)}, status=202)


def update_deletion_request(pk, *, actor, status, progress_message='', delay_reason='', expected_completion_at=None):
    from django.core.exceptions import ValidationError
    if not (actor.is_active and actor.is_staff and actor.has_perm('backend.manage_account_deletion')):
        raise ValidationError('FORBIDDEN')
    if status not in ('reviewing', 'waiting_user', 'delayed', 'completed'):
        raise ValidationError('INVALID_REQUEST')
    if not isinstance(progress_message, str) or not progress_message.strip() or len(progress_message) > 1000:
        raise ValidationError('PROGRESS_MESSAGE_REQUIRED')
    if status == 'delayed' and (not delay_reason.strip() or len(delay_reason) > 1000 or
            not expected_completion_at or expected_completion_at <= timezone.now()):
        raise ValidationError('DELAY_REASON_AND_DATE_REQUIRED')
    with transaction.atomic():
        hint = AccountDeletionRequest.objects.get(pk=pk)
        user = _lock_deletion_user(hint.user)
        row = AccountDeletionRequest.objects.select_for_update().get(pk=pk)
        if row.status == 'completed':
            raise ValidationError('ALREADY_COMPLETED')
        if not deletion_ready() or row.policy_version != settings.ACCOUNT_DELETION_POLICY_VERSION:
            raise ValidationError('RETENTION_POLICY_PENDING')
        row.blockers = _deletion_blockers(user)
        if status == 'completed':
            # Historical transactions require human review, but are retained and never rewritten.
            if set(row.blockers) - {'TRANSACTION_HISTORY'}:
                raise ValidationError('UNRESOLVED_ACCOUNT_RESPONSIBILITY')
            _erase_account(user, row)
        else:
            row.status = status
        row.first_responded_at = row.first_responded_at or timezone.now()
        row.progress_message = progress_message.strip()
        row.delay_reason = delay_reason.strip() if status == 'delayed' else ''
        row.expected_completion_at = expected_completion_at if status == 'delayed' else None
        row.save()
        AccountDeletionAudit.objects.create(request=row, actor=actor, action=status)
        return row


def cleanup_deletion_data(*, apply=False):
    """Expiry removes pseudonyms and free text, never financial history or pending applications."""
    now = timezone.now()
    markers = TrialIdentityMarker.objects.filter(expires_at__lte=now)
    challenges = AccountDeletionChallenge.objects.filter(expires_at__lte=now)
    requests = AccountDeletionRequest.objects.filter(status='completed', retain_until__lte=now)
    counts = {'markers': markers.count(), 'challenges': challenges.count(), 'requests': requests.count()}
    if apply:
        with transaction.atomic():
            markers.delete()
            challenges.delete()
            requests.delete()
    return counts


def dispatch(request, path):
    methods = {'account/profile': ('GET', 'PATCH'), 'account/preferences': ('GET', 'PATCH'),
        'account/sessions': ('GET',), 'account/sessions/revoke': ('POST',),
        'account/avatar': ('POST', 'DELETE'), 'account/avatar-image': ('GET',),
        'account/deletion-eligibility': ('GET',), 'account/deletion-challenges': ('POST',), 'account/delete': ('POST',)}
    if path not in methods:
        return None
    if request.method not in methods[path]:
        response = error(request, 'METHOD_NOT_ALLOWED', 405)
        response['Allow'] = ', '.join(methods[path])
        return response
    if not session_is_active(request):
        return error(request, 'AUTH_REQUIRED', 401, '请先登录。')
    register_session(request)
    from .views import payload
    try:
        if path == 'account/profile':
            if request.method == 'PATCH':
                name = payload(request, ('display_name',))['display_name']
                if (not isinstance(name, str) or not name.strip() or len(name.strip()) > 80 or
                        any(ord(char) < 32 or ord(char) == 127 for char in name)):
                    raise ValueError('INVALID_REQUEST')
                with transaction.atomic():
                    _lock_current(request)
                    request.user.first_name = name.strip()
                    request.user.save(update_fields=['first_name'])
                    _audit(request, 'profile.name_updated')
            return JsonResponse(profile_data(request.user))
        if path == 'account/preferences':
            with transaction.atomic():
                _lock_current(request)
                profile, _ = AccountProfile.objects.get_or_create(user=request.user)
                if request.method == 'PATCH':
                    value = payload(request, ('product_updates',))['product_updates']
                    if type(value) is not bool:
                        raise ValueError('INVALID_REQUEST')
                    profile.product_updates = value
                    profile.preference_changed_at = timezone.now()
                    profile.save(update_fields=['product_updates', 'preference_changed_at'])
                    _audit(request, 'profile.preferences_updated')
                return JsonResponse({'product_updates': profile.product_updates if profile.preference_changed_at else True,
                    'necessary_notifications': True})
        if path == 'account/sessions':
            offset = request.GET.get('offset', '0')
            if set(request.GET) - {'offset'} or len(request.GET.getlist('offset')) > 1 or not re.fullmatch(r'\d{1,9}', offset):
                raise ValueError('INVALID_REQUEST')
            rows = list(AccountSession.objects.filter(user=request.user, revoked_at__isnull=True,
                session_key__in=Session.objects.filter(expire_date__gt=timezone.now()).values('session_key'))
                .order_by('-created_at', '-id')[int(offset):int(offset)+51])
            return JsonResponse({'items': [{'id': str(row.pk), 'client': row.client,
                'location': '未知', 'ip': row.ip or '', 'created_at': row.created_at.isoformat(),
                'is_current': row.session_key == request.session.session_key} for row in rows[:50]],
                'next_offset': int(offset)+50 if len(rows) > 50 else None})
        if path == 'account/sessions/revoke':
            # Two explicit input forms prevent accidentally revoking current credentials.
            try:
                data = payload(request, ('session_id',))
            except ValueError as exc:
                if str(exc) != 'INVALID_REQUEST':
                    raise
                data = payload(request, ('other_sessions',))
            query = AccountSession.objects.filter(user=request.user)
            if 'other_sessions' in data:
                if data['other_sessions'] is not True:
                    raise ValueError('INVALID_REQUEST')
                query = query.exclude(session_key=request.session.session_key)
            else:
                try:
                    session_id = uuid.UUID(data['session_id'])
                except (TypeError, ValueError, AttributeError) as exc:
                    raise ValueError('INVALID_REQUEST') from exc
                query = query.filter(pk=session_id)
                if not query.exists():
                    return error(request, 'NOT_FOUND', 404)
                if query.filter(session_key=request.session.session_key).exists():
                    return error(request, 'CURRENT_SESSION_PROTECTED', 409, '请通过退出登录注销当前设备。')
            with transaction.atomic():
                # Stable ordering serializes crossed multi-device revocations.
                rows = list(AccountSession.objects.select_for_update().filter(
                    user=request.user).order_by('id'))
                _lock_current(request)
                ids = set(query.filter(revoked_at__isnull=True).values_list('id', flat=True))
                targets = [row for row in rows if row.pk in ids]
                Session.objects.filter(pk__in=[row.session_key for row in targets]).delete()
                AccountSession.objects.filter(pk__in=ids).update(revoked_at=timezone.now())
                _audit(request, 'auth.other_sessions_revoked')
            return JsonResponse({'revoked_count': len(targets)})
        if path == 'account/avatar-image':
            profile = AccountProfile.objects.filter(user=request.user).first()
            if not profile or not profile.avatar_name:
                return error(request, 'NOT_FOUND', 404)
            response = FileResponse(default_storage.open(profile.avatar_name, 'rb'), content_type='image/jpeg')
            response['Cache-Control'] = 'no-store'
            return response
        if path == 'account/avatar':
            encoded = None
            if request.method == 'POST':
                length = request.META.get('CONTENT_LENGTH', '')
                if not length.isdecimal() or int(length) > 2 * 1024 * 1024 + 65536:
                    raise ValueError('AVATAR_TOO_LARGE')
                if request.content_type != 'multipart/form-data' or set(request.FILES) != {'avatar'} or request.POST or len(request.FILES.getlist('avatar')) != 1:
                    raise ValueError('INVALID_REQUEST')
                encoded = _avatar_bytes(request.FILES['avatar'])
            else:
                payload(request, ())
            token = None
            if encoded is not None:
                _avatar_storage_ready()
                token = 'avatars/' + uuid.uuid4().hex + '/'
                # Reject ordinary nested transactions before touching media. TestCase's
                # special outer transaction is not evidence of this durable commit.
                with transaction.atomic(durable=True):
                    _lock_current(request)
                    profile, _ = AccountProfile.objects.get_or_create(user=request.user)
                    profile = AccountProfile.objects.select_for_update().get(pk=profile.pk)
                    if not isinstance(profile.pending_avatar_deletions, list):
                        raise OSError('AVATAR_QUEUE_INVALID')
                    profile.pending_avatar_deletions.append(token)
                    profile.save(update_fields=['pending_avatar_deletions'])
            # Contexts exit right-to-left: the file lock survives real COMMIT/ROLLBACK.
            # No compensating write/delete: commit acknowledgement can be uncertain.
            with ExitStack() as locks, transaction.atomic():
                _lock_current(request)
                profile, _ = AccountProfile.objects.get_or_create(user=request.user)
                profile = AccountProfile.objects.select_for_update().get(pk=profile.pk)
                if not isinstance(profile.pending_avatar_deletions, list):
                    raise OSError('AVATAR_QUEUE_INVALID')
                previous = profile.avatar_name
                saved = None
                if token is not None:
                    if token not in profile.pending_avatar_deletions:
                        raise ValueError('INVALID_REQUEST')
                    directory = locks.enter_context(_avatar_namespace_lock(token, create=True))
                    _lock_current(request)
                    profile.refresh_from_db()
                    if token not in profile.pending_avatar_deletions:
                        raise ValueError('INVALID_REQUEST')
                    if _avatar_namespace_files(token, directory):
                        raise OSError('AVATAR_NAMESPACE_CONTENTS_INVALID')
                    saved = default_storage.save(token + 'avatar.jpg', ContentFile(encoded))
                    files = _avatar_namespace_files(token, directory)
                    if (not isinstance(saved, str) or not saved.startswith(token) or
                            files != [saved[len(token):]]):
                        raise OSError('AVATAR_SAVE_RESULT_INVALID')
                    fd = os.open(files[0], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory[0])
                    with os.fdopen(fd, 'rb') as stored:
                        if stored.read(len(encoded) + 1) != encoded:
                            raise OSError('AVATAR_SAVE_CONTENTS_INVALID')
                    profile.pending_avatar_deletions.remove(token)
                profile.avatar_name = saved or ''
                if previous and previous not in profile.pending_avatar_deletions:
                    profile.pending_avatar_deletions.append(previous)
                profile.save(update_fields=['avatar_name', 'pending_avatar_deletions'])
                _audit(request, 'profile.avatar_updated')
            # Schedule outside the upload rollback handler: an old-file cleanup
            # failure must never remove the new, already committed avatar.
            transaction.on_commit(lambda: cleanup_avatar_files(request.user.pk))
            profile.refresh_from_db()
            return JsonResponse({'avatar_url': avatar_url(profile),
                                 'cleanup_pending': bool(profile.pending_avatar_deletions)})
        if path == 'account/deletion-eligibility':
            return JsonResponse(deletion_eligibility(request.user))
        if path == 'account/deletion-challenges':
            return deletion_challenge(request)
        if path == 'account/delete':
            return delete_account(request)
    except ValueError as exc:
        code = str(exc)
        allowed = {'INVALID_REQUEST', 'INVALID_JSON', 'INVALID_BODY_SIZE', 'JSON_REQUIRED',
                   'AUTH_REQUIRED', 'INVALID_AVATAR', 'AVATAR_TOO_LARGE'}
        if code not in allowed:
            raise
        return error(request, code, 401 if code == 'AUTH_REQUIRED' else 413 if code == 'AVATAR_TOO_LARGE' else 422)
