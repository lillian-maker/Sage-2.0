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
import json
import secrets
import smtplib
from datetime import date, timedelta
from urllib.parse import urlsplit

from django.contrib.sessions.models import Session
from django.conf import settings
from django.contrib.auth import get_user, get_user_model, logout
from django.core.mail import send_mail
from django.utils.crypto import constant_time_compare
from django.views.decorators.debug import sensitive_variables
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage, FileSystemStorage
from django.db import models, transaction, IntegrityError, connection
from django.http import FileResponse, JsonResponse
from django.utils import timezone

from .http import client_ip, error
from .models import AccountProfile, AccountSession, AuditEvent, LoginIdentity, Membership
from .models import (AccountDeletionChallenge, AccountDeletionRequest, AccountDeletionAudit,
                     TrialIdentityMarker, TrialAttempt, BillingAccount)
from .models import (AccountPasskey, PasskeyChallenge, PasskeyRecovery, PasskeyRecoveryCode,
                     LoginChallenge, InvoiceRequest, InvoiceAudit)

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
            revoked_at__isnull=True).update(revoked_at=timezone.now(), sensitive_verified_at=None,
                sensitive_method='', sensitive_account_id=None)


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
    if action in ('auth.passkey_registered','auth.passkey_removed','auth.business_sensitive_verified'):
        from .notifications import record_security_notice
        record_security_notice(request.user,action+':'+str(request.request_id))


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


def deletion_ready(user=None):
    from .views import email_ready
    try:
        reviewed = date.fromisoformat(settings.ACCOUNT_DELETION_POLICY_REVIEWED_AT)
    except (TypeError, ValueError):
        return False
    policy_ready = bool(settings.ACCOUNT_DELETION_ENABLED and
        settings.ACCOUNT_DELETION_POLICY_VERSION and reviewed <= timezone.localdate() and
        settings.ACCOUNT_DELETION_RETENTION_DAYS > 0 and
        settings.ACCOUNT_DELETION_MARKER_RETENTION_DAYS > 0 and
        settings.ACCOUNT_DELETION_RETENTION_MONTHS == 12 and
        len(settings.ACCOUNT_DELETION_MARKER_KEY) >= 50)
    if not policy_ready:
        return False
    if user is None or LoginIdentity.objects.filter(user=user,method='email').exists():
        return email_ready(settings.ACCOUNT_DELETION_SES_TEMPLATE_ID)
    from .sms import sms_auth_ready
    phone = LoginIdentity.objects.filter(user=user,method='phone').first()
    return bool(phone and user.has_usable_password() and sms_auth_ready(phone.identifier))


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
    available = deletion_ready(user)
    if not available:
        blockers.insert(0, 'RETENTION_POLICY_PENDING')
    identity = LoginIdentity.objects.filter(user=user,method__in=('email','phone')).order_by('method').first()
    if not identity:
        blockers.append('IDENTITY_VERIFICATION_REQUIRED')
        available = False
    try:
        first_due,completion_due = _business_due(timezone.now(),2),_business_due(timezone.now(),7)
    except ValueError:
        first_due=completion_due=None
        blockers.append('CALENDAR_YEAR_UNAVAILABLE')
    return {'eligible': available and not blockers, 'available': available, 'blockers': blockers,
            'verification_method':identity.method if identity else None,
            'calendar_ready':first_due is not None,
            'first_response_due_at':first_due.isoformat() if first_due else None,
            'completion_due_at':completion_due.isoformat() if completion_due else None,
            'policy_version': settings.ACCOUNT_DELETION_POLICY_VERSION,
            'request': deletion_request_data(AccountDeletionRequest.objects.filter(user=user).first()),
            'retained_data': ['necessary_billing_records'],
            'message': ('日历尚未核实，申请将交专人处理，不自动承诺截止日期。' if first_due is None else
                '复杂账号提交申请后由专人核验，2个工作日内首次回复，原则上7个工作日内完成。') if available else
                '账号注销尚未开放，等待保留规则审定及原登录方式验证接通。'}


def _business_due(now, days):
    # 国办发明电〔2025〕7号, retrieved from the State Council official notice:
    # https://www.gov.cn/zhengce/content/202511/content_7047090.htm
    # Never extrapolate unpublished future years or substitute a third-party calendar.
    if not timezone.is_aware(now) or type(days) is not int or days < 0:
        raise ValueError('INVALID_REQUEST')
    now=timezone.localtime(now)
    holiday_ranges=((1,1,3),(2,15,23),(4,4,6),(5,1,5),(6,19,21),(9,25,27),(10,1,7))
    holidays={date(2026,m,d) for m,start,end in holiday_ranges for d in range(start,end+1)}
    workdays={date(2026,m,d) for m,d in ((1,4),(2,14),(2,28),(5,9),(9,20),(10,10))}
    if now.year!=2026:
        raise ValueError('CALENDAR_YEAR_UNAVAILABLE')
    while days:
        now += timedelta(days=1)
        if now.year!=2026:
            raise ValueError('CALENDAR_YEAR_UNAVAILABLE')
        if now.date() in workdays or (now.weekday()<5 and now.date() not in holidays):
            days -= 1
    return now


def _retention_due(now):
    from .billing_rules import month_boundary
    return month_boundary(now,settings.ACCOUNT_DELETION_RETENTION_MONTHS)


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
                defaults={'expires_at': _retention_due(now),
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
    AccountPasskey.objects.filter(user=user).delete()
    PasskeyChallenge.objects.filter(user=user).delete()
    PasskeyRecoveryCode.objects.filter(user=user).delete()
    PasskeyRecovery.objects.filter(user=user).delete()
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
    row.retain_until = _retention_due(now)
    row.delay_reason, row.progress_message = '', ''
    row.save()
    transaction.on_commit(lambda: cleanup_avatar_files(user.pk))


@sensitive_variables()
def deletion_challenge(request):
    from .views import payload, digest, throttle
    if not deletion_ready(getattr(request,'user',None)):
        return error(request, 'RETENTION_POLICY_PENDING', 503)
    identity = LoginIdentity.objects.filter(user=request.user, method='email').first()
    if not identity:
        identity = LoginIdentity.objects.filter(user=request.user,method='phone').first()
    if not identity:
        return error(request, 'IDENTITY_VERIFICATION_REQUIRED', 409)
    data=payload(request,('password',) if identity.method=='phone' else ())
    code, now, challenge_id = f'{secrets.randbelow(1000000):06d}', timezone.now(), uuid.uuid4()
    with transaction.atomic():
        _lock_current(request)
        if not throttle('deletion-ip:' + client_ip(request), settings.AUTH_IP_LIMIT) or not throttle(
                'deletion-user:' + str(request.user.pk), settings.AUTH_IDENTIFIER_LIMIT, settings.AUTH_RESEND_SECONDS):
            return error(request, 'RATE_LIMITED', 429)
        user=get_user_model().objects.get(pk=request.user.pk)
        if identity.method=='phone' and (not isinstance(data['password'],str) or not user.check_password(data['password'])):
            return error(request,'INVALID_CREDENTIALS',401)
        AccountDeletionChallenge.objects.filter(user=request.user, consumed_at__isnull=True).update(consumed_at=now)
        row = AccountDeletionChallenge.objects.create(id=challenge_id, user=request.user,
            method=identity.method,password_digest=digest(user.password) if identity.method=='phone' else '',
            identifier_digest=trial_identifier_digest(identity.method, identity.identifier),
            session_digest=digest(request.session.session_key), code_digest=digest(str(challenge_id) + ':' + code),
            expires_at=now + timedelta(seconds=settings.AUTH_CODE_TTL_SECONDS))
    try:
        if identity.method=='phone':
            from .sms import send_code
            sent=send_code(identity.identifier,code,purpose='account_deletion')
        elif settings.AUTH_EMAIL_TRANSPORT == 'ses':
            from .email import send_deletion_code
            sent = send_deletion_code(identity.identifier, code)
        else:
            sent = send_mail('Sage 账号注销验证码 / Account deletion code',
                f'账号注销验证码 / Account deletion code: {code}\n仅用于永久注销账号，请勿分享。',
                settings.DEFAULT_FROM_EMAIL, [identity.identifier], fail_silently=False)
    except (OSError, smtplib.SMTPException):
        logger.warning('deletion_delivery_failed request_id=%s', request.request_id)
        return error(request, 'DELIVERY_FAILED', 503)
    remaining=int((row.expires_at-timezone.now()).total_seconds())
    if sent != 1 or remaining<=0:
        return error(request, 'DELIVERY_FAILED', 503)
    delivered=AccountDeletionChallenge.objects.filter(pk=row.pk,consumed_at__isnull=True,
        expires_at__gt=timezone.now()).update(delivered=True)
    if delivered!=1:
        return error(request,'DELIVERY_FAILED',503)
    return JsonResponse({'challenge_id': str(row.pk), 'expires_in': remaining,
                         'resend_after': settings.AUTH_RESEND_SECONDS}, status=201)


@sensitive_variables()
def delete_account(request):
    from .views import payload, digest
    if not deletion_ready(request.user):
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
        identity = LoginIdentity.objects.filter(user=user,method=challenge.method).first() if challenge else None
        now = timezone.now()
        if (not challenge or not identity or not challenge.delivered or challenge.consumed_at or
                challenge.expires_at <= now or challenge.attempts >= settings.AUTH_CODE_MAX_ATTEMPTS or
                not constant_time_compare(challenge.session_digest, digest(request.session.session_key)) or
                not constant_time_compare(challenge.identifier_digest, trial_identifier_digest(identity.method, identity.identifier)) or
                (identity.method=='phone' and not constant_time_compare(challenge.password_digest,digest(user.password)))):
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
        try:
            first_due,completion_due=_business_due(now,2),_business_due(now,7)
        except ValueError:
            first_due=completion_due=None
            blockers.append('CALENDAR_YEAR_UNAVAILABLE')
        row = AccountDeletionRequest.objects.create(user=user, policy_version=data['policy_version'], blockers=blockers,
            first_response_due_at=first_due, completion_due_at=completion_due,
            retain_until=_retention_due(now))
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
        if not deletion_ready(user) or row.policy_version != settings.ACCOUNT_DELETION_POLICY_VERSION:
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


def _passkey_ready():
    try:
        origin = urlsplit(settings.PASSKEY_ORIGIN)
        return bool(settings.PASSKEY_RP_ID and origin.scheme == 'https' and
            origin.hostname == settings.PASSKEY_RP_ID and origin.netloc == settings.PASSKEY_RP_ID and
            origin.path in ('','/') and not origin.query and not origin.fragment and
            not origin.username and not origin.password)
    except (TypeError, ValueError):
        return False


def _fresh_internal(user):
    fresh = get_user_model().objects.get(pk=user.pk)
    if not fresh.is_active or not fresh.is_staff:
        raise ValueError('FORBIDDEN')
    return fresh


def require_sensitive(request, account=None):
    """Call inside the action's transaction; fixed 15 minutes, live permission/session."""
    with transaction.atomic():
        if (not getattr(request,'user',None) or not request.user.is_authenticated or
                not getattr(request,'session',None) or not request.session.session_key):
            raise ValueError('AUTH_REQUIRED')
        register_session(request)
        _lock_current(request)
        user = get_user_model().objects.get(pk=request.user.pk)
        row = AccountSession.objects.get(session_key=request.session.session_key)
        now = timezone.now()
        if (row.sensitive_verified_at is None or row.sensitive_verified_at > now or
                row.sensitive_verified_at + timedelta(minutes=15) <= now):
            raise ValueError('SENSITIVE_CONFIRMATION_REQUIRED')
        if user.is_staff:
            if not _passkey_ready() or row.sensitive_method != 'passkey' or not AccountPasskey.objects.filter(
                    user=user,revoked_at__isnull=True).exists():
                raise ValueError('SENSITIVE_CONFIRMATION_REQUIRED')
        else:
            account_id = getattr(account,'pk',account)
            from .sms import sms_auth_ready
            if (not account_id or str(row.sensitive_account_id) != str(account_id) or
                    row.sensitive_method != 'phone' or not sms_auth_ready()):
                raise ValueError('SENSITIVE_CONFIRMATION_REQUIRED')
            if not Membership.objects.filter(user=user,account_id=account_id,account__kind='business',
                    is_active=True,role__in=('owner','billing_admin')).exists():
                raise ValueError('FORBIDDEN')
        return row


def _confirm_sensitive(request, method, account_id=None):
    now = timezone.now()
    AccountSession.objects.filter(session_key=request.session.session_key,user=request.user,
        revoked_at__isnull=True).update(sensitive_verified_at=now,sensitive_method=method,
        sensitive_account_id=account_id)
    return {'verified_until':(now+timedelta(minutes=15)).isoformat(),'method':method,
            **({'account_id':str(account_id)} if account_id else {})}


def _recovery_available(user,request):
    from .views import digest
    return PasskeyRecovery.objects.filter(user=user,used_at__isnull=True,expires_at__gt=timezone.now(),
        session_digest=digest(request.session.session_key)).exists()


def authorize_passkey_recovery(user, *, reason, session_id):
    """Server-command/one-use recovery-code boundary; never authorize sensitive actions."""
    if not isinstance(reason,str) or not re.fullmatch(r'[A-Za-z0-9_-]{3,80}',reason):
        raise ValueError('INVALID_RECOVERY_REASON')
    with transaction.atomic():
        list(AccountSession.objects.select_for_update().filter(user=user).order_by('pk'))
        user = get_user_model().objects.select_for_update().get(pk=user.pk)
        _fresh_internal(user)
        now = timezone.now()
        try:
            session_id=uuid.UUID(str(session_id))
        except (ValueError,TypeError,AttributeError):
            raise ValueError('FRESH_INTERNAL_SESSION_REQUIRED') from None
        selected=AccountSession.objects.filter(pk=session_id,user=user,revoked_at__isnull=True,
            created_at__gte=now-timedelta(minutes=5)).first()
        if not selected or not Session.objects.filter(pk=selected.session_key,expire_date__gt=now).exists():
            raise ValueError('FRESH_INTERNAL_SESSION_REQUIRED')
        keys = list(AccountSession.objects.filter(user=user).exclude(pk=selected.pk).values_list('session_key',flat=True))
        Session.objects.filter(pk__in=keys).delete()
        AccountSession.objects.filter(user=user).exclude(pk=selected.pk).update(revoked_at=now)
        AccountSession.objects.filter(user=user).update(sensitive_verified_at=None,
            sensitive_method='',sensitive_account_id=None)
        PasskeyChallenge.objects.filter(user=user,consumed_at__isnull=True).update(consumed_at=now)
        AccountPasskey.objects.filter(user=user,revoked_at__isnull=True).update(revoked_at=now)
        PasskeyRecoveryCode.objects.filter(user=user,consumed_at__isnull=True).update(consumed_at=now)
        from .models import AuthFlow
        AuthFlow.objects.filter(user=user,consumed_at__isnull=True).update(consumed_at=now,password_hash='')
        LoginChallenge.objects.filter(password_user=user,consumed_at__isnull=True).update(consumed_at=now)
        from .views import digest
        PasskeyRecovery.objects.update_or_create(user=user,defaults={'authorized_at':now,
            'expires_at':now+timedelta(minutes=15),'used_at':None,'session_digest':digest(selected.session_key)})
        AuditEvent.objects.create(actor=user,action='auth.passkey_recovery_authorized',request_id=uuid.uuid4())


def security_data(request):
    from .sms import sms_auth_ready
    user = get_user_model().objects.get(pk=request.user.pk)
    keys = list(AccountPasskey.objects.filter(user=user,revoked_at__isnull=True).order_by('pk')) if user.is_staff else []
    row = AccountSession.objects.filter(session_key=request.session.session_key,user=user,revoked_at__isnull=True).first()
    valid = bool(row and row.sensitive_verified_at and
        row.sensitive_verified_at <= timezone.now() < row.sensitive_verified_at+timedelta(minutes=15))
    if valid and user.is_staff:
        valid = _passkey_ready() and row.sensitive_method == 'passkey' and bool(keys)
    elif valid:
        valid = row.sensitive_method == 'phone' and sms_auth_ready() and Membership.objects.filter(
            user=user,account_id=row.sensitive_account_id,account__kind='business',
            is_active=True,role__in=('owner','billing_admin')).exists()
    recovery = user.is_staff and _recovery_available(user,request)
    mode=_phone_binding_mode(user)
    google_recent=mode=='google' and _google_recent(request)
    return {'internal_user':user.is_staff,
        'phone_binding':{'available':bool(mode and _phone_binding_ready() and (mode!='google' or google_recent)),
            'requires_google_reauthentication':bool(mode=='google' and not google_recent),
            'password_required':mode=='password','new_password_required':mode=='google'},
        'passkeys':[{'id':str(k.pk),'label':k.label,'synced':k.synced,'created_at':k.created_at.isoformat()} for k in keys],
        'verified_until':(row.sensitive_verified_at+timedelta(minutes=15)).isoformat() if valid else None,
        'verified_method':row.sensitive_method if valid else None,
        'verified_account_id':str(row.sensitive_account_id) if valid and row.sensitive_account_id else None,
        'recovery_binding_available':bool(recovery),
        'capabilities':{'passkey_registration':bool(user.is_staff and _passkey_ready() and (valid or recovery)),
            'passkey_authentication':bool(user.is_staff and _passkey_ready() and keys),
            'passkey_removal':bool(user.is_staff and valid and len(keys)>1),
            'sms_business':bool(not user.is_staff and sms_auth_ready() and LoginIdentity.objects.filter(user=user,method='phone').exists()),
            'backup_codes':bool(user.is_staff and valid)}}


@sensitive_variables()
def _passkey_dispatch(request, path):
    from .views import payload, digest, throttle
    from webauthn import (generate_registration_options, generate_authentication_options,
        verify_registration_response, verify_authentication_response, options_to_json,
        base64url_to_bytes)
    from webauthn.helpers import bytes_to_base64url
    from webauthn.helpers.exceptions import WebAuthnException
    from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
        ResidentKeyRequirement, UserVerificationRequirement)
    _fresh_internal(request.user)
    if not _passkey_ready():
        raise ValueError('PASSKEY_UNAVAILABLE')
    if path.endswith('backup-codes'):
        payload(request,())
        with transaction.atomic():
            require_sensitive(request)
            PasskeyRecoveryCode.objects.filter(user=request.user,consumed_at__isnull=True).update(consumed_at=timezone.now())
            codes = [secrets.token_hex(12) for _ in range(8)]
            PasskeyRecoveryCode.objects.bulk_create([PasskeyRecoveryCode(user=request.user,
                digest=digest('passkey-recovery:'+str(request.user.pk)+':'+code)) for code in codes])
            _audit(request,'auth.passkey_recovery_codes_created')
            return JsonResponse({'codes':codes,'purpose':'rebind_only'},status=201)
    if path.endswith('recover'):
        data = payload(request,('password','code'))
        with transaction.atomic():
            _lock_current(request)
            user = _fresh_internal(request.user)
            if not throttle('passkey-recovery:'+str(user.pk),settings.AUTH_IDENTIFIER_LIMIT):
                return error(request,'RATE_LIMITED',429)
            if (not isinstance(data['password'],str) or not isinstance(data['code'],str) or
                    not re.fullmatch(r'[a-f0-9]{24}',data['code']) or not user.check_password(data['password'])):
                return error(request,'INVALID_CREDENTIALS',401)
            code = PasskeyRecoveryCode.objects.select_for_update().filter(user=user,
                digest=digest('passkey-recovery:'+str(user.pk)+':'+data['code']),consumed_at__isnull=True).first()
            if not code:
                return error(request,'INVALID_CREDENTIALS',401)
            authorize_passkey_recovery(user,reason='optional-recovery-code',session_id=AccountSession.objects.get(
                user=user,session_key=request.session.session_key).pk)
            return JsonResponse({'status':'rebind_required'})
    if path.endswith('remove'):
        data = payload(request,('id',))
        with transaction.atomic():
            require_sensitive(request)
            keys = AccountPasskey.objects.select_for_update().filter(user=request.user,revoked_at__isnull=True)
            if keys.count()<2:
                raise ValueError('LAST_CREDENTIAL_PROTECTED')
            key = keys.filter(pk=data['id']).first() if str(data['id']).isdigit() else None
            if key is None:
                raise ValueError('NOT_FOUND')
            key.revoked_at=timezone.now();key.save(update_fields=['revoked_at'])
            AccountSession.objects.filter(user=request.user).update(sensitive_verified_at=None,
                sensitive_method='',sensitive_account_id=None)
            _audit(request,'auth.passkey_removed')
            return JsonResponse({'removed':True,'id':str(key.pk)})
    if path.endswith('registration-options') or path.endswith('authentication-options'):
        registration = path.endswith('registration-options')
        data = payload(request,('label',) if registration else ())
        if registration and (not isinstance(data['label'],str) or not 1<=len(data['label'].strip())<=80 or
                any(ord(c)<32 for c in data['label'])):
            raise ValueError('INVALID_REQUEST')
        with transaction.atomic():
            _lock_current(request)
            if not throttle('passkey:'+str(request.user.pk),settings.AUTH_IDENTIFIER_LIMIT):
                return error(request,'RATE_LIMITED',429)
            if registration and not _recovery_available(request.user,request):
                require_sensitive(request)
            keys = AccountPasskey.objects.filter(user=request.user,revoked_at__isnull=True)
            descriptors = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(k.credential_id)) for k in keys]
            if not registration and not descriptors:
                raise ValueError('SENSITIVE_CONFIRMATION_REQUIRED')
            if registration:
                options = generate_registration_options(rp_id=settings.PASSKEY_RP_ID,rp_name='Sage',
                    user_id=str(request.user.pk).encode(),user_name='Sage administrator '+str(request.user.pk),
                    authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,
                        user_verification=UserVerificationRequirement.REQUIRED),exclude_credentials=descriptors)
            else:
                options=generate_authentication_options(rp_id=settings.PASSKEY_RP_ID,allow_credentials=descriptors,
                    user_verification=UserVerificationRequirement.REQUIRED)
            now=timezone.now()
            PasskeyChallenge.objects.filter(user=request.user,purpose='register' if registration else 'verify',
                consumed_at__isnull=True).update(consumed_at=now)
            row=PasskeyChallenge.objects.create(user=request.user,purpose='register' if registration else 'verify',
                challenge=bytes_to_base64url(options.challenge),session_digest=digest(request.session.session_key),
                label=data['label'].strip() if registration else '',expires_at=now+timedelta(minutes=5))
            return JsonResponse({'challenge_id':str(row.pk),'public_key':json.loads(options_to_json(options))})
    data=payload(request,('challenge_id','credential'))
    registration=path.endswith('register')
    if not isinstance(data['credential'],dict):
        raise ValueError('INVALID_REQUEST')
    try:
        challenge_id=uuid.UUID(data['challenge_id'])
    except (ValueError,TypeError,AttributeError):
        raise ValueError('INVALID_REQUEST') from None
    with transaction.atomic():
        _lock_current(request)
        row=PasskeyChallenge.objects.select_for_update().filter(pk=challenge_id,user=request.user,
            purpose='register' if registration else 'verify',consumed_at__isnull=True).first()
        now=timezone.now()
        if not row or row.expires_at<=now or not constant_time_compare(row.session_digest,digest(request.session.session_key)):
            raise ValueError('INVALID_CHALLENGE')
        row.consumed_at=now;row.save(update_fields=['consumed_at'])
        # Verification failure consumes this ceremony; never allow response replay.
        try:
            if registration:
                if not _recovery_available(request.user,request):
                    require_sensitive(request)
                verified=verify_registration_response(credential=data['credential'],
                    expected_challenge=base64url_to_bytes(row.challenge),expected_rp_id=settings.PASSKEY_RP_ID,
                    expected_origin=settings.PASSKEY_ORIGIN.rstrip('/'),require_user_verification=True)
                if row.expires_at<=timezone.now():
                    raise ValueError('INVALID_CHALLENGE')
                with transaction.atomic():
                    key=AccountPasskey.objects.create(user=request.user,credential_id=bytes_to_base64url(verified.credential_id),
                        public_key=bytes_to_base64url(verified.credential_public_key),sign_count=verified.sign_count,
                        label=row.label,synced=verified.credential_device_type.value=='multi_device',
                        backed_up=verified.credential_backed_up)
                    if row.expires_at<=timezone.now():
                        raise ValueError('INVALID_CHALLENGE')
                PasskeyRecovery.objects.filter(user=request.user,used_at__isnull=True).update(used_at=now)
                result={'id':str(key.pk),'label':key.label,'synced':key.synced}
            else:
                key=AccountPasskey.objects.select_for_update().filter(user=request.user,
                    credential_id=data['credential'].get('id'),revoked_at__isnull=True).first()
                if not key:
                    return error(request,'INVALID_CHALLENGE',422)
                verified=verify_authentication_response(credential=data['credential'],
                    expected_challenge=base64url_to_bytes(row.challenge),expected_rp_id=settings.PASSKEY_RP_ID,
                    expected_origin=settings.PASSKEY_ORIGIN.rstrip('/'),credential_public_key=base64url_to_bytes(key.public_key),
                    credential_current_sign_count=key.sign_count,require_user_verification=True)
                if row.expires_at<=timezone.now():
                    raise ValueError('INVALID_CHALLENGE')
                key.sign_count=verified.new_sign_count
                key.synced=verified.credential_device_type.value=='multi_device'
                key.backed_up=verified.credential_backed_up
                key.save(update_fields=['sign_count','synced','backed_up'])
                result={}
        except (WebAuthnException,ValueError,TypeError,KeyError,IntegrityError):
            return error(request,'INVALID_CHALLENGE',422)
        _audit(request,'auth.passkey_registered' if registration else 'auth.passkey_verified')
        return JsonResponse({**result,**_confirm_sensitive(request,'passkey')},status=201 if registration else 200)


@sensitive_variables()
def _business_sensitive(request, path):
    from .views import payload,digest,throttle
    from .sms import sms_auth_ready,send_code
    now=timezone.now()
    if path.endswith('sensitive-challenges'):
        data=payload(request,('account_id','password'))
        try:
            account_id=uuid.UUID(data['account_id'])
        except (ValueError,TypeError,AttributeError):
            raise ValueError('INVALID_REQUEST') from None
        with transaction.atomic():
            _lock_current(request)
            user=get_user_model().objects.get(pk=request.user.pk)
            if user.is_staff or not Membership.objects.filter(user=user,account_id=account_id,account__kind='business',
                    is_active=True,role__in=('owner','billing_admin')).exists():
                raise ValueError('FORBIDDEN')
            if not throttle('business-confirm:'+str(user.pk),settings.AUTH_IDENTIFIER_LIMIT,settings.AUTH_RESEND_SECONDS):
                return error(request,'RATE_LIMITED',429)
            identity=LoginIdentity.objects.filter(user=user,method='phone').first()
            if not identity or not sms_auth_ready(identity.identifier):
                raise ValueError('SMS_CHANNEL_UNAVAILABLE')
            if not isinstance(data['password'],str) or not user.check_password(data['password']):
                return error(request,'INVALID_CREDENTIALS',401)
            code=f'{secrets.randbelow(1000000):06d}';challenge_id=uuid.uuid4()
            LoginChallenge.objects.filter(password_user=user,purpose='business_sensitive',consumed_at__isnull=True).update(consumed_at=now)
            row=LoginChallenge.objects.create(id=challenge_id,password_user=user,account_id=account_id,
                method='phone',identifier=identity.identifier,purpose='business_sensitive',
                code_digest=digest(str(challenge_id)+':'+code),session_digest=digest(request.session.session_key),
                expires_at=now+timedelta(seconds=settings.AUTH_CODE_TTL_SECONDS))
        try:
            sent=send_code(identity.identifier,code,purpose='business_sensitive')
        except OSError:
            return error(request,'DELIVERY_FAILED',503)
        remaining=int((row.expires_at-timezone.now()).total_seconds())
        if sent!=1 or remaining<=0:
            return error(request,'DELIVERY_FAILED',503)
        delivered=LoginChallenge.objects.filter(pk=row.pk,consumed_at__isnull=True,
            expires_at__gt=timezone.now()).update(delivered=True)
        if delivered!=1:
            return error(request,'DELIVERY_FAILED',503)
        return JsonResponse({'challenge_id':str(row.pk),'expires_in':remaining,
            'resend_after':settings.AUTH_RESEND_SECONDS},status=201)
    data=payload(request,('challenge_id','code'))
    try:
        challenge_id=uuid.UUID(data['challenge_id'])
    except (ValueError,TypeError,AttributeError):
        raise ValueError('INVALID_REQUEST') from None
    with transaction.atomic():
        _lock_current(request)
        user=get_user_model().objects.get(pk=request.user.pk)
        row=LoginChallenge.objects.select_for_update().filter(pk=challenge_id,password_user=user,
            purpose='business_sensitive',method='phone').first()
        now=timezone.now()
        if (not row or not row.delivered or row.consumed_at or row.expires_at<=now or
                row.attempts>=settings.AUTH_CODE_MAX_ATTEMPTS or
                not constant_time_compare(row.session_digest,digest(request.session.session_key)) or
                not LoginIdentity.objects.filter(user=user,method='phone',identifier=row.identifier).exists()):
            return error(request,'INVALID_CODE',422)
        if user.is_staff or not Membership.objects.filter(user=user,account_id=row.account_id,account__kind='business',
                is_active=True,role__in=('owner','billing_admin')).exists():
            raise ValueError('FORBIDDEN')
        if not sms_auth_ready(row.identifier):
            raise ValueError('SMS_CHANNEL_UNAVAILABLE')
        row.attempts+=1;row.save(update_fields=['attempts'])
        if not isinstance(data['code'],str) or not re.fullmatch(r'[0-9]{6}',data['code']) or not constant_time_compare(
                row.code_digest,digest(str(row.pk)+':'+data['code'])):
            return error(request,'INVALID_CODE',422)
        if row.expires_at<=timezone.now():
            return error(request,'INVALID_CODE',422)
        row.consumed_at=now;row.save(update_fields=['consumed_at'])
        _audit(request,'auth.business_sensitive_verified')
        return JsonResponse(_confirm_sensitive(request,'phone',row.account_id))


def _phone_binding_mode(user):
    if user.is_staff or LoginIdentity.objects.filter(user=user,method='phone').exists():
        return None
    if LoginIdentity.objects.filter(user=user,method='email').exists() and user.has_usable_password():
        return 'password'
    if LoginIdentity.objects.filter(user=user,method='google').exists() and not LoginIdentity.objects.filter(
            user=user,method='email').exists():
        return 'google'
    return None


def _phone_binding_ready(identifier=None):
    from .sms import sms_auth_ready,phone_ready
    return sms_auth_ready(identifier) and phone_ready(identifier,purpose='phone_binding')


def _google_recent(request):
    row=AccountSession.objects.filter(user=request.user,session_key=request.session.session_key,
        revoked_at__isnull=True).first()
    now=timezone.now()
    return bool(row and row.google_verified_at and now-timedelta(minutes=5)<row.google_verified_at<=now)


@sensitive_variables()
def _phone_binding(request,path):
    from .views import payload,digest,throttle,validate_new_password
    from django.contrib.auth import update_session_auth_hash
    from django.contrib.auth.hashers import make_password,check_password
    from django.core.exceptions import ValidationError
    from .sms import send_code
    if path.endswith('challenges'):
        with transaction.atomic():
            user=_lock_deletion_user(request.user)
            _lock_current(request)
            mode=_phone_binding_mode(user)
            if not mode:
                raise ValueError('PHONE_BINDING_UNAVAILABLE')
            data=payload(request,('phone','new_password') if mode=='google' else ('phone','password'))
            phone=data['phone']
            if not isinstance(phone,str) or not re.fullmatch(r'\+861[0-9]{10}',phone):
                raise ValueError('INVALID_REQUEST')
            if not _phone_binding_ready(phone):
                raise ValueError('SMS_CHANNEL_UNAVAILABLE')
            if not throttle('phone-bind-user:'+str(user.pk),settings.AUTH_IDENTIFIER_LIMIT,settings.AUTH_RESEND_SECONDS) or not throttle(
                    'phone-bind-ip:'+client_ip(request),settings.AUTH_IP_LIMIT):
                return error(request,'RATE_LIMITED',429)
            if mode=='google':
                if not _google_recent(request):
                    raise ValueError('GOOGLE_REAUTHENTICATION_REQUIRED')
                try:
                    validate_new_password(data['new_password'],user)
                except ValidationError:
                    return error(request,'INVALID_PASSWORD',422)
                password_hash=make_password(data['new_password'])
            else:
                if not isinstance(data['password'],str) or not check_password(data['password'],user.password):
                    return error(request,'INVALID_CREDENTIALS',401)
                password_hash=''
            # Source is established by existing password / freshly validated OIDC,
            # but ownership of the proposed number still requires its dedicated SMS.
            now=timezone.now();code=f'{secrets.randbelow(1000000):06d}';challenge_id=uuid.uuid4()
            LoginChallenge.objects.filter(password_user=user,purpose='phone_binding',
                consumed_at__isnull=True).update(consumed_at=now,password_hash='')
            row=LoginChallenge.objects.create(id=challenge_id,password_user=user,purpose='phone_binding',
                method='phone',identifier=phone,password_hash=password_hash,
                session_digest=digest(request.session.session_key),code_digest=digest(str(challenge_id)+':'+code),
                expires_at=now+timedelta(seconds=settings.AUTH_CODE_TTL_SECONDS))
        try:
            sent=send_code(phone,code,purpose='phone_binding')
        except OSError:
            return error(request,'DELIVERY_FAILED',503)
        remaining=int((row.expires_at-timezone.now()).total_seconds())
        if sent!=1 or remaining<=0 or not LoginChallenge.objects.filter(pk=row.pk,consumed_at__isnull=True,
                expires_at__gt=timezone.now()).update(delivered=True):
            return error(request,'DELIVERY_FAILED',503)
        return JsonResponse({'challenge_id':str(row.pk),'expires_in':remaining,
            'resend_after':settings.AUTH_RESEND_SECONDS},status=201)
    data=payload(request,('challenge_id','code'))
    try:
        challenge_id=uuid.UUID(data['challenge_id'])
    except (ValueError,TypeError,AttributeError):
        raise ValueError('INVALID_REQUEST') from None
    with transaction.atomic():
        user=_lock_deletion_user(request.user)
        _lock_current(request)
        mode=_phone_binding_mode(user)
        row=LoginChallenge.objects.select_for_update().filter(pk=challenge_id,password_user=user,
            purpose='phone_binding',method='phone').first()
        now=timezone.now()
        if (not mode or not row or not row.delivered or row.consumed_at or row.expires_at<=now or
                row.attempts>=settings.AUTH_CODE_MAX_ATTEMPTS or
                not constant_time_compare(row.session_digest,digest(request.session.session_key)) or
                bool(row.password_hash)!=(mode=='google')):
            return error(request,'INVALID_CODE',422)
        if not _phone_binding_ready(row.identifier):
            raise ValueError('SMS_CHANNEL_UNAVAILABLE')
        if mode=='google' and not _google_recent(request):
            raise ValueError('GOOGLE_REAUTHENTICATION_REQUIRED')
        row.attempts+=1;row.save(update_fields=['attempts'])
        if not isinstance(data['code'],str) or not re.fullmatch(r'[0-9]{6}',data['code']) or not constant_time_compare(
                row.code_digest,digest(str(row.pk)+':'+data['code'])):
            return error(request,'INVALID_CODE',422)
        row.consumed_at=now
        try:
            with transaction.atomic():
                LoginIdentity.objects.create(user=user,method='phone',identifier=row.identifier)
        except IntegrityError:
            row.password_hash='';row.save(update_fields=['consumed_at','password_hash'])
            return error(request,'PHONE_ALREADY_REGISTERED',409)
        if row.expires_at<=timezone.now():
            # Roll back identity binding and password together after any DB wait.
            raise ValueError('INVALID_CHALLENGE')
        if mode=='google':
            user.password=row.password_hash;user.save(update_fields=['password'])
            # Keep only this newly verified browser, with Django's new auth hash.
            keys=list(AccountSession.objects.filter(user=user).exclude(session_key=request.session.session_key)
                .values_list('session_key',flat=True))
            Session.objects.filter(pk__in=keys).delete()
            AccountSession.objects.filter(user=user).exclude(session_key=request.session.session_key).update(revoked_at=now)
            AccountSession.objects.filter(user=user,session_key=request.session.session_key).update(revoked_at=now)
            request.user=user
            update_session_auth_hash(request,user)
            register_session(request)
        row.password_hash='';row.save(update_fields=['consumed_at','password_hash'])
        AccountSession.objects.filter(user=user).update(sensitive_verified_at=None,sensitive_method='',
            sensitive_account_id=None,google_verified_at=None)
        _audit(request,'auth.phone_bound')
        from .notifications import record_security_notice
        record_security_notice(user,'phone-bound:'+str(request.request_id))
        return JsonResponse({'bound':True,'phone':row.identifier,'confirmation_required':True})


def _invoice_access(request, account):
    user=get_user_model().objects.get(pk=request.user.pk)
    if not user.is_active or (account.kind=='personal' and account.personal_owner_id!=user.pk):
        raise ValueError('FORBIDDEN')
    if account.kind=='business':
        if not Membership.objects.filter(account=account,user=user,is_active=True,
                role__in=('owner','billing_admin')).exists():
            raise ValueError('FORBIDDEN')


def _invoice_data(row):
    return {'id':str(row.pk),'order_id':str(row.order_id),'title':row.title,
        'recipient_email':row.recipient_email,'status':row.status,'created_at':row.created_at.isoformat()}


@sensitive_variables()
def _send_invoice_email(row):
    # This sends only the finance-approved template/reference, never manufactures a tax invoice.
    from .email import Credential,ClientProfile,HttpProfile,SesClient,SendEmailRequest,TencentCloudSDKException
    http=HttpProfile();http.endpoint='ses.tencentcloudapi.com';http.reqTimeout=10
    client=SesClient(Credential(settings.SES_SECRET_ID,settings.SES_SECRET_KEY),settings.SES_REGION,
        ClientProfile(httpProfile=http,retryer=None,disable_region_breaker=True))
    message=SendEmailRequest()
    message.from_json_string(json.dumps({'FromEmailAddress':settings.DEFAULT_FROM_EMAIL,
        'Destination':[row.recipient_email],'Subject':'Sage 发票通知 / Invoice notice','TriggerType':1,
        'Template':{'TemplateID':int(settings.INVOICE_SES_TEMPLATE_ID),
            'TemplateData':json.dumps({'invoice_reference':row.invoice_reference})}}))
    try:
        result=client.SendEmail(message)
    except (TencentCloudSDKException,OSError):
        raise OSError('INVOICE_DELIVERY_UNKNOWN') from None
    reference=getattr(result,'MessageId',None)
    return reference if isinstance(reference,str) and 0<len(reference)<=128 else ''


def update_invoice_request(request, pk, *, action, invoice_reference=''):
    from .views import email_ready
    try:
        pk=uuid.UUID(str(pk))
    except (ValueError,TypeError,AttributeError):
        raise ValueError('INVALID_REQUEST') from None
    if action=='send' and connection.in_atomic_block and any(
            not getattr(block,'_from_testcase',False) for block in connection.atomic_blocks):
        # A provider call must never run before the attempt is durably saved.
        raise ValueError('INVOICE_DISPATCH_REQUIRES_COMMIT')
    with transaction.atomic():
        require_sensitive(request)
        actor=_fresh_internal(request.user)
        if not actor.has_perm('backend.manage_invoice'):
            raise ValueError('FORBIDDEN')
        hint=InvoiceRequest.objects.filter(pk=pk).first()
        if not hint:
            raise ValueError('NOT_FOUND')
        BillingAccount.objects.select_for_update().get(pk=hint.account_id)
        row=InvoiceRequest.objects.select_for_update().filter(pk=pk).first()
        if not row:
            raise ValueError('NOT_FOUND')
        if action=='accept' and row.status=='requested':
            row.status='accepted'
        elif action=='needs_information' and row.status in ('requested','accepted','needs_information'):
            row.status='needs_information'
        elif action=='record_issued':
            _invoice_refund_check(row)
            if not settings.INVOICE_FULFILLMENT_APPROVED:
                raise ValueError('FINANCIAL_POLICY_PENDING')
            if row.status not in ('accepted','needs_information') or not isinstance(invoice_reference,str) or not re.fullmatch(
                    r'[A-Za-z0-9_-]{3,128}',invoice_reference):
                raise ValueError('INVALID_INVOICE_TRANSITION')
            row.invoice_reference=invoice_reference;row.status='issued'
        elif action=='send':
            _invoice_refund_check(row)
            if not (settings.INVOICE_FULFILLMENT_APPROVED and settings.AUTH_EMAIL_TRANSPORT=='ses' and
                    email_ready(settings.INVOICE_SES_TEMPLATE_ID)):
                raise ValueError('FINANCIAL_POLICY_PENDING')
            if row.status not in ('issued','failed') or not row.invoice_reference:
                # An unknown attempt requires independent reconciliation, never blind retry.
                raise ValueError('INVALID_INVOICE_TRANSITION')
            row.status='sending'
        else:
            raise ValueError('INVALID_INVOICE_TRANSITION')
        row.handled_by=actor;row.save()
        InvoiceAudit.objects.create(request=row,actor=actor,action=action)
    if action=='send':
        try:
            reference=_send_invoice_email(row)
        except OSError:
            reference=None
        with transaction.atomic():
            row=InvoiceRequest.objects.select_for_update().get(pk=pk)
            accepted=isinstance(reference,str) and 0<len(reference)<=128
            row.status='sent' if accepted else 'unknown'
            row.provider_reference=reference if accepted else ''
            row.save(update_fields=['status','provider_reference','updated_at'])
            InvoiceAudit.objects.create(request=row,actor=actor,action=row.status)
    return _invoice_data(row)


def _invoice_refund_check(row):
    from .commerce.models import CreditRefund,ServiceRefund,PaymentFact
    payment_ids=set(str(v) for v in PaymentFact.objects.filter(order_id=row.order_id).values_list('pk',flat=True))
    if CreditRefund.objects.filter(payment__order_id=row.order_id).exclude(state='released').exists():
        raise ValueError('REFUND_REVIEW_REQUIRED')
    for refund in ServiceRefund.objects.filter(account_id=row.account_id).exclude(status='terminated'):
        if not isinstance(refund.allocations,list) or not refund.allocations or any(
                not isinstance(item,dict) or not item.get('payment_id') for item in refund.allocations):
            raise ValueError('REFUND_REVIEW_REQUIRED')
        if any(str(item.get('payment_id')) in payment_ids for item in refund.allocations):
            raise ValueError('REFUND_REVIEW_REQUIRED')


def _invoice_dispatch(request,path):
    from .views import payload
    from .commerce.models import Order,PaymentFact
    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError
    if path=='account/invoices/handle':
        data=payload(request,('id','action'),('invoice_reference',))
        return JsonResponse(update_invoice_request(request,data['id'],action=data['action'],
            invoice_reference=data.get('invoice_reference','')))
    if request.method=='GET':
        if set(request.GET)!={'account_id'} or len(request.GET.getlist('account_id'))!=1:
            raise ValueError('INVALID_REQUEST')
        try:
            account_id=uuid.UUID(request.GET['account_id'])
        except (ValueError,TypeError,AttributeError):
            raise ValueError('INVALID_REQUEST') from None
        account=BillingAccount.objects.filter(pk=account_id).first()
        if not account:
            raise ValueError('NOT_FOUND')
        _invoice_access(request,account)
        return JsonResponse({'items':[_invoice_data(row) for row in InvoiceRequest.objects.filter(
            account=account).order_by('-created_at')[:100]],'available':bool(settings.INVOICE_REQUEST_ENABLED)})
    data=payload(request,('order_id','recipient_email','title','tax_number','request_key'))
    try:
        order_id=uuid.UUID(data['order_id'])
        validate_email(data['recipient_email'])
    except (ValidationError,ValueError,TypeError,AttributeError):
        raise ValueError('INVALID_REQUEST') from None
    if (not isinstance(data['title'],str) or not 1<=len(data['title'].strip())<=200 or
            any(ord(c)<32 for c in data['title']) or not isinstance(data['tax_number'],str) or
            not re.fullmatch(r'[A-Za-z0-9]{0,32}',data['tax_number']) or not isinstance(data['request_key'],str) or
            not re.fullmatch(r'[A-Za-z0-9_-]{8,64}',data['request_key'])):
        raise ValueError('INVALID_REQUEST')
    with transaction.atomic():
        _lock_current(request)
        order=Order.objects.select_related('account').filter(pk=order_id).first()
        if not order:
            raise ValueError('NOT_FOUND')
        account=BillingAccount.objects.select_for_update().get(pk=order.account_id)
        _invoice_access(request,account)
        if account.kind=='business':
            require_sensitive(request,account)
        payload_hash=hashlib.sha256(json.dumps([str(order_id),data['recipient_email'],data['title'].strip(),
            data['tax_number']],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        existing=InvoiceRequest.objects.filter(account=account,request_key=data['request_key']).first()
        if existing:
            if existing.request_hash!=payload_hash:
                raise ValueError('IDEMPOTENCY_CONFLICT')
            return JsonResponse(_invoice_data(existing))
        if not settings.INVOICE_REQUEST_ENABLED:
            raise ValueError('INVOICE_REQUEST_UNAVAILABLE')
        if (order.status not in ('fulfilled','paid_pending_entitlement') or order.paid_at is None or
                not PaymentFact.objects.filter(order=order,disposition='accepted').exists() or
                InvoiceRequest.objects.filter(order=order).exists()):
            raise ValueError('INVALID_INVOICE_TRANSITION')
        row=InvoiceRequest.objects.create(account=account,order=order,created_by=request.user,
            recipient_email=data['recipient_email'],title=data['title'].strip(),tax_number=data['tax_number'],
            request_key=data['request_key'],request_hash=payload_hash)
        InvoiceAudit.objects.create(request=row,actor=request.user,action='requested')
        return JsonResponse(_invoice_data(row),status=201)


def dispatch(request, path):
    methods = {'account/profile': ('GET', 'PATCH'), 'account/preferences': ('GET', 'PATCH'),
        'account/sessions': ('GET',), 'account/sessions/revoke': ('POST',),
        'account/avatar': ('POST', 'DELETE'), 'account/avatar-image': ('GET',),
        'account/deletion-eligibility': ('GET',), 'account/deletion-challenges': ('POST',), 'account/delete': ('POST',),
        'account/security':('GET',), 'account/sensitive-challenges':('POST',),'account/sensitive-verify':('POST',),
        **{'account/passkeys/'+name:('POST',) for name in ('registration-options','register',
            'authentication-options','verify','remove','backup-codes','recover')},
        'account/invoices':('GET','POST'),'account/invoices/handle':('POST',)}
    methods.update({'account/phone/challenges':('POST',),'account/phone/verify':('POST',)})
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
        if path=='account/security':
            return JsonResponse(security_data(request))
        if path.startswith('account/passkeys/'):
            return _passkey_dispatch(request,path)
        if path in ('account/phone/challenges','account/phone/verify'):
            return _phone_binding(request,path)
        if path in ('account/sensitive-challenges','account/sensitive-verify'):
            return _business_sensitive(request,path)
        if path.startswith('account/invoices'):
            return _invoice_dispatch(request,path)
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
                   'AUTH_REQUIRED', 'INVALID_AVATAR', 'AVATAR_TOO_LARGE','FORBIDDEN','NOT_FOUND',
                   'SENSITIVE_CONFIRMATION_REQUIRED','RECOVERY_AUTHORIZATION_REQUIRED','PASSKEY_UNAVAILABLE',
                   'INVALID_CHALLENGE','LAST_CREDENTIAL_PROTECTED','SMS_CHANNEL_UNAVAILABLE',
                   'FINANCIAL_POLICY_PENDING','INVOICE_REQUEST_UNAVAILABLE','IDEMPOTENCY_CONFLICT',
                   'INVALID_INVOICE_TRANSITION','REFUND_REVIEW_REQUIRED','CALENDAR_YEAR_UNAVAILABLE',
                   'FRESH_INTERNAL_SESSION_REQUIRED','INVOICE_DISPATCH_REQUIRES_COMMIT'}
        allowed.update(('PHONE_BINDING_UNAVAILABLE','GOOGLE_REAUTHENTICATION_REQUIRED','PHONE_ALREADY_REGISTERED'))
        if code not in allowed:
            raise
        return error(request, code, 401 if code == 'AUTH_REQUIRED' else 403 if code in ('FORBIDDEN','SENSITIVE_CONFIRMATION_REQUIRED','GOOGLE_REAUTHENTICATION_REQUIRED')
            else 503 if code in ('PASSKEY_UNAVAILABLE','SMS_CHANNEL_UNAVAILABLE','FINANCIAL_POLICY_PENDING',
                'INVOICE_REQUEST_UNAVAILABLE','CALENDAR_YEAR_UNAVAILABLE') else 404 if code=='NOT_FOUND'
            else 413 if code == 'AVATAR_TOO_LARGE' else 422)
