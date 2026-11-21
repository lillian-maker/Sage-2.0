"""SAGE_WORKBENCH_FAKE_ADAPTERS hands out deterministic stand-ins only in development/test."""
from unittest import mock

from django.test import SimpleTestCase, override_settings

from backend.workbench.services import extensions, fake_adapters
from backend.workbench.tests.test_experts_extensions import ExtensionFixtures

NAMES = ('WORKBENCH_EXPERT_DRAFT_GENERATOR', 'WORKBENCH_EXPERT_AVATAR_GENERATOR', 'WORKBENCH_EXPERT_TRY_RUNNER',
         'WORKBENCH_CONNECTOR_AUTHORIZER')
UNSET = {name: None for name in NAMES}
FLAG_ON = {'SAGE_WORKBENCH_FAKE_ADAPTERS': '1'}


def configured(**kwargs):
    return 'configured'


class FakeAdapterSwitchTests(SimpleTestCase):
    @override_settings(ENVIRONMENT='test', **UNSET)
    def test_flag_off_means_unavailable(self):
        with mock.patch.dict('os.environ', {'SAGE_WORKBENCH_FAKE_ADAPTERS': ''}):
            self.assertEqual([extensions.adapter(name) for name in NAMES], [None] * 4)

    def test_flag_on_in_development_and_test(self):
        for environment in ('development', 'test'):
            with override_settings(ENVIRONMENT=environment, **UNSET), mock.patch.dict('os.environ', FLAG_ON):
                self.assertEqual([extensions.adapter(name) for name in NAMES],
                                 [fake_adapters.ADAPTERS[name] for name in NAMES])

    def test_never_in_production(self):
        for environment in ('production', 'staging', ''):
            with override_settings(ENVIRONMENT=environment, **UNSET), mock.patch.dict('os.environ', FLAG_ON):
                self.assertEqual([extensions.adapter(name) for name in NAMES], [None] * 4)

    @override_settings(ENVIRONMENT='development', WORKBENCH_EXPERT_TRY_RUNNER=configured)
    def test_configured_setting_wins(self):
        with mock.patch.dict('os.environ', FLAG_ON):
            self.assertIs(extensions.adapter('WORKBENCH_EXPERT_TRY_RUNNER'), configured)

    def test_fakes_are_deterministic(self):
        expert = mock.Mock(public_id='01ABC', name='望野')
        expert.name = '望野'
        connection = mock.Mock(public_id='01CONN')
        self.assertEqual(fake_adapters.avatar_generator(expert, None), fake_adapters.avatar_generator(expert, None))
        self.assertRegex(fake_adapters.avatar_generator(expert, None),
                         r'^digital-employees/assets/light/512/AGT-0\d\d\.webp$')
        self.assertEqual(fake_adapters.try_runner(expert, '你好', None, None),
                         {'reply': '（本地演示回复）望野 收到：你好', 'credits_used': 0})
        self.assertEqual(fake_adapters.connector_authorizer(connection, None), {'credential_ref': 'fake:01CONN'})
        draft = fake_adapters.draft_generator('把中文  内容改写成德语', None, None)
        self.assertEqual(draft['title'], '把中文 内容改写成德语')


@override_settings(ENVIRONMENT='test', **UNSET)
class FakeConnectorFlowTests(ExtensionFixtures):
    def test_connect_through_fake_authorizer(self):
        self.login(self.lillian, self.business)
        url = 'app/connections/%s/connect' % self.jungle_conn.public_id
        self.assert_error(self.post(url, {}), 503, 'SOURCE_UNAVAILABLE')
        with mock.patch.dict('os.environ', FLAG_ON):
            body = self.ok(self.post(url, {}))
        self.assertEqual(body['connection']['state'], 'connected')
        self.jungle_conn.refresh_from_db()
        self.assertEqual(self.jungle_conn.credential_ref, 'fake:%s' % self.jungle_conn.public_id)
