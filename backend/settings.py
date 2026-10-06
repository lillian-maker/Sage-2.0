"""No production defaults for secrets, database credentials or commercial gates."""
import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[1]
ENVIRONMENT = os.environ.get('SAGE_ENV', 'production')
if ENVIRONMENT not in ('production', 'development', 'test'):
    raise ImproperlyConfigured('Invalid SAGE_ENV')
LOCAL = ENVIRONMENT != 'production'
SECRET_KEY = os.environ.get('SAGE_SECRET_KEY', '')
if not SECRET_KEY:
    if not LOCAL:
        raise ImproperlyConfigured('SAGE_SECRET_KEY is required')
    SECRET_KEY = secrets.token_urlsafe(64)
if len(SECRET_KEY) < 50:
    raise ImproperlyConfigured('SAGE_SECRET_KEY must contain at least 50 characters')
DEBUG = False
ALLOWED_HOSTS = [host.strip() for host in os.environ.get(
    'SAGE_ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver' if LOCAL else '').split(',') if host.strip()]
if not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS:
    raise ImproperlyConfigured('Explicit SAGE_ALLOWED_HOSTS are required')

INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes',
                  'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
                  'django.contrib.admin', 'backend.apps.BackendConfig',
                  'backend.commerce.apps.CommerceConfig']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware',
              'backend.http.RequestContextMiddleware',
              'django.contrib.sessions.middleware.SessionMiddleware',
              'django.middleware.common.CommonMiddleware',
              'django.middleware.csrf.CsrfViewMiddleware',
              'django.contrib.auth.middleware.AuthenticationMiddleware',
              'django.contrib.messages.middleware.MessageMiddleware']
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'APP_DIRS': True,
              'OPTIONS': {'context_processors': [
                  'django.template.context_processors.request',
                  'django.contrib.auth.context_processors.auth',
                  'django.contrib.messages.context_processors.messages']}}]
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'tmp' / 'admin-static'
MEDIA_ROOT = Path(os.environ.get('SAGE_MEDIA_ROOT', str(BASE_DIR / 'tmp' / 'avatars')))
ROOT_URLCONF = 'backend.urls'
WSGI_APPLICATION = 'backend.wsgi.application'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True
TIME_ZONE = 'Asia/Shanghai'
LANGUAGE_CODE = 'zh-hans'
APPEND_SLASH = False
DATABASES = {'default': {}}
if os.environ.get('SAGE_DB_NAME'):
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.postgresql', 'NAME': os.environ['SAGE_DB_NAME'],
        'USER': os.environ.get('SAGE_DB_USER', ''), 'PASSWORD': os.environ.get('SAGE_DB_PASSWORD', ''),
        'HOST': os.environ.get('SAGE_DB_HOST', '127.0.0.1'),
        'PORT': os.environ.get('SAGE_DB_PORT', '5432'), 'CONN_MAX_AGE': 0,
        'OPTIONS': {'connect_timeout': 5},
    }
elif LOCAL:
    DATABASES['default'] = {'ENGINE': 'django.db.backends.sqlite3',
                            'NAME': BASE_DIR / 'tmp' / 'sage-local.sqlite3'}
else:
    raise ImproperlyConfigured('Production requires PostgreSQL configuration')

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not LOCAL
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 60 * 60 * 24 * 7
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = not LOCAL
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_FAILURE_VIEW = 'backend.http.csrf_failure'
SECURE_SSL_REDIRECT = not LOCAL
SECURE_HSTS_SECONDS = 31536000 if not LOCAL else 0
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
DATA_UPLOAD_MAX_MEMORY_SIZE = 8192
AUTH_PASSWORD_VALIDATORS = []  # No password sign-in or password creation endpoint.

# A missing delivery configuration must never fall back to a displayed/logged OTP.
AUTH_EMAIL_ENABLED = os.environ.get('SAGE_AUTH_EMAIL_ENABLED') == '1'
AUTH_EMAIL_TRANSPORT = os.environ.get('SAGE_AUTH_EMAIL_TRANSPORT', 'smtp')
SES_SECRET_ID = os.environ.get('SAGE_SES_SECRET_ID', '')
SES_SECRET_KEY = os.environ.get('SAGE_SES_SECRET_KEY', '')
SES_REGION = os.environ.get('SAGE_SES_REGION', '')
SES_TEMPLATE_ID = os.environ.get('SAGE_SES_TEMPLATE_ID', '')
AUTH_PHONE_ENABLED = os.environ.get('SAGE_AUTH_PHONE_ENABLED') == '1'
SMS_TEMPLATE_APPROVED = os.environ.get('SAGE_SMS_TEMPLATE_APPROVED') == '1'
SMS_ALLOWED_COUNTRY_CODES = tuple(filter(None, os.environ.get('SAGE_SMS_ALLOWED_COUNTRY_CODES', '').split(',')))
SMS_SECRET_ID = os.environ.get('SAGE_SMS_SECRET_ID', '')
SMS_SECRET_KEY = os.environ.get('SAGE_SMS_SECRET_KEY', '')
SMS_REGION = os.environ.get('SAGE_SMS_REGION', '')
SMS_SDK_APP_ID = os.environ.get('SAGE_SMS_SDK_APP_ID', '')
SMS_SIGN = os.environ.get('SAGE_SMS_SIGN', '')
SMS_TEMPLATE_ID = os.environ.get('SAGE_SMS_TEMPLATE_ID', '')
# Only exact configured proxy peers may supply a single, overwritten client-IP header.
TRUSTED_PROXY_IPS = tuple(filter(None, os.environ.get('SAGE_TRUSTED_PROXY_IPS', '').split(',')))
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.environ.get('SAGE_EMAIL_HOST', '')
EMAIL_PORT = int(os.environ.get('SAGE_EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.environ.get('SAGE_EMAIL_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('SAGE_EMAIL_PASSWORD', '')
EMAIL_USE_TLS = True
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.environ.get('SAGE_EMAIL_FROM', '')
AUTH_CODE_TTL_SECONDS = 300
AUTH_CODE_MAX_ATTEMPTS = 5
AUTH_RESEND_SECONDS = 60
AUTH_IDENTIFIER_LIMIT = 5
AUTH_IP_LIMIT = 20
AUTH_WINDOW_SECONDS = 900

# The shipping process must separately approve real channel adapters and products.
BILLING_SALES_ENABLED = False
# Independent stop for new renewal dispatches; never gates recorded money or delivery.
BILLING_RENEWAL_CHARGING_ENABLED = False
BILLING_APPROVED_CHANNELS = {}
# D03 quote lifetime awaits approval; no client-supplied expiry or default TTL.
BILLING_QUOTE_TTL_SECONDS = None
# Versioned, reviewed plain-text documents supplied before sales approval.
# Each version: {'approved': True, 'zh': {'title': ..., 'paragraphs': [...]}, 'en': ...}.
BILLING_TERMS_DOCUMENTS = {}
# D10 eligibility must be confirmed before exposing paid Credits purchases.
BILLING_CREDITS_PURCHASE_ENABLED = False
LOGGING = {'version': 1, 'disable_existing_loggers': False,
           'handlers': {'console': {'class': 'logging.StreamHandler'}},
           'loggers': {'sage': {'handlers': ['console'], 'level': 'INFO', 'propagate': False}}}
