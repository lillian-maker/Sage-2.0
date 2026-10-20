"""Real database isolation and delivery gates, with no external messages."""
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .models import BillingAccount, Membership, NotificationRead, NotificationDelivery
from .commerce.models import BillingNotification, Subscription, SubscriptionPeriod, Quote, Order, ProductVersion
from .notifications import (assert_renewal_notice, mark_read, notification_page,
    prepare_renewal_notice, record_renewal_notice_blocked)


class NotificationVisibilityTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.owner = users.objects.create_user(username='notice-owner')
        self.member = users.objects.create_user(username='notice-member')
        self.other = users.objects.create_user(username='notice-outsider')
        self.company = BillingAccount.objects.create(kind='business', name='Fixture company')
        self.member_row = Membership.objects.create(user=self.member, account=self.company, role='member')
        Membership.objects.create(user=self.owner, account=self.company, role='owner')
        self.financial = self.notice('payment_received', 'payment')
        self.personal = self.notice('account_security', 'security', recipient_user=self.member)
        self.client = Client()
        self.client.force_login(self.member)

    def notice(self, kind, key, **fields):
        return BillingNotification.objects.create(account=self.company, type=kind,
            source_event=key, occurred_at=timezone.now(), **fields)

    def test_member_sees_only_targeted_notice_not_company_money(self):
        page = notification_page(self.member)
        self.assertEqual([row['id'] for row in page['items']], [str(self.personal.pk)])
        self.assertEqual(page['unread_count'], 1)
        self.assertEqual(notification_page(self.other)['unread_count'], 0)
        self.assertEqual([row['id'] for row in notification_page(self.owner)['items']], [str(self.financial.pk)])

    def test_seat_purchase_and_reduction_notices_remain_admin_only(self):
        notices = [self.notice(kind, 'capacity-'+kind)
            for kind in ('next_seats_purchased', 'seats_reduced')]
        page = notification_page(self.owner)
        self.assertEqual({row['type'] for row in page['items']},
            {'payment_received', 'next_seats_purchased', 'seats_reduced'})
        self.assertEqual([row['id'] for row in notification_page(self.member)['items']],
            [str(self.personal.pk)])
        with self.assertRaisesRegex(ValueError, 'FORBIDDEN'):
            mark_read(self.member, {'notification_ids': [str(notices[0].pk)]})
        self.assertFalse(NotificationRead.objects.exists())

    def test_each_person_has_separate_read_state_and_first_timestamp_is_kept(self):
        self.member_row.role = 'billing_admin'
        self.member_row.save(update_fields=['role'])
        mark_read(self.member, {'notification_ids': [str(self.financial.pk)]})
        original = NotificationRead.objects.get(user=self.member, notification=self.financial).read_at
        mark_read(self.member, {'notification_ids': [str(self.financial.pk)]})
        self.assertEqual(NotificationRead.objects.get(user=self.member, notification=self.financial).read_at, original)
        self.assertEqual(notification_page(self.owner)['unread_count'], 1)
        self.assertEqual(notification_page(self.member)['unread_count'], 1)

    def test_removal_immediately_hides_records_and_unread_count(self):
        self.member_row.is_active = False
        self.member_row.save(update_fields=['is_active'])
        self.assertEqual(notification_page(self.member)['items'], [])
        self.assertEqual(notification_page(self.member)['unread_count'], 0)
        with self.assertRaisesRegex(ValueError, 'FORBIDDEN'):
            mark_read(self.member, {'notification_ids': [str(self.personal.pk)]})

    def test_demotion_removes_financial_visibility(self):
        Membership.objects.filter(user=self.owner).update(role='member')
        self.assertEqual(notification_page(self.owner)['items'], [])

    def test_mark_all_does_not_touch_another_users_or_hidden_notices(self):
        mark_read(self.member, {'all': True})
        self.assertEqual(NotificationRead.objects.filter(user=self.member).count(), 1)
        self.assertFalse(NotificationRead.objects.filter(notification=self.financial).exists())
        new = self.notice('account_security', 'later', recipient_user=self.member)
        self.assertEqual(notification_page(self.member)['unread_count'], 1)
        self.assertIsNone(notification_page(self.member)['items'][0]['read_at'])

    def test_cross_account_or_mixed_request_cannot_partially_mark(self):
        with self.assertRaisesRegex(ValueError, 'FORBIDDEN'):
            mark_read(self.member, {'notification_ids': [str(self.personal.pk), str(self.financial.pk)]})
        self.assertEqual(NotificationRead.objects.count(), 0)

    def test_invalid_read_requests_are_rejected(self):
        for data in ({}, {'all': False}, {'all': True, 'notification_ids': []},
                {'notification_ids': []}, {'notification_ids': ['bad']},
                {'notification_ids': [str(self.personal.pk)]*2}, {'notification_ids': [None]}):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, 'INVALID_REQUEST'):
                mark_read(self.member, data)

    def test_http_auth_methods_pagination_and_no_store(self):
        url = '/api/account/notifications'
        self.assertEqual(Client().get(url).status_code, 401)
        self.assertEqual(self.client.post(url, '{}', content_type='application/json').status_code, 405)
        for query in ('?offset=-1', '?offset=1&offset=2', '?offset=１', '?unexpected=1'):
            self.assertEqual(self.client.get(url+query).status_code, 422)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(response.json()['unread_count'], 1)

    def test_csrf_is_required_for_mark_read(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.member)
        response = strict.post('/api/account/notifications/read',
            json.dumps({'all': True}), content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(NotificationRead.objects.count(), 0)

    def test_read_api_and_summary_share_real_state(self):
        response = self.client.post('/api/account/notifications/read',
            json.dumps({'notification_ids': [str(self.personal.pk)]}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/account/notifications/summary').json()['unread_count'], 0)
        self.assertIsNotNone(self.client.get('/api/account/notifications').json()['items'][0]['read_at'])

    def test_pagination_is_stable_and_retains_total_unread(self):
        for index in range(52):
            self.notice('account_security', f'page-{index}', recipient_user=self.member)
        first = notification_page(self.member)
        second = notification_page(self.member, first['next_offset'])
        self.assertEqual((len(first['items']), len(second['items']), first['total'], second['unread_count']), (50, 3, 53, 53))
        self.assertFalse({row['id'] for row in first['items']} & {row['id'] for row in second['items']})

    def test_internal_security_notice_needs_no_fabricated_billing_account(self):
        from .notifications import record_security_notice
        staff = get_user_model().objects.create_user(username='no-billing-staff', is_staff=True)
        notice = record_security_notice(staff, 'fixture-passkey')
        self.assertIsNone(notice.account_id)
        page = notification_page(staff)
        self.assertIsNone(page['items'][0]['account_id'])
        self.assertEqual(page['items'][0]['account_name'], '')
        self.assertEqual(notification_page(self.member)['total'], 1)


class RenewalNoticeTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user = get_user_model().objects.create_user(username='renewal-notice-owner')
        self.account = BillingAccount.objects.create(kind='personal', name='Fixture', personal_owner=self.user)
        Membership.objects.create(account=self.account, user=self.user, role='owner')
        product = ProductVersion.objects.create(code='fixture', version=1, kind='subscription',
            audience='personal', billing_cycle='monthly', unit_amount_minor=19900,
            credits_quantity='10', entitlements={}, entitlement_version='f', credit_rule_version='f',
            terms_version='f', state='published', effective_at=self.now)
        snapshot = {'amount_minor': 19900, 'currency': 'CNY', 'channel': 'fixture', 'merchant_entity': 'fixture-merchant'}
        quote = Quote.objects.create(account=self.account, product=product, snapshot=snapshot, amount_minor=19900,
            currency='CNY', expires_at=self.now+timedelta(minutes=10))
        order = Order.objects.create(account=self.account, quote=quote, created_by=self.user,
            idempotency_key='notice-fixture', request_hash='a'*64, snapshot=snapshot, amount_minor=19900,
            currency='CNY', status='fulfilled', paid_at=self.now)
        self.subscription = Subscription.objects.create(account=self.account, initial_order=order,
            original_anchor=self.now, billing_timezone='Asia/Shanghai', calendar_rule_version='fixture',
            billing_cycle='monthly', mandate={'channel': 'fixture', 'merchant_reference': 'fixture-merchant',
                'agreement_reference': 'fixture-agreement', 'evidence_hash': 'a'*64,
                'executor': 'sage', 'automatic_retry': False})
        self.charge_at = self.now+timedelta(days=28)

    def prepare(self):
        return prepare_renewal_notice(self.account, self.subscription.pk, 1, charge_at=self.charge_at)

    def test_one_day_notice_has_real_amount_and_replay_is_same(self):
        notice = self.prepare()
        self.assertEqual(self.prepare().pk, notice.pk)
        self.assertEqual(notice.occurred_at, self.charge_at-timedelta(days=1))
        self.assertEqual(notice.amount_minor, 19900)
        with self.assertRaisesRegex(ValueError, 'RENEWAL_NOTICE_CONFLICT'):
            prepare_renewal_notice(self.account, self.subscription.pk, 1, charge_at=self.charge_at-timedelta(hours=1))

    def test_missing_failed_unknown_and_unconfirmed_never_enable_new_debit(self):
        for state in (None, 'pending', 'sending', 'failed', 'unknown', 'sent'):
            NotificationDelivery.objects.all().delete()
            if state:
                NotificationDelivery.objects.create(notification=self.prepare(), user=self.user,
                    channel='email', state=state)
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, 'RENEWAL_NOTICE_REQUIRED'):
                assert_renewal_notice(self.subscription, 1, charge_at=self.charge_at)

    def test_verified_send_does_not_require_read_and_late_send_is_rejected(self):
        delivery = NotificationDelivery.objects.create(notification=self.prepare(), user=self.user,
            channel='email', state='sent', provider_reference='fixture-send-id',
            confirmed_at=self.charge_at-timedelta(days=1, seconds=1))
        self.assertTrue(assert_renewal_notice(self.subscription, 1, charge_at=self.charge_at))
        self.assertEqual(NotificationRead.objects.count(), 0)
        delivery.confirmed_at = self.charge_at-timedelta(hours=23)
        delivery.save(update_fields=['confirmed_at'])
        with self.assertRaisesRegex(ValueError, 'RENEWAL_NOTICE_REQUIRED'):
            assert_renewal_notice(self.subscription, 1, charge_at=self.charge_at)

    def test_departed_admin_send_does_not_satisfy_current_company_notice(self):
        NotificationDelivery.objects.create(notification=self.prepare(), user=self.user, channel='email',
            state='sent', provider_reference='fixture-send-id', confirmed_at=self.now)
        Membership.objects.filter(user=self.user).update(is_active=False)
        with self.assertRaisesRegex(ValueError, 'RENEWAL_NOTICE_REQUIRED'):
            assert_renewal_notice(self.subscription, 1, charge_at=self.charge_at)

    def test_disabled_user_with_active_membership_cannot_satisfy_notice(self):
        NotificationDelivery.objects.create(notification=self.prepare(), user=self.user, channel='email',
            state='sent', provider_reference='fixture-send-id', confirmed_at=self.now)
        type(self.user).objects.filter(pk=self.user.pk).update(is_active=False)
        with self.assertRaisesRegex(ValueError, 'RENEWAL_NOTICE_REQUIRED'):
            assert_renewal_notice(self.subscription, 1, charge_at=self.charge_at)

    def test_alert_intent_is_recorded_once_after_debit_rollback(self):
        first = record_renewal_notice_blocked(self.subscription, 1)
        self.assertEqual(first.pk, record_renewal_notice_blocked(self.subscription, 1).pk)
        self.assertEqual(first.code, 'renewal_notice_blocked')


@override_settings(NOTIFICATION_DELIVERY_ENABLED=True, NOTIFICATION_EMAIL_TEMPLATE_ID='123',
    NOTIFICATION_EMAIL_TEMPLATE_APPROVED=True, NOTIFICATION_SITE_ORIGIN='https://sage.example.invalid')
class NotificationDispatchTests(TransactionTestCase):
    def setUp(self):
        from .models import LoginIdentity
        from .notifications import prepare_notice_delivery
        self.user = get_user_model().objects.create_user(username='dispatch-fixture')
        self.account = BillingAccount.objects.create(kind='personal', name='Fixture', personal_owner=self.user)
        Membership.objects.create(account=self.account, user=self.user, role='owner')
        LoginIdentity.objects.create(user=self.user, method='email', identifier='fixture@example.invalid')
        self.notice = BillingNotification.objects.create(account=self.account, type='payment_received',
            source_event='fixture-payment', occurred_at=timezone.now())
        self.delivery = prepare_notice_delivery(self.notice, self.user)

    def test_send_ack_is_persisted_and_replay_never_sends_again(self):
        from .notifications import send_notice_delivery
        with patch('backend.notifications._transport_notice', return_value='fixture-message') as send:
            self.assertEqual(send_notice_delivery(self.delivery.pk), 'sent')
            self.assertEqual(send_notice_delivery(self.delivery.pk), 'sent')
            self.assertEqual(send.call_count, 1)
        self.delivery.refresh_from_db()
        self.assertIsNotNone(self.delivery.confirmed_at)
        self.assertEqual(NotificationRead.objects.count(), 0)

    def test_timeout_is_query_only_and_not_retried(self):
        from .notifications import send_notice_delivery
        with patch('backend.notifications._transport_notice', side_effect=OSError) as send:
            self.assertEqual(send_notice_delivery(self.delivery.pk), 'unknown')
            self.assertEqual(send_notice_delivery(self.delivery.pk), 'unknown')
            self.assertEqual(send.call_count, 1)

    def test_closed_configuration_keeps_pending_and_never_calls_transport(self):
        from .notifications import send_notice_delivery
        with override_settings(NOTIFICATION_DELIVERY_ENABLED=False), patch('backend.notifications._transport_notice') as send:
            with self.assertRaisesRegex(OSError, 'NOTICE_CHANNEL_UNAVAILABLE'):
                send_notice_delivery(self.delivery.pk)
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.state, 'pending')
        send.assert_not_called()

    def test_removed_member_does_not_receive_queued_money_message(self):
        from .notifications import send_notice_delivery
        Membership.objects.filter(user=self.user).update(is_active=False)
        with patch('backend.notifications._transport_notice') as send:
            self.assertEqual(send_notice_delivery(self.delivery.pk), 'failed')
        send.assert_not_called()

    def test_worker_dry_run_has_no_writes_or_external_calls(self):
        from io import StringIO
        from django.core.management import call_command
        before = (BillingNotification.objects.count(), NotificationDelivery.objects.count())
        with patch('backend.notifications._transport_notice') as send:
            call_command('process_notifications', stdout=StringIO())
        self.assertEqual(before, (BillingNotification.objects.count(), NotificationDelivery.objects.count()))
        send.assert_not_called()

    def test_worker_requires_explicit_start_and_sends_once(self):
        from io import StringIO
        from django.core.management import call_command, CommandError
        with self.assertRaisesRegex(CommandError, 'NOTICE_START_TIME_REQUIRED'):
            call_command('process_notifications', apply=True, stdout=StringIO())
        with patch('backend.notifications._transport_notice', return_value='fixture-message') as send:
            for _ in range(2):
                call_command('process_notifications', apply=True,
                    since=(timezone.now()-timedelta(days=1)).isoformat(), stdout=StringIO())
            self.assertEqual(send.call_count, 1)

    def test_transport_uses_readable_title_and_keeps_email_type(self):
        from .notifications import _transport_notice, NOTICE_TITLES
        self.assertEqual(set(NOTICE_TITLES),set(BillingNotification.TYPES))
        with patch('backend.email.send_notice',return_value='fixture-message') as email:
            _transport_notice(self.delivery,'fixture@example.invalid')
        self.assertEqual(email.call_args.args[2]['type'],'payment_received')
        self.assertEqual(email.call_args.args[2]['title'],'付款已确认')
        self.delivery.channel='sms'
        with override_settings(NOTIFICATION_SMS_TEMPLATE_ID='124',NOTIFICATION_SMS_TEMPLATE_APPROVED=True), \
                patch('backend.sms.send_notice',return_value='fixture-sms') as sms:
            _transport_notice(self.delivery,'+8613800000001')
        self.assertEqual(sms.call_args.args[2][0],'付款已确认')

    def test_unknown_notification_type_fails_before_claim_and_never_sends_code(self):
        from .notifications import prepare_notice_delivery,send_notice_delivery
        notice=BillingNotification.objects.create(account=self.account,type='payment_received',
            source_event='unknown-fixture-type',occurred_at=timezone.now())
        # Simulate an unrecognized legacy/corrupt row; normal model saves reject it.
        BillingNotification.objects.filter(pk=notice.pk).update(type='unknown_fixture_type')
        notice.refresh_from_db()
        delivery=prepare_notice_delivery(notice,self.user)
        with patch('backend.notifications._transport_notice') as send:
            with self.assertRaisesRegex(ValueError,'NOTICE_TYPE_UNSUPPORTED'):
                send_notice_delivery(delivery.pk)
        delivery.refresh_from_db();self.assertEqual(delivery.state,'pending')
        self.assertIsNone(delivery.attempted_at);send.assert_not_called()


@override_settings(NOTIFICATION_DELIVERY_ENABLED=True,NOTIFICATION_EMAIL_TEMPLATE_ID='123',
    NOTIFICATION_EMAIL_TEMPLATE_APPROVED=True,NOTIFICATION_SMS_TEMPLATE_ID='124',
    NOTIFICATION_SMS_TEMPLATE_APPROVED=False,NOTIFICATION_SITE_ORIGIN='https://sage.example.invalid')
class NotificationWorkerIsolationTests(TransactionTestCase):
    def notice(self,index,method):
        from .models import LoginIdentity
        user=get_user_model().objects.create_user(username='worker-isolation-'+str(index))
        account=BillingAccount.objects.create(kind='personal',name='Synthetic',personal_owner=user)
        Membership.objects.create(account=account,user=user,role='owner')
        LoginIdentity.objects.create(user=user,method=method,
            identifier='worker'+str(index)+'@example.invalid' if method == 'email' else '+8613800000001')
        notice=BillingNotification.objects.create(account=account,type='payment_received',
            source_event='worker-isolation-'+str(index),occurred_at=timezone.now())
        return user,notice

    def worker(self):
        from io import StringIO
        from django.core.management import call_command
        output=StringIO()
        call_command('process_notifications',apply=True,
            since=(timezone.now()-timedelta(days=1)).isoformat(),stdout=output)
        return json.loads(output.getvalue())

    def test_unconfigured_sms_stays_pending_and_other_email_still_sends(self):
        _,phone=self.notice(1,'phone');_,email=self.notice(2,'email')
        with patch('backend.notifications._transport_notice',return_value='fixture-message') as send:
            with self.assertLogs('sage',level='WARNING') as logs:
                result=self.worker()
            self.assertEqual(send.call_count,1)
        self.assertEqual(result['channels_unavailable'],1)
        self.assertEqual(result['sent'],1)
        self.assertEqual(NotificationDelivery.objects.get(notification=phone).state,'pending')
        self.assertEqual(NotificationDelivery.objects.get(notification=email).state,'sent')
        self.assertNotIn('+8613800000001','\n'.join(logs.output))
        self.assertNotIn('@example.invalid','\n'.join(logs.output))

    @override_settings(NOTIFICATION_SMS_TEMPLATE_APPROVED=True)
    def test_unknown_sms_does_not_send_again_after_email_is_added(self):
        from .models import LoginIdentity
        user,notice=self.notice(1,'phone')
        with patch('backend.notifications._transport_notice',side_effect=OSError) as first:
            self.assertEqual(self.worker()['unknown'],1)
        LoginIdentity.objects.create(user=user,method='email',identifier='added@example.invalid')
        with patch('backend.notifications._transport_notice') as second:
            self.worker()
        self.assertEqual(first.call_count,1);second.assert_not_called()
        self.assertEqual(NotificationDelivery.objects.filter(notification=notice).count(),1)
        self.assertEqual(NotificationDelivery.objects.get(notification=notice).state,'unknown')

    def test_existing_attempted_delivery_prevents_all_automatic_channel_changes(self):
        from .models import LoginIdentity
        from .notifications import prepare_notice_delivery
        from django.db import transaction
        for state in ('sending','unknown','sent','delivered'):
            with self.subTest(state=state),transaction.atomic():
                user,notice=self.notice(state,'phone')
                original=prepare_notice_delivery(notice,user)
                original.state=state;original.save(update_fields=['state'])
                LoginIdentity.objects.create(user=user,method='email',identifier=state+'@example.invalid')
                self.assertEqual(prepare_notice_delivery(notice,user).pk,original.pk)
                self.assertEqual(NotificationDelivery.objects.filter(notification=notice).count(),1)
                transaction.set_rollback(True)

    def test_already_created_other_channel_cannot_claim_after_unknown(self):
        from .notifications import send_notice_delivery
        user,notice=self.notice(1,'email')
        NotificationDelivery.objects.create(notification=notice,user=user,channel='sms',state='unknown',
            attempted_at=timezone.now())
        pending=NotificationDelivery.objects.create(notification=notice,user=user,channel='email')
        with patch('backend.notifications._transport_notice') as send:
            self.assertEqual(send_notice_delivery(pending.pk),'unknown')
        pending.refresh_from_db();self.assertEqual(pending.state,'pending');send.assert_not_called()

    def test_changed_contact_is_reported_and_does_not_stop_other_recipients(self):
        from .models import LoginIdentity
        from .notifications import prepare_notice_delivery
        self.notice(1,'email');self.notice(2,'email')
        def contact_changes(notice,user):
            delivery=prepare_notice_delivery(notice,user)
            if notice.source_event == 'worker-isolation-1':
                LoginIdentity.objects.filter(user=user,method='email').delete()
                LoginIdentity.objects.create(user=user,method='phone',identifier='+8613800000001')
            return delivery
        with patch('backend.management.commands.process_notifications.prepare_notice_delivery',side_effect=contact_changes), \
                patch('backend.notifications._transport_notice',return_value='fixture-message') as send:
            with self.assertLogs('sage',level='WARNING'):
                result=self.worker()
        self.assertEqual(result['contacts_changed'],1);self.assertEqual(result['sent'],1)
        self.assertEqual(send.call_count,1)

    def test_unexpected_worker_error_is_not_swallowed(self):
        self.notice(1,'email')
        with patch('backend.management.commands.process_notifications.send_notice_delivery',side_effect=ValueError('unexpected-fixture')):
            with self.assertRaisesRegex(ValueError,'unexpected-fixture'):
                self.worker()

    @override_settings(NOTIFICATION_SMS_TEMPLATE_APPROVED=True)
    def test_inflight_sms_and_concurrent_new_email_cannot_claim_another_send(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from django.db import connection,close_old_connections
        from .models import LoginIdentity
        from .notifications import prepare_notice_delivery,send_notice_delivery
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row locks required')
        user,notice=self.notice(1,'phone')
        original=prepare_notice_delivery(notice,user)
        started,released=Event(),Event()
        def transport(row,identifier):
            started.set()
            if not released.wait(timeout=10):
                raise RuntimeError('Synthetic transport was not released')
            return 'fixture-inflight-message'
        def first_send():
            close_old_connections()
            try:return send_notice_delivery(original.pk)
            finally:close_old_connections()
        with patch('backend.notifications._transport_notice',side_effect=transport) as send, \
                ThreadPoolExecutor(max_workers=1) as pool:
            first=pool.submit(first_send)
            try:
                self.assertTrue(started.wait(timeout=5))
                LoginIdentity.objects.create(user=user,method='email',identifier='concurrent@example.invalid')
                self.assertEqual(prepare_notice_delivery(notice,user).pk,original.pk)
                # A pre-existing other-channel row must also fail closed at claim.
                pending=NotificationDelivery.objects.create(notification=notice,user=user,channel='email')
                self.assertEqual(send_notice_delivery(pending.pk),'sending')
                pending.refresh_from_db();self.assertEqual(pending.state,'pending')
            finally:
                released.set()
            self.assertEqual(first.result(timeout=10),'sent')
        self.assertEqual(send.call_count,1)
