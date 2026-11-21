"""The inbox seed loads the Figma sample world and is idempotent."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from backend.workbench.models import (AppNotification, Automation, AutomationRun, InboxItem, InboxSourceDailyStat,
                                      TaskStep)
from backend.workbench.seeds import inbox as seed_module


class InboxSeedTests(TestCase):
    def run_seed(self):
        call_command('seed_app', '--only', 'inbox', stdout=StringIO())

    def test_seed_twice(self):
        self.run_seed()
        counts = (InboxItem.objects.count(), Automation.objects.count(), AutomationRun.objects.count(),
                  AppNotification.objects.count(), TaskStep.objects.count())
        self.run_seed()
        self.assertEqual(counts, (InboxItem.objects.count(), Automation.objects.count(),
                                  AutomationRun.objects.count(), AppNotification.objects.count(),
                                  TaskStep.objects.count()))
        self.assertEqual(InboxItem.objects.count(), len(seed_module.ITEMS))
        self.assertEqual(AutomationRun.objects.filter(state='missed').count(), 1)
        funnel = [sum(InboxSourceDailyStat.objects.values_list(f, flat=True))
                  for f in ('received', 'after_rules', 'after_expert', 'admitted')]
        self.assertEqual(tuple(funnel), seed_module.FEISHU['funnel'])


class FullSeedInboxTests(TestCase):
    """Screens 48 and 53 after the whole sample world is loaded (twice)."""

    def test_inbox_numbers_and_handover(self):
        from backend.models import MemberRemoval, Membership
        from backend.workbench.common import AppContext
        from backend.workbench.models import AuthorizationRequest, HandoffItem
        from backend.workbench.seeds import handover
        from backend.workbench.services import inbox

        for _ in range(2):
            call_command('seed_app', stdout=StringIO())
        member = Membership.objects.get(user__email='lillian@sagedemo.com', account__kind='business')
        ctx = AppContext(member=member, account=member.account, user=member.user)
        counts = inbox.inbox_counts(ctx)
        self.assertEqual({key: counts[key] for key in ('all', 'decisions', 'mentions', 'external')},
                         {'all': 9, 'decisions': 4, 'mentions': 3, 'external': 5})
        entries = {entry['type']: entry for entry in inbox.decision_entries(ctx)}
        self.assertEqual(set(entries), {'approve', 'supply', 'accept', 'invite'})
        self.assertEqual(entries['approve']['authorization']['estimated_credits'], 40)
        self.assertEqual(entries['accept']['title'], '德语 Listing 本地化样稿 v1')
        self.assertEqual(entries['supply']['context'], 'Amazon.de 关键词与类目机会 · 觅位')
        self.assertEqual(AuthorizationRequest.objects.filter(
            task__title='德国站竞品格局调研', task__account=member.account, decision='pending').count(), 1)
        self.assertEqual(MemberRemoval.objects.filter(request_key=handover.REQUEST_KEY).count(), 1)
        [group] = inbox.handover_groups(ctx)
        self.assertEqual(group['from_member'], {'name': 'Wang Min'})
        self.assertEqual(sorted((item['kind'], item['target']['title']) for item in group['items']),
                         [('approval', 'Jungle Scout 只读查询'), ('automation', '差评日报'),
                          ('automation', '每月新市场机会扫描'), ('custom_expert', '售后话术专家'),
                          ('task', 'Q4 广告复盘'), ('task', '法国站选品调研'),
                          ('workspace_ownership', '德国市场进入')])
        self.assertEqual(HandoffItem.objects.filter(successor=member).count(), 7)
        # 75 设计是办理离职前的成员列表状态，Wang Min 保持 active 且仍在成员列表可见。
        self.assertTrue(Membership.objects.get(user__email='wangmin@sage.demo', account=member.account).is_active)
