"""Pull new messages from every connected inbox source through the registered adapters."""
from django.core.management.base import BaseCommand

from backend.workbench.services.inbox import pull_all


class Command(BaseCommand):
    help = 'Fetch, filter and judge new messages for all connected inbox sources.'

    def handle(self, *args, **options):
        for source, stats, error_code in pull_all():
            self.stdout.write('%s %s %s' % (source.public_id, source.kind, error_code or stats))
