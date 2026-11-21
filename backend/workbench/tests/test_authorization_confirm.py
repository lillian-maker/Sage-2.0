"""Contract tests: confirm_scopes / confirm_reasons on GET app/authorizations/{id} (82 屏，D32).

Derived, response-only keys. A decision scope needs the second confirmation (“批准使用 X？”) when it is broad
(workspace-wide standing permission), when it lets a pay-per-call extension (Extension.billing_note) run again
without asking (task / workspace), or when the action is not read-only (sends data out; every scope). A one-off
read-only approval stays a single click (05/48).
"""
from backend.workbench.tests.base import AppTestCase
from backend.workbench.tests.test_billing_fixtures import authorization, extension, task, workspace


class AuthorizationConfirmTests(AppTestCase):
    def detail(self, item):
        return self.ok(self.get('app/authorizations/%s' % item.public_id))

    def test_standalone_read_only_needs_no_confirmation(self):
        account = self.login(self.lillian)
        body = self.detail(authorization(account, task(account, self.member(self.lillian, account))))
        self.assertEqual(body['allowed_scopes'], ['once', 'task'])
        self.assertEqual(body['confirm_scopes'], [])
        self.assertEqual(body['confirm_reasons'], {})

    def test_workspace_scope_is_broad(self):
        self.login(self.lillian, self.business)
        space = workspace(self.business, self.lillian)
        body = self.detail(authorization(self.business, task(self.business, self.member(self.lillian), space=space)))
        self.assertEqual(body['allowed_scopes'], ['once', 'task', 'workspace'])
        self.assertEqual(body['confirm_scopes'], ['workspace'])
        self.assertEqual(body['confirm_reasons'], {'workspace': ['broad_scope']})

    def test_pay_per_call_extension_standing_permission(self):
        self.login(self.lillian, self.business)
        paid = extension()
        paid.billing_note = '按次计费'
        paid.save()
        space = workspace(self.business, self.lillian)
        body = self.detail(authorization(self.business, task(self.business, self.member(self.lillian), space=space),
                                         extension=paid))
        self.assertEqual(body['confirm_scopes'], ['task', 'workspace'])
        self.assertEqual(body['confirm_reasons'], {'task': ['paid'], 'workspace': ['broad_scope', 'paid']})
        self.assertNotIn('once', body['confirm_scopes'])

    def test_non_read_only_action_confirms_every_scope(self):
        account = self.login(self.lillian)
        item = authorization(account, task(account, self.member(self.lillian, account)), action_kind='send')
        body = self.detail(item)
        self.assertEqual(body['allowed_scopes'], ['once', 'task'])
        self.assertEqual(body['confirm_scopes'], ['once', 'task'])
        self.assertEqual(body['confirm_reasons'], {'once': ['external_send'], 'task': ['external_send']})

    def test_existing_keys_unchanged(self):
        account = self.login(self.lillian)
        body = self.detail(authorization(account, task(account, self.member(self.lillian, account))))
        for key in ('purpose_text', 'scope_text', 'action_kind', 'scope_params', 'scope_options', 'estimate_state',
                    'estimated_credits', 'validity', 'decision', 'decision_scope', 'charge_target', 'allowed_scopes',
                    'policy_approval_required', 'version'):
            self.assertIn(key, body)
