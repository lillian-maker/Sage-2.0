"""Manual trusted CSV import; dry-run by default, no list values in output."""
import csv
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from backend.invitations import import_codes, read_invitation_csv


class Command(BaseCommand):
    help = 'Validate 邀请码,博主,状态 CSV; --apply atomically imports without enabling the ready gate.'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        try:
            rows = read_invitation_csv(options['path'])
            result = import_codes(rows, apply=options['apply'])
        except (OSError, UnicodeError, csv.Error, ValueError, DatabaseError) as exc:
            # Never include CSV contents, paths, driver errors, or mapping values.
            raise CommandError('INVITATION_IMPORT_FAILED') from exc
        self.stdout.write(json.dumps(result))
