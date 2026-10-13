"""Reviewed expiry cleanup only; no identities printed and no scheduler created."""
import json

from django.core.management.base import BaseCommand

from backend.account import cleanup_deletion_data


class Command(BaseCommand):
    help = 'Inspect expired account-deletion records; add --apply to clean up. Never changes financial records.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        counts = cleanup_deletion_data(apply=options['apply'])
        self.stdout.write(json.dumps({'mode': 'apply' if options['apply'] else 'dry-run', **counts}))
