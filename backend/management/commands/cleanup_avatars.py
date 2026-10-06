"""Explicit retry of durable avatar cleanup; dry-run is the default."""
import json

from django.core.management.base import BaseCommand, CommandError

from backend.account import cleanup_avatar_files
from backend.models import AccountProfile


class Command(BaseCommand):
    help = 'Inspect pending avatar cleanup; add --apply to retry. Does not expose paths or identities.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        pending = AccountProfile.objects.exclude(pending_avatar_deletions=[])
        users = list(pending.values_list('user_id', flat=True))
        if options['apply']:
            for user_id in users:
                cleanup_avatar_files(user_id)
        remaining = AccountProfile.objects.exclude(pending_avatar_deletions=[]).count()
        self.stdout.write(json.dumps({'mode': 'apply' if options['apply'] else 'dry_run',
            'profiles_before': len(users), 'profiles_remaining': remaining}))
        if options['apply'] and remaining:
            raise CommandError('AVATAR_CLEANUP_PENDING')
