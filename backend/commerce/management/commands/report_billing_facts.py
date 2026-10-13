"""Account-scoped ledger observations; no write, channel call or revenue recognition."""
import json
import uuid
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.utils import timezone

from backend.models import BillingAccount
from backend.commerce.billing import task_cost_observation
from backend.commerce.models import (BillingIssue, CreditFreeze, CreditRefund, Order,
    PaymentAttempt, PaymentAttemptEvent, PaymentFact)


class Command(BaseCommand):
    help = 'Read account-scoped billing facts: a UTC [start,end) report or one order/transaction/internal refund/task lookup. No write or channel call.'

    def add_arguments(self, parser):
        parser.add_argument('--account', required=True, type=uuid.UUID)
        parser.add_argument('--start', help='Report mode: inclusive UTC ISO timestamp, ending in Z or +00:00.')
        parser.add_argument('--end', help='Report mode: exclusive UTC ISO timestamp, ending in Z or +00:00.')
        parser.add_argument('--include-age', action='store_true', help='Report mode: append current account backlog ages, not historical window ages or alert thresholds.')
        lookup = parser.add_mutually_exclusive_group()
        lookup.add_argument('--order', help='Lookup an internal Order UUID within --account.')
        lookup.add_argument('--transaction-reference', help='Lookup an exact stored channel transaction reference; ambiguous matches are rejected.')
        lookup.add_argument('--refund', help='Lookup an internal CreditRefund UUID, not a PSP refund reference.')
        lookup.add_argument('--task', help='Observe current internal costs for a Reservation UUID within --account; not Credits charges.')

    def handle(self, *args, **options):
        selectors = [key for key in ('order', 'transaction_reference', 'refund', 'task')
                     if options.get(key) is not None]
        if len(selectors) > 1 or (selectors and (
                any(options.get(key) is not None for key in ('start','end')) or options['include_age'])):
            raise CommandError('INVALID_LOOKUP_MODE')
        if selectors:
            try:
                account_id = uuid.UUID(str(options['account']))
            except ValueError as exc:
                raise CommandError('INVALID_ACCOUNT') from exc
            if not BillingAccount.objects.filter(pk=account_id).exists():
                raise CommandError('ACCOUNT_NOT_FOUND')
            result = self.lookup(account_id, selectors[0], options[selectors[0]])
            self.stdout.write(json.dumps(result, ensure_ascii=False, cls=DjangoJSONEncoder))
            return
        boundaries = []
        for field in ('start', 'end'):
            value = options[field]
            try:
                if not isinstance(value, str) or 'T' not in value or not value.endswith(('Z', '+00:00')):
                    raise ValueError
                parsed = datetime.fromisoformat(value)
                if parsed.utcoffset() != timedelta(0):
                    raise ValueError
            except ValueError as exc:
                raise CommandError('UTC_WINDOW_REQUIRED') from exc
            boundaries.append(parsed)
        start, end = boundaries
        if start >= end:
            raise CommandError('INVALID_WINDOW')
        try:
            account_id = uuid.UUID(str(options['account']))
        except ValueError as exc:
            raise CommandError('INVALID_ACCOUNT') from exc
        if not BillingAccount.objects.filter(pk=account_id).exists():
            raise CommandError('ACCOUNT_NOT_FOUND')

        receipts = PaymentFact.objects.filter(order__account_id=account_id, disposition='accepted',
            currency='CNY', paid_at__gte=start, paid_at__lt=end)
        refunds = CreditRefund.objects.filter(payment__order__account_id=account_id,
            payment__currency='CNY', state='refunded', resolved_at__gte=start, resolved_at__lt=end)
        result = {
            'account_id': str(account_id), 'currency': 'CNY',
            'scope': 'internal_ledger_facts_not_recognized_revenue',
            'window': {'start': start.isoformat(), 'end': end.isoformat(), 'bounds': '[start,end)'},
            'period_flows': {
                # Python integer aggregation stays exact even when the sum exceeds a database bigint.
                'accepted_receipt_amount_minor': str(sum(receipts.values_list('amount_minor', flat=True).iterator())),
                'accepted_paid_order_count': receipts.values('order_id').distinct().count(),
                'confirmed_credit_refund_amount_minor': str(sum(refunds.values_list('amount_minor', flat=True).iterator())),
                'confirmed_credit_refund_count': refunds.count(),
            },
            'current_cohort_state': {
                'unknown_payment_attempt_count': PaymentAttempt.objects.filter(account_id=account_id,
                    state='unknown', created_at__gte=start, created_at__lt=end).count(),
                'paid_pending_delivery_order_count': Order.objects.filter(account_id=account_id,
                    status='paid_pending_entitlement', currency='CNY', paid_at__gte=start, paid_at__lt=end,
                    payment_facts__disposition='accepted', payment_facts__currency='CNY').distinct().count(),
                'unresolved_issue_count': BillingIssue.objects.filter(account_id=account_id,
                    state='open', first_seen_at__gte=start, first_seen_at__lt=end).count(),
            },
            'definitions': {
                'accepted_receipts': 'Gross accepted CNY receipts by paid_at; excludes review facts. Refunds do not erase the original receipt.',
                'confirmed_credit_refunds': 'Only CreditRefund state=refunded, by resolved_at. Reserved and released amounts are not refunded money.',
                'current_cohort_state': 'Current statuses, not a historical as-of reconstruction: attempts by created_at, paid orders by paid_at, open issues by first_seen_at.',
                'classification': 'An explicit account is selected. Test/live classification is not recorded, so this is not a production business KPI report.',
            },
            'not_provided': ['rates', 'revenue_recognition', 'live_vs_test_classification',
                             'other_refund_types', 'historical_state_reconstruction'],
        }
        if options['include_age']:
            result['current_backlog_age'] = self.backlog_age(account_id)
        self.stdout.write(json.dumps(result, ensure_ascii=False))

    def backlog_age(self, account_id):
        observed_at = timezone.now()

        def summarize(starts):
            starts = list(starts)
            valid = [value for value in starts if value is not None and value <= observed_at]
            future = [value for value in starts if value is not None and value > observed_at]
            oldest = min(valid) if valid else None
            age = observed_at - oldest if oldest is not None else None
            return {'count': len(starts), 'known_start_count': len(valid),
                'unknown_start_count': sum(value is None for value in starts),
                'future_start_count': len(future),
                'earliest_future_start_at': min(future).isoformat() if future else None,
                'oldest_start_at': oldest.isoformat() if oldest is not None else None,
                'max_age_microseconds': str((age.days*86400+age.seconds)*1000000+age.microseconds)
                    if age is not None else None}

        unknown_starts = dict.fromkeys(PaymentAttempt.objects.filter(account_id=account_id,
            state='unknown').values_list('id', flat=True))
        # An ignored event cannot reset the current episode. A later applied
        # non-unknown event invalidates earlier unknown evidence, even if the
        # current state disagrees; do not substitute attempt.created_at. IDs
        # preserve this attempt's serialized application order across clock skew.
        events = PaymentAttemptEvent.objects.filter(attempt_id__in=unknown_starts, applied=True
            ).order_by('attempt_id','id').values_list('attempt_id','outcome','created_at')
        for attempt_id, outcome, at in events:
            if outcome != 'unknown':
                unknown_starts[attempt_id] = None
            elif unknown_starts[attempt_id] is None:
                unknown_starts[attempt_id] = at
        return {'scope': 'current_account_state_not_windowed_or_historical',
            'observed_at': observed_at.isoformat(),
            'unknown_payment_attempts': summarize(unknown_starts.values()),
            'paid_pending_delivery_orders': summarize(Order.objects.filter(account_id=account_id,
                status='paid_pending_entitlement').values_list('paid_at', flat=True)),
            'frozen_credit_allocations': summarize(CreditFreeze.objects.filter(batch__account_id=account_id,
                state='frozen').values_list('created_at', flat=True)),
            'unresolved_issues': summarize(BillingIssue.objects.filter(account_id=account_id,
                state='open').values_list('first_seen_at', flat=True))}

    def lookup(self, account_id, selector, reference):
        """Only explicit fields from existing facts; never call payment/fulfillment helpers."""
        if selector == 'task':
            try:
                reservation_id = uuid.UUID(str(reference))
            except ValueError as exc:
                raise CommandError('INVALID_LOOKUP_REFERENCE') from exc
            try:
                summary = task_cost_observation(account_id, reservation_id)
            except ValueError as exc:
                if str(exc) == 'NOT_FOUND':
                    raise CommandError('LOOKUP_NOT_FOUND') from exc
                raise CommandError('INVALID_COST_EVENT') from exc
            return {'account_id': str(account_id), 'reservation_id': str(reservation_id),
                'scope': 'current_internal_task_cost_observation_not_credits_charge', **summary}
        if selector == 'transaction_reference':
            if (not isinstance(reference, str) or not reference.strip() or len(reference) > 128
                    or any(ord(char) < 32 or ord(char) == 127 for char in reference)):
                raise CommandError('INVALID_LOOKUP_REFERENCE')
            matches = list(PaymentFact.objects.filter(order__account_id=account_id,
                transaction_reference=reference).values_list('order_id', flat=True)[:2])
            if len(matches) > 1:
                raise CommandError('AMBIGUOUS_TRANSACTION_REFERENCE')
            order_id = matches[0] if matches else None
        else:
            try:
                reference = uuid.UUID(str(reference))
            except ValueError as exc:
                raise CommandError('INVALID_LOOKUP_REFERENCE') from exc
            order_id = reference if selector == 'order' else CreditRefund.objects.filter(
                payment__order__account_id=account_id, pk=reference).values_list('payment__order_id', flat=True).first()
        order = Order.objects.filter(account_id=account_id, pk=order_id).values(
            'id','status','amount_minor','currency','paid_at','created_at').first()
        if order is None:
            raise CommandError('LOOKUP_NOT_FOUND')
        order['amount_minor'] = str(order['amount_minor'])
        payments = list(PaymentFact.objects.filter(order_id=order_id, order__account_id=account_id)
            .order_by('created_at','id').values('id','channel','transaction_reference',
                'amount_minor','currency','disposition','paid_at'))
        refunds = list(CreditRefund.objects.filter(payment__order_id=order_id,
            payment__order__account_id=account_id).order_by('created_at','id').values(
                'id','payment_id','state','amount_minor','created_at','resolved_at'))
        for item in payments + refunds:
            item['amount_minor'] = str(item['amount_minor'])
        attempt = PaymentAttempt.objects.filter(account_id=account_id, order_id=order_id).values(
            'id','state','needs_reconciliation','expires_at').first()
        related = (Q(object_type='order', object_id=order_id) |
            Q(object_type='payment', object_id__in=[item['id'] for item in payments]) |
            Q(object_type='credit_refund', object_id__in=[item['id'] for item in refunds]))
        if attempt:
            related |= Q(object_type='payment_attempt', object_id=attempt['id'])
        issues = list(BillingIssue.objects.filter(related, account_id=account_id)
            .order_by('first_seen_at','id').values('id','code','object_type','object_id',
                'state','first_seen_at','last_seen_at'))
        return {'account_id': str(account_id),
            'scope': 'internal_billing_lookup_not_channel_verification',
            'order': order, 'payments': payments, 'refunds': refunds,
            'payment_attempt': attempt, 'issues': issues,
            'delivery': {'status': 'completed' if order['status'] == 'fulfilled' else
                         'pending' if order['paid_at'] else 'not_started'}}
