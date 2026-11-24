"""Screens 16-18 (631, 17, 18): the core workspace "德国市场进入" lists exactly six tasks ("任务 6", sidebar
"展开显示 · 另 3 个").

Other seeds create more tasks in the core workspace for their own screens (inbox hand-offs, the kick-off sample,
Chen's ad plan, the stuck X1 review, finished tasks on the task list, the monthly scan run). Every non-archived task
outside the six below is detached from the workspace after all seeds ran; their artifacts and inbox items keep the
workspace.
"""
from datetime import timedelta

from ._clock import timezone

from backend.workbench.models import Task

from . import artifacts as artifact_seed
from . import core
from . import tasks as task_seed

# After kickoff (97), the last seed that adds a task to the core workspace.
ORDER = 98

LOGISTICS_TITLE = '德国物流与 VAT 成本测算'
# The workspace task list on screen 631.
WORKSPACE_TASKS = ('德国站竞品格局调研', '德国 EPR 与 WEEE 合规清单', 'Amazon.de 关键词与类目机会', '德语 Listing 本地化',
                   LOGISTICS_TITLE, '进入方案汇总（经营脉络）')
# The sidebar group "未关联工作区" lists only "整理本周 Amazon 差评" (727:7); detached demo tasks that fit another
# workspace move there. Their recency keeps them out of those groups' three visible rows.
OTHER_WORKSPACE_TASKS = {'黑五大促': ('Q4 广告复盘', 'Q4 达人合作清单', '黑五投放素材清单', '黑五备货节奏'),
                         '品牌官网改版': ('品牌主张文案',)}


def _logistics(account, member, workspace):
    experts = {name: task_seed._expert(name, code, title) for name, code, title in task_seed.EXPERTS}
    at = timezone.localtime(timezone.now()).replace(month=10, day=30, hour=16, minute=0, second=0, microsecond=0)
    task, created = task_seed._task(account, member, workspace, LOGISTICS_TITLE, experts['通途'], 'accepted', at)
    if created:
        task_seed._message(task, at - timedelta(hours=1), '测算德国站头程物流与 VAT 成本。', user=member.user)
        Task.objects.filter(pk=task.pk).update(accepted_at=at)


def seed(ctx):
    if 'users' not in ctx:
        core.seed(ctx)
    account, workspace = ctx['business'], ctx['workspace']
    _logistics(account, ctx['memberships']['lillian'], workspace)
    Task.objects.filter(account=account, workspace=workspace, archived_at__isnull=True).exclude(
        title__in=WORKSPACE_TASKS).update(workspace=None)
    for name, titles in OTHER_WORKSPACE_TASKS.items():
        Task.objects.filter(account=account, workspace__isnull=True, archived_at__isnull=True,
                            title__in=titles).update(workspace=artifact_seed.workspace(ctx, name))
