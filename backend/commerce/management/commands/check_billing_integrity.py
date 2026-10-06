"""Internal local audit; optional issue recording, never billing repairs or channel calls."""
import json
import uuid

from django.core.management.base import BaseCommand, CommandError

from backend.models import BillingAccount
from backend.commerce.billing import check_billing_integrity, record_billing_findings


class Command(BaseCommand):
    help = 'Check internal billing consistency; read-only unless --record is specified. Not channel reconciliation.'

    def add_arguments(self, parser):
        parser.add_argument('--account', required=True, type=uuid.UUID)
        parser.add_argument('--record', action='store_true', help='Persist findings only; never modify billing records or resolve issues.')

    def handle(self, *args, **options):
        try:
            account = BillingAccount.objects.get(pk=options['account'])
        except BillingAccount.DoesNotExist as exc:
            raise CommandError('ACCOUNT_NOT_FOUND') from exc
        result = record_billing_findings(account) if options['record'] else check_billing_integrity(account)
        self.stdout.write(json.dumps(result,ensure_ascii=False))
        if result['findings']:
            raise CommandError('BILLING_REVIEW_REQUIRED')
