"""app/notifications: merges AppNotification (E56) with the existing billing notices without copying them."""
import re
from datetime import datetime

from django.db import transaction
from django.utils import timezone
from .. import clock

from backend.models import BillingAccount
from backend.notifications import NOTICE_TITLES, mark_read, notification_page, unread_count

from ..common import PAGE_SIZE
from ..models import AppNotification
from .inbox import expert_brief

CATEGORIES = ('all', 'task_progress', 'billing')
USAGE_TYPE = re.compile(r'credits_usage_(\d{1,3})')
LEDGER_LABEL = {'personal': '个人账本', 'business': '企业账本'}


def app_notifications(ctx):
    # @-mentions live in the inbox "@ 与指派" view (Q-B-2 A), so the centre lists only passive reminders.
    return AppNotification.objects.filter(account=ctx.account, recipient=ctx.user).exclude(
        category='mention').select_related('actor_expert')


def app_item(n):
    return {'source': 'app', 'id': n.public_id, 'category': n.category, 'type': n.type, 'text': n.text,
            'context_label': n.context_label, 'actor_expert': expert_brief(n.actor_expert), 'action': n.action,
            'target': {'type': n.target_type, 'id': n.target_id} if n.target_type else None,
            'occurred_at': n.occurred_at, 'read_at': n.read_at}


def usage_percent(notice_type):
    match = USAGE_TYPE.fullmatch(notice_type or '')
    return int(match.group(1)) if match else None


def _billing_stream(user, unread, needed):
    rows, offset, total = [], 0, None
    while True:
        page = notification_page(user, offset)
        if total is None:
            total = page['total']
        rows.extend(item for item in page['items'] if not (unread and item['read_at']))
        if len(rows) >= needed or page['next_offset'] is None:
            break
        offset = page['next_offset']
    kinds = {str(pk): kind for pk, kind in BillingAccount.objects.filter(
        pk__in={row['account_id'] for row in rows[:needed] if row['account_id']}).values_list('pk', 'kind')}
    items = []
    for row in rows[:needed]:
        kind = kinds.get(row['account_id'])
        billing = dict(row, account_kind=kind, usage_percent=usage_percent(row['type']))
        occurred = datetime.fromisoformat(row['occurred_at'])
        local = timezone.localtime(occurred)
        # Billing notices are dated facts, so the row shows the ledger and the calendar day instead of a relative time.
        label = ' · '.join(part for part in (LEDGER_LABEL.get(kind, ''), '%d月%d日' % (local.month, local.day)) if part)
        items.append({'source': 'billing', 'id': row['id'], 'category': 'billing', 'type': row['type'],
                      'text': NOTICE_TITLES.get(row['type'], '账务提醒'), 'context_label': label,
                      'actor_expert': None, 'action': 'open', 'target': None,
                      'occurred_at': occurred, 'read_at': row['read_at'], 'billing': billing})
    return items, (unread_count(user) if unread else total)


def centre_unread(ctx):
    return app_notifications(ctx).filter(read_at__isnull=True).count() + unread_count(ctx.user)


def notification_list(ctx, category, unread, offset):
    needed = offset + PAGE_SIZE
    merged, total = [], 0
    if category in ('all', 'task_progress'):
        rows = app_notifications(ctx)
        if unread:
            rows = rows.filter(read_at__isnull=True)
        total += rows.count()
        merged.extend(app_item(n) for n in rows.order_by('-occurred_at', '-id')[:needed])
    if category in ('all', 'billing'):
        items, count = _billing_stream(ctx.user, unread, needed)
        total += count
        merged.extend(items)
    merged.sort(key=lambda item: item['occurred_at'], reverse=True)
    return {'items': merged[offset:needed], 'offset': offset, 'total': total,
            'next_offset': needed if needed < total else None, 'unread_count': centre_unread(ctx)}


def _id_list(value):
    if not isinstance(value, list) or len(value) > 100 or any(not isinstance(v, str) for v in value):
        raise ValueError('VALIDATION_FAILED')
    if len(set(value)) != len(value):
        raise ValueError('VALIDATION_FAILED')
    return value


@transaction.atomic
def mark_notifications_read(ctx, data):
    from django.utils import timezone
    now = clock.now()
    if 'all' in data:
        if set(data) != {'all'} or data['all'] is not True:
            raise ValueError('VALIDATION_FAILED')
        app_notifications(ctx).filter(read_at__isnull=True).update(read_at=now, updated_at=now)
        mark_read(ctx.user, {'all': True})
    else:
        app_ids = _id_list(data.get('app_ids', []))
        billing_ids = _id_list(data.get('billing_ids', []))
        if not app_ids and not billing_ids:
            raise ValueError('VALIDATION_FAILED')
        rows = app_notifications(ctx).filter(public_id__in=app_ids)
        if rows.count() != len(app_ids):
            raise ValueError('NOT_FOUND')
        rows.filter(read_at__isnull=True).update(read_at=now, updated_at=now)
        if billing_ids:
            # Existing errors (INVALID_REQUEST, FORBIDDEN) pass through and roll back the app update.
            mark_read(ctx.user, {'notification_ids': billing_ids})
    return {'updated': True, 'unread_count': centre_unread(ctx)}
