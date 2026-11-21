"""Contract tests: app/artifacts, app/reviews, app/citations (backend-plan 2.1-2.3, screens 13, 23, 41, 54-56)."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from backend.workbench.models import (AccountPolicy, Artifact, ArtifactShare, ArtifactUserState, ArtifactVersion,
                                      Citation, ContentReview, ContentReviewIssue, Expert, KnowledgeBase,
                                      KnowledgeDocument, Task, TaskDelivery, Workspace, WorkspaceMember,
                                      WorkspaceRuleVersion)
from backend.workbench.services import artifacts as store
from backend.workbench.tests.base import AppTestCase

MEMORY_STORAGE = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    'artifacts': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
}

REPORT_V1 = {'format': 'blocks', 'blocks': [
    {'type': 'heading', 'level': 1, 'text': '结论'},
    {'type': 'paragraph', 'text': '结论：建议进入德国站，首批备货 1,500 台。价格带：主力价格带为 €39–69。'},
]}
REPORT_V2 = {'format': 'blocks', 'blocks': [
    {'type': 'heading', 'level': 1, 'text': '结论'},
    {'type': 'paragraph', 'text': '结论：建议进入德国站，首批备货 800 台，以 6 周销量验证后再补货。价格带：主力价格带为 €39–69。'},
    {'type': 'table', 'columns': ['品牌来源', '前 20 名席位'], 'rows': [['德国本土品牌', '6 席']]},
]}
LISTING = {'format': 'blocks', 'blocks': [
    {'type': 'paragraph', 'text': 'Luftreiniger X1 – tötet 99,9 % aller Viren'},
    {'type': 'list_item', 'text': 'Du wirst die frische Luft lieben – leise und effizient.'},
]}


def make_expert(name, specialty=''):
    return Expert.objects.create(source='system', group='role', name=name, title=specialty or name,
                                 specialty=specialty or None, responsibilities='-', version_label='v1',
                                 status='published', share_scope='account', communication_style='concise',
                                 uncertainty_policy='ask_first')


class World:
    """Shared fixture: workspace W (Lillian owner / Chen editor / Mark viewer) with tasks and artifacts."""

    @classmethod
    def build_world(cls):
        now = timezone.now()
        cls.wangye = make_expert('望野', '市场竞争与机会研究')
        cls.anjie = make_expert('安界', '产品合规与隐私')
        cls.shouheng = make_expert('守衡', '内控审计与独立复核')
        cls.mlillian, cls.mchen, cls.mmark = (cls.member(cls, u) for u in (cls.lillian, cls.chen, cls.mark))
        cls.ws = Workspace.objects.create(account=cls.business, name='德国市场进入', created_from='blank',
                                          created_by=cls.lillian)
        cls.ws_other = Workspace.objects.create(account=cls.business, name='黑五大促', created_from='blank',
                                                created_by=cls.chen)
        for user, role in ((cls.lillian, 'owner'), (cls.chen, 'editor'), (cls.mark, 'viewer')):
            WorkspaceMember.objects.create(account=cls.business, workspace=cls.ws, user=user, role=role, state='active')
        WorkspaceMember.objects.create(account=cls.business, workspace=cls.ws_other, user=cls.chen, role='owner',
                                       state='active')
        cls.rules = WorkspaceRuleVersion.objects.create(account=cls.business, workspace=cls.ws, number=3,
                                                        rules=['对外内容一律用德语。'], author=cls.lillian,
                                                        change_note='x')
        cls.task = cls.make_task('德国站竞品格局调研', cls.ws, cls.mlillian, status='awaiting_user')
        cls.task_alone = cls.make_task('X1 德语 Listing 上架前审核', None, cls.mlillian, status='delivered')
        cls.task_chen = cls.make_task('Chen 的私人任务', None, cls.mchen)
        cls.report = cls.make_artifact('德国站竞品格局报告', 'document', cls.ws, cls.task, [REPORT_V1, REPORT_V2],
                                       when=now - timedelta(minutes=10))
        cls.sheet = cls.make_artifact('竞品价格带.xlsx', 'spreadsheet', cls.ws, cls.task,
                                      [{'format': 'table', 'columns': ['a'], 'rows': [['1']]}],
                                      when=now - timedelta(minutes=20))
        cls.listing = cls.make_artifact('X1 Listing DE', 'document', None, cls.task_alone, [REPORT_V1, LISTING],
                                        when=now - timedelta(days=1), by_user=cls.lillian)
        cls.image = cls.make_artifact('X1 主图.png', 'image', None, cls.task_alone,
                                      [{'format': 'image', 'caption': '主图'}], when=now - timedelta(days=2),
                                      by_user=cls.lillian)
        cls.private = cls.make_artifact('Chen 的成果', 'document', None, cls.task_chen, [REPORT_V1],
                                        when=now - timedelta(days=3))
        cls.kb = KnowledgeBase.objects.create(account=cls.business, name='德国市场进入资料库', scope='workspace',
                                              workspace=cls.ws, owner=cls.lillian, cite_audience='workspace_all')
        cls.doc = KnowledgeDocument.objects.create(account=cls.business, knowledge_base=cls.kb,
                                                   title='德国 EPR 法规摘要.pdf', source_kind='upload',
                                                   content_ref='store:x', index_state='indexed')
        v2 = cls.report.current_version
        Citation.objects.create(account=cls.business, task=cls.task, artifact_version=v2, ref_index=2,
                                external_label='Amazon.de 类目页 · 11月3日')
        Citation.objects.create(account=cls.business, task=cls.task, artifact_version=v2, ref_index=1,
                                document=cls.doc, cited_version=2, locator='§3')
        Citation.objects.create(account=cls.business, task=cls.task_chen, document=cls.doc, locator='§9')

    @classmethod
    def make_task(cls, title, workspace, member, status='running'):
        return Task.objects.create(account=cls.business, workspace=workspace, title=title, creator=member,
                                   owner=member, model_mode='auto', status=status, lead_expert=cls.wangye)

    @classmethod
    def make_artifact(cls, title, kind, workspace, task, bodies, when=None, by_user=None):
        artifact = Artifact.objects.create(account=cls.business, workspace=workspace, source_task=task, title=title,
                                           kind=kind, created_by_user=by_user,
                                           created_by_expert=None if by_user else cls.wangye)
        for body in bodies:
            store.create_version(artifact, body, author_expert=None if by_user else cls.wangye, author_user=by_user)
        if when:
            ArtifactVersion.objects.filter(pk=artifact.current_version_id).update(created_at=when)
        artifact.refresh_from_db()
        return artifact


@override_settings(STORAGES=MEMORY_STORAGE)
class ArtifactBase(World, AppTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.build_world()

    def as_(self, user):
        self.login(user, self.business)


class ArtifactListTests(ArtifactBase):
    def test_list_fields_counts_and_default_sort(self):
        self.as_(self.lillian)
        body = self.ok(self.get('app/artifacts'))
        titles = [item['title'] for item in body['items']]
        self.assertEqual(titles, ['德国站竞品格局报告', '竞品价格带.xlsx', 'X1 Listing DE', 'X1 主图.png'])
        self.assertEqual((body['offset'], body['total'], body['next_offset']), (0, 4, None))
        first = body['items'][0]
        self.assertEqual(set(first), {'id', 'title', 'kind', 'workspace', 'source_task', 'current_version', 'share',
                                      'updated_at', 'trashed_at', 'last_opened_at'})
        self.assertEqual(first['workspace'], {'id': self.ws.public_id, 'name': '德国市场进入'})
        self.assertEqual(first['source_task'], {'id': self.task.public_id, 'title': '德国站竞品格局调研'})
        version = first['current_version']
        self.assertEqual((version['number'], version['state'], version['previous_number']), (2, 'draft', 1))
        self.assertEqual(version['author'], {'kind': 'expert', 'name': '望野', 'avatar_ref': None})
        self.assertEqual(first['updated_at'], version['created_at'])
        counts = body['counts']
        self.assertEqual(counts['all'], 4)
        self.assertEqual(counts['kinds'], {'document': 2, 'spreadsheet': 1, 'presentation': 0, 'webpage': 0,
                                           'image': 1})
        self.assertEqual(counts['trash'], 0)
        self.assertEqual(counts['workspaces'], [{'id': self.ws.public_id, 'name': '德国市场进入', 'count': 2},
                                                {'id': None, 'name': None, 'count': 2}])

    def test_filters(self):
        self.as_(self.lillian)
        kinds = self.ok(self.get('app/artifacts', {'kind': 'spreadsheet'}))
        self.assertEqual([item['title'] for item in kinds['items']], ['竞品价格带.xlsx'])
        none = self.ok(self.get('app/artifacts', {'workspace': 'none'}))
        self.assertEqual({item['title'] for item in none['items']}, {'X1 Listing DE', 'X1 主图.png'})
        in_ws = self.ok(self.get('app/artifacts', {'workspace': self.ws.public_id}))
        self.assertEqual(in_ws['total'], 2)
        by_task = self.ok(self.get('app/artifacts', {'task': self.task.public_id, 'sort': 'updated'}))
        self.assertEqual(by_task['summary'], {'created': 1, 'modified': 1})
        self.assertEqual(self.ok(self.get('app/artifacts', {'q': '价格'}))['total'], 1)
        mine = self.ok(self.get('app/artifacts', {'scope': 'mine'}))
        self.assertEqual(mine['total'], 4)
        self.as_(self.mark)
        shared = self.ok(self.get('app/artifacts', {'scope': 'shared'}))
        self.assertEqual({item['title'] for item in shared['items']}, {'德国站竞品格局报告', '竞品价格带.xlsx'})
        self.assertEqual(self.ok(self.get('app/artifacts', {'scope': 'mine'}))['total'], 0)

    def test_sort_opened_lists_only_my_opened_artifacts(self):
        self.as_(self.lillian)
        self.ok(self.get('app/artifacts/%s' % self.sheet.public_id))
        self.ok(self.get('app/artifacts/%s' % self.report.public_id))
        body = self.ok(self.get('app/artifacts', {'sort': 'opened'}))
        self.assertEqual([item['title'] for item in body['items']], ['德国站竞品格局报告', '竞品价格带.xlsx'])
        self.assertIsNotNone(body['items'][0]['last_opened_at'])

    def test_invalid_query(self):
        self.as_(self.lillian)
        self.assert_error(self.get('app/artifacts', {'color': 'red'}), 422, 'INVALID_REQUEST')
        self.assert_error(self.get('app/artifacts', {'kind': 'video'}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.get('app/artifacts', {'sort': 'size'}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.get('app/artifacts', {'workspace': self.ws_other.public_id}), 404, 'NOT_FOUND')
        self.assert_error(self.get('app/artifacts', {'task': self.task_chen.public_id}), 404, 'NOT_FOUND')

    def test_non_member_is_404_and_private_artifacts_hidden(self):
        self.login(self.anna, self.business)
        self.assert_error(self.get('app/artifacts'), 404, 'NOT_FOUND')
        self.as_(self.lillian)
        self.assert_error(self.get('app/artifacts/%s' % self.private.public_id), 404, 'NOT_FOUND')


class ArtifactDetailTests(ArtifactBase):
    def test_detail_fields_and_records_last_opened(self):
        TaskDelivery.objects.create(account=self.business, task=self.task, artifact_version=self.report.current_version,
                                    summary='s')
        self.as_(self.lillian)
        body = self.ok(self.get('app/artifacts/%s' % self.report.public_id))
        self.assertEqual(body['title'], '德国站竞品格局报告')
        self.assertEqual(body['current_version']['citation_count'], 2)
        self.assertEqual(body['current_version']['author']['name'], '望野')
        self.assertEqual(body['delivery']['decision'], 'pending')
        self.assertTrue(body['can_edit'])
        self.assertTrue(ArtifactUserState.objects.filter(artifact=self.report, user=self.lillian).exists())
        self.as_(self.mark)
        self.assertFalse(self.ok(self.get('app/artifacts/%s' % self.report.public_id))['can_edit'])

    def test_versions_content_and_citations(self):
        self.as_(self.lillian)
        versions = self.ok(self.get('app/artifacts/%s/versions' % self.report.public_id))
        self.assertEqual([item['number'] for item in versions['items']], [2, 1])
        self.assertEqual(set(versions['items'][0]), {'number', 'state', 'label', 'author', 'created_at',
                                                     'change_summary', 'restored_from', 'size_bytes'})
        content = self.ok(self.get('app/artifacts/%s/versions/2/content' % self.report.public_id))
        self.assertEqual(content['body'], REPORT_V2)
        self.assertEqual((content['number'], content['kind']), (2, 'document'))
        self.assert_error(self.get('app/artifacts/%s/versions/9/content' % self.report.public_id), 404, 'NOT_FOUND')
        citations = self.ok(self.get('app/artifacts/%s/versions/2/citations' % self.report.public_id))
        self.assertEqual([item['ref_index'] for item in citations['items']], [1, 2])
        self.assertEqual(citations['items'][0]['document'], {'id': self.doc.public_id, 'title': '德国 EPR 法规摘要.pdf'})
        self.assertEqual(citations['items'][0]['cited_version'], 2)
        self.assertEqual(citations['items'][1]['label'], 'Amazon.de 类目页 · 11月3日')
        self.assertEqual(citations['total'], 2)

    def test_missing_content_is_source_unavailable(self):
        ArtifactVersion.objects.filter(pk=self.report.current_version_id).update(content_ref='store:missing.json')
        self.as_(self.lillian)
        self.assert_error(self.get('app/artifacts/%s/versions/2/content' % self.report.public_id), 503,
                          'SOURCE_UNAVAILABLE')

    def test_download(self):
        self.as_(self.lillian)
        response = self.get('app/artifacts/%s/versions/2/download' % self.report.public_id)
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('首批备货 800 台', response.content.decode())

    def test_diff(self):
        self.as_(self.lillian)
        body = self.ok(self.get('app/artifacts/%s/versions/2/diff' % self.report.public_id))
        self.assertEqual(body['base']['number'], 1)
        self.assertEqual(body['target']['number'], 2)
        self.assertEqual(body['restore_preview'], {'next_number': 3})
        self.assertTrue(body['can_restore'])
        self.assertIn({'deleted': '首批备货 1,500 台。', 'inserted': '首批备货 800 台，以 6 周销量验证后再补货。'},
                      body['hunks'])
        self.assertIn({'context': '价格带：主力价格带为 €39–69。'}, body['hunks'])
        self.assertIn({'inserted': '德国本土品牌 | 6 席'}, body['hunks'])
        explicit = self.ok(self.get('app/artifacts/%s/versions/2/diff' % self.report.public_id, {'base': '1'}))
        self.assertEqual(explicit['hunks'], body['hunks'])
        self.assert_error(self.get('app/artifacts/%s/versions/2/diff' % self.report.public_id, {'base': '2'}), 422,
                          'VALIDATION_FAILED')
        self.assert_error(self.get('app/artifacts/%s/versions/1/diff' % self.report.public_id), 422,
                          'VALIDATION_FAILED')
        self.as_(self.mark)
        self.assertFalse(self.ok(self.get('app/artifacts/%s/versions/2/diff' % self.report.public_id))['can_restore'])

    def test_diff_non_text_reports_change_only(self):
        store.create_version(self.sheet, {'format': 'table', 'columns': ['a'], 'rows': [['2']]})
        self.as_(self.lillian)
        body = self.ok(self.get('app/artifacts/%s/versions/2/diff' % self.sheet.public_id))
        self.assertEqual(body['hunks'], [])
        self.assertTrue(body['changed'])


class ArtifactWriteTests(ArtifactBase):
    def test_patch_rename_and_validation(self):
        self.as_(self.chen)
        body = self.ok(self.patch('app/artifacts/%s' % self.report.public_id, {'title': ' 新标题 '}))
        self.assertEqual(body['artifact']['title'], '新标题')
        self.assert_error(self.patch('app/artifacts/%s' % self.report.public_id, {'title': ''}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.patch('app/artifacts/%s' % self.report.public_id, {'title': 'x' * 121}), 422,
                          'VALIDATION_FAILED')
        self.assert_error(self.patch('app/artifacts/%s' % self.report.public_id, {'size': 1}), 422, 'INVALID_REQUEST')
        self.as_(self.mark)
        self.assert_error(self.patch('app/artifacts/%s' % self.report.public_id, {'title': 'x'}), 403,
                          'PERMISSION_DENIED')

    def test_patch_move_workspace(self):
        self.as_(self.lillian)
        body = self.ok(self.patch('app/artifacts/%s' % self.report.public_id, {'workspace_id': None}))
        self.assertIsNone(body['artifact']['workspace'])
        body = self.ok(self.patch('app/artifacts/%s' % self.listing.public_id, {'workspace_id': self.ws.public_id}))
        self.assertEqual(body['artifact']['workspace']['id'], self.ws.public_id)
        self.assert_error(self.patch('app/artifacts/%s' % self.listing.public_id,
                                     {'workspace_id': self.ws_other.public_id}), 404, 'NOT_FOUND')
        WorkspaceMember.objects.create(account=self.business, workspace=self.ws_other, user=self.lillian,
                                       role='viewer', state='active')
        self.assert_error(self.patch('app/artifacts/%s' % self.listing.public_id,
                                     {'workspace_id': self.ws_other.public_id}), 403, 'PERMISSION_DENIED')

    def test_write_requires_account_header(self):
        self.as_(self.lillian)
        self.assert_error(self.patch('app/artifacts/%s' % self.report.public_id, {'title': 'x'}, account=False), 409,
                          'ACCOUNT_CHANGED')

    def test_trash_and_restore(self):
        self.as_(self.lillian)
        url = 'app/artifacts/%s/trash' % self.sheet.public_id
        body = self.ok(self.post(url))
        self.assertEqual(body['artifact']['id'], self.sheet.public_id)
        self.assertIsNotNone(body['artifact']['trashed_at'])
        self.assert_error(self.post(url), 409, 'STATE_CONFLICT')
        listing = self.ok(self.get('app/artifacts'))
        self.assertEqual((listing['total'], listing['counts']['trash'], listing['counts']['all']), (3, 1, 3))
        self.assertEqual(self.ok(self.get('app/artifacts', {'trashed': '1'}))['total'], 1)
        self.assertIsNone(self.ok(self.delete(url))['artifact']['trashed_at'])
        self.assert_error(self.delete(url), 409, 'STATE_CONFLICT')
        self.as_(self.mark)
        self.assert_error(self.post(url), 403, 'PERMISSION_DENIED')

    def test_shares(self):
        self.as_(self.lillian)
        url = 'app/artifacts/%s/shares' % self.report.public_id
        first = self.ok(self.post(url, {'audience': 'workspace_members'}), 201)['share']
        self.assertEqual(set(first), {'id', 'audience', 'url', 'expires_at'})
        second = self.ok(self.post(url, {'audience': 'only_me', 'expires_in_days': 7}), 201)['share']
        self.assertIsNotNone(second['expires_at'])
        self.assertEqual(ArtifactShare.objects.filter(artifact=self.report, revoked_at__isnull=True).count(), 1)
        listed = self.ok(self.get('app/artifacts'))['items'][0]['share']
        self.assertEqual((listed['id'], listed['audience']), (second['id'], 'only_me'))
        self.assert_error(self.post(url, {'audience': 'public_link'}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post(url, {'audience': 'only_me', 'expires_in_days': 0}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post(url, {'audience': 'only_me', 'secret': 'abcdef'}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post('app/artifacts/%s/shares' % self.listing.public_id,
                                    {'audience': 'workspace_members'}), 422, 'VALIDATION_FAILED')
        AccountPolicy.objects.create(account=self.business, expert_share_max_scope='account',
                                     approval_required_actions=['external_publish'])
        self.assert_error(self.post(url, {'audience': 'encrypted_link', 'secret': 'abcdef'}), 409,
                          'POLICY_APPROVAL_REQUIRED')
        revoked = self.ok(self.delete('%s/%s' % (url, second['id'])))['share']
        self.assertIsNotNone(revoked['revoked_at'])
        self.assert_error(self.delete('%s/%s' % (url, second['id'])), 409, 'STATE_CONFLICT')
        self.ok(self.post(url, {'audience': 'workspace_members'}), 201)
        self.assertIsNotNone(self.ok(self.delete(url))['share']['revoked_at'])
        self.assert_error(self.delete(url), 409, 'STATE_CONFLICT')
        self.as_(self.mark)
        self.assert_error(self.post(url, {'audience': 'only_me'}), 403, 'PERMISSION_DENIED')

    def test_restore_version(self):
        self.as_(self.lillian)
        url = 'app/artifacts/%s/versions/1/restore' % self.report.public_id
        body = self.ok(self.post(url, {'client_request_id': 'r-1'}), 201)
        self.assertEqual(body['version'], {'number': 3, 'state': 'draft', 'restored_from': 1})
        self.report.refresh_from_db()
        self.assertEqual(self.report.current_version.number, 3)
        content = self.ok(self.get('app/artifacts/%s/versions/3/content' % self.report.public_id))
        self.assertEqual(content['body'], REPORT_V1)
        self.assert_error(self.post('app/artifacts/%s/versions/3/restore' % self.report.public_id,
                                    {'client_request_id': 'r-2'}), 409, 'STATE_CONFLICT')
        self.assert_error(self.post(url, {}), 422, 'INVALID_REQUEST')
        self.assert_error(self.post(url, {'client_request_id': ''}), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post('app/artifacts/%s/versions/7/restore' % self.report.public_id,
                                    {'client_request_id': 'r-3'}), 404, 'NOT_FOUND')
        self.as_(self.mark)
        self.assert_error(self.post(url, {'client_request_id': 'r-4'}), 403, 'PERMISSION_DENIED')

    def test_restore_replays_client_request_id(self):
        self.as_(self.lillian)
        url = 'app/artifacts/%s/versions/1/restore' % self.report.public_id
        first = self.ok(self.post(url, {'client_request_id': 'same'}), 201)
        count = ArtifactVersion.objects.filter(artifact=self.report).count()
        self.assertEqual(self.ok(self.post(url, {'client_request_id': 'same'}), 201), first)
        self.assertEqual(ArtifactVersion.objects.filter(artifact=self.report).count(), count)
        self.assertEqual(ArtifactVersion.objects.get(artifact=self.report, number=3).client_request_id, 'same')
        self.assert_error(self.post('app/artifacts/%s/versions/2/restore' % self.report.public_id,
                                    {'client_request_id': 'same'}), 409, 'IDEMPOTENCY_CONFLICT')

    def test_restore_rejected_in_trash_and_for_participants_without_workspace(self):
        self.as_(self.lillian)
        self.ok(self.post('app/artifacts/%s/trash' % self.report.public_id))
        self.assert_error(self.post('app/artifacts/%s/versions/1/restore' % self.report.public_id,
                                    {'client_request_id': 'r'}), 409, 'STATE_CONFLICT')


@override_settings(STORAGES=MEMORY_STORAGE)
class ReviewTests(ArtifactBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.task_alone.workspace = cls.ws
        cls.task_alone.save(update_fields=['workspace'])
        cls.image_candidate = store.create_version(cls.image, {'format': 'image', 'caption': '修改版'},
                                                   label='修改版', author_expert=cls.anjie)
        cls.image.current_version = cls.image.versions.get(number=1)
        cls.image.save(update_fields=['current_version'])
        cls.review = ContentReview.objects.create(account=cls.business, task=cls.task_alone,
                                                  dimensions=['platform_policy', 'market_law', 'brand'],
                                                  conclusion='需要修改 3 处后再上架。', state='reviewed',
                                                  target_platform='Amazon.de', target_market='德国',
                                                  verifier_expert=cls.shouheng)
        for expert in (cls.anjie, cls.wangye):
            cls.review.reviewers.add(expert)
        for version in (cls.listing.current_version, cls.image.current_version):
            cls.review.artifact_versions.add(version)
        issue = lambda **kw: ContentReviewIssue.objects.create(account=cls.business, review=cls.review, **kw)
        cls.brand = issue(dimension='brand', severity='medium', title='品牌规范：对外内容应使用正式称谓“Sie”',
                          location='五点描述第 2 条', original_text='Du wirst die frische Luft lieben',
                          suggested_text='Sie werden die frische Luft lieben', basis='规则', basis_rule_version=cls.rules,
                          basis_rule_index=1, highlight_spans={'original': [[0, 2]], 'suggested': [[0, 3]]})
        cls.health = issue(dimension='market_law', severity='high', title='健康功效宣称超出允许范围', location='标题',
                           original_text='Luftreiniger X1 – tötet 99,9 % aller Viren',
                           suggested_text='Luftreiniger X1 – filtert bis zu 99,9 % der Partikel ab 0,3 µm',
                           basis='德国《反不正当竞争法》（UWG）§5')
        cls.photo = issue(dimension='platform_policy', severity='medium', title='主图右上角含促销文字',
                          basis='Amazon 主图要求', suggested_artifact_version=cls.image_candidate)

    def url(self, suffix=''):
        return 'app/reviews/%s%s' % (self.review.public_id, suffix)

    def test_detail_shape(self):
        self.as_(self.mark)
        body = self.ok(self.get(self.url()))
        self.assertEqual(body['state'], 'reviewed')
        self.assertEqual([item['name'] for item in body['reviewers']], ['安界', '望野'])
        self.assertEqual(body['verifier_expert']['name'], '守衡')
        self.assertEqual(body['targets'], [
            {'artifact_id': self.listing.public_id, 'title': 'X1 Listing DE', 'version': 2, 'kind': 'document'},
            {'artifact_id': self.image.public_id, 'title': 'X1 主图.png', 'version': 1, 'kind': 'image'}])
        self.assertEqual((body['target_platform'], body['target_market']), ('Amazon.de', '德国'))
        self.assertEqual(body['counts'], {'issues': 3, 'by_dimension': {'platform_policy': 1, 'market_law': 1,
                                                                        'brand': 1, 'facts': None}})
        self.assertEqual([item['id'] for item in body['issues']],
                         [self.health.public_id, self.photo.public_id, self.brand.public_id])
        brand = body['issues'][2]
        self.assertEqual(brand['basis_rule'], {'version': 3, 'index': 1})
        self.assertEqual(brand['highlight_spans'], {'original': [[0, 2]], 'suggested': [[0, 3]]})
        self.assertEqual(body['issues'][1]['suggested_artifact_version'],
                         {'artifact_id': self.image.public_id, 'number': 2, 'title': 'X1 主图.png', 'label': '修改版'})
        self.assertIsNone(body['result_version'])
        self.assertEqual(set(brand), {'id', 'dimension', 'severity', 'title', 'location', 'original_text',
                                      'suggested_text', 'highlight_spans', 'basis', 'basis_rule', 'action',
                                      'suggested_artifact_version'})
        self.login(self.anna, self.business)
        self.assert_error(self.get(self.url()), 404, 'NOT_FOUND')

    def test_single_apply_accumulates_into_one_draft(self):
        self.as_(self.lillian)
        first = self.ok(self.patch(self.url('/issues/%s' % self.health.public_id), {'action': 'applied'}))
        self.assertEqual(first['issue'], {'id': self.health.public_id, 'action': 'applied'})
        self.assertEqual(first['result_version'],
                         {'artifact_id': self.listing.public_id, 'number': 3, 'title': 'X1 Listing DE', 'label': ''})
        self.assertEqual(first['artifact_version'], {'number': 3})
        second = self.ok(self.patch(self.url('/issues/%s' % self.brand.public_id), {'action': 'applied'}))
        self.assertEqual(second['result_version']['number'], 3)
        content = self.ok(self.get('app/artifacts/%s/versions/3/content' % self.listing.public_id))['body']
        self.assertIn('filtert bis zu 99,9 % der Partikel', content['blocks'][0]['text'])
        self.assertTrue(content['blocks'][1]['text'].startswith('Sie werden'))
        self.assert_error(self.patch(self.url('/issues/%s' % self.brand.public_id), {'action': 'ignored'}), 409,
                          'STATE_CONFLICT')

    def test_ignore_image_apply_and_validation(self):
        self.as_(self.chen)
        ignored = self.ok(self.patch(self.url('/issues/%s' % self.brand.public_id), {'action': 'ignored'}))
        self.assertIsNone(ignored['result_version'])
        photo = self.ok(self.patch(self.url('/issues/%s' % self.photo.public_id), {'action': 'applied'}))
        self.assertEqual(photo['result_version'],
                         {'artifact_id': self.image.public_id, 'number': 2, 'title': 'X1 主图.png', 'label': '修改版'})
        self.image.refresh_from_db()
        self.assertEqual(self.image.current_version_id, self.image_candidate.pk)
        self.assert_error(self.patch(self.url('/issues/%s' % self.health.public_id), {'action': 'pending'}), 422,
                          'VALIDATION_FAILED')
        self.assert_error(self.patch(self.url('/issues/%s' % self.listing.public_id), {'action': 'applied'}), 404,
                          'NOT_FOUND')
        self.as_(self.mark)
        self.assert_error(self.patch(self.url('/issues/%s' % self.health.public_id), {'action': 'applied'}), 403,
                          'PERMISSION_DENIED')

    def test_apply_all_state_machine(self):
        self.as_(self.lillian)
        self.ok(self.patch(self.url('/issues/%s' % self.brand.public_id), {'action': 'applied'}))
        with self.captureOnCommitCallbacks(execute=True):
            body = self.ok(self.post(self.url('/apply-all'), {'client_request_id': 'c-1'}), 202)
        self.assertEqual(body['review'], {'id': self.review.public_id, 'state': 'rewriting'})
        detail = self.ok(self.get(self.url()))
        self.assertEqual(detail['state'], 'rewritten')
        self.assertEqual({item['action'] for item in detail['issues']}, {'applied'})
        self.assertEqual(detail['result_version'],
                         {'artifact_id': self.listing.public_id, 'number': 3, 'title': 'X1 Listing DE', 'label': ''})
        content = self.ok(self.get('app/artifacts/%s/versions/3/content' % self.listing.public_id))['body']
        self.assertIn('filtert', content['blocks'][0]['text'])
        self.assertTrue(content['blocks'][1]['text'].startswith('Sie werden'))
        self.assertEqual(ArtifactVersion.objects.filter(artifact=self.listing).count(), 3)
        self.assert_error(self.post(self.url('/apply-all'), {}), 409, 'STATE_CONFLICT')
        self.assert_error(self.patch(self.url('/issues/%s' % self.health.public_id), {'action': 'ignored'}), 409,
                          'STATE_CONFLICT')

    def test_apply_all_replays_client_request_id(self):
        self.as_(self.lillian)
        with self.captureOnCommitCallbacks(execute=True):
            self.ok(self.post(self.url('/apply-all'), {'client_request_id': 'all-1'}), 202)
        self.review.refresh_from_db()
        self.assertEqual(self.review.client_request_id, 'all-1')
        replay = self.ok(self.post(self.url('/apply-all'), {'client_request_id': 'all-1'}), 202)
        self.assertEqual(replay['review'], {'id': self.review.public_id, 'state': 'rewritten'})
        other = ContentReview.objects.create(account=self.business, task=self.task_alone, dimensions=['brand'],
                                             conclusion='无问题', state='reviewed')
        self.assert_error(self.post('app/reviews/%s/apply-all' % other.public_id, {'client_request_id': 'all-1'}),
                          409, 'IDEMPOTENCY_CONFLICT')

    def test_apply_all_conflicts_and_permissions(self):
        self.as_(self.mark)
        self.assert_error(self.post(self.url('/apply-all'), {}), 403, 'PERMISSION_DENIED')
        self.as_(self.lillian)
        store.create_version(self.listing, LISTING)
        self.assert_error(self.post(self.url('/apply-all'), {}), 409, 'STATE_CONFLICT')
        ContentReviewIssue.objects.filter(review=self.review).update(action='ignored')
        ContentReview.objects.filter(pk=self.review.pk).update(state='reviewed')
        self.assert_error(self.post(self.url('/apply-all'), {}), 409, 'STATE_CONFLICT')
        ContentReview.objects.filter(pk=self.review.pk).update(state='rewriting')
        self.assert_error(self.patch(self.url('/issues/%s' % self.health.public_id), {'action': 'applied'}), 409,
                          'STATE_CONFLICT')


class CitationTests(ArtifactBase):
    def test_document_citations_hide_invisible_tasks(self):
        self.as_(self.lillian)
        body = self.ok(self.get('app/citations', {'document': self.doc.public_id, 'offset': '0'}))
        self.assertEqual(body['total'], 1)
        item = body['items'][0]
        self.assertEqual(set(item), {'id', 'task', 'artifact', 'locator', 'ref_index', 'created_at'})
        self.assertEqual(item['task']['title'], '德国站竞品格局调研')
        self.assertEqual(item['task']['lead_expert']['name'], '望野')
        self.assertIn('avatar_ref', item['task']['lead_expert'])
        self.assertEqual(item['artifact'], {'id': self.report.public_id, 'title': '德国站竞品格局报告'})
        self.assertEqual(item['locator'], '§3')

    def test_errors(self):
        self.as_(self.lillian)
        self.assert_error(self.get('app/citations'), 422, 'INVALID_REQUEST')
        self.assert_error(self.get('app/citations', {'document': self.doc.public_id, 'x': '1'}), 422,
                          'INVALID_REQUEST')
        self.assert_error(self.get('app/citations', {'document': self.report.public_id}), 404, 'NOT_FOUND')
        self.login(self.lillian)
        self.assert_error(self.get('app/citations', {'document': self.doc.public_id}), 404, 'NOT_FOUND')
