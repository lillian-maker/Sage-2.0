#!/usr/bin/env python3
"""Synthetic, network-disabled auth configuration checks; never read real secrets."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / 'drafts/restricted-deploy'


def main():
    if len(sys.argv) != 2:
        print('Usage: auth-config-audit.py <existing-local-app-image-id>', file=sys.stderr)
        return 2
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT')
           if key in os.environ}
    with tempfile.TemporaryDirectory(prefix='sage-auth-config-') as directory:
        env.update(SAGE_IMAGE_TAG='synthetic-auth-config', SAGE_DOCKER_SECRETS_DIR=directory,
                   SAGE_FRONTEND_SUBNET='192.0.2.0/24', SAGE_GATEWAY_IP='192.0.2.2',
                   SAGE_APP_IP='192.0.2.3', SAGE_AUTH_EMAIL_ENABLED='1',
                   SAGE_AUTH_GOOGLE_ENABLED='1', SAGE_AUTH_CAPTCHA_ENABLED='1',
                   SAGE_SES_REGION='ap-hongkong', SAGE_CAPTCHA_APP_ID='123',
                   SAGE_AUTH_REGISTRATION_SES_TEMPLATE_ID='31')
        base = ['docker', 'compose', '--env-file', '/dev/null', '--profile', 'tools', '-p', 'sage-auth-config-audit',
                '-f', str(DEPLOY / 'compose.yaml')]
        plain = subprocess.run(base + ['config', '--format', 'json'], env=env,
                               check=True, capture_output=True, text=True)
        combined = subprocess.run(base + ['-f', str(DEPLOY / 'compose.auth.yaml'),
                                         'config', '--format', 'json'], env=env,
                                  check=True, capture_output=True, text=True)
        before, after = json.loads(plain.stdout), json.loads(combined.stdout)
        assert before['networks'] == after['networks']
        for service in ('migrate', 'db', 'web'):
            assert before['services'][service] == after['services'][service]
        app = after['services']['app']
        assert app['networks'] == before['services']['app']['networks']
        assert {item['source'] for item in app['secrets']} == {
            'django_key', 'db_runtime_password', 'auth_credentials'}
        assert next(item['target'] for item in app['secrets']
                    if item['source'] == 'auth_credentials') == 'auth_credentials.json'
        assert after['secrets']['auth_credentials']['file'] == directory + '/auth_credentials.json'
        for key in ('SAGE_AUTH_EMAIL_ENABLED', 'SAGE_AUTH_GOOGLE_ENABLED',
                    'SAGE_AUTH_CAPTCHA_ENABLED', 'SAGE_AUTH_REGISTRATION_ENABLED',
                    'SAGE_AUTH_PHONE_ENABLED', 'SAGE_ACCOUNT_DELETION_ENABLED',
                    'SAGE_AUTH_REGISTRATION_TERMS_READY', 'SAGE_GOOGLE_BRAND_READY'):
            assert app['environment'][key] == '0'
        assert not any('SECRET' in key for key in app['environment'])
        for key in ('SAGE_SES_REGION', 'SAGE_CAPTCHA_APP_ID',
                    'SAGE_AUTH_REGISTRATION_SES_TEMPLATE_ID'):
            assert app['environment'][key] == env[key]

    # All fixture files live in this new container's tmpfs, with actual Linux UID/modes.
    # No image build/pull, DB, host credential mounts, ports or network are needed.
    fixture = r'''
import json, os, pathlib, subprocess, sys
root = pathlib.Path('/run/secrets')
for name in ('django_key', 'db_password'):
    path = root / name
    path.write_text('synthetic-' + name + '-not-a-real-secret-' + 'x' * 50)
    path.chmod(0o400)
    os.chown(path, 10001, 10001)
path = root / 'auth_credentials.json'
keys = ('SAGE_SES_SECRET_ID', 'SAGE_SES_SECRET_KEY', 'SAGE_CAPTCHA_SECRET_ID',
        'SAGE_CAPTCHA_SECRET_KEY', 'SAGE_CAPTCHA_APP_SECRET_KEY', 'SAGE_GOOGLE_CLIENT_SECRET')
values = {key: 'synthetic-value-' + str(index) for index, key in enumerate(keys)}
baseline = {'PATH': os.environ['PATH'], 'PYTHONDONTWRITEBYTECODE': '1'}
checks = []

def identity():
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)

def run(label, content=None, *, mode=0o400, owner=10001, gates=None, success=False, loaded=False):
    if path.exists() or path.is_symlink():
        path.unlink()
    if content is not None:
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        path.chmod(mode)
        os.chown(path, owner, 10001)
    child_env = dict(baseline, **(gates or {}))
    code = ('import os; assert os.getuid()==10001; '
            'assert bool(os.environ.get("SAGE_SES_SECRET_ID")) == ' + repr(loaded) +
            '; print("CONFIG_OK")')
    result = subprocess.run([sys.executable, '/audit-entrypoint.py', sys.executable, '-c', code],
                            env=child_env, preexec_fn=identity, capture_output=True, text=True)
    output = result.stdout + result.stderr
    assert not any(value in output for value in values.values()), label + ': secret echoed'
    assert 'synthetic-' not in output, label + ': fixture echoed'
    if success:
        assert result.returncode == 0 and result.stdout.strip() == 'CONFIG_OK', label
    else:
        assert result.returncode != 0, label + ': accepted invalid file'
        assert result.stdout == '', label + ': process executed'
        assert result.stderr.strip() == 'Authentication credential file is invalid, unreadable or incomplete.', label
    checks.append(label)

run('closed-legacy-without-file', success=True)
run('closed-migration-without-file', gates={'SAGE_DB_USER': 'sage_migrate'}, success=True)
run('valid-file-keeps-closed-gates', json.dumps(values), success=True, loaded=True)
run('enabled-with-complete-file', json.dumps(values), gates={
    'SAGE_AUTH_EMAIL_ENABLED': '1', 'SAGE_AUTH_CAPTCHA_ENABLED': '1',
    'SAGE_AUTH_GOOGLE_ENABLED': '1'}, success=True, loaded=True)
run('email-only-does-not-require-google', json.dumps({key: values[key] for key in keys[:2]}),
    gates={'SAGE_AUTH_EMAIL_ENABLED': '1'}, success=True, loaded=True)
for gate, required in (
        ('SAGE_AUTH_EMAIL_ENABLED', keys[:2]),
        ('SAGE_AUTH_CAPTCHA_ENABLED', keys[2:5]),
        ('SAGE_AUTH_GOOGLE_ENABLED', keys[5:])):
    run(gate + '-missing-file', gates={gate: '1'})
    for key in required:
        partial = {name: value for name, value in values.items() if name != key}
        run(key + '-missing', json.dumps(partial), gates={gate: '1'})
run('world-readable-rejected', json.dumps(values), mode=0o444)
run('group-readable-rejected', json.dumps(values), mode=0o440)
run('wrong-owner-rejected', json.dumps(values), owner=0)
run('unreadable-rejected', json.dumps(values), mode=0)
run('invalid-json-redacted', '{synthetic-invalid-secret')
run('invalid-encoding-redacted', b'\xffsynthetic-invalid-secret')
run('non-object-rejected', '[]')
run('unknown-key-redacted', json.dumps({'synthetic-secret-key': 'synthetic-value'}))
run('empty-value-rejected', json.dumps({keys[0]: ''}))
run('non-string-rejected', json.dumps({keys[0]: 42}))
run('control-character-rejected', json.dumps({keys[0]: 'synthetic-\nsecret'}))
run('oversized-file-rejected', ' ' * 16385)
run('oversized-value-rejected', json.dumps({keys[0]: 'x' * 4097}))
# Exercise the existing Django checker through the changed file loader, with fixtures only.
path.write_text(json.dumps(values))
path.chmod(0o400)
os.chown(path, 10001, 10001)
config = dict(baseline, SAGE_ENV='test', SAGE_ALLOWED_HOSTS='sage.example.invalid',
    SAGE_AUTH_EMAIL_ENABLED='1', SAGE_AUTH_EMAIL_TRANSPORT='ses', SAGE_SES_REGION='ap-hongkong',
    SAGE_EMAIL_FROM='sender@example.invalid', SAGE_CAPTCHA_APP_ID='123',
    SAGE_AUTH_CAPTCHA_ENABLED='1', SAGE_AUTH_REGISTRATION_ENABLED='1',
    SAGE_AUTH_REGISTRATION_TERMS_READY='1', SAGE_AUTH_REGISTRATION_TERMS_VERSION='fixture-v1',
    SAGE_AUTH_REGISTRATION_TERMS_URL='/terms', SAGE_AUTH_REGISTRATION_PRIVACY_URL='/privacy',
    SAGE_AUTH_REGISTRATION_SES_TEMPLATE_ID='31', SAGE_AUTH_PASSWORD_SES_TEMPLATE_ID='32',
    SAGE_AUTH_PASSWORD_CHANGED_SES_TEMPLATE_ID='33', SAGE_AUTH_GOOGLE_BIND_SES_TEMPLATE_ID='34',
    SAGE_AUTH_GOOGLE_ENABLED='1', SAGE_GOOGLE_BRAND_READY='1',
    SAGE_GOOGLE_CLIENT_ID='fixture.apps.googleusercontent.com',
    SAGE_GOOGLE_REDIRECT_URI='https://sage.example.invalid/api/auth/google/callback')
result = subprocess.run([sys.executable, '/audit-entrypoint.py', sys.executable,
                         'manage.py', 'check_auth_readiness', '--strict'],
                        env=config, preexec_fn=identity, capture_output=True, text=True)
assert result.returncode == 0, 'readiness integration failed'
assert not any(value in result.stdout + result.stderr for value in values.values()), 'readiness echoed secret'
report = json.loads(result.stdout)
assert report['configuration_ready'] and not report['ready_for_public_launch']
assert report['external_requests'] == 0 and not report['database_access']
checks.append('existing-readiness-through-file-loader')
print(json.dumps({'entrypoint_checks': len(checks), 'linux_uid': 10001,
                  'credential_mode': '0400', 'external_requests': 0, 'database_access': False}))
'''
    result = subprocess.run([
        'docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '--read-only',
        '--user', '0:0', '--tmpfs', '/run/secrets:mode=0755',
        '--mount', f'type=bind,src={DEPLOY / "docker-entrypoint.py"},dst=/audit-entrypoint.py,readonly',
        '--entrypoint', 'python', '-i', sys.argv[1], '-'],
        input=fixture, env=env, capture_output=True, text=True)
    if result.returncode:
        # Only assertion labels and a fixed error; never dump captured container output.
        print('Synthetic authentication configuration audit failed.', file=sys.stderr)
        for line in result.stderr.splitlines():
            if line.startswith('AssertionError:'):
                print(line, file=sys.stderr)
        return 1
    report = json.loads(result.stdout)
    report.update(result='passed', compose_app_only=True, migration_unchanged=True,
                  default_gates_closed=True, networks_unchanged=True)
    print(json.dumps(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
