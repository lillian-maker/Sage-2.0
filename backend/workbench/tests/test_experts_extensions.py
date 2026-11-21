from django.test import override_settings

from backend.workbench.models import (AccountPolicy, ConnectorConnection, ExtensionAllowEntry, ExtensionInstall,
                                      InboxSource)
from backend.workbench.tests.base import AppTestCase
from backend.workbench.tests import test_experts_support as f

SUPPORT = 'backend.workbench.tests.test_experts_support.'


class ExtensionFixtures(AppTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ppt = f.extension('PPT 生成', kind='plugin', version='v2.1', description='把报告和结论排成可编辑的演示文稿。')
        cls.analysis = f.extension('竞品格局分析', version='v1.3',
                                   prerequisite_inputs=['竞品清单或类目页链接'], outputs=['竞品格局报告（文档）'],
                                   acceptance_template=['覆盖前 20 名', '注明数据日期'])
        cls.pricing = f.extension('定价策略', version='v1.1')
        cls.jungle = f.extension('Jungle Scout', kind='connector', tier='third_party', version='v1.8',
                                 billing_note='按次计费')
        cls.estimate = f.extension('销量估算', required_connector=cls.jungle)
        cls.mail = f.extension('企业邮箱', kind='connector', version='v1.2')
        cls.agent = f.extension('售后工单智能体', kind='agent', tier='enterprise', account=cls.business)
        cls.market_only = f.extension('图片背景替换', kind='plugin', global_usage_count=9800)
        cls.drive = f.extension('Google Drive', kind='connector', version='v1.4')
        cls.legal = f.extension('企业法务知识库', kind='connector', tier='enterprise', account=cls.business)
        for ext in (cls.ppt, cls.analysis, cls.pricing, cls.jungle, cls.estimate, cls.mail):
            f.install(cls.business, ext, cls.lillian)
        f.install(cls.business, cls.agent, cls.lillian, enabled=False)
        cls.jungle_conn = f.connection(cls.business, cls.jungle)
        cls.mail_conn = f.connection(cls.business, cls.mail, state='connected')
        InboxSource.objects.create(account=cls.business, owner_user=cls.lillian, kind='company_email',
                                   connection=cls.mail_conn, sender_filter='all_members')
        cls.wangye = f.system_expert('AGT-007', '望野', '市场竞争与机会研究')
        cls.hengjia = f.system_expert('AGT-026', '衡价', '定价促销与商品组合')
        f.capability(cls.business, cls.wangye, cls.analysis)
        f.capability(cls.business, cls.hengjia, cls.pricing)
        f.capability(cls.business, cls.wangye, cls.ppt)
        f.capability(cls.business, cls.hengjia, cls.ppt)


class ExtensionListTests(ExtensionFixtures):
    def test_installed_tab(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/extensions'))
        self.assertEqual(body['tab'], 'installed')
        self.assertEqual(body['total'], 7)
        self.assertEqual(body['counts'], {'installed': 7})
        self.assertEqual(body['kind_counts'], {'plugin': 1, 'skill': 3, 'connector': 2, 'agent': 1})
        items = {i['name']: i for i in body['items']}
        self.assertEqual(items['PPT 生成']['used_by_expert_count'], 2)
        self.assertEqual(items['PPT 生成']['installed_version'], 'v2.1')
        self.assertTrue(items['销量估算']['missing_connection'])
        self.assertEqual(items['销量估算']['required_connector'],
                         {'id': self.jungle.public_id, 'name': 'Jungle Scout', 'connection_state': 'not_connected'})
        self.assertEqual(items['Jungle Scout']['connection'], {'id': self.jungle_conn.public_id,
                                                               'state': 'not_connected'})
        self.assertTrue(items['企业邮箱']['used_by_inbox'])
        self.assertEqual(items['售后工单智能体']['install_state'], 'disabled')
        for key in ('id', 'kind', 'name', 'description', 'publisher_tier', 'access', 'billing_note',
                    'installed_version', 'latest_version', 'install_state', 'enabled', 'used_by_expert_count',
                    'used_by_inbox', 'global_usage_count', 'connection', 'required_connector', 'missing_connection'):
            self.assertIn(key, items['PPT 生成'])

    def test_market_tab_scope_alias_and_filters(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/extensions', {'tab': 'market'}))
        names = [i['name'] for i in body['items']]
        self.assertIn('图片背景替换', names)
        self.assertNotIn('售后工单智能体', names)
        self.assertEqual(body['counts']['installed'], 7)
        body = self.ok(self.get('app/extensions', {'scope': 'installed', 'kind': 'skill'}))
        self.assertEqual(body['total'], 3)
        self.assertEqual(body['kind_counts']['skill'], 3)
        body = self.ok(self.get('app/extensions', {'q': '演示文稿'}))
        self.assertEqual([i['name'] for i in body['items']], ['PPT 生成'])
        self.assert_error(self.get('app/extensions', {'tab': 'x'}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.get('app/extensions', {'kind': 'x'}), 422, 'VALIDATION_FAILED')

    def test_other_account_extension_hidden(self):
        self.login(self.lillian)
        self.assert_error(self.get('app/extensions/%s' % self.agent.public_id), 404, 'NOT_FOUND')


class ExtensionDetailTests(ExtensionFixtures):
    def test_detail_with_common_next(self):
        self.login(self.lillian, self.business)
        member = self.member(self.lillian)
        for index in range(4):
            row = f.task(self.business, member, '调研 %d' % index)
            f.step(row, 1, self.wangye, skill=self.analysis)
            if index < 3:
                f.step(row, 2, self.hengjia, skill=self.pricing)
            if index < 1:
                f.step(row, 3, self.wangye, skill=self.estimate)
        body = self.ok(self.get('app/extensions/%s' % self.analysis.public_id))
        self.assertEqual(body['acceptance_template'], ['覆盖前 20 名', '注明数据日期'])
        self.assertEqual(body['prerequisite_inputs'], ['竞品清单或类目页链接'])
        self.assertEqual(body['common_next'][0], {'extension_id': self.pricing.public_id, 'name': '定价策略',
                                                  'ratio': 0.75, 'needs_connection': False})
        self.assertEqual(body['common_next'][1]['needs_connection'], True)
        self.assertEqual(body['used_by_experts'][0]['name'], '望野')
        self.assertIn('avatar_ref', body['used_by_experts'][0])
        detail = self.ok(self.get('app/extensions/%s' % self.estimate.public_id))
        self.assertEqual(detail['required_connector']['connection']['id'], self.jungle_conn.public_id)

    def test_versions(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/extensions/%s/versions' % self.analysis.public_id))
        self.assertEqual(body['items'][0]['version'], 'v1.3')
        self.assertEqual(body['total'], 1)


class InstallTests(ExtensionFixtures):
    def test_member_installs_official_extension(self):
        self.login(self.mark, self.business)
        body = self.ok(self.post('app/extensions/%s/install' % self.drive.public_id, {}), 201)
        self.assertEqual(body['install']['install_state'], 'installed')
        self.assertEqual(body['install']['installed_version'], 'v1.4')
        self.assertTrue(ConnectorConnection.objects.filter(account=self.business, extension=self.drive,
                                                           state='not_connected').exists())
        self.assert_error(self.post('app/extensions/%s/install' % self.drive.public_id, {}), 409, 'STATE_CONFLICT')

    def test_policy_gates(self):
        AccountPolicy.objects.create(account=self.business, expert_share_max_scope='account',
                                     extension_allowed_tiers=['official', 'community_reviewed'],
                                     approval_required_actions=['install_third_party_extension'])
        community = f.extension('差评归因', tier='community_reviewed')
        third = f.extension('物流追踪智能体', kind='agent', tier='third_party')
        self.login(self.mark, self.business)
        self.assert_error(self.post('app/extensions/%s/install' % third.public_id, {}), 403, 'EXTENSION_NOT_ALLOWED')
        self.assert_error(self.post('app/extensions/%s/install' % community.public_id, {}), 409,
                          'POLICY_APPROVAL_REQUIRED')
        ExtensionAllowEntry.objects.create(account=self.business, extension=third, added_by=self.lillian)
        self.login(self.lillian, self.business)
        self.ok(self.post('app/extensions/%s/install' % third.public_id, {}), 201)
        self.ok(self.post('app/extensions/%s/install' % community.public_id, {}), 201)

    def test_enterprise_requires_admin(self):
        self.login(self.mark, self.business)
        self.assert_error(self.post('app/extensions/%s/install' % self.legal.public_id, {}), 403, 'PERMISSION_DENIED')
        self.login(self.chen, self.business)
        self.ok(self.post('app/extensions/%s/install' % self.legal.public_id, {}), 201)

    def test_enable_disable_transitions(self):
        self.login(self.lillian, self.business)
        url = 'app/extensions/%s/' % self.ppt.public_id
        body = self.ok(self.post(url + 'disable', {}))
        self.assertEqual(body['install']['install_state'], 'disabled')
        self.assert_error(self.post(url + 'disable', {}), 409, 'STATE_CONFLICT')
        self.assertTrue(self.ok(self.post(url + 'enable', {}))['install']['enabled'])
        self.assert_error(self.post(url + 'enable', {}), 409, 'STATE_CONFLICT')
        self.assert_error(self.post('app/extensions/%s/enable' % self.drive.public_id, {}), 409, 'STATE_CONFLICT')

    def test_member_cannot_disable_others_install(self):
        self.login(self.mark, self.business)
        self.assert_error(self.post('app/extensions/%s/disable' % self.ppt.public_id, {}), 403, 'PERMISSION_DENIED')

    def test_write_requires_account_header(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.post('app/extensions/%s/install' % self.drive.public_id, {}, account=False), 409,
                          'ACCOUNT_CHANGED')


class ImportTests(ExtensionFixtures):
    manifest = {'kind': 'skill', 'name': '德语本地化校对', 'description': '校对德语文案', 'version': 'v1.1'}

    def test_import_local_skill(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.post('app/extensions/import', {'manifest': self.manifest}), 201)
        self.assertEqual(body['extension']['publisher_tier'], 'local_import')
        self.assertEqual(body['install']['installed_version'], 'v1.1')
        listing = self.ok(self.get('app/extensions'))
        self.assertEqual(listing['counts']['installed'], 8)
        self.assert_error(self.post('app/extensions/import', {'manifest': self.manifest}), 422, 'VALIDATION_FAILED')

    def test_import_validation_and_policy(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.post('app/extensions/import', {'manifest': dict(self.manifest, kind='plugin')}), 422,
                          'VALIDATION_FAILED')
        self.assert_error(self.post('app/extensions/import', {'manifest': {'name': 'x'}}), 422, 'VALIDATION_FAILED')
        AccountPolicy.objects.create(account=self.business, expert_share_max_scope='account',
                                     extension_allowed_tiers=['official'])
        self.assert_error(self.post('app/extensions/import', {'manifest': self.manifest}), 403,
                          'EXTENSION_NOT_ALLOWED')


class ConnectionTests(ExtensionFixtures):
    url = 'app/connections/%s/%s'

    @override_settings(WORKBENCH_CONNECTOR_AUTHORIZER=SUPPORT + 'fake_authorizer')
    def test_connect_and_disconnect(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.post(self.url % (self.jungle_conn.public_id, 'connect'), {}))
        self.assertEqual(body['connection']['state'], 'connected')
        self.assert_error(self.post(self.url % (self.jungle_conn.public_id, 'connect'), {}), 409, 'STATE_CONFLICT')
        listing = self.ok(self.get('app/extensions', {'kind': 'skill'}))
        estimate = next(i for i in listing['items'] if i['name'] == '销量估算')
        self.assertFalse(estimate['missing_connection'])
        body = self.ok(self.post(self.url % (self.jungle_conn.public_id, 'disconnect'), {}))
        self.assertEqual(body['connection']['state'], 'not_connected')
        self.assert_error(self.post(self.url % (self.jungle_conn.public_id, 'disconnect'), {}), 409, 'STATE_CONFLICT')

    @override_settings(WORKBENCH_CONNECTOR_AUTHORIZER=SUPPORT + 'redirect_authorizer')
    def test_connect_returns_authorize_url(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.post(self.url % (self.jungle_conn.public_id, 'connect'), {}))
        self.assertEqual(body['authorize_url'], 'https://auth.example.com/start')
        self.jungle_conn.refresh_from_db()
        self.assertEqual(self.jungle_conn.state, 'not_connected')

    def test_connect_without_authorizer(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.post(self.url % (self.jungle_conn.public_id, 'connect'), {}), 503,
                          'SOURCE_UNAVAILABLE')

    @override_settings(WORKBENCH_CONNECTOR_AUTHORIZER=SUPPORT + 'failing_authorizer')
    def test_connect_provider_down(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.post(self.url % (self.jungle_conn.public_id, 'connect'), {}), 503,
                          'SOURCE_UNAVAILABLE')

    @override_settings(WORKBENCH_CONNECTOR_AUTHORIZER=SUPPORT + 'fake_authorizer')
    def test_disabled_and_policy(self):
        self.login(self.lillian, self.business)
        ConnectorConnection.objects.filter(pk=self.jungle_conn.pk).update(state='disabled')
        self.assert_error(self.post(self.url % (self.jungle_conn.public_id, 'connect'), {}), 409, 'STATE_CONFLICT')
        ConnectorConnection.objects.filter(pk=self.jungle_conn.pk).update(state='expired')
        AccountPolicy.objects.create(account=self.business, expert_share_max_scope='account',
                                     extension_allowed_tiers=['official'])
        self.assert_error(self.post(self.url % (self.jungle_conn.public_id, 'connect'), {}), 403,
                          'EXTENSION_NOT_ALLOWED')

    def test_other_users_connection_is_404(self):
        own = f.connection(self.business, self.drive, owner=self.chen)
        self.login(self.lillian, self.business)
        self.assert_error(self.post(self.url % (own.public_id, 'connect'), {}), 404, 'NOT_FOUND')
        self.assertEqual(ExtensionInstall.objects.filter(account=self.business).count(), 7)
