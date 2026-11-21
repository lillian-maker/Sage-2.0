"""Contract tests: app/authorizations/* and app/grants (C-A-5, C-A-6, C-D-18, Q-P5, Q-V11)."""
from backend.commerce.models import Reservation
from backend.models import AuditEvent
from backend.workbench.models import AuthorizationGrant
from backend.workbench.tests.base import AppTestCase
from backend.workbench.tests.test_billing_fixtures import authorization, credits, task, workspace


class AuthorizationReadTests(AppTestCase):
    def test_detail_shape_personal(self):
        account = self.login(self.lillian)
        item = authorization(account, task(account, self.member(self.lillian, account)))
        body = self.ok(self.get('app/authorizations/%s' % item.public_id))
        for key in ('purpose_text', 'scope_text', 'action_kind', 'scope_params', 'scope_options', 'estimate_state',
                    'estimated_credits', 'validity', 'decision', 'decision_scope', 'charge_target', 'allowed_scopes'):
            self.assertIn(key, body)
        self.assertEqual(body['estimated_credits'], 40)
        self.assertEqual(body['charge_target']['label'], '计入个人额度')
        self.assertEqual(body['allowed_scopes'], ['once', 'task'])

    def test_workspace_scope_only_for_workspace_tasks(self):
        self.login(self.lillian, self.business)
        space = workspace(self.business, self.lillian)
        item = authorization(self.business, task(self.business, self.member(self.lillian), space=space))
        body = self.ok(self.get('app/authorizations/%s' % item.public_id))
        self.assertEqual(body['allowed_scopes'], ['once', 'task', 'workspace'])
        self.assertEqual(body['charge_target']['label'], '计入企业额度')

    def test_unrelated_member_gets_404(self):
        item = authorization(self.business, task(self.business, self.member(self.lillian)))
        self.login(self.mark, self.business)
        self.assert_error(self.get('app/authorizations/%s' % item.public_id), 404, 'NOT_FOUND')


class AuthorizationDecisionTests(AppTestCase):
    def setUp(self):
        self.account = self.login(self.lillian)
        self.owner = self.member(self.lillian, self.account)
        self.task = task(self.account, self.owner)
        self.item = authorization(self.account, self.task)

    def url(self, action):
        return 'app/authorizations/%s/%s' % (self.item.public_id, action)

    def test_approve_reserves_and_audits(self):
        credits(self.account)
        body = self.ok(self.post(self.url('approve'), {'decision_scope': 'once', 'client_request_id': 'c1'}))
        self.assertEqual(body['authorization']['decision'], 'approved')
        self.assertIsNone(body['grant'])
        reservation = Reservation.objects.get(pk=body['authorization']['reservation_id'])
        self.assertEqual(reservation.quantity, '40')
        self.assertTrue(reservation.task_key.startswith(self.task.public_id))
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'running')
        self.assertTrue(AuditEvent.objects.filter(action='authorization.approve', target_id=self.item.public_id).exists())
        replay = self.ok(self.post(self.url('approve'), {'decision_scope': 'once', 'client_request_id': 'c1'}))
        self.assertEqual(replay, body)

    def test_approve_task_scope_creates_grant(self):
        credits(self.account)
        body = self.ok(self.post(self.url('approve'), {'decision_scope': 'task', 'client_request_id': 'c2'}))
        self.assertEqual(body['grant']['scope'], 'task')
        self.assertTrue(AuthorizationGrant.objects.filter(public_id=body['grant']['id'], task=self.task).exists())

    def test_approve_without_credits(self):
        self.assert_error(self.post(self.url('approve'), {'client_request_id': 'c3'}), 409, 'INSUFFICIENT_CREDITS')
        self.item.refresh_from_db()
        self.assertEqual(self.item.decision, 'pending')

    def test_approve_while_checking_conflicts(self):
        self.item.estimate_state = 'checking'
        self.item.save()
        self.assert_error(self.post(self.url('approve'), {'client_request_id': 'c4'}), 409, 'STATE_CONFLICT')

    def test_workspace_scope_rejected_for_standalone_task(self):
        self.assert_error(self.post(self.url('approve'), {'decision_scope': 'workspace', 'client_request_id': 'c5'}),
                          422, 'VALIDATION_FAILED')

    def test_expired(self):
        self.item.decision = 'expired'
        self.item.save()
        self.assert_error(self.post(self.url('approve'), {'client_request_id': 'c6'}), 409, 'AUTHORIZATION_EXPIRED')

    def test_reject_from_unavailable(self):
        self.item.estimate_state = 'unavailable'
        self.item.save()
        body = self.ok(self.post(self.url('reject'), {'client_request_id': 'r1'}))
        self.assertEqual(body['authorization']['decision'], 'rejected')
        self.assertEqual(body['authorization']['estimate_state'], 'unavailable')
        self.assertTrue(AuditEvent.objects.filter(action='authorization.reject').exists())
        self.assert_error(self.post(self.url('approve'), {'client_request_id': 'r2'}), 409, 'STATE_CONFLICT')

    def test_revise_and_estimate(self):
        body = self.ok(self.post(self.url('revise'), {'scope_params': {'top_n': 20}, 'client_request_id': 'v1'}))
        self.assertEqual(body['authorization']['estimate_state'], 'checking')
        self.assertEqual(body['authorization']['scope_text'], 'Amazon.de · 小家电类目 · 前 20 名')
        self.assert_error(self.post(self.url('revise'), {'scope_params': {'top_n': 30}, 'client_request_id': 'v2'}),
                          422, 'VALIDATION_FAILED')
        self.assert_error(self.post(self.url('estimate'), {'client_request_id': 'e1'}), 409, 'STATE_CONFLICT')
        self.item.refresh_from_db()
        self.item.estimate_state = 'unavailable'
        self.item.save()
        body = self.ok(self.post(self.url('estimate'), {'client_request_id': 'e2'}), 202)
        self.assertEqual(body['authorization']['estimate_state'], 'checking')

    def test_version_guards_decisions(self):
        self.assertEqual(self.ok(self.get('app/authorizations/%s' % self.item.public_id))['version'], 1)
        revised = self.ok(self.post(self.url('revise'), {'scope_params': {'top_n': 20}, 'client_request_id': 'w1',
                                                         'version': 1}))
        self.assertEqual(revised['authorization']['version'], 2)
        self.assert_error(self.post(self.url('reject'), {'client_request_id': 'w2', 'version': 1}), 409,
                          'VERSION_CONFLICT')
        self.assert_error(self.post(self.url('reject'), {'client_request_id': 'w3', 'version': '2'}), 422,
                          'VALIDATION_FAILED')
        rejected = self.ok(self.post(self.url('reject'), {'client_request_id': 'w4', 'version': 2}))
        self.assertEqual(rejected['authorization']['version'], 3)
        self.item.refresh_from_db()
        self.assertEqual(self.item.version, 3)

    def test_approve_checks_version(self):
        credits(self.account)
        self.assert_error(self.post(self.url('approve'), {'client_request_id': 'a1', 'version': 7}), 409,
                          'VERSION_CONFLICT')
        body = self.ok(self.post(self.url('approve'), {'client_request_id': 'a2', 'version': 1}))
        self.assertEqual(body['authorization']['version'], 2)

    def test_non_initiator_cannot_decide(self):
        item = authorization(self.business, task(self.business, self.member(self.lillian)))
        from backend.workbench.models import TaskParticipant
        TaskParticipant.objects.create(account=self.business, task=item.task, kind='human', user=self.mark,
                                       role='collaborator', state='active')
        self.login(self.mark, self.business)
        self.ok(self.get('app/authorizations/%s' % item.public_id))
        self.assert_error(self.post('app/authorizations/%s/approve' % item.public_id, {'client_request_id': 'p1'}),
                          403, 'PERMISSION_DENIED')


class GrantTests(AppTestCase):
    def test_list_and_revoke(self):
        self.login(self.lillian, self.business)
        space = workspace(self.business, self.lillian)
        item = authorization(self.business, task(self.business, self.member(self.lillian), space=space))
        grant = AuthorizationGrant.objects.create(account=self.business, user=self.lillian, scope='workspace',
                                                  workspace=space, extension=item.extension,
                                                  action_kind='read_query', source_request=item)
        body = self.ok(self.get('app/grants'))
        self.assertEqual(body['total'], 1)
        row = body['items'][0]
        self.assertEqual(row['extension']['name'], 'Jungle Scout')
        self.assertEqual(row['workspace']['name'], '德国市场进入')
        self.assertIsNone(row['task'])
        result = self.ok(self.delete('app/grants/%s' % grant.public_id))
        self.assertEqual(result['id'], grant.public_id)
        self.assertIsNotNone(result['revoked_at'])
        self.assertEqual(self.ok(self.get('app/grants'))['total'], 0)
        self.assertTrue(AuditEvent.objects.filter(action='grant.revoke').exists())

    def test_other_users_grant_is_404(self):
        item = authorization(self.business, task(self.business, self.member(self.lillian)))
        grant = AuthorizationGrant.objects.create(account=self.business, user=self.lillian, scope='task',
                                                  task=item.task, extension=item.extension,
                                                  action_kind='read_query', source_request=item)
        self.login(self.mark, self.business)
        self.assert_error(self.delete('app/grants/%s' % grant.public_id), 404, 'NOT_FOUND')
