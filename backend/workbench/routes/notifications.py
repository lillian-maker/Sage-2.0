"""app/notifications: notification centre list and read marks."""
from ..common import json_body, offset, query
from ..services import notifications as service


def list_notifications(request, ctx):
    params = query(request, ('category', 'unread', 'offset'))
    category = params.get('category', 'all')
    if category not in service.CATEGORIES or params.get('unread', '0') not in ('0', '1'):
        raise ValueError('INVALID_REQUEST')
    return service.notification_list(ctx, category, params.get('unread') == '1', offset(request))


def mark_read(request, ctx):
    return service.mark_notifications_read(ctx, json_body(request, (), ('all', 'app_ids', 'billing_ids')))


ROUTES = [
    ('GET', 'notifications', list_notifications),
    ('POST', 'notifications/read', mark_read),
]
