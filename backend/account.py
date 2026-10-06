"""Small, same-origin account endpoints; reuse Django identity and sessions."""
import re
import uuid
import logging
from io import BytesIO
import warnings

from django.contrib.sessions.models import Session
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.http import FileResponse, JsonResponse
from django.utils import timezone

from .http import client_ip, error
from .models import AccountProfile, AccountSession, AuditEvent, LoginIdentity, Membership

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
    return {'display_name': user.first_name, 'email': identity.identifier if identity else '',
            'avatar_url': avatar_url(profile), 'email_read_only': True}


def _lock_current(request):
    record = AccountSession.objects.select_for_update().get(session_key=request.session.session_key)
    if record.revoked_at or record.user_id != request.user.pk or not request.user.is_active:
        raise ValueError('AUTH_REQUIRED')
    # A revoker holds this same record lock until the actual Session row is deleted.
    if not Session.objects.filter(pk=record.session_key, expire_date__gt=timezone.now()).exists():
        raise ValueError('AUTH_REQUIRED')


def _audit(request, action):
    AuditEvent.objects.create(actor=request.user, action=action, request_id=request.request_id)


def cleanup_avatar_files(user_id):
    """Retry durable old-file cleanup, serialized with uploads for this profile."""
    with transaction.atomic():
        profile = AccountProfile.objects.select_for_update().filter(user_id=user_id).first()
        if not profile:
            return 0
        remaining = []
        for name in profile.pending_avatar_deletions:
            if name == profile.avatar_name:
                # Never erase the live avatar even if a malformed queue contains it.
                remaining.append(name)
                logger.error('avatar_cleanup_current_file_protected')
                continue
            try:
                default_storage.delete(name)
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


def deletion_eligibility(user):
    from .commerce.models import Order, Subscription, SubscriptionPeriod
    accounts = Membership.objects.filter(user=user, is_active=True).values_list('account_id', flat=True)
    now = timezone.now()
    blockers = ['RETENTION_POLICY_PENDING']
    if (Subscription.objects.filter(account_id__in=accounts, auto_renew=True).exists() or
            SubscriptionPeriod.objects.filter(subscription__account_id__in=accounts,
                starts_at__lte=now, ends_at__gt=now).exists()):
        blockers.append('ACTIVE_SUBSCRIPTION')
    if Order.objects.filter(account_id__in=accounts).exclude(status__in=[
            'fulfilled', 'cancelled', 'expired', 'refunded']).exists():
        blockers.append('UNSETTLED_ORDER')
    for membership in Membership.objects.filter(user=user, is_active=True,
            account__kind='business', role__in=['owner', 'billing_admin']):
        if not Membership.objects.filter(account=membership.account, is_active=True,
                role__in=['owner', 'billing_admin']).exclude(user=user).exists():
            blockers.append('SOLE_BUSINESS_ADMIN')
            break
    return {'eligible': False, 'available': False, 'blockers': blockers,
            'retained_data': ['necessary_billing_records'],
            'message': '账号删除尚未开放，数据保留规则确认后提供邮箱验证与最终确认。'}


def dispatch(request, path):
    methods = {'account/profile': ('GET', 'PATCH'), 'account/preferences': ('GET', 'PATCH'),
        'account/sessions': ('GET',), 'account/sessions/revoke': ('POST',),
        'account/avatar': ('POST', 'DELETE'), 'account/avatar-image': ('GET',),
        'account/deletion-eligibility': ('GET',), 'account/delete': ('POST',)}
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
                return JsonResponse({'product_updates': profile.product_updates,
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
            saved = None
            try:
                with transaction.atomic():
                    _lock_current(request)
                    profile, _ = AccountProfile.objects.get_or_create(user=request.user)
                    profile = AccountProfile.objects.select_for_update().get(pk=profile.pk)
                    previous = profile.avatar_name
                    if encoded is not None:
                        saved = default_storage.save('avatars/' + uuid.uuid4().hex + '.jpg', ContentFile(encoded))
                    profile.avatar_name = saved or ''
                    if previous and previous not in profile.pending_avatar_deletions:
                        profile.pending_avatar_deletions.append(previous)
                    profile.save(update_fields=['avatar_name', 'pending_avatar_deletions'])
                    _audit(request, 'profile.avatar_updated')
            except Exception:
                if saved:
                    # Keep failed-transaction uploads retryable too; the old
                    # avatar was not committed away and must remain untouched.
                    profile, _ = AccountProfile.objects.get_or_create(user=request.user)
                    with transaction.atomic():
                        profile = AccountProfile.objects.select_for_update().get(pk=profile.pk)
                        if saved not in profile.pending_avatar_deletions:
                            profile.pending_avatar_deletions.append(saved)
                            profile.save(update_fields=['pending_avatar_deletions'])
                raise
            # Schedule outside the upload rollback handler: an old-file cleanup
            # failure must never remove the new, already committed avatar.
            transaction.on_commit(lambda: cleanup_avatar_files(request.user.pk))
            profile.refresh_from_db()
            return JsonResponse({'avatar_url': avatar_url(profile),
                                 'cleanup_pending': bool(profile.pending_avatar_deletions)})
        if path == 'account/deletion-eligibility':
            return JsonResponse(deletion_eligibility(request.user))
        if path == 'account/delete':
            return error(request, 'RETENTION_POLICY_PENDING', 503,
                         '账号删除尚未开放，数据保留规则确认后提供邮箱验证与最终确认。')
    except ValueError as exc:
        code = str(exc)
        allowed = {'INVALID_REQUEST', 'INVALID_JSON', 'INVALID_BODY_SIZE', 'JSON_REQUIRED',
                   'AUTH_REQUIRED', 'INVALID_AVATAR', 'AVATAR_TOO_LARGE'}
        if code not in allowed:
            raise
        return error(request, code, 401 if code == 'AUTH_REQUIRED' else 413 if code == 'AVATAR_TOO_LARGE' else 422)
