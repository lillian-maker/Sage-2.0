"""Purpose-bound email/phone registration, CAPTCHA login and original-identity recovery."""
import logging
import re
import secrets
import smtplib
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction
from django.db.models import Q, Value
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.debug import sensitive_variables
from tencentcloud.common.common_client import CommonClient
from tencentcloud.common.credential import Credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile

from .account import _lock_deletion_user, register_session
from .http import client_ip, error
from .invitations import create_registered_user, validate_code
from .models import (AccountProfile, AccountSession, AuditEvent, AuthFlow, AuthThrottle,
    BillingAccount, LoginChallenge, LoginIdentity, Membership)
from .views import (digest, email_ready, normalized_identifier, password_reset_ready,
    payload, password_identifier, session_data, throttle, validate_new_password)

logger = logging.getLogger('sage')


def captcha_ready():
    app_id = str(settings.CAPTCHA_APP_ID)
    try:
        return bool(settings.AUTH_CAPTCHA_ENABLED and settings.CAPTCHA_SECRET_ID and
            settings.CAPTCHA_SECRET_KEY and settings.CAPTCHA_APP_SECRET_KEY and
            app_id.isascii() and app_id.isdigit() and int(app_id) > 0)
    except ValueError:
        return False


def registration_ready(method='email'):
    if method == 'phone':
        from .sms import phone_ready
        return bool(captcha_ready() and registration_terms_ready() and phone_ready(purpose='registration'))
    delivery = email_ready(settings.AUTH_REGISTRATION_SES_TEMPLATE_ID)
    return bool(captcha_ready() and delivery and registration_terms_ready())


def recovery_ready(method='email'):
    if method == 'phone':
        from .sms import phone_ready
        return phone_ready(purpose='password_reset')
    return password_reset_ready()


def registration_terms_ready():
    return bool(settings.AUTH_REGISTRATION_ENABLED and settings.AUTH_REGISTRATION_TERMS_READY and
        isinstance(settings.AUTH_REGISTRATION_TERMS_VERSION, str) and
        1 <= len(settings.AUTH_REGISTRATION_TERMS_VERSION) <= 128 and
        _legal_url(settings.AUTH_REGISTRATION_TERMS_URL) and
        _legal_url(settings.AUTH_REGISTRATION_PRIVACY_URL))


def _legal_url(value):
    if not isinstance(value,str) or not value or len(value)>2048 or any(
            ord(char)<=32 or ord(char)==127 or char=='\\' for char in value):
        return False
    try:
        parsed = urlsplit(value)
        if value.startswith('/') and not value.startswith('//') and not parsed.netloc and not parsed.scheme:
            return True
        if parsed.username or parsed.password:
            return False
        URLValidator(schemes=['https'])(value)
        return True
    except (ValueError,ValidationError):
        return False


@sensitive_variables()
def verify_captcha(ticket, randstr, address):
    http = HttpProfile()
    http.endpoint, http.reqTimeout = 'captcha.tencentcloudapi.com', 10
    profile = ClientProfile(httpProfile=http, retryer=None, disable_region_breaker=True)
    client = CommonClient('captcha', '2019-07-22',
        Credential(settings.CAPTCHA_SECRET_ID, settings.CAPTCHA_SECRET_KEY), '', profile)
    try:
        result = client.call_json('DescribeCaptchaResult', {'CaptchaType': 9,
            'Ticket': ticket, 'Randstr': randstr, 'CaptchaAppId': int(settings.CAPTCHA_APP_ID),
            'AppSecretKey': settings.CAPTCHA_APP_SECRET_KEY, 'UserIp': address})
    except (TencentCloudSDKException, OSError, ValueError):
        raise OSError('CAPTCHA_UNAVAILABLE') from None
    response = result.get('Response') if isinstance(result, dict) else None
    return isinstance(response, dict) and type(response.get('CaptchaCode')) is int and response['CaptchaCode'] == 1


def flow_data(flow):
    return {'flow_id': str(flow.pk), 'purpose': flow.purpose, 'stage': flow.stage,
        'method': flow.method,
        'expires_in': max(0, int((flow.expires_at-timezone.now()).total_seconds())),
        'captcha_app_id': str(settings.CAPTCHA_APP_ID)}


def _binding_lock(request):
    binding = request.session.get('login_binding')
    if not binding:
        raise ValueError('INVALID_FLOW')
    key = digest('flow-session:' + binding)
    now = timezone.now()
    AuthThrottle.objects.get_or_create(key=key, defaults={'window_at': now, 'last_at': now})
    AuthThrottle.objects.select_for_update().get(pk=key)
    return digest(binding)


def _identity_locks(request,identifier,google_sub='',*,method='email'):
    """Every auth flow acquires IP before identity, even when not charging a limit."""
    now = timezone.now()
    keys = ['auth-ip:'+client_ip(request),'identity:'+method+':'+identifier]
    if google_sub:
        keys.append('identity:google:'+google_sub)
    for key in keys:
        hashed = digest(key)
        AuthThrottle.objects.get_or_create(key=hashed,defaults={
            'window_at':now,'last_at':now-timedelta(days=1)})
        AuthThrottle.objects.select_for_update().get(pk=hashed)


def _live(flow):
    if flow.consumed_at or flow.expires_at <= timezone.now():
        raise ValueError('INVALID_FLOW')


def _invalidate(flows, now):
    # Freeze IDs before changing predicates, and always acquire Flow before OTP.
    rows = list(flows.order_by('pk').select_for_update().values_list('pk','challenge_id','purpose'))
    ids = [row[0] for row in rows]
    AuthFlow.objects.filter(pk__in=ids).update(consumed_at=now,password_hash='')
    google_ids = [row[0] for row in rows if row[2]=='google']
    if google_ids:
        AuthFlow.objects.filter(pk__in=google_ids).update(
            google_state_digest=None,google_nonce_digest='',google_claim='',google_sub='',
            first_name='',last_name='',identifier='',original_identity=None,
            return_to='',google_intent='',user=None)
    LoginChallenge.objects.filter(pk__in=[row[1] for row in rows if row[1]],
        consumed_at__isnull=True).update(consumed_at=now)


def _locked_flow(request, value, stage=None, purpose=None):
    binding = _binding_lock(request)
    try:
        flow_id = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise ValueError('INVALID_FLOW') from None
    hint = AuthFlow.objects.filter(pk=flow_id, session_digest=binding).first()
    if not hint:
        raise ValueError('INVALID_FLOW')
    # Same identity -> sessions -> accounts -> user -> flow/challenge lock order
    # as reset/deletion. This also serializes different browsers for one identity.
    _identity_locks(request,hint.identifier,hint.google_sub,method=hint.method)
    user = None
    if hint.user_id:
        target = get_user_model().objects.filter(pk=hint.user_id).first()
        if target:
            try:
                user = _lock_deletion_user(target)
            except ValueError as exc:
                if str(exc) != 'AUTH_REQUIRED':
                    raise
    flow = AuthFlow.objects.select_for_update().filter(pk=flow_id).first()
    if (flow is None or flow.consumed_at or flow.expires_at <= timezone.now() or
            flow.method != hint.method or flow.identifier != hint.identifier or flow.user_id != hint.user_id or
            flow.session_digest != binding or
            (stage and flow.stage != stage) or (purpose and flow.purpose != purpose)):
        raise ValueError('INVALID_FLOW')
    return flow, user


@sensitive_variables()
def start(request, purpose):
    fields = {'registration': ('first_name','last_name','identifier','password',
        'agreement_acceptance','invitation_code'), 'login': ('identifier','password'),
        'recovery': ('identifier',)}[purpose]
    data = payload(request, fields, optional=('method',))
    method,identifier = password_identifier(data)
    if request.GET or request.user.is_authenticated:
        raise ValueError('INVALID_REQUEST')
    if not captcha_ready():
        return error(request, 'CAPTCHA_UNAVAILABLE', 503, '人机验证暂未接通，请稍后再试。')
    if method == 'phone' and not settings.AUTH_PHONE_ENABLED:
        return error(request,'CHANNEL_UNAVAILABLE',503)
    if purpose == 'registration' and not registration_ready(method):
        return error(request, 'REGISTRATION_UNAVAILABLE', 503, '注册服务暂未就绪，请稍后再试。')
    if purpose == 'recovery' and not recovery_ready(method):
        return error(request, 'CHANNEL_UNAVAILABLE', 503)
    fields = {}
    if purpose == 'registration':
        if data['agreement_acceptance'] != settings.AUTH_REGISTRATION_TERMS_VERSION:
            raise ValueError('INVALID_AGREEMENT')
        for name in ('first_name', 'last_name'):
            value = data[name]
            if (not isinstance(value, str) or len(value.strip()) > 150 or
                    any(ord(char) < 32 for char in value) or (name == 'first_name' and not value.strip())):
                raise ValueError('INVALID_REQUEST')
            fields[name] = value.strip()
        validate_code(data['invitation_code'])
        try:
            validate_new_password(data['password'], get_user_model()(email=identifier if method=='email' else '', **fields))
        except ValidationError as exc:
            return error(request, 'INVALID_PASSWORD', 422, exc.messages[0])
        fields.update(agreement_version=data['agreement_acceptance'], agreement_accepted_at=timezone.now())
    elif purpose == 'login' and (not isinstance(data['password'], str) or not 1 <= len(data['password']) <= 128):
        raise ValueError('INVALID_REQUEST')
    with transaction.atomic():
        binding = _binding_lock(request)
        _identity_locks(request,identifier,method=method)
        if not throttle('auth-ip:' + client_ip(request), settings.AUTH_IP_LIMIT):
            return error(request, 'RATE_LIMITED', 429)
        if purpose == 'login' and not throttle('identity:'+method+':' + identifier, settings.AUTH_IDENTIFIER_LIMIT):
            return error(request, 'RATE_LIMITED', 429)
    # Persist the cheap limiter before paying for password hashing or later writes.
    if purpose == 'registration':
        fields['password_hash'] = make_password(data['password'])
    with transaction.atomic():
        binding = _binding_lock(request)
        _identity_locks(request,identifier,method=method)
        identity = LoginIdentity.objects.select_related('user').filter(method=method, identifier=identifier).first()
        user = None
        if identity:
            try:
                user = _lock_deletion_user(identity.user)
            except ValueError as exc:
                if str(exc) != 'AUTH_REQUIRED':
                    raise
        _invalidate(AuthFlow.objects.filter(session_digest=binding, consumed_at__isnull=True), timezone.now())
        if purpose == 'login':
            valid = user.check_password(data['password']) if user else check_password(data['password'], make_password(None))
            if not valid or user is None:
                return error(request,'INVALID_CREDENTIALS',401,'手机号或密码不正确。' if method=='phone' else '邮箱或密码不正确。')
            fields['password_hash'] = user.password
        # Registration response does not reveal whether the address already exists.
        flow = AuthFlow.objects.create(purpose=purpose, method=method, identifier=identifier,
            session_digest=binding, user=user if purpose != 'registration' else None,
            expires_at=timezone.now()+timedelta(seconds=settings.AUTH_CODE_TTL_SECONDS), **fields)
    return JsonResponse(flow_data(flow), status=201)


@sensitive_variables()
def _send_code(request, flow):
    if ((flow.purpose == 'registration' and not registration_ready(flow.method)) or
            (flow.purpose == 'recovery' and not recovery_ready(flow.method))):
        return error(request, 'CHANNEL_UNAVAILABLE', 503)
    _live(flow)
    now, code, challenge_id = timezone.now(), f'{secrets.randbelow(1000000):06d}', uuid.uuid4()
    if flow.challenge_id:
        LoginChallenge.objects.filter(pk=flow.challenge_id).update(consumed_at=now)
    item = LoginChallenge.objects.create(id=challenge_id,
        purpose='registration' if flow.purpose == 'registration' else 'password_reset',
        password_user=flow.user, method=flow.method, identifier=flow.identifier,
        code_digest=digest(str(challenge_id)+':'+code), session_digest=flow.session_digest,
        expires_at=flow.expires_at)
    flow.challenge, flow.stage = item, 'code'
    flow.save(update_fields=['challenge','stage'])
    result = flow_data(flow)
    result.update(challenge_id=str(item.pk), resend_after=settings.AUTH_RESEND_SECONDS)
    if flow.method == 'phone' and flow.purpose == 'recovery' and (not flow.user_id or
            not LoginIdentity.objects.filter(user_id=flow.user_id,method='phone',identifier=flow.identifier,user__is_active=True).exists()):
        # Same public state, but no SMS cost or delivery claim for unknown identities.
        return JsonResponse(result,status=201)
    try:
        if flow.method == 'phone':
            from .sms import send_code
            sent = send_code(flow.identifier,code,purpose='registration' if flow.purpose=='registration' else 'password_reset')
        elif settings.AUTH_EMAIL_TRANSPORT == 'ses':
            from .email import send_registration_code, send_password_code
            sent = (send_registration_code if flow.purpose == 'registration' else send_password_code)(flow.identifier, code)
        else:
            label = '注册验证码 / Registration code' if flow.purpose == 'registration' else '密码重置验证码 / Password reset code'
            sent = send_mail('Sage '+label, 'Sage '+label+': '+code+'\n5 分钟内有效，请勿分享。',
                settings.DEFAULT_FROM_EMAIL, [flow.identifier], fail_silently=False)
    except (smtplib.SMTPException, OSError):
        logger.warning('auth_delivery_failed request_id=%s', request.request_id)
        return error(request, 'DELIVERY_FAILED', 503)
    if sent != 1:
        logger.warning('auth_delivery_failed request_id=%s', request.request_id)
        return error(request, 'DELIVERY_FAILED', 503)
    _live(flow)
    item.delivered = True
    item.save(update_fields=['delivered'])
    _live(flow)
    result.update(flow_data(flow))
    return JsonResponse(result, status=201)


@sensitive_variables()
def captcha(request):
    data = payload(request, ('flow_id','ticket','randstr'))
    if not captcha_ready():
        return error(request, 'CAPTCHA_UNAVAILABLE', 503)
    if any(not isinstance(data[key], str) or not 1 <= len(data[key]) <= 2048 for key in ('ticket','randstr')):
        raise ValueError('INVALID_REQUEST')
    with transaction.atomic():
        flow, user = _locked_flow(request, data['flow_id'], 'captcha')
        if not throttle('auth-ip:'+client_ip(request),settings.AUTH_IP_LIMIT):
            return error(request,'RATE_LIMITED',429)
        if flow.attempts >= settings.AUTH_CODE_MAX_ATTEMPTS:
            return error(request, 'CAPTCHA_REJECTED', 422)
        flow.attempts += 1
        flow.save(update_fields=['attempts'])
        ticket_digest = digest('captcha-ticket:' + data['ticket'])
        if data['ticket'].startswith('trerror'):
            return error(request, 'CAPTCHA_REJECTED', 422)
        # Keep every submitted ticket, including failures/unknown delivery,
        # when a later attempt replaces the flow's current digest.
        _, fresh_ticket = AuthThrottle.objects.get_or_create(key=ticket_digest,
            defaults={'window_at':timezone.now(), 'last_at':timezone.now(), 'count':1})
        if not fresh_ticket:
            return error(request, 'CAPTCHA_REJECTED', 422)
        try:
            with transaction.atomic():
                flow.captcha_digest = ticket_digest
                flow.save(update_fields=['captcha_digest'])
        except IntegrityError:
            return error(request, 'CAPTCHA_REJECTED', 422)
    # The claim is committed before supplier I/O or any account/challenge write.
    unavailable = False
    try:
        valid = verify_captcha(data['ticket'], data['randstr'], client_ip(request))
    except OSError:
        valid, unavailable = False, True
        logger.warning('auth_captcha_unavailable request_id=%s', request.request_id)
    with transaction.atomic():
        flow, user = _locked_flow(request,data['flow_id'],'captcha')
        if not constant_time_compare(flow.captcha_digest or '',ticket_digest):
            raise ValueError('INVALID_FLOW')
        if unavailable:
            return error(request,'CAPTCHA_UNAVAILABLE',503)
        if not valid:
            return error(request, 'CAPTCHA_REJECTED', 422)
        if flow.purpose == 'login':
            if flow.method == 'phone' and not settings.AUTH_PHONE_ENABLED:
                return error(request,'CHANNEL_UNAVAILABLE',503)
            if (user is None or not constant_time_compare(flow.password_hash, user.password) or
                    not LoginIdentity.objects.filter(user=user, method=flow.method, identifier=flow.identifier).exists()):
                return error(request, 'INVALID_CREDENTIALS', 401)
            _live(flow)
            flow.consumed_at, flow.password_hash = timezone.now(), ''
            flow.save(update_fields=['consumed_at','password_hash'])
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            register_session(request)
            AuditEvent.objects.create(actor=user, action='auth.password_verified', request_id=request.request_id)
            if flow.expires_at <= timezone.now():
                raise ValueError('INVALID_FLOW')
            return JsonResponse(session_data(request))
        if not throttle('identity:'+flow.method+':'+flow.identifier,settings.AUTH_IDENTIFIER_LIMIT,settings.AUTH_RESEND_SECONDS):
            return error(request,'RATE_LIMITED',429)
    # Send quota is committed independently of database/delivery outcome.
    with transaction.atomic():
        flow, _ = _locked_flow(request,data['flow_id'],'captcha')
        if not constant_time_compare(flow.captcha_digest or '',ticket_digest):
            raise ValueError('INVALID_FLOW')
        return _send_code(request, flow)


def resend(request):
    data = payload(request, ('flow_id',))
    with transaction.atomic():
        flow, _ = _locked_flow(request, data['flow_id'], 'code')
        if not throttle('auth-ip:'+client_ip(request),settings.AUTH_IP_LIMIT) or not throttle(
                'identity:'+flow.method+':'+flow.identifier,settings.AUTH_IDENTIFIER_LIMIT,settings.AUTH_RESEND_SECONDS):
            return error(request,'RATE_LIMITED',429)
        expected_challenge = flow.challenge_id
    with transaction.atomic():
        flow, _ = _locked_flow(request,data['flow_id'],'code')
        if flow.challenge_id != expected_challenge:
            raise ValueError('INVALID_FLOW')
        return _send_code(request, flow)


def cancel(request):
    data = payload(request, ('flow_id',))
    with transaction.atomic():
        flow, _ = _locked_flow(request, data['flow_id'])
        _invalidate(AuthFlow.objects.filter(pk=flow.pk), timezone.now())
    return JsonResponse({'status':'cancelled'})


@sensitive_variables()
def _locked_challenge(flow,data,purpose):
    item = LoginChallenge.objects.select_for_update().filter(pk=flow.challenge_id).first()
    if (item is None or str(item.pk) != data['challenge_id'] or not item.delivered or
            item.consumed_at or item.expires_at <= timezone.now() or
            item.session_digest != flow.session_digest or item.identifier != flow.identifier or item.method != flow.method or
            item.purpose != ('registration' if purpose == 'registration' else 'password_reset')):
        raise ValueError('INVALID_CODE')
    return item


@sensitive_variables()
def verify(request, purpose):
    fields = ('flow_id','challenge_id','code','invitation_code') if purpose == 'registration' else ('flow_id','challenge_id','code')
    data = payload(request, fields)
    if not isinstance(data['code'], str) or not re.fullmatch(r'[0-9]{6}', data['code']):
        raise ValueError('INVALID_CODE')
    with transaction.atomic():
        flow, user = _locked_flow(request, data['flow_id'], 'code', purpose)
        item = _locked_challenge(flow,data,purpose)
        if item.attempts >= settings.AUTH_CODE_MAX_ATTEMPTS:
            return error(request, 'INVALID_CODE', 422)
        item.attempts += 1
        item.save(update_fields=['attempts'])
        if not constant_time_compare(item.code_digest, digest(str(item.pk)+':'+data['code'])):
            return error(request, 'INVALID_CODE', 422)
    # A later invitation/account/database failure must not undo this attempt.
    with transaction.atomic():
        flow,user = _locked_flow(request,data['flow_id'],'code',purpose)
        item = _locked_challenge(flow,data,purpose)
        if not constant_time_compare(item.code_digest,digest(str(item.pk)+':'+data['code'])):
            raise ValueError('INVALID_CODE')
        now = timezone.now()
        if purpose == 'recovery':
            if user is None or not LoginIdentity.objects.filter(user=user, method=flow.method, identifier=flow.identifier).exists():
                return error(request, 'INVALID_CODE', 422)
            _live(flow)
            item.consumed_at, flow.stage = now, 'password'
            item.save(update_fields=['consumed_at'])
            flow.save(update_fields=['stage'])
            _live(flow)
            return JsonResponse(flow_data(flow))
        if not registration_ready(flow.method) or flow.agreement_version != settings.AUTH_REGISTRATION_TERMS_VERSION:
            return error(request, 'REGISTRATION_UNAVAILABLE', 503)
        if LoginIdentity.objects.filter(method=flow.method, identifier=flow.identifier).exists():
            return error(request, 'REGISTRATION_FAILED', 422, '无法完成注册，请登录或找回密码。')
        # Catch expected invitation stops inside a savepoint: attempts persist,
        # while all new user/account/consent/attribution writes roll back together.
        try:
            with transaction.atomic():
                user = create_registered_user(invitation_code=data['invitation_code'],
                    expires_at=flow.expires_at,username=uuid.uuid4().hex,
                    first_name=flow.first_name, last_name=flow.last_name)
                user.password = flow.password_hash
                user.save(update_fields=['password'])
                LoginIdentity.objects.create(user=user, method=flow.method, identifier=flow.identifier)
                AccountProfile.objects.create(user=user, agreement_version=flow.agreement_version,
                    agreement_accepted_at=flow.agreement_accepted_at)
                account = BillingAccount.objects.create(kind='personal', name='个人账户', personal_owner=user)
                Membership.objects.create(account=account, user=user, role='owner')
                _live(flow)
        except ValueError as exc:
            if str(exc) == 'INVALID_INVITATION_CODE':
                return error(request, str(exc), 422, '无效邀请码，请检查')
            if str(exc) == 'INVITATION_CODES_UNAVAILABLE':
                return error(request, str(exc), 503, '邀请码校验服务暂未就绪，请稍后重试。')
            if str(exc) == 'INVALID_FLOW':
                return error(request,str(exc),422)
            raise
        _live(flow)
        flow.consumed_at, flow.password_hash, item.consumed_at = timezone.now(), '', timezone.now()
        flow.save(update_fields=['consumed_at','password_hash'])
        item.save(update_fields=['consumed_at'])
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        register_session(request)
        AuditEvent.objects.create(actor=user, action='auth.registered', request_id=request.request_id)
        if flow.expires_at <= timezone.now():
            raise ValueError('INVALID_FLOW')
        return JsonResponse(session_data(request))


def _password_notification(identifier, request_id, method='email'):
    if method == 'phone':
        from .sms import phone_ready,send_code
        if not phone_ready(identifier,purpose='password_changed'):
            logger.warning('password_change_notification_unavailable request_id=%s',request_id)
            return
        try:
            if send_code(identifier,purpose='password_changed') != 1:
                logger.warning('password_change_notification_failed request_id=%s',request_id)
        except OSError:
            logger.warning('password_change_notification_failed request_id=%s',request_id)
        return
    template = settings.AUTH_PASSWORD_CHANGED_SES_TEMPLATE_ID
    if not (settings.AUTH_EMAIL_TRANSPORT == 'ses' and password_reset_ready() and
            email_ready(template)):
        logger.warning('password_change_notification_unavailable request_id=%s', request_id)
        return
    try:
        from .email import send_password_changed
        if send_password_changed(identifier) != 1:
            logger.warning('password_change_notification_failed request_id=%s', request_id)
    except OSError:
        logger.warning('password_change_notification_failed request_id=%s', request_id)


@sensitive_variables()
@transaction.atomic
def change_password(request, user, identifier, password, *, expires_at, method='email'):
    """Both password entry points use this single business/deadline boundary."""
    if method == 'email':
        user.email = identifier  # Validation only; LoginIdentity remains authoritative.
    validate_new_password(password, user)
    user.set_password(password)
    if expires_at <= timezone.now():
        raise ValueError('INVALID_FLOW')
    user.save(update_fields=['password'])
    now = timezone.now()
    _invalidate(AuthFlow.objects.filter(Q(user=user)|Q(method=method,identifier=identifier),consumed_at__isnull=True),now)
    LoginChallenge.objects.filter(Q(password_user=user)|Q(method=method,identifier=identifier),consumed_at__isnull=True).update(consumed_at=now)
    keys = list(AccountSession.objects.filter(user=user).values_list('session_key', flat=True))
    # The changed Django auth hash also rejects legacy sessions without metadata.
    AccountSession.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=now)
    Session.objects.filter(pk__in=keys).delete()
    AuditEvent.objects.create(actor=user, action='auth.password_reset', request_id=request.request_id)
    # Includes every DB write and lock wait, not just the expensive hash.
    if expires_at <= timezone.now():
        raise ValueError('INVALID_FLOW')
    transaction.on_commit(lambda: _password_notification(identifier, request.request_id,method))


@sensitive_variables()
def complete(request):
    data = payload(request, ('flow_id','password'))
    try:
        validate_new_password(data['password'])
    except ValidationError as exc:
        return error(request, 'INVALID_PASSWORD', 422, exc.messages[0])
    with transaction.atomic():
        flow, user = _locked_flow(request, data['flow_id'], 'password', 'recovery')
        if user is None or not LoginIdentity.objects.filter(user=user, method=flow.method, identifier=flow.identifier).exists():
            return error(request, 'INVALID_FLOW', 422)
        try:
            change_password(request,user,flow.identifier,data['password'],expires_at=flow.expires_at,method=flow.method)
        except ValidationError:
            return error(request, 'INVALID_PASSWORD', 422, '密码过于常见或与个人信息相似，请更换后重试。')
        _live(flow)
    logout(request)
    return JsonResponse({'status':'password_reset'})


@transaction.atomic
def cleanup_auth_data(*, apply=False):
    """Scrub expired/consumed technical state; never touch users or ticket tombstones."""
    now = timezone.now()
    expired = Q(expires_at__lte=now) | Q(consumed_at__isnull=False)
    all_flows = AuthFlow.objects.filter(expired)
    flows = all_flows.exclude(identifier='',password_hash='',first_name='',last_name='',
        google_state_digest=None,google_nonce_digest='',google_claim='',google_sub='',
        original_identity=None,return_to='',google_intent='')
    challenges = LoginChallenge.objects.filter(expired).exclude(identifier='',code_digest='')
    counts = {'flows':flows.count(),'challenges':challenges.count()}
    if apply:
        # A cleanup scan must not wait on an in-flight grant's pre-held rows.
        # Include already scrubbed Flow IDs so a previously busy OTP is retried.
        flow_ids = list(all_flows.order_by('pk').select_for_update(skip_locked=True).values_list('pk',flat=True))
        flows = flows.filter(pk__in=flow_ids)
        challenges = challenges.filter(Q(authflow__isnull=True)|Q(authflow__pk__in=flow_ids))
        challenge_ids = list(challenges.order_by('pk').select_for_update(skip_locked=True,
            of=('self',)).values_list('pk',flat=True))
        challenges = LoginChallenge.objects.filter(pk__in=challenge_ids)
        counts = {'flows':flows.count(),'challenges':len(challenge_ids)}
        flows.update(password_hash='',first_name='',last_name='',identifier='',user=None,
            agreement_version='',agreement_accepted_at=None,
            google_state_digest=None,google_nonce_digest='',google_claim='',google_sub='',
            original_identity=None,return_to='',google_intent='',
            consumed_at=Coalesce('consumed_at',Value(now)))
        challenges.update(identifier='',code_digest='',password_user=None,
            consumed_at=Coalesce('consumed_at',Value(now)))
    return counts
