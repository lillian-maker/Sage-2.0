"""Task domain rules: visibility, state machine (entity catalog 3.2), billing hooks and executor write-backs.

Visibility and edit levels come from services.workspaces (Q-P2 A, Q-P10 A). Billing goes through the
existing commerce hooks: business accounts start_shared_task / resume_shared_task / complete_shared_task,
personal accounts reserve_credits / release_credits keyed by Task.public_id (Q-C-10 A).
"""
import re
from fractions import Fraction

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from .. import clock

from backend.commerce import billing
from backend.commerce.models import DepartmentMember, Reservation, SharedLimitChange
from ..models import (AcceptanceCriterion, Artifact, AuthorizationRequest, ContentReview, Expert, Extension,
                      ExtensionInstall, KickoffQuestion, KnowledgeBase, KnowledgeDocument, Task, TaskDelivery,
                      TaskMessage, TaskParticipant, TaskStep, TaskThread, TaskUserState, Workspace)
from . import workspaces as ws
from .notify import notify

ACTIVE_STATUSES = ('running', 'awaiting_user', 'paused', 'stuck')
# Q-D-12 A: drafts and awaiting_kickoff belong to no group.
STATUS_GROUPS = {'needs_me': ('awaiting_user', 'delivered'), 'running': ('running', 'paused'), 'done': ('accepted',)}
TERMINAL = ('accepted', 'stopped')
PAUSABLE = ('running', 'awaiting_user', 'stuck')
STOPPABLE = ('running', 'awaiting_user', 'stuck', 'paused')
BEFORE_START = ('draft', 'awaiting_kickoff')
SCOPES = ('all', 'owned', 'created', 'shared', 'archived')
CRID = re.compile(r'[A-Za-z0-9._:-]{1,64}')
BODY_MAX = 8000
REASON_MAX = 500  # Q-V6 A
ANSWER_MAX = 500
CRITERIA_MAX = 10  # Q-V7 A
CRITERION_MAX = 200
ID_LIST_MAX = 20
SHARED_TASK_TYPE = 'workbench_task'
SOURCE_LABELS = {'company_email': '公司邮箱', 'amazon_buyer_messages': 'Amazon.de 买家消息',
                 'store_notifications': '店铺通知', 'feishu': '飞书', 'wecom': '企业微信'}


# Validation ---------------------------------------------------------------

def client_request_id(value):
    if not isinstance(value, str) or not CRID.fullmatch(value):
        raise ValueError('VALIDATION_FAILED')
    return value


def text_value(value, maximum, required=True):
    return ws.text_field(value, maximum, required)


def id_list(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > ID_LIST_MAX or not all(isinstance(item, str) for item in value):
        raise ValueError('VALIDATION_FAILED')
    return list(dict.fromkeys(value))


def model_mode(value):
    if value not in ('auto', 'standard', 'deep'):
        raise ValueError('VALIDATION_FAILED')
    return value


# Access -------------------------------------------------------------------

def relation_q(user):
    """Tasks the user takes part in personally (owner, creator or active human participant)."""
    return (Q(owner__user=user) | Q(creator__user=user)
            | Q(pk__in=TaskParticipant.objects.filter(user=user, kind='human', state='active').values('task_id')))


def get_task(ctx, task_id, level='view'):
    task = (Task.objects.filter(account=ctx.account, public_id=task_id)
            .select_related('owner', 'creator', 'workspace', 'lead_expert', 'shared_task').first())
    access = ws.task_access(ctx, task)
    if access is None:
        raise ValueError('NOT_FOUND')
    if level == 'edit' and access != 'edit':
        raise ValueError('PERMISSION_DENIED')
    if level == 'owner' and task.owner.user_id != ctx.user.pk:
        raise ValueError('PERMISSION_DENIED')
    return task


def lock(task):
    return Task.objects.select_for_update().select_related('owner', 'creator', 'workspace', 'shared_task').get(pk=task.pk)


def editable_workspace(ctx, public_id):
    workspace = Workspace.objects.filter(account=ctx.account, public_id=public_id, deleted_at__isnull=True).first()
    member = ws.my_member(ctx, workspace)
    if member is None:
        raise ValueError('NOT_FOUND')
    if member.role not in ws.EDIT_ROLES:
        raise ValueError('PERMISSION_DENIED')
    return workspace


def visible_documents(ctx, ids):
    docs = list(KnowledgeDocument.objects.filter(account=ctx.account, public_id__in=ids, deleted_at__isnull=True))
    if len(docs) != len(ids):
        raise ValueError('NOT_FOUND')
    return docs


def kb_documents(ctx, ids):
    """Whole-base attach (03 资料选择): every current document of each visible base joins the task."""
    workspace_ids = ws.member_rows(ctx).exclude(role='guest').values('workspace_id')
    bases = list(KnowledgeBase.objects.filter(account=ctx.account, public_id__in=ids).filter(
        Q(scope='personal', owner=ctx.user) | Q(scope='workspace', workspace_id__in=workspace_ids) |
        Q(scope='account')))
    if len(bases) != len(ids):
        raise ValueError('NOT_FOUND')
    return list(KnowledgeDocument.objects.filter(account=ctx.account, knowledge_base__in=bases,
                                                 deleted_at__isnull=True))


def visible_experts(ctx, ids):
    experts = list(Expert.objects.filter(Q(account__isnull=True) | Q(account=ctx.account), public_id__in=ids)
                   .exclude(status='archived'))
    if len(experts) != len(ids):
        raise ValueError('NOT_FOUND')
    return experts


def installed_skills(ctx, ids):
    # Q-A-2 B: the skill picker only lists installed skills.
    installed = ExtensionInstall.objects.filter(account=ctx.account, enabled=True).values('extension_id')
    skills = list(Extension.objects.filter(public_id__in=ids, kind='skill', pk__in=installed))
    if len(skills) != len(ids):
        raise ValueError('NOT_FOUND')
    return skills


# Serialization helpers ------------------------------------------------------

def expert_ref(expert):
    return ws.expert_ref(expert)


def workspace_ref(workspace):
    return {'id': workspace.public_id, 'name': workspace.name} if workspace else None


def user_ref(ctx, user):
    if user is None:
        return None
    return {'id': str(user.pk), 'display_name': ws.full_name(user), 'is_me': user.pk == ctx.user.pk}


def number(value):
    value = Fraction(value)
    return value.numerator if value.denominator == 1 else float(round(value, 2))


def current_revision(task):
    return task.steps.aggregate(top=Max('plan_revision'))['top'] or 1


def current_steps(task):
    return task.steps.filter(plan_revision=current_revision(task)).order_by('order')


def progress(task):
    steps = list(current_steps(task).values_list('status', flat=True))
    return {'done': sum(1 for status in steps if status == 'done'), 'total': len(steps)}


def current_step(task):
    steps = current_steps(task)
    step = (steps.filter(status__in=('running', 'awaiting_user', 'failed', 'missing_capability')).first()
            or steps.filter(status='queued').first())
    if step is None:
        return None
    return {'id': step.public_id, 'order': step.order, 'title': step.title, 'activity': step.activity,
            'status': step.status}


def expert_participants(task):
    rows = (task.participants.filter(kind='expert', state='active').select_related('expert')
            .order_by('role', 'created_at'))
    lead = [row for row in rows if row.role == 'lead']
    return lead + [row for row in rows if row.role != 'lead']


def used_credits_for_reservations(reservations):
    total = Fraction(0)
    for value in reservations.filter(state='settled').values_list('settled_quantity', flat=True):
        total += Fraction(value or '0')
    return total


def task_reservations(task):
    if task.shared_task_id:
        return Reservation.objects.filter(shared_task_id=task.shared_task_id)
    return Reservation.objects.filter(account=task.account, task_key=task.public_id, shared_task__isnull=True)


def task_used_credits(task):
    return number(used_credits_for_reservations(task_reservations(task)))


def step_used_credits(task, step):
    reservations = task_reservations(task).filter(cost_events__step_key=step.public_id).distinct()
    return number(used_credits_for_reservations(reservations))


def user_state(task, user):
    return TaskUserState.objects.filter(task=task, user=user).first()


def accepted_version(task):
    delivery = (task.deliveries.filter(decision='accepted').select_related('artifact_version')
                .order_by('-decided_at').first())
    return {'number': delivery.artifact_version.number} if delivery else None


# List rows ----------------------------------------------------------------

def active_item(task):
    run = task.source_automation_run
    return {
        'id': task.public_id, 'title': task.title, 'status': task.status,
        'workspace': workspace_ref(task.workspace), 'progress': progress(task), 'current_step': current_step(task),
        'stuck_rounds': task.stuck_rounds, 'last_activity_at': task.last_activity_at,
        'experts': [expert_ref(row.expert) for row in expert_participants(task)],
        'source_automation': {'id': run.automation.public_id, 'name': run.automation.name} if run else None,
    }


def list_item(ctx, task, states):
    state = states.get(task.pk)
    lead = task.lead_expert
    collaborators = task.participants.filter(kind='expert', state='active', role='collaborator').count()
    is_owner_or_creator = ctx.user.pk in (task.owner.user_id, task.creator.user_id)
    shared = (not is_owner_or_creator and task.participants.filter(
        user=ctx.user, kind='human', state='active').exists())
    return {
        'id': task.public_id, 'title': task.title, 'status': task.status,
        'workspace': workspace_ref(task.workspace),
        'lead_expert': expert_ref(lead),
        'counts': {'collaborators': collaborators},
        'owner': user_ref(ctx, task.owner.user),
        'shared_with_me': shared,
        'progress': progress(task),
        'accepted_version': accepted_version(task),
        'pending_authorization_count': task.authorization_requests.filter(decision='pending').count(),
        'last_activity_at': task.last_activity_at,
        'archived_at': task.archived_at,
        'pinned_at': state.pinned_at if state else None,
        'unread': ws.unread_map(ctx.user, [task])[task.pk],
    }


# Detail -------------------------------------------------------------------

def pause_info(task):
    if task.status != 'paused':
        return None
    notice = task.messages.filter(kind='quota_notice').order_by('-sent_at').first()
    department_name, member_share = None, None
    row = (DepartmentMember.objects.filter(membership=task.owner, ended_period__isnull=True)
           .select_related('department').first())
    if row:
        department_name, member_share = row.department.name, row.department.per_member_share
    # Q-A-7 C: an individually set limit wins over the department share.
    limit = SharedLimitChange.objects.filter(membership=task.owner).order_by('-version').first()
    if limit and limit.quantity is not None:
        member_share = number(limit.quantity)
    steps = list(current_steps(task))
    done = [step for step in steps if step.status == 'done']
    resume = next((step for step in steps if step.status != 'done'), None)
    available = not (task.shared_task and task.shared_task.resume_blocked)
    return {'reason': task.pause_reason, 'paused_at': notice.sent_at if notice else task.updated_at,
            'department_name': department_name, 'member_share': member_share,
            'completed_steps': len(done), 'resume_step': resume.order if resume else None,
            'resume_available': available}


def participant_row(ctx, row):
    return {'id': row.public_id, 'kind': row.kind, 'role': row.role, 'state': row.state,
            'expert': expert_ref(row.expert), 'user': user_ref(ctx, row.user)}


def pending_delivery(task):
    """714 屏任务级入口：最新一条待验收交付。"""
    delivery = (task.deliveries.filter(decision='pending').select_related('artifact_version__artifact')
                .order_by('-created_at', '-id').first())
    if delivery is None:
        return None
    version = delivery.artifact_version
    return {'id': delivery.public_id,
            'artifact_version': {'id': version.public_id, 'artifact_id': version.artifact.public_id,
                                 'title': version.artifact.title, 'number': version.number,
                                 'state': version.state}}


def task_authorization_row(row):
    return {'id': row.public_id, 'decision': row.decision, 'decision_scope': row.decision_scope,
            'decided_at': row.decided_at, 'estimate_state': row.estimate_state,
            'estimated_credits': row.estimated_credits, 'action_kind': row.action_kind,
            'purpose_text': row.purpose_text, 'scope_text': row.scope_text, 'scope_params': row.scope_params,
            'validity': row.validity, 'version': row.version,
            'extension': {'id': row.extension.public_id, 'name': row.extension.name},
            'requested_by_expert': expert_ref(row.requested_by_expert), 'created_at': row.created_at}


def playbook_template(task):
    template = task.playbook_template
    if not isinstance(template, dict):
        return None
    live = set(task.steps.values_list('public_id', flat=True))
    variables = [{'name': row['name'], 'default': row.get('default') or ''}
                 for row in template.get('variables') or [] if isinstance(row, dict) and row.get('name')]
    names = {row['name'] for row in variables}
    steps = [{'step': row['step'], 'variable': row.get('variable') if row.get('variable') in names else None}
             for row in template.get('steps') or [] if isinstance(row, dict) and row.get('step') in live]
    return {'name': template.get('name') or task.title, 'variables': variables, 'steps': steps}


def task_detail(ctx, task):
    state = user_state(task, ctx.user)
    participants = task.participants.filter(state='active').select_related('expert', 'user').order_by('created_at')
    run = task.source_automation_run
    item = task.source_inbox_item
    materials = (KnowledgeDocument.objects.filter(pk__in=TaskMessage.attached_documents.through.objects.filter(
        taskmessage__task=task).values('knowledgedocument_id')).count())
    revisions = current_revision(task) if task.steps.exists() else 1
    return {
        'id': task.public_id, 'title': task.title, 'status': task.status, 'complexity': task.complexity,
        'model_mode': task.model_mode, 'workspace': workspace_ref(task.workspace),
        'owner': user_ref(ctx, task.owner.user), 'creator': user_ref(ctx, task.creator.user),
        'lead_expert': expert_ref(task.lead_expert),
        'access': ws.task_access(ctx, task),
        'counts': {'artifacts': Artifact.objects.filter(source_task=task, trashed_at__isnull=True).count(),
                   'materials': materials, 'participants': participants.count()},
        'participants': [participant_row(ctx, row) for row in participants],
        'progress': progress(task),
        'replan_count': revisions - 1,
        'used_credits': task_used_credits(task),
        'pause_reason': task.pause_reason,
        'pause': pause_info(task),
        'stuck_rounds': task.stuck_rounds,
        'rule_version': {'number': task.rule_version.number} if task.rule_version else None,
        'scope_text': task.scope_text,
        'source_automation_run': {
            'id': run.public_id, 'automation_id': run.automation.public_id, 'automation_name': run.automation.name,
            'scheduled_for': run.scheduled_for, 'variables': run.variables} if run else None,
        'source_inbox_item': {
            'id': item.public_id, 'source_label': SOURCE_LABELS.get(item.source.kind, item.source.kind),
            'sender_label': item.sender_label, 'received_at': item.received_at,
            'original_text': item.original_text, 'translated_text': item.translated_text} if item else None,
        'suggestions': [{'id': row.public_id, 'text': row.text, 'requires_authorization': row.requires_authorization}
                        for row in task.suggestions.filter(state='open').order_by('created_at')[:3]],
        'reviews': [{'id': row.public_id, 'state': row.state}
                    for row in ContentReview.objects.filter(task=task).order_by('created_at', 'id')[:5]],
        'pending_delivery': pending_delivery(task),
        'playbook_template': playbook_template(task),
        'authorizations': [task_authorization_row(row) for row in
                           task.authorization_requests.select_related('extension', 'requested_by_expert')
                           .order_by('-created_at', '-id')[:10]],
        'forked_from_task': ({'id': task.forked_from_task.public_id, 'title': task.forked_from_task.title}
                             if task.forked_from_task else None),
        'forked_from_message': task.forked_from_message.public_id if task.forked_from_message else None,
        'archived_at': task.archived_at, 'accepted_at': task.accepted_at, 'stopped_at': task.stopped_at,
        'last_activity_at': task.last_activity_at, 'created_at': task.created_at,
        'pinned_at': state.pinned_at if state else None,
        'last_read_at': state.last_read_at if state else None,
        'marked_unread': state.marked_unread if state else False,
    }


# Messages -----------------------------------------------------------------

def main_thread(task):
    thread, _ = TaskThread.objects.get_or_create(task=task, kind='main', defaults={'account': task.account})
    return thread


def branch_groups(thread):
    """Latest edit group in a thread: [root, edit1, edit2...] ordered by branch_index, or None."""
    edited = (TaskMessage.objects.filter(thread=thread, parent_message__isnull=False)
              .order_by('-sent_at').select_related('parent_message').first())
    if edited is None:
        return None
    root = edited.parent_message
    return [root] + list(TaskMessage.objects.filter(thread=thread, parent_message=root).order_by('branch_index'))


def latest_branch_index(thread):
    group = branch_groups(thread)
    return group[-1].branch_index if group else 1


def thread_messages(thread, branch=None):
    """Messages of the selected branch (Q-A-13 A): shared history, one message of the edit group, then that branch."""
    messages = list(TaskMessage.objects.filter(thread=thread).order_by('sent_at', 'id')
                    .select_related('author_expert', 'author_user', 'parent_message'))
    group = branch_groups(thread) if thread.kind == 'main' else None
    if not group:
        return messages, {}
    if branch is None:
        branch = len(group)
    if not 1 <= branch <= len(group):
        raise ValueError('VALIDATION_FAILED')
    root, chosen = group[0], group[branch - 1]
    group_ids = {message.pk for message in group}
    chain_start = min((message.sent_at for message in messages if message.branch_index == root.branch_index),
                      default=root.sent_at)
    selected = []
    for message in messages:
        if message.pk in group_ids:
            if message.pk == chosen.pk:
                selected.append(message)
        elif message.sent_at < root.sent_at:
            # Older abandoned branches stay hidden; history shared by every branch is kept.
            if message.branch_index == root.branch_index or message.sent_at < chain_start:
                selected.append(message)
        elif message.branch_index == chosen.branch_index:
            selected.append(message)
    positions = {message.pk: (index + 1, len(group)) for index, message in enumerate(group)}
    return selected, positions


def author_role(task, message, roles):
    if message.author_kind == 'expert':
        return roles.get(('expert', message.author_expert_id))
    if message.author_kind == 'user':
        if message.author_user_id == task.owner.user_id:
            return 'owner'
        return roles.get(('human', message.author_user_id))
    return None


def card(message):
    if message.card_type == 'delivery' and message.card_id:
        delivery = (TaskDelivery.objects.filter(public_id=message.card_id, task_id=message.task_id)
                    .select_related('artifact_version__artifact').first())
        if delivery is None:
            return None
        criteria = delivery.task.criteria.filter(step__isnull=True, removed_at__isnull=True)
        version = delivery.artifact_version
        return {'type': 'delivery', 'id': delivery.public_id, 'summary': delivery.summary,
                'decision': delivery.decision, 'return_reason': delivery.return_reason,
                'criteria_met': criteria.filter(verdict='met').count(), 'criteria_total': criteria.count(),
                'artifact_version': {'id': version.public_id, 'artifact_id': version.artifact.public_id,
                                     'title': version.artifact.title, 'number': version.number,
                                     'state': version.state}}
    if message.card_type:
        return {'type': message.card_type, 'id': message.card_id}
    return None


def message_row(task, message, roles, positions=None):
    metrics = message.run_metrics or None
    if metrics is not None:
        steps = {key: metrics[key] for key in ('steps_done', 'steps_total') if key in metrics}
        parallel = metrics.get('parallel') if message.kind == 'parallel_card' else None
        metrics = {key: metrics.get(key) for key in ('tool_calls', 'documents_read', 'artifacts_created',
                                                     'elapsed_seconds')}
        metrics.update(steps)
        if parallel:
            metrics['parallel'] = parallel
    position = (positions or {}).get(message.pk)
    return {
        'id': message.public_id,
        'thread': 'main' if message.thread.kind == 'main' else message.thread.public_id,
        'kind': message.kind, 'body': message.body, 'sent_at': message.sent_at,
        'author_kind': message.author_kind, 'author_role': author_role(task, message, roles),
        'author_user': ({'id': str(message.author_user.pk), 'display_name': ws.full_name(message.author_user)}
                        if message.author_user else None),
        'author_expert': expert_ref(message.author_expert),
        'attachments': [{'id': doc.public_id, 'title': doc.title, 'index_state': doc.index_state}
                        for doc in message.attached_documents.order_by('id')],
        'citations': [{'id': row.public_id, 'label': row.external_label or (row.document.title if row.document else ''),
                       'document_id': row.document.public_id if row.document else None}
                      for row in message.citations.select_related('document').order_by('ref_index', 'id')],
        'run_metrics': metrics,
        'branch': {'index': position[0], 'count': position[1]} if position else None,
        'parent_message': message.parent_message.public_id if message.parent_message else None,
        'card': card(message),
        'is_important': message.is_important,
    }


def participant_roles(task):
    roles = {}
    for row in task.participants.filter(state='active'):
        if row.kind == 'expert':
            roles[('expert', row.expert_id)] = row.role
        else:
            roles[('human', row.user_id)] = row.role
    return roles


def touch(task, now=None):
    task.last_activity_at = now or clock.now()
    task.save(update_fields=['last_activity_at', 'updated_at'])


def post_message(ctx, task, thread, body, documents=(), experts=(), skills=(), branch_index=None, parent=None):
    message = TaskMessage.objects.create(
        account=task.account, task=task, thread=thread, author_kind='user', author_user=ctx.user, kind='text',
        body=body, parent_message=parent,
        branch_index=branch_index if branch_index is not None else (
            latest_branch_index(thread) if thread.kind == 'main' else 1))
    if documents:
        message.attached_documents.set(documents)
    if experts:
        message.mentioned_experts.set(experts)
    if skills:
        message.requested_skills.set(skills)
    touch(task, message.sent_at)
    return message


# Create / fork -------------------------------------------------------------

def title_from(body):
    first = body.strip().splitlines()[0].strip()
    return first[:80]


@transaction.atomic
def create_task(ctx, data, forked_from_message=None):
    body = text_value(data.get('body'), BODY_MAX)
    documents = visible_documents(ctx, id_list(data.get('attached_document_ids')))
    kb_ids = id_list(data.get('attached_knowledge_base_ids'))
    if kb_ids:
        seen = {doc.pk for doc in documents}
        documents += [doc for doc in kb_documents(ctx, kb_ids) if doc.pk not in seen]
    experts = visible_experts(ctx, id_list(data.get('mentioned_expert_ids')))
    skills = installed_skills(ctx, id_list(data.get('requested_skill_ids')))
    mode = model_mode(data.get('model_mode', 'auto'))
    workspace = None
    if data.get('workspace_id') is not None:
        if not isinstance(data['workspace_id'], str):
            raise ValueError('VALIDATION_FAILED')
        workspace = editable_workspace(ctx, data['workspace_id'])
    if forked_from_message is None and data.get('forked_from_message_id') is not None:
        forked_from_message = visible_message(ctx, data['forked_from_message_id'])
    if forked_from_message is not None and 'workspace_id' not in data:
        workspace = forked_from_message.task.workspace
    # Q-A-19 A: the task exists from the first send; the executor triages complexity afterwards.
    task = Task.objects.create(
        account=ctx.account, workspace=workspace, title=title_from(body), creator=ctx.member, owner=ctx.member,
        model_mode=mode, status='awaiting_kickoff',
        forked_from_task=forked_from_message.task if forked_from_message else None,
        forked_from_message=forked_from_message)
    TaskParticipant.objects.create(account=ctx.account, task=task, kind='human', user=ctx.user, role='lead',
                                   state='active')
    # Q-A-2 B: picked experts join as collaborators; the lead is chosen by the executor.
    for expert in experts:
        TaskParticipant.objects.create(account=ctx.account, task=task, kind='expert', expert=expert,
                                       role='collaborator', state='active', invited_by=ctx.user)
    thread = TaskThread.objects.create(account=ctx.account, task=task, kind='main')
    message = post_message(ctx, task, thread, body, documents, experts, skills, branch_index=1)
    return task, message


def visible_message(ctx, message_id):
    if not isinstance(message_id, str):
        raise ValueError('VALIDATION_FAILED')
    message = TaskMessage.objects.filter(account=ctx.account, public_id=message_id).select_related('task').first()
    if message is None or ws.task_access(ctx, message.task) is None:
        raise ValueError('NOT_FOUND')
    return message


def fork_task(ctx, task, data):
    message_id = data.get('message_id')
    if not isinstance(message_id, str):
        raise ValueError('VALIDATION_FAILED')
    message = TaskMessage.objects.filter(task=task, public_id=message_id).select_related('task').first()
    if message is None:
        raise ValueError('NOT_FOUND')
    payload = {key: value for key, value in data.items() if key != 'message_id'}
    return create_task(ctx, payload, forked_from_message=message)


# Lifecycle ------------------------------------------------------------------

def _billing(fn, *args, mapping=None, **kwargs):
    mapping = {'SEAT_REQUIRED': 'INSUFFICIENT_CREDITS', 'TASK_SEAT_REQUIRED': 'INSUFFICIENT_CREDITS',
               'FORBIDDEN': 'PERMISSION_DENIED', 'LEGACY_RESERVATION_REVIEW_REQUIRED': 'STATE_CONFLICT',
               'TASK_NOT_RUNNING': 'STATE_CONFLICT', 'UNSETTLED_USAGE_REQUIRED': 'STATE_CONFLICT',
               'TASK_RESULT_NOT_READY': 'STATE_CONFLICT', 'INVALID_IDENTIFIER': 'VALIDATION_FAILED',
               **(mapping or {})}
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        code = str(exc)
        raise ValueError(mapping.get(code, code)) from exc


def start_response(task):
    return {'task': {'id': task.public_id, 'status': task.status,
                     'rule_version': {'number': task.rule_version.number} if task.rule_version else None},
            'shared_task_id': str(task.shared_task_id) if task.shared_task_id else None}


def _answers(task, answers, user):
    if answers is None:
        return
    if not isinstance(answers, list) or len(answers) > 20:
        raise ValueError('VALIDATION_FAILED')
    questions = {row.public_id: row for row in task.kickoff_questions.all()}
    now = clock.now()
    for entry in answers:
        if not isinstance(entry, dict) or set(entry) != {'question_id', 'answer'} or entry['question_id'] not in questions:
            raise ValueError('VALIDATION_FAILED')
        question = questions[entry['question_id']]
        question.answer = text_value(entry['answer'], ANSWER_MAX)
        question.answered_by, question.answered_at = user, now
        question.save(update_fields=['answer', 'answered_by', 'answered_at', 'updated_at'])


def reserve_quantity(task):
    total = sum(step.estimated_credits or 0 for step in current_steps(task))
    return str(max(total, 1))


@transaction.atomic
def start_task(ctx, task, data):
    client_request_id(data.get('client_request_id'))
    task = lock(task)
    if task.status != 'awaiting_kickoff':
        # Q-V11 A: a replayed start of an already started task returns the first result.
        started = task.shared_task_id or Reservation.objects.filter(account=task.account, task_key=task.public_id).exists()
        if task.status == 'running' and started and data.get('answers') is None:
            return start_response(task)
        raise ValueError('STATE_CONFLICT')
    _answers(task, data.get('answers'), ctx.user)
    # Q-A-10 B: unanswered questions stay null and are left to the experts' defaults.
    if task.workspace_id and task.workspace.current_rule_version_id:
        task.rule_version = task.workspace.current_rule_version
    if ctx.account.kind == 'business':
        shared = _billing(billing.start_shared_task, ctx.account, task.owner.pk, task.public_id, SHARED_TASK_TYPE)
        task.shared_task_id = shared['id']
        task.save(update_fields=['shared_task'])
        task.refresh_from_db(fields=['shared_task'])
        scope = billing._business_task_credit_scope(ctx.account, task.shared_task)
        if not scope['can_consume'] or scope['available'] <= 0:
            raise ValueError('INSUFFICIENT_CREDITS')
    else:
        _billing(billing.reserve_credits, ctx.account, task.public_id, reserve_quantity(task))
    task.status, task.pause_reason = 'running', None
    task.last_activity_at = clock.now()
    task.save(update_fields=['status', 'pause_reason', 'rule_version', 'last_activity_at', 'updated_at'])
    _ensure_kickoff_card(task)
    return start_response(task)


def _ensure_kickoff_card(task):
    """D96: the read-only 开工确认卡 after 开始执行 keys off the kickoff_card message; tasks created through
    /tasks/new never got one (only the seeds wrote it), so their confirmed brief disappeared after start."""
    thread = main_thread(task)
    if thread.messages.filter(kind='kickoff_card').exists():
        return
    expert = task.lead_expert
    TaskMessage.objects.create(
        account=task.account, task=task, thread=thread,
        author_kind='expert' if expert else 'system', author_expert=expert,
        kind='kickoff_card', body='开始前先和你确认几件事。确认后我才会开始执行，不会提前消耗 Credits。',
        card_type='kickoff', card_id=task.public_id, sent_at=clock.now(),
        branch_index=latest_branch_index(thread))


@transaction.atomic
def pause_task(ctx, task, data):
    client_request_id(data.get('client_request_id'))
    task = lock(task)
    if task.status == 'paused' and task.pause_reason == 'user':
        return {'task': {'id': task.public_id, 'status': task.status, 'pause_reason': task.pause_reason}}
    if task.status not in PAUSABLE:
        raise ValueError('STATE_CONFLICT')
    task.status, task.pause_reason = 'paused', 'user'
    task.last_activity_at = clock.now()
    task.save(update_fields=['status', 'pause_reason', 'last_activity_at', 'updated_at'])
    return {'task': {'id': task.public_id, 'status': task.status, 'pause_reason': task.pause_reason}}


@transaction.atomic
def resume_task(ctx, task, data):
    client_request_id(data.get('client_request_id'))
    task = lock(task)
    if task.status == 'running' and task.pause_reason is None:
        return {'task': {'id': task.public_id, 'status': task.status, 'pause_reason': None}}
    if task.status != 'paused':
        raise ValueError('STATE_CONFLICT')
    if task.shared_task_id:
        _billing(billing.resume_shared_task, ctx.account, ctx.user, task.shared_task_id)
    task.status, task.pause_reason = 'running', None
    task.last_activity_at = clock.now()
    task.save(update_fields=['status', 'pause_reason', 'last_activity_at', 'updated_at'])
    return {'task': {'id': task.public_id, 'status': task.status, 'pause_reason': None}}


QUOTA_ADMIN_ROLES = ('owner', 'billing_admin')


def remind_quota_admins(ctx, task):
    """14 屏「提醒管理员」: one in-app notification per administrator, per task and day (dedupe_key)."""
    from backend.models import Membership
    from ..models import AppNotification
    if task.status != 'paused' or task.pause_reason != 'quota_exhausted':
        raise ValueError('STATE_CONFLICT')
    active = Membership.objects.filter(account=ctx.account, is_active=True, user__is_active=True).select_related('user')
    admins = list(active.filter(role__in=QUOTA_ADMIN_ROLES).exclude(user=ctx.user).order_by('pk'))
    if not admins:
        # Q-A-8 A: with no other administrator the reminder falls back to the owner, even when that is the caller.
        admins = list(active.filter(role='owner').order_by('pk'))
    if not admins:
        raise ValueError('STATE_CONFLICT')
    key = 'quota-reminder:%s:%s' % (task.public_id, clock.localdate().isoformat())
    created = not AppNotification.objects.filter(dedupe_key=key, recipient__in=[m.user for m in admins]).exists()
    owner = ctx.user.get_full_name() or (ctx.user.email.split('@')[0] if ctx.user.email else '成员')
    for admin in admins:
        notify(account=ctx.account, recipient=admin.user, category='task_progress', type='quota_reminder_to_admin',
               text='%s 的任务“%s”因本月额度用完已暂停，请调高额度' % (owner, task.title), dedupe_key=key,
               context_label=task.title, target_type='org_quota', target_id=task.public_id, action='open')
    return {'reminded': len(admins), 'created': created}


@transaction.atomic
def stop_task(ctx, task, data):
    client_request_id(data.get('client_request_id'))
    task = lock(task)
    if task.status == 'stopped':
        return {'task': {'id': task.public_id, 'status': task.status, 'stopped_at': task.stopped_at}}
    if task.status not in STOPPABLE:
        raise ValueError('STATE_CONFLICT')
    if task.shared_task_id and task.shared_task.state == 'running':
        _billing(billing.complete_shared_task, ctx.account, task.shared_task_id, result_persisted=True)
    reservation = Reservation.objects.filter(account=ctx.account, task_key=task.public_id,
                                             shared_task__isnull=True, state='reserved').first()
    if reservation:
        _billing(billing.release_credits, ctx.account, reservation.pk)
    now = clock.now()
    task.status, task.stopped_at, task.last_activity_at = 'stopped', now, now
    task.save(update_fields=['status', 'stopped_at', 'last_activity_at', 'updated_at'])
    task.steps.filter(status__in=('queued', 'running', 'awaiting_user')).update(status='skipped', updated_at=now)
    return {'task': {'id': task.public_id, 'status': task.status, 'stopped_at': task.stopped_at}}


def get_delivery(task, delivery_id):
    delivery = (TaskDelivery.objects.filter(task=task, public_id=delivery_id)
                .select_related('artifact_version__artifact').first())
    if delivery is None:
        raise ValueError('NOT_FOUND')
    return delivery


def accept_response(task, delivery):
    version = delivery.artifact_version
    return {'delivery': {'id': delivery.public_id, 'decision': delivery.decision, 'decided_at': delivery.decided_at},
            'task': {'id': task.public_id, 'status': task.status, 'accepted_at': task.accepted_at},
            'artifact_version': {'id': version.public_id, 'number': version.number, 'state': version.state}}


@transaction.atomic
def accept_delivery(ctx, task, delivery_id, data):
    client_request_id(data.get('client_request_id'))
    task = lock(task)
    delivery = get_delivery(task, delivery_id)
    if delivery.decision == 'accepted':
        return accept_response(task, delivery)
    if delivery.decision != 'pending' or task.status != 'delivered':
        raise ValueError('STATE_CONFLICT')
    if task.shared_task_id:
        _billing(billing.complete_shared_task, ctx.account, task.shared_task_id, result_persisted=True)
    now = clock.now()
    delivery.decision, delivery.decided_by, delivery.decided_at = 'accepted', ctx.user, now
    delivery.save(update_fields=['decision', 'decided_by', 'decided_at', 'updated_at'])
    version = delivery.artifact_version
    version.state = 'accepted'
    version.save(update_fields=['state', 'updated_at'])
    artifact = version.artifact
    artifact.current_version = version
    artifact.save(update_fields=['current_version', 'updated_at'])
    task.status, task.accepted_at, task.last_activity_at = 'accepted', now, now
    task.save(update_fields=['status', 'accepted_at', 'last_activity_at', 'updated_at'])
    return accept_response(task, delivery)


@transaction.atomic
def return_delivery(ctx, task, delivery_id, data):
    client_request_id(data.get('client_request_id'))
    reason = text_value(data.get('reason'), REASON_MAX)
    verdicts = data.get('verdicts')
    task = lock(task)
    delivery = get_delivery(task, delivery_id)
    response = lambda: {'delivery': {'id': delivery.public_id, 'decision': delivery.decision,  # noqa: E731
                                     'return_reason': delivery.return_reason, 'decided_at': delivery.decided_at},
                        'task': {'id': task.public_id, 'status': task.status}}
    if delivery.decision == 'returned' and delivery.return_reason == reason:
        return response()
    if delivery.decision != 'pending' or task.status != 'delivered':
        raise ValueError('STATE_CONFLICT')
    if verdicts is not None:
        # Q-A-9 A: the person returning marks each criterion.
        if not isinstance(verdicts, list) or len(verdicts) > CRITERIA_MAX:
            raise ValueError('VALIDATION_FAILED')
        criteria = {row.public_id: row for row in task.criteria.filter(removed_at__isnull=True)}
        for entry in verdicts:
            if (not isinstance(entry, dict) or set(entry) != {'criterion_id', 'verdict'}
                    or entry['criterion_id'] not in criteria or entry['verdict'] not in ('met', 'unmet', 'pending')):
                raise ValueError('VALIDATION_FAILED')
            row = criteria[entry['criterion_id']]
            row.verdict = entry['verdict']
            row.save(update_fields=['verdict', 'updated_at'])
    now = clock.now()
    delivery.decision, delivery.return_reason = 'returned', reason
    delivery.decided_by, delivery.decided_at = ctx.user, now
    delivery.save(update_fields=['decision', 'return_reason', 'decided_by', 'decided_at', 'updated_at'])
    task.status, task.last_activity_at = 'needs_revision', now
    task.save(update_fields=['status', 'last_activity_at', 'updated_at'])
    return response()


@transaction.atomic
def edit_message(ctx, task, message_id, data):
    body = text_value(data.get('body'), BODY_MAX)
    client_request_id(data.get('client_request_id'))
    task = lock(task)
    thread = main_thread(task)
    target = TaskMessage.objects.filter(task=task, public_id=message_id).select_related('parent_message').first()
    if target is None:
        raise ValueError('NOT_FOUND')
    messages, _ = thread_messages(thread)
    user_messages = [message for message in messages if message.author_kind == 'user']
    latest = user_messages[-1] if user_messages else None
    root = target.parent_message or target
    if (latest is not None and latest.pk != target.pk and latest.parent_message_id == root.pk
            and latest.body == body and latest.author_user_id == ctx.user.pk):
        return edit_response(task, latest)
    if task.status in TERMINAL or latest is None or latest.pk != target.pk or target.thread_id != thread.pk:
        raise ValueError('STATE_CONFLICT')
    top = TaskMessage.objects.filter(thread=thread).aggregate(top=Max('branch_index'))['top'] or 1
    message = post_message(ctx, task, thread, body, list(target.attached_documents.all()),
                           list(target.mentioned_experts.all()), list(target.requested_skills.all()),
                           branch_index=top + 1, parent=root)
    # Q-A-13 A: the current run is abandoned and the executor restarts on the new branch.
    if task.status in ('running', 'awaiting_user', 'stuck', 'delivered', 'needs_revision'):
        task.status, task.stuck_rounds = 'running', 0
        task.save(update_fields=['status', 'stuck_rounds', 'updated_at'])
    return edit_response(task, message)


def edit_response(task, message):
    return {'message': {'id': message.public_id, 'parent_message': message.parent_message.public_id,
                        'branch_index': message.branch_index, 'body': message.body, 'sent_at': message.sent_at},
            'task': {'id': task.public_id, 'status': task.status}}


def thread_row(thread):
    anchor = thread.anchor_message
    anchor_data = None
    if anchor:
        author = anchor.author_expert.name if anchor.author_expert else ws.full_name(anchor.author_user)
        anchor_data = {'message_id': anchor.public_id, 'excerpt': (anchor.body or '')[:60], 'author_name': author,
                       'sent_at': anchor.sent_at}
    return {'id': thread.public_id, 'kind': thread.kind, 'anchor': anchor_data,
            'expert': expert_ref(thread.expert), 'message_count': thread.messages.count(),
            'brought_back_message': thread.brought_back_message.public_id if thread.brought_back_message else None,
            'created_at': thread.created_at}


@transaction.atomic
def open_side_thread(ctx, task, data):
    anchor_id = data.get('anchor_message_id')
    if not isinstance(anchor_id, str):
        raise ValueError('VALIDATION_FAILED')
    anchor = TaskMessage.objects.filter(account=ctx.account, public_id=anchor_id).select_related('thread').first()
    if anchor is None:
        raise ValueError('NOT_FOUND')
    if anchor.task_id != task.pk or anchor.thread.kind != 'main':
        raise ValueError('VALIDATION_FAILED')
    existing = TaskThread.objects.filter(task=task, kind='side', anchor_message=anchor).first()
    if existing:
        return existing, False
    # Q-A-11 A: one side thread per anchor message, created on click.
    thread = TaskThread.objects.create(account=ctx.account, task=task, kind='side', anchor_message=anchor,
                                       expert=anchor.author_expert or task.lead_expert)
    return thread, True


@transaction.atomic
def bring_back(ctx, task, thread_id, data):
    client_request_id(data.get('client_request_id'))
    thread = TaskThread.objects.filter(task=task, public_id=thread_id).select_related('brought_back_message').first()
    if thread is None:
        raise ValueError('NOT_FOUND')
    if thread.kind == 'main':
        raise ValueError('STATE_CONFLICT')
    thread = TaskThread.objects.select_for_update().get(pk=thread.pk)
    if thread.brought_back_message_id is None:
        last = (thread.messages.filter(author_kind='expert').order_by('-sent_at').first()
                or thread.messages.order_by('-sent_at').first())
        if last is None:
            raise ValueError('STATE_CONFLICT')
        expert = thread.expert or task.lead_expert
        message = TaskMessage.objects.create(
            account=task.account, task=task, thread=main_thread(task), author_kind='expert' if expert else 'system',
            author_expert=expert, kind='summary', body='由侧聊带回主线：' + (last.body or ''),
            card_type='side_thread', card_id=thread.public_id, branch_index=latest_branch_index(main_thread(task)))
        thread.brought_back_message = message
        thread.save(update_fields=['brought_back_message', 'updated_at'])
        touch(task, message.sent_at)
    message = thread.brought_back_message
    return {'message': {'id': message.public_id, 'thread': 'main', 'body': message.body, 'sent_at': message.sent_at},
            'thread': {'id': thread.public_id, 'brought_back_message': message.public_id}}


# Kickoff, criteria, steps --------------------------------------------------------

def criterion_row(row):
    return {'id': row.public_id, 'text': row.text, 'evidence': row.evidence, 'verdict': row.verdict,
            'origin': row.origin, 'order': row.order, 'step_id': row.step.public_id if row.step else None}


def question_row(row):
    return {'id': row.public_id, 'order': row.order, 'question': row.question, 'options': row.options or [],
            'answer': row.answer, 'answered_at': row.answered_at}


def step_brief(step):
    return {'id': step.public_id, 'order': step.order, 'stage': step.stage, 'title': step.title, 'label': step.label,
            'expert': expert_ref(step.expert), 'skill': extension_ref(step.skill), 'status': step.status}


def extension_ref(extension):
    if extension is None:
        return None
    return {'id': extension.public_id, 'name': extension.name, 'kind': extension.kind}


def kickoff(task):
    steps = list(current_steps(task).select_related('expert', 'skill', 'required_extension'))
    rule = task.rule_version or (task.workspace.current_rule_version if task.workspace else None)
    criteria = task.criteria.filter(step__isnull=True, removed_at__isnull=True).order_by('order')
    return {
        'task_id': task.public_id, 'status': task.status, 'complexity': task.complexity,
        'estimated_expert_count': task.estimated_expert_count,
        'estimated_duration_minutes': task.estimated_duration_minutes,
        'questions': [question_row(row) for row in task.kickoff_questions.order_by('order')],
        'brief': {'goal': task.goal, 'scope_text': task.scope_text, 'deliverables': task.deliverables,
                  'constraints': task.constraints, 'rule_version': {'number': rule.number} if rule else None},
        'criteria': [criterion_row(row) for row in criteria],
        'missing_capabilities': [{'step_id': step.public_id, 'title': step.title,
                                  'extension': extension_ref(step.required_extension)}
                                 for step in steps if step.status == 'missing_capability'],
        'plan': {'step_count': len(steps), 'expert_count': len({step.expert_id for step in steps if step.expert_id}),
                 'steps': [step_brief(step) for step in steps]},
    }


def require_before_start(task):
    if task.status not in BEFORE_START:
        raise ValueError('STATE_CONFLICT')


@transaction.atomic
def answer_question(ctx, task, question_id, data):
    task = lock(task)
    require_before_start(task)
    question = task.kickoff_questions.filter(public_id=question_id).first()
    if question is None:
        raise ValueError('NOT_FOUND')
    answer = text_value(data.get('answer'), ANSWER_MAX, required=False)
    question.answer = answer
    question.answered_by = ctx.user if answer else None
    question.answered_at = clock.now() if answer else None
    question.save(update_fields=['answer', 'answered_by', 'answered_at', 'updated_at'])
    return question


@transaction.atomic
def add_criterion(ctx, task, data):
    task = lock(task)
    require_before_start(task)
    text = text_value(data.get('text'), CRITERION_MAX)
    active = task.criteria.filter(step__isnull=True, removed_at__isnull=True)
    if active.count() >= CRITERIA_MAX:
        raise ValueError('VALIDATION_FAILED')
    order = (task.criteria.filter(step__isnull=True).aggregate(top=Max('order'))['top'] or 0) + 1
    return AcceptanceCriterion.objects.create(account=task.account, task=task, order=order, text=text, origin='user')


def get_criterion(task, criterion_id):
    row = task.criteria.filter(public_id=criterion_id, removed_at__isnull=True).select_related('step').first()
    if row is None:
        raise ValueError('NOT_FOUND')
    return row


@transaction.atomic
def update_criterion(ctx, task, criterion_id, data):
    task = lock(task)
    row = get_criterion(task, criterion_id)
    fields = []
    if 'text' in data:
        require_before_start(task)
        row.text = text_value(data['text'], CRITERION_MAX)
        fields.append('text')
    if 'verdict' in data:
        # Q-A-9 A: verdicts are marked by the user while reviewing a delivery.
        if task.status not in ('delivered', 'needs_revision') or data['verdict'] not in ('pending', 'met', 'unmet'):
            raise ValueError('STATE_CONFLICT' if data['verdict'] in ('pending', 'met', 'unmet') else 'VALIDATION_FAILED')
        row.verdict = data['verdict']
        fields.append('verdict')
    if not fields:
        raise ValueError('VALIDATION_FAILED')
    row.save(update_fields=fields + ['updated_at'])
    return row


@transaction.atomic
def remove_criterion(ctx, task, criterion_id):
    task = lock(task)
    require_before_start(task)
    row = get_criterion(task, criterion_id)
    row.removed_at = clock.now()
    row.save(update_fields=['removed_at', 'updated_at'])
    return row


def duration_seconds(step):
    if step.started_at and step.finished_at:
        return int((step.finished_at - step.started_at).total_seconds())
    return None


def step_row(task, step):
    authorization = (AuthorizationRequest.objects.filter(task=task, step=step)
                     .order_by('decision', '-created_at').first())
    criteria = step.criteria.filter(removed_at__isnull=True)
    return {
        **step_brief(step),
        'required_extension': extension_ref(step.required_extension),
        'estimated_credits': step.estimated_credits, 'used_credits': step_used_credits(task, step),
        'duration_seconds': duration_seconds(step), 'started_at': step.started_at,
        'finished_at': step.finished_at, 'activity': step.activity, 'inputs': step.inputs or [],
        'depends_on': [row.public_id for row in step.depends_on.order_by('order')],
        'in_background': step.in_background,
        'assist_experts': [expert_ref(row) for row in step.assist_experts.order_by('id')],
        'authorization_id': authorization.public_id if authorization else None,
        'criteria_total': criteria.count(), 'criteria_met': criteria.filter(verdict='met').count(),
    }


def steps(task, statuses=None):
    rows = list(current_steps(task).select_related('expert', 'skill', 'required_extension'))
    summary = {'running': sum(1 for row in rows if row.status == 'running'),
               'in_background': sum(1 for row in rows if row.status == 'running' and row.in_background),
               'awaiting_user': sum(1 for row in rows if row.status == 'awaiting_user'),
               'done': sum(1 for row in rows if row.status == 'done')}
    if statuses:
        rows = [row for row in rows if row.status in statuses]
    return {'items': [step_row(task, row) for row in rows], 'total': len(rows), 'summary': summary,
            'plan_revision': current_revision(task)}


# Per-user state --------------------------------------------------------------

def state_for(task, user):
    state, _ = TaskUserState.objects.get_or_create(task=task, user=user, defaults={'account': task.account})
    return state


# Executor write-backs (Q-R1 A: the executor reports, Django stores) -------------------

@transaction.atomic
def executor_triage(task, *, complexity, estimated_expert_count=None, estimated_duration_minutes=None,
                    goal=None, scope_text=None, deliverables=None, constraints=None, questions=(), criteria=()):
    """Record kickoff results; simple tasks skip the kickoff card (T3)."""
    task = lock(task)
    if task.status not in BEFORE_START:
        raise ValueError('STATE_CONFLICT')
    task.complexity = complexity
    task.estimated_expert_count, task.estimated_duration_minutes = estimated_expert_count, estimated_duration_minutes
    task.goal, task.scope_text, task.deliverables, task.constraints = goal, scope_text, deliverables, constraints
    task.status = 'awaiting_kickoff'
    task.save()
    for order, (question, options) in enumerate(questions, start=1):
        KickoffQuestion.objects.create(account=task.account, task=task, order=order, question=question,
                                       options=list(options))
    for order, text in enumerate(criteria, start=1):
        AcceptanceCriterion.objects.create(account=task.account, task=task, order=order, text=text, origin='ai')
    return task


@transaction.atomic
def executor_update_step(step, *, status, activity=None, now=None):
    """Advance one step and keep Task.status in line (T5 when a step waits for the user)."""
    now = now or clock.now()
    step = TaskStep.objects.select_for_update().select_related('task').get(pk=step.pk)
    task = lock(step.task)
    if task.status in TERMINAL or task.status == 'paused':
        raise ValueError('STATE_CONFLICT')
    allowed = {'queued': ('running', 'skipped', 'missing_capability'), 'running': ('done', 'failed', 'awaiting_user'),
               'awaiting_user': ('running', 'skipped'), 'failed': ('running', 'skipped'),
               'missing_capability': ('queued', 'skipped')}
    if status not in allowed.get(step.status, ()):
        raise ValueError('STATE_CONFLICT')
    step.status = status
    if activity is not None:
        step.activity = activity[:120]
    if status == 'running' and step.started_at is None:
        step.started_at = now
    if status in ('done', 'failed', 'skipped'):
        step.finished_at = now
    step.save()
    if status == 'awaiting_user' and task.status == 'running':
        task.status = 'awaiting_user'
    elif status == 'running' and task.status in ('awaiting_user', 'stuck', 'needs_revision'):
        task.status, task.stuck_rounds = 'running', 0
    task.last_activity_at = now
    task.save(update_fields=['status', 'stuck_rounds', 'last_activity_at', 'updated_at'])
    if status == 'done':
        notify(account=task.account, recipient=task.owner.user, category='task_progress', type='step_completed',
               text='第 %d 步已完成：%s' % (step.order, step.title), dedupe_key='step:%s:done' % step.public_id,
               context_label=task.title, target_type='task', target_id=task.public_id, action='open',
               actor_expert=step.expert, occurred_at=now)
    return step


@transaction.atomic
def executor_mark_stuck(task, rounds):
    """T9: the executor decides what one round is (Q-R11); the backend only stores it."""
    task = lock(task)
    if task.status not in ('running', 'stuck'):
        raise ValueError('STATE_CONFLICT')
    task.status, task.stuck_rounds = 'stuck', rounds
    task.save(update_fields=['status', 'stuck_rounds', 'updated_at'])
    return task


@transaction.atomic
def executor_post_message(task, *, expert, body, kind='text', thread=None, run_metrics=None, now=None):
    thread = thread or main_thread(task)
    message = TaskMessage.objects.create(
        account=task.account, task=task, thread=thread, author_kind='expert', author_expert=expert, kind=kind,
        body=body, run_metrics=run_metrics, sent_at=now or clock.now(),
        branch_index=latest_branch_index(thread) if thread.kind == 'main' else 1)
    touch(task, message.sent_at)
    return message


@transaction.atomic
def executor_pause_for_quota(task, *, body, now=None):
    """T7 caused by billing (consume_shared_task returned INSUFFICIENT_CREDITS)."""
    task = lock(task)
    if task.status not in PAUSABLE:
        raise ValueError('STATE_CONFLICT')
    now = now or clock.now()
    task.status, task.pause_reason, task.last_activity_at = 'paused', 'quota_exhausted', now
    task.save(update_fields=['status', 'pause_reason', 'last_activity_at', 'updated_at'])
    TaskMessage.objects.create(account=task.account, task=task, thread=main_thread(task), author_kind='system',
                               kind='quota_notice', body=body, sent_at=now)
    return task


@transaction.atomic
def executor_deliver(task, *, artifact_version, summary, expert, body=None, suggestions=(), now=None):
    """T11: post the delivery card and wait for acceptance."""
    task = lock(task)
    if task.status not in ('running', 'needs_revision'):
        raise ValueError('STATE_CONFLICT')
    now = now or clock.now()
    delivery = TaskDelivery.objects.create(account=task.account, task=task, artifact_version=artifact_version,
                                           summary=summary)
    TaskMessage.objects.create(account=task.account, task=task, thread=main_thread(task), author_kind='expert',
                               author_expert=expert, kind='delivery_card', body=body or summary,
                               card_type='delivery', card_id=delivery.public_id, sent_at=now,
                               branch_index=latest_branch_index(main_thread(task)))
    for text, requires_authorization in suggestions:
        task.suggestions.create(account=task.account, text=text, requires_authorization=requires_authorization)
    task.status, task.last_activity_at = 'delivered', now
    task.save(update_fields=['status', 'last_activity_at', 'updated_at'])
    notify(account=task.account, recipient=task.owner.user, category='task_progress', type='delivered',
           text='%s 已交付，等你验收' % task.title, dedupe_key='delivery:%s' % delivery.public_id,
           context_label=task.title, target_type='task', target_id=task.public_id, action='open',
           actor_expert=expert, occurred_at=now)
    return delivery
