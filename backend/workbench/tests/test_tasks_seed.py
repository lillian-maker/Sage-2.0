"""The tasks seed loads the Figma task screens and is idempotent, alone and together with the inbox seed."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from backend.models import Membership
from backend.workbench.models import (Artifact, Citation, Expert, Task, TaskDelivery, TaskMessage, TaskStep, TaskSuggestion,
                                      TaskThread)
from backend.workbench.seeds import tasks as seed_module
from backend.workbench.services import tasks as svc


def counts():
    return tuple(model.objects.count() for model in (Task, TaskMessage, TaskStep, TaskThread, TaskDelivery,
                                                     TaskSuggestion, Citation, Expert))


class TasksSeedTests(TestCase):
    def run_seed(self, *modules):
        args = []
        for name in modules:
            args += ['--only', name]
        call_command('seed_app', *args, stdout=StringIO())

    def test_seed_twice(self):
        self.run_seed('core', 'tasks')
        before = counts()
        self.run_seed('core', 'tasks')
        self.assertEqual(before, counts())
        main = Task.objects.get(title=seed_module.MAIN_TITLE)
        self.assertEqual(main.status, 'awaiting_user')
        self.assertEqual(svc.progress(main), {'done': 3, 'total': 9})
        # The sales estimate waits for the Jungle Scout authorization (screen 21).
        self.assertEqual(main.steps.get(order=3).status, 'awaiting_user')
        delivery = main.deliveries.get()
        self.assertEqual(delivery.summary, seed_module.DELIVERY_SUMMARY)
        self.assertEqual(delivery.decision, 'accepted')
        listing = Task.objects.get(title=seed_module.LISTING_TITLE)
        self.assertEqual((listing.status, listing.deliveries.get().decision), ('delivered', 'pending'))
        self.assertEqual(main.suggestions.filter(state='open').first().text, '把进入清单整理成 Excel 打勾表')
        self.assertEqual(main.threads.filter(kind='side').count(), 1)
        self.assertIsNotNone(svc.branch_groups(main.threads.get(kind='main')))
        lillian = Membership.objects.get(user__email='lillian@sagedemo.com', account=main.account)
        # The other two of screen 715's six come from the inbox seed (scan automation run, WEEE hand-off).
        # Same filter as GET app/tasks/active: archived seed tasks (e.g. the screen 14 quota pause) are not counted.
        active = Task.objects.filter(owner=lillian, status__in=svc.ACTIVE_STATUSES, archived_at__isnull=True)
        self.assertEqual(active.count(), 4)
        self.assertEqual(active.filter(status='stuck').count(), 1)
        self.assertEqual(active.filter(status='awaiting_user').count(), 1)
        self.assertEqual(Expert.objects.filter(catalog_code='AGT-007').count(), 1)

    def test_seed_with_inbox(self):
        self.run_seed('core', 'tasks', 'inbox')
        before = counts()
        self.run_seed('core', 'tasks', 'inbox')
        self.assertEqual(before, counts())
        self.assertEqual(Task.objects.filter(title=seed_module.MAIN_TITLE).count(), 1)
        self.assertEqual(Expert.objects.filter(name='望野').count(), 1)

    def test_full_seed_shares_report_with_artifacts_seed(self):
        self.run_seed()
        before = counts()
        self.run_seed()
        self.assertEqual(before, counts())
        # The billing seed adds a same-titled task in Lillian's personal account.
        main = Task.objects.get(title=seed_module.MAIN_TITLE, account__kind='business')
        self.assertEqual(Artifact.objects.filter(title=seed_module.REPORT_TITLE).count(), 1)
        delivery = main.deliveries.get()
        self.assertEqual(delivery.summary, seed_module.DELIVERY_SUMMARY)
        self.assertEqual(delivery.artifact_version.number, 2)
        lillian = Membership.objects.get(user__email='lillian@sagedemo.com', account=main.account)
        active = Task.objects.filter(account=main.account, archived_at__isnull=True,
                                     status__in=svc.ACTIVE_STATUSES).filter(svc.relation_q(lillian.user)).distinct()
        self.assertEqual((active.count(), active.filter(status='stuck').count(),
                          active.filter(status='awaiting_user').count()), (6, 1, 1))
        self.assertEqual(set(active.values_list('title', flat=True)),
                         {seed_module.MAIN_TITLE, 'Amazon.de 关键词与类目机会', 'X1 德语 Listing 上架前审核',
                          'Q4 欧洲达人合作清单', '每月新市场机会扫描', 'WEEE 注册材料补件通知'})
        handoff = Task.objects.get(title='处理差评：插头过热')
        self.assertEqual((handoff.status, handoff.source_inbox_item.group), ('delivered', 'in_progress'))
        self.assertEqual(handoff.source_inbox_item.task_id, handoff.pk)
        self.assertFalse(handoff.deliveries.exists())
        self.assertEqual(list(handoff.steps.order_by('order').values_list('status', flat=True)),
                         ['done', 'done', 'running', 'awaiting_user'])
