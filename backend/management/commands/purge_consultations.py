"""No production scheduler is created. Dry-run is the default and prints no PII."""
import json

from django.core.management.base import BaseCommand
from django.utils import timezone

from backend.consultations import purge_record
from backend.models import Consultation


class Command(BaseCommand):
    help = 'Preview or apply completed consultation personal-information retention cleanup.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        now = timezone.now()
        records = Consultation.objects.filter(status='completed', purge_at__lte=now,
                                               purged_at__isnull=True).order_by('pk')
        eligible = records.count()
        changed = 0
        if options['apply']:
            for record_id in records.values_list('pk', flat=True).iterator(chunk_size=200):
                changed += purge_record(record_id, scheduled=True, now=now)
        self.stdout.write(json.dumps({'mode': 'apply' if options['apply'] else 'dry-run',
                                      'eligible': eligible, 'purged': changed}))
