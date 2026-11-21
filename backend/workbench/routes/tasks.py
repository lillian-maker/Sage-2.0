"""app/tasks routes (backend-plan 2.1–2.3, section 3 screens 03–15, 20–24, 50)."""
from django.db.models import Q
from django.utils import timezone
from .. import clock

from .. import common
from ..models import Task, TaskMessage, TaskThread
from ..services import tasks as svc
from ..services import workspaces as ws

BODY_LIMIT = 64 * 1024


def _active(request, ctx):
    params = common.query(request, ('filter', 'offset'))
    choice = params.get('filter', 'all')
    if choice not in ('all', 'stuck', 'awaiting_user'):
        raise ValueError('VALIDATION_FAILED')
    base = (Task.objects.filter(account=ctx.account, archived_at__isnull=True, status__in=svc.ACTIVE_STATUSES)
            .filter(svc.relation_q(ctx.user)).distinct())
    counts = {'all': base.count(), 'stuck': base.filter(status='stuck').count(),
              'awaiting_user': base.filter(status='awaiting_user').count()}
    rows = base if choice == 'all' else base.filter(status=choice)
    rows = rows.select_related('workspace', 'source_automation_run__automation').order_by('-last_activity_at', '-id')
    return {'counts': counts, **common.page(request, rows, svc.active_item)}


def _filtered(ctx, params):
    """Base set and the non-tab filters; counts follow "every filter except the current tab" (C-F-1)."""
    workspace = params.get('workspace')
    if workspace is None:
        base = Task.objects.filter(account=ctx.account).filter(svc.relation_q(ctx.user))
    elif workspace == 'none':
        base = Task.objects.filter(account=ctx.account, workspace__isnull=True).filter(svc.relation_q(ctx.user))
    else:
        target, _ = ws.get_workspace(ctx, workspace)
        base = Task.objects.filter(account=ctx.account, workspace=target).filter(ws.visible_tasks_q(ctx))
    if 'status' in params:
        statuses = params['status'].split(',')
        if not all(status in Task.STATUSES for status in statuses):
            raise ValueError('VALIDATION_FAILED')
        base = base.filter(status__in=statuses)
    if 'expert' in params:
        base = base.filter(Q(lead_expert__public_id=params['expert']) | Q(
            participants__expert__public_id=params['expert'], participants__state='active'))
    if 'q' in params:
        text = params['q'].strip()
        if len(text) > 100:
            raise ValueError('VALIDATION_FAILED')
        if text:
            base = base.filter(title__icontains=text)
    return Task.objects.filter(pk__in=base.values('pk'))


def _scope(qs, scope, user):
    if scope == 'archived':
        return qs.filter(archived_at__isnull=False)
    qs = qs.filter(archived_at__isnull=True)
    if scope == 'owned':
        return qs.filter(owner__user=user)
    if scope == 'created':
        return qs.filter(creator__user=user)
    if scope == 'shared':
        # Q-F-2 A: shared excludes tasks I own or created.
        return qs.exclude(owner__user=user).exclude(creator__user=user).filter(
            participants__user=user, participants__kind='human', participants__state='active')
    return qs


def _group(qs, group):
    return qs if group == 'all' else qs.filter(status__in=svc.STATUS_GROUPS[group])


def _list(request, ctx):
    params = common.query(request, ('scope', 'workspace', 'status', 'status_group', 'expert', 'q', 'sort', 'offset'))
    scope, group = params.get('scope', 'all'), params.get('status_group', 'all')
    if scope not in svc.SCOPES or (group != 'all' and group not in svc.STATUS_GROUPS) or params.get('sort', 'updated') != 'updated':
        raise ValueError('VALIDATION_FAILED')
    base = _filtered(ctx, params)
    scoped = _scope(base, scope, ctx.user)
    counts = {name: _group(_scope(base, name, ctx.user), group).distinct().count() for name in svc.SCOPES}
    for name in svc.STATUS_GROUPS:
        counts[name] = _group(scoped, name).distinct().count()
    rows = (_group(scoped, group).distinct().select_related('workspace', 'lead_expert', 'owner__user', 'creator')
            .order_by('-last_activity_at', '-id'))
    states = {row.task_id: row for row in svc.TaskUserState.objects.filter(user=ctx.user, task__in=rows)}
    return {'counts': counts, **common.page(request, rows, lambda task: svc.list_item(ctx, task, states))}


def _create(request, ctx):
    data = common.json_body(request, ('body',), ('attached_document_ids', 'attached_knowledge_base_ids',
                                                 'mentioned_expert_ids',
                                                 'requested_skill_ids', 'model_mode', 'workspace_id',
                                                 'forked_from_message_id'), BODY_LIMIT)
    limited = ws.rate_limited(request, ctx, 'tasks_create', 30)
    if limited:
        return limited
    task, message = svc.create_task(ctx, data)
    return common.respond({'task': {'id': task.public_id, 'status': task.status, 'complexity': task.complexity,
                                    'forked_from_task': task.forked_from_task.public_id if task.forked_from_task else None,
                                    'forked_from_message': (task.forked_from_message.public_id
                                                            if task.forked_from_message else None)},
                           'message': {'id': message.public_id}}, 201)


def _detail(request, ctx, id):
    common.query(request)
    return svc.task_detail(ctx, svc.get_task(ctx, id))


def _update(request, ctx, id):
    data = common.json_body(request, (), ('title', 'workspace_id', 'model_mode'))
    if not data:
        raise ValueError('VALIDATION_FAILED')
    task = svc.get_task(ctx, id, 'owner')
    fields = []
    if 'title' in data:
        task.title = svc.text_value(data['title'], 80)
        fields.append('title')
    if 'model_mode' in data:
        task.model_mode = svc.model_mode(data['model_mode'])
        fields.append('model_mode')
    if 'workspace_id' in data:
        # Q-P9 A: editor of the source and of the target workspace.
        if task.workspace_id:
            member = ws.my_member(ctx, task.workspace)
            if member is None or member.role not in ws.EDIT_ROLES:
                raise ValueError('PERMISSION_DENIED')
        if data['workspace_id'] is None:
            task.workspace = None
        elif isinstance(data['workspace_id'], str):
            task.workspace = svc.editable_workspace(ctx, data['workspace_id'])
        else:
            raise ValueError('VALIDATION_FAILED')
        fields.append('workspace')
    task.save(update_fields=fields + ['updated_at'])
    return {'task': svc.task_detail(ctx, task)}


def _action(name, level='owner'):
    def handler(request, ctx, id):
        data = common.json_body(request, ('client_request_id',), ('answers',) if name == 'start' else ())
        task = svc.get_task(ctx, id, level)
        return getattr(svc, '%s_task' % name)(ctx, task, data)
    handler.__name__ = '_%s' % name
    return handler


def _fork(request, ctx, id):
    data = common.json_body(request, ('message_id', 'body'), ('attached_document_ids', 'mentioned_expert_ids',
                                                               'requested_skill_ids', 'model_mode', 'workspace_id'),
                            BODY_LIMIT)
    task = svc.get_task(ctx, id)
    limited = ws.rate_limited(request, ctx, 'tasks_create', 30)
    if limited:
        return limited
    new, message = svc.fork_task(ctx, task, data)
    return common.respond({'task': {'id': new.public_id, 'status': new.status, 'complexity': new.complexity,
                                    'forked_from_task': task.public_id,
                                    'forked_from_message': new.forked_from_message.public_id},
                           'message': {'id': message.public_id}}, 201)


def _archive(request, ctx, id):
    task = svc.get_task(ctx, id, 'owner')
    if task.archived_at is not None:
        raise ValueError('STATE_CONFLICT')
    task.archived_at = clock.now()
    task.save(update_fields=['archived_at', 'updated_at'])
    return {'task_id': task.public_id, 'archived_at': task.archived_at}


def _unarchive(request, ctx, id):
    task = svc.get_task(ctx, id, 'owner')
    if task.archived_at is None:
        raise ValueError('STATE_CONFLICT')
    task.archived_at = None
    task.save(update_fields=['archived_at', 'updated_at'])
    return {'task_id': task.public_id, 'archived_at': None}


def _pin(request, ctx, id):
    task = svc.get_task(ctx, id)
    state = svc.state_for(task, ctx.user)
    # Q-D-19 A: pinning is personal.
    if request.method == 'POST':
        state.pinned_at = state.pinned_at or clock.now()
    else:
        state.pinned_at = None
    state.save(update_fields=['pinned_at', 'updated_at'])
    return {'pinned_at': state.pinned_at}


def _read(request, ctx, id):
    task = svc.get_task(ctx, id)
    state = svc.state_for(task, ctx.user)
    if request.method == 'POST':
        state.last_read_at, state.marked_unread = clock.now(), False
    else:
        state.marked_unread = True
    state.save(update_fields=['last_read_at', 'marked_unread', 'updated_at'])
    return {'last_read_at': state.last_read_at, 'marked_unread': state.marked_unread}


def _thread(task, value):
    if value in (None, 'main'):
        return svc.main_thread(task)
    thread = TaskThread.objects.filter(task=task, public_id=value).first()
    if thread is None:
        raise ValueError('NOT_FOUND')
    return thread


def _messages(request, ctx, id):
    params = common.query(request, ('thread', 'branch', 'offset'))
    task = svc.get_task(ctx, id)
    thread = _thread(task, params.get('thread'))
    branch = None
    if 'branch' in params:
        if not params['branch'].isdigit():
            raise ValueError('VALIDATION_FAILED')
        branch = int(params['branch'])
    messages, positions = svc.thread_messages(thread, branch)
    roles = svc.participant_roles(task)
    return common.page(request, messages, lambda message: svc.message_row(task, message, roles, positions))


def _post_message(request, ctx, id):
    data = common.json_body(request, ('body',), ('thread', 'attached_document_ids'), BODY_LIMIT)
    task = svc.get_task(ctx, id, 'edit')
    body = svc.text_value(data['body'], svc.BODY_MAX)
    if data.get('thread') is not None and not isinstance(data['thread'], str):
        raise ValueError('VALIDATION_FAILED')
    thread = _thread(task, data.get('thread'))
    documents = svc.visible_documents(ctx, svc.id_list(data.get('attached_document_ids')))
    limited = ws.rate_limited(request, ctx, 'task_messages', 60)
    if limited:
        return limited
    message = svc.post_message(ctx, task, thread, body, documents)
    return common.respond({'message': svc.message_row(task, message, svc.participant_roles(task))}, 201)


def _patch_message(request, ctx, id, mid):
    data = common.json_body(request, ('is_important',))
    task = svc.get_task(ctx, id)
    message = TaskMessage.objects.filter(task=task, public_id=mid).first()
    if message is None:
        raise ValueError('NOT_FOUND')
    if ws.task_access(ctx, task) != 'edit':
        raise ValueError('PERMISSION_DENIED')
    if not isinstance(data['is_important'], bool):
        raise ValueError('VALIDATION_FAILED')
    message.is_important = data['is_important']
    message.save(update_fields=['is_important', 'updated_at'])
    return {'message': {'id': message.public_id, 'is_important': message.is_important}}


def _edit_message(request, ctx, id, mid):
    data = common.json_body(request, ('body', 'client_request_id'), (), BODY_LIMIT)
    task = svc.get_task(ctx, id, 'edit')
    return common.respond(svc.edit_message(ctx, task, mid, data), 201)


def _threads(request, ctx, id):
    params = common.query(request, ('kind', 'offset'))
    kind = params.get('kind', 'side')
    if kind not in ('side', 'main'):
        raise ValueError('VALIDATION_FAILED')
    task = svc.get_task(ctx, id)
    rows = (TaskThread.objects.filter(task=task, kind=kind)
            .select_related('anchor_message__author_expert', 'anchor_message__author_user', 'expert',
                            'brought_back_message').order_by('created_at', 'id'))
    return common.page(request, rows, svc.thread_row)


def _open_thread(request, ctx, id):
    data = common.json_body(request, ('anchor_message_id',))
    task = svc.get_task(ctx, id, 'edit')
    thread, created = svc.open_side_thread(ctx, task, data)
    return common.respond({'thread': {'id': thread.public_id, 'kind': thread.kind,
                                      'anchor_message': thread.anchor_message.public_id,
                                      'expert': svc.expert_ref(thread.expert)}}, 201 if created else 200)


def _bring_back(request, ctx, id, tid):
    data = common.json_body(request, ('client_request_id',))
    task = svc.get_task(ctx, id, 'edit')
    limited = ws.rate_limited(request, ctx, 'task_bring_back', 30)
    if limited:
        return limited
    return common.respond(svc.bring_back(ctx, task, tid, data), 201)


def _kickoff(request, ctx, id):
    common.query(request)
    return svc.kickoff(svc.get_task(ctx, id))


def _answer(request, ctx, id, qid):
    data = common.json_body(request, ('answer',))
    task = svc.get_task(ctx, id, 'owner')
    return {'question': svc.question_row(svc.answer_question(ctx, task, qid, data))}


def _criteria(request, ctx, id):
    params = common.query(request, ('step',))
    task = svc.get_task(ctx, id)
    rows = task.criteria.filter(removed_at__isnull=True).select_related('step').order_by('order', 'id')
    step = params.get('step', 'none')
    if step == 'none':
        rows = rows.filter(step__isnull=True)
    else:
        if not task.steps.filter(public_id=step).exists():
            raise ValueError('NOT_FOUND')
        rows = rows.filter(step__public_id=step)
    items = [svc.criterion_row(row) for row in rows]
    return {'items': items, 'total': len(items)}


def _add_criterion(request, ctx, id):
    data = common.json_body(request, ('text',))
    task = svc.get_task(ctx, id, 'owner')
    return common.respond({'criterion': svc.criterion_row(svc.add_criterion(ctx, task, data))}, 201)


def _criterion(request, ctx, id, cid):
    task = svc.get_task(ctx, id, 'owner')
    if request.method == 'DELETE':
        row = svc.remove_criterion(ctx, task, cid)
        return {'criterion': {'id': row.public_id, 'removed_at': row.removed_at}}
    data = common.json_body(request, (), ('text', 'verdict'))
    return {'criterion': svc.criterion_row(svc.update_criterion(ctx, task, cid, data))}


def _steps(request, ctx, id):
    params = common.query(request, ('status', 'offset'))
    task = svc.get_task(ctx, id)
    statuses = None
    if 'status' in params:
        statuses = params['status'].split(',')
        valid = {value for value, _ in svc.TaskStep._meta.get_field('status').choices}
        if not set(statuses) <= valid:
            raise ValueError('VALIDATION_FAILED')
    result = svc.steps(task, statuses)
    return {**result, 'offset': 0, 'next_offset': None}


def _accept(request, ctx, id, did):
    data = common.json_body(request, ('client_request_id',))
    task = svc.get_task(ctx, id, 'owner')
    return svc.accept_delivery(ctx, task, did, data)


def _return(request, ctx, id, did):
    data = common.json_body(request, ('reason', 'client_request_id'), ('verdicts',))
    task = svc.get_task(ctx, id, 'owner')
    return svc.return_delivery(ctx, task, did, data)


def _quota_reminder(request, ctx, id):
    common.json_body(request, ())
    task = svc.get_task(ctx, id, 'owner')
    result = svc.remind_quota_admins(ctx, task)
    return common.respond(result, 201 if result['created'] else 200)


ROUTES = [
    ('GET', 'tasks/active', _active),
    ('GET', 'tasks', _list),
    ('POST', 'tasks', _create),
    ('GET', 'tasks/{id}', _detail),
    ('PATCH', 'tasks/{id}', _update),
    ('POST', 'tasks/{id}/start', _action('start')),
    ('POST', 'tasks/{id}/pause', _action('pause')),
    ('POST', 'tasks/{id}/resume', _action('resume')),
    ('POST', 'tasks/{id}/stop', _action('stop')),
    ('POST', 'tasks/{id}/quota-reminders', _quota_reminder),
    ('POST', 'tasks/{id}/fork', _fork),
    ('POST', 'tasks/{id}/archive', _archive),
    ('POST', 'tasks/{id}/unarchive', _unarchive),
    ('POST', 'tasks/{id}/pin', _pin),
    ('DELETE', 'tasks/{id}/pin', _pin),
    ('POST', 'tasks/{id}/read', _read),
    ('DELETE', 'tasks/{id}/read', _read),
    ('GET', 'tasks/{id}/messages', _messages),
    ('POST', 'tasks/{id}/messages', _post_message),
    ('PATCH', 'tasks/{id}/messages/{mid}', _patch_message),
    ('POST', 'tasks/{id}/messages/{mid}/edit', _edit_message),
    ('GET', 'tasks/{id}/threads', _threads),
    ('POST', 'tasks/{id}/threads', _open_thread),
    ('POST', 'tasks/{id}/threads/{tid}/bring-back', _bring_back),
    ('GET', 'tasks/{id}/kickoff', _kickoff),
    ('PATCH', 'tasks/{id}/kickoff/questions/{qid}', _answer),
    ('GET', 'tasks/{id}/criteria', _criteria),
    ('POST', 'tasks/{id}/criteria', _add_criterion),
    ('PATCH', 'tasks/{id}/criteria/{cid}', _criterion),
    ('DELETE', 'tasks/{id}/criteria/{cid}', _criterion),
    ('GET', 'tasks/{id}/steps', _steps),
    ('POST', 'tasks/{id}/deliveries/{did}/accept', _accept),
    ('POST', 'tasks/{id}/deliveries/{did}/return', _return),
]
