"""Account domain: App email-code login, profile settings, device sessions, notification matrix, memories, grants."""
import re
import uuid
from datetime import time, timedelta

from django.conf import settings
from django.contrib.auth import login
from django.db import transaction
from django.db.models import Case, IntegerField, Value, When
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from .. import clock
from django.views.decorators.debug import sensitive_variables

from backend.http import client_ip, error
from backend.models import AccountProfile, AccountSession, AuditEvent, AuthFlow, LoginIdentity

from ..common import iso, json_body, page, query
from ..models import AccountPolicy, AuthorizationGrant, MemoryItem, NotificationPreference

DEFAULT_ASSISTANT_NAME = 'Sage 助手'
SESSION_TOUCH_INTERVAL = timedelta(minutes=5)
SENSITIVE_WINDOW = timedelta(minutes=15)

# ---------------------------------------------------------------- email code login (C-D-1, C-D-2)


def email_code_enabled():
    # Off unless deployment opts in, so the website keeps the legacy closed behaviour (AUTH_FLOW_REQUIRED).
    return bool(getattr(settings, 'AUTH_EMAIL_CODE_LOGIN_ENABLED', False))


@sensitive_variables()
def email_code_request(request, path, data):
    """auth/challenges and auth/verify; data is the already validated exact-key payload."""
    if not email_code_enabled():
        return error(request, 'AUTH_FLOW_REQUIRED', 403, '请使用注册、密码登录或找回密码流程。')
    if path == 'auth/verify':
        return _email_code_verify(request, data)
    if data['method'] != 'email':
        return error(request, 'AUTH_FLOW_REQUIRED', 403, '请使用注册、密码登录或找回密码流程。')
    return _email_code_challenge(request, data)


@sensitive_variables()
def _email_code_challenge(request, data):
    from backend import authentication as auth
    from backend.account import _lock_deletion_user
    from backend.views import email_ready, password_identifier, throttle
    _, identifier = password_identifier(data)
    if request.user.is_authenticated:
        raise ValueError('INVALID_REQUEST')
    if not email_ready():
        return error(request, 'CHANNEL_UNAVAILABLE', 503)
    with transaction.atomic():
        auth._binding_lock(request)
        auth._identity_locks(request, identifier)
        if not throttle('auth-ip:' + client_ip(request), settings.AUTH_IP_LIMIT) or not throttle(
                'identity:email:' + identifier, settings.AUTH_IDENTIFIER_LIMIT, settings.AUTH_RESEND_SECONDS):
            return error(request, 'RATE_LIMITED', 429)
    with transaction.atomic():
        binding = auth._binding_lock(request)
        auth._identity_locks(request, identifier)
        identity = LoginIdentity.objects.select_related('user').filter(method='email', identifier=identifier).first()
        user = None
        if identity:
            try:
                user = _lock_deletion_user(identity.user)
            except ValueError as exc:
                if str(exc) != 'AUTH_REQUIRED':
                    raise
        now = clock.now()
        auth._invalidate(AuthFlow.objects.filter(session_digest=binding, consumed_at__isnull=True), now)
        # Unknown addresses get the same public response; _send_code skips delivery for them (Q-D-1: no auto signup).
        flow = AuthFlow.objects.create(purpose='email_code', method='email', identifier=identifier,
                                       session_digest=binding, user=user, stage='code',
                                       expires_at=now + timedelta(seconds=settings.AUTH_CODE_TTL_SECONDS))
        return auth._send_code(request, flow)


@sensitive_variables()
def _email_code_verify(request, data):
    from backend import authentication as auth
    from backend.account import register_session
    from backend.views import session_data
    if not isinstance(data['code'], str) or not re.fullmatch(r'[0-9]{6}', data['code']):
        raise ValueError('INVALID_CODE')
    try:
        challenge_id = uuid.UUID(data['challenge_id'])
    except (TypeError, ValueError, AttributeError):
        raise ValueError('INVALID_CODE') from None
    if request.user.is_authenticated:
        raise ValueError('INVALID_REQUEST')
    with transaction.atomic():
        binding = auth._binding_lock(request)
        hint = AuthFlow.objects.filter(challenge_id=challenge_id, session_digest=binding,
                                       purpose='email_code').first()
        if hint is None:
            raise ValueError('INVALID_CODE')
    flow_id, body = str(hint.pk), {'challenge_id': str(challenge_id)}
    with transaction.atomic():
        flow, _ = auth._locked_flow(request, flow_id, 'code', 'email_code')
        item = auth._locked_challenge(flow, body, 'email_code')
        if item.attempts >= settings.AUTH_CODE_MAX_ATTEMPTS:
            return error(request, 'INVALID_CODE', 422)
        item.attempts += 1
        item.save(update_fields=['attempts'])
        if not auth.constant_time_compare(item.code_digest, auth.digest(str(item.pk) + ':' + data['code'])):
            return error(request, 'INVALID_CODE', 422)
    # The attempt above is committed even if the login below fails.
    with transaction.atomic():
        flow, user = auth._locked_flow(request, flow_id, 'code', 'email_code')
        item = auth._locked_challenge(flow, body, 'email_code')
        if not auth.constant_time_compare(item.code_digest, auth.digest(str(item.pk) + ':' + data['code'])):
            raise ValueError('INVALID_CODE')
        if user is None or not LoginIdentity.objects.filter(user=user, method='email',
                                                             identifier=flow.identifier).exists():
            return error(request, 'INVALID_CODE', 422)
        auth._live(flow)
        now = clock.now()
        flow.consumed_at, item.consumed_at = now, now
        flow.save(update_fields=['consumed_at'])
        item.save(update_fields=['consumed_at'])
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        register_session(request)
        AuditEvent.objects.create(actor=user, action='auth.email_code_verified', request_id=request.request_id)
        return JsonResponse(session_data(request))


# ---------------------------------------------------------------- auth/session extras (C-D-4)


def session_extra(request, data):
    data['capabilities']['byok'] = False
    if email_code_enabled():
        from backend.views import email_ready
        data['capabilities']['email_login'] = bool(email_ready())
    if not data.get('user'):
        return data
    profile = AccountProfile.objects.filter(user=request.user).first()
    data['user']['onboarding_completed_at'] = iso(profile.onboarding_completed_at) if profile else None
    active = next((item for item in data['accounts'] if item['id'] == data['active_account_id']), None)
    if active and active['kind'] == 'business':
        data['capabilities']['byok'] = bool(AccountPolicy.objects.filter(
            account_id=active['id'], byok_enabled=True).exists())
    return data


# ---------------------------------------------------------------- device sessions (C-D-6)


def parse_client_header(value):
    """X-Sage-Client: "<device_name>; <app_version>"; anything malformed is ignored."""
    if not isinstance(value, str) or ';' not in value:
        return None
    device, version = (part.strip() for part in value.split(';', 1))
    if not (1 <= len(device) <= 80 and 1 <= len(version) <= 32):
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in device + version):
        return None
    return device, version


def touch_session(record, request):
    if record is None or record.revoked_at:
        return record
    updates = {}
    client = parse_client_header(request.headers.get('X-Sage-Client'))
    if client and (record.device_name, record.app_version) != client:
        updates['device_name'], updates['app_version'] = client
    now = clock.now()
    if record.last_seen_at is None or now - record.last_seen_at >= SESSION_TOUCH_INTERVAL:
        updates['last_seen_at'] = now
    if updates:
        AccountSession.objects.filter(pk=record.pk, revoked_at__isnull=True).update(**updates)
        for key, value in updates.items():
            setattr(record, key, value)
    return record


def session_item(row, request):
    return {'id': str(row.pk), 'client': row.client, 'location': row.location_label or '未知',
            'ip': row.ip or '', 'created_at': row.created_at.isoformat(),
            'is_current': row.session_key == request.session.session_key,
            'device_name': row.device_name, 'app_version': row.app_version,
            'last_seen_at': iso(row.last_seen_at)}


# ---------------------------------------------------------------- profile (C-D-5)

THEME_STYLES = ('business_steady', 'neo_electric', 'fashion_editorial', 'comfort_readable', 'minimal_focus')
CHOICES = {
    'theme_style': THEME_STYLES,
    'appearance_mode': ('system', 'light', 'dark'),
    'density': ('theme', 'comfortable', 'compact'),
    'language': ('system', 'zh-Hans', 'en'),
    'memory_model': ('standard', 'deep'),
    'default_model': ('auto', 'standard', 'deep'),
}
BOOLEANS = ('dnd_enabled', 'badge_enabled', 'memory_auto_enabled', 'complexity_hint_enabled', 'ext_auto_invoke',
            'ext_auto_update', 'ext_show_needs_connection', 'two_step_on_new_device')
LISTS = ('business_markets', 'business_channels', 'business_categories')
IDLE_LOCK_MINUTES = (15, 30, 60)
PROFILE_KEYS = ('display_name', 'assistant_name', 'preferred_name', 'dnd_start', 'dnd_end', 'idle_lock_minutes',
                'onboarding_completed') + BOOLEANS + LISTS + tuple(CHOICES)
LIST_MAX_ITEMS = 20
LIST_ITEM_MAX = 20
_HHMM = re.compile(r'([01][0-9]|2[0-3]):([0-5][0-9])')


def _plain(value):
    return isinstance(value, str) and not any(ord(char) < 32 or ord(char) == 127 for char in value)


def _hhmm(value):
    return value.strftime('%H:%M') if value is not None else None


def profile_extra(user, profile):
    return {
        'assistant_name': profile.assistant_name or DEFAULT_ASSISTANT_NAME,
        'preferred_name': profile.preferred_name or user.first_name,
        **{key: list(getattr(profile, key) or []) for key in LISTS},
        'onboarding_completed_at': iso(profile.onboarding_completed_at),
        **{key: getattr(profile, key) for key in CHOICES},
        **{key: getattr(profile, key) for key in BOOLEANS},
        'dnd_start': _hhmm(profile.dnd_start), 'dnd_end': _hhmm(profile.dnd_end),
        'idle_lock_minutes': profile.idle_lock_minutes,
    }


def _validated(key, value):
    """Returns the model value for one key or raises VALIDATION_FAILED."""
    if key in BOOLEANS:
        if type(value) is not bool:
            raise ValueError('VALIDATION_FAILED')
        return value
    if key in CHOICES:
        if value not in CHOICES[key]:
            raise ValueError('VALIDATION_FAILED')
        return value
    if key in LISTS:
        if not isinstance(value, list) or len(value) > LIST_MAX_ITEMS:
            raise ValueError('VALIDATION_FAILED')
        result = []
        for item in value:
            if not _plain(item) or not 1 <= len(item.strip()) <= LIST_ITEM_MAX:
                raise ValueError('VALIDATION_FAILED')
            if item.strip() not in result:
                result.append(item.strip())
        return result
    if key == 'assistant_name':
        if value is None or value == '':
            return ''
        if not _plain(value) or not 2 <= len(value.strip()) <= 8:
            raise ValueError('VALIDATION_FAILED')
        return value.strip()
    if key == 'preferred_name':
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if not _plain(value) or len(value.strip()) > 40:
            raise ValueError('VALIDATION_FAILED')
        return value.strip()
    if key in ('dnd_start', 'dnd_end'):
        if value is None:
            return None
        match = _HHMM.fullmatch(value) if isinstance(value, str) else None
        if not match:
            raise ValueError('VALIDATION_FAILED')
        return time(int(match.group(1)), int(match.group(2)))
    if key == 'idle_lock_minutes':
        if value is not None and (type(value) is not int or value not in IDLE_LOCK_MINUTES):
            raise ValueError('VALIDATION_FAILED')
        return value
    if key == 'onboarding_completed':
        if value is not True:
            raise ValueError('VALIDATION_FAILED')
        return value
    raise ValueError('INVALID_REQUEST')


def _sensitive_recent(request):
    row = AccountSession.objects.filter(session_key=request.session.session_key, user=request.user,
                                        revoked_at__isnull=True).first()
    now = clock.now()
    return bool(row and row.sensitive_verified_at and row.sensitive_verified_at <= now < row.sensitive_verified_at + SENSITIVE_WINDOW)


def patch_profile(request):
    """PATCH account/profile; the caller returns profile_data afterwards."""
    from backend.account import _audit, _lock_current
    from backend.views import payload
    data = payload(request, (), optional=PROFILE_KEYS)
    if not data:
        raise ValueError('INVALID_REQUEST')
    name = None
    if 'display_name' in data:
        name = data['display_name']
        if (not isinstance(name, str) or not name.strip() or len(name.strip()) > 80 or
                any(ord(char) < 32 or ord(char) == 127 for char in name)):
            raise ValueError('INVALID_REQUEST')
        name = name.strip()
    changes = {key: _validated(key, value) for key, value in data.items() if key != 'display_name'}
    with transaction.atomic():
        _lock_current(request)
        profile, _ = AccountProfile.objects.select_for_update().get_or_create(user=request.user)
        fields = []
        for key, value in changes.items():
            if key == 'onboarding_completed':
                if profile.onboarding_completed_at is None:
                    profile.onboarding_completed_at = clock.now()
                    fields.append('onboarding_completed_at')
                continue
            if key == 'two_step_on_new_device' and profile.two_step_on_new_device and value is False and \
                    not _sensitive_recent(request):
                raise ValueError('SENSITIVE_CONFIRMATION_REQUIRED')
            setattr(profile, key, value)
            fields.append(key)
        if profile.dnd_start is not None and profile.dnd_start == profile.dnd_end:
            raise ValueError('VALIDATION_FAILED')
        if fields:
            profile.save(update_fields=fields)
            _audit(request, 'profile.settings_updated')
        if name is not None:
            request.user.first_name = name
            request.user.save(update_fields=['first_name'])
            _audit(request, 'profile.name_updated')


# ---------------------------------------------------------------- notification matrix (C-D-19)

NOTIFICATION_TYPES = ('needs_action', 'task_completed', 'mention_assign', 'billing')
NOTIFICATION_CHANNELS = ('in_app', 'push', 'email')


def notification_default(type_, channel):
    """Q-O16 A: in-app on for all, push only for needs_action, email off."""
    return channel == 'in_app' or (channel == 'push' and type_ == 'needs_action')


def notification_settings(ctx):
    stored = {(row.type, row.channel): row.enabled for row in NotificationPreference.objects.filter(
        account=ctx.account, user=ctx.user)}
    items = []
    for type_ in NOTIFICATION_TYPES:
        for channel in NOTIFICATION_CHANNELS:
            default = notification_default(type_, channel)
            enabled = stored.get((type_, channel), default)
            items.append({'type': type_, 'channel': channel, 'enabled': enabled, 'is_default': enabled == default})
    return {'items': items}


def replace_notification_settings(ctx, items):
    expected = {(t, c) for t in NOTIFICATION_TYPES for c in NOTIFICATION_CHANNELS}
    if not isinstance(items, list) or len(items) != len(expected):
        raise ValueError('VALIDATION_FAILED')
    values = {}
    for item in items:
        if (not isinstance(item, dict) or set(item) != {'type', 'channel', 'enabled'} or
                type(item['enabled']) is not bool):
            raise ValueError('VALIDATION_FAILED')
        key = (item['type'], item['channel'])
        if key not in expected or key in values:
            raise ValueError('VALIDATION_FAILED')
        values[key] = item['enabled']
    with transaction.atomic():
        rows = NotificationPreference.objects.select_for_update().filter(account=ctx.account, user=ctx.user)
        existing = {(row.type, row.channel): row for row in rows}
        # Only deviations from the default are stored, so "no row" keeps meaning "default" (E11).
        for (type_, channel), enabled in values.items():
            row = existing.get((type_, channel))
            if enabled == notification_default(type_, channel):
                if row:
                    row.delete()
            elif row:
                if row.enabled != enabled:
                    row.enabled = enabled
                    row.save(update_fields=['enabled', 'updated_at'])
            else:
                NotificationPreference.objects.create(account=ctx.account, user=ctx.user, type=type_,
                                                      channel=channel, enabled=enabled)
    return notification_settings(ctx)


# ---------------------------------------------------------------- memories (C-D-16)

MEMORY_CATEGORIES = ('about_me', 'recent_projects', 'lessons')


def _memories(ctx):
    return MemoryItem.objects.filter(account=ctx.account, user=ctx.user)


def memory_item(row):
    task = row.source_task
    return {'id': row.public_id, 'category': row.category, 'content': row.content, 'source_kind': row.source_kind,
            'source_task': {'id': task.public_id, 'title': task.title} if task else None,
            'created_at': iso(row.created_at), 'edited_at': iso(row.edited_at)}


def memory_list(request, ctx):
    params = query(request, ('category', 'offset'))
    rows = _memories(ctx)
    counts = {category: 0 for category in MEMORY_CATEGORIES}
    for category in rows.values_list('category', flat=True):
        counts[category] += 1
    if 'category' in params:
        if params['category'] not in MEMORY_CATEGORIES:
            raise ValueError('INVALID_REQUEST')
        rows = rows.filter(category=params['category'])
    order = Case(*[When(category=value, then=Value(index)) for index, value in enumerate(MEMORY_CATEGORIES)],
                 output_field=IntegerField())
    rows = rows.select_related('source_task').annotate(category_order=order).order_by(
        'category_order', '-created_at', '-id')
    result = page(request, rows, memory_item)
    result['category_counts'] = counts
    return result


def _memory(ctx, public_id, lock=False):
    rows = _memories(ctx).select_related('source_task')
    row = (rows.select_for_update(of=('self',)) if lock else rows).filter(public_id=public_id).first()
    if row is None:
        raise ValueError('NOT_FOUND')
    return row


def update_memory(request, ctx, public_id):
    content = json_body(request, ('content',))['content']
    if not _plain(content) or not 1 <= len(content.strip()) <= 300:
        raise ValueError('VALIDATION_FAILED')
    with transaction.atomic():
        row = _memory(ctx, public_id, lock=True)
        row.content, row.edited_at = content.strip(), clock.now()
        row.save(update_fields=['content', 'edited_at', 'updated_at'])
    return memory_item(row)


def delete_memory(request, ctx, public_id):
    with transaction.atomic():
        _memory(ctx, public_id, lock=True).delete()
    return HttpResponse(status=204)


# ---------------------------------------------------------------- authorization grants (C-D-18)


def _grants(ctx):
    return AuthorizationGrant.objects.filter(account=ctx.account, user=ctx.user, revoked_at__isnull=True)


def grant_item(row):
    return {'id': row.public_id, 'extension': {'id': row.extension.public_id, 'name': row.extension.name},
            'action_kind': row.action_kind, 'scope': row.scope, 'scope_text': row.source_request.scope_text,
            'task': {'id': row.task.public_id, 'title': row.task.title} if row.task_id else None,
            'workspace': {'id': row.workspace.public_id, 'name': row.workspace.name} if row.workspace_id else None,
            'created_at': iso(row.created_at)}


def grant_list(request, ctx):
    query(request, ('offset',))
    rows = _grants(ctx).select_related('extension', 'task', 'workspace', 'source_request').order_by('-created_at', '-id')
    return page(request, rows, grant_item)


def revoke_grant(request, ctx, public_id):
    with transaction.atomic():
        row = _grants(ctx).select_for_update().filter(public_id=public_id).first()
        if row is None:
            raise ValueError('NOT_FOUND')
        row.revoked_at = clock.now()
        row.save(update_fields=['revoked_at', 'updated_at'])
        AuditEvent.objects.create(actor=ctx.user, account=ctx.account, action='grant.revoke',
                                  request_id=request.request_id, target_type='authorization_grant',
                                  target_id=row.public_id)
    return {'id': row.public_id, 'revoked_at': iso(row.revoked_at)}
