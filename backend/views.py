"""Same-origin account APIs. Financial writes stay behind explicit server gates."""
import hashlib
import json
import logging
import mimetypes
import re
import secrets
import smtplib
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model, logout
from django.contrib.auth.password_validation import (MinimumLengthValidator,
    get_default_password_validators, validate_password)
from django.core.exceptions import RequestDataTooBig, ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import DatabaseError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.http import FileResponse, JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_variables, sensitive_post_parameters

from .billing import load_catalog, quote
from .http import error, client_ip
from .models import (AuditEvent, AuthThrottle, BillingAccount, LoginChallenge,
                     LoginIdentity, Membership, PurchaseInquiry)

logger = logging.getLogger('sage')
_DEFAULT_EMAIL_TEMPLATE = object()


def health(request, *, readiness=False):
    """Process/schema probes only: never imply payments or delivery are available."""
    if request.method not in ('GET', 'HEAD'):
        response = JsonResponse({'status': 'method_not_allowed'}, status=405)
        response['Allow'] = 'GET, HEAD'
    else:
        ready = True
        if readiness:
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT 1')
                executor = MigrationExecutor(connection)
                ready = not executor.migration_plan(executor.loader.graph.leaf_nodes())
                if not ready:
                    logger.warning('health_schema_pending request_id=%s', request.request_id)
            except DatabaseError:
                ready = False
                logger.warning('health_database_unavailable request_id=%s', request.request_id)
        response = JsonResponse({'status': ('ready' if readiness else 'alive') if ready else 'not_ready'},
                                status=200 if ready else 503)
    response['Cache-Control'] = 'no-store'
    return response


def digest(value):
    return salted_hmac('sage-account-v1', value, algorithm='sha256').hexdigest()


@sensitive_variables()
def payload(request, fields, optional=()):
    if request.content_type != 'application/json':
        raise ValueError('JSON_REQUIRED')
    try:
        raw = request.body
        if len(raw) > 8192:
            raise ValueError('INVALID_BODY_SIZE')
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RequestDataTooBig, RecursionError) as exc:
        raise ValueError('INVALID_JSON') from exc
    if not isinstance(data, dict) or not set(fields) <= set(data) or set(data)-set(fields)-set(optional):
        raise ValueError('INVALID_REQUEST')
    return data


def email_transport_ready():
    if not settings.AUTH_EMAIL_ENABLED:
        return False
    try:
        validate_email(settings.DEFAULT_FROM_EMAIL)
    except ValidationError:
        return False
    if settings.AUTH_EMAIL_TRANSPORT == 'ses':
        return bool(settings.SES_SECRET_ID and settings.SES_SECRET_KEY and
                    settings.SES_REGION in ('ap-hongkong', 'ap-guangzhou'))
    return bool(settings.AUTH_EMAIL_TRANSPORT == 'smtp' and settings.EMAIL_HOST)


def email_ready(template_id=_DEFAULT_EMAIL_TEMPLATE):
    if not email_transport_ready():
        return False
    if settings.AUTH_EMAIL_TRANSPORT != 'ses':
        return True
    template = str(settings.SES_TEMPLATE_ID if template_id is _DEFAULT_EMAIL_TEMPLATE else template_id)
    try:
        return bool(template.isascii() and template.isdigit() and int(template) > 0)
    except ValueError:
        return False


def normalized_identifier(method, identifier):
    if not isinstance(identifier, str) or len(identifier) > 254:
        raise ValueError('INVALID_IDENTIFIER')
    identifier = identifier.strip()
    if method == 'email':
        try:
            validate_email(identifier)
        except ValidationError as exc:
            raise ValueError('INVALID_IDENTIFIER') from exc
        local, domain = identifier.rsplit('@', 1)
        # Do not collapse aliases or merge unverified phone/email identities.
        return local + '@' + domain.lower()
    if method == 'phone' and re.fullmatch(r'\+[1-9]\d{7,14}', identifier):
        return identifier
    raise ValueError('INVALID_IDENTIFIER')


def password_identifier(data):
    """New website phone flows are mainland-only; legacy normalizer stays unchanged."""
    method = data.get('method','email')
    if method not in ('email','phone'):
        raise ValueError('INVALID_IDENTIFIER')
    identifier = data['identifier']
    if method == 'phone':
        if not isinstance(identifier,str) or not re.fullmatch(r'\+861[0-9]{10}',identifier):
            raise ValueError('INVALID_IDENTIFIER')
        return method,identifier
    return method,normalized_identifier(method,identifier)


def password_reset_ready():
    # Password mail must never accidentally use an approved sign-in template.
    return email_ready(settings.AUTH_PASSWORD_SES_TEMPLATE_ID)


def membership(request, financial=False):
    if not request.user.is_authenticated:
        return None
    item = Membership.objects.select_related('account').filter(
        user=request.user, account_id=request.session.get('active_account_id'), is_active=True).first()
    if financial and item and item.role not in ('owner', 'billing_admin'):
        return None
    return item


def session_data(request):
    from .authentication import captcha_ready, registration_ready, registration_terms_ready, recovery_ready
    from .google_auth import ready as google_ready
    original_email = request.user.is_authenticated and LoginIdentity.objects.filter(user=request.user,method='email').exists()
    original_phone = request.user.is_authenticated and LoginIdentity.objects.filter(user=request.user,method='phone').exists()
    google_managed = request.user.is_authenticated and not original_email and not original_phone and LoginIdentity.objects.filter(user=request.user,method='google').exists()
    data = {'authenticated': request.user.is_authenticated, 'user': None, 'accounts': [],
            'active_account_id': None, 'csrf_token': get_token(request),
            'capabilities': {'email_login': False, 'phone_login': False,
                'password_login': captcha_ready(), 'password_reset': captcha_ready() and password_reset_ready() and (not request.user.is_authenticated or original_email),
                'password_managed_by_google': bool(google_managed),
                'email_registration': registration_ready(), 'captcha': captcha_ready(),
                'phone_registration': registration_ready('phone'),
                'phone_password_login': bool(settings.AUTH_PHONE_ENABLED and captcha_ready()),
                'phone_password_reset': bool(captcha_ready() and recovery_ready('phone') and (not request.user.is_authenticated or original_phone)),
                'google_login': google_ready(),
                'google_registration': google_ready() and registration_terms_ready(),
                'captcha_app_id': str(settings.CAPTCHA_APP_ID) if captcha_ready() else None,
                'registration_terms_version': settings.AUTH_REGISTRATION_TERMS_VERSION if registration_terms_ready() else None,
                'registration_terms_url': settings.AUTH_REGISTRATION_TERMS_URL if registration_terms_ready() else None,
                'registration_privacy_url': settings.AUTH_REGISTRATION_PRIVACY_URL if registration_terms_ready() else None,
                'procurement': True}}
    if not request.session.get('login_binding'):
        request.session['login_binding'] = secrets.token_urlsafe(32)
    if request.user.is_authenticated:
        identity = LoginIdentity.objects.filter(user=request.user).order_by('id').first()
        masked = ''
        if identity:
            if identity.method == 'email':
                local, domain = identity.identifier.rsplit('@', 1)
                masked = local[:1] + '***@' + domain
            elif identity.method == 'google':
                if identity.provider_email:
                    local,domain = identity.provider_email.rsplit('@',1)
                    masked = local[:1]+'***@'+domain
            else:
                masked = identity.identifier[:3] + '****' + identity.identifier[-4:]
        data['user'] = {'id': str(request.user.pk), 'display_name': request.user.first_name,
                        'masked_identifier': masked}
        from .account import avatar_url
        from .models import AccountProfile
        data['user']['avatar_url'] = avatar_url(AccountProfile.objects.filter(user=request.user).first())
        data['accounts'] = [{'id': str(item.account_id), 'name': item.account.name,
                             'kind': item.account.kind, 'role': item.role}
                            for item in Membership.objects.select_related('account').filter(
                                user=request.user, is_active=True).order_by('id')]
        ids = [item['id'] for item in data['accounts']]
        selected = request.session.get('active_account_id')
        if selected not in ids:
            selected = ids[0] if ids else None
            request.session['active_account_id'] = selected
        data['active_account_id'] = selected
    return data


def throttle(key, limit, cooldown=0):
    """Must be called inside atomic: serialize across processes, not local memory."""
    now = timezone.now()
    row, _ = AuthThrottle.objects.get_or_create(key=digest(key), defaults={
        'window_at': now, 'last_at': now-timedelta(days=1), 'count': 0})
    row = AuthThrottle.objects.select_for_update().get(pk=row.pk)
    now = timezone.now()  # Lock waits can outlast another request's timestamp.
    if (now-row.window_at).total_seconds() >= settings.AUTH_WINDOW_SECONDS:
        row.window_at, row.count = now, 0
    if row.count >= limit or (now-row.last_at).total_seconds() < cooldown:
        return False
    row.count += 1
    row.last_at = now
    row.save(update_fields=['count', 'window_at', 'last_at'])
    return True


@sensitive_variables()
def password_challenge(request):
    data = payload(request, ('identifier',), optional=('method',))
    if not request.user.is_authenticated:
        return error(request, 'AUTH_REQUIRED', 403)
    method,identifier = password_identifier(data)
    from .authentication import recovery_ready
    if not recovery_ready(method):
        return error(request, 'CHANNEL_UNAVAILABLE', 503)
    if not LoginIdentity.objects.filter(user=request.user, method=method, identifier=identifier).exists():
        return error(request, 'FORBIDDEN', 403)
    binding = request.session.get('login_binding')
    if not binding:
        return error(request, 'SESSION_REQUIRED', 403, '请刷新页面后再试。')
    code, now, challenge_id = f'{secrets.randbelow(1000000):06d}', timezone.now(), uuid.uuid4()
    with transaction.atomic():
        from .authentication import _binding_lock,_identity_locks
        from .account import _lock_deletion_user,session_is_active
        _binding_lock(request)
        _identity_locks(request,identifier,method=method)
        if not throttle('password-reset-ip:' + client_ip(request), settings.AUTH_IP_LIMIT) or not throttle(
                'identity:'+method+':' + identifier, settings.AUTH_IDENTIFIER_LIMIT, settings.AUTH_RESEND_SECONDS):
            return error(request, 'RATE_LIMITED', 429, '请求较频繁，请稍后重试。')
        identity = LoginIdentity.objects.filter(method=method, identifier=identifier, user__is_active=True).first()
        try:
            user = _lock_deletion_user(request.user)
        except ValueError as exc:
            if str(exc) != 'AUTH_REQUIRED':
                raise
            return error(request,'FORBIDDEN',403)
        if not identity or identity.user_id != user.pk or not session_is_active(request):
            return error(request,'FORBIDDEN',403)
        LoginChallenge.objects.filter(purpose='password_reset', method=method, identifier=identifier,
            session_digest=digest(binding), consumed_at__isnull=True).update(consumed_at=now)
        record = LoginChallenge.objects.create(id=challenge_id, purpose='password_reset', method=method,
            password_user_id=identity.user_id if identity else None, identifier=identifier,
            code_digest=digest(str(challenge_id) + ':' + code), session_digest=digest(binding),
            expires_at=now + timedelta(seconds=settings.AUTH_CODE_TTL_SECONDS))
    try:
        if method == 'phone':
            from .sms import send_code
            sent = send_code(identifier,code,purpose='password_reset')
        elif settings.AUTH_EMAIL_TRANSPORT == 'ses':
            from .email import send_password_code
            sent = send_password_code(identifier, code)
        else:
            # Identical delivery and public response for existing, unknown or disabled accounts.
            sent = send_mail('Sage 密码重置验证码 / Password reset code',
                f'密码重置验证码 / Password reset code: {code}\n5 分钟内有效，请勿分享。\n'
                '如果此邮箱对应可用的 Sage 账号，可在发起页面设置新密码；否则请先注册。\n'
                'Use this code on the requesting page only if this email has an active Sage account.',
                settings.DEFAULT_FROM_EMAIL, [identifier], fail_silently=False)
    except (smtplib.SMTPException, OSError):
        logger.warning('password_delivery_failed request_id=%s', request.request_id)
        return error(request, 'DELIVERY_FAILED', 503, '验证码发送未成功，请稍后重试。')
    if sent != 1:
        return error(request, 'DELIVERY_FAILED', 503, '验证码发送未成功，请稍后重试。')
    with transaction.atomic():
        current,user = _locked_password_challenge(request,record.pk,{
            'identifier':identifier,'method':method,'password_user_id':record.password_user_id},require_delivered=False)
        current.delivered=True
        current.save(update_fields=['delivered'])
        if current.expires_at <= timezone.now():
            raise ValueError('INVALID_CODE')
        remaining=max(0,int((current.expires_at-timezone.now()).total_seconds()))
    return JsonResponse({'challenge_id': str(record.pk), 'expires_in': remaining,
                         'resend_after': settings.AUTH_RESEND_SECONDS}, status=201)


@sensitive_variables()
def validate_new_password(password, user=None):
    """Website credential creation policy; never apply it to historical sign-in."""
    if not isinstance(password, str) or not password:
        raise ValidationError('请输入密码。')
    if not re.fullmatch(r'[\x21-\x7e]+', password):
        if re.search(r'\s', password):
            raise ValidationError('密码不能包含空格、制表符或换行。')
        raise ValidationError('密码仅支持英文字母、数字和英文标点，不支持中文或 emoji。')
    if not 8 <= len(password) <= 16:
        raise ValidationError('密码须为8–16位，不会截断输入。')
    missing = [label for pattern, label in ((r'[A-Z]', '大写字母'), (r'[a-z]', '小写字母'),
        (r'[0-9]', '数字'), (r'[^A-Za-z0-9]', '符号')) if not re.search(pattern, password)]
    if missing:
        raise ValidationError('密码须同时包含大写字母、小写字母、数字和符号；缺少：' + '、'.join(missing) + '。')
    if user is not None:
        # The website's approved 8–16 boundary replaces only the length validator.
        # Keep configured common/user-aware checks and the stronger admin policy.
        validators = [validator for validator in get_default_password_validators()
                      if not isinstance(validator, MinimumLengthValidator)]
        validate_password(password, user, password_validators=validators)


@sensitive_variables()
def _locked_password_challenge(request,challenge_id,hint,*,require_delivered=True):
    from .authentication import _binding_lock,_identity_locks
    from .account import _lock_deletion_user,session_is_active
    from .models import AuthFlow
    binding = _binding_lock(request)
    method=hint['method']
    if method not in ('email','phone'):
        raise ValueError('INVALID_CODE')
    _identity_locks(request,hint['identifier'],method=method)
    target = get_user_model().objects.filter(pk=hint['password_user_id']).first()
    if target is None or target.pk != request.user.pk:
        raise ValueError('INVALID_CODE')
    try:
        user = _lock_deletion_user(target)
    except ValueError:
        raise ValueError('INVALID_CODE') from None
    # The password helper invalidates all identity flows after this OTP check.
    from django.db.models import Q
    list(AuthFlow.objects.select_for_update().filter(Q(user=user)|Q(method=method,identifier=hint['identifier'])).order_by('pk'))
    item = LoginChallenge.objects.select_for_update(of=('self',)).filter(pk=challenge_id,
        purpose='password_reset',authflow__isnull=True).first()
    if (item is None or (require_delivered and not item.delivered) or item.consumed_at or item.expires_at <= timezone.now() or
            item.method != method or item.identifier != hint['identifier'] or
            item.password_user_id != user.pk or not constant_time_compare(item.session_digest,binding) or
            not session_is_active(request) or not LoginIdentity.objects.filter(user=user,
                method=method,identifier=item.identifier).exists()):
        raise ValueError('INVALID_CODE')
    return item,user


@sensitive_variables()
def password_reset(request):
    data = payload(request, ('challenge_id', 'code', 'password'))
    if not request.user.is_authenticated:
        return error(request, 'AUTH_REQUIRED', 403)
    try:
        challenge_id = uuid.UUID(data['challenge_id'])
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError('INVALID_CODE') from exc
    if not isinstance(data['code'], str) or not re.fullmatch(r'\d{6}', data['code']):
        raise ValueError('INVALID_CODE')
    try:
        validate_new_password(data['password'])
    except ValidationError as exc:
        return error(request, 'INVALID_PASSWORD', 422, exc.messages[0])
    hint = LoginChallenge.objects.filter(pk=challenge_id, purpose='password_reset',authflow__isnull=True).values(
        'identifier', 'method', 'password_user_id').first()
    if not hint:
        raise ValueError('INVALID_CODE')
    with transaction.atomic():
        item,user = _locked_password_challenge(request,challenge_id,hint)
        if item.attempts >= settings.AUTH_CODE_MAX_ATTEMPTS:
            raise ValueError('INVALID_CODE')
        item.attempts += 1
        item.save(update_fields=['attempts'])
        if not constant_time_compare(item.code_digest, digest(str(item.id) + ':' + data['code'])):
            return error(request, 'INVALID_CODE', 422, '验证码无效或已过期，请重新获取。')
    # Persist the attempt before re-locking exact state for business work.
    with transaction.atomic():
        item,user = _locked_password_challenge(request,challenge_id,hint)
        if not constant_time_compare(item.code_digest,digest(str(item.pk)+':'+data['code'])):
            raise ValueError('INVALID_CODE')
        # Apply user-aware Django validators only after ownership proof (no enumeration via validator output).
        try:
            from .authentication import change_password
            change_password(request,user,item.identifier,data['password'],expires_at=item.expires_at,method=item.method)
        except ValidationError:
            return error(request, 'INVALID_PASSWORD', 422, '密码过于常见或与个人信息相似，请更换后重试。')
    logout(request)
    return JsonResponse({'status': 'password_reset'})


def inquiry_dict(item):
    return {'id': str(item.id), 'company': item.company, 'seats': item.seats,
            'requirements': item.requirements, 'status': item.status, 'created_at': item.created_at.isoformat()}


@sensitive_variables()
def inquiries(request, member):
    if request.method == 'GET':
        raw_offset = request.GET.get('offset', '0')
        if (set(request.GET) - {'offset'} or len(request.GET.getlist('offset')) > 1 or
                not re.fullmatch(r'\d{1,9}', raw_offset)):
            raise ValueError('INVALID_REQUEST')
        offset = int(raw_offset)
        rows = PurchaseInquiry.objects.filter(account=member.account).order_by('-created_at', '-id')
        total = rows.count()
        return JsonResponse({'account_id': str(member.account_id), 'offset': offset, 'total': total,
            'next_offset': offset+50 if offset+50 < total else None,
            'items': [inquiry_dict(item) for item in rows[offset:offset+50]]})
    data = payload(request, ('company', 'seats', 'requirements', 'contact_email'))
    if (type(data['seats']) is not int or not 1 <= data['seats'] <= 10000 or
            not isinstance(data['company'], str) or not 1 <= len(data['company'].strip()) <= 160 or
            not isinstance(data['requirements'], str) or not 1 <= len(data['requirements'].strip()) <= 2000):
        raise ValueError('INVALID_REQUEST')
    data['company'] = data['company'].strip()
    data['requirements'] = data['requirements'].strip()
    data['contact_email'] = normalized_identifier('email', data['contact_email'])
    request_hash = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        BillingAccount.objects.select_for_update().get(pk=member.account_id)
        # A retry of the exact same form for 10 minutes returns the stored result.
        existing = PurchaseInquiry.objects.filter(account=member.account, request_hash=request_hash,
            created_at__gte=timezone.now()-timedelta(minutes=10)).first()
        if existing:
            return JsonResponse(inquiry_dict(existing))
        if not throttle('procurement:' + str(member.account_id), 10):
            return error(request, 'RATE_LIMITED', 429)
        item = PurchaseInquiry.objects.create(account=member.account, created_by=request.user,
                                               request_hash=request_hash, **data)
        AuditEvent.objects.create(actor=request.user, account=member.account,
                                  action='procurement.received', request_id=request.request_id)
    return JsonResponse(inquiry_dict(item), status=201)


@sensitive_post_parameters()
@sensitive_variables()
def api(request, path):
    # Financial mutations and device revocation share one real session-row
    # lock. Keep it until existing business transactions commit; do not wrap
    # reads, anonymous forms or OTP delivery in this transaction.
    if request.method == 'POST' and (path.startswith('billing/') or
            path == 'account/business-invitations/accept') and request.user.is_authenticated:
        from .account import register_session, _lock_current
        try:
            with transaction.atomic():
                register_session(request)
                _lock_current(request)
                # Offboarding and financial mutations serialize on the same
                # account before checking membership, including procurement.
                selected = request.session.get('active_account_id')
                if selected and path.startswith('billing/') and path != 'billing/accounts/select':
                    # A request can cache its session before a concurrent
                    # selection commits. Never authorize a write from that cache.
                    if request.session.load().get('active_account_id') != selected:
                        return error(request,'ACCOUNT_CHANGED',409,'当前计费账户已改变，请刷新确认后重新操作。')
                    BillingAccount.objects.select_for_update().filter(pk=selected).first()
                result = _api_request(request, path)
            if isinstance(result, dict) and 'payment_dispatch' in result:
                from .payments import finish_payment
                return finish_payment(request, result)
            if isinstance(result, dict) and 'payment_query' in result:
                from .payments import finish_payment_query
                return finish_payment_query(request, result)
            return result
        except DatabaseError:
            logger.error('api_database_unavailable request_id=%s', request.request_id)
            return error(request, 'DATABASE_UNAVAILABLE', 503)
        except ValueError as exc:
            if str(exc) != 'AUTH_REQUIRED':
                raise
            return error(request, 'AUTH_REQUIRED', 401, '请先登录。')
    return _api_request(request, path)


@sensitive_post_parameters()
@sensitive_variables()
def _api_request(request, path):
    routes = {
        'auth/session': ('GET',), 'auth/challenges': ('POST',), 'auth/verify': ('POST',),
        'auth/logout': ('POST',), 'billing/accounts/select': ('POST',),
        'auth/password-login': ('POST',), 'auth/password-challenges': ('POST',), 'auth/password-reset': ('POST',),
        'auth/invitation-check': ('POST',),
        'auth/register/start': ('POST',), 'auth/register/verify': ('POST',),
        'auth/captcha': ('POST',), 'auth/recovery/start': ('POST',),
        'auth/recovery/verify': ('POST',), 'auth/recovery/complete': ('POST',),
        'auth/resend': ('POST',), 'auth/cancel': ('POST',),
        'auth/google/start': ('POST',), 'auth/google/callback': ('GET',),
        'auth/google/resume': ('POST',), 'auth/google/register': ('POST',),
        'auth/google/bind-password': ('POST',), 'auth/google/bind-code/start': ('POST',),
        'auth/google/bind-code/verify': ('POST',), 'auth/google/bind': ('POST',),
        'consultations': ('POST',),
        'account/notifications/summary': ('GET',), 'account/notifications': ('GET',),
        'account/notifications/read': ('POST',), 'account/usage': ('GET',),
        'account/business-invitations': ('GET',), 'account/business-invitations/accept': ('POST',),
        'account/security': ('GET',),
        'account/passkeys/registration-options': ('POST',), 'account/passkeys/register': ('POST',),
        'account/passkeys/authentication-options': ('POST',), 'account/passkeys/verify': ('POST',),
        'account/passkeys/remove': ('POST',), 'account/sensitive-challenges': ('POST',),
        'account/passkeys/backup-codes': ('POST',), 'account/passkeys/recover': ('POST',),
        'account/sensitive-verify': ('POST',),
        'account/phone/challenges': ('POST',), 'account/phone/verify': ('POST',),
        'account/invoices': ('GET', 'POST'), 'account/invoices/handle': ('POST',),
        'billing/account': ('GET',), 'billing/catalog': ('GET',),
        'billing/orders': ('GET', 'POST'), 'billing/purchase-inquiries': ('GET', 'POST'),
        'billing/subscriptions': ('GET',), 'billing/organization': ('GET',),
        'billing/organization/usage': ('GET',),
        'billing/organization/members/remove': ('POST',),
        'billing/organization/members/role': ('POST',),
        'billing/organization/seats/assignment': ('POST',),
        'billing/organization/invitations': ('GET', 'POST'),
        'billing/organization/invitations/revoke': ('POST',),
        'billing/payment-methods': ('GET',), 'billing/notifications': ('GET',),
        'billing/credits/transactions': ('GET',), 'billing/organization-history': ('GET',),
        'billing/automatic-topup': ('GET',),
        'billing/automatic-topup/disable': ('POST',),
        'trial': ('GET',),
        'billing/my-usage': ('GET',),
        'billing/purchase-quotes': ('POST',),
        'billing/purchase-catalog': ('GET',),
    }
    methods = routes.get(path)
    if path.startswith('billing/orders/'):
        methods = ('POST',) if path.endswith(('/pay', '/payment-status')) else ('GET',)
    if path.startswith('billing/purchase-quotes/'):
        methods = ('GET',)
    if re.fullmatch(r'billing/subscriptions/[^/]+/cancel', path):
        methods = ('POST',)
    if re.fullmatch(r'billing/subscriptions/[^/]+/(change-quotes|changes|refunds|seat-quotes|seat-purchases|seat-reduction-quotes|seat-reductions)', path):
        methods = ('POST',)
    if re.fullmatch(r'billing/subscriptions/[^/]+/(change-options|refund-assessment|change-quotes/[^/]+|seat-quotes/[^/]+|seat-reduction-quotes/[^/]+)', path):
        methods = ('GET',)
    if re.fullmatch(r'billing/refunds/[^/]+', path):
        methods = ('GET',)
    if re.fullmatch(r'billing/orders/[^/]+/refunds', path):
        methods = ('POST',)
    if methods and request.method not in methods:
        result = error(request, 'METHOD_NOT_ALLOWED', 405)
        result['Allow'] = ', '.join(methods)
        return result
    try:
        from .account import dispatch, session_is_active, revoke_current_session
        if request.method == 'POST' and path.startswith('auth/') and request.GET:
            raise ValueError('INVALID_REQUEST')
        if request.user.is_authenticated and not session_is_active(request):
            logout(request)
        if path == 'consultations':
            from .consultations import submit
            return submit(request)
        if path == 'auth/invitation-check':
            from .invitations import check_request
            return check_request(request)
        if path == 'auth/session':
            return JsonResponse(session_data(request))
        if path.startswith('auth/google/') and path in routes:
            from . import google_auth
            return {'auth/google/start':google_auth.start,'auth/google/callback':google_auth.callback,
                'auth/google/resume':google_auth.resume,'auth/google/register':google_auth.register,
                'auth/google/bind-password':google_auth.bind_password,
                'auth/google/bind-code/start':google_auth.bind_code_start,
                'auth/google/bind-code/verify':google_auth.bind_code_verify,
                'auth/google/bind':google_auth.bind}[path](request)
        if path in ('auth/challenges', 'auth/verify'):
            payload(request, ('method', 'identifier') if path == 'auth/challenges' else ('challenge_id', 'code'))
            return error(request, 'AUTH_FLOW_REQUIRED', 403, '请使用注册、密码登录或找回密码流程。')
        from . import authentication
        if path in ('auth/register/start', 'auth/password-login', 'auth/recovery/start'):
            return authentication.start(request, {'auth/register/start':'registration',
                'auth/password-login':'login', 'auth/recovery/start':'recovery'}[path])
        if path in ('auth/register/verify', 'auth/recovery/verify'):
            return authentication.verify(request, 'registration' if path == 'auth/register/verify' else 'recovery')
        if path in ('auth/captcha', 'auth/resend', 'auth/cancel', 'auth/recovery/complete'):
            if request.GET:
                raise ValueError('INVALID_REQUEST')
            return {'auth/captcha':authentication.captcha, 'auth/resend':authentication.resend,
                'auth/cancel':authentication.cancel, 'auth/recovery/complete':authentication.complete}[path](request)
        if path == 'auth/password-challenges':
            return password_challenge(request)
        if path == 'auth/password-reset':
            return password_reset(request)
        if path == 'auth/logout':
            payload(request, ())
            revoke_current_session(request)
            logout(request)
            return JsonResponse({'ok': True})
        if path == 'billing/catalog':
            result = load_catalog()
            result['blockers'] = [code for code in result['blockers'] if code != 'AUTH_NOT_CONNECTED']
            return JsonResponse(result)
        if path.startswith('payments/webhooks/'):
            return error(request, 'CHANNEL_UNAVAILABLE', 503)
        if not request.user.is_authenticated:
            return error(request, 'AUTH_REQUIRED', 401, '请先登录。')
        if path in ('account/business-invitations', 'account/business-invitations/accept'):
            from .commerce import subscriptions
            if request.method == 'GET':
                raw_offset = request.GET.get('offset', '0')
                if (set(request.GET)-{'offset'} or len(request.GET.getlist('offset')) > 1 or
                        not re.fullmatch(r'[0-9]{1,9}', raw_offset)):
                    raise ValueError('INVALID_REQUEST')
                return JsonResponse(subscriptions.business_invitations(recipient=request.user, offset=int(raw_offset)))
            if request.GET:
                raise ValueError('INVALID_REQUEST')
            data = payload(request, ('invitation_id', 'confirm'))
            if data['confirm'] is not True:
                raise ValueError('INVALID_REQUEST')
            return JsonResponse(subscriptions.accept_business_invitation(request.user, data['invitation_id']))
        if path.startswith('account/'):
            try:
                if path.startswith('account/notifications') or path == 'account/usage':
                    from .notifications import dispatch as notification_dispatch
                    return notification_dispatch(request, path)
                result = dispatch(request, path)
            except OSError:
                logger.error('account_storage_unavailable request_id=%s', request.request_id)
                return error(request, 'STORAGE_UNAVAILABLE', 503)
            return result if result is not None else error(request, 'NOT_FOUND', 404)
        if path == 'trial':
            if request.GET:
                raise ValueError('INVALID_REQUEST')
            from .trials import read_trial
            return JsonResponse(read_trial(request.user))
        if path == 'billing/accounts/select':
            data = payload(request, ('account_id',))
            try:
                account_id = uuid.UUID(data['account_id'])
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError('INVALID_REQUEST') from exc
            if not Membership.objects.filter(user=request.user, account_id=account_id, is_active=True).exists():
                return error(request, 'FORBIDDEN', 403, '无权访问此账户。')
            request.session['active_account_id'] = str(account_id)
            return JsonResponse(session_data(request))
        member = membership(request, financial=path != 'billing/my-usage')
        if not member:
            return error(request, 'FORBIDDEN', 403, '此账户的账务信息仅对授权管理员开放。')
        expected_account = request.headers.get('X-Billing-Account')
        if ((request.method not in ('GET', 'HEAD') and not expected_account) or
                (expected_account and expected_account != str(member.account_id))):
            return error(request, 'ACCOUNT_CHANGED', 409, '当前计费账户已改变，请刷新确认后重新操作。')
        if path == 'billing/purchase-inquiries':
            return inquiries(request, member)
        from .commerce import billing as commerce
        from .commerce import subscriptions
        matched_reduction = re.fullmatch(r'billing/subscriptions/([^/]+)/(seat-reduction-quotes|seat-reductions|seat-reduction-quotes/[^/]+)', path)
        if matched_reduction:
            if request.GET:
                raise ValueError('INVALID_REQUEST')
            subscription_id, action = matched_reduction.groups()
            if action.startswith('seat-reduction-quotes/'):
                return JsonResponse(subscriptions.seat_reduction_detail(member.account, request.user, subscription_id, action.split('/')[-1]))
            if not settings.BILLING_SEAT_PURCHASE_ENABLED:
                return error(request, 'SEAT_PURCHASE_UNAVAILABLE', 503)
            if action == 'seat-reduction-quotes':
                data = payload(request, ('seat_ids',))
                return JsonResponse(subscriptions.seat_reduction_quote(member.account, request.user, subscription_id,
                    data['seat_ids'], request_key=request.headers.get('Idempotency-Key', '')), status=201)
            data = payload(request, ('quote_id', 'confirm'))
            if data['confirm'] is not True:
                raise ValueError('INVALID_REQUEST')
            from .account import require_sensitive
            require_sensitive(request, member.account)
            return JsonResponse(subscriptions.confirm_seat_reduction(member.account, request.user, subscription_id,
                data['quote_id'], request_key=request.headers.get('Idempotency-Key', '')), status=201)
        matched_seats = re.fullmatch(r'billing/subscriptions/([^/]+)/(seat-quotes|seat-purchases|seat-quotes/[^/]+)', path)
        if matched_seats:
            if request.GET:
                raise ValueError('INVALID_REQUEST')
            subscription_id, action = matched_seats.groups()
            if action.startswith('seat-quotes/'):
                return JsonResponse(subscriptions.seat_purchase_detail(member.account, request.user,
                    subscription_id, action.split('/')[-1]))
            if not settings.BILLING_SEAT_PURCHASE_ENABLED:
                return error(request, 'SEAT_PURCHASE_UNAVAILABLE', 503)
            if action == 'seat-quotes':
                data = payload(request, ('quantity',), optional=('scope', 'cover_seat_ids'))
                return JsonResponse(subscriptions.seat_purchase_quote(member.account, request.user,
                    subscription_id, request_key=request.headers.get('Idempotency-Key', ''), **data), status=201)
            data = payload(request, ('quote_id', 'agreement_acceptance'))
            from .account import require_sensitive
            require_sensitive(request, member.account)
            return JsonResponse(subscriptions.confirm_seat_purchase(member.account, request.user,
                subscription_id, data['quote_id'], agreement_acceptance=data['agreement_acceptance'],
                request_key=request.headers.get('Idempotency-Key', '')), status=201)
        matched_change = re.fullmatch(r'billing/subscriptions/([^/]+)/(change-options|change-quotes|changes|refund-assessment|refunds|change-quotes/[^/]+)', path)
        if matched_change:
            if request.GET:
                raise ValueError('INVALID_REQUEST')
            subscription_id, action = matched_change.groups()
            if action == 'change-options':
                return JsonResponse(subscriptions.subscription_change_options(member.account, request.user, subscription_id))
            if action == 'change-quotes':
                data = payload(request, ('product_id',))
                return JsonResponse(subscriptions.subscription_change_quote(member.account, request.user,
                    subscription_id, data['product_id'], request_key=request.headers.get('Idempotency-Key', '')), status=201)
            if action.startswith('change-quotes/'):
                return JsonResponse(subscriptions.subscription_change_detail(member.account, request.user,
                    subscription_id, action.split('/')[-1]))
            if action == 'changes':
                data = payload(request, ('quote_id', 'agreement_acceptance'))
                if member.account.kind == 'business':
                    from .account import require_sensitive
                    require_sensitive(request, member.account)
                return JsonResponse(subscriptions.confirm_subscription_change(member.account, request.user,
                    subscription_id, data['quote_id'], agreement_acceptance=data['agreement_acceptance'],
                    request_key=request.headers.get('Idempotency-Key', '')), status=201)
            from .commerce import refunds
            if action == 'refund-assessment':
                return JsonResponse(refunds.service_refund_assessment(member.account, request.user, subscription_id))
            data = payload(request, ('request_key', 'confirm'))
            if data['confirm'] is not True:
                raise ValueError('INVALID_REQUEST')
            if member.account.kind == 'business':
                from .account import require_sensitive
                require_sensitive(request, member.account)
            return JsonResponse(refunds.request_service_refund(member.account, request.user,
                subscription_id, data['request_key']), status=201)
        if re.fullmatch(r'billing/refunds/[^/]+', path):
            if request.GET:
                raise ValueError('INVALID_REQUEST')
            from .commerce.models import ServiceRefund, CreditRefund
            from .commerce.refunds import refund_data
            try:
                refund_id = uuid.UUID(path.split('/')[-1])
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError('NOT_FOUND') from exc
            refund = ServiceRefund.objects.filter(account=member.account, pk=refund_id).first()
            if refund:
                return JsonResponse(refund_data(refund))
            item = commerce._get(CreditRefund.objects, payment__order__account=member.account, pk=refund_id)
            return JsonResponse({'id': str(item.pk), 'status': 'refunded' if item.state == 'refunded' else
                'terminated' if item.state == 'released' else item.review_state,
                'amount_minor': str(item.amount_minor), 'currency': 'CNY'})
        if path in ('billing/organization/invitations', 'billing/organization/invitations/revoke'):
            if request.method == 'GET':
                raw_offset = request.GET.get('offset', '0')
                if (set(request.GET)-{'offset'} or len(request.GET.getlist('offset')) > 1 or
                        not re.fullmatch(r'[0-9]{1,9}', raw_offset)):
                    raise ValueError('INVALID_REQUEST')
                return JsonResponse(subscriptions.business_invitations(member.account, actor=request.user, offset=int(raw_offset)))
            if request.GET:
                raise ValueError('INVALID_REQUEST')
            from .account import require_sensitive
            require_sensitive(request, member.account)
            if path.endswith('/revoke'):
                data = payload(request, ('invitation_id', 'confirm'))
                if data['confirm'] is not True:
                    raise ValueError('INVALID_REQUEST')
                return JsonResponse(subscriptions.revoke_business_invitation(member.account, request.user, data['invitation_id']))
            data = payload(request, ('method', 'identifier', 'role', 'paid', 'request_key'))
            return JsonResponse(subscriptions.create_business_invitation(member.account, request.user, **data), status=201)
        if path in ('billing/organization/members/role', 'billing/organization/seats/assignment'):
            if request.GET:
                raise ValueError('INVALID_REQUEST')
            from .account import require_sensitive
            require_sensitive(request, member.account)
            fields = ('membership_id', 'role', 'expected_version', 'request_key', 'confirm') if path.endswith('/role') else (
                'seat_id', 'membership_id', 'expected_version', 'request_key', 'confirm')
            data = payload(request, fields)
            if data.pop('confirm') is not True:
                raise ValueError('INVALID_REQUEST')
            if path.endswith('/role'):
                result = subscriptions.change_member_role(member.account, request.user, **data)
            else:
                result = {'account_id':str(member.account_id), **subscriptions.assign_seat(member.account, request.user, **data)}
            return JsonResponse(result)
        if path == 'billing/organization/members/remove':
            if request.GET:
                raise ValueError('INVALID_REQUEST')
            data = payload(request, ('membership_id', 'request_key', 'confirm'))
            if data['confirm'] is not True or type(data['membership_id']) is not int or data['membership_id'] < 1:
                raise ValueError('INVALID_REQUEST')
            from .account import require_sensitive
            require_sensitive(request, member.account)
            return JsonResponse(subscriptions.remove_member(member.account,request.user,
                data['membership_id'],request_key=data['request_key'],request_id=request.request_id))
        if path == 'billing/organization/usage':
            raw_member, raw_offset = request.GET.get('membership_id', ''), request.GET.get('offset', '0')
            if (set(request.GET)-{'membership_id', 'offset'} or
                    any(len(request.GET.getlist(key)) > 1 for key in request.GET) or
                    not re.fullmatch(r'[1-9][0-9]{0,9}', raw_member) or
                    not re.fullmatch(r'[0-9]{1,9}', raw_offset)):
                raise ValueError('INVALID_REQUEST')
            return JsonResponse(commerce.shared_task_usage(member.account, request.user,
                membership_id=int(raw_member), offset=int(raw_offset)))
        if path == 'billing/purchase-catalog':
            if request.GET:
                raise ValueError('INVALID_REQUEST')
            return JsonResponse(commerce.purchase_catalog(member.account))
        if path == 'billing/purchase-quotes':
            if request.GET:
                raise ValueError('INVALID_REQUEST')
            data = payload(request, ('product_id', 'seat_quantity', 'channel', 'buyer_region'))
            ttl = settings.BILLING_QUOTE_TTL_SECONDS
            if type(ttl) is not int or ttl <= 0:
                raise ValueError('CONFIG_INCOMPLETE')
            try:
                expires_at = timezone.now() + timedelta(seconds=ttl)
            except OverflowError as exc:
                raise ValueError('CONFIG_INCOMPLETE') from exc
            product = commerce._get(commerce.ProductVersion.objects.all(), pk=data['product_id'])
            if product.kind == 'credits' and not settings.BILLING_CREDITS_PURCHASE_ENABLED:
                raise ValueError('CONFIG_INCOMPLETE')
            commerce.purchase_benefits(product)
            document = commerce.purchase_terms(product.terms_version)
            result = commerce.create_quote(member.account, request.user,
                data['product_id'], data['seat_quantity'], expires_at,
                channel=data['channel'], buyer_region=data['buyer_region'], terms_digest=document['digest'])
            return JsonResponse(commerce.purchase_quote_detail(member.account, result['id']), status=201)
        if path.startswith('billing/purchase-quotes/'):
            if request.GET or path.count('/') != 2:
                raise ValueError('INVALID_REQUEST')
            return JsonResponse(commerce.purchase_quote_detail(member.account, path.split('/')[-1]))
        if path == 'billing/my-usage':
            if request.GET:
                raise ValueError('INVALID_REQUEST')
            return JsonResponse(subscriptions.organization_summary(member.account,viewer=request.user))
        if path == 'billing/automatic-topup':
            return JsonResponse({**subscriptions.automatic_topup_summary(member.account),
                'presets': subscriptions.topup_presets(member.account), 'can_authorize': False})
        if path == 'billing/automatic-topup/disable':
            data = payload(request, ('authorization_id', 'confirm'))
            if request.GET or data['confirm'] is not True or member.account.kind != 'business':
                raise ValueError('INVALID_REQUEST')
            # Stopping future charges needs no new payment consent or SMS delivery.
            # The original in-flight payment and provider revocation still need confirmation.
            subscriptions.request_topup_revocation(member.account, request.user, data['authorization_id'])
            return JsonResponse(subscriptions.automatic_topup_summary(member.account))
        if path == 'billing/payment-methods':
            raw_offset = request.GET.get('offset','0')
            if not re.fullmatch(r'\d{1,9}',raw_offset):
                raise ValueError('INVALID_REQUEST')
            return JsonResponse(subscriptions.payment_methods_summary(member.account,offset=int(raw_offset)))
        if path == 'billing/organization':
            raw_offset = request.GET.get('offset','0')
            if not re.fullmatch(r'\d{1,9}',raw_offset):
                raise ValueError('INVALID_REQUEST')
            result = subscriptions.organization_summary(member.account,offset=int(raw_offset),admin_actor=request.user)
            next_items = (result.get('next_paid_period') or {}).get('uncovered_seat_items', [])
            next_members = dict(Membership.objects.filter(account=member.account,
                pk__in=[item['membership_id'] for item in next_items if item['membership_id']]).values_list('pk', 'user_id'))
            for item in next_items:
                item['user_id'] = next_members.get(item['membership_id'])
            identities = {item.user_id:item for item in LoginIdentity.objects.filter(
                user_id__in=[item['user_id'] for item in result['members']+next_items]).order_by('-id')}
            for item in result['members']+next_items:
                identity = identities.get(item.pop('user_id'))
                item['masked_identifier'] = ''
                if identity:
                    if identity.method == 'email':
                        local,domain = identity.identifier.rsplit('@',1)
                        item['masked_identifier'] = local[:1]+'***@'+domain
                    elif identity.method == 'google':
                        if identity.provider_email:
                            local,domain = identity.provider_email.rsplit('@',1)
                            item['masked_identifier'] = local[:1]+'***@'+domain
                    else:
                        item['masked_identifier'] = identity.identifier[:3]+'****'+identity.identifier[-4:]
            return JsonResponse(result)
        if path == 'billing/subscriptions':
            return JsonResponse({'items': subscriptions.list_subscriptions(member.account)})
        if re.fullmatch(r'billing/subscriptions/[^/]+/cancel', path):
            payload(request, ())
            result = subscriptions.cancel_subscription(member.account, request.user, path.split('/')[2])
            return JsonResponse({'subscription': result})
        if path == 'billing/account':
            result = commerce.account_summary(member.account)
            result['account'] = {'id': str(member.account_id), 'name': member.account.name,
                                 'kind': member.account.kind, 'role': member.role}
            return JsonResponse(result)
        if path in ('billing/credits/transactions','billing/organization-history'):
            raw_offset = request.GET.get('offset', '0')
            if (set(request.GET) - {'offset'} or len(request.GET.getlist('offset')) > 1 or
                    not re.fullmatch(r'[0-9]{1,9}', raw_offset)):
                raise ValueError('INVALID_REQUEST')
            if path == 'billing/organization-history':
                return JsonResponse(subscriptions.organization_history(member.account,offset=int(raw_offset)))
            return JsonResponse(commerce.credit_transactions_page(member.account,int(raw_offset)))
        if path == 'billing/notifications':
            raw_offset = request.GET.get('offset', '0')
            if (set(request.GET) - {'offset'} or len(request.GET.getlist('offset')) > 1 or
                    not re.fullmatch(r'\d{1,9}', raw_offset)):
                raise ValueError('INVALID_REQUEST')
            return JsonResponse(commerce.notifications_page(member.account,int(raw_offset)))
        if path == 'billing/orders':
            if request.method == 'GET':
                raw_offset = request.GET.get('offset', '0')
                if (set(request.GET) - {'offset'} or len(request.GET.getlist('offset')) > 1 or
                        not re.fullmatch(r'\d{1,9}', raw_offset)):
                    raise ValueError('INVALID_REQUEST')
                return JsonResponse(commerce.orders_page(member.account, int(raw_offset)))
            data = payload(request, ('quote_id', 'agreement_acceptance'))
            if request.headers.get('Idempotency-Key', '').startswith(('renewal:', 'recovery:', 'topup:')):
                raise ValueError('INVALID_IDEMPOTENCY_KEY')
            quote_record = commerce._get(commerce.Quote.objects.all(), account=member.account, pk=data['quote_id'])
            if not quote_record.snapshot.get('terms_document_digest'):
                raise ValueError('TERMS_UNAVAILABLE')
            if (quote_record.snapshot.get('kind') == 'credits' and not settings.BILLING_CREDITS_PURCHASE_ENABLED
                    and not commerce.Order.objects.filter(account=member.account, quote=quote_record).exists()
                    and not commerce.Order.objects.filter(account=member.account,
                        idempotency_key=request.headers.get('Idempotency-Key', '')).exists()):
                raise ValueError('CONFIG_INCOMPLETE')
            result = commerce.create_order(member.account, request.user, data,
                                           request.headers.get('Idempotency-Key', ''))
            return JsonResponse(result, status=201)
        if path.startswith('billing/orders/'):
            try:
                assessment = re.fullmatch(r'billing/orders/([^/]+)/refund-assessment',path)
                payment = re.fullmatch(r'billing/orders/([^/]+)/pay',path)
                payment_status = re.fullmatch(r'billing/orders/([^/]+)/payment-status',path)
                wallet = re.fullmatch(r'billing/orders/([^/]+)/wallet-test',path)
                refund_request = re.fullmatch(r'billing/orders/([^/]+)/refunds',path)
                matched = payment or payment_status or assessment or wallet or refund_request
                order_id = uuid.UUID(matched.group(1) if matched else path.split('/')[-1])
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError('NOT_FOUND') from exc
            if payment:
                from .payments import start_payment
                return start_payment(request, order_id)
            if payment_status:
                from .payments import start_payment_query
                return start_payment_query(request, order_id)
            if refund_request:
                data = payload(request, ('request_key', 'confirm'))
                if data['confirm'] is not True:
                    raise ValueError('INVALID_REQUEST')
                if member.account.kind == 'business':
                    from .account import require_sensitive
                    require_sensitive(request, member.account)
                from .commerce.refunds import request_credit_refund
                return JsonResponse(request_credit_refund(member.account, request.user, order_id,
                    data['request_key']), status=201)
            if wallet:
                if request.GET:
                    raise ValueError('INVALID_REQUEST')
                if not expected_account:
                    return error(request, 'ACCOUNT_CHANGED', 409)
                order = commerce.Order.objects.filter(account=member.account, pk=order_id).first()
                if not order:
                    raise ValueError('NOT_FOUND')
                configuration = settings.BILLING_WALLET_TESTS
                if (settings.ENVIRONMENT != 'test' or not isinstance(configuration, dict) or
                        order.status != 'pending_payment' or order.paid_at or
                        order.currency != 'CNY' or order.amount_minor <= 0 or
                        commerce.PaymentAttempt.objects.filter(order=order).exists()):
                    return error(request, 'CHANNEL_UNAVAILABLE', 503)
                tests = []
                for channel in ('google_pay', 'apple_pay'):
                    enabled = configuration.get(channel)
                    if not isinstance(enabled, dict) or enabled.get('enabled') is not True:
                        continue
                    data = {'environment': 'TEST', 'channel': channel, 'currency': 'CNY',
                        'amount_minor': str(order.amount_minor), 'billing_cycle': order.snapshot.get('billing_cycle', ''),
                        'account_id': str(member.account_id), 'order_id': str(order.pk)}
                    country = enabled.get('country_code')
                    if channel == 'apple_pay' and isinstance(country, str) and re.fullmatch(r'[A-Z]{2}', country):
                        data['country_code'] = country
                    tests.append(data)
                return JsonResponse({'wallet_tests': tests}) if tests else error(request, 'CHANNEL_UNAVAILABLE', 503)
            if assessment:
                result = commerce.credit_refund_assessment(member.account,order_id)
                result['can_submit'] = bool(result['status'] == 'assessment_ready' and result.get('reference_minor') and
                    int(result['reference_minor']) > 0)
                return JsonResponse(result)
            result = commerce.order_detail(member.account, order_id)
            from .payments import payment_start_capability, payment_qr
            capability = payment_start_capability(member.account, order_id)
            if capability:
                result['payment_start'] = capability
            qr = payment_qr(member.account, order_id)
            if qr:
                result['payment_qr'] = qr
            return JsonResponse(result)
        if path.startswith('billing/'):
            return error(request, 'FEATURE_UNAVAILABLE', 503, '该功能尚未开放，不会产生扣款或变更。')
        return error(request, 'NOT_FOUND', 404)
    except DatabaseError:
        if (path in ('auth/captcha','auth/register/verify') or path in ('auth/google/register','auth/google/bind')) and request.user.is_authenticated:
            logout(request)
        logger.error('api_database_unavailable request_id=%s', request.request_id)
        return error(request, 'DATABASE_UNAVAILABLE', 503)
    except ValueError as exc:
        if (path in ('auth/captcha','auth/register/verify') or path in ('auth/google/register','auth/google/bind')) and request.user.is_authenticated:
            logout(request)
        code = str(exc)
        if code in ('INVITATION_UNAVAILABLE', 'INVITATION_ALREADY_PENDING', 'MEMBER_ALREADY_EXISTS',
                'NO_AVAILABLE_SEAT', 'SEAT_RESERVED_FOR_INVITATION', 'TASK_SETTLEMENT_REQUIRED',
                'SEAT_CHANGED', 'MEMBER_ROLE_CHANGED', 'SEAT_NOT_RELEASABLE', 'SEAT_COVERAGE_CHANGED',
                'MEMBER_ALREADY_SEATED', 'SEAT_LOCKED_UNTIL_NEXT_SERVICE_MONTH', 'NEXT_PAID_PERIOD_REQUIRED'):
            return error(request, code, 409)
        if code == 'SUBSCRIPTION_EXPIRED':
            return error(request, code, 422)
        if code == 'SENSITIVE_CONFIRMATION_REQUIRED':
            return error(request, code, 403, '请先完成身份确认，再重新操作。')
        if code == 'OWNER_ROLE_PROTECTED':
            return error(request, code, 403)
        if code in ('QUOTE_EXPIRED', 'SOURCE_SUBSCRIPTION_CHANGED', 'REFUND_ALREADY_REQUESTED',
                'SUBSCRIPTION_ALREADY_CHANGED', 'PAYMENT_RECONCILIATION_REQUIRED', 'UPGRADE_NOT_AVAILABLE',
                'REFUND_PENDING', 'REFUND_REVIEW_REQUIRED'):
            return error(request, code, 409)
        if code == 'CURRENT_PRICE_REQUIRED':
            return error(request, code, 503)
        if code in ('SERVICE_NOT_ACTIVE', 'CREDITS_SUBSCRIPTION_REQUIRED', 'ANNUAL_REFUND_ONLY', 'NO_REFUNDABLE_SERVICE', 'FUTURE_CREDITS_REQUIRE_REVIEW',
                'REFUND_NOT_ELIGIBLE', 'INVALID_IDEMPOTENCY_KEY', 'MINIMUM_SEATS_REQUIRED', 'INVALID_SEAT_VERSION',
                'BUSINESS_PLAN_SWITCH_NOT_SUPPORTED'):
            return error(request, code, 422)
        permitted = {'INVALID_REQUEST', 'INVALID_IDENTIFIER', 'INVALID_CODE', 'INVALID_JSON',
                     'INVALID_BODY_SIZE', 'JSON_REQUIRED', 'CATALOG_INVALID', 'CONFIG_INCOMPLETE',
                     'NOT_FOUND', 'INVALID_IDEMPOTENCY_KEY', 'IDEMPOTENCY_CONFLICT',
                     'QUOTE_EXPIRED', 'QUOTE_NOT_FOUND', 'INVALID_AGREEMENT', 'FORBIDDEN',
                     'INVALID_PAYLOAD', 'TERMS_MISMATCH', 'QUOTE_ALREADY_ORDERED', 'RETENTION_POLICY_PENDING'}
        permitted.update(('INVALID_SEATS', 'TERMS_UNAVAILABLE', 'MEMBER_ALREADY_REMOVED', 'OWNER_REMOVAL_FORBIDDEN'))
        permitted.add('INVALID_PASSWORD')
        permitted.add('INVALID_FLOW')
        permitted.update(('PAYMENT_EXPIRED', 'PAYMENT_INTENT_CONFLICT', 'ORDER_STATE_CONFLICT'))
        if code in ('PAYMENT_EXPIRED', 'PAYMENT_INTENT_CONFLICT', 'ORDER_STATE_CONFLICT'):
            return error(request, code, 409)
        if code in ('GOOGLE_CONFLICT','GOOGLE_REJECTED'):
            return error(request,code,409 if code=='GOOGLE_CONFLICT' else 422)
        if code == 'INVALID_INVITATION_CODE':
            return error(request, code, 422, '无效邀请码，请检查')
        if code == 'INVITATION_CODES_UNAVAILABLE':
            return error(request, code, 503, '邀请码校验服务暂未就绪，请稍后重试。')
        if code not in permitted:
            logger.error('unexpected_validation request_id=%s', request.request_id)
            return error(request, 'REQUEST_FAILED', 500)
        status = 409 if code in ('IDEMPOTENCY_CONFLICT', 'QUOTE_ALREADY_ORDERED', 'MEMBER_ALREADY_REMOVED', 'OWNER_REMOVAL_FORBIDDEN') else 503 if code in ('CONFIG_INCOMPLETE', 'CATALOG_INVALID', 'TERMS_UNAVAILABLE', 'RETENTION_POLICY_PENDING') else (
            404 if code == 'NOT_FOUND' else 403 if code == 'FORBIDDEN' else 422)
        return error(request, code, status)


@csrf_exempt
def preview_quote(request):
    """Stateless public calculation, exempt only here; never creates a quote/order."""
    if request.method != 'POST':
        return error(request, 'METHOD_NOT_ALLOWED', 405)
    origin = request.headers.get('Origin')
    if origin and origin != request.scheme + '://' + request.get_host():
        return error(request, 'INVALID_ORIGIN', 403)
    try:
        result = quote(payload(request, ('plan_code', 'billing_cycle', 'seat_quantity')))
        result['blockers'] = [code for code in result['blockers'] if code != 'AUTH_NOT_CONNECTED']
        return JsonResponse(result)
    except ValueError as exc:
        allowed = {'INVALID_REQUEST', 'INVALID_SEATS', 'PRICE_NOT_CONFIGURED', 'CATALOG_INVALID',
                   'JSON_REQUIRED', 'INVALID_JSON', 'INVALID_BODY_SIZE'}
        code = str(exc) if str(exc) in allowed else 'INVALID_REQUEST'
        return error(request, code, 503 if code == 'CATALOG_INVALID' else 422)


def public_file(request, path=''):
    if not settings.LOCAL or request.method not in ('GET', 'HEAD'):
        return error(request, 'NOT_FOUND', 404)
    relative = path or 'index.html'
    root = (settings.BASE_DIR / 'dist').resolve()
    file = (root / relative).resolve()
    if (root not in file.parents or any(part.startswith('.') for part in file.relative_to(root).parts)
            or not file.is_file()):
        return error(request, 'NOT_FOUND', 404)
    result = FileResponse(file.open('rb'), content_type=mimetypes.guess_type(file.name)[0])
    result['Cache-Control'] = 'no-cache'
    return result
