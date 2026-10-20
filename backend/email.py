"""Tencent SES template delivery; acceptance is not proof of mailbox delivery."""
import json

from django.conf import settings
from django.views.decorators.debug import sensitive_variables
from tencentcloud.common.credential import Credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.ses.v20201002.models import SendEmailRequest
from tencentcloud.ses.v20201002.ses_client import SesClient


@sensitive_variables()
def send_login_code(identifier, code):
    return _send_code(identifier, code, settings.SES_TEMPLATE_ID,
                      'Sage 登录验证码 / Sign-in code')


@sensitive_variables()
def send_password_code(identifier, code):
    return _send_code(identifier, code, settings.AUTH_PASSWORD_SES_TEMPLATE_ID,
                      'Sage 密码重置验证码 / Password reset code')


@sensitive_variables()
def send_registration_code(identifier, code):
    return _send_code(identifier, code, settings.AUTH_REGISTRATION_SES_TEMPLATE_ID,
                      'Sage 注册验证码 / Registration code')


@sensitive_variables()
def send_google_bind_code(identifier, code):
    return _send_code(identifier, code, settings.AUTH_GOOGLE_BIND_SES_TEMPLATE_ID,
                      'Sage Google 绑定验证码 / Google connection code')


@sensitive_variables()
def send_password_changed(identifier):
    return _send_code(identifier, None, settings.AUTH_PASSWORD_CHANGED_SES_TEMPLATE_ID,
                      'Sage 密码已变更 / Password changed')


@sensitive_variables()
def send_deletion_code(identifier, code):
    return _send_code(identifier, code, settings.ACCOUNT_DELETION_SES_TEMPLATE_ID,
                      'Sage 账号注销验证码 / Account closure code')


@sensitive_variables()
def _send_code(identifier, code, template_id, subject):
    return int(bool(_send_template(identifier, {} if code is None else {'code': code}, template_id, subject)))


@sensitive_variables()
def send_notice(identifier, template_id, parameters):
    from .views import email_ready
    if settings.AUTH_EMAIL_TRANSPORT != 'ses' or not email_ready(template_id):
        raise OSError('EMAIL_CHANNEL_UNAVAILABLE')
    return _send_template(identifier, parameters, template_id, 'Sage 账户通知 / Account notice')


@sensitive_variables()
def _send_template(identifier, parameters, template_id, subject):
    http = HttpProfile()
    http.endpoint = 'ses.tencentcloudapi.com'
    http.reqTimeout = 10
    profile = ClientProfile(httpProfile=http, retryer=None, disable_region_breaker=True)
    client = SesClient(Credential(settings.SES_SECRET_ID, settings.SES_SECRET_KEY),
                       settings.SES_REGION, profile)
    request = SendEmailRequest()
    request.from_json_string(json.dumps({
        'FromEmailAddress': settings.DEFAULT_FROM_EMAIL, 'Destination': [identifier],
        'Subject': subject, 'TriggerType': 1,
        'Template': {'TemplateID': int(template_id),
                     'TemplateData': json.dumps(parameters)}}))
    try:
        result = client.SendEmail(request)
    except TencentCloudSDKException:
        # Provider bodies may contain identifiers. Never propagate or log them.
        raise OSError('EMAIL_DELIVERY_FAILED') from None
    reference = getattr(result, 'MessageId', None)
    return reference if isinstance(reference, str) and 0 < len(reference) <= 128 else ''
