"""Seed coverage for screens 16-19, 22, 23, 54-56: seed_app --only core --only artifacts, twice."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from backend.models import BillingAccount
from backend.workbench.models import Artifact, ContentReview, Playbook, Workspace
from backend.workbench.tests.base import AppClientMixin
from backend.workbench.tests.test_artifacts import MEMORY_STORAGE


@override_settings(STORAGES=MEMORY_STORAGE)
class ArtifactSeedTests(AppClientMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        for _ in range(2):
            call_command('seed_app', '--only', 'core', '--only', 'artifacts', stdout=StringIO())
        cls.business = BillingAccount.objects.get(kind='business', name='Sage Demo 企业')
        from django.contrib.auth import get_user_model
        cls.lillian = get_user_model().objects.get(email='lillian@sagedemo.com')

    def setUp(self):
        self.login(self.lillian, self.business)

    def test_artifact_library_counts_match_screen_54(self):
        body = self.ok(self.get('app/artifacts'))
        counts = body['counts']
        self.assertEqual(counts['all'], 24)
        self.assertEqual(counts['kinds'], {'document': 9, 'spreadsheet': 6, 'presentation': 2, 'webpage': 3,
                                           'image': 4})
        self.assertEqual({row['name']: row['count'] for row in counts['workspaces']},
                         {'德国市场进入': 11, '品牌官网改版': 7, '黑五大促': 4, None: 2})
        self.assertEqual(counts['trash'], 3)
        self.assertEqual(body['items'][0]['title'], '德国站竞品格局报告')
        self.assertEqual(Artifact.objects.filter(account=self.business).count(), 27)

    def test_report_preview_and_diff_match_screens_55_56(self):
        report = Artifact.objects.get(account=self.business, title='德国站竞品格局报告')
        detail = self.ok(self.get('app/artifacts/%s' % report.public_id))
        self.assertEqual(detail['current_version']['citation_count'], 3)
        self.assertEqual(detail['delivery']['decision'], 'pending')
        content = self.ok(self.get('app/artifacts/%s/versions/2/content' % report.public_id))['body']
        table = next(block for block in content['blocks'] if block['type'] == 'table')
        self.assertEqual(table['rows'][0], ['德国本土品牌', '6 席', '€49–69'])
        diff = self.ok(self.get('app/artifacts/%s/versions/2/diff' % report.public_id))
        self.assertIn({'deleted': '首批备货 1,500 台。', 'inserted': '首批备货 800 台，以 6 周销量验证后再补货。'},
                      diff['hunks'])
        self.assertEqual(sum(1 for hunk in diff['hunks'] if 'context' not in hunk), 2)
        self.assertEqual(diff['restore_preview'], {'next_number': 3})
        summary = self.ok(self.get('app/artifacts', {'task': report.source_task.public_id}))['summary']
        self.assertEqual(summary, {'created': 2, 'modified': 1})

    def test_review_rules_and_playbook(self):
        review = ContentReview.objects.get(task__title='X1 德语 Listing 上架前审核')
        body = self.ok(self.get('app/reviews/%s' % review.public_id))
        self.assertEqual(body['counts'], {'issues': 3, 'by_dimension': {'platform_policy': 1, 'market_law': 1,
                                                                        'brand': 1, 'facts': 0}})
        self.assertEqual([row['name'] for row in body['reviewers']], ['安界', '立言', '觅位', '绘影'])
        self.assertEqual(body['issues'][2]['basis_rule'], {'version': 3, 'index': 1})
        workspace = Workspace.objects.get(account=self.business, name='德国市场进入')
        rules = self.ok(self.get('app/workspaces/%s/rules' % workspace.public_id))
        self.assertEqual((rules['current']['number'], len(rules['current']['rules'])), (3, 6))
        self.assertEqual(rules['versions']['items'][0]['change_note'], '价格下限 €35 → €39')
        playbooks = self.ok(self.get('app/playbooks'))['items']
        self.assertEqual([(row['name'], row['step_count'], row['expert_count']) for row in playbooks],
                         [('新市场进入评估', 6, 5)])
        self.assertEqual(Playbook.objects.filter(name='新市场进入评估').count(), 1)
