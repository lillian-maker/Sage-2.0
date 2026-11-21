"""app/inbox: items, sources, handover and the "需要我决定" aggregate."""
from ..common import json_body, page, query
from ..services import inbox as service
from ..services.notifications import app_item

ITEM_GROUPS = service.LIST_GROUPS + ('ignored',)
CHANNELS = ('external', 'decisions', 'mentions')


def list_decisions(request, ctx):
    params = query(request, ('type', 'offset', 'count_only'))
    if params.get('count_only') not in (None, '1'):
        raise ValueError('INVALID_REQUEST')
    kind = params.get('type')
    if kind is not None and kind not in service.DECISION_TYPES:
        raise ValueError('INVALID_REQUEST')
    counts = service.inbox_counts(ctx)
    if params.get('count_only') == '1':
        return {'counts': counts, 'badge_count': counts['all']}
    entries = service.decision_entries(ctx)
    if kind:
        entries = [entry for entry in entries if entry['type'] == kind]
    data = page(request, entries, lambda entry: entry)
    data.update(counts=counts, badge_count=counts['all'])
    return data


def list_items(request, ctx):
    params = query(request, ('group', 'source', 'channel', 'q', 'sort', 'offset'))
    channel = params.get('channel', 'external')
    if params.get('sort', 'received') != 'received' or channel not in CHANNELS:
        raise ValueError('INVALID_REQUEST')
    counts = service.inbox_counts(ctx)
    if channel != 'external':
        # The decisions and mentions tabs share this list; their rows keep their own shapes.
        if set(params) - {'channel', 'offset'}:
            raise ValueError('INVALID_REQUEST')
        if channel == 'decisions':
            data = page(request, service.decision_entries(ctx), lambda entry: entry)
        else:
            data = page(request, service.mention_notifications(ctx), app_item)
        data['counts'] = counts
        return data
    rows = service.own_items(ctx).select_related('source', 'workspace', 'task').prefetch_related('suggested_experts')
    group = params.get('group')
    if group is None:
        rows = rows.exclude(group='ignored')
    elif group in ITEM_GROUPS:
        rows = rows.filter(group=group)
    else:
        raise ValueError('INVALID_REQUEST')
    source = params.get('source')
    if source is not None:
        if source not in service.SOURCE_KINDS:
            raise ValueError('INVALID_REQUEST')
        rows = rows.filter(source__kind=source)
    if 'q' in params:
        rows = rows.filter(title__icontains=service.search_text(params['q']))
    data = page(request, rows.order_by('-received_at', '-id'), service.item_brief)
    data['counts'] = counts
    return data


def get_item(request, ctx, id):
    service.sync_done(ctx)
    item = service.resolve(service.own_items(ctx).select_related('source', 'workspace', 'task'), id)
    return {'item': service.item_detail(item)}


def hand_off(request, ctx, id):
    data = json_body(request, ('client_request_id',), ('lead_expert_id', 'collaborator_expert_ids'))
    limited = service.rate_limited(request, 'inbox-hand-off', 30)
    if limited:
        return limited
    return service.hand_off(ctx, id, data)


def ignore(request, ctx, id):
    json_body(request, ())
    return service.ignore_item(ctx, id)


def list_sources(request, ctx):
    query(request, ())
    rows = service.own_sources(ctx).prefetch_related('responsible_experts').order_by('created_at', 'id')
    return {'items': [service.source_brief(source) for source in rows]}


def get_source(request, ctx, id):
    return {'source': service.source_detail(service.resolve(service.own_sources(ctx), id))}


def patch_source(request, ctx, id):
    data = json_body(request, ('version',), ('watched_groups', 'keywords', 'sender_filter', 'initiative',
                                             'responsible_expert_ids'))
    return service.update_source(ctx, id, data)


def disconnect_source(request, ctx, id):
    json_body(request, ())
    return service.disconnect_source(ctx, id)


def list_handover(request, ctx):
    query(request, ())
    return {'groups': service.handover_groups(ctx)}


def accept_handoff(request, ctx, id):
    return service.accept_handoff(ctx, id, json_body(request, ('client_request_id',)))


def reactivate_handoff(request, ctx, id):
    return service.reactivate_handoff(ctx, id, json_body(request, ('client_request_id',)))


ROUTES = [
    ('GET', 'inbox/decisions', list_decisions),
    ('GET', 'inbox/items', list_items),
    ('GET', 'inbox/items/{id}', get_item),
    ('POST', 'inbox/items/{id}/hand-off', hand_off),
    ('POST', 'inbox/items/{id}/ignore', ignore),
    ('GET', 'inbox/sources', list_sources),
    ('GET', 'inbox/sources/{id}', get_source),
    ('PATCH', 'inbox/sources/{id}', patch_source),
    ('POST', 'inbox/sources/{id}/disconnect', disconnect_source),
    ('GET', 'inbox/handover', list_handover),
    ('POST', 'inbox/handover/{id}/accept', accept_handoff),
    ('POST', 'inbox/handover/{id}/reactivate', reactivate_handoff),
]
