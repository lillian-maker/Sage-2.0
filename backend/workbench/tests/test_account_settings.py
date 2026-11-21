"""App settings: notification matrix (C-D-19), memories (C-D-16), authorization grants (C-D-18)."""
import os
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from backend.models import AccountProfile, AccountSession, Membership
from backend.workbench.models import (AuthorizationGrant, AuthorizationRequest, Expert, Extension, MemoryItem,
                                      NotificationPreference, Task, Workspace)
from backend.workbench.services.account import NOTIFICATION_CHANNELS, NOTIFICATION_TYPES
from backend.workbench.tests.base import AppTestCase


def full_matrix(overrides=None):
    overrides = overrides or {}
    return [{'type': t, 'channel': c, 'enabled': overrides.get((t, c), c == 'in_app' or (c == 'push' and t == 'needs_action'))}
            for t in NOTIFICATION_TYPES for c in NOTIFICATION_CHANNELS]


class NotificationSettingsTests(AppTestCase):
    def setUp(self):
        self.login(self.lillian, self.business)

    def test_defaults(self):
        items = self.ok(self.get('app/settings/notifications'))['items']
        self.assertEqual(len(items), 12)
        self.assertEqual([(i['type'], i['channel']) for i in items][:3],
                         [('needs_action', 'in_app'), ('needs_action', 'push'), ('needs_action', 'email')])
        enabled = {(i['type'], i['channel']): i['enabled'] for i in items}
        self.assertTrue(all(enabled[(t, 'in_app')] for t in NOTIFICATION_TYPES))
        self.assertEqual([t for t in NOTIFICATION_TYPES if enabled[(t, 'push')]], ['needs_action'])
        self.assertFalse(any(enabled[(t, 'email')] for t in NOTIFICATION_TYPES))
        self.assertTrue(all(i['is_default'] for i in items))
        self.assertEqual(set(items[0]), {'type', 'channel', 'enabled', 'is_default'})

    def test_put_full_matrix(self):
        body = self.ok(self.put('app/settings/notifications', {'items': full_matrix({('billing', 'email'): True,
                                                                                       ('needs_action', 'push'): False})}))
        state = {(i['type'], i['channel']): (i['enabled'], i['is_default']) for i in body['items']}
        self.assertEqual(state[('billing', 'email')], (True, False))
        self.assertEqual(state[('needs_action', 'push')], (False, False))
        self.assertEqual(state[('task_completed', 'in_app')], (True, True))
        self.assertEqual(self.ok(self.get('app/settings/notifications')), body)
        self.assertEqual(NotificationPreference.objects.filter(user=self.lillian).count(), 2)
        self.ok(self.put('app/settings/notifications', {'items': full_matrix()}))
        self.assertFalse(NotificationPreference.objects.filter(user=self.lillian).exists())

    def test_scoped_to_account_and_user(self):
        self.ok(self.put('app/settings/notifications', {'items': full_matrix({('billing', 'email'): True})}))
        self.login(self.lillian, self.personal)
        self.assertTrue(all(i['is_default'] for i in self.ok(self.get('app/settings/notifications'))['items']))
        self.login(self.chen, self.business)
        self.assertTrue(all(i['is_default'] for i in self.ok(self.get('app/settings/notifications'))['items']))

    def test_validation(self):
        matrix = full_matrix()
        bad = [matrix[:11], matrix + matrix[:1], matrix[:11] + [matrix[0]],
               matrix[:11] + [{'type': 'billing', 'channel': 'sms', 'enabled': True}],
               matrix[:11] + [{'type': 'nope', 'channel': 'email', 'enabled': True}],
               matrix[:11] + [{'type': 'billing', 'channel': 'email', 'enabled': 'yes'}],
               matrix[:11] + [{'type': 'billing', 'channel': 'email', 'enabled': True, 'x': 1}],
               'all', None]
        for items in bad:
            self.assert_error(self.put('app/settings/notifications', {'items': items}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.put('app/settings/notifications', {'items': matrix, 'x': 1}), 422, 'INVALID_REQUEST')
        self.assertFalse(NotificationPreference.objects.exists())

    def test_account_header_and_membership(self):
        self.assert_error(self.put('app/settings/notifications', {'items': full_matrix()}, account=False), 409,
                          'ACCOUNT_CHANGED')
        self.login(self.anna, self.business)
        self.assert_error(self.get('app/settings/notifications'), 404, 'NOT_FOUND')


class WorldMixin:
    def world(self):
        lead = self.member(self.lillian)
        self.workspace = Workspace.objects.create(account=self.business, name='德国市场进入', created_from='blank',
                                                  created_by=self.lillian)
        self.task = Task.objects.create(account=self.business, title='Q4 广告复盘', creator=lead, owner=lead,
                                        model_mode='auto')
        self.expert = Expert.objects.create(source='system', group='role', name='调研员', title='调研员',
                                            responsibilities='调研', version_label='v1', share_scope='account',
                                            communication_style='concise', uncertainty_policy='ask_first')
        self.jungle = Extension.objects.create(kind='connector', name='Jungle Scout', description='d',
                                               publisher_tier='official', latest_version='1')
        self.seller = Extension.objects.create(kind='connector', name='Amazon Seller Central', description='d',
                                               publisher_tier='official', latest_version='1')

    def grant(self, user, extension, *, task=None, workspace=None, account=None, created_at=None, revoked=False):
        account = account or self.business
        request = AuthorizationRequest.objects.create(account=account, task=task, requested_by_expert=self.expert,
                                                      extension=extension, action_kind='read_query', scope_text='s',
                                                      validity='this_task', decision='approved')
        row = AuthorizationGrant.objects.create(account=account, user=user, scope='task' if task else 'workspace',
                                                task=task, workspace=workspace, extension=extension,
                                                action_kind='read_query', source_request=request,
                                                revoked_at=timezone.now() if revoked else None)
        if created_at:
            AuthorizationGrant.objects.filter(pk=row.pk).update(created_at=created_at)
        return row


class MemoryTests(WorldMixin, AppTestCase):
    def setUp(self):
        self.login(self.lillian, self.business)
        self.world()
        now = timezone.now()

        def add(category, content, days, user=None, account=None, task=None):
            row = MemoryItem.objects.create(account=account or self.business, user=user or self.lillian,
                                            category=category, content=content,
                                            source_kind='task' if task else 'onboarding', source_task=task)
            MemoryItem.objects.filter(pk=row.pk).update(created_at=now - timedelta(days=days))
            return row
        self.lesson = add('lessons', 'L1', 0, task=self.task)
        self.recent = add('recent_projects', 'R1', 1)
        self.about_old = add('about_me', 'A-old', 5)
        self.about_new = add('about_me', 'A-new', 2)
        self.foreign = add('about_me', 'chen', 0, user=self.chen)
        self.other_account = add('about_me', 'personal', 0, account=self.personal)

    def test_list_grouped_with_counts(self):
        body = self.ok(self.get('app/memories'))
        self.assertEqual([i['content'] for i in body['items']], ['A-new', 'A-old', 'R1', 'L1'])
        self.assertEqual(body['category_counts'], {'about_me': 2, 'recent_projects': 1, 'lessons': 1})
        self.assertEqual((body['offset'], body['total'], body['next_offset']), (0, 4, None))
        lesson = body['items'][3]
        self.assertEqual(set(lesson), {'id', 'category', 'content', 'source_kind', 'source_task', 'created_at',
                                       'edited_at'})
        self.assertEqual(lesson['source_task'], {'id': self.task.public_id, 'title': 'Q4 广告复盘'})
        self.assertIsNone(body['items'][0]['source_task'])
        self.assertIsNone(lesson['edited_at'])

    def test_category_filter_keeps_counts(self):
        body = self.ok(self.get('app/memories', {'category': 'about_me'}))
        self.assertEqual([i['content'] for i in body['items']], ['A-new', 'A-old'])
        self.assertEqual(body['total'], 2)
        self.assertEqual(body['category_counts'], {'about_me': 2, 'recent_projects': 1, 'lessons': 1})
        self.assert_error(self.get('app/memories', {'category': 'misc'}), 422, 'INVALID_REQUEST')
        self.assert_error(self.get('app/memories', {'sort': 'x'}), 422, 'INVALID_REQUEST')

    def test_patch(self):
        body = self.ok(self.patch('app/memories/' + self.about_new.public_id, {'content': '  称呼我 Lily  '}))
        self.assertEqual(body['content'], '称呼我 Lily')
        self.assertIsNotNone(body['edited_at'])
        self.about_new.refresh_from_db()
        self.assertEqual(self.about_new.content, '称呼我 Lily')
        for value in ('', '   ', 'x' * 301, 5, None):
            self.assert_error(self.patch('app/memories/' + self.about_new.public_id, {'content': value}), 422,
                              'VALIDATION_FAILED')
        self.assert_error(self.patch('app/memories/' + self.about_new.public_id, {'content': 'a', 'x': 1}), 422,
                          'INVALID_REQUEST')
        self.ok(self.patch('app/memories/' + self.about_new.public_id, {'content': 'x' * 300}))

    def test_patch_delete_foreign_not_found(self):
        for row in (self.foreign, self.other_account):
            self.assert_error(self.patch('app/memories/' + row.public_id, {'content': 'x'}), 404, 'NOT_FOUND')
            self.assert_error(self.delete('app/memories/' + row.public_id), 404, 'NOT_FOUND')
        self.assertEqual(MemoryItem.objects.count(), 6)

    def test_delete(self):
        response = self.delete('app/memories/' + self.recent.public_id)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(MemoryItem.objects.filter(pk=self.recent.pk).exists())
        self.assertEqual(self.ok(self.get('app/memories'))['category_counts']['recent_projects'], 0)
        self.assert_error(self.delete('app/memories/' + self.recent.public_id), 404, 'NOT_FOUND')

    def test_write_needs_account_header(self):
        self.assert_error(self.delete('app/memories/' + self.recent.public_id, account=False), 409, 'ACCOUNT_CHANGED')

    def test_pagination(self):
        MemoryItem.objects.bulk_create([MemoryItem(account=self.business, user=self.lillian, category='lessons',
                                                   content='bulk %d' % i, source_kind='task') for i in range(50)])
        first = self.ok(self.get('app/memories'))
        self.assertEqual((len(first['items']), first['total'], first['next_offset']), (50, 54, 50))
        second = self.ok(self.get('app/memories', {'offset': '50'}))
        self.assertEqual((len(second['items']), second['next_offset']), (4, None))


class GrantTests(WorldMixin, AppTestCase):
    def setUp(self):
        self.login(self.lillian, self.business)
        self.world()
        now = timezone.now()
        self.ws_grant = self.grant(self.lillian, self.jungle, workspace=self.workspace, created_at=now)
        self.task_grant = self.grant(self.lillian, self.seller, task=self.task, created_at=now - timedelta(days=10))
        self.revoked = self.grant(self.lillian, self.seller, workspace=self.workspace, revoked=True)
        self.foreign = self.grant(self.chen, self.jungle, workspace=self.workspace)

    def test_list(self):
        body = self.ok(self.get('app/grants'))
        self.assertEqual([i['id'] for i in body['items']], [self.ws_grant.public_id, self.task_grant.public_id])
        self.assertEqual((body['offset'], body['total'], body['next_offset']), (0, 2, None))
        first, second = body['items']
        self.assertEqual(set(first), {'id', 'extension', 'action_kind', 'scope', 'scope_text', 'task', 'workspace',
                                      'created_at'})
        # Screen 74 labels each grant with the request's wording ("只读查询" / "读取订单").
        self.assertEqual(first['scope_text'], 's')
        self.assertEqual(first['extension'], {'id': self.jungle.public_id, 'name': 'Jungle Scout'})
        self.assertEqual((first['scope'], first['task'], first['workspace']),
                         ('workspace', None, {'id': self.workspace.public_id, 'name': '德国市场进入'}))
        self.assertEqual((second['scope'], second['task'], second['workspace'], second['action_kind']),
                         ('task', {'id': self.task.public_id, 'title': 'Q4 广告复盘'}, None, 'read_query'))
        self.assert_error(self.get('app/grants', {'scope': 'task'}), 422, 'INVALID_REQUEST')

    def test_other_account_hidden(self):
        self.login(self.lillian, self.personal)
        self.assertEqual(self.ok(self.get('app/grants'))['total'], 0)

    def test_revoke(self):
        body = self.ok(self.delete('app/grants/' + self.ws_grant.public_id))
        self.assertEqual(body['id'], self.ws_grant.public_id)
        self.assertIsNotNone(body['revoked_at'])
        self.ws_grant.refresh_from_db()
        self.assertIsNotNone(self.ws_grant.revoked_at)
        self.assertEqual(self.ok(self.get('app/grants'))['total'], 1)
        self.assert_error(self.delete('app/grants/' + self.ws_grant.public_id), 404, 'NOT_FOUND')

    def test_revoke_foreign_or_revoked_not_found(self):
        for row in (self.foreign, self.revoked):
            self.assert_error(self.delete('app/grants/' + row.public_id), 404, 'NOT_FOUND')
        self.assertIsNone(AuthorizationGrant.objects.get(pk=self.foreign.pk).revoked_at)
        self.assert_error(self.delete('app/grants/' + self.task_grant.public_id, account=False), 409,
                          'ACCOUNT_CHANGED')

    def test_method_not_allowed(self):
        self.assertEqual(self.post('app/grants', {}).status_code, 405)


class AccountSeedTests(TestCase):
    def run_seed(self):
        call_command('seed_app', '--only', 'core', '--only', 'account', stdout=StringIO())

    def test_seed_idempotent_and_matches_figma(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('SAGE_SEED_PASSWORD', None)
            self.run_seed()
            self.run_seed()
        member = Membership.objects.get(user__email='lillian@sagedemo.com', account__kind='business')
        lillian = member.user
        self.assertEqual(lillian.first_name, 'Lillian Li')
        self.assertFalse(lillian.has_usable_password())
        profile = AccountProfile.objects.get(user=lillian)
        self.assertEqual((profile.preferred_name, profile.assistant_name, profile.business_markets,
                          profile.business_channels, profile.business_categories, profile.idle_lock_minutes),
                         ('Lillian', '', ['德国', '美国'], ['Amazon', '独立站'], ['家用小家电'], 30))
        self.assertEqual((profile.dnd_start.strftime('%H:%M'), profile.dnd_end.strftime('%H:%M')), ('22:00', '08:00'))
        self.assertIsNotNone(profile.onboarding_completed_at)
        memories = MemoryItem.objects.filter(user=lillian, account=member.account)
        self.assertEqual(memories.count(), 6)
        self.assertEqual({c: memories.filter(category=c).count() for c in ('about_me', 'recent_projects', 'lessons')},
                         {'about_me': 3, 'recent_projects': 2, 'lessons': 1})
        grants = AuthorizationGrant.objects.filter(user=lillian, revoked_at__isnull=True)
        self.assertEqual({(g.extension.name, g.scope, g.task.title if g.task else g.workspace.name) for g in grants},
                         {('Jungle Scout', 'workspace', '德国市场进入'), ('Amazon Seller Central', 'task', 'Q4 广告复盘')})
        sessions = AccountSession.objects.filter(user=lillian, revoked_at__isnull=True)
        self.assertEqual([(s.device_name, s.app_version, s.location_label) for s in sessions],
                         [('Mac mini', '0.1.0', '杭州')])

    def test_seed_password_from_environment(self):
        with patch.dict(os.environ, {'SAGE_SEED_PASSWORD': 'Fixture!Seed42'}):
            self.run_seed()
        member = Membership.objects.get(user__email='lillian@sagedemo.com', account__kind='business')
        self.assertTrue(member.user.check_password('Fixture!Seed42'))

    def test_only_account_runs_core_first(self):
        call_command('seed_app', '--only', 'account', stdout=StringIO())
        self.assertEqual(MemoryItem.objects.count(), 6)
