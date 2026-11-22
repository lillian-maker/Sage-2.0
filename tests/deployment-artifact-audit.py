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
    assert 'compose.egress.yaml' in package
    assert 'git -C "$ROOT" archive --format=tar "$SHA"' in package
    assert 'tar -czf "$OUT/.sage-docker-$SHA-deployment.tar.gz.tmp" -C "$CANDIDATE" .' in package

    backup = contents(PROD / 'sage-prod-backup.sh')
    assert backup.index('pg_restore -l "$TMP"') < backup.index('mv "$TMP" "$FINAL"')
    assert backup.index('mv "$TMP" "$FINAL"') < backup.index("find \"$DEST\" -name 'sage_preview_*.dump'")

    archive = contents(PROD / 'sage-log-archive.sh')
    assert '"$p" "$p".*' in archive
    assert 'host nginx logs missing' in archive
    assert 'sagegogo-access.log*' in archive

    host_nginx = contents(PROD / 'nginx-sagegogo.conf')
    access_log = 'access_log /var/log/nginx/sagegogo-access.log sage_edge;'
    assert host_nginx.count(access_log) == 2
    assert host_nginx.count('error_log  /var/log/nginx/sagegogo-error.log crit;') == 2

    compose = contents(DEPLOY / 'compose.yaml')
    egress = contents(DEPLOY / 'compose.egress.yaml')
    assert 'max-file: "36"' in compose
    assert 'max-file: "36"' in egress
    assert '${SAGE_PUBLIC_HTTPS_PORT:-9444}:8443' in compose
    print('deployment artifact audit passed')


if __name__ == '__main__':
    main()
