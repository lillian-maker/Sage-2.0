"""Contract tests for app/tasks (backend-plan 2.1–2.3; entity catalog 3.2 task state machine)."""
from datetime import timedelta
from unittest import mock

from django.utils import timezone

from backend.commerce.models import CreditBatch, CreditEntry, Reservation, SharedTask
from backend.workbench.models import (AcceptanceCriterion, Artifact, ArtifactVersion, AuthorizationRequest, Expert,
                                      Extension, ExtensionInstall, KickoffQuestion, KnowledgeBase, KnowledgeDocument,
                                      Task, TaskDelivery, TaskMessage, TaskParticipant, TaskStep, TaskThread,
                                      TaskUserState, Workspace, WorkspaceMember, WorkspaceRuleVersion, AppNotification)
from backend.workbench.services import tasks as svc
from backend.workbench.tests.base import AppTestCase


def make_expert(name, specialty, **extra):
    return Expert.objects.create(source='system', group='role', name=name, title=specialty, specialty=specialty,
                                 responsibilities='负责%s' % specialty, version_label='v1', status='published',
                                 share_scope='account', communication_style='concise',
                                 uncertainty_policy='ask_first', avatar_ref='avatars/%s.webp' % name, **extra)


class TaskWorld(AppTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.wangye = make_expert('望野', '市场竞争与机会研究')
        cls.anjie = make_expert('安界', '产品合规与隐私')
        cls.workspace = Workspace.objects.create(account=cls.business, name='德国市场进入', created_from='blank',
                                                 created_by=cls.lillian)
        for user, role in ((cls.lillian, 'owner'), (cls.chen, 'editor'), (cls.mark, 'viewer')):
            WorkspaceMember.objects.create(account=cls.business, workspace=cls.workspace, user=user, role=role,
                                           state='active')
        cls.rule = WorkspaceRuleVersion.objects.create(account=cls.business, workspace=cls.workspace, number=3,
                                                       rules=['竞品数据只用近 90 天'], author=cls.lillian,
                                                       change_note='v3')
        cls.workspace.current_rule_version = cls.rule
        cls.workspace.save()
        cls.kb = KnowledgeBase.objects.create(account=cls.business, name='竞品研究资料', scope='account',
                                              owner=cls.lillian, cite_audience='account_all')
        kb = cls.kb
        cls.doc = KnowledgeDocument.objects.create(account=cls.business, knowledge_base=kb, title='竞品清单.xlsx',
                                                   source_kind='upload', content_ref='mem:1', index_state='indexed')

    def make_task(self, status='running', owner=None, workspace=True, **extra):
        owner_member = self.member(owner or self.lillian)
        task = Task.objects.create(account=self.business, workspace=self.workspace if workspace else None,
                                   title=extra.pop('title', '德国站竞品格局调研'), creator=owner_member,
                                   owner=owner_member, model_mode='auto', status=status, lead_expert=self.wangye,
                                   **extra)
        TaskParticipant.objects.create(account=self.business, task=task, kind='human', user=owner_member.user,
                                       role='lead', state='active')
        TaskParticipant.objects.create(account=self.business, task=task, kind='expert', expert=self.wangye,
                                       role='lead', state='active')
        TaskParticipant.objects.create(account=self.business, task=task, kind='expert', expert=self.anjie,
                                       role='collaborator', state='active')
        thread = TaskThread.objects.create(account=self.business, task=task, kind='main')
        now = timezone.now()
        first = TaskMessage.objects.create(account=self.business, task=task, thread=thread, author_kind='user',
                                           author_user=owner_member.user, kind='text', body='帮我评估家用小家电进入德国市场的机会',
                                           sent_at=now - timedelta(minutes=12))
        first.attached_documents.add(self.doc)
        reply = TaskMessage.objects.create(account=self.business, task=task, thread=thread, author_kind='expert',
                                           author_expert=self.wangye, kind='text', body='我把任务拆成 5 步，前 3 步已完成。',
                                           sent_at=now - timedelta(minutes=10),
                                           run_metrics={'tool_calls': 6, 'documents_read': 24, 'artifacts_created': 2,
                                                        'elapsed_seconds': 1025})
        task.first_message, task.reply = first, reply
        return task

    def add_steps(self, task, statuses=('done', 'done', 'done', 'awaiting_user', 'queued')):
        now = timezone.now()
        rows = []
        for order, status in enumerate(statuses, start=1):
            rows.append(TaskStep.objects.create(
                account=self.business, task=task, order=order, stage='research', title='步骤 %d' % order,
                expert=self.wangye if order != 3 else self.anjie, status=status, estimated_credits=10,
                started_at=now - timedelta(minutes=30) if status != 'queued' else None,
                finished_at=now - timedelta(minutes=27) if status == 'done' else None,
                inputs=['竞品前 50 名'] if order == 4 else None))
        if len(rows) > 3:
            rows[3].depends_on.add(rows[1])
        return rows

    def make_delivery(self, task):
        artifact = Artifact.objects.create(account=self.business, workspace=self.workspace, source_task=task,
                                           title='德国站竞品格局报告', kind='document')
        version = ArtifactVersion.objects.create(account=self.business, artifact=artifact, number=2,
                                                 content_ref='mem:report')
        delivery = TaskDelivery.objects.create(account=self.business, task=task, artifact_version=version,
                                               summary='德国站竞品格局报告已完成：建议进入。')
        TaskMessage.objects.create(account=self.business, task=task, thread=svc.main_thread(task),
                                   author_kind='expert', author_expert=self.wangye, kind='delivery_card',
                                   body='已交付。', card_type='delivery', card_id=delivery.public_id)
        return delivery


class ActiveAndListTests(TaskWorld):
    def test_active_counts_items_and_filter(self):
        awaiting = self.make_task('awaiting_user')
        self.add_steps(awaiting)
        stuck = self.make_task('stuck', title='X1 德语 Listing 审核', stuck_rounds=3)
        self.make_task('running', title='Amazon.de 关键词与类目机会')
        self.make_task('accepted', title='德国物流与 VAT 成本测算')
        self.make_task('running', title='已归档', archived_at=timezone.now())
        self.make_task('running', owner=self.chen, title='别人的任务')  # visible via workspace, not mine
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/tasks/active'))
        self.assertEqual(body['counts'], {'all': 3, 'stuck': 1, 'awaiting_user': 1})
        self.assertEqual(body['total'], 3)
        row = next(item for item in body['items'] if item['id'] == awaiting.public_id)
        self.assertEqual(row['progress'], {'done': 3, 'total': 5})
        self.assertEqual(row['current_step']['order'], 4)
        self.assertEqual(row['workspace'], {'id': self.workspace.public_id, 'name': '德国市场进入'})
        self.assertEqual([e['name'] for e in row['experts']], ['望野', '安界'])
        self.assertIn('avatar_ref', row['experts'][0])
        self.assertIsNone(row['source_automation'])
        self.assertIn('last_activity_at', row)
        stuck_only = self.ok(self.get('app/tasks/active', {'filter': 'stuck'}))
        self.assertEqual([item['id'] for item in stuck_only['items']], [stuck.public_id])
        self.assertEqual(stuck_only['items'][0]['stuck_rounds'], 3)
        self.assertEqual(stuck_only['counts']['all'], 3)
        self.assert_error(self.get('app/tasks/active', {'filter': 'nope'}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.get('app/tasks/active', {'bogus': '1'}), 422, 'INVALID_REQUEST')

    def test_non_member_is_404(self):
        self.login(self.anna)
        self.client.session['active_account_id'] = str(self.business.pk)
        session = self.client.session
        session['active_account_id'] = str(self.business.pk)
        session.save()
        self.assert_error(self.get('app/tasks/active'), 404, 'NOT_FOUND')

    def test_list_scope_counts_and_items(self):
        own = self.make_task('awaiting_user')
        AuthorizationRequest.objects.create(account=self.business, task=own, requested_by_expert=self.wangye,
                                            extension=Extension.objects.create(kind='connector', name='卖家精灵',
                                                                               description='x',
                                                                               publisher_tier='official',
                                                                               latest_version='1'),
                                            action_kind='read_query', scope_text='前 50 名', validity='this_request')
        shared = self.make_task('running', owner=self.chen, title='首页文案 A/B 方案', workspace=False)
        TaskParticipant.objects.create(account=self.business, task=shared, kind='human', user=self.lillian,
                                       role='shared_viewer', state='active')
        accepted = self.make_task('accepted', title='德国物流与 VAT 成本测算')
        delivery = self.make_delivery(accepted)
        delivery.decision, delivery.decided_at = 'accepted', timezone.now()
        delivery.save()
        self.make_task('draft', title='北欧市场进入评估', workspace=False)
        self.make_task('accepted', title='旧任务', archived_at=timezone.now())
        self.make_task('running', owner=self.mark, title='Mark 独立任务', workspace=False)  # invisible
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/tasks', {'sort': 'updated'}))
        self.assertEqual(body['counts'], {'all': 4, 'owned': 3, 'created': 3, 'shared': 1, 'archived': 1,
                                          'needs_me': 1, 'running': 1, 'done': 1})
        self.assertEqual(body['total'], 4)
        row = next(item for item in body['items'] if item['id'] == own.public_id)
        self.assertEqual(row['lead_expert']['name'], '望野')
        self.assertEqual(row['lead_expert']['specialty'], '市场竞争与机会研究')
        self.assertEqual(row['counts']['collaborators'], 1)
        self.assertEqual(row['owner'], {'id': str(self.lillian.pk), 'display_name': 'Lillian Li', 'is_me': True})
        self.assertEqual(row['pending_authorization_count'], 1)
        self.assertFalse(row['shared_with_me'])
        shared_row = next(item for item in body['items'] if item['id'] == shared.public_id)
        self.assertTrue(shared_row['shared_with_me'])
        self.assertIsNone(shared_row['workspace'])
        accepted_row = next(item for item in body['items'] if item['id'] == accepted.public_id)
        self.assertEqual(accepted_row['accepted_version'], {'number': 2})
        archived = self.ok(self.get('app/tasks', {'scope': 'archived'}))
        self.assertEqual(archived['total'], 1)
        shared_only = self.ok(self.get('app/tasks', {'scope': 'shared'}))
        self.assertEqual([item['id'] for item in shared_only['items']], [shared.public_id])
        none_ws = self.ok(self.get('app/tasks', {'workspace': 'none'}))
        self.assertEqual(none_ws['counts']['all'], 2)
        self.assertEqual(self.ok(self.get('app/tasks', {'q': '物流'}))['total'], 1)
        self.assertEqual(self.ok(self.get('app/tasks', {'expert': self.anjie.public_id}))['total'], 4)
        self.assertEqual(self.ok(self.get('app/tasks', {'status': 'draft'}))['total'], 1)
        self.assert_error(self.get('app/tasks', {'scope': 'mine'}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.get('app/tasks', {'status': 'weird'}), 422, 'VALIDATION_FAILED')

    def test_workspace_list_uses_workspace_visibility_and_groups(self):
        self.make_task('awaiting_user')
        self.make_task('delivered', title='德语 Listing 本地化样稿')
        self.make_task('running', owner=self.chen, title='德国 EPR 与 WEEE 合规清单')
        self.make_task('accepted', title='德国物流与 VAT 成本测算')
        self.make_task('draft', title='进入方案汇总')
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/tasks', {'workspace': self.workspace.public_id, 'sort': 'updated'}))
        self.assertEqual(body['counts']['all'], 5)
        self.assertEqual(body['counts']['needs_me'], 2)
        self.assertEqual(body['counts']['running'], 1)
        self.assertEqual(body['counts']['done'], 1)
        group = self.ok(self.get('app/tasks', {'workspace': self.workspace.public_id, 'status_group': 'needs_me'}))
        self.assertEqual(group['total'], 2)
        self.assert_error(self.get('app/tasks', {'workspace': '01ARZ3NDEKTSV4RRFFQ69G5FAV'}), 404, 'NOT_FOUND')


class CreateAndDetailTests(TaskWorld):
    def test_create_task_persists_awaiting_kickoff(self):
        self.login(self.lillian, self.business)
        skill = Extension.objects.create(kind='skill', name='竞品分析', description='x', publisher_tier='official',
                                         latest_version='1')
        ExtensionInstall.objects.create(account=self.business, extension=skill, installed_version='1',
                                        installed_by=self.lillian)
        body = self.ok(self.post('app/tasks', {
            'body': '帮我评估家用小家电进入德国市场的机会', 'attached_document_ids': [self.doc.public_id],
            'mentioned_expert_ids': [self.anjie.public_id], 'requested_skill_ids': [skill.public_id],
            'model_mode': 'auto', 'workspace_id': self.workspace.public_id}), 201)
        self.assertEqual(body['task']['status'], 'awaiting_kickoff')
        self.assertIn('complexity', body['task'])
        task = Task.objects.get(public_id=body['task']['id'])
        self.assertEqual(task.workspace, self.workspace)
        self.assertEqual(task.owner, self.member(self.lillian))
        self.assertTrue(task.participants.filter(expert=self.anjie, role='collaborator').exists())
        message = TaskMessage.objects.get(public_id=body['message']['id'])
        self.assertEqual(list(message.attached_documents.all()), [self.doc])
        self.assertEqual(list(message.requested_skills.all()), [skill])

    def test_create_with_attached_knowledge_base_expands_documents(self):
        self.login(self.lillian, self.business)
        extra = KnowledgeDocument.objects.create(account=self.business, knowledge_base=self.kb,
                                                 title='德国 EPR 法规摘要.pdf', source_kind='upload',
                                                 content_ref='mem:2', index_state='indexed')
        body = self.ok(self.post('app/tasks', {'body': '整库研究',
                                               'attached_knowledge_base_ids': [self.kb.public_id]}), 201)
        message = TaskMessage.objects.get(public_id=body['message']['id'])
        self.assertEqual({doc.pk for doc in message.attached_documents.all()}, {self.doc.pk, extra.pk})
        self.assert_error(
            self.post('app/tasks', {'body': 'x', 'attached_knowledge_base_ids': ['01ARZ3NDEKTSV4RRFFQ69G5FAV']}),
            404, 'NOT_FOUND')

    def test_create_validation_and_not_found(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.post('app/tasks', {'body': '   ', 'model_mode': 'auto'}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post('app/tasks', {'body': 'x', 'model_mode': 'fast'}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post('app/tasks', {'body': 'x', 'mentioned_expert_ids': ['01ARZ3NDEKTSV4RRFFQ69G5FAV']}),
                          404, 'NOT_FOUND')
        self.assert_error(self.post('app/tasks', {'body': 'x', 'attached_document_ids': ['01ARZ3NDEKTSV4RRFFQ69G5FAV']}),
                          404, 'NOT_FOUND')
        self.assert_error(self.post('app/tasks', {'body': 'x', 'unknown': 1}), 422, 'INVALID_REQUEST')
        self.login(self.mark, self.business)
        self.assert_error(self.post('app/tasks', {'body': 'x', 'workspace_id': self.workspace.public_id}), 403,
                          'PERMISSION_DENIED')

    def test_detail_fields(self):
        task = self.make_task('paused', pause_reason='quota_exhausted')
        self.add_steps(task, ('done', 'done', 'done', 'queued', 'queued'))
        TaskParticipant.objects.create(account=self.business, task=task, kind='human', user=self.chen,
                                       role='collaborator', state='active')
        Artifact.objects.create(account=self.business, source_task=task, title='报告', kind='document')
        task.suggestions.create(account=self.business, text='测算头程 + FBA 费用')
        task.suggestions.create(account=self.business, text='查看前 50 名竞品的月销量', requires_authorization=True)
        task.suggestions.create(account=self.business, text='已关闭', state='dismissed')
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/tasks/%s' % task.public_id))
        self.assertEqual(body['title'], '德国站竞品格局调研')
        self.assertEqual(body['counts'], {'artifacts': 1, 'materials': 1, 'participants': 4})
        self.assertEqual(body['progress'], {'done': 3, 'total': 5})
        self.assertEqual(body['replan_count'], 0)
        self.assertEqual(body['used_credits'], 0)
        self.assertEqual(body['pause']['completed_steps'], 3)
        self.assertEqual(body['pause']['resume_step'], 4)
        self.assertTrue(body['pause']['resume_available'])
        self.assertIn('department_name', body['pause'])
        self.assertIn('member_share', body['pause'])
        experts = [row['expert'] for row in body['participants'] if row['kind'] == 'expert']
        self.assertTrue(all('avatar_ref' in expert for expert in experts))
        self.assertEqual([row['text'] for row in body['suggestions']], ['测算头程 + FBA 费用', '查看前 50 名竞品的月销量'])
        self.assertTrue(body['suggestions'][1]['requires_authorization'])
        self.assertIsNone(body['source_automation_run'])
        self.assertIsNone(body['source_inbox_item'])

    def test_detail_used_credits_and_replan(self):
        task = self.make_task('running')
        steps = self.add_steps(task)
        TaskStep.objects.create(account=self.business, task=task, order=1, stage='research', title='新计划',
                                plan_revision=2)
        shared = SharedTask.objects.create(account=self.business, membership=self.member(self.lillian),
                                           task_key=task.public_id, task_type='workbench_task')
        task.shared_task = shared
        task.save()
        reservation = Reservation.objects.create(account=self.business, task_key='actual:1', membership=shared.membership,
                                                 shared_task=shared, quantity='62', settled_quantity='62',
                                                 state='settled')
        from backend.commerce.models import TaskCostEvent
        TaskCostEvent.objects.create(account=self.business, reservation=reservation, source='executor',
                                     event_key='e1', payload={}, request_hash='h', step_key=steps[1].public_id)
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/tasks/%s' % task.public_id))
        self.assertEqual(body['used_credits'], 62)
        self.assertEqual(body['replan_count'], 1)
        self.assertEqual(body['progress'], {'done': 0, 'total': 1})

    def test_detail_hidden_for_outsiders(self):
        task = self.make_task('running', owner=self.mark, workspace=False)
        self.login(self.chen, self.business)
        self.assert_error(self.get('app/tasks/%s' % task.public_id), 404, 'NOT_FOUND')

    def test_patch_owner_only(self):
        task = self.make_task('running')
        self.login(self.chen, self.business)
        self.assert_error(self.patch('app/tasks/%s' % task.public_id, {'title': '新标题'}), 403, 'PERMISSION_DENIED')
        self.login(self.lillian, self.business)
        body = self.ok(self.patch('app/tasks/%s' % task.public_id, {'title': '新标题', 'model_mode': 'deep',
                                                                     'workspace_id': None}))
        self.assertEqual(body['task']['title'], '新标题')
        self.assertEqual(body['task']['model_mode'], 'deep')
        self.assertIsNone(body['task']['workspace'])
        self.assert_error(self.patch('app/tasks/%s' % task.public_id, {'title': ''}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.patch('app/tasks/%s' % task.public_id,
                                     {'workspace_id': '01ARZ3NDEKTSV4RRFFQ69G5FAV'}), 404, 'NOT_FOUND')

    def test_fork_references_source(self):
        task = self.make_task('running')
        self.login(self.lillian, self.business)
        body = self.ok(self.post('app/tasks/%s/fork' % task.public_id,
                                 {'message_id': task.reply.public_id, 'body': '换成法国市场再看一遍'}), 201)
        self.assertEqual(body['task']['forked_from_task'], task.public_id)
        self.assertEqual(body['task']['forked_from_message'], task.reply.public_id)
        new = Task.objects.get(public_id=body['task']['id'])
        self.assertEqual(new.workspace, self.workspace)
        self.assertEqual(new.messages.count(), 1)  # Q-A-12 A: referenced, not copied
        self.assertEqual(task.status, Task.objects.get(pk=task.pk).status)
        self.assert_error(self.post('app/tasks/%s/fork' % task.public_id,
                                    {'message_id': '01ARZ3NDEKTSV4RRFFQ69G5FAV', 'body': 'x'}), 404, 'NOT_FOUND')
        self.assert_error(self.post('app/tasks/%s/fork' % task.public_id,
                                    {'message_id': task.reply.public_id, 'body': ''}), 422, 'VALIDATION_FAILED')
        via_create = self.ok(self.post('app/tasks', {'body': 'fork', 'forked_from_message_id': task.reply.public_id}),
                             201)
        self.assertEqual(via_create['task']['forked_from_task'], task.public_id)


class MessageTests(TaskWorld):
    def test_messages_fields_order_and_threads(self):
        task = self.make_task('running')
        task.reply.citations.create(account=self.business, task=task, external_label='Amazon.de 类目页 · 11月3日')
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/tasks/%s/messages' % task.public_id, {'thread': 'main'}))
        self.assertEqual(body['total'], 2)
        first, reply = body['items']
        self.assertEqual(first['author_kind'], 'user')
        self.assertEqual(first['author_role'], 'owner')
        self.assertEqual(first['attachments'], [{'id': self.doc.public_id, 'title': '竞品清单.xlsx',
                                                 'index_state': 'indexed'}])
        self.assertEqual(reply['author_expert']['specialty'], '市场竞争与机会研究')
        self.assertEqual(reply['author_role'], 'lead')
        self.assertEqual(reply['citations'][0]['label'], 'Amazon.de 类目页 · 11月3日')
        self.assertEqual(reply['run_metrics'], {'tool_calls': 6, 'documents_read': 24, 'artifacts_created': 2,
                                                'elapsed_seconds': 1025})
        self.assertIsNone(reply['branch'])
        self.assertFalse(reply['is_important'])
        self.assertLess(first['sent_at'], reply['sent_at'])

    def test_post_message_main_and_side(self):
        task = self.make_task('running')
        self.login(self.lillian, self.business)
        body = self.ok(self.post('app/tasks/%s/messages' % task.public_id, {'body': '补充：也看看 WEEE'}), 201)
        self.assertEqual(body['message']['thread'], 'main')
        self.assertEqual(body['message']['body'], '补充：也看看 WEEE')
        thread = self.ok(self.post('app/tasks/%s/threads' % task.public_id,
                                   {'anchor_message_id': task.reply.public_id}), 201)['thread']
        side = self.ok(self.post('app/tasks/%s/messages' % task.public_id,
                                 {'body': 'LUCID 登记需要多久？', 'thread': thread['id']}), 201)
        self.assertEqual(side['message']['thread'], thread['id'])
        self.assert_error(self.post('app/tasks/%s/messages' % task.public_id, {'body': ''}), 422, 'VALIDATION_FAILED')
        self.login(self.mark, self.business)  # workspace viewer: read only
        self.assert_error(self.post('app/tasks/%s/messages' % task.public_id, {'body': 'x'}), 403,
                          'PERMISSION_DENIED')
        self.ok(self.get('app/tasks/%s/messages' % task.public_id))

    def test_mark_important(self):
        task = self.make_task('running')
        self.login(self.lillian, self.business)
        path = 'app/tasks/%s/messages/%s' % (task.public_id, task.reply.public_id)
        self.assertEqual(self.ok(self.patch(path, {'is_important': True})),
                         {'message': {'id': task.reply.public_id, 'is_important': True}})
        self.assert_error(self.patch(path, {'is_important': 'yes'}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.patch('app/tasks/%s/messages/01ARZ3NDEKTSV4RRFFQ69G5FAV' % task.public_id,
                                     {'is_important': True}), 404, 'NOT_FOUND')
        self.login(self.mark, self.business)
        self.assert_error(self.patch(path, {'is_important': False}), 403, 'PERMISSION_DENIED')

    def test_edit_creates_branch(self):
        task = self.make_task('awaiting_user')
        self.login(self.lillian, self.business)
        path = 'app/tasks/%s/messages/%s/edit' % (task.public_id, task.first_message.public_id)
        body = self.ok(self.post(path, {'body': '只看 Amazon.de', 'client_request_id': 'edit-1'}), 201)
        self.assertEqual(body['message']['parent_message'], task.first_message.public_id)
        self.assertEqual(body['message']['branch_index'], 2)
        self.assertEqual(body['task']['status'], 'running')
        replay = self.ok(self.post(path, {'body': '只看 Amazon.de', 'client_request_id': 'edit-1'}), 201)
        self.assertEqual(replay['message']['id'], body['message']['id'])
        latest = self.ok(self.get('app/tasks/%s/messages' % task.public_id))
        self.assertEqual([item['body'] for item in latest['items']], ['只看 Amazon.de'])
        self.assertEqual(latest['items'][0]['branch'], {'index': 2, 'count': 2})
        original = self.ok(self.get('app/tasks/%s/messages' % task.public_id, {'branch': '1'}))
        self.assertEqual(len(original['items']), 2)
        self.assertEqual(original['items'][0]['branch'], {'index': 1, 'count': 2})
        # The original is no longer the latest user message.
        self.assert_error(self.post(path, {'body': '再改', 'client_request_id': 'edit-2'}), 409, 'STATE_CONFLICT')
        self.assert_error(self.post(path, {'body': '', 'client_request_id': 'edit-3'}), 422, 'VALIDATION_FAILED')

    def test_edit_terminal_task_conflicts(self):
        task = self.make_task('accepted')
        self.login(self.lillian, self.business)
        self.assert_error(self.post('app/tasks/%s/messages/%s/edit' % (task.public_id, task.first_message.public_id),
                                    {'body': 'x', 'client_request_id': 'e'}), 409, 'STATE_CONFLICT')

    def test_side_thread_list_and_bring_back(self):
        task = self.make_task('running')
        other = self.make_task('running', title='其他任务')
        self.login(self.lillian, self.business)
        path = 'app/tasks/%s/threads' % task.public_id
        first = self.ok(self.post(path, {'anchor_message_id': task.reply.public_id}), 201)['thread']
        self.assertEqual(first['kind'], 'side')
        self.assertEqual(first['expert']['name'], '望野')
        again = self.ok(self.post(path, {'anchor_message_id': task.reply.public_id}), 200)['thread']
        self.assertEqual(again['id'], first['id'])
        self.assert_error(self.post(path, {'anchor_message_id': other.reply.public_id}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post(path, {'anchor_message_id': '01ARZ3NDEKTSV4RRFFQ69G5FAV'}), 404, 'NOT_FOUND')
        thread = TaskThread.objects.get(public_id=first['id'])
        thread.expert = self.anjie
        thread.save()
        TaskMessage.objects.create(account=self.business, task=task, thread=thread, author_kind='expert',
                                   author_expert=self.anjie, kind='text',
                                   body='通常 3–5 个工作日。WEEE 注册号需要 4–6 周。')
        listing = self.ok(self.get(path, {'kind': 'side'}))
        row = listing['items'][0]
        self.assertEqual(row['message_count'], 1)
        self.assertEqual(row['expert']['name'], '安界')
        self.assertEqual(row['anchor']['author_name'], '望野')
        self.assertTrue(row['anchor']['excerpt'].startswith('我把任务拆成'))
        self.assertIn('sent_at', row['anchor'])
        back = self.ok(self.post('%s/%s/bring-back' % (path, first['id']), {'client_request_id': 'b1'}), 201)
        self.assertEqual(back['message']['thread'], 'main')
        self.assertIn('WEEE', back['message']['body'])
        self.assertEqual(back['thread']['brought_back_message'], back['message']['id'])
        replay = self.ok(self.post('%s/%s/bring-back' % (path, first['id']), {'client_request_id': 'b1'}), 201)
        self.assertEqual(replay['message']['id'], back['message']['id'])
        main = svc.main_thread(task)
        self.assert_error(self.post('%s/%s/bring-back' % (path, main.public_id), {'client_request_id': 'b2'}), 409,
                          'STATE_CONFLICT')
        side_messages = self.ok(self.get('app/tasks/%s/messages' % task.public_id, {'thread': first['id']}))
        self.assertEqual(side_messages['total'], 1)


class KickoffTests(TaskWorld):
    def kickoff_task(self):
        task = self.make_task('awaiting_kickoff', complexity='complex', estimated_expert_count=5,
                              estimated_duration_minutes=25, goal='判断家用小家电是否值得在 12 月前进入德国站',
                              scope_text='Amazon.de 小家电类目前 20 名竞品', deliverables='一页结论',
                              constraints='竞品数据只用近 90 天')
        q1 = KickoffQuestion.objects.create(account=self.business, task=task, order=1, question='重点评估哪个渠道？',
                                            options=['Amazon.de', 'Amazon.de + 独立站', '由 Sage 决定'])
        KickoffQuestion.objects.create(account=self.business, task=task, order=2, question='交付成什么形式？',
                                       options=['一页结论', '完整报告 + 数据表', '由 Sage 决定'])
        AcceptanceCriterion.objects.create(account=self.business, task=task, order=1, text='覆盖前 20 名竞品与价格带',
                                           origin='ai')
        self.add_steps(task, ('queued',) * 5)
        task.q1 = q1
        return task

    def test_kickoff_card(self):
        task = self.kickoff_task()
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/tasks/%s/kickoff' % task.public_id))
        self.assertEqual((body['complexity'], body['estimated_expert_count'], body['estimated_duration_minutes']),
                         ('complex', 5, 25))
        self.assertEqual(len(body['questions']), 2)
        self.assertEqual(body['questions'][0]['options'][0], 'Amazon.de')
        self.assertEqual(body['brief']['goal'], '判断家用小家电是否值得在 12 月前进入德国站')
        self.assertEqual(body['brief']['rule_version'], {'number': 3})
        self.assertEqual(body['criteria'][0]['text'], '覆盖前 20 名竞品与价格带')
        self.assertEqual(body['plan']['step_count'], 5)
        self.assertEqual(body['plan']['expert_count'], 2)
        self.assertEqual(body['missing_capabilities'], [])

    def test_answer_and_criteria_editing_before_start(self):
        task = self.kickoff_task()
        self.login(self.lillian, self.business)
        answer = self.ok(self.patch('app/tasks/%s/kickoff/questions/%s' % (task.public_id, task.q1.public_id),
                                    {'answer': 'Amazon.de'}))
        self.assertEqual(answer['question']['answer'], 'Amazon.de')
        created = self.ok(self.post('app/tasks/%s/criteria' % task.public_id, {'text': '数字注明来源与日期'}), 201)
        self.assertEqual(created['criterion']['origin'], 'user')
        self.assertEqual(created['criterion']['order'], 2)
        cid = created['criterion']['id']
        self.ok(self.patch('app/tasks/%s/criteria/%s' % (task.public_id, cid), {'text': '关键数字注明来源'}))
        self.ok(self.delete('app/tasks/%s/criteria/%s' % (task.public_id, cid)))
        self.assertEqual(self.ok(self.get('app/tasks/%s/criteria' % task.public_id))['total'], 1)
        for index in range(9):
            self.ok(self.post('app/tasks/%s/criteria' % task.public_id, {'text': '条目 %d' % index}), 201)
        self.assert_error(self.post('app/tasks/%s/criteria' % task.public_id, {'text': '第 11 条'}), 422,
                          'VALIDATION_FAILED')
        self.login(self.chen, self.business)
        self.assert_error(self.post('app/tasks/%s/criteria' % task.public_id, {'text': 'x'}), 403,
                          'PERMISSION_DENIED')

    def test_criteria_by_step(self):
        task = self.make_task('running')
        steps = self.add_steps(task)
        AcceptanceCriterion.objects.create(account=self.business, task=task, order=1, text='任务级', origin='ai',
                                           evidence='报告 §1')
        AcceptanceCriterion.objects.create(account=self.business, task=task, step=steps[3], order=1,
                                           text='覆盖前 50 名中至少 45 个商品', origin='ai')
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/tasks/%s/criteria' % task.public_id, {'step': 'none'}))
        self.assertEqual(body['items'][0]['evidence'], '报告 §1')
        self.assertEqual(set(body['items'][0]), {'id', 'text', 'evidence', 'verdict', 'origin', 'order', 'step_id'})
        step = self.ok(self.get('app/tasks/%s/criteria' % task.public_id, {'step': steps[3].public_id}))
        self.assertEqual(step['items'][0]['text'], '覆盖前 50 名中至少 45 个商品')
        self.assert_error(self.post('app/tasks/%s/criteria' % task.public_id, {'text': '开工后'}), 409, 'STATE_CONFLICT')

    def test_start_personal_reserves_credits(self):
        member = self.member(self.lillian, self.personal)
        task = Task.objects.create(account=self.personal, title='个人任务', creator=member, owner=member,
                                   model_mode='auto', status='awaiting_kickoff')
        TaskStep.objects.create(account=self.personal, task=task, order=1, stage='research', title='s',
                                estimated_credits=40)
        self.login(self.lillian, self.personal)
        path = 'app/tasks/%s/start' % task.public_id
        self.assert_error(self.post(path, {'client_request_id': 'start-1'}), 409, 'INSUFFICIENT_CREDITS')
        self.assertEqual(Task.objects.get(pk=task.pk).status, 'awaiting_kickoff')
        batch = CreditBatch.objects.create(account=self.personal, source='purchased', source_key='fixture',
                                           initial_quantity='100', expires_at=timezone.now() + timedelta(days=30))
        CreditEntry.objects.create(batch=batch, kind='grant', quantity='100', event_key='grant')
        body = self.ok(self.post(path, {'client_request_id': 'start-1'}))
        self.assertEqual(body['task']['status'], 'running')
        self.assertIsNone(body['shared_task_id'])
        self.assertEqual(Reservation.objects.get(account=self.personal, task_key=task.public_id).quantity, '40')
        self.assertEqual(self.ok(self.post(path, {'client_request_id': 'start-1'}))['task']['status'], 'running')

    def test_start_creates_kickoff_card_message_once(self):
        # D96: 用户创建的任务开工后保留只读开工确认卡（此前只有种子任务有 kickoff_card 消息）。
        member = self.member(self.lillian, self.personal)
        task = Task.objects.create(account=self.personal, title='个人任务', creator=member, owner=member,
                                   model_mode='auto', status='awaiting_kickoff')
        TaskStep.objects.create(account=self.personal, task=task, order=1, stage='research', title='s',
                                estimated_credits=40)
        batch = CreditBatch.objects.create(account=self.personal, source='purchased', source_key='fixture',
                                           initial_quantity='100', expires_at=timezone.now() + timedelta(days=30))
        CreditEntry.objects.create(batch=batch, kind='grant', quantity='100', event_key='grant')
        self.login(self.lillian, self.personal)
        path = 'app/tasks/%s/start' % task.public_id
        self.ok(self.post(path, {'client_request_id': 'start-1'}))
        cards = TaskMessage.objects.filter(task=task, kind='kickoff_card')
        self.assertEqual(cards.count(), 1)
        card = cards.get()
        self.assertEqual((card.card_type, card.card_id), ('kickoff', task.public_id))
        self.assertEqual(card.author_kind, 'system')  # 无主责专家时退化为系统消息
        listed = self.ok(self.get('app/tasks/%s/messages' % task.public_id))
        self.assertEqual([m['kind'] for m in listed['items']].count('kickoff_card'), 1)
        # 重复开始（幂等重放）不再补卡
        self.ok(self.post(path, {'client_request_id': 'start-1'}))
        self.assertEqual(TaskMessage.objects.filter(task=task, kind='kickoff_card').count(), 1)

    def test_start_keeps_existing_kickoff_card(self):
        member = self.member(self.lillian, self.personal)
        task = Task.objects.create(account=self.personal, title='个人任务', creator=member, owner=member,
                                   model_mode='auto', status='awaiting_kickoff')
        TaskStep.objects.create(account=self.personal, task=task, order=1, stage='research', title='s',
                                estimated_credits=40)
        batch = CreditBatch.objects.create(account=self.personal, source='purchased', source_key='fixture',
                                           initial_quantity='100', expires_at=timezone.now() + timedelta(days=30))
        CreditEntry.objects.create(batch=batch, kind='grant', quantity='100', event_key='grant')
        thread = svc.main_thread(task)
        TaskMessage.objects.create(account=self.personal, task=task, thread=thread, author_kind='system',
                                   kind='kickoff_card', body='开工前确认。', card_type='kickoff',
                                   card_id=task.public_id)
        self.login(self.lillian, self.personal)
        self.ok(self.post('app/tasks/%s/start' % task.public_id, {'client_request_id': 'start-1'}))
        card = TaskMessage.objects.get(task=task, kind='kickoff_card')
        self.assertEqual(card.body, '开工前确认。')

    def test_start_business_without_seat_is_insufficient(self):
        task = self.kickoff_task()
        self.login(self.lillian, self.business)
        self.assert_error(self.post('app/tasks/%s/start' % task.public_id, {'client_request_id': 's'}), 409,
                          'INSUFFICIENT_CREDITS')
        self.assertFalse(SharedTask.objects.filter(task_key=task.public_id).exists())

    def test_start_business_success_locks_rule_version_and_answers(self):
        task = self.kickoff_task()

        def fake_start(account, membership_id, task_key, task_type):
            shared = SharedTask.objects.create(account=account, membership_id=membership_id, task_key=task_key,
                                               task_type=task_type)
            return {'id': str(shared.pk)}

        self.login(self.lillian, self.business)
        with mock.patch('backend.commerce.billing.start_shared_task', side_effect=fake_start) as start, \
                mock.patch('backend.commerce.billing._business_task_credit_scope',
                           return_value={'can_consume': True, 'available': 100}):
            body = self.ok(self.post('app/tasks/%s/start' % task.public_id, {
                'client_request_id': 'start-b', 'answers': [{'question_id': task.q1.public_id,
                                                             'answer': 'Amazon.de'}]}))
        start.assert_called_once_with(self.business, self.member(self.lillian).pk, task.public_id, 'workbench_task')
        self.assertEqual(body['task'], {'id': task.public_id, 'status': 'running', 'rule_version': {'number': 3}})
        self.assertTrue(body['shared_task_id'])
        self.assertEqual(KickoffQuestion.objects.get(pk=task.q1.pk).answer, 'Amazon.de')
        self.assertIsNone(task.kickoff_questions.get(order=2).answer)  # Q-A-10 B: skipped
        self.assert_error(self.patch('app/tasks/%s/kickoff/questions/%s' % (task.public_id, task.q1.public_id),
                                     {'answer': 'x'}), 409, 'STATE_CONFLICT')

    def test_start_errors(self):
        task = self.kickoff_task()
        self.login(self.chen, self.business)
        self.assert_error(self.post('app/tasks/%s/start' % task.public_id, {'client_request_id': 's'}), 403,
                          'PERMISSION_DENIED')
        self.login(self.lillian, self.business)
        self.assert_error(self.post('app/tasks/%s/start' % task.public_id, {'client_request_id': ''}), 422,
                          'VALIDATION_FAILED')
        self.assert_error(self.post('app/tasks/%s/start' % task.public_id, {
            'client_request_id': 's', 'answers': [{'question_id': 'nope', 'answer': 'x'}]}), 422, 'VALIDATION_FAILED')
        delivered = self.make_task('delivered')
        self.assert_error(self.post('app/tasks/%s/start' % delivered.public_id, {'client_request_id': 's'}), 409,
                          'STATE_CONFLICT')


class StepTests(TaskWorld):
    def test_steps_graph(self):
        task = self.make_task('running')
        steps = self.add_steps(task, ('done', 'running', 'running', 'awaiting_user', 'queued'))
        steps[2].in_background = True
        steps[2].activity = '正在读取 竞品清单.xlsx 第 3 个工作表'
        steps[2].save()
        auth = AuthorizationRequest.objects.create(
            account=self.business, task=task, step=steps[3], requested_by_expert=self.wangye,
            extension=Extension.objects.create(kind='connector', name='卖家精灵', description='x',
                                               publisher_tier='official', latest_version='1'),
            action_kind='read_query', scope_text='前 50 名', validity='this_request')
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/tasks/%s/steps' % task.public_id))
        self.assertEqual(body['total'], 5)
        self.assertEqual(body['summary']['running'], 2)
        self.assertEqual(body['summary']['in_background'], 1)
        first = body['items'][0]
        self.assertEqual(first['expert']['name'], '望野')
        self.assertEqual(first['duration_seconds'], 180)
        self.assertEqual(first['used_credits'], 0)
        self.assertEqual(first['estimated_credits'], 10)
        fourth = body['items'][3]
        self.assertEqual(fourth['inputs'], ['竞品前 50 名'])
        self.assertEqual(fourth['depends_on'], [steps[1].public_id])
        self.assertEqual(fourth['authorization_id'], auth.public_id)
        self.assertEqual(body['items'][2]['activity'], '正在读取 竞品清单.xlsx 第 3 个工作表')
        running = self.ok(self.get('app/tasks/%s/steps' % task.public_id, {'status': 'running,awaiting_user'}))
        self.assertEqual(running['total'], 3)
        self.assert_error(self.get('app/tasks/%s/steps' % task.public_id, {'status': 'x'}), 422, 'VALIDATION_FAILED')


class LifecycleTests(TaskWorld):
    def test_pause_resume_stop(self):
        task = self.make_task('running')
        self.login(self.lillian, self.business)
        base = 'app/tasks/%s/' % task.public_id
        self.assertEqual(self.ok(self.post(base + 'pause', {'client_request_id': 'p'}))['task']['status'], 'paused')
        self.assertEqual(self.ok(self.post(base + 'pause', {'client_request_id': 'p'}))['task']['status'], 'paused')
        body = self.ok(self.post(base + 'resume', {'client_request_id': 'r'}))
        self.assertEqual(body['task'], {'id': task.public_id, 'status': 'running', 'pause_reason': None})
        self.assertEqual(self.ok(self.post(base + 'stop', {'client_request_id': 's'}))['task']['status'], 'stopped')
        self.assert_error(self.post(base + 'pause', {'client_request_id': 'p2'}), 409, 'STATE_CONFLICT')
        self.assert_error(self.post(base + 'resume', {'client_request_id': 'r2'}), 409, 'STATE_CONFLICT')

    def test_resume_business_calls_billing(self):
        task = self.make_task('paused', pause_reason='quota_exhausted')
        shared = SharedTask.objects.create(account=self.business, membership=self.member(self.lillian),
                                           task_key=task.public_id, task_type='workbench_task', state='paused')
        task.shared_task = shared
        task.save()
        self.login(self.lillian, self.business)
        path = 'app/tasks/%s/resume' % task.public_id
        # No paid seat in the fixture world: billing refuses the resume.
        self.assert_error(self.post(path, {'client_request_id': 'r'}), 409, 'TASK_RESUME_FORBIDDEN')
        with mock.patch('backend.commerce.billing.resume_shared_task', side_effect=ValueError('INSUFFICIENT_CREDITS')):
            self.assert_error(self.post(path, {'client_request_id': 'r'}), 409, 'INSUFFICIENT_CREDITS')
        with mock.patch('backend.commerce.billing.resume_shared_task', return_value={}) as resume:
            self.ok(self.post(path, {'client_request_id': 'r'}))
        resume.assert_called_once_with(self.business, self.lillian, shared.pk)
        self.assertEqual(Task.objects.get(pk=task.pk).status, 'running')

    def test_stop_rules(self):
        self.login(self.lillian, self.business)
        for status in ('awaiting_kickoff', 'delivered', 'accepted'):
            task = self.make_task(status)
            self.assert_error(self.post('app/tasks/%s/stop' % task.public_id, {'client_request_id': 's'}), 409,
                              'STATE_CONFLICT')
        paused = self.make_task('paused')
        self.ok(self.post('app/tasks/%s/stop' % paused.public_id, {'client_request_id': 's'}))

    def test_viewer_cannot_change_state(self):
        task = self.make_task('running')
        self.login(self.mark, self.business)
        self.assert_error(self.post('app/tasks/%s/pause' % task.public_id, {'client_request_id': 'p'}), 403,
                          'PERMISSION_DENIED')

    def test_accept_delivery(self):
        task = self.make_task('delivered')
        delivery = self.make_delivery(task)
        shared = SharedTask.objects.create(account=self.business, membership=self.member(self.lillian),
                                           task_key=task.public_id, task_type='workbench_task')
        task.shared_task = shared
        task.save()
        self.login(self.chen, self.business)
        path = 'app/tasks/%s/deliveries/%s/accept' % (task.public_id, delivery.public_id)
        self.assert_error(self.post(path, {'client_request_id': 'a'}), 403, 'PERMISSION_DENIED')
        self.login(self.lillian, self.business)
        body = self.ok(self.post(path, {'client_request_id': 'a'}))
        self.assertEqual(body['delivery']['decision'], 'accepted')
        self.assertTrue(body['delivery']['decided_at'])
        self.assertEqual(body['task']['status'], 'accepted')
        self.assertEqual(body['artifact_version'], {'id': delivery.artifact_version.public_id, 'number': 2,
                                                    'state': 'accepted'})
        self.assertEqual(SharedTask.objects.get(pk=shared.pk).state, 'completed')
        self.assertEqual(self.ok(self.post(path, {'client_request_id': 'a'}))['task']['status'], 'accepted')
        messages = self.ok(self.get('app/tasks/%s/messages' % task.public_id))['items']
        card = messages[-1]['card']
        self.assertEqual(card['artifact_version']['state'], 'accepted')
        self.assertEqual(card['artifact_version']['title'], '德国站竞品格局报告')

    def test_return_delivery(self):
        task = self.make_task('delivered')
        delivery = self.make_delivery(task)
        criterion = AcceptanceCriterion.objects.create(account=self.business, task=task, order=1, text='x',
                                                       origin='ai', verdict='met')
        self.login(self.lillian, self.business)
        path = 'app/tasks/%s/deliveries/%s/return' % (task.public_id, delivery.public_id)
        self.assert_error(self.post(path, {'reason': '', 'client_request_id': 'r'}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post(path, {'reason': 'x' * 501, 'client_request_id': 'r'}), 422, 'VALIDATION_FAILED')
        body = self.ok(self.post(path, {'reason': '缺少前 20 名的价格带', 'client_request_id': 'r',
                                        'verdicts': [{'criterion_id': criterion.public_id, 'verdict': 'unmet'}]}))
        self.assertEqual(body['delivery']['decision'], 'returned')
        self.assertEqual(body['delivery']['return_reason'], '缺少前 20 名的价格带')
        self.assertEqual(body['task']['status'], 'needs_revision')
        self.assertEqual(AcceptanceCriterion.objects.get(pk=criterion.pk).verdict, 'unmet')
        accept = 'app/tasks/%s/deliveries/%s/accept' % (task.public_id, delivery.public_id)
        self.assert_error(self.post(accept, {'client_request_id': 'a'}), 409, 'STATE_CONFLICT')
        self.assert_error(self.post('app/tasks/%s/deliveries/01ARZ3NDEKTSV4RRFFQ69G5FAV/accept' % task.public_id,
                                    {'client_request_id': 'a'}), 404, 'NOT_FOUND')

    def test_archive_pin_read(self):
        task = self.make_task('accepted')
        self.login(self.lillian, self.business)
        base = 'app/tasks/%s/' % task.public_id
        archived = self.ok(self.post(base + 'archive'))
        self.assertTrue(archived['archived_at'])
        self.assert_error(self.post(base + 'archive'), 409, 'STATE_CONFLICT')
        self.assertIsNone(self.ok(self.post(base + 'unarchive'))['archived_at'])
        self.assert_error(self.post(base + 'unarchive'), 409, 'STATE_CONFLICT')
        self.assertTrue(self.ok(self.post(base + 'pin'))['pinned_at'])
        self.assertIsNone(self.ok(self.delete(base + 'pin'))['pinned_at'])
        read = self.ok(self.post(base + 'read'))
        self.assertTrue(read['last_read_at'])
        self.assertFalse(read['marked_unread'])
        self.assertTrue(self.ok(self.delete(base + 'read'))['marked_unread'])
        self.login(self.chen, self.business)
        self.ok(self.post(base + 'pin'))  # personal state for any viewer (Q-D-19 A)
        self.assert_error(self.post(base + 'archive'), 403, 'PERMISSION_DENIED')
        self.assertEqual(TaskUserState.objects.filter(task=task).count(), 2)


class ExecutorTests(TaskWorld):
    def test_executor_step_progress_stuck_and_delivery(self):
        task = self.make_task('running')
        steps = self.add_steps(task, ('running', 'queued'))
        svc.executor_update_step(steps[0], status='done')
        self.assertTrue(AppNotification.objects.filter(recipient=self.lillian, type='step_completed').exists())
        svc.executor_update_step(steps[1], status='running', activity='正在估算竞品月销量')
        svc.executor_update_step(steps[1], status='awaiting_user')
        self.assertEqual(Task.objects.get(pk=task.pk).status, 'awaiting_user')
        with self.assertRaisesRegex(ValueError, 'STATE_CONFLICT'):
            svc.executor_update_step(steps[0], status='running')
        svc.executor_update_step(steps[1], status='running')
        svc.executor_mark_stuck(task, 3)
        self.assertEqual(Task.objects.get(pk=task.pk).stuck_rounds, 3)
        task.refresh_from_db()
        task.status = 'running'
        task.save()
        artifact = Artifact.objects.create(account=self.business, source_task=task, title='报告', kind='document')
        version = ArtifactVersion.objects.create(account=self.business, artifact=artifact, number=1, content_ref='m')
        delivery = svc.executor_deliver(task, artifact_version=version, summary='已完成', expert=self.wangye,
                                        suggestions=[('测算头程 + FBA 费用', False)])
        self.assertEqual(Task.objects.get(pk=task.pk).status, 'delivered')
        self.assertTrue(AppNotification.objects.filter(recipient=self.lillian, type='delivered').exists())
        self.assertEqual(task.messages.get(card_id=delivery.public_id).kind, 'delivery_card')

    def test_executor_quota_pause(self):
        task = self.make_task('running')
        svc.executor_pause_for_quota(task, body='额度已用完')
        task.refresh_from_db()
        self.assertEqual((task.status, task.pause_reason), ('paused', 'quota_exhausted'))
        self.login(self.lillian, self.business)
        self.assertEqual(self.ok(self.get('app/tasks/%s' % task.public_id))['pause']['reason'], 'quota_exhausted')
