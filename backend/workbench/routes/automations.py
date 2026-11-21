"""app/automations and app/automation-runs."""
from django.db.models import F

from ..common import json_body, page, query, respond
from ..services import automations as service
from ..services.inbox import rate_limited, resolve


def list_automations(request, ctx):
    params = query(request, ('sort', 'offset'))
    sort = params.get('sort', 'created')
    rows = service.own_automations(ctx)
    if sort == 'created':
        rows = rows.order_by('created_at', 'id')
    elif sort == 'next_run':
        rows = rows.order_by(F('next_run_at').asc(nulls_last=True), 'id')
    else:
        raise ValueError('INVALID_REQUEST')
    return page(request, rows, service.automation_brief)


def create_automation(request, ctx):
    data = json_body(request, ('name', 'content_kind', 'schedule_kind', 'schedule_time', 'timezone'),
                     ('prompt', 'playbook', 'playbook_variables', 'workspace', 'lead_expert', 'schedule_weekday',
                      'schedule_monthday', 'created_from_task'))
    limited = rate_limited(request, 'automation-create', 30)
    if limited:
        return limited
    return respond({'automation': service.create_automation(ctx, data)}, 201)


def get_automation(request, ctx, id):
    return {'automation': service.automation_detail(resolve(service.own_automations(ctx), id))}


def patch_automation(request, ctx, id):
    data = json_body(request, ('version',), tuple(field for field in service.FIELDS if field != 'content_kind'))
    return {'automation': service.update_automation(ctx, id, data)}


def enable(request, ctx, id):
    json_body(request, ())
    return {'automation': service.enable_automation(ctx, id)}


def pause(request, ctx, id):
    return {'automation': service.pause_automation(ctx, id)}


def run_now(request, ctx, id):
    return respond(service.run_now(ctx, id, json_body(request, ('client_request_id',))), 202)


def automation_runs(request, ctx, id):
    query(request, ('offset',))
    automation = resolve(service.own_automations(ctx), id)
    return page(request, service.ordered_runs(service.own_runs(ctx).filter(automation=automation)), service.run_brief)


def list_runs(request, ctx):
    params = query(request, ('automation', 'offset'))
    rows = service.own_runs(ctx)
    if 'automation' in params:
        automation = service.own_automations(ctx).filter(public_id=params['automation']).first()
        if automation is None:
            raise ValueError('NOT_FOUND')
        rows = rows.filter(automation=automation)
    return page(request, service.ordered_runs(rows), service.run_brief)


def get_run(request, ctx, run_id):
    return {'run': service.run_detail(resolve(service.own_runs(ctx).select_related('task'), run_id))}


ROUTES = [
    ('GET', 'automations', list_automations),
    ('POST', 'automations', create_automation),
    ('GET', 'automations/{id}', get_automation),
    ('PATCH', 'automations/{id}', patch_automation),
    ('POST', 'automations/{id}/enable', enable),
    ('DELETE', 'automations/{id}/enable', pause),
    ('POST', 'automations/{id}/run-now', run_now),
    ('GET', 'automations/{id}/runs', automation_runs),
    ('GET', 'automation-runs', list_runs),
    ('GET', 'automation-runs/{run_id}', get_run),
]
