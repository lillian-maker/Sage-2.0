from django.core.management.base import BaseCommand
from django.db.models import Q

from backend.models import BillingAccount
from backend.commerce.models import SubscriptionPeriod, CreditBatch
from backend.commerce.subscriptions import grant_due_credits, close_expired_renewals, record_expired_notifications


class Command(BaseCommand):
    help = 'Deliver paid subscription credits, stop expired renewals and record actual expiry notices. No charge or external message.'

    def handle(self, *args, **options):
        count, closed, notices = 0, 0, 0
        accounts = SubscriptionPeriod.objects.values_list('subscription__account_id', flat=True).distinct()
        batches = CreditBatch.objects.values_list('account_id',flat=True).distinct()
        for account in BillingAccount.objects.filter(Q(pk__in=accounts)|Q(pk__in=batches)).iterator():
            count += grant_due_credits(account)['created_batches']
            closed += close_expired_renewals(account)
            notices += record_expired_notifications(account)
        self.stdout.write(f'Delivered credit batches: {count}; expired renewals blocked: {closed}; payment attempts created: 0; expiry notices recorded: {notices}')
