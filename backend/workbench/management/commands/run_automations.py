"""Execute due automations; scheduled by the host (cron/launchd), not by the web process."""
from django.core.management.base import BaseCommand

from backend.workbench.services.automations import run_due


class Command(BaseCommand):
    help = 'Start every enabled automation whose next run time has passed; late ones are recorded as missed.'

    def handle(self, *args, **options):
        for public_id, scheduled, outcome in run_due():
            self.stdout.write('%s %s %s' % (public_id, scheduled.isoformat(), outcome))
