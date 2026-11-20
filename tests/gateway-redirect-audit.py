#!/usr/bin/env python3
"""Run docker-nginx.conf in an existing local sage-web image and check the public redirects.

Usage: gateway-redirect-audit.py <existing-local-web-image-id>. Uses a throwaway self-signed
certificate; no network pull, no app container, no real secrets.
"""
import http.client
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix='sage-gateway-redirect-') as directory:
        secrets = Path(directory)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=sagegogo.com',
                        '-addext', 'subjectAltName=DNS:sagegogo.com,DNS:www.sagegogo.com',
                        '-keyout', str(secrets / 'tls_key'), '-out', str(secrets / 'tls_certificate')],
                       check=True, capture_output=True)
        (secrets / 'preview_htpasswd').write_text('synthetic:$apr1$invalid$invalid\n')
        for path in secrets.iterdir():
            path.chmod(0o644)
        https_port, http_port = free_port(), free_port()
        name = f'sage-gateway-redirect-{https_port}'
        subprocess.run(['docker', 'run', '-d', '--rm', '--pull', 'never', '--name', name, '--read-only',
                        '--tmpfs', '/tmp', '--add-host', 'app:127.0.0.1',
                        '-p', f'127.0.0.1:{https_port}:8443', '-p', f'127.0.0.1:{http_port}:8080',
                        '-v', f'{ROOT / "drafts/restricted-deploy/docker-nginx.conf"}:/etc/nginx/nginx.conf:ro',
                        '-v', f'{secrets}:/run/secrets:ro', '--entrypoint', 'nginx', sys.argv[1], '-g', 'daemon off;'],
                       check=True, capture_output=True)
        try:
            context = ssl.create_default_context(cafile=str(secrets / 'tls_certificate'))

            def request(scheme, host, path, sni=None):
                for _ in range(50):
                    try:
                        if scheme == 'https':
                            connection = http.client.HTTPSConnection('127.0.0.1', https_port, timeout=5, context=context)
                            connection.sock = context.wrap_socket(socket.create_connection(('127.0.0.1', https_port), 5),
                                                                  server_hostname=sni or host)
                        else:
                            connection = http.client.HTTPConnection('127.0.0.1', http_port, timeout=5)
                        connection.request('GET', path, headers={'Host': host})
                        response = connection.getresponse()
                        return response.status, response.getheader('Location'), response.getheader('Strict-Transport-Security')
                    except ConnectionRefusedError:
                        time.sleep(0.1)
                    except (http.client.RemoteDisconnected, ConnectionResetError):
                        return 444, None, None
                raise AssertionError('Gateway did not start')

            # Docker's port forwarder accepts before nginx listens; wait for a real response first.
            deadline = time.monotonic() + 20
            while True:
                try:
                    if request('https', 'sagegogo.com', '/index.html')[0] == 200:
                        break
                except (ssl.SSLError, OSError):
                    pass
                if time.monotonic() > deadline:
                    raise AssertionError('Gateway did not become ready')
                time.sleep(0.2)
            assert request('http', 'sagegogo.com', '/pages/pricing.html?plan=pro') == \
                (301, 'https://sagegogo.com/pages/pricing.html?plan=pro', None), 'HTTP apex redirect'
            assert request('http', 'www.sagegogo.com', '/') == (301, 'https://sagegogo.com/', None), 'HTTP www redirect'
            assert request('http', 'unapproved.invalid', '/')[0] == 444, 'Unknown HTTP host accepted'
            status, location, hsts = request('https', 'www.sagegogo.com', '/docs?x=1')
            assert (status, location) == (301, 'https://sagegogo.com/docs?x=1') and hsts, 'HTTPS www redirect'
            assert request('https', 'sagegogo.com', '/index.html')[0] == 200, 'Apex site unavailable'
            assert request('https', 'unapproved.invalid', '/index.html', sni='sagegogo.com')[0] in \
                (400, 403, 404, 421, 444), 'Host mismatch accepted'
            try:
                request('https', 'unapproved.invalid', '/', sni='unapproved.invalid')
            except ssl.SSLError:
                pass
            else:
                raise AssertionError('Unknown SNI accepted')
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
    print(json.dumps({'result': 'passed', 'checks': 7, 'http_redirects_to': 'https://sagegogo.com',
                      'www_redirects_to': 'https://sagegogo.com', 'unknown_host_and_sni_refused': True}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
