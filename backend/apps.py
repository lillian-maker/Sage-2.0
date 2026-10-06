from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Error, Warning, register
from django.core.validators import validate_email
from django.core.exceptions import ValidationError


@register(deploy=True)
def deployment_configuration(app_configs, **kwargs):
    errors = []
    if settings.LOCAL:
        errors.append(Error('Deployment requires SAGE_ENV=production.', id='sage.E001'))
    if settings.DATABASES['default']['ENGINE'] != 'django.db.backends.postgresql':
        errors.append(Error('Deployment requires PostgreSQL.', id='sage.E002'))
    if any(host.startswith('.') or '*' in host or '/' in host or ':' in host
           for host in settings.ALLOWED_HOSTS):
        errors.append(Error('Use explicit deployment hostnames without schemes, ports or wildcards.',
                            id='sage.E003'))
    if settings.AUTH_EMAIL_ENABLED:
        try:
            validate_email(settings.DEFAULT_FROM_EMAIL)
            valid_sender = True
        except ValidationError:
            valid_sender = False
        if settings.AUTH_EMAIL_TRANSPORT == 'ses':
            from .views import email_ready
            if not email_ready():
                errors.append(Error('Enabled SES requires region, credentials, template and valid sender.',
                                    id='sage.E004'))
        elif (settings.AUTH_EMAIL_TRANSPORT != 'smtp' or not settings.EMAIL_HOST or not valid_sender or
                settings.EMAIL_BACKEND != 'django.core.mail.backends.smtp.EmailBackend' or
                not settings.EMAIL_USE_TLS or not 1 <= settings.EMAIL_PORT <= 65535):
            errors.append(Error('Enabled email sign-in requires SMTP, STARTTLS and a valid sender.',
                                id='sage.E004'))
    else:
        errors.append(Warning('Email sign-in is disabled; account activation is not ready.', id='sage.W001'))
    if settings.AUTH_PHONE_ENABLED:
        from .sms import phone_ready
        if not phone_ready():
            errors.append(Error('Enabled SMS requires approved template, scope and complete configuration.',
                                id='sage.E005'))
    if not settings.BILLING_SALES_ENABLED:
        errors.append(Warning('Real sales are disabled; this is not a commercial launch approval.', id='sage.W002'))
    return errors


class BackendConfig(AppConfig):
    name = 'backend'
    default_auto_field = 'django.db.models.BigAutoField'
