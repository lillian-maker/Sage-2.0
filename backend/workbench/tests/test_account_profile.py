"""account/profile appended keys (C-D-5) and account/sessions device fields (C-D-6)."""
from datetime import timedelta

from django.contrib.sessions.models import Session
from django.utils import timezone

from backend.models import AccountProfile, AccountSession, AuditEvent
from backend.workbench.tests.base import AppTestCase

NEW_KEYS = {'assistant_name', 'preferred_name', 'business_markets', 'business_channels', 'business_categories',
            'onboarding_completed_at', 'theme_style', 'appearance_mode', 'density', 'language',
            'memory_auto_enabled', 'memory_model', 'default_model', 'complexity_hint_enabled', 'ext_auto_invoke',
            'ext_auto_update', 'ext_show_needs_connection', 'dnd_enabled', 'dnd_start', 'dnd_end', 'badge_enabled',
            'two_step_on_new_device', 'idle_lock_minutes'}
OLD_KEYS = {'display_name', 'phone', 'password_reset_method', 'email', 'avatar_url', 'email_read_only'}


class ProfileTests(AppTestCase):
    def setUp(self):
        self.login(self.lillian)

    def profile(self):
        return AccountProfile.objects.get(user=self.lillian)

    def test_get_defaults(self):
        body = self.ok(self.get('account/profile'))
        self.assertEqual(set(body), OLD_KEYS | NEW_KEYS)
        self.assertEqual(body['email'], 'lillian@example.com')
        self.assertEqual(body['assistant_name'], 'Sage 助手')
        self.assertEqual(body['preferred_name'], 'Lillian')
        self.assertEqual((body['theme_style'], body['appearance_mode'], body['density'], body['language']),
                         ('business_steady', 'system', 'theme', 'system'))
        self.assertEqual((body['default_model'], body['memory_model']), ('auto', 'standard'))
        self.assertEqual(body['business_markets'], [])
        self.assertIsNone(body['dnd_start'])
        self.assertIsNone(body['idle_lock_minutes'])
        self.assertIsNone(body['onboarding_completed_at'])
        self.assertIs(body['two_step_on_new_device'], False)

    def test_patch_many_keys(self):
        data = {'business_markets': ['德国', ' 美国 ', '德国'], 'business_channels': ['Amazon', '独立站'],
                'business_categories': ['家用小家电'], 'theme_style': 'neo_electric', 'appearance_mode': 'dark',
                'density': 'compact', 'language': 'zh-Hans', 'assistant_name': '小智', 'preferred_name': 'Lily',
                'dnd_enabled': True, 'dnd_start': '22:00', 'dnd_end': '08:00', 'badge_enabled': False,
                'memory_auto_enabled': False, 'memory_model': 'deep', 'default_model': 'deep',
                'complexity_hint_enabled': False, 'ext_auto_invoke': False, 'ext_auto_update': False,
                'ext_show_needs_connection': False, 'two_step_on_new_device': True, 'idle_lock_minutes': 30,
                'display_name': 'Lillian Li'}
        body = self.ok(self.patch('account/profile', data))
        self.assertEqual(body['business_markets'], ['德国', '美国'])
        self.assertEqual((body['dnd_start'], body['dnd_end']), ('22:00', '08:00'))
        self.assertEqual(body['display_name'], 'Lillian Li')
        self.assertNotIn('onboarding_completed', body)
        for key in set(data) - {'business_markets', 'dnd_start', 'dnd_end'}:
            self.assertEqual(body[key], data[key], key)
        again = self.ok(self.get('account/profile'))
        self.assertEqual(again, body)
        self.assertTrue(AuditEvent.objects.filter(action='profile.settings_updated').exists())

    def test_reset_nullable_values(self):
        self.ok(self.patch('account/profile', {'assistant_name': '小智', 'preferred_name': 'Lily',
                                               'idle_lock_minutes': 15, 'dnd_start': '22:00', 'dnd_end': '08:00'}))
        body = self.ok(self.patch('account/profile', {'assistant_name': None, 'preferred_name': '',
                                                      'idle_lock_minutes': None, 'dnd_start': None}))
        self.assertEqual((body['assistant_name'], body['preferred_name']), ('Sage 助手', 'Lillian'))
        self.assertIsNone(body['idle_lock_minutes'])
        self.assertIsNone(body['dnd_start'])

    def test_validation_failed(self):
        cases = [{'theme_style': 'retro'}, {'appearance_mode': 'auto'}, {'density': 'tiny'}, {'language': 'fr'},
                 {'memory_model': 'fast'}, {'default_model': 'gpt'}, {'assistant_name': '小'},
                 {'assistant_name': '一二三四五六七八九'}, {'assistant_name': 3}, {'preferred_name': 'x' * 41},
                 {'business_markets': '德国'}, {'business_markets': ['x' * 21]}, {'business_markets': ['']},
                 {'business_channels': [str(i) for i in range(21)]}, {'business_categories': [1]},
                 {'dnd_start': '24:00'}, {'dnd_start': '7:00'}, {'dnd_end': 800},
                 {'dnd_start': '22:00', 'dnd_end': '22:00'}, {'idle_lock_minutes': 20},
                 {'idle_lock_minutes': '30'}, {'idle_lock_minutes': True}, {'badge_enabled': 'true'},
                 {'dnd_enabled': 1}, {'onboarding_completed': False}, {'onboarding_completed': 'yes'}]
        for data in cases:
            self.assert_error(self.patch('account/profile', data), 422, 'VALIDATION_FAILED')
        self.assertFalse(AccountProfile.objects.filter(user=self.lillian).exclude(theme_style='business_steady').exists())

    def test_dnd_same_as_stored_end_rejected(self):
        self.ok(self.patch('account/profile', {'dnd_start': '22:00', 'dnd_end': '08:00'}))
        self.assert_error(self.patch('account/profile', {'dnd_start': '08:00'}), 422, 'VALIDATION_FAILED')

    def test_unknown_and_empty_body(self):
        self.assert_error(self.patch('account/profile', {'onboarding': True}), 422, 'INVALID_REQUEST')
        self.assert_error(self.patch('account/profile', {}), 422, 'INVALID_REQUEST')
        self.assert_error(self.patch('account/profile', {'email': 'x@example.com'}), 422, 'INVALID_REQUEST')
        self.assert_error(self.patch('account/profile', {'display_name': ''}), 422, 'INVALID_REQUEST')

    def test_onboarding_completed_is_written_once(self):
        first = self.ok(self.patch('account/profile', {'onboarding_completed': True, 'theme_style': 'minimal_focus'}))
        self.assertIsNotNone(first['onboarding_completed_at'])
        second = self.ok(self.patch('account/profile', {'onboarding_completed': True}))
        self.assertEqual(second['onboarding_completed_at'], first['onboarding_completed_at'])
        self.assertEqual(self.ok(self.get('auth/session'))['user']['onboarding_completed_at'],
                         first['onboarding_completed_at'])

    def test_two_step_disable_needs_sensitive_confirmation(self):
        self.ok(self.patch('account/profile', {'two_step_on_new_device': True}))
        self.assert_error(self.patch('account/profile', {'two_step_on_new_device': False}), 403,
                          'SENSITIVE_CONFIRMATION_REQUIRED')
        self.assertTrue(self.profile().two_step_on_new_device)
        AccountSession.objects.filter(session_key=self.client.session.session_key).update(
            sensitive_verified_at=timezone.now() - timedelta(minutes=16))
        self.assert_error(self.patch('account/profile', {'two_step_on_new_device': False}), 403,
                          'SENSITIVE_CONFIRMATION_REQUIRED')
        AccountSession.objects.filter(session_key=self.client.session.session_key).update(
            sensitive_verified_at=timezone.now())
        self.assertIs(self.ok(self.patch('account/profile', {'two_step_on_new_device': False}))
                      ['two_step_on_new_device'], False)

    def test_profile_is_per_user(self):
        self.ok(self.patch('account/profile', {'theme_style': 'neo_electric'}))
        self.login(self.chen)
        self.assertEqual(self.ok(self.get('account/profile'))['theme_style'], 'business_steady')

    def test_anonymous(self):
        self.client.logout()
        self.assert_error(self.get('account/profile'), 401, 'AUTH_REQUIRED')


class SessionListTests(AppTestCase):
    def setUp(self):
        self.login(self.lillian)

    def current(self):
        return AccountSession.objects.get(session_key=self.client.session.session_key)

    def test_items_and_paging_keys(self):
        body = self.ok(self.get('account/sessions', HTTP_X_SAGE_CLIENT='MacBook Pro; 0.1.0'))
        self.assertEqual((body['offset'], body['total'], body['next_offset']), (0, 1, None))
        item = body['items'][0]
        self.assertEqual(set(item), {'id', 'client', 'location', 'ip', 'created_at', 'is_current', 'device_name',
                                     'app_version', 'last_seen_at'})
        self.assertEqual((item['device_name'], item['app_version'], item['location'], item['is_current']),
                         ('MacBook Pro', '0.1.0', '未知', True))
        self.assertIsNotNone(item['last_seen_at'])

    def test_location_label_and_other_device(self):
        session = Session.objects.create(session_key='other-device-key', session_data='',
                                         expire_date=timezone.now() + timedelta(days=1))
        seen = timezone.now() - timedelta(days=3)
        AccountSession.objects.create(user=self.lillian, session_key=session.pk, client='Sage', device_name='Mac mini',
                                      app_version='0.1.0', location_label='杭州', last_seen_at=seen)
        body = self.ok(self.get('account/sessions'))
        self.assertEqual(body['total'], 2)
        other = next(item for item in body['items'] if not item['is_current'])
        self.assertEqual((other['device_name'], other['location'], other['last_seen_at']),
                         ('Mac mini', '杭州', seen.isoformat()))

    def test_malformed_client_header_ignored(self):
        for value in ('MacBook Pro', ';0.1.0', 'x' * 81 + '; 1', 'Mac\x01; 1'):
            self.ok(self.get('account/sessions', HTTP_X_SAGE_CLIENT=value))
        self.assertIsNone(self.current().device_name)

    def test_last_seen_written_at_most_every_five_minutes(self):
        self.ok(self.get('account/sessions'))
        recent = timezone.now() - timedelta(minutes=2)
        AccountSession.objects.filter(pk=self.current().pk).update(last_seen_at=recent)
        self.ok(self.get('account/sessions'))
        self.assertEqual(self.current().last_seen_at, recent)
        stale = timezone.now() - timedelta(minutes=6)
        AccountSession.objects.filter(pk=self.current().pk).update(last_seen_at=stale)
        self.ok(self.get('account/sessions'))
        self.assertGreater(self.current().last_seen_at, stale)

    def test_offset_validation(self):
        self.assert_error(self.get('account/sessions', {'offset': '-1'}), 422, 'INVALID_REQUEST')
        body = self.ok(self.get('account/sessions', {'offset': '50'}))
        self.assertEqual((body['items'], body['offset'], body['total']), ([], 50, 1))
