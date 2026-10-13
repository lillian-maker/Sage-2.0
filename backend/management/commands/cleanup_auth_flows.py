"""Explicit technical auth-state cleanup, dry-run by default."""
import json

from django.core.management.base import BaseCommand

from backend.authentication import cleanup_auth_data


class Command(BaseCommand):
    help = 'Inspect expired/consumed auth state; add --apply to scrub. Never modifies accounts or ticket tombstones.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        counts = cleanup_auth_data(apply=options['apply'])
        self.stdout.write(json.dumps({'mode':'apply' if options['apply'] else 'dry-run',**counts}))
