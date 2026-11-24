"""Contract tests: app/workspaces/{id}/knowledge-bases (D106, 24 屏 资料来源)."""
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from backend.models import AuditEvent, BillingAccount
from backend.workbench.models import (KnowledgeBase, KnowledgeDocument, Workspace, WorkspaceKnowledgeSource,
                                      WorkspaceMember)
from backend.workbench.tests.base import make_business
from backend.workbench.tests.test_workspaces import WorkspaceBase


class WorkspaceKnowledgeSourceTests(WorkspaceBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.main_kb = KnowledgeBase.objects.get(account=cls.business, name='德国市场进入资料库')
        cls.law = KnowledgeBase.objects.create(account=cls.business, name='德国市场法规库', scope='workspace',
                                               workspace=cls.ws, owner=cls.lillian, cite_audience='workspace_all')
        cls.law.editors.set([cls.lillian, cls.chen])
        KnowledgeDocument.objects.create(account=cls.business, knowledge_base=cls.law, title='WEEE 注册指南 2026.pdf',
                                         source_kind='upload', content_ref='store:y', index_state='indexed')
        cls.shared = KnowledgeBase.objects.create(account=cls.business, name='竞品研究资料', scope='account',
                                                  owner=cls.chen, cite_audience='account_all')
        for kb in (cls.law, cls.shared):
            WorkspaceKnowledgeSource.objects.create(account=cls.business, workspace=cls.ws, knowledge_base=kb,
                                                    added_by=cls.lillian)
        cls.other = make_business('别家企业', members=((cls.anna, 'owner'),))
        cls.other_ws = Workspace.objects.create(account=cls.other, name='别家工作区', created_from='blank',
                                                created_by=cls.anna)
        WorkspaceMember.objects.create(account=cls.other, workspace=cls.other_ws, user=cls.anna, role='owner',
                                       state='active')
        cls.other_kb = KnowledgeBase.objects.create(account=cls.other, name='别家资料库', scope='workspace',
                                                    workspace=cls.other_ws, owner=cls.anna,
                                                    cite_audience='workspace_all')
        WorkspaceKnowledgeSource.objects.create(account=cls.other, workspace=cls.other_ws, knowledge_base=cls.other_kb)

    def sources(self, workspace=None):
        return self.url('/knowledge-bases', workspace)

    def source(self, kb, workspace=None):
        return self.url('/knowledge-bases/%s' % kb.public_id, workspace)

    def snapshot(self, kb):
        kb.refresh_from_db()
        return (kb.name, kb.scope, kb.workspace_id, kb.owner_id, kb.cite_audience, kb.expert_cite_enabled,
                kb.version, sorted(kb.editors.values_list('pk', flat=True)),
                list(kb.documents.order_by('pk').values_list('pk', 'title', 'deleted_at')))

    def test_list_returns_only_referenced_bases(self):
        self.as_(self.lillian)
        body = self.ok(self.get(self.sources()))
        self.assertEqual([item['name'] for item in body['items']], ['德国市场法规库', '竞品研究资料'])
        self.assertTrue(body['can_manage'])
        law = body['items'][0]
        self.assertEqual(law['id'], self.law.public_id)
        self.assertEqual(law['scope'], 'workspace')
        self.assertEqual(law['workspace'], {'id': self.ws.public_id, 'name': '德国市场进入'})
        self.assertEqual(law['document_count'], 1)
        self.assertTrue({'cite_audience', 'expert_cite_enabled', 'editor_department', 'can_edit',
                         'processing_count', 'failed_count', 'content_updated_at'} <= set(law))
        self.as_(self.mark)
        body = self.ok(self.get(self.sources()))
        self.assertEqual([item['name'] for item in body['items']], ['德国市场法规库', '竞品研究资料'])
        self.assertFalse(body['can_manage'])

    def test_workspace_without_sources_lists_nothing(self):
        self.as_(self.lillian)
        body = self.ok(self.get(self.sources(self.ws2)))
        self.assertEqual(body['items'], [])
        self.assertFalse(body['can_manage'])

    def test_remove_only_drops_the_reference(self):
        before = self.snapshot(self.law)
        self.as_(self.lillian)
        counts = self.ok(self.get(self.url()))['counts']
        body = self.ok(self.delete(self.source(self.law)))
        self.assertEqual(body, {'knowledge_base': {'id': self.law.public_id, 'name': '德国市场法规库'}})
        self.assertFalse(WorkspaceKnowledgeSource.objects.filter(workspace=self.ws, knowledge_base=self.law).exists())
        self.assertEqual(self.snapshot(self.law), before)
        self.assertEqual(self.ok(self.get('app/knowledge-bases/%s' % self.law.public_id))['name'], '德国市场法规库')
        counts['documents'] -= 1
        self.assertEqual(self.ok(self.get(self.url()))['counts'], counts)
        self.assertEqual([item['name'] for item in self.ok(self.get(self.sources()))['items']], ['竞品研究资料'])
        event = AuditEvent.objects.get(account=self.business, action='workspace.knowledge_source.remove')
        self.assertEqual((event.actor_id, event.target_type, event.target_id),
                         (self.lillian.pk, 'workspace', self.ws.public_id))
        self.assertIn('德国市场法规库', event.summary)
        self.assert_error(self.delete(self.source(self.law)), 404, 'NOT_FOUND')

    def test_remove_requires_workspace_owner(self):
        for user in (self.chen, self.mark):
            self.as_(user)
            self.assert_error(self.delete(self.source(self.law)), 403, 'PERMISSION_DENIED')
        self.assertTrue(WorkspaceKnowledgeSource.objects.filter(workspace=self.ws, knowledge_base=self.law).exists())
        self.assertFalse(AuditEvent.objects.filter(action='workspace.knowledge_source.remove').exists())

    def test_unreferenced_or_hidden_is_not_found(self):
        self.as_(self.lillian)
        self.assert_error(self.delete(self.source(self.main_kb)), 404, 'NOT_FOUND')
        self.assert_error(self.get(self.sources(self.hidden)), 404, 'NOT_FOUND')
        self.assert_error(self.delete(self.source(self.law, self.hidden)), 404, 'NOT_FOUND')
        self.as_(self.anna)
        self.assert_error(self.get(self.sources()), 404, 'NOT_FOUND')

    def test_cross_account_is_not_found_and_rejected(self):
        self.as_(self.lillian)
        self.assert_error(self.get(self.sources(self.other_ws)), 404, 'NOT_FOUND')
        self.assert_error(self.delete(self.source(self.other_kb, self.other_ws)), 404, 'NOT_FOUND')
        self.assert_error(self.delete(self.source(self.other_kb)), 404, 'NOT_FOUND')
        self.assertTrue(WorkspaceKnowledgeSource.objects.filter(knowledge_base=self.other_kb).exists())
        self.login(self.anna, self.other)
        body = self.ok(self.get(self.sources(self.other_ws)))
        self.assertEqual([item['name'] for item in body['items']], ['别家资料库'])
        self.assert_error(self.delete(self.source(self.law, self.other_ws)), 404, 'NOT_FOUND')
        with self.assertRaises(ValidationError):
            WorkspaceKnowledgeSource.objects.create(account=self.business, workspace=self.ws,
                                                    knowledge_base=self.other_kb)
        with self.assertRaises(ValidationError):
            WorkspaceKnowledgeSource.objects.create(account=self.other, workspace=self.ws, knowledge_base=self.main_kb)

    def test_document_count_follows_visible_sources(self):
        """D108: counts.documents only counts documents in the bases the workspace references and the user can see."""
        private = KnowledgeBase.objects.create(account=self.business, name='Chen 的个人库', scope='personal',
                                               owner=self.chen, cite_audience='owner_only')
        for title in ('个人笔记 1', '个人笔记 2', '个人笔记 3'):
            KnowledgeDocument.objects.create(account=self.business, knowledge_base=private, title=title,
                                             source_kind='upload', content_ref='store:p', index_state='indexed')
        WorkspaceKnowledgeSource.objects.create(account=self.business, workspace=self.ws, knowledge_base=private,
                                                added_by=self.chen)
        KnowledgeDocument.objects.create(account=self.business, knowledge_base=self.law, title='已删除.pdf',
                                         source_kind='upload', content_ref='store:d', index_state='indexed',
                                         deleted_at=timezone.now())
        self.as_(self.lillian)
        body = self.ok(self.get(self.url()))
        listed = self.ok(self.get(self.sources()))['items']
        self.assertEqual(body['counts']['documents'], sum(item['document_count'] for item in listed))
        self.assertEqual(body['counts']['documents'], 1)
        self.as_(self.chen)
        listed = self.ok(self.get(self.sources()))['items']
        self.assertEqual([item['name'] for item in listed], ['Chen 的个人库', '德国市场法规库', '竞品研究资料'])
        self.assertEqual(self.ok(self.get(self.url()))['counts']['documents'], 4)

    def test_remove_hidden_base_is_not_found(self):
        private = KnowledgeBase.objects.create(account=self.business, name='Chen 的个人库', scope='personal',
                                               owner=self.chen, cite_audience='owner_only')
        WorkspaceKnowledgeSource.objects.create(account=self.business, workspace=self.ws, knowledge_base=private,
                                                added_by=self.chen)
        self.as_(self.lillian)
        self.assert_error(self.delete(self.source(private)), 404, 'NOT_FOUND')
        self.assertTrue(WorkspaceKnowledgeSource.objects.filter(workspace=self.ws, knowledge_base=private).exists())
        self.assertFalse(AuditEvent.objects.filter(action='workspace.knowledge_source.remove').exists())

class WorkspaceKnowledgeSourceSeedTests(TestCase):
    def test_seeded_workspace_references_only_the_regulation_base(self):
        for _ in range(2):
            call_command('seed_app', only=['core', 'experts', 'knowledge'], stdout=StringIO())
        account = BillingAccount.objects.get(name='Sage Demo 企业', kind='business')
        workspace = Workspace.objects.get(account=account, name='德国市场进入')
        self.assertEqual(list(WorkspaceKnowledgeSource.objects.filter(workspace=workspace)
                              .values_list('knowledge_base__name', flat=True)), ['德国市场法规库'])
        main = KnowledgeBase.objects.get(account=account, name='德国市场进入资料库')
        self.assertEqual((main.scope, main.workspace_id), ('workspace', workspace.pk))

class WorkspaceKnowledgeSourceMigrationTests(TransactionTestCase):
    """0012 keeps what each workspace showed before: one reference per workspace-scope base."""

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([('workbench', target)])
        return executor.loader.project_state([('workbench', target)]).apps

    def test_existing_workspace_bases_become_sources(self):
        self.addCleanup(self.migrate, '0012_d106_workspace_knowledge_sources')
        apps = self.migrate('0011_d81_playbook_template')
        user = apps.get_model('auth', 'User').objects.create(username='mig-owner', email='mig@example.com')
        account = apps.get_model('backend', 'BillingAccount').objects.create(kind='business', name='迁移企业')
        Workspace, KnowledgeBase = apps.get_model('workbench', 'Workspace'), apps.get_model('workbench', 'KnowledgeBase')
        ws = Workspace.objects.create(account=account, name='德国市场进入', created_from='blank', created_by=user)
        kept = [KnowledgeBase.objects.create(account=account, name=name, scope='workspace', workspace=ws, owner=user,
                                             cite_audience='workspace_all') for name in ('资料库', '法规库')]
        KnowledgeBase.objects.create(account=account, name='企业库', scope='account', owner=user,
                                     cite_audience='account_all')
        apps = self.migrate('0012_d106_workspace_knowledge_sources')
        rows = apps.get_model('workbench', 'WorkspaceKnowledgeSource').objects.order_by('knowledge_base_id')
        self.assertEqual([(row.workspace_id, row.knowledge_base_id, row.account_id) for row in rows],
                         [(ws.pk, kb.pk, account.pk) for kb in kept])
