from unittest import mock

from django.test import override_settings
from django.utils import timezone

from backend.commerce.models import Department, DepartmentMember
from backend.workbench.models import AccountPolicy, Expert, ExpertTeam, TaskDelivery
from backend.workbench.tests.base import AppTestCase
from backend.workbench.tests import test_experts_support as f

SUPPORT = 'backend.workbench.tests.test_experts_support.'


class ExpertFixtures(AppTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ceo = f.system_expert('MGMT-CEO', '天时', '首席执行官 CEO', group='management',
                                  capability_tags=['目标拆解', '资源取舍', '决策方案'])
        cls.cgo = f.system_expert('MGMT-CGO', '地利', '首席增长官 CGO', group='management', specialty='首席增长官')
        cls.hengyuan = f.system_expert('AGT-001', '衡远', '经营目标与资源统筹', domain='经营与组织')
        cls.wangye = f.system_expert('AGT-007', '望野', '市场竞争与机会研究', specialty='市场竞争与机会研究',
                                     responsibilities='发现市场变化与尚未满足的机会。',
                                     capability_tags=['竞品格局', '市场规模', '机会评估'], reports_to=cls.cgo)
        cls.yijing = f.system_expert('AGT-028', '译境', '本地化与市场适配', domain='渠道经营', specialty='本地化与市场适配')
        cls.anjie = f.system_expert('AGT-044', '安界', '产品合规与隐私', domain='财务与合规', specialty='合规与认证')
        cls.miwei = f.system_expert('AGT-022', '觅位', 'Amazon 商品与搜索运营', domain='渠道经营', specialty='关键词研究')
        cls.qingzhang = f.system_expert('AGT-040', '清账', 'GMV 结算与会计对账', domain='财务与合规', specialty='对账复核')
        cls.assistant = f.system_expert('SAGE-ASSISTANT', 'Sage 助手', '兜底主责', group='role', domain='')
        cls.mine = f.custom_expert(cls.business, cls.lillian, '售后专家', share_scope='only_me')
        cls.shared = f.custom_expert(cls.business, cls.chen, '共享专家', share_scope='account')
        cls.private = f.custom_expert(cls.business, cls.chen, '私有专家', share_scope='only_me')
        cls.skill = f.extension('竞品格局分析', version='v1.3')
        cls.uninstalled = f.extension('市场规模测算')
        cls.connector = f.extension('Jungle Scout', kind='connector', tier='third_party', version='v1.8')
        cls.estimate = f.extension('销量估算', required_connector=cls.connector)
        f.install(cls.business, cls.skill, cls.lillian)
        f.install(cls.business, cls.estimate, cls.lillian)
        f.install(cls.business, cls.connector, cls.lillian)
        f.connection(cls.business, cls.connector)
        for ext in (cls.skill, cls.uninstalled):
            f.capability(cls.business, cls.wangye, ext)
        f.capability(cls.business, cls.wangye, cls.connector, required_for=cls.estimate)


class ExpertListTests(ExpertFixtures):
    def test_list_counts_and_fields(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/experts'))
        self.assertEqual(body['counts'], {'all': 10, 'management': 2, 'role': 6, 'mine': 1, 'shared': 1})
        names = [item['name'] for item in body['items']]
        self.assertEqual(names[:3], ['天时', '地利', '衡远'])
        self.assertNotIn('私有专家', names)
        self.assertNotIn('Sage 助手', names)
        self.assertEqual(body['assistant']['name'], 'Sage 助手')
        item = body['items'][0]
        for key in ('id', 'name', 'title', 'group', 'domain', 'specialty', 'responsibilities', 'capability_tags',
                    'avatar_ref', 'source', 'assignable'):
            self.assertIn(key, item)
        self.assertEqual(item['capability_tags'], ['目标拆解', '资源取舍', '决策方案'])
        self.assertEqual(body['total'], 10)
        domains = {row['domain']: row['count'] for row in body['domain_counts']}
        self.assertEqual(domains['财务与合规'], 2)

    def test_group_domain_and_search_filters(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/experts', {'group': 'management'}))
        self.assertEqual([i['name'] for i in body['items']], ['天时', '地利'])
        self.assertEqual(body['counts']['all'], 10)
        body = self.ok(self.get('app/experts', {'domain': '财务与合规'}))
        self.assertEqual({i['name'] for i in body['items']}, {'安界', '清账'})
        body = self.ok(self.get('app/experts', {'q': '竞品格局'}))
        self.assertEqual([i['name'] for i in body['items']], ['望野'])
        body = self.ok(self.get('app/experts', {'group': 'shared'}))
        self.assertEqual([i['name'] for i in body['items']], ['共享专家'])

    def test_invalid_group_and_unknown_param(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.get('app/experts', {'group': 'x'}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.get('app/experts', {'foo': '1'}), 422, 'INVALID_REQUEST')

    def test_non_member_gets_404(self):
        self.login(self.anna, self.business)
        self.assert_error(self.get('app/experts'), 404, 'NOT_FOUND')


class ExpertDetailTests(ExpertFixtures):
    def test_detail_stats_skills_tools(self):
        self.login(self.lillian, self.business)
        workspace = f.workspace(self.business, self.lillian)
        member = self.member(self.lillian)
        t1 = f.task(self.business, member, '德国站竞品格局调研', lead_expert=self.wangye, workspace=workspace)
        t2 = f.task(self.business, member, 'Q4 欧洲达人合作清单')
        f.step(t2, 1, self.wangye)
        f.task(self.business, self.member(self.chen), '别人的任务', lead_expert=self.wangye)
        body = self.ok(self.get('app/experts/%s' % self.wangye.public_id))
        self.assertEqual(body['stats'], {'skill_count': 2, 'history_task_count': 2})
        skills = {s['name']: s for s in body['skills']}
        self.assertEqual(skills['竞品格局分析']['install_state'], 'installed')
        self.assertEqual(skills['竞品格局分析']['installed_version'], 'v1.3')
        self.assertEqual(skills['市场规模测算']['install_state'], 'not_installed')
        self.assertEqual(body['tools'][0]['name'], 'Jungle Scout')
        self.assertEqual(body['tools'][0]['connection_state'], 'not_connected')
        self.assertEqual(body['tools'][0]['required_for']['name'], '销量估算')
        self.assertEqual(len(body['recent_tasks']), 2)
        self.assertEqual(body['recent_tasks'][0]['role'], 'collaborator')
        self.assertEqual(body['recent_tasks'][1]['workspace']['name'], '德国市场进入')
        for key in ('status', 'wizard_step', 'start_mode', 'start_description', 'template_from',
                    'communication_style', 'uncertainty_policy', 'forbidden_rules', 'knowledge_bases',
                    'similar_experts', 'try_estimated_credits', 'catalog_code', 'version_label', 'share_scope',
                    'version'):
            self.assertIn(key, body)
        self.assertEqual(body['catalog_code'], 'AGT-007')
        self.assertEqual(body['try_estimated_credits'], 2)

    def test_private_expert_of_other_user_is_404(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.get('app/experts/%s' % self.private.public_id), 404, 'NOT_FOUND')

    def test_department_share(self):
        department = Department.objects.create(account=self.business, name='客服组')
        DepartmentMember.objects.create(department=department, membership=self.member(self.mark))
        expert = f.custom_expert(self.business, self.chen, '部门专家', share_scope='department',
                                 shared_department=department)
        self.login(self.mark, self.business)
        self.ok(self.get('app/experts/%s' % expert.public_id))
        self.login(self.lillian, self.business)
        self.assert_error(self.get('app/experts/%s' % expert.public_id), 404, 'NOT_FOUND')


class NetworkTests(ExpertFixtures):
    def test_network_counts_relations_and_rates(self):
        self.login(self.lillian, self.business)
        member = self.member(self.lillian)
        for index in range(3):
            row = f.task(self.business, member, '任务 %d' % index, lead_expert=self.wangye)
            f.step(row, 1, self.miwei)
            f.step(row, 2, self.wangye)
            f.step(row, 3, self.anjie)
            if index < 2:
                f.step(row, 4, self.qingzhang, stage='review')
            artifact = row.artifacts.create(account=self.business, title='报告', kind='document')
            version = artifact.versions.create(account=self.business, number=1, content_ref='seed:none')
            TaskDelivery.objects.create(account=self.business, task=row, artifact_version=version, summary='交付',
                                        decision='returned' if index == 0 else 'accepted')
        old = f.task(self.business, member, '很久以前', lead_expert=self.wangye)
        f.step(old, 1, self.anjie)
        old.__class__.objects.filter(pk=old.pk).update(last_activity_at=timezone.now() - timezone.timedelta(days=120))
        body = self.ok(self.get('app/experts/%s/network' % self.wangye.public_id))
        self.assertEqual(body['window_days'], 90)
        self.assertEqual(body['center']['name'], '望野')
        peers = {p['expert']['name']: p for p in body['peers']}
        self.assertEqual(peers['安界']['collaboration_count'], 3)
        self.assertEqual(peers['安界']['relation'], 'downstream')
        self.assertEqual(peers['安界']['acceptance_rate'], 67)
        self.assertEqual(peers['觅位']['relation'], 'upstream')
        self.assertEqual(peers['清账']['relation'], 'reviews_me')
        self.assertEqual(peers['清账']['return_count'], 1)
        self.assertEqual(peers['地利']['relation'], 'reports_to')
        self.assertEqual(body['peers'][-1]['expert']['name'], '地利')
        self.assertIn('avatar_ref', body['peers'][0]['expert'])
        self.assertTrue(peers['安界']['summary'])


@override_settings(WORKBENCH_EXPERT_DRAFT_GENERATOR=SUPPORT + 'fake_generator')
class ExpertWizardTests(ExpertFixtures):
    def create(self, **data):
        return self.post('app/experts', data)

    def test_create_describe_draft(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.create(start_mode='describe', start_description='把中文内容改写成德语并检查合规'), 201)
        expert = body['expert']
        self.assertEqual(expert['status'], 'draft')
        self.assertEqual(expert['wizard_step'], 1)
        self.assertEqual(expert['name'], '德语内容官')
        self.assertEqual(expert['start_mode'], 'describe')
        self.assertEqual([s['name'] for s in expert['similar_experts']], ['译境'])
        listing = self.ok(self.get('app/experts', {'group': 'mine'}))
        self.assertEqual(listing['counts']['mine'], 2)

    @override_settings(WORKBENCH_EXPERT_DRAFT_GENERATOR=None)
    def test_describe_without_generator(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.create(start_mode='describe', start_description='x'), 503, 'SOURCE_UNAVAILABLE')

    @override_settings(WORKBENCH_EXPERT_DRAFT_GENERATOR=SUPPORT + 'failing_generator')
    def test_describe_generator_down(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.create(start_mode='describe', start_description='x'), 503, 'SOURCE_UNAVAILABLE')

    def test_create_validation(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.create(start_mode='describe', start_description='x' * 501), 422, 'VALIDATION_FAILED')
        self.assert_error(self.create(start_mode='nope'), 422, 'VALIDATION_FAILED')
        self.assert_error(self.create(start_mode='blank', template_from=self.wangye.public_id), 422,
                          'VALIDATION_FAILED')
        self.assert_error(self.create(start_mode='template', template_from='0' * 26), 404, 'NOT_FOUND')

    def test_create_from_template_and_blank(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.create(start_mode='template', template_from=self.wangye.public_id), 201)['expert']
        self.assertEqual(body['template_from']['name'], '望野')
        self.assertEqual(body['responsibilities'], '发现市场变化与尚未满足的机会。')
        self.assertEqual({s['name'] for s in body['skills']}, {'竞品格局分析', '市场规模测算'})
        first = self.ok(self.create(start_mode='blank'), 201)['expert']
        second = self.ok(self.create(start_mode='blank'), 201)['expert']
        self.assertNotEqual(first['name'], second['name'])

    def test_create_rate_limited(self):
        self.login(self.lillian, self.business)
        with mock.patch('backend.workbench.services.workspaces.throttle', return_value=False):
            self.assert_error(self.create(start_mode='blank'), 429, 'RATE_LIMITED')

    def draft(self):
        self.login(self.lillian, self.business)
        return self.ok(self.create(start_mode='describe', start_description='德语'), 201)['expert']

    def test_patch_steps_and_publish(self):
        draft = self.draft()
        url = 'app/experts/%s' % draft['id']
        body = self.ok(self.patch(url, {'version': draft['version'], 'wizard_step': 2,
                                        'communication_style': 'conclusion_first',
                                        'uncertainty_policy': 'ask_first',
                                        'forbidden_rules': ['不使用“最好”“第一”等绝对化宣称', '不翻译品牌名与产品型号']}))
        expert = body['expert']
        self.assertEqual(expert['wizard_step'], 2)
        self.assertEqual(len(expert['forbidden_rules']), 2)
        body = self.ok(self.patch(url, {'version': expert['version'], 'skill_ids': [self.estimate.public_id],
                                        'wizard_step': 4}))
        expert = body['expert']
        self.assertEqual([s['name'] for s in expert['skills']], ['销量估算'])
        self.assertEqual(expert['tools'][0]['name'], 'Jungle Scout')
        self.assertEqual(expert['tools'][0]['required_for']['name'], '销量估算')
        self.assert_error(self.post(url + '/publish', {'version': expert['version'] - 1}), 409, 'VERSION_CONFLICT')
        published = self.ok(self.post(url + '/publish', {'version': expert['version']}))['expert']
        self.assertEqual(published['status'], 'published')
        self.assertEqual(published['share_scope'], 'only_me')
        self.assertIsNone(published['wizard_step'])
        self.assert_error(self.post(url + '/publish', {'version': published['version']}), 409, 'STATE_CONFLICT')
        self.assert_error(self.patch(url, {'version': published['version'], 'name': 'x'}), 409, 'STATE_CONFLICT')

    def test_patch_errors(self):
        draft = self.draft()
        url = 'app/experts/%s' % draft['id']
        v = draft['version']
        self.assert_error(self.patch(url, {'version': v + 5, 'wizard_step': 2}), 409, 'VERSION_CONFLICT')
        self.assert_error(self.patch(url, {'version': v, 'forbidden_rules': ['x'] * 11}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.patch(url, {'version': v, 'forbidden_rules': ['x' * 51]}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.patch(url, {'version': v, 'skill_ids': [self.uninstalled.public_id]}), 403,
                          'EXTENSION_NOT_ALLOWED')
        self.assert_error(self.patch(url, {'version': v, 'skill_ids': [self.connector.public_id]}), 422,
                          'VALIDATION_FAILED')
        many = [f.extension('技能%d' % i).public_id for i in range(21)]
        self.assert_error(self.patch(url, {'version': v, 'skill_ids': many}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.patch(url, {'version': v, 'name': '共享专家'}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.patch(url, {'version': v, 'wizard_step': 9}), 422, 'VALIDATION_FAILED')
        AccountPolicy.objects.create(account=self.business, expert_share_max_scope='only_me')
        self.assert_error(self.patch(url, {'version': v, 'share_scope': 'account'}), 403, 'PERMISSION_DENIED')
        self.assert_error(self.patch('app/experts/%s' % self.wangye.public_id, {'version': 1, 'name': 'x'}), 403,
                          'PERMISSION_DENIED')

    def test_patch_knowledge_bases_only_citable(self):
        draft = self.draft()
        citable = f.knowledge_base(self.business, self.chen, '竞品研究资料')
        closed = f.knowledge_base(self.business, self.chen, '关闭引用', expert_cite_enabled=False)
        url = 'app/experts/%s' % draft['id']
        self.assert_error(self.patch(url, {'version': draft['version'], 'knowledge_base_ids': [closed.public_id]}),
                          422, 'VALIDATION_FAILED')
        body = self.ok(self.patch(url, {'version': draft['version'], 'knowledge_base_ids': [citable.public_id]}))
        self.assertEqual(body['expert']['knowledge_bases'], [{'id': citable.public_id, 'name': '竞品研究资料'}])

    def test_publish_requires_identity(self):
        self.login(self.lillian, self.business)
        draft = self.ok(self.create(start_mode='blank'), 201)['expert']
        self.assert_error(self.post('app/experts/%s/publish' % draft['id'], {'version': draft['version']}), 422,
                          'VALIDATION_FAILED')

    def test_other_member_cannot_edit_shared_expert(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.patch('app/experts/%s' % self.shared.public_id, {'version': 1, 'name': 'x'}), 403,
                          'PERMISSION_DENIED')


class AvatarAndTryTests(ExpertFixtures):
    @override_settings(WORKBENCH_EXPERT_AVATAR_GENERATOR=SUPPORT + 'fake_avatar')
    def test_regenerate_avatar(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.post('app/experts/%s/avatar' % self.mine.public_id, {}))
        self.assertTrue(body['avatar_ref'].startswith('generated/'))
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.avatar_ref, body['avatar_ref'])
        self.assert_error(self.post('app/experts/%s/avatar' % self.wangye.public_id, {}), 403, 'PERMISSION_DENIED')
        with mock.patch('backend.workbench.services.workspaces.throttle', return_value=False):
            self.assert_error(self.post('app/experts/%s/avatar' % self.mine.public_id, {}), 429, 'RATE_LIMITED')

    def test_avatar_without_generator(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.post('app/experts/%s/avatar' % self.mine.public_id, {}), 503, 'SOURCE_UNAVAILABLE')

    @override_settings(WORKBENCH_EXPERT_TRY_RUNNER=SUPPORT + 'fake_try')
    def test_try(self):
        self.login(self.lillian, self.business)
        url = 'app/experts/%s/try' % self.wangye.public_id
        with mock.patch('backend.workbench.services.inbox.require_credits'):
            body = self.ok(self.post(url, {'prompt': '把“超长续航，行业第一”翻译成德语'}))
            self.assertEqual(body, {'reply': 'Lange Akkulaufzeit', 'credits_used': 2})
            self.assert_error(self.post(url, {'prompt': 'x' * 501}), 422, 'VALIDATION_FAILED')
        with mock.patch('backend.workbench.services.inbox.require_credits', side_effect=ValueError('INSUFFICIENT_CREDITS')):
            self.assert_error(self.post(url, {'prompt': 'hi'}), 409, 'INSUFFICIENT_CREDITS')
        with mock.patch('backend.workbench.services.workspaces.throttle', return_value=False):
            self.assert_error(self.post(url, {'prompt': 'hi'}), 429, 'RATE_LIMITED')


class TeamTests(ExpertFixtures):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.template = ExpertTeam.objects.create(account=None, name='新市场进入小组', lead_expert=cls.wangye,
                                                 source='template', visibility='account',
                                                 description='竞品、关键词、合规、定价与数字复核，一次组齐')
        cls.template.members.set([cls.wangye, cls.anjie, cls.miwei])
        cls.chen_private = ExpertTeam.objects.create(account=cls.business, name='Chen 私有', lead_expert=cls.anjie,
                                                     source='custom', visibility='only_me', created_by=cls.chen)
        cls.chen_private.members.set([cls.anjie, cls.miwei])

    def test_list_teams(self):
        self.login(self.lillian, self.business)
        member = self.member(self.lillian)
        for _ in range(2):
            f.task(self.business, member, '用模板', team=self.template)
        body = self.ok(self.get('app/teams'))
        self.assertEqual(body['total'], 1)
        team = body['items'][0]
        self.assertEqual(team['source'], 'template')
        self.assertEqual(team['lead_expert']['name'], '望野')
        self.assertEqual(team['usage_count'], 2)
        self.assertEqual(team['members'][0]['name'], '望野')
        self.assertIn('avatar_ref', team['members'][0])

    def test_create_team(self):
        self.login(self.lillian, self.business)
        ids = [self.anjie.public_id, self.miwei.public_id, self.qingzhang.public_id]
        body = self.ok(self.post('app/teams', {'name': '售后快速响应', 'member_ids': ids,
                                               'lead_expert_id': self.anjie.public_id, 'visibility': 'only_me'}), 201)
        self.assertEqual(body['team']['lead_expert']['name'], '安界')
        self.assertEqual(len(body['team']['members']), 3)
        self.assertEqual(body['team']['source'], 'custom')
        listing = self.ok(self.get('app/teams'))
        self.assertEqual([t['source'] for t in listing['items']], ['template', 'custom'])

    def test_create_team_validation(self):
        self.login(self.lillian, self.business)
        one = [self.anjie.public_id]
        base = {'name': '组', 'lead_expert_id': self.anjie.public_id, 'visibility': 'only_me'}
        self.assert_error(self.post('app/teams', dict(base, member_ids=one)), 422, 'VALIDATION_FAILED')
        nine = [e.public_id for e in Expert.objects.filter(account__isnull=True)][:9]
        self.assert_error(self.post('app/teams', dict(base, member_ids=nine)), 422, 'VALIDATION_FAILED')
        two = [self.miwei.public_id, self.qingzhang.public_id]
        self.assert_error(self.post('app/teams', dict(base, member_ids=two)), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post('app/teams', dict(base, member_ids=two + [self.anjie.public_id],
                                                      visibility='workspace')), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post('app/teams', dict(base, member_ids=[self.anjie.public_id, self.private.public_id])),
                          422, 'VALIDATION_FAILED')

    def test_member_expert_ids_alias(self):
        self.login(self.lillian, self.business)
        ids = [self.anjie.public_id, self.miwei.public_id]
        self.ok(self.post('app/teams', {'name': '别名', 'member_expert_ids': ids, 'lead_expert_id': ids[0],
                                        'visibility': 'account'}), 201)
