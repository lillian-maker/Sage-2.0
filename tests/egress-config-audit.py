#!/usr/bin/env python3
"""Compose-level checks for compose.egress.yaml: only the egress service reaches the internet."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

DEPLOY = Path(__file__).resolve().parents[1] / 'drafts/restricted-deploy'


def config(*files, env):
    command = ['docker', 'compose', '--env-file', '/dev/null', '--profile', 'tools', '-p', 'sage-egress-audit']
    for name in files:
        command += ['-f', str(DEPLOY / name)]
    return json.loads(subprocess.run(command + ['config', '--format', 'json'], env=env,
                                     check=True, capture_output=True, text=True).stdout)


def main():
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT') if key in os.environ}
    with tempfile.TemporaryDirectory(prefix='sage-egress-config-') as directory:
        env.update(SAGE_IMAGE_TAG='synthetic-egress', SAGE_DOCKER_SECRETS_DIR=directory,
                   SAGE_FRONTEND_SUBNET='192.0.2.0/24', SAGE_GATEWAY_IP='192.0.2.2', SAGE_APP_IP='192.0.2.3',
                   SAGE_EGRESS_UPSTREAM='relay.example.invalid:3128')
        before = config('compose.yaml', 'compose.auth.yaml', env=env)
        after = config('compose.yaml', 'compose.auth.yaml', 'compose.egress.yaml', env=env)
    for service in ('db', 'web', 'migrate'):
        assert before['services'][service] == after['services'][service], service + ' changed'
    for name in ('backend', 'frontend', 'edge'):
        assert before['networks'][name] == after['networks'][name], name + ' network changed'
    app, egress = after['services']['app'], after['services']['egress']
    assert set(app['networks']) == set(before['services']['app']['networks']) | {'egress_internal'}
    assert after['networks']['egress_internal'].get('internal') is True
    assert not after['networks']['egress_external'].get('internal')
    internet = {name for name, network in after['networks'].items() if not network.get('internal')} - {'edge'}
    for name, service in after['services'].items():
        if name != 'egress':
            assert not internet & set(service.get('networks', {})), name + ' reaches the internet'
    assert set(egress['networks']) == {'egress_internal', 'egress_external'}
    assert not egress.get('secrets') and not egress.get('ports') and not egress.get('volumes')
    assert egress['read_only'] and egress['user'] == '10001:10001' and egress['cap_drop'] == ['ALL']
    assert egress['entrypoint'] == ['python', '/app/deploy/egress_proxy.py']
    assert egress['environment']['SAGE_EGRESS_ALLOW'] == \
        'ses.tencentcloudapi.com,captcha.tencentcloudapi.com,sms.tencentcloudapi.com'
    assert egress['environment']['SAGE_EGRESS_UPSTREAM_HOSTS'] == 'oauth2.googleapis.com,www.googleapis.com'
    assert egress['environment']['SAGE_EGRESS_UPSTREAM'] == 'relay.example.invalid:3128'
    assert app['environment']['HTTPS_PROXY'] == 'http://egress:3128'
    for key, value in before['services']['app']['environment'].items():
        assert app['environment'][key] == value, key + ' changed'
    print(json.dumps({'result': 'passed', 'internet_services': ['egress'], 'base_services_unchanged': True,
                      'auth_gates_unchanged': True}))


if __name__ == '__main__':
    main()
