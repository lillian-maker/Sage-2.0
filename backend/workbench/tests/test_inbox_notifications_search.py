"""Contract tests for app/notifications and app/search."""
from django.utils import timezone

from backend.notifications import record_security_notice
from backend.workbench.services.notify import notify
from backend.workbench.tests.base import AppTestCase
from backend.workbench.tests.test_inbox_support import (make_automation, make_expert, make_item, make_source,
                                                        make_task, make_workspace)


class NotificationTests(AppTestCase):
    def setUp(self):
        self.anjie = make_expert('安界')
        self.step = notify(account=self.business, recipient=self.lillian, category='task_progress',
                           type='step_completed', text='安界完成了第 3 步：EPR 注册要求核对',
                           context_label='欧盟包装法规对照表', target_type='task', target_id='T1',
                           action='open', actor_expert=self.anjie, dedupe_key='step:1')
        self.mention = notify(account=self.business, recipient=self.lillian, category='mention', type='mentioned',
                              text='Chen Yu 提到了你', dedupe_key='mention:1')
        self.billing = record_security_notice(self.lillian, 'login-1')
        self.login(self.lillian, self.business)

    def test_list_merges_app_and_billing_without_mentions(self):
        body = self.ok(self.get('app/notifications'))
        sources = sorted(item['source'] for item in body['items'])
        self.assertEqual(sources, ['app', 'billing'])
        app = next(i for i in body['items'] if i['source'] == 'app')
        self.assertEqual(app['actor_expert']['name'], '安界')
        self.assertEqual(app['context_label'], '欧盟包装法规对照表')
        self.assertEqual(body['total'], 2)
        self.assertEqual(body['unread_count'], 2)

    def test_billing_items_carry_title_and_ledger_date(self):
        from datetime import datetime
        from backend.commerce.models import BillingNotification
        at = timezone.make_aware(datetime(2026, 11, 3, 16, 0))
        BillingNotification.objects.create(account=self.business, type='credits_usage_25', source_event='usage:t',
                                           occurred_at=at)
        items = [i for i in self.ok(self.get('app/notifications', {'category': 'billing'}))['items']]
        usage = next(i for i in items if i['type'] == 'credits_usage_25')
        self.assertEqual(usage['text'], '本期额度已使用 25%')
        self.assertEqual(usage['context_label'], '企业账本 · 11月3日')
        self.assertIsNone(usage['actor_expert'])
        security = next(i for i in items if i['type'] == 'account_security')
        self.assertEqual(security['text'], '账号安全提醒')
        local = timezone.localtime(datetime.fromisoformat(security['occurred_at']))
        self.assertTrue(security['context_label'].endswith('%d月%d日' % (local.month, local.day)))

    def test_category_and_unread_filters(self):
        self.assertEqual(self.ok(self.get('app/notifications', {'category': 'billing'}))['total'], 1)
        self.assertEqual(self.ok(self.get('app/notifications', {'category': 'task_progress'}))['total'], 1)
        self.assert_error(self.get('app/notifications', {'category': 'mention'}), 422, 'INVALID_REQUEST')
        self.assert_error(self.get('app/notifications', {'unread': 'yes'}), 422, 'INVALID_REQUEST')

    def test_mark_selected_and_all(self):
        body = self.ok(self.post('app/notifications/read', {'app_ids': [self.step.public_id]}))
        self.assertEqual(body['unread_count'], 1)
        self.assertEqual(self.ok(self.get('app/notifications', {'unread': '1'}))['total'], 1)
        body = self.ok(self.post('app/notifications/read', {'all': True}))
        self.assertEqual(body['unread_count'], 0)
        self.mention.refresh_from_db()
        self.assertIsNone(self.mention.read_at)

    def test_mark_validation(self):
        self.assert_error(self.post('app/notifications/read', {}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post('app/notifications/read', {'all': False}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post('app/notifications/read', {'app_ids': ['01HZZZZZZZZZZZZZZZZZZZZZZZ']}), 404,
                          'NOT_FOUND')
        self.assert_error(self.post('app/notifications/read', {'ids': []}), 422, 'INVALID_REQUEST')

    def test_other_users_notifications_hidden(self):
        self.login(self.mark, self.business)
        self.assertEqual(self.ok(self.get('app/notifications'))['total'], 0)
        self.assert_error(self.post('app/notifications/read', {'app_ids': [self.step.public_id]}), 404, 'NOT_FOUND')


class SearchTests(AppTestCase):
    def setUp(self):
        self.workspace = make_workspace(self.business, self.lillian, [(self.lillian, 'owner'), (self.chen, 'editor')])
        expert = make_expert('衡价')
        make_task(self.business, self.member(self.lillian), title='德国站竞品价格调研', workspace=self.workspace,
                  last_activity_at=timezone.now())
        make_automation(self.business, self.member(self.lillian), self.workspace, expert, name='每周德国站竞品价格监控')
        make_item(make_source(self.business, self.lillian), '德国站差评提到插头过热')
        self.login(self.lillian, self.business)

    def test_all_returns_category_previews(self):
        body = self.ok(self.get('app/search', {'q': '德国'}))
        categories = {c['key']: c for c in body['categories']}
        self.assertEqual(categories['tasks']['items'][0]['title'], '德国站竞品价格调研')
        self.assertEqual(categories['workspaces']['items'][0]['context'], '企业 · 1 个任务 · 2 位成员')
        self.assertEqual(categories['automations']['items'][0]['context'], '衡价 · 每周一 09:00')
        self.assertEqual(categories['inbox']['total'], 1)
        self.assertEqual(categories['all']['total'], sum(c['total'] for k, c in categories.items() if k != 'all'))

    def test_single_category_page(self):
        body = self.ok(self.get('app/search', {'q': '德国', 'category': 'tasks'}))
        self.assertEqual(body['total'], 1)
        self.assertEqual(body['categories'][0]['key'], 'tasks')

    def test_task_snippet_is_the_matching_sentence(self):
        from backend.workbench.services.search import sentence_snippet
        body = '清单已整理。上架前必须取得 WEEE 注册号，并在德国包装法 LUCID 系统完成登记。两项都建议本周启动。'
        self.assertEqual(sentence_snippet(body, '德国'), '…上架前必须取得 WEEE 注册号，并在德国包装法 LUCID 系统完成登记…')
        self.assertIsNone(sentence_snippet(body, '法国'))

    def test_document_hits_carry_their_knowledge_base(self):
        from backend.workbench.models import KnowledgeBase, KnowledgeDocument
        kb = KnowledgeBase.objects.create(account=self.business, name='德国市场法规库', scope='account',
                                          owner=self.lillian, cite_audience='workspace_all')
        KnowledgeDocument.objects.create(account=self.business, knowledge_base=kb, title='德国 EPR 法规摘要.pdf',
                                         source_kind='upload', uploaded_by=self.lillian)
        categories = {c['key']: c for c in self.ok(self.get('app/search', {'q': '德国'}))['categories']}
        hit = next(item for c in categories.values() for item in c['items'] if item['type'] == 'document')
        self.assertEqual(hit['knowledge_base_id'], kb.public_id)
        self.assertEqual(hit['context'], '德国市场法规库')

    def test_validation(self):
        self.assert_error(self.get('app/search', {'q': ''}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.get('app/search', {'q': 'x' * 101}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.get('app/search', {'q': '德国', 'category': 'people'}), 422, 'INVALID_REQUEST')

    def test_other_member_does_not_see_private_objects(self):
        self.login(self.mark, self.business)
        categories = {c['key']: c for c in self.ok(self.get('app/search', {'q': '德国'}))['categories']}
        self.assertEqual(categories['tasks']['total'], 0)
        self.assertEqual(categories['inbox']['total'], 0)
        self.assertEqual(categories['automations']['total'], 0)
        self.assertEqual(categories['workspaces']['total'], 0)
