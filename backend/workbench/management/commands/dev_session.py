"""Create a signed-in session for a seeded user (SAGE_ENV development/test only).

Writes the session cookie value to --out (mode 0600) instead of printing it, so local
browser checks and app e2e runs can sign in without receiving a verification code.
"""
import os
from importlib import import_module

from django.conf import settings
from django.contrib.auth import login
from django.core.management.base import BaseCommand, CommandError
from django.http import HttpRequest

from backend.models import LoginIdentity, Membership


class Command(BaseCommand):
    help = 'Create a dev session for a seeded user and write its cookie value to a file.'

    def add_arguments(self, parser):
        parser.add_argument('--email', default='lillian@sagedemo.com')
        parser.add_argument('--account', choices=('business', 'personal'), default='business')
        parser.add_argument('--out', default=str(settings.BASE_DIR / 'tmp' / 'dev-session.txt'))

    def handle(self, *args, **options):
        if settings.ENVIRONMENT not in ('development', 'test'):
            raise CommandError('DEV_SESSION_ENVIRONMENT_FORBIDDEN')
        identity = LoginIdentity.objects.filter(method='email', identifier=options['email']).select_related('user').first()
        if identity is None:
            raise CommandError('UNKNOWN_USER (run seed_app first)')
        user = identity.user
        membership = Membership.objects.filter(user=user, account__kind=options['account'],
                                               is_active=True).order_by('account_id').first()
        if membership is None:
            raise CommandError('NO_%s_ACCOUNT' % options['account'].upper())
        request = HttpRequest()
        request.session = import_module(settings.SESSION_ENGINE).SessionStore()
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        request.session['active_account_id'] = str(membership.account_id)
        request.session.save()
        path = options['out']
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write('%s=%s\n' % (settings.SESSION_COOKIE_NAME, request.session.session_key))
        self.stdout.write('session written to %s (account %s)' % (path, membership.account_id))
