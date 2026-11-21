"""种子回归（第二轮）：B/D 组登记的缺口——主任务内容、自动化运行时间、订单与订阅座位、样稿版本号。"""
import uuid
from datetime import timedelta
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from backend.commerce.models import Order, PaidSeat, Subscription
from backend.models import Membership
from backend.workbench.models import Artifact, AutomationRun
from backend.workbench.tests.base import AppTestCase


def rid():
    return uuid.uuid4().hex


class SeedRegressionBase(AppTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        call_command('seed_app')
        call_command('seed_app')  # 第二次必须幂等

    def setUp(self):
        super().setUp()
        self.seeded_lillian = Membership.objects.get(user__email='lillian@sagedemo.com',
                                                     account__kind='business')
        self.seeded_business = self.seeded_lillian.account

    def login_seeded(self):
        self.login(self.seeded_lillian.user, self.seeded_business)


class SeedContentTests(SeedRegressionBase):
    def test_seed_is_idempotent_for_new_rows(self):
        self.assertEqual(Subscription.objects.filter(account=self.seeded_business).count(), 1)
        self.assertEqual(PaidSeat.objects.filter(subscription__account=self.seeded_business).count(), 3)
        personal = Membership.objects.get(user__email='lillian@sagedemo.com', account__kind='personal').account
        self.assertEqual(Order.objects.filter(account=personal, display_number__endswith='-0042').count(), 1)
        self.assertFalse(Order.objects.filter(account=self.seeded_business, display_number__endswith='-0042').exists())

    def test_main_task_content_is_rich(self):
        from backend.workbench.models import (AcceptanceCriterion, Task, TaskMessage, TaskStep,
                                              TaskSuggestion, TaskThread)
        task = Task.objects.get(account=self.seeded_business, title='德国站竞品格局调研', workspace__isnull=False)
        self.assertEqual(TaskThread.objects.filter(task=task).count(), 2)
        # 主责消息把“我把任务拆成 5 步…”与三条发现合并成一条编号消息（682:2），总数为 14。
        self.assertGreaterEqual(TaskMessage.objects.filter(task=task).count(), 14)
        self.assertEqual(AcceptanceCriterion.objects.filter(task=task, step__isnull=True).count(), 5)
        self.assertGreaterEqual(TaskStep.objects.filter(task=task).count(), 6)
        self.assertGreaterEqual(TaskSuggestion.objects.filter(task=task, state='open').count(), 2)
        self.login_seeded()
        detail = self.ok(self.get('app/tasks/%s' % task.public_id))
        self.assertGreaterEqual(len(detail['suggestions']), 2)
        self.assertIn('reviews', detail)  # 第二轮新增键位存在
        self.assertIn('pending_delivery', detail)
        self.assertIn('authorizations', detail)

    def test_automation_run_scheduled_within_a_week(self):
        run = AutomationRun.objects.get(account=self.seeded_business, state='running',
                                        title='运行指令 · 每月新市场机会扫描')
        self.assertGreater(run.scheduled_for, timezone.now() - timedelta(days=7))

    def test_paid_order_appears_in_orders_api(self):
        personal = Membership.objects.get(user__email='lillian@sagedemo.com', account__kind='personal')
        self.login(personal.user, personal.account)
        body = self.ok(self.get('billing/orders'))
        item = next(row for row in body['items'] if row['display_number'].endswith('-0042'))
        self.assertEqual(item['status'], 'fulfilled')
        self.assertEqual(item['snapshot']['credits_quantity'], '5000')
        self.assertEqual(item['snapshot']['kind'], 'credits')
        self.assertEqual(item['amount_minor'], 44900)
        detail = self.ok(self.get('billing/orders/%s' % item['id'], {'extended': 'true'}))
        self.assertEqual(detail['payments'][0]['channel'], 'alipay')
        self.assertEqual(detail['payments'][0]['status'], 'accepted')
        self.assertTrue(detail['payments'][0]['transaction_reference_masked'].endswith('8821'))
        self.assertEqual(detail['delivery']['status'], 'completed')
        self.assertEqual(detail['delivery']['credit_batch']['quantity'], '5000')

    def test_business_task_can_start_with_seats_and_credits(self):
        self.login_seeded()
        created = self.ok(self.post('app/tasks', {'body': '调研一下 Amazon.fr 厨房电器竞品'}), 201)
        body = self.ok(self.post('app/tasks/%s/start' % created['task']['id'], {'client_request_id': rid()}))
        self.assertEqual(body['task']['status'], 'running')

    @override_settings(WORKBENCH_EXPERT_TRY_RUNNER='backend.workbench.tests.test_integration2_seed.fake_try')
    def test_expert_try_runs_with_seeded_credits(self):
        self.login_seeded()
        body = self.ok(self.get('app/experts'))
        reply = self.ok(self.post('app/experts/%s/try' % body['items'][0]['id'], {'prompt': '帮我看看'}))
        self.assertEqual(reply['reply'], '试一下回复')
        self.assertEqual(reply['credits_used'], 1)


def fake_try(expert, prompt, user, account):
    return {'reply': '试一下回复', 'credits_used': 1}
