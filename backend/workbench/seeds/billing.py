"""Billing / org screens: pending authorization (C-A-5), usage (C-C-8/9), departments, quotas, audit and offboarding."""
import uuid
from datetime import timedelta

from ._clock import timezone

from backend.commerce import billing as ledger
from backend.commerce.models import (BillingNotification, CreditBatch, CreditEntry, Department, DepartmentMember, Order, PaidSeat,
                                     PaymentFact, ProductVersion, Quote, Reservation, Subscription,
                                     SubscriptionPeriod)
from backend.models import AuditEvent, BillingAccount, InvoiceRequest, Membership
from backend.workbench.models import (AccountPolicy, AuthorizationRequest, Automation, AutomationRun, Expert, Extension,
                                     Task)

from . import core

# After core; before account (90), which reuses the business tasks created here by title.
ORDER = 80

SEED_NAMESPACE = uuid.UUID('0d6c1f6e-3a0b-4c8e-9f57-5b2d6a1e7c40')
# name, monthly_quota, per_member_share, members (key, is_lead) -- 758:314/318, 745:296, 761:326
DEPARTMENTS = (('运营部', 9000, 3000, (('lillian', False), ('chen', True), ('mark', False))),
               ('市场部', 4000, 2000, (('zhao', True),)))
# 76 屏：运营部打开了部门内调剂（每人份额一半保底、一半进调剂池）。
POOLED_DEPARTMENTS = ('运营部',)
# Settled shared Credits per business member this service month.
# 76 屏运营部成员额度：Lillian 本期已用 1,280。
BUSINESS_SPEND = (('lillian', '1280'), ('chen', '640'))
# Personal account usage (C-C-9, 63 屏): task title, Credits, settled days ago, extra hours back; 1,280 used this
# period in total. The two same-day rows need distinct hours so 641:2 sorts 420 before 310 (reverse last_used_at).
PERSONAL_TASKS = (('德国站竞品格局调研', '420', 0, 0), ('德国 EPR 与 WEEE 合规清单', '310', 0, 2), ('整理本周 Amazon 差评', '180', 1, 0),
                  ('Amazon 差评日报', '40', 1, 0), ('首页文案 A/B 方案', '330', 2, 0))
# 63 屏「Amazon 差评日报（自动化）」：个人账本里由自动化运行产生的任务。
PERSONAL_AUTOMATION_TASKS = ('Amazon 差评日报',)
PERSONAL_CREDITS = '5000'
# Enterprise subscription seats (758 / 760「10 已购名额」).
SEAT_QUANTITY = 10
# Offboarding preview (760:476) shows Wang Min; core pins the business member set, so Mark owns these instead.
OFFBOARDING_TASKS = (('欧洲站售后话术整理', 'running'), ('11 月退货原因分析', 'awaiting_user'))
# action, summary, target_type, actor, (day of the current month, hour, minute) -- 759:321–354, newest first
AUDIT = (('org.quota.adjust', '市场部 3,000 → 4,000', 'department', 'lillian', (5, 9, 10)),
         ('org.policy.update', '需要审批的操作：增加“安装第三方扩展”', 'policy', 'sun', (4, 18, 3)),
         ('authorization.approve', '批准 Jungle Scout 只读查询', 'authorization', 'chen', (4, 14, 27)),
         ('member.remove', 'Zhou Lei → 接手人 Chen Yu', 'membership', 'lillian', (3, 16, 20)),
         ('member.department.change', 'Mark Weber → 运营部（立即生效）', 'membership', 'lillian', (2, 11, 0)),
         ('org.policy.update', '数据保留 180 天 → 365 天', 'policy', 'lillian', (1, 10, 0)))
# 759:270「支付、对外发布、安装第三方扩展」
APPROVAL_REQUIRED = ['payment', 'external_publish', 'install_third_party_extension']
AUTHORIZATION = dict(
    action_kind='read_query', title='使用 Jungle Scout 读取竞品销量',
    purpose_text='望野想用 Jungle Scout 读取 Amazon.de 小家电类目前 50 名竞品的销量估算。',
    scope_text='Amazon.de · 小家电类目 · 前 50 名', scope_params={'top_n': 50},
    scope_options=[{'top_n': 20, 'label': '前 20 名'}, {'top_n': 50, 'label': '前 50 名'}],
    estimate_state='estimated', estimated_credits=40, validity='this_request')


def _key(*parts):
    return uuid.uuid5(SEED_NAMESPACE, ':'.join(str(part) for part in parts))


def _expert():
    expert = Expert.objects.filter(catalog_code='wangye').first() or Expert.objects.filter(status='published').order_by('id').first()
    if expert is None:
        expert = Expert.objects.create(source='system', group='role', name='望野', title='市场研究员',
                                       responsibilities='竞品与市场调研', version_label='v1', status='published',
                                       share_scope='account', communication_style='conclusion_first',
                                       uncertainty_policy='ask_first')
    return expert


def _extension(name):
    extension = Extension.objects.filter(name=name).order_by('id').first()
    if extension is None:
        extension = Extension.objects.create(kind='connector', name=name, description=name + ' 只读连接',
                                             publisher_tier='official', latest_version='1.0.0')
    return extension


def _task(account, membership, title, workspace=None, status='awaiting_user'):
    task = Task.objects.filter(account=account, title=title).order_by('id').first()
    if task is None:
        # last_activity_at follows the seed clock, not the Task default (wall clock).
        task = Task.objects.create(account=account, workspace=workspace, title=title, creator=membership,
                                   owner=membership, model_mode='auto', status=status, last_activity_at=timezone.now())
    return task


def _shared_spend(account, membership, quantity, now):
    """Settled shared Credits written straight to the ledger, so the demo needs no paid seats."""
    key = 'seed-billing-shared-%d' % membership.pk
    if Reservation.objects.filter(account=account, task_key=key).exists():
        return
    batch = CreditBatch.objects.create(account=account, source='purchased', source_key='batch:' + key,
                                       initial_quantity=quantity, expires_at=now + timedelta(days=90))
    CreditEntry.objects.create(batch=batch, kind='grant', quantity=quantity, event_key='grant')
    reservation = Reservation.objects.create(account=account, task_key=key, membership=membership, quantity=quantity,
                                             settled_quantity=quantity, state='settled',
                                             # same window as the subscription period below, which is
                                             # what the member rows count shared usage against
                                             shared_period_start=_month_start(now, 0),
                                             shared_period_end=_month_start(now, -1))
    CreditEntry.objects.create(batch=batch, reservation=reservation, kind='reserve', quantity=quantity,
                               event_key='r:' + key)
    CreditEntry.objects.create(batch=batch, reservation=reservation, kind='settle', quantity=quantity,
                               event_key='s:' + key)


def _orders_seats(business, members, users, now):
    """757 屏已支付订单 + 生效中的月度订阅与已分配座位（F 组开工/试一下额度的根因）。"""
    if Subscription.objects.filter(account=business).exists():
        return
    subscription_snapshot = {'product_id': 'seed', 'code': 'Enterprise', 'version': 1,
                             'kind': 'subscription', 'audience': 'business', 'billing_cycle': 'monthly',
                             'seat_quantity': SEAT_QUANTITY, 'unit_amount_minor': 29700, 'amount_minor': 29700 * SEAT_QUANTITY,
                             'currency': 'CNY'}
    subscription_product = ProductVersion.objects.create(
        code='Enterprise', version=1, kind='subscription', audience='business',
        billing_cycle='monthly', unit_amount_minor=29700, minimum_seats=1, state='published',
        effective_at=now - timedelta(days=90))
    subscription_quote = Quote.objects.create(account=business, product=subscription_product,
                                              snapshot=subscription_snapshot, amount_minor=29700 * SEAT_QUANTITY, currency='CNY',
                                              expires_at=now + timedelta(days=30))
    subscription_order = Order.objects.create(
        account=business, created_by=users['lillian'], quote=subscription_quote,
        idempotency_key='seed-billing-subscription', request_hash='seed', snapshot=subscription_snapshot,
        amount_minor=29700 * SEAT_QUANTITY, currency='CNY', status='fulfilled', paid_at=now - timedelta(days=2),
        display_number='SG-2026-11-0008')
    PaymentFact.objects.create(order=subscription_order, channel='alipay', merchant_reference='seed-subscription',
                               transaction_reference='2026110122001000000000001234', amount_minor=29700 * SEAT_QUANTITY,
                               currency='CNY', evidence_hash='a' * 64, disposition='accepted',
                               paid_at=now - timedelta(days=2))
    # The enterprise service month is the calendar month (745「本服务月结束 11月30日」).
    subscription = Subscription.objects.create(
        account=business, initial_order=subscription_order, original_anchor=_month_start(now, 0),
        billing_timezone='Asia/Shanghai', calendar_rule_version='v1', billing_cycle='monthly',
        mandate={}, auto_renew=True)
    # 758 / 760「10 已购名额 = 4 已分配 + 1 邀请预留 + 0 本月锁定 + 5 可分配」：Wang Min 的座位在 d82_org 里分配。
    assigned = {1: members['lillian'], 2: members['chen'], 3: members['zhao']}
    seats = [PaidSeat.objects.create(subscription=subscription, position=position, membership=assigned.get(position))
             for position in range(1, SEAT_QUANTITY + 1)]
    SubscriptionPeriod.objects.create(
        subscription=subscription, order=subscription_order, cycle_index=0,
        starts_at=_month_start(now, 0), ends_at=_month_start(now, -1),
        snapshot={**subscription_snapshot, 'credits_quantity': '0',
                  'seat_ids': [str(seat.pk) for seat in seats]})

    # The 5,000 Credits top-up order (SG-2026-11-0042) belongs to the personal ledger (64/66 屏); the business
    # ledger keeps the same Credits as a plain grant so its balances do not change.
    ledger.grant_credits(business, 'purchased', 'batch:seed-order-credits-5000', '5000', now + timedelta(days=365))
    # 共享池余量：让开发环境开工任务有额度可预占（BUSINESS_SPEND 的批次已全部结算）。
    if not CreditBatch.objects.filter(account=business, source_key='seed-billing-pool').exists():
        ledger.grant_credits(business, 'purchased', 'seed-billing-pool', '50000', now + timedelta(days=365))


# 64 屏个人账本：Pro 月度订阅三个周期（9 月待核对、10 月已支付、11 月本期进行中）与 10 月 18 日加购 2,000 Credits。
# Months follow the seed clock; the credits order is display-only (no grant), so the personal balance stays 5,000.
PERSONAL_PLAN = {'product_id': 'seed', 'code': 'pro', 'version': 1, 'kind': 'subscription', 'audience': 'personal',
                 'billing_cycle': 'monthly', 'unit_amount_minor': 49900, 'amount_minor': 49900, 'currency': 'CNY'}
PERSONAL_TOPUP = {'product_id': 'seed', 'code': 'seed-credits-2000', 'version': 1, 'kind': 'credits',
                  'audience': 'personal', 'credits_quantity': '2000', 'unit_amount_minor': 19900,
                  'amount_minor': 19900, 'currency': 'CNY'}
# months back, display sequence, day of month, needs review. The 10月 invoice is created on the 31st so 764:2 lists
# it after the 10月18日 top-up (设计按行的时间倒序), while the period text still reads 10月1日 – 10月30日.
PERSONAL_PERIODS = ((2, '0142', 1, True), (1, '0187', 31, False), (0, '0021', 1, False))
PERSONAL_TOPUP_ORDER = (1, '0193', 18)
# 66 屏：本月 3 日 14:22 加购 5,000 Credits，标价 ¥499，加购优惠 −¥50（discount_* 存在订单快照里，D29）。
PERSONAL_CREDITS_ORDER = {'product_id': 'seed', 'code': 'seed-credits-5000', 'version': 1, 'kind': 'credits',
                          'audience': 'personal', 'credits_quantity': '5000', 'unit_amount_minor': 49900,
                          'amount_minor': 44900, 'discount_minor': 5000, 'discount_label': '加购 5,000 Credits 省 10%',
                          'currency': 'CNY'}
PERSONAL_CREDITS_REFERENCE = '2026110322001000000000008821'
# 64 屏「下载发票」：10 月的两张账单已开具发票。
PERSONAL_INVOICED = ('0187', '0193')


def _personal_automation_run(personal, owner, name, at):
    automation = Automation.objects.filter(account=personal, owner=owner, name=name).first()
    if automation is None:
        automation = Automation.objects.create(
            account=personal, owner=owner, name=name, content_kind='prompt',
            prompt='汇总昨天 Amazon 店铺的新差评并按主题归类', schedule_kind='daily',
            schedule_time=timezone.localtime(at).time().replace(hour=18, minute=0, second=0, microsecond=0),
            timezone='Asia/Shanghai', state='enabled', estimated_duration_minutes=15, estimated_credits=40)
    scheduled = timezone.localtime(at).replace(hour=18, minute=0, second=0, microsecond=0)
    run, _ = AutomationRun.objects.get_or_create(
        account=personal, automation=automation, scheduled_for=scheduled,
        defaults=dict(title='运行指令 · %s' % name, trigger='schedule', started_at=scheduled,
                      finished_at=scheduled + timedelta(minutes=12), state='succeeded'))
    return run


def _month_start(now, back):
    local = timezone.localtime(now)
    year, month = local.year, local.month - back
    while month < 1:
        year, month = year - 1, month + 12
    while month > 12:
        year, month = year + 1, month - 12
    return local.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _personal_order(personal, user, product, snapshot, key, number, at, needs_review=False):
    quote = Quote.objects.create(account=personal, product=product, snapshot=snapshot,
                                 amount_minor=snapshot['amount_minor'], currency='CNY', expires_at=at + timedelta(days=1))
    order = Order.objects.create(account=personal, created_by=user, quote=quote, idempotency_key=key,
                                 request_hash='seed', snapshot=snapshot, amount_minor=snapshot['amount_minor'],
                                 currency='CNY', status='fulfilled', paid_at=at, display_number=number)
    Order.objects.filter(pk=order.pk).update(created_at=at)
    PaymentFact.objects.create(order=order, channel='alipay', merchant_reference=key,
                               transaction_reference=at.strftime('%Y%m%d') + '22001' + number.replace('-', ''),
                               amount_minor=snapshot['amount_minor'], currency='CNY', evidence_hash=('c' if needs_review else 'd') * 64,
                               disposition='amount_review' if needs_review else 'accepted', paid_at=at)
    return order


def _personal_orders(personal, user, now):
    if Subscription.objects.filter(account=personal).exists():
        return Order.objects.filter(account=personal, idempotency_key='seed-personal-credits-5000').first()
    plan, _ = ProductVersion.objects.get_or_create(
        code='pro', version=1, defaults=dict(kind='subscription', audience='personal', billing_cycle='monthly',
                                             unit_amount_minor=49900, state='published',
                                             effective_at=now - timedelta(days=365)))
    topup, _ = ProductVersion.objects.get_or_create(
        code='seed-credits-2000', version=1, defaults=dict(kind='credits', audience='personal', billing_cycle='',
                                                           unit_amount_minor=19900, credits_quantity='2000',
                                                           state='published', effective_at=now - timedelta(days=365)))
    subscription = None
    for index, (back, sequence, day, review) in enumerate(PERSONAL_PERIODS):
        start, end = _month_start(now, back), _month_start(now, back - 1)
        number = 'SG-%s-%s' % (start.strftime('%Y-%m'), sequence)
        order = _personal_order(personal, user, plan, PERSONAL_PLAN, 'seed-personal-pro-%d' % index, number,
                                start.replace(day=day, hour=9), review)
        if subscription is None:
            subscription = Subscription.objects.create(
                account=personal, initial_order=order, original_anchor=start, billing_timezone='Asia/Shanghai',
                calendar_rule_version='v1', billing_cycle='monthly', mandate={}, auto_renew=True)
        SubscriptionPeriod.objects.create(subscription=subscription, order=order, cycle_index=index, starts_at=start,
                                          ends_at=end, snapshot={**PERSONAL_PLAN, 'credits_quantity': '0'})
    back, sequence, day = PERSONAL_TOPUP_ORDER
    at = _month_start(now, back).replace(day=day, hour=15, minute=8)
    _personal_order(personal, user, topup, PERSONAL_TOPUP, 'seed-personal-credits-2000',
                    'SG-%s-%s' % (at.strftime('%Y-%m'), sequence), at)
    for order in Order.objects.filter(account=personal):
        if order.display_number and order.display_number.rsplit('-', 1)[-1] in PERSONAL_INVOICED:
            InvoiceRequest.objects.create(account=personal, order=order, created_by=user, recipient_email=user.email,
                                          title='个人', status='sent', request_key='seed-invoice-%s' % order.pk,
                                          request_hash='seed', invoice_reference='INV-%s' % order.display_number)
    credits, _ = ProductVersion.objects.get_or_create(
        code='seed-credits-5000', version=1, defaults=dict(kind='credits', audience='personal', billing_cycle='',
                                                           unit_amount_minor=49900, credits_quantity='5000',
                                                           state='published', effective_at=now - timedelta(days=365)))
    at = _month_start(now, 0).replace(day=3, hour=14, minute=22)
    order = _personal_order(personal, user, credits, PERSONAL_CREDITS_ORDER, 'seed-personal-credits-5000',
                            'SG-%s-0042' % at.strftime('%Y-%m'), at)
    PaymentFact.objects.filter(order=order).update(transaction_reference=PERSONAL_CREDITS_REFERENCE,
                                                   created_at=at + timedelta(seconds=31))
    return order


def seed(ctx):
    if 'users' not in ctx:
        core.seed(ctx)
    now = timezone.now()
    users, business, workspace = ctx['users'], ctx['business'], ctx['workspace']
    members = ctx['memberships']
    # 760:476：安全与策略管理员是 Sun Tao（不占名额），Chen Yu 只是运营部负责人
    if members['chen'].is_security_admin:
        members['chen'].is_security_admin = False
        members['chen'].save(update_fields=['is_security_admin'])
    if not members['sun'].is_security_admin:
        members['sun'].is_security_admin = True
        members['sun'].save(update_fields=['is_security_admin'])

    AccountPolicy.objects.get_or_create(account=business, defaults={'expert_share_max_scope': 'account',
                                                                    'approval_required_actions': APPROVAL_REQUIRED})
    for name, monthly, share, assigned in DEPARTMENTS:
        department, _ = Department.objects.get_or_create(
            account=business, name=name, defaults={'monthly_quota': monthly, 'per_member_share': share,
                                                   'pooling_enabled': name in POOLED_DEPARTMENTS})
        for key, is_lead in assigned:
            if not DepartmentMember.objects.filter(membership=members[key], ended_period__isnull=True).exists():
                DepartmentMember.objects.create(department=department, membership=members[key], is_lead=is_lead)
    for key, quantity in BUSINESS_SPEND:
        _shared_spend(business, members[key], quantity, now)
    _orders_seats(business, members, users, now)

    # 760:476「邀请中 · 23 小时后失效」一行：走真实邀请服务，预留一个空座位
    from backend.commerce.subscriptions import create_business_invitation
    invite = create_business_invitation(business, users['lillian'], method='email', identifier='lishan@sage.demo',
                                        role='member', paid=True, request_key='seed-invite-lishan')
    # 758:428 / 760:428 邀请行显示受邀人姓名；成员行记录用户 id，供“（你）”按用户匹配（D41）
    from backend.commerce.models import BusinessInvitation
    from ..services import org_people
    # The service stamps created_at / expires_at from the real clock; pin both to the seed clock (sent 1 hour ago,
    # 23 hours left) so the row reads 「23 小时后失效」. A queryset update keeps the expiry > created_at constraint.
    BusinessInvitation.objects.filter(pk=invite['id']).update(created_at=now - timedelta(hours=1),
                                                              expires_at=now + timedelta(hours=23))
    org_people.record_invitation(business, BusinessInvitation.objects.get(pk=invite['id']), 'Li Shan')
    for membership in members.values():
        if membership.account_id == business.pk:
            org_people.record_member(membership)

    lillian = members['lillian']
    research = _task(business, lillian, '德国站竞品格局调研', workspace=workspace)
    if not AuthorizationRequest.objects.filter(account=business, task=research,
                                               purpose_text=AUTHORIZATION['purpose_text']).exists():
        AuthorizationRequest.objects.create(account=business, task=research, requested_by_expert=_expert(),
                                            extension=_extension('Jungle Scout'), **AUTHORIZATION)
    for title, status in OFFBOARDING_TASKS:
        _task(business, members['mark'], title, status=status)

    month = _month_start(now, 0)
    for index, (action, summary, target_type, actor, (day, hour, minute)) in enumerate(AUDIT):
        request_id = _key('audit', index)
        if not AuditEvent.objects.filter(account=business, request_id=request_id).exists():
            event = AuditEvent.objects.create(account=business, actor=members[actor].user, action=action,
                                              request_id=request_id, target_type=target_type, summary=summary)
            at = month.replace(day=day, hour=hour, minute=minute)
            AuditEvent.objects.filter(pk=event.pk).update(created_at=at)

    personal = BillingAccount.objects.get(kind='personal', personal_owner=users['lillian'])
    owner = Membership.objects.get(account=personal, user=users['lillian'])
    credits_order = _personal_orders(personal, users['lillian'], now)
    existing = CreditBatch.objects.filter(account=personal, source_key='seed-billing-personal').first()
    if existing is None:
        # The personal 5,000 Credits are the ones bought by SG-…-0042: granted at payment, valid 12 months.
        paid = credits_order.paid_at if credits_order else now
        ledger.grant_credits(personal, 'purchased', 'seed-billing-personal', PERSONAL_CREDITS,
                             paid + timedelta(days=365), order=credits_order)
        CreditBatch.objects.filter(account=personal, source_key='seed-billing-personal').update(created_at=paid)
    for title, quantity, days, hours in PERSONAL_TASKS:
        task = _task(personal, owner, title)
        if title in PERSONAL_AUTOMATION_TASKS and task.source_automation_run_id is None:
            task.source_automation_run = _personal_automation_run(personal, owner, title, now - timedelta(days=days, hours=hours))
            task.save(update_fields=['source_automation_run'])
        if Reservation.objects.filter(account=personal, task_key=task.public_id).exists():
            continue
        reservation = ledger.reserve_credits(personal, task.public_id, quantity)
        ledger.settle_credits(personal, reservation['id'], quantity)
        CreditEntry.objects.filter(reservation_id=reservation['id']).update(created_at=now - timedelta(days=days, hours=hours))
    # 61 屏“本期额度已使用 25% · 个人账本 · 11月3日”: commerce raises the notice when settlement crosses 25%, which
    # in the seed happens at seed time; the row is dated to the 3rd like the usage it summarises (update skips the
    # model's immutability check, seed only).
    BillingNotification.objects.filter(account=personal, type='credits_usage_25').update(
        occurred_at=_month_start(now, 0) + timedelta(days=2, hours=16))
    ctx.update(billing_members=members)
