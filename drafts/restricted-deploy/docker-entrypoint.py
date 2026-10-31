"""Load only explicitly mounted secrets; supplier credentials belong to app alone."""
import json
import os
import stat
import sys
from pathlib import Path

for name, filename in (('SAGE_SECRET_KEY', 'django_key'), ('SAGE_DB_PASSWORD', 'db_password')):
    value = Path('/run/secrets', filename).read_bytes().decode('utf-8').rstrip('\r\n')
    if not value:
        raise RuntimeError(f'{filename} must not be empty')
    os.environ[name] = value

auth_keys = {
    'SAGE_SES_SECRET_ID', 'SAGE_SES_SECRET_KEY',
    'SAGE_CAPTCHA_SECRET_ID', 'SAGE_CAPTCHA_SECRET_KEY', 'SAGE_CAPTCHA_APP_SECRET_KEY',
    'SAGE_GOOGLE_CLIENT_SECRET',
}
auth_path = Path('/run/secrets/auth_credentials.json')
credentials = {}
try:
    # No mount is needed for the original, fully closed deployment or migrations.
    if auth_path.exists():
        metadata = auth_path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077 or metadata.st_size > 16384):
            raise ValueError
        credentials = json.loads(auth_path.read_text(encoding='utf-8'))
        if not isinstance(credentials, dict) or set(credentials) - auth_keys:
            raise ValueError
        if any(not isinstance(value, str) or not value.strip() or len(value) > 4096
               or any(ord(char) < 32 or ord(char) == 127 for char in value)
               for value in credentials.values()):
            raise ValueError
        os.environ.update(credentials)
    for gate, required in (
            ('SAGE_AUTH_EMAIL_ENABLED', ('SAGE_SES_SECRET_ID', 'SAGE_SES_SECRET_KEY')),
            ('SAGE_AUTH_CAPTCHA_ENABLED', ('SAGE_CAPTCHA_SECRET_ID', 'SAGE_CAPTCHA_SECRET_KEY',
                                          'SAGE_CAPTCHA_APP_SECRET_KEY')),
            ('SAGE_AUTH_GOOGLE_ENABLED', ('SAGE_GOOGLE_CLIENT_SECRET',))):
        if os.environ.get(gate) == '1' and (
                any(not credentials.get(key) for key in required)):
            raise ValueError
except (OSError, UnicodeError, ValueError, TypeError):
    # Parser errors can contain credential text: never print the original exception.
    sys.exit('Authentication credential file is invalid, unreadable or incomplete.')
if len(sys.argv) < 2:
    raise RuntimeError('An application command is required')
os.execvp(sys.argv[1], sys.argv[1:])
