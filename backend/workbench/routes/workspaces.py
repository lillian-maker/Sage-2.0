"""app/workspaces (list, detail, members, rules) and app/playbooks (backend-plan 2.1-2.3; screens 16-19, 22, 24)."""
import re

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from .. import clock

from backend.models import Membership

from ..common import iso, json_body, page, query, respond
from ..ids import PUBLIC_ID_PATTERN
from ..models import (AcceptanceCriterion, Artifact, Playbook, PlaybookStep, Task, TaskStep, TaskThread,
                      TaskUserState, Workspace, WorkspaceMember, WorkspaceRuleVersion)
from ..services import workspaces as ws

CREATED_FROM = ('blank', 'copy_rules', 'playbook')
# Guests are not offered in the first release (Q-P1 C); only account members can be added.
INVITE_ROLES = ('owner', 'editor', 'viewer')
ACCESS_SUMMARY = {'owner': '全部', 'editor': '任务、资料', 'viewer': '只能查看成果与资料', 'guest': '只能查看被分享的任务'}
RECENT_TASKS = 3
PREVIEW_MEMBERS = 5
PLAYBOOK_VISIBILITY = ('only_me', 'workspace', 'account')
PLAYBOOK_MAX_STEPS = 50
PLAYBOOK_MAX_VARIABLES = 10
VARIABLE_NAME = re.compile(r'[^{}\s][^{}]{0,19}')
STEP_KEYS = {'source_step', 'order', 'title'}


def integer(value, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError('VALIDATION_FAILED')
    return value


def rule_ref(version):
    return {'number': version.number} if version else None


# Workspaces ---------------------------------------------------------------------

def member_sort_key(row):
    return (ws.ROLE_ORDER.index(row.role), row.accepted_at or row.created_at, row.pk)


def active_members(workspace):
    rows = WorkspaceMember.objects.filter(workspace=workspace, state='active').select_related('user')
    return sorted(rows, key=member_sort_key)


def workspace_summary(ctx, workspace, member):
    tasks = list(ws.workspace_tasks(ctx, workspace, member).order_by('-last_activity_at', '-pk'))
    unread = ws.unread_map(ctx.user, tasks)
    return tasks, unread


def list_workspaces(request, ctx):
    query(request, ('offset',))
    members = {row.workspace_id: row for row in ws.member_rows(ctx)}
    workspaces = list(ws.visible_workspaces(ctx).select_related('current_rule_version'))
    rows = []
    for workspace in workspaces:
        member = members[workspace.pk]
        tasks, unread = workspace_summary(ctx, workspace, member)
        last = tasks[0].last_activity_at if tasks else workspace.created_at
        rows.append((workspace, member, tasks, unread, last))
    pinned = sorted((row for row in rows if row[1].pinned_at), key=lambda row: row[1].pinned_at, reverse=True)
    others = sorted((row for row in rows if not row[1].pinned_at), key=lambda row: row[4], reverse=True)

    def serialize(row):
        workspace, member, tasks, unread, last = row
        return {
            'id': workspace.public_id, 'name': workspace.name, 'description': workspace.description,
            'role': member.role, 'pinned_at': iso(member.pinned_at), 'last_activity_at': iso(last),
            'task_count': len(tasks), 'unread_count': sum(1 for task in tasks if unread[task.pk]),
            'current_rule_version': rule_ref(workspace.current_rule_version),
            'recent_tasks': [{'id': task.public_id, 'title': task.title, 'status': task.status,
                              'last_activity_at': iso(task.last_activity_at), 'unread': unread[task.pk]}
                             for task in tasks[:RECENT_TASKS]],
        }
    return page(request, pinned + others, serialize)


def workspace_detail(ctx, workspace, member):
    tasks, unread = workspace_summary(ctx, workspace, member)
    preview = [ws.person(row.user) for row in active_members(workspace)[:PREVIEW_MEMBERS]]
    return {
        'id': workspace.public_id, 'name': workspace.name, 'description': workspace.description,
        'account_kind': ctx.account.kind, 'role': member.role, 'pinned_at': iso(member.pinned_at),
        'created_from': workspace.created_from, 'created_at': iso(workspace.created_at),
        'counts': {'tasks': len(tasks), 'documents': ws.workspace_document_count(workspace),
                   'members': ws.active_member_count(workspace)},
        'unread_count': sum(1 for task in tasks if unread[task.pk]),
        'current_rule_version': rule_ref(workspace.current_rule_version),
        'members_preview': [{'display_name': row['display_name'], 'avatar_name': row['avatar_name']}
                            for row in preview],
        'can_manage': member.role == 'owner',
        'version': workspace.version,
    }


def get_workspace(request, ctx, id):
    query(request)
    workspace, member = ws.get_workspace(ctx, id)
    return workspace_detail(ctx, workspace, member)


def visible_playbooks(ctx):
    workspace_ids = ws.member_rows(ctx).exclude(role='guest').values('workspace_id')
    return Playbook.objects.filter(account=ctx.account).filter(
        Q(created_by=ctx.user) | Q(visibility='account')
        | Q(visibility='workspace', source_task__workspace_id__in=workspace_ids))


def create_workspace(request, ctx):
    data = json_body(request, ('name', 'created_from'), ('description', 'source_workspace', 'source_playbook'))
    name = ws.text_field(data['name'], 40)
    description = ws.text_field(data.get('description'), 200, required=False) if data.get('description') else None
    created_from = data['created_from']
    if created_from not in CREATED_FROM:
        raise ValueError('VALIDATION_FAILED')
    sources = {'source_workspace': data.get('source_workspace'), 'source_playbook': data.get('source_playbook')}
    needed = {'blank': None, 'copy_rules': 'source_workspace', 'playbook': 'source_playbook'}[created_from]
    for key, value in sources.items():
        if (key == needed) != (value is not None) or (value is not None and not isinstance(value, str)):
            raise ValueError('VALIDATION_FAILED')
    source_workspace = source_playbook = None
    if created_from == 'copy_rules':
        source_workspace, source_member = ws.get_workspace(ctx, sources['source_workspace'])
        if source_member.role == 'guest':
            raise ValueError('NOT_FOUND')
    elif created_from == 'playbook':
        source_playbook = visible_playbooks(ctx).filter(public_id=sources['source_playbook']).first()
        if source_playbook is None:
            raise ValueError('NOT_FOUND')
    limited = ws.rate_limited(request, ctx, 'workspace-create', 20)
    if limited:
        return limited
    now = clock.now()
    first_task = None
    with transaction.atomic():
        workspace = Workspace.objects.create(account=ctx.account, name=name, description=description,
                                             created_from=created_from, source_workspace=source_workspace,
                                             source_playbook=source_playbook, created_by=ctx.user)
        WorkspaceMember.objects.create(account=ctx.account, workspace=workspace, user=ctx.user, role='owner',
                                       state='active', accepted_at=now)
        rules = source_workspace.current_rule_version if source_workspace else None
        if rules is not None:
            copy = WorkspaceRuleVersion.objects.create(
                account=ctx.account, workspace=workspace, number=1, rules=list(rules.rules), author=ctx.user,
                change_note=('复制自“%s”的规则 v%d' % (source_workspace.name, rules.number))[:200])
            workspace.current_rule_version = copy
            workspace.save(update_fields=['current_rule_version', 'updated_at'])
        if source_playbook is not None:
            variables = {item['name']: item['default'] for item in normalize_variables(source_playbook.variables)}
            first_task = Task.objects.create(
                account=ctx.account, workspace=workspace, title=source_playbook.name[:80], creator=ctx.member,
                owner=ctx.member, model_mode='auto', status='draft', source_playbook=source_playbook,
                playbook_variables=variables, rule_version=workspace.current_rule_version)
            TaskThread.objects.create(account=ctx.account, task=first_task, kind='main')
    return respond({'workspace': {'id': workspace.public_id, 'name': workspace.name, 'created_from': created_from,
                                  'current_rule_version': rule_ref(workspace.current_rule_version)},
                    'first_task': {'id': first_task.public_id} if first_task else None}, 201)


def patch_workspace(request, ctx, id):
    data = json_body(request, (), ('name', 'description', 'version'))
    workspace, member = ws.get_workspace(ctx, id)
    ws.require_owner(member)
    expected = data.get('version')
    if 'version' in data and type(expected) is not int:
        raise ValueError('VALIDATION_FAILED')
    changes = {}
    if 'name' in data:
        changes['name'] = ws.text_field(data['name'], 40)
    if 'description' in data:
        changes['description'] = (ws.text_field(data['description'], 200, required=False)
                                  if data['description'] else None)
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update().get(pk=workspace.pk)
        # version is optional so older clients keep working; when sent it must match (VERSION_CONFLICT).
        if 'version' in data and expected != workspace.version:
            raise ValueError('VERSION_CONFLICT')
        if changes:
            for key, value in changes.items():
                setattr(workspace, key, value)
            workspace.version += 1
            workspace.save(update_fields=list(changes) + ['version', 'updated_at'])
    return {'workspace': workspace_detail(ctx, workspace, member)}


def delete_workspace(request, ctx, id):
    workspace, member = ws.get_workspace(ctx, id)
    ws.require_owner(member)
    now = clock.now()
    with transaction.atomic():
        # Q-S2 A: tasks become standalone; artifacts stay with their owners.
        Task.objects.filter(workspace=workspace).update(workspace=None)
        Artifact.objects.filter(workspace=workspace).update(workspace=None)
        workspace.deleted_at = now
        workspace.save(update_fields=['deleted_at', 'updated_at'])
    return {'deleted_at': iso(now)}


def pin(request, ctx, id):
    workspace, member = ws.get_workspace(ctx, id)
    member.pinned_at = clock.now() if request.method == 'POST' else None
    member.save(update_fields=['pinned_at', 'updated_at'])
    return {'pinned_at': iso(member.pinned_at)}


def read_all(request, ctx, id):
    workspace, member = ws.get_workspace(ctx, id)
    tasks, unread = workspace_summary(ctx, workspace, member)
    now = clock.now()
    updated = 0
    with transaction.atomic():
        for task in tasks:
            if not unread[task.pk]:
                continue
            TaskUserState.objects.update_or_create(task=task, user=ctx.user, defaults={
                'account': ctx.account, 'last_read_at': now, 'marked_unread': False})
            updated += 1
    return {'updated_count': updated}


def archive_completed(request, ctx, id):
    data = json_body(request, ('expected_count',))
    workspace, member = ws.get_workspace(ctx, id)
    ws.require_editor(member)
    expected = integer(data['expected_count'])
    with transaction.atomic():
        tasks = list(ws.workspace_tasks(ctx, workspace, member).filter(status='accepted').select_for_update())
        if len(tasks) != expected:
            raise ValueError('STATE_CONFLICT')
        Task.objects.filter(pk__in=[task.pk for task in tasks]).update(archived_at=clock.now())
    return {'archived_count': len(tasks), 'task_ids': [task.public_id for task in tasks]}


# Members --------------------------------------------------------------------------

def access_summary(row, guest_tasks):
    if row.role == 'guest' and len(guest_tasks) == 1:
        return '仅“%s”一个任务' % guest_tasks[0].title
    return ACCESS_SUMMARY[row.role]


def member_row(ctx, row):
    guest_tasks = list(row.guest_tasks.order_by('title')) if row.role == 'guest' else []
    user = ws.person(row.user)
    return {
        'id': row.public_id,
        'user': {'display_name': user['display_name'], 'avatar_name': user['avatar_name']} if user else None,
        'email': row.user.email if row.user_id else row.invite_email,
        'role': row.role, 'state': row.state, 'access_summary': access_summary(row, guest_tasks),
        'guest_tasks': [{'id': task.public_id, 'title': task.title} for task in guest_tasks],
        'last_active_at': iso(row.user.last_login) if row.user_id else None,
        'is_me': row.user_id == ctx.user.pk,
    }


def list_members(request, ctx, id):
    query(request, ('offset',))
    workspace, _ = ws.get_workspace(ctx, id)
    rows = sorted(WorkspaceMember.objects.filter(workspace=workspace).exclude(state='removed').select_related('user'),
                  key=member_sort_key)
    body = page(request, rows, lambda row: member_row(ctx, row))
    body['counts'] = {'active': sum(1 for row in rows if row.state == 'active'),
                      'invited': sum(1 for row in rows if row.state == 'invited')}
    return body


def add_member(request, ctx, id):
    data = json_body(request, ('email', 'role'))
    workspace, member = ws.get_workspace(ctx, id)
    ws.require_owner(member)
    email = data['email'].strip() if isinstance(data['email'], str) else None
    try:
        validate_email(email)
    except ValidationError as exc:
        raise ValueError('VALIDATION_FAILED') from exc
    if data['role'] not in INVITE_ROLES:
        raise ValueError('VALIDATION_FAILED')
    user = (get_user_model().objects.filter(email__iexact=email, pk__in=Membership.objects.filter(
        account=ctx.account, is_active=True).values('user_id')).first())
    if user is None:
        # Someone outside the account would be a guest, which the first release does not support (Q-P1 C).
        raise ValueError('VALIDATION_FAILED')
    limited = ws.rate_limited(request, ctx, 'workspace-member')
    if limited:
        return limited
    now = clock.now()
    with transaction.atomic():
        row = WorkspaceMember.objects.select_for_update().filter(workspace=workspace, user=user).first()
        if row is not None and row.state != 'removed':
            raise ValueError('STATE_CONFLICT')
        if row is None:
            row = WorkspaceMember(account=ctx.account, workspace=workspace, user=user)
        row.role, row.state, row.invited_by, row.accepted_at, row.pinned_at = data['role'], 'active', ctx.user, now, None
        row.save()
    return respond({'member': member_row(ctx, row)}, 201)


def target_member(workspace, mid):
    row = (WorkspaceMember.objects.select_for_update().select_related('user')
           .filter(workspace=workspace, public_id=mid).exclude(state='removed').first())
    if row is None:
        raise ValueError('NOT_FOUND')
    return row


def last_owner(workspace, row):
    return row.role == 'owner' and row.state == 'active' and not WorkspaceMember.objects.filter(
        workspace=workspace, role='owner', state='active').exclude(pk=row.pk).exists()


def patch_member(request, ctx, id, mid):
    data = json_body(request, ('role',))
    workspace, member = ws.get_workspace(ctx, id)
    ws.require_owner(member)
    with transaction.atomic():
        row = target_member(workspace, mid)
        if data['role'] not in INVITE_ROLES or row.role == 'guest':
            raise ValueError('VALIDATION_FAILED')
        if data['role'] != 'owner' and last_owner(workspace, row):
            raise ValueError('STATE_CONFLICT')
        row.role = data['role']
        row.save(update_fields=['role', 'updated_at'])
    return {'member': member_row(ctx, row)}


def remove_member(request, ctx, id, mid):
    workspace, member = ws.get_workspace(ctx, id)
    with transaction.atomic():
        row = target_member(workspace, mid)
        if row.pk != member.pk:
            ws.require_owner(member)
        if last_owner(workspace, row):
            raise ValueError('STATE_CONFLICT')
        row.state, row.pinned_at = 'removed', None
        row.save(update_fields=['state', 'pinned_at', 'updated_at'])
    return {'member': member_row(ctx, row)}


# Rules ------------------------------------------------------------------------------

def rule_version_row(version):
    return {'number': version.number, 'author': {'name': ws.full_name(version.author)},
            'created_at': iso(version.created_at), 'change_note': version.change_note,
            'restored_from': version.restored_from.number if version.restored_from_id else None}


def get_rules(request, ctx, id):
    query(request, ('offset',))
    workspace, member = ws.get_workspace(ctx, id)
    current = workspace.current_rule_version
    versions = workspace.rule_versions.select_related('author', 'restored_from').order_by('-number')
    return {
        'current': {'number': current.number, 'rules': current.rules,
                    'author': {'name': ws.full_name(current.author)}, 'created_at': iso(current.created_at)}
        if current else None,
        'versions': page(request, versions, rule_version_row),
        'can_edit': member.role == 'owner',
    }


def locked_workspace(workspace, expected):
    workspace = Workspace.objects.select_for_update().select_related('current_rule_version').get(pk=workspace.pk)
    current = workspace.current_rule_version
    if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int)):
        raise ValueError('VALIDATION_FAILED')
    if expected != (current.number if current else None) and not (expected == 0 and current is None):
        raise ValueError('VERSION_CONFLICT')
    return workspace, current


def append_rules(ctx, workspace, current, rules, note, restored_from=None):
    top = workspace.rule_versions.aggregate(top=Max('number'))['top'] or 0
    version = WorkspaceRuleVersion.objects.create(account=ctx.account, workspace=workspace, number=top + 1,
                                                  rules=rules, author=ctx.user, change_note=note,
                                                  restored_from=restored_from)
    workspace.current_rule_version = version
    workspace.save(update_fields=['current_rule_version', 'updated_at'])
    return version


def post_rules(request, ctx, id):
    data = json_body(request, ('rules', 'version'), ('change_note',))
    workspace, member = ws.get_workspace(ctx, id)
    ws.require_owner(member)
    with transaction.atomic():
        workspace, current = locked_workspace(workspace, data['version'])
        rules = ws.clean_rules(data['rules'])
        note = ws.text_field(data.get('change_note'), 200, required=False) if data.get('change_note') else None
        note = note or ws.rules_change_note(current.rules if current else [], rules)
        version = append_rules(ctx, workspace, current, rules, note)
    return respond({'version': {'number': version.number}}, 201)


def restore_rules(request, ctx, id, v):
    data = json_body(request, (), ('version',))
    workspace, member = ws.get_workspace(ctx, id)
    ws.require_owner(member)
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update().select_related('current_rule_version').get(pk=workspace.pk)
        current = workspace.current_rule_version
        if 'version' in data:
            workspace, current = locked_workspace(workspace, data['version'])
        source = workspace.rule_versions.filter(number=int(v)).first()
        if source is None:
            raise ValueError('NOT_FOUND')
        if current is not None and source.pk == current.pk:
            raise ValueError('STATE_CONFLICT')
        # Q-S13 A: a rollback appends a new version with the old content.
        version = append_rules(ctx, workspace, current, list(source.rules), '回滚到 v%d' % source.number, source)
    return respond({'version': {'number': version.number, 'restored_from': source.number}}, 201)


# Playbooks ---------------------------------------------------------------------------

def normalize_variables(raw):
    """E33.variables holds list[{name, default}] (Q-F-5 A); plain names from older rows read as default None."""
    items = []
    for item in raw or []:
        if isinstance(item, str):
            items.append({'name': item, 'default': None})
        elif isinstance(item, dict) and isinstance(item.get('name'), str):
            items.append({'name': item['name'], 'default': item.get('default')})
    return items


def clean_variables(raw, titles):
    if not isinstance(raw, list) or len(raw) > PLAYBOOK_MAX_VARIABLES:
        raise ValueError('VALIDATION_FAILED')
    items, seen = [], set()
    for item in raw:
        if isinstance(item, str):
            item = {'name': item}
        if not isinstance(item, dict) or set(item) - {'name', 'default'}:
            raise ValueError('VALIDATION_FAILED')
        name = item.get('name').strip() if isinstance(item.get('name'), str) else ''
        default = item.get('default')
        if not VARIABLE_NAME.fullmatch(name) or name in seen:
            raise ValueError('VALIDATION_FAILED')
        if default is not None:
            default = ws.text_field(default, 60)
        if not any('{%s}' % name in title for title in titles):
            raise ValueError('VALIDATION_FAILED')
        seen.add(name)
        items.append({'name': name, 'default': default})
    return items


def playbook_counts(playbook):
    steps = playbook.steps.all()
    return steps.count(), steps.exclude(expert__isnull=True).values('expert').distinct().count()


def playbook_row(playbook, full=False):
    step_count, expert_count = playbook_counts(playbook)
    row = {'id': playbook.public_id, 'name': playbook.name, 'visibility': playbook.visibility,
           'variables': normalize_variables(playbook.variables), 'step_count': step_count,
           'expert_count': expert_count, 'created_at': iso(playbook.created_at)}
    if full:
        row['created_by'] = {'id': str(playbook.created_by_id), 'name': ws.full_name(playbook.created_by)}
        row['source_task'] = ({'id': playbook.source_task.public_id, 'title': playbook.source_task.title}
                              if playbook.source_task_id else None)
    return row


def list_playbooks(request, ctx):
    params = query(request, ('offset', 'q'))
    qs = visible_playbooks(ctx).select_related('created_by', 'source_task').order_by('-created_at', '-pk')
    if params.get('q'):
        qs = qs.filter(name__icontains=params['q'].strip())
    return page(request, qs, lambda playbook: playbook_row(playbook, full=True))


def get_playbook(request, ctx, id):
    query(request)
    playbook = visible_playbooks(ctx).select_related('created_by', 'source_task').filter(public_id=id).first()
    if playbook is None:
        raise ValueError('NOT_FOUND')
    row = playbook_row(playbook, full=True)
    steps = list(playbook.steps.select_related('expert', 'skill').prefetch_related('depends_on').order_by('order'))
    row['steps'] = [{'id': step.public_id, 'order': step.order, 'stage': step.stage, 'title': step.title,
                     'expert': ws.expert_ref(step.expert),
                     'skill': {'id': step.skill.public_id, 'name': step.skill.name} if step.skill_id else None,
                     'depends_on': [dep.public_id for dep in sorted(step.depends_on.all(), key=lambda d: d.order)],
                     'acceptance': step.acceptance} for step in steps]
    return row


def create_playbook(request, ctx):
    data = json_body(request, ('source_task', 'name', 'visibility', 'steps'), ('variables',))
    steps = data['steps']
    if not isinstance(steps, list) or any(not isinstance(item, dict) or set(item) - STEP_KEYS
                                          or 'source_step' not in item or 'title' not in item for item in steps):
        raise ValueError('INVALID_REQUEST')
    if not isinstance(data['source_task'], str):
        raise ValueError('VALIDATION_FAILED')
    task = (ws.visible_tasks(ctx).select_related('owner', 'creator', 'workspace')
            .filter(public_id=data['source_task']).first())
    if task is None:
        raise ValueError('NOT_FOUND')
    # Q-F-6 A: the task owner or creator may save it as a playbook.
    if not ws.is_owner_or_creator(ctx, task):
        raise ValueError('PERMISSION_DENIED')
    if task.status != 'accepted':
        raise ValueError('STATE_CONFLICT')
    name = ws.text_field(data['name'], 40)
    visibility = data['visibility']
    if visibility not in PLAYBOOK_VISIBILITY or (visibility == 'workspace' and task.workspace_id is None):
        raise ValueError('VALIDATION_FAILED')
    if not steps or len(steps) > PLAYBOOK_MAX_STEPS:
        raise ValueError('VALIDATION_FAILED')
    sources = {step.public_id: step for step in task.steps.select_related('expert', 'skill')}
    chosen = []
    for index, item in enumerate(steps):
        source = sources.get(item['source_step']) if isinstance(item['source_step'], str) else None
        order = item.get('order', index + 1)
        if source is None or any(source is row[1] for row in chosen):
            raise ValueError('VALIDATION_FAILED')
        chosen.append((integer(order, 1), source, ws.text_field(item['title'], 80)))
    chosen.sort(key=lambda row: row[0])
    variables = clean_variables(data.get('variables', []), [row[2] for row in chosen])
    limited = ws.rate_limited(request, ctx, 'playbook-create', 20)
    if limited:
        return limited
    with transaction.atomic():
        playbook = Playbook.objects.create(account=ctx.account, name=name, source_task=task, variables=variables,
                                           visibility=visibility, created_by=ctx.user)
        created = {}
        for order, (_, source, title) in enumerate(chosen, start=1):
            acceptance = list(AcceptanceCriterion.objects.filter(step=source, removed_at__isnull=True)
                              .order_by('order').values_list('text', flat=True))
            created[source.pk] = PlaybookStep.objects.create(
                account=ctx.account, playbook=playbook, order=order, stage=source.stage, title=title,
                expert=source.expert, skill=source.skill, acceptance=acceptance)
        for source_pk, step in created.items():
            deps = [created[dep] for dep in sources_deps(source_pk) if dep in created]
            if deps:
                step.depends_on.add(*deps)
    return respond({'playbook': playbook_row(playbook)}, 201)


def sources_deps(step_pk):
    return TaskStep.depends_on.through.objects.filter(from_taskstep_id=step_pk).values_list('to_taskstep_id', flat=True)


ID = PUBLIC_ID_PATTERN

ROUTES = [
    ('GET', 'workspaces', list_workspaces),
    ('POST', 'workspaces', create_workspace),
    ('GET', 'workspaces/{id}', get_workspace),
    ('PATCH', 'workspaces/{id}', patch_workspace),
    ('DELETE', 'workspaces/{id}', delete_workspace),
    ('POST', 'workspaces/{id}/pin', pin),
    ('DELETE', 'workspaces/{id}/pin', pin),
    ('POST', 'workspaces/{id}/read-all', read_all),
    ('POST', 'workspaces/{id}/archive-completed', archive_completed),
    ('GET', 'workspaces/{id}/members', list_members),
    ('POST', 'workspaces/{id}/members', add_member),
    ('PATCH', 'workspaces/{id}/members/{mid}', patch_member),
    ('DELETE', 'workspaces/{id}/members/{mid}', remove_member),
    ('GET', 'workspaces/{id}/rules', get_rules),
    ('POST', 'workspaces/{id}/rules', post_rules),
    ('POST', r'^workspaces/(?P<id>%s)/rules/(?P<v>[0-9]{1,5})/restore$' % ID, restore_rules),
    ('GET', 'playbooks', list_playbooks),
    ('POST', 'playbooks', create_playbook),
    ('GET', 'playbooks/{id}', get_playbook),
]
