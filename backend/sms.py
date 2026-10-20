"""Tencent SMS OTP; provider acceptance does not prove handset delivery."""
import json
import re

from django.conf import settings
from django.views.decorators.debug import sensitive_variables


def phone_ready(identifier=None, purpose='login'):
    required = ('SMS_SECRET_ID', 'SMS_SECRET_KEY', 'SMS_REGION', 'SMS_SDK_APP_ID',
                'SMS_SIGN')
    if not (getattr(settings, 'AUTH_PHONE_ENABLED', False) and
            getattr(settings, 'SMS_TEMPLATE_APPROVED', False) and
            all(isinstance(getattr(settings, field, None), str) and getattr(settings, field).strip()
                for field in required)):
        return False
    # SMS product Region values, not API access domains; no cross-region retry.
    if settings.SMS_REGION not in ('ap-beijing','ap-guangzhou','ap-nanjing'):
        return False
    if not re.fullmatch(r'[1-9][0-9]*', settings.SMS_SDK_APP_ID):
        return False
    template, approved = _template(purpose)
    if not approved or not isinstance(template,str) or not re.fullmatch(r'[1-9][0-9]*',template):
        return False
    allowed = getattr(settings, 'SMS_ALLOWED_COUNTRY_CODES', ())
    if not isinstance(allowed, (tuple, list)) or not allowed or not all(
            isinstance(code, str) and re.fullmatch(r'[1-9][0-9]{0,2}', code) for code in allowed):
        return False
    if purpose != 'login' and '86' not in allowed:
        return False
    ttl = settings.AUTH_CODE_TTL_SECONDS
    if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl <= 0 or ttl % 60:
        return False
    if identifier is not None:
        if not isinstance(identifier, str) or not re.fullmatch(r'\+[1-9][0-9]{7,14}', identifier):
            return False
        if purpose != 'login' and not re.fullmatch(r'\+861[0-9]{10}',identifier):
            return False
        return any(identifier.startswith('+' + code) for code in allowed)
    return True


def _template(purpose):
    if purpose == 'login':
        return settings.SMS_TEMPLATE_ID, settings.SMS_TEMPLATE_APPROVED
    if purpose == 'registration':
        return settings.SMS_REGISTRATION_TEMPLATE_ID, settings.SMS_REGISTRATION_TEMPLATE_APPROVED
    if purpose == 'password_reset':
        return settings.SMS_PASSWORD_TEMPLATE_ID, settings.SMS_PASSWORD_TEMPLATE_APPROVED
    if purpose == 'password_changed':
        return settings.SMS_PASSWORD_CHANGED_TEMPLATE_ID, settings.SMS_PASSWORD_CHANGED_TEMPLATE_APPROVED
    if purpose == 'account_deletion':
        return settings.SMS_DELETION_TEMPLATE_ID, settings.SMS_DELETION_TEMPLATE_APPROVED
    if purpose == 'business_sensitive':
        return settings.SMS_BUSINESS_TEMPLATE_ID, settings.SMS_BUSINESS_TEMPLATE_APPROVED
    if purpose == 'phone_binding':
        return getattr(settings,'SMS_BINDING_TEMPLATE_ID',''),getattr(settings,'SMS_BINDING_TEMPLATE_APPROVED',False)
    return '', False


def sms_auth_ready(identifier=None):
    """The approved five-purpose phone product opens as one reviewed surface."""
    return bool(settings.SMS_REAL_DELIVERY_VERIFIED and all(phone_ready(identifier, purpose) for purpose in (
        'registration', 'password_reset', 'password_changed', 'account_deletion', 'business_sensitive')))


@sensitive_variables()
def send_login_code(identifier, code):
    return send_code(identifier, code)


@sensitive_variables()
def send_code(identifier, code=None, *, purpose='login'):
    if not phone_ready(identifier,purpose):
        raise OSError('SMS_CHANNEL_UNAVAILABLE')
    if purpose != 'password_changed' and (not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code)):
        raise ValueError('INVALID_CODE')
    return int(bool(_send(identifier, _template(purpose)[0],
        [] if purpose == 'password_changed' else [code, str(settings.AUTH_CODE_TTL_SECONDS // 60)])))


@sensitive_variables()
def send_notice(identifier, template_id, parameters):
    """Transport only; callers must separately approve each notice purpose/template."""
    # Reuse complete region/app/sign/country/TTL/credential validation without
    # borrowing login-template approval to authorize a notification purpose.
    required = ('SMS_SECRET_ID','SMS_SECRET_KEY','SMS_REGION','SMS_SDK_APP_ID','SMS_SIGN')
    if not (settings.AUTH_PHONE_ENABLED and settings.SMS_REAL_DELIVERY_VERIFIED and
            all(isinstance(getattr(settings,key,None),str) and getattr(settings,key).strip() for key in required) and
            settings.SMS_REGION in ('ap-beijing','ap-guangzhou','ap-nanjing') and
            re.fullmatch(r'[1-9][0-9]*',settings.SMS_SDK_APP_ID) and
            isinstance(identifier,str) and re.fullmatch(r'\+[1-9][0-9]{7,14}',identifier) and
            isinstance(settings.SMS_ALLOWED_COUNTRY_CODES,(tuple,list)) and settings.SMS_ALLOWED_COUNTRY_CODES and
            all(isinstance(c,str) and re.fullmatch(r'[1-9][0-9]{0,2}',c) for c in settings.SMS_ALLOWED_COUNTRY_CODES) and
            any(identifier.startswith('+'+c) for c in settings.SMS_ALLOWED_COUNTRY_CODES)):
        raise OSError('SMS_CHANNEL_UNAVAILABLE')
    if (not isinstance(template_id,str) or not re.fullmatch(r'[1-9][0-9]*',template_id) or
            not isinstance(parameters,list) or len(parameters)>10 or any(
                not isinstance(v,str) or len(v)>100 or any(ord(c)<32 for c in v) or
                re.search(r'https?://|www\.',v,re.I) for v in parameters)):
        raise ValueError('INVALID_NOTICE')
    return _send(identifier,template_id,parameters)


@sensitive_variables()
def _send(identifier, template_id, parameters):
    from tencentcloud.common.credential import Credential
    from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.common.profile.http_profile import HttpProfile
    from tencentcloud.sms.v20210111.models import SendSmsRequest
    from tencentcloud.sms.v20210111.sms_client import SmsClient

    http = HttpProfile()
    http.endpoint = 'sms.tencentcloudapi.com'
    http.reqTimeout = 10
    profile = ClientProfile(httpProfile=http, retryer=None, disable_region_breaker=True)
    client = SmsClient(Credential(settings.SMS_SECRET_ID, settings.SMS_SECRET_KEY),
                       settings.SMS_REGION, profile)
    request = SendSmsRequest()
    request.from_json_string(json.dumps({
        'PhoneNumberSet': [identifier], 'SmsSdkAppId': settings.SMS_SDK_APP_ID,
        'SignName': settings.SMS_SIGN, 'TemplateId': template_id,
        'TemplateParamSet': parameters}))
    try:
        result = client.SendSms(request)
    except (TencentCloudSDKException,OSError):
        # SDK bodies can contain phone numbers, OTPs, credentials and rejected text.
        raise OSError('SMS_DELIVERY_FAILED') from None
    statuses = getattr(result,'SendStatusSet',None)
    if not isinstance(statuses,(list,tuple)) or len(statuses) != 1:
        return ''
    item = statuses[0]
    serial=getattr(item,'SerialNo',None)
    return serial if (getattr(item,'Code',None) == 'Ok' and isinstance(serial,str) and 0<len(serial)<=128 and
        getattr(item,'PhoneNumber',None) == identifier) else ''
