import json
from datetime import date, timedelta

from django.core.files.base import ContentFile
from django.utils import timezone

from backend.models import AuditEvent
from backend.commerce.models import Department, DepartmentMember
from backend.workbench.models import (Citation, KnowledgeDocument, KnowledgeEntity, KnowledgeFact,
                                      KnowledgeRelation)
from backend.workbench.services import artifacts as artifact_service
from backend.workbench.services import knowledge as knowledge_service
from backend.workbench.tests.base import AppTestCase
from backend.workbench.tests import test_experts_support as f


class KnowledgeFixtures(AppTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ws = f.workspace(cls.business, cls.lillian, members=((cls.chen, 'editor'), (cls.mark, 'viewer')))
        cls.other_ws = f.workspace(cls.business, cls.chen, name='黑五大促')
        cls.personal_kb = f.knowledge_base(cls.business, cls.lillian, '品牌手册 2026', scope='personal',
                                           cite='owner_only')
        cls.ws_kb = f.knowledge_base(cls.business, cls.lillian, '德国市场进入资料库', scope='workspace',
                                     workspace_row=cls.ws, cite='workspace_all')
        cls.account_kb = f.knowledge_base(cls.business, cls.chen, '竞品研究资料')
        cls.closed_kb = f.knowledge_base(cls.business, cls.chen, '客服话术库', expert_cite_enabled=False)
        cls.hidden_kb = f.knowledge_base(cls.business, cls.chen, '黑五资料', scope='workspace',
                                         workspace_row=cls.other_ws, cite='workspace_all')
        cls.chen_personal = f.knowledge_base(cls.business, cls.chen, 'Chen 个人', scope='personal', cite='owner_only')
        cls.epr = f.document(cls.ws_kb, '德国 EPR 法规摘要.pdf', cls.lillian, size_bytes=2516582, page_count=6)
        cls.brand = f.document(cls.ws_kb, '品牌手册 2026.pdf', cls.chen, index_state='indexing', index_progress=62)
        cls.sales = f.document(cls.ws_kb, '2025 Q3 销售报表.csv', cls.mark, data_date=date(2025, 8, 2))
        cls.failed = f.document(cls.closed_kb, '退货话术.docx', cls.chen, index_state='failed')
        cls.wangye = f.system_expert('AGT-007', '望野', '市场竞争与机会研究')
        member = cls.member_of(cls.lillian)
        cls.task = f.task(cls.business, member, '德国站竞品格局调研', lead_expert=cls.wangye, workspace=cls.ws)
        cls.chen_task = f.task(cls.business, cls.member_of(cls.chen), 'Chen 私有任务')
        for _ in range(3):
            Citation.objects.create(account=cls.business, document=cls.epr, task=cls.task, locator='§3')
        Citation.objects.create(account=cls.business, document=cls.epr, task=cls.chen_task, locator='§1')
        Citation.objects.create(account=cls.business, document=cls.sales, task=cls.task, locator='§2')

    @classmethod
    def member_of(cls, user):
        from backend.models import Membership
        return Membership.objects.get(user=user, account=cls.business)


class KnowledgeBaseTests(KnowledgeFixtures):
    def test_list_order_and_counts(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/knowledge-bases'))
        self.assertEqual([i['name'] for i in body['items']],
                         ['品牌手册 2026', '德国市场进入资料库', '客服话术库', '竞品研究资料'])
        ws_item = body['items'][1]
        self.assertEqual(ws_item['workspace'], {'id': self.ws.public_id, 'name': '德国市场进入'})
        self.assertEqual(ws_item['document_count'], 3)
        self.assertEqual(ws_item['processing_count'], 1)
        failed = body['items'][2]
        self.assertEqual(failed['failed_count'], 1)
        self.assertEqual(failed['failed_document_ids'], [self.failed.public_id])
        self.assertEqual(body['index_summary']['failed_count'], 1)
        self.assertIsNotNone(body['index_summary']['last_run_at'])
        for key in ('id', 'name', 'scope', 'workspace', 'cite_audience', 'expert_cite_enabled', 'editor_department',
                    'document_count', 'processing_count', 'failed_count', 'failed_document_ids',
                    'content_updated_at', 'can_edit'):
            self.assertIn(key, ws_item)

    def test_scope_citable_and_search(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/knowledge-bases', {'citable': 'true'}))
        self.assertEqual([i['name'] for i in body['items']], ['品牌手册 2026', '德国市场进入资料库', '竞品研究资料'])
        body = self.ok(self.get('app/knowledge-bases', {'scope': 'workspace'}))
        self.assertEqual(body['total'], 1)
        body = self.ok(self.get('app/knowledge-bases', {'q': '竞品'}))
        self.assertEqual([i['name'] for i in body['items']], ['竞品研究资料'])
        self.assert_error(self.get('app/knowledge-bases', {'scope': 'x'}), 422, 'VALIDATION_FAILED')

    def test_detail(self):
        self.login(self.lillian, self.business)
        self.ws_kb.editors.set([self.lillian, self.chen])
        body = self.ok(self.get('app/knowledge-bases/%s' % self.ws_kb.public_id))
        self.assertEqual(body['citation_count'], 4)
        self.assertEqual(body['stale_policy'], {'threshold_days': 90})
        self.assertEqual([e['name'] for e in body['editors']], ['Lillian Li', 'Chen Yu'])
        self.assertTrue(body['can_edit'])
        self.assertEqual(body['version'], 1)
        self.assert_error(self.get('app/knowledge-bases/%s' % self.hidden_kb.public_id), 404, 'NOT_FOUND')
        self.assert_error(self.get('app/knowledge-bases/%s' % self.chen_personal.public_id), 404, 'NOT_FOUND')

    def test_patch_writes_audit(self):
        self.login(self.lillian, self.business)
        url = 'app/knowledge-bases/%s' % self.ws_kb.public_id
        body = self.ok(self.patch(url, {'version': 1, 'expert_cite_enabled': False,
                                        'editor_ids': [str(self.chen.pk)]}))
        kb = body['knowledge_base']
        self.assertFalse(kb['expert_cite_enabled'])
        self.assertEqual(kb['version'], 2)
        self.assertEqual([e['name'] for e in kb['editors']], ['Chen Yu'])
        event = AuditEvent.objects.get(target_type='knowledge_base', target_id=self.ws_kb.public_id)
        self.assertEqual(event.action, 'knowledge_base.permission.update')
        audit = self.ok(self.get(url + '/audit'))
        self.assertEqual(audit['items'][0]['action'], 'knowledge_base.permission.update')
        self.assertEqual(audit['items'][0]['actor']['name'], 'Lillian Li')
        self.assert_error(self.patch(url, {'version': 1, 'expert_cite_enabled': True}), 409, 'VERSION_CONFLICT')
        self.assert_error(self.patch(url, {'version': 2, 'cite_audience': 'account_all'}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.patch(url, {'version': 2, 'editor_ids': [str(self.anna.pk)]}), 422,
                          'VALIDATION_FAILED')
        self.assert_error(self.patch(url, {'version': 2, 'editor_department_id': 'nope'}), 422, 'VALIDATION_FAILED')
        citable = self.ok(self.get('app/knowledge-bases', {'citable': 'true'}))
        self.assertNotIn('德国市场进入资料库', [i['name'] for i in citable['items']])

    def test_department_editor_and_permissions(self):
        department = Department.objects.create(account=self.business, name='客服组')
        DepartmentMember.objects.create(department=department, membership=self.member(self.mark))
        self.login(self.chen, self.business)
        url = 'app/knowledge-bases/%s' % self.account_kb.public_id
        body = self.ok(self.patch(url, {'version': 1, 'editor_department_id': str(department.pk)}))
        self.assertEqual(body['knowledge_base']['editor_department']['name'], '客服组')
        self.login(self.mark, self.business)
        self.assertTrue(self.ok(self.get(url))['can_edit'])
        self.login(self.mark, self.business)
        ws_url = 'app/knowledge-bases/%s' % self.ws_kb.public_id
        self.assert_error(self.patch(ws_url, {'version': 1, 'expert_cite_enabled': False}), 403, 'PERMISSION_DENIED')
        self.assert_error(self.get(ws_url + '/audit'), 403, 'PERMISSION_DENIED')


class DocumentTests(KnowledgeFixtures):
    def test_list_fields_sort_and_counts(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/documents', {'knowledge_base': self.ws_kb.public_id, 'sort': 'cited'}))
        self.assertEqual([i['title'] for i in body['items']][:2], ['德国 EPR 法规摘要.pdf', '2025 Q3 销售报表.csv'])
        epr = body['items'][0]
        self.assertEqual(epr['citation_count'], 3)
        self.assertEqual(epr['uploaded_by']['name'], 'Lillian Li')
        sales = body['items'][1]
        self.assertTrue(sales['is_stale'])
        brand = body['items'][2]
        self.assertEqual((brand['index_state'], brand['index_progress'], brand['citation_count']), ('indexing', 62, 0))
        for key in ('id', 'title', 'source_kind', 'uploaded_by', 'source_expert', 'source_url', 'refresh_interval',
                    'size_bytes', 'page_count', 'index_state', 'index_progress', 'data_date', 'is_stale',
                    'citation_count', 'updated_at'):
            self.assertIn(key, epr)
        body = self.ok(self.get('app/documents', {'knowledge_base': self.ws_kb.public_id, 'sort': 'name'}))
        self.assertEqual(body['items'][0]['title'], '2025 Q3 销售报表.csv')
        body = self.ok(self.get('app/documents', {'workspace': self.ws.public_id, 'q': 'EPR'}))
        self.assertEqual(body['total'], 1)
        self.assert_error(self.get('app/documents', {'sort': 'x'}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.get('app/documents', {'knowledge_base': self.hidden_kb.public_id}), 404, 'NOT_FOUND')

    def test_detail_related_entities(self):
        self.login(self.lillian, self.business)
        entity = KnowledgeEntity.objects.create(account=self.business, name='WEEE 注册', entity_type='regulation')
        entity.documents.add(self.epr)
        body = self.ok(self.get('app/documents/%s' % self.epr.public_id))
        self.assertEqual(body['knowledge_base']['workspace']['name'], '德国市场进入')
        self.assertEqual(body['related_entities'], [{'id': entity.public_id, 'name': 'WEEE 注册',
                                                     'entity_type': 'regulation'}])
        self.assertEqual(body['citation_count'], 3)
        self.assertIn('created_at', body)

    def store(self, doc, body):
        doc.content_ref = knowledge_service.save_document_content(doc, body)
        doc.save(update_fields=['content_ref'])

    def test_pages_and_download(self):
        self.login(self.lillian, self.business)
        storage = artifact_service.content_storage()
        original = storage.save('knowledge/test-original.pdf', ContentFile(b'%PDF-1.4 test'))
        self.store(self.epr, {'pages': [{'blocks': [{'kind': 'heading', 'text': '德国 EPR 法规摘要（2026 版）',
                                                     'spans': []}]}, {'blocks': []}],
                              'original': original, 'original_name': '德国 EPR 法规摘要.pdf',
                              'content_type': 'application/pdf'})
        body = self.ok(self.get('app/documents/%s/pages/1' % self.epr.public_id))
        self.assertEqual((body['page'], body['page_count']), (1, 6))
        self.assertEqual(body['blocks'][0]['text'], '德国 EPR 法规摘要（2026 版）')
        self.assert_error(self.get('app/documents/%s/pages/3' % self.epr.public_id), 404, 'NOT_FOUND')
        response = self.get('app/documents/%s/download' % self.epr.public_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'%PDF-1.4 test')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assert_error(self.get('app/documents/%s/pages/1' % self.brand.public_id), 409, 'STATE_CONFLICT')
        self.assert_error(self.get('app/documents/%s/download' % self.sales.public_id), 503, 'SOURCE_UNAVAILABLE')

    def test_create_web_document(self):
        self.login(self.chen, self.business)
        self.ws_kb.editors.add(self.chen)
        data = {'knowledge_base_id': self.ws_kb.public_id, 'source_kind': 'web',
                'source_url': 'https://www.amazon.de/kleingeraete', 'refresh_interval': 'weekly',
                'title': 'Amazon.de 小家电类目页'}
        body = self.ok(self.post('app/documents', data), 201)
        self.assertEqual(body['document']['index_state'], 'queued')
        self.assertEqual(body['document']['refresh_interval'], 'weekly')
        self.assert_error(self.post('app/documents', dict(data, source_url='ftp://x')), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post('app/documents', dict(data, source_kind='upload')), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post('app/documents', dict(data, refresh_interval='hourly')), 422, 'VALIDATION_FAILED')
        self.login(self.mark, self.business)
        self.assert_error(self.post('app/documents', data), 403, 'PERMISSION_DENIED')

    def test_reindex_only_failed(self):
        self.login(self.chen, self.business)
        body = self.ok(self.post('app/documents/%s/reindex' % self.failed.public_id, {}), 202)
        self.assertEqual(body['document']['index_state'], 'queued')
        self.assert_error(self.post('app/documents/%s/reindex' % self.failed.public_id, {}), 409, 'STATE_CONFLICT')
        self.login(self.mark, self.business)
        KnowledgeDocument.objects.filter(pk=self.failed.pk).update(index_state='failed')
        self.assert_error(self.post('app/documents/%s/reindex' % self.failed.public_id, {}), 403, 'PERMISSION_DENIED')


class GraphTests(KnowledgeFixtures):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        make = lambda name, kind, **kw: KnowledgeEntity.objects.create(account=cls.business, name=name,
                                                                       entity_type=kind, **kw)
        cls.germany = make('德国市场', 'market')
        cls.weee = make('WEEE 注册', 'regulation', subtitle='法规 · 德国市场')
        cls.x1 = make('我们的 X1 系列', 'product')
        make('竞品 A · Brand K', 'competitor')
        KnowledgeRelation.objects.create(account=cls.business, from_entity=cls.germany, to_entity=cls.weee,
                                         kind='requires')
        KnowledgeRelation.objects.create(account=cls.business, from_entity=cls.x1, to_entity=cls.germany,
                                         kind='plans_to_enter')
        new = KnowledgeFact.objects.create(account=cls.business, entity=cls.weee, statement='注册周期 4–6 周',
                                           source_document=cls.epr, source_date=date(2026, 10, 1),
                                           conflict_key='weee_cycle')
        KnowledgeFact.objects.create(account=cls.business, entity=cls.weee, statement='上架前必须取得 WEEE 注册号',
                                     source_document=cls.epr, source_date=date(2026, 10, 1))
        cls.old = KnowledgeFact.objects.create(account=cls.business, entity=cls.weee, statement='注册周期约 8 周',
                                               source_date=date(2025, 6, 1), validity='superseded',
                                               superseded_by=new, conflict_key='weee_cycle')
        cls.new = new
        Citation.objects.create(account=cls.business, fact=new, task=cls.task)
        Citation.objects.create(account=cls.business, fact=new, task=cls.chen_task)

    def test_list_and_type_counts(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/graph/entities'))
        self.assertEqual(body['total'], 4)
        self.assertEqual(body['type_counts']['market'], 1)
        self.assertEqual(body['type_counts']['competitor'], 1)
        weee = next(i for i in body['items'] if i['name'] == 'WEEE 注册')
        self.assertTrue(weee['has_conflict'])
        body = self.ok(self.get('app/graph/entities', {'type': 'regulation', 'q': 'WEEE'}))
        self.assertEqual([i['name'] for i in body['items']], ['WEEE 注册'])
        self.assertEqual(body['type_counts']['market'], 1)
        self.assert_error(self.get('app/graph/entities', {'type': 'x'}), 422, 'VALIDATION_FAILED')

    def test_entity_detail(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/graph/entities/%s' % self.weee.public_id))
        self.assertEqual(body['subtitle'], '法规 · 德国市场')
        self.assertEqual(body['facts_total'], 3)
        self.assertEqual(len(body['facts']), 2)
        self.assertEqual(body['facts'][0]['source_label'], '德国 EPR 法规摘要.pdf')
        self.assertEqual(body['conflicts'][0]['preferred_fact_id'], self.new.public_id)
        self.assertEqual(len(body['conflicts'][0]['facts']), 2)
        self.assertEqual(body['cited_task_count'], 1)
        self.assertEqual(body['cited_tasks'][0]['title'], '德国站竞品格局调研')
        full = self.ok(self.get('app/graph/entities/%s' % self.weee.public_id, {'include_superseded': 'true'}))
        self.assertEqual(len(full['facts']), 3)
        self.assertEqual(full['facts'][2]['superseded_by'], self.new.public_id)

    def test_neighborhood(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.get('app/graph/entities/%s/neighborhood' % self.germany.public_id))
        self.assertEqual(body['nodes'][0]['name'], '德国市场')
        self.assertEqual({n['name'] for n in body['nodes']}, {'德国市场', 'WEEE 注册', '我们的 X1 系列'})
        self.assertEqual(len(body['edges']), 2)
        self.assertIn({'from': self.germany.public_id, 'to': self.weee.public_id, 'kind': 'requires'}, body['edges'])

    def test_other_account_entity_404(self):
        self.login(self.lillian)
        self.assert_error(self.get('app/graph/entities/%s' % self.weee.public_id), 404, 'NOT_FOUND')

class GraphConflictResolveTests(GraphTests):
    def url(self, key='weee_cycle'):
        return 'app/graph/entities/%s/conflicts/%s/resolve' % (self.weee.public_id, key)

    def test_resolve_prefers_chosen_fact_and_clears_conflict(self):
        self.login(self.lillian, self.business)
        body = self.ok(self.post(self.url(), {'preferred_fact_id': self.old.public_id}))
        self.assertEqual(body['conflicts'], [])
        self.assertFalse(body['has_conflict'])
        self.old.refresh_from_db()
        self.new.refresh_from_db()
        self.assertEqual((self.old.validity, self.old.superseded_by_id), ('current', None))
        self.assertEqual((self.new.validity, self.new.superseded_by_id), ('superseded', self.old.pk))
        self.assertIsNone(self.new.conflict_key)
        self.assertIsNone(self.old.conflict_key)
        listing = self.ok(self.get('app/graph/entities', {'q': 'WEEE'}))
        self.assertFalse(listing['items'][0]['has_conflict'])
        self.assert_error(self.post(self.url(), {'preferred_fact_id': self.old.public_id}), 404, 'NOT_FOUND')

    def test_resolve_validation(self):
        self.login(self.lillian, self.business)
        self.assert_error(self.post(self.url(), {}), 422, 'INVALID_REQUEST')
        other = KnowledgeFact.objects.create(account=self.business, entity=self.weee, statement='x')
        self.assert_error(self.post(self.url(), {'preferred_fact_id': other.public_id}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post(self.url('nope'), {'preferred_fact_id': self.new.public_id}), 404, 'NOT_FOUND')

    def test_resolve_requires_edit_rights(self):
        self.login(self.mark, self.business)
        self.assert_error(self.post(self.url(), {'preferred_fact_id': self.new.public_id}), 403, 'PERMISSION_DENIED')
        self.login(self.lillian)
        self.assert_error(self.post(self.url(), {'preferred_fact_id': self.new.public_id}), 404, 'NOT_FOUND')
