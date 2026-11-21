"""Account-scoped App settings: notification matrix, personal memories, persistent authorizations."""
from ..common import json_body
from ..services import account as service


def get_notifications(request, ctx):
    return service.notification_settings(ctx)


def put_notifications(request, ctx):
    return service.replace_notification_settings(ctx, json_body(request, ('items',))['items'])


def list_memories(request, ctx):
    return service.memory_list(request, ctx)


def patch_memory(request, ctx, id):
    return service.update_memory(request, ctx, id)


def delete_memory(request, ctx, id):
    return service.delete_memory(request, ctx, id)


def list_grants(request, ctx):
    return service.grant_list(request, ctx)


def revoke_grant(request, ctx, id):
    return service.revoke_grant(request, ctx, id)


ROUTES = [
    ('GET', 'settings/notifications', get_notifications),
    ('PUT', 'settings/notifications', put_notifications),
    ('GET', 'memories', list_memories),
    ('PATCH', 'memories/{id}', patch_memory),
    ('DELETE', 'memories/{id}', delete_memory),
    ('GET', 'grants', list_grants),
    ('DELETE', 'grants/{id}', revoke_grant),
]
