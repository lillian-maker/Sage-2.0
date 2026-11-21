"""Fixture builders shared by the inbox-domain contract tests (no test cases here)."""
from datetime import timedelta

from django.utils import timezone

from backend.commerce.billing import grant_credits
from backend.workbench.models import (Automation, AutomationPermission, ConnectorConnection, Expert, Extension,
                                      InboxItem, InboxItemEvent, InboxSource, Task, Workspace, WorkspaceMember)


def make_expert(name, account=None, **extra):
    fields = dict(source='system' if account is None else 'custom', group='role', name=name, title=name + '专家',
                  specialty=name + '方向', responsibilities=name + '的职责', version_label='v1', status='published',
                  share_scope='account', communication_style='concise', uncertainty_policy='ask_first',
                  avatar_ref='experts/%s.png' % name)
    fields.update(extra)
    return Expert.objects.create(account=account, **fields)


def make_extension(name, kind='connector', **extra):
    return Extension.objects.create(kind=kind, name=name, description=name + ' 扩展', publisher_tier='official',
                                    latest_version='1.0', **extra)


def make_connection(account, extension, user, state='connected'):
    return ConnectorConnection.objects.create(account=account, extension=extension, owner_user=user, state=state,
                                              connected_by=user, connected_at=timezone.now())


def make_source(account, user, kind='company_email', state='connected', **extra):
    ext = make_extension('来源-%s-%s' % (kind, user.pk))
    conn = make_connection(account, ext, user, state)
    return InboxSource.objects.create(account=account, owner_user=user, kind=kind, connection=conn,
                                      sender_filter='all_members_except_bots', **extra)


def make_item(source, title, group='todo', confidence=80, experts=(), minutes_ago=10, **extra):
    item = InboxItem.objects.create(
        account=source.account, recipient=source.owner_user, source=source, external_ref='ref-%s' % title,
        title=title, received_at=timezone.now() - timedelta(minutes=minutes_ago), original_text='原文 ' + title,
        group=group, confidence=confidence, rationale='依据 ' + title, sender_label='企业邮箱', **extra)
    item.suggested_experts.set(experts)
    InboxItemEvent.objects.create(account=item.account, item=item, at=item.received_at, kind='received',
                                  text='收到')
    return item


def make_workspace(account, creator, members=(), name='德国市场进入'):
    ws = Workspace.objects.create(account=account, name=name, created_from='blank', created_by=creator)
    for user, role in members:
        WorkspaceMember.objects.create(account=account, workspace=ws, user=user, role=role, state='active')
    return ws


def make_task(account, member, title='德国站竞品格局调研', workspace=None, status='running', lead=None, **extra):
    return Task.objects.create(account=account, workspace=workspace, title=title, creator=member, owner=member,
                               lead_expert=lead, model_mode='auto', status=status, **extra)


def give_credits(account, quantity='1000'):
    grant_credits(account, 'purchased', 'test-grant-%s' % account.pk, quantity, timezone.now() + timedelta(days=30))


def make_automation(account, member, workspace, lead, name='每周德国站竞品价格监控', state='draft', **extra):
    from datetime import time
    fields = dict(content_kind='prompt', prompt='对比竞品价格', schedule_kind='weekly', schedule_time=time(9, 0),
                  schedule_weekday=1, timezone='Asia/Shanghai')
    fields.update(extra)
    a = Automation.objects.create(account=account, owner=member, workspace=workspace, lead_expert=lead, name=name,
                                  state=state, **fields)
    AutomationPermission.objects.create(account=account, automation=a, kind='workspace_write', state='granted')
    return a
