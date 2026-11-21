"""Contract tests: app/usage/summary and app/usage/tasks (C-C-8, C-C-9, Q-C-19)."""
from unittest.mock import patch

from backend.workbench.tests.base import AppTestCase
from backend.workbench.tests.test_billing_fixtures import credits, spend, task, workspace


class UsageTests(AppTestCase):
    def setUp(self):
        self.account = self.login(self.lillian)
        owner = self.member(self.lillian, self.account)
        credits(self.account)
        space = workspace(self.account, self.lillian)
        self.big = task(self.account, owner, '德国站竞品格局调研', space=space)
        self.mid = task(self.account, owner, '法国站选品调研')
        spend(self.account, '%s:a' % self.big.public_id, '300')
        spend(self.account, '%s:b' % self.big.public_id, '120')
        spend(self.account, '%s:a' % self.mid.public_id, '310')
        spend(self.account, 'legacy-history-key', '180')

    def test_summary(self):
        body = self.ok(self.get('app/usage/summary'))
        self.assertEqual(body['period_used'], '910')
        self.assertEqual(body['period_total'], '5000')
        self.assertAlmostEqual(body['used_ratio'], 0.182, places=3)
        for key in ('period_start', 'period_end', 'daily_average', 'daily', 'as_of'):
            self.assertIn(key, body)
        self.assertEqual(sum(int(day['credits']) for day in body['daily']), 910)

    def test_tasks_sorted_by_credits(self):
        body = self.ok(self.get('app/usage/tasks', {'sort': 'credits'}))
        self.assertEqual(body['total'], 3)
        self.assertEqual([row['credits'] for row in body['items']], ['420', '310', '180'])
        first = body['items'][0]
        self.assertEqual(first['task_id'], self.big.public_id)
        self.assertEqual(first['title'], '德国站竞品格局调研')
        self.assertEqual(first['workspace']['name'], '德国市场进入')
        self.assertEqual(first['source'], 'task')
        legacy = body['items'][2]
        self.assertIsNone(legacy['task_id'])
        self.assertIsNone(legacy['title'])
        self.assertEqual(legacy['source'], 'other')
        self.assertIn('next_offset', body)

    def test_tasks_rejects_unknown_sort(self):
        self.assert_error(self.get('app/usage/tasks', {'sort': 'name'}), 422, 'INVALID_REQUEST')

    def test_ledger_unreadable(self):
        with patch('backend.commerce.billing.credits_summary', side_effect=ValueError('LEDGER_INCONSISTENT')):
            self.assert_error(self.get('app/usage/summary'), 503, 'SOURCE_UNAVAILABLE')
