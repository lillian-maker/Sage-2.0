import json
import logging

from django.core.management.base import BaseCommand

from backend.models import BillingAccount
from backend.commerce.models import Subscription
from backend.commerce.subscriptions import schedule_due_renewals


class Command(BaseCommand):
    help = ('Prepare renewal intents for auto-renewing terms that end within three days. '
            'The charge window is the last day before expiry. Never contacts a payment channel.')

    def handle(self, *args, **options):
        total = {'prepared': 0, 'existing': 0, 'skipped': {}}
        accounts = Subscription.objects.filter(auto_renew=True, cancellation_state='none').values('account_id')
        for account in BillingAccount.objects.filter(pk__in=accounts).iterator():
            result = schedule_due_renewals(account)
            total['prepared'] += result['prepared']
            total['existing'] += result['existing']
            for code, count in result['skipped'].items():
                total['skipped'][code] = total['skipped'].get(code, 0) + count
        if total['skipped']:
            logging.getLogger('sage').warning('renewal_schedule_skipped %s', json.dumps(total['skipped'], sort_keys=True))
        self.stdout.write(json.dumps(total, sort_keys=True))
