"""User-scoped notices and verified pre-renewal delivery; no public send API."""
import uuid
import re
import logging
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.db.models import Q, Subquery
from django.http import JsonResponse
from django.utils import timezone

from .models import Membership, LoginIdentity, NotificationRead, NotificationDelivery
from .commerce.models import BillingNotification


NOTICE_TITLES = {
    'payment_received': '付款已确认', 'cancellation_processing': '正在关闭自动续费',
    'cancellation_confirmed': '自动续费已关闭', 'credit_refund_reserved': '点数退款申请已记录',
    'credit_refund_refunded': '点数退款已确认', 'credit_refund_released': '点数退款已终止',
    'payment_failed': '付款未完成', 'renewal_failed': '续费未完成',
    'credit_batch_expired': '点数已到期', 'service_period_ended': '服务已到期',
    'topup_authorization_recorded': '自动充值已授权', 'topup_authorization_paused': '自动充值已暂停',
    'topup_revocation_processing': '正在关闭自动充值', 'topup_revocation_confirmed': '自动充值已关闭',
    'seat_assignment_changed': '使用名额已变更', 'shared_limit_changed': '成员点数设置已变更',
    'renewal_payment_received': '续费付款已确认', 'topup_payment_received': '充值付款已确认',
    'topup_payment_failed': '充值付款未完成', 'renewal_reminder': '续费提醒',
    'renewal_notice_blocked': '续费提醒未送出', 'invoice_requested': '发票申请已记录',
    'invoice_sent': '发票已发送', 'account_security': '账号安全提醒',
    'member_removed': '企业成员已移除', 'member_role_changed': '企业成员权限已变更',
    'plan_changed': '套餐已变更', 'service_refund_requested': '订阅退款申请已记录',
    'service_refund_refunded': '订阅退款已确认', 'service_refund_failed': '订阅退款需核对',
    'seats_purchased': '新增使用名额已开通', 'seat_capacity_ending': '本期新增名额即将到期',
    'next_seats_purchased': '下一期新增名额已购买', 'seats_reduced': '本期购买名额已减少',
}
_ATTEMPTED_NOTICE_STATES = ('sending', 'unknown', 'sent', 'delivered')


def visible_notifications(user):
    active = Membership.objects.filter(user=user, user__is_active=True, is_active=True)
    financial = active.filter(role__in=('owner', 'billing_admin'))
    return BillingNotification.objects.filter(
        Q(account__isnull=True, recipient_user=user, recipient_user__is_active=True, type='account_security') |
        (Q(account_id__in=Subquery(active.values('account_id'))) & (
            Q(recipient_user=user) |
            Q(recipient_user__isnull=True, account_id__in=Subquery(financial.values('account_id'))))))


def unread_count(user):
    return visible_notifications(user).exclude(
        pk__in=Subquery(NotificationRead.objects.filter(user=user).values('notification_id'))).count()


def notification_page(user, offset=0):
    if type(offset) is not int or not 0 <= offset <= 999999999:
        raise ValueError('INVALID_REQUEST')
    query = visible_notifications(user)
    rows = list(query.select_related('account').order_by('-occurred_at', '-id')[offset:offset+51])
    reads = dict(NotificationRead.objects.filter(user=user, notification__in=rows[:50])
                 .values_list('notification_id', 'read_at'))
    items = []
    for row in rows[:50]:
        known = row.type in BillingNotification.TYPES
        items.append({'id': str(row.pk), 'account_id': str(row.account_id) if row.account_id else None,
            'account_name': row.account.name if row.account_id else '', 'type': row.type if known else 'unknown',
            'occurred_at': row.occurred_at.isoformat(),
            'read_at': reads[row.pk].isoformat() if row.pk in reads else None,
            'order_id': str(row.order_id) if known and row.order_id else None,
            'subscription_id': str(row.subscription_id) if known and row.subscription_id else None,
            'refund_id': str(row.refund_id) if known and row.refund_id else None,
            'amount_minor': str(row.amount_minor) if known and row.currency == 'CNY' and row.amount_minor is not None else None,
            'currency': 'CNY' if known and row.currency == 'CNY' and row.amount_minor is not None else None})
    return {'user_id': str(user.pk), 'items': items, 'offset': offset, 'total': query.count(),
            'next_offset': offset+50 if len(rows) > 50 else None, 'unread_count': unread_count(user)}


@transaction.atomic
def mark_read(user, data):
    if set(data) == {'all'} and data['all'] is True:
        rows = visible_notifications(user)
    elif set(data) == {'notification_ids'} and isinstance(data['notification_ids'], list):
        values = data['notification_ids']
        if not 1 <= len(values) <= 100 or any(not isinstance(item, str) for item in values):
            raise ValueError('INVALID_REQUEST')
        try:
            ids = {uuid.UUID(item) for item in values}
        except ValueError as exc:
            raise ValueError('INVALID_REQUEST') from exc
        if len(ids) != len(values):
            raise ValueError('INVALID_REQUEST')
        rows = visible_notifications(user).filter(pk__in=ids)
        if rows.count() != len(ids):
            raise ValueError('FORBIDDEN')
    else:
        raise ValueError('INVALID_REQUEST')
    now = timezone.now()
    NotificationRead.objects.bulk_create([NotificationRead(user=user, notification=row, read_at=now)
        for row in rows], ignore_conflicts=True)
    return {'updated': True, 'unread_count': unread_count(user)}


def dispatch(request, path):
    from .views import payload
    if path == 'account/notifications/summary':
        if request.GET:
            raise ValueError('INVALID_REQUEST')
        return JsonResponse({'user_id': str(request.user.pk), 'unread_count': unread_count(request.user)})
    if path == 'account/notifications':
        raw = request.GET.get('offset', '0')
        if (set(request.GET)-{'offset'} or len(request.GET.getlist('offset')) > 1 or
                not raw.isascii() or not raw.isdigit() or len(raw) > 9):
            raise ValueError('INVALID_REQUEST')
        return JsonResponse(notification_page(request.user, int(raw)))
    if path == 'account/notifications/read':
        if request.GET:
            raise ValueError('INVALID_REQUEST')
        return JsonResponse(mark_read(request.user, payload(request, (), optional=('notification_ids', 'all'))))
    if path == 'account/usage':
        raw = request.GET.get('offset', '0')
        if (set(request.GET)-{'offset'} or len(request.GET.getlist('offset')) > 1 or
                not raw.isascii() or not raw.isdigit() or len(raw) > 9):
            raise ValueError('INVALID_REQUEST')
        from .commerce.models import SharedTask
        from .commerce.billing import _shared_task_dict
        active = Membership.objects.filter(user=request.user, is_active=True).values('pk')
        tasks = SharedTask.objects.filter(membership_id__in=Subquery(active)).select_related('account')
        tasks = tasks.order_by('-created_at', '-pk')
        offset, total = int(raw), tasks.count()
        items = [{**{key: value for key, value in _shared_task_dict(task).items() if key != 'resume_blocked'},
                  'account_name': task.account.name} for task in tasks[offset:offset+50]]
        return JsonResponse({'user_id': str(request.user.pk), 'items': items, 'offset': offset,
            'total': total, 'next_offset': offset+50 if offset+50 < total else None})
    return None


@transaction.atomic
def prepare_renewal_notice(account, subscription_id, cycle_index, *, charge_at):
    """Trusted scheduler: snapshot amount and planned charge, never send on GET."""
    from .commerce.billing import _lock_account, _get, _datetime
    from .commerce.models import Subscription
    from .commerce.subscriptions import _boundary, _months, _renewal_snapshot
    account = _lock_account(account)
    subscription = _get(Subscription.objects.select_for_update().select_related('initial_order'),
        account=account, pk=subscription_id)
    _datetime(charge_at)
    if (type(cycle_index) is not int or cycle_index < 1 or not subscription.auto_renew or
            subscription.cancellation_state != 'none' or
            charge_at > _boundary(subscription, cycle_index * _months(subscription))):
        raise ValueError('INVALID_RENEWAL_NOTICE')
    remind_at = charge_at-timedelta(days=1)
    amount = _renewal_snapshot(subscription)['amount_minor']
    key = f'renewal:{subscription.pk}:{cycle_index}:reminder'
    notice, created = BillingNotification.objects.get_or_create(account=account, source_event=key,
        defaults={'type': 'renewal_reminder', 'subscription': subscription,
            'order': subscription.initial_order, 'amount_minor': amount,
            'currency': 'CNY', 'occurred_at': remind_at})
    if (notice.occurred_at != remind_at or notice.amount_minor != amount or
            notice.subscription_id != subscription.pk or notice.type != 'renewal_reminder'):
        raise ValueError('RENEWAL_NOTICE_CONFLICT')
    return notice


def assert_renewal_notice(subscription, cycle_index, *, charge_at=None):
    """Fail closed before a NEW debit; original in-flight payments remain queryable.

    A provider's identified send acknowledgement is not a claim of reading or
    handset delivery. Unknown / failed sends never qualify. A late notice cannot
    satisfy the agreed one-day notice; the customer may renew manually.
    """
    source = f'renewal:{subscription.pk}:{cycle_index}:reminder'
    notice = BillingNotification.objects.filter(account=subscription.account,
        subscription=subscription, source_event=source, type='renewal_reminder').first()
    planned = charge_at or (notice.occurred_at+timedelta(days=1) if notice else None)
    deadline = planned-timedelta(days=1) if planned else None
    current_admins = Membership.objects.filter(account=subscription.account, is_active=True,
        user__is_active=True, role__in=('owner', 'billing_admin')).values('user_id')
    deliveries = NotificationDelivery.objects.filter(notification=notice,
        user_id__in=Subquery(current_admins)) if notice else NotificationDelivery.objects.none()
    from .commerce.subscriptions import _renewal_snapshot
    valid = bool(notice and notice.currency == 'CNY' and
        notice.amount_minor == _renewal_snapshot(subscription)['amount_minor'] and
        notice.occurred_at == deadline and
        deliveries.filter(state__in=('sent', 'delivered'), confirmed_at__lte=deadline,
            confirmed_at__isnull=False).exclude(provider_reference='').exists() and
        not deliveries.filter(state__in=('pending', 'sending', 'failed', 'unknown')).exists())
    if not valid:
        raise ValueError('RENEWAL_NOTICE_REQUIRED')
    return True


def record_renewal_notice_blocked(subscription, cycle_index):
    """Call after the rejected debit transaction has rolled back."""
    from .commerce.models import BillingIssue
    return BillingIssue.objects.update_or_create(account=subscription.account,
        code='renewal_notice_blocked', object_type='renewal_cycle',
        object_id=uuid.uuid5(subscription.pk, str(cycle_index)),
        defaults={'last_seen_at': timezone.now()})[0]


def record_security_notice(user, event_key):
    """Target the verified identity's existing personal account, never a fixture."""
    from .models import BillingAccount
    account = BillingAccount.objects.filter(personal_owner=user, kind='personal').first()
    return BillingNotification.objects.get_or_create(account=account, recipient_user=user,
        source_event=f'security:{event_key}', defaults={'type': 'account_security', 'occurred_at': timezone.now()})[0]


def _destination(user):
    identities = LoginIdentity.objects.filter(user=user)
    email = identities.filter(method='email').first()
    google = identities.filter(method='google').exclude(provider_email='').first()
    phone = identities.filter(method='phone').first()
    if email or google:
        return 'email', email.identifier if email else google.provider_email
    if phone:
        return 'sms', phone.identifier
    raise ValueError('NOTICE_CONTACT_REQUIRED')


@transaction.atomic
def prepare_notice_delivery(notice, user):
    notice = BillingNotification.objects.select_for_update().get(pk=notice.pk)
    if not visible_notifications(user).filter(pk=notice.pk).exists():
        raise ValueError('FORBIDDEN')
    original = NotificationDelivery.objects.filter(notification=notice, user=user,
        state__in=_ATTEMPTED_NOTICE_STATES).order_by('pk').first()
    if original:
        return original
    channel, _ = _destination(user)
    return NotificationDelivery.objects.get_or_create(notification=notice, user=user, channel=channel)[0]


def _notice_configuration(channel):
    template = getattr(settings, 'NOTIFICATION_'+channel.upper()+'_TEMPLATE_ID', '')
    if (not getattr(settings, 'NOTIFICATION_DELIVERY_ENABLED', False) or
            not getattr(settings, 'NOTIFICATION_'+channel.upper()+'_TEMPLATE_APPROVED', False) or
            not isinstance(template, str) or not re.fullmatch(r'[1-9][0-9]*', template)):
        raise OSError('NOTICE_CHANNEL_UNAVAILABLE')
    origin = getattr(settings, 'NOTIFICATION_SITE_ORIGIN', '')
    try:
        parsed = urlsplit(origin)
        valid = (parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password and
            parsed.path in ('', '/') and not parsed.query and not parsed.fragment and parsed.port in (None, 443))
    except ValueError:
        valid = False
    if not valid:
        raise OSError('NOTICE_CHANNEL_UNAVAILABLE')
    return template, origin


def _transport_notice(row, identifier):
    notice = row.notification
    if notice.type not in NOTICE_TITLES:
        raise ValueError('NOTICE_TYPE_UNSUPPORTED')
    title = NOTICE_TITLES[notice.type]
    template, origin = _notice_configuration(row.channel)
    amount = f'{notice.amount_minor//100}.{notice.amount_minor%100:02d}' if notice.amount_minor is not None else ''
    when = notice.occurred_at+timedelta(days=1) if notice.type == 'renewal_reminder' else notice.occurred_at
    from zoneinfo import ZoneInfo
    date = when.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
    if row.channel == 'email':
        from .email import send_notice
        return send_notice(identifier, template, {'type': notice.type, 'title': title, 'amount': amount, 'date': date,
            'billing_url': origin.rstrip('/')+'/pages/billing.html#subscriptions',
            'notifications_url': origin.rstrip('/')+'/pages/notifications.html'})
    # The approved SMS BODY must contain the fixed cancellation URL; arbitrary
    # URL parameters are rejected by the shared SMS transport.
    from .sms import send_notice
    return send_notice(identifier, template, [title, amount, date])


def send_notice_delivery(delivery_id):
    """Commit before I/O; never resend an unknown or already attempted message."""
    if transaction.get_connection().in_atomic_block:
        raise ValueError('NOTICE_REQUIRES_AUTOCOMMIT')
    with transaction.atomic():
        notice_id = NotificationDelivery.objects.values_list('notification_id', flat=True).get(pk=delivery_id)
        BillingNotification.objects.select_for_update().get(pk=notice_id)
        row = NotificationDelivery.objects.select_for_update().select_related('notification', 'user').get(pk=delivery_id)
        if row.state != 'pending':
            return row.state
        original = NotificationDelivery.objects.filter(notification_id=notice_id, user=row.user,
            state__in=_ATTEMPTED_NOTICE_STATES).exclude(pk=row.pk).order_by('pk').first()
        if original:
            return original.state
        if not row.user.is_active or not visible_notifications(row.user).filter(pk=row.notification_id).exists():
            row.state = 'failed'
            row.save(update_fields=['state'])
            return row.state
        channel, identifier = _destination(row.user)
        if channel != row.channel:
            raise ValueError('NOTICE_CONTACT_CHANGED')
        if row.notification.type not in NOTICE_TITLES:
            raise ValueError('NOTICE_TYPE_UNSUPPORTED')
        _notice_configuration(channel)
        row.state, row.attempted_at = 'sending', timezone.now()
        row.save(update_fields=['state', 'attempted_at'])
    try:
        reference = _transport_notice(row, identifier)
    except OSError:
        # Timeout/response loss could follow a real send. Keep it query-only.
        reference = ''
        logging.getLogger('sage').warning('notice_dispatch_unknown delivery_id=%s', row.pk)
    with transaction.atomic():
        current = NotificationDelivery.objects.select_for_update().get(pk=row.pk)
        current.provider_reference = reference if isinstance(reference, str) and re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', reference) else ''
        current.state = 'sent' if current.provider_reference else 'unknown'
        current.confirmed_at = timezone.now() if current.provider_reference else None
        current.save(update_fields=['provider_reference', 'state', 'confirmed_at'])
        return current.state
