"""Contract tests for app/automations and app/automation-runs, plus the due-run entry point."""
import uuid
from datetime import datetime, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.utils import timezone

from backend.commerce.models import Reservation, TaskCostEvent
from backend.workbench.models import (AppNotification, AuthorizationRequest, AutomationPermission, AutomationRun,
                                      Task, TaskStep)
from backend.workbench.services import automations as service
from backend.workbench.tests.base import AppTestCase
from backend.workbench.tests.test_inbox_support import (give_credits, make_automation, make_expert, make_extension,
                                                        make_workspace)

SHANGHAI = ZoneInfo('Asia/Shanghai')


def rid():
    return uuid.uuid4().hex


class AutomationApiTests(AppTestCase):
    def setUp(self):
        self.hengjia = make_expert('衡价')
        self.workspace = make_workspace(self.business, self.lillian,
                                        [(self.lillian, 'owner'), (self.mark, 'viewer')])
        self.login(self.lillian, self.business)

    def payload(self, **extra):
        data = {'name': '每周德国站竞品价格监控', 'content_kind': 'prompt', 'prompt': '对比前 20 名竞品价格',
                'lead_expert': self.hengjia.public_id, 'workspace': self.workspace.public_id,
                'schedule_kind': 'weekly', 'schedule_weekday': 1, 'schedule_time': '09:00',
                'timezone': 'Asia/Shanghai'}
        data.update(extra)
        return data

    def test_create_returns_draft_with_permissions(self):
        body = self.ok(self.post('app/automations', self.payload()), 201)['automation']
        self.assertEqual(body['state'], 'draft')
        self.assertEqual(body['schedule'], {'kind': 'weekly', 'time': '09:00', 'weekday': 1, 'monthday': None,
                                            'timezone': 'Asia/Shanghai'})
        self.assertEqual(body['lead_expert']['name'], '衡价')
        self.assertEqual(body['permissions'][0]['kind'], 'workspace_write')
        self.assertEqual(body['permissions'][0]['state'], 'granted')
        self.assertEqual(body['permissions'][0]['label'], '工作区“德国市场进入” · 写入成果')
        self.assertEqual(datetime.fromisoformat(body['next_run_at']).astimezone(SHANGHAI).isoweekday(), 1)

    def test_create_validation(self):
        self.assert_error(self.post('app/automations', self.payload(schedule_weekday=None)), 422,
                          'VALIDATION_FAILED')
        self.assert_error(self.post('app/automations', self.payload(workspace=None)), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post('app/automations', self.payload(schedule_time='9am')), 422, 'VALIDATION_FAILED')
        self.assert_error(self.post('app/automations', self.payload(timezone='Mars/Base')), 422,
                          'VALIDATION_FAILED')
        data = self.payload()
        del data['workspace']
        self.assert_error(self.post('app/automations', data), 422, 'VALIDATION_FAILED')
        del data['name']
        self.assert_error(self.post('app/automations', data), 422, 'INVALID_REQUEST')

    def test_create_in_viewer_workspace_denied(self):
        self.login(self.mark, self.business)
        self.assert_error(self.post('app/automations', self.payload()), 403, 'PERMISSION_DENIED')

    def test_list_only_own_and_sort(self):
        self.ok(self.post('app/automations', self.payload()), 201)
        self.ok(self.post('app/automations', self.payload(name='Amazon 差评日报', schedule_kind='daily',
                                                          schedule_weekday=None, schedule_time='18:00')), 201)
        names = [a['name'] for a in self.ok(self.get('app/automations'))['items']]
        self.assertEqual(names, ['每周德国站竞品价格监控', 'Amazon 差评日报'])
        by_next = self.ok(self.get('app/automations', {'sort': 'next_run'}))['items']
        self.assertEqual(len(by_next), 2)
        self.assert_error(self.get('app/automations', {'sort': 'name'}), 422, 'INVALID_REQUEST')
        self.login(self.mark, self.business)
        self.assertEqual(self.ok(self.get('app/automations'))['total'], 0)

    def test_patch_version_and_workspace_change_pauses(self):
        created = self.ok(self.post('app/automations', self.payload()), 201)['automation']
        path = 'app/automations/%s' % created['id']
        updated = self.ok(self.patch(path, {'version': 1, 'name': '竞品价格监控'}))['automation']
        self.assertEqual((updated['name'], updated['version']), ('竞品价格监控', 2))
        self.assert_error(self.patch(path, {'version': 1, 'name': 'x'}), 409, 'VERSION_CONFLICT')
        self.ok(self.post(path + '/enable'))
        other = make_workspace(self.business, self.lillian, [(self.lillian, 'owner')], name='欧洲合规')
        moved = self.ok(self.patch(path, {'version': 2, 'workspace': other.public_id}))['automation']
        self.assertEqual(moved['state'], 'paused')
        self.assertIsNone(moved['next_run_at'])

    def test_enable_pause_cycle(self):
        created = self.ok(self.post('app/automations', self.payload()), 201)['automation']
        path = 'app/automations/%s/enable' % created['id']
        self.assertEqual(self.ok(self.post(path))['automation']['state'], 'enabled')
        self.assert_error(self.post(path), 409, 'STATE_CONFLICT')
        paused = self.ok(self.delete(path))['automation']
        self.assertEqual(paused['state'], 'paused')
        self.assertIsNone(paused['next_run_at'])
        self.assert_error(self.delete(path), 409, 'STATE_CONFLICT')

    def test_enable_needs_connection_or_grant(self):
        created = self.ok(self.post('app/automations', self.payload()), 201)['automation']
        AutomationPermission.objects.create(account=self.business, automation_id=service.Automation.objects.get(
            public_id=created['id']).pk, kind='connector_read', extension=make_extension('Jungle Scout'),
            state='needs_connection')
        self.assert_error(self.post('app/automations/%s/enable' % created['id']), 409, 'CONNECTION_REQUIRED')
        detail = self.ok(self.get('app/automations/%s' % created['id']))['automation']
        self.assertEqual(detail['state'], 'draft')
        self.assertEqual(detail['permissions'][1]['label'], 'Jungle Scout · 读取价格数据')

    def test_run_now_and_runs(self):
        give_credits(self.business)
        created = self.ok(self.post('app/automations', self.payload()), 201)['automation']
        base = 'app/automations/%s' % created['id']
        self.assert_error(self.post(base + '/run-now', {'client_request_id': rid()}), 409, 'STATE_CONFLICT')
        self.ok(self.post(base + '/enable'))
        run = self.ok(self.post(base + '/run-now', {'client_request_id': rid()}), 202)['run']
        self.assertEqual(run['state'], 'running')
        again = self.ok(self.post(base + '/run-now', {'client_request_id': rid()}), 202)['run']
        self.assertEqual(again['id'], run['id'])
        task = Task.objects.get(source_automation_run__public_id=run['id'])
        self.assertEqual(task.lead_expert, self.hengjia)
        runs = self.ok(self.get(base + '/runs'))['items']
        self.assertEqual([r['id'] for r in runs], [run['id']])
        self.assertEqual(runs[0]['trigger'], 'manual')
        all_runs = self.ok(self.get('app/automation-runs', {'automation': created['id']}))
        self.assertEqual(all_runs['total'], 1)
        done = TaskStep.objects.create(account=self.business, task=task, order=1, stage='research',
                                       title='采集竞品价格', expert=self.hengjia, status='done', estimated_credits=30)
        TaskStep.objects.create(account=self.business, task=task, order=2, stage='analyze', title='对比价格',
                                expert=self.hengjia, status='queued', estimated_credits=12)
        reservation = Reservation.objects.create(account=self.business, task_key=task.public_id, quantity='8',
                                                 settled_quantity='8', state='settled')
        TaskCostEvent.objects.create(account=self.business, reservation=reservation, source='executor',
                                     event_key='run-e1', payload={}, request_hash='h', step_key=done.public_id)
        detail = self.ok(self.get('app/automation-runs/%s' % run['id']))['run']
        self.assertEqual(detail['steps'][0]['title'], '采集竞品价格')
        # Q-F-14 A: only settled ledger Credits count, never the step estimate.
        self.assertEqual([step['credits_used'] for step in detail['steps']], [8, None])
        self.assertEqual(detail['credits_used'], 8)
        self.assertEqual(detail['task_id'], task.public_id)
        self.assertEqual(detail['automation']['schedule']['kind'], 'weekly')

    def test_run_detail_step_authorization_carries_title(self):
        # 60 屏：挂起步骤的「等你确认：<标题>」来自授权标题，不是范围描述。
        give_credits(self.business)
        created = self.ok(self.post('app/automations', self.payload()), 201)['automation']
        base = 'app/automations/%s' % created['id']
        self.ok(self.post(base + '/enable'))
        run = self.ok(self.post(base + '/run-now', {'client_request_id': rid()}), 202)['run']
        task = Task.objects.get(source_automation_run__public_id=run['id'])
        step = TaskStep.objects.create(account=self.business, task=task, order=1, stage='research',
                                       title='价格带与定价建议', expert=self.hengjia, status='awaiting_user')
        AuthorizationRequest.objects.create(
            account=self.business, task=task,
            automation_run=AutomationRun.objects.get(public_id=run['id']), step=step,
            requested_by_expert=self.hengjia, extension=make_extension('Keepa'), action_kind='read_query',
            title='使用 Keepa 读取历史价格（只读）', scope_text='Amazon.fr · 历史价格', validity='this_task')
        detail = self.ok(self.get('app/automation-runs/%s' % run['id']))['run']
        self.assertEqual(detail['steps'][0]['authorization']['title'], '使用 Keepa 读取历史价格（只读）')
        self.assertEqual(detail['pending_authorization_count'], 1)

    def test_run_now_replays_client_request_id(self):
        give_credits(self.business)
        created = self.ok(self.post('app/automations', self.payload()), 201)['automation']
        base = 'app/automations/%s' % created['id']
        self.ok(self.post(base + '/enable'))
        key = rid()
        run = self.ok(self.post(base + '/run-now', {'client_request_id': key}), 202)['run']
        self.assertEqual(AutomationRun.objects.get(public_id=run['id']).client_request_id, key)
        AutomationRun.objects.filter(public_id=run['id']).update(state='succeeded')
        replay = self.ok(self.post(base + '/run-now', {'client_request_id': key}), 202)['run']
        self.assertEqual(replay, {'id': run['id'], 'state': 'succeeded'})
        fresh = self.ok(self.post(base + '/run-now', {'client_request_id': rid()}), 202)['run']
        self.assertNotEqual(fresh['id'], run['id'])
        self.assertEqual(AutomationRun.objects.count(), 2)
        other = self.ok(self.post('app/automations', self.payload(name='Amazon 差评日报')), 201)['automation']
        self.ok(self.post('app/automations/%s/enable' % other['id']))
        self.assert_error(self.post('app/automations/%s/run-now' % other['id'], {'client_request_id': key}), 409,
                          'IDEMPOTENCY_CONFLICT')

    def test_run_now_requires_credits(self):
        created = self.ok(self.post('app/automations', self.payload()), 201)['automation']
        self.ok(self.post('app/automations/%s/enable' % created['id']))
        self.assert_error(self.post('app/automations/%s/run-now' % created['id'], {'client_request_id': rid()}),
                          409, 'INSUFFICIENT_CREDITS')
        self.assertFalse(AutomationRun.objects.exists())

    def test_runs_hidden_from_other_members(self):
        created = self.ok(self.post('app/automations', self.payload()), 201)['automation']
        self.login(self.mark, self.business)
        self.assert_error(self.get('app/automations/%s' % created['id']), 404, 'NOT_FOUND')
        self.assert_error(self.get('app/automation-runs', {'automation': created['id']}), 404, 'NOT_FOUND')
        self.login(self.anna, self.business)
        self.assert_error(self.get('app/automations'), 404, 'NOT_FOUND')


class ScheduleTests(AppTestCase):
    def setUp(self):
        self.expert = make_expert('明镜')
        self.workspace = make_workspace(self.business, self.lillian, [(self.lillian, 'owner')])
        self.owner = self.member(self.lillian)

    def automation(self, **extra):
        return make_automation(self.business, self.owner, self.workspace, self.expert, **extra)

    def test_compute_next_run_weekly_monthly(self):
        weekly = self.automation()
        after = datetime(2025, 11, 4, 12, 0, tzinfo=SHANGHAI)  # Tuesday
        self.assertEqual(service.compute_next_run(weekly, after), datetime(2025, 11, 10, 9, 0, tzinfo=SHANGHAI))
        monthly = self.automation(schedule_kind='monthly', schedule_weekday=None, schedule_monthday=31)
        self.assertEqual(service.compute_next_run(monthly, datetime(2025, 11, 1, tzinfo=SHANGHAI)),
                         datetime(2025, 11, 30, 9, 0, tzinfo=SHANGHAI))

    def test_run_due_starts_and_advances(self):
        give_credits(self.business)
        a = self.automation(state='enabled')
        now = timezone.now()
        a.next_run_at = now - timedelta(minutes=5)
        a.save()
        results = service.run_due(now)
        self.assertEqual(results[0][2], 'started')
        a.refresh_from_db()
        self.assertGreater(a.next_run_at, now)
        self.assertEqual(AutomationRun.objects.get().trigger, 'schedule')
        self.assertEqual(service.run_due(now), [])

    def test_run_due_records_missed_with_notification(self):
        a = self.automation(state='enabled', name='月度经营复盘', schedule_kind='monthly', schedule_weekday=None,
                            schedule_monthday=1, schedule_time=datetime(2025, 1, 1, 10).time())
        now = timezone.now()
        a.next_run_at = now - timedelta(hours=3)
        a.save()
        self.assertEqual(service.run_due(now)[0][2], 'missed')
        run = AutomationRun.objects.get()
        self.assertEqual((run.state, run.missed_reason), ('missed', 'app_not_running'))
        note = AppNotification.objects.get(type='automation_missed')
        self.assertEqual(note.action, 'run_now')
        self.assertEqual(note.recipient, self.lillian)
        self.login(self.lillian, self.business)
        items = self.ok(self.get('app/automations'))['items']
        self.assertEqual(items[0]['last_run']['state'], 'missed')

    def test_run_due_without_credits_records_failure(self):
        a = self.automation(state='enabled')
        a.next_run_at = timezone.now() - timedelta(minutes=1)
        a.save()
        self.assertEqual(service.run_due()[0][2], 'failed:INSUFFICIENT_CREDITS')
        self.assertEqual(AutomationRun.objects.get().failure_code, 'insufficient_credits')

    def test_paused_automation_not_run(self):
        a = self.automation(state='paused')
        a.next_run_at = timezone.now() - timedelta(minutes=1)
        a.save()
        self.assertEqual(service.run_due(), [])

    def test_management_command(self):
        give_credits(self.business)
        a = self.automation(state='enabled')
        a.next_run_at = timezone.now() - timedelta(minutes=1)
        a.save()
        out = StringIO()
        call_command('run_automations', stdout=out)
        self.assertIn('started', out.getvalue())
