"""Contract tests: POST app/tasks/{id}/quota-reminders (14 屏「提醒管理员」, D39).

A task paused because the monthly quota ran out lets its owner remind the enterprise administrators (owner and
billing_admin memberships, excluding the caller) with an in-app notification; with no other administrator it falls
back to the account owner (Q-A-8 A), which may be the caller. One reminder per task per day:
repeating the call the same day is answered 200 and creates nothing.
"""
from backend.workbench.models import AppNotification
from backend.workbench.tests.test_tasks import TaskWorld


class QuotaReminderTests(TaskWorld):
    def url(self, task):
        return 'app/tasks/%s/quota-reminders' % task.public_id

    def test_member_reminds_owner_and_billing_admin(self):
        task = self.make_task('paused', owner=self.mark, pause_reason='quota_exhausted')
        self.login(self.mark, self.business)
        body = self.ok(self.post(self.url(task), {}), 201)
        self.assertEqual(body, {'reminded': 2, 'created': True})
        notes = AppNotification.objects.filter(type='quota_reminder_to_admin').order_by('recipient_id')
        self.assertEqual({n.recipient_id for n in notes}, {self.lillian.pk, self.chen.pk})
        note = notes.get(recipient=self.lillian)
        self.assertEqual(note.category, 'task_progress')
        self.assertEqual(note.text, 'Mark Weber 的任务“德国站竞品格局调研”因本月额度用完已暂停，请调高额度')
        self.assertEqual((note.target_type, note.target_id, note.action), ('org_quota', task.public_id, 'open'))
        self.assertEqual(note.context_label, '德国站竞品格局调研')

    def test_once_per_day(self):
        task = self.make_task('paused', owner=self.mark, pause_reason='quota_exhausted')
        self.login(self.mark, self.business)
        self.ok(self.post(self.url(task), {}), 201)
        self.assertEqual(self.ok(self.post(self.url(task), {}), 200), {'reminded': 2, 'created': False})
        self.assertEqual(AppNotification.objects.filter(type='quota_reminder_to_admin').count(), 2)

    def test_owner_reminds_other_admins_only(self):
        task = self.make_task('paused', pause_reason='quota_exhausted')
        self.login(self.lillian, self.business)
        self.assertEqual(self.ok(self.post(self.url(task), {}), 201)['reminded'], 1)
        self.assertEqual(list(AppNotification.objects.values_list('recipient_id', flat=True)), [self.chen.pk])

    def test_requires_quota_pause(self):
        self.login(self.lillian, self.business)
        for status, reason in (('running', None), ('paused', 'user')):
            task = self.make_task(status, pause_reason=reason)
            self.assert_error(self.post(self.url(task), {}), 409, 'STATE_CONFLICT')

    def test_only_task_owner(self):
        task = self.make_task('paused', pause_reason='quota_exhausted')
        task.participants.create(account=self.business, kind='human', user=self.chen, role='collaborator',
                                 state='active')
        self.login(self.chen, self.business)
        self.assert_error(self.post(self.url(task), {}), 403, 'PERMISSION_DENIED')

    def test_no_other_admin_falls_back_to_owner(self):
        task = self.make_task('paused', pause_reason='quota_exhausted')
        self.login(self.lillian, self.business)
        chen = self.member(self.chen)
        chen.role = 'member'
        chen.save(update_fields=['role'])
        self.assertEqual(self.ok(self.post(self.url(task), {}), 201)['reminded'], 1)
        self.assertEqual(list(AppNotification.objects.values_list('recipient_id', flat=True)), [self.lillian.pk])

    def test_body_must_be_empty_object(self):
        task = self.make_task('paused', owner=self.mark, pause_reason='quota_exhausted')
        self.login(self.mark, self.business)
        self.assert_error(self.post(self.url(task), {'x': 1}), 422, 'INVALID_REQUEST')
