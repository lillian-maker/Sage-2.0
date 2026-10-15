"""Only the two secrets explicitly granted to this process enter its environment."""
import os
import sys
from pathlib import Path

for name, filename in (('SAGE_SECRET_KEY', 'django_key'), ('SAGE_DB_PASSWORD', 'db_password')):
    value = Path('/run/secrets', filename).read_bytes().decode('utf-8').rstrip('\r\n')
    if not value:
        raise RuntimeError(f'{filename} must not be empty')
    os.environ[name] = value
if len(sys.argv) < 2:
    raise RuntimeError('An application command is required')
os.execvp(sys.argv[1], sys.argv[1:])
