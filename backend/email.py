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
    http = HttpProfile()
    http.endpoint = 'ses.tencentcloudapi.com'
    http.reqTimeout = 10
    profile = ClientProfile(httpProfile=http, retryer=None, disable_region_breaker=True)
    client = SesClient(Credential(settings.SES_SECRET_ID, settings.SES_SECRET_KEY),
                       settings.SES_REGION, profile)
    request = SendEmailRequest()
    request.from_json_string(json.dumps({
        'FromEmailAddress': settings.DEFAULT_FROM_EMAIL, 'Destination': [identifier],
        'Subject': 'Sage 登录验证码 / Sign-in code', 'TriggerType': 1,
        'Template': {'TemplateID': int(settings.SES_TEMPLATE_ID),
                     'TemplateData': json.dumps({'code': code})}}))
    try:
        result = client.SendEmail(request)
    except TencentCloudSDKException:
        # Provider bodies may contain identifiers. Never propagate or log them.
        raise OSError('EMAIL_DELIVERY_FAILED') from None
    return 1 if result.MessageId else 0
