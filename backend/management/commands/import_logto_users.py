"""Dry-run by default. Import Django users into Logto through the Management API.

The Logto custom user id is the Django user primary key. Only a verified email
login identity becomes primaryEmail. Django PBKDF2-SHA256 hashes are converted
to Logto's Legacy import shape; unsupported hashes are omitted and counted.
No email, hash or token is printed.
"""
import base64
import json
import os
import time
from urllib import error, request

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from backend.models import LoginIdentity


def legacy_password_digest(encoded):
    if not isinstance(encoded, str):
        return None
    parts = encoded.split('$', 3)
    if len(parts) != 4 or parts[0] != 'pbkdf2_sha256':
        return None
    _, iterations, salt, encoded_digest = parts
    try:
        rounds = int(iterations)
        digest = base64.b64decode(encoded_digest, validate=True)
    except (TypeError, ValueError):
        return None
    if rounds <= 0 or len(digest) != 32 or not salt:
        return None
    return json.dumps(
        ['pbkdf2', [salt, str(rounds), str(len(digest)), 'sha256', '@'], digest.hex()],
        separators=(',', ':'),
    )


def build_user_payload(user):
    email = LoginIdentity.objects.filter(
        user=user, method='email').order_by('id').values_list('identifier', flat=True).first() or ''
    payload = {'id': str(user.pk)}
    if email:
        payload['primaryEmail'] = email
    digest = None
    if user.has_usable_password() and user.password:
        digest = legacy_password_digest(user.password)
    if digest is not None:
        payload['passwordAlgorithm'] = 'Legacy'
        payload['passwordDigest'] = digest
    return payload, digest is not None


def post_user(endpoint, token, payload, *, timeout=15):
    body = json.dumps(payload).encode('utf-8')
    call = request.Request(
        endpoint.rstrip('/') + '/api/users',
        data=body,
        headers={
            'Authorization': 'Bearer ' + token,
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with request.urlopen(call, timeout=timeout) as response:
            return response.status, 'created'
    except error.HTTPError as exc:
        detail = exc.read(4096).decode('utf-8', 'replace')
        if exc.code == 422 and 'user.id_already_in_use' in detail:
            return exc.code, 'exists'
        return exc.code, 'http-error'
    except (error.URLError, TimeoutError, OSError):
        return 0, 'transport-error'


def import_users(users, *, apply=False, endpoint='', token='', post=post_user,
                 sleep=time.sleep, delay=0.25):
    counts = {
        'total': 0,
        'created': 0,
        'exists': 0,
        'failed': 0,
        'passwords_skipped': 0,
    }
    for user in users:
        payload, has_password = build_user_payload(user)
        counts['total'] += 1
        if not has_password:
            counts['passwords_skipped'] += 1
        if not apply:
            counts['created'] += 1
            continue
        status, outcome = post(endpoint, token, payload)
        if 200 <= status < 300 or outcome == 'created':
            counts['created'] += 1
        elif outcome == 'exists':
            counts['exists'] += 1
        else:
            counts['failed'] += 1
        if delay > 0:
            sleep(delay)
    return counts


class Command(BaseCommand):
    help = (
        'Dry-run by default. Add --apply and LOGTO_MANAGEMENT_ENDPOINT / '
        'LOGTO_MANAGEMENT_TOKEN to import Django users into Logto. '
        'Excludes staff unless --include-staff is set.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--delay', type=float, default=0.25)
        parser.add_argument('--include-staff', action='store_true')

    def handle(self, *args, **options):
        apply = options['apply']
        endpoint = os.environ.get('LOGTO_MANAGEMENT_ENDPOINT', '')
        token = os.environ.get('LOGTO_MANAGEMENT_TOKEN', '')
        if apply and (not endpoint or not token):
            raise CommandError('LOGTO_MANAGEMENT_ENDPOINT_AND_TOKEN_REQUIRED')
        users = get_user_model().objects.filter(is_active=True).order_by('pk')
        if not options['include_staff']:
            users = users.exclude(is_staff=True)
        if options['limit'] > 0:
            users = users[:options['limit']]
        result = import_users(
            users.iterator(),
            apply=apply,
            endpoint=endpoint,
            token=token,
            delay=max(0.0, options['delay']),
        )
        self.stdout.write(json.dumps({'mode': 'apply' if apply else 'dry-run', **result}))
