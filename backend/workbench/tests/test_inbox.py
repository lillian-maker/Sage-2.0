"""Contract tests for app/inbox (items, sources, pull pipeline, decisions, handover)."""
import uuid
from datetime import timedelta

from django.utils import timezone

from backend.models import MemberRemoval
from backend.workbench.models import (AuthorizationRequest, HandoffItem, InboxItem, InboxSourceDailyStat,
                                      KickoffQuestion, Task, TaskParticipant)
from backend.workbench.services import inbox as service
from backend.workbench.tests.base import AppTestCase
from backend.workbench.tests.test_inbox_support import (give_credits, make_automation, make_expert, make_extension,
                                                        make_item, make_source, make_task, make_workspace)


def rid():
    return uuid.uuid4().hex


class InboxItemTests(AppTestCase):
    def setUp(self):
        self.jieyou = make_expert('解忧')
        self.anjie = make_expert('安界')
        self.source = make_source(self.business, self.lillian, kind='amazon_buyer_messages')
        self.item = make_item(self.source, '德国站差评提到“插头过热”', group='todo', confidence=92,
                              experts=[self.jieyou, self.anjie], priority='high')
        self.pending = make_item(self.source, '买家询问插头是否适配英国', group='pending_confirmation', confidence=45,
                                 minutes_ago=30)
        self.login(self.lillian, self.business)

    def test_list_fields_counts_and_order(self):
        body = self.ok(self.get('app/inbox/items'))
        self.assertEqual([i['id'] for i in body['items']], [self.item.public_id, self.pending.public_id])
        first = body['items'][0]
        for key in ('id', 'title', 'group', 'confidence', 'priority', 'sender_label', 'received_at', 'source',
                    'suggested_experts', 'workspace', 'task'):
            self.assertIn(key, first)
        self.assertEqual(first['suggested_experts'][0]['name'], '解忧')
        self.assertEqual(body['total'], 2)
        self.assertEqual(body['counts']['groups']['todo'], 1)
        self.assertEqual(body['counts']['external'], 2)
        self.assertEqual(body['counts']['sources']['amazon_buyer_messages'], 2)

    def test_filters_and_validation(self):
        body = self.ok(self.get('app/inbox/items', {'group': 'pending_confirmation'}))
        self.assertEqual([i['id'] for i in body['items']], [self.pending.public_id])
        self.assertEqual(self.ok(self.get('app/inbox/items', {'q': '插头过热'}))['total'], 1)
        self.assertEqual(self.ok(self.get('app/inbox/items', {'source': 'feishu'}))['total'], 0)
        self.assert_error(self.get('app/inbox/items', {'group': 'nope'}), 422, 'INVALID_REQUEST')
        self.assert_error(self.get('app/inbox/items', {'sort': 'title'}), 422, 'INVALID_REQUEST')
        self.assert_error(self.get('app/inbox/items', {'extra': '1'}), 422, 'INVALID_REQUEST')

    def test_channels(self):
        from backend.workbench.services.notify import notify
        notify(account=self.business, recipient=self.lillian, category='mention', type='mentioned',
               text='Chen Yu 提到了你', dedupe_key='m1')
        mentions = self.ok(self.get('app/inbox/items', {'channel': 'mentions'}))
        self.assertEqual([i['text'] for i in mentions['items']], ['Chen Yu 提到了你'])
        self.assertEqual(mentions['counts']['mentions'], 1)
        decisions = self.ok(self.get('app/inbox/items', {'channel': 'decisions'}))
        self.assertEqual(decisions['total'], decisions['counts']['decisions'])
        self.assertEqual(self.ok(self.get('app/inbox/items', {'channel': 'external'}))['total'], 2)
        self.assert_error(self.get('app/inbox/items', {'channel': 'other'}), 422, 'INVALID_REQUEST')
        self.assert_error(self.get('app/inbox/items', {'channel': 'mentions', 'group': 'todo'}), 422,
                          'INVALID_REQUEST')

    def test_detail_has_text_rationale_events(self):
        item = self.ok(self.get('app/inbox/items/%s' % self.item.public_id))['item']
        self.assertEqual(item['original_text'], '原文 德国站差评提到“插头过热”')
        self.assertEqual(item['rationale'], '依据 德国站差评提到“插头过热”')
        self.assertEqual(item['events'][0]['kind'], 'received')

    def test_other_users_items_are_hidden(self):
        self.login(self.mark, self.business)
        self.assertEqual(self.ok(self.get('app/inbox/items'))['total'], 0)
        self.assert_error(self.get('app/inbox/items/%s' % self.item.public_id), 404, 'NOT_FOUND')

    def test_non_member_gets_404(self):
        self.login(self.anna, self.business)
        self.assert_error(self.get('app/inbox/items'), 404, 'NOT_FOUND')
        self.assert_error(self.get('app/inbox/decisions'), 404, 'NOT_FOUND')

    def test_ignore_then_conflict(self):
        body = self.ok(self.post('app/inbox/items/%s/ignore' % self.pending.public_id))
        self.assertEqual(body['item']['group'], 'ignored')
        self.assertIsNotNone(body['item']['ignored_at'])
        self.assert_error(self.post('app/inbox/items/%s/ignore' % self.pending.public_id), 409, 'STATE_CONFLICT')
        self.assertEqual(self.ok(self.get('app/inbox/items'))['total'], 1)
        self.assertEqual(self.ok(self.get('app/inbox/items', {'group': 'ignored'}))['total'], 1)

    def test_ignore_rejects_body_keys(self):
        self.assert_error(self.post('app/inbox/items/%s/ignore' % self.item.public_id, {'x': 1}), 422,
                          'INVALID_REQUEST')

    def test_hand_off_defaults_to_first_suggested_expert(self):
        give_credits(self.business)
        body = self.ok(self.post('app/inbox/items/%s/hand-off' % self.item.public_id, {'client_request_id': rid()}))
        self.assertEqual(body['item']['group'], 'in_progress')
        task = Task.objects.get(public_id=body['task']['id'])
        self.assertEqual(task.lead_expert, self.jieyou)
        self.assertEqual(task.status, 'running')
        self.assertEqual(task.source_inbox_item, self.item)
        roles = set(TaskParticipant.objects.filter(task=task).values_list('expert__name', 'role'))
        self.assertEqual(roles, {('解忧', 'lead'), ('安界', 'collaborator')})
        self.assert_error(self.post('app/inbox/items/%s/hand-off' % self.item.public_id,
                                    {'client_request_id': rid()}), 409, 'STATE_CONFLICT')

    def test_hand_off_explicit_lead_and_validation(self):
        give_credits(self.business)
        self.assert_error(self.post('app/inbox/items/%s/hand-off' % self.pending.public_id,
                                    {'client_request_id': rid()}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post('app/inbox/items/%s/hand-off' % self.pending.public_id, {}), 422,
                          'INVALID_REQUEST')
        body = self.ok(self.post('app/inbox/items/%s/hand-off' % self.pending.public_id,
                                 {'client_request_id': rid(), 'lead_expert_id': self.anjie.public_id,
                                  'collaborator_expert_ids': []}))
        self.assertEqual(Task.objects.get(public_id=body['task']['id']).lead_expert, self.anjie)

    def test_hand_off_requires_credits(self):
        self.assert_error(self.post('app/inbox/items/%s/hand-off' % self.item.public_id,
                                    {'client_request_id': rid()}), 409, 'INSUFFICIENT_CREDITS')
        self.item.refresh_from_db()
        self.assertEqual(self.item.group, 'todo')

    def test_accepted_task_marks_item_done(self):
        give_credits(self.business)
        body = self.ok(self.post('app/inbox/items/%s/hand-off' % self.item.public_id, {'client_request_id': rid()}))
        Task.objects.filter(public_id=body['task']['id']).update(status='accepted')
        detail = self.ok(self.get('app/inbox/items/%s' % self.item.public_id))['item']
        self.assertEqual(detail['group'], 'done')
        self.assertEqual(detail['events'][-1]['kind'], 'completed')

    def test_write_requires_account_header(self):
        self.assert_error(self.post('app/inbox/items/%s/ignore' % self.item.public_id, account=False), 409,
                          'ACCOUNT_CHANGED')


class InboxSourceTests(AppTestCase):
    def setUp(self):
        self.jieyou = make_expert('解忧')
        self.source = make_source(self.business, self.lillian, kind='feishu', watched_groups=['#德国站运营'],
                                  keywords=['差评'])
        self.login(self.lillian, self.business)

    def test_list_and_detail(self):
        items = self.ok(self.get('app/inbox/sources'))['items']
        self.assertEqual([s['id'] for s in items], [self.source.public_id])
        self.assertEqual(items[0]['connection_state'], 'connected')
        today = timezone.localdate()
        InboxSourceDailyStat.objects.create(account=self.business, source=self.source, date=today, received=10,
                                            after_rules=8, after_expert=3, admitted=2)
        InboxSourceDailyStat.objects.create(account=self.business, source=self.source,
                                            date=today - timedelta(days=9), received=100)
        detail = self.ok(self.get('app/inbox/sources/%s' % self.source.public_id))['source']
        self.assertEqual(detail['funnel_7d'], {'received': 10, 'after_rules': 8, 'after_expert': 3, 'admitted': 2})
        self.assertEqual(detail['watched_groups'], ['#德国站运营'])
        self.assertIn('version', detail)

    def test_patch_with_version(self):
        version = self.ok(self.get('app/inbox/sources/%s' % self.source.public_id))['source']['version']
        body = self.ok(self.patch('app/inbox/sources/%s' % self.source.public_id, {
            'version': version, 'keywords': ['差评', '退货'], 'initiative': 'draft_suggestion',
            'responsible_expert_ids': [self.jieyou.public_id]}))['source']
        self.assertEqual(body['keywords'], ['差评', '退货'])
        self.assertEqual(body['initiative'], 'draft_suggestion')
        self.assertEqual(body['responsible_experts'][0]['name'], '解忧')
        self.assert_error(self.patch('app/inbox/sources/%s' % self.source.public_id,
                                     {'version': version, 'keywords': ['x']}), 409, 'VERSION_CONFLICT')

    def test_version_column_increments(self):
        path = 'app/inbox/sources/%s' % self.source.public_id
        self.assertEqual(self.ok(self.get(path))['source']['version'], 1)
        body = self.ok(self.patch(path, {'version': 1, 'keywords': ['退货']}))['source']
        self.assertEqual(body['version'], 2)
        self.source.refresh_from_db()
        self.assertEqual(self.source.version, 2)
        self.assert_error(self.patch(path, {'version': 1, 'keywords': ['x']}), 409, 'VERSION_CONFLICT')

    def test_patch_validation(self):
        version = self.ok(self.get('app/inbox/sources/%s' % self.source.public_id))['source']['version']
        path = 'app/inbox/sources/%s' % self.source.public_id
        self.assert_error(self.patch(path, {'keywords': []}), 422, 'INVALID_REQUEST')
        self.assert_error(self.patch(path, {'version': version, 'initiative': 'auto'}), 422, 'VALIDATION_FAILED')
        experts = [make_expert('专家%d' % i).public_id for i in range(4)]
        self.assert_error(self.patch(path, {'version': version, 'responsible_expert_ids': experts}), 422,
                          'VALIDATION_FAILED')

    def test_disconnect_keeps_items_and_blocks_patch(self):
        make_item(self.source, '飞书消息')
        body = self.ok(self.post('app/inbox/sources/%s/disconnect' % self.source.public_id))
        self.assertIsNotNone(body['source']['disconnected_at'])
        self.source.connection.refresh_from_db()
        self.assertEqual((self.source.connection.state, self.source.connection.revoked_reason), ('revoked', 'user'))
        self.assertEqual(InboxItem.objects.filter(source=self.source).count(), 1)
        self.assert_error(self.post('app/inbox/sources/%s/disconnect' % self.source.public_id), 409, 'STATE_CONFLICT')
        version = self.ok(self.get('app/inbox/sources/%s' % self.source.public_id))['source']['version']
        self.assert_error(self.patch('app/inbox/sources/%s' % self.source.public_id,
                                     {'version': version, 'keywords': ['x']}), 409, 'CONNECTION_REQUIRED')

    def test_other_member_cannot_see_source(self):
        self.login(self.mark, self.business)
        self.assertEqual(self.ok(self.get('app/inbox/sources'))['items'], [])
        self.assert_error(self.get('app/inbox/sources/%s' % self.source.public_id), 404, 'NOT_FOUND')


class FakeAdapter(service.SourceAdapter):
    def __init__(self, messages):
        self.messages = messages

    def fetch(self, source, since):
        return self.messages


class FakeJudge(service.Judge):
    def __init__(self, expert, workspace):
        self.expert, self.workspace = expert, workspace

    def judge(self, source, message):
        if '无关' in message.text:
            return service.Judgment(admit=False)
        confidence = 90 if '差评' in message.text else 40
        return service.Judgment(admit=True, confidence=confidence, rationale='命中关键词', experts=[self.expert],
                                workspace=self.workspace)


class PullPipelineTests(AppTestCase):
    def setUp(self):
        self.expert = make_expert('解忧')
        self.workspace = make_workspace(self.business, self.lillian, [(self.lillian, 'owner')])
        self.source = make_source(self.business, self.lillian, kind='feishu', keywords=['差评', '报价'])
        now = timezone.now()
        msg = service.RawMessage
        self.messages = [
            msg('m1', now, '差评预警', '新差评：插头过热'),
            msg('m2', now, '报价', '报价单（无关）'),
            msg('m3', now, '报价', '供应商报价单'),
            msg('m4', now, '闲聊', '今天天气不错'),
            msg('m5', now, '差评', '机器人转发差评', sender_is_bot=True),
        ]
        service.register_adapter('feishu', FakeAdapter(self.messages))
        self.previous_judge = service.set_judge(FakeJudge(self.expert, self.workspace))

    def tearDown(self):
        service.unregister_adapter('feishu')
        service.set_judge(self.previous_judge)

    def test_pull_filters_judges_and_counts(self):
        stats = service.pull_source(self.source)
        self.assertEqual(stats, {'received': 5, 'after_rules': 3, 'after_expert': 2, 'admitted': 2})
        groups = dict(InboxItem.objects.filter(source=self.source).values_list('external_ref', 'group'))
        self.assertEqual(groups, {'m1': 'todo', 'm3': 'pending_confirmation'})
        item = InboxItem.objects.get(external_ref='m1')
        self.assertEqual(item.workspace, self.workspace)
        self.assertIn('归入工作区“德国市场进入”', item.events.get().text)
        again = service.pull_source(self.source)
        self.assertEqual(again['admitted'], 0)
        stat = InboxSourceDailyStat.objects.get(source=self.source)
        self.assertEqual((stat.received, stat.admitted), (10, 2))

    def test_disconnected_source_is_not_pulled(self):
        self.source.disconnected_at = timezone.now()
        self.source.save()
        with self.assertRaisesMessage(ValueError, 'CONNECTION_REQUIRED'):
            service.pull_source(self.source)
        self.assertEqual(service.pull_all(), [])

    def test_default_adapter_reports_unavailable(self):
        service.unregister_adapter('feishu')
        results = service.pull_all()
        self.assertEqual(results[0][2], 'SOURCE_UNAVAILABLE')


class DecisionTests(AppTestCase):
    def setUp(self):
        self.expert = make_expert('望野')
        self.extension = make_extension('Jungle Scout')
        self.workspace = make_workspace(self.business, self.lillian, [(self.lillian, 'owner')])
        self.task = make_task(self.business, self.member(self.lillian), workspace=self.workspace,
                              lead=make_expert('觅位'))
        self.auth_request = AuthorizationRequest.objects.create(
            account=self.business, task=self.task, requested_by_expert=self.expert, extension=self.extension,
            action_kind='read_query', scope_text='读取竞品销量', validity='this_request', estimated_credits=12,
            estimate_state='estimated')
        self.question = KickoffQuestion.objects.create(account=self.business, task=self.task, order=1,
                                                       question='需要确认目标价格带')
        other = make_task(self.business, self.member(self.chen), title='首页文案 A/B 方案')
        self.invite = TaskParticipant.objects.create(account=self.business, task=other, kind='human',
                                                     user=self.lillian, role='collaborator', state='invited',
                                                     invited_by=self.chen)
        self.login(self.lillian, self.business)

    def test_decisions_list_and_badge(self):
        body = self.ok(self.get('app/inbox/decisions'))
        types = sorted(entry['type'] for entry in body['items'])
        self.assertEqual(types, ['approve', 'invite', 'supply'])
        approve = next(e for e in body['items'] if e['type'] == 'approve')
        self.assertEqual(approve['authorization']['estimated_credits'], 12)
        self.assertEqual(approve['authorization']['charge_target']['kind'], 'member_share')
        self.assertEqual(approve['authorization']['charge_target']['label'], '计入企业额度')
        self.assertIn('version', approve['authorization'])
        self.assertEqual(approve['title'], 'Jungle Scout 读取竞品销量')
        invite = next(e for e in body['items'] if e['type'] == 'invite')
        self.assertEqual(invite['title'], 'Chen Yu 邀请你加入任务')
        self.assertEqual(invite['actor']['initials'], 'CY')
        self.assertEqual(body['counts']['decisions'], 3)
        self.assertEqual(body['badge_count'], body['counts']['all'])

    def test_badge_counts_external_items_too(self):
        make_item(make_source(self.business, self.lillian), '报价单', group='todo')
        body = self.ok(self.get('app/inbox/decisions', {'count_only': '1'}))
        self.assertNotIn('items', body)
        self.assertEqual(body['counts']['external'], 1)
        self.assertEqual(body['badge_count'], 4)

    def test_type_filter_and_validation(self):
        body = self.ok(self.get('app/inbox/decisions', {'type': 'supply'}))
        self.assertEqual([e['id'] for e in body['items']], [self.question.public_id])
        self.assert_error(self.get('app/inbox/decisions', {'type': 'other'}), 422, 'INVALID_REQUEST')
        self.assert_error(self.get('app/inbox/decisions', {'count_only': 'yes'}), 422, 'INVALID_REQUEST')

    def test_answered_and_decided_entries_disappear(self):
        self.question.answer = '30-50 欧元'
        self.question.save()
        self.auth_request.decision = 'approved'
        self.auth_request.save()
        body = self.ok(self.get('app/inbox/decisions'))
        self.assertEqual([e['type'] for e in body['items']], ['invite'])

    def test_other_member_sees_own_decisions_only(self):
        self.login(self.mark, self.business)
        self.assertEqual(self.ok(self.get('app/inbox/decisions'))['total'], 0)


class HandoverTests(AppTestCase):
    def setUp(self):
        self.lead_member = self.member(self.lillian)
        self.removed = self.member(self.mark)
        self.removal = MemberRemoval.objects.create(account=self.business, membership=self.removed,
                                                    actor=self.lillian, request_key=rid(),
                                                    successor_membership=self.lead_member)
        self.task = make_task(self.business, self.removed, title='欧盟包装法规对照表', status='paused',
                              pause_reason='handoff')
        self.task_item = HandoffItem.objects.create(account=self.business, removal=self.removal,
                                                    successor=self.lead_member, kind='task', target_type='task',
                                                    target_id=self.task.public_id)
        self.workspace = make_workspace(self.business, self.lillian, [(self.lillian, 'owner')])
        self.automation = make_automation(self.business, self.removed, self.workspace, make_expert('衡价'),
                                          state='paused')
        self.auto_item = HandoffItem.objects.create(account=self.business, removal=self.removal,
                                                    successor=self.lead_member, kind='automation',
                                                    target_type='automation', target_id=self.automation.public_id)
        self.login(self.lillian, self.business)

    def test_groups(self):
        groups = self.ok(self.get('app/inbox/handover'))['groups']
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['from_member']['name'], 'Mark Weber')
        targets = {i['kind']: i['target'] for i in groups[0]['items']}
        self.assertEqual(targets['task']['title'], '欧盟包装法规对照表')
        self.assertEqual(targets['automation']['status'], 'paused')

    def test_accept_task(self):
        give_credits(self.business)
        body = self.ok(self.post('app/inbox/handover/%s/accept' % self.task_item.public_id,
                                 {'client_request_id': rid()}))
        self.assertEqual(body['item']['state'], 'accepted')
        self.assertEqual(body['task']['status'], 'running')
        self.task.refresh_from_db()
        self.assertEqual(self.task.owner, self.lead_member)
        self.assert_error(self.post('app/inbox/handover/%s/accept' % self.task_item.public_id,
                                    {'client_request_id': rid()}), 409, 'STATE_CONFLICT')

    def test_accept_wrong_kind_conflicts(self):
        self.assert_error(self.post('app/inbox/handover/%s/accept' % self.auto_item.public_id,
                                    {'client_request_id': rid()}), 409, 'STATE_CONFLICT')

    def test_reactivate_automation(self):
        body = self.ok(self.post('app/inbox/handover/%s/reactivate' % self.auto_item.public_id,
                                 {'client_request_id': rid()}))
        self.assertEqual(body['automation']['state'], 'enabled')
        self.automation.refresh_from_db()
        self.assertEqual(self.automation.owner, self.lead_member)
        self.assertIsNotNone(self.automation.next_run_at)

    def test_resolved_groups_disappear(self):
        HandoffItem.objects.filter(removal=self.removal).update(state='dismissed')
        self.assertEqual(self.ok(self.get('app/inbox/handover'))['groups'], [])

    def test_non_successor_sees_nothing(self):
        self.login(self.chen, self.business)
        self.assertEqual(self.ok(self.get('app/inbox/handover'))['groups'], [])
        self.assert_error(self.post('app/inbox/handover/%s/accept' % self.task_item.public_id,
                                    {'client_request_id': rid()}), 404, 'NOT_FOUND')
