"""Account screens 01/02/67/68/69/70/74: Lillian's profile, memories, grants and another signed-in device."""
import os
import secrets
from datetime import time, timedelta

from django.contrib.sessions.models import Session
from ._clock import timezone

from backend.models import AccountProfile, AccountSession, Membership
from backend.workbench.models import (AuthorizationGrant, AuthorizationRequest, Expert, Extension, MemoryItem,
                                      Task)

# Runs after other domains so tasks/experts/extensions they seed are reused instead of duplicated.
ORDER = 90

PROFILE = dict(preferred_name='Lillian', assistant_name='', business_markets=['德国', '美国'],
               business_channels=['Amazon', '独立站'], business_categories=['家用小家电'],
               dnd_enabled=True, dnd_start=time(22, 0), dnd_end=time(8, 0), idle_lock_minutes=30)
TASKS = ('Q4 达人合作清单', '德国站竞品格局调研', '首页文案 A/B 方案', 'Q4 广告复盘')
# category, content, source_kind, task title, days ago (None = 11 月 N 日)
MEMORIES = (
    ('about_me', '称呼我 Lillian；沟通时先给结论', 'onboarding', None, (11, 1)),
    ('about_me', '主营家用小家电，主要市场为德国、美国；渠道为 Amazon 与独立站', 'onboarding', None, (11, 1)),
    ('about_me', '表格偏好用 Excel 格式交付', 'task', 'Q4 达人合作清单', (11, 3)),
    ('recent_projects', '正在推进德国市场进入，计划 12 月上架 X1 系列', 'task', '德国站竞品格局调研', 0),
    ('recent_projects', '品牌官网改版处于文案 A/B 测试阶段', 'task', '首页文案 A/B 方案', 1),
    ('lessons', 'Jungle Scout 的销量估算在小类目误差较大，需与 Keepa 交叉核对', 'task', '德国站竞品格局调研', 0),
)
# extension, scope, task title or None (= workspace), when
GRANTS = (('Jungle Scout', 'workspace', None, 0), ('Amazon Seller Central', 'task', 'Q4 广告复盘', (11, 3)))
OTHER_DEVICE = ('Mac mini', '0.1.0', '杭州', timedelta(days=3))


def _when(spec, now):
    if isinstance(spec, int):
        return now - timedelta(days=spec)
    month, day = spec
    value = now.replace(month=month, day=day, hour=10, minute=0, second=0, microsecond=0)
    return value if value <= now else value.replace(year=now.year - 1)


def _task(account, workspace, membership, title):
    task = Task.objects.filter(account=account, title=title).order_by('id').first()
    if task is None:
        # Task.last_activity_at defaults to the wall clock; seeded drafts sit at the 11 月 3 日 memory/grant date.
        task = Task.objects.create(account=account, workspace=workspace if title == '德国站竞品格局调研' else None,
                                   title=title, creator=membership, owner=membership, model_mode='auto',
                                   last_activity_at=_when((11, 3), timezone.now()))
    return task


def _expert():
    expert = Expert.objects.filter(status='published').order_by('id').first() or Expert.objects.order_by('id').first()
    if expert is None:
        expert = Expert.objects.create(source='system', group='role', name='市场调研员', title='市场调研员',
                                       responsibilities='竞品与市场数据调研', version_label='v1', status='published',
                                       share_scope='account', communication_style='conclusion_first',
                                       uncertainty_policy='ask_first')
    return expert


def _extension(name):
    extension = Extension.objects.filter(name=name).order_by('id').first()
    if extension is None:
        extension = Extension.objects.create(kind='connector', name=name, description=name + ' 只读连接',
                                             publisher_tier='official', latest_version='1.0.0')
    return extension


def _seed_password(users):
    password = os.environ.get('SAGE_SEED_PASSWORD')
    if not password:
        return
    for user in users.values():
        if not user.check_password(password):
            user.set_password(password)
            user.save(update_fields=['password'])


def seed(ctx):
    if 'users' not in ctx:
        from . import core
        core.seed(ctx)
    now = timezone.now()
    users, account, workspace = ctx['users'], ctx['business'], ctx['workspace']
    lillian = users['lillian']
    membership = Membership.objects.get(account=account, user=lillian)
    _seed_password(users)

    # display_name is User.first_name (account.profile_data); Figma shows the full name "Lillian Li". The last
    # name is cleared so first + last name joins (task participants, billing) do not read "Lillian Li Li".
    if (lillian.first_name, lillian.last_name) != ('Lillian Li', ''):
        lillian.first_name, lillian.last_name = 'Lillian Li', ''
        lillian.save(update_fields=['first_name', 'last_name'])
    profile, _ = AccountProfile.objects.get_or_create(user=lillian)
    for key, value in PROFILE.items():
        setattr(profile, key, value)
    # 677:2: 两步验证（新设备登录时）开着。
    profile.two_step_on_new_device = True
    profile.onboarding_completed_at = profile.onboarding_completed_at or _when((11, 1), now)
    profile.save()

    tasks = {title: _task(account, workspace, membership, title) for title in TASKS}
    for category, content, source_kind, title, when in MEMORIES:
        row, created = MemoryItem.objects.get_or_create(
            account=account, user=lillian, content=content,
            defaults={'category': category, 'source_kind': source_kind,
                      'source_task': tasks[title] if title else None})
        if created:
            MemoryItem.objects.filter(pk=row.pk).update(created_at=_when(when, now))

    expert = _expert()
    for name, scope, title, when in GRANTS:
        extension, task = _extension(name), tasks[title] if title else None
        target = {'task': task, 'workspace': None} if task else {'task': None, 'workspace': workspace}
        if AuthorizationGrant.objects.filter(account=account, user=lillian, extension=extension, scope=scope,
                                             revoked_at__isnull=True, **target).exists():
            continue
        request = AuthorizationRequest.objects.create(
            account=account, task=task, requested_by_expert=expert, extension=extension, action_kind='read_query',
            scope_text='读取订单' if task else '只读查询', validity='this_task', decision='approved',
            decision_scope=scope, decided_by=lillian, decided_at=_when(when, now))
        grant = AuthorizationGrant.objects.create(account=account, user=lillian, scope=scope, extension=extension,
                                                  action_kind='read_query', source_request=request, **target)
        AuthorizationGrant.objects.filter(pk=grant.pk).update(created_at=_when(when, now))

    device, version, location, ago = OTHER_DEVICE
    row = AccountSession.objects.filter(user=lillian, device_name=device, revoked_at__isnull=True).first()
    if row is None or not Session.objects.filter(pk=row.session_key, expire_date__gt=now).exists():
        # A session row nobody holds a cookie for: it only makes "another signed-in device" visible on screen 74.
        key = secrets.token_hex(16)
        Session.objects.create(session_key=key, session_data='', expire_date=now + timedelta(days=30))
        if row:
            row.revoked_at = now
            row.save(update_fields=['revoked_at'])
        row = AccountSession.objects.create(user=lillian, session_key=key, client='Sage / macOS', ip=None,
                                            device_name=device, app_version=version, location_label=location,
                                            last_seen_at=now - ago)
        AccountSession.objects.filter(pk=row.pk).update(created_at=now - ago - timedelta(days=7))
    ctx.update(account_tasks=tasks)
