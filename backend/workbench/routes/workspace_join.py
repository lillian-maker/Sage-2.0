"""app/workspaces/{id}/join-requests (D33; 83 屏 工作区「你还不是这个工作区的成员 · 申请加入」).

An active member of the billing account who is not a workspace member can ask to join. Workspace owners get an
in-app notification and approve (editor / viewer) or reject from the members page; the applicant is notified of
the outcome and can withdraw while pending. Workspace detail itself stays NOT_FOUND for non-members (Q-P10 A).
"""
from django.db import transaction
from django.utils import timezone
from .. import clock

from ..common import iso, json_body, page, query, respond
from ..models import AppNotification, Workspace, WorkspaceJoinRequest, WorkspaceMember
from ..services import workspaces as ws
from ..services.notify import notify
from .workspaces import member_row

APPROVE_ROLES = ('editor', 'viewer')
TEXT_MAX = 200


def join_request_row(row):
    return {'id': row.public_id, 'state': row.state, 'message': row.message, 'role': row.decided_role,
            'created_at': iso(row.created_at), 'decided_at': iso(row.decided_at),
            'requester': ws.person(row.requester), 'workspace': {'id': row.workspace.public_id}}


def account_workspace(ctx, public_id, lock=False):
    rows = Workspace.objects.filter(account=ctx.account, public_id=public_id, deleted_at__isnull=True)
    workspace = (rows.select_for_update() if lock else rows).first()
    if workspace is None:
        raise ValueError('NOT_FOUND')
    return workspace


def optional_text(data, key):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value.strip()) > TEXT_MAX:
        raise ValueError('VALIDATION_FAILED')
    return value.strip()


def requested_key(row):
    return 'join_requested:%s' % row.public_id


def close_owner_notices(row):
    now = clock.now()
    AppNotification.objects.filter(account=row.account, dedupe_key=requested_key(row), read_at__isnull=True).update(
        read_at=now, updated_at=now)


def apply(request, ctx, id):
    data = json_body(request, (), ('message',))
    message = optional_text(data, 'message')
    workspace = account_workspace(ctx, id)
    if ws.my_member(ctx, workspace) is not None:
        raise ValueError('STATE_CONFLICT')
    limited = ws.rate_limited(request, ctx, 'workspace-join', limit=10)
    if limited:
        return limited
    with transaction.atomic():
        workspace = account_workspace(ctx, id, lock=True)
        pending = WorkspaceJoinRequest.objects.filter(workspace=workspace, requester=ctx.user, state='pending').first()
        if pending is not None:
            return {'join_request': join_request_row(pending)}
        row = WorkspaceJoinRequest.objects.create(account=ctx.account, workspace=workspace, requester=ctx.user,
                                                  message=message)
        owners = WorkspaceMember.objects.filter(workspace=workspace, role='owner', state='active',
                                                user__isnull=False).select_related('user')
        for owner in owners:
            notify(account=ctx.account, recipient=owner.user, category='workspace', type='join_requested',
                   text='%s 申请加入工作区“%s”' % (ws.full_name(ctx.user), workspace.name),
                   context_label=workspace.name, target_type='workspace_members', target_id=workspace.public_id,
                   action='open', dedupe_key=requested_key(row))
    return respond({'join_request': join_request_row(row)}, 201)


def list_requests(request, ctx, id):
    query(request, ('offset',))
    workspace, member = ws.get_workspace(ctx, id)
    ws.require_owner(member)
    rows = (WorkspaceJoinRequest.objects.filter(workspace=workspace, state='pending')
            .select_related('requester', 'workspace').order_by('created_at', 'pk'))
    return page(request, rows, join_request_row)


def mine(request, ctx, id):
    query(request)
    workspace = account_workspace(ctx, id)
    row = (WorkspaceJoinRequest.objects.filter(workspace=workspace, requester=ctx.user)
           .select_related('requester', 'workspace').order_by('-created_at', '-pk').first())
    return {'join_request': join_request_row(row) if row else None}


def pending_for_decision(ctx, id, rid):
    workspace, member = ws.get_workspace(ctx, id)
    ws.require_owner(member)
    row = (WorkspaceJoinRequest.objects.select_for_update().select_related('requester', 'workspace')
           .filter(workspace=workspace, public_id=rid).first())
    if row is None:
        raise ValueError('NOT_FOUND')
    if row.state != 'pending':
        raise ValueError('STATE_CONFLICT')
    return workspace, row


def approve(request, ctx, id, rid):
    data = json_body(request, ('role',))
    if data['role'] not in APPROVE_ROLES:
        raise ValueError('VALIDATION_FAILED')
    now = clock.now()
    with transaction.atomic():
        workspace, row = pending_for_decision(ctx, id, rid)
        member = WorkspaceMember.objects.select_for_update().filter(workspace=workspace, user=row.requester).first()
        if member is None:
            member = WorkspaceMember(account=ctx.account, workspace=workspace, user=row.requester)
        if member.state != 'active':
            member.role, member.state, member.invited_by, member.accepted_at, member.pinned_at = (
                data['role'], 'active', ctx.user, now, None)
            member.save()
        row.state, row.decided_role, row.decided_by, row.decided_at = 'approved', data['role'], ctx.user, now
        row.save()
        close_owner_notices(row)
        notify(account=ctx.account, recipient=row.requester, category='workspace', type='join_approved',
               text='你已加入工作区“%s”' % workspace.name, context_label=workspace.name, target_type='workspace',
               target_id=workspace.public_id, action='open', dedupe_key='join_approved:%s' % row.public_id)
    return {'join_request': join_request_row(row), 'member': member_row(ctx, member)}


def reject(request, ctx, id, rid):
    data = json_body(request, (), ('reason',))
    reason = optional_text(data, 'reason')
    now = clock.now()
    with transaction.atomic():
        workspace, row = pending_for_decision(ctx, id, rid)
        row.state, row.reject_reason, row.decided_by, row.decided_at = 'rejected', reason, ctx.user, now
        row.save()
        close_owner_notices(row)
        notify(account=ctx.account, recipient=row.requester, category='workspace', type='join_rejected',
               text='加入工作区的申请未通过' + ('：' + reason if reason else ''), context_label=workspace.name,
               dedupe_key='join_rejected:%s' % row.public_id)
    return {'join_request': join_request_row(row)}


def withdraw(request, ctx, id, rid):
    json_body(request, ())
    with transaction.atomic():
        workspace = account_workspace(ctx, id)
        row = (WorkspaceJoinRequest.objects.select_for_update().select_related('requester', 'workspace')
               .filter(workspace=workspace, public_id=rid, requester=ctx.user).first())
        if row is None:
            raise ValueError('NOT_FOUND')
        if row.state != 'pending':
            raise ValueError('STATE_CONFLICT')
        row.state, row.decided_at = 'withdrawn', clock.now()
        row.save()
        close_owner_notices(row)
    return {'join_request': join_request_row(row)}


ROUTES = [
    ('POST', 'workspaces/{id}/join-requests', apply),
    ('GET', 'workspaces/{id}/join-requests', list_requests),
    ('GET', 'workspaces/{id}/join-requests/mine', mine),
    ('POST', 'workspaces/{id}/join-requests/{rid}/approve', approve),
    ('POST', 'workspaces/{id}/join-requests/{rid}/reject', reject),
    ('POST', 'workspaces/{id}/join-requests/{rid}/withdraw', withdraw),
]
