"""Screens 53/75/78: Wang Min's offboarding — member list, offboarding dialog and the handover inbox.

The screen dates the removal 11 月 5 日 (14 days before the 11 月 19 日 seed clock). MemberRemoval is immutable (save
and queryset update both refuse) and created_at is auto_now_add, so the row is created with django's clock patched
to that instant; nothing else in the seed runs under the patch.

Wang Min's membership stays active: the 75/78 designs are the pre-confirmation state (the 办理离职 dialog opens from
his member row), while the 53 handover inbox is the post-removal state — the removal itself is real, run through
services.billing.offboard, so the hand-off items, paused tasks/automations, workspace ownership transfer, revoked
connections, audit row and "已交接给你" notification all come from the production path.
"""
from datetime import datetime, time, timedelta
from unittest import mock

from ._clock import timezone

from backend.commerce.models import CreditBatch, CreditEntry, Department, DepartmentMember, Reservation
from backend.models import MemberRemoval, Membership
from backend.workbench.models import (AuthorizationRequest, Automation, AutomationPermission, AutomationRun,
                                      ConnectorConnection, Expert, Playbook, PlaybookStep, Task, TaskStep, TaskThread,
                                      WorkspaceMember)
from backend.workbench.services import billing as org

from . import core

# After the expert seeds; offboarding must see every other seed's rows (experts_activity now runs later,
# but its rows belong to Lillian and do not affect Wang Min's offboarding).
ORDER = 96

USER = ('wangmin', 'wangmin@sage.demo', 'Wang', 'Min')
REQUEST_KEY = 'seed-handover-wangmin'
# title, status, lead expert name, last activity (hours before now) -- 78 行：已暂停/进行中/已完成 · 今天/昨天
TASKS = (('法国站选品调研', 'paused', '望野', 2), ('Q4 广告复盘', 'running', '觅位', 3),
         ('差评日报 · 11月4日', 'accepted', '回声', 26))
# 53「已暂停 · 停在第 4 步」：前三步完成，第四步在进行
FRANCE_STEPS = (('理解选品目标与范围', 'done'), ('收集法国站类目数据', 'done'), ('整理竞品清单', 'done'),
                ('估算竞品销量', 'running'), ('利润与物流测算', 'queued'), ('汇总选品建议', 'queued'))
# 78 行的已消耗 Credits，按任务结算进共享额度账本
TASK_CREDITS = (('法国站选品调研', '186'), ('Q4 广告复盘', '74'), ('差评日报 · 11月4日', '40'))
APPROVAL = dict(action_kind='read_query', scope_text='读取法国站竞品销量', scope_params={'marketplace': 'amazon.fr'},
                estimate_state='estimated', estimated_credits=24, validity='this_request',
                purpose_text='对比法国站前 20 名竞品近 30 天销量')
# 78「自动化 2 个」：名称, lead, schedule_kind, schedule_time
AUTOMATIONS = (('每月新市场机会扫描', '望野', 'monthly', time(9, 0)), ('差评日报', '回声', 'daily', time(18, 0)))
CUSTOM_EXPERT = '售后话术专家'
PLAYBOOKS = ('差评回复与补偿', '竞品价格周报')
CONNECTIONS = ('Jungle Scout', '企业邮箱')


def _expert(name):
    from .inbox import _expert as expert
    return expert(name)


def _extension(name):
    from .inbox import _extension as extension
    return extension(name)


def _task_credits(business, member, task, quantity, now):
    """Settled shared Credits keyed by the task, so offboarding preview shows real per-task usage."""
    if Reservation.objects.filter(account=business, task_key=task.public_id).exists():
        return
    key = 'seed-handover-credits-%s' % task.public_id
    batch = CreditBatch.objects.create(account=business, source='purchased', source_key='batch:' + key,
                                       initial_quantity=quantity, expires_at=now + timedelta(days=90))
    CreditEntry.objects.create(batch=batch, kind='grant', quantity=quantity, event_key='grant:' + key)
    reservation = Reservation.objects.create(account=business, task_key=task.public_id, membership=member,
                                             quantity=quantity, settled_quantity=quantity, state='settled',
                                             shared_period_start=now - timedelta(days=1),
                                             shared_period_end=now + timedelta(days=29))
    CreditEntry.objects.create(batch=batch, reservation=reservation, kind='reserve', quantity=quantity,
                               event_key='r:' + key)
    CreditEntry.objects.create(batch=batch, reservation=reservation, kind='settle', quantity=quantity,
                               event_key='s:' + key)


def seed(ctx):
    if 'users' not in ctx:
        core.seed(ctx)
    business, lillian = ctx['business'], ctx['memberships']['lillian']
    if MemberRemoval.objects.filter(account=business, request_key=REQUEST_KEY).exists():
        return
    now = timezone.now()
    key, email, first, last = USER
    user = core._user(email, first, last)
    ctx['users'][key] = user
    member, _ = Membership.objects.get_or_create(account=business, user=user, defaults={'role': 'member'})
    type(user).objects.filter(pk=user.pk).update(last_login=now - timedelta(days=1))
    department = Department.objects.filter(account=business, name='运营部').first()
    if department and not DepartmentMember.objects.filter(membership=member, ended_period__isnull=True).exists():
        DepartmentMember.objects.create(department=department, membership=member)
    tasks = {}
    for title, status, lead, hours in TASKS:
        task = Task.objects.filter(account=business, owner=member, title=title).first()
        if task is None:
            task = Task.objects.create(account=business, title=title, creator=member, owner=member,
                                       lead_expert=_expert(lead), model_mode='auto', status=status,
                                       last_activity_at=now - timedelta(hours=hours))
            TaskThread.objects.create(account=business, task=task, kind='main')
        tasks[title] = task
    research = tasks[TASKS[0][0]]
    if not research.steps.exists():
        for order, (title, status) in enumerate(FRANCE_STEPS, start=1):
            TaskStep.objects.create(account=business, task=research, order=order, stage='research', title=title,
                                    expert=research.lead_expert, status=status)
    for title, quantity in TASK_CREDITS:
        _task_credits(business, member, tasks[title], quantity, now)
    if not AuthorizationRequest.objects.filter(task=research).exists():
        AuthorizationRequest.objects.create(account=business, task=research, requested_by_expert=research.lead_expert,
                                            extension=_extension('Jungle Scout'), **APPROVAL)
    for name, lead, schedule_kind, schedule_time in AUTOMATIONS:
        if Automation.objects.filter(account=business, owner=member, name=name).exists():
            continue
        automation = Automation.objects.create(
            account=business, owner=member, name=name, lead_expert=_expert(lead), content_kind='prompt',
            prompt='%s：按排期自动执行' % name, schedule_kind=schedule_kind, schedule_time=schedule_time,
            schedule_monthday=1 if schedule_kind == 'monthly' else None,
            timezone='Asia/Shanghai', state='enabled')
        AutomationPermission.objects.create(account=business, automation=automation, kind='workspace_write',
                                            state='granted')
    # 05「直接拒绝」：法国站评估任务由每月新市场机会扫描 11月1日 09:00 的运行发起（复用已有运行记录）
    rejected_fr = Task.objects.filter(account=business, title='法国站新品机会评估').first()
    if rejected_fr and not rejected_fr.source_automation_run_id:
        run = AutomationRun.objects.filter(automation__account=business, automation__name='每月新市场机会扫描',
                                           scheduled_for=timezone.make_aware(datetime(2026, 11, 1, 9, 0))).first()
        if run:
            rejected_fr.source_automation_run = run
            rejected_fr.save(update_fields=['source_automation_run', 'updated_at'])
    if not Expert.objects.filter(account=business, source='custom', created_by=user, name=CUSTOM_EXPERT).exists():
        Expert.objects.create(account=business, source='custom', group='role', name=CUSTOM_EXPERT, title=CUSTOM_EXPERT,
                              responsibilities='整理德国站售后常见问题的标准回复话术', version_label='v1',
                              status='published', share_scope='department', shared_department=department,
                              created_by=user,
                              communication_style='conclusion_first', uncertainty_policy='ask_first')
    for name in PLAYBOOKS:
        if Playbook.objects.filter(account=business, created_by=user, name=name).exists():
            continue
        playbook = Playbook.objects.create(account=business, name=name, visibility='only_me', created_by=user,
                                           variables=[])
        PlaybookStep.objects.create(account=business, playbook=playbook, order=1, stage='research', title=name,
                                    expert=_expert('望野'))
    extensions = {name: _extension(name) for name in CONNECTIONS}
    for name, extension in extensions.items():
        ConnectorConnection.objects.get_or_create(
            account=business, extension=extension, owner_user=user,
            defaults={'state': 'connected', 'connected_by': user, 'connected_at': now - timedelta(days=30),
                      'credential_ref': 'seed:handover:%s' % name})
    # 工作区交接：Wang Min 持有「德国市场进入」，offboard 把所有权转给接手人 Lillian
    workspace = ctx['workspace']
    WorkspaceMember.objects.filter(account=business, workspace=workspace, user=lillian.user).update(role='editor')
    WorkspaceMember.objects.get_or_create(
        account=business, workspace=workspace, user=user,
        defaults={'role': 'owner', 'state': 'active', 'invited_by': lillian.user})
    removed_at = now - timedelta(days=14)
    with mock.patch('django.utils.timezone.now', return_value=removed_at):
        removal = MemberRemoval.objects.create(account=business, membership=member, actor=lillian.user,
                                               request_key=REQUEST_KEY, successor_membership=lillian,
                                               memory_purge_acknowledged_at=removed_at)
    org.offboard(business, lillian.user, removal.pk)
    ctx.update(handover_removal=removal, handover_member=member)
