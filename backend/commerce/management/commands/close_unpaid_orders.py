from django.core.management.base import BaseCommand
from django.utils import timezone

from backend.billing_rules import UNPAID_ORDER_TTL
from backend.commerce.billing import expire_unpaid_order
from backend.commerce.models import Order, PaymentAttempt


class Command(BaseCommand):
    help = ('Close unpaid checkout orders past their payment deadline. Undispatched orders close '
            'locally; dispatched Alipay and WeChat Pay payments are closed and then queried. Never charges.')

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=500)

    def handle(self, *args, **options):
        from backend import payments
        counts = {'expired': 0, 'channel_closed': 0, 'paid': 0, 'still_open': 0,
                  'unsupported_channel': 0, 'failed': 0}
        cutoff = timezone.now() - UNPAID_ORDER_TTL
        rows = (Order.objects.filter(status='pending_payment', paid_at__isnull=True, created_at__lte=cutoff)
                .select_related('account').order_by('created_at')[:max(1, options['limit'])])
        for order in rows:
            try:
                outcome = expire_unpaid_order(order.account, order.pk)
                if outcome == 'expired':
                    counts['expired'] += 1
                if outcome != 'needs_channel_close':
                    continue
                close = {'alipay': payments.close_alipay_payment,
                         'wechat': payments.close_wechat_payment}.get(order.snapshot.get('channel'))
                if close is None:
                    counts['unsupported_channel'] += 1
                    continue
                attempt = PaymentAttempt.objects.get(order=order)
                close(order.account, attempt.pk)
                order.refresh_from_db()
                if order.status == 'closed':
                    counts['channel_closed'] += 1
                elif order.paid_at:
                    counts['paid'] += 1
                else:
                    counts['still_open'] += 1
            except (ValueError, RuntimeError):
                counts['failed'] += 1
        self.stdout.write('; '.join(f'{key}: {value}' for key, value in counts.items()))
        if counts['failed']:
            self.stderr.write('UNPAID_ORDER_CLOSE_FAILED')
