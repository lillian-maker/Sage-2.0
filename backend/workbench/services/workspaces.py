"""Workspace and task access rules shared by the workspaces, playbooks, artifacts, reviews and citations routes.

Visibility follows Q-P2 A (only workspaces and tasks you take part in) and Q-P10 A (invisible objects are
NOT_FOUND; visible objects whose action is not allowed are PERMISSION_DENIED).
"""
import difflib

from django.db import transaction
from django.db.models import Q

from ...http import error
from ...models import AccountProfile
from ...views import throttle
from ..models import Task, TaskParticipant, TaskUserState, Workspace, WorkspaceMember

EDIT_ROLES = ('owner', 'editor')
ROLE_ORDER = ('owner', 'editor', 'viewer', 'guest')
LEVELS = {None: 0, 'view': 1, 'edit': 2}
RULES_MAX = 30
RULE_MAX_LENGTH = 200


def best(*levels):
    return max(levels, key=LEVELS.__getitem__)


def full_name(user):
    if user is None:
        return None
    return ' '.join(part for part in (user.first_name, user.last_name) if part) or user.email


def person(user):
    if user is None:
        return None
    profile = AccountProfile.objects.filter(user=user).only('avatar_name').first()
    return {'id': str(user.pk), 'name': full_name(user), 'display_name': full_name(user),
            'avatar_name': profile.avatar_name if profile else ''}


AVATAR_PREFIXES = ('digital-employees/assets/', 'assets/people/', 'assets/avatars/')


def avatar_url(ref):
    """开发态头像：仓库资产前缀的 ref 转 GET app/avatars 路径，其余原样（如 generated/）。"""
    if ref and ref.startswith(AVATAR_PREFIXES):
        return '/api/app/avatars/' + ref
    return ref


def expert_ref(expert):
    if expert is None:
        return None
    return {'id': expert.public_id, 'name': expert.name, 'specialty': expert.title or expert.specialty,
            'avatar_ref': avatar_url(expert.avatar_ref)}


def rate_limited(request, ctx, name, limit=30):
    """RATE_LIMITED is returned directly (not via ValueError), like the existing auth handlers."""
    with transaction.atomic():
        allowed = throttle('app:%s:%s:%s' % (name, ctx.account.pk, ctx.user.pk), limit)
    if allowed:
        return None
    return error(request, 'RATE_LIMITED', 429, '请求较频繁，请稍后重试。')


def text_field(value, maximum, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValueError('VALIDATION_FAILED')
    value = value.strip()
    if (required and not value) or len(value) > maximum:
        raise ValueError('VALIDATION_FAILED')
    return value or None


# Workspaces ---------------------------------------------------------------

def member_rows(ctx):
    return WorkspaceMember.objects.filter(account=ctx.account, user=ctx.user, state='active',
                                          workspace__deleted_at__isnull=True)


def visible_workspaces(ctx):
    return Workspace.objects.filter(account=ctx.account, deleted_at__isnull=True,
                                    pk__in=member_rows(ctx).values('workspace_id'))


def my_member(ctx, workspace):
    if workspace is None or workspace.deleted_at is not None or workspace.account_id != ctx.account.pk:
        return None
    return WorkspaceMember.objects.filter(workspace=workspace, user=ctx.user, state='active').first()


def get_workspace(ctx, public_id):
    workspace = Workspace.objects.filter(account=ctx.account, public_id=public_id, deleted_at__isnull=True).first()
    member = my_member(ctx, workspace)
    if member is None:
        raise ValueError('NOT_FOUND')
    return workspace, member


def require_owner(member):
    if member.role != 'owner':
        raise ValueError('PERMISSION_DENIED')


def require_editor(member):
    if member.role not in EDIT_ROLES:
        raise ValueError('PERMISSION_DENIED')


# Tasks --------------------------------------------------------------------

def visible_tasks_q(ctx):
    user = ctx.user
    members = member_rows(ctx)
    guest_tasks = WorkspaceMember.guest_tasks.through.objects.filter(
        workspacemember__in=members.filter(role='guest')).values('task_id')
    return (Q(owner__user=user) | Q(creator__user=user)
            | Q(pk__in=TaskParticipant.objects.filter(user=user, kind='human', state='active').values('task_id'))
            | Q(workspace_id__in=members.exclude(role='guest').values('workspace_id'))
            | Q(pk__in=guest_tasks))


def visible_tasks(ctx):
    return Task.objects.filter(account=ctx.account).filter(visible_tasks_q(ctx))


def task_access(ctx, task):
    """'edit', 'view' or None for the current user."""
    if task is None or task.account_id != ctx.account.pk:
        return None
    user_id = ctx.user.pk
    if task.owner.user_id == user_id or task.creator.user_id == user_id:
        return 'edit'
    level = None
    participant = TaskParticipant.objects.filter(task=task, user_id=user_id, kind='human', state='active').first()
    if participant:
        level = 'view' if participant.role == 'shared_viewer' else 'edit'
    if task.workspace_id:
        member = my_member(ctx, task.workspace)
        if member and member.role in EDIT_ROLES:
            level = best(level, 'edit')
        elif member and member.role == 'viewer':
            level = best(level, 'view')
        elif member and member.role == 'guest' and member.guest_tasks.filter(pk=task.pk).exists():
            level = best(level, 'view')
    return level


def is_owner_or_creator(ctx, task):
    return task is not None and ctx.user.pk in (task.owner.user_id, task.creator.user_id)


def unread_map(user, tasks):
    """Unread = never read, explicitly marked unread, or activity after the last read."""
    tasks = list(tasks)
    states = {row.task_id: row for row in TaskUserState.objects.filter(user=user, task__in=tasks)}
    result = {}
    for task in tasks:
        state = states.get(task.pk)
        if state is None:
            result[task.pk] = task.creator.user_id != user.pk
        else:
            result[task.pk] = bool(state.marked_unread or state.last_read_at is None
                                   or state.last_read_at < task.last_activity_at)
    return result


def workspace_tasks(ctx, workspace, member, include_archived=False):
    qs = Task.objects.filter(account=ctx.account, workspace=workspace).select_related('creator', 'owner')
    if member.role == 'guest':
        qs = qs.filter(pk__in=member.guest_tasks.values('pk'))
    if not include_archived:
        qs = qs.filter(archived_at__isnull=True)
    return qs


def active_member_count(workspace):
    return WorkspaceMember.objects.filter(workspace=workspace, state='active').count()


# Rules ----------------------------------------------------------------------

def clean_rules(rules):
    if not isinstance(rules, list) or len(rules) > RULES_MAX:
        raise ValueError('VALIDATION_FAILED')
    return [text_field(rule, RULE_MAX_LENGTH) for rule in rules]


def rules_change_note(old, new):
    """Fallback note when the editor leaves change_note empty (Q-R14 B)."""
    if not old:
        return '创建 %d 条规则' % len(new)
    parts = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == 'insert':
            parts.extend('新增：' + rule for rule in new[j1:j2])
        elif tag == 'delete':
            parts.extend('删除：' + rule for rule in old[i1:i2])
        elif tag == 'replace':
            parts.extend('修改第 %d 条' % (index + 1) for index in range(j1, j2))
            if i2 - i1 > j2 - j1:
                parts.extend('删除：' + rule for rule in old[i1 + (j2 - j1):i2])
    note = '；'.join(parts) or '调整规则顺序'
    return note if len(note) <= 200 else note[:199] + '…'
