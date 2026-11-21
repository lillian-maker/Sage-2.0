"""Task history behind the expert screens: 望野's history and collaboration network (28-29 屏), team usage
(30 屏), "常见后续技能" (45 屏), document citations (40-41 屏), WEEE fact citations (42 屏) and the
德语内容官 draft (33-38 屏).

Runs after the tasks/artifacts/inbox seeds and only tops counts up to the Figma numbers, so rows those
seeds already created (for example citations of 德国 EPR 法规摘要.pdf) are counted, not duplicated.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from itertools import combinations

from ._clock import timezone

from backend.models import Membership
from backend.workbench.common import AppContext
from backend.workbench.models import (Artifact, ArtifactVersion, Citation, Expert, ExpertCapability, ExpertTeam,
                                      Extension, KnowledgeBase, KnowledgeDocument, KnowledgeEntity, Task,
                                      TaskDelivery, TaskParticipant, TaskStep, TaskThread, Workspace, WorkspaceMember)
from backend.workbench.services import experts as expert_service
from backend.workbench.services import workspaces as ws

# After kickoff.py (97): the kickoff task must exist before the history top-up measures, otherwise the
# 24-task target on screen 28 is overshot by one. Tie with workspace_tasks.py resolves by module name.
ORDER = 98
CENTER = 'AGT-007'
CENTER_ORDER = 2
# code, step order, stage, skill, collaboration target (29 屏)
PEERS = (('AGT-022', 1, 'research', None, 14), ('AGT-044', 3, 'analyze', None, 18),
         ('AGT-026', 4, 'analyze', '定价策略', 12), ('AGT-040', 5, 'review', None, 9),
         ('AGT-029', 6, 'deliver', None, 3))
SUFFIX_PEERS = ('AGT-040',)
RATE_TARGETS = {'AGT-044': 94, 'AGT-026': 92}
RETURN_TARGETS = {'AGT-040': 2}
HISTORY_TARGET = 24
NEXT_TARGETS = (('定价策略', 0.68, 7), ('销量估算', 0.41, 8))
# 45 屏「常见后续技能」：68% 与 41% 要同时成立，用到竞品格局分析的可见任务总数须为 22
# （真实任务 17 个，另给 5 个已归档售后任务补一步；补在最后，不产生后续技能关系，专家用售后任务的主责）。
NEXT_BASE_TOTAL = 22
ANALYSIS = '竞品格局分析'
FILLER_TITLES = (
    '法国站竞品格局调研', '英国站竞品格局调研', '西班牙站竞品格局调研', '意大利站竞品格局调研', '荷兰站竞品格局调研',
    '波兰站进入评估', '瑞典站进入评估', '空气炸锅品类机会扫描', '手持吸尘器品类机会扫描', '电热水壶价格带分析',
    '咖啡机市场容量测算', '破壁机竞品评论洞察', '电动牙刷新品机会评估', '加湿器季节性需求分析',
    '电饭煲欧洲渠道调研', '榨汁杯达人带货机会', 'X2 系列上市竞品对标', 'X1 Pro 定位研究', 'Otto.de 渠道可行性',
    '独立站德语市场测试', '德国站年度竞品复盘', '欧洲站价格战监测', '小家电类目趋势周报', '德国站新品机会清单',
    '北欧站竞品价格带', '法国站合规门槛梳理', '英国站 UKCA 影响评估', '欧盟能效新规影响评估', '德国站季度机会复盘',
    '欧洲小家电市场年报')
TEAM_TARGETS = (('新市场进入小组', None, 6), ('售后快速响应', 'business', 14))
AFTER_SALES_TITLE = '差评回复与补偿方案 · 第 %d 批'
AFTER_SALES_LEAD = 'AGT-037'
RECENT = (
    # title, workspace name, lead code, offset (or fixed Beijing-time date), role of 望野
    ('德国站竞品格局调研', '德国市场进入', CENTER, timedelta(minutes=10), 'lead'),
    ('Q4 欧洲达人合作清单', '黑五大促', 'AGT-029', timedelta(days=1), 'collaborator'),
    ('北欧市场进入评估', None, CENTER, datetime(2026, 10, 26, 16, 0, tzinfo=ZoneInfo('Asia/Shanghai')), 'lead'),
)
LISTING_REVIEW_TITLE = 'X1 德语 Listing 上架前审核'
DOCUMENT_CITATIONS = (('德国 EPR 法规摘要.pdf', 18), ('竞品清单.xlsx', 9), ('Amazon.de 小家电类目页', 6),
                      ('2025 Q3 销售报表.csv', 2), ('德国站关键词清单.xlsx', 3), ('德国消费者调研 2026.pdf', 2),
                      ('竞品评论摘录.docx', 1))
DOCUMENT_KB = '德国市场进入资料库'
FACT_ENTITY, FACT_TASKS = 'WEEE 注册', 2
DRAFT = {
    'name': '德语内容官', 'title': '德国市场内容本地化与合规审校',
    'responsibilities': '把中文产品与营销内容改写为地道的德语版本，检查宣称是否符合德国与欧盟的广告和产品规定，并标出需要人工确认的表述。',
    'capability_tags': ['德语本地化', '宣称合规', '人工确认点'],
    'start_description': '把中文产品与营销内容改写成地道的德语，并检查宣称是否合规',
    'forbidden_rules': ['不使用“最好”“第一”等绝对化宣称', '不翻译品牌名与产品型号'],
    'skills': ('德语本地化校对', '宣称合规检查', 'Amazon 关键词研究'),
    'knowledge_bases': ('品牌手册 2026', '德国市场法规库'),
}


def _context(business, user):
    member = Membership.objects.get(account=business, user=user)
    return AppContext(member=member, account=business, user=user), member


def _expert(code):
    return Expert.objects.get(catalog_code=code)


def _extension(business, name):
    return Extension.objects.filter(name=name, account__in=[business]).first() or Extension.objects.get(
        name=name, account__isnull=True)


def _new_task(business, member, title, lead, at, workspace=None, status='accepted', archived=False, team=None):
    task = Task.objects.create(account=business, workspace=workspace, title=title, creator=member, owner=member,
                               lead_expert=lead, model_mode='auto', status=status, team=team, last_activity_at=at,
                               accepted_at=at if status == 'accepted' else None, archived_at=at if archived else None)
    TaskThread.objects.create(account=business, task=task, kind='main')
    TaskParticipant.objects.create(account=business, task=task, kind='human', user=member.user, role='lead',
                                   state='active')
    if lead:
        TaskParticipant.objects.create(account=business, task=task, kind='expert', expert=lead, role='lead',
                                       state='active')
    Task.objects.filter(pk=task.pk).update(created_at=at - timedelta(hours=3))
    return task


def _delivery(task, decisions):
    artifact = Artifact.objects.create(account=task.account, title=task.title[:110] + '报告', kind='document',
                                       created_by_expert=task.lead_expert)
    for number, decision in enumerate(decisions, start=1):
        version = ArtifactVersion.objects.create(account=task.account, artifact=artifact, number=number,
                                                 state='accepted' if decision == 'accepted' else 'draft',
                                                 author_expert=task.lead_expert, content_ref='seed:none')
        TaskDelivery.objects.create(account=task.account, task=task, artifact_version=version, summary=task.title,
                                    decision=decision, decided_by=task.owner.user, decided_at=task.last_activity_at)
    artifact.current_version = version
    artifact.save(update_fields=['current_version'])


def seed_recent(ctx, business, member, center):
    for title, workspace_name, lead_code, offset, role in RECENT:
        task = Task.objects.filter(account=business, title=title).order_by('pk').first()
        if task is None:
            workspace = None
            if workspace_name:
                workspace, created = Workspace.objects.get_or_create(
                    account=business, name=workspace_name, deleted_at__isnull=True,
                    defaults={'created_from': 'blank', 'created_by': member.user})
                if created:
                    WorkspaceMember.objects.create(account=business, workspace=workspace, user=member.user,
                                                   role='owner', state='active')
            at = offset if isinstance(offset, datetime) else timezone.now() - offset
            task = _new_task(business, member, title, _expert(lead_code), at, workspace,
                             status='accepted' if timezone.now() - at > timedelta(days=2) else 'running')
            if lead_code == CENTER:
                TaskStep.objects.create(account=business, task=task, order=CENTER_ORDER, stage='analyze',
                                        title='竞品格局分析', expert=center,
                                        skill=_extension(business, ANALYSIS))
        if role == 'collaborator':
            TaskParticipant.objects.get_or_create(account=business, task=task, expert=center, defaults={
                'kind': 'expert', 'role': 'collaborator', 'state': 'active'})


def _measure(ctx, center):
    """Shared tasks and delivery decisions per peer, computed like services.experts.network."""
    since = timezone.now() - timedelta(days=expert_service.NETWORK_WINDOW_DAYS)
    tasks = set(expert_service.expert_tasks(ctx, center).filter(last_activity_at__gte=since)
                .values_list('pk', flat=True))
    shared = {}
    for task_id, expert_id in TaskStep.objects.filter(task_id__in=tasks).exclude(expert=center).values_list(
            'task_id', 'expert_id'):
        shared.setdefault(expert_id, set()).add(task_id)
    decisions = {}
    for task_id, decision in TaskDelivery.objects.filter(task_id__in=tasks).exclude(decision='pending').values_list(
            'task_id', 'decision'):
        decisions.setdefault(task_id, []).append(decision)
    return shared, decisions


def _plan_deliveries(count, ranges, base):
    """Pick fillers whose delivery was returned (R) or returned then accepted (Y) to hit the 29 屏 numbers."""
    def score(returned, both):
        total = 0
        for code, indexes in ranges.items():
            acc, ret = base[code]
            for i in indexes:
                if i in returned:
                    ret += 1
                elif i in both:
                    acc, ret = acc + 1, ret + 1
                else:
                    acc += 1
            if code in RATE_TARGETS and acc + ret:
                total += abs(round(acc * 100 / (acc + ret)) - RATE_TARGETS[code])
            if code in RETURN_TARGETS:
                total += 10 * abs(ret - RETURN_TARGETS[code])
        return total

    best = (score(set(), set()), set(), set())
    for rsize in (0, 1, 2):
        for ret in combinations(range(count), rsize):
            returned = set(ret)
            for bsize in (0, 1, 2, 3):
                for both in combinations([i for i in range(count) if i not in returned], bsize):
                    value = score(returned, set(both))
                    if value < best[0]:
                        best = (value, returned, set(both))
    return best[1], best[2]


def seed_history(ctx, business, member, center):
    if Task.objects.filter(account=business, title=FILLER_TITLES[0], owner=member).exists():
        return []
    shared, decisions = _measure(ctx, center)
    peers = {code: _expert(code) for code, *_ in PEERS}
    existing = {code: len(shared.get(peers[code].pk, ())) for code, *_ in PEERS}
    history = expert_service.expert_tasks(ctx, center).count()
    need = {code: max(0, target - existing[code]) for code, _, _, _, target in PEERS}
    # One spare filler past the largest need, so a suffix peer (清账) can hold a returned
    # delivery in a task the highest-rate peer (安界, in the leading range) never touched.
    count = min(len(FILLER_TITLES), max([HISTORY_TARGET - history] + [n + 1 for n in need.values() if n] + [0]))
    ranges = {code: (range(count - need[code], count) if code in SUFFIX_PEERS else range(need[code]))
              for code in need}
    base = {}
    for code in need:
        rows = [d for task_id in shared.get(peers[code].pk, ()) for d in decisions.get(task_id, [])]
        base[code] = (rows.count('accepted'), rows.count('returned'))
    returned, both = _plan_deliveries(count, ranges, base)
    analysis = _extension(business, ANALYSIS)
    fillers = []
    for index in range(count):
        at = timezone.now() - timedelta(days=12 + index * 70 // max(count, 1))
        task = _new_task(business, member, FILLER_TITLES[index], center, at,
                         status='needs_revision' if index in returned else 'accepted', archived=True)
        TaskStep.objects.create(account=business, task=task, order=CENTER_ORDER, stage='analyze',
                                title='竞品格局分析', expert=center, skill=analysis, status='done')
        for code, order, stage, skill, _ in PEERS:
            if index in ranges[code]:
                TaskStep.objects.create(account=business, task=task, order=order, stage=stage,
                                        title='%s 协作' % peers[code].name, expert=peers[code], status='done',
                                        skill=_extension(business, skill) if skill else None)
        _delivery(task, ['returned'] if index in returned else ['returned', 'accepted'] if index in both
                  else ['accepted'])
        fillers.append(task)
    return fillers


def seed_next_skills(ctx, business, center, fillers):
    analysis = _extension(business, ANALYSIS)
    used_tasks = set(TaskStep.objects.filter(task__in=ws.visible_tasks(ctx), skill=analysis)
                     .values_list('task_id', flat=True))
    shortfall = NEXT_BASE_TOTAL - len(used_tasks)
    if shortfall > 0:
        pool = Task.objects.filter(account=business, archived_at__isnull=False,
                                   title__startswith=AFTER_SALES_TITLE.split('%')[0]).exclude(
                                   pk__in=used_tasks).order_by('-pk')[:shortfall]
        for task in pool:
            TaskStep.objects.get_or_create(account=business, task=task, order=99,
                                           defaults={'stage': 'analyze', 'title': ANALYSIS,
                                                     # 用任务自己的主责，避免把这些任务算进望野的「历史任务 24」
                                                     'expert': task.lead_expert,
                                                     'skill': analysis, 'status': 'done'})
    used = {}
    for task_id, order, skill_id in TaskStep.objects.filter(task__in=ws.visible_tasks(ctx), skill__isnull=False) \
            .values_list('task_id', 'order', 'skill_id'):
        used.setdefault(task_id, []).append((order, skill_id))
    with_analysis = {t: steps for t, steps in used.items() if any(s == analysis.pk for _, s in steps)}
    total = len(with_analysis)
    for name, ratio, order in NEXT_TARGETS:
        skill = _extension(business, name)
        have = {t for t, steps in with_analysis.items()
                if any(s == skill.pk and o > min(o2 for o2, s2 in steps if s2 == analysis.pk) for o, s in steps)}
        target = min(range(total + 1), key=lambda x: abs(x / total - ratio)) if total else 0
        for task in [t for t in fillers if t.pk not in have][:max(0, target - len(have))]:
            TaskStep.objects.create(account=business, task=task, order=order, stage='analyze', title=name,
                                    expert=center, skill=skill, status='done')


def seed_teams(business, member, fillers):
    for name, scope, target in TEAM_TARGETS:
        team = ExpertTeam.objects.filter(account=business if scope else None, name=name).first()
        if team is None:
            continue
        have = Task.objects.filter(account=business, team=team).count()
        missing = max(0, target - have)
        if scope is None:
            pool = [t for t in fillers if t.team_id is None][:missing]
            Task.objects.filter(pk__in=[t.pk for t in pool]).update(team=team)
            continue
        lead = _expert(AFTER_SALES_LEAD)
        start = Task.objects.filter(account=business, title__startswith=AFTER_SALES_TITLE.split('%')[0]).count()
        for index in range(missing):
            at = timezone.now() - timedelta(days=3 + (start + index) * 5)
            _new_task(business, member, AFTER_SALES_TITLE % (start + index + 1), lead, at, archived=True, team=team)


def seed_citations(ctx, business, center):
    kb = KnowledgeBase.objects.filter(account=business, name=DOCUMENT_KB).first()
    if kb is None:
        return
    visible = ws.visible_tasks(ctx)
    fillers = list(Task.objects.filter(account=business, title__in=FILLER_TITLES, owner=ctx.member).order_by('pk'))
    listing = visible.filter(title=LISTING_REVIEW_TITLE).first()
    for title, target in DOCUMENT_CITATIONS:
        doc = KnowledgeDocument.objects.filter(knowledge_base=kb, title=title, deleted_at__isnull=True).first()
        if doc is None:
            continue
        have = Citation.objects.filter(document=doc, task__in=visible).count()
        sources = ([listing] if listing and title.startswith('德国 EPR') else []) + fillers
        for index in range(max(0, target - have)):
            task = sources[index % len(sources)] if sources else None
            if task is None:
                break
            row = Citation.objects.create(account=business, document=doc, task=task, locator='§%d' % (index % 6 + 1),
                                          cited_version=1)
            at = timezone.now() - timedelta(days=1) if task == listing else task.last_activity_at
            Citation.objects.filter(pk=row.pk).update(created_at=at)
    entity = KnowledgeEntity.objects.filter(account=business, name=FACT_ENTITY).first()
    fact = entity.facts.filter(validity='current', conflict_key__isnull=False).first() if entity else None
    if fact is None:
        return
    cited = set(Citation.objects.filter(fact__entity=entity, task__in=visible).values_list('task_id', flat=True))
    main = visible.filter(title=RECENT[0][0]).first()
    for task in [t for t in (main, listing, *fillers) if t is not None and t.pk not in cited][:max(
            0, FACT_TASKS - len(cited))]:
        Citation.objects.create(account=business, fact=fact, task=task, locator='事实')


def seed_draft(business, user):
    if Expert.objects.filter(account=business, name=DRAFT['name']).exclude(status='archived').exists():
        return
    draft = Expert.objects.create(
        account=business, source='custom', group='role', name=DRAFT['name'], title=DRAFT['title'],
        responsibilities=DRAFT['responsibilities'], capability_tags=DRAFT['capability_tags'], version_label='',
        status='draft', share_scope='only_me', created_by=user, start_mode='describe',
        start_description=DRAFT['start_description'], communication_style='conclusion_first',
        uncertainty_policy='ask_first', forbidden_rules=DRAFT['forbidden_rules'], wizard_step=6)
    for name in DRAFT['skills']:
        skill = _extension(business, name)
        ExpertCapability.objects.create(account=business, expert=draft, extension=skill)
        if skill.required_connector:
            ExpertCapability.objects.get_or_create(account=business, expert=draft, extension=skill.required_connector,
                                                   defaults={'required_for': skill})
    draft.knowledge_bases.set(KnowledgeBase.objects.filter(account=business, name__in=DRAFT['knowledge_bases']))


def seed(ctx):
    business, user = ctx['business'], ctx['users']['lillian']
    app_ctx, member = _context(business, user)
    center = _expert(CENTER)
    seed_recent(app_ctx, business, member, center)
    fillers = seed_history(app_ctx, business, member, center)
    seed_teams(business, member, fillers or list(
        Task.objects.filter(account=business, title__in=FILLER_TITLES, owner=member).order_by('pk')))
    # seed_next_skills 需要售后任务已经存在（NEXT_BASE_TOTAL 的分母借它们的归档任务），故在 seed_teams 之后。
    seed_next_skills(app_ctx, business, center, fillers or list(
        Task.objects.filter(account=business, title__in=FILLER_TITLES, owner=member).order_by('pk')))
    seed_citations(app_ctx, business, center)
    seed_draft(business, user)
