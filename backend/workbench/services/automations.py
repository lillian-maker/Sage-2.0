"""Automations: schedules, permissions, enable/pause, manual and due runs (no real timer, Q-R1)."""
import calendar
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from django.db import transaction
from django.db.models import F, Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from .. import clock

from ..models import (AuthorizationGrant, AuthorizationRequest, Automation, AutomationPermission, AutomationRun,
                      Citation, ConnectorConnection, Playbook, Task, TaskStep, TaskThread)
from .inbox import expert_brief, require_credits, resolve, visible_experts, visible_workspaces, workspace_role
from .notify import notify

SCHEDULE_KINDS = ('daily', 'weekly', 'monthly')
CONTENT_KINDS = ('prompt', 'playbook')
MISSED_LABELS = {'mac_asleep': 'Mac 处于休眠', 'app_not_running': 'Sage 未运行'}
DEFAULT_GRACE = timedelta(minutes=15)
_TIMEZONES = None


def _valid_timezone(name):
    global _TIMEZONES
    if _TIMEZONES is None:
        _TIMEZONES = available_timezones()
    return isinstance(name, str) and name in _TIMEZONES


def own_automations(ctx):
    """Q-C-17 A: only the owner sees and manages an automation."""
    return Automation.objects.filter(account=ctx.account, owner=ctx.member).select_related(
        'workspace', 'lead_expert', 'playbook')


def visible_playbooks(ctx):
    return Playbook.objects.filter(account=ctx.account).filter(~Q(visibility='only_me') | Q(created_by=ctx.user))


def schedule_dict(a):
    return {'kind': a.schedule_kind, 'time': a.schedule_time.strftime('%H:%M') if a.schedule_time else None,
            'weekday': a.schedule_weekday, 'monthday': a.schedule_monthday, 'timezone': a.timezone}


def compute_next_run(a, after):
    """weekday is ISO (1 = Monday); monthday 29–31 falls back to the month's last day (Q-V8 A)."""
    zone = ZoneInfo(a.timezone)
    local = after.astimezone(zone)
    for offset in range(0, 400):
        day = local.date() + timedelta(days=offset)
        if a.schedule_kind == 'weekly' and day.isoweekday() != a.schedule_weekday:
            continue
        if a.schedule_kind == 'monthly':
            last = calendar.monthrange(day.year, day.month)[1]
            if day.day != min(a.schedule_monthday, last):
                continue
        candidate = datetime.combine(day, a.schedule_time, tzinfo=zone)
        if candidate > after:
            return candidate
    return None


def last_run(a):
    run = a.runs.annotate(at=Coalesce('scheduled_for', 'started_at')).order_by(F('at').desc(nulls_last=True), '-id').first()
    if run is None:
        return None
    return {'id': run.public_id, 'state': run.state, 'scheduled_for': run.scheduled_for,
            'finished_at': run.finished_at, 'missed_reason': run.missed_reason}


def automation_brief(a):
    return {'id': a.public_id, 'name': a.name, 'content_kind': a.content_kind, 'state': a.state, 'version': a.version,
            'lead_expert': expert_brief(a.lead_expert), 'schedule': schedule_dict(a), 'next_run_at': a.next_run_at,
            'workspace': {'id': a.workspace.public_id, 'name': a.workspace.name} if a.workspace else None,
            'last_run': last_run(a)}


# What each connector is read for; connectors not listed fall back to the generic wording.
CONNECTOR_READ_SCOPES = {'Jungle Scout': '读取价格数据', 'Keepa': '读取历史价格', '店铺商品目录': '读取本周上架商品'}


def permission_label(p, a):
    if p.kind == 'workspace_write':
        return '工作区“%s” · 写入成果' % (a.workspace.name if a.workspace else '')
    name = p.extension.name if p.extension else ''
    return '%s · %s' % (name, CONNECTOR_READ_SCOPES.get(name, '读取数据'))


def automation_detail(a):
    data = automation_brief(a)
    data.update(
        prompt=a.prompt, playbook={'id': a.playbook.public_id, 'name': a.playbook.name} if a.playbook else None,
        playbook_variables=a.playbook_variables, lead_expert=expert_brief(a.lead_expert, specialty=True),
        estimated_duration_minutes=a.estimated_duration_minutes, estimated_credits=a.estimated_credits,
        expected_output=a.expected_output,
        created_from_task=a.created_from_task.public_id if a.created_from_task_id else None,
        permissions=[{'id': p.public_id, 'kind': p.kind,
                      'extension': {'id': p.extension.public_id, 'name': p.extension.name} if p.extension else None,
                      'label': permission_label(p, a), 'state': p.state}
                     for p in a.permissions.select_related('extension').order_by('id')])
    return data


# ---------------------------------------------------------------- validation

def _parse_time(value):
    if not isinstance(value, str):
        raise ValueError('VALIDATION_FAILED')
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('VALIDATION_FAILED') from exc
    if len(value) not in (5, 8) or parsed.tzinfo is not None:
        raise ValueError('VALIDATION_FAILED')
    return parsed.replace(microsecond=0)


def _optional_int(value, low, high):
    if value is None:
        return None
    if type(value) is not int or not low <= value <= high:
        raise ValueError('VALIDATION_FAILED')
    return value


def _playbook_variable_names(playbook):
    names = []
    for entry in playbook.variables or []:
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, dict):
            name = entry.get('name') or entry.get('key')
            if isinstance(name, str):
                names.append(name)
    return names


def _delivery_expert(playbook):
    """Q-C-8 A: a playbook automation's lead is the expert of its delivery-stage step."""
    step = playbook.steps.filter(stage='deliver', expert__isnull=False).order_by('-order').first() or \
        playbook.steps.filter(expert__isnull=False).order_by('-order').first()
    return step.expert if step else None


FIELDS = ('name', 'content_kind', 'prompt', 'playbook', 'playbook_variables', 'workspace', 'lead_expert',
          'schedule_kind', 'schedule_time', 'schedule_weekday', 'schedule_monthday', 'timezone', 'created_from_task')


def apply_fields(ctx, a, data):
    """Validate and copy request fields onto `a`; returns True when the workspace changed."""
    workspace_changed = False
    if 'name' in data:
        name = data['name'].strip() if isinstance(data['name'], str) else ''
        if not 1 <= len(name) <= 60:
            raise ValueError('VALIDATION_FAILED')
        a.name = name
    if 'content_kind' in data:
        if data['content_kind'] not in CONTENT_KINDS:
            raise ValueError('VALIDATION_FAILED')
        a.content_kind = data['content_kind']
    if 'prompt' in data:
        prompt = data['prompt']
        if prompt is not None and (not isinstance(prompt, str) or len(prompt) > 4000):
            raise ValueError('VALIDATION_FAILED')
        a.prompt = prompt.strip() if prompt else None
    if 'playbook' in data:
        a.playbook = resolve(visible_playbooks(ctx), data['playbook']) if data['playbook'] is not None else None
    if 'playbook_variables' in data:
        variables = data['playbook_variables']
        if variables is not None and (not isinstance(variables, dict) or len(variables) > 20 or any(
                not isinstance(v, str) or len(v) > 200 for v in variables.values())):
            raise ValueError('VALIDATION_FAILED')
        a.playbook_variables = variables
    if 'workspace' in data:
        if data['workspace'] is None:
            raise ValueError('VALIDATION_FAILED')  # Q-C-4 A: required by the API, column stays nullable
        workspace = resolve(visible_workspaces(ctx), data['workspace'])
        if workspace_role(workspace, ctx.user) not in ('owner', 'editor'):
            raise ValueError('PERMISSION_DENIED')
        workspace_changed = workspace.pk != a.workspace_id
        a.workspace = workspace
    if 'lead_expert' in data:
        a.lead_expert = resolve(visible_experts(ctx), data['lead_expert']) if data['lead_expert'] is not None else None
    if 'schedule_kind' in data:
        if data['schedule_kind'] not in SCHEDULE_KINDS:
            raise ValueError('VALIDATION_FAILED')
        a.schedule_kind = data['schedule_kind']
    if 'schedule_time' in data:
        a.schedule_time = _parse_time(data['schedule_time'])
    if 'schedule_weekday' in data:
        a.schedule_weekday = _optional_int(data['schedule_weekday'], 1, 7)
    if 'schedule_monthday' in data:
        a.schedule_monthday = _optional_int(data['schedule_monthday'], 1, 31)
    if 'timezone' in data:
        if not _valid_timezone(data['timezone']):
            raise ValueError('VALIDATION_FAILED')
        a.timezone = data['timezone']
    if 'created_from_task' in data:
        value = data['created_from_task']
        a.created_from_task = resolve(Task.objects.filter(account=ctx.account).filter(
            Q(owner=ctx.member) | Q(creator=ctx.member)), value) if value is not None else None
    return workspace_changed


def validate_complete(a):
    if not a.name or a.content_kind not in CONTENT_KINDS or a.workspace_id is None:
        raise ValueError('VALIDATION_FAILED')
    if a.schedule_kind not in SCHEDULE_KINDS or a.schedule_time is None or not _valid_timezone(a.timezone):
        raise ValueError('VALIDATION_FAILED')
    if (a.schedule_kind == 'weekly') != (a.schedule_weekday is not None):
        raise ValueError('VALIDATION_FAILED')
    if (a.schedule_kind == 'monthly') != (a.schedule_monthday is not None):
        raise ValueError('VALIDATION_FAILED')
    if a.content_kind == 'prompt' and (not a.prompt or a.lead_expert_id is None):
        raise ValueError('VALIDATION_FAILED')
    if a.content_kind == 'playbook':
        if a.playbook_id is None:
            raise ValueError('VALIDATION_FAILED')
        names = _playbook_variable_names(a.playbook)
        variables = a.playbook_variables or {}
        if set(variables) - set(names) or set(names) - set(variables):
            raise ValueError('VALIDATION_FAILED')
        if a.lead_expert_id is None:
            a.lead_expert = _delivery_expert(a.playbook)


def sync_workspace_permission(a):
    AutomationPermission.objects.filter(automation=a, kind='workspace_write').delete()
    if a.workspace_id:
        AutomationPermission.objects.create(account=a.account, automation=a, kind='workspace_write',
                                            state='needs_grant')
    refresh_permissions(a)


def refresh_permissions(a):
    owner_user = a.owner.user
    for p in a.permissions.select_related('extension', 'extension__required_connector'):
        if p.kind == 'workspace_write':
            state = 'granted' if workspace_role(a.workspace, owner_user) in ('owner', 'editor') else 'needs_grant'
        else:
            ext = p.extension
            connector = ext if ext is None or ext.kind == 'connector' else (ext.required_connector or ext)
            connected = connector is not None and ConnectorConnection.objects.filter(
                account=a.account, extension=connector, state='connected').filter(
                Q(owner_user=owner_user) | Q(owner_user__isnull=True)).exists()
            if not connected:
                state = 'needs_connection'
            else:
                grant = p.grant if p.grant_id and p.grant.revoked_at is None else AuthorizationGrant.objects.filter(
                    account=a.account, user=owner_user, extension=ext, scope='workspace', workspace=a.workspace,
                    revoked_at__isnull=True).first()
                state = 'granted' if grant else 'needs_grant'
                if grant and p.grant_id != grant.pk:
                    p.grant = grant
        p.state = state
        p.save(update_fields=['state', 'grant', 'updated_at'])


def ensure_runnable(a):
    """Atomic check used by enable, run-now and reactivate; raises without touching the automation."""
    if a.content_kind == 'playbook' and (a.playbook_id is None or not Playbook.objects.filter(
            pk=a.playbook_id).filter(~Q(visibility='only_me') | Q(created_by=a.owner.user)).exists()):
        raise ValueError('VALIDATION_FAILED')
    refresh_permissions(a)
    states = list(a.permissions.values_list('kind', 'state'))
    if ('workspace_write', 'needs_grant') in states:
        raise ValueError('PERMISSION_DENIED')
    if any(state == 'needs_connection' for _, state in states):
        raise ValueError('CONNECTION_REQUIRED')
    if any(state == 'needs_grant' for _, state in states):
        raise ValueError('AUTHORIZATION_REQUIRED')


def activate(a, now=None):
    a.state = 'enabled'
    a.next_run_at = compute_next_run(a, now or clock.now())
    a.save(update_fields=['state', 'next_run_at', 'updated_at'])


# ---------------------------------------------------------------- writes

@transaction.atomic
def create_automation(ctx, data):
    a = Automation(account=ctx.account, owner=ctx.member, state='draft', version=1)
    apply_fields(ctx, a, data)
    validate_complete(a)
    a.next_run_at = compute_next_run(a, clock.now())  # shown as "启用后下次运行"
    a.save()
    sync_workspace_permission(a)
    return automation_detail(a)


@transaction.atomic
def update_automation(ctx, automation_id, data):
    a = resolve(own_automations(ctx).select_for_update(of=('self',)), automation_id)
    version = data.get('version')
    if type(version) is not int:
        raise ValueError('VALIDATION_FAILED')
    changes = {key: value for key, value in data.items() if key != 'version'}
    workspace_changed = apply_fields(ctx, a, changes)
    validate_complete(a)
    if version != a.version:
        raise ValueError('VERSION_CONFLICT')
    a.version += 1
    now = clock.now()
    a.next_run_at = compute_next_run(a, now) if a.state in ('draft', 'enabled') else None
    a.save()
    if workspace_changed:
        sync_workspace_permission(a)
        if a.state == 'enabled':
            # Q-C-1 A: changed permissions pause an enabled automation until re-enabled.
            a.state, a.next_run_at = 'paused', None
            a.save(update_fields=['state', 'next_run_at', 'updated_at'])
    return automation_detail(a)


@transaction.atomic
def enable_automation(ctx, automation_id):
    a = resolve(own_automations(ctx).select_for_update(of=('self',)), automation_id)
    if a.state == 'enabled':
        raise ValueError('STATE_CONFLICT')
    ensure_runnable(a)
    activate(a)
    return automation_detail(a)


@transaction.atomic
def pause_automation(ctx, automation_id):
    a = resolve(own_automations(ctx).select_for_update(of=('self',)), automation_id)
    if a.state != 'enabled':
        raise ValueError('STATE_CONFLICT')
    # Q-C-2 A: a run already in progress keeps going.
    a.state, a.next_run_at = 'paused', None
    a.save(update_fields=['state', 'next_run_at', 'updated_at'])
    return automation_detail(a)


def run_title(a):
    if a.content_kind == 'playbook':
        first = next(iter((a.playbook_variables or {}).items()), None)
        return '运行 Playbook' + (' · %s：%s' % first if first else '')
    return '运行指令 · %s' % a.name


def start_run(a, *, trigger, scheduled_for=None, now=None, client_request_id=None):
    now = now or clock.now()
    run = AutomationRun.objects.create(
        account=a.account, automation=a, title=run_title(a)[:120], trigger=trigger, scheduled_for=scheduled_for,
        started_at=now, state='running', variables=a.playbook_variables if a.content_kind == 'playbook' else None,
        client_request_id=client_request_id)
    task = Task.objects.create(
        account=a.account, workspace=a.workspace, title=a.name[:80], creator=a.owner, owner=a.owner,
        lead_expert=a.lead_expert, model_mode='auto', status='running', source_automation_run=run,
        source_playbook=a.playbook if a.content_kind == 'playbook' else None, goal=a.prompt
        if a.content_kind == 'prompt' else None, last_activity_at=now)
    TaskThread.objects.create(account=a.account, task=task, kind='main')
    if a.content_kind == 'playbook' and a.playbook_id:
        for step in a.playbook.steps.order_by('order'):
            TaskStep.objects.create(account=a.account, task=task, order=step.order, stage=step.stage,
                                    title=step.title, expert=step.expert, skill=step.skill)
    run.task = task
    run.save(update_fields=['task', 'updated_at'])
    return run


@transaction.atomic
def run_now(ctx, automation_id, data):
    from .inbox import client_request_id
    key = client_request_id(data.get('client_request_id'))
    a = resolve(own_automations(ctx).select_for_update(of=('self',)), automation_id)
    previous = AutomationRun.objects.filter(account=ctx.account, client_request_id=key).first()
    if previous is not None:
        if previous.automation_id != a.pk:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return {'run': {'id': previous.public_id, 'state': previous.state}}
    if a.state != 'enabled':
        raise ValueError('STATE_CONFLICT')  # Q-C-3 A
    active = a.runs.filter(trigger='manual', state__in=('running', 'awaiting_user')).order_by('-id').first()
    if active is not None:
        # A second click while a manual run is still going joins that run instead of starting another.
        return {'run': {'id': active.public_id, 'state': active.state}}
    ensure_runnable(a)
    require_credits(a.account)
    run = start_run(a, trigger='manual', client_request_id=key)
    return {'run': {'id': run.public_id, 'state': run.state}}


# ---------------------------------------------------------------- scheduler entry points

def record_missed(a, scheduled_for, reason='app_not_running', now=None):
    run, created = AutomationRun.objects.get_or_create(
        automation=a, scheduled_for=scheduled_for, trigger='schedule',
        defaults={'account': a.account, 'title': run_title(a)[:120], 'state': 'missed', 'missed_reason': reason})
    if created:
        local = timezone.localtime(scheduled_for, ZoneInfo(a.timezone))
        notify(account=a.account, recipient=a.owner.user, category='automation', type='automation_missed',
               text='自动化“%s”已错过一次计划运行' % a.name,
               context_label='%d月%d日 %s · %s' % (local.month, local.day, local.strftime('%H:%M'),
                                                MISSED_LABELS.get(reason, reason)),
               target_type='automation', target_id=a.public_id, action='run_now',
               dedupe_key='automation_missed:%s' % run.public_id, occurred_at=now)
    return run


def run_due(now=None, grace=DEFAULT_GRACE):
    """Execute every enabled automation whose next_run_at has passed. Safe to call repeatedly."""
    now = now or clock.now()
    results = []
    due = list(Automation.objects.filter(state='enabled', next_run_at__lte=now).values_list('pk', flat=True))
    for pk in due:
        with transaction.atomic():
            a = Automation.objects.select_for_update().select_related('owner__user', 'playbook', 'workspace').filter(
                pk=pk, state='enabled', next_run_at__lte=now).first()
            if a is None:
                continue
            scheduled = a.next_run_at
            if AutomationRun.objects.filter(automation=a, trigger='schedule', scheduled_for=scheduled).exists():
                outcome = 'duplicate'
            elif now - scheduled > grace:
                record_missed(a, scheduled, 'app_not_running', now)
                outcome = 'missed'
            else:
                try:
                    with transaction.atomic():
                        ensure_runnable(a)
                        require_credits(a.account)
                        start_run(a, trigger='schedule', scheduled_for=scheduled, now=now)
                    outcome = 'started'
                except ValueError as exc:
                    AutomationRun.objects.create(
                        account=a.account, automation=a, title=run_title(a)[:120], trigger='schedule',
                        scheduled_for=scheduled, started_at=now, finished_at=now, state='failed',
                        failure_code=str(exc).lower()[:40])
                    outcome = 'failed:%s' % exc
            a.next_run_at = compute_next_run(a, now)
            a.save(update_fields=['next_run_at', 'updated_at'])
            results.append((a.public_id, scheduled, outcome))
    return results


# ---------------------------------------------------------------- runs

def own_runs(ctx):
    return AutomationRun.objects.filter(account=ctx.account, automation__owner=ctx.member).select_related('automation')


def ordered_runs(queryset):
    return queryset.annotate(at=Coalesce('scheduled_for', 'started_at')).order_by(F('at').desc(nulls_last=True), '-id')


def pending_authorizations(run):
    return AuthorizationRequest.objects.filter(automation_run=run, decision='pending').count()


def run_brief(run):
    return {'id': run.public_id, 'automation': {'id': run.automation.public_id, 'name': run.automation.name},
            'title': run.title, 'trigger': run.trigger, 'scheduled_for': run.scheduled_for,
            'started_at': run.started_at, 'finished_at': run.finished_at, 'state': run.state,
            'failure_code': run.failure_code, 'missed_reason': run.missed_reason,
            'pending_authorization_count': pending_authorizations(run)}


def _seconds(start, end):
    return int((end - start).total_seconds()) if start and end else None


def run_detail(run):
    now = clock.now()
    from .tasks import step_used_credits, task_used_credits
    steps, credits = [], None
    if run.task_id:
        credits = task_used_credits(run.task)
        all_steps = TaskStep.objects.filter(task=run.task)
        revision = all_steps.order_by('-plan_revision').values_list('plan_revision', flat=True).first()
        for step in all_steps.filter(plan_revision=revision).select_related('expert', 'skill').order_by('order'):
            auth = AuthorizationRequest.objects.filter(step=step).order_by('-created_at').first()
            # Q-F-14 A: settled ledger Credits by TaskCostEvent.step_key; a step that has not started reports null.
            used = None if step.status == 'queued' else step_used_credits(run.task, step)
            steps.append({'id': step.public_id, 'title': step.title,
                          'skill_name': step.skill.name if step.skill else None,
                          'citation_count': Citation.objects.filter(task_step=step).count(),
                          'expert': expert_brief(step.expert), 'status': step.status,
                          'authorization': {'id': auth.public_id, 'title': auth.title,
                                            'scope_text': auth.scope_text,
                                            'decision': auth.decision} if auth else None,
                          'credits_used': used,
                          'duration_seconds': _seconds(step.started_at, step.finished_at or (
                              now if step.status == 'running' else None))})
    data = run_brief(run)
    data['automation']['schedule'] = schedule_dict(run.automation)
    end = run.finished_at or (now if run.state in ('running', 'awaiting_user') else None)
    data.update(variables=run.variables, task_id=run.task.public_id if run.task_id else None,
                elapsed_seconds=_seconds(run.started_at, end), credits_used=credits, steps=steps)
    return data
