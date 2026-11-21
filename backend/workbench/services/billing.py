"""Billing-domain workbench services: authorizations, usage, org settings, quotas and offboarding.

Workbench reads the commerce ledger (never the reverse), so everything that joins Task / Workspace
data with Reservation / CreditEntry rows lives here.
"""
import csv
import io
import re
import uuid
from datetime import timedelta
from fractions import Fraction

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from .. import clock

from backend.commerce import billing as ledger
from backend.commerce.models import CreditEntry, Department, DepartmentMember, SharedLimitChange
from backend.commerce.subscriptions import _boundary, _calendar_zone, active_subscription_periods, \
    current_department_members
from backend.models import AuditEvent, MemberRemoval, Membership

from ..common import iso
from ..models import (AccountPolicy, AuthorizationGrant, AuthorizationRequest, Automation, ConnectorConnection,
                      Expert, Extension, ExtensionAllowEntry, HandoffItem, MemoryItem, Playbook, Task,
                      TaskParticipant, Workspace, WorkspaceMember)
from .notify import notify

REQUEST_KEY = re.compile(r'[A-Za-z0-9._:-]{1,64}')
AUDIT_NAMESPACE = uuid.UUID('6b1f0c1e-6d0e-4a55-9a43-3d1f9b2a8c11')
DONE_TASK_STATUSES = ('accepted', 'stopped')
HANDOFF_PAUSE_STATUSES = ('awaiting_kickoff', 'running', 'awaiting_user', 'stuck', 'needs_revision')
MAX_QUOTA = 10 ** 9


# ---------------------------------------------------------------- shared helpers

def display_name(user):
    return user.get_full_name() or (user.email.split('@')[0] if user.email else '成员')


def credits_text(value):
    return ledger._text(value)


def amount_label(value):
    return '不限' if value is None else f'{value:,}'


def keyed_request_id(account, scope, key):
    """Idempotency keys are folded into AuditEvent.request_id because the audited tables have no request_key column."""
    return uuid.uuid5(AUDIT_NAMESPACE, '%s:%s:%s' % (account.pk, scope, key))


def audit(account, actor, action, summary, target_type, target_id, request_id=None):
    return AuditEvent.objects.create(actor=actor, account=account, action=action, request_id=request_id or uuid.uuid4(),
                                     target_type=target_type, target_id=str(target_id), summary=summary[:200])


def request_key(value):
    if not isinstance(value, str) or not REQUEST_KEY.fullmatch(value):
        raise ValueError('INVALID_IDEMPOTENCY_KEY')
    return value


def client_request_id(data):
    value = data.get('client_request_id')
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError('INVALID_REQUEST')
    return value


def service_month(account, now=None):
    """Current service month: the subscription calendar when one is active, else the local calendar month."""
    now = now or clock.now()
    period = active_subscription_periods(account, now).select_related('subscription').order_by('starts_at').first()
    if period:
        subscription = period.subscription
        zone = _calendar_zone(subscription.billing_timezone)
        local, anchor = now.astimezone(zone), subscription.original_anchor.astimezone(zone)
        index = (local.year - anchor.year) * 12 + local.month - anchor.month
        if _boundary(subscription, index) > now:
            index -= 1
        return _boundary(subscription, index), _boundary(subscription, index + 1), period
    local = timezone.localtime(now)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = timezone.localtime(start + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, end, None


def settled_entries(account, start, end):
    return CreditEntry.objects.filter(batch__account=account, kind='settle', created_at__gte=start, created_at__lt=end)


def used_by_member(account, start, end, membership_ids=None):
    rows = settled_entries(account, start, end).filter(reservation__membership__isnull=False)
    if membership_ids is not None:
        rows = rows.filter(reservation__membership_id__in=list(membership_ids))
    totals = {}
    for member_id, quantity in rows.values_list('reservation__membership_id', 'quantity'):
        totals[member_id] = totals.get(member_id, Fraction(0)) + ledger._quantity(quantity)
    return totals


def require_business(ctx):
    if ctx.account.kind != 'business':
        raise ValueError('PERMISSION_DENIED')


def require_role(ctx, *roles, security_admin=False):
    if ctx.member.role in roles or (security_admin and ctx.member.is_security_admin):
        return
    raise ValueError('PERMISSION_DENIED')


def target_member(account, mid):
    try:
        return Membership.objects.select_related('user').get(account=account, pk=int(mid))
    except (Membership.DoesNotExist, ValueError) as exc:
        raise ValueError('NOT_FOUND') from exc


def root_task_key(task_key):
    return task_key.split(':', 1)[0]


# ---------------------------------------------------------------- authorizations (C-A-5, C-A-6)

def _task_owner_member(item):
    if item.task_id:
        return item.task.owner
    if item.automation_run_id:
        return item.automation_run.automation.owner
    return None


def can_decide(ctx, item):
    """Q-P5 B: the initiator (task owner/creator, automation owner) or the workspace owner."""
    if item.task_id:
        task = item.task
        if ctx.member.pk in (task.owner_id, task.creator_id):
            return True
        return bool(task.workspace_id and WorkspaceMember.objects.filter(
            workspace_id=task.workspace_id, user=ctx.user, role='owner', state='active').exists())
    if item.automation_run_id:
        return item.automation_run.automation.owner_id == ctx.member.pk
    return False


def can_view(ctx, item):
    if can_decide(ctx, item):
        return True
    return bool(item.task_id and TaskParticipant.objects.filter(task_id=item.task_id, user=ctx.user,
                                                                state='active').exists())


def find_authorization(ctx, public_id, lock=False):
    query = AuthorizationRequest.objects.filter(account=ctx.account, public_id=public_id)
    item = (query.select_for_update() if lock else query).first()
    if item is None or not can_view(ctx, item):
        raise ValueError('NOT_FOUND')
    return item


def allowed_scopes(item):
    scopes = ['once']
    if item.task_id:
        scopes.append('task')
        if item.task.workspace_id and item.action_kind == 'read_query':
            scopes.append('workspace')
    return scopes


def confirm_reasons(item, scopes):
    """82 屏「批准使用 X？」: which decision scopes need a second confirmation, and why (D32)."""
    reasons = {}
    for scope in scopes:
        found = []
        if scope == 'workspace':
            found.append('broad_scope')
        if scope in ('task', 'workspace') and item.extension.billing_note:
            found.append('paid')
        if item.action_kind != 'read_query':
            found.append('external_send')
        if found:
            reasons[scope] = found
    return reasons


def charge_target(ctx):
    """Q-A-6 C: business requests draw on the member's own share (single limit before department share)."""
    if ctx.account.kind == 'personal':
        return {'kind': 'personal_account', 'label': '计入个人额度', 'department': None}
    assigned = current_department_members(ctx.account).filter(membership=ctx.member).select_related('department').first()
    return {'kind': 'member_share', 'label': '计入企业额度',
            'department': {'id': str(assigned.department_id), 'name': assigned.department.name} if assigned else None}


def authorization_dict(ctx, item):
    task = item.task
    scopes = allowed_scopes(item)
    reasons = confirm_reasons(item, scopes)
    return {
        'id': item.public_id,
        'task': {'id': task.public_id, 'title': task.title} if task else None,
        'extension': {'id': item.extension.public_id, 'name': item.extension.name},
        'expert': {'id': item.requested_by_expert.public_id, 'name': item.requested_by_expert.name},
        'purpose_text': item.purpose_text, 'scope_text': item.scope_text, 'action_kind': item.action_kind,
        'scope_params': item.scope_params, 'scope_options': item.scope_options or [],
        'estimate_state': item.estimate_state, 'estimated_credits': item.estimated_credits,
        'validity': item.validity, 'decision': item.decision, 'decision_scope': item.decision_scope,
        'decided_at': iso(item.decided_at),
        'reservation_id': str(item.reservation_id) if item.reservation_id else None,
        'policy_approval_required': item.policy_approval_required,
        'charge_target': charge_target(ctx), 'allowed_scopes': scopes,
        'confirm_scopes': [scope for scope in scopes if scope in reasons], 'confirm_reasons': reasons,
        'created_at': iso(item.created_at), 'version': item.version,
    }


def _approve_result(item):
    grant = item.grants.filter(revoked_at__isnull=True).order_by('created_at').first()
    return {'authorization': {'id': item.public_id, 'decision': item.decision, 'decision_scope': item.decision_scope,
                              'decided_at': iso(item.decided_at),
                              'reservation_id': str(item.reservation_id) if item.reservation_id else None,
                              'version': item.version},
            'grant': {'id': grant.public_id, 'scope': grant.scope} if grant else None}


def _check_version(item, data):
    """version is optional (older callers omit it); when sent it must match the stored row."""
    if 'version' not in data:
        return
    if type(data['version']) is not int:
        raise ValueError('VALIDATION_FAILED')
    if data['version'] != item.version:
        raise ValueError('VERSION_CONFLICT')


def _bump(item):
    item.version += 1
    item.save()


def _decidable(ctx, item):
    if item.decision == 'expired':
        raise ValueError('AUTHORIZATION_EXPIRED')
    if item.decision != 'pending':
        raise ValueError('STATE_CONFLICT')
    if not can_decide(ctx, item):
        raise ValueError('PERMISSION_DENIED')


@transaction.atomic
def approve(ctx, public_id, data):
    item = find_authorization(ctx, public_id, lock=True)
    client_request_id(data)
    scope = data.get('decision_scope', 'once')
    # Q-V11 A without a stored decision key: a repeat of the same decision by the same user replays.
    if item.decision == 'approved' and item.decided_by_id == ctx.user.pk and item.decision_scope == scope:
        return _approve_result(item)
    _decidable(ctx, item)
    _check_version(item, data)
    if scope not in allowed_scopes(item):
        raise ValueError('VALIDATION_FAILED')
    if item.estimate_state == 'checking':
        raise ValueError('STATE_CONFLICT')
    if item.policy_approval_required and not (ctx.member.role == 'owner' or ctx.member.is_security_admin):
        raise ValueError('POLICY_APPROVAL_REQUIRED')
    if item.estimated_credits:
        key = '%s:%s' % (item.task.public_id, item.public_id) if item.task_id else 'authorization:%s' % item.public_id
        payer = _task_owner_member(item)
        try:
            reservation = ledger.reserve_credits(
                ctx.account, key, str(item.estimated_credits),
                membership_id=payer.pk if ctx.account.kind == 'business' and payer else None)
        except ValueError as exc:
            # A business member without a usable seat or share has no Credits to draw (C-C-13).
            if str(exc) in ('NOT_FOUND', 'FORBIDDEN', 'CONFIG_INCOMPLETE'):
                raise ValueError('INSUFFICIENT_CREDITS') from exc
            raise
        item.reservation_id = reservation['id']
    if scope in ('task', 'workspace'):
        fields = dict(account=ctx.account, user=ctx.user, extension=item.extension, action_kind=item.action_kind,
                      scope=scope, task=item.task if scope == 'task' else None,
                      workspace=item.task.workspace if scope == 'workspace' else None)
        if not AuthorizationGrant.objects.filter(revoked_at__isnull=True, **fields).exists():
            AuthorizationGrant.objects.create(source_request=item, **fields)
    item.decision, item.decision_scope = 'approved', scope
    item.decided_by, item.decided_at = ctx.user, clock.now()
    _bump(item)
    task = item.task
    if task and task.status == 'awaiting_user' and not task.authorization_requests.filter(decision='pending').exists():
        task.status = 'running'
        task.last_activity_at = clock.now()
        task.save(update_fields=['status', 'last_activity_at', 'updated_at'])
    audit(ctx.account, ctx.user, 'authorization.approve', '批准 %s · %s' % (item.extension.name, item.scope_text),
          'authorization', item.public_id)
    return _approve_result(item)


@transaction.atomic
def reject(ctx, public_id, data):
    item = find_authorization(ctx, public_id, lock=True)
    client_request_id(data)
    if not (item.decision == 'rejected' and item.decided_by_id == ctx.user.pk):
        _decidable(ctx, item)
        _check_version(item, data)
        item.decision, item.decided_by, item.decided_at = 'rejected', ctx.user, clock.now()
        _bump(item)
        audit(ctx.account, ctx.user, 'authorization.reject', '拒绝 %s · %s' % (item.extension.name, item.scope_text),
              'authorization', item.public_id)
    return {'authorization': {'id': item.public_id, 'decision': item.decision, 'estimate_state': item.estimate_state,
                              'decided_at': iso(item.decided_at), 'version': item.version}}


def _option_values(item):
    return [option.get('top_n') if isinstance(option, dict) else option for option in item.scope_options or []]


@transaction.atomic
def revise(ctx, public_id, data):
    item = find_authorization(ctx, public_id, lock=True)
    client_request_id(data)
    params = data.get('scope_params')
    if not isinstance(params, dict) or set(params) != {'top_n'} or type(params['top_n']) is not int:
        raise ValueError('VALIDATION_FAILED')
    _decidable(ctx, item)
    if params['top_n'] not in _option_values(item):
        raise ValueError('VALIDATION_FAILED')
    # A repeat of the same revision replays before the version check (Q-V11 A).
    if not (item.scope_params.get('top_n') == params['top_n'] and item.estimate_state == 'checking'):
        _check_version(item, data)
        item.scope_params = {**item.scope_params, 'top_n': params['top_n']}
        item.scope_text = re.sub(r'前\s*\d+\s*名', '前 %d 名' % params['top_n'], item.scope_text)[:200]
        item.estimate_state, item.estimated_credits = 'checking', None
        _bump(item)
    return {'authorization': {'id': item.public_id, 'scope_text': item.scope_text, 'scope_params': item.scope_params,
                              'estimate_state': item.estimate_state, 'version': item.version}}


@transaction.atomic
def estimate(ctx, public_id, data):
    item = find_authorization(ctx, public_id, lock=True)
    client_request_id(data)
    _decidable(ctx, item)
    if item.estimate_state != 'unavailable':
        raise ValueError('STATE_CONFLICT')
    item.estimate_state = 'checking'
    _bump(item)
    return {'authorization': {'id': item.public_id, 'estimate_state': item.estimate_state, 'version': item.version}}


# ---------------------------------------------------------------- usage (C-C-8, C-C-9)

def _usage_entries(ctx, start, end):
    rows = settled_entries(ctx.account, start, end)
    if ctx.account.kind == 'business':
        rows = rows.filter(reservation__membership=ctx.member)
    return rows


def _member_share(ctx):
    latest = SharedLimitChange.objects.filter(membership=ctx.member).order_by('-version').first()
    if latest and latest.quantity is not None:
        return ledger._quantity(latest.quantity)
    assigned = current_department_members(ctx.account).filter(membership=ctx.member).select_related('department').first()
    if assigned and assigned.department.per_member_share is not None:
        return Fraction(assigned.department.per_member_share)
    return None


def usage_summary(ctx):
    now = clock.now()
    start, end, _ = service_month(ctx.account, now)
    try:
        rows = list(_usage_entries(ctx, start, end).values_list('quantity', 'created_at'))
        used = sum((ledger._quantity(quantity) for quantity, _ in rows), Fraction(0))
        share = _member_share(ctx) if ctx.account.kind == 'business' else None
        if share is not None:
            total = max(share, used)
        else:
            balance = ledger.credits_summary(ctx.account)
            total = used + ledger._quantity(balance['available']) + ledger._quantity(balance['reserved'])
    except ValueError as exc:
        raise ValueError('SOURCE_UNAVAILABLE') from exc
    days, cursor = {}, timezone.localtime(start).date()
    last = min(timezone.localtime(now).date(), timezone.localtime(end - timedelta(microseconds=1)).date())
    while cursor <= last:
        days[cursor] = Fraction(0)
        cursor += timedelta(days=1)
    for quantity, created_at in rows:
        day = timezone.localtime(created_at).date()
        days[day] = days.get(day, Fraction(0)) + ledger._quantity(quantity)
    elapsed = max(1, len([day for day in days if day <= timezone.localtime(now).date()]))
    average = used / elapsed
    return {'account_id': str(ctx.account.pk), 'period_start': iso(start), 'period_end': iso(end),
            'period_used': credits_text(used), 'period_total': credits_text(total),
            'used_ratio': round(float(used / total), 4) if total else None,
            'daily_average': str((2 * average.numerator + average.denominator) // (2 * average.denominator)),
            'daily': [{'date': day.isoformat(), 'credits': credits_text(value)} for day, value in sorted(days.items())],
            'as_of': iso(now)}


def usage_tasks(ctx, sort):
    now = clock.now()
    start, end, _ = service_month(ctx.account, now)
    groups = {}
    try:
        for key, quantity, created_at in _usage_entries(ctx, start, end).values_list(
                'reservation__task_key', 'quantity', 'created_at'):
            root = root_task_key(key or '')
            credits, last = groups.get(root, (Fraction(0), created_at))
            groups[root] = (credits + ledger._quantity(quantity), max(last, created_at))
    except ValueError as exc:
        raise ValueError('SOURCE_UNAVAILABLE') from exc
    tasks = {task.public_id: task for task in Task.objects.filter(account=ctx.account, public_id__in=list(groups))
             .select_related('workspace')}
    items = []
    for key, (credits, last) in groups.items():
        task = tasks.get(key)
        items.append({'task_id': task.public_id if task else None, 'task_key': key,
                      'title': task.title if task else None,
                      'workspace': {'id': task.workspace.public_id, 'name': task.workspace.name}
                      if task and task.workspace_id else None,
                      'source': ('automation' if task.source_automation_run_id else 'task') if task else 'other',
                      'credits': credits, 'last_used_at': last})
    if sort == 'credits':
        items.sort(key=lambda row: (-row['credits'], row['task_key']))
    else:
        items.sort(key=lambda row: (row['last_used_at'], row['task_key']), reverse=True)
    for row in items:
        row['credits'], row['last_used_at'] = credits_text(row['credits']), iso(row['last_used_at'])
    return {'account_id': str(ctx.account.pk), 'period_start': iso(start), 'period_end': iso(end)}, items


# ---------------------------------------------------------------- departments (C-D-20, C-D-21, C-D-23)

def department_used(account, department, start, end, now=None):
    members = current_department_members(account, now).filter(department=department).values_list('membership_id', flat=True)
    return sum(used_by_member(account, start, end, members).values(), Fraction(0))


def department_dict(department, member_count, used):
    return {'id': str(department.pk), 'name': department.name, 'member_count': member_count,
            'monthly_quota': department.monthly_quota, 'per_member_share': department.per_member_share,
            'pooling_enabled': department.pooling_enabled, 'quota_version': department.quota_version,
            'used_credits': credits_text(used)}


def departments(ctx):
    require_business(ctx)
    require_role(ctx, 'owner', 'billing_admin', security_admin=True)
    now = clock.now()
    start, end, _ = service_month(ctx.account, now)
    active = set(Membership.objects.filter(account=ctx.account, is_active=True).values_list('pk', flat=True))
    current = [row for row in current_department_members(ctx.account, now) if row.membership_id in active]
    used = used_by_member(ctx.account, start, end, active)
    items = []
    rows = list(Department.objects.filter(account=ctx.account).order_by('name'))
    for department in rows:
        members = [row.membership_id for row in current if row.department_id == department.pk]
        items.append(department_dict(department, len(members), sum((used.get(pk, Fraction(0)) for pk in members),
                                                                   Fraction(0))))
    return {'all_count': len(active), 'unassigned_count': len(active - {row.membership_id for row in current}),
            'allocated_total': sum(row.monthly_quota or 0 for row in rows), 'items': items}


@transaction.atomic
def create_department(ctx, data):
    require_business(ctx)
    require_role(ctx, 'owner', 'billing_admin')
    name = data['name'].strip() if isinstance(data['name'], str) else ''
    if not 1 <= len(name) <= 40 or Department.objects.filter(account=ctx.account, name=name).exists():
        raise ValueError('VALIDATION_FAILED')
    try:
        with transaction.atomic():
            department = Department.objects.create(account=ctx.account, name=name)
    except IntegrityError as exc:
        raise ValueError('VALIDATION_FAILED') from exc
    return department_dict(department, 0, Fraction(0))


@transaction.atomic
def assign_department(ctx, mid, data):
    """Takes effect at the next paid period (effective_period = the current period); immediately without one."""
    require_business(ctx)
    require_role(ctx, 'owner', 'billing_admin')
    key = request_key(data['request_key'])
    target = target_member(ctx.account, mid)
    if not target.is_active:
        raise ValueError('NOT_FOUND')
    department = None
    if data['department_id'] is not None:
        try:
            department = Department.objects.get(account=ctx.account, pk=uuid.UUID(str(data['department_id'])))
        except (Department.DoesNotExist, ValueError) as exc:
            raise ValueError('VALIDATION_FAILED') from exc
    if type(data['is_lead']) is not bool:
        raise ValueError('VALIDATION_FAILED')
    now = clock.now()
    period = active_subscription_periods(ctx.account, now).order_by('ends_at').first()
    effective = period.ends_at if period else now
    request_id = keyed_request_id(ctx.account, 'department', key)
    previous = AuditEvent.objects.filter(account=ctx.account, action='member.department.change',
                                         request_id=request_id).first()
    label = department.name if department else '未分配部门'
    if previous:
        if previous.target_id != str(target.pk) or '→ %s（' % label not in previous.summary:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return {'effective_period_start': iso(effective)}
    current = list(current_department_members(ctx.account, now).filter(membership=target, ended_period__isnull=True))
    DepartmentMember.objects.filter(membership=target, effective_period__isnull=False,
                                    effective_period__ends_at__gt=now).delete()
    if period is None:
        DepartmentMember.objects.filter(pk__in=[row.pk for row in current]).delete()
        if department:
            DepartmentMember.objects.create(department=department, membership=target, is_lead=data['is_lead'])
    else:
        unchanged = [row for row in current if department and row.department_id == department.pk
                     and row.is_lead == data['is_lead']]
        if not unchanged:
            DepartmentMember.objects.filter(pk__in=[row.pk for row in current]).update(ended_period=period)
            if department:
                DepartmentMember.objects.create(department=department, membership=target, is_lead=data['is_lead'],
                                                effective_period=period)
    summary = '%s → %s（%s生效）' % (display_name(target.user), label, '下个服务月' if period else '立即')
    audit(ctx.account, ctx.user, 'member.department.change', summary, 'membership', target.pk, request_id=request_id)
    return {'effective_period_start': iso(effective)}


def _bounded(value):
    if value is not None and (type(value) is not int or not 0 <= value <= MAX_QUOTA):
        raise ValueError('INVALID_REQUEST')
    return value


@transaction.atomic
def department_quota(account, actor, department_id, data):
    """POST billing/org/departments/{id}/quota; the billing view already holds the account lock (Q-P6 A)."""
    if account.kind != 'business':
        raise ValueError('FORBIDDEN')
    ledger._authorize_purchase(account, actor)
    monthly, share = _bounded(data['monthly_quota']), _bounded(data['per_member_share'])
    if type(data['pooling_enabled']) is not bool or type(data['expected_version']) is not int:
        raise ValueError('INVALID_REQUEST')
    key = request_key(data['request_key'])
    try:
        department = Department.objects.select_for_update().get(account=account, pk=uuid.UUID(department_id))
    except (Department.DoesNotExist, ValueError) as exc:
        raise ValueError('NOT_FOUND') from exc
    result = lambda: {'department_id': str(department.pk), 'quota_version': department.quota_version,
                      'monthly_quota': department.monthly_quota, 'per_member_share': department.per_member_share,
                      'pooling_enabled': department.pooling_enabled}
    request_id = keyed_request_id(account, 'department-quota', key)
    previous = AuditEvent.objects.filter(account=account, action='org.quota.adjust', request_id=request_id).first()
    if previous:
        if previous.target_id != str(department.pk) or department.quota_version != data['expected_version'] + 1:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        return result()
    if department.quota_version != data['expected_version']:
        raise ValueError('VERSION_CONFLICT')
    start, end, _ = service_month(account)
    if monthly is not None and monthly < department_used(account, department, start, end):
        raise ValueError('QUOTA_BELOW_USED')
    before = department.monthly_quota
    department.monthly_quota, department.per_member_share = monthly, share
    department.pooling_enabled = data['pooling_enabled']
    department.quota_version += 1
    department.save()
    audit(account, actor, 'org.quota.adjust', '%s %s → %s' % (department.name, amount_label(before), amount_label(monthly)),
          'department', department.pk, request_id=request_id)
    return result()


def member_quota(account, actor, membership_id, data):
    """POST billing/org/members/{mid}/quota: QUOTA_BELOW_USED, then the existing set_shared_limit."""
    if not re.fullmatch(r'[1-9][0-9]{0,9}', membership_id):
        raise ValueError('NOT_FOUND')
    membership_id, quantity = int(membership_id), data['quantity']
    if quantity is not None and isinstance(quantity, str) and isinstance(data['request_key'], str) and \
            not SharedLimitChange.objects.filter(membership_id=membership_id, request_key=data['request_key']).exists():
        start, end, _ = service_month(account)
        used = used_by_member(account, start, end, [membership_id]).get(membership_id, Fraction(0))
        if ledger._quantity(quantity) < used:
            raise ValueError('QUOTA_BELOW_USED')
    return ledger.set_shared_limit(account, actor, membership_id, quantity, expected_version=data['expected_version'],
                                   request_key=data['request_key'])


# ---------------------------------------------------------------- policy (Q-P4, C-D-24)

POLICY_LABELS = {'approval_required_actions': '需审批的操作', 'approver_rule': '审批人', 'expert_share_max_scope': '专家共享范围',
                 'retention_days': '数据保留', 'byok_enabled': '自带密钥', 'extension_allowlist_kinds': '扩展白名单类别',
                 'extension_allowed_tiers': '扩展白名单来源'}
EXTENSION_KINDS = ('plugin', 'skill', 'connector', 'agent')
PUBLISHER_TIERS = ('official', 'community_reviewed', 'third_party', 'enterprise', 'local_import')


def policy(account):
    item, _ = AccountPolicy.objects.get_or_create(account=account, defaults={'expert_share_max_scope': 'account'})
    return item


def can_edit_policy(ctx):
    return ctx.member.role == 'owner' or ctx.member.is_security_admin


def policy_dict(ctx, item):
    entries = ExtensionAllowEntry.objects.filter(account=ctx.account).select_related('extension').order_by('created_at')
    return {**{field: getattr(item, field) for field in POLICY_LABELS}, 'version': item.version,
            'updated_at': iso(item.updated_at), 'can_edit': can_edit_policy(ctx),
            'extension_allow_entries': [allow_entry_dict(entry) for entry in entries]}


def allow_entry_dict(entry):
    return {'id': entry.public_id, 'added_at': iso(entry.created_at),
            'extension': {'id': entry.extension.public_id, 'name': entry.extension.name, 'kind': entry.extension.kind}}


def _subset(value, allowed):
    return isinstance(value, list) and len(set(value)) == len(value) and all(item in allowed for item in value)


def _valid_policy_value(field, value):
    if field == 'approval_required_actions':
        return isinstance(value, list) and len(value) <= 20 and all(isinstance(v, str) and 0 < len(v) <= 40 for v in value)
    if field == 'approver_rule':
        return isinstance(value, str) and len(value) <= 24
    if field == 'expert_share_max_scope':
        return value in ('only_me', 'department', 'account')
    if field == 'retention_days':
        return value is None or (type(value) is int and 1 <= value <= 3650)
    if field == 'byok_enabled':
        return type(value) is bool
    if field == 'extension_allowlist_kinds':
        return _subset(value, EXTENSION_KINDS)
    return _subset(value, PUBLISHER_TIERS)


@transaction.atomic
def update_policy(ctx, data):
    if not can_edit_policy(ctx):
        raise ValueError('PERMISSION_DENIED')
    changes = {key: value for key, value in data.items() if key != 'version'}
    if not changes or type(data['version']) is not int:
        raise ValueError('VALIDATION_FAILED')
    item = AccountPolicy.objects.select_for_update().get(pk=policy(ctx.account).pk)
    if item.version != data['version']:
        raise ValueError('VERSION_CONFLICT')
    if not all(_valid_policy_value(field, value) for field, value in changes.items()):
        raise ValueError('VALIDATION_FAILED')
    for field, value in changes.items():
        setattr(item, field, value)
    item.version += 1
    item.save()
    audit(ctx.account, ctx.user, 'org.policy.update', '更新策略：' + '、'.join(POLICY_LABELS[key] for key in changes),
          'policy', item.public_id)
    return policy_dict(ctx, item)


@transaction.atomic
def add_allow_entry(ctx, data):
    if not can_edit_policy(ctx):
        raise ValueError('PERMISSION_DENIED')
    extension = Extension.objects.filter(Q(account__isnull=True) | Q(account=ctx.account),
                                         public_id=data['extension_id']).first() if isinstance(
        data['extension_id'], str) else None
    if extension is None:
        raise ValueError('VALIDATION_FAILED')
    entry = ExtensionAllowEntry.objects.filter(account=ctx.account, extension=extension).first()
    if entry is None:
        entry = ExtensionAllowEntry.objects.create(account=ctx.account, extension=extension, added_by=ctx.user)
        audit(ctx.account, ctx.user, 'org.policy.update', '扩展白名单 添加 %s' % extension.name, 'extension',
              extension.public_id)
    return allow_entry_dict(entry)


@transaction.atomic
def remove_allow_entry(ctx, public_id):
    if not can_edit_policy(ctx):
        raise ValueError('PERMISSION_DENIED')
    entry = ExtensionAllowEntry.objects.filter(account=ctx.account, public_id=public_id).select_related('extension').first()
    if entry is None:
        raise ValueError('NOT_FOUND')
    entry.delete()
    audit(ctx.account, ctx.user, 'org.policy.update', '扩展白名单 移除 %s' % entry.extension.name, 'extension',
          entry.extension.public_id)
    return {'id': public_id, 'removed': True}


# ---------------------------------------------------------------- audit (C-D-25, Q-D5 A)

def require_audit_reader(ctx):
    if not (ctx.member.role == 'owner' or ctx.member.is_security_admin):
        raise ValueError('PERMISSION_DENIED')


def audit_events(ctx, params):
    require_audit_reader(ctx)
    rows = AuditEvent.objects.filter(account=ctx.account).select_related('actor').order_by('-created_at', '-id')
    if 'action' in params:
        rows = rows.filter(action=params['action'])
    if 'actor' in params:
        if not re.fullmatch(r'[1-9][0-9]{0,9}', params['actor']):
            raise ValueError('INVALID_REQUEST')
        member = Membership.objects.filter(account=ctx.account, pk=int(params['actor'])).first()
        rows = rows.filter(actor_id=member.user_id if member else None) if member else rows.none()
    return rows


def audit_members(account):
    return dict(Membership.objects.filter(account=account).values_list('user_id', 'pk'))


def audit_dict(event, members):
    actor = event.actor
    return {'id': str(event.pk), 'created_at': iso(event.created_at),
            'actor': {'id': members.get(actor.pk), 'display_name': display_name(actor)} if actor else None,
            'action': event.action, 'summary': event.summary, 'target_type': event.target_type,
            'target_id': event.target_id}


def _cell(value):
    text = '' if value is None else str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@') else text


def audit_export(ctx):
    require_audit_reader(ctx)
    now = clock.now()
    audit(ctx.account, ctx.user, 'audit.export', '导出近 90 天审计记录', 'account', ctx.account.pk)
    members = audit_members(ctx.account)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['created_at', 'actor', 'action', 'summary', 'target_type', 'target_id'])
    for event in AuditEvent.objects.filter(account=ctx.account, created_at__gte=now - timedelta(days=90)).select_related(
            'actor').order_by('-created_at', '-id'):
        row = audit_dict(event, members)
        writer.writerow([_cell(value) for value in (row['created_at'], row['actor']['display_name'] if row['actor'] else '',
                                                    row['action'], row['summary'], row['target_type'], row['target_id'])])
    response = HttpResponse('\ufeff' + buffer.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="audit-%s.csv"' % timezone.localtime(now).strftime('%Y%m%d')
    return response


# ---------------------------------------------------------------- offboarding (C-D-26, C-D-27, Q-S5 A, Q-D-5 A)

def task_used_credits(account, task):
    rows = CreditEntry.objects.filter(batch__account=account, kind='settle').filter(
        Q(reservation__task_key=task.public_id) | Q(reservation__task_key__startswith=task.public_id + ':'))
    return credits_text(sum((ledger._quantity(value) for value in rows.values_list('quantity', flat=True)), Fraction(0)))


def owned_workspaces(account, user):
    return Workspace.objects.filter(account=account, deleted_at__isnull=True, members__user=user, members__role='owner',
                                    members__state='active').order_by('name').distinct()


def pending_approvals(account, member):
    return AuthorizationRequest.objects.filter(account=account, decision='pending').filter(
        Q(task__owner=member) | Q(automation_run__automation__owner=member))


def custom_experts(account, user):
    return Expert.objects.filter(account=account, source='custom', created_by=user).exclude(status='archived')


def live_connections(account, user):
    return ConnectorConnection.objects.filter(account=account, owner_user=user, state__in=('connected', 'expired'))


def offboarding_preview(ctx, mid):
    require_business(ctx)
    require_role(ctx, 'owner')
    target = target_member(ctx.account, mid)
    tasks = Task.objects.filter(account=ctx.account, owner=target).order_by('-last_activity_at', '-id')
    user = target.user
    # 离职办理已执行后，接手人再看交接预览时，所有权与连接器撤销已完成：一并把交接记录里的对象列回来
    handed = HandoffItem.objects.filter(account=ctx.account, removal__membership=target,
                                        kind='workspace_ownership').values_list('target_id', flat=True)
    workspaces = {item.public_id: item for item in owned_workspaces(ctx.account, user)}
    for item in Workspace.objects.filter(account=ctx.account, public_id__in=handed):
        workspaces.setdefault(item.public_id, item)
    return target, tasks, {
        'approvals_count': pending_approvals(ctx.account, target).count(),
        'automations_count': Automation.objects.filter(account=ctx.account, owner=target).count(),
        'workspaces': [{'id': item.public_id, 'name': item.name} for item in
                       sorted(workspaces.values(), key=lambda row: row.name)],
        'custom_assets_count': custom_experts(ctx.account, user).count() +
        Playbook.objects.filter(account=ctx.account, created_by=user).count(),
        'connections_count': live_connections(ctx.account, user).count() +
        ConnectorConnection.objects.filter(account=ctx.account, owner_user=user, state='revoked',
                                           revoked_reason='offboarding').count()}


def offboard(account, actor, removal_id):
    """Run once per MemberRemoval inside the remove transaction; returns the handoff item count."""
    removal = MemberRemoval.objects.select_related('membership__user', 'successor_membership__user').get(
        account=account, pk=removal_id)
    if removal.successor_membership_id is None:
        return 0
    items = HandoffItem.objects.filter(account=account, removal=removal)
    if AuditEvent.objects.filter(account=account, action='member.offboard', target_id=str(removal.pk)).exists():
        return items.count()
    target, successor, now = removal.membership, removal.successor_membership, clock.now()
    user = target.user

    def hand(kind, target_type, target_id):
        item, _ = HandoffItem.objects.get_or_create(account=account, removal=removal, target_type=target_type,
                                                    target_id=target_id,
                                                    defaults={'kind': kind, 'successor': successor})
        return item

    tasks = list(Task.objects.filter(account=account, owner=target).exclude(status__in=DONE_TASK_STATUSES))
    for task in tasks:
        item = hand('task', 'task', task.public_id)
        if task.status in HANDOFF_PAUSE_STATUSES:
            # 确认接手后恢复到暂停前的状态；接手前的确认页靠 resume_status 展示「确认前保持暂停」
            item.resume_status = task.status
            item.save(update_fields=['resume_status', 'updated_at'])
            task.status, task.pause_reason = 'paused', 'handoff'
            task.save(update_fields=['status', 'pause_reason', 'updated_at'])
    for item in pending_approvals(account, target):
        hand('approval', 'authorization', item.public_id)
    for automation in Automation.objects.filter(account=account, owner=target):
        hand('automation', 'automation', automation.public_id)
        if automation.state == 'enabled':
            automation.state = 'paused'
            automation.save(update_fields=['state', 'updated_at'])
    for workspace in owned_workspaces(account, user):
        hand('workspace_ownership', 'workspace', workspace.public_id)
        row, _ = WorkspaceMember.objects.get_or_create(
            account=account, workspace=workspace, user=successor.user,
            defaults={'role': 'owner', 'state': 'active', 'invited_by': actor})
        if row.role != 'owner' or row.state != 'active':
            row.role, row.state = 'owner', 'active'
            row.save(update_fields=['role', 'state', 'updated_at'])
        WorkspaceMember.objects.filter(account=account, workspace=workspace, user=user, role='owner',
                                       state='active').update(state='removed', updated_at=now)
    for expert in custom_experts(account, user):
        hand('custom_expert', 'expert', expert.public_id)
    live_connections(account, user).update(state='revoked', revoked_reason='offboarding', updated_at=now)
    AuthorizationGrant.objects.filter(account=account, user=user, revoked_at__isnull=True).update(revoked_at=now,
                                                                                                 updated_at=now)
    if removal.memory_purge_acknowledged_at:
        deleted, _ = MemoryItem.objects.filter(account=account, user=user).delete()
        audit(account, actor, 'memory.purge', '%s 的记忆已删除（%d 条）' % (display_name(user), deleted), 'membership',
              target.pk)
    count = items.count()
    audit(account, actor, 'member.offboard', '%s → 接手人 %s' % (display_name(user), display_name(successor.user)),
          'member_removal', removal.pk)
    if count:
        notify(account=account, recipient=successor.user, category='mention', type='assigned',
               text='%s 的 %d 项工作已交接给你' % (display_name(user), count), dedupe_key='handoff:%s' % removal.pk,
               target_type='member_removal', target_id=str(removal.pk), action='go_inbox')
    return count
