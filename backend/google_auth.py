"""Google hosted OIDC code flow; original Sage identities require explicit proof."""
import logging
import re
import secrets
import smtplib
import uuid
from datetime import timedelta
from urllib.parse import unquote, urlencode, urlsplit

import requests
from google.auth import exceptions as google_exceptions
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from django.conf import settings
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.hashers import check_password
from django.core.mail import send_mail
from django.db import DatabaseError, transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.debug import sensitive_variables

from .account import _lock_deletion_user, register_session
from .authentication import (_binding_lock, _identity_locks, _invalidate, _live,
    _locked_flow, _legal_url, registration_terms_ready)
from .http import client_ip, error
from .invitations import create_registered_user
from .models import (AccountProfile, AuditEvent, AuthFlow, BillingAccount,
    LoginChallenge, LoginIdentity, Membership)
from .views import (digest, email_ready, normalized_identifier, payload,
    session_data, throttle)

logger = logging.getLogger('sage')
AUTHORIZE_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
CERTS_URL = 'https://www.googleapis.com/oauth2/v1/certs'
CALLBACK_PATH = '/api/auth/google/callback'


def ready():
    uri = settings.GOOGLE_REDIRECT_URI
    try:
        parsed = urlsplit(uri)
        return bool(settings.AUTH_GOOGLE_ENABLED and settings.GOOGLE_BRAND_READY and
            settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET and
            _legal_url(uri) and parsed.scheme == 'https' and parsed.path == CALLBACK_PATH and
            not parsed.query and not parsed.fragment)
    except (TypeError, ValueError):
        return False


def bind_email_ready():
    return email_ready(settings.AUTH_GOOGLE_BIND_SES_TEMPLATE_ID)


def safe_return(value, language='zh'):
    home = '/index-en.html' if language == 'en' else '/index.html'
    if (not isinstance(value,str) or len(value)>2048 or
            any(ord(c)<=32 or ord(c)==127 or c=='\\' for c in value)):
        return home
    try:
        parsed = urlsplit(value)
        path = unquote(parsed.path)
        allowed = {'/','/index.html','/index-en.html','/pages/product.html'}
        for name in ('billing','checkout','pricing','trial','profile','team','enterprise','docs','download'):
            allowed.update(('/pages/'+name+'.html','/pages/'+name+'-en.html'))
        if parsed.scheme or parsed.netloc or path not in allowed or value.startswith('//'):
            return home
        # Return context cannot transport credentials or contact information.
        if re.search(r'(?:^|&)(?:code|state|password|token|email|identifier|phone)=',
                unquote(parsed.query),re.I):
            return home
        return home if path=='/' else value
    except ValueError:
        return home


@sensitive_variables()
def _cert_request(url, method='GET', **kwargs):
    if url != CERTS_URL or method != 'GET':
        raise ValueError('GOOGLE_REJECTED')
    # verify_oauth2_token's cert fetch otherwise uses Request's 120s default.
    with requests.Session() as session:
        return Request(session=session)(url, method='GET', timeout=10, allow_redirects=False)


@sensitive_variables()
def exchange(code, nonce_digest):
    try:
        response = requests.post(TOKEN_URL, data={'code':code,'client_id':settings.GOOGLE_CLIENT_ID,
            'client_secret':settings.GOOGLE_CLIENT_SECRET,'redirect_uri':settings.GOOGLE_REDIRECT_URI,
            'grant_type':'authorization_code'}, timeout=10, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError('GOOGLE_REJECTED')
        token = response.json().get('id_token')
        if not isinstance(token,str) or not token or len(token)>16384:
            raise ValueError('GOOGLE_REJECTED')
        claims = id_token.verify_oauth2_token(token,_cert_request,settings.GOOGLE_CLIENT_ID)
    except (requests.RequestException,google_exceptions.TransportError):
        raise OSError('GOOGLE_UNAVAILABLE') from None
    except (ValueError,TypeError,KeyError,AttributeError,google_exceptions.GoogleAuthError):
        raise ValueError('GOOGLE_REJECTED') from None
    if (claims.get('iss') not in ('accounts.google.com','https://accounts.google.com') or
            claims.get('aud') != settings.GOOGLE_CLIENT_ID or claims.get('email_verified') is not True or
            not isinstance(claims.get('nonce'),str) or
            not constant_time_compare(digest(claims['nonce']),nonce_digest) or
            not isinstance(claims.get('sub'),str) or
            not re.fullmatch(r'[\x21-\x7e]{1,255}',claims['sub']) or
            type(claims.get('exp')) is not int or claims['exp'] <= timezone.now().timestamp()):
        raise ValueError('GOOGLE_REJECTED')
    try:
        email = normalized_identifier('email',claims.get('email'))
    except ValueError:
        raise ValueError('GOOGLE_REJECTED') from None
    names = {}
    for field,claim in (('first_name','given_name'),('last_name','family_name')):
        value = claims.get(claim,'')
        names[field] = value.strip() if isinstance(value,str) and len(value.strip())<=150 and not any(
            ord(c)<32 or ord(c)==127 for c in value) else ''
    return {'sub':claims['sub'],'email':email,**names}


def _data(flow):
    result = {'flow_id':str(flow.pk),'purpose':'google','stage':flow.stage,
        'expires_in':max(0,int((flow.expires_at-timezone.now()).total_seconds())),
        'email':flow.identifier,'first_name':flow.first_name,'last_name':flow.last_name,
        'intent':flow.google_intent,'bind_email_available':bind_email_ready()}
    if flow.stage == 'bind_code' and flow.challenge_id:
        result.update(challenge_id=str(flow.challenge_id),resend_after=settings.AUTH_RESEND_SECONDS)
    return result


def _pending(request,value,stage=None):
    if request.user.is_authenticated or not ready():
        raise ValueError('INVALID_FLOW')
    return _locked_flow(request,value,stage,'google')


@sensitive_variables()
def start(request):
    data = payload(request,('intent','return_to','language'))
    if request.user.is_authenticated or data['intent'] not in ('login','register') or data['language'] not in ('zh','en'):
        raise ValueError('INVALID_REQUEST')
    if not ready():
        return error(request,'GOOGLE_UNAVAILABLE',503,'Google 认证尚未接通，请选择其他方式。')
    state,nonce = secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    with transaction.atomic():
        binding = _binding_lock(request)
        _identity_locks(request,'')
        if not throttle('auth-ip:'+client_ip(request),settings.AUTH_IP_LIMIT):
            return error(request,'RATE_LIMITED',429)
        _invalidate(AuthFlow.objects.filter(session_digest=binding,consumed_at__isnull=True),timezone.now())
        flow = AuthFlow.objects.create(purpose='google',stage='oauth',identifier='',session_digest=binding,
            google_state_digest=digest(state),google_nonce_digest=digest(nonce),google_intent=data['intent'],
            return_to=safe_return(data['return_to'],data['language']),language=data['language'],
            expires_at=timezone.now()+timedelta(seconds=settings.AUTH_CODE_TTL_SECONDS))
    request.session['google_flow_id'] = str(flow.pk)
    request.session['google_language'] = data['language']
    request.session.pop('google_result',None)
    url = AUTHORIZE_URL+'?'+urlencode({'client_id':settings.GOOGLE_CLIENT_ID,'response_type':'code',
        'scope':'openid email profile','redirect_uri':settings.GOOGLE_REDIRECT_URI,'state':state,
        'nonce':nonce,'prompt':'select_account','hl':'en' if data['language']=='en' else 'zh-CN'})
    return JsonResponse({'authorization_url':url},status=201)


def _redirect(request):
    response = HttpResponse(status=303)
    response['Location'] = '/pages/auth-en.html?google=resume' if request.session.get('google_language')=='en' else '/pages/auth.html?google=resume'
    response['Cache-Control'],response['Referrer-Policy'] = 'no-store','no-referrer'
    return response


def _original(flow,user):
    if user is None or not flow.original_identity_id or not LoginIdentity.objects.filter(
            pk=flow.original_identity_id,user=user,method='email',identifier=flow.identifier).exists():
        raise ValueError('INVALID_FLOW')
    if LoginIdentity.objects.filter(method='google',identifier=flow.google_sub).exists():
        raise ValueError('GOOGLE_CONFLICT')


def _finish(request,flow,user,*,new=False):
    target = safe_return('/index-en.html' if flow.language=='en' else '/index.html',flow.language) if new else safe_return(flow.return_to,flow.language)
    expiry = flow.expires_at
    _live(flow)
    _invalidate(AuthFlow.objects.filter(pk=flow.pk),timezone.now())
    login(request,user,backend='django.contrib.auth.backends.ModelBackend')
    record=register_session(request)
    record.google_verified_at=timezone.now()
    record.save(update_fields=['google_verified_at'])
    AuditEvent.objects.create(actor=user,action='auth.google_registered' if new else 'auth.google_verified',request_id=request.request_id)
    if expiry<=timezone.now():
        raise ValueError('INVALID_FLOW')
    result = {'status':'authenticated','session':session_data(request),'return_to':target}
    if expiry<=timezone.now():
        raise ValueError('INVALID_FLOW')
    # A short-lived read receipt, not reusable proof or another login grant.
    request.session['google_result'] = {'user_id':str(user.pk),'return_to':target,
        'expires_at':expiry.timestamp()}
    return result


@sensitive_variables()
def callback(request):
    """Claim commits before I/O. All callback outcomes use the same clean URL."""
    authenticated_here = False
    try:
        if request.user.is_authenticated or not ready():
            raise ValueError('GOOGLE_UNAVAILABLE')
        query = request.GET
        if any(len(query.getlist(key))>1 for key in ('state','code','error')):
            raise ValueError('GOOGLE_REJECTED')
        state,code,provider_error = query.get('state'),query.get('code'),query.get('error')
        if (not isinstance(state,str) or not 1<=len(state)<=256 or bool(code)==bool(provider_error) or
                code is not None and (not isinstance(code,str) or not 1<=len(code)<=4096)):
            raise ValueError('GOOGLE_REJECTED')
        with transaction.atomic():
            binding = _binding_lock(request)
            _identity_locks(request,'')
            flow = AuthFlow.objects.select_for_update().filter(purpose='google',session_digest=binding,
                google_state_digest=digest(state),stage='oauth').first()
            if flow is None:
                raise ValueError('INVALID_FLOW')
            _live(flow)
            if provider_error:
                _invalidate(AuthFlow.objects.filter(pk=flow.pk),timezone.now())
                request.session['google_result'] = {'error':'GOOGLE_CANCELLED'}
                return _redirect(request)
            flow.stage,flow.google_claim,flow.google_state_digest = 'exchange',secrets.token_hex(32),None
            flow.save(update_fields=['stage','google_claim','google_state_digest'])
            claim,nonce_digest,flow_id = flow.google_claim,flow.google_nonce_digest,flow.pk
        claims = exchange(code,nonce_digest)
        with transaction.atomic():
            binding = _binding_lock(request)
            _identity_locks(request,claims['email'],claims['sub'])
            bound = LoginIdentity.objects.select_related('user').filter(method='google',identifier=claims['sub']).first()
            original = LoginIdentity.objects.select_related('user').filter(method='email',identifier=claims['email']).first() if bound is None else None
            user = _lock_deletion_user((bound or original).user) if bound or original else None
            flow = AuthFlow.objects.select_for_update().get(pk=flow_id)
            _live(flow)
            if flow.session_digest!=binding or flow.stage!='exchange' or not constant_time_compare(flow.google_claim,claim):
                raise ValueError('INVALID_FLOW')
            flow.google_sub,flow.identifier = claims['sub'],claims['email']
            flow.first_name,flow.last_name = claims['first_name'],claims['last_name']
            flow.google_nonce_digest,flow.google_claim = '', ''
            flow.user,flow.original_identity = user,original
            flow.stage = 'bind_proof' if original else 'register'
            flow.save()
            if bound:
                # Keep the latest verified Google email; the sub and any Sage
                # email login/recovery identity remain unchanged.
                if bound.provider_email != claims['email']:
                    bound.provider_email = claims['email']
                    bound.save(update_fields=['provider_email'])
                _finish(request,flow,user)
                authenticated_here = True
            if not bound:
                _live(flow)
        return _redirect(request)
    except (ValueError,OSError,DatabaseError,AuthFlow.DoesNotExist) as exc:
        if authenticated_here or (request.user.is_authenticated and 'flow_id' in locals()):
            logout(request)
        code = str(exc) if str(exc) in ('GOOGLE_UNAVAILABLE','GOOGLE_CANCELLED','INVALID_FLOW') else 'GOOGLE_REJECTED'
        if 'flow_id' in locals():
            try:
                with transaction.atomic():
                    failed,_ = _locked_flow(request,str(flow_id),'exchange','google')
                    if constant_time_compare(failed.google_claim,claim):
                        _invalidate(AuthFlow.objects.filter(pk=flow_id),timezone.now())
            except (ValueError,DatabaseError):
                # A cancelled/expired/removed flow cannot grant authority;
                # busy/database-failed technical cleanup is retried by TTL cleanup.
                logger.warning('google_failed_flow_cleanup_pending request_id=%s',request.request_id)
        request.session['google_result'] = {'error':code}
        logger.warning('google_callback_failed request_id=%s',request.request_id)
        return _redirect(request)


def resume(request):
    payload(request,())
    result = request.session.get('google_result')
    if result:
        if result.get('error'):
            request.session.pop('google_result',None)
            return error(request,result['error'],422 if result['error']!='GOOGLE_UNAVAILABLE' else 503)
        if (not request.user.is_authenticated or result.get('user_id')!=str(request.user.pk) or
                not isinstance(result.get('expires_at'),(float,int)) or result['expires_at']<=timezone.now().timestamp()):
            request.session.pop('google_result',None)
            raise ValueError('INVALID_FLOW')
        return JsonResponse({'status':'authenticated','session':session_data(request),
            'return_to':safe_return(result.get('return_to'),request.session.get('google_language','zh'))})
    with transaction.atomic():
        flow,user = _pending(request,request.session.get('google_flow_id'))
        if flow.stage not in ('register','bind_proof','bind_code','bind_confirm'):
            raise ValueError('INVALID_FLOW')
        if flow.stage!='register':
            _original(flow,user)
        return JsonResponse(_data(flow))


@sensitive_variables()
def register(request):
    data = payload(request,('flow_id','first_name','last_name','agreement_acceptance','invitation_code'))
    if not registration_terms_ready():
        return error(request,'REGISTRATION_UNAVAILABLE',503)
    if data['agreement_acceptance']!=settings.AUTH_REGISTRATION_TERMS_VERSION:
        raise ValueError('INVALID_AGREEMENT')
    names = {}
    for key in ('first_name','last_name'):
        value = data[key]
        if not isinstance(value,str) or len(value.strip())>150 or any(ord(c)<32 or ord(c)==127 for c in value) or key=='first_name' and not value.strip():
            raise ValueError('INVALID_REQUEST')
        names[key] = value.strip()
    with transaction.atomic():
        flow,_ = _pending(request,data['flow_id'],'register')
        if (LoginIdentity.objects.filter(method='google',identifier=flow.google_sub).exists() or
                LoginIdentity.objects.filter(method='email',identifier=flow.identifier).exists()):
            raise ValueError('GOOGLE_CONFLICT')
        user = create_registered_user(invitation_code=data['invitation_code'],expires_at=flow.expires_at,
            username=uuid.uuid4().hex,**names)
        LoginIdentity.objects.create(user=user,method='google',identifier=flow.google_sub,provider_email=flow.identifier)
        AccountProfile.objects.create(user=user,agreement_version=data['agreement_acceptance'],agreement_accepted_at=timezone.now())
        account = BillingAccount.objects.create(kind='personal',name='个人账户',personal_owner=user)
        Membership.objects.create(account=account,user=user,role='owner')
        return JsonResponse(_finish(request,flow,user,new=True))


@sensitive_variables()
def bind_password(request):
    data = payload(request,('flow_id','password'))
    if not isinstance(data['password'],str) or not 1<=len(data['password'])<=128:
        raise ValueError('INVALID_REQUEST')
    with transaction.atomic():
        flow,user = _pending(request,data['flow_id'],'bind_proof')
        _original(flow,user)
        if (flow.attempts>=settings.AUTH_CODE_MAX_ATTEMPTS or
                not throttle('auth-ip:'+client_ip(request),settings.AUTH_IP_LIMIT) or
                not throttle('identity:email:'+flow.identifier,settings.AUTH_IDENTIFIER_LIMIT)):
            return error(request,'RATE_LIMITED',429)
        flow.attempts += 1
        flow.save(update_fields=['attempts'])
        password_hash = user.password
        original = (user.pk,flow.original_identity_id,flow.google_sub,flow.identifier,flow.session_digest)
    # No setter: User.check_password could upgrade and save an unlocked hash.
    # Limiter/attempt writes have already committed before high-cost verification.
    if not check_password(data['password'],password_hash):
        return error(request,'INVALID_CREDENTIALS',401)
    with transaction.atomic():
        flow,user = _pending(request,data['flow_id'],'bind_proof')
        _original(flow,user)
        if ((user.pk,flow.original_identity_id,flow.google_sub,flow.identifier,flow.session_digest)!=original or
                not constant_time_compare(user.password,password_hash)):
            raise ValueError('INVALID_FLOW')
        flow.stage,flow.password_hash = 'bind_confirm',password_hash
        flow.save(update_fields=['stage','password_hash'])
        _live(flow)
        return JsonResponse(_data(flow))


@sensitive_variables()
def bind_code_start(request):
    data = payload(request,('flow_id',))
    if not bind_email_ready():
        return error(request,'CHANNEL_UNAVAILABLE',503)
    with transaction.atomic():
        flow,user = _pending(request,data['flow_id'])
        if flow.stage not in ('bind_proof','bind_code'):
            raise ValueError('INVALID_FLOW')
        _original(flow,user)
        if not throttle('auth-ip:'+client_ip(request),settings.AUTH_IP_LIMIT) or not throttle(
                'identity:email:'+flow.identifier,settings.AUTH_IDENTIFIER_LIMIT,settings.AUTH_RESEND_SECONDS):
            return error(request,'RATE_LIMITED',429)
        previous = flow.challenge_id
    with transaction.atomic():
        flow,user = _pending(request,data['flow_id'])
        _original(flow,user)
        if flow.stage not in ('bind_proof','bind_code') or flow.challenge_id!=previous:
            raise ValueError('INVALID_FLOW')
        now,challenge_id,code = timezone.now(),uuid.uuid4(),f'{secrets.randbelow(1000000):06d}'
        if previous:
            LoginChallenge.objects.filter(pk=previous).update(consumed_at=now)
        item = LoginChallenge.objects.create(id=challenge_id,purpose='google_bind',method='email',
            identifier=flow.identifier,password_user=user,session_digest=flow.session_digest,
            code_digest=digest(str(challenge_id)+':'+code),expires_at=flow.expires_at)
        flow.challenge,flow.stage = item,'bind_code'
        flow.save(update_fields=['challenge','stage'])
        try:
            if settings.AUTH_EMAIL_TRANSPORT=='ses':
                from .email import send_google_bind_code
                sent = send_google_bind_code(flow.identifier,code)
            else:
                sent = send_mail('Sage Google 绑定验证码 / Google connection code',
                    'Sage Google connection code: '+code+'\n5 分钟内有效，请勿分享。',
                    settings.DEFAULT_FROM_EMAIL,[flow.identifier],fail_silently=False)
        except (OSError,smtplib.SMTPException):
            sent = 0
        if sent!=1:
            logger.warning('google_bind_delivery_failed request_id=%s',request.request_id)
            return error(request,'DELIVERY_FAILED',503)
        _live(flow)
        item.delivered = True
        item.save(update_fields=['delivered'])
        _live(flow)
        return JsonResponse(_data(flow),status=201)


def _challenge(flow,data,user):
    item = LoginChallenge.objects.select_for_update().filter(pk=flow.challenge_id).first()
    if (item is None or str(item.pk)!=data['challenge_id'] or item.purpose!='google_bind' or
            item.password_user_id!=user.pk or item.identifier!=flow.identifier or
            item.session_digest!=flow.session_digest or item.consumed_at or not item.delivered or
            item.expires_at<=timezone.now()):
        raise ValueError('INVALID_CODE')
    return item


@sensitive_variables()
def bind_code_verify(request):
    data = payload(request,('flow_id','challenge_id','code'))
    if not isinstance(data['code'],str) or not re.fullmatch(r'[0-9]{6}',data['code']):
        raise ValueError('INVALID_CODE')
    with transaction.atomic():
        flow,user = _pending(request,data['flow_id'],'bind_code')
        _original(flow,user)
        item = _challenge(flow,data,user)
        if item.attempts>=settings.AUTH_CODE_MAX_ATTEMPTS:
            return error(request,'INVALID_CODE',422)
        item.attempts += 1
        item.save(update_fields=['attempts'])
        if not constant_time_compare(item.code_digest,digest(str(item.pk)+':'+data['code'])):
            return error(request,'INVALID_CODE',422)
    with transaction.atomic():
        flow,user = _pending(request,data['flow_id'],'bind_code')
        _original(flow,user)
        item = _challenge(flow,data,user)
        if not constant_time_compare(item.code_digest,digest(str(item.pk)+':'+data['code'])):
            raise ValueError('INVALID_CODE')
        item.consumed_at = timezone.now()
        item.save(update_fields=['consumed_at'])
        flow.stage,flow.password_hash = 'bind_confirm',user.password
        flow.save(update_fields=['stage','password_hash'])
        _live(flow)
        return JsonResponse(_data(flow))


def bind(request):
    data = payload(request,('flow_id','confirm'))
    if data['confirm'] is not True:
        raise ValueError('INVALID_REQUEST')
    with transaction.atomic():
        flow,user = _pending(request,data['flow_id'],'bind_confirm')
        _original(flow,user)
        if not constant_time_compare(flow.password_hash,user.password):
            raise ValueError('INVALID_FLOW')
        LoginIdentity.objects.create(user=user,method='google',identifier=flow.google_sub,provider_email=flow.identifier)
        return JsonResponse(_finish(request,flow,user))
