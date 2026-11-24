"""Chen Yu joins the tasks whose boards draw him in the participants panel (CY avatar, "参与者 · 4"): the kickoff task,
the listing review, the accepted 品牌主张文案 and the returned first-round report. Runs after every task seed."""
from backend.workbench.models import Task, TaskParticipant

ORDER = 100
TITLES = ('德国站进入评估', 'X1 德语 Listing 上架前审核', '品牌主张文案', '竞品格局报告 · 首轮验收')


def seed(ctx):
    business, lillian, chen = ctx['business'], ctx['users']['lillian'], ctx['users']['chen']
    for task in Task.objects.filter(account=business, owner__user=lillian, title__in=TITLES):
        if task.participants.filter(kind='human', user=chen).exists():
            continue
        TaskParticipant.objects.create(account=business, task=task, kind='human', user=chen, role='collaborator',
                                       state='active', invited_by=lillian)
