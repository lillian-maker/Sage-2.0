"""Screen 691 (交办后的任务): a buyer complaint that was handed to 解忧 and now waits for Lillian to confirm the draft.

Uses its own inbox item so the todo item on screens 47/48 stays available for the hand-off flow. The task is
"delivered" (drafts handed over, no formal TaskDelivery) so it stays out of the six active tasks of screen 715 and
adds no acceptance card to screen 48.
"""
from datetime import timedelta

from ._clock import timezone

from backend.workbench.models import Artifact, ArtifactVersion, InboxItem, InboxItemEvent, Task
from backend.workbench.services import artifacts as store

from . import artifacts as artifact_seed
from . import core
from . import inbox as inbox_seed
from . import tasks as task_seed

ORDER = 45
ITEM_REF = 'seed-handoff'
ITEM_TITLE = '德国站差评：插头发烫，买家不敢再用'
TITLE = '处理差评：插头过热'
ORIGINAL = 'Nach zwei Wochen wird der Stecker sehr heiß. Ich habe Angst, das Gerät weiter zu benutzen. ★☆☆☆☆'
TRANSLATED = '用了两周插头变得很烫，我不敢再用了。'
RULE_NOTE = '已按工作区“德国市场进入”的规则归入本工作区；回复将使用德语，只生成草稿。'
LEAD_NOTE = ('已起草德语回复与补偿方案（全额退款或换新二选一），并请质守排查批次。'
             '回复草稿在右侧成果中，你确认后再发送。')
REPLY = ('Sehr geehrte Kundin, sehr geehrter Kunde,\n\nes tut uns leid, dass der Stecker heiß wird. Bitte benutzen Sie '
         'das Gerät nicht weiter. Wir bieten Ihnen eine volle Rückerstattung oder ein Ersatzgerät an.')
BATCH = '请质守排查同批次（2026-09 批）是否有插头发热反馈，并确认是否需要暂停该批次发货。'
# title, stage, expert, status, minutes taken, activity
STEPS = (
    ('理解买家问题与订单信息', 'understand', '解忧', 'done', 1, None),
    ('起草德语回复与补偿方案', 'analyze', '解忧', 'done', 2, None),
    ('排查同批次是否有同类问题', 'research', '质守', 'running', None, '进行中'),
    ('确认回复草稿', 'deliver', '解忧', 'awaiting_user', None, '等待你确认'),
)


def seed(ctx):
    if 'users' not in ctx:
        core.seed(ctx)
    account, user, member, workspace = (ctx['business'], ctx['users']['lillian'], ctx['memberships']['lillian'],
                                        ctx['workspace'])
    sources = ctx.get('inbox_sources') or inbox_seed._sources(
        account, user, {name: inbox_seed._expert(name) for name in inbox_seed.EXPERTS})
    if InboxItem.objects.filter(source=sources['amazon_buyer_messages'], external_ref=ITEM_REF).exists():
        return
    experts = {name: task_seed._expert(name, code, title) for name, code, title in task_seed.EXPERTS}
    lead, quality = experts['解忧'], experts['质守']
    received = inbox_seed._received_at(timezone.localdate(), 0, 10, 41)
    item = InboxItem.objects.create(
        source=sources['amazon_buyer_messages'], external_ref=ITEM_REF, account=account, recipient=user,
        title=ITEM_TITLE, received_at=received, original_text=ORIGINAL, translated_text=TRANSLATED,
        group='in_progress', confidence=92, priority='high', sender_label='Amazon.de 买家', channel_label='Amazon.de',
        workspace=workspace, rationale='涉及产品安全，属于高优先级。',
        suggestion='交给解忧起草回复与补偿方案，同时请质守排查批次。')
    item.suggested_experts.set([lead, quality])
    task, _ = task_seed._task(account, member, workspace, TITLE, lead, 'delivered', received + timedelta(minutes=2),
                              (quality,))
    Task.objects.filter(pk=task.pk).update(source_inbox_item=item, created_at=received + timedelta(minutes=1))
    item.task = task
    item.save(update_fields=['task', 'updated_at'])
    InboxItemEvent.objects.create(account=account, item=item, at=received, kind='received',
                                  text='%s 收到并归入工作区“%s”' % (received.strftime('%H:%M'), workspace.name))
    InboxItemEvent.objects.create(account=account, item=item, at=received + timedelta(minutes=1), kind='handed_off',
                                  text='%s 交给解忧处理' % (received + timedelta(minutes=1)).strftime('%H:%M'))
    task_seed._message(task, received + timedelta(minutes=1), RULE_NOTE)
    task_seed._message(task, received + timedelta(minutes=2), LEAD_NOTE, expert=lead)
    start = received + timedelta(minutes=1)
    for order, (title, stage, name, status, minutes, activity) in enumerate(STEPS, 1):
        finished = start + timedelta(minutes=minutes) if minutes else None
        task.steps.create(account=account, order=order, stage=stage, title=title, expert=experts[name], status=status,
                          activity=activity, estimated_credits=4, started_at=start if status != 'queued' else None,
                          finished_at=finished)
        start = finished or start
    for title, body in (('回复草稿（德语）', REPLY), ('批次排查请求', BATCH)):
        artifact = Artifact.objects.create(account=account, workspace=workspace, source_task=task, title=title,
                                           kind='document', created_by_expert=lead)
        version = store.create_version(artifact, artifact_seed.doc(body), state='draft', author_expert=lead)
        # Just before the report draft (10 分钟前) so the library on screen 54 still opens with the report.
        ArtifactVersion.objects.filter(pk=version.pk).update(created_at=received + timedelta(minutes=1))
