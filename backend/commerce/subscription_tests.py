"""Explicit isolated fixtures, not real prices, mandates or payments."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from backend.models import AuditEvent, Membership
from backend.billing_rules import month_boundary
from .billing import create_quote, create_order, record_confirmed_payment, credits_summary, grant_credits
from .models import Subscription, SubscriptionPeriod, CreditBatch, CreditEntry, Order, PaymentFact, ProductVersion, PaymentAttempt
from .tests import fixture_account, FIXTURE_CHANNELS
from .subscriptions import (activate_subscription, append_paid_period, grant_due_credits,
                            cancel_subscription, confirm_cancellation, list_subscriptions)


@override_settings(BILLING_SALES_ENABLED=True, BILLING_RENEWAL_CHARGING_ENABLED=True,
                   BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
class RenewalIntentTests(TransactionTestCase):
    def setUp(self):
        SubscriptionTests.setUp(self)

    def paid(self):
        return SubscriptionTests.paid(self)

    def activate(self, *args, **kwargs):
        return SubscriptionTests.activate(self, *args, **kwargs)

    def prepare(self, subscription_id, **changes):
        from .subscriptions import prepare_renewal_attempt
        values = dict(cycle_index=1, dispatch_not_before=month_boundary(self.anchor, 12)-timedelta(days=1),
                      expires_at=month_boundary(self.anchor, 12))
        values.update(changes)
        return prepare_renewal_attempt(self.account, subscription_id, **values)

    def test_renewal_stop_preserves_intent_and_does_not_stop_purchase_or_receipts(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        from .subscriptions import fulfill_renewal_attempt
        sub = self.activate()
        attempt = self.prepare(sub['id'])
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        with override_settings(BILLING_RENEWAL_CHARGING_ENABLED=False):
            with self.assertRaisesRegex(ValueError, 'RENEWAL_CHARGING_DISABLED'):
                claim_payment_dispatch(self.account, attempt['id'])
            row = PaymentAttempt.objects.get(pk=attempt['id'])
            self.assertEqual(row.state, 'created')
            self.assertIsNone(row.dispatched_at)
            self.assertIsNone(row.dispatch_authorization)
            self.assertEqual(Order.objects.get(pk=row.order_id).status, 'pending_payment')
            # The renewal-specific switch does not block a separate new purchase.
            from .billing import prepare_payment_attempt
            # Use a fresh unpaid order; paid orders must not be dispatched again.
            quote = create_quote(self.account, self.user, self.product.pk, 1,
                timezone.now()+timedelta(minutes=5), channel='fixture', buyer_region='CN')
            purchase_order = create_order(self.account, self.user,
                {'quote_id':quote['id'], 'agreement_acceptance':'fixture-1'}, 'stop-purchase')
            purchase = prepare_payment_attempt(self.account, self.user, purchase_order['id'],
                'stop-purchase', expires_at=timezone.now()+timedelta(minutes=15))
            self.assertTrue(claim_payment_dispatch(self.account, purchase['id'])['dispatch'])
        with override_settings(BILLING_RENEWAL_CHARGING_ENABLED=True):
            self.assertTrue(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        with override_settings(BILLING_SALES_ENABLED=False, BILLING_RENEWAL_CHARGING_ENABLED=False):
            self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
            record_attempt_event(self.account, attempt['id'], 'stop-unknown', 'unknown',
                source='local', evidence_hash='')
            record_confirmed_payment(self.account, row.order_id, channel='fixture',
                merchant_reference='fixture-shenzhen', transaction_reference='stop-receipt',
                amount_minor=12345, currency='CNY', paid_at=timezone.now(), evidence_hash='b'*64)
            result = fulfill_renewal_attempt(self.account, attempt['id'])
            self.assertEqual(result, fulfill_renewal_attempt(self.account, attempt['id']))
            self.assertEqual(SubscriptionPeriod.objects.filter(subscription_id=sub['id']).count(), 2)
            self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])

    def test_payment_method_removal_stops_dispatch_and_waits_for_revocation(self):
        from .billing import claim_payment_dispatch
        from .subscriptions import (register_verified_payment_method, payment_method_impact,
            request_payment_method_removal, finish_payment_method_removal)
        method = register_verified_payment_method(self.account, channel='fixture',
            merchant_reference='fixture-shenzhen', fingerprint='c'*64, evidence_hash='d'*64)
        sub = self.activate(payment_method_id=method)
        attempt = self.prepare(sub['id'])
        impact = payment_method_impact(self.account, self.user, method)
        result = request_payment_method_removal(self.account, self.user, method,
            expected_impact=impact['confirmation_hash'])
        self.assertEqual(result['state'], 'removing')
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        self.assertEqual(credits_summary(self.account)['available'], '17.25')
        with self.assertRaisesRegex(ValueError, 'REMOVAL_PENDING'):
            finish_payment_method_removal(self.account, method, evidence_hash='e'*64)
        confirm_cancellation(self.account, sub['id'], agreement_reference='fixture-agreement', evidence_hash='f'*64)
        self.assertEqual(finish_payment_method_removal(self.account, method, evidence_hash='e'*64)['state'], 'removed')
        self.assertEqual(finish_payment_method_removal(self.account, method, evidence_hash='e'*64)['state'], 'removed')
        with self.assertRaisesRegex(ValueError, 'IDEMPOTENCY_CONFLICT'):
            finish_payment_method_removal(self.account, method, evidence_hash='a'*64)

    def topup_fixture(self):
        from .subscriptions import register_verified_payment_method
        order = SubscriptionTests.business_paid(self,'shared_only')
        self.activate(order)
        method = register_verified_payment_method(self.account,channel='fixture',merchant_reference='fixture-shenzhen',
            fingerprint='c'*64,evidence_hash='d'*64)
        pack = ProductVersion.objects.create(code='fixture_business_pack',version=1,kind='credits',audience='business',
            unit_amount_minor=731,credits_quantity='10',entitlements={'fixture':True},entitlement_version='fixture-1',
            credit_rule_version='fixture-1',terms_version='fixture-1',state='published',effective_at=self.anchor)
        args = dict(payment_method_id=method,product_id=pack.pk,threshold='2',monthly_cap_minor=1462,
            mandate={**self.mandate,'agreement_reference':'independent-topup'},terms_version='topup-fixture-1',
            consent_evidence_hash='e'*64,expected_version=0,request_key='topup-consent')
        return args

    def test_enterprise_topup_notifications_are_distinct_atomic_and_idempotent(self):
        from django.db import transaction
        from .models import BillingNotification
        from .subscriptions import (record_topup_authorization, pause_topup_authorization,
                                    request_topup_revocation, confirm_topup_revocation)
        from .billing import notifications_page
        args = self.topup_fixture()
        before = BillingNotification.objects.count()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            with self.assertRaisesRegex(ValueError,'ROLLBACK'):
                with transaction.atomic():
                    record_topup_authorization(self.account,self.user,**args)
                    raise ValueError('ROLLBACK')
            self.assertEqual(BillingNotification.objects.count(),before)
            result = record_topup_authorization(self.account,self.user,**args)
            self.assertEqual(result,record_topup_authorization(self.account,self.user,**args))
        with self.assertRaisesRegex(ValueError,'ROLLBACK'):
            with transaction.atomic():
                pause_topup_authorization(self.account,reason='user_requested')
                raise ValueError('ROLLBACK')
        self.assertFalse(BillingNotification.objects.filter(type='topup_authorization_paused').exists())
        pause_topup_authorization(self.account,reason='user_requested')
        pause_topup_authorization(self.account,reason='user_requested')
        with self.assertRaisesRegex(ValueError,'ROLLBACK'):
            with transaction.atomic():
                request_topup_revocation(self.account,self.user,result['id'])
                raise ValueError('ROLLBACK')
        self.assertFalse(BillingNotification.objects.filter(type='topup_revocation_processing').exists())
        request_topup_revocation(self.account,self.user,result['id'])
        request_topup_revocation(self.account,self.user,result['id'])
        values = dict(agreement_reference=args['mandate']['agreement_reference'],evidence_hash='f'*64)
        with self.assertRaisesRegex(ValueError,'ROLLBACK'):
            with transaction.atomic():
                confirm_topup_revocation(self.account,result['id'],**values)
                raise ValueError('ROLLBACK')
        self.assertFalse(BillingNotification.objects.filter(type='topup_revocation_confirmed').exists())
        confirm_topup_revocation(self.account,result['id'],**values)
        confirm_topup_revocation(self.account,result['id'],**values)
        rows = [row for row in notifications_page(self.account)['items'] if row['type'].startswith('topup_')]
        self.assertEqual({row['type'] for row in rows},{'topup_authorization_recorded','topup_authorization_paused',
            'topup_revocation_processing','topup_revocation_confirmed'})
        self.assertEqual(len(rows),4)
        self.assertTrue(all(row['amount_minor'] is None and row['currency'] is None for row in rows))
        self.assertTrue(all(set(row)=={'id','type','order_id','subscription_id','refund_id','amount_minor','currency','occurred_at'} for row in rows))

    def test_topup_consent_is_independent_versioned_and_refund_pauses(self):
        from .subscriptions import record_topup_authorization
        from .billing import freeze_credits, resolve_credit_freeze
        from .models import TopupAuthorization
        args = self.topup_fixture()
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            record_topup_authorization(self.account,self.user,**args)
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            with self.assertRaisesRegex(ValueError,'INDEPENDENT_AUTHORIZATION_REQUIRED'):
                record_topup_authorization(self.account,self.user,**{**args,'mandate':self.mandate})
            result = record_topup_authorization(self.account,self.user,**args)
            self.assertFalse(result['charging_enabled'])
            self.assertEqual(record_topup_authorization(self.account,self.user,**args),result)
            with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
                record_topup_authorization(self.account,self.user,**{**args,'threshold':'3'})
            with self.assertRaisesRegex(ValueError,'AUTHORIZATION_VERSION_CHANGED'):
                record_topup_authorization(self.account,self.user,**{**args,'request_key':'another'})
        self.assertEqual(Order.objects.count(),1)
        self.assertEqual(TopupAuthorization.objects.count(),1)
        grant_credits(self.account,'purchased','fixture-refund','10',self.anchor+timedelta(days=10))
        batch = CreditBatch.objects.get(source='purchased')
        hold = freeze_credits(self.account,batch.pk,'refund','2')
        consent = TopupAuthorization.objects.get(pk=result['id'])
        consent.refresh_from_db()
        self.assertEqual((consent.state,consent.pause_reason),('paused','credits_refund'))
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            with self.assertRaisesRegex(ValueError,'REFUND_PENDING'):
                record_topup_authorization(self.account,self.user,**{**args,'request_key':'during-refund',
                    'expected_version':1,'mandate':{**args['mandate'],'agreement_reference':'fresh-topup'}})
        resolve_credit_freeze(self.account,hold['id'],'released',evidence_hash='f'*64)
        consent.refresh_from_db()
        self.assertEqual(consent.state,'paused')
        self.assertEqual(AuditEvent.objects.filter(action='topup.authorization_paused').count(),1)

    def test_topup_expiry_pauses_cancelled_subscription_and_never_resumes_on_replay(self):
        from .subscriptions import record_topup_authorization, close_expired_renewals
        from .models import TopupAuthorization
        args = self.topup_fixture()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            result = record_topup_authorization(self.account,self.user,**args)
            cancel_subscription(self.account,self.user,Subscription.objects.get(account=self.account).pk)
            self.now.return_value = month_boundary(self.anchor,12)
            close_expired_renewals(self.account)
            record = TopupAuthorization.objects.get(pk=result['id'])
            self.assertEqual((record.state,record.pause_reason),('paused','subscription_expired'))
            self.assertEqual(record_topup_authorization(self.account,self.user,**args),result)
            record.refresh_from_db()
            self.assertEqual(record.state,'paused')
            close_expired_renewals(self.account)
            self.assertEqual(AuditEvent.objects.filter(action='topup.authorization_paused').count(),1)

    def test_topup_receipt_notification_survives_delivery_failure_and_manual_purchase_is_separate(self):
        from .subscriptions import record_topup_authorization, prepare_topup_attempt
        from .billing import claim_payment_dispatch, notifications_page
        from .models import BillingNotification
        args = self.topup_fixture()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            auth = record_topup_authorization(self.account,self.user,**args)
            attempt = prepare_topup_attempt(self.account,auth['id'],trigger_key='notice-receipt',
                starts_at=self.anchor,ends_at=self.anchor+timedelta(days=30),policy_version='fixture-window',
                expires_at=self.anchor+timedelta(hours=1))
            with self.assertRaisesRegex(ValueError,'TOPUP_CHARGING_DISABLED'):
                claim_payment_dispatch(self.account,attempt['id'])
            order = PaymentAttempt.objects.get(pk=attempt['id']).order
            values = dict(channel='fixture',merchant_reference='fixture-shenzhen',transaction_reference='notice-topup-receipt',
                amount_minor=731,currency='CNY',paid_at=self.anchor,evidence_hash='b'*64)
            with patch('backend.commerce.billing.fulfill_paid_order',side_effect=ValueError('DELIVERY_FAILED')):
                with self.assertRaisesRegex(ValueError,'DELIVERY_FAILED'):
                    record_confirmed_payment(self.account,order.pk,**values)
            row = BillingNotification.objects.get(order=order)
            self.assertEqual((row.type,row.amount_minor,row.currency),('topup_payment_received',731,'CNY'))
            self.assertEqual(Order.objects.get(pk=order.pk).status,'paid_pending_entitlement')
            self.assertFalse(CreditBatch.objects.filter(order=order).exists())
            record_confirmed_payment(self.account,order.pk,**values)
            record_confirmed_payment(self.account,order.pk,**values)
            self.assertEqual(BillingNotification.objects.filter(order=order).count(),1)
            public = next(item for item in notifications_page(self.account)['items'] if item['order_id']==str(order.pk))
            self.assertEqual((public['type'],public['amount_minor'],public['currency']),('topup_payment_received','731','CNY'))
            quote = create_quote(self.account,self.user,args['product_id'],1,self.anchor+timedelta(hours=1),channel='fixture',buyer_region='CN')
            manual = create_order(self.account,self.user,{'quote_id':quote['id'],'agreement_acceptance':'fixture-1'},'topup:manual-not-budget')
            record_confirmed_payment(self.account,manual['id'],**{**values,'transaction_reference':'notice-manual-receipt'})
            self.assertEqual(BillingNotification.objects.get(order_id=manual['id']).type,'payment_received')

    def test_topup_failed_notice_requires_applied_failure_and_rolls_back_with_event(self):
        from django.db import transaction
        from .subscriptions import record_topup_authorization, prepare_topup_attempt
        from .billing import record_attempt_event, claim_payment_dispatch
        from .models import BillingNotification, PaymentAttemptEvent
        args = self.topup_fixture()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            auth = record_topup_authorization(self.account,self.user,**args)
            def prepare(key):
                return prepare_topup_attempt(self.account,auth['id'],trigger_key=key,
                    starts_at=self.anchor,ends_at=self.anchor+timedelta(days=30),policy_version='fixture-window',
                    expires_at=self.anchor+timedelta(hours=1))
            first = prepare('notice-failure')
            with self.assertRaisesRegex(ValueError,'TOPUP_CHARGING_DISABLED'):
                claim_payment_dispatch(self.account,first['id'])
            # Isolated fixture of a prior unknown PSP dispatch; the real gate stays off.
            PaymentAttempt.objects.filter(pk=first['id']).update(state='unknown',dispatched_at=self.anchor)
            record_attempt_event(self.account,first['id'],'unknown','unknown',source='local',evidence_hash='')
            self.assertFalse(BillingNotification.objects.filter(type='topup_payment_failed').exists())
            with self.assertRaisesRegex(ValueError,'ROLLBACK'):
                with transaction.atomic():
                    record_attempt_event(self.account,first['id'],'failed','failed',source='provider_query',evidence_hash='c'*64)
                    raise ValueError('ROLLBACK')
            self.assertFalse(PaymentAttemptEvent.objects.filter(attempt_id=first['id'],event_key='failed').exists())
            self.assertFalse(BillingNotification.objects.filter(type='topup_payment_failed').exists())
            record_attempt_event(self.account,first['id'],'failed','failed',source='provider_query',evidence_hash='c'*64)
            record_attempt_event(self.account,first['id'],'failed','failed',source='provider_query',evidence_hash='c'*64)
            record_attempt_event(self.account,first['id'],'failed-again','failed',source='provider_query',evidence_hash='d'*64)
            row = BillingNotification.objects.get(order_id=PaymentAttempt.objects.get(pk=first['id']).order_id)
            self.assertEqual(row.type,'topup_payment_failed')
            self.assertEqual((row.amount_minor,row.currency),(None,''))
            second = prepare('notice-paid-late-failure')
            order = PaymentAttempt.objects.get(pk=second['id']).order
            record_confirmed_payment(self.account,order.pk,channel='fixture',merchant_reference='fixture-shenzhen',
                transaction_reference='notice-paid-before-failure',amount_minor=731,currency='CNY',paid_at=self.anchor,evidence_hash='b'*64)
            record_attempt_event(self.account,second['id'],'late-failed','failed',source='provider_query',evidence_hash='e'*64)
            self.assertFalse(PaymentAttemptEvent.objects.get(attempt_id=second['id'],event_key='late-failed').applied)
            self.assertEqual(BillingNotification.objects.filter(type='topup_payment_failed').count(),1)
            self.assertFalse(BillingNotification.objects.filter(order=order,type='topup_payment_failed').exists())

    def test_topup_budget_binds_intent_and_counts_success_and_inflight_across_versions(self):
        from .subscriptions import record_topup_authorization, prepare_topup_attempt, topup_budget_summary
        from .billing import claim_payment_dispatch, record_attempt_event
        from .models import TopupBudget
        args = self.topup_fixture()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        window = dict(starts_at=self.anchor,ends_at=self.anchor+timedelta(days=30),policy_version='fixture-window')
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            auth = record_topup_authorization(self.account,self.user,**args)
            def prepare(key,authorization=auth['id']):
                return prepare_topup_attempt(self.account,authorization,trigger_key=key,
                    expires_at=self.anchor+timedelta(hours=1),**window)
            first = prepare('one')
            self.assertEqual(prepare('one'),first)
            self.assertEqual(TopupBudget.objects.count(),1)
            with self.assertRaisesRegex(ValueError,'TOPUP_CHARGING_DISABLED'):
                claim_payment_dispatch(self.account,first['id'])
            with self.assertRaisesRegex(ValueError,'TOPUP_PENDING'):
                prepare('two')
            # Explicit isolated fixture of a previously dispatched unknown PSP result.
            PaymentAttempt.objects.filter(pk=first['id']).update(state='unknown',dispatched_at=self.anchor)
            self.assertEqual(topup_budget_summary(self.account,**window)['reserved_minor'],731)
            from .subscriptions import automatic_topup_summary
            summary = automatic_topup_summary(self.account)
            self.assertFalse(summary['charging_enabled'])
            self.assertEqual(summary['budget']['reserved_minor'],'731')
            self.assertEqual(summary['budget']['remaining_minor'],'731')
            self.assertNotIn('independent-topup',json.dumps(summary))
            record_attempt_event(self.account,first['id'],'failed','failed',source='provider_query',evidence_hash='b'*64)
            self.assertEqual(topup_budget_summary(self.account,**window)['reserved_minor'],0)
            second = prepare('two')
            order = PaymentAttempt.objects.get(pk=second['id']).order
            record_confirmed_payment(self.account,order.pk,channel='fixture',merchant_reference='fixture-shenzhen',
                transaction_reference='topup-success',amount_minor=731,currency='CNY',paid_at=self.anchor,evidence_hash='b'*64)
            self.assertEqual(topup_budget_summary(self.account,**window)['spent_minor'],731)
            new = record_topup_authorization(self.account,self.user,**{**args,'request_key':'new-consent',
                'expected_version':1,'mandate':{**args['mandate'],'agreement_reference':'new-topup'}})
            third = prepare('three',new['id'])
            totals = topup_budget_summary(self.account,**window)
            self.assertEqual((totals['spent_minor'],totals['reserved_minor']),(731,731))
            self.assertEqual(PaymentAttempt.objects.get(pk=third['id']).dispatch_authorization,None)
            with self.assertRaisesRegex(ValueError,'TOPUP_PENDING'):
                prepare('four',new['id'])
            third_order = PaymentAttempt.objects.get(pk=third['id']).order
            record_confirmed_payment(self.account,third_order.pk,channel='fixture',merchant_reference='fixture-shenzhen',
                transaction_reference='topup-success-three',amount_minor=731,currency='CNY',paid_at=self.anchor,evidence_hash='b'*64)
            with self.assertRaisesRegex(ValueError,'TOPUP_BUDGET_EXCEEDED'):
                prepare('four',new['id'])
            with self.assertRaisesRegex(ValueError,'BUDGET_WINDOW_CONFLICT'):
                prepare_topup_attempt(self.account,new['id'],trigger_key='shift-window',expires_at=self.anchor+timedelta(hours=1),
                    **{**window,'ends_at':self.anchor+timedelta(days=31)})
            self.assertEqual(TopupBudget.objects.count(),3)

    def test_topup_removal_waits_for_separate_revocation_and_preserves_paid_subscription(self):
        from .subscriptions import (record_topup_authorization, confirm_topup_revocation, payment_method_impact,
            request_payment_method_removal,finish_payment_method_removal)
        from .models import TopupAuthorization
        args = self.topup_fixture()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            result = record_topup_authorization(self.account,self.user,**args)
        evidence = dict(agreement_reference='independent-topup',evidence_hash='f'*64)
        with self.assertRaisesRegex(ValueError,'REVOCATION_NOT_REQUESTED'):
            confirm_topup_revocation(self.account,result['id'],**evidence)
        method = args['payment_method_id']
        impact = payment_method_impact(self.account,self.user,method)
        request_payment_method_removal(self.account,self.user,method,expected_impact=impact['confirmation_hash'])
        self.assertEqual(TopupAuthorization.objects.get(pk=result['id']).state,'revoking')
        with self.assertRaisesRegex(ValueError,'REMOVAL_PENDING'):
            finish_payment_method_removal(self.account,method,evidence_hash='a'*64)
        confirm_topup_revocation(self.account,result['id'],**evidence)
        confirm_topup_revocation(self.account,result['id'],**evidence)
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
            confirm_topup_revocation(self.account,result['id'],**{**evidence,'evidence_hash':'b'*64})
        finish_payment_method_removal(self.account,method,evidence_hash='a'*64)
        self.assertTrue(Subscription.objects.get(account=self.account).auto_renew)
        self.assertEqual(AuditEvent.objects.filter(action='topup.authorization_revoked').count(),1)

    def test_authorization_change_versions_dispatch_and_retired_revocation(self):
        from .subscriptions import (register_verified_payment_method, replace_subscription_authorization,
            confirm_retired_authorization, payment_method_impact, request_payment_method_removal,
            finish_payment_method_removal)
        from .billing import claim_payment_dispatch
        old = register_verified_payment_method(self.account, channel='fixture',
            merchant_reference='fixture-shenzhen', fingerprint='c'*64, evidence_hash='d'*64)
        new = register_verified_payment_method(self.account, channel='fixture',
            merchant_reference='fixture-shenzhen', fingerprint='e'*64, evidence_hash='f'*64)
        order = self.paid()
        sub = self.activate(order, payment_method_id=old)
        attempt = self.prepare(sub['id'])
        mandate = {**self.mandate, 'agreement_reference': 'replacement-agreement'}
        args = dict(payment_method_id=new, mandate=mandate, expected_version=0, request_key='replace-one')
        counts = (Order.objects.count(), SubscriptionPeriod.objects.count(), CreditBatch.objects.count())
        result = replace_subscription_authorization(self.account, self.user, sub['id'], **args)
        self.assertEqual(result['version'], 1)
        self.assertEqual(replace_subscription_authorization(self.account, self.user, sub['id'], **args), result)
        self.assertEqual((Order.objects.count(), SubscriptionPeriod.objects.count(), CreditBatch.objects.count()), counts)
        self.activate(order, payment_method_id=old)  # Original activation replay remains idempotent.
        with self.assertRaisesRegex(ValueError, 'AUTHORIZATION_VERSION_CHANGED'):
            replace_subscription_authorization(self.account, self.user, sub['id'], **{**args, 'request_key': 'stale'})
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        self.assertTrue(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        row = PaymentAttempt.objects.get(pk=attempt['id'])
        self.assertEqual(row.dispatch_authorization['mandate'], mandate)
        self.assertEqual(row.dispatch_authorization['authorization_version'], 1)
        impact = payment_method_impact(self.account, self.user, old)
        self.assertEqual(len(impact['retired_authorizations']), 1)
        from .subscriptions import payment_methods_summary
        public = next(item for item in payment_methods_summary(self.account)['items'] if item['id'] == old)
        self.assertEqual(public['retired_authorizations'], impact['retired_authorizations'])
        self.assertNotIn('fixture-agreement', json.dumps(public))
        request_payment_method_removal(self.account, self.user, old, expected_impact=impact['confirmation_hash'])
        with self.assertRaisesRegex(ValueError, 'REMOVAL_PENDING'):
            finish_payment_method_removal(self.account, old, evidence_hash='a'*64)
        confirm_retired_authorization(self.account, result['id'], agreement_reference='fixture-agreement', evidence_hash='b'*64)
        confirm_retired_authorization(self.account, result['id'], agreement_reference='fixture-agreement', evidence_hash='b'*64)
        finish_payment_method_removal(self.account, old, evidence_hash='a'*64)
        current = Subscription.objects.get(pk=sub['id'])
        self.assertTrue(current.auto_renew)
        self.assertEqual(str(current.payment_method_id), new)

    def test_authorization_change_keeps_inflight_and_rolls_back_failed_audit(self):
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization
        from .billing import claim_payment_dispatch
        from .models import AuthorizationChange
        old = register_verified_payment_method(self.account, channel='fixture',
            merchant_reference='fixture-shenzhen', fingerprint='c'*64, evidence_hash='d'*64)
        new = register_verified_payment_method(self.account, channel='fixture',
            merchant_reference='fixture-shenzhen', fingerprint='e'*64, evidence_hash='f'*64)
        sub = self.activate(payment_method_id=old)
        attempt = self.prepare(sub['id'])
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        claim_payment_dispatch(self.account, attempt['id'])
        snapshot = PaymentAttempt.objects.get(pk=attempt['id']).dispatch_authorization
        args = dict(payment_method_id=new, mandate={**self.mandate, 'agreement_reference':'replacement'},
            expected_version=0, request_key='replacement')
        with patch('backend.commerce.subscriptions.AuditEvent.objects.create', side_effect=RuntimeError('audit failure')):
            with self.assertRaisesRegex(RuntimeError, 'audit failure'):
                replace_subscription_authorization(self.account, self.user, sub['id'], **args)
        self.assertEqual(AuthorizationChange.objects.count(), 0)
        self.assertEqual(Subscription.objects.get(pk=sub['id']).authorization_version, 0)
        with self.assertRaisesRegex(ValueError, 'FORBIDDEN'):
            replace_subscription_authorization(self.account, self.other_user, sub['id'], **args)
        with self.assertRaisesRegex(ValueError, 'MANDATE_NOT_CONFIRMED'):
            replace_subscription_authorization(self.account, self.user, sub['id'], **{**args, 'mandate':{}})
        replace_subscription_authorization(self.account, self.user, sub['id'], **args)
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        self.assertEqual(PaymentAttempt.objects.get(pk=attempt['id']).dispatch_authorization, snapshot)
        with self.assertRaisesRegex(ValueError, 'IDEMPOTENCY_CONFLICT'):
            replace_subscription_authorization(self.account, self.user, sub['id'], **{**args, 'payment_method_id':old})

    @override_settings(BILLING_TERMS_DOCUMENTS={'fixture-1':{'approved':True,
        'zh':{'title':'测试','paragraphs':['测试协议']},'en':{'title':'Fixture','paragraphs':['Fixture terms']}}})
    def test_cross_channel_authorization_preserves_inflight_and_future_commercial_snapshot(self):
        from .subscriptions import (register_verified_payment_method, replace_subscription_authorization, prepare_renewal_attempt,
            fulfill_renewal_attempt, confirm_retired_authorization, payment_method_impact,
            request_payment_method_removal, finish_payment_method_removal)
        from .billing import claim_payment_dispatch, purchase_terms
        channels = {**FIXTURE_CHANNELS, 'replacement': dict(FIXTURE_CHANNELS['fixture'])}
        old = register_verified_payment_method(self.account, channel='fixture', merchant_reference='fixture-shenzhen', fingerprint='c'*64, evidence_hash='d'*64)
        new = register_verified_payment_method(self.account, channel='replacement', merchant_reference='fixture-shenzhen', fingerprint='e'*64, evidence_hash='f'*64)
        digest = purchase_terms('fixture-1')['digest']
        quote = create_quote(self.account,self.user,self.product.pk,1,timezone.now()+timedelta(minutes=5),channel='fixture',buyer_region='CN',terms_digest=digest)
        order = create_order(self.account,self.user,{'quote_id':quote['id'],'agreement_acceptance':'fixture-1'},'public-original')
        record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',transaction_reference='public-original',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
        sub = self.activate(order['id'],payment_method_id=old)
        initial = Subscription.objects.get(pk=sub['id']).initial_order
        original_snapshot = dict(initial.snapshot)
        first = self.prepare(sub['id'])
        self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
        claim_payment_dispatch(self.account,first['id'])
        old_dispatch = PaymentAttempt.objects.get(pk=first['id']).dispatch_authorization
        mandate = {**self.mandate,'channel':'replacement','agreement_reference':'new-channel-agreement'}
        args = dict(payment_method_id=new,mandate=mandate,expected_version=0,request_key='cross-channel')
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            result = replace_subscription_authorization(self.account,self.user,sub['id'],**args)
            self.assertEqual(replace_subscription_authorization(self.account,self.user,sub['id'],**args),result)
            self.assertFalse(claim_payment_dispatch(self.account,first['id'])['dispatch'])
            first_row = PaymentAttempt.objects.get(pk=first['id'])
            self.assertEqual(first_row.dispatch_authorization,old_dispatch)
            record_confirmed_payment(self.account,first_row.order_id,channel='fixture',merchant_reference='fixture-shenzhen',
                transaction_reference='old-channel-inflight',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
            # Removing an already-reconciled old instrument does not erase its
            # historical dispatch identity or prevent outstanding delivery.
            confirm_retired_authorization(self.account,result['id'],agreement_reference='fixture-agreement',evidence_hash='c'*64)
            impact = payment_method_impact(self.account,self.user,old)
            request_payment_method_removal(self.account,self.user,old,expected_impact=impact['confirmation_hash'])
            finish_payment_method_removal(self.account,old,evidence_hash='d'*64)
            fulfill_renewal_attempt(self.account,first['id'])
            second = prepare_renewal_attempt(self.account,sub['id'],cycle_index=2,
                dispatch_not_before=month_boundary(self.anchor,24)-timedelta(days=1),expires_at=month_boundary(self.anchor,24))
            second_row = PaymentAttempt.objects.get(pk=second['id'])
            self.assertEqual(second_row.order.snapshot,{**original_snapshot,'channel':'replacement'})
            self.assertEqual(second_row.order.snapshot['terms_document_digest'],digest)
            self.now.return_value = month_boundary(self.anchor,24)-timedelta(hours=1)
            # Simulated stored corruption cannot expand this transport-only change.
            saved_snapshot = second_row.order.snapshot
            Order.objects.filter(pk=second_row.order_id).update(snapshot={**saved_snapshot,'entitlements':{'unexpected':True}})
            with self.assertRaisesRegex(ValueError,'RENEWAL_SNAPSHOT_CONFLICT'):
                claim_payment_dispatch(self.account,second['id'])
            self.assertIsNone(PaymentAttempt.objects.get(pk=second['id']).dispatched_at)
            Order.objects.filter(pk=second_row.order_id).update(snapshot=saved_snapshot)
            self.assertTrue(claim_payment_dispatch(self.account,second['id'])['dispatch'])
            second_row.refresh_from_db()
            self.assertEqual(second_row.dispatch_authorization['mandate'],mandate)
            record_confirmed_payment(self.account,second_row.order_id,channel='replacement',merchant_reference='fixture-shenzhen',
                transaction_reference='new-channel-next-cycle',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
            Order.objects.filter(pk=second_row.order_id).update(snapshot={**saved_snapshot,'terms_version':'changed'})
            with self.assertRaisesRegex(ValueError,'SUBSCRIPTION_CHANGE_POLICY_REQUIRED'):
                fulfill_renewal_attempt(self.account,second['id'])
            Order.objects.filter(pk=second_row.order_id).update(snapshot=saved_snapshot)
            with override_settings(BILLING_SALES_ENABLED=False,BILLING_RENEWAL_CHARGING_ENABLED=False,BILLING_APPROVED_CHANNELS={}):
                fulfill_renewal_attempt(self.account,second['id'])
            self.assertEqual(SubscriptionPeriod.objects.filter(subscription_id=sub['id']).count(),3)
        initial.refresh_from_db()
        self.assertEqual(initial.snapshot,original_snapshot)

    def test_cross_channel_rejects_unapproved_or_invalid_authorization_without_changing_binding(self):
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization
        from .models import AuthorizationChange
        sub = self.activate()
        new = register_verified_payment_method(self.account, channel='replacement', merchant_reference='fixture-shenzhen', fingerprint='e'*64, evidence_hash='f'*64)
        args = dict(payment_method_id=new,mandate={**self.mandate,'channel':'replacement','agreement_reference':'replacement'},expected_version=0,request_key='cross')
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            replace_subscription_authorization(self.account,self.user,sub['id'],**args)
        channels = {**FIXTURE_CHANNELS,'replacement':dict(FIXTURE_CHANNELS['fixture'])}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            for invalid in ({'automatic_retry':True},{'merchant_reference':'different-merchant'},{'executor':'provider'}):
                with self.assertRaises(ValueError):
                    replace_subscription_authorization(self.account,self.user,sub['id'],**{**args,'mandate':{**args['mandate'],**invalid}})
        self.assertEqual(AuthorizationChange.objects.count(),0)
        self.assertEqual(Subscription.objects.get(pk=sub['id']).mandate,self.mandate)

    def test_undispatched_channel_changes_preserve_intent_and_pin_latest_transport(self):
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization, fulfill_renewal_attempt
        from .billing import claim_payment_dispatch, fulfill_paid_order, order_detail
        sub = self.activate()
        pending = self.prepare(sub['id'])
        before = PaymentAttempt.objects.get(pk=pending['id'])
        order_before = Order.objects.values().get(pk=before.order_id)
        quote_before = dict(before.order.quote.snapshot)
        channels = {**FIXTURE_CHANNELS, **{key:dict(FIXTURE_CHANNELS['fixture']) for key in ('replacement','third')}}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            for version, channel in enumerate(('replacement','third')):
                method = register_verified_payment_method(self.account,channel=channel,
                    merchant_reference='fixture-shenzhen',fingerprint=str(version+1)*64,evidence_hash='d'*64)
                mandate = {**self.mandate,'channel':channel,'agreement_reference':channel}
                args = dict(payment_method_id=method,mandate=mandate,expected_version=version,request_key=channel)
                changed = replace_subscription_authorization(self.account,self.user,sub['id'],**args)
                self.assertEqual(replace_subscription_authorization(self.account,self.user,sub['id'],**args),changed)
                self.assertEqual(self.prepare(sub['id']),pending)
                self.assertEqual(Order.objects.values().get(pk=before.order_id),order_before)
                row = PaymentAttempt.objects.get(pk=pending['id'])
                self.assertEqual(row.order.quote.snapshot,quote_before)
                self.assertIsNone(row.dispatch_authorization)
            self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
            claimed = claim_payment_dispatch(self.account,pending['id'])
            self.assertTrue(claimed['dispatch'])
            self.assertEqual(claimed['channel'],'third')
            row.refresh_from_db()
            self.assertEqual(row.dispatch_authorization['authorization_version'],2)
            frozen = dict(row.dispatch_authorization)
            self.assertFalse(claim_payment_dispatch(self.account,row.pk)['dispatch'])
            # Later authorization changes cannot rewrite this transaction's receipt identity.
            replace_subscription_authorization(self.account,self.user,sub['id'],payment_method_id=method,
                mandate={**mandate,'agreement_reference':'after-dispatch'},expected_version=2,request_key='after-dispatch')
            wrong = record_confirmed_payment(self.account,row.order_id,channel='fixture',
                merchant_reference='fixture-shenzhen',transaction_reference='wrong-original-channel',
                amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
            self.assertEqual(wrong['disposition'],'identity_mismatch_review')
            received = record_confirmed_payment(self.account,row.order_id,channel='third',
                merchant_reference='fixture-shenzhen',transaction_reference='migrated-channel',
                amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
            self.assertEqual(received['disposition'],'accepted')
            fulfill_paid_order(self.account,row.order_id)
            result = fulfill_renewal_attempt(self.account,row.pk)
            self.assertEqual(fulfill_renewal_attempt(self.account,row.pk),result)
            row.refresh_from_db()
            self.assertEqual(row.dispatch_authorization,frozen)
            self.assertEqual(row.order.snapshot,order_before['snapshot'])
            self.assertEqual(SubscriptionPeriod.objects.filter(subscription_id=sub['id']).count(),2)
            self.assertEqual(PaymentAttempt.objects.count(),1)
            self.assertTrue(order_detail(self.account,row.order_id)['needs_review'])

    def test_cross_channel_subscription_change_never_changes_independent_topup(self):
        from .subscriptions import record_topup_authorization, register_verified_payment_method, replace_subscription_authorization
        from .models import TopupAuthorization
        args = self.topup_fixture()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']},
                    'replacement':dict(FIXTURE_CHANNELS['fixture'])}
        new = register_verified_payment_method(self.account,channel='replacement',merchant_reference='fixture-shenzhen',fingerprint='e'*64,evidence_hash='f'*64)
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            topup = record_topup_authorization(self.account,self.user,**args)
            before = TopupAuthorization.objects.values().get(pk=topup['id'])
            sub = Subscription.objects.get(account=self.account)
            replace_subscription_authorization(self.account,self.user,sub.pk,payment_method_id=new,
                mandate={**self.mandate,'channel':'replacement','agreement_reference':'replacement'},expected_version=0,request_key='cross-channel')
            self.assertEqual(TopupAuthorization.objects.values().get(pk=topup['id']),before)

    def test_undispatched_migration_rejects_unsafe_intents_and_rolls_back_authorization(self):
        from django.db import transaction
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization
        from .models import AuthorizationChange, PaymentAttemptEvent
        from .billing import _persist_confirmed_payment, claim_payment_dispatch
        sub = self.activate()
        pending = self.prepare(sub['id'])
        row = PaymentAttempt.objects.get(pk=pending['id'])
        channels = {**FIXTURE_CHANNELS,'replacement':dict(FIXTURE_CHANNELS['fixture'])}
        method = register_verified_payment_method(self.account,channel='replacement',merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        args = dict(payment_method_id=method,mandate={**self.mandate,'channel':'replacement','agreement_reference':'replacement'},expected_version=0,request_key='migration')
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            for label in ('reconciliation','frozen','event','funds','expired','closed','commercial'):
                with self.subTest(label=label), transaction.atomic():
                    if label == 'reconciliation':
                        PaymentAttempt.objects.filter(pk=row.pk).update(needs_reconciliation=True)
                    elif label == 'frozen':
                        PaymentAttempt.objects.filter(pk=row.pk).update(dispatch_authorization={})
                    elif label == 'event':
                        PaymentAttemptEvent.objects.create(attempt=row,event_key='unexpected',outcome='unknown',source='local',evidence_hash='',applied=False)
                    elif label == 'funds':
                        _persist_confirmed_payment(self.account,row.order_id,channel='fixture',merchant_reference='other',transaction_reference='unexpected',amount_minor=1,currency='CNY',paid_at=timezone.now(),evidence_hash='c'*64)
                    elif label == 'expired':
                        self.now.return_value = month_boundary(self.anchor,12)
                    elif label == 'closed':
                        Order.objects.filter(pk=row.order_id).update(status='closed')
                    else:
                        Order.objects.filter(pk=row.order_id).update(snapshot={**row.order.snapshot,'amount_minor':1})
                    with self.assertRaisesRegex(ValueError,'RENEWAL_INTENT_PENDING|RENEWAL_NOT_AUTHORIZED'):
                        replace_subscription_authorization(self.account,self.user,sub['id'],**args)
                    self.assertFalse(AuthorizationChange.objects.exists())
                    transaction.set_rollback(True)
                self.now.return_value = self.anchor
            with patch('backend.commerce.subscriptions.AuditEvent.objects.create',side_effect=RuntimeError('audit failure')):
                with self.assertRaisesRegex(RuntimeError,'audit failure'):
                    replace_subscription_authorization(self.account,self.user,sub['id'],**args)
            self.assertFalse(AuthorizationChange.objects.exists())
            self.assertEqual(Subscription.objects.get(pk=sub['id']).authorization_version,0)
            replace_subscription_authorization(self.account,self.user,sub['id'],**args)
            self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
            with patch('backend.commerce.models.PaymentAttempt.save',side_effect=RuntimeError('dispatch failure')):
                with self.assertRaisesRegex(RuntimeError,'dispatch failure'):
                    claim_payment_dispatch(self.account,row.pk)
            row.refresh_from_db()
            self.assertEqual(row.state,'created')
            self.assertIsNone(row.dispatched_at)
            self.assertIsNone(row.dispatch_authorization)
            self.assertEqual(Subscription.objects.get(pk=sub['id']).authorization_version,1)

    def test_migrated_dispatch_rejects_forged_authorization_but_preserves_received_funds(self):
        from django.db import transaction
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization, fulfill_renewal_attempt
        from .billing import claim_payment_dispatch, _persist_confirmed_payment
        sub = self.activate()
        pending = self.prepare(sub['id'])
        method = register_verified_payment_method(self.account,channel='replacement',merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        channels = {**FIXTURE_CHANNELS,'replacement':dict(FIXTURE_CHANNELS['fixture'])}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            replace_subscription_authorization(self.account,self.user,sub['id'],payment_method_id=method,
                mandate={**self.mandate,'channel':'replacement','agreement_reference':'replacement'},expected_version=0,request_key='migration')
            self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
            claim_payment_dispatch(self.account,pending['id'])
        row = PaymentAttempt.objects.get(pk=pending['id'])
        frozen = row.dispatch_authorization
        for label, auth in (('missing',None),('no-history',{**frozen,'authorization_version':12}),
            ('boolean-version',{**frozen,'authorization_version':True}),('wrong-sub',{**frozen,'subscription_id':'other'}),
            ('wrong-mandate',{**frozen,'mandate':{**frozen['mandate'],'agreement_reference':'forged'}})):
            with self.subTest(label=label), transaction.atomic():
                PaymentAttempt.objects.filter(pk=row.pk).update(dispatch_authorization=auth)
                fact = _persist_confirmed_payment(self.account,row.order_id,channel='replacement',merchant_reference='fixture-shenzhen',
                    transaction_reference=label,amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
                self.assertEqual(fact['disposition'],'identity_mismatch_review')
                self.assertTrue(PaymentFact.objects.filter(pk=fact['id']).exists())
                self.assertIsNone(Order.objects.get(pk=row.order_id).paid_at)
                with self.assertRaises(ValueError):
                    fulfill_renewal_attempt(self.account,row.pk)
                self.assertEqual(SubscriptionPeriod.objects.count(),1)
                transaction.set_rollback(True)

    def test_channel_replacement_and_dispatch_serialize_on_one_intent(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Row-lock assertion requires PostgreSQL')
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization, fulfill_renewal_attempt
        from .billing import claim_payment_dispatch
        sub = self.activate()
        pending = self.prepare(sub['id'])
        method = register_verified_payment_method(self.account,channel='replacement',merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        channels = {**FIXTURE_CHANNELS,'replacement':dict(FIXTURE_CHANNELS['fixture'])}
        self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
        barrier = Barrier(2)
        def work(replace):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                if replace:
                    return replace_subscription_authorization(self.account,self.user,sub['id'],payment_method_id=method,
                        mandate={**self.mandate,'channel':'replacement','agreement_reference':'replacement'},expected_version=0,request_key='migration')
                return claim_payment_dispatch(self.account,pending['id'])
            finally:
                close_old_connections()
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(work,(True,False)))
            row = PaymentAttempt.objects.get(pk=pending['id'])
            self.assertTrue(results[1]['dispatch'])
            self.assertEqual(row.dispatch_authorization['authorization_version'],0 if results[1]['channel']=='fixture' else 1)
            self.assertEqual(row.dispatch_authorization['mandate']['channel'],results[1]['channel'])
            record_confirmed_payment(self.account,row.order_id,channel=results[1]['channel'],merchant_reference='fixture-shenzhen',
                transaction_reference='serialized',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
            fulfill_renewal_attempt(self.account,row.pk)
            self.assertFalse(claim_payment_dispatch(self.account,row.pk)['dispatch'])
            self.assertEqual(PaymentAttempt.objects.count(),1)
            self.assertEqual(Order.objects.count(),2)
            self.assertEqual(SubscriptionPeriod.objects.count(),2)

    def test_undispatched_expired_renewal_recovery_can_dispatch_and_activate(self):
        from .billing import claim_payment_dispatch
        from .subscriptions import activate_recovery_subscription
        sub = self.activate()
        original = self.prepare(sub['id'])
        self.now.return_value = month_boundary(self.anchor,12)
        self.assertEqual(claim_payment_dispatch(self.account,original['id'])['status'],'expired')
        recovery = self.recover(original)
        row = PaymentAttempt.objects.get(pk=recovery['id'])
        self.assertTrue(claim_payment_dispatch(self.account,row.pk)['dispatch'])
        record_confirmed_payment(self.account,row.order_id,channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='undispatched-recovery',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
        cancel_subscription(self.account,self.user,sub['id'])
        confirm_cancellation(self.account,sub['id'],agreement_reference='fixture-agreement',evidence_hash='c'*64)
        args = dict(activated_at=timezone.now(),mandate={**self.mandate,'agreement_reference':'new-recovery'})
        recovered = activate_recovery_subscription(self.account,row.pk,**args)
        self.assertEqual(activate_recovery_subscription(self.account,row.pk,**args),recovered)
        self.assertEqual(Subscription.objects.filter(auto_renew=True).count(),1)
        self.assertEqual(PaymentAttempt.objects.count(),2)

    def test_unknown_or_future_creation_channel_cannot_be_erased_by_migration(self):
        from django.db import transaction
        from .models import Quote
        from .billing import claim_payment_dispatch
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization
        sub = self.activate()
        pending = self.prepare(sub['id'])
        row = PaymentAttempt.objects.get(pk=pending['id'])
        channels = {**FIXTURE_CHANNELS,'replacement':dict(FIXTURE_CHANNELS['fixture'])}
        method = register_verified_payment_method(self.account,channel='replacement',merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        # The replacement did not exist at intent creation; simulate a damaged
        # stored channel in both immutable rows, not a client attack route.
        self.now.return_value = self.anchor+timedelta(days=1)
        args = dict(payment_method_id=method,mandate={**self.mandate,'channel':'replacement','agreement_reference':'replacement'},expected_version=0,request_key='migration')
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            for channel in (None,'unknown','replacement'):
                with self.subTest(channel=channel), transaction.atomic():
                    corrupted = {**row.order.snapshot,'channel':channel}
                    Order.objects.filter(pk=row.order_id).update(snapshot=corrupted)
                    Quote.objects.filter(pk=row.order.quote_id).update(snapshot=corrupted)
                    with self.assertRaisesRegex(ValueError,'RENEWAL_SNAPSHOT_CONFLICT'):
                        replace_subscription_authorization(self.account,self.user,sub['id'],**args)
                    transaction.set_rollback(True)
            replace_subscription_authorization(self.account,self.user,sub['id'],**args)
            self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
            for channel in (None,'unknown','replacement'):
                with self.subTest(dispatch_channel=channel):
                    corrupted = {**row.order.snapshot,'channel':channel}
                    Order.objects.filter(pk=row.order_id).update(snapshot=corrupted)
                    Quote.objects.filter(pk=row.order.quote_id).update(snapshot=corrupted)
                    try:
                        with self.assertRaisesRegex(ValueError,'RENEWAL_SNAPSHOT_CONFLICT'):
                            claim_payment_dispatch(self.account,row.pk)
                        self.assertIsNone(PaymentAttempt.objects.get(pk=row.pk).dispatched_at)
                    finally:
                        Order.objects.filter(pk=row.order_id).update(snapshot=row.order.snapshot)
                        Quote.objects.filter(pk=row.order.quote_id).update(snapshot=row.order.snapshot)

    def test_renewal_preserves_original_accepted_terms_digest_without_public_new_purchase(self):
        from .billing import purchase_terms
        terms = {'fixture-1':{'approved':True,'zh':{'title':'测试','paragraphs':['测试协议']},'en':{'title':'Fixture','paragraphs':['Fixture terms']}}}
        with override_settings(BILLING_TERMS_DOCUMENTS=terms):
            digest = purchase_terms('fixture-1')['digest']
            quote = create_quote(self.account,self.user,self.product.pk,1,timezone.now()+timedelta(minutes=5),channel='fixture',buyer_region='CN',terms_digest=digest)
            order = create_order(self.account,self.user,{'quote_id':quote['id'],'agreement_acceptance':'fixture-1'},'public-initial')
            record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',transaction_reference='public-initial',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
            sub = self.activate(order['id'])
            attempt = self.prepare(sub['id'])
            self.assertEqual(PaymentAttempt.objects.get(pk=attempt['id']).order.snapshot,Order.objects.get(pk=order['id']).snapshot)
            self.assertEqual(PaymentAttempt.objects.get(pk=attempt['id']).order.snapshot['terms_document_digest'],digest)

    def test_payment_method_unknown_inflight_blocks_removal_but_success_still_delivers(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        from .subscriptions import (register_verified_payment_method, payment_method_impact,
            request_payment_method_removal, finish_payment_method_removal, fulfill_renewal_attempt)
        method = register_verified_payment_method(self.account, channel='fixture',
            merchant_reference='fixture-shenzhen', fingerprint='c'*64, evidence_hash='d'*64)
        sub = self.activate(payment_method_id=method)
        attempt = self.prepare(sub['id'])
        old_impact = payment_method_impact(self.account, self.user, method)
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        self.assertTrue(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        with self.assertRaisesRegex(ValueError, 'PAYMENT_METHOD_IMPACT_CHANGED'):
            request_payment_method_removal(self.account, self.user, method,
                expected_impact=old_impact['confirmation_hash'])
        impact = payment_method_impact(self.account, self.user, method)
        self.assertEqual(len(impact['unresolved_payments']), 1)
        request_payment_method_removal(self.account, self.user, method, expected_impact=impact['confirmation_hash'])
        confirm_cancellation(self.account, sub['id'], agreement_reference='fixture-agreement', evidence_hash='f'*64)
        record_attempt_event(self.account, attempt['id'], 'unknown', 'unknown', source='local', evidence_hash='')
        with self.assertRaisesRegex(ValueError, 'REMOVAL_PENDING'):
            finish_payment_method_removal(self.account, method, evidence_hash='e'*64)
        row = PaymentAttempt.objects.get(pk=attempt['id'])
        record_confirmed_payment(self.account, row.order_id, channel='fixture', merchant_reference='fixture-shenzhen',
            transaction_reference='method-inflight', amount_minor=12345, currency='CNY',
            paid_at=timezone.now(), evidence_hash='b'*64)
        fulfill_renewal_attempt(self.account, attempt['id'])
        self.assertEqual(SubscriptionPeriod.objects.count(), 2)
        self.assertEqual(finish_payment_method_removal(self.account, method, evidence_hash='e'*64)['state'], 'removed')
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])

    def test_duplicate_receipt_blocks_method_removal_without_regranting(self):
        from .billing import claim_payment_dispatch
        from .subscriptions import (register_verified_payment_method, payment_method_impact,
            request_payment_method_removal, finish_payment_method_removal, fulfill_renewal_attempt)
        method = register_verified_payment_method(self.account, channel='fixture',
            merchant_reference='fixture-shenzhen', fingerprint='c'*64, evidence_hash='d'*64)
        sub = self.activate(payment_method_id=method)
        attempt = self.prepare(sub['id'])
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        claim_payment_dispatch(self.account, attempt['id'])
        row = PaymentAttempt.objects.get(pk=attempt['id'])
        for reference in ('original-receipt', 'duplicate-receipt'):
            record_confirmed_payment(self.account, row.order_id, channel='fixture',
                merchant_reference='fixture-shenzhen', transaction_reference=reference,
                amount_minor=12345, currency='CNY', paid_at=timezone.now(), evidence_hash='b'*64)
        fulfill_renewal_attempt(self.account, attempt['id'])
        self.assertEqual(SubscriptionPeriod.objects.count(), 2)
        self.assertEqual(PaymentFact.objects.filter(order=row.order).count(), 2)
        impact = payment_method_impact(self.account, self.user, method)
        self.assertEqual(impact['unresolved_payments'], [{'id': attempt['id'], 'status': 'review'}])
        request_payment_method_removal(self.account, self.user, method, expected_impact=impact['confirmation_hash'])
        confirm_cancellation(self.account, sub['id'], agreement_reference='fixture-agreement', evidence_hash='f'*64)
        with self.assertRaisesRegex(ValueError, 'REMOVAL_PENDING'):
            finish_payment_method_removal(self.account, method, evidence_hash='e'*64)

    def test_payment_method_scope_replay_removed_binding_and_atomic_failure(self):
        from .subscriptions import (register_verified_payment_method, payment_method_impact,
            request_payment_method_removal, finish_payment_method_removal)
        from .models import PaymentMethod
        args = dict(channel='fixture', merchant_reference='fixture-shenzhen', fingerprint='c'*64, evidence_hash='d'*64)
        method = register_verified_payment_method(self.account, **args)
        other = register_verified_payment_method(self.other, **args)
        self.assertNotEqual(method, other)
        self.assertEqual(register_verified_payment_method(self.account, **args), method)
        with self.assertRaisesRegex(ValueError, 'FORBIDDEN'):
            payment_method_impact(self.account, self.other_user, method)
        with self.assertRaisesRegex(ValueError, 'NOT_FOUND'):
            payment_method_impact(self.other, self.other_user, method)
        sub = self.activate(payment_method_id=method)
        impact = payment_method_impact(self.account, self.user, method)
        with patch('backend.commerce.subscriptions.AuditEvent.objects.create', side_effect=RuntimeError('audit-failed')):
            with self.assertRaisesRegex(RuntimeError, 'audit-failed'):
                request_payment_method_removal(self.account, self.user, method, expected_impact=impact['confirmation_hash'])
        self.assertEqual(PaymentMethod.objects.get(pk=method).state, 'active')
        self.assertTrue(Subscription.objects.get(pk=sub['id']).auto_renew)
        request_payment_method_removal(self.account, self.user, method, expected_impact=impact['confirmation_hash'])
        request_payment_method_removal(self.account, self.user, method, expected_impact=impact['confirmation_hash'])
        self.assertEqual(AuditEvent.objects.filter(action='payment_method.remove_requested').count(), 1)
        self.assertEqual(PaymentMethod.objects.get(pk=other).state, 'active')
        confirm_cancellation(self.account, sub['id'], agreement_reference='fixture-agreement', evidence_hash='f'*64)
        finish_payment_method_removal(self.account, method, evidence_hash='e'*64)
        self.now.return_value = month_boundary(self.anchor, 12)
        new_order = self.paid()
        with self.assertRaisesRegex(ValueError, 'PAYMENT_METHOD_BINDING_REQUIRED'):
            activate_subscription(self.account, new_order, activated_at=timezone.now(), billing_timezone='Asia/Shanghai',
                calendar_rule_version='fixture-calendar-v1', mandate=self.mandate)
        with self.assertRaisesRegex(ValueError, 'PAYMENT_METHOD_NOT_AVAILABLE'):
            activate_subscription(self.account, new_order, activated_at=timezone.now(), billing_timezone='Asia/Shanghai',
                calendar_rule_version='fixture-calendar-v1', mandate=self.mandate, payment_method_id=method)

    @override_settings(BILLING_APPROVED_CHANNELS={'fixture':{**FIXTURE_CHANNELS['fixture'],
        'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}})
    def test_payment_methods_show_independent_topup_history_without_writes_or_secrets(self):
        from django.test.utils import CaptureQueriesContext
        from .models import TopupAuthorization
        from .subscriptions import (record_topup_authorization, payment_methods_summary,
            request_topup_revocation, confirm_topup_revocation, register_verified_payment_method)
        args = self.topup_fixture()
        first = record_topup_authorization(self.account,self.user,**args)
        second = record_topup_authorization(self.account,self.user,**{**args,
            'expected_version':1,'request_key':'second-topup',
            'mandate':{**args['mandate'],'agreement_reference':'second-independent'}})
        method = args['payment_method_id']
        public = next(item for item in payment_methods_summary(self.account)['items'] if item['id']==method)
        self.assertEqual([item['state'] for item in public['topup_authorizations']],['superseded','authorized'])
        self.assertEqual([item['version'] for item in public['topup_authorizations']],[1,2])
        request_topup_revocation(self.account,self.user,first['id'])
        before = list(TopupAuthorization.objects.values())
        with CaptureQueriesContext(connection) as queries:
            data = payment_methods_summary(self.account)
            self.assertEqual(data,payment_methods_summary(self.account))
        for query in queries.captured_queries:
            self.assertRegex(query['sql'],r'^(SELECT|BEGIN|COMMIT|SAVEPOINT|RELEASE SAVEPOINT)\b')
        self.assertEqual(before,list(TopupAuthorization.objects.values()))
        self.assertFalse(data['can_edit'])
        self.assertFalse(data['automatic_topup_supported'])
        public = next(item for item in data['items'] if item['id']==method)
        timestamp = self.anchor.astimezone(ZoneInfo('UTC')).isoformat()
        self.assertEqual(public['topup_authorizations'][0],{
            'id':first['id'],'state':'revoking','version':1,'created_at':timestamp,
            'revocation_requested_at':timestamp,'revoked_at':None})
        self.assertEqual(public['topup_authorizations'][1]['id'],second['id'])
        self.assertIsNone(public['topup_authorizations'][1]['revocation_requested_at'])
        for secret in ('mandate','agreement_reference','evidence_hash','fingerprint','configuration','request_key','actor'):
            self.assertNotIn(secret,json.dumps(data))
        confirm_topup_revocation(self.account,first['id'],agreement_reference=args['mandate']['agreement_reference'],evidence_hash='f'*64)
        public = next(item for item in payment_methods_summary(self.account)['items'] if item['id']==method)
        self.assertEqual(public['topup_authorizations'][0]['state'],'revoked')
        self.assertEqual(public['topup_authorizations'][0]['revoked_at'],timestamp)
        self.assertEqual(public['topup_authorizations'][1]['state'],'authorized')
        other_method = register_verified_payment_method(self.other,channel='fixture',
            merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        self.assertEqual(payment_methods_summary(self.other)['items'][0]['id'],other_method)
        self.assertEqual(payment_methods_summary(self.other)['items'][0]['topup_authorizations'],[])

    @override_settings(BILLING_APPROVED_CHANNELS={'fixture':{**FIXTURE_CHANNELS['fixture'],
        'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}})
    def test_payment_methods_topup_projection_http_requires_financial_account(self):
        from .subscriptions import record_topup_authorization
        args = self.topup_fixture()
        record = record_topup_authorization(self.account,self.user,**args)
        client = Client()
        self.assertEqual(client.get('/api/billing/payment-methods').status_code,401)
        client.force_login(self.user)
        state=client.session;state['active_account_id']=str(self.account.pk);state.save()
        response=client.get('/api/billing/payment-methods',HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['items'][0]['topup_authorizations'][0]['id'],record['id'])
        self.assertEqual(client.get('/api/billing/payment-methods',HTTP_X_BILLING_ACCOUNT=str(self.other.pk)).status_code,409)
        self.assertEqual(client.post('/api/billing/payment-methods',{},content_type='application/json').status_code,405)
        client.force_login(self.other_user)
        state=client.session;state['active_account_id']=str(self.account.pk);state.save()
        self.assertEqual(client.get('/api/billing/payment-methods').status_code,403)
        Membership.objects.create(account=self.account,user=self.other_user,role='member')
        self.assertEqual(client.get('/api/billing/payment-methods').status_code,403)

    def test_canonical_intent_replay_and_changed_window(self):
        sub = self.activate()
        attempt = self.prepare(sub['id'])
        self.assertEqual(attempt, self.prepare(sub['id']))
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(Order.objects.count(), 2)
        with self.assertRaisesRegex(ValueError, 'PAYMENT_INTENT_CONFLICT'):
            self.prepare(sub['id'], dispatch_not_before=month_boundary(self.anchor, 12)-timedelta(hours=1))
        with self.assertRaisesRegex(ValueError, 'RENEWAL_CYCLE_CONFLICT'):
            self.prepare(sub['id'], cycle_index=2)

    def test_renewal_dispatch_pins_authorization_once_and_keeps_it_private(self):
        from .billing import claim_payment_dispatch, order_detail
        from .subscriptions import register_verified_payment_method
        method = register_verified_payment_method(self.account,channel='fixture',merchant_reference='fixture-shenzhen',
            fingerprint='c'*64,evidence_hash='d'*64)
        sub = self.activate(payment_method_id=method)
        attempt = self.prepare(sub['id'])
        row = PaymentAttempt.objects.get(pk=attempt['id'])
        self.assertIsNone(row.dispatch_authorization)
        self.assertFalse(claim_payment_dispatch(self.account,attempt['id'])['dispatch'])
        row.refresh_from_db()
        self.assertIsNone(row.dispatch_authorization)
        self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
        self.assertTrue(claim_payment_dispatch(self.account,attempt['id'])['dispatch'])
        row.refresh_from_db()
        expected = {'subscription_id':sub['id'],'payment_method_id':method,'mandate':self.mandate,
                    'authorization_version':0}
        self.assertEqual(row.dispatch_authorization,expected)
        self.assertNotIn('agreement_reference',json.dumps(order_detail(self.account,row.order_id)))
        cancel_subscription(self.account,self.user,sub['id'])
        self.assertFalse(claim_payment_dispatch(self.account,attempt['id'])['dispatch'])
        row.refresh_from_db()
        self.assertEqual(row.dispatch_authorization,expected)
        # Simulate a later binding revision without rewriting the in-flight fact.
        Subscription.objects.filter(pk=sub['id']).update(payment_method=None)
        from .subscriptions import payment_method_impact
        self.assertEqual(len(payment_method_impact(self.account,self.user,method)['unresolved_payments']),1)
        row.dispatch_authorization = None
        with self.assertRaisesRegex(ValueError,'IMMUTABLE_BILLING_SNAPSHOT'):
            row.save(update_fields=['dispatch_authorization'])

    def failed_renewal(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        sub = self.activate()
        attempt = self.prepare(sub['id'])
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        claim_payment_dispatch(self.account, attempt['id'])
        record_attempt_event(self.account, attempt['id'], 'decline', 'failed', source='provider_query', evidence_hash='c'*64)
        self.now.return_value = month_boundary(self.anchor, 12)
        return sub, attempt

    def test_renewal_failure_notification_is_not_a_receipt_or_duplicate(self):
        from .models import BillingNotification
        from .billing import record_attempt_event
        sub, attempt = self.failed_renewal()
        notice = BillingNotification.objects.get(type='renewal_failed')
        self.assertEqual(str(notice.subscription_id),sub['id'])
        self.assertIsNone(notice.amount_minor)
        self.assertEqual(notice.currency,'')
        record_attempt_event(self.account,attempt['id'],'another-decline','failed',
            source='verified_callback',evidence_hash='d'*64)
        self.assertEqual(BillingNotification.objects.filter(type='renewal_failed').count(),1)

    def test_renewal_receipt_is_classified_before_delivery_and_replays_once(self):
        from .models import BillingNotification
        from .billing import claim_payment_dispatch, record_attempt_event
        sub = self.activate()
        attempt = self.prepare(sub['id'])
        self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
        claim_payment_dispatch(self.account,attempt['id'])
        order_id = PaymentAttempt.objects.get(pk=attempt['id']).order_id
        values = dict(channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='renewal-notice',amount_minor=12345,currency='CNY',
            paid_at=timezone.now(),evidence_hash='b'*64)
        with patch('backend.commerce.billing.fulfill_paid_order',side_effect=ValueError('DELIVERY_FAILED')):
            with self.assertRaisesRegex(ValueError,'DELIVERY_FAILED'):
                record_confirmed_payment(self.account,order_id,**values)
        row = BillingNotification.objects.get(order_id=order_id)
        self.assertEqual(row.type,'renewal_payment_received')
        self.assertEqual(str(row.subscription_id),sub['id'])
        self.assertEqual(row.amount_minor,12345)
        self.assertEqual(Order.objects.get(pk=order_id).status,'paid_pending_entitlement')
        record_confirmed_payment(self.account,order_id,**values)
        record_confirmed_payment(self.account,order_id,**{**values,'transaction_reference':'renewal-duplicate'})
        record_attempt_event(self.account,attempt['id'],'stale-failure','failed',
            source='provider_query',evidence_hash='c'*64)
        self.assertEqual(BillingNotification.objects.filter(order_id=order_id).count(),1)
        self.assertFalse(BillingNotification.objects.filter(order_id=order_id,type='renewal_failed').exists())

    def test_actual_expiry_notices_are_atomic_per_period_not_account_shutdown(self):
        from django.db import transaction
        from .models import BillingNotification
        from .subscriptions import record_expired_notifications
        sub = self.activate()
        self.assertEqual(record_expired_notifications(self.account),0)
        self.now.return_value = month_boundary(self.anchor,1)
        with self.assertRaisesRegex(RuntimeError,'rollback'):
            with transaction.atomic():
                self.assertEqual(record_expired_notifications(self.account),1)
                raise RuntimeError('rollback')
        self.assertFalse(BillingNotification.objects.filter(type='credit_batch_expired').exists())
        self.assertEqual(record_expired_notifications(self.account),1)
        self.assertEqual(record_expired_notifications(self.account),0)
        self.now.return_value = month_boundary(self.anchor,12)
        self.assertEqual(record_expired_notifications(self.account),1)
        notice = BillingNotification.objects.get(type='service_period_ended')
        self.assertEqual(str(notice.subscription_id),sub['id'])
        self.assertEqual(notice.occurred_at,month_boundary(self.anchor,12))
        self.assertIsNone(notice.amount_minor)
        self.assertEqual(record_expired_notifications(self.account),0)
        self.assertEqual(CreditBatch.objects.count(),1)
        self.assertEqual(SubscriptionPeriod.objects.count(),1)

    def test_delivery_command_records_expiry_without_payment_or_repeat(self):
        from io import StringIO
        from django.core.management import call_command
        from .models import BillingNotification
        self.activate()
        self.now.return_value = month_boundary(self.anchor,12)
        output = StringIO()
        call_command('grant_subscription_credits',stdout=output)
        self.assertIn('payment attempts created: 0',output.getvalue())
        self.assertEqual(BillingNotification.objects.filter(type='credit_batch_expired').count(),12)
        self.assertEqual(BillingNotification.objects.filter(type='service_period_ended').count(),1)
        count = BillingNotification.objects.count()
        call_command('grant_subscription_credits',stdout=StringIO())
        self.assertEqual(BillingNotification.objects.count(),count)
        self.assertFalse(PaymentAttempt.objects.exists())
        self.assertEqual(PaymentFact.objects.count(),1)

    def recover(self, attempt, **changes):
        from .subscriptions import prepare_recovery_attempt
        values = dict(agreement_acceptance='fixture-1', expires_at=month_boundary(self.anchor,12)+timedelta(hours=1))
        values.update(changes)
        return prepare_recovery_attempt(self.account, self.user, attempt['id'], **values)

    def test_renewal_read_flags_extra_receipts_without_blocking_accepted_delivery(self):
        from django.db import transaction
        from .billing import claim_payment_dispatch, _persist_confirmed_payment
        from .subscriptions import fulfill_renewal_attempt
        sub=self.activate()
        attempt=self.prepare(sub['id'])
        self.now.return_value=month_boundary(self.anchor,12)-timedelta(hours=1)
        claim_payment_dispatch(self.account,attempt['id'])
        row=PaymentAttempt.objects.get(pk=attempt['id'])
        values=dict(channel='fixture',merchant_reference='fixture-shenzhen',amount_minor=12345,
            currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
        record_confirmed_payment(self.account,row.order_id,transaction_reference='normal-renewal',**values)
        self.assertEqual(list_subscriptions(self.account)[0]['renewal']['status'],'succeeded')
        client=Client();client.force_login(self.user)
        state=client.session;state['active_account_id']=str(self.account.pk);state.save()
        for label, changes in (('duplicate',{}),('amount',{'amount_minor':1}),('identity',{'merchant_reference':'other-fixture'})):
            with self.subTest(label=label):
                self.assertEqual(list_subscriptions(self.account)[0]['renewal']['status'],'succeeded')
                with transaction.atomic():
                    # The public receipt wrapper requires autocommit. Exercise
                    # its exact persistence path inside an isolated savepoint.
                    with transaction.atomic():
                        fact=_persist_confirmed_payment(self.account,row.order_id,transaction_reference=label,**{**values,**changes})
                        self.assertEqual(fact['disposition'],{'duplicate':'duplicate_payment_review',
                            'amount':'amount_mismatch_review','identity':'identity_mismatch_review'}[label])
                        data=client.get('/api/billing/subscriptions',HTTP_X_BILLING_ACCOUNT=str(self.account.pk)).json()['items'][0]['renewal']
                        self.assertEqual(data['status'],'review')
                        self.assertTrue(data['query_original_payment'])
                        self.assertFalse(data['manual_payment_eligible'])
                        self.assertFalse(data['new_subscription_payment_eligible'])
                        transaction.set_rollback(True)
                self.assertEqual(list_subscriptions(self.account)[0]['renewal']['status'],'succeeded')
        record_confirmed_payment(self.account,row.order_id,transaction_reference='delivery-duplicate',**values)
        self.now.return_value=month_boundary(self.anchor,12)+timedelta(days=4)
        from .subscriptions import close_expired_renewals
        result=list_subscriptions(self.account)[0]['renewal']
        self.assertEqual(result['status'],'review')
        self.assertTrue(result['query_original_payment'])
        self.assertFalse(result['reauthorization_required'])
        self.assertFalse(result['close_future_auto_renewal'])
        self.assertEqual(close_expired_renewals(self.account),0)
        self.assertTrue(Subscription.objects.get(pk=sub['id']).auto_renew)
        row.refresh_from_db();self.assertFalse(row.needs_reconciliation)
        first=fulfill_renewal_attempt(self.account,row.pk)
        self.assertEqual(first,fulfill_renewal_attempt(self.account,row.pk))
        self.assertEqual(SubscriptionPeriod.objects.filter(subscription_id=sub['id']).count(),2)
        self.assertEqual(PaymentAttempt.objects.count(),1)

    def test_recovery_extra_receipts_remain_visible_after_single_delivery(self):
        from .billing import claim_payment_dispatch
        from .subscriptions import activate_recovery_subscription, register_verified_payment_method
        sub,original=self.failed_renewal()
        recovery=self.recover(original)
        claim_payment_dispatch(self.account,recovery['id'])
        row=PaymentAttempt.objects.get(pk=recovery['id'])
        values=dict(channel='fixture',merchant_reference='fixture-shenzhen',amount_minor=12345,
            currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
        record_confirmed_payment(self.account,row.order_id,transaction_reference='recovery-accepted',**values)
        self.assertEqual(list_subscriptions(self.account)[0]['renewal']['status'],'succeeded')
        record_confirmed_payment(self.account,row.order_id,transaction_reference='recovery-duplicate',**values)
        result=list_subscriptions(self.account)[0]['renewal']
        self.assertEqual(result['status'],'review')
        self.assertTrue(result['query_original_payment'])
        self.now.return_value=month_boundary(self.anchor,12)+timedelta(days=4)
        from .subscriptions import close_expired_renewals
        result=list_subscriptions(self.account)[0]['renewal']
        self.assertFalse(result['reauthorization_required'])
        self.assertFalse(result['close_future_auto_renewal'])
        self.assertEqual(close_expired_renewals(self.account),0)
        self.assertTrue(Subscription.objects.get(pk=sub['id']).auto_renew)
        cancel_subscription(self.account,self.user,sub['id'])
        confirm_cancellation(self.account,sub['id'],agreement_reference=self.mandate['agreement_reference'],evidence_hash='c'*64)
        method=register_verified_payment_method(self.account,channel='fixture',merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        args=dict(activated_at=timezone.now(),mandate={**self.mandate,'agreement_reference':'recovery-new'},payment_method_id=method)
        new=activate_recovery_subscription(self.account,row.pk,**args)
        self.assertEqual(new,activate_recovery_subscription(self.account,row.pk,**args))
        old=next(item for item in list_subscriptions(self.account) if item['id']==sub['id'])
        self.assertEqual(old['renewal']['status'],'review')
        self.assertTrue(old['renewal']['query_original_payment'])
        self.assertFalse(old['renewal']['manual_payment_eligible'])
        self.assertFalse(old['renewal']['reauthorization_required'])
        self.assertFalse(old['renewal']['close_future_auto_renewal'])
        self.assertEqual(close_expired_renewals(self.account),0)
        self.assertTrue(Subscription.objects.get(pk=new['id']).auto_renew)
        self.assertEqual(CreditBatch.objects.filter(subscription_period__subscription_id=new['id']).count(),1)
        self.assertEqual(PaymentAttempt.objects.count(),2)

    def test_manual_recovery_has_unique_order_and_requires_terms(self):
        sub, original = self.failed_renewal()
        with self.assertRaisesRegex(ValueError, 'TERMS_MISMATCH'):
            self.recover(original, agreement_acceptance='not-accepted')
        self.assertEqual(Order.objects.count(),2)
        recovery = self.recover(original)
        self.assertEqual(recovery,self.recover(original))
        self.assertEqual(Order.objects.count(),3)
        self.assertEqual(PaymentAttempt.objects.get(pk=recovery['id']).recovery_of_id, PaymentAttempt.objects.get(pk=original['id']).pk)
        with self.assertRaisesRegex(ValueError,'PAYMENT_INTENT_CONFLICT'):
            self.recover(original,expires_at=month_boundary(self.anchor,12)+timedelta(hours=2))
        self.assertFalse(list_subscriptions(self.account)[0]['renewal']['manual_payment_eligible'])

    def test_recovery_rechecks_original_money_before_dispatch(self):
        from .billing import claim_payment_dispatch
        sub, original = self.failed_renewal()
        recovery = self.recover(original)
        row = PaymentAttempt.objects.get(pk=original['id'])
        record_confirmed_payment(self.account,row.order_id,channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='late-original',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
        self.assertFalse(claim_payment_dispatch(self.account,recovery['id'])['dispatch'])
        self.assertIsNone(PaymentAttempt.objects.get(pk=recovery['id']).dispatched_at)
        self.assertEqual(PaymentFact.objects.count(),2)

    def test_recovery_claims_once_and_unknown_never_retries(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        sub, original = self.failed_renewal()
        recovery = self.recover(original)
        self.assertTrue(claim_payment_dispatch(self.account,recovery['id'])['dispatch'])
        record_attempt_event(self.account,recovery['id'],'timeout','unknown',source='local',evidence_hash='')
        self.assertFalse(claim_payment_dispatch(self.account,recovery['id'])['dispatch'])
        self.assertEqual(self.recover(original)['id'],recovery['id'])
        result = list_subscriptions(self.account)[0]['renewal']
        self.assertTrue(result['query_original_payment'])
        self.assertFalse(result['manual_payment_eligible'])

    def test_recovery_outside_window_rejected(self):
        sub, original = self.failed_renewal()
        self.now.return_value = month_boundary(self.anchor,12)+timedelta(days=3)
        with self.assertRaisesRegex(ValueError,'RECOVERY_NOT_ELIGIBLE'):
            self.recover(original)
        self.assertEqual(Order.objects.count(),2)

    def test_dispatched_expiry_is_not_manual_recovery_eligible(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        sub = self.activate()
        original = self.prepare(sub['id'])
        self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
        claim_payment_dispatch(self.account,original['id'])
        record_attempt_event(self.account,original['id'],'expired','expired',source='provider_query',evidence_hash='c'*64)
        self.now.return_value = month_boundary(self.anchor,12)
        self.assertFalse(list_subscriptions(self.account)[0]['renewal']['manual_payment_eligible'])
        with self.assertRaisesRegex(ValueError,'RECOVERY_NOT_ELIGIBLE'):
            self.recover(original)

    @override_settings(BILLING_TERMS_DOCUMENTS={'fixture-1':{'approved':True,
        'zh':{'title':'测试','paragraphs':['测试协议']},'en':{'title':'Fixture','paragraphs':['Fixture terms']}}})
    def test_cross_channel_failed_renewal_recovery_preserves_terms_and_delivers_once(self):
        from .billing import claim_payment_dispatch, record_attempt_event, purchase_terms
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization, activate_recovery_subscription
        digest = purchase_terms('fixture-1')['digest']
        quote = create_quote(self.account,self.user,self.product.pk,1,timezone.now()+timedelta(minutes=5),channel='fixture',buyer_region='CN',terms_digest=digest)
        order = create_order(self.account,self.user,{'quote_id':quote['id'],'agreement_acceptance':'fixture-1'},'public-recovery-initial')
        record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',transaction_reference='public-recovery-initial',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
        sub = self.activate(order['id'])
        method = register_verified_payment_method(self.account,channel='replacement',merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        mandate = {**self.mandate,'channel':'replacement','agreement_reference':'replacement'}
        channels = {**FIXTURE_CHANNELS,'replacement':dict(FIXTURE_CHANNELS['fixture'])}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            original = self.prepare(sub['id'])
            replace_subscription_authorization(self.account,self.user,sub['id'],payment_method_id=method,
                mandate=mandate,expected_version=0,request_key='replacement')
            self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
            claim_payment_dispatch(self.account,original['id'])
            record_attempt_event(self.account,original['id'],'declined','failed',source='provider_query',evidence_hash='c'*64)
            self.now.return_value = month_boundary(self.anchor,12)
            recovery = self.recover(original)
            self.assertEqual(self.recover(original),recovery)
            row = PaymentAttempt.objects.get(pk=recovery['id'])
            original_snapshot = Order.objects.get(pk=order['id']).snapshot
            self.assertEqual(row.order.snapshot,{**original_snapshot,'channel':'replacement'})
            self.assertEqual(row.order.snapshot['terms_document_digest'],digest)
            self.assertTrue(claim_payment_dispatch(self.account,recovery['id'])['dispatch'])
            self.assertFalse(claim_payment_dispatch(self.account,recovery['id'])['dispatch'])
            record_confirmed_payment(self.account,row.order_id,channel='replacement',merchant_reference='fixture-shenzhen',transaction_reference='recovery-new-channel',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
            cancel_subscription(self.account,self.user,sub['id'])
            confirm_cancellation(self.account,sub['id'],agreement_reference='replacement',evidence_hash='c'*64)
            values = dict(activated_at=timezone.now(),mandate={**mandate,'agreement_reference':'recovery-fresh'},payment_method_id=method)
            new = activate_recovery_subscription(self.account,recovery['id'],**values)
            self.assertEqual(activate_recovery_subscription(self.account,recovery['id'],**values),new)
            self.assertEqual(CreditBatch.objects.filter(subscription_period__subscription_id=new['id']).count(),1)
            self.assertEqual(Subscription.objects.filter(auto_renew=True).count(),1)
            self.assertEqual(Order.objects.get(pk=row.order_id).status,'fulfilled')
            self.assertEqual(Order.objects.get(pk=order['id']).snapshot,original_snapshot)

    def test_old_unknown_or_replaced_authorization_cannot_create_recovery(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization
        sub = self.activate()
        original = self.prepare(sub['id'])
        self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
        claim_payment_dispatch(self.account,original['id'])
        record_attempt_event(self.account,original['id'],'unknown','unknown',source='local',evidence_hash='')
        method = register_verified_payment_method(self.account,channel='fixture',merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        replace_subscription_authorization(self.account,self.user,sub['id'],payment_method_id=method,
            mandate={**self.mandate,'agreement_reference':'replacement'},expected_version=0,request_key='replacement')
        self.now.return_value = month_boundary(self.anchor,12)
        with self.assertRaisesRegex(ValueError,'RECOVERY_NOT_ELIGIBLE'):
            self.recover(original)
        record_attempt_event(self.account,original['id'],'declined','failed',source='provider_query',evidence_hash='c'*64)
        with self.assertRaisesRegex(ValueError,'RECOVERY_NOT_ELIGIBLE'):
            self.recover(original)
        self.assertEqual(PaymentAttempt.objects.count(),1)
        self.assertEqual(Order.objects.count(),2)

    def test_recovery_activation_uses_new_confirmed_authorization_and_actual_start(self):
        from .billing import claim_payment_dispatch
        from .subscriptions import activate_recovery_subscription
        sub, original = self.failed_renewal()
        recovery = self.recover(original)
        claim_payment_dispatch(self.account,recovery['id'])
        row = PaymentAttempt.objects.get(pk=recovery['id'])
        record_confirmed_payment(self.account,row.order_id,channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='recovery-paid',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='b'*64)
        self.now.return_value += timedelta(minutes=10)
        activated_at = timezone.now()
        new_mandate = {**self.mandate,'agreement_reference':'fixture-reauthorized','evidence_hash':'d'*64}
        with self.assertRaisesRegex(ValueError,'RECOVERY_AUTHORIZATION_REQUIRED'):
            activate_recovery_subscription(self.account,recovery['id'],activated_at=activated_at,mandate=new_mandate)
        self.assertEqual(PaymentFact.objects.count(),2)
        cancel_subscription(self.account,self.user,sub['id'])
        confirm_cancellation(self.account,sub['id'],agreement_reference=self.mandate['agreement_reference'],evidence_hash='c'*64)
        with self.assertRaisesRegex(ValueError,'RECOVERY_AUTHORIZATION_REQUIRED'):
            activate_recovery_subscription(self.account,recovery['id'],activated_at=activated_at,mandate=self.mandate)
        from .subscriptions import register_verified_payment_method
        method = register_verified_payment_method(self.account,channel='fixture',merchant_reference='fixture-shenzhen',
            fingerprint='c'*64,evidence_hash='d'*64)
        with self.assertRaisesRegex(ValueError,'PAYMENT_METHOD_BINDING_REQUIRED'):
            activate_recovery_subscription(self.account,recovery['id'],activated_at=activated_at,mandate=new_mandate)
        result = activate_recovery_subscription(self.account,recovery['id'],activated_at=activated_at,mandate=new_mandate,payment_method_id=method)
        self.assertEqual(result,activate_recovery_subscription(self.account,recovery['id'],activated_at=activated_at,mandate=new_mandate,payment_method_id=method))
        self.assertEqual(str(Subscription.objects.get(pk=result['id']).payment_method_id),method)
        self.assertEqual(Subscription.objects.get(pk=result['id']).original_anchor,activated_at)
        self.assertEqual(Subscription.objects.get(pk=sub['id']).original_anchor,self.anchor)
        self.assertEqual(Subscription.objects.filter(auto_renew=True).count(),1)
        self.assertEqual(CreditBatch.objects.filter(subscription_period__subscription_id=result['id']).count(),1)
        self.assertEqual(credits_summary(self.account)['available'],'17.25')
        self.assertEqual(Order.objects.get(pk=row.order_id).status,'fulfilled')
        old = next(item for item in list_subscriptions(self.account) if item['id'] == sub['id'])
        self.assertIsNone(old['renewal'])
        original_row = PaymentAttempt.objects.get(pk=original['id'])
        record_confirmed_payment(self.account,original_row.order_id,channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='original-paid-after-recovery',amount_minor=12345,currency='CNY',paid_at=timezone.now(),evidence_hash='e'*64)
        old = next(item for item in list_subscriptions(self.account) if item['id'] == sub['id'])
        self.assertEqual(old['renewal']['status'],'review')
        self.assertTrue(old['renewal']['query_original_payment'])
        self.assertFalse(old['renewal']['manual_payment_eligible'])
        self.assertEqual(CreditBatch.objects.filter(subscription_period__subscription_id=result['id']).count(),1)

    def test_recovery_cancel_before_claim_and_cross_account(self):
        from .billing import claim_payment_dispatch
        from .subscriptions import prepare_recovery_attempt
        sub, original = self.failed_renewal()
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            prepare_recovery_attempt(self.other,self.other_user,original['id'],agreement_acceptance='fixture-1',expires_at=timezone.now()+timedelta(hours=1))
        recovery = self.recover(original)
        cancel_subscription(self.account,self.user,sub['id'])
        self.assertFalse(claim_payment_dispatch(self.account,recovery['id'])['dispatch'])
        self.assertIsNone(PaymentAttempt.objects.get(pk=recovery['id']).dispatched_at)

    def test_unknown_original_and_recovery_chaining_never_create_orders(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        sub = self.activate()
        original = self.prepare(sub['id'])
        self.now.return_value = month_boundary(self.anchor,12)-timedelta(hours=1)
        claim_payment_dispatch(self.account,original['id'])
        record_attempt_event(self.account,original['id'],'timeout','unknown',source='local',evidence_hash='')
        self.now.return_value = month_boundary(self.anchor,12)
        with self.assertRaisesRegex(ValueError,'RECOVERY_NOT_ELIGIBLE'):
            self.recover(original)
        self.assertEqual(Order.objects.count(),2)
        record_attempt_event(self.account,original['id'],'resolved-failed','failed',source='provider_query',evidence_hash='d'*64)
        recovery = self.recover(original)
        with self.assertRaisesRegex(ValueError,'RECOVERY_NOT_ELIGIBLE'):
            self.recover(recovery)
        self.assertEqual(Order.objects.count(),3)

    def test_recovery_sales_gate_and_unstarted_expiry(self):
        from .billing import claim_payment_dispatch
        _, original = self.failed_renewal()
        with override_settings(BILLING_SALES_ENABLED=False):
            with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
                self.recover(original)
        self.assertEqual(Order.objects.count(),2)
        recovery = self.recover(original)
        with override_settings(BILLING_SALES_ENABLED=False):
            with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
                claim_payment_dispatch(self.account,recovery['id'])
        self.now.return_value += timedelta(hours=1)
        result = claim_payment_dispatch(self.account,recovery['id'])
        self.assertFalse(result['dispatch'])
        self.assertEqual(result['status'],'expired')
        self.assertEqual(PaymentFact.objects.count(),1)

    def test_cancel_before_prepare_or_dispatch_never_claims(self):
        from .billing import claim_payment_dispatch
        sub = self.activate()
        attempt = self.prepare(sub['id'])
        cancel_subscription(self.account, self.user, sub['id'])
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        row = PaymentAttempt.objects.get(pk=attempt['id'])
        self.assertEqual(row.state, 'cancelled')
        self.assertIsNone(row.dispatched_at)
        self.assertEqual(row.order.status, 'closed')
        self.assertEqual(self.prepare(sub['id'])['id'], attempt['id'])
        self.assertEqual(Order.objects.count(), 2)

    def test_cancelled_subscription_cannot_create_sage_charge(self):
        sub = self.activate()
        cancel_subscription(self.account, self.user, sub['id'])
        with self.assertRaisesRegex(ValueError, 'RENEWAL_NOT_AUTHORIZED'):
            self.prepare(sub['id'])
        self.assertEqual(Order.objects.count(), 1)

    def test_provider_executor_is_not_charged_by_sage(self):
        self.mandate['executor'] = 'provider'
        sub = self.activate()
        with self.assertRaisesRegex(ValueError, 'RENEWAL_EXECUTOR_CONFLICT'):
            self.prepare(sub['id'])
        self.assertEqual(PaymentAttempt.objects.count(), 0)

    def test_expiry_and_confirmed_failure_never_retry(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        sub = self.activate()
        attempt = self.prepare(sub['id'])
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        claim_payment_dispatch(self.account, attempt['id'])
        record_attempt_event(self.account, attempt['id'], 'declined', 'failed', source='provider_query', evidence_hash='c'*64)
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        self.assertEqual(self.prepare(sub['id'])['id'], attempt['id'])
        self.now.return_value = month_boundary(self.anchor, 12)
        self.assertEqual(list_subscriptions(self.account)[0]['status'], 'expired')
        self.assertEqual(SubscriptionPeriod.objects.count(), 1)
        self.assertEqual(PaymentFact.objects.count(), 1)

    def test_cross_account_and_public_reserved_key_are_rejected(self):
        from .subscriptions import prepare_renewal_attempt
        sub = self.activate()
        with self.assertRaisesRegex(ValueError, 'NOT_FOUND'):
            prepare_renewal_attempt(self.other, sub['id'], cycle_index=1,
                dispatch_not_before=self.anchor, expires_at=month_boundary(self.anchor, 12))
        client = Client()
        client.force_login(self.user)
        session = client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        for key in (f"renewal:{sub['id']}:1",f"recovery:{sub['id']}"):
            response = client.post('/api/billing/orders', json.dumps({'quote_id': str(self.product.pk),
                'agreement_acceptance': 'fixture-1'}), content_type='application/json',
                HTTP_X_BILLING_ACCOUNT=str(self.account.pk), HTTP_IDEMPOTENCY_KEY=key)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()['error']['code'], 'INVALID_IDEMPOTENCY_KEY')
        self.assertEqual(Order.objects.count(), 1)

    def test_window_and_unknown_do_not_create_another_charge(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        sub = self.activate()
        attempt = self.prepare(sub['id'])
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        self.assertTrue(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        record_attempt_event(self.account, attempt['id'], 'timeout', 'unknown', source='local', evidence_hash='')
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        self.assertEqual(self.prepare(sub['id'])['id'], attempt['id'])
        self.assertEqual(PaymentAttempt.objects.count(), 1)

    def test_failed_renewal_window_has_no_free_service_and_closes_once(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        from .subscriptions import close_expired_renewals
        sub = self.activate()
        attempt = self.prepare(sub['id'])
        end = month_boundary(self.anchor, 12)
        self.now.return_value = end-timedelta(hours=1)
        claim_payment_dispatch(self.account, attempt['id'])
        record_attempt_event(self.account, attempt['id'], 'failure', 'failed', source='provider_query', evidence_hash='c'*64)
        before = list_subscriptions(self.account)[0]
        self.assertEqual(before['status'], 'active')
        self.assertFalse(before['renewal']['manual_payment_eligible'])
        self.now.return_value = end
        expired = list_subscriptions(self.account)[0]
        self.assertEqual(expired['status'], 'expired')
        self.assertTrue(expired['renewal']['manual_payment_eligible'])
        self.assertFalse(expired['capabilities']['manual_payment'])
        self.assertEqual(credits_summary(self.account)['available'], '0')
        self.assertEqual(close_expired_renewals(self.account), 0)
        self.now.return_value = end+timedelta(days=3)
        self.assertEqual(close_expired_renewals(self.account), 1)
        self.assertEqual(close_expired_renewals(self.account), 0)
        closed = list_subscriptions(self.account)[0]
        self.assertFalse(closed['auto_renew'])
        self.assertFalse(closed['renewal']['manual_payment_eligible'])
        self.assertTrue(closed['renewal']['reauthorization_required'])
        self.assertEqual(closed['cancellation_state'], 'processing')
        self.assertEqual(AuditEvent.objects.filter(action='subscription.recovery_expired').count(), 1)

    def test_unknown_receipt_blocks_new_payment_even_after_recovery_deadline(self):
        from .billing import claim_payment_dispatch, record_attempt_event
        from .subscriptions import close_expired_renewals
        sub = self.activate()
        attempt = self.prepare(sub['id'])
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        claim_payment_dispatch(self.account, attempt['id'])
        record_attempt_event(self.account, attempt['id'], 'timeout', 'unknown', source='local', evidence_hash='')
        self.now.return_value = month_boundary(self.anchor, 12)+timedelta(days=3)
        close_expired_renewals(self.account)
        result = list_subscriptions(self.account)[0]['renewal']
        self.assertTrue(result['query_original_payment'])
        self.assertFalse(result['manual_payment_eligible'])
        self.assertFalse(result['new_subscription_payment_eligible'])
        self.assertEqual(Order.objects.count(), 2)

    def test_missed_scheduler_still_closes_authorization_without_inventing_failure(self):
        from .subscriptions import close_expired_renewals
        self.activate()
        self.now.return_value = month_boundary(self.anchor, 12)+timedelta(days=3)
        self.assertEqual(close_expired_renewals(self.account), 1)
        self.assertEqual(close_expired_renewals(self.account), 0)
        result = list_subscriptions(self.account)[0]
        self.assertFalse(result['auto_renew'])
        self.assertEqual(result['renewal']['status'], 'missing')
        self.assertFalse(result['renewal']['new_subscription_payment_eligible'])
        self.assertFalse(result['renewal']['manual_payment_eligible'])
        self.assertEqual(PaymentAttempt.objects.count(), 0)

    def test_inflight_payment_after_cancel_delivers_once(self):
        from .billing import claim_payment_dispatch
        from .subscriptions import fulfill_renewal_attempt
        sub = self.activate()
        attempt = self.prepare(sub['id'])
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        self.assertTrue(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        cancel_subscription(self.account, self.user, sub['id'])
        row = PaymentAttempt.objects.get(pk=attempt['id'])
        record_confirmed_payment(self.account, row.order_id, channel='fixture', merchant_reference='fixture-shenzhen',
            transaction_reference='renewal', amount_minor=12345, currency='CNY', paid_at=timezone.now(), evidence_hash='b'*64)
        result = fulfill_renewal_attempt(self.account, attempt['id'])
        self.assertEqual(result, fulfill_renewal_attempt(self.account, attempt['id']))
        self.assertFalse(result['auto_renew'])
        self.assertEqual(SubscriptionPeriod.objects.count(), 2)
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])


@override_settings(BILLING_SALES_ENABLED=True, BILLING_RENEWAL_CHARGING_ENABLED=True,
                   BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
class SubscriptionTests(TransactionTestCase):
    def setUp(self):
        self.user, self.account = fixture_account()
        self.other_user, self.other = fixture_account('other')
        Membership.objects.create(account=self.account, user=self.user, role='owner')
        Membership.objects.create(account=self.other, user=self.other_user, role='owner')
        self.anchor = datetime(2028, 1, 31, 1, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.clock = patch('django.utils.timezone.now', return_value=self.anchor)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.product = ProductVersion.objects.create(code='fixture_plan', version=1, kind='subscription',
            audience='personal', billing_cycle='annual', unit_amount_minor=12345, credits_quantity='17.25',
            entitlements={'fixture': True}, entitlement_version='fixture-1', credit_rule_version='fixture-1',
            terms_version='fixture-1', state='published', effective_at=self.anchor-timedelta(days=1))
        self.mandate = dict(channel='fixture', merchant_reference='fixture-shenzhen',
            agreement_reference='fixture-agreement', evidence_hash='a'*64, executor='sage', automatic_retry=False)
        self.sequence = 0

    def paid(self):
        self.sequence += 1
        quote = create_quote(self.account, self.user, self.product.pk, 1, timezone.now()+timedelta(minutes=5),
                             channel='fixture', buyer_region='CN')
        order = create_order(self.account, self.user, {'quote_id':quote['id'], 'agreement_acceptance':'fixture-1'}, str(self.sequence))
        record_confirmed_payment(self.account, order['id'], channel='fixture', merchant_reference='fixture-shenzhen',
            transaction_reference=str(self.sequence), amount_minor=12345, currency='CNY', paid_at=timezone.now(), evidence_hash='b'*64)
        return order['id']

    def activate(self, order_id=None, **kwargs):
        return activate_subscription(self.account, order_id or self.paid(), activated_at=self.anchor,
            billing_timezone='Asia/Shanghai', calendar_rule_version='fixture-calendar-v1', mandate=self.mandate, **kwargs)

    def business_paid(self, resource_mode, billing_cycle='annual'):
        from backend.models import BillingAccount
        self.account = BillingAccount.objects.create(kind='business',name='fixture-business')
        Membership.objects.create(account=self.account,user=self.user,role='owner')
        self.product = ProductVersion.objects.create(code='fixture_business',version=1 if billing_cycle == 'annual' else 2,kind='subscription',
            audience='business',billing_cycle=billing_cycle,minimum_seats=2,unit_amount_minor=12345,
            credits_quantity='0' if resource_mode == 'shared_only' else '17.25',
            entitlements={'resource_mode':resource_mode},entitlement_version='fixture-1',credit_rule_version='fixture-1',
            terms_version='fixture-1',state='published',effective_at=self.anchor-timedelta(days=1))
        quote = create_quote(self.account,self.user,self.product.pk,2,self.anchor+timedelta(minutes=5),channel='fixture',buyer_region='CN')
        order = create_order(self.account,self.user,{'quote_id':quote['id'],'agreement_acceptance':'fixture-1'},'business-first')
        record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='business-paid' if billing_cycle == 'annual' else 'business-paid-monthly',amount_minor=24690,currency='CNY',paid_at=self.anchor,evidence_hash='b'*64)
        return order['id']

    def test_zero_credit_enterprise_has_no_next_grant_for_paid_periods(self):
        for cycle in ('annual','monthly'):
            with self.subTest(cycle=cycle):
                sub=self.activate(self.business_paid('shared_only',cycle))
                self.assertIsNone(sub['next_credit_grant_at'])
                client=Client();client.force_login(self.user)
                state=client.session;state['active_account_id']=str(self.account.pk);state.save()
                response=client.get('/api/billing/subscriptions',HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
                self.assertEqual(response.status_code,200)
                self.assertIsNone(response.json()['items'][0]['next_credit_grant_at'])

    def test_positive_historical_credit_grants_keep_anchor_after_cancellation(self):
        sub=self.activate(self.business_paid('seat_included'))
        # Withdrawing the current catalog must not replace the paid period terms.
        ProductVersion.objects.filter(pk=self.product.pk).update(state='withdrawn')
        self.now.return_value=month_boundary(self.anchor,1)
        cancel_subscription(self.account,self.user,sub['id'])
        current=list_subscriptions(self.account)[0]
        self.assertEqual(current['next_credit_grant_at'],month_boundary(self.anchor,2).isoformat())
        self.assertEqual(current['cancellation_state'],'processing')
        self.now.return_value=month_boundary(self.anchor,12)
        self.assertIsNone(list_subscriptions(self.account)[0]['next_credit_grant_at'])

    def test_unknown_period_credit_quantity_is_not_treated_as_zero(self):
        sub=self.activate(self.business_paid('shared_only'))
        period=SubscriptionPeriod.objects.get(subscription_id=sub['id'])
        for value in (None,True,'unknown','-1'):
            with self.subTest(value=value):
                SubscriptionPeriod.objects.filter(pk=period.pk).update(snapshot={**period.snapshot,'credits_quantity':value})
                with self.assertRaisesMessage(ValueError,'INVALID_CREDITS'):
                    list_subscriptions(self.account)

    def test_annual_refund_scope_uses_original_service_month_boundaries(self):
        from .billing import credit_refund_assessment
        sub = Subscription.objects.get(pk=self.activate()['id'])
        for instant, current, count in (
            (self.anchor, 0, 11),
            (month_boundary(self.anchor,1)-timedelta(microseconds=1),0,11),
            (month_boundary(self.anchor,1),1,10),
            (month_boundary(self.anchor,2),2,9),
            (month_boundary(self.anchor,11),11,0)):
            with self.subTest(instant=instant):
                self.now.return_value = instant
                value = credit_refund_assessment(self.account,sub.initial_order_id)
                self.assertEqual(value['status'],'scope_ready')
                self.assertIs(value['can_submit'],False)
                self.assertEqual(value['future_month_count'],count)
                self.assertEqual(value['current_service_month'],{
                    'starts_at':month_boundary(self.anchor,current).isoformat(),
                    'ends_at':month_boundary(self.anchor,current+1).isoformat()})
                self.assertEqual(value['future_service_months'],[{
                    'starts_at':month_boundary(self.anchor,i).isoformat(),
                    'ends_at':month_boundary(self.anchor,i+1).isoformat()} for i in range(current+1,12)])
                self.assertFalse({'reference_minor','paid_minor','mandate','evidence_hash','channel'} & value.keys())
        for instant, reason in ((self.anchor-timedelta(microseconds=1),'annual_service_not_started'),
                                (month_boundary(self.anchor,12),'annual_service_ended')):
            self.now.return_value = instant
            value=credit_refund_assessment(self.account,sub.initial_order_id)
            self.assertEqual(value['reason'],reason)
            self.assertNotIn('future_month_count',value)

    def test_annual_refund_scope_rejects_incomplete_or_complex_sources(self):
        from .billing import credit_refund_assessment
        from .models import Quote
        sub=Subscription.objects.get(pk=self.activate()['id'])
        period=sub.periods.get()
        order=sub.initial_order
        SubscriptionPeriod.objects.filter(pk=period.pk).update(granted_months=7)
        self.assertEqual(credit_refund_assessment(self.account,order.pk)['future_month_count'],11)
        Subscription.objects.filter(pk=sub.pk).update(billing_timezone='invalid-zone')
        self.assertEqual(credit_refund_assessment(self.account,order.pk)['reason'],'annual_source_review_required')
        Subscription.objects.filter(pk=sub.pk).update(billing_timezone='Asia/Shanghai')
        for change in ({'ends_at':period.ends_at-timedelta(days=1)},
                       {'cycle_index':1}, {'snapshot':{**period.snapshot,'offset_minor':1}}):
            with self.subTest(change=change):
                SubscriptionPeriod.objects.filter(pk=period.pk).update(**change)
                self.assertEqual(credit_refund_assessment(self.account,order.pk)['reason'],'annual_source_review_required')
                SubscriptionPeriod.objects.filter(pk=period.pk).update(ends_at=period.ends_at,
                    cycle_index=period.cycle_index,snapshot=period.snapshot)
        for change in ({'terms_document_digest':'z'*64},{'terms_version':''},{'code':'not-the-source'}):
            with self.subTest(change=change):
                altered={**order.snapshot,**change}
                for model, pk in ((Order,order.pk),(Quote,order.quote_id),(SubscriptionPeriod,period.pk)):
                    model.objects.filter(pk=pk).update(snapshot=altered)
                self.assertEqual(credit_refund_assessment(self.account,order.pk)['reason'],'annual_source_review_required')
        altered={**order.snapshot,'offset_minor':1}
        for model, pk in ((Order,order.pk),(Quote,order.quote_id),(SubscriptionPeriod,period.pk)):
            model.objects.filter(pk=pk).update(snapshot=altered)
        self.assertEqual(credit_refund_assessment(self.account,order.pk)['reason'],'annual_source_review_required')

    def test_annual_refund_scope_preserves_digest_and_refuses_multiple_periods(self):
        from .billing import credit_refund_assessment
        from .models import Quote
        sub=Subscription.objects.get(pk=self.activate()['id'])
        order=sub.initial_order
        period=sub.periods.get()
        snapshot={**order.snapshot,'terms_document_digest':'e'*64}
        for model, pk in ((Order,order.pk),(Quote,order.quote_id),(SubscriptionPeriod,period.pk)):
            model.objects.filter(pk=pk).update(snapshot=snapshot)
        self.assertEqual(credit_refund_assessment(self.account,order.pk)['status'],'scope_ready')
        self.assertEqual(Order.objects.get(pk=order.pk).snapshot,snapshot)
        second=Order.objects.get(pk=self.paid())
        SubscriptionPeriod.objects.create(subscription=sub,order=second,cycle_index=1,
            starts_at=period.ends_at,ends_at=month_boundary(self.anchor,24),snapshot=second.snapshot)
        self.assertEqual(credit_refund_assessment(self.account,order.pk)['reason'],'annual_source_review_required')
        self.assertEqual(credit_refund_assessment(self.account,second.pk)['reason'],'payment_not_fulfilled')

    def test_annual_refund_scope_http_is_account_scoped_and_read_only(self):
        from django.test.utils import CaptureQueriesContext
        sub=Subscription.objects.get(pk=self.activate()['id'])
        path=f'/api/billing/orders/{sub.initial_order_id}/refund-assessment'
        client=Client()
        self.assertEqual(client.get(path).status_code,401)
        client.force_login(self.user)
        state=client.session;state['active_account_id']=str(self.account.pk);state.save()
        with CaptureQueriesContext(connection) as queries:
            first=client.get(path,HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
            second=client.get(path,HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json()['status'],'scope_ready')
        self.assertEqual(first.json(),second.json())
        for query in queries:
            self.assertNotRegex(query['sql'].lstrip().upper(),r'^(INSERT|UPDATE|DELETE)\b')
        self.assertEqual(client.post(path).status_code,405)
        Membership.objects.filter(account=self.account,user=self.user).update(role='member')
        self.assertEqual(client.get(path,HTTP_X_BILLING_ACCOUNT=str(self.account.pk)).status_code,403)
        client.force_login(self.other_user)
        state=client.session;state['active_account_id']=str(self.other.pk);state.save()
        self.assertEqual(client.get(path,HTTP_X_BILLING_ACCOUNT=str(self.other.pk)).status_code,404)

    def test_business_teams_monthly_grants_are_bound_to_paid_seats(self):
        from .models import PaidSeat
        order = self.business_paid('seat_included')
        result = self.activate(order)
        self.assertEqual(self.activate(order)['id'],result['id'])
        self.assertEqual(PaidSeat.objects.filter(subscription_id=result['id']).count(),2)
        self.assertEqual(CreditBatch.objects.count(),2)
        self.assertEqual(CreditBatch.objects.filter(seat__isnull=True).count(),0)
        self.assertEqual(credits_summary(self.account)['available'],'34.5')
        self.now.return_value = month_boundary(self.anchor,1)
        grant_due_credits(self.account)
        grant_due_credits(self.account)
        self.assertEqual(CreditBatch.objects.count(),4)
        self.assertEqual(credits_summary(self.account)['available'],'34.5')

    def test_enterprise_seats_have_no_included_credit_grants(self):
        from .models import PaidSeat
        result = self.activate(self.business_paid('shared_only'))
        self.assertEqual(PaidSeat.objects.filter(subscription_id=result['id']).count(),2)
        self.assertEqual(CreditBatch.objects.count(),0)

    def test_business_unknown_resource_mode_does_not_deliver(self):
        order = self.business_paid('seat_included')
        # Simulate a legacy corrupt snapshot, bypassing the model deliberately:
        # the normal quote path now rejects this before any payment can start.
        record = Order.objects.get(pk=order)
        snapshot = dict(record.snapshot)
        snapshot['entitlements'] = {'resource_mode': 'unknown'}
        Order.objects.filter(pk=order).update(snapshot=snapshot)
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            self.activate(order)
        self.assertEqual(Subscription.objects.count(),0)

    def test_seat_reassignment_keeps_credits_and_rejects_stale_changes(self):
        from .subscriptions import assign_seat
        from .models import PaidSeat, SeatChange
        from django.contrib.auth import get_user_model
        self.activate(self.business_paid('seat_included'))
        first = Membership.objects.get(account=self.account,user=self.user)
        second = Membership.objects.create(account=self.account,user=get_user_model().objects.create_user(username='seat-two'),role='member')
        seat = PaidSeat.objects.order_by('position').first()
        batches = list(CreditBatch.objects.values_list('id','initial_quantity','expires_at'))
        result = assign_seat(self.account,self.user,seat.pk,first.pk,expected_version=0,request_key='assign-first')
        self.assertEqual(result,assign_seat(self.account,self.user,seat.pk,first.pk,expected_version=0,request_key='assign-first'))
        result = assign_seat(self.account,self.user,seat.pk,second.pk,expected_version=1,request_key='replace')
        self.assertEqual(result['version'],2)
        with self.assertRaisesRegex(ValueError,'SEAT_CHANGED'):
            assign_seat(self.account,self.user,seat.pk,first.pk,expected_version=1,request_key='stale')
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
            assign_seat(self.account,self.user,seat.pk,first.pk,expected_version=1,request_key='replace')
        self.assertEqual(list(CreditBatch.objects.values_list('id','initial_quantity','expires_at')),batches)
        self.assertEqual(SeatChange.objects.count(),2)
        change = SeatChange.objects.order_by('-id').first()
        self.assertEqual((change.before_membership_id,change.after_membership_id),(first.pk,second.pk))

    def test_seat_assignment_rejects_unauthorized_duplicate_and_foreign_member(self):
        from .subscriptions import assign_seat
        from .models import PaidSeat
        self.activate(self.business_paid('seat_included'))
        member = Membership.objects.get(account=self.account,user=self.user)
        seats = list(PaidSeat.objects.order_by('position'))
        with self.assertRaisesRegex(ValueError,'FORBIDDEN'):
            assign_seat(self.account,self.other_user,seats[0].pk,member.pk,expected_version=0,request_key='unauthorized')
        foreign = Membership.objects.get(account=self.other,user=self.other_user)
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            assign_seat(self.account,self.user,seats[0].pk,foreign.pk,expected_version=0,request_key='foreign')
        assign_seat(self.account,self.user,seats[0].pk,member.pk,expected_version=0,request_key='first')
        with self.assertRaisesRegex(ValueError,'MEMBER_ALREADY_SEATED'):
            assign_seat(self.account,self.user,seats[1].pk,member.pk,expected_version=0,request_key='duplicate')

    def test_expired_subscription_allows_unassignment_not_replacement(self):
        from .subscriptions import assign_seat
        from .models import PaidSeat
        self.activate(self.business_paid('shared_only'))
        member = Membership.objects.get(account=self.account,user=self.user)
        seat = PaidSeat.objects.first()
        assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=0,request_key='first')
        self.now.return_value = month_boundary(self.anchor,12)
        assign_seat(self.account,self.user,seat.pk,None,expected_version=1,request_key='remove')
        with self.assertRaisesRegex(ValueError,'SUBSCRIPTION_EXPIRED'):
            assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=2,request_key='again')
        self.assertEqual(PaidSeat.objects.count(),2)

    def test_seat_change_audit_failure_rolls_back_assignment(self):
        from .subscriptions import assign_seat
        from .models import PaidSeat, SeatChange
        self.activate(self.business_paid('seat_included'))
        member = Membership.objects.get(account=self.account,user=self.user)
        seat = PaidSeat.objects.first()
        with patch('backend.commerce.models.SeatChange.save',side_effect=RuntimeError('audit-failed')):
            with self.assertRaisesRegex(RuntimeError,'audit-failed'):
                assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=0,request_key='assign')
        seat.refresh_from_db()
        self.assertIsNone(seat.membership_id)
        self.assertEqual(seat.version,0)
        self.assertEqual(SeatChange.objects.count(),0)

    def business_member(self, mode='seat_included'):
        from .models import PaidSeat
        from .subscriptions import assign_seat
        self.activate(self.business_paid(mode))
        member = Membership.objects.get(account=self.account,user=self.user)
        seat = PaidSeat.objects.order_by('position').first()
        assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=0,request_key='member-first')
        grant_credits(self.account,'purchased','shared-fixture','10',self.anchor+timedelta(days=2))
        return member

    def test_enterprise_seat_and_shared_limit_notifications_follow_versions(self):
        from django.db import transaction
        from .billing import set_shared_limit, notifications_page
        from .subscriptions import assign_seat
        from .models import PaidSeat, BillingNotification
        member = self.business_member('shared_only')
        seat = PaidSeat.objects.get(membership=member)
        self.assertEqual(BillingNotification.objects.filter(type='seat_assignment_changed').count(),1)
        with self.assertRaisesRegex(ValueError,'ROLLBACK'):
            with transaction.atomic():
                assign_seat(self.account,self.user,seat.pk,None,expected_version=1,request_key='clear')
                set_shared_limit(self.account,self.user,member.pk,'2',expected_version=0,request_key='cap')
                raise ValueError('ROLLBACK')
        self.assertEqual(BillingNotification.objects.filter(type='seat_assignment_changed').count(),1)
        self.assertFalse(BillingNotification.objects.filter(type='shared_limit_changed').exists())
        assign_seat(self.account,self.user,seat.pk,None,expected_version=1,request_key='clear')
        assign_seat(self.account,self.user,seat.pk,None,expected_version=1,request_key='clear')
        set_shared_limit(self.account,self.user,member.pk,'2',expected_version=0,request_key='cap')
        set_shared_limit(self.account,self.user,member.pk,'2',expected_version=0,request_key='cap')
        set_shared_limit(self.account,self.user,member.pk,None,expected_version=1,request_key='uncap')
        rows = [row for row in notifications_page(self.account)['items'] if row['type'] in ('seat_assignment_changed','shared_limit_changed')]
        self.assertEqual(len(rows),4)
        self.assertTrue(all(row['amount_minor'] is None and row['currency'] is None for row in rows))
        self.assertEqual(BillingNotification.objects.filter(type='seat_assignment_changed',subscription=seat.subscription).count(),2)

    def test_business_shared_cap_keeps_own_seat_credits_usable(self):
        from .billing import set_shared_limit, reserve_credits, settle_credits
        member = self.business_member()
        set_shared_limit(self.account,self.user,member.pk,'3',expected_version=0,request_key='cap')
        with self.assertRaisesRegex(ValueError,'INSUFFICIENT_CREDITS'):
            reserve_credits(self.account,'too-much','21',membership_id=member.pk)
        task = reserve_credits(self.account,'within','20',membership_id=member.pk)
        entries = CreditEntry.objects.filter(reservation_id=task['id'],kind='reserve')
        self.assertEqual(entries.get(batch__source='purchased').quantity,'3')
        self.assertEqual(entries.get(batch__source='subscription').quantity,'17')
        settle_credits(self.account,task['id'],'1')
        reserve_credits(self.account,'after-release','2',membership_id=member.pk)
        with self.assertRaisesRegex(ValueError,'INSUFFICIENT_CREDITS'):
            reserve_credits(self.account,'other-seat-forbidden','18',membership_id=member.pk)

    def test_shared_limit_changes_are_versioned_and_do_not_reset_usage(self):
        from .billing import set_shared_limit, reserve_credits, settle_credits
        member = self.business_member('shared_only')
        initial = set_shared_limit(self.account,self.user,member.pk,'5',expected_version=0,request_key='cap')
        self.assertEqual(initial,set_shared_limit(self.account,self.user,member.pk,'5',expected_version=0,request_key='cap'))
        task = reserve_credits(self.account,'use','4',membership_id=member.pk)
        settle_credits(self.account,task['id'],'4')
        set_shared_limit(self.account,self.user,member.pk,'2',expected_version=1,request_key='reduce')
        with self.assertRaisesRegex(ValueError,'INSUFFICIENT_CREDITS'):
            reserve_credits(self.account,'over-cap','1',membership_id=member.pk)
        with self.assertRaisesRegex(ValueError,'LIMIT_CHANGED'):
            set_shared_limit(self.account,self.user,member.pk,None,expected_version=1,request_key='stale')
        with self.assertRaisesRegex(ValueError,'FORBIDDEN'):
            set_shared_limit(self.account,self.other_user,member.pk,'5',expected_version=2,request_key='foreign')

    def test_shared_budget_rejects_wrong_member_and_cross_month_settlement(self):
        from .billing import reserve_credits, settle_credits
        member = self.business_member('shared_only')
        foreign = Membership.objects.get(account=self.other,user=self.other_user)
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            reserve_credits(self.account,'foreign','1',membership_id=foreign.pk)
        grant_credits(self.account,'purchased','long-shared','10',self.anchor+timedelta(days=100))
        self.now.return_value = self.anchor+timedelta(days=3)
        task = reserve_credits(self.account,'cross-period','1',membership_id=member.pk)
        self.now.return_value = month_boundary(self.anchor,1)
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            settle_credits(self.account,task['id'],'1')

    def test_shared_limit_resets_usage_not_configuration_each_service_month(self):
        from .billing import set_shared_limit, reserve_credits, settle_credits
        member = self.business_member('shared_only')
        grant_credits(self.account,'purchased','long-shared','10',self.anchor+timedelta(days=100))
        set_shared_limit(self.account,self.user,member.pk,'2',expected_version=0,request_key='cap')
        task = reserve_credits(self.account,'first-month','2',membership_id=member.pk)
        settle_credits(self.account,task['id'],'2')
        with self.assertRaisesRegex(ValueError,'INSUFFICIENT_CREDITS'):
            reserve_credits(self.account,'over','1',membership_id=member.pk)
        self.now.return_value = month_boundary(self.anchor,1)
        reserve_credits(self.account,'second-month','2',membership_id=member.pk)
        with self.assertRaisesRegex(ValueError,'INSUFFICIENT_CREDITS'):
            reserve_credits(self.account,'second-over','1',membership_id=member.pk)

    def test_organization_summary_reads_current_seat_limit_and_reserved_usage(self):
        from .billing import set_shared_limit, reserve_credits
        from .subscriptions import organization_summary
        member = self.business_member('shared_only')
        set_shared_limit(self.account,self.user,member.pk,'5',expected_version=0,request_key='cap')
        reserve_credits(self.account,'read-reserved','2',membership_id=member.pk)
        result = organization_summary(self.account)
        self.assertEqual((result['seat_count'],result['assigned_seats']),(2,1))
        row = result['members'][0]
        self.assertEqual((row['shared_limit'],row['shared_used'],row['shared_reserved']),('5','0','2'))
        self.assertFalse(result['can_edit'])

    def test_business_renewal_preserves_seat_quantity_and_original_slots(self):
        from .subscriptions import prepare_renewal_attempt, fulfill_renewal_attempt
        from .billing import claim_payment_dispatch
        from .models import PaidSeat
        result = self.activate(self.business_paid('seat_included'))
        seats = set(PaidSeat.objects.values_list('id',flat=True))
        end = month_boundary(self.anchor,12)
        self.now.return_value = end-timedelta(hours=1)
        attempt = prepare_renewal_attempt(self.account,result['id'],cycle_index=1,
            dispatch_not_before=end-timedelta(days=1),expires_at=end)
        row = PaymentAttempt.objects.get(pk=attempt['id'])
        self.assertEqual(row.order.snapshot['seat_quantity'],2)
        self.assertEqual(row.order.amount_minor,24690)
        claim_payment_dispatch(self.account,attempt['id'])
        record_confirmed_payment(self.account,row.order_id,channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='business-renewal',amount_minor=24690,currency='CNY',paid_at=timezone.now(),evidence_hash='c'*64)
        fulfill_renewal_attempt(self.account,attempt['id'])
        self.now.return_value = end
        grant_due_credits(self.account)
        self.assertEqual(set(PaidSeat.objects.values_list('id',flat=True)),seats)
        self.assertEqual(CreditBatch.objects.filter(expires_at__gt=end).count(),2)
        self.assertEqual(credits_summary(self.account)['available'],'34.5')

    def test_requires_accepted_payment_and_valid_mandate(self):
        order_id = self.paid()
        for mandate in ({}, {**self.mandate,'automatic_retry':True}, {**self.mandate,'channel':'other'},
                        {**self.mandate,'merchant_reference':'other'}):
            with self.assertRaises(ValueError):
                activate_subscription(self.account, order_id, activated_at=self.anchor,
                    billing_timezone='Asia/Shanghai', calendar_rule_version='fixture', mandate=mandate)
        with self.assertRaisesRegex(ValueError, 'NOT_FOUND'):
            activate_subscription(self.other, order_id, activated_at=self.anchor,
                billing_timezone='Asia/Shanghai', calendar_rule_version='fixture', mandate=self.mandate)
        PaymentFact.objects.all().delete()
        with self.assertRaisesRegex(ValueError, 'PAYMENT_NOT_CONFIRMED'):
            self.activate(order_id)
        self.assertEqual(Subscription.objects.count(), 0)

    def test_annual_grants_only_current_month_and_replay_does_not_reset(self):
        order_id = self.paid()
        result = self.activate(order_id)
        self.assertEqual(self.activate(order_id), result)
        self.assertEqual((Subscription.objects.count(),SubscriptionPeriod.objects.count(),CreditBatch.objects.count()),(1,1,1))
        self.assertEqual(credits_summary(self.account)['available'],'17.25')
        self.assertEqual(Order.objects.get(pk=order_id).status,'fulfilled')
        self.assertNotIn('mandate', result)
        self.assertEqual(result['status'],'active')
        self.assertEqual(result['next_credit_grant_at'],month_boundary(self.anchor,1).isoformat())

    def test_calendar_roundtrip_month_end_leap_year_and_no_thirteenth_grant(self):
        self.activate()
        for month in range(1,12):
            self.now.return_value = month_boundary(self.anchor,month)
            self.assertEqual(grant_due_credits(self.account)['created_batches'],1)
            self.assertEqual(grant_due_credits(self.account)['created_batches'],0)
            batch = CreditBatch.objects.get(service_month=month)
            self.assertEqual(batch.expires_at,month_boundary(self.anchor,month+1))
            self.assertEqual(credits_summary(self.account)['available'],'17.25')
        self.now.return_value = month_boundary(self.anchor,12)
        self.assertEqual(grant_due_credits(self.account)['created_batches'],0)
        self.assertEqual(credits_summary(self.account)['available'],'0')
        self.assertEqual(CreditBatch.objects.count(),12)
        self.assertEqual((Order.objects.count(),PaymentFact.objects.count()),(1,1))

    def test_cancel_retains_annual_grants_and_requires_channel_confirmation(self):
        sub = self.activate()
        result = cancel_subscription(self.account,self.user,sub['id'])
        self.assertFalse(result['auto_renew'])
        self.assertEqual(result['cancellation_state'],'processing')
        self.assertEqual(result,cancel_subscription(self.account,self.user,sub['id']))
        self.now.return_value = month_boundary(self.anchor,1)
        grant_due_credits(self.account)
        self.assertEqual(credits_summary(self.account)['available'],'17.25')
        with self.assertRaises(ValueError):
            confirm_cancellation(self.account,sub['id'],agreement_reference='wrong',evidence_hash='c'*64)
        confirmed = confirm_cancellation(self.account,sub['id'],agreement_reference='fixture-agreement',evidence_hash='c'*64)
        self.assertEqual(confirmed['cancellation_state'],'completed')
        self.assertEqual(confirmed,confirm_cancellation(self.account,sub['id'],agreement_reference='fixture-agreement',evidence_hash='c'*64))
        self.assertEqual(AuditEvent.objects.filter(action='subscription.cancel_requested').count(),1)

    def test_early_paid_renewal_is_not_spendable_until_its_start(self):
        sub = self.activate()
        self.now.return_value = month_boundary(self.anchor,11)
        grant_due_credits(self.account)
        order_id = self.paid()
        result = append_paid_period(self.account,sub['id'],order_id,cycle_index=1)
        self.assertEqual(credits_summary(self.account)['available'],'17.25')
        self.assertEqual(CreditBatch.objects.filter(subscription_period__order_id=order_id).count(),0)
        self.assertEqual(result,append_paid_period(self.account,sub['id'],order_id,cycle_index=1))
        self.now.return_value = month_boundary(self.anchor,12)
        grant_due_credits(self.account)
        self.assertEqual(credits_summary(self.account)['available'],'17.25')
        self.assertEqual(CreditBatch.objects.filter(subscription_period__order_id=order_id).count(),1)

    def test_cancel_does_not_swallow_already_inflight_paid_period(self):
        sub = self.activate()
        self.now.return_value = month_boundary(self.anchor,11)
        order_id = self.paid()
        cancel_subscription(self.account,self.user,sub['id'])
        append_paid_period(self.account,sub['id'],order_id,cycle_index=1)
        self.now.return_value = month_boundary(self.anchor,12)
        grant_due_credits(self.account)
        self.assertEqual(credits_summary(self.account)['available'],'17.25')

    def test_missed_months_expire_not_rollover_and_original_snapshot_survives(self):
        self.activate()
        ProductVersion.objects.filter(pk=self.product.pk).update(credits_quantity='999',state='retired')
        self.now.return_value = month_boundary(self.anchor,5)
        grant_due_credits(self.account)
        self.assertEqual(credits_summary(self.account)['available'],'17.25')
        self.assertEqual(credits_summary(self.account)['expired'],'86.25')
        self.assertEqual(CreditBatch.objects.count(),6)

    def test_overlapping_subscription_and_duplicate_period_refuse_without_losing_money(self):
        sub = self.activate()
        second = self.paid()
        with self.assertRaisesRegex(ValueError,'SUBSCRIPTION_CONFLICT'):
            self.activate(second)
        append_paid_period(self.account,sub['id'],second,cycle_index=1)
        third = self.paid()
        with self.assertRaisesRegex(ValueError,'SUBSCRIPTION_CONFLICT'):
            append_paid_period(self.account,sub['id'],third,cycle_index=1)
        self.assertEqual(PaymentFact.objects.count(),3)
        self.assertEqual(Order.objects.get(pk=third).status,'paid_pending_entitlement')

    def test_late_renewal_unknown_policy_rejected_and_purchase_balance_retained(self):
        ProductVersion.objects.filter(pk=self.product.pk).update(billing_cycle='monthly')
        sub = self.activate()
        grant_credits(self.account,'purchased','independent','2',month_boundary(self.anchor,12))
        self.now.return_value = month_boundary(self.anchor,1)+timedelta(seconds=1)
        late = self.paid()
        with self.assertRaisesRegex(ValueError,'LATE_RENEWAL_REQUIRES_ACTIVATION'):
            append_paid_period(self.account,sub['id'],late,cycle_index=1)
        grant_due_credits(self.account)
        self.assertEqual(credits_summary(self.account)['available'],'2')
        self.assertEqual(list_subscriptions(self.account)[0]['status'],'expired')

    def test_rollback_delivery_keeps_money_and_can_repair(self):
        order_id = self.paid()
        with patch('backend.commerce.subscriptions.grant_credits',side_effect=RuntimeError('fixture failure')):
            with self.assertRaises(RuntimeError): self.activate(order_id)
        self.assertEqual(Subscription.objects.count(),0)
        self.assertEqual(PaymentFact.objects.count(),1)
        self.activate(order_id)
        self.assertEqual(CreditEntry.objects.count(),1)

    def test_invalid_time_inputs_and_changed_replay_are_rejected(self):
        order_id = self.paid()
        for moment, zone in ((self.anchor.replace(tzinfo=None),'Asia/Shanghai'),(self.anchor,'Not/AZone'),
                            (self.anchor+timedelta(days=1),'Asia/Shanghai')):
            with self.assertRaises(ValueError):
                activate_subscription(self.account,order_id,activated_at=moment,billing_timezone=zone,
                                      calendar_rule_version='fixture',mandate=self.mandate)
        self.activate(order_id)
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
            activate_subscription(self.account,order_id,activated_at=self.anchor,billing_timezone='UTC',
                calendar_rule_version='fixture-calendar-v1',mandate=self.mandate)

    def test_read_cancel_api_csrf_account_isolation_and_empty_list(self):
        sub = self.activate()
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get('/api/billing/subscriptions').status_code,401)
        client.force_login(self.user)
        token = client.get('/api/auth/session').json()['csrf_token']
        self.assertEqual(client.get('/api/billing/subscriptions').json()['items'][0]['id'],sub['id'])
        path = '/api/billing/subscriptions/'+sub['id']+'/cancel'
        self.assertEqual(client.post(path,'{}',content_type='application/json').status_code,403)
        self.assertEqual(client.post(path,'{}',content_type='application/json',HTTP_X_CSRFTOKEN=token).status_code,409)
        response = client.post(path,'{}',content_type='application/json',HTTP_X_CSRFTOKEN=token,HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['subscription']['cancellation_state'],'processing')
        self.assertEqual(list_subscriptions(self.other),[])
        with self.assertRaisesRegex(ValueError,'FORBIDDEN'):
            cancel_subscription(self.account,self.other_user,sub['id'])
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            cancel_subscription(self.other,self.other_user,sub['id'])


@override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
class OrganizationHistoryTests(TransactionTestCase):
    """Existing organization audit rows only, never a management write endpoint."""

    def setUp(self):
        from backend.models import LoginIdentity
        from .models import PaidSeat
        SubscriptionTests.setUp(self)
        self.personal = self.account
        order_id = SubscriptionTests.business_paid(self, 'shared_only')
        SubscriptionTests.activate(self, order_id)
        self.owner = Membership.objects.get(account=self.account,user=self.user)
        self.member = Membership.objects.create(account=self.account,user=self.other_user,role='member')
        self.seat = PaidSeat.objects.filter(subscription__account=self.account).order_by('position').first()
        LoginIdentity.objects.create(user=self.user,method='email',identifier='finance-history@example.invalid')

    def history(self, **options):
        from .subscriptions import organization_history
        return organization_history(self.account, **options)

    def seed_changes(self):
        from .subscriptions import assign_seat
        from .billing import set_shared_limit
        assign_seat(self.account,self.user,self.seat.pk,self.owner.pk,expected_version=0,request_key='private-seat-one')
        assign_seat(self.account,self.user,self.seat.pk,self.member.pk,expected_version=1,request_key='private-seat-two')
        set_shared_limit(self.account,self.user,self.member.pk,'9007199254740993.125',expected_version=0,request_key='private-limit-one')
        set_shared_limit(self.account,self.user,self.member.pk,'0',expected_version=1,request_key='private-limit-two')

    def test_history_recovers_versions_before_after_and_exact_quantities(self):
        self.seed_changes()
        Membership.objects.filter(pk=self.owner.pk).update(is_active=False)
        result = self.history()
        self.assertEqual(result['account_id'],str(self.account.pk))
        self.assertEqual(result['total'],4)
        self.assertIsNone(result['next_offset'])
        seats = {row['version']:row for row in result['items'] if row['type'] == 'seat_assignment'}
        limits = {row['version']:row for row in result['items'] if row['type'] == 'shared_limit'}
        self.assertIsNone(seats[1]['before_membership_id'])
        self.assertEqual(seats[2]['before_membership_id'],self.owner.pk)
        self.assertEqual(seats[2]['after_membership_id'],self.member.pk)
        self.assertEqual(seats[2]['before_version'],1)
        self.assertEqual(seats[2]['seat_position'],1)
        self.assertEqual(limits[1]['quantity'],'9007199254740993.125')
        self.assertTrue(limits[1]['previous_known'])
        self.assertIsNone(limits[1]['previous_quantity'])
        self.assertEqual(limits[2]['previous_quantity'],'9007199254740993.125')
        self.assertEqual(limits[2]['quantity'],'0')
        for item in result['items']:
            self.assertEqual(item['actor_masked_identifier'],'f***@example.invalid')
        rendered = json.dumps(result)
        for excluded in ('finance-history', 'private-', 'request_key', 'actor_id', 'evidence_hash', 'agreement_reference'):
            self.assertNotIn(excluded,rendered)

    def test_mixed_history_is_stably_paginated_without_gaps_at_identical_times(self):
        from .models import SharedLimitChange
        from .subscriptions import assign_seat
        assign_seat(self.account,self.user,self.seat.pk,self.owner.pk,expected_version=0,request_key='one-seat')
        SharedLimitChange.objects.bulk_create([SharedLimitChange(membership=self.member,
            actor=self.user,quantity=str(number),version=number,request_key=f'page-{number}')
            for number in range(1,52)])
        first,second = self.history(),self.history(offset=50)
        self.assertEqual(first['total'],52)
        self.assertEqual(first['next_offset'],50)
        self.assertEqual(len(first['items']),50)
        self.assertEqual(len(second['items']),2)
        self.assertIsNone(second['next_offset'])
        identifiers = [item['id'] for item in first['items']+second['items']]
        self.assertEqual(len(set(identifiers)),52)
        self.assertEqual(self.history()['items'],first['items'])
        self.assertEqual(self.history(offset=999999999)['items'],[])

    def test_missing_previous_limit_is_unknown_not_unlimited(self):
        from .models import SharedLimitChange
        SharedLimitChange.objects.create(membership=self.member,actor=self.user,
            quantity='0',version=3,request_key='missing-earlier-version')
        item = self.history()['items'][0]
        self.assertFalse(item['previous_known'])
        self.assertIsNone(item['previous_quantity'])
        self.assertEqual(item['quantity'],'0')

    def test_actor_phone_is_masked_and_missing_identity_does_not_expose_username(self):
        from backend.models import LoginIdentity
        self.seed_changes()
        LoginIdentity.objects.create(user=self.user,method='phone',identifier='13800138000')
        self.assertEqual(self.history()['items'][0]['actor_masked_identifier'],'138****8000')
        LoginIdentity.objects.filter(user=self.user).delete()
        self.assertEqual(self.history()['items'][0]['actor_masked_identifier'],'')

    def test_other_business_has_no_history_and_personal_account_is_rejected(self):
        from backend.models import BillingAccount
        from .subscriptions import organization_history
        self.seed_changes()
        other_business = BillingAccount.objects.create(kind='business',name='Other history fixture')
        self.assertEqual(organization_history(other_business)['items'],[])
        with self.assertRaisesRegex(ValueError,'FORBIDDEN'):
            organization_history(self.personal)
        for offset in (-1,True,1.5,'1',1000000000):
            with self.subTest(offset=offset),self.assertRaisesRegex(ValueError,'INVALID_REQUEST'):
                self.history(offset=offset)

    def test_financial_api_checks_account_role_and_strict_single_ascii_offset(self):
        self.seed_changes()
        client = Client()
        path = '/api/billing/organization-history'
        self.assertEqual(client.get(path).status_code,401)
        client.force_login(self.user)
        session = client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        self.assertEqual(client.get(path).json()['total'],4)
        for query in ('?offset=-1','?offset=1.5','?offset=+1','?offset=１２',
                      '?offset=1&offset=2','?offset=','?offset=1000000000','?unexpected=1'):
            with self.subTest(query=query):
                self.assertEqual(client.get(path+query).status_code,422)
        self.assertEqual(client.get(path,HTTP_X_BILLING_ACCOUNT=str(self.other.pk)).status_code,409)
        client.force_login(self.other_user)
        session = client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        self.assertEqual(client.get(path).status_code,403)
        Membership.objects.filter(pk=self.member.pk).update(role='billing_admin')
        self.assertEqual(client.get(path).status_code,200)
        self.assertEqual(client.post(path, '{}', content_type='application/json').status_code,405)

    def test_reads_are_repeatable_and_do_not_write_or_lock_rows(self):
        from django.test.utils import CaptureQueriesContext
        self.seed_changes()
        with CaptureQueriesContext(connection) as queries:
            first,second = self.history(),self.history()
        self.assertEqual(first,second)
        self.assertTrue(queries.captured_queries)
        for row in queries.captured_queries:
            self.assertRegex(row['sql'],r'^\(?SELECT\b')
            self.assertNotRegex(row['sql'],r'(?i);|\b(?:INSERT|UPDATE|DELETE|INTO|LOCK)\b')


@override_settings(BILLING_SALES_ENABLED=True, BILLING_RENEWAL_CHARGING_ENABLED=True,
                   BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
class SubscriptionConcurrencyTests(TransactionTestCase):
    setUp = SubscriptionTests.setUp
    paid = SubscriptionTests.paid
    activate = SubscriptionTests.activate
    prepare = RenewalIntentTests.prepare
    failed_renewal = RenewalIntentTests.failed_renewal
    recover = RenewalIntentTests.recover
    business_paid = SubscriptionTests.business_paid
    business_member = SubscriptionTests.business_member
    topup_fixture = RenewalIntentTests.topup_fixture
    def parallel(self, operations):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row locks required')
        barrier = Barrier(len(operations))
        def run(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operation()
            except ValueError as exc:
                return str(exc)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            return list(pool.map(run,operations))

    def test_parallel_activation_grant_and_cancel(self):
        order_id = self.paid()
        results = self.parallel([lambda:self.activate(order_id)]*2)
        self.assertEqual(results[0],results[1])
        self.assertEqual(Subscription.objects.count(),1)
        self.now.return_value = month_boundary(self.anchor,1)
        self.parallel([lambda:grant_due_credits(self.account)]*2)
        self.assertEqual(CreditBatch.objects.count(),2)
        self.parallel([lambda:cancel_subscription(self.account,self.user,results[0]['id'])]*2)
        self.assertEqual(AuditEvent.objects.filter(action='subscription.cancel_requested').count(),1)

    def test_parallel_topup_consents_have_one_version_winner(self):
        from .subscriptions import record_topup_authorization
        from .models import TopupAuthorization
        args = self.topup_fixture()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        def record(key):
            return record_topup_authorization(self.account,self.user,**{**args,'request_key':key,
                'mandate':{**args['mandate'],'agreement_reference':key}})
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            results = self.parallel([lambda:record('one'),lambda:record('two')])
        self.assertEqual(sum(isinstance(result,dict) for result in results),1)
        self.assertIn('AUTHORIZATION_VERSION_CHANGED',results)
        self.assertEqual(TopupAuthorization.objects.count(),1)
        self.assertEqual(Order.objects.count(),1)

    def test_parallel_topup_budget_reserves_only_one_inflight_intent(self):
        from .subscriptions import record_topup_authorization, prepare_topup_attempt
        from .models import TopupBudget
        args = self.topup_fixture()
        channels = {'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}}
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            auth = record_topup_authorization(self.account,self.user,**args)
            def prepare(key):
                return prepare_topup_attempt(self.account,auth['id'],trigger_key=key,
                    starts_at=self.anchor,ends_at=self.anchor+timedelta(days=30),policy_version='fixture-window',
                    expires_at=self.anchor+timedelta(hours=1))
            results = self.parallel([lambda:prepare('one'),lambda:prepare('two')])
        self.assertEqual(sum(isinstance(result,dict) for result in results),1)
        self.assertIn('TOPUP_PENDING',results)
        self.assertEqual(TopupBudget.objects.count(),1)
        self.assertEqual(Order.objects.count(),2)

    def test_parallel_authorization_change_has_one_version_winner(self):
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization
        from .models import AuthorizationChange
        method = register_verified_payment_method(self.account,channel='fixture',merchant_reference='fixture-shenzhen',
            fingerprint='c'*64,evidence_hash='d'*64)
        sub = self.activate(payment_method_id=method)
        def replace(key):
            return replace_subscription_authorization(self.account,self.user,sub['id'],payment_method_id=method,
                mandate={**self.mandate,'agreement_reference':key},expected_version=0,request_key=key)
        results = self.parallel([lambda:replace('one'), lambda:replace('two')])
        self.assertEqual(sum(isinstance(result,dict) for result in results),1)
        self.assertIn('AUTHORIZATION_VERSION_CHANGED',results)
        self.assertEqual(AuthorizationChange.objects.count(),1)
        self.assertEqual(Subscription.objects.get(pk=sub['id']).authorization_version,1)

    def test_parallel_cross_channel_change_has_one_winner_without_new_orders(self):
        from .subscriptions import register_verified_payment_method, replace_subscription_authorization
        from .models import AuthorizationChange
        sub = self.activate()
        channels = {**FIXTURE_CHANNELS,'replacement':dict(FIXTURE_CHANNELS['fixture'])}
        method = register_verified_payment_method(self.account,channel='replacement',merchant_reference='fixture-shenzhen',fingerprint='c'*64,evidence_hash='d'*64)
        def replace(key):
            return replace_subscription_authorization(self.account,self.user,sub['id'],payment_method_id=method,
                mandate={**self.mandate,'channel':'replacement','agreement_reference':key},expected_version=0,request_key=key)
        with override_settings(BILLING_APPROVED_CHANNELS=channels):
            results = self.parallel([lambda:replace('one'),lambda:replace('two')])
        self.assertEqual(sum(isinstance(result,dict) for result in results),1)
        self.assertIn('AUTHORIZATION_VERSION_CHANGED',results)
        self.assertEqual(AuthorizationChange.objects.count(),1)
        self.assertEqual(Subscription.objects.get(pk=sub['id']).authorization_version,1)
        self.assertEqual(Order.objects.count(),1)

    def test_parallel_seat_assignment_has_one_winner_and_one_audit(self):
        from .subscriptions import assign_seat
        from .models import PaidSeat, SeatChange
        self.activate(self.business_paid('seat_included'))
        member = Membership.objects.get(account=self.account,user=self.user)
        seat = PaidSeat.objects.first()
        results = self.parallel([
            lambda:assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=0,request_key='one'),
            lambda:assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=0,request_key='two')])
        self.assertEqual(sum(isinstance(item,dict) for item in results),1)
        self.assertIn('SEAT_CHANGED',results)
        self.assertEqual(SeatChange.objects.count(),1)
        self.assertEqual(CreditBatch.objects.count(),2)

    def test_parallel_shared_reservations_include_inflight_usage_in_cap(self):
        from .billing import set_shared_limit, reserve_credits
        member = self.business_member('shared_only')
        set_shared_limit(self.account,self.user,member.pk,'5',expected_version=0,request_key='cap')
        results = self.parallel([
            lambda:reserve_credits(self.account,'one','4',membership_id=member.pk),
            lambda:reserve_credits(self.account,'two','4',membership_id=member.pk)])
        self.assertEqual(sum(isinstance(item,dict) for item in results),1)
        self.assertIn('INSUFFICIENT_CREDITS',results)
        self.assertEqual(credits_summary(self.account)['reserved'],'4')

    def test_parallel_method_removal_and_dispatch_preserve_one_path(self):
        from .billing import claim_payment_dispatch
        from .subscriptions import (register_verified_payment_method, payment_method_impact,
            request_payment_method_removal)
        method = register_verified_payment_method(self.account, channel='fixture',
            merchant_reference='fixture-shenzhen', fingerprint='c'*64, evidence_hash='d'*64)
        sub = self.activate(payment_method_id=method)
        attempt = self.prepare(sub['id'])
        impact = payment_method_impact(self.account, self.user, method)
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        results = self.parallel([
            lambda:request_payment_method_removal(self.account, self.user, method, expected_impact=impact['confirmation_hash']),
            lambda:claim_payment_dispatch(self.account, attempt['id'])])
        row = PaymentAttempt.objects.get(pk=attempt['id'])
        if row.dispatched_at:
            self.assertIn('PAYMENT_METHOD_IMPACT_CHANGED', results)
            fresh = payment_method_impact(self.account, self.user, method)
            self.assertEqual(len(fresh['unresolved_payments']), 1)
            request_payment_method_removal(self.account, self.user, method, expected_impact=fresh['confirmation_hash'])
        else:
            self.assertEqual(row.state, 'cancelled')
        self.assertFalse(Subscription.objects.get(pk=sub['id']).auto_renew)
        self.assertFalse(claim_payment_dispatch(self.account, attempt['id'])['dispatch'])
        self.assertEqual(AuditEvent.objects.filter(action='payment_method.remove_requested').count(), 1)

    def test_parallel_renewal_creation_and_cancel_dispatch(self):
        from .billing import claim_payment_dispatch
        sub = self.activate()
        results = self.parallel([lambda: self.prepare(sub['id'])]*2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertEqual(Order.objects.count(), 2)
        self.now.return_value = month_boundary(self.anchor, 12)-timedelta(hours=1)
        self.parallel([lambda: cancel_subscription(self.account, self.user, sub['id']),
                       lambda: claim_payment_dispatch(self.account, results[0]['id'])])
        attempt = PaymentAttempt.objects.get()
        self.assertIn(attempt.state, ('pending', 'cancelled'))
        self.assertEqual(attempt.dispatched_at is not None, attempt.state == 'pending')
        self.assertFalse(Subscription.objects.get().auto_renew)
        self.assertFalse(claim_payment_dispatch(self.account, attempt.pk)['dispatch'])

    def test_parallel_recovery_creates_and_dispatches_once(self):
        from .billing import claim_payment_dispatch
        _, original = self.failed_renewal()
        results = self.parallel([lambda:self.recover(original)]*2)
        self.assertEqual(results[0],results[1])
        self.assertEqual(Order.objects.count(),3)
        self.assertEqual(PaymentAttempt.objects.filter(recovery_of__isnull=False).count(),1)
        claims = self.parallel([lambda:claim_payment_dispatch(self.account,results[0]['id'])]*2)
        self.assertEqual(sum(item['dispatch'] for item in claims),1)
