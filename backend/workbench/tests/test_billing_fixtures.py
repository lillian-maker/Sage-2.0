"""Fixtures shared by the billing-domain workbench tests (no test cases here)."""
from datetime import timedelta

from django.utils import timezone

from backend.commerce import billing as ledger
from backend.workbench.models import (AuthorizationRequest, Expert, Extension, Task, Workspace,
                                      WorkspaceMember)


def expert():
    item, _ = Expert.objects.get_or_create(catalog_code='wangye', defaults=dict(
        source='system', group='role', name='望野', title='市场研究员', responsibilities='竞品与市场调研',
        version_label='v1', share_scope='account', communication_style='concise',
        uncertainty_policy='ask_first', status='published'))
    return item


def extension(name='Jungle Scout', kind='connector'):
    item, _ = Extension.objects.get_or_create(account=None, kind=kind, name=name, defaults=dict(
        description=name, publisher_tier='official', latest_version='1.0'))
    return item


def workspace(account, user, name='德国市场进入'):
    item = Workspace.objects.create(account=account, name=name, created_from='blank', created_by=user)
    WorkspaceMember.objects.create(account=account, workspace=item, user=user, role='owner', state='active')
    return item


def task(account, member, title='德国站竞品格局调研', space=None, status='awaiting_user', **extra):
    return Task.objects.create(account=account, workspace=space, title=title, creator=member, owner=member,
                               model_mode='auto', status=status, **extra)


def authorization(account, item, **extra):
    values = dict(
        requested_by_expert=expert(), extension=extension(), action_kind='read_query',
        purpose_text='望野想用 Jungle Scout 读取 Amazon.de 小家电类目前 50 名竞品的销量估算。',
        scope_text='Amazon.de · 小家电类目 · 前 50 名', scope_params={'top_n': 50},
        scope_options=[{'top_n': 20, 'label': '前 20 名'}, {'top_n': 50, 'label': '前 50 名'}],
        estimate_state='estimated', estimated_credits=40, validity='this_request')
    values.update(extra)
    return AuthorizationRequest.objects.create(account=account, task=item, **values)


def credits(account, quantity='5000', key='fixture-credits'):
    return ledger.grant_credits(account, 'purchased', key, quantity, timezone.now() + timedelta(days=365))


def spend(account, key, quantity):
    reservation = ledger.reserve_credits(account, key, quantity)
    return ledger.settle_credits(account, reservation['id'], quantity)
