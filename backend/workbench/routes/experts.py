"""Experts, expert creation wizard and teams (27-38 屏)."""
from django.db import transaction

from .. import common
from ..services import experts as svc
from ..services import extensions as ext_service
from ..services import inbox as inbox_service
from ..services import workspaces as ws

SEARCH_LENGTH = 100


def _version(data):
    value = data.get('version')
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError('VALIDATION_FAILED')
    return value


def list_experts(request, ctx):
    params = common.query(request, ('group', 'domain', 'q', 'offset'))
    group = params.get('group') or 'all'
    if group not in svc.GROUPS:
        raise ValueError('VALIDATION_FAILED')
    qs = svc.listable_experts(ctx).filter(svc.group_q(ctx, group))
    if params.get('domain'):
        qs = qs.filter(domain=params['domain'])
    text = (params.get('q') or '').strip()
    if len(text) > SEARCH_LENGTH:
        raise ValueError('VALIDATION_FAILED')
    if text:
        qs = svc.Expert.objects.filter(pk__in=qs.filter(svc.search_q(text)).values('pk'))
    data = common.page(request, svc.ordered(qs), lambda e: svc.expert_item(ctx, e))
    data.update(counts=svc.counts(ctx), domain_counts=svc.domain_counts(ctx), assistant=svc.assistant(ctx))
    return data


def expert_detail(request, ctx, id):
    common.query(request)
    return svc.expert_detail(ctx, svc.get_expert(ctx, id))


def expert_network(request, ctx, id):
    common.query(request)
    return svc.network(ctx, svc.get_expert(ctx, id))


def create_expert(request, ctx):
    data = common.json_body(request, ('start_mode',), ('start_description', 'template_from'))
    limited = ws.rate_limited(request, ctx, 'expert_create')
    if limited:
        return limited
    if data['start_mode'] not in svc.START_MODES:
        raise ValueError('VALIDATION_FAILED')
    with transaction.atomic():
        expert = svc.create_draft(ctx, data['start_mode'], data.get('start_description'), data.get('template_from'))
    return common.respond({'expert': svc.expert_detail(ctx, expert)}, 201)


def update_expert(request, ctx, id):
    data = common.json_body(request, ('version',), svc.PATCH_FIELDS)
    _version(data)
    with transaction.atomic():
        expert = svc.editable_expert(ctx, id)
        expert = svc.Expert.objects.select_for_update().get(pk=expert.pk)
        svc.update_draft(ctx, expert, data)
    return {'expert': svc.expert_detail(ctx, expert)}


def publish_expert(request, ctx, id):
    data = common.json_body(request, ('version',))
    version = _version(data)
    with transaction.atomic():
        expert = svc.editable_expert(ctx, id)
        expert = svc.Expert.objects.select_for_update().get(pk=expert.pk)
        svc.publish(ctx, expert, version)
    return {'expert': svc.expert_detail(ctx, expert)}


def regenerate_avatar(request, ctx, id):
    # Uploading a replacement waits for the upload protocol (Q-D1); only regeneration is served.
    common.json_body(request, ())
    expert = svc.editable_expert(ctx, id)
    limited = ws.rate_limited(request, ctx, 'expert_avatar', 10)
    if limited:
        return limited
    generator = ext_service.adapter('WORKBENCH_EXPERT_AVATAR_GENERATOR')
    if generator is None:
        raise ValueError('SOURCE_UNAVAILABLE')
    try:
        ref = generator(expert=expert, user=ctx.user)
    except ConnectionError as exc:
        raise ValueError('SOURCE_UNAVAILABLE') from exc
    if not isinstance(ref, str) or not ref or len(ref) > 200:
        raise ValueError('SOURCE_UNAVAILABLE')
    expert.avatar_ref = ref
    expert.save(update_fields=['avatar_ref', 'updated_at'])
    return {'avatar_ref': ref}


def try_expert(request, ctx, id):
    data = common.json_body(request, ('prompt',))
    expert = svc.get_expert(ctx, id)
    limited = ws.rate_limited(request, ctx, 'expert_try', 10)
    if limited:
        return limited
    prompt = ws.text_field(data['prompt'], 500)
    inbox_service.require_credits(ctx.account)
    runner = ext_service.adapter('WORKBENCH_EXPERT_TRY_RUNNER')
    if runner is None:
        raise ValueError('SOURCE_UNAVAILABLE')
    try:
        result = runner(expert=expert, prompt=prompt, user=ctx.user, account=ctx.account) or {}
    except ConnectionError as exc:
        raise ValueError('SOURCE_UNAVAILABLE') from exc
    if not isinstance(result.get('reply'), str):
        raise ValueError('SOURCE_UNAVAILABLE')
    return {'reply': result['reply'], 'credits_used': result.get('credits_used', 0)}


def list_teams(request, ctx):
    common.query(request, ('offset',))
    teams = sorted(svc.visible_teams(ctx).select_related('lead_expert'),
                   key=lambda t: (t.source != 'template', t.created_at, t.pk))
    return common.page(request, teams, lambda t: svc.team_dict(ctx, t))


def create_team(request, ctx):
    data = common.json_body(request, ('name', 'lead_expert_id', 'visibility'),
                            ('description', 'member_ids', 'member_expert_ids'))
    if ('member_ids' in data) == ('member_expert_ids' in data):
        raise ValueError('VALIDATION_FAILED')
    with transaction.atomic():
        team = svc.create_team(ctx, data)
    return common.respond({'team': svc.team_dict(ctx, team)}, 201)


ROUTES = [
    ('GET', 'experts', list_experts),
    ('POST', 'experts', create_expert),
    ('GET', 'experts/{id}', expert_detail),
    ('PATCH', 'experts/{id}', update_expert),
    ('GET', 'experts/{id}/network', expert_network),
    ('POST', 'experts/{id}/publish', publish_expert),
    ('POST', 'experts/{id}/avatar', regenerate_avatar),
    ('POST', 'experts/{id}/try', try_expert),
    ('GET', 'teams', list_teams),
    ('POST', 'teams', create_team),
]
