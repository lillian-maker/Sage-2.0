#!/usr/bin/env python3
"""Static regression checks for the offline deployment artifacts."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / 'drafts' / 'restricted-deploy'
PROD = DEPLOY / 'prod-20261107'


def contents(path):
    return path.read_text(encoding='utf-8')


def main():
    package = contents(PROD / 'package-deployment.sh')
    assert 'docker save -o "$RAW_ARCHIVE"' in package
    assert 'docker save "$APP_TAG" "$WEB_TAG" | gzip' not in package
    assert 'Unexpected app image platform' in package
    assert 'Unexpected web image platform' in package
    assert 'compose.egress.yaml' in package
    assert 'git -C "$ROOT" archive --format=tar "$SHA"' in package
    assert package.index('tar -czf "$TMP_BUNDLE" -C "$TMP_CANDIDATE" .') < package.index('mv "$TMP_CANDIDATE" "$CANDIDATE"')

    backup = contents(PROD / 'sage-prod-backup.sh')
    assert backup.index('pg_restore --file=/dev/null "$TMP"') < backup.index('mv "$TMP" "$FINAL"')
    assert backup.index('mv "$TMP" "$FINAL"') < backup.index("find \"$DEST\" -name 'sage_preview_*.dump'")

    archive = contents(PROD / 'sage-log-archive.sh')
    assert 'DEST=$MONTH/$(date -u +%Y%m%d-%H%M%S)' in archive
    assert '"$p" "$p".*' in archive
    assert 'host nginx logs missing' in archive
    assert 'sagegogo-access.log*' in archive

    host_nginx = contents(PROD / 'nginx-sagegogo.conf')
    access_log = 'access_log /var/log/nginx/sagegogo-access.log sage_edge;'
    assert host_nginx.count(access_log) == 2
    assert host_nginx.count('error_log  /var/log/nginx/sagegogo-error.log crit;') == 2

    gateway = contents(DEPLOY / 'docker-nginx.conf')
    assert 'absolute_redirect off;' in gateway

    phase_a = contents(PROD / 'phase-a-procedure.md')
    assert phase_a.index('sage-log-archive.sh') < phase_a.index('stop app web')

    cron = contents(PROD / 'sage-prod-cron.sh')
    assert 'LOCK_DIR=/var/lock/sage-prod' in cron
    assert '/tmp/sage-prod-cron-' not in cron
    assert 'sage-compose.sh' in cron
    assert '-f compose.yaml' not in cron

    for name in ('compose.auth.yaml', 'compose.alipay.yaml', 'compose.sales.yaml'):
        assert name in package
    helper = contents(PROD / 'sage-compose.sh')
    order = ['compose.sales.yaml', 'compose.alipay.yaml', 'compose.egress.yaml', 'compose.auth.yaml', 'compose.offline.yaml']
    positions = [helper.index('/' + name) for name in order]
    assert positions == sorted(positions), 'overlays must be prepended in reverse order'
    assert 'compose.public.yaml' not in helper
    assert 'Unknown compose overlay' in helper
    assert (PROD / 'sage-compose.sh').stat().st_mode & 0o111

    compose = contents(DEPLOY / 'compose.yaml')
    egress = contents(DEPLOY / 'compose.egress.yaml')
    assert 'max-file: "36"' in compose
    assert 'max-file: "36"' in egress
    assert '${SAGE_PUBLIC_HTTPS_PORT:-9444}:8443' in compose
    print('deployment artifact audit passed')


if __name__ == '__main__':
    main()
