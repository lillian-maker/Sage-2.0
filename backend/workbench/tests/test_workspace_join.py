"""Contract tests: app/workspaces/{id}/join-requests (83 屏 工作区「无权限」· 申请加入，D33).

A member of the billing account who is not a member of the workspace can ask to join; the workspace owners are
notified in the app, list the pending requests and approve (as editor or viewer) or reject; the applicant is
notified of the outcome and may withdraw while pending. Workspace detail stays NOT_FOUND for non-members.
"""
from django.utils import timezone

from backend.workbench.models import AppNotification, Workspace, WorkspaceJoinRequest, WorkspaceMember
from backend.workbench.tests.base import AppTestCase


class JoinRequestBase(AppTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        now = timezone.now()
        cls.ws = Workspace.objects.create(account=cls.business, name='德国市场进入', created_from='blank',
                                          created_by=cls.lillian)
        for user, role in ((cls.lillian, 'owner'), (cls.chen, 'editor')):
            WorkspaceMember.objects.create(account=cls.business, workspace=cls.ws, user=user, role=role,
                                           state='active', accepted_at=now)

    def url(self, suffix='', ws=None):
        return 'app/workspaces/%s/join-requests%s' % ((ws or self.ws).public_id, suffix)

    def apply(self, message='想参与 EPR 资料整理'):
        self.login(self.mark, self.business)
        return self.ok(self.post(self.url(), {'message': message}), 201)['join_request']


class ApplyTests(JoinRequestBase):
    def test_non_member_still_gets_404_on_detail(self):
        self.login(self.mark, self.business)
        self.assert_error(self.get('app/workspaces/%s' % self.ws.public_id), 404, 'NOT_FOUND')

    def test_mine_before_and_after(self):
        self.login(self.mark, self.business)
        self.assertEqual(self.ok(self.get(self.url('/mine'))), {'join_request': None})
        created = self.apply()
        self.assertEqual(set(created), {'id', 'state', 'message', 'role', 'created_at', 'decided_at', 'requester',
                                        'workspace'})
        self.assertEqual(created['state'], 'pending')
        self.assertEqual(created['workspace'], {'id': self.ws.public_id})
        self.assertEqual(created['requester']['name'], 'Mark Weber')
        self.assertEqual(self.ok(self.get(self.url('/mine')))['join_request']['id'], created['id'])

    def test_repeat_returns_pending_request(self):
        first = self.apply()
        again = self.ok(self.post(self.url(), {}), 200)['join_request']
        self.assertEqual(again['id'], first['id'])
        self.assertEqual(WorkspaceJoinRequest.objects.count(), 1)

    def test_owners_are_notified(self):
        created = self.apply()
        note = AppNotification.objects.get(recipient=self.lillian, type='join_requested')
        self.assertEqual(note.category, 'workspace')
        self.assertEqual(note.text, 'Mark Weber 申请加入工作区“德国市场进入”')
        self.assertEqual((note.target_type, note.target_id), ('workspace_members', self.ws.public_id))
        self.assertEqual(note.action, 'open')
        self.assertFalse(AppNotification.objects.filter(recipient=self.chen).exists())
        self.assertIn(created['id'], note.dedupe_key)

    def test_owner_sees_it_in_notification_centre(self):
        self.apply()
        self.login(self.lillian, self.business)
        items = self.ok(self.get('app/notifications'))['items']
        row = next(i for i in items if i['type'] == 'join_requested')
        self.assertEqual(row['target'], {'type': 'workspace_members', 'id': self.ws.public_id})

    def test_unknown_deleted_and_member(self):
        self.login(self.mark, self.business)
        self.assert_error(self.post('app/workspaces/01ZZZZZZZZZZZZZZZZZZZZZZZZ/join-requests', {}), 404, 'NOT_FOUND')
        gone = Workspace.objects.create(account=self.business, name='旧工作区', created_from='blank',
                                        created_by=self.lillian, deleted_at=timezone.now())
        self.assert_error(self.post(self.url(ws=gone), {}), 404, 'NOT_FOUND')
        self.assert_error(self.get(self.url('/mine', ws=gone)), 404, 'NOT_FOUND')
        self.login(self.chen, self.business)
        self.assert_error(self.post(self.url(), {}), 409, 'STATE_CONFLICT')

    def test_other_account_cannot_apply(self):
        self.login(self.mark)
        self.assert_error(self.post(self.url(), {}), 404, 'NOT_FOUND')

    def test_message_validation(self):
        self.login(self.mark, self.business)
        self.assert_error(self.post(self.url(), {'message': 'x' * 201}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post(self.url(), {'message': 3}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post(self.url(), {'role': 'owner'}), 422, 'INVALID_REQUEST')


class OwnerTests(JoinRequestBase):
    def setUp(self):
        self.pending = self.apply()

    def decide(self, action, data=None, user=None):
        self.login(user or self.lillian, self.business)
        return self.post(self.url('/%s/%s' % (self.pending['id'], action)), data or {})

    def test_list_for_owner_only(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.get(self.url()))
        self.assertEqual(body['total'], 1)
        self.assertEqual(body['items'][0]['id'], self.pending['id'])
        self.assertEqual(body['items'][0]['message'], '想参与 EPR 资料整理')
        self.login(self.chen, self.business)
        self.assert_error(self.get(self.url()), 403, 'PERMISSION_DENIED')
        self.login(self.mark, self.business)
        self.assert_error(self.get(self.url()), 404, 'NOT_FOUND')

    def test_approve_adds_member_and_notifies(self):
        body = self.ok(self.decide('approve', {'role': 'viewer'}))
        self.assertEqual(body['join_request']['state'], 'approved')
        self.assertEqual(body['join_request']['role'], 'viewer')
        self.assertIsNotNone(body['join_request']['decided_at'])
        self.assertEqual(body['member']['role'], 'viewer')
        row = WorkspaceMember.objects.get(workspace=self.ws, user=self.mark)
        self.assertEqual((row.state, row.role), ('active', 'viewer'))
        note = AppNotification.objects.get(recipient=self.mark, type='join_approved')
        self.assertEqual(note.text, '你已加入工作区“德国市场进入”')
        self.assertEqual((note.target_type, note.target_id), ('workspace', self.ws.public_id))
        self.login(self.mark, self.business)
        self.ok(self.get('app/workspaces/%s' % self.ws.public_id))
        self.assertTrue(AppNotification.objects.get(recipient=self.lillian, type='join_requested').read_at)

    def test_approve_reactivates_removed_member(self):
        WorkspaceMember.objects.create(account=self.business, workspace=self.ws, user=self.mark, role='editor',
                                       state='removed')
        self.ok(self.decide('approve', {'role': 'editor'}))
        row = WorkspaceMember.objects.get(workspace=self.ws, user=self.mark)
        self.assertEqual((row.state, row.role), ('active', 'editor'))

    def test_approve_role_validation_and_twice(self):
        self.assert_error(self.decide('approve', {'role': 'owner'}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.decide('approve', {}), 422, 'INVALID_REQUEST')
        self.ok(self.decide('approve', {'role': 'editor'}))
        self.assert_error(self.decide('approve', {'role': 'editor'}), 409, 'STATE_CONFLICT')
        self.assert_error(self.decide('reject'), 409, 'STATE_CONFLICT')

    def test_non_owner_cannot_decide(self):
        self.assert_error(self.decide('approve', {'role': 'viewer'}, user=self.chen), 403, 'PERMISSION_DENIED')
        self.assert_error(self.decide('reject', user=self.mark), 404, 'NOT_FOUND')

    def test_reject_notifies_and_allows_new_request(self):
        body = self.ok(self.decide('reject', {'reason': '这个工作区只对运营组开放'}))
        self.assertEqual(body['join_request']['state'], 'rejected')
        note = AppNotification.objects.get(recipient=self.mark, type='join_rejected')
        self.assertEqual(note.text, '加入工作区的申请未通过：这个工作区只对运营组开放')
        self.assertEqual(note.target_type, '')
        self.assertFalse(WorkspaceMember.objects.filter(workspace=self.ws, user=self.mark).exists())
        again = self.apply('补充：负责 EPR')
        self.assertNotEqual(again['id'], self.pending['id'])

    def test_reject_reason_validation(self):
        self.assert_error(self.decide('reject', {'reason': 'x' * 201}), 422, 'VALIDATION_FAILED')

    def test_withdraw_by_applicant_only(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.post(self.url('/%s/withdraw' % self.pending['id']), {}), 404, 'NOT_FOUND')
        self.login(self.mark, self.business)
        body = self.ok(self.post(self.url('/%s/withdraw' % self.pending['id']), {}))
        self.assertEqual(body['join_request']['state'], 'withdrawn')
        self.assert_error(self.post(self.url('/%s/withdraw' % self.pending['id']), {}), 409, 'STATE_CONFLICT')
        self.assertEqual(self.ok(self.get(self.url('/mine')))['join_request']['state'], 'withdrawn')
        self.login(self.lillian, self.business)
        self.assertEqual(self.ok(self.get(self.url()))['total'], 0)
        self.assert_error(self.decide('approve', {'role': 'viewer'}), 409, 'STATE_CONFLICT')
        self.assertTrue(AppNotification.objects.get(recipient=self.lillian, type='join_requested').read_at)

    def test_unknown_request_id(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.post(self.url('/01ZZZZZZZZZZZZZZZZZZZZZZZZ/approve'), {'role': 'viewer'}),
                          404, 'NOT_FOUND')
