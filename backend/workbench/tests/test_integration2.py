"""后端集成第二轮契约测试：任务详情补字段、decisions 形状统一、列表筛选、审核导出、头像路由、成员额度版本号。"""
import uuid
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from backend.commerce.billing import set_shared_limit
from backend.workbench.models import (AcceptanceCriterion, Artifact, AuthorizationRequest, Citation, ContentReview,
                                      ContentReviewIssue, ExtensionInstall, KnowledgeBase, KnowledgeDocument, Task,
                                      TaskDelivery, TaskMessage, TaskThread)
from backend.workbench.services import artifacts as store
from backend.workbench.tests.base import AppTestCase
from backend.workbench.tests.test_inbox_support import (give_credits, make_expert, make_extension, make_task,
                                                        make_workspace)
from backend.workbench.tests.test_experts_support import extension as make_skill, install


def rid():
    return uuid.uuid4().hex


def make_review(account, task, verifier, state='reviewed'):
    review = ContentReview.objects.create(account=account, task=task, dimensions=['platform_policy', 'brand'],
                                          conclusion='需要修改 1 处后再上架。', state=state,
                                          target_platform='Amazon.de', target_market='德国',
                                          verifier_expert=verifier)
    ContentReviewIssue.objects.create(account=account, review=review, dimension='brand', severity='medium',
                                      title='称谓不统一', location='五点描述', original_text='Du', suggested_text='Sie',
                                      basis='品牌规范')
    return review


class TaskDetailAdditionTests(AppTestCase):
    def setUp(self):
        self.expert = make_expert('望野')
        self.workspace = make_workspace(self.business, self.lillian, [(self.lillian, 'owner')])
        self.task = make_task(self.business, self.member(self.lillian), workspace=self.workspace, lead=self.expert)
        TaskThread.objects.create(account=self.business, task=self.task, kind='main')
        self.login(self.lillian, self.business)

    def _authorization(self, decision, **extra):
        fields = dict(account=self.business, task=self.task, requested_by_expert=self.expert,
                      extension=make_extension('Jungle Scout %s' % rid()[:6]), action_kind='read_query',
                      scope_text='读取竞品销量', purpose_text='读取前 50 名竞品销量', validity='this_request',
                      estimate_state='estimated', estimated_credits=40, decision=decision)
        fields.update(extra)
        if decision != 'pending':
            fields.update(decided_by=self.lillian, decided_at=timezone.now())
        return AuthorizationRequest.objects.create(**fields)

    def _delivery(self, decision='pending'):
        artifact = Artifact.objects.create(account=self.business, source_task=self.task, kind='document',
                                           title='报告 %s' % rid()[:4], created_by_expert=self.expert)
        version = store.create_version(artifact, {'format': 'blocks', 'blocks': []}, author_expert=self.expert)
        return TaskDelivery.objects.create(account=self.business, task=self.task, artifact_version=version,
                                           summary='请验收', decision=decision)

    def test_detail_has_reviews_pending_delivery_and_authorization_history(self):
        review = make_review(self.business, self.task, self.expert)
        delivery = self._delivery()
        pending = self._authorization('pending')
        approved = self._authorization('approved')
        rejected = self._authorization('rejected')
        body = self.ok(self.get('app/tasks/%s' % self.task.public_id))
        self.assertEqual(body['reviews'], [{'id': review.public_id, 'state': 'reviewed'}])
        self.assertEqual(body['pending_delivery']['id'], delivery.public_id)
        self.assertEqual(body['pending_delivery']['artifact_version']['number'], 1)
        rows = {row['id']: row for row in body['authorizations']}
        self.assertEqual(set(rows), {pending.public_id, approved.public_id, rejected.public_id})
        self.assertEqual(rows[approved.public_id]['decision'], 'approved')
        self.assertEqual(rows[rejected.public_id]['decision'], 'rejected')
        self.assertEqual(rows[pending.public_id]['version'], pending.version)
        self.assertEqual(rows[pending.public_id]['estimated_credits'], 40)

    def test_detail_empty_additions(self):
        body = self.ok(self.get('app/tasks/%s' % self.task.public_id))
        self.assertEqual(body['reviews'], [])
        self.assertIsNone(body['pending_delivery'])
        self.assertEqual(body['authorizations'], [])

    def test_pending_delivery_disappears_after_decision(self):
        delivery = self._delivery(decision='accepted')
        body = self.ok(self.get('app/tasks/%s' % self.task.public_id))
        self.assertIsNone(body['pending_delivery'])


class DecisionEntryShapeTests(AppTestCase):
    def setUp(self):
        self.expert = make_expert('望野')
        self.task = make_task(self.business, self.member(self.lillian), lead=self.expert)
        self.auth_request = AuthorizationRequest.objects.create(
            account=self.business, task=self.task, requested_by_expert=self.expert,
            extension=make_extension('Jungle Scout'), action_kind='read_query', scope_text='读取竞品销量',
            validity='this_request', estimate_state='estimated', estimated_credits=12)

    def test_approve_entry_has_version_and_object_charge_target(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/inbox/decisions'))
        entry = next(item for item in body['items'] if item['type'] == 'approve')
        self.assertEqual(entry['authorization']['version'], self.auth_request.version)
        target = entry['authorization']['charge_target']
        self.assertEqual(target['kind'], 'member_share')
        self.assertEqual(target['label'], '计入企业额度')
        self.assertIn('department', target)

    def test_personal_charge_target_object(self):
        member = self.member(self.lillian, self.personal)
        task = make_task(self.personal, member, lead=self.expert)
        AuthorizationRequest.objects.create(
            account=self.personal, task=task, requested_by_expert=self.expert,
            extension=make_extension('Jungle Scout'), action_kind='read_query', scope_text='读取竞品销量',
            validity='this_request')
        self.login(self.lillian, self.personal)
        body = self.ok(self.get('app/inbox/decisions'))
        entry = next(item for item in body['items'] if item['type'] == 'approve')
        self.assertEqual(entry['authorization']['charge_target'],
                         {'kind': 'personal_account', 'label': '计入个人额度', 'department': None})


class FilterTests(AppTestCase):
    def setUp(self):
        self.login(self.lillian, self.business)

    def test_extensions_installed_filter(self):
        installed = make_skill('已装技能')
        install(self.business, installed, self.lillian)
        other = make_skill('未装技能')
        connector = make_extension('连接器')
        body = self.ok(self.get('app/extensions', {'installed': 'true'}))
        names = {item['name'] for item in body['items']}
        self.assertIn('已装技能', names)
        self.assertNotIn('未装技能', names)
        body = self.ok(self.get('app/extensions', {'installed': 'false', 'tab': 'market'}))
        names = {item['name'] for item in body['items']}
        self.assertNotIn('已装技能', names)
        self.assertIn('未装技能', names)
        self.assert_error(self.get('app/extensions', {'installed': 'yes'}), 422, 'VALIDATION_FAILED')

    def _document(self, kb, title):
        return KnowledgeDocument.objects.create(account=self.business, knowledge_base=kb, title=title,
                                                source_kind='upload', uploaded_by=self.lillian,
                                                content_ref='seed:%s' % title, index_state='indexed')

    def test_documents_task_filter(self):
        kb = KnowledgeBase.objects.create(account=self.business, name='资料库', scope='account',
                                          owner=self.lillian)
        attached = self._document(kb, '竞品清单.xlsx')
        self._document(kb, '其他资料.pdf')
        task = make_task(self.business, self.member(self.lillian), lead=make_expert('望野'))
        thread = TaskThread.objects.create(account=self.business, task=task, kind='main')
        message = TaskMessage.objects.create(account=self.business, task=task, thread=thread, author_kind='user',
                                             author_user=self.lillian, kind='text', body='看一下')
        message.attached_documents.add(attached)
        body = self.ok(self.get('app/documents', {'task': task.public_id}))
        self.assertEqual([item['title'] for item in body['items']], ['竞品清单.xlsx'])
        self.assert_error(self.get('app/documents', {'task': '01M7PTBRHHVKJ25K04VEVFTAN2'}), 404, 'NOT_FOUND')

    def test_citations_knowledge_base_filter(self):
        kb = KnowledgeBase.objects.create(account=self.business, name='资料库', scope='account',
                                          owner=self.lillian)
        other_kb = KnowledgeBase.objects.create(account=self.business, name='别的库', scope='account',
                                                owner=self.lillian)
        document = self._document(kb, '竞品清单.xlsx')
        other_doc = self._document(other_kb, '无关.pdf')
        task = make_task(self.business, self.member(self.lillian), lead=make_expert('望野'))
        Citation.objects.create(account=self.business, task=task, document=document, ref_index=1)
        Citation.objects.create(account=self.business, task=task, document=other_doc, ref_index=2)
        body = self.ok(self.get('app/citations', {'knowledge_base': kb.public_id}))
        self.assertEqual(body['total'], 1)
        body = self.ok(self.get('app/citations', {'document': document.public_id}))
        self.assertEqual(body['total'], 1)
        self.assert_error(self.get('app/citations'), 422, 'INVALID_REQUEST')
        self.assert_error(self.get('app/citations', {'document': document.public_id,
                                                     'knowledge_base': kb.public_id}), 422, 'INVALID_REQUEST')


class ReviewExportTests(AppTestCase):
    def test_export_returns_json_report(self):
        expert = make_expert('守衡')
        task = make_task(self.business, self.member(self.lillian), lead=make_expert('安界'))
        review = make_review(self.business, task, expert)
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/reviews/%s/export' % review.public_id))
        self.assertEqual(body['report']['id'], review.public_id)
        self.assertEqual(body['report']['conclusion'], '需要修改 1 处后再上架。')
        self.assertEqual(len(body['report']['issues']), 1)
        self.assertEqual(body['report']['issues'][0]['title'], '称谓不统一')
        self.assertEqual(body['report']['task']['id'], task.public_id)
        self.assertIn('generated_at', body['report'])
        self.assert_error(self.get('app/reviews/01M7PTBRHHVKJ25K04VEVFTAN2/export'), 404, 'NOT_FOUND')


class AvatarRouteTests(AppTestCase):
    def setUp(self):
        self.login(self.lillian, self.business)

    def test_whitelisted_avatar_is_served(self):
        response = self.get('app/avatars/assets/people/AGT-001.webp')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/webp')
        response = self.get('app/avatars/digital-employees/assets/light/512/AGT-001.webp')
        self.assertEqual(response.status_code, 200)

    def test_theme_avatar_sets_are_served(self):
        for style in ('photo', 'iso', 'collage', 'flat', 'line'):
            response = self.get('app/avatars/assets/avatars/%s/AGT-007.webp' % style)
            self.assertEqual(response.status_code, 200, style)
            self.assertEqual(response['Content-Type'], 'image/webp')
        response = self.get('app/avatars/assets/avatars/photo/MGMT-CEO.webp')
        self.assertEqual(response.status_code, 200)

    def test_theme_avatar_prefix_rejects_traversal_and_non_images(self):
        self.assert_error(self.get('app/avatars/assets/avatars/../../backend/settings.py'), 404, 'NOT_FOUND')
        self.assert_error(self.get('app/avatars/assets/avatars/photo/../../../manage.py'), 404, 'NOT_FOUND')
        self.assert_error(self.get('app/avatars/assets/avatars/photo/NOPE-000.webp'), 404, 'NOT_FOUND')
        self.assert_error(self.get('app/avatars/assets/avatarsX/photo/AGT-007.webp'), 404, 'NOT_FOUND')

    def test_traversal_and_outside_prefix_are_rejected(self):
        self.assert_error(self.get('app/avatars/assets/people/../../backend/settings.py'), 404, 'NOT_FOUND')
        self.assert_error(self.get('app/avatars/backend/settings.py'), 404, 'NOT_FOUND')
        self.assert_error(self.get('app/avatars/assets/people/NOPE-000.webp'), 404, 'NOT_FOUND')

    def test_expert_ref_points_at_avatar_route(self):
        make_expert('图灵', avatar_ref='assets/people/AGT-777.webp')
        make_expert('素人', avatar_ref='experts/legacy.png')
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/experts'))
        refs = {item['name']: item['avatar_ref'] for item in body['items']}
        self.assertEqual(refs['图灵'], '/api/app/avatars/assets/people/AGT-777.webp')
        self.assertEqual(refs['素人'], 'experts/legacy.png')

    def test_theme_avatar_ref_points_at_avatar_route(self):
        from backend.workbench.services.workspaces import avatar_url
        self.assertEqual(avatar_url('assets/avatars/iso/AGT-007.webp'), '/api/app/avatars/assets/avatars/iso/AGT-007.webp')


class MemberQuotaVersionTests(AppTestCase):
    def test_members_return_shared_limit_version(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.get('billing/organization'))
        rows = {row['id']: row for row in body['members']}
        self.assertIn('shared_limit_version', rows[self.member(self.lillian).pk])
        self.assertEqual(rows[self.member(self.lillian).pk]['shared_limit_version'], 0)
        set_shared_limit(self.business, self.lillian, self.member(self.chen).pk, '500',
                         expected_version=0, request_key='seed-test-%s' % rid())
        body = self.ok(self.get('billing/organization'))
        rows = {row['id']: row for row in body['members']}
        self.assertEqual(rows[self.member(self.chen).pk]['shared_limit_version'], 1)
