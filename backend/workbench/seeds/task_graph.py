"""Screen 21 (编排视图): the sales-estimate node of "德国站竞品格局调研" carries the Jungle Scout authorization.

The request itself is created by seeds/inbox.py and seeds/billing.py (one shared row); this links it to the step
once both the step graph (seeds/tasks.py) and the request exist.
"""
from backend.workbench.models import AuthorizationRequest, Task

from . import core
from . import tasks as task_seed

ORDER = 99


def seed(ctx):
    if 'users' not in ctx:
        core.seed(ctx)
    task = Task.objects.filter(account=ctx['business'], title=task_seed.MAIN_TITLE, workspace=ctx['workspace']).first()
    step = task and task.steps.filter(label='销量估算').first()
    if step is None:
        return
    AuthorizationRequest.objects.filter(task=task, step__isnull=True, decision='pending',
                                        extension__name='Jungle Scout').update(step=step)
