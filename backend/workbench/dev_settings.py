"""Settings for the app workbench development environment.

Same as backend.settings but on a separate SQLite file, so app seed data never mixes with
the existing local database (tmp/sage-local.sqlite3). Use with SAGE_ENV=development only:
DJANGO_SETTINGS_MODULE=backend.workbench.dev_settings
"""
from django.core.exceptions import ImproperlyConfigured

from backend.settings import *  # noqa: F401,F403
from backend.settings import BASE_DIR, ENVIRONMENT

if ENVIRONMENT != 'development':
    raise ImproperlyConfigured('backend.workbench.dev_settings requires SAGE_ENV=development')

import os

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': os.environ.get(
    'SAGE_APP_DEV_DB', str(BASE_DIR / 'tmp' / 'sage-app-dev.sqlite3')),
    # Parallel UI renders hit the threaded dev server; wait for SQLite's write lock instead of failing at once.
    'OPTIONS': {'timeout': 20}}}

# Print outbound mail (e.g. login codes) to the runserver log so the app e2e
# environment can complete the email-code flow without a real SMTP server.
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Local storage for knowledge-base file uploads (POST /api/app/documents/upload).
KNOWLEDGE_UPLOAD_DIR = os.environ.get('SAGE_KNOWLEDGE_UPLOAD_DIR', str(BASE_DIR / 'tmp' / 'knowledge-uploads'))
KNOWLEDGE_UPLOAD_MAX_BYTES = int(os.environ.get('SAGE_KNOWLEDGE_UPLOAD_MAX_BYTES', str(20 * 1024 * 1024)))
KNOWLEDGE_UPLOAD_EXTENSIONS = ('.pdf', '.docx', '.doc', '.xlsx', '.xls', '.csv', '.pptx', '.txt', '.md')
