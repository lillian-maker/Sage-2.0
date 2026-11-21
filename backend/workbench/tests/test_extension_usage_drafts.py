"""Contract tests: draft experts are not counted in extension usage (D38, 46 屏) and expert skills carry their
connector dependency (D37, 35 屏)."""
from backend.workbench.tests import test_experts_support as f
from backend.workbench.tests.base import AppTestCase


class ExtensionUsageExcludesDraftsTests(AppTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ads = f.extension('Amazon 广告', kind='connector')
        cls.keywords = f.extension('Amazon 关键词研究', version='v2.0', required_connector=cls.ads)
        f.install(cls.business, cls.keywords, cls.lillian)
        cls.miwei = f.system_expert('AGT-022', '觅位', '关键词与类目机会')
        cls.draft = f.custom_expert(cls.business, cls.lillian, '德语内容官', status='draft', share_scope='only_me')
        f.capability(cls.business, cls.miwei, cls.keywords)
        f.capability(cls.business, cls.draft, cls.keywords)

    def test_list_count_skips_drafts(self):
        self.login(self.lillian, self.business)
        items = {i['name']: i for i in self.ok(self.get('app/extensions'))['items']}
        self.assertEqual(items['Amazon 关键词研究']['used_by_expert_count'], 1)

    def test_detail_count_and_names_skip_drafts(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/extensions/%s' % self.keywords.public_id))
        self.assertEqual(body['used_by_expert_count'], 1)
        self.assertEqual([e['name'] for e in body['used_by_experts']], ['觅位'])

    def test_published_custom_expert_still_counted(self):
        f.custom_expert(self.business, self.lillian, '售后话术专家')
        published = f.custom_expert(self.business, self.lillian, '售后话术专家二')
        f.capability(self.business, published, self.keywords)
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/extensions/%s' % self.keywords.public_id))
        self.assertEqual(body['used_by_expert_count'], 2)
        self.assertEqual(sorted(e['name'] for e in body['used_by_experts']), ['售后话术专家二', '觅位'])


class ExpertSkillConnectorTests(AppTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.deepl = f.extension('DeepL', kind='connector')
        cls.ads = f.extension('Amazon 广告', kind='connector')
        cls.local = f.extension('德语本地化校对', tier='local_import', version='v1.1', account=cls.business,
                                required_connector=cls.deepl)
        cls.keywords = f.extension('Amazon 关键词研究', version='v2.0', required_connector=cls.ads)
        cls.plain = f.extension('竞品格局分析', version='v1.3')
        f.install(cls.business, cls.keywords, cls.lillian)
        f.connection(cls.business, cls.ads, state='connected')
        cls.draft = f.custom_expert(cls.business, cls.lillian, '德语内容官', status='draft', share_scope='only_me')
        for ext in (cls.local, cls.keywords, cls.plain):
            f.capability(cls.business, cls.draft, ext)

    def test_skills_carry_required_connector_whether_or_not_connected(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/experts/%s' % self.draft.public_id))
        skills = {s['name']: s for s in body['skills']}
        self.assertEqual(skills['德语本地化校对']['required_connector'],
                         {'id': self.deepl.public_id, 'name': 'DeepL', 'connection_state': 'not_connected'})
        self.assertEqual(skills['德语本地化校对']['install_state'], 'not_installed')
        self.assertEqual(skills['Amazon 关键词研究']['required_connector'],
                         {'id': self.ads.public_id, 'name': 'Amazon 广告', 'connection_state': 'connected'})
        self.assertIsNone(skills['竞品格局分析']['required_connector'])
