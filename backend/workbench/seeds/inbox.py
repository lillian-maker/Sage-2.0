"""Inbox, notifications and automations sample data from Figma screens 47/48/52/57/60/61."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from ._clock import timezone

from backend.workbench.models import (AuthorizationRequest, Automation, AutomationPermission, AutomationRun,
                                      ConnectorConnection, Expert, Extension, InboxItem, InboxItemEvent,
                                      InboxSource, InboxSourceDailyStat, KickoffQuestion, Task, TaskDelivery,
                                      TaskParticipant, TaskStep, TaskThread)
from backend.workbench.services.notify import notify

from . import core
from .billing import AUTHORIZATION

ORDER = 40
# Minutes since last activity on the seed clock; screen 715 shows the A/B copy task at "26 分钟".
ACTIVITY_MINUTES = {'首页文案 A/B 方案': 26}
# Screen 48 list ages on the seed clock: 待你审批 10 分钟前, 待你补充 1 小时前, 待你验收 / 邀请 昨天. created_at is
# auto_now_add (wall clock), so the rows are back-dated after creation.
DECISION_AGE_MINUTES = {'approve': 10, 'supply': 60, 'accept': 25 * 60, 'invite': 26 * 60}
ZONE = ZoneInfo('Asia/Shanghai')

EXPERTS = ('立言', '解忧', '安心', '契约', '结伴', '守店', '安界', '通途', '望野', '觅位', '译境', '衡价', '回声', '明镜',
           '知量', '质守')

# title, source kind, group, confidence, priority, experts, sender_label, channel_label, (days back, hour, minute),
# original, translated, rationale, suggestion. Days back -1 means the most recent Monday before yesterday.
ITEMS = (
    ('德国站一条差评提到“插头过热”', 'amazon_buyer_messages', 'todo', 92, 'high', ('解忧', '质守'), 'Amazon.de 买家',
     'Amazon.de', (0, 10, 41),
     'Nach zwei Wochen wird der Stecker sehr heiß. Ich habe Angst, das Gerät weiter zu benutzen. ★☆☆☆☆',
     '用了两周插头变得很烫，我不敢再用了。',
     '涉及产品安全，属于高优先级；判断为待办的把握 92%。同类反馈本月第 3 条，可能与批次有关。',
     '交给解忧起草回复与补偿方案，同时请质守排查批次；回复按工作区规则使用德语。'),
    ('买家询问插头是否适配英国', 'amazon_buyer_messages', 'pending_confirmation', 45, 'normal', ('安心',),
     'Amazon.de 买家', '买家消息', (0, 10, 20),
     'Does this come with a UK plug or do I need an adapter?', '这款是英标插头吗，还是需要转换头？', None, None),
    ('供应商发来新报价单', 'company_email', 'pending_confirmation', 48, 'normal', ('契约',), 'Shenzhen OEM',
     '供应商邮件', (0, 9, 58), '附件为 2026 年第一季度报价单，单价较上季度上调 4%。', None, None, None),
    ('达人 @kuechenfreude 询问合作报价', 'company_email', 'todo', 85, 'normal', ('结伴',), '企业邮箱', '企业邮箱',
     (0, 9, 30), 'Hallo! Ich hätte Interesse an einer Kooperation. Können wir über die Konditionen sprechen?',
     '你好！我有兴趣合作，可以聊聊合作条件吗？', None, None),
    ('店铺绩效：订单缺陷率上升', 'store_notifications', 'todo', 96, 'high', ('守店',), 'Seller Central', '店铺后台',
     (0, 8, 12), 'Your Order Defect Rate has increased to 0.9% in the last 60 days.', '过去 60 天订单缺陷率上升到 0.9%。',
     None, None),
    ('WEEE 注册材料补件通知', 'feishu', 'in_progress', 90, 'normal', ('安界',), '企业邮箱', '企业邮箱', (1, 16, 5),
     'EPR 注册审核反馈：需补充 WEEE 品类申报表。', None, None, None),
    ('物流商 11 月报价确认', 'feishu', 'done', 88, 'normal', ('通途',), '企业邮箱', '企业邮箱', (-1, 11, 30),
     '物流商确认 11 月头程报价维持不变。', None, None, None),
)
FEISHU = {'watched_groups': ['#德国站运营', '#售后反馈'], 'keywords': ['差评', '退货', 'EPR', '报价'],
          'experts': ('解忧', '安界'), 'funnel': (12480, 12301, 179, 23)}

# name, lead, kind, time, weekday, monthday, state, missed
AUTOMATIONS = (
    ('每周德国站竞品价格监控', '衡价', 'weekly', time(9, 0), 1, None, 'enabled', False),
    ('Amazon 差评日报', '回声', 'daily', time(18, 0), None, None, 'enabled', False),
    ('月度经营复盘', '明镜', 'monthly', time(10, 0), None, 1, 'enabled', True),
    ('黑五库存预警', '知量', 'daily', time(8, 0), None, None, 'paused', False),
    ('新品上架清单', '知量', 'weekly', time(9, 0), 1, None, 'draft', False),
)
# Screens 57/58: per-rule prompt, estimate and the connector the rule reads (state of that permission).
AUTOMATION_DETAILS = {
    # 640:2 最近结果 reads the last run (成功 · 11月3日, 排除行); without it the cell fell back to the kind label.
    '每周德国站竞品价格监控': {
        'prompt': '对比 Amazon.de 小家电类目前 20 名竞品的价格变化，标出降价超过 10% 的商品，并给出我们是否跟进的建议。',
        'estimate': (3, 25, '1 份价格对比表'), 'connector': ('Jungle Scout', 'needs_connection'), 'last_success_days': 16},
    'Amazon 差评日报': {'last_success_days': 1},
    # Enabling fails (connection missing), so the list shows the roll-back toast of screen 57.
    '黑五库存预警': {'connector': ('Keepa', 'needs_connection')},
    # Screens 2827:2548 / 2946:1337: saved, not enabled, the catalog connection still missing and no estimate yet.
    '新品上架清单': {'prompt': '整理本周上架的新品并生成清单。',
               'estimate': (None, None, None), 'connector': ('店铺商品目录', 'needs_connection')},
}
# The scan runs the "新市场进入评估" Playbook, whose lead is 望野 (screen 59 "望野 · 由 Playbook 指定"); the catalog
# has no separate scanning expert.
SCAN_STEPS = (('understand', '确认扫描范围与市场清单', '望野'), ('research', '采集各市场品类搜索趋势', '望野'),
              ('analyze', '评估进入门槛与合规要求', '安界'), ('deliver', '整理新市场机会清单', '望野'))


def _expert(name):
    expert = Expert.objects.filter(account__isnull=True, name=name).first()
    if expert is None:
        expert = Expert.objects.create(
            source='system', group='role', name=name, title=name, specialty=name, responsibilities=name,
            avatar_ref='experts/%s.png' % name, version_label='v1', status='published', share_scope='account',
            communication_style='concise', uncertainty_policy='ask_first')
    return expert


def _extension(name, kind='connector'):
    ext = Extension.objects.filter(account__isnull=True, name=name).first()
    if ext is None:
        ext = Extension.objects.create(kind=kind, name=name, description='%s 连接器' % name,
                                       publisher_tier='official', latest_version='1.0')
    return ext


def _connection(account, user, name):
    ext = _extension(name)
    conn, _ = ConnectorConnection.objects.get_or_create(
        account=account, extension=ext, owner_user=user,
        defaults={'state': 'connected', 'connected_by': user, 'connected_at': timezone.now()})
    return conn


SOURCE_CONNECTORS = {'amazon_buyer_messages': 'Amazon 卖家中心', 'company_email': '企业邮箱',
                     'store_notifications': 'Amazon 店铺通知', 'feishu': '飞书'}


def _sources(account, user, experts):
    sources = {}
    for kind, connector in SOURCE_CONNECTORS.items():
        conn = _connection(account, user, connector)
        defaults = {'connection': conn, 'sender_filter': 'all_members_except_bots'}
        if kind == 'feishu':
            defaults.update(watched_groups=FEISHU['watched_groups'], keywords=FEISHU['keywords'],
                            initiative='draft_suggestion')
        source, created = InboxSource.objects.get_or_create(account=account, owner_user=user, kind=kind,
                                                            defaults=defaults)
        if created and kind == 'feishu':
            source.responsible_experts.set([experts[name] for name in FEISHU['experts']])
        sources[kind] = source
    received, rules, judged, admitted = FEISHU['funnel']
    today = timezone.localdate()
    if not InboxSourceDailyStat.objects.filter(source=sources['feishu']).exists():
        for offset in range(7):
            share = 7 if offset == 0 else 1
            divisor = 13
            InboxSourceDailyStat.objects.create(
                account=account, source=sources['feishu'], date=today - timedelta(days=offset),
                received=received * share // divisor, after_rules=rules * share // divisor,
                after_expert=judged * share // divisor, admitted=admitted * share // divisor)
        stat = InboxSourceDailyStat.objects.get(source=sources['feishu'], date=today)
        totals = [sum(InboxSourceDailyStat.objects.filter(source=sources['feishu']).values_list(f, flat=True))
                  for f in ('received', 'after_rules', 'after_expert', 'admitted')]
        stat.received += received - totals[0]
        stat.after_rules += rules - totals[1]
        stat.after_expert += judged - totals[2]
        stat.admitted += admitted - totals[3]
        stat.save()
    return sources


def _received_at(today, back, hour, minute):
    if back < 0:
        back = today.weekday() or 7
        if back < 2:
            back += 7
    return datetime.combine(today - timedelta(days=back), time(hour, minute), tzinfo=ZONE)


def _items(account, user, member, workspace, sources, experts):
    today = timezone.localdate()
    for index, (title, kind, group, confidence, priority, names, label, channel, (back, hour, minute), original,
                translated, rationale, suggestion) in enumerate(ITEMS):
        received_at = _received_at(today, back, hour, minute)
        item, created = InboxItem.objects.get_or_create(
            source=sources[kind], external_ref='seed-%d' % index,
            defaults={'account': account, 'recipient': user, 'title': title, 'received_at': received_at,
                      'original_text': original, 'translated_text': translated, 'group': group,
                      'confidence': confidence, 'priority': priority, 'sender_label': label,
                      'channel_label': channel, 'workspace': workspace,
                      'rationale': rationale or '命中关键词且与工作区“%s”的在办事项相关。' % workspace.name,
                      'suggestion': suggestion or '交给%s处理，先给出回复草稿再由你确认。' % names[0]})
        if not created:
            continue
        item.suggested_experts.set([experts[name] for name in names])
        InboxItemEvent.objects.create(account=account, item=item, at=received_at, kind='received',
                                      text='%s 收到并归入工作区“%s”' % (received_at.strftime('%H:%M'), workspace.name))
        if group in ('in_progress', 'done'):
            task = Task.objects.create(account=account, workspace=workspace, title=title[:80], creator=member,
                                       owner=member, lead_expert=experts[names[0]], model_mode='auto',
                                       status='running' if group == 'in_progress' else 'accepted',
                                       source_inbox_item=item, last_activity_at=received_at)
            TaskThread.objects.create(account=account, task=task, kind='main')
            item.task = task
            item.save(update_fields=['task', 'updated_at'])
            InboxItemEvent.objects.create(account=account, item=item, at=received_at + timedelta(minutes=2),
                                          kind='handed_off', text='交给%s处理' % names[0])


def _task(account, member, workspace, title, lead, status='running'):
    task = Task.objects.filter(account=account, title=title, owner=member).first()
    if task is None:
        task = Task.objects.create(account=account, workspace=workspace, title=title, creator=member, owner=member,
                                   lead_expert=lead, model_mode='auto', status=status,
                                   last_activity_at=timezone.now() - timedelta(minutes=ACTIVITY_MINUTES.get(title, 0)))
        TaskThread.objects.create(account=account, task=task, kind='main')
    return task


def _decisions(ctx, account, users, memberships, workspace, experts):
    from .artifacts import workspace as named_workspace
    lillian, chen = memberships['lillian'], memberships['chen']
    now = timezone.now()

    def age(kind):
        return now - timedelta(minutes=DECISION_AGE_MINUTES[kind])

    research = _task(account, lillian, workspace, '德国站竞品格局调研', experts['望野'], status='awaiting_user')
    # One request shared with seeds/billing.py (same purpose_text), so screen 48 shows a single 40 Credits approval.
    if not AuthorizationRequest.objects.filter(task=research, purpose_text=AUTHORIZATION['purpose_text']).exists():
        request = AuthorizationRequest.objects.create(account=account, task=research,
                                                      requested_by_expert=experts['望野'],
                                                      extension=_extension('Jungle Scout'), **AUTHORIZATION)
        AuthorizationRequest.objects.filter(pk=request.pk).update(created_at=age('approve'))
        # The "德语 Listing 本地化样稿 v1 · 译境" accept card is the pending TaskDelivery from seeds/tasks.py.
        TaskDelivery.objects.filter(account=account, decision='pending', task__owner=lillian,
                                    task__title='德语 Listing 本地化').update(created_at=age('accept'))
    keywords = _task(account, lillian, workspace, 'Amazon.de 关键词与类目机会', experts['觅位'])
    question, created = KickoffQuestion.objects.get_or_create(account=account, task=keywords, order=1,
                                                              defaults={'question': '需要确认目标价格带'})
    if created:
        KickoffQuestion.objects.filter(pk=question.pk).update(created_at=age('supply'))
    abtest = _task(account, chen, named_workspace(ctx, '品牌官网改版'), '首页文案 A/B 方案', experts['立言'])
    invite, created = TaskParticipant.objects.get_or_create(account=account, task=abtest, kind='human',
                                                            user=users['lillian'],
                                                            defaults={'role': 'collaborator', 'state': 'invited',
                                                                      'invited_by': users['chen']})
    if created:
        TaskParticipant.objects.filter(pk=invite.pk).update(created_at=age('invite'))


def _automations(account, member, workspace, experts):
    now = timezone.now()
    created = {}
    for name, lead, kind, at, weekday, monthday, state, missed in AUTOMATIONS:
        a = Automation.objects.filter(account=account, owner=member, name=name).first()
        if a is None:
            details = AUTOMATION_DETAILS.get(name, {})
            minutes, credits, output = details.get('estimate', (15, 20, None))
            a = Automation.objects.create(
                account=account, owner=member, workspace=workspace, name=name, lead_expert=experts[lead],
                content_kind='prompt', prompt=details.get('prompt', '%s：汇总结果写入工作区“%s”' % (name, workspace.name)),
                schedule_kind=kind, schedule_time=at, schedule_weekday=weekday, schedule_monthday=monthday,
                timezone='Asia/Shanghai', state=state, estimated_duration_minutes=minutes, estimated_credits=credits,
                expected_output=output)
            if 'connector' in details:
                connector, permission_state = details['connector']
                AutomationPermission.objects.create(account=account, automation=a, kind='connector_read',
                                                    extension=_extension(connector), state=permission_state)
            AutomationPermission.objects.create(account=account, automation=a, kind='workspace_write', state='granted')
            if 'last_success_days' in details:
                local = (now - timedelta(days=details['last_success_days'])).astimezone(ZONE)
                at_time = local.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
                AutomationRun.objects.create(account=account, automation=a, title='运行指令 · %s' % name,
                                             trigger='schedule', scheduled_for=at_time, started_at=at_time,
                                             finished_at=at_time + timedelta(minutes=minutes), state='succeeded')
            if state == 'enabled':
                from backend.workbench.services.automations import compute_next_run
                a.next_run_at = compute_next_run(a, now)
                a.save(update_fields=['next_run_at'])
            if missed:
                from backend.workbench.services.automations import record_missed
                last = datetime(now.year, now.month, 1, 10, 0, tzinfo=ZONE)
                record_missed(a, last, 'mac_asleep', now)
        created[name] = a
    scan = Automation.objects.filter(account=account, owner=member, name='每月新市场机会扫描').first()
    if scan is None:
        scan = Automation.objects.create(
            account=account, owner=member, workspace=workspace, name='每月新市场机会扫描', lead_expert=experts['望野'],
            content_kind='prompt', prompt='扫描欧洲新市场机会', schedule_kind='monthly', schedule_time=time(9, 0),
            schedule_monthday=1, timezone='Asia/Shanghai', state='enabled')
        AutomationPermission.objects.create(account=account, automation=scan, kind='workspace_write', state='granted')
        # 相对播种时间取近 7 天内（D 组：固定历史日期让运行时长显示成 382 天）。
        scheduled = (now - timedelta(days=2)).astimezone(ZONE).replace(hour=9, minute=0, second=0, microsecond=0)
        run = AutomationRun.objects.create(account=account, automation=scan, title='运行指令 · 每月新市场机会扫描',
                                           trigger='schedule', scheduled_for=scheduled, started_at=scheduled,
                                           state='running')
        task = Task.objects.create(account=account, workspace=workspace, title=scan.name, creator=member, owner=member,
                                   lead_expert=experts['望野'], model_mode='auto', status='running',
                                   source_automation_run=run, last_activity_at=scheduled)
        TaskThread.objects.create(account=account, task=task, kind='main')
        for order, (stage, title, expert) in enumerate(SCAN_STEPS, 1):
            status = 'done' if order <= 2 else 'running' if order == 3 else 'queued'
            TaskStep.objects.create(
                account=account, task=task, order=order, stage=stage, title=title, expert=experts[expert],
                status=status, estimated_credits=6, started_at=scheduled + timedelta(minutes=4 * (order - 1))
                if order <= 3 else None, finished_at=scheduled + timedelta(minutes=4 * order) if order <= 2 else None)
        run.task = task
        run.save(update_fields=['task', 'updated_at'])
        from backend.workbench.services.automations import compute_next_run
        scan.next_run_at = compute_next_run(scan, now)
        scan.save(update_fields=['next_run_at'])
    return created


def _notifications(account, user, experts):
    now = timezone.now()
    # 61 屏：两条任务进展都属于工作区“德国市场进入”的任务（D26）。
    notify(account=account, recipient=user, category='task_progress', type='step_completed',
           text='安界完成了“德国 EPR 与 WEEE 合规清单”第 3 步', context_label='德国市场进入', target_type='task',
           action='open', actor_expert=experts['安界'], dedupe_key='seed:step:anjie:epr:3',
           occurred_at=now - timedelta(minutes=32))
    notify(account=account, recipient=user, category='task_progress', type='delivered',
           text='德国站竞品格局调研 已交付，等你验收（在收件中处理）', context_label='德国市场进入',
           target_type='task', action='go_inbox', dedupe_key='seed:delivered:competitor-report',
           occurred_at=now - timedelta(hours=1))
    notify(account=account, recipient=user, category='mention', type='mentioned', text='Chen Yu 在任务中提到了你',
           context_label='首页文案 A/B 方案', target_type='task', action='go_inbox', dedupe_key='seed:mention:chen',
           occurred_at=now - timedelta(hours=1))
    # Screen 48 "@与指派 3": this mention, the one above and the Wang Min hand-off from seeds/handover.py.
    notify(account=account, recipient=user, category='mention', type='mentioned',
           text='Chen Yu 在任务中提到了你：首月预算按 €3,000 安排', context_label='德国站广告投放计划',
           target_type='task', action='go_inbox', dedupe_key='seed:mention:chen:ads',
           occurred_at=now - timedelta(hours=3))


def seed(ctx):
    if 'users' not in ctx:
        core.seed(ctx)
    account, users, memberships, workspace = ctx['business'], ctx['users'], ctx['memberships'], ctx['workspace']
    experts = {name: _expert(name) for name in EXPERTS}
    sources = _sources(account, users['lillian'], experts)
    _items(account, users['lillian'], memberships['lillian'], workspace, sources, experts)
    _decisions(ctx, account, users, memberships, workspace, experts)

    automations = _automations(account, memberships['lillian'], workspace, experts)
    _notifications(account, users['lillian'], experts)
    ctx.update(inbox_sources=sources, inbox_experts=experts, automations=automations)
