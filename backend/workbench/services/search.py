"""app/search: one query across the objects the current member may see (catalog 4.1, C-F-15)."""
import logging

from django.db.models import Q

from ..common import PAGE_SIZE, offset as read_offset
from ..models import Artifact, Extension, KnowledgeDocument, Task, TaskMessage, TaskStep, Workspace, WorkspaceMember
from .automations import own_automations, schedule_dict
from . import workspaces as ws
from .inbox import own_items, visible_experts, visible_workspaces

logger = logging.getLogger(__name__)
CATEGORIES = ('all', 'tasks', 'workspaces', 'files', 'experts', 'knowledge', 'extensions', 'inbox', 'automations')
PREVIEW = 3
VERSION_LABELS = {'draft': '草稿', 'accepted': '已验收', 'published': '已发布'}
EXTENSION_LABELS = {'plugin': '插件', 'skill': '技能', 'connector': '连接器', 'agent': '智能体'}
WEEKDAYS = '一二三四五六日'


def snippet(text, q, before=20, after=40):
    index = (text or '').lower().find(q.lower())
    if index < 0:
        return None
    start, end = max(0, index - before), min(len(text), index + len(q) + after)
    return ('…' if start else '') + text[start:end] + ('…' if end < len(text) else '')


SENTENCE_END = '。！？；!?;\n'


def sentence_snippet(text, q, limit=60):
    """The sentence holding the first match, shown as an excerpt (“…sentence…”); long sentences fall back to a window."""
    text = text or ''
    index = text.lower().find(q.lower())
    if index < 0:
        return None
    start = max((text.rfind(ch, 0, index) for ch in SENTENCE_END), default=-1) + 1
    ends = [pos for pos in (text.find(ch, index + len(q)) for ch in SENTENCE_END) if pos >= 0]
    end = min(ends) if ends else len(text)
    sentence = text[start:end].strip()
    if not sentence or len(sentence) > limit:
        return snippet(text, q)
    return '…%s…' % sentence


def visible_tasks(ctx):
    workspaces = WorkspaceMember.objects.filter(user=ctx.user, state='active', role__in=('owner', 'editor', 'viewer'),
                                                workspace__account=ctx.account).values('workspace_id')
    guest = WorkspaceMember.objects.filter(user=ctx.user, state='active', role='guest').values('guest_tasks')
    return Task.objects.filter(account=ctx.account).filter(
        Q(owner__user=ctx.user) | Q(creator__user=ctx.user) |
        Q(participants__user=ctx.user, participants__state='active') |
        Q(workspace_id__in=workspaces) | Q(pk__in=guest)).distinct()


def _tasks(ctx, q):
    # System notices (routing, rule reminders) are not conversation content and would make every task hit.
    spoken = TaskMessage.objects.filter(body__icontains=q).exclude(author_kind='system').values('task_id')
    rows = visible_tasks(ctx).filter(Q(title__icontains=q) | Q(pk__in=spoken) |
                                     Q(steps__title__icontains=q)).distinct().select_related(
        'workspace').order_by('-last_activity_at', '-id')

    def serialize(task):
        # Hits inside the task: message bodies (latest first, the excerpt comes from the newest) and step titles.
        hits = list(TaskMessage.objects.filter(task=task, body__icontains=q).exclude(author_kind='system').order_by('-sent_at', '-id').values_list(
            'body', flat=True)[:50])
        hits += list(TaskStep.objects.filter(task=task, title__icontains=q).order_by('order').values_list(
            'title', flat=True)[:50])
        return {'type': 'task', 'id': task.public_id, 'title': task.title,
                'context': task.workspace.name if task.workspace else '未关联工作区',
                'occurred_at': task.last_activity_at,
                'snippet': sentence_snippet(hits[0], q) if hits else None, 'extra_hits': max(0, len(hits) - 1)}
    return rows, serialize


def _workspaces(ctx, q):
    rows = visible_workspaces(ctx).filter(Q(name__icontains=q) | Q(description__icontains=q)).order_by('name', 'id')

    def serialize(ws):
        kind = '企业' if ctx.account.kind == 'business' else '个人'
        tasks = Task.objects.filter(workspace=ws, archived_at__isnull=True).count()
        members = WorkspaceMember.objects.filter(workspace=ws, state='active').count()
        return {'type': 'workspace', 'id': ws.public_id, 'title': ws.name,
                'context': '%s · %d 个任务 · %d 位成员' % (kind, tasks, members),
                'snippet': snippet(ws.description, q), 'extra_hits': 0}
    return rows, serialize


def _files(ctx, q):
    workspaces = visible_workspaces(ctx).values('pk')
    rows = Artifact.objects.filter(account=ctx.account, trashed_at__isnull=True, title__icontains=q).filter(
        Q(workspace_id__in=workspaces) | Q(created_by_user=ctx.user) | Q(source_task__in=visible_tasks(ctx))).distinct(
    ).select_related('current_version', 'source_task').order_by('-updated_at', '-id')

    def serialize(artifact):
        parts = ['成果']
        version = artifact.current_version
        if version:
            parts.append('%s v%s' % (VERSION_LABELS.get(version.state, version.state), version.number))
        if artifact.source_task:
            parts.append(artifact.source_task.title)
        return {'type': 'artifact', 'id': artifact.public_id, 'title': artifact.title, 'context': ' · '.join(parts),
                'snippet': None, 'extra_hits': 0}
    return rows, serialize


def _experts(ctx, q):
    rows = visible_experts(ctx).filter(Q(name__icontains=q) | Q(title__icontains=q) | Q(specialty__icontains=q) |
                                       Q(responsibilities__icontains=q)).order_by('name', 'id')

    def serialize(expert):
        return {'type': 'expert', 'id': expert.public_id, 'title': expert.name, 'context': expert.title,
                'avatar_ref': ws.avatar_url(expert.avatar_ref), 'snippet': snippet(expert.responsibilities, q), 'extra_hits': 0}
    return rows, serialize


def _knowledge(ctx, q):
    workspaces = visible_workspaces(ctx).values('pk')
    rows = KnowledgeDocument.objects.filter(account=ctx.account, deleted_at__isnull=True, title__icontains=q).filter(
        Q(knowledge_base__scope='personal', knowledge_base__owner=ctx.user) |
        Q(knowledge_base__scope='workspace', knowledge_base__workspace_id__in=workspaces) |
        Q(knowledge_base__scope='account')).select_related('knowledge_base').order_by('-updated_at', '-id')

    def serialize(doc):
        return {'type': 'document', 'id': doc.public_id, 'title': doc.title, 'context': doc.knowledge_base.name,
                'knowledge_base_id': doc.knowledge_base.public_id, 'snippet': None, 'extra_hits': 0}
    return rows, serialize


def _extensions(ctx, q):
    rows = Extension.objects.filter(Q(account__isnull=True) | Q(account=ctx.account)).filter(
        Q(name__icontains=q) | Q(description__icontains=q)).order_by('name', 'id')

    def serialize(ext):
        return {'type': 'extension', 'id': ext.public_id, 'title': ext.name,
                'context': EXTENSION_LABELS.get(ext.kind, ext.kind), 'snippet': snippet(ext.description, q),
                'extra_hits': 0}
    return rows, serialize


def _inbox(ctx, q):
    # Q-F-18 A: only the member's own items, titles only; message bodies are not indexed.
    rows = own_items(ctx).exclude(group='ignored').filter(title__icontains=q).order_by('-received_at', '-id')

    def serialize(item):
        return {'type': 'inbox_item', 'id': item.public_id, 'title': item.title, 'context': item.sender_label,
                'occurred_at': item.received_at, 'snippet': None, 'extra_hits': 0}
    return rows, serialize


def schedule_label(a):
    hm = a.schedule_time.strftime('%H:%M')
    if a.schedule_kind == 'weekly' and a.schedule_weekday:
        return '每周%s %s' % (WEEKDAYS[a.schedule_weekday - 1], hm)
    if a.schedule_kind == 'monthly':
        return '每月 %s 日 %s' % (a.schedule_monthday, hm)
    return '每天 %s' % hm


def _automations(ctx, q):
    rows = own_automations(ctx).filter(name__icontains=q).order_by('created_at', 'id')

    def serialize(a):
        lead = a.lead_expert.name if a.lead_expert else 'Playbook'
        return {'type': 'automation', 'id': a.public_id, 'title': a.name,
                'context': '%s · %s' % (lead, schedule_label(a)), 'schedule': schedule_dict(a),
                'snippet': None, 'extra_hits': 0}
    return rows, serialize


SEARCHERS = {'tasks': _tasks, 'workspaces': _workspaces, 'files': _files, 'experts': _experts,
             'knowledge': _knowledge, 'extensions': _extensions, 'inbox': _inbox, 'automations': _automations}


def _category(ctx, key, q, start, size):
    try:
        rows, serialize = SEARCHERS[key](ctx, q)
        total = rows.count()
        return {'key': key, 'state': 'ok', 'total': total,
                'items': [serialize(row) for row in rows[start:start + size]]}
    except Exception:  # one failing category must not break the others (catalog 4.1)
        logger.exception('search_category_failed key=%s', key)
        return {'key': key, 'state': 'unavailable', 'total': None, 'items': []}


def search(ctx, request, q, category):
    if category == 'all':
        read_offset(request)
        categories = [_category(ctx, key, q, 0, PREVIEW) for key in CATEGORIES[1:]]
        total = sum(c['total'] for c in categories if c['total'] is not None)
        return {'q': q, 'categories': [{'key': 'all', 'state': 'ok', 'total': total, 'items': []}] + categories}
    start = read_offset(request)
    result = _category(ctx, category, q, start, PAGE_SIZE)
    total = result['total']
    return {'q': q, 'categories': [result], 'offset': start, 'total': total,
            'next_offset': start + PAGE_SIZE if total is not None and start + PAGE_SIZE < total else None}
