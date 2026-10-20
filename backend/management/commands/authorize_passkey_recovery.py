"""Explicit internal-only server authorization; no secrets or PII printed."""
import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from backend.account import authorize_passkey_recovery


class Command(BaseCommand):
    help = 'Inspect a 15-minute internal passkey rebind authorization; --apply revokes existing credentials/sessions. Never grants sensitive-action access.'

    def add_arguments(self,parser):
        parser.add_argument('--user-id',required=True,type=int)
        parser.add_argument('--reason-code',required=True)
        parser.add_argument('--session-id',help='AccountSession UUID from the owner\'s new login (not the session cookie). Required for --apply.')
        parser.add_argument('--apply',action='store_true')

    def handle(self,*args,**options):
        user=get_user_model().objects.filter(pk=options['user_id'],is_active=True,is_staff=True).first()
        if not user:
            raise CommandError('ACTIVE_INTERNAL_ACCOUNT_REQUIRED')
        if options['apply']:
            try:
                authorize_passkey_recovery(user,reason=options['reason_code'],session_id=options['session_id'])
            except ValueError as exc:
                raise CommandError(str(exc)) from None
        self.stdout.write(json.dumps({'mode':'apply' if options['apply'] else 'dry-run',
            'internal_account':True,'expires_in':900,'sensitive_actions_authorized':False,
            'old_credentials_and_sessions_revoked':bool(options['apply']),
            'next_step':'sign_in_and_bind_new_passkey'}))
