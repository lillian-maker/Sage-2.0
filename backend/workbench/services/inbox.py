"""Inbox domain: sources and their pull pipeline, items, handover, and the "需要我决定" aggregate.

Also holds small helpers shared by the inbox, notifications, automations and search routes.
"""
import re
from dataclasses import dataclass, field
from datetime import timedelta
from fractions import Fraction

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from .. import clock

from backend.models import Membership
from backend.http import error

from ..models import (AppNotification, AuthorizationRequest, Automation, ConnectorConnection, Expert,
                      HandoffItem, InboxItem, InboxItemEvent, InboxSource, InboxSourceDailyStat, KickoffQuestion,
                      Task, TaskDelivery, TaskParticipant, TaskStep, TaskThread, Workspace, WorkspaceMember)
from . import workspaces as ws

SOURCE_KINDS = ('company_email', 'amazon_buyer_messages', 'store_notifications', 'feishu', 'wecom')
LIST_GROUPS = ('pending_confirmation', 'todo', 'in_progress', 'done')
SENDER_FILTERS = ('all_members_except_bots', 'all_members')
INITIATIVES = ('report_only', 'draft_suggestion', 'start_task')
DECISION_TYPES = ('approve', 'supply', 'accept', 'invite')
ACTION_LABELS = {'read_query': '只读查询'}
CONFIDENCE_THRESHOLD = 50  # Q-R4 A: fixed threshold
MAX_RESPONSIBLE_EXPERTS = 3  # Q-B-13 A
CLIENT_REQUEST_ID = re.compile(r'[A-Za-z0-9._:-]{1,64}')


# ---------------------------------------------------------------- shared helpers

def person_name(user):
    if user is None:
        return ''
    return ('%s %s' % (user.first_name, user.last_name)).strip() or user.email


def initials(user):
    parts = [p for p in (user.first_name, user.last_name) if p]
    return ''.join(p[0] for p in parts).upper() if parts else (user.email[:1].upper() if user.email else '')


def expert_brief(expert, specialty=False):
    if expert is None:
        return None
    data = {'id': expert.public_id, 'name': expert.name, 'avatar_ref': ws.avatar_url(expert.avatar_ref)}
    if specialty:
        data['specialty'] = expert.specialty
    return data


def local_hm(value):
    return timezone.localtime(value).strftime('%H:%M')


def visible_experts(ctx):
    return Expert.objects.filter(
        Q(account__isnull=True, status='published') |
        (Q(account=ctx.account) & ~Q(status='archived') & (~Q(share_scope='only_me') | Q(created_by=ctx.user))))


def visible_workspaces(ctx):
    return Workspace.objects.filter(account=ctx.account, deleted_at__isnull=True, members__user=ctx.user,
                                    members__state='active').distinct()


def workspace_role(workspace, user):
    if workspace is None:
        return None
    row = WorkspaceMember.objects.filter(workspace=workspace, user=user, state='active').first()
    return row.role if row else None


def resolve(queryset, public_id):
    if not isinstance(public_id, str):
        raise ValueError('VALIDATION_FAILED')
    obj = queryset.filter(public_id=public_id).first()
    if obj is None:
        raise ValueError('NOT_FOUND')
    return obj


def client_request_id(value):
    if not isinstance(value, str) or not CLIENT_REQUEST_ID.fullmatch(value):
        raise ValueError('VALIDATION_FAILED')
    return value


def require_credits(account):
    """Gate only: the reservation itself belongs to the executor and commerce (Q-R9 A)."""
    from backend.commerce.billing import credits_summary
    if Fraction(credits_summary(account)['available']) <= 0:
        raise ValueError('INSUFFICIENT_CREDITS')


def rate_limited(request, bucket, limit):
    from backend.views import throttle
    with transaction.atomic():
        allowed = throttle('app:%s:%s' % (bucket, request.user.pk), limit)
    if allowed:
        return None
    return error(request, 'RATE_LIMITED', 429, '操作太频繁，请稍后再试。')


def search_text(value, limit=100):
    if not isinstance(value, str):
        raise ValueError('VALIDATION_FAILED')
    value = value.strip()
    if not 1 <= len(value) <= limit:
        raise ValueError('VALIDATION_FAILED')
    return value


def task_current_step(task):
    steps = TaskStep.objects.filter(task=task)
    latest = steps.order_by('-plan_revision').values_list('plan_revision', flat=True).first()
    if latest is None:
        return None
    step = steps.filter(plan_revision=latest).exclude(status__in=('done', 'skipped')).order_by('order').first()
    return step.order if step else None


# ---------------------------------------------------------------- items

def sync_done(ctx):
    """I5 (Q-S19 A): an item whose task has been accepted becomes done."""
    rows = list(InboxItem.objects.filter(account=ctx.account, recipient=ctx.user, group='in_progress',
                                         task__status='accepted'))
    now = clock.now()
    for item in rows:
        item.group = 'done'
        item.save(update_fields=['group', 'updated_at'])
        InboxItemEvent.objects.create(account=item.account, item=item, at=now, kind='completed',
                                      text='%s 关联任务已验收，标为已完成' % local_hm(now))


def own_items(ctx):
    return InboxItem.objects.filter(account=ctx.account, recipient=ctx.user)


def item_brief(item):
    return {'id': item.public_id, 'title': item.title, 'group': item.group, 'confidence': item.confidence,
            'priority': item.priority, 'sender_label': item.sender_label, 'channel_label': item.channel_label,
            'received_at': item.received_at,
            'source': {'id': item.source.public_id, 'kind': item.source.kind},
            'suggested_experts': [expert_brief(e) for e in item.suggested_experts.all()],
            'workspace': {'id': item.workspace.public_id, 'name': item.workspace.name} if item.workspace else None,
            'task': {'id': item.task.public_id, 'title': item.task.title} if item.task else None,
            'ignored_at': item.ignored_at}


def item_detail(item):
    data = item_brief(item)
    data.update(original_text=item.original_text, translated_text=item.translated_text, rationale=item.rationale,
                suggestion=item.suggestion,
                events=[{'at': e.at, 'kind': e.kind, 'text': e.text} for e in item.events.order_by('at', 'id')])
    return data


def inbox_counts(ctx):
    """Single source for every inbox number (Q-A-17/Q-D-11/Q-B-1 suggestion C).

    external = items still to handle (pending_confirmation + todo); all = decisions + external, and the
    sidebar / app-icon badge equals all. @-mentions come from AppNotification (Q-B-2 A) and are listed
    separately so one event is never counted twice.
    """
    sync_done(ctx)
    active = own_items(ctx).exclude(group='ignored')
    groups = dict.fromkeys(LIST_GROUPS, 0)
    for row in active.values('group').annotate(n=Count('id')):
        groups[row['group']] = row['n']
    sources = dict.fromkeys(SOURCE_KINDS, 0)
    for row in active.values('source__kind').annotate(n=Count('id')):
        sources[row['source__kind']] = row['n']
    decisions = len(decision_entries(ctx))
    mentions = mention_notifications(ctx).filter(read_at__isnull=True).count()
    external = groups['pending_confirmation'] + groups['todo']
    return {'all': decisions + external, 'decisions': decisions, 'mentions': mentions, 'external': external,
            'groups': groups, 'sources': sources}


def mention_notifications(ctx):
    return AppNotification.objects.filter(account=ctx.account, recipient=ctx.user, category='mention').select_related(
        'actor_expert').order_by('-occurred_at', '-id')


def _event(item, kind, text, at=None):
    InboxItemEvent.objects.create(account=item.account, item=item, at=at or clock.now(), kind=kind, text=text[:200])


@transaction.atomic
def ignore_item(ctx, item_id):
    item = resolve(own_items(ctx).select_for_update(), item_id)
    if item.group not in ('pending_confirmation', 'todo'):
        raise ValueError('STATE_CONFLICT')
    now = clock.now()
    item.group, item.ignored_at = 'ignored', now
    item.save(update_fields=['group', 'ignored_at', 'updated_at'])
    _event(item, 'ignored', '%s 已忽略' % local_hm(now), now)
    return {'item': {'id': item.public_id, 'group': item.group, 'ignored_at': item.ignored_at}}


@transaction.atomic
def hand_off(ctx, item_id, data):
    item = resolve(own_items(ctx).select_for_update(), item_id)
    client_request_id(data.get('client_request_id'))
    if item.group not in ('pending_confirmation', 'todo') or item.task_id:
        raise ValueError('STATE_CONFLICT')
    experts = visible_experts(ctx)
    suggested = list(item.suggested_experts.all())
    lead_id = data.get('lead_expert_id')
    collaborator_ids = data.get('collaborator_expert_ids')
    if collaborator_ids is not None and (not isinstance(collaborator_ids, list) or len(collaborator_ids) > 7):
        raise ValueError('VALIDATION_FAILED')
    if lead_id is not None:
        lead = resolve(experts, lead_id)
    elif suggested:
        lead = suggested[0]  # Q-B-4 A
    else:
        raise ValueError('VALIDATION_FAILED')
    if collaborator_ids is not None:
        collaborators = [resolve(experts, value) for value in collaborator_ids]
    else:
        collaborators = [e for e in suggested if e.pk != lead.pk]
    collaborators = [e for i, e in enumerate(collaborators) if e.pk != lead.pk and e not in collaborators[:i]]
    require_credits(ctx.account)
    now = clock.now()
    task = Task.objects.create(account=ctx.account, workspace=item.workspace, title=item.title[:80],
                               creator=ctx.member, owner=ctx.member, lead_expert=lead, model_mode='auto',
                               status='running', source_inbox_item=item, last_activity_at=now)
    TaskThread.objects.create(account=ctx.account, task=task, kind='main')
    TaskParticipant.objects.create(account=ctx.account, task=task, kind='expert', expert=lead, role='lead',
                                   state='active', invited_by=ctx.user)
    for expert in collaborators:
        TaskParticipant.objects.create(account=ctx.account, task=task, kind='expert', expert=expert,
                                       role='collaborator', state='active', invited_by=ctx.user)
    item.group, item.task = 'in_progress', task
    item.save(update_fields=['group', 'task', 'updated_at'])
    _event(item, 'handed_off', '%s 交给%s处理' % (local_hm(now), lead.name), now)
    return {'item': {'id': item.public_id, 'group': item.group}, 'task': {'id': task.public_id, 'title': task.title}}


# ---------------------------------------------------------------- sources

def own_sources(ctx):
    return InboxSource.objects.filter(account=ctx.account, owner_user=ctx.user).select_related(
        'connection', 'connection__extension')


def source_version(source):
    return source.version


def source_brief(source):
    return {'id': source.public_id, 'kind': source.kind, 'connection_state': source.connection.state,
            'initiative': source.initiative, 'disconnected_at': source.disconnected_at,
            'responsible_experts': [expert_brief(e) for e in source.responsible_experts.all()],
            'version': source_version(source)}


def funnel_7d(source):
    today = clock.localdate()
    rows = InboxSourceDailyStat.objects.filter(source=source, date__gt=today - timedelta(days=7), date__lte=today)
    totals = dict.fromkeys(('received', 'after_rules', 'after_expert', 'admitted'), 0)
    for row in rows:
        for key in totals:
            totals[key] += getattr(row, key)
    return totals


def source_detail(source):
    data = source_brief(source)
    data.update(watched_groups=source.watched_groups, keywords=source.keywords, sender_filter=source.sender_filter,
                connection={'id': source.connection.public_id, 'state': source.connection.state,
                            'extension': {'id': source.connection.extension.public_id,
                                          'name': source.connection.extension.name}},
                funnel_7d=funnel_7d(source))
    return data


def _string_list(value, max_items, max_len):
    if not isinstance(value, list) or len(value) > max_items:
        raise ValueError('VALIDATION_FAILED')
    cleaned = []
    for entry in value:
        if not isinstance(entry, str) or not 1 <= len(entry.strip()) <= max_len:
            raise ValueError('VALIDATION_FAILED')
        if entry.strip() not in cleaned:
            cleaned.append(entry.strip())
    return cleaned


@transaction.atomic
def update_source(ctx, source_id, data):
    source = resolve(own_sources(ctx).select_for_update(), source_id)
    changes, experts = {}, None
    if 'watched_groups' in data:
        changes['watched_groups'] = _string_list(data['watched_groups'], 20, 60)
    if 'keywords' in data:
        changes['keywords'] = _string_list(data['keywords'], 20, 20)
    if 'sender_filter' in data:
        if data['sender_filter'] not in SENDER_FILTERS:
            raise ValueError('VALIDATION_FAILED')
        changes['sender_filter'] = data['sender_filter']
    if 'initiative' in data:
        if data['initiative'] not in INITIATIVES:
            raise ValueError('VALIDATION_FAILED')
        changes['initiative'] = data['initiative']
    if 'responsible_expert_ids' in data:
        ids = data['responsible_expert_ids']
        if not isinstance(ids, list) or len(ids) > MAX_RESPONSIBLE_EXPERTS or len(set(map(str, ids))) != len(ids):
            raise ValueError('VALIDATION_FAILED')
        experts = [resolve(visible_experts(ctx), value) for value in ids]
    version = data.get('version')
    if type(version) is not int:
        raise ValueError('VALIDATION_FAILED')
    if version != source_version(source):
        raise ValueError('VERSION_CONFLICT')
    if source.disconnected_at or source.connection.state != 'connected':
        raise ValueError('CONNECTION_REQUIRED')
    for key, value in changes.items():
        setattr(source, key, value)
    source.version += 1
    source.save()
    if experts is not None:
        source.responsible_experts.set(experts)
    source.refresh_from_db()
    return {'source': source_detail(source)}


@transaction.atomic
def disconnect_source(ctx, source_id):
    source = resolve(own_sources(ctx).select_for_update(), source_id)
    if source.disconnected_at:
        raise ValueError('STATE_CONFLICT')
    now = clock.now()
    source.disconnected_at = now
    source.save(update_fields=['disconnected_at', 'updated_at'])
    connection = source.connection
    if connection.state != 'revoked':
        connection.state, connection.revoked_reason = 'revoked', 'user'
        connection.save(update_fields=['state', 'revoked_reason', 'updated_at'])
    # Q-B-12 A: items and daily stats stay; the client asks for confirmation first.
    return {'source': {'id': source.public_id, 'disconnected_at': source.disconnected_at}}


# ---------------------------------------------------------------- pull pipeline (Q-X3 A)

@dataclass
class RawMessage:
    external_ref: str
    received_at: object
    title: str
    text: str
    sender_label: str = ''
    translated_text: str = None
    group: str = None
    sender_is_bot: bool = False


@dataclass
class Judgment:
    admit: bool
    confidence: int = 0
    priority: str = 'normal'
    rationale: str = ''
    suggestion: str = None
    experts: list = field(default_factory=list)
    workspace: object = None
    title: str = None


class SourceAdapter:
    """Reads messages from one external source. The real connectors are not wired yet."""

    def fetch(self, source, since):
        raise ValueError('SOURCE_UNAVAILABLE')


class Judge:
    """Expert judgement (confidence, rationale, routing) is produced by the executor (Q-R4 A)."""

    def judge(self, source, message):
        raise ValueError('SOURCE_UNAVAILABLE')


_adapters = {}
_judge = [Judge()]


def register_adapter(kind, adapter):
    if kind not in SOURCE_KINDS:
        raise ValueError('INVALID_SOURCE_KIND')
    previous = _adapters.get(kind)
    _adapters[kind] = adapter
    return previous


def unregister_adapter(kind):
    _adapters.pop(kind, None)


def set_judge(judge):
    previous = _judge[0]
    _judge[0] = judge or Judge()
    return previous


def adapter_for(kind):
    return _adapters.get(kind) or SourceAdapter()


def passes_rules(source, message):
    if source.sender_filter == 'all_members_except_bots' and message.sender_is_bot:
        return False
    if source.watched_groups and message.group is not None and message.group not in source.watched_groups:
        return False
    if source.keywords:
        haystack = '%s\n%s' % (message.title, message.text)
        return any(keyword in haystack for keyword in source.keywords)
    return True


def pull_source(source, *, since=None, now=None):
    """Fetch, filter, judge and admit; only admitted messages are stored, the rest only counted."""
    now = now or clock.now()
    if source.disconnected_at or source.connection.state != 'connected':
        raise ValueError('CONNECTION_REQUIRED')
    messages = adapter_for(source.kind).fetch(source, since)
    judge = _judge[0]
    stats = dict.fromkeys(('received', 'after_rules', 'after_expert', 'admitted'), 0)
    with transaction.atomic():
        for message in messages:
            stats['received'] += 1
            if not passes_rules(source, message):
                continue
            stats['after_rules'] += 1
            verdict = judge.judge(source, message)
            if not verdict.admit:
                continue
            stats['after_expert'] += 1
            if InboxItem.objects.filter(source=source, external_ref=message.external_ref).exists():
                continue
            confidence = max(0, min(100, int(verdict.confidence)))
            item = InboxItem.objects.create(
                account=source.account, recipient=source.owner_user, source=source,
                external_ref=message.external_ref[:128], title=(verdict.title or message.title)[:120],
                received_at=message.received_at, original_text=message.text,
                translated_text=message.translated_text,
                group='todo' if confidence >= CONFIDENCE_THRESHOLD else 'pending_confirmation',
                confidence=confidence, priority=verdict.priority if verdict.priority in ('high', 'normal') else 'normal',
                rationale=verdict.rationale, suggestion=verdict.suggestion, workspace=verdict.workspace,
                sender_label=message.sender_label[:60])
            if verdict.experts:
                item.suggested_experts.set(verdict.experts)
            text = '%s 收到' % local_hm(message.received_at)
            if verdict.workspace is not None:
                text += '并归入工作区“%s”' % verdict.workspace.name
            _event(item, 'received', text, message.received_at)
            stats['admitted'] += 1
        stat, _ = InboxSourceDailyStat.objects.select_for_update().get_or_create(
            account=source.account, source=source, date=timezone.localdate(now))
        for key, value in stats.items():
            setattr(stat, key, getattr(stat, key) + value)
        stat.save()
    return stats


def pull_all(now=None):
    results = []
    for source in InboxSource.objects.filter(disconnected_at__isnull=True, connection__state='connected').select_related(
            'connection', 'account'):
        try:
            results.append((source, pull_source(source, now=now), None))
        except ValueError as exc:
            results.append((source, None, str(exc)))
    return results


# ---------------------------------------------------------------- "需要我决定"

def _approvers(account, member):
    """Q-P4 B: department lead of the requester, else the account owners."""
    from backend.commerce.models import DepartmentMember
    row = DepartmentMember.objects.filter(membership=member, ended_period__isnull=True).select_related('department').first()
    if row:
        lead = DepartmentMember.objects.filter(department=row.department, is_lead=True, ended_period__isnull=True,
                                               membership__is_active=True).exclude(membership=member).first()
        if lead:
            return {lead.membership_id}
    return set(Membership.objects.filter(account=account, role='owner', is_active=True).values_list('pk', flat=True))


def _approve_entry(request, ctx):
    # charge_target 形状与 GET app/authorizations/{id} 一致（对象；E 组 R-E-4）。
    from .billing import charge_target as charge_target_object
    task, run = request.task, request.automation_run
    owner = task.owner if task else run.automation.owner if run else None
    context_title = task.title if task else run.automation.name if run else ''
    return {'type': 'approve', 'id': request.public_id,
            'title': request.title or '%s %s' % (request.extension.name, request.scope_text),
            'context': '%s · %s' % (context_title, request.requested_by_expert.name),
            'created_at': request.created_at, 'expert': expert_brief(request.requested_by_expert),
            'actor': None,
            'target': {'type': 'authorization', 'id': request.public_id, 'task_id': task.public_id if task else None,
                       'automation_run_id': run.public_id if run else None,
                       'automation_id': run.automation.public_id if run else None},
            'authorization': {
                'id': request.public_id, 'purpose_text': request.purpose_text, 'scope_text': request.scope_text,
                'scope_params': request.scope_params, 'scope_options': request.scope_options or [],
                'action_kind': request.action_kind, 'validity': request.validity,
                'estimate_state': request.estimate_state, 'estimated_credits': request.estimated_credits,
                'charge_target': charge_target_object(ctx), 'decision': request.decision,
                'version': request.version,
                'policy_approval_required': request.policy_approval_required,
                'requested_by': {'expert': request.requested_by_expert.name,
                                 'task_owner': person_name(owner.user) if owner else ''}}}


def decision_entries(ctx):
    member, account = ctx.member, ctx.account
    entries = []
    requests = AuthorizationRequest.objects.filter(account=account, decision='pending').select_related(
        'task', 'task__owner__user', 'automation_run__automation__owner__user', 'extension', 'requested_by_expert')
    for request in requests:
        owner = request.task.owner if request.task else (
            request.automation_run.automation.owner if request.automation_run else None)
        if owner is None:
            continue
        if request.policy_approval_required:
            if member.pk not in _approvers(account, owner):
                continue
        elif owner.pk != member.pk:
            continue
        entries.append(_approve_entry(request, ctx))
    questions = KickoffQuestion.objects.filter(account=account, answer__isnull=True, task__owner=member).exclude(
        task__status__in=('accepted', 'stopped')).select_related('task', 'task__lead_expert')
    for q in questions:
        lead = q.task.lead_expert
        entries.append({'type': 'supply', 'id': q.public_id, 'title': q.question,
                        'context': ' · '.join(filter(None, (q.task.title, lead.name if lead else ''))),
                        'created_at': q.created_at, 'expert': expert_brief(lead), 'actor': None,
                        'target': {'type': 'kickoff_question', 'id': q.public_id, 'task_id': q.task.public_id}})
    deliveries = TaskDelivery.objects.filter(account=account, decision='pending', task__owner=member).select_related(
        'task', 'task__workspace', 'task__lead_expert', 'artifact_version__artifact', 'artifact_version__author_expert')
    for d in deliveries:
        version = d.artifact_version
        expert = version.author_expert or d.task.lead_expert
        # A delivery is accepted on behalf of the workspace it lands in, so the card names the workspace.
        where = d.task.workspace.name if d.task.workspace else d.task.title
        entries.append({'type': 'accept', 'id': d.public_id,
                        'title': '%s v%s' % (version.artifact.title, version.number),
                        'context': ' · '.join(filter(None, (where, expert.name if expert else ''))),
                        'created_at': d.created_at, 'expert': expert_brief(expert), 'actor': None,
                        'target': {'type': 'delivery', 'id': d.public_id, 'task_id': d.task.public_id}})
    invites = TaskParticipant.objects.filter(account=account, kind='human', user=ctx.user, state='invited').select_related(
        'task', 'task__workspace', 'invited_by')
    for p in invites:
        inviter = p.invited_by
        entries.append({'type': 'invite', 'id': p.public_id,
                        'title': '%s 邀请你加入任务' % (person_name(inviter) or '有人'),
                        'context': ' · '.join(filter(None, (p.task.title, p.task.workspace.name if p.task.workspace else ''))),
                        'created_at': p.created_at, 'expert': None,
                        'actor': {'name': person_name(inviter), 'initials': initials(inviter)} if inviter else None,
                        'target': {'type': 'task_participant', 'id': p.public_id, 'task_id': p.task.public_id}})
    workspace_invites = WorkspaceMember.objects.filter(
        account=account, state='invited', workspace__deleted_at__isnull=True).filter(
        Q(user=ctx.user) | Q(user__isnull=True, invite_email__iexact=ctx.user.email or '-')).select_related(
        'workspace', 'invited_by')
    for w in workspace_invites:
        inviter = w.invited_by
        entries.append({'type': 'invite', 'id': w.public_id,
                        'title': '%s 邀请你加入工作区' % (person_name(inviter) or '有人'),
                        'context': w.workspace.name, 'created_at': w.created_at, 'expert': None,
                        'actor': {'name': person_name(inviter), 'initials': initials(inviter)} if inviter else None,
                        'target': {'type': 'workspace_member', 'id': w.public_id, 'task_id': None,
                                   'workspace_id': w.workspace.public_id}})
    entries.sort(key=lambda e: (e['created_at'], e['id']), reverse=True)
    return entries


# ---------------------------------------------------------------- handover (C-B-15)

def _handoff_target(item):
    target = {'type': item.target_type, 'id': item.target_id, 'title': '', 'status': None, 'current_step': None}
    if item.kind == 'task':
        task = Task.objects.filter(account=item.account, public_id=item.target_id).first()
        if task:
            target.update(title=task.title, status=task.status, current_step=task_current_step(task))
    elif item.kind == 'approval':
        request = AuthorizationRequest.objects.filter(account=item.account, public_id=item.target_id).select_related(
            'extension', 'requested_by_expert').first()
        if request:
            target.update(title=request.title or '%s %s' % (request.extension.name, ACTION_LABELS.get(request.action_kind, '')),
                          status=request.decision, expert=expert_brief(request.requested_by_expert),
                          task_id=request.task.public_id if request.task_id else None)
    elif item.kind == 'automation':
        automation = Automation.objects.filter(account=item.account, public_id=item.target_id).first()
        if automation:
            target.update(title=automation.name, status=automation.state)
    elif item.kind == 'workspace_ownership':
        workspace = Workspace.objects.filter(account=item.account, public_id=item.target_id).first()
        if workspace:
            target.update(title=workspace.name, status='owner')
    elif item.kind == 'custom_expert':
        expert = Expert.objects.filter(account=item.account, public_id=item.target_id).select_related(
            'shared_department').first()
        if expert:
            target.update(title=expert.name, status=expert.share_scope,
                          shared_department=expert.shared_department.name if expert.shared_department else None)
    return target


def handover_groups(ctx):
    """Groups that still have pending items; fully handled removals disappear (Q-B-14 A)."""
    items = HandoffItem.objects.filter(account=ctx.account, successor=ctx.member).select_related(
        'removal', 'removal__membership__user').order_by('created_at', 'id')
    by_removal = {}
    for item in items:
        by_removal.setdefault(item.removal_id, []).append(item)
    groups = []
    for rows in by_removal.values():
        if not any(row.state == 'pending' for row in rows):
            continue
        removal = rows[0].removal
        removed_user = removal.membership.user
        groups.append({
            'removal_id': str(removal.pk), 'from_member': {'name': person_name(removed_user)},
            'removed_at': removal.created_at,
            'revoked_connection_count': ConnectorConnection.objects.filter(
                account=ctx.account, owner_user=removed_user, state='revoked', revoked_reason='offboarding').count(),
            'items': [{'id': row.public_id, 'kind': row.kind, 'state': row.state, 'resolved_at': row.resolved_at,
                       'resume_status': row.resume_status,
                       'target': _handoff_target(row)} for row in rows]})
    groups.sort(key=lambda g: g['removed_at'], reverse=True)
    return groups


def _pending_handoff(ctx, item_id, kind):
    item = resolve(HandoffItem.objects.filter(account=ctx.account, successor=ctx.member).select_for_update(), item_id)
    if item.state != 'pending' or item.kind != kind:
        raise ValueError('STATE_CONFLICT')
    return item


@transaction.atomic
def accept_handoff(ctx, item_id, data):
    client_request_id(data.get('client_request_id'))
    item = _pending_handoff(ctx, item_id, 'task')
    task = Task.objects.select_for_update().filter(account=ctx.account, public_id=item.target_id).first()
    if task is None:
        raise ValueError('NOT_FOUND')
    if task.workspace_id and workspace_role(task.workspace, ctx.user) not in ('owner', 'editor'):
        raise ValueError('PERMISSION_DENIED')
    require_credits(ctx.account)
    task.owner = ctx.member
    fields = ['owner', 'updated_at']
    if task.status == 'paused' and task.pause_reason == 'handoff':
        task.status, task.pause_reason = item.resume_status or 'running', None
        fields += ['status', 'pause_reason']
    task.save(update_fields=fields)
    item.state, item.resolved_at = 'accepted', clock.now()
    item.save(update_fields=['state', 'resolved_at', 'updated_at'])
    return {'item': {'id': item.public_id, 'state': item.state}, 'task': {'id': task.public_id, 'status': task.status}}


@transaction.atomic
def reactivate_handoff(ctx, item_id, data):
    from . import automations
    client_request_id(data.get('client_request_id'))
    item = _pending_handoff(ctx, item_id, 'automation')
    automation = Automation.objects.select_for_update().filter(account=ctx.account, public_id=item.target_id).first()
    if automation is None:
        raise ValueError('NOT_FOUND')
    if automation.state != 'paused':
        raise ValueError('STATE_CONFLICT')
    automation.owner = ctx.member
    automation.save(update_fields=['owner', 'updated_at'])
    automations.ensure_runnable(automation)
    automations.activate(automation)
    item.state, item.resolved_at = 'reactivated', clock.now()
    item.save(update_fields=['state', 'resolved_at', 'updated_at'])
    return {'item': {'id': item.public_id, 'state': item.state},
            'automation': {'id': automation.public_id, 'state': automation.state}}
