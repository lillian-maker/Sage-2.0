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
    return '', False


@sensitive_variables()
def send_login_code(identifier, code):
    return send_code(identifier, code)


@sensitive_variables()
def send_code(identifier, code=None, *, purpose='login'):
    if not phone_ready(identifier,purpose):
        raise OSError('SMS_CHANNEL_UNAVAILABLE')
    if purpose != 'password_changed' and (not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code)):
        raise ValueError('INVALID_CODE')
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
        'SignName': settings.SMS_SIGN, 'TemplateId': _template(purpose)[0],
        'TemplateParamSet': [] if purpose == 'password_changed' else [code, str(settings.AUTH_CODE_TTL_SECONDS // 60)]}))
    try:
        result = client.SendSms(request)
    except (TencentCloudSDKException,OSError):
        # SDK bodies can contain phone numbers, OTPs, credentials and rejected text.
        raise OSError('SMS_DELIVERY_FAILED') from None
    statuses = getattr(result,'SendStatusSet',None)
    if not isinstance(statuses,(list,tuple)) or len(statuses) != 1:
        return 0
    item = statuses[0]
    serial=getattr(item,'SerialNo',None)
    return int(getattr(item,'Code',None) == 'Ok' and isinstance(serial,str) and bool(serial) and
        getattr(item,'PhoneNumber',None) == identifier)
