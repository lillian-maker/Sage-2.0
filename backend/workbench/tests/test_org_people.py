"""Contract tests: app/org/people and app/org/invitations/{id}/profile (75/78 屏，D41).

BusinessInvitation and the commerce member list carry no display name or user id, so the workbench keeps a side
table (OrgPersonProfile) keyed by invitation id or membership id. The invite dialog writes the invitee's name after
the commerce invitation is created; workbench member flows (department move) record the member's user id. GET
app/org/people returns both maps plus the caller's own membership/user id so the page can mark "（你）".
"""
from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from backend.commerce.models import BusinessInvitation, Department
from backend.models import Membership
from backend.workbench.models import OrgPersonProfile
from backend.workbench.tests.base import AppTestCase


class SeededPeopleTests(AppTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        call_command('seed_app', only=['core', 'billing'], stdout=StringIO())
        seeded = Membership.objects.get(user__email='lillian@sagedemo.com', account__kind='business')
        cls.org, cls.owner = seeded.account, seeded.user
        cls.invite = BusinessInvitation.objects.get(account=cls.org, identifier='lishan@sage.demo')

    def test_seed_is_idempotent(self):
        before = OrgPersonProfile.objects.count()
        call_command('seed_app', only=['core', 'billing'], stdout=StringIO())
        self.assertEqual(OrgPersonProfile.objects.count(), before)

    def test_people_has_invite_name_and_me(self):
        self.login(self.owner, self.org)
        body = self.ok(self.get('app/org/people'))
        self.assertEqual(set(body), {'me', 'invitations', 'members'})
        me = Membership.objects.get(account=self.org, user=self.owner)
        self.assertEqual(body['me'], {'membership_id': me.pk, 'user_id': self.owner.pk})
        invites = {row['invitation_id']: row for row in body['invitations']}
        self.assertEqual(invites[str(self.invite.pk)], {'invitation_id': str(self.invite.pk), 'display_name': 'Li Shan',
                                                        'user_id': None})
        members = {row['membership_id']: row for row in body['members']}
        self.assertEqual(members[me.pk]['user_id'], self.owner.pk)
        self.assertEqual(members[me.pk]['display_name'], 'Lillian Li')

    def test_set_invitation_profile(self):
        self.login(self.owner, self.org)
        url = 'app/org/invitations/%s/profile' % self.invite.pk
        body = self.ok(self.put(url, {'display_name': '  李珊 '}))
        self.assertEqual(body, {'invitation_id': str(self.invite.pk), 'display_name': '李珊', 'user_id': None})
        names = {row['invitation_id']: row['display_name'] for row in self.ok(self.get('app/org/people'))['invitations']}
        self.assertEqual(names[str(self.invite.pk)], '李珊')

    def test_invalid_profile(self):
        self.login(self.owner, self.org)
        url = 'app/org/invitations/%s/profile' % self.invite.pk
        self.assert_error(self.put(url, {'display_name': ' '}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.put(url, {'display_name': 'x' * 81}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.put(url, {}), 422, 'INVALID_REQUEST')
        missing = 'app/org/invitations/00000000-0000-4000-8000-000000000000/profile'
        self.assert_error(self.put(missing, {'display_name': 'A'}), 404, 'NOT_FOUND')
        self.assert_error(self.put('app/org/invitations/not-a-uuid/profile', {'display_name': 'A'}), 404, 'NOT_FOUND')

    def test_plain_member_cannot_read_or_write(self):
        mark = get_user_model().objects.get(email='mark@sage.demo')
        self.login(mark, self.org)
        self.assert_error(self.get('app/org/people'), 403, 'PERMISSION_DENIED')
        url = 'app/org/invitations/%s/profile' % self.invite.pk
        self.assert_error(self.put(url, {'display_name': 'A'}), 403, 'PERMISSION_DENIED')


class MemberFlowTests(AppTestCase):
    def test_department_move_records_member_profile(self):
        self.login(self.lillian, self.business)
        department = Department.objects.create(account=self.business, name='运营部')
        mark = self.member(self.mark)
        self.assertFalse(OrgPersonProfile.objects.filter(membership=mark).exists())
        self.ok(self.post('app/org/members/%s/department' % mark.pk,
                          {'department_id': str(department.pk), 'is_lead': False, 'request_key': 'move-mark-1'}))
        profile = OrgPersonProfile.objects.get(membership=mark)
        self.assertEqual((profile.user_id, profile.display_name), (self.mark.pk, 'Mark Weber'))
        members = {row['membership_id']: row for row in self.ok(self.get('app/org/people'))['members']}
        self.assertEqual(members[mark.pk]['user_id'], self.mark.pk)

    def test_other_account_invitation_is_not_found(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.put('app/org/invitations/00000000-0000-4000-8000-000000000001/profile',
                                   {'display_name': 'A'}), 404, 'NOT_FOUND')

    def test_personal_account_is_denied(self):
        self.login(self.lillian)
        self.assert_error(self.get('app/org/people'), 403, 'PERMISSION_DENIED')

PINNED = '2026-11-19T10:53:00+08:00'


class InvitationClockTests(AppTestCase):
    """Invitation expiry follows the pinned workbench clock in development/test only (邀请中 · 23 小时后失效)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        call_command('seed_app', only=['core', 'billing'], stdout=StringIO())
        seeded = Membership.objects.get(user__email='lillian@sagedemo.com', account__kind='business')
        cls.org, cls.owner = seeded.account, seeded.user
        cls.invite = BusinessInvitation.objects.get(account=cls.org, identifier='lishan@sage.demo')
        pinned = datetime.fromisoformat(PINNED)
        BusinessInvitation.objects.filter(pk=cls.invite.pk).update(created_at=pinned - timedelta(hours=1),
                                                                   expires_at=pinned + timedelta(hours=23))

    def status(self):
        from backend.commerce.subscriptions import _pending_invitations, business_invitations
        rows = {row['id']: row['status'] for row in business_invitations(self.org, actor=self.owner)['items']}
        pending = {str(row.pk) for row in _pending_invitations(self.org, timezone.now())}
        return rows[str(self.invite.pk)], str(self.invite.pk) in pending

    def test_pinned_clock_keeps_invitation_pending(self):
        for env in ('development', 'test'):
            with patch.dict('os.environ', {'SAGE_SEED_NOW': PINNED}), override_settings(ENVIRONMENT=env):
                self.assertEqual(self.status(), ('pending', True))

    def test_production_and_unpinned_use_real_clock(self):
        with patch.dict('os.environ', {'SAGE_SEED_NOW': PINNED}), override_settings(ENVIRONMENT='production'):
            self.assertEqual(self.status(), ('expired', False))
        with patch.dict('os.environ', {'SAGE_SEED_NOW': ''}), override_settings(ENVIRONMENT='development'):
            self.assertEqual(self.status(), ('expired', False))
