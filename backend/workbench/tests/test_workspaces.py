"""Contract tests: app/workspaces and app/playbooks (backend-plan 2.1-2.3; screens 16-19, 22, 24, 58)."""
from datetime import timedelta

from django.utils import timezone

from backend.models import Membership
from backend.workbench.models import (AcceptanceCriterion, Expert, KnowledgeBase, KnowledgeDocument, Playbook,
                                      Task, TaskStep, TaskUserState, Workspace, WorkspaceMember,
                                      WorkspaceRuleVersion)
from backend.workbench.tests.base import AppTestCase, make_user


def make_expert(name):
    return Expert.objects.create(source='system', group='role', name=name, title=name, responsibilities='-',
                                 version_label='v1', status='published', share_scope='account',
                                 communication_style='concise', uncertainty_policy='ask_first')


class WorkspaceBase(AppTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        now = timezone.now()
        cls.wangye = make_expert('望野')
        cls.yijing = make_expert('译境')
        cls.mlillian, cls.mchen, cls.mmark = (cls.member(cls, u) for u in (cls.lillian, cls.chen, cls.mark))
        cls.ws = Workspace.objects.create(account=cls.business, name='德国市场进入', created_from='blank',
                                          created_by=cls.lillian)
        for user, role in ((cls.lillian, 'owner'), (cls.chen, 'editor'), (cls.mark, 'viewer')):
            WorkspaceMember.objects.create(account=cls.business, workspace=cls.ws, user=user, role=role,
                                           state='active', accepted_at=now)
        cls.guest = WorkspaceMember.objects.create(account=cls.business, workspace=cls.ws,
                                                   invite_email='anna@partner.de', role='guest', state='invited',
                                                   invited_by=cls.lillian)
        cls.ws2 = Workspace.objects.create(account=cls.business, name='黑五大促', created_from='blank',
                                           created_by=cls.chen)
        WorkspaceMember.objects.create(account=cls.business, workspace=cls.ws2, user=cls.chen, role='owner',
                                       state='active')
        WorkspaceMember.objects.create(account=cls.business, workspace=cls.ws2, user=cls.lillian, role='editor',
                                       state='active')
        cls.hidden = Workspace.objects.create(account=cls.business, name='Chen 的私有工作区', created_from='blank',
                                              created_by=cls.chen)
        WorkspaceMember.objects.create(account=cls.business, workspace=cls.hidden, user=cls.chen, role='owner',
                                       state='active')
        for number, rules in ((1, ['对外内容一律用德语。']), (2, ['对外内容一律用德语。', '价格统一用欧元。']),
                              (3, ['对外内容一律用德语。', '价格统一用欧元。', '不承诺具体送达时间。'])):
            version = WorkspaceRuleVersion.objects.create(account=cls.business, workspace=cls.ws, number=number,
                                                          rules=rules, author=cls.lillian, change_note='v%d' % number)
        cls.ws.current_rule_version = version
        cls.ws.save(update_fields=['current_rule_version'])
        cls.tasks = []
        for index, (title, status) in enumerate((('德国站竞品格局调研', 'awaiting_user'), ('EPR 注册材料整理', 'running'),
                                                 ('德语 Listing 初稿', 'accepted'), ('物流方案对比', 'accepted'))):
            cls.tasks.append(cls.make_task(title, cls.ws, status, now - timedelta(hours=index + 1)))
        cls.archived = cls.make_task('已归档任务', cls.ws, 'accepted', now)
        Task.objects.filter(pk=cls.archived.pk).update(archived_at=now)
        cls.ws2_task = cls.make_task('黑五备货', cls.ws2, 'running', now - timedelta(minutes=5), member=cls.mchen)
        TaskUserState.objects.create(account=cls.business, task=cls.tasks[1], user=cls.lillian,
                                     last_read_at=now)
        kb = KnowledgeBase.objects.create(account=cls.business, name='德国市场进入资料库', scope='workspace',
                                          workspace=cls.ws, owner=cls.lillian, cite_audience='workspace_all')
        for title in ('德国 EPR 法规摘要.pdf', '竞品价格表.xlsx'):
            KnowledgeDocument.objects.create(account=cls.business, knowledge_base=kb, title=title,
                                             source_kind='upload', content_ref='store:x', index_state='indexed')

    @classmethod
    def make_task(cls, title, workspace, status, when, member=None):
        member = member or cls.mlillian
        task = Task.objects.create(account=cls.business, workspace=workspace, title=title, creator=member,
                                   owner=member, model_mode='auto', status=status, lead_expert=cls.wangye)
        Task.objects.filter(pk=task.pk).update(last_activity_at=when)
        task.refresh_from_db()
        return task

    def as_(self, user):
        self.login(user, self.business)

    def url(self, suffix='', workspace=None):
        return 'app/workspaces/%s%s' % ((workspace or self.ws).public_id, suffix)


class WorkspaceReadTests(WorkspaceBase):
    def test_list_shape_sort_and_recent_tasks(self):
        self.as_(self.lillian)
        body = self.ok(self.get('app/workspaces'))
        self.assertEqual([item['name'] for item in body['items']], ['黑五大促', '德国市场进入'])
        self.assertEqual((body['offset'], body['total'], body['next_offset']), (0, 2, None))
        item = body['items'][1]
        self.assertTrue({'id', 'name', 'pinned_at', 'task_count', 'unread_count', 'recent_tasks',
                         'current_rule_version'} <= set(item))
        self.assertEqual(item['task_count'], 4)
        self.assertEqual(item['unread_count'], 0)
        self.assertEqual([task['title'] for task in item['recent_tasks']],
                         ['德国站竞品格局调研', 'EPR 注册材料整理', '德语 Listing 初稿'])
        self.assertEqual(set(item['recent_tasks'][0]), {'id', 'title', 'status', 'last_activity_at', 'unread'})
        self.assertEqual(item['current_rule_version'], {'number': 3})
        self.assertIsNone(body['items'][0]['current_rule_version'])
        self.as_(self.mark)
        mark = self.ok(self.get('app/workspaces'))['items'][0]
        self.assertEqual(mark['unread_count'], 4)
        self.assertFalse(mark['recent_tasks'][0]['unread'] is False)

    def test_pin_moves_workspace_first(self):
        self.as_(self.lillian)
        pinned = self.ok(self.post(self.url('/pin')))
        self.assertIsNotNone(pinned['pinned_at'])
        self.assertEqual(self.ok(self.get('app/workspaces'))['items'][0]['name'], '德国市场进入')
        self.assertIsNone(self.ok(self.delete(self.url('/pin')))['pinned_at'])
        self.assertEqual(self.ok(self.get('app/workspaces'))['items'][0]['name'], '黑五大促')
        self.assert_error(self.post(self.url('/pin', self.hidden)), 404, 'NOT_FOUND')

    def test_detail_counts(self):
        self.as_(self.mark)
        body = self.ok(self.get(self.url()))
        self.assertEqual(body['name'], '德国市场进入')
        self.assertEqual(body['counts'], {'tasks': 4, 'documents': 2, 'members': 3})
        self.assertEqual(body['account_kind'], 'business')
        self.assertEqual(body['current_rule_version'], {'number': 3})
        self.assertEqual(body['unread_count'], 4)
        self.assertEqual(body['role'], 'viewer')
        self.assertEqual([row['display_name'] for row in body['members_preview']],
                         ['Lillian Li', 'Chen Yu', 'Mark Weber'])
        self.assertIn('avatar_name', body['members_preview'][0])
        self.assert_error(self.get(self.url(workspace=self.hidden)), 404, 'NOT_FOUND')
        self.login(self.anna, self.business)
        self.assert_error(self.get(self.url()), 404, 'NOT_FOUND')

    def test_invalid_query(self):
        self.as_(self.lillian)
        self.assert_error(self.get('app/workspaces', {'sort': 'name'}), 422, 'INVALID_REQUEST')


class WorkspaceWriteTests(WorkspaceBase):
    def test_create_blank(self):
        self.as_(self.mark)
        response = self.post('app/workspaces', {'name': ' 法国市场进入 ', 'description': None, 'created_from': 'blank'})
        body = self.ok(response, 201)
        self.assertEqual(body['workspace']['name'], '法国市场进入')
        self.assertEqual(body['workspace']['created_from'], 'blank')
        self.assertIsNone(body['workspace']['current_rule_version'])
        self.assertIsNone(body['first_task'])
        workspace = Workspace.objects.get(public_id=body['workspace']['id'])
        self.assertTrue(WorkspaceMember.objects.filter(workspace=workspace, user=self.mark, role='owner',
                                                       state='active').exists())

    def test_create_validation(self):
        self.as_(self.lillian)
        for data in ({'name': '', 'created_from': 'blank'}, {'name': 'x' * 41, 'created_from': 'blank'},
                     {'name': 'A', 'created_from': 'blank', 'source_workspace': self.ws.public_id},
                     {'name': 'A', 'created_from': 'copy_rules'}, {'name': 'A', 'created_from': 'clone'}):
            self.assert_error(self.post('app/workspaces', data), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post('app/workspaces', {'name': 'A'}), 422, 'INVALID_REQUEST')
        self.assert_error(self.post('app/workspaces', {'name': 'A', 'created_from': 'blank', 'color': 1}), 422,
                          'INVALID_REQUEST')
        self.assert_error(self.post('app/workspaces', {'name': 'A', 'created_from': 'blank'}, account=False), 409,
                          'ACCOUNT_CHANGED')

    def test_create_copy_rules(self):
        self.as_(self.lillian)
        body = self.ok(self.post('app/workspaces', {'name': '法国市场进入', 'created_from': 'copy_rules',
                                                   'source_workspace': self.ws.public_id}), 201)
        self.assertEqual(body['workspace']['current_rule_version'], {'number': 1})
        workspace = Workspace.objects.get(public_id=body['workspace']['id'])
        self.assertEqual(workspace.current_rule_version.rules, self.ws.current_rule_version.rules)
        self.assertEqual(workspace.source_workspace, self.ws)
        self.assert_error(self.post('app/workspaces', {'name': 'B', 'created_from': 'copy_rules',
                                                      'source_workspace': self.hidden.public_id}), 404, 'NOT_FOUND')

    def test_create_from_playbook(self):
        playbook = Playbook.objects.create(account=self.business, name='新市场进入评估', visibility='account',
                                           created_by=self.chen, variables=[{'name': '市场', 'default': '德国'}])
        self.as_(self.mark)
        body = self.ok(self.post('app/workspaces', {'name': '法国市场进入', 'created_from': 'playbook',
                                                   'source_playbook': playbook.public_id}), 201)
        task = Task.objects.get(public_id=body['first_task']['id'])
        self.assertEqual((task.status, task.source_playbook, task.workspace.public_id),
                         ('draft', playbook, body['workspace']['id']))
        self.assertEqual(task.owner.user, self.mark)
        private = Playbook.objects.create(account=self.business, name='私有', visibility='only_me',
                                          created_by=self.chen)
        self.assert_error(self.post('app/workspaces', {'name': 'C', 'created_from': 'playbook',
                                                      'source_playbook': private.public_id}), 404, 'NOT_FOUND')

    def test_patch(self):
        self.as_(self.lillian)
        body = self.ok(self.patch(self.url(), {'name': '德国市场', 'description': '德国站进入项目'}))
        self.assertEqual((body['workspace']['name'], body['workspace']['description']), ('德国市场', '德国站进入项目'))
        self.assert_error(self.patch(self.url(), {'name': ''}), 422, 'VALIDATION_FAILED')
        self.as_(self.chen)
        self.assert_error(self.patch(self.url(), {'name': 'x'}), 403, 'PERMISSION_DENIED')

    def test_patch_version_conflict(self):
        self.as_(self.lillian)
        version = self.ok(self.get(self.url()))['version']
        body = self.ok(self.patch(self.url(), {'name': '德国站', 'version': version}))
        self.assertEqual(body['workspace']['version'], version + 1)
        self.assert_error(self.patch(self.url(), {'name': 'x', 'version': version}), 409, 'VERSION_CONFLICT')
        self.assert_error(self.patch(self.url(), {'name': 'x', 'version': '2'}), 422, 'VALIDATION_FAILED')
        self.assertEqual(self.ok(self.patch(self.url(), {'name': '德国市场'}))['workspace']['version'], version + 2)

    def test_delete_detaches_tasks(self):
        self.as_(self.chen)
        self.assert_error(self.delete(self.url()), 403, 'PERMISSION_DENIED')
        self.as_(self.lillian)
        body = self.ok(self.delete(self.url()))
        self.assertIsNotNone(body['deleted_at'])
        self.assertFalse(Task.objects.filter(workspace=self.ws).exists())
        self.assert_error(self.get(self.url()), 404, 'NOT_FOUND')
        self.assertEqual([item['name'] for item in self.ok(self.get('app/workspaces'))['items']], ['黑五大促'])

    def test_read_all(self):
        self.as_(self.lillian)
        TaskUserState.objects.filter(task=self.tasks[1], user=self.lillian).update(marked_unread=True)
        self.assertEqual(self.ok(self.post(self.url('/read-all')))['updated_count'], 1)
        self.as_(self.mark)
        self.assertEqual(self.ok(self.post(self.url('/read-all')))['updated_count'], 4)
        self.assertEqual(self.ok(self.get(self.url()))['unread_count'], 0)
        self.assert_error(self.post(self.url('/read-all', self.hidden)), 404, 'NOT_FOUND')

    def test_archive_completed(self):
        self.as_(self.mark)
        self.assert_error(self.post(self.url('/archive-completed'), {'expected_count': 2}), 403, 'PERMISSION_DENIED')
        self.as_(self.chen)
        self.assert_error(self.post(self.url('/archive-completed'), {'expected_count': 3}), 409, 'STATE_CONFLICT')
        self.assert_error(self.post(self.url('/archive-completed'), {'expected_count': '2'}), 422,
                          'VALIDATION_FAILED')
        body = self.ok(self.post(self.url('/archive-completed'), {'expected_count': 2}))
        self.assertEqual(body['archived_count'], 2)
        self.assertEqual(set(body['task_ids']), {self.tasks[2].public_id, self.tasks[3].public_id})
        self.assertEqual(self.ok(self.get(self.url()))['counts']['tasks'], 2)


class MemberTests(WorkspaceBase):
    def test_list(self):
        self.as_(self.mark)
        body = self.ok(self.get(self.url('/members')))
        self.assertEqual([row['role'] for row in body['items']], ['owner', 'editor', 'viewer', 'guest'])
        self.assertEqual(body['total'], 4)
        self.assertEqual(body['counts'], {'active': 3, 'invited': 1})
        row = body['items'][2]
        self.assertEqual(set(row), {'id', 'user', 'email', 'role', 'state', 'access_summary', 'guest_tasks',
                                    'last_active_at', 'is_me'})
        self.assertTrue(row['is_me'])
        self.assertEqual(row['user']['display_name'], 'Mark Weber')
        guest = body['items'][3]
        self.assertEqual((guest['user'], guest['email'], guest['state']), (None, 'anna@partner.de', 'invited'))

    def test_invite(self):
        zoe = make_user('zoe@sage.demo', 'Zoe', 'Hu')
        Membership.objects.create(account=self.business, user=zoe, role='member')
        self.as_(self.chen)
        self.assert_error(self.post(self.url('/members'), {'email': 'zoe@sage.demo', 'role': 'editor'}), 403,
                          'PERMISSION_DENIED')
        self.as_(self.lillian)
        body = self.ok(self.post(self.url('/members'), {'email': 'ZOE@sage.demo', 'role': 'editor'}), 201)
        self.assertEqual((body['member']['role'], body['member']['state'], body['member']['email']),
                         ('editor', 'active', 'zoe@sage.demo'))
        self.assert_error(self.post(self.url('/members'), {'email': 'zoe@sage.demo', 'role': 'viewer'}), 409,
                          'STATE_CONFLICT')
        # Guests are not offered in the first release (Q-P1 C).
        self.assert_error(self.post(self.url('/members'), {'email': 'anna@partner.de', 'role': 'guest'}), 422,
                          'VALIDATION_FAILED')
        self.assert_error(self.post(self.url('/members'), {'email': 'anna@partner.de', 'role': 'viewer'}), 422,
                          'VALIDATION_FAILED')
        self.assert_error(self.post(self.url('/members'), {'email': 'not-an-email', 'role': 'viewer'}), 422,
                          'VALIDATION_FAILED')

    def test_change_role_and_remove(self):
        mark_row = WorkspaceMember.objects.get(workspace=self.ws, user=self.mark)
        lillian_row = WorkspaceMember.objects.get(workspace=self.ws, user=self.lillian)
        self.as_(self.chen)
        self.assert_error(self.patch(self.url('/members/%s' % mark_row.public_id), {'role': 'editor'}), 403,
                          'PERMISSION_DENIED')
        self.as_(self.lillian)
        body = self.ok(self.patch(self.url('/members/%s' % mark_row.public_id), {'role': 'editor'}))
        self.assertEqual(body['member']['role'], 'editor')
        self.assert_error(self.patch(self.url('/members/%s' % mark_row.public_id), {'role': 'guest'}), 422,
                          'VALIDATION_FAILED')
        self.assert_error(self.patch(self.url('/members/%s' % lillian_row.public_id), {'role': 'viewer'}), 409,
                          'STATE_CONFLICT')
        self.assert_error(self.delete(self.url('/members/%s' % lillian_row.public_id)), 409, 'STATE_CONFLICT')
        removed = self.ok(self.delete(self.url('/members/%s' % mark_row.public_id)))
        self.assertEqual(removed['member']['state'], 'removed')
        self.ok(self.delete(self.url('/members/%s' % self.guest.public_id)))
        self.assertEqual(self.ok(self.get(self.url('/members')))['total'], 2)
        self.as_(self.mark)
        self.assert_error(self.get(self.url()), 404, 'NOT_FOUND')


class RuleTests(WorkspaceBase):
    def test_get(self):
        self.as_(self.chen)
        body = self.ok(self.get(self.url('/rules')))
        self.assertEqual(body['current']['number'], 3)
        self.assertEqual(len(body['current']['rules']), 3)
        self.assertEqual(body['current']['author']['name'], 'Lillian Li')
        self.assertEqual([row['number'] for row in body['versions']['items']], [3, 2, 1])
        self.assertEqual(set(body['versions']['items'][0]), {'number', 'author', 'created_at', 'change_note',
                                                             'restored_from'})
        self.assertFalse(body['can_edit'])
        self.as_(self.lillian)
        self.assertTrue(self.ok(self.get(self.url('/rules')))['can_edit'])

    def test_post(self):
        self.as_(self.chen)
        self.assert_error(self.post(self.url('/rules'), {'rules': ['a'], 'version': 3}), 403, 'PERMISSION_DENIED')
        self.as_(self.lillian)
        self.assert_error(self.post(self.url('/rules'), {'rules': ['a'], 'version': 2}), 409, 'VERSION_CONFLICT')
        self.assert_error(self.post(self.url('/rules'), {'rules': ['a'] * 31, 'version': 3}), 422,
                          'VALIDATION_FAILED')
        self.assert_error(self.post(self.url('/rules'), {'rules': ['a' * 201], 'version': 3}), 422,
                          'VALIDATION_FAILED')
        rules = ['对外内容一律用德语。', '价格统一用欧元。', '不承诺具体送达时间。', '不写竞品品牌名。']
        body = self.ok(self.post(self.url('/rules'), {'rules': rules, 'version': 3}), 201)
        self.assertEqual(body['version']['number'], 4)
        current = self.ok(self.get(self.url('/rules')))
        self.assertEqual(current['versions']['items'][0]['change_note'], '新增：不写竞品品牌名。')
        noted = self.ok(self.post(self.url('/rules'), {'rules': rules[:1], 'version': 4, 'change_note': '精简'}), 201)
        self.assertEqual(noted['version']['number'], 5)

    def test_restore(self):
        self.as_(self.lillian)
        body = self.ok(self.post(self.url('/rules/1/restore'), {}), 201)
        self.assertEqual(body['version'], {'number': 4, 'restored_from': 1})
        self.ws.refresh_from_db()
        self.assertEqual(self.ws.current_rule_version.rules, ['对外内容一律用德语。'])
        self.assert_error(self.post(self.url('/rules/4/restore'), {}), 409, 'STATE_CONFLICT')
        self.assert_error(self.post(self.url('/rules/9/restore'), {}), 404, 'NOT_FOUND')
        self.assert_error(self.post(self.url('/rules/1/restore'), {'version': 3}), 409, 'VERSION_CONFLICT')
        self.as_(self.mark)
        self.assert_error(self.post(self.url('/rules/2/restore'), {}), 403, 'PERMISSION_DENIED')


class PlaybookTests(WorkspaceBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.source = cls.tasks[2]
        cls.steps = []
        for order, (title, expert) in enumerate((('德国站关键词与类目', cls.wangye), ('德语 Listing 撰写', cls.yijing),
                                                 ('竞品价格带', cls.wangye)), start=1):
            cls.steps.append(TaskStep.objects.create(account=cls.business, task=cls.source, order=order,
                                                     stage='research', title=title, expert=expert, status='done'))
        cls.steps[1].depends_on.add(cls.steps[0])
        cls.hidden_task = cls.make_task('Chen 的私有任务', cls.hidden, 'accepted', timezone.now(), member=cls.mchen)
        AcceptanceCriterion.objects.create(account=cls.business, task=cls.source, step=cls.steps[1], order=1,
                                           text='标题不超过 200 字符', origin='ai')

    def payload(self, **overrides):
        data = {'source_task': self.source.public_id, 'name': '新市场进入评估', 'visibility': 'workspace',
                'variables': [{'name': '市场', 'default': '德国'}, '平台'],
                'steps': [{'source_step': self.steps[0].public_id, 'order': 1, 'title': '{市场}站{平台}关键词与类目'},
                          {'source_step': self.steps[1].public_id, 'order': 2, 'title': '{市场}语 Listing 撰写'}]}
        data.update(overrides)
        return data

    def test_create(self):
        self.as_(self.lillian)
        body = self.ok(self.post('app/playbooks', self.payload()), 201)['playbook']
        self.assertEqual(set(body), {'id', 'name', 'visibility', 'variables', 'step_count', 'expert_count',
                                     'created_at'})
        self.assertEqual((body['step_count'], body['expert_count']), (2, 2))
        self.assertEqual(body['variables'], [{'name': '市场', 'default': '德国'}, {'name': '平台', 'default': None}])
        playbook = Playbook.objects.get(public_id=body['id'])
        steps = list(playbook.steps.order_by('order'))
        self.assertEqual([step.title for step in steps], ['{市场}站{平台}关键词与类目', '{市场}语 Listing 撰写'])
        self.assertEqual(list(steps[1].depends_on.all()), [steps[0]])
        self.assertEqual(steps[1].acceptance, ['标题不超过 200 字符'])
        self.assertEqual(playbook.source_task, self.source)

    def test_create_errors(self):
        self.as_(self.lillian)
        for overrides in ({'name': ''}, {'name': 'x' * 41}, {'steps': []}, {'visibility': 'team'},
                          {'variables': ['品类']}):
            self.assert_error(self.post('app/playbooks', self.payload(**overrides)), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post('app/playbooks', self.payload(steps=[
            {'source_step': self.steps[0].public_id, 'order': 1, 'title': 't', 'grant': True}])), 422,
            'INVALID_REQUEST')
        self.assert_error(self.post('app/playbooks', self.payload(source_task=self.tasks[0].public_id)), 409,
                          'STATE_CONFLICT')
        self.assert_error(self.post('app/playbooks', self.payload(source_task=self.hidden_task.public_id)), 404,
                          'NOT_FOUND')
        self.as_(self.mark)
        self.assert_error(self.post('app/playbooks', self.payload()), 403, 'PERMISSION_DENIED')

    def test_list_visibility_and_detail(self):
        self.as_(self.lillian)
        shared = self.ok(self.post('app/playbooks', self.payload()), 201)['playbook']
        private = self.ok(self.post('app/playbooks', self.payload(name='私人模板', visibility='only_me')), 201)['playbook']
        listed = self.ok(self.get('app/playbooks'))
        self.assertEqual({item['name'] for item in listed['items']}, {'新市场进入评估', '私人模板'})
        item = listed['items'][0]
        self.assertTrue({'id', 'name', 'step_count', 'expert_count', 'created_by', 'visibility'} <= set(item))
        self.assertEqual(item['created_by']['name'], 'Lillian Li')
        self.as_(self.mark)
        self.assertEqual([item['name'] for item in self.ok(self.get('app/playbooks'))['items']], ['新市场进入评估'])
        detail = self.ok(self.get('app/playbooks/%s' % shared['id']))
        self.assertEqual([step['title'] for step in detail['steps']], ['{市场}站{平台}关键词与类目', '{市场}语 Listing 撰写'])
        self.assertEqual(detail['steps'][0]['expert']['name'], '望野')
        self.assertEqual(detail['steps'][1]['depends_on'], [detail['steps'][0]['id']])
        self.assert_error(self.get('app/playbooks/%s' % private['id']), 404, 'NOT_FOUND')
        self.login(self.anna, self.business)
        self.assert_error(self.get('app/playbooks'), 404, 'NOT_FOUND')
