"""Task sample data from Figma screens 715 (active tasks), 726 (task list), 631 (workspace tasks) and the
"德国站竞品格局调研" task detail (messages, kickoff card, steps, delivery, side thread, edit branches)."""
from datetime import timedelta

from ._clock import timezone

from backend.workbench.models import (AcceptanceCriterion, Artifact, ArtifactVersion, AuthorizationRequest, Citation,
                                      Expert, Extension, KickoffQuestion, Task, TaskDelivery, TaskMessage, TaskParticipant,
                                      TaskStep, TaskSuggestion, TaskThread, TaskUserState)
from backend.workbench.services import artifacts as store

from . import artifacts as artifact_seed
from . import core

ORDER = 20

# name, catalog code (天时 has no catalog code), title
EXPERTS = (
    ('望野', 'AGT-007', '市场竞争与机会研究'), ('安界', 'AGT-044', '合规专家'), ('清账', 'AGT-040', '财务核算专家'),
    ('觅位', 'AGT-022', '定价策略专家'), ('立言', 'AGT-029', '品牌文案专家'), ('通途', 'AGT-019', '物流方案专家'),
    ('译境', 'AGT-028', '本地化翻译专家'), ('解忧', 'AGT-037', '售后客服专家'), ('质守', 'AGT-018', '质量管理专家'),
    ('天时', None, '节奏规划专家'),
)

MAIN_TITLE = '德国站竞品格局调研'
REPORT_TITLE = '德国站竞品格局报告'
DELIVERY_SUMMARY = ('德国站竞品格局报告已完成：建议进入，但需先完成 WEEE 注册（4–6 周）。报告共 7 个部分，'
                    '三个最容易卡上线的门槛已单独列出，数字已由清账复核。')
DELIVERY_BODY = '已交付。结论：建议进入，但需先完成 WEEE 注册（4–6 周）。下面是验收清单的对照，每条都附了证据。'
KEY_POINTS = (
    'Amazon.de 小家电类目前 20 名中，德国本土品牌占 6 席，价格带集中在 €39–69。',
    '头部竞品差评集中在插头规格和德语说明书，这是可切入的差异点。',
    '上架前必须取得 WEEE 注册号并完成 EPR 登记，安界已整理清单。',
)
# Screens 682/712/709/2736: one lead message with the numbered findings; its document sources are added by the
# artifacts seed once the knowledge documents exist.
PROGRESS_BODY = '我把任务拆成 5 步，前 3 步已完成。初步结论：\n%s\n要判断能不能进入，还需要估算竞品的月销量。' % '\n'.join(
    '%d. %s' % (i, text) for i, text in enumerate(KEY_POINTS, 1))
# Screen 21 (编排视图): the follow-up plan after v2 was accepted. key, stage, node label, title, expert, status,
# credits, activity, upstream keys. The sales estimate is ordered before the running keyword step so it stays the
# task's current step ("正在估算竞品月销量" on screen 715) while it waits for the Jungle Scout authorization.
MAIN_GRAPH = (
    ('brief', 'understand', '任务理解', '建立任务账本', '望野', 'done', 8, None, ()),
    ('landscape', 'research', '竞品格局分析', '竞品格局分析', '望野', 'done', 62, None, ('brief',)),
    ('sales', 'analyze', '销量估算', '估算竞品月销量', '望野', 'awaiting_user', 40, '正在估算竞品月销量',
     ('landscape', 'keywords')),
    ('keywords', 'research', '关键词研究', 'Amazon.de 关键词与类目', '觅位', 'running', 34, '正在检索 Amazon.de 类目页',
     ('brief',)),
    ('compliance', 'research', '合规清单', 'EPR / WEEE 合规清单', '安界', 'done', 41, None, ('brief',)),
    ('pricing', 'analyze', '定价策略', '价格带与定价建议', '觅位', 'queued', 30, None, ('landscape', 'sales')),
    ('numbers', 'review', '对账复核', '数字复核', '清账', 'queued', 12, None, ('sales', 'pricing')),
    ('claims', 'review', '合规复核', '宣称与合规复核', '安界', 'queued', 15, None, ('compliance',)),
    ('summary', 'deliver', '报告汇总', '汇总进入建议', '望野', 'queued', 25, None, ('numbers', 'claims')),
)
# Screen 22 (存为 Playbook): the suggested template — six of the nine steps, three of them parameterized.
MAIN_PLAYBOOK_VARIABLES = [{'name': '市场', 'default': '德国'}, {'name': '平台', 'default': 'Amazon.de'},
                           {'name': '品类', 'default': '家用小家电'}]
MAIN_PLAYBOOK_STEPS = (('landscape', '市场'), ('keywords', '平台'), ('compliance', '市场'), ('pricing', None),
                       ('numbers', None), ('summary', None))
# Inputs of the sales-estimate node, resolved from the knowledge graph.
SALES_INPUTS = ('Amazon.de 小家电类目', '竞品前 50 名', '我们的 X1 系列')
SALES_CRITERIA = (('覆盖前 50 名中至少 45 个商品', 'pending'), ('每个估算注明数据日期与来源', 'pending'),
                  ('由清账复核汇总数字', 'pending'))
# Screen 06 (并行工作卡): the card 望野 posted when the research stage fanned out, as it read at 17:05 of work.
PARALLEL_BODY = '计划已开始。研究阶段由 3 位专家并行进行，我会在全部完成后汇总。'
PARALLEL_ROWS = (
    ('望野', 'landscape', '竞品格局分析', '正在读取 竞品清单.xlsx 第 3 个工作表', 372, False),
    ('觅位', 'keywords', '关键词与类目', '正在检索 Amazon.de 类目页', 280, False),
    ('安界', 'compliance', 'EPR / WEEE 合规清单', '正在核对 LUCID 登记要求', 483, True),
    ('清账', 'numbers', '数字复核', None, None, False),
)
PARALLEL_METRICS = {'tool_calls': 6, 'documents_read': 24, 'artifacts_created': 2, 'elapsed_seconds': 1025}
# Delivery-time wording of the acceptance list (08 交付验收); the kickoff card of 德国站进入评估 keeps the short wording.
MAIN_CRITERIA = (
    ('覆盖前 20 名竞品，含价格带与差评集中点', '报告 §2 · 数据表'),
    ('每个数字注明来源与数据日期', '报告附录 A'),
    ('列出上架前必须完成的合规事项与周期', '报告 §3'),
    ('给出“进入 / 暂缓”结论及 3 条依据', '报告 §1'),
    ('关键数字由清账复核', '复核记录'),
)
MAIN_QUESTIONS = (
    ('先看哪个类目？', ['小家电', '厨房用品', '个护电器'], '小家电'),
    ('竞品范围取前多少名？', ['前 10 名', '前 20 名', '前 50 名'], '前 20 名'),
)
SUGGESTIONS = (('把进入清单整理成 Excel 打勾表', False), ('查看前 50 名竞品的月销量', True),
               ('起草 WEEE 注册材料清单', False))

# Screen 715 lists six active tasks: the main task (waiting for Lillian), the three below, the monthly scan
# automation run (seeds/inbox.py) and the WEEE inbox hand-off (seeds/inbox.py).
# title, lead, workspace (None = core workspace), status, collaborators, steps done/total, current activity
# Minutes since last activity per ACTIVE row, as on screen 715 (8 / 41 / 12 分钟).
ACTIVE_MINUTES = (8, 41, 12)
# Screen 23: the user's request that started the X1 content review.
OPENING = {'X1 德语 Listing 上架前审核': '上架前帮我审核这份德语 Listing 和主图，看看有没有违反 Amazon.de 政策和德国法规的地方。'}
# Screen 23 (内容审核结果卡) names the review's plan steps; other active tasks keep numbered placeholder steps.
STEP_TITLES = {'X1 德语 Listing 上架前审核': ('拆分审核对象与维度', '平台政策审核', '法规与品牌规范审核', '汇总结果待你验收',
                                          '应用改写并生成 v3')}
# Screen 23 also names who ran each review step and how long the finished ones took (安界 · 1 分钟 …).
STEP_EXPERTS = {'X1 德语 Listing 上架前审核': ('安界', '觅位', '立言', '安界', '望野')}
STEP_MINUTES = {'X1 德语 Listing 上架前审核': (1, 3, 4)}
ACTIVE = (
    ('Amazon.de 关键词与类目机会', '觅位', None, 'running', ('望野',), 2, 5, '检索类目页'),
    ('X1 德语 Listing 上架前审核', '安界', None, 'stuck', ('望野',), 3, 5, '3 处需修改'),
    ('Q4 欧洲达人合作清单', '立言', '黑五大促', 'running', (), 3, 5, '筛选德语区家居类达人'),
)
# Screen 48 "待你验收": the localisation sample is delivered and waits for Lillian.
LISTING_TITLE = '德语 Listing 本地化'
LISTING_ARTIFACT = '德语 Listing 本地化样稿'
LISTING_SUMMARY = '德语 Listing 本地化样稿 v1 已完成，请验收。'
# Screen 726: finished and archived rows next to the active ones.
FINISHED = (
    ('品牌主张文案', '立言', 'accepted', None),
    ('德国站利润测算', '清账', 'accepted', 'archived'),
    ('黑五备货节奏', '天时', 'stopped', None),
)
# Screen 631: a workspace task owned by Chen. Lillian sees it as a workspace member, not as a participant, so it
# stays out of her six active tasks on screen 715.
SHARED_TITLE = '德国站广告投放计划'


def _expert(name, code, title):
    expert = Expert.objects.filter(catalog_code=code).first() if code else None
    if expert is None:
        expert = Expert.objects.filter(account__isnull=True, name=name).exclude(status='archived').first()
    if expert is None:
        expert = Expert.objects.create(
            source='system', group='role', catalog_code=code, name=name, title=title, specialty=title,
            responsibilities=title,
            avatar_ref='digital-employees/assets/light/512/%s.webp' % code if code else None,
            version_label='v1', status='published', share_scope='account', communication_style='conclusion_first',
            uncertainty_policy='ask_first')
    return expert


def _task(account, member, workspace, title, lead, status, at, collaborators=()):
    """Returns (task, created); lookups match seeds/inbox.py so both seeds share the same rows."""
    task = Task.objects.filter(account=account, title=title, owner=member).first()
    if task is not None:
        return task, False
    task = Task.objects.create(account=account, workspace=workspace, title=title, creator=member, owner=member,
                               lead_expert=lead, model_mode='auto', status=status, complexity='normal',
                               last_activity_at=at)
    TaskThread.objects.create(account=account, task=task, kind='main')
    TaskParticipant.objects.create(account=account, task=task, kind='human', user=member.user, role='lead',
                                   state='active')
    TaskParticipant.objects.create(account=account, task=task, kind='expert', expert=lead, role='lead',
                                   state='active')
    for expert in collaborators:
        TaskParticipant.objects.create(account=account, task=task, kind='expert', expert=expert,
                                       role='collaborator', state='active', invited_by=member.user)
    Task.objects.filter(pk=task.pk).update(created_at=at - timedelta(hours=2))
    return task, True


def _message(task, at, body, kind='text', expert=None, user=None, thread=None, **extra):
    thread = thread or task.threads.get(kind='main')
    return TaskMessage.objects.create(
        account=task.account, task=task, thread=thread, author_kind='expert' if expert else 'user' if user else 'system',
        author_expert=expert, author_user=user, kind=kind, body=body, sent_at=at, **extra)


def _steps(task, rows, done, now, activity=None, running_status='running', minutes=()):
    for order, (stage, title, expert, credits) in enumerate(rows, 1):
        if order <= done:
            status = 'done'
        elif order == done + 1 and task.status not in ('accepted', 'stopped'):
            status = running_status
        else:
            status = 'queued'
        start = now - timedelta(minutes=12 * (len(rows) - order + 1))
        TaskStep.objects.create(
            account=task.account, task=task, order=order, stage=stage, title=title, expert=expert, status=status,
            estimated_credits=credits, activity=activity if status != 'done' and status != 'queued' else None,
            started_at=start if status != 'queued' else None,
            finished_at=start + timedelta(minutes=minutes[order - 1] if order <= len(minutes) else 10)
            if status == 'done' else None)


def _main_graph(task, experts, now):
    steps = {}
    for order, (key, stage, label, title, name, status, credits, activity, _) in enumerate(MAIN_GRAPH, 1):
        started = now - timedelta(minutes=10 * (len(MAIN_GRAPH) - order + 2)) if status != 'queued' else None
        steps[key] = TaskStep.objects.create(
            account=task.account, task=task, order=order, stage=stage, label=label, title=title,
            expert=experts[name], status=status, estimated_credits=credits, activity=activity,
            started_at=started, finished_at=started + timedelta(minutes=8) if status == 'done' else None,
            inputs=[{'label': text, 'source': 'knowledge_graph'} for text in SALES_INPUTS] if key == 'sales' else None)
    for key, *_, upstream in MAIN_GRAPH:
        steps[key].depends_on.set([steps[name] for name in upstream])
    for order, (text, verdict) in enumerate(SALES_CRITERIA, 1):
        AcceptanceCriterion.objects.create(account=task.account, task=task, step=steps['sales'], order=order,
                                           text=text, origin='ai', verdict=verdict)
    return steps


def _parallel_card(task, lead, graph, at, experts):
    rows = [{'expert': _expert_ref(experts[name]), 'step_id': graph[key].public_id, 'title': title,
             'activity': activity, 'elapsed_seconds': seconds, 'in_background': background}
            for name, key, title, activity, seconds, background in PARALLEL_ROWS]
    _message(task, at, PARALLEL_BODY, kind='parallel_card', expert=lead, branch_index=2,
             run_metrics={**PARALLEL_METRICS, 'parallel': {'rows': rows}})


def _expert_ref(expert):
    return {'name': expert.name, 'avatar_ref': expert.avatar_ref}


def _main_task(ctx, account, member, workspace, experts, now):
    wangye, anjie = experts['望野'], experts['安界']
    # Conversation clock matches the boards (request 09:40, 望野's progress note 09:52, delivery 10:52) on the seed day.
    start = timezone.localtime(now).replace(hour=9, minute=40, second=0, microsecond=0)
    # v2 was accepted; the follow-up "前 50 名月销量" step now waits for the Jungle Scout authorization.
    task, created = _task(account, member, workspace, MAIN_TITLE, wangye, 'awaiting_user',
                          now - timedelta(minutes=10), (anjie,))
    if not created:
        return task
    # Boards list four participants: 望野, 安界, Lillian Li and Chen Yu. 清账 still owns its plan steps (658:2).
    TaskParticipant.objects.create(account=account, task=task, kind='human', user=ctx['users']['chen'],
                                   role='collaborator', state='active', invited_by=member.user)
    task.goal = '判断家用小家电是否值得在 12 月前进入德国站'
    task.scope_text = 'Amazon.de 小家电类目前 20 名竞品、价格带、EPR / WEEE 合规门槛'
    task.deliverables = '竞品格局报告（文档）+ 价格带数据表'
    task.constraints = '竞品数据只用近 90 天；对外沟通只出草稿'
    task.estimated_expert_count, task.estimated_duration_minutes = 3, 60
    task.save(update_fields=['goal', 'scope_text', 'deliverables', 'constraints', 'estimated_expert_count',
                             'estimated_duration_minutes', 'updated_at'])
    user = member.user
    _message(task, start, '帮我评估家用小家电进入德国市场的机会，重点看 Amazon.de 的竞争格局和 EPR 合规要求，给出是否进入的建议。', user=user)
    _message(task, start + timedelta(minutes=1), '开工前确认两件事，以及这次的验收清单。', kind='kickoff_card',
             expert=wangye, card_type='kickoff', card_id=task.public_id)
    for order, (question, options, answer) in enumerate(MAIN_QUESTIONS, 1):
        KickoffQuestion.objects.create(account=account, task=task, order=order, question=question, options=options,
                                       answer=answer, answered_by=user, answered_at=start + timedelta(minutes=3))
    for order, (text, evidence) in enumerate(MAIN_CRITERIA, 1):
        AcceptanceCriterion.objects.create(account=account, task=task, order=order, text=text, origin='ai',
                                           verdict='met', evidence=evidence)
    # The user edited the scope once: branch 2 replaced "前 10 名" with "前 20 名" (screen "编辑消息 · 2/2").
    root = _message(task, start + timedelta(minutes=4), '竞品先看前 10 名就够了。', user=user)
    _message(task, start + timedelta(minutes=5), '好的，按前 10 名整理。', expert=wangye)
    _message(task, start + timedelta(minutes=6), '竞品看前 20 名，本土品牌单独标出来。', user=user,
             parent_message=root, branch_index=2)
    _message(task, start + timedelta(minutes=7), '收到，按前 20 名整理，本土品牌单独标出。计划分 5 步。', kind='plan',
             expert=wangye, branch_index=2, card_type='plan', card_id=task.public_id)
    graph = _main_graph(task, experts, now)
    task.playbook_template = {'name': '新市场进入评估', 'variables': MAIN_PLAYBOOK_VARIABLES,
                              'steps': [{'step': graph[key].public_id, 'variable': variable}
                                        for key, variable in MAIN_PLAYBOOK_STEPS]}
    task.save(update_fields=['playbook_template', 'updated_at'])
    _parallel_card(task, wangye, graph, start + timedelta(minutes=8), experts)
    progress = _message(task, start + timedelta(minutes=12), PROGRESS_BODY, expert=wangye, branch_index=2,
                        run_metrics={'steps_done': 3, 'steps_total': 5})
    Citation.objects.create(account=account, task=task, task_message=progress,
                            external_label='Amazon.de 类目页 · 11月3日', ref_index=2)
    side = TaskThread.objects.create(account=account, task=task, kind='side', anchor_message=progress, expert=anjie)
    _message(task, start + timedelta(minutes=24), 'LUCID 登记需要多久？会影响 12 月上架吗？', user=user, thread=side)
    _message(task, start + timedelta(minutes=25), '通常 3–5 个工作日。WEEE 注册号需要 4–6 周，是关键路径，建议本周提交。',
             expert=anjie, thread=side)
    _message(task, start + timedelta(minutes=26), '好，把这个结论写进清单。', user=user, thread=side)
    back = _message(task, start + timedelta(minutes=27), 'EPR 注册清单已更新，见右侧侧聊中的讨论结论。', kind='summary', expert=anjie,
                    branch_index=2, card_type='side_thread', card_id=side.public_id)
    side.brought_back_message = back
    side.save(update_fields=['brought_back_message', 'updated_at'])
    # The report and its content belong to the artifacts seed; reuse its spec so both seeds share one row.
    spec = next(row for row in artifact_seed.ARTIFACTS if row[0] == REPORT_TITLE)
    version = artifact_seed.artifact(ctx, spec, {workspace.name: workspace}).current_version
    delivery = TaskDelivery.objects.create(account=account, task=task, artifact_version=version,
                                           summary=DELIVERY_SUMMARY, decision='accepted', decided_by=user,
                                           decided_at=now - timedelta(minutes=15))
    card = _message(task, start + timedelta(minutes=72), DELIVERY_BODY, kind='delivery_card', expert=wangye,
                    branch_index=2, card_type='delivery', card_id=delivery.public_id, is_important=True)
    version.task_message = card
    version.save(update_fields=['task_message', 'updated_at'])
    for text, requires_authorization in SUGGESTIONS:
        TaskSuggestion.objects.create(account=account, task=task, text=text,
                                      requires_authorization=requires_authorization,
                                      state='started' if requires_authorization else 'open')
    _message(task, start + timedelta(minutes=74), '查看前 50 名竞品的月销量需要用 Jungle Scout 读取数据，等你批准后继续。',
             expert=wangye, branch_index=2)
    TaskUserState.objects.create(account=account, task=task, user=user, pinned_at=now - timedelta(days=1),
                                 last_read_at=now - timedelta(minutes=30))
    return task


def _active(ctx, account, member, workspace, experts, now):
    tasks = []
    for index, (title, lead, ws_name, status, collaborators, done, total, activity) in enumerate(ACTIVE):
        at = now - timedelta(minutes=ACTIVE_MINUTES[index])
        task_workspace = artifact_seed.workspace(ctx, ws_name) if ws_name else workspace
        task, created = _task(account, member, task_workspace, title, experts[lead], status, at,
                              [experts[name] for name in collaborators])
        tasks.append(task)
        if not created:
            continue
        rows = [(('understand', 'research', 'analyze', 'review', 'deliver')[min(order, 4)],
                 STEP_TITLES[title][order] if title in STEP_TITLES else '%s · 第 %d 步' % (title, order + 1),
                 experts[STEP_EXPERTS[title][order]] if title in STEP_EXPERTS else experts[lead], 6) for order in range(total)]
        step_status = 'awaiting_user' if status == 'awaiting_user' else 'failed' if status == 'stuck' else 'running'
        _steps(task, rows, done, at, activity, step_status, STEP_MINUTES.get(title, ()))
        _message(task, at - timedelta(minutes=30), OPENING.get(title, '%s，今天内给我初稿。' % title), user=member.user)
        if status == 'stuck':
            task.stuck_rounds = 3
            task.save(update_fields=['stuck_rounds', 'updated_at'])
            _message(task, at, '连续 3 轮没能拿到一致的法规原文，需要你决定换个来源还是先跳过。', expert=experts[lead])
        else:
            _message(task, at, activity, expert=experts[lead])
    return tasks


def _listing(ctx, account, member, workspace, experts, now):
    at = now - timedelta(hours=2)
    task, created = _task(account, member, workspace, LISTING_TITLE, experts['译境'], 'delivered', at,
                          [experts['立言']])
    if not created:
        return task
    _steps(task, [('research', '整理中文卖点与关键词', experts['译境'], 4),
                  ('analyze', '改写德语标题与五点描述', experts['译境'], 6),
                  ('deliver', '交付德语 Listing 样稿', experts['译境'], 2)], 3, at)
    _message(task, at - timedelta(hours=1), '把 X1 的 Listing 改写成德语样稿，先给一版看看语气。', user=member.user)
    spec = next(row for row in artifact_seed.ARTIFACTS if row[0] == LISTING_ARTIFACT)
    version = artifact_seed.artifact(ctx, spec, {workspace.name: workspace}).current_version
    delivery = TaskDelivery.objects.create(account=account, task=task, artifact_version=version,
                                           summary=LISTING_SUMMARY)
    card = _message(task, at, LISTING_SUMMARY, kind='delivery_card', expert=experts['译境'], card_type='delivery',
                    card_id=delivery.public_id, is_important=True)
    version.task_message = card
    version.save(update_fields=['task_message', 'updated_at'])
    return task


def _finished(account, member, workspace, experts, now):
    for index, (title, lead, status, archived) in enumerate(FINISHED):
        at = now - timedelta(days=2 + index)
        task, created = _task(account, member, workspace, title, experts[lead], status, at)
        if not created:
            continue
        _steps(task, [('analyze', title, experts[lead], 8), ('deliver', '交付%s' % title, experts[lead], 4)],
               2 if status == 'accepted' else 1, at)
        _message(task, at - timedelta(hours=1), '请完成：%s' % title, user=member.user)
        if status == 'accepted':
            artifact = Artifact.objects.create(account=account, workspace=workspace, source_task=task,
                                               kind='document', title=title, created_by_expert=experts[lead])
            body = {'format': 'blocks', 'blocks': [{'type': 'paragraph', 'text': '%s（已验收）' % title}]}
            version = store.create_version(artifact, body, state='accepted', author_expert=experts[lead])
            ArtifactVersion.objects.filter(pk=version.pk).update(created_at=at)
            TaskDelivery.objects.create(account=account, task=task, artifact_version=version, summary='%s 已完成。' % title,
                                        decision='accepted', decided_by=member.user, decided_at=at)
            task.accepted_at = at
        else:
            task.stopped_at = at
        task.archived_at = at if archived else None
        task.save(update_fields=['accepted_at', 'stopped_at', 'archived_at', 'updated_at'])


def _shared(account, users, memberships, workspace, experts, now):
    task, created = _task(account, memberships['chen'], workspace, SHARED_TITLE, experts['立言'], 'running',
                          now - timedelta(hours=1))
    if created:
        _steps(task, [('research', '整理关键词', experts['立言'], 6), ('deliver', '输出投放计划', experts['立言'], 6)],
               1, now - timedelta(hours=1), '撰写广告标题')
        _message(task, now - timedelta(hours=2), '做一份德国站首月广告投放计划。', user=users['chen'])
    # 20 全部任务的「共享给我」行：状态避开 ACTIVE_STATUSES，不进入 15 进行中总览（全部 6 已与设计一致）
    for title, owner_key in (('Q3 经营复盘', 'mark'), ('黑五投放素材清单', 'chen')):
        row, _ = _task(account, memberships[owner_key], None, title, experts['立言'], 'awaiting_kickoff',
                       now - timedelta(days=3))
        if not TaskParticipant.objects.filter(task=row, kind='human', user=users['lillian']).exists():
            TaskParticipant.objects.create(account=account, task=row, kind='human', user=users['lillian'],
                                           role='collaborator', state='active', invited_by=users[owner_key])
    return task


# Screen 763 (全局搜索 “德国”): nine matching tasks (with seeds/kickoff.py), the EPR checklist hit and the review digest.
EPR_TITLE = '德国 EPR 与 WEEE 合规清单'
EPR_NOTE = '清单已按上架顺序整理。上架前必须取得 WEEE 注册号，并在德国包装法 LUCID 系统完成登记。两项都建议本周启动。'
REVIEW_TITLE = '整理本周 Amazon 差评'
REVIEW_NOTE = '本周差评已整理完。德国站差评集中在插头规格和德语说明书缺失。建议先补德语说明书。'
ARCHIVED_SEARCH = (('德国站关键词研究', '觅位', 12),)
# Screen 693 (工作区): the EPR checklist has two collaborators; the CEO's plan summary is still a draft.
EPR_COLLABORATORS = ('通途', '译境')
SUMMARY_TITLE = '进入方案汇总（经营脉络）'


def _search_examples(ctx, account, member, workspace, experts, now):
    epr = artifact_seed.task(ctx, EPR_TITLE, workspace)
    for name in EPR_COLLABORATORS:
        TaskParticipant.objects.get_or_create(account=account, task=epr, kind='expert', expert=experts[name],
                                              defaults={'role': 'collaborator', 'state': 'active',
                                                        'invited_by': member.user})
    summary, created = _task(account, member, workspace, SUMMARY_TITLE, experts['天时'], 'draft',
                             timezone.localtime(now).replace(month=10, day=29, hour=16, minute=0))
    if created:
        _message(summary, summary.last_activity_at, '把竞品、合规、物流与渠道的结论汇总成一份进入方案。', user=member.user)
    if not TaskMessage.objects.filter(task=epr, body=EPR_NOTE).exists():
        _message(epr, (epr.last_activity_at or now) - timedelta(minutes=5), EPR_NOTE, expert=experts['安界'])
    yesterday = now - timedelta(days=1)
    review, created = _task(account, member, None, REVIEW_TITLE, experts['解忧'], 'accepted', yesterday)
    if created:
        _message(review, yesterday - timedelta(hours=1), '帮我整理本周 Amazon 差评，按问题归类。', user=member.user)
        _message(review, yesterday, REVIEW_NOTE, expert=experts['解忧'])
        Task.objects.filter(pk=review.pk).update(accepted_at=yesterday)
    for title, lead, days in ARCHIVED_SEARCH:
        at = now - timedelta(days=days)
        task, created = _task(account, member, workspace, title, experts[lead], 'accepted', at)
        if created:
            _message(task, at - timedelta(hours=1), '请完成：%s' % title, user=member.user)
            Task.objects.filter(pk=task.pk).update(accepted_at=at, archived_at=at)


def seed(ctx):
    if 'users' not in ctx:
        core.seed(ctx)
    account, users, memberships, workspace = ctx['business'], ctx['users'], ctx['memberships'], ctx['workspace']
    experts = {name: _expert(name, code, title) for name, code, title in EXPERTS}
    now = timezone.now()
    member = memberships['lillian']
    main = _main_task(ctx, account, member, workspace, experts, now)
    active = _active(ctx, account, member, workspace, experts, now)
    _listing(ctx, account, member, workspace, experts, now)
    _finished(account, member, workspace, experts, now)
    shared = _shared(account, users, memberships, workspace, experts, now)
    _search_examples(ctx, account, member, workspace, experts, now)
    _rejected(account, member, workspace, experts, now)
    _returned_review(account, member, experts, main, now)
    _quota_paused(account, member, now)
    ctx.update(task_experts=experts, main_task=main, active_tasks=active, shared_workspace_task=shared)


# 05「直接拒绝 / 已拒绝已估算」：被拒任务保留概览（来源、步骤）与拒绝时的估算口径
REJECTED_STEPS = (
    ('法国站竞品格局分析', 'done'), ('Amazon.fr 关键词与类目', 'done'), ('法国 EPR 合规清单', 'done'),
    ('数字复核', 'queued'), ('汇总进入建议', 'queued'),
    ('价格带与定价建议', 'skipped'), ('后续处理待确认：使用 Keepa 读取历史价格（只读）', 'skipped'),
)


def _rejected_auth(task, expert, title, purpose, scope, estimated):
    extension = Extension.objects.filter(name='Jungle Scout').first()
    if extension is None or AuthorizationRequest.objects.filter(account=task.account, task=task,
                                                                title=title).exists():
        return
    AuthorizationRequest.objects.create(
        account=task.account, task=task, requested_by_expert=expert,
        extension=extension,
        action_kind='read_query', title=title, purpose_text=purpose, scope_text=scope,
        scope_params={'top_n': 50}, estimate_state='estimated' if estimated else 'unavailable',
        estimated_credits=20 if estimated else None, validity='this_request',
        decision='rejected', decision_scope='once', decided_by=task.owner.user,
        decided_at=task.last_activity_at)


def _rejected(account, member, workspace, experts, now):
    fr, created = _task(account, member, workspace, '法国站新品机会评估', experts['望野'], 'rejected',
                        now - timedelta(days=8))
    if created:
        fr.scope_text = '市场：法国 · 平台：Amazon.fr · 品类：家用小家电'
        for order, (title, status) in enumerate(REJECTED_STEPS, start=1):
            TaskStep.objects.create(account=account, task=fr, order=order, stage='research', title=title,
                                    status=status)
    else:
        legacy = fr.steps.filter(title='后续处理待确认').first()
        if legacy:
            legacy.title = REJECTED_STEPS[-1][0]
            legacy.save(update_fields=['title'])
    # 被拒任务保留在归档里，不计入工作区「任务 6」
    if fr.archived_at is None:
        fr.archived_at = now - timedelta(days=8)
        fr.save(update_fields=['scope_text', 'archived_at', 'updated_at'])
    _rejected_auth(fr, experts['望野'], '使用 Jungle Scout 读取法国站销量',
                   '望野想用 Jungle Scout 读取 Amazon.fr 小家电类目前 50 名竞品的销量估算。',
                   'Amazon.fr · 小家电类目 · 前 50 名', estimated=False)
    weekly, _ = _task(account, member, workspace, '全站点差评主题周报', experts['望野'],
                      'rejected', now - timedelta(days=9))
    if weekly.archived_at is None:
        weekly.archived_at = now - timedelta(days=9)
        weekly.save(update_fields=['archived_at', 'updated_at'])
    _rejected_auth(weekly, experts['望野'], '使用 Jungle Scout 汇总差评关键词',
                   '望野想用 Jungle Scout 汇总各站点近 30 天差评关键词。',
                   '全站点 · 近 30 天差评', estimated=True)


# 08「已退回等待修改」：主任务的报告已验收，退回态单独放在一条归档任务上（不计入概览与专家协作统计：无主责专家、无步骤）
RETURNED_TITLE = '竞品格局报告 · 首轮验收'
RETURNED_REASON = '请补充价格带数据来源。'
RETURNED_CRITERIA = (
    ('覆盖前 20 名竞品，含价格带与差评集中点', '报告 §2 · 数据表', 'met'),
    ('每个数字注明来源与数据日期', '报告附录 A', 'unmet'),
    ('列出上架前必须完成的合规事项与周期', '报告 §3', 'met'),
    ('给出“进入 / 暂缓”结论及 3 条依据', '报告 §1', 'met'),
    ('关键数字由清账复核', '复核记录', 'met'),
)


def _returned_review(account, member, experts, main, now):
    if Task.objects.filter(account=account, title=RETURNED_TITLE, owner=member).exists():
        return
    delivered = main.deliveries.filter(decision='accepted').select_related('artifact_version').first()
    if delivered is None:
        return
    at = now - timedelta(days=1)
    task = Task.objects.create(account=account, title=RETURNED_TITLE, creator=member, owner=member,
                               model_mode='auto', status='needs_revision', complexity='normal',
                               last_activity_at=at, archived_at=at)
    TaskThread.objects.create(account=account, task=task, kind='main')
    TaskParticipant.objects.create(account=account, task=task, kind='human', user=member.user, role='lead',
                                   state='active')
    for order, (text, evidence, verdict) in enumerate(RETURNED_CRITERIA, 1):
        AcceptanceCriterion.objects.create(account=account, task=task, order=order, text=text, origin='ai',
                                           verdict=verdict, evidence=evidence)
    delivery = TaskDelivery.objects.create(account=account, task=task, artifact_version=delivered.artifact_version,
                                           summary=DELIVERY_SUMMARY, decision='returned',
                                           return_reason=RETURNED_REASON, decided_by=member.user, decided_at=at)
    _message(task, at - timedelta(minutes=5), DELIVERY_BODY, kind='delivery_card', expert=experts['望野'],
             card_type='delivery', card_id=delivery.public_id)


# 14「本月额度已用完」：额度中断的暂停态放在一条归档任务上（主任务正等待授权，不能改成暂停；无主责专家、不计入概览）
QUOTA_TITLE = '竞品格局调研 · 额度暂停'
QUOTA_NOTICE = '本月额度已用完，任务已暂停'


def _quota_paused(account, member, now):
    if Task.objects.filter(account=account, title=QUOTA_TITLE, owner=member).exists():
        return
    at = now - timedelta(minutes=30)
    task = Task.objects.create(account=account, title=QUOTA_TITLE, creator=member, owner=member, model_mode='auto',
                               status='paused', pause_reason='quota_exhausted', complexity='normal',
                               last_activity_at=at, archived_at=at)
    TaskThread.objects.create(account=account, task=task, kind='main')
    TaskParticipant.objects.create(account=account, task=task, kind='human', user=member.user, role='lead',
                                   state='active')
    titles = ('明确评估范围与指标', '收集 Amazon.de 竞品与价格带', '整理 EPR / WEEE 合规要求', '估算竞品销量',
              '形成进入建议与风险清单')
    for order, title in enumerate(titles, 1):
        TaskStep.objects.create(account=account, task=task, order=order, stage='research', title=title,
                                status='done' if order <= 3 else 'queued', estimated_credits=6)
    _message(task, at, QUOTA_NOTICE, kind='quota_notice')
