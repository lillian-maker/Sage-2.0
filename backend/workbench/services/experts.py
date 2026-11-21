"""Experts (E35/E36), teams (E37) and the collaboration network (29 屏)."""
import re
from datetime import timedelta

from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone
from .. import clock

from backend.commerce.models import DepartmentMember

from ..models import (AccountPolicy, Expert, ExpertCapability, ExpertTeam, ExtensionInstall, Task, TaskDelivery,
                      TaskParticipant, TaskStep)
from . import extensions as ext_service
from . import workspaces as ws

ASSISTANT_CODE = 'SAGE-ASSISTANT'
GROUPS = ('all', 'management', 'role', 'mine', 'shared')
SHARE_ORDER = ('only_me', 'department', 'account')
START_MODES = ('blank', 'describe', 'template')
STYLES = ('concise', 'detailed', 'conclusion_first')
UNCERTAINTY = ('ask_first', 'act_then_confirm')
WIZARD_STEPS = 6
MAX_SKILLS = 20
MAX_FORBIDDEN = 10
FORBIDDEN_LENGTH = 50
MAX_TAGS = 8
TAG_LENGTH = 16
TRY_ESTIMATED_CREDITS = 2  # 38 屏 "约消耗 2 Credits"
NETWORK_WINDOW_DAYS = 90
FREQUENT_HANDOFF = 10
SIMILAR_MIN_SCORE = 3
TEAM_MIN, TEAM_MAX = 2, 8
DEFAULT_STYLE = 'conclusion_first'
DEFAULT_UNCERTAINTY = 'ask_first'
_WORD = re.compile(r'\w')


# Visibility -------------------------------------------------------------------

def my_department_ids(ctx):
    return DepartmentMember.objects.filter(membership=ctx.member, ended_period__isnull=True).values('department_id')


def visible_experts(ctx):
    """System catalog + own drafts/published + published experts shared to me (Q-P2)."""
    shared = Q(share_scope='account') | Q(share_scope='department', shared_department_id__in=my_department_ids(ctx))
    return Expert.objects.filter(
        Q(account__isnull=True, status='published')
        | (Q(account=ctx.account, created_by=ctx.user) & ~Q(status='archived'))
        | (Q(account=ctx.account, status='published') & shared))


def listable_experts(ctx):
    return visible_experts(ctx).exclude(catalog_code=ASSISTANT_CODE)


def ordered(qs):
    return qs.order_by(Case(When(source='system', then=Value(0)), default=Value(1), output_field=IntegerField()),
                       Case(When(group='management', then=Value(0)), default=Value(1), output_field=IntegerField()),
                       'catalog_code', 'created_at', 'pk')


def get_expert(ctx, public_id):
    expert = visible_experts(ctx).filter(public_id=public_id).first()
    if expert is None:
        raise ValueError('NOT_FOUND')
    return expert


def editable_expert(ctx, public_id):
    expert = get_expert(ctx, public_id)
    if expert.source != 'custom' or expert.account_id != ctx.account.pk or expert.created_by_id != ctx.user.pk:
        raise ValueError('PERMISSION_DENIED')
    return expert


def group_q(ctx, group):
    if group == 'management':
        return Q(source='system', group='management')
    if group == 'role':
        return Q(source='system', group='role')
    if group == 'mine':
        return Q(source='custom', created_by=ctx.user)
    if group == 'shared':
        return Q(source='custom') & ~Q(created_by=ctx.user)
    return Q()


def search_q(text):
    return (Q(name__icontains=text) | Q(title__icontains=text) | Q(specialty__icontains=text)
            | Q(responsibilities__icontains=text) | Q(capability_tags__icontains=text)
            | Q(capabilities__extension__name__icontains=text))


# Serializers -------------------------------------------------------------------

def expert_item(ctx, expert):
    return {'id': expert.public_id, 'name': expert.name, 'title': expert.title, 'group': expert.group,
            'domain': expert.domain, 'specialty': expert.specialty, 'responsibilities': expert.responsibilities,
            'capability_tags': expert.capability_tags, 'avatar_ref': ws.avatar_url(expert.avatar_ref), 'source': expert.source,
            'status': expert.status, 'assignable': expert.status == 'published',
            'is_mine': expert.created_by_id == ctx.user.pk}


def expert_tasks(ctx, expert):
    involved = (Q(lead_expert=expert)
                | Q(pk__in=TaskParticipant.objects.filter(expert=expert, kind='expert').values('task_id'))
                | Q(pk__in=TaskStep.objects.filter(expert=expert).values('task_id')))
    return ws.visible_tasks(ctx).filter(involved).distinct()


def capabilities(ctx, expert):
    return ExpertCapability.objects.filter(account=ctx.account, expert=expert).select_related(
        'extension', 'extension__required_connector', 'required_for').order_by('pk')


def bigrams(text):
    chars = [c for c in (text or '') if _WORD.match(c)]
    return {a + b for a, b in zip(chars, chars[1:])}


def similar_experts(ctx, expert):
    """Title overlap against published experts (Q-R6 is open; this is the replaceable heuristic)."""
    if expert.status != 'draft':
        return []
    probe = bigrams(expert.title)
    if not probe:
        return []
    scored = []
    for other in listable_experts(ctx).filter(status='published').exclude(pk=expert.pk):
        score = len(probe & bigrams(other.title))
        if score >= SIMILAR_MIN_SCORE:
            scored.append((-score, other.catalog_code or '', other))
    scored.sort(key=lambda item: item[:2])
    return [{'id': o.public_id, 'name': o.name, 'specialty': o.specialty or o.title} for _, _, o in scored[:3]]


def _required_connector(extension, pre):
    required = extension.required_connector
    if required is None:
        return None
    return {'id': required.public_id, 'name': required.name, 'connection_state': pre.connection_state(required)}


def expert_detail(ctx, expert):
    pre = ext_service.Preload(ctx)
    data = expert_item(ctx, expert)
    skills, tools = [], []
    for row in capabilities(ctx, expert):
        extension = row.extension
        install = pre.installs.get(extension.pk)
        if extension.kind == 'skill':
            skills.append({'extension_id': extension.public_id, 'name': extension.name,
                           'publisher_tier': extension.publisher_tier,
                           'installed_version': install.installed_version if install else None,
                           'latest_version': extension.latest_version,
                           'install_state': ext_service.install_state(install),
                           'required_connector': _required_connector(extension, pre)})
        else:
            tools.append({'extension_id': extension.public_id, 'name': extension.name, 'kind': extension.kind,
                          'access': row.access,
                          'required_for': ({'id': row.required_for.public_id, 'name': row.required_for.name}
                                           if row.required_for else None),
                          'connection_state': pre.connection_state(extension) if extension.kind == 'connector'
                          else ext_service.install_state(install),
                          'connection_id': (pre.connections[extension.pk].public_id
                                            if extension.pk in pre.connections else None)})
    tasks = expert_tasks(ctx, expert)
    recent = []
    # 28 屏的行要能覆盖“未关联工作区 · 主责”（每月扫描自动化）等代表性任务，取最近五条。
    for task in tasks.select_related('workspace').order_by('-last_activity_at')[:5]:
        recent.append({'id': task.public_id, 'title': task.title,
                       'workspace': {'id': task.workspace.public_id, 'name': task.workspace.name}
                       if task.workspace else None,
                       'role': 'lead' if task.lead_expert_id == expert.pk else 'collaborator',
                       'last_activity_at': task.last_activity_at})
    template = expert.template_from
    data.update(
        stats={'skill_count': len(skills), 'history_task_count': tasks.count()},
        skills=skills, tools=tools, recent_tasks=recent,
        status=expert.status, wizard_step=expert.wizard_step, start_mode=expert.start_mode,
        start_description=expert.start_description,
        template_from={'id': template.public_id, 'name': template.name} if template else None,
        communication_style=expert.communication_style, uncertainty_policy=expert.uncertainty_policy,
        forbidden_rules=expert.forbidden_rules,
        knowledge_bases=[{'id': kb.public_id, 'name': kb.name} for kb in expert.knowledge_bases.order_by('pk')],
        similar_experts=similar_experts(ctx, expert), try_estimated_credits=TRY_ESTIMATED_CREDITS,
        catalog_code=expert.catalog_code, version_label=expert.version_label, share_scope=expert.share_scope,
        shared_department=({'id': str(expert.shared_department_id), 'name': expert.shared_department.name}
                           if expert.shared_department_id else None),
        version=expert.version, updated_at=expert.updated_at,
        reports_to=ws.expert_ref(expert.reports_to),
        can_edit=expert.source == 'custom' and expert.created_by_id == ctx.user.pk,
    )
    return data


def counts(ctx):
    qs = listable_experts(ctx)
    return {group: qs.filter(group_q(ctx, group)).count() for group in GROUPS}


def domain_counts(ctx):
    rows = {}
    for domain in listable_experts(ctx).exclude(domain='').values_list('domain', flat=True):
        rows[domain] = rows.get(domain, 0) + 1
    first_seen = list(ordered(listable_experts(ctx).exclude(domain='')).values_list('domain', flat=True))
    order = list(dict.fromkeys(first_seen))
    return [{'domain': domain, 'count': rows[domain]} for domain in order]


def assistant(ctx):
    from ...models import AccountProfile
    row = Expert.objects.filter(catalog_code=ASSISTANT_CODE, account__isnull=True, status='published').first()
    if row is None:
        return None
    profile = AccountProfile.objects.filter(user=ctx.user).only('assistant_name').first()
    data = expert_item(ctx, row)
    if profile and profile.assistant_name:
        data['name'] = profile.assistant_name
    return data


# Drafts --------------------------------------------------------------------------

def unique_name(ctx, base, exclude=None):
    base = (base or '新专家').strip()[:16] or '新专家'
    taken = set(Expert.objects.filter(account=ctx.account).exclude(status='archived')
                .exclude(pk=getattr(exclude, 'pk', None)).values_list('name', flat=True))
    if base not in taken:
        return base
    for n in range(2, 1000):
        candidate = '%s%d' % (base[:16 - len(str(n))], n)
        if candidate not in taken:
            return candidate
    raise ValueError('VALIDATION_FAILED')


def clean_tags(value):
    if not isinstance(value, list) or len(value) > MAX_TAGS:
        raise ValueError('VALIDATION_FAILED')
    return [ws.text_field(tag, TAG_LENGTH) for tag in value]


def create_draft(ctx, mode, description, template_id):
    fields = {'title': '', 'responsibilities': '', 'capability_tags': [], 'name': None}
    template = None
    copied = []
    if mode == 'describe':
        if template_id is not None:
            raise ValueError('VALIDATION_FAILED')
        description = ws.text_field(description, 500)
        generator = ext_service.adapter('WORKBENCH_EXPERT_DRAFT_GENERATOR')
        if generator is None:
            raise ValueError('SOURCE_UNAVAILABLE')
        try:
            generated = generator(description=description, account=ctx.account, user=ctx.user) or {}
        except ConnectionError as exc:
            raise ValueError('SOURCE_UNAVAILABLE') from exc
        fields.update(name=generated.get('name'), title=(generated.get('title') or '')[:40],
                      responsibilities=generated.get('responsibilities') or '',
                      capability_tags=[str(t)[:TAG_LENGTH] for t in generated.get('capability_tags') or []][:MAX_TAGS])
    elif mode == 'template':
        if description is not None:
            raise ValueError('VALIDATION_FAILED')
        if not isinstance(template_id, str):
            raise ValueError('VALIDATION_FAILED')
        template = listable_experts(ctx).filter(public_id=template_id, status='published').first()
        if template is None:
            raise ValueError('NOT_FOUND')
        fields.update(name=template.name + '副本', title=template.title,
                      responsibilities=template.responsibilities, capability_tags=list(template.capability_tags))
        copied = list(capabilities(ctx, template))
    else:
        if description is not None or template_id is not None:
            raise ValueError('VALIDATION_FAILED')
    expert = Expert.objects.create(
        account=ctx.account, source='custom', group='role', name=unique_name(ctx, fields['name']),
        title=fields['title'], domain=template.domain if template else '',
        specialty=template.specialty if template else None, responsibilities=fields['responsibilities'],
        capability_tags=fields['capability_tags'], version_label='', status='draft', share_scope='only_me',
        created_by=ctx.user, template_from=template, start_mode=mode,
        start_description=description if mode == 'describe' else None,
        communication_style=template.communication_style if template else DEFAULT_STYLE,
        uncertainty_policy=template.uncertainty_policy if template else DEFAULT_UNCERTAINTY,
        forbidden_rules=list(template.forbidden_rules) if template else [], wizard_step=1)
    for row in copied:
        ExpertCapability.objects.create(account=ctx.account, expert=expert, extension=row.extension,
                                        access=row.access, required_for=row.required_for)
    return expert


def set_skills(ctx, expert, skill_ids):
    if not isinstance(skill_ids, list) or len(skill_ids) > MAX_SKILLS or len(set(map(str, skill_ids))) != len(skill_ids):
        raise ValueError('VALIDATION_FAILED')
    if not all(isinstance(value, str) for value in skill_ids):
        raise ValueError('VALIDATION_FAILED')
    found = {e.public_id: e for e in ext_service.catalog(ctx).filter(public_id__in=skill_ids)
             .select_related('required_connector')}
    if len(found) != len(skill_ids) or any(e.kind != 'skill' for e in found.values()):
        raise ValueError('VALIDATION_FAILED')
    installed = set(ExtensionInstall.objects.filter(account=ctx.account, extension__in=found.values(), enabled=True,
                                                    pending_permission_review=False).values_list('extension_id', flat=True))
    skills = [found[public_id] for public_id in skill_ids]
    if any(skill.pk not in installed for skill in skills):
        raise ValueError('EXTENSION_NOT_ALLOWED')
    ExpertCapability.objects.filter(account=ctx.account, expert=expert).delete()
    for skill in skills:
        ExpertCapability.objects.create(account=ctx.account, expert=expert, extension=skill)
    # Tools follow from the chosen skills (36 屏 "所选技能需要的工具与连接").
    for skill in skills:
        connector = skill.required_connector
        if connector and not ExpertCapability.objects.filter(expert=expert, extension=connector).exists():
            ExpertCapability.objects.create(account=ctx.account, expert=expert, extension=connector,
                                            required_for=skill)


def set_knowledge_bases(ctx, expert, kb_ids):
    from .knowledge import citable_bases
    if not isinstance(kb_ids, list) or not all(isinstance(v, str) for v in kb_ids) or len(set(kb_ids)) != len(kb_ids):
        raise ValueError('VALIDATION_FAILED')
    bases = list(citable_bases(ctx).filter(public_id__in=kb_ids))
    if len(bases) != len(kb_ids):
        raise ValueError('VALIDATION_FAILED')
    expert.knowledge_bases.set(bases)


def check_share_scope(ctx, scope, department_id):
    if scope not in SHARE_ORDER:
        raise ValueError('VALIDATION_FAILED')
    policy = AccountPolicy.objects.filter(account=ctx.account).first()
    limit = policy.expert_share_max_scope if policy else ('account' if ctx.account.kind == 'business' else 'only_me')
    if SHARE_ORDER.index(scope) > SHARE_ORDER.index(limit):
        raise ValueError('PERMISSION_DENIED')
    if scope != 'department':
        if department_id is not None:
            raise ValueError('VALIDATION_FAILED')
        return None
    from .knowledge import department_of
    department = department_of(ctx, department_id)
    if department is None:
        raise ValueError('VALIDATION_FAILED')
    return department


PATCH_FIELDS = ('wizard_step', 'name', 'title', 'responsibilities', 'capability_tags', 'communication_style',
                'uncertainty_policy', 'forbidden_rules', 'skill_ids', 'knowledge_base_ids', 'share_scope',
                'shared_department_id', 'domain', 'specialty')


def update_draft(ctx, expert, data):
    if expert.status != 'draft':
        raise ValueError('STATE_CONFLICT')
    if data['version'] != expert.version:
        raise ValueError('VERSION_CONFLICT')
    if 'wizard_step' in data:
        step = data['wizard_step']
        if not isinstance(step, int) or isinstance(step, bool) or not 1 <= step <= WIZARD_STEPS:
            raise ValueError('VALIDATION_FAILED')
        expert.wizard_step = step
    if 'name' in data:
        name = ws.text_field(data['name'], 16)
        if Expert.objects.filter(account=ctx.account, name=name).exclude(status='archived').exclude(pk=expert.pk).exists():
            raise ValueError('VALIDATION_FAILED')
        expert.name = name
    if 'title' in data:
        expert.title = ws.text_field(data['title'], 40)
    if 'responsibilities' in data:
        expert.responsibilities = ws.text_field(data['responsibilities'], 500)
    if 'domain' in data:
        expert.domain = ws.text_field(data['domain'], 24, required=False) or ''
    if 'specialty' in data:
        expert.specialty = ws.text_field(data['specialty'], 40, required=False)
    if 'capability_tags' in data:
        expert.capability_tags = clean_tags(data['capability_tags'])
    if 'communication_style' in data:
        if data['communication_style'] not in STYLES:
            raise ValueError('VALIDATION_FAILED')
        expert.communication_style = data['communication_style']
    if 'uncertainty_policy' in data:
        if data['uncertainty_policy'] not in UNCERTAINTY:
            raise ValueError('VALIDATION_FAILED')
        expert.uncertainty_policy = data['uncertainty_policy']
    if 'forbidden_rules' in data:
        rules = data['forbidden_rules']
        if not isinstance(rules, list) or len(rules) > MAX_FORBIDDEN:
            raise ValueError('VALIDATION_FAILED')
        expert.forbidden_rules = [ws.text_field(rule, FORBIDDEN_LENGTH) for rule in rules]
    if 'share_scope' in data or 'shared_department_id' in data:
        scope = data.get('share_scope', expert.share_scope)
        department = check_share_scope(ctx, scope, data.get('shared_department_id'))
        expert.share_scope = scope
        expert.shared_department = department
    if 'skill_ids' in data:
        set_skills(ctx, expert, data['skill_ids'])
    if 'knowledge_base_ids' in data:
        set_knowledge_bases(ctx, expert, data['knowledge_base_ids'])
    expert.version += 1
    expert.save()
    return expert


def publish(ctx, expert, version):
    if expert.status != 'draft':
        raise ValueError('STATE_CONFLICT')
    if version != expert.version:
        raise ValueError('VERSION_CONFLICT')
    if not (expert.name.strip() and expert.title.strip() and expert.responsibilities.strip()):
        raise ValueError('VALIDATION_FAILED')
    check_share_scope(ctx, expert.share_scope, str(expert.shared_department_id) if expert.shared_department_id else None)
    expert.status = 'published'
    expert.wizard_step = None
    expert.version_label = 'v1.0'
    expert.version += 1
    expert.save()
    return expert


# Network ----------------------------------------------------------------------------

def network(ctx, expert):
    """Peers from shared tasks in the last 90 days; relation and rates come from steps and deliveries."""
    since = clock.now() - timedelta(days=NETWORK_WINDOW_DAYS)
    tasks = list(expert_tasks(ctx, expert).filter(last_activity_at__gte=since).values_list('pk', flat=True))
    steps = TaskStep.objects.filter(task_id__in=tasks, expert__isnull=False).values_list(
        'task_id', 'expert_id', 'order', 'stage')
    center_first, peer_rows = {}, {}
    for task_id, expert_id, order, stage in steps:
        if expert_id == expert.pk:
            center_first[task_id] = min(order, center_first.get(task_id, order))
        else:
            peer_rows.setdefault(expert_id, []).append((task_id, order, stage))
    deliveries = {}
    for task_id, decision in TaskDelivery.objects.filter(task_id__in=tasks).exclude(decision='pending').values_list(
            'task_id', 'decision'):
        deliveries.setdefault(task_id, []).append(decision)
    peers = []
    experts = Expert.objects.in_bulk(list(peer_rows) + ([expert.reports_to_id] if expert.reports_to_id else []))
    for peer_id, rows in peer_rows.items():
        task_ids = {task_id for task_id, _, _ in rows}
        before = sum(1 for task_id, order, _ in rows if order < center_first.get(task_id, order + 1))
        reviews = sum(1 for _, _, stage in rows if stage == 'review')
        count = len(task_ids)
        if peer_id == expert.reports_to_id:
            relation = 'reports_to'
        elif reviews * 2 > len(rows):
            relation = 'reviews_me'
        elif before * 2 > len(rows):
            relation = 'upstream'
        else:
            relation = 'handoff' if count >= FREQUENT_HANDOFF else 'downstream'
        decisions = [d for task_id in task_ids for d in deliveries.get(task_id, [])]
        accepted = decisions.count('accepted')
        peers.append({'expert': _peer_ref(experts[peer_id]), 'relation': relation, 'collaboration_count': count,
                      'acceptance_rate': round(accepted * 100 / len(decisions)) if decisions else None,
                      'return_count': decisions.count('returned'),
                      'summary': _summary(relation, experts[peer_id])})
    if expert.reports_to_id and expert.reports_to_id not in peer_rows:
        peers.append({'expert': _peer_ref(experts[expert.reports_to_id]), 'relation': 'reports_to',
                      'collaboration_count': 0, 'acceptance_rate': None, 'return_count': 0,
                      'summary': _summary('reports_to', experts[expert.reports_to_id])})
    peers.sort(key=lambda p: (-p['collaboration_count'], p['expert']['name']))
    center = _peer_ref(expert)
    return {'center': center, 'window_days': NETWORK_WINDOW_DAYS, 'peers': peers}


def _peer_ref(expert):
    return {'id': expert.public_id, 'name': expert.name, 'title': expert.title,
            'specialty': expert.specialty or expert.title, 'avatar_ref': ws.avatar_url(expert.avatar_ref)}


def _summary(relation, peer):
    if peer.collaboration_note:
        return peer.collaboration_note
    label = peer.specialty or peer.title
    return {'reports_to': '向%s汇报' % peer.name,
            'reviews_me': '复核我产出的内容',
            'upstream': '给我提供%s相关的资料' % label,
            'handoff': '我把%s相关的工作交给他' % label,
            'downstream': '我的产出交给他做%s' % label}[relation]


# Teams --------------------------------------------------------------------------------

def visible_teams(ctx):
    return ExpertTeam.objects.filter(
        Q(account__isnull=True, source='template')
        | Q(account=ctx.account, visibility='account') | Q(account=ctx.account, created_by=ctx.user))


def team_dict(ctx, team):
    members = visible_experts(ctx).filter(pk__in=team.members.values('pk'))
    members = sorted(members, key=lambda e: (e.pk != team.lead_expert_id, e.catalog_code or '', e.pk))
    return {'id': team.public_id, 'name': team.name, 'description': team.description, 'source': team.source,
            'visibility': team.visibility, 'is_mine': team.created_by_id == ctx.user.pk,
            'lead_expert': {'id': team.lead_expert.public_id, 'name': team.lead_expert.name,
                            'avatar_ref': ws.avatar_url(team.lead_expert.avatar_ref)},
            'members': [{'id': e.public_id, 'name': e.name, 'avatar_ref': ws.avatar_url(e.avatar_ref)} for e in members],
            'usage_count': Task.objects.filter(account=ctx.account, team=team).count(),
            'created_at': team.created_at}


def create_team(ctx, data):
    name = ws.text_field(data['name'], 40)
    description = ws.text_field(data.get('description'), 200, required=False)
    ids = data.get('member_ids', data.get('member_expert_ids'))
    if not isinstance(ids, list) or not all(isinstance(v, str) for v in ids) or len(set(ids)) != len(ids):
        raise ValueError('VALIDATION_FAILED')
    if not TEAM_MIN <= len(ids) <= TEAM_MAX:
        raise ValueError('VALIDATION_FAILED')
    if data['visibility'] not in ('only_me', 'account'):
        raise ValueError('VALIDATION_FAILED')
    if data['lead_expert_id'] not in ids:
        raise ValueError('VALIDATION_FAILED')
    members = list(listable_experts(ctx).filter(public_id__in=ids, status='published'))
    if len(members) != len(ids):
        raise ValueError('VALIDATION_FAILED')
    if ExpertTeam.objects.filter(account=ctx.account, name=name).exists():
        raise ValueError('VALIDATION_FAILED')
    lead = next(e for e in members if e.public_id == data['lead_expert_id'])
    team = ExpertTeam.objects.create(account=ctx.account, name=name, description=description, lead_expert=lead,
                                     source='custom', visibility=data['visibility'], created_by=ctx.user)
    team.members.set(members)
    return team

