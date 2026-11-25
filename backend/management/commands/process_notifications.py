"""Prepare actual financial notices and dispatch only with explicit approval."""
import json
import logging
from datetime import timedelta
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from backend.models import Membership
from backend.commerce.models import BillingNotification, PaymentAttempt
from backend.notifications import (prepare_renewal_notice, prepare_notice_delivery,
    send_notice_delivery, record_renewal_notice_blocked, renewal_notice_confirmed)

REMINDER_ALERT_AFTER = timedelta(minutes=30)


class Command(BaseCommand):
    help = 'Inspect pending notices; --apply sends only via separately approved templates. No payments.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--since', help='Approved notification start, as an aware ISO datetime.')

    def handle(self, *args, **options):
        if options['apply'] and not settings.NOTIFICATION_DELIVERY_ENABLED:
            raise CommandError('NOTICE_CHANNEL_UNAVAILABLE')
        since = None
        if options['apply']:
            try:
                since = datetime.fromisoformat(options['since'])
                if timezone.is_naive(since):
                    raise ValueError
            except (TypeError, ValueError):
                raise CommandError('NOTICE_START_TIME_REQUIRED') from None
        now = timezone.now()
        attempts = PaymentAttempt.objects.filter(subscription__isnull=False, dispatched_at__isnull=True,
            state='created', subscription__auto_renew=True).select_related('subscription__initial_order', 'account')
        result = {'mode': 'apply' if options['apply'] else 'dry-run', 'renewals': attempts.count(),
            'sent': 0, 'unknown': 0, 'notice_unconfirmed': 0, 'contacts_missing': 0, 'contacts_changed': 0,
            'channels_unavailable': 0, 'recipients_unavailable': 0, 'unsupported_types': 0, 'payments_dispatched': 0}
        if not options['apply']:
            result['pending_notices'] = BillingNotification.objects.count()
            self.stdout.write(json.dumps(result))
            return
        for attempt in attempts:
            notice = prepare_renewal_notice(attempt.account, attempt.subscription_id,
                attempt.renewal_cycle_index, charge_at=attempt.dispatch_not_before)
            if (now > notice.occurred_at+REMINDER_ALERT_AFTER and
                    not renewal_notice_confirmed(attempt.subscription, attempt.renewal_cycle_index,
                        charge_at=attempt.dispatch_not_before)):
                # Alert only; the renewal charge still runs at its planned time.
                record_renewal_notice_blocked(attempt.subscription, attempt.renewal_cycle_index)
                result['notice_unconfirmed'] += 1
        for notice in BillingNotification.objects.select_related('account').filter(
                created_at__gte=since).exclude(type='account_security').iterator():
            if notice.type == 'renewal_reminder':
                # Send from the reminder time (two days before expiry) until the
                # planned charge; a job that runs late still sends, never backdates.
                if not notice.occurred_at <= now < notice.occurred_at+timedelta(days=1):
                    continue
            elif notice.occurred_at > now:
                continue
            members = Membership.objects.filter(account=notice.account, is_active=True, user__is_active=True,
                role__in=('owner', 'billing_admin')).select_related('user')
            if notice.recipient_user_id:
                members = members.filter(user_id=notice.recipient_user_id)
            for member in members:
                try:
                    delivery = prepare_notice_delivery(notice, member.user)
                    if delivery.state != 'pending':
                        continue
                    outcome = send_notice_delivery(delivery.pk)
                except ValueError as exc:
                    counters = {'NOTICE_CONTACT_REQUIRED': 'contacts_missing',
                        'NOTICE_CONTACT_CHANGED': 'contacts_changed', 'FORBIDDEN': 'recipients_unavailable',
                        'NOTICE_TYPE_UNSUPPORTED': 'unsupported_types'}
                    if str(exc) not in counters:
                        raise
                    result[counters[str(exc)]] += 1
                    logging.getLogger('sage').warning('notice_recipient_blocked notification_id=%s code=%s',notice.pk,str(exc))
                    continue
                except OSError as exc:
                    if str(exc) != 'NOTICE_CHANNEL_UNAVAILABLE':
                        raise
                    result['channels_unavailable'] += 1
                    logging.getLogger('sage').warning('notice_recipient_blocked notification_id=%s code=NOTICE_CHANNEL_UNAVAILABLE',notice.pk)
                    continue
                if outcome in ('sent', 'unknown'):
                    result[outcome] += 1
        self.stdout.write(json.dumps(result))
