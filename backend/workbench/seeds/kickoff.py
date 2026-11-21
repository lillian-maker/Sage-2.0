"""Screen 713/2018 (开工确认卡): a task that waits for kickoff, with two open questions and missing capabilities.

Runs after seeds/knowledge.py so the opening request can attach real documents.
"""
from datetime import timedelta

from ._clock import timezone

from backend.workbench.models import AcceptanceCriterion, Extension, KickoffQuestion, KnowledgeDocument, Task

from . import core
from . import tasks as task_seed

ORDER = 97
TITLE = '德国站进入评估'
PROMPT = '帮我评估家用小家电进入德国市场的机会，重点看 Amazon.de 的竞争格局和 EPR 合规要求，给出是否进入的建议。'
KICKOFF_NOTE = '开始前先和你确认几件事。确认后我才会开始执行，不会提前消耗 Credits。'
ATTACHMENTS = ('竞品清单.xlsx', '品牌手册 2026.pdf')
# question, options, suggested answer (answered_at stays empty until Lillian confirms)
QUESTIONS = (
    ('重点评估哪个渠道？', ['Amazon.de', 'Amazon.de + 独立站', '由 Sage 决定'], 'Amazon.de'),
    ('交付成什么形式？', ['一页结论', '完整报告 + 数据表', '由 Sage 决定'], '完整报告 + 数据表'),
)
CRITERIA = ('覆盖前 20 名竞品与价格带', '数字注明来源与日期', '列出上架前的合规事项', '给出进入或暂缓的结论',
            '关键数字由清账复核')
# stage, title, expert, required extension (missing capability)
STEPS = (
    ('understand', '明确评估范围与指标', '望野', None),
    ('research', '收集 Amazon.de 竞品与价格带', '望野', None),
    ('research', '整理 EPR / WEEE 合规要求', '安界', None),
    ('research', '估算竞品销量', '望野', '销量估算'),
    ('research', '读取竞品销量数据', '望野', 'Jungle Scout'),
    ('analyze', '关键词与类目机会', '觅位', None),
    ('review', '复核价格带与利润测算', '清账', None),
    ('deliver', '形成进入建议与风险清单', '立言', None),
)


def seed(ctx):
    if 'users' not in ctx:
        core.seed(ctx)
    account, member, workspace = ctx['business'], ctx['memberships']['lillian'], ctx['workspace']
    experts = {name: task_seed._expert(name, code, title) for name, code, title in task_seed.EXPERTS}
    now = timezone.now()
    start = timezone.localtime(now).replace(hour=9, minute=40, second=0, microsecond=0)
    task, created = task_seed._task(account, member, workspace, TITLE, experts['望野'], 'awaiting_kickoff',
                                    start + timedelta(minutes=1), (experts['安界'],))
    if not created:
        return
    Task.objects.filter(pk=task.pk).update(
        goal='判断家用小家电是否值得在 12 月前进入德国站',
        scope_text='Amazon.de 小家电类目前 20 名竞品、价格带、EPR / WEEE 合规',
        deliverables='竞品格局报告（文档）+ 价格带数据表', constraints='竞品数据只用近 90 天；对外沟通只出草稿',
        complexity='complex', estimated_expert_count=5, estimated_duration_minutes=25)
    opening = task_seed._message(task, start, PROMPT, user=member.user)
    docs = [KnowledgeDocument.objects.filter(account=account, title=title, deleted_at__isnull=True).order_by('id').first()
            for title in ATTACHMENTS]
    opening.attached_documents.set([doc for doc in docs if doc])
    task_seed._message(task, start + timedelta(minutes=1), KICKOFF_NOTE, kind='kickoff_card', expert=experts['望野'],
                       card_type='kickoff', card_id=task.public_id)
    for order, (question, options, suggested) in enumerate(QUESTIONS, 1):
        KickoffQuestion.objects.create(account=account, task=task, order=order, question=question, options=options,
                                       answer=suggested)
    for order, text in enumerate(CRITERIA, 1):
        AcceptanceCriterion.objects.create(account=account, task=task, order=order, text=text, origin='ai')
    for order, (stage, title, expert, required) in enumerate(STEPS, 1):
        extension = Extension.objects.filter(name=required).order_by('id').first() if required else None
        task.steps.create(account=account, order=order, stage=stage, title=title, expert=experts[expert],
                          status='missing_capability' if extension else 'queued', required_extension=extension,
                          estimated_credits=6)
