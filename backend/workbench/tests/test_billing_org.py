"""Contract tests: app/org/* and billing/org/* (C-D-20..26, Q-P4, Q-P6, Q-D5, Q-S5)."""
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from backend.commerce.models import CreditBatch, CreditEntry, Department, DepartmentMember, Reservation
from backend.commerce.subscriptions import remove_member
from backend.models import AuditEvent, MemberRemoval
from backend.workbench.models import (Automation, ConnectorConnection, HandoffItem, MemoryItem,
                                      ExtensionAllowEntry)
from backend.workbench.services.billing import offboard
from backend.workbench.tests.base import AppTestCase
from backend.workbench.tests.test_billing_fixtures import authorization, extension, task


def business_spend(account, membership, quantity, key):
    """Settled shared Credits for one member this month, written directly so no seat setup is needed."""
    batch = CreditBatch.objects.create(account=account, source='purchased', source_key='batch:' + key,
                                       initial_quantity=quantity, expires_at=timezone.now() + timedelta(days=90))
    CreditEntry.objects.create(batch=batch, kind='grant', quantity=quantity, event_key='grant')
    now = timezone.now()
    reservation = Reservation.objects.create(account=account, task_key=key, membership=membership,
                                             quantity=quantity, settled_quantity=quantity, state='settled',
                                             shared_period_start=now - timedelta(days=1),
                                             shared_period_end=now + timedelta(days=1))
    CreditEntry.objects.create(batch=batch, reservation=reservation, kind='reserve', quantity=quantity, event_key='r:' + key)
    CreditEntry.objects.create(batch=batch, reservation=reservation, kind='settle', quantity=quantity, event_key='s:' + key)


class DepartmentTests(AppTestCase):
    def setUp(self):
        self.ops = Department.objects.create(account=self.business, name='运营部', monthly_quota=9000, per_member_share=3000)
        DepartmentMember.objects.create(department=self.ops, membership=self.member(self.mark))
        business_spend(self.business, self.member(self.mark), '1280', 'mark-task')

    def test_list(self):
        self.login(self.chen, self.business)
        body = self.ok(self.get('app/org/departments'))
        self.assertEqual((body['all_count'], body['unassigned_count'], body['allocated_total']), (3, 2, 9000))
        row = body['items'][0]
        self.assertEqual((row['name'], row['member_count'], row['used_credits'], row['quota_version']), ('运营部', 1, '1280', 1))

    def test_member_cannot_list(self):
        self.login(self.mark, self.business)
        self.assert_error(self.get('app/org/departments'), 403, 'PERMISSION_DENIED')

    def test_create(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.post('app/org/departments', {'name': '市场部'}), 201)
        self.assertEqual(body['name'], '市场部')
        self.assert_error(self.post('app/org/departments', {'name': '市场部'}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post('app/org/departments', {'name': ' '}), 422, 'VALIDATION_FAILED')

    def test_assign_member(self):
        self.login(self.lillian, self.business)
        chen = self.member(self.chen)
        body = self.ok(self.post('app/org/members/%d/department' % chen.pk,
                                 {'department_id': str(self.ops.pk), 'is_lead': True, 'request_key': 'dep-1'}))
        self.assertIn('effective_period_start', body)
        self.assertEqual(self.ok(self.get('app/org/departments'))['items'][0]['member_count'], 2)
        self.assertTrue(AuditEvent.objects.filter(action='member.department.change').exists())
        self.assert_error(self.post('app/org/members/%d/department' % chen.pk,
                                    {'department_id': None, 'is_lead': False, 'request_key': 'dep-1'}),
                          409, 'IDEMPOTENCY_CONFLICT')

    def test_department_quota(self):
        self.login(self.chen, self.business)
        url = 'billing/org/departments/%s/quota' % self.ops.pk
        data = {'monthly_quota': 10000, 'per_member_share': 3000, 'pooling_enabled': True,
                'expected_version': 1, 'request_key': 'q-1'}
        body = self.ok(self.post(url, data))
        self.assertEqual((body['quota_version'], body['monthly_quota'], body['pooling_enabled']), (2, 10000, True))
        self.assertEqual(self.ok(self.post(url, data)), body)
        event = AuditEvent.objects.get(action='org.quota.adjust')
        self.assertEqual(event.summary, '运营部 9,000 → 10,000')
        self.assert_error(self.post(url, {**data, 'request_key': 'q-2'}), 409, 'VERSION_CONFLICT')
        self.assert_error(self.post(url, {**data, 'monthly_quota': 1000, 'expected_version': 2, 'request_key': 'q-3'}),
                          422, 'QUOTA_BELOW_USED')

    def test_member_quota(self):
        self.login(self.chen, self.business)
        url = 'billing/org/members/%d/quota' % self.member(self.mark).pk
        self.assert_error(self.post(url, {'quantity': '1000', 'expected_version': 0, 'request_key': 'm-1'}),
                          422, 'QUOTA_BELOW_USED')
        body = self.ok(self.post(url, {'quantity': '2000', 'expected_version': 0, 'request_key': 'm-2'}))
        self.assertEqual(body, {'version': 1, 'quantity': '2000'})
        self.assertTrue(AuditEvent.objects.filter(action='org.quota.adjust', target_type='membership').exists())
        self.assert_error(self.post(url, {'quantity': '3000', 'expected_version': 0, 'request_key': 'm-3'}),
                          409, 'LIMIT_CHANGED')

    def test_member_cannot_adjust_quota(self):
        self.login(self.mark, self.business)
        url = 'billing/org/departments/%s/quota' % self.ops.pk
        response = self.post(url, {'monthly_quota': 1, 'per_member_share': None, 'pooling_enabled': False,
                                   'expected_version': 1, 'request_key': 'x'})
        self.assertEqual(response.status_code, 403)

    def test_organization_members_extra_keys(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.get('billing/organization'))
        mark = next(row for row in body['members'] if row['id'] == self.member(self.mark).pk)
        self.assertEqual(mark['display_name'], 'Mark Weber')
        self.assertEqual(mark['department'], {'id': str(self.ops.pk), 'name': '运营部'})
        for key in ('last_seen_at', 'is_security_admin', 'is_lead'):
            self.assertIn(key, mark)
        only = self.ok(self.get('billing/organization', {'department': str(self.ops.pk)}))
        self.assertEqual([row['id'] for row in only['members']], [self.member(self.mark).pk])
        self.assertEqual(len(self.ok(self.get('billing/organization', {'department': 'none'}))['members']), 2)
        self.assert_error(self.get('billing/organization', {'department': 'bad'}), 422, 'INVALID_REQUEST')


class PolicyAuditTests(AppTestCase):
    def test_policy_get_patch(self):
        self.login(self.mark, self.business)
        body = self.ok(self.get('app/org/policy'))
        self.assertEqual(body['version'], 1)
        self.assert_error(self.patch('app/org/policy', {'version': 1, 'byok_enabled': True}), 403, 'PERMISSION_DENIED')
        self.login(self.lillian, self.business)
        body = self.ok(self.patch('app/org/policy', {'version': 1, 'extension_allowed_tiers': ['official', 'community_reviewed']}))
        self.assertEqual(body['version'], 2)
        self.assert_error(self.patch('app/org/policy', {'version': 1, 'byok_enabled': True}), 409, 'VERSION_CONFLICT')
        self.assert_error(self.patch('app/org/policy', {'version': 2, 'extension_allowed_tiers': ['bogus']}), 422, 'VALIDATION_FAILED')
        self.assertTrue(AuditEvent.objects.filter(action='org.policy.update').exists())

    def test_extension_allowlist(self):
        self.login(self.lillian, self.business)
        item = extension()
        body = self.ok(self.post('app/org/policy/extensions', {'extension_id': item.public_id}), 201)
        self.assertEqual(self.ok(self.get('app/org/policy'))['extension_allow_entries'][0]['extension']['name'], 'Jungle Scout')
        self.ok(self.delete('app/org/policy/extensions/%s' % body['id']))
        self.assertFalse(ExtensionAllowEntry.objects.exists())

    def test_audit_list_and_export(self):
        AuditEvent.objects.create(actor=self.lillian, account=self.business, action='org.quota.adjust',
                                  request_id='00000000-0000-0000-0000-000000000001', summary='市场部 3,000 → 4,000',
                                  target_type='department', target_id='x')
        self.login(self.chen, self.business)
        self.assert_error(self.get('app/org/audit'), 403, 'PERMISSION_DENIED')
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/org/audit', {'action': 'org.quota.adjust'}))
        self.assertEqual(body['items'][0]['actor']['display_name'], 'Lillian Li')
        self.assertEqual(body['items'][0]['summary'], '市场部 3,000 → 4,000')
        response = self.get('app/org/audit/export')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertIn('市场部 3,000 → 4,000', response.content.decode('utf-8-sig'))
        self.assertTrue(AuditEvent.objects.filter(action='audit.export').exists())


class OffboardingTests(AppTestCase):
    def setUp(self):
        self.mark_member = self.member(self.mark)
        self.paused = task(self.business, self.mark_member, '法国站选品调研', status='running')
        self.done = task(self.business, self.mark_member, '差评日报 · 11月4日', status='accepted')
        authorization(self.business, self.paused)
        self.automation = Automation.objects.create(
            account=self.business, owner=self.mark_member, name='差评日报', content_kind='prompt', prompt='x',
            schedule_kind='daily', schedule_time='09:00', timezone='Asia/Shanghai', state='enabled')
        self.connection = ConnectorConnection.objects.create(account=self.business, extension=extension(),
                                                             owner_user=self.mark, state='connected')
        MemoryItem.objects.create(account=self.business, user=self.mark, category='about_me', content='x',
                                  source_kind='onboarding')

    def test_preview(self):
        self.login(self.chen, self.business)
        self.assert_error(self.get('app/org/members/%d/offboarding-preview' % self.mark_member.pk), 403, 'PERMISSION_DENIED')
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/org/members/%d/offboarding-preview' % self.mark_member.pk))
        self.assertEqual(body['member']['display_name'], 'Mark Weber')
        self.assertEqual(body['tasks']['total'], 2)
        self.assertEqual((body['approvals_count'], body['automations_count'], body['connections_count']), (1, 1, 1))

    def test_remove_with_successor_hands_off(self):
        chen = self.member(self.chen)
        with self.assertRaisesRegex(ValueError, 'SUCCESSOR_INVALID'):
            remove_member(self.business, self.lillian, self.mark_member.pk, request_key='off-0',
                          successor_membership_id=self.mark_member.pk, memory_purge_acknowledged=True)
        result = remove_member(self.business, self.lillian, self.mark_member.pk, request_key='off-1',
                               successor_membership_id=chen.pk, memory_purge_acknowledged=True)
        count = offboard(self.business, self.lillian, result['removal_id'])
        self.assertEqual(count, 3)
        self.assertEqual(offboard(self.business, self.lillian, result['removal_id']), 3)
        self.paused.refresh_from_db()
        self.assertEqual((self.paused.status, self.paused.pause_reason), ('paused', 'handoff'))
        self.assertFalse(HandoffItem.objects.filter(target_id=self.done.public_id).exists())
        self.automation.refresh_from_db()
        self.assertEqual(self.automation.state, 'paused')
        self.connection.refresh_from_db()
        self.assertEqual((self.connection.state, self.connection.revoked_reason), ('revoked', 'offboarding'))
        self.assertFalse(MemoryItem.objects.filter(user=self.mark).exists())
        self.assertEqual(MemberRemoval.objects.get(pk=result['removal_id']).successor_membership_id, chen.pk)
        event = AuditEvent.objects.get(action='member.offboard')
        self.assertEqual(event.summary, 'Mark Weber → 接手人 Chen Yu')
        self.assertTrue(AuditEvent.objects.filter(action='memory.purge').exists())

    def test_remove_api_returns_handoff_count(self):
        self.login(self.lillian, self.business)
        with patch('backend.account.require_sensitive'):
            body = self.ok(self.post('billing/organization/members/remove', {
                'membership_id': self.mark_member.pk, 'request_key': 'off-api', 'confirm': True,
                'successor_membership_id': self.member(self.chen).pk, 'memory_purge_acknowledged': True}))
            self.assertEqual(body['handoff_item_count'], 3)
            bad = self.post('billing/organization/members/remove', {
                'membership_id': self.member(self.chen).pk, 'request_key': 'off-api-2', 'confirm': True,
                'successor_membership_id': self.mark_member.pk, 'memory_purge_acknowledged': True})
            self.assert_error(bad, 422, 'SUCCESSOR_INVALID')
