"""Persistence tests use explicit commercial fixtures, never production seeds."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from backend.models import BillingAccount, Membership
from .billing import (
    account_summary, create_order, create_quote, credits_summary, fulfill_paid_order, grant_credits,
    list_orders, order_detail, record_confirmed_payment, release_credits,
    reserve_credits, settle_credits,
)
from .models import CreditBatch, CreditEntry, Order, PaymentFact, ProductVersion, Reservation


def fixture_account(name="buyer"):
    user = get_user_model().objects.create_user(username=name)
    account = BillingAccount.objects.create(kind="personal", name=name, personal_owner=user)
    return user, account


def fixture_product(audience="personal"):
    return ProductVersion.objects.create(
        code="fixture_pack", version=1, kind="credits", audience=audience,
        unit_amount_minor=731, credits_quantity="10.000000000000000000000000000001",
        entitlements={"fixture": True, "benefits": {"zh": ["隔离测试权益，不对外销售。"],
                      "en": ["Isolated test benefits, not for sale."]}}, entitlement_version="fixture-1",
        credit_rule_version="fixture-1", terms_version="fixture-1", state="published",
        effective_at=timezone.now() - timedelta(days=1),
    )


FIXTURE_CHANNELS = {"fixture": {"enabled": True, "currency": "CNY", "merchant_entity": "fixture-shenzhen", "regions": ["CN"], "capabilities": ["one_time", "recurring_monthly", "recurring_annual"]}}


@override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS={
    'fixture':{**FIXTURE_CHANNELS['fixture'],'capabilities':[*FIXTURE_CHANNELS['fixture']['capabilities'],'automatic_topup']}})
class SharedIncludedBusinessTests(TransactionTestCase):
    """Approved China-reference resources; all amounts and receipts are synthetic."""
    def setUp(self):
        from .subscription_tests import SubscriptionTests
        SubscriptionTests.setUp(self)
        self.account=BillingAccount.objects.create(kind='business',name='shared-included-fixture')
        self.owner=Membership.objects.create(account=self.account,user=self.user,role='owner')
        self.product=ProductVersion.objects.create(code='fixture_shared_included',version=1,kind='subscription',
            audience='business',billing_cycle='annual',minimum_seats=10,unit_amount_minor=12345,
            credits_quantity='17.25',entitlements={'resource_mode':'shared_included'},
            entitlement_version='fixture-1',credit_rule_version='fixture-1',terms_version='fixture-1',
            state='published',effective_at=self.anchor-timedelta(days=1))

    def quote(self, seats=10):
        return create_quote(self.account,self.user,self.product.pk,seats,timezone.now()+timedelta(minutes=5),
            channel='fixture',buyer_region='CN')

    def paid(self, seats=10):
        self.sequence+=1
        quote=self.quote(seats)
        order=create_order(self.account,self.user,{'quote_id':quote['id'],'agreement_acceptance':'fixture-1'},
            'shared-included:'+str(self.sequence))
        record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='shared-included:'+str(self.sequence),amount_minor=12345*seats,currency='CNY',
            paid_at=timezone.now(),evidence_hash='b'*64)
        return order['id']

    def activate(self, seats=10):
        from .subscriptions import activate_subscription
        self.order_id=self.paid(seats)
        return activate_subscription(self.account,self.order_id,activated_at=self.anchor,
            billing_timezone='Asia/Shanghai',calendar_rule_version='fixture-calendar-v1',mandate=self.mandate)

    def member_task(self, position=1):
        from .models import PaidSeat
        from .subscriptions import assign_seat
        from .billing import start_shared_task
        employee=get_user_model().objects.create_user(username='shared-included-employee-'+str(position))
        member=Membership.objects.create(account=self.account,user=employee,role='member')
        seat=PaidSeat.objects.get(subscription__account=self.account,position=position)
        assign_seat(self.account,self.user,seat.pk,member.pk,expected_version=seat.version,
            request_key='shared-seat:'+str(position))
        task=start_shared_task(self.account,member.pk,'shared-task:'+str(position),'voc')
        return member,seat,task

    def topup(self):
        from .subscriptions import register_verified_payment_method, authorize_topup_preset
        method=register_verified_payment_method(self.account,channel='fixture',merchant_reference='fixture-shenzhen',
            fingerprint='c'*64,evidence_hash='d'*64)
        pack=fixture_product(audience='business')
        preset={'approved':True,'product_id':str(pack.pk),'threshold':'2','monthly_cap_minor':1462,
            'terms_version':'topup-fixture-1','policy_version':'service-month-v1'}
        setting=override_settings(BILLING_TOPUP_PRESETS={'approved':preset})
        setting.enable();self.addCleanup(setting.disable)
        return authorize_topup_preset(self.account,self.user,preset_key='approved',payment_method_id=method,
            mandate={**self.mandate,'agreement_reference':'shared-included-topup'},consent_evidence_hash='e'*64,
            expected_version=0,request_key='shared-topup-consent')

    def test_teams_one_seat_can_be_bought_and_activated(self):
        ProductVersion.objects.filter(pk=self.product.pk).update(minimum_seats=1,entitlements={'resource_mode':'seat_included'})
        self.product.refresh_from_db()
        self.activate(1)
        batch=CreditBatch.objects.get(account=self.account)
        self.assertIsNotNone(batch.seat_id)
        self.assertEqual(batch.initial_quantity,'17.25')
        with self.assertRaisesRegex(ValueError,'INVALID_SEATS'):self.quote(0)

    def test_enterprise_minimum_ten_is_not_a_ten_seat_increment(self):
        with self.assertRaisesRegex(ValueError,'INVALID_SEATS'):self.quote(9)
        self.assertEqual(self.quote(10)['amount_minor'],123450)
        self.assertEqual(self.quote(11)['amount_minor'],135795)
        ProductVersion.objects.filter(pk=self.product.pk).update(minimum_seats=9)
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):self.quote(10)

    def test_both_new_business_modes_require_positive_monthly_quantity(self):
        for mode in ('shared_included','seat_included'):
            with self.subTest(mode=mode):
                ProductVersion.objects.filter(pk=self.product.pk).update(credits_quantity='0',
                    entitlements={'resource_mode':mode})
                with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):self.quote()

    def test_old_no_included_mode_cannot_create_a_new_purchase(self):
        ProductVersion.objects.filter(pk=self.product.pk).update(minimum_seats=2,credits_quantity='0',
            entitlements={'resource_mode':'shared_only'})
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):self.quote()
        self.assertFalse(Order.objects.exists())

    def test_named_business_plans_cannot_sell_the_other_allowance_mode(self):
        for code, correct, wrong in (('teams','seat_included','shared_included'),
                                     ('enterprise','shared_included','seat_included')):
            with self.subTest(code=code):
                ProductVersion.objects.filter(pk=self.product.pk).update(code=code,
                    entitlements={'resource_mode':wrong})
                with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
                    self.quote(10)
                ProductVersion.objects.filter(pk=self.product.pk).update(entitlements={'resource_mode':correct})
                self.assertEqual(self.quote(10)['amount_minor'],123450)
        self.assertFalse(Order.objects.exists())

    def test_named_legacy_enterprise_keeps_historical_delivery_gate_only(self):
        from .billing import _sale_gate
        ProductVersion.objects.filter(pk=self.product.pk).update(code='enterprise',minimum_seats=2,
            credits_quantity='0',entitlements={'resource_mode':'shared_only'})
        self.product.refresh_from_db()
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            self.quote(2)
        self.assertEqual(_sale_gate(self.product,self.account,timezone.now(),'fixture','CN',
            'fixture-shenzhen',historical_subscription=True),'fixture-shenzhen')

    def test_monthly_shared_batch_is_total_capacity_once_and_activation_replays(self):
        from .subscriptions import activate_subscription, grant_due_credits
        from .models import PaidSeat
        ProductVersion.objects.filter(pk=self.product.pk).update(billing_cycle='monthly')
        result=self.activate()
        batch=CreditBatch.objects.get(account=self.account)
        self.assertEqual((batch.source,batch.seat_id,batch.initial_quantity,batch.service_month),
            ('subscription',None,'172.5',0))
        self.assertEqual(PaidSeat.objects.filter(subscription_id=result['id']).count(),10)
        self.assertEqual(grant_due_credits(self.account),{'created_batches':0})
        self.assertEqual(activate_subscription(self.account,self.order_id,activated_at=self.anchor,
            billing_timezone='Asia/Shanghai',calendar_rule_version='fixture-calendar-v1',mandate=self.mandate),result)
        self.assertEqual(CreditBatch.objects.filter(account=self.account).count(),1)

    def test_annual_grants_keep_monthly_expiry_and_late_history_is_not_spendable(self):
        from backend.billing_rules import month_boundary
        from .subscriptions import grant_due_credits
        self.activate()
        self.now.return_value=month_boundary(self.anchor,2)
        self.assertEqual(grant_due_credits(self.account),{'created_batches':2})
        self.assertEqual(credits_summary(self.account)['available'],'172.5')
        for index,batch in enumerate(CreditBatch.objects.filter(account=self.account).order_by('service_month')):
            self.assertEqual(batch.service_month,index)
            self.assertEqual(batch.expires_at,month_boundary(self.anchor,index+1))
        self.now.return_value=month_boundary(self.anchor,12)
        grant_due_credits(self.account)
        self.assertEqual(CreditBatch.objects.filter(account=self.account).count(),12)
        self.assertEqual(credits_summary(self.account)['available'],'0')

    def test_two_employees_consume_the_same_pool_without_private_or_other_company_points(self):
        from .billing import consume_shared_task
        self.activate()
        first,_,one=self.member_task(1)
        _,_,two=self.member_task(2)
        private=BillingAccount.objects.create(kind='personal',name='shared-fixture-private',personal_owner=first.user)
        other=BillingAccount.objects.create(kind='business',name='shared-fixture-other')
        for account in (private,other):grant_credits(account,'purchased','isolation-fixture','100',self.anchor+timedelta(days=1))
        self.assertTrue(consume_shared_task(self.account,one['id'],'first','100')['accepted'])
        self.assertTrue(consume_shared_task(self.account,two['id'],'second','72.5')['accepted'])
        self.assertFalse(consume_shared_task(self.account,two['id'],'insufficient','0.01')['accepted'])
        self.assertEqual(CreditEntry.objects.filter(reservation__shared_task_id__in=[one['id'],two['id']],kind='settle')
            .values('batch_id').distinct().count(),1)
        for account in (private,other):self.assertEqual(credits_summary(account)['available'],'100')

    def test_shared_pool_uses_earliest_expiry_and_excludes_expired_frozen_points(self):
        from .billing import consume_shared_task, freeze_credits, _business_task_credit_scope
        self.activate();_,_,task=self.member_task()
        batch=CreditBatch.objects.get(account=self.account)
        freeze_credits(self.account,batch.pk,'shared-frozen','170')
        purchase=grant_credits(self.account,'purchased','shared-purchased','10',self.anchor+timedelta(days=1))
        grant_credits(self.account,'purchased','shared-expired','100',self.anchor-timedelta(seconds=1))
        self.assertEqual(str(_business_task_credit_scope(self.account)['shared_available']),'25/2')
        self.assertTrue(consume_shared_task(self.account,task['id'],'draw','4')['accepted'])
        entries=CreditEntry.objects.filter(reservation__shared_task_id=task['id'],kind='settle')
        self.assertFalse(entries.filter(batch=batch).exists())
        self.assertEqual(entries.get(batch_id=purchase['id']).quantity,'4')

    def test_shared_pool_prefers_included_only_when_expiry_is_equal(self):
        from .billing import consume_shared_task
        self.activate();_,_,task=self.member_task()
        batch=CreditBatch.objects.get(account=self.account)
        purchase=grant_credits(self.account,'purchased','same-expiry-purchase','10',batch.expires_at)
        self.assertTrue(consume_shared_task(self.account,task['id'],'draw','4')['accepted'])
        entries=CreditEntry.objects.filter(reservation__shared_task_id=task['id'],kind='settle')
        self.assertEqual(entries.get(batch=batch).quantity,'4')
        self.assertFalse(entries.filter(batch_id=purchase['id']).exists())

    def test_member_shared_usage_includes_actual_and_legacy_draws_and_resets_each_month(self):
        from backend.billing_rules import month_boundary
        from .billing import consume_shared_task
        from .subscriptions import organization_summary, grant_due_credits
        self.activate();member,_,task=self.member_task()
        _,_,other_task=self.member_task(2)
        consume_shared_task(self.account,task['id'],'member-shared-first','2')
        consume_shared_task(self.account,other_task['id'],'other-member-shared','7')
        reservation=reserve_credits(self.account,'member-legacy-shared','3',membership_id=member.pk)
        settle_credits(self.account,reservation['id'],'3')
        row=next(row for row in organization_summary(self.account)['members'] if row['id']==member.pk)
        self.assertEqual((row['shared_used'],row['shared_reserved'],row['total_credits']),('5','0','2'))
        self.now.return_value=month_boundary(self.anchor,1)
        grant_due_credits(self.account)
        consume_shared_task(self.account,task['id'],'member-shared-next-month','4')
        row=next(row for row in organization_summary(self.account)['members'] if row['id']==member.pk)
        self.assertEqual((row['shared_used'],row['shared_reserved'],row['total_credits']),('4','0','6'))

    def test_member_shared_usage_excludes_teams_own_included_points(self):
        from .billing import consume_shared_task
        from .subscriptions import organization_summary
        ProductVersion.objects.filter(pk=self.product.pk).update(minimum_seats=1,entitlements={'resource_mode':'seat_included'})
        self.activate(1);member,_,task=self.member_task()
        grant_credits(self.account,'purchased','member-shared-purchase','10',self.anchor+timedelta(days=1))
        consume_shared_task(self.account,task['id'],'member-own-first','2')
        row=next(row for row in organization_summary(self.account)['members'] if row['id']==member.pk)
        self.assertEqual((row['shared_used'],row['total_credits']),('0','2'))
        consume_shared_task(self.account,task['id'],'member-shared-after-own','16')
        row=next(row for row in organization_summary(self.account)['members'] if row['id']==member.pk)
        self.assertEqual((row['shared_used'],row['total_credits']),('0.75','18'))

    def test_removal_and_reassignment_preserve_shared_pool_and_do_not_regrant(self):
        from .billing import consume_shared_task, start_shared_task
        from .subscriptions import remove_member, assign_seat, grant_due_credits
        self.activate();member,seat,task=self.member_task()
        self.assertTrue(consume_shared_task(self.account,task['id'],'first','1')['accepted'])
        remove_member(self.account,self.user,member.pk,request_key='shared-remove')
        seat.refresh_from_db();self.assertIsNone(seat.membership_id)
        assign_seat(self.account,self.user,seat.pk,self.owner.pk,expected_version=seat.version,request_key='shared-reassign')
        replacement=start_shared_task(self.account,self.owner.pk,'replacement','voc')
        self.assertTrue(consume_shared_task(self.account,replacement['id'],'second','1')['accepted'])
        self.assertTrue(consume_shared_task(self.account,task['id'],'removed-running','1')['accepted'])
        self.assertEqual(grant_due_credits(self.account),{'created_batches':0})
        self.assertEqual(CreditBatch.objects.filter(account=self.account).count(),1)
        self.assertEqual(credits_summary(self.account)['available'],'169.5')

    def test_legacy_internal_reservation_can_use_shared_included(self):
        self.activate();member,_,_=self.member_task()
        purchase=grant_credits(self.account,'purchased','legacy-earlier','1',self.anchor+timedelta(days=1))
        reservation=reserve_credits(self.account,'legacy-shared-use','3',membership_id=member.pk)
        settle_credits(self.account,reservation['id'],'3')
        self.assertEqual(credits_summary(self.account)['available'],'170.5')
        self.assertEqual(CreditEntry.objects.get(reservation_id=reservation['id'],kind='settle',batch_id=purchase['id']).quantity,'1')

    def test_prepaid_renewal_grants_once_when_its_service_month_begins(self):
        from backend.billing_rules import month_boundary
        from .subscriptions import append_paid_period, grant_due_credits
        result=self.activate();renewal=self.paid()
        append_paid_period(self.account,result['id'],renewal,cycle_index=1)
        append_paid_period(self.account,result['id'],renewal,cycle_index=1)
        self.assertEqual(CreditBatch.objects.filter(account=self.account).count(),1)
        self.now.return_value=month_boundary(self.anchor,12)
        grant_due_credits(self.account)
        self.assertEqual(credits_summary(self.account)['available'],'172.5')
        self.assertEqual(CreditBatch.objects.filter(account=self.account,subscription_period__cycle_index=1).count(),1)
        self.assertEqual(grant_due_credits(self.account),{'created_batches':0})

    def test_future_service_refund_preserves_this_month_and_stops_future_shared_grants(self):
        from backend.billing_rules import month_boundary
        from .subscriptions import grant_due_credits
        from .refunds import request_service_refund
        result=self.activate()
        request_service_refund(self.account,self.user,result['id'],'shared-future-refund')
        self.assertEqual(credits_summary(self.account)['available'],'172.5')
        self.now.return_value=month_boundary(self.anchor,1)
        self.assertEqual(grant_due_credits(self.account),{'created_batches':0})
        self.assertEqual(credits_summary(self.account)['available'],'0')

    def test_shared_included_cannot_be_refunded_as_purchased_credits(self):
        from .billing import reserve_credit_refund
        from .models import CreditRefund
        self.activate()
        with self.assertRaisesRegex(ValueError,'REFUND_NOT_ELIGIBLE'):
            reserve_credit_refund(self.account,self.order_id,'shared-not-purchased',review_evidence_hash='a'*64)
        self.assertFalse(CreditRefund.objects.exists())

    def test_auto_topup_counts_shared_included_and_requires_actual_demand(self):
        from .subscriptions import evaluate_automatic_topup
        from .billing import freeze_credits, consume_shared_task
        self.activate();self.topup()
        self.assertEqual(evaluate_automatic_topup(self.account)['reason'],'ABOVE_THRESHOLD')
        batch=CreditBatch.objects.get(account=self.account)
        freeze_credits(self.account,batch.pk,'shared-topup-freeze','171')
        _,_,task=self.member_task()
        self.assertEqual(evaluate_automatic_topup(self.account)['reason'],'NO_SHARED_DEMAND')
        self.assertTrue(consume_shared_task(self.account,task['id'],'needs-shared','0.5')['accepted'])
        self.assertTrue(evaluate_automatic_topup(self.account)['triggered'])

    def test_next_month_shared_grant_closes_unsubmitted_topup(self):
        from backend.billing_rules import month_boundary
        from .subscriptions import evaluate_automatic_topup, claim_automatic_topup, grant_due_credits
        from .billing import consume_shared_task
        from .models import PaymentAttempt
        self.activate();self.topup();_,_,task=self.member_task()
        self.assertTrue(consume_shared_task(self.account,task['id'],'near-empty','171')['accepted'])
        attempt=evaluate_automatic_topup(self.account)['attempt']
        self.now.return_value=month_boundary(self.anchor,1)-timedelta(minutes=1)
        PaymentAttempt.objects.filter(pk=attempt['id']).update(expires_at=month_boundary(self.anchor,1)+timedelta(minutes=1))
        self.now.return_value=month_boundary(self.anchor,1)
        grant_due_credits(self.account)
        result=claim_automatic_topup(self.account,attempt['id'])
        self.assertFalse(result['dispatch'])
        self.assertIn(result['status'],('cancelled','expired'))
        self.assertEqual(credits_summary(self.account)['available'],'172.5')

    def test_restored_shared_allowance_cancels_a_live_unsubmitted_topup(self):
        from .subscriptions import evaluate_automatic_topup, claim_automatic_topup
        from .billing import freeze_credits, resolve_credit_freeze, consume_shared_task
        from .models import PaymentAttempt
        self.activate();self.topup();_,_,task=self.member_task()
        batch=CreditBatch.objects.get(account=self.account)
        frozen=freeze_credits(self.account,batch.pk,'shared-restorable','171')
        self.assertTrue(consume_shared_task(self.account,task['id'],'shared-demand','0.5')['accepted'])
        attempt=evaluate_automatic_topup(self.account)['attempt']
        resolve_credit_freeze(self.account,frozen['id'],'released',evidence_hash='f'*64)
        result=claim_automatic_topup(self.account,attempt['id'])
        self.assertEqual((result['dispatch'],result['status'],result['reason']),
            (False,'cancelled','TOPUP_NOT_NEEDED'))
        row=PaymentAttempt.objects.select_related('order').get(pk=attempt['id'])
        self.assertIsNone(row.dispatched_at)
        self.assertEqual(row.order.status,'closed')

    @override_settings(BILLING_RENEWAL_CHARGING_ENABLED=True)
    def test_historical_zero_included_contract_still_delivers_its_authorized_renewal(self):
        from backend.billing_rules import month_boundary
        from .subscription_tests import SubscriptionTests, sent_renewal_notice_fixture
        from .subscriptions import prepare_renewal_attempt, fulfill_renewal_attempt
        from .billing import claim_payment_dispatch
        from .models import PaymentAttempt, Subscription
        order=SubscriptionTests.business_paid(self,'shared_only')
        result=SubscriptionTests.activate(self,order)
        original=Order.objects.get(pk=order).snapshot
        boundary=month_boundary(self.anchor,12)
        attempt=prepare_renewal_attempt(self.account,result['id'],cycle_index=1,
            dispatch_not_before=boundary-timedelta(hours=1),expires_at=boundary)
        sent_renewal_notice_fixture(self.account,attempt['id'])
        self.now.return_value=boundary-timedelta(hours=1)
        self.assertTrue(claim_payment_dispatch(self.account,attempt['id'])['dispatch'])
        renewal=PaymentAttempt.objects.get(pk=attempt['id'])
        record_confirmed_payment(self.account,renewal.order_id,channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='historical-shared-renewal',amount_minor=24690,currency='CNY',
            paid_at=timezone.now(),evidence_hash='b'*64)
        first=fulfill_renewal_attempt(self.account,renewal.pk)
        self.assertEqual(first,fulfill_renewal_attempt(self.account,renewal.pk))
        self.assertEqual(Subscription.objects.get(pk=result['id']).periods.count(),2)
        self.assertEqual(Order.objects.get(pk=order).snapshot,original)
        self.assertFalse(CreditBatch.objects.filter(account=self.account).exists())
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):self.quote()


@override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
class BusinessTaskCreditScopeTests(TransactionTestCase):
    def setUp(self):
        from .subscription_tests import SubscriptionTests
        from .subscriptions import assign_seat
        from .models import PaidSeat
        from .billing import start_shared_task
        SubscriptionTests.setUp(self)
        SubscriptionTests.activate(self,SubscriptionTests.business_paid(self,'seat_included'))
        self.employee=get_user_model().objects.create_user(username='scope-employee')
        self.member=Membership.objects.create(account=self.account,user=self.employee,role='member')
        self.seat=PaidSeat.objects.order_by('position').first()
        assign_seat(self.account,self.user,self.seat.pk,self.member.pk,expected_version=0,request_key='scope-seat')
        self.shared=grant_credits(self.account,'purchased','scope-shared','10',self.anchor+timedelta(days=2))
        self.private=BillingAccount.objects.create(kind='personal',name='scope-private',personal_owner=self.employee)
        grant_credits(self.private,'purchased','scope-private','100',self.anchor+timedelta(days=1))
        self.task=start_shared_task(self.account,self.member.pk,'scope-task','voc')

    def consume(self, quantity, event='actual'):
        from .billing import consume_shared_task
        return consume_shared_task(self.account,self.task['id'],event,quantity)

    def test_own_included_precedes_earlier_expiring_shared_credits(self):
        self.assertTrue(self.consume('5')['accepted'])
        entries=CreditEntry.objects.filter(reservation__shared_task_id=self.task['id'],kind='settle')
        self.assertEqual(entries.count(),1)
        self.assertEqual(entries.get().batch.seat_id,self.seat.pk)
        self.assertEqual(entries.get().quantity,'5')
        self.assertEqual(credits_summary(self.private)['available'],'100')

    def test_shared_follows_own_included_and_legacy_cap_has_no_effect(self):
        from .billing import set_shared_limit
        set_shared_limit(self.account,self.user,self.member.pk,'0',expected_version=0,request_key='historical-cap')
        self.assertTrue(self.consume('20')['accepted'])
        entries=CreditEntry.objects.filter(reservation__shared_task_id=self.task['id'],kind='settle')
        self.assertEqual(entries.get(batch__seat=self.seat).quantity,'17.25')
        self.assertEqual(entries.get(batch__source='purchased').quantity,'2.75')
        self.assertFalse(entries.filter(batch__seat__isnull=False).exclude(batch__seat=self.seat).exists())
        self.assertEqual(credits_summary(self.account)['reserved'],'0')

    def test_other_seat_and_private_balance_cannot_cover_insufficient_usage(self):
        from .billing import resume_shared_task
        result=self.consume('28')
        self.assertEqual((result['accepted'],result['status']),(False,'paused'))
        self.assertEqual(credits_summary(self.account)['consumed'],'0')
        self.assertEqual(credits_summary(self.private)['available'],'100')
        with self.assertRaisesRegex(ValueError,'INSUFFICIENT_CREDITS'):
            resume_shared_task(self.account,self.employee,self.task['id'])
        grant_credits(self.account,'purchased','scope-replenish','1',self.anchor+timedelta(days=2))
        self.assertFalse(self.consume('28')['accepted'])
        resume_shared_task(self.account,self.employee,self.task['id'])
        result=self.consume('28')
        self.assertTrue(result['accepted'])
        self.assertEqual(self.consume('28'),result)
        self.assertEqual(credits_summary(self.account)['consumed'],'28')

    def test_only_other_members_points_cannot_resume_a_task(self):
        from .billing import resume_shared_task
        self.assertTrue(self.consume('27.25')['accepted'])
        self.assertFalse(self.consume('1','later')['accepted'])
        self.assertEqual(credits_summary(self.account)['available'],'17.25')
        with self.assertRaisesRegex(ValueError,'INSUFFICIENT_CREDITS'):
            resume_shared_task(self.account,self.employee,self.task['id'])

    def test_member_without_paid_seat_cannot_resume_using_shared_funds(self):
        from .billing import resume_shared_task
        from .subscriptions import assign_seat
        self.assertFalse(self.consume('28')['accepted'])
        assign_seat(self.account,self.user,self.seat.pk,None,expected_version=1,request_key='no-paid-seat')
        grant_credits(self.account,'purchased','enough-shared','30',self.anchor+timedelta(days=2))
        with self.assertRaisesRegex(ValueError,'TASK_RESUME_FORBIDDEN'):
            resume_shared_task(self.account,self.employee,self.task['id'])
        self.assertEqual(credits_summary(self.account)['consumed'],'0')

    def test_active_member_without_paid_seat_cannot_continue_running_task(self):
        from .subscriptions import assign_seat
        assign_seat(self.account,self.user,self.seat.pk,None,expected_version=1,request_key='config-only')
        result=self.consume('1')
        self.assertEqual((result['accepted'],result['status']),(False,'paused'))
        self.assertEqual(credits_summary(self.account)['consumed'],'0')

    def test_removed_members_running_task_uses_only_shared_and_never_resumes_after_pause(self):
        from .subscriptions import remove_member
        from .billing import resume_shared_task
        remove_member(self.account,self.user,self.member.pk,request_key='scope-remove')
        self.assertTrue(self.consume('10')['accepted'])
        entry=CreditEntry.objects.get(reservation__shared_task_id=self.task['id'],kind='settle')
        self.assertEqual(entry.batch.source,'purchased')
        self.assertFalse(self.consume('1','after-shared-empty')['accepted'])
        grant_credits(self.account,'purchased','after-removal','10',self.anchor+timedelta(days=2))
        with self.assertRaisesRegex(ValueError,'TASK_RESUME_FORBIDDEN'):
            resume_shared_task(self.account,self.user,self.task['id'])

    def test_seat_reassigned_to_another_member_does_not_move_running_tasks_credit_access(self):
        from .models import PaidSeat
        # Model-level ownership change isolates consumption from the separate seat-change policy.
        owner=Membership.objects.get(account=self.account,user=self.user)
        PaidSeat.objects.filter(pk=self.seat.pk).update(membership=owner)
        self.assertFalse(self.consume('11')['accepted'])
        self.assertEqual(credits_summary(self.account)['consumed'],'0')

    def test_scope_excludes_expired_frozen_private_and_unassigned_included_batches(self):
        from .billing import _business_task_credit_scope, freeze_credits
        from .models import SharedTask
        from fractions import Fraction
        grant_credits(self.account,'purchased','scope-expired','100',self.anchor-timedelta(seconds=1))
        freeze_credits(self.account,self.shared['id'],'scope-freeze','3')
        scope=_business_task_credit_scope(self.account,SharedTask.objects.get(pk=self.task['id']))
        self.assertEqual((scope['included_available'],scope['shared_available'],scope['available']),
                         (Fraction('17.25'),Fraction(7),Fraction('24.25')))
        shared=_business_task_credit_scope(self.account)
        self.assertEqual((shared['included_available'],shared['available']),(0,7))
        other_business=BillingAccount.objects.create(kind='business',name='scope-other-business')
        with self.assertRaisesRegex(ValueError,'FORBIDDEN'):
            _business_task_credit_scope(other_business,SharedTask.objects.get(pk=self.task['id']))

    def test_shared_batches_keep_expiry_order_after_the_own_allowance(self):
        early=grant_credits(self.account,'purchased','scope-shared-earlier','2',self.anchor+timedelta(days=1))
        self.assertTrue(self.consume('18.25')['accepted'])
        entries=CreditEntry.objects.filter(reservation__shared_task_id=self.task['id'],kind='settle')
        self.assertEqual(entries.get(batch__seat=self.seat).quantity,'17.25')
        self.assertEqual(entries.get(batch_id=early['id']).quantity,'1')
        self.assertFalse(entries.filter(batch_id=self.shared['id']).exists())

    def test_superseded_or_refunded_seat_cannot_start_another_task(self):
        from .billing import start_shared_task
        from .models import Subscription
        for field in ('superseded_at','refund_cutoff_at'):
            with self.subTest(field=field):
                Subscription.objects.filter(pk=self.seat.subscription_id).update(**{field:self.anchor})
                with self.assertRaisesRegex(ValueError,'SEAT_REQUIRED'):
                    start_shared_task(self.account,self.member.pk,'obsolete-'+field,'voc')
                Subscription.objects.filter(pk=self.seat.subscription_id).update(**{field:None})

    def test_ordinary_expiry_keeps_existing_member_access_to_unexpired_shared_purchase(self):
        from .billing import start_shared_task, consume_shared_task
        from backend.billing_rules import month_boundary
        self.now.return_value=month_boundary(self.anchor,12)
        grant_credits(self.account,'purchased','scope-long-lived','2',self.now.return_value+timedelta(days=1))
        task=start_shared_task(self.account,self.member.pk,'expired-plan-task','voc')
        self.assertTrue(consume_shared_task(self.account,task['id'],'actual','1')['accepted'])
        entry=CreditEntry.objects.get(reservation__shared_task_id=task['id'],kind='settle')
        self.assertEqual(entry.batch.source,'purchased')

    def test_next_service_month_grants_only_restore_the_same_members_allowance(self):
        from .subscriptions import grant_due_credits
        from .billing import resume_shared_task
        from backend.billing_rules import month_boundary
        self.assertTrue(self.consume('27.25')['accepted'])
        self.assertFalse(self.consume('1','pending-next-month')['accepted'])
        self.now.return_value=month_boundary(self.anchor,1)
        grant_due_credits(self.account)
        resume_shared_task(self.account,self.employee,self.task['id'])
        self.assertTrue(self.consume('1','pending-next-month')['accepted'])
        entry=CreditEntry.objects.get(reservation__shared_task_id=self.task['id'],
            reservation__actual_quantity='1',kind='settle')
        self.assertEqual((entry.batch.seat_id,entry.batch.service_month),(self.seat.pk,1))


@override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS,
    BILLING_CREDITS_PURCHASE_ENABLED=True, BILLING_QUOTE_TTL_SECONDS=300,
    BILLING_TERMS_DOCUMENTS={'fixture-1': {'approved':True,
        'zh':{'title':'测试条款','paragraphs':['仅限隔离测试。']},
        'en':{'title':'Test terms','paragraphs':['Isolated fixture only.']}}})
class PublicPurchaseEligibilityTests(TransactionTestCase):
    def setUp(self):
        self.user, self.account = fixture_account()
        self.now = timezone.now()
        self.product = ProductVersion.objects.create(code='pro',version=1,kind='subscription',
            audience='personal',billing_cycle='monthly',unit_amount_minor=19900,credits_quantity='10',
            entitlements={'benefits':{'zh':['测试权益'],'en':['Test benefit']}},
            entitlement_version='fixture-1',credit_rule_version='fixture-1',terms_version='fixture-1',
            state='published',effective_at=self.now-timedelta(days=1))

    def quote(self, product=None, public=True):
        from .billing import purchase_terms
        product = product or self.product
        seats = 2 if self.account.kind == 'business' and product.kind == 'subscription' else 1
        return create_quote(self.account,self.user,product.pk,seats,
            self.now+timedelta(minutes=5),channel='fixture',buyer_region='CN',
            terms_digest=purchase_terms('fixture-1')['digest'] if public else None)

    def order(self, quote, key):
        return create_order(self.account,self.user,
            {'quote_id':quote['id'],'agreement_acceptance':'fixture-1'},key)

    def subscribed(self, *, active=True, renewing=False, future=False):
        from .models import Subscription, SubscriptionPeriod
        order = self.order(self.quote(public=False),'initial')
        Order.objects.filter(pk=order['id']).update(status='fulfilled',paid_at=self.now)
        subscription = Subscription.objects.create(account=self.account,initial_order_id=order['id'],
            original_anchor=self.now-timedelta(days=1),billing_timezone='Asia/Shanghai',
            calendar_rule_version='fixture-1',billing_cycle='monthly',mandate={},
            auto_renew=renewing,cancellation_state='none' if renewing else 'completed')
        starts = self.now+timedelta(days=1) if future else self.now-timedelta(days=2)
        ends = self.now+timedelta(days=30) if active or future else self.now-timedelta(days=1)
        SubscriptionPeriod.objects.create(subscription=subscription,order_id=order['id'],cycle_index=0,
            starts_at=starts,ends_at=ends,snapshot=order['snapshot'])
        return subscription

    def test_existing_paid_period_blocks_public_new_purchase_but_not_internal_quote(self):
        from .billing import purchase_catalog, purchase_quote_detail
        quote = self.quote()
        self.subscribed()
        self.assertFalse(purchase_quote_detail(self.account,quote['id'])['can_order'])
        self.assertFalse(purchase_catalog(self.account)['items'][0]['quote_available'])
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            self.quote()
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            self.order(quote,'new-plan')
        self.assertIn('id',self.quote(public=False))

    def test_future_paid_and_expired_renewing_subscriptions_block_public_new_purchase(self):
        self.subscribed(future=True)
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            self.quote()
        from .models import SubscriptionPeriod, Subscription
        SubscriptionPeriod.objects.update(starts_at=self.now-timedelta(days=2),ends_at=self.now-timedelta(days=1))
        Subscription.objects.update(auto_renew=True,cancellation_state='none')
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            self.quote()
        Subscription.objects.update(auto_renew=False,cancellation_state='completed')
        self.assertIn('id',self.quote())

    def test_pending_new_subscription_blocks_second_order_and_preserves_original_replay(self):
        from .billing import purchase_catalog, purchase_quote_detail
        first,second = self.quote(),self.quote()
        order = self.order(first,'first')
        self.assertEqual(self.order(first,'first'),order)
        self.assertFalse(purchase_quote_detail(self.account,second['id'])['can_order'])
        self.assertFalse(purchase_catalog(self.account)['items'][0]['quote_available'])
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            self.order(second,'second')
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            self.quote()
        Order.objects.filter(pk=order['id']).update(status='closed')
        self.assertIn('id',self.quote())

    def test_public_credits_require_current_paid_period_not_auto_renew_or_future_period(self):
        from .billing import purchase_catalog
        pack = fixture_product()
        with self.assertRaisesRegex(ValueError,'CREDITS_SUBSCRIPTION_REQUIRED'):
            self.quote(pack)
        blocked = next(item for item in purchase_catalog(self.account)['items'] if item['kind']=='credits')
        self.assertFalse(blocked['quote_available'])
        self.assertEqual(blocked['unavailable_reason'],'CREDITS_SUBSCRIPTION_REQUIRED')
        self.assertIn('id',self.quote(pack,public=False))
        self.subscribed(future=True)
        with self.assertRaisesRegex(ValueError,'CREDITS_SUBSCRIPTION_REQUIRED'):
            self.quote(pack)
        from .models import SubscriptionPeriod
        SubscriptionPeriod.objects.update(starts_at=self.now-timedelta(days=1))
        quote = self.quote(pack)
        order = self.order(quote,'credits')
        delayed = self.quote(pack)
        SubscriptionPeriod.objects.update(ends_at=self.now-timedelta(seconds=1))
        self.assertEqual(self.order(quote,'credits'),order)
        with self.assertRaisesRegex(ValueError,'CREDITS_SUBSCRIPTION_REQUIRED'):
            self.order(delayed,'expired-credits')
        with self.assertRaisesRegex(ValueError,'CREDITS_SUBSCRIPTION_REQUIRED'):
            self.quote(pack)

    def test_expired_credits_quote_explains_renewal_and_resumes_after_paid_term_returns(self):
        from .billing import purchase_catalog, purchase_quote_detail
        from .models import SubscriptionPeriod
        pack = fixture_product()
        self.subscribed()
        quote = self.quote(pack)
        SubscriptionPeriod.objects.update(ends_at=self.now-timedelta(seconds=1))
        detail = purchase_quote_detail(self.account,quote['id'])
        self.assertFalse(detail['can_order'])
        self.assertEqual(detail['unavailable_reason'],'CREDITS_SUBSCRIPTION_REQUIRED')
        SubscriptionPeriod.objects.update(ends_at=self.now+timedelta(days=30))
        self.assertTrue(purchase_quote_detail(self.account,quote['id'])['can_order'])
        self.assertTrue(next(item for item in purchase_catalog(self.account)['items'] if item['kind']=='credits')['quote_available'])
        self.assertIn('id',self.order(quote,'renewed-pack'))

    def test_credit_purchase_stays_closed_when_total_switch_is_off(self):
        from .billing import purchase_catalog, purchase_quote_detail, prepare_payment_attempt, claim_payment_dispatch
        pack = fixture_product()
        self.subscribed()
        quote, delayed = self.quote(pack), self.quote(pack)
        order = self.order(quote,'switch-order')
        attempt = prepare_payment_attempt(self.account,self.user,order['id'],'switch-attempt',
            expires_at=self.now+timedelta(minutes=5))
        with override_settings(BILLING_CREDITS_PURCHASE_ENABLED=False):
            self.assertFalse(next(item for item in purchase_catalog(self.account)['items'] if item['kind']=='credits')['quote_available'])
            self.assertFalse(purchase_quote_detail(self.account,delayed['id'])['can_order'])
            for action in (lambda:self.quote(pack),lambda:self.order(delayed,'switch-delayed'),
                    lambda:claim_payment_dispatch(self.account,attempt['id'])):
                with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
                    action()
        from .models import PaymentAttempt
        self.assertEqual(PaymentAttempt.objects.get(pk=attempt['id']).state,'created')

    def test_expiry_rechecked_before_preparing_and_claiming_unpaid_credit_order(self):
        from .billing import prepare_payment_attempt, claim_payment_dispatch
        from .models import SubscriptionPeriod, PaymentAttempt
        pack = fixture_product()
        self.subscribed()
        prepared_order = self.order(self.quote(pack),'before-expiry')
        unprepared_order = self.order(self.quote(pack),'prepare-after-expiry')
        expiry = self.now+timedelta(minutes=5)
        attempt = prepare_payment_attempt(self.account,self.user,prepared_order['id'],'before-expiry',expires_at=expiry)
        SubscriptionPeriod.objects.update(ends_at=self.now-timedelta(seconds=1))
        self.assertEqual(prepare_payment_attempt(self.account,self.user,prepared_order['id'],'before-expiry',expires_at=expiry),attempt)
        with self.assertRaisesRegex(ValueError,'CREDITS_SUBSCRIPTION_REQUIRED'):
            prepare_payment_attempt(self.account,self.user,unprepared_order['id'],'after-expiry',expires_at=expiry)
        with self.assertRaisesRegex(ValueError,'CREDITS_SUBSCRIPTION_REQUIRED'):
            claim_payment_dispatch(self.account,attempt['id'])
        row = PaymentAttempt.objects.get(pk=attempt['id'])
        self.assertEqual(row.state,'created')
        self.assertIsNone(row.dispatched_at)
        self.assertFalse(row.events.exists())
        SubscriptionPeriod.objects.update(ends_at=self.now+timedelta(days=30))
        self.assertTrue(claim_payment_dispatch(self.account,attempt['id'])['dispatch'])
        self.assertFalse(claim_payment_dispatch(self.account,attempt['id'])['dispatch'])

    def test_expiry_does_not_discard_late_received_money_or_old_unexpired_credits(self):
        from .billing import prepare_payment_attempt, claim_payment_dispatch
        from .models import SubscriptionPeriod
        pack = fixture_product()
        self.subscribed()
        quote = self.quote(pack)
        order = self.order(quote,'paid-pack')
        attempt = prepare_payment_attempt(self.account,self.user,order['id'],'paid-pack',
            expires_at=self.now+timedelta(minutes=5))
        self.assertTrue(claim_payment_dispatch(self.account,attempt['id'])['dispatch'])
        SubscriptionPeriod.objects.update(ends_at=self.now-timedelta(seconds=1))
        with override_settings(BILLING_CREDITS_PURCHASE_ENABLED=False):
            fact = record_confirmed_payment(self.account,order['id'],channel='fixture',
                merchant_reference='fixture-shenzhen',transaction_reference='late-paid-pack',
                amount_minor=731,currency='CNY',paid_at=self.now,evidence_hash='a'*64)
            self.assertEqual(fact['disposition'],'accepted')
            self.assertEqual(fulfill_paid_order(self.account,order['id'])['status'],'fulfilled')
            self.assertEqual(self.order(quote,'paid-pack')['id'],order['id'])
            self.assertFalse(claim_payment_dispatch(self.account,attempt['id'])['dispatch'])
        self.assertEqual((PaymentFact.objects.count(),CreditBatch.objects.count()),(1,1))
        held = reserve_credits(self.account,'retained-paid-balance','1')
        settle_credits(self.account,held['id'],'1')
        self.assertEqual(credits_summary(self.account)['consumed'],'1')
        self.assertEqual(CreditBatch.objects.get().source,'purchased')

    def test_service_cutoffs_and_confirmed_signing_failure_share_purchase_eligibility(self):
        from .billing import purchase_catalog
        from .models import Subscription
        pack = fixture_product()
        subscription = self.subscribed()
        Subscription.objects.filter(pk=subscription.pk).update(mandate={
            'renewal_setup_failure':{'status':'confirmed_failed','evidence_hash':'f'*64}},cancellation_state='none')
        self.assertTrue(next(item for item in purchase_catalog(self.account)['items'] if item['kind']=='credits')['quote_available'])
        for field in ('superseded_at','refund_cutoff_at'):
            with self.subTest(cutoff=field):
                try:
                    Subscription.objects.filter(pk=subscription.pk).update(**{field:self.now+timedelta(days=1)})
                    self.assertIn('id',self.quote(pack))
                    Subscription.objects.filter(pk=subscription.pk).update(**{field:self.now-timedelta(seconds=1)})
                    with self.assertRaisesRegex(ValueError,'CREDITS_SUBSCRIPTION_REQUIRED'):
                        self.quote(pack)
                finally:
                    Subscription.objects.filter(pk=subscription.pk).update(**{field:None})

    def test_business_purchase_uses_same_paid_service_rule_without_member_limits(self):
        from .billing import purchase_catalog, purchase_quote_detail, prepare_payment_attempt, claim_payment_dispatch
        from .models import SubscriptionPeriod
        self.account.kind='business'; self.account.personal_owner=None
        self.account.save(update_fields=['kind','personal_owner'])
        Membership.objects.create(account=self.account,user=self.user,role='owner')
        self.product = ProductVersion.objects.create(code='teams',version=1,kind='subscription',
            audience='business',billing_cycle='monthly',minimum_seats=2,unit_amount_minor=19900,credits_quantity='10',
            entitlements={'resource_mode':'seat_included','benefits':{'zh':['测试权益'],'en':['Test benefit']}},
            entitlement_version='fixture-1',credit_rule_version='fixture-1',terms_version='fixture-1',
            state='published',effective_at=self.now-timedelta(days=1))
        pack = fixture_product('business')
        with self.assertRaisesRegex(ValueError,'CREDITS_SUBSCRIPTION_REQUIRED'):
            self.quote(pack)
        self.subscribed()
        quote = self.quote(pack)
        order = self.order(quote,'business-pack')
        attempt = prepare_payment_attempt(self.account,self.user,order['id'],'business-pack',
            expires_at=self.now+timedelta(minutes=5))
        delayed = self.quote(pack)
        SubscriptionPeriod.objects.update(ends_at=self.now-timedelta(seconds=1))
        self.assertEqual(next(item for item in purchase_catalog(self.account)['items'] if item['kind']=='credits')['unavailable_reason'],
            'CREDITS_SUBSCRIPTION_REQUIRED')
        self.assertEqual(purchase_quote_detail(self.account,delayed['id'])['unavailable_reason'],'CREDITS_SUBSCRIPTION_REQUIRED')
        for action in (lambda:self.order(delayed,'business-expired'),lambda:claim_payment_dispatch(self.account,attempt['id'])):
            with self.assertRaisesRegex(ValueError,'CREDITS_SUBSCRIPTION_REQUIRED'):
                action()

    def test_closed_subscription_with_unresolved_receipt_does_not_allow_repurchase(self):
        quote = self.quote()
        order = self.order(quote,'late-source')
        Order.objects.filter(pk=order['id']).update(status='closed')
        record_confirmed_payment(self.account,order['id'],channel='fixture',
            merchant_reference='fixture-shenzhen',transaction_reference='late-source',
            amount_minor=19900,currency='CNY',paid_at=self.now,evidence_hash='a'*64)
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            self.quote()
        self.assertEqual(self.order(quote,'late-source')['id'],order['id'])


@override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
class CommerceTests(TransactionTestCase):
    def setUp(self):
        self.user, self.account = fixture_account()
        self.other_user, self.other = fixture_account("other")
        self.product = fixture_product()
        self.now = timezone.now()

    def quote(self):
        return create_quote(self.account, self.user, self.product.pk, 1, self.now + timedelta(minutes=5), channel="fixture", buyer_region="CN")

    def test_credit_refund_assessment_uses_owned_order_and_full_unused_balance(self):
        from .billing import credit_refund_assessment
        order = self.order()
        self.assertEqual(credit_refund_assessment(self.account,order['id'])['reason'],'payment_not_fulfilled')
        record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='refund-source',amount_minor=731,currency='CNY',paid_at=self.now,evidence_hash='b'*64)
        before = CreditEntry.objects.count()
        full = credit_refund_assessment(self.account,order['id'])
        self.assertFalse(full['can_submit'])
        self.assertEqual(full['reference_minor'],'731')
        self.assertEqual(full['unused_quantity'],self.product.credits_quantity)
        self.assertEqual(CreditEntry.objects.count(),before)
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            credit_refund_assessment(self.other,order['id'])
        reservation = reserve_credits(self.account,'refund-task','1')
        self.assertEqual(credit_refund_assessment(self.account,order['id'])['reason'],'task_settlement_required')
        settle_credits(self.account,reservation['id'],'1')
        partial = credit_refund_assessment(self.account,order['id'])
        self.assertEqual(partial['reason'],'policy_review_required')
        from fractions import Fraction
        expected = Fraction(731)*(Fraction(self.product.credits_quantity)-1)/Fraction(self.product.credits_quantity)
        self.assertEqual(partial['reference_minor'], str((2*expected.numerator+expected.denominator)//(2*expected.denominator)))
        self.assertEqual(partial['unrounded_minor'],{'numerator':str(expected.numerator),'denominator':str(expected.denominator)})
        record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='refund-source-duplicate',amount_minor=731,currency='CNY',paid_at=self.now,evidence_hash='c'*64)
        self.assertEqual(credit_refund_assessment(self.account,order['id'])['reason'],'payment_review_required')

    def test_credit_refund_assessment_rejects_expiry_freeze_and_spent_balance(self):
        from .billing import credit_refund_assessment, freeze_credits, resolve_credit_freeze
        order = self.order()
        record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='refund-boundaries',amount_minor=731,currency='CNY',paid_at=self.now,evidence_hash='b'*64)
        batch = CreditBatch.objects.get(order_id=order['id'])
        with patch('backend.commerce.billing.timezone.now',return_value=batch.expires_at):
            self.assertEqual(credit_refund_assessment(self.account,order['id'])['reason'],'credits_expired')
        hold = freeze_credits(self.account,batch.pk,'refund-assessment-hold','1')
        self.assertEqual(credit_refund_assessment(self.account,order['id'])['reason'],'refund_review_required')
        resolve_credit_freeze(self.account,hold['id'],'released',evidence_hash='c'*64)
        reservation = reserve_credits(self.account,'spend-refund-balance',batch.initial_quantity)
        settle_credits(self.account,reservation['id'],batch.initial_quantity)
        self.assertEqual(credit_refund_assessment(self.account,order['id'])['reason'],'no_unused_credits')

    def test_credit_refund_assessment_http_is_owned_and_read_only(self):
        order = self.order()
        Membership.objects.create(account=self.account,user=self.user,role='owner')
        Membership.objects.create(account=self.other,user=self.other_user,role='owner')
        url = f"/api/billing/orders/{order['id']}/refund-assessment"
        self.assertEqual(self.client.get(url).status_code,401)
        self.client.force_login(self.user)
        self.client.get('/api/auth/session')
        response = self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()['can_submit'])
        self.assertEqual(self.client.post(url,{}).status_code,405)
        self.assertEqual(self.client.get(url,HTTP_X_BILLING_ACCOUNT=str(self.other.pk)).status_code,409)
        self.client.force_login(self.other_user)
        self.client.get('/api/auth/session')
        self.assertEqual(self.client.get(url).status_code,404)

    def test_credit_refund_funds_and_credits_reserve_and_resolve_together(self):
        from .billing import reserve_credit_refund, resolve_credit_refund, resolve_credit_freeze
        from .models import CreditRefund
        order = self.order()
        record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='refund-funds',amount_minor=731,currency='CNY',paid_at=self.now,evidence_hash='b'*64)
        values = dict(review_evidence_hash='c'*64)
        held = reserve_credit_refund(self.account,order['id'],'reviewed-refund',**values)
        self.assertEqual(held['amount_minor'],'731')
        self.assertEqual(held['state'],'reserved')
        detail = order_detail(self.account,order['id'])
        self.assertEqual(detail['refunds'][0]['state'],'reserved')
        self.assertEqual(detail['refunds'][0]['amount_minor'],'731')
        self.assertNotIn('review_evidence_hash',detail['refunds'][0])
        self.assertNotIn('evidence_hash',detail['refunds'][0])
        self.assertEqual(credits_summary(self.account)['available'],'0')
        self.assertEqual(held,reserve_credit_refund(self.account,order['id'],'reviewed-refund',**values))
        with self.assertRaisesRegex(ValueError,'REFUND_NOT_ELIGIBLE'):
            reserve_credit_refund(self.account,order['id'],'second-refund',**values)
        with self.assertRaisesRegex(ValueError,'REFUND_EVIDENCE_REQUIRED'):
            resolve_credit_refund(self.account,held['id'],'failed',evidence_hash='d'*64)
        self.assertEqual(CreditRefund.objects.get(pk=held['id']).state,'reserved')
        with self.assertRaisesRegex(ValueError,'REFUND_FUNDS_RESOLUTION_REQUIRED'):
            resolve_credit_freeze(self.account,CreditRefund.objects.get(pk=held['id']).freeze_id,'released',evidence_hash='d'*64)
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            resolve_credit_freeze(self.other,CreditRefund.objects.get(pk=held['id']).freeze_id,'released',evidence_hash='d'*64)
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            resolve_credit_refund(self.other,held['id'],'refunded',evidence_hash='d'*64)
        with patch.object(CreditRefund,'save',side_effect=RuntimeError('fixture write failure')):
            with self.assertRaisesRegex(RuntimeError,'fixture write failure'):
                resolve_credit_refund(self.account,held['id'],'refunded',evidence_hash='d'*64)
        self.assertEqual(credits_summary(self.account)['refunded'],'0')
        result = resolve_credit_refund(self.account,held['id'],'refunded',evidence_hash='d'*64)
        self.assertEqual(result,resolve_credit_refund(self.account,held['id'],'refunded',evidence_hash='d'*64))
        self.assertEqual(CreditRefund.objects.count(),1)
        self.assertEqual(credits_summary(self.account)['refunded'],self.product.credits_quantity)
        self.assertEqual(order_detail(self.account,order['id'])['refunds'][0]['state'],'refunded')
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
            resolve_credit_refund(self.account,held['id'],'released',evidence_hash='e'*64)

    def test_credit_refund_release_reassesses_and_reservation_rolls_back(self):
        from .billing import reserve_credit_refund, resolve_credit_refund
        from .models import CreditRefund
        order = self.order()
        record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='refund-release',amount_minor=731,currency='CNY',paid_at=self.now,evidence_hash='b'*64)
        with patch.object(CreditRefund.objects,'create',side_effect=RuntimeError('fixture failure')):
            with self.assertRaisesRegex(RuntimeError,'fixture failure'):
                reserve_credit_refund(self.account,order['id'],'fail',review_evidence_hash='c'*64)
        self.assertEqual(credits_summary(self.account)['frozen'],'0')
        hold = reserve_credit_refund(self.account,order['id'],'r'*128,review_evidence_hash='c'*64)
        resolve_credit_refund(self.account,hold['id'],'released',evidence_hash='d'*64)
        self.assertEqual(credits_summary(self.account)['available'],self.product.credits_quantity)
        replay = reserve_credit_refund(self.account,order['id'],'r'*128,review_evidence_hash='c'*64)
        self.assertEqual(replay['state'],'released')
        task = reserve_credits(self.account,'post-release-use','1')
        settle_credits(self.account,task['id'],'1')
        reassessed = reserve_credit_refund(self.account,order['id'],'reassess',review_evidence_hash='e'*64)
        self.assertEqual(reassessed['amount_minor'],'658')
        self.assertEqual(CreditRefund.objects.count(),2)

    def test_internal_billing_check_reports_without_changing_records(self):
        from .billing import check_billing_integrity, reserve_credit_refund
        from .models import CreditFreeze
        order = self.order()
        self.assertEqual(check_billing_integrity(self.account)['findings'],[])
        record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='integrity-check',amount_minor=731,currency='CNY',paid_at=self.now,evidence_hash='b'*64)
        self.assertEqual(check_billing_integrity(self.account)['findings'],[])
        refund = reserve_credit_refund(self.account,order['id'],'integrity-refund',review_evidence_hash='c'*64)
        self.assertEqual(check_billing_integrity(self.account)['findings'],[])
        # Deliberate storage corruption: reports must not silently repair it.
        CreditFreeze.objects.filter(creditrefund__pk=refund['id']).update(state='released')
        Order.objects.filter(pk=order['id']).update(status='paid_pending_entitlement')
        before = CreditEntry.objects.count()
        report = check_billing_integrity(self.account)
        codes = {item['code'] for item in report['findings']}
        self.assertIn('PAID_DELIVERY_PENDING',codes)
        self.assertIn('REFUND_LEDGER_MISMATCH',codes)
        self.assertEqual(CreditEntry.objects.count(),before)
        self.assertEqual(Order.objects.get(pk=order['id']).status,'paid_pending_entitlement')
        self.assertEqual(check_billing_integrity(self.other)['findings'],[])
        self.assertNotIn('evidence_hash',str(report))
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from io import StringIO
        import json
        output = StringIO()
        with self.assertRaisesRegex(CommandError,'BILLING_REVIEW_REQUIRED'):
            call_command('check_billing_integrity',account=self.account.pk,stdout=output)
        self.assertEqual(json.loads(output.getvalue())['findings'],report['findings'])
        output = StringIO()
        call_command('check_billing_integrity',account=self.other.pk,stdout=output)
        self.assertEqual(json.loads(output.getvalue())['scope'],'internal_ledger_only')

    def test_billing_issue_recording_deduplicates_and_never_auto_closes(self):
        from .billing import record_billing_findings
        from .models import BillingIssue
        order = self.order()
        record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='issue-recording',amount_minor=731,currency='CNY',paid_at=self.now,evidence_hash='b'*64)
        Order.objects.filter(pk=order['id']).update(status='paid_pending_entitlement')
        first = record_billing_findings(self.account)
        self.assertEqual(first['created'],1)
        issue = BillingIssue.objects.get()
        first_seen = issue.first_seen_at
        later = timezone.now()+timedelta(seconds=10)
        with patch('backend.commerce.billing.timezone.now',return_value=later):
            again = record_billing_findings(self.account)
        self.assertEqual(again['created'],0)
        issue.refresh_from_db()
        self.assertEqual(issue.first_seen_at,first_seen)
        self.assertEqual(issue.last_seen_at,later)
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from io import StringIO
        output = StringIO()
        with self.assertRaisesRegex(CommandError,'BILLING_REVIEW_REQUIRED'):
            call_command('check_billing_integrity',account=self.account.pk,record=True,stdout=output)
        self.assertIn('"created": 0',output.getvalue())
        Order.objects.filter(pk=order['id']).update(status='fulfilled')
        self.assertEqual(record_billing_findings(self.account)['observed'],0)
        issue.refresh_from_db()
        self.assertEqual(issue.state,'open')
        self.assertEqual(issue.last_seen_at,later)
        self.assertEqual(record_billing_findings(self.other)['created'],0)
        self.assertEqual(BillingIssue.objects.count(),1)

    def test_business_subscription_resource_configuration_is_checked_before_quote(self):
        from .models import Quote
        business = BillingAccount.objects.create(kind='business', name='configuration-check')
        Membership.objects.create(account=business, user=self.user, role='owner')
        for mode, quantity in [(None, '0'), ('unknown', '10'), ('shared_only', '1')]:
            with self.subTest(mode=mode, quantity=quantity):
                product = ProductVersion.objects.create(code=f'fixture_{mode}', version=1,
                    kind='subscription', audience='business', billing_cycle='monthly', minimum_seats=2,
                    unit_amount_minor=100, entitlements={'resource_mode': mode}, credits_quantity=quantity,
                    entitlement_version='fixture-1', credit_rule_version='fixture-1', terms_version='fixture-1',
                    state='published', effective_at=self.now-timedelta(days=1))
                with self.assertRaisesRegex(ValueError, 'CONFIG_INCOMPLETE'):
                    create_quote(business, self.user, product.pk, 2,
                        self.now + timedelta(minutes=5), channel='fixture', buyer_region='CN')
                self.assertEqual(Quote.objects.filter(account=business).count(), 0)

    def order(self):
        quote = self.quote()
        return create_order(self.account, self.user, {"quote_id": quote["id"], "agreement_acceptance": "fixture-1"}, "purchase-1")

    def grant(self, quantity="10", key="grant", expires_at=None, source="purchased"):
        return grant_credits(self.account, source, key, quantity, expires_at or self.now + timedelta(days=10))

    def test_task_cost_replay_is_exact_and_does_not_charge_credits(self):
        from .billing import record_task_cost, task_cost_summary
        self.grant()
        task = reserve_credits(self.account,'task-cost','5')
        values = dict(source='fixture-model',event_key='call-1',resource='fixture-resource',
            usage={'input_tokens':'12','output_tokens':'3'},retry_index=0,outcome='succeeded',
            cost_amount='0.000000000000000000000001',currency='CNY',occurred_at=self.now,evidence_hash='a'*64)
        event = record_task_cost(self.account,task['id'],**values)
        self.assertEqual(event,record_task_cost(self.account,task['id'],**values))
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
            record_task_cost(self.account,task['id'],**{**values,'cost_amount':'1'})
        summary = task_cost_summary(self.account,task['id'])
        self.assertEqual(summary['costs'],{'CNY':'0.000000000000000000000001'})
        self.assertEqual(summary['event_count'],1)
        self.assertEqual(credits_summary(self.account)['consumed'],'0')
        self.assertEqual(credits_summary(self.account)['reserved'],'5')

    def test_task_cost_legacy_events_remain_unclassified(self):
        from .billing import record_task_cost, task_cost_summary
        from .models import TaskCostEvent
        self.grant()
        task = reserve_credits(self.account,'cost-legacy','5')
        values = dict(source='fixture-model',event_key='legacy',resource='fixture-resource',
            usage={'input_tokens':'12'},retry_index=0,outcome='succeeded',cost_amount='0.1',
            currency='CNY',occurred_at=self.now,evidence_hash='a'*64)
        event = record_task_cost(self.account,task['id'],**values)
        row = TaskCostEvent.objects.get(pk=event['id'])
        before = (row.payload.copy(),row.request_hash,credits_summary(self.account))
        summary = task_cost_summary(self.account,task['id'])
        self.assertIn('billing_breakdown',summary)
        self.assertEqual(summary['billing_breakdown'],{
            'task_usage':{'event_count':0,'unknown_cost_events':0,'costs':{}},
            'sage_fault':{'event_count':0,'unknown_cost_events':0,'costs':{}},
            'unclassified':{'event_count':1,'unknown_cost_events':0,'costs':{'CNY':'0.1'}}})
        self.assertNotIn('billing_category',row.payload)
        self.assertEqual(event,record_task_cost(self.account,task['id'],**values))
        row.refresh_from_db()
        self.assertEqual((row.payload,row.request_hash,credits_summary(self.account)),before)

    def test_task_cost_breakdown_separates_usage_and_sage_fault_without_debits(self):
        from django.test.utils import CaptureQueriesContext
        from .billing import record_task_cost, task_cost_summary
        self.grant()
        task = reserve_credits(self.account,'cost-breakdown','5')
        before = (credits_summary(self.account),CreditEntry.objects.count())
        values = dict(source='fixture-model',resource='fixture-resource',usage={'api_calls':'1'},
            retry_index=1,occurred_at=self.now,evidence_hash='a'*64)
        for key,category,cost,currency,outcome in (
                ('cancel','task_usage','0.1','CNY','cancelled'),
                ('partial','task_usage','0.2','CNY','failed'),
                ('foreign','task_usage','2','USD','succeeded'),
                ('fault','sage_fault','0.4','CNY','failed'),
                ('unknown','sage_fault',None,None,'succeeded')):
            record_task_cost(self.account,task['id'],event_key=key,billing_category=category,
                cost_amount=cost,currency=currency,outcome=outcome,**values)
        with CaptureQueriesContext(connection) as queries:
            summary = task_cost_summary(self.account,task['id'])
        self.assertEqual(summary,{'event_count':5,'unknown_cost_events':1,
            'costs':{'CNY':'0.7','USD':'2'},'billing_breakdown':{
                'task_usage':{'event_count':3,'unknown_cost_events':0,'costs':{'CNY':'0.3','USD':'2'}},
                'sage_fault':{'event_count':2,'unknown_cost_events':1,'costs':{'CNY':'0.4'}},
                'unclassified':{'event_count':0,'unknown_cost_events':0,'costs':{}}}})
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for q in queries))
        self.assertEqual((credits_summary(self.account),CreditEntry.objects.count()),before)

    def test_task_cost_billing_category_rejects_invalid_values_and_replay_changes(self):
        from .billing import record_task_cost
        from .models import TaskCostEvent
        self.grant()
        task = reserve_credits(self.account,'cost-category','5')
        values = dict(source='fixture-model',event_key='call',resource='fixture-resource',usage={'input_tokens':'1'},
            retry_index=0,outcome='failed',cost_amount=None,currency=None,occurred_at=self.now,evidence_hash='a'*64)
        for category in ('','unclassified','billable',True,[],{}):
            with self.subTest(category=category), self.assertRaisesRegex(ValueError,'INVALID_COST_EVENT'):
                record_task_cost(self.account,task['id'],billing_category=category,**values)
        self.assertEqual(TaskCostEvent.objects.count(),0)
        event = record_task_cost(self.account,task['id'],billing_category='sage_fault',**values)
        self.assertEqual(event,record_task_cost(self.account,task['id'],billing_category='sage_fault',**values))
        for category in ('task_usage',None):
            with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
                record_task_cost(self.account,task['id'],billing_category=category,**values)
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            record_task_cost(self.other,task['id'],billing_category='task_usage',**values)
        self.assertEqual(TaskCostEvent.objects.count(),1)

    def test_task_cost_completion_retains_billing_category_after_settlement(self):
        from .billing import record_task_cost, complete_task_cost, task_cost_summary
        from .models import TaskCostEvent
        self.grant()
        task = reserve_credits(self.account,'cost-classified-completion','5')
        settle_credits(self.account,task['id'],'2')
        before = (credits_summary(self.account),CreditEntry.objects.count())
        event = record_task_cost(self.account,task['id'],source='fixture-model',event_key='call',
            resource='fixture-resource',usage={'input_tokens':'1'},retry_index=1,outcome='failed',
            cost_amount=None,currency=None,occurred_at=self.now,evidence_hash='a'*64,billing_category='sage_fault')
        complete_task_cost(self.account,event['id'],source='fixture-model',request_key='receipt',
            cost_amount='0.123456789012345678901',currency='CNY',occurred_at=self.now,evidence_hash='b'*64)
        summary = task_cost_summary(self.account,task['id'])
        self.assertEqual(summary['billing_breakdown']['sage_fault'],{'event_count':1,'unknown_cost_events':0,
            'costs':{'CNY':'0.123456789012345678901'}})
        self.assertEqual(summary['billing_breakdown']['task_usage']['costs'],{})
        self.assertEqual(TaskCostEvent.objects.get(pk=event['id']).payload['billing_category'],'sage_fault')
        self.assertEqual((credits_summary(self.account),CreditEntry.objects.count()),before)

    def test_task_cost_summary_rejects_corrupt_billing_category(self):
        from .billing import record_task_cost, task_cost_summary
        from .models import TaskCostEvent
        self.grant()
        task = reserve_credits(self.account,'cost-corrupt-category','5')
        event = record_task_cost(self.account,task['id'],source='fixture-model',event_key='call',
            resource='fixture-resource',usage={'input_tokens':'1'},retry_index=0,outcome='succeeded',
            cost_amount='0.1',currency='CNY',occurred_at=self.now,evidence_hash='a'*64)
        row = TaskCostEvent.objects.get(pk=event['id'])
        for category in ('unknown',None,True,[],{}):
            # Simulate storage corruption; normal model writes are immutable.
            TaskCostEvent.objects.filter(pk=row.pk).update(payload={**row.payload,'billing_category':category})
            with self.subTest(category=category), self.assertRaisesRegex(ValueError,'INVALID_COST_EVENT'):
                task_cost_summary(self.account,task['id'])
        self.assertEqual(credits_summary(self.account)['consumed'],'0')

    def test_task_cost_unknown_and_late_retry_remain_separate_from_user_charge(self):
        from .billing import record_task_cost, task_cost_summary
        self.grant()
        task = reserve_credits(self.account,'task-cost','5')
        settle_credits(self.account,task['id'],'2')
        for key,cost,currency,outcome in [('a',None,None,'failed'),('b','1','CNY','succeeded'),('c','2','USD','succeeded')]:
            record_task_cost(self.account,task['id'],source='fixture-data',event_key=key,resource='fixture-resource',
                usage={'api_calls':'1'},retry_index=1,outcome=outcome,cost_amount=cost,currency=currency,
                occurred_at=self.now,evidence_hash='a'*64)
        summary = task_cost_summary(self.account,task['id'])
        self.assertEqual(summary['costs'],{'CNY':'1','USD':'2'})
        self.assertEqual(summary['unknown_cost_events'],1)
        self.assertEqual(credits_summary(self.account)['consumed'],'2')

    def test_task_cost_rejects_cross_account_and_invalid_telemetry(self):
        from .billing import record_task_cost, task_cost_summary
        self.grant()
        task = reserve_credits(self.account,'task-cost','5')
        values = dict(source='fixture-model',event_key='call-1',resource='fixture-resource',usage={'input_tokens':'1'},
            retry_index=0,outcome='succeeded',cost_amount=None,currency=None,occurred_at=self.now,evidence_hash='a'*64)
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            record_task_cost(self.other,task['id'],**values)
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            task_cost_summary(self.other,task['id'])
        for change in ({'usage':{'prompt':'sensitive text'}},{'usage':{'input_tokens':True}},
                       {'cost_amount':'1'},{'retry_index':True},{'outcome':'pending'},{'evidence_hash':''}):
            with self.assertRaises(ValueError):
                record_task_cost(self.account,task['id'],**{**values,**change})
        self.assertEqual(task_cost_summary(self.account,task['id'])['event_count'],0)

    def test_task_cost_event_cannot_move_tasks_or_mutate_evidence(self):
        from .billing import record_task_cost
        from .models import TaskCostEvent
        self.grant()
        first = reserve_credits(self.account,'cost-task-1','2')
        second = reserve_credits(self.account,'cost-task-2','2')
        values = dict(source='fixture-model',event_key='call-1',resource='fixture-resource',usage={'input_tokens':'1'},
            retry_index=0,outcome='failed',cost_amount=None,currency=None,occurred_at=self.now,evidence_hash='a'*64)
        event = record_task_cost(self.account,first['id'],**values)
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
            record_task_cost(self.account,second['id'],**values)
        row = TaskCostEvent.objects.get(pk=event['id'])
        row.payload = {**row.payload,'outcome':'succeeded'}
        with self.assertRaisesRegex(ValueError,'IMMUTABLE_BILLING_SNAPSHOT'):
            row.save()
        self.assertEqual(TaskCostEvent.objects.count(),1)

    def test_credit_transactions_reads_exact_ledger_actions_without_writes(self):
        from django.test.utils import CaptureQueriesContext
        from .billing import credit_transactions_page, freeze_credits, resolve_credit_freeze
        batch = self.grant('10.000000000000000000000000000001')
        task = reserve_credits(self.account,'private-task-key','5')
        settle_credits(self.account,task['id'],'2')
        hold = freeze_credits(self.account,batch['id'],'private-freeze','2')
        resolve_credit_freeze(self.account,hold['id'],'released',evidence_hash='a'*64)
        hold = freeze_credits(self.account,batch['id'],'private-refund','3')
        resolve_credit_freeze(self.account,hold['id'],'refunded',evidence_hash='b'*64)
        with CaptureQueriesContext(connection) as queries:
            page = credit_transactions_page(self.account)
        self.assertTrue(all(not query['sql'].lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for query in queries))
        self.assertEqual(page['account_id'],str(self.account.pk))
        self.assertEqual(page['total'],8)
        self.assertEqual({row['kind'] for row in page['items']},{'grant','reserve','settle','release','freeze','unfreeze','refund'})
        grant = next(row for row in page['items'] if row['kind']=='grant')
        self.assertEqual(grant['quantity'],'10.000000000000000000000000000001')
        self.assertTrue(all(set(row)=={'id','kind','quantity','batch_id','source','order_id','reservation_id','seat_id','occurred_at'} for row in page['items']))
        self.assertTrue(all(isinstance(row['id'],str) for row in page['items']))
        self.assertEqual(credit_transactions_page(self.other)['items'],[])

    def test_credit_transactions_http_is_owned_paginated_and_read_only(self):
        for index in range(51):
            self.grant('1',key=f'ledger-{index}')
        Membership.objects.create(account=self.account,user=self.user,role='owner')
        Membership.objects.create(account=self.other,user=self.other_user,role='owner')
        url = '/api/billing/credits/transactions'
        self.assertEqual(self.client.get(url).status_code,401)
        self.client.force_login(self.user)
        self.client.get('/api/auth/session')
        page = self.client.get(url).json()
        self.assertEqual((len(page['items']),page['total'],page['offset'],page['next_offset']),(50,51,0,50))
        last = self.client.get(url+'?offset=50').json()
        self.assertEqual((len(last['items']),last['offset'],last['next_offset']),(1,50,None))
        self.assertFalse(set(row['id'] for row in page['items']) & set(row['id'] for row in last['items']))
        for query in ('?offset=-1','?offset=0&offset=1','?private=1','?offset=1000000000'):
            self.assertEqual(self.client.get(url+query).status_code,422)
        self.assertEqual(self.client.post(url,{}).status_code,405)
        self.assertEqual(self.client.get(url,HTTP_X_BILLING_ACCOUNT=str(self.other.pk)).status_code,409)
        Membership.objects.filter(user=self.user,account=self.account).update(role='member')
        self.assertEqual(self.client.get(url).status_code,403)
        self.client.force_login(self.other_user)
        self.client.get('/api/auth/session')
        self.assertEqual(self.client.get(url).json()['items'],[])

    def test_credit_transactions_unknown_and_inconsistent_refs_never_claim_charge(self):
        from .billing import credit_transactions_page
        self.grant()
        CreditEntry.objects.update(kind='future-kind')
        row = credit_transactions_page(self.account)['items'][0]
        self.assertEqual(row['kind'],'unknown')
        self.assertIsNone(row['quantity'])
        CreditEntry.objects.update(kind='grant')
        self.grant('2',key='foreign-task')
        grant_credits(self.other,'purchased','foreign','2',self.now+timedelta(days=1))
        foreign = reserve_credits(self.other,'private-foreign','1')
        CreditEntry.objects.filter(batch__account=self.account).update(reservation_id=foreign['id'])
        with self.assertRaisesRegex(ValueError,'LEDGER_INCONSISTENT'):
            credit_transactions_page(self.account)

    def test_billing_notifications_payment_is_once_and_survives_delivery_failure(self):
        from .billing import notifications_page
        from .models import BillingNotification
        order = self.order()
        values = dict(channel='fixture',merchant_reference='fixture-shenzhen',transaction_reference='notice-payment',
            amount_minor=731,currency='CNY',paid_at=self.now,evidence_hash='b'*64)
        with patch('backend.commerce.billing.fulfill_paid_order',side_effect=ValueError('DELIVERY_FAILED')):
            with self.assertRaisesRegex(ValueError,'DELIVERY_FAILED'):
                record_confirmed_payment(self.account,order['id'],**values)
        record_confirmed_payment(self.account,order['id'],**values)
        record_confirmed_payment(self.account,order['id'],**{**values,'transaction_reference':'duplicate'})
        page = notifications_page(self.account)
        self.assertEqual(page['account_id'],str(self.account.pk))
        self.assertEqual(page['total'],1)
        self.assertEqual(page['items'][0]['type'],'payment_received')
        self.assertEqual(page['items'][0]['amount_minor'],'731')
        self.assertEqual(page['items'][0]['order_id'],order['id'])
        self.assertNotIn('evidence_hash',page['items'][0])
        row = BillingNotification.objects.get()
        row.type = 'cancellation_confirmed'
        with self.assertRaisesRegex(ValueError,'IMMUTABLE_BILLING_SNAPSHOT'):
            row.save()

    def test_billing_notifications_refund_states_and_rollback(self):
        from .billing import reserve_credit_refund, resolve_credit_refund, notifications_page
        from .models import BillingNotification
        order = self.order()
        record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='notice-refund',amount_minor=731,currency='CNY',paid_at=self.now,evidence_hash='b'*64)
        with self.assertRaisesRegex(ValueError,'ROLLBACK'):
            with transaction.atomic():
                reserve_credit_refund(self.account,order['id'],'rolled-back',review_evidence_hash='c'*64)
                raise ValueError('ROLLBACK')
        self.assertEqual(BillingNotification.objects.count(),1)
        item = reserve_credit_refund(self.account,order['id'],'refund',review_evidence_hash='c'*64)
        reserve_credit_refund(self.account,order['id'],'refund',review_evidence_hash='c'*64)
        resolve_credit_refund(self.account,item['id'],'released',evidence_hash='d'*64)
        resolve_credit_refund(self.account,item['id'],'released',evidence_hash='d'*64)
        self.assertEqual({row['type'] for row in notifications_page(self.account)['items']},
            {'payment_received','credit_refund_reserved','credit_refund_released'})
        self.assertEqual(BillingNotification.objects.count(),3)
        second = reserve_credit_refund(self.account,order['id'],'refund-again',review_evidence_hash='c'*64)
        resolve_credit_refund(self.account,second['id'],'refunded',evidence_hash='e'*64)
        self.assertIn('credit_refund_refunded',[row['type'] for row in notifications_page(self.account)['items']])

    def test_billing_notifications_http_permissions_pagination_and_unknown(self):
        from .models import BillingNotification
        for index in range(51):
            BillingNotification.objects.create(account=self.account,type='payment_received',source_event=f'fixture:{index}',
                occurred_at=self.now,amount_minor=731,currency='CNY')
        BillingNotification.objects.filter(source_event='fixture:0').update(type='future-unknown')
        Membership.objects.create(account=self.account,user=self.user,role='owner')
        Membership.objects.create(account=self.other,user=self.other_user,role='owner')
        url = '/api/billing/notifications'
        self.assertEqual(self.client.get(url).status_code,401)
        self.client.force_login(self.user)
        self.client.get('/api/auth/session')
        page = self.client.get(url).json()
        self.assertEqual(len(page['items']),50)
        self.assertEqual(page['next_offset'],50)
        pages = page['items']+self.client.get(url+'?offset=50').json()['items']
        unknown = next(row for row in pages if row['type']=='unknown')
        self.assertIsNone(unknown['amount_minor'])
        self.assertIsNone(unknown['currency'])
        for query in ('?offset=-1','?offset=1.0','?offset=0&offset=1','?anything=1','?offset=1000000000'):
            response = self.client.get(url+query)
            self.assertEqual(response.status_code,422)
            self.assertEqual(response.json()['error']['code'],'INVALID_REQUEST')
        self.assertEqual(self.client.post(url,{}).status_code,405)
        self.assertEqual(self.client.get(url,HTTP_X_BILLING_ACCOUNT=str(self.other.pk)).status_code,409)
        Membership.objects.filter(account=self.account,user=self.user).update(role='member')
        self.assertEqual(self.client.get(url).status_code,403)
        self.client.force_login(self.other_user)
        self.client.get('/api/auth/session')
        self.assertEqual(self.client.get(url).json()['items'],[])

    def test_billing_notifications_cancellation_is_atomic_and_idempotent(self):
        from .subscriptions import cancel_subscription, confirm_cancellation
        from .models import BillingNotification, Subscription, SubscriptionPeriod
        order = self.order()
        Membership.objects.create(account=self.account,user=self.user,role='owner')
        sub = Subscription.objects.create(account=self.account,initial_order_id=order['id'],original_anchor=self.now,
            billing_timezone='Asia/Shanghai',calendar_rule_version='fixture-1',billing_cycle='monthly',
            mandate={'agreement_reference':'fixture-agreement'})
        SubscriptionPeriod.objects.create(subscription=sub,order_id=order['id'],cycle_index=0,
            starts_at=self.now,ends_at=self.now+timedelta(days=30),snapshot={'code':'fixture','credits_quantity':'0'})
        with self.assertRaisesRegex(ValueError,'ROLLBACK'):
            with transaction.atomic():
                cancel_subscription(self.account,self.user,sub.pk)
                raise ValueError('ROLLBACK')
        self.assertEqual(BillingNotification.objects.count(),0)
        cancel_subscription(self.account,self.user,sub.pk)
        cancel_subscription(self.account,self.user,sub.pk)
        confirm_cancellation(self.account,sub.pk,agreement_reference='fixture-agreement',evidence_hash='c'*64)
        confirm_cancellation(self.account,sub.pk,agreement_reference='fixture-agreement',evidence_hash='c'*64)
        self.assertEqual(set(BillingNotification.objects.values_list('type',flat=True)),
            {'cancellation_processing','cancellation_confirmed'})
        self.assertEqual(BillingNotification.objects.count(),2)

    def test_task_cost_completion_preserves_original_usage_and_credits(self):
        from .billing import record_task_cost, complete_task_cost, task_cost_summary
        from .models import TaskCostEvent, TaskCostCompletion, CreditEntry
        self.grant()
        task = reserve_credits(self.account,'cost-completion','5')
        settle_credits(self.account,task['id'],'2')
        values = dict(source='fixture-model',resource='fixture-resource',usage={'api_calls':'1'},
            retry_index=0,outcome='failed',occurred_at=self.now,evidence_hash='a'*64)
        event = record_task_cost(self.account,task['id'],event_key='unknown',cost_amount=None,currency=None,**values)
        record_task_cost(self.account,task['id'],event_key='known',cost_amount='0.1',currency='CNY',**values)
        record_task_cost(self.account,task['id'],event_key='usd',cost_amount='2',currency='USD',**values)
        original = TaskCostEvent.objects.get(pk=event['id'])
        before = (original.payload.copy(),original.request_hash,credits_summary(self.account),CreditEntry.objects.count())
        amount = '0.000000000000000000000000000001'
        completion = complete_task_cost(self.account,event['id'],source='fixture-model',request_key='receipt-1',
            cost_amount=amount,currency='CNY',occurred_at=self.now,evidence_hash='b'*64)
        self.assertEqual(completion,complete_task_cost(self.account,event['id'],source='fixture-model',request_key='receipt-1',
            cost_amount=amount,currency='CNY',occurred_at=self.now,evidence_hash='b'*64))
        original.refresh_from_db()
        self.assertEqual((original.payload,original.request_hash,credits_summary(self.account),CreditEntry.objects.count()),before)
        self.assertEqual(TaskCostEvent.objects.count(),3)
        self.assertEqual(TaskCostCompletion.objects.count(),1)
        self.assertEqual(task_cost_summary(self.account,task['id']),{'event_count':3,'unknown_cost_events':0,
            'costs':{'CNY':'0.100000000000000000000000000001','USD':'2'},'billing_breakdown':{
                'task_usage':{'event_count':0,'unknown_cost_events':0,'costs':{}},
                'sage_fault':{'event_count':0,'unknown_cost_events':0,'costs':{}},
                'unclassified':{'event_count':3,'unknown_cost_events':0,
                    'costs':{'CNY':'0.100000000000000000000000000001','USD':'2'}}}})

    def test_task_cost_completion_rejects_conflicts_known_cost_and_cross_account(self):
        from .billing import record_task_cost, complete_task_cost, task_cost_summary
        from .models import TaskCostCompletion
        self.grant()
        task = reserve_credits(self.account,'cost-completion','5')
        values = dict(source='fixture-model',resource='fixture-resource',usage={'api_calls':'1'},
            retry_index=0,outcome='failed',occurred_at=self.now,evidence_hash='a'*64)
        unknown = record_task_cost(self.account,task['id'],event_key='unknown',cost_amount=None,currency=None,**values)
        other = record_task_cost(self.account,task['id'],event_key='other',cost_amount=None,currency=None,**values)
        known = record_task_cost(self.account,task['id'],event_key='known',cost_amount='1',currency='CNY',**values)
        receipt = dict(source='fixture-model',request_key='receipt-1',cost_amount='0.10',currency='CNY',
            occurred_at=self.now,evidence_hash='b'*64)
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            complete_task_cost(self.other,unknown['id'],**receipt)
        with self.assertRaisesRegex(ValueError,'COST_SOURCE_MISMATCH'):
            complete_task_cost(self.account,unknown['id'],**{**receipt,'source':'untrusted-source'})
        with self.assertRaisesRegex(ValueError,'COST_ALREADY_KNOWN'):
            complete_task_cost(self.account,known['id'],**receipt)
        first = complete_task_cost(self.account,unknown['id'],**receipt)
        self.assertEqual(first,complete_task_cost(self.account,unknown['id'],**{**receipt,'cost_amount':'0.1'}))
        for change in ({'cost_amount':'2'},{'evidence_hash':'c'*64},{'request_key':'receipt-2'}):
            with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
                complete_task_cost(self.account,unknown['id'],**{**receipt,**change})
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
            complete_task_cost(self.account,other['id'],**receipt)
        self.assertEqual(TaskCostCompletion.objects.count(),1)
        self.assertEqual(task_cost_summary(self.account,task['id'])['unknown_cost_events'],1)

    def test_task_cost_completion_validates_evidence_and_is_immutable(self):
        from .billing import record_task_cost, complete_task_cost
        from .models import TaskCostCompletion
        self.grant()
        task = reserve_credits(self.account,'cost-completion','5')
        event = record_task_cost(self.account,task['id'],source='fixture-model',event_key='unknown',
            resource='fixture-resource',usage={'api_calls':'1'},retry_index=0,outcome='failed',
            cost_amount=None,currency=None,occurred_at=self.now,evidence_hash='a'*64)
        receipt = dict(source='fixture-model',request_key='receipt-1',cost_amount='0',currency='CNY',
            occurred_at=self.now,evidence_hash='b'*64)
        for change in ({'cost_amount':None},{'cost_amount':True},{'cost_amount':0.1},{'cost_amount':'-1'},
                       {'cost_amount':'1e-3'},{'currency':'cny'},{'currency':None},{'evidence_hash':''},
                       {'occurred_at':self.now.replace(tzinfo=None)},{'request_key':''}):
            with self.assertRaises(ValueError):
                complete_task_cost(self.account,event['id'],**{**receipt,**change})
        self.assertEqual(TaskCostCompletion.objects.count(),0)
        result = complete_task_cost(self.account,event['id'],**receipt)
        row = TaskCostCompletion.objects.get(pk=result['id'])
        for field,value in [('cost_amount','1'),('account_id',self.other.pk),('evidence_hash','c'*64)]:
            row.refresh_from_db()
            setattr(row,field,value)
            with self.assertRaisesRegex(ValueError,'IMMUTABLE_BILLING_SNAPSHOT'):
                row.save()

    def test_refund_freeze_protects_only_requested_batch_and_is_idempotent(self):
        from .billing import freeze_credits, resolve_credit_freeze
        batch = self.grant()
        self.grant('5',key='other-batch')
        hold = freeze_credits(self.account,batch['id'],'refund-1','7')
        self.assertEqual(hold,freeze_credits(self.account,batch['id'],'refund-1','7'))
        self.assertEqual(credits_summary(self.account)['available'],'8')
        self.assertEqual(credits_summary(self.account)['frozen'],'7')
        with self.assertRaisesRegex(ValueError,'INSUFFICIENT_CREDITS'):
            reserve_credits(self.account,'too-large','9')
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
            freeze_credits(self.account,batch['id'],'refund-1','6')
        with self.assertRaisesRegex(ValueError,'REFUND_EVIDENCE_REQUIRED'):
            resolve_credit_freeze(self.account,hold['id'],'refunded',evidence_hash='')
        self.assertEqual(credits_summary(self.account)['frozen'],'7')
        result = resolve_credit_freeze(self.account,hold['id'],'refunded',evidence_hash='a'*64)
        self.assertEqual(result,resolve_credit_freeze(self.account,hold['id'],'refunded',evidence_hash='a'*64))
        self.assertEqual(credits_summary(self.account)['refunded'],'7')
        self.assertEqual(credits_summary(self.account)['frozen'],'0')
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
            resolve_credit_freeze(self.account,hold['id'],'released',evidence_hash='b'*64)

    def test_freeze_rejects_active_reservation_cross_account_and_expiry(self):
        from .billing import freeze_credits
        batch = self.grant()
        held = reserve_credits(self.account,'running','2')
        with self.assertRaisesRegex(ValueError,'TASK_SETTLEMENT_REQUIRED'):
            freeze_credits(self.account,batch['id'],'refund-1','1')
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            freeze_credits(self.other,batch['id'],'refund-1','1')
        release_credits(self.account,held['id'])
        with patch('django.utils.timezone.now',return_value=self.now+timedelta(days=11)):
            with self.assertRaisesRegex(ValueError,'CREDITS_EXPIRED'):
                freeze_credits(self.account,batch['id'],'refund-1','1')

    def test_freeze_release_after_expiry_does_not_revive_balance(self):
        from .billing import freeze_credits, resolve_credit_freeze
        batch = self.grant()
        hold = freeze_credits(self.account,batch['id'],'refund-1','7')
        with patch('django.utils.timezone.now',return_value=self.now+timedelta(days=11)):
            self.assertEqual(credits_summary(self.account)['frozen'],'7')
            self.assertEqual(credits_summary(self.account)['expired'],'3')
            resolve_credit_freeze(self.account,hold['id'],'released',evidence_hash='b'*64)
            self.assertEqual(credits_summary(self.account)['available'],'0')
            self.assertEqual(credits_summary(self.account)['expired'],'10')

    def test_freeze_unknown_cross_account_and_failed_write_preserve_hold(self):
        from .billing import freeze_credits, resolve_credit_freeze
        batch = self.grant()
        hold = freeze_credits(self.account,batch['id'],'refund-1','7')
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            resolve_credit_freeze(self.other,hold['id'],'refunded',evidence_hash='a'*64)
        for outcome in ('unknown','failed','processing'):
            with self.assertRaisesRegex(ValueError,'REFUND_EVIDENCE_REQUIRED'):
                resolve_credit_freeze(self.account,hold['id'],outcome,evidence_hash='a'*64)
        with patch('backend.commerce.models.CreditFreeze.save',side_effect=RuntimeError('fixture-write-failure')):
            with self.assertRaisesRegex(RuntimeError,'fixture-write-failure'):
                resolve_credit_freeze(self.account,hold['id'],'refunded',evidence_hash='a'*64)
        self.assertEqual(credits_summary(self.account)['frozen'],'7')
        self.assertEqual(CreditEntry.objects.filter(kind='refund').count(),0)

    def test_sales_disabled_and_missing_configuration_refuse(self):
        with override_settings(BILLING_SALES_ENABLED=False):
            with self.assertRaisesRegex(ValueError, "CONFIG_INCOMPLETE"):
                self.quote()
        ProductVersion.objects.filter(pk=self.product.pk).update(unit_amount_minor=None)
        with self.assertRaisesRegex(ValueError, "CONFIG_INCOMPLETE"):
            self.quote()
        self.assertFalse(Order.objects.exists())

    def test_channel_currency_region_capability_rechecked_at_order(self):
        quote = self.quote()
        payload = {"quote_id": quote["id"], "agreement_acceptance": "fixture-1"}
        for configuration in ({"fixture": {}}, {"fixture": {**FIXTURE_CHANNELS["fixture"], "currency": "USD"}}, {"fixture": {**FIXTURE_CHANNELS["fixture"], "regions": []}}, {"fixture": {**FIXTURE_CHANNELS["fixture"], "capabilities": []}}, {"fixture": {**FIXTURE_CHANNELS["fixture"], "regions": None}}, {"fixture": {**FIXTURE_CHANNELS["fixture"], "capabilities": None}}):
            with override_settings(BILLING_APPROVED_CHANNELS=configuration):
                with self.assertRaisesRegex(ValueError, "CONFIG_INCOMPLETE"):
                    create_order(self.account, self.user, payload, "not-approved")

    def test_zero_pack_and_annual_channel_without_annual_approval_refused(self):
        ProductVersion.objects.filter(pk=self.product.pk).update(credits_quantity="0")
        with self.assertRaisesRegex(ValueError, "CONFIG_INCOMPLETE"):
            self.quote()
        ProductVersion.objects.filter(pk=self.product.pk).update(credits_quantity="1", kind="subscription", billing_cycle="annual")
        with override_settings(BILLING_APPROVED_CHANNELS={"fixture": {**FIXTURE_CHANNELS["fixture"], "capabilities": ["recurring_monthly"]}}):
            with self.assertRaisesRegex(ValueError, "CONFIG_INCOMPLETE"):
                self.quote()

    def test_receipt_wrong_merchant_or_channel_is_retained_without_delivery(self):
        order = self.order()
        for channel, merchant in [('other', 'fixture-shenzhen'), ('fixture', 'other-merchant')]:
            result = record_confirmed_payment(self.account, order['id'], channel=channel,
                merchant_reference=merchant, transaction_reference=f'{channel}:{merchant}',
                amount_minor=731, currency='CNY', paid_at=self.now, evidence_hash='b'*64)
            self.assertEqual(result['disposition'], 'identity_mismatch_review')
        self.assertEqual(PaymentFact.objects.count(), 2)
        self.assertEqual(CreditBatch.objects.count(), 0)
        self.assertIsNone(Order.objects.get(pk=order['id']).paid_at)

    def test_server_snapshot_and_idempotent_order(self):
        quote = self.quote()
        payload = {"quote_id": quote["id"], "agreement_acceptance": "fixture-1"}
        result = create_order(self.account, self.user, payload, "same")
        self.assertEqual(result, create_order(self.account, self.user, payload, "same"))
        self.assertEqual(result["amount_minor"], 731)
        self.product.state = "retired"
        self.product.save()
        self.assertEqual(order_detail(self.account, result["id"])["order"]["amount_minor"], 731)
        self.assertEqual(Order.objects.count(), 1)

    def test_published_product_and_financial_snapshots_cannot_be_overwritten(self):
        from .models import Quote
        quote = self.quote()
        order = self.order()
        self.product.unit_amount_minor = 900
        with self.assertRaisesRegex(ValueError, "IMMUTABLE_BILLING_SNAPSHOT"):
            self.product.save()
        self.product.refresh_from_db()
        self.product.state = "draft"
        with self.assertRaisesRegex(ValueError, "IMMUTABLE_BILLING_SNAPSHOT"):
            self.product.save()
        quote_model = Quote.objects.get(pk=quote["id"])
        quote_model.amount_minor = 1
        with self.assertRaisesRegex(ValueError, "IMMUTABLE_BILLING_SNAPSHOT"):
            quote_model.save()
        order_model = Order.objects.get(pk=order["id"])
        order_model.snapshot["amount_minor"] = 1
        with self.assertRaisesRegex(ValueError, "IMMUTABLE_BILLING_SNAPSHOT"):
            order_model.save()

    def test_client_money_identity_and_reused_key_conflict(self):
        quote = self.quote()
        payload = {"quote_id": quote["id"], "agreement_acceptance": "fixture-1"}
        for field in ("amount_minor", "user_id", "currency", "account_id"):
            with self.assertRaisesRegex(ValueError, "INVALID_PAYLOAD"):
                create_order(self.account, self.user, dict(payload, **{field: 1}), "bad")
        create_order(self.account, self.user, payload, "same")
        payload["quote_id"] = self.quote()["id"]
        with self.assertRaisesRegex(ValueError, "IDEMPOTENCY_CONFLICT"):
            create_order(self.account, self.user, payload, "same")

    def test_account_scope_permissions_and_quote_expiry(self):
        quote = self.quote()
        payload = {"quote_id": quote["id"], "agreement_acceptance": "fixture-1"}
        with self.assertRaisesRegex(ValueError, "FORBIDDEN"):
            create_order(self.account, self.other_user, payload, "foreign")
        with self.assertRaisesRegex(ValueError, "NOT_FOUND"):
            create_order(self.other, self.other_user, payload, "foreign")
        order = create_order(self.account, self.user, payload, "owned")
        with self.assertRaisesRegex(ValueError, "NOT_FOUND"):
            order_detail(self.other, order["id"])
        self.assertEqual(list_orders(self.other), [])
        expired = create_quote(self.account, self.user, self.product.pk, 1, self.now + timedelta(seconds=1), channel="fixture", buyer_region="CN")
        from .models import Quote
        Quote.objects.filter(pk=expired["id"]).update(expires_at=self.now - timedelta(seconds=1))
        with self.assertRaisesRegex(ValueError, "QUOTE_EXPIRED"):
            create_order(self.account, self.user, {"quote_id": expired["id"], "agreement_acceptance": "fixture-1"}, "expired")

    def test_business_member_cannot_buy_and_unknown_credits_rules_fail_closed(self):
        business = BillingAccount.objects.create(kind="business", name="organization")
        Membership.objects.create(account=business, user=self.user, role="member", is_active=True)
        with self.assertRaisesRegex(ValueError, "FORBIDDEN"):
            create_order(business, self.user, {"quote_id": self.quote()["id"], "agreement_acceptance": "fixture-1"}, "member")
        with self.assertRaisesRegex(ValueError, "CONFIG_INCOMPLETE"):
            reserve_credits(business, "task", "1")

    def test_quote_reuse_new_key_and_terms_mismatch_are_rejected(self):
        quote = self.quote()
        with self.assertRaisesRegex(ValueError, "TERMS_MISMATCH"):
            create_order(self.account, self.user, {"quote_id": quote["id"], "agreement_acceptance": "wrong"}, "first")
        payload = {"quote_id": quote["id"], "agreement_acceptance": "fixture-1"}
        create_order(self.account, self.user, payload, "first")
        with self.assertRaisesRegex(ValueError, "QUOTE_ALREADY_ORDERED"):
            create_order(self.account, self.user, payload, "second")

    def test_confirmed_receipt_duplicate_and_second_actual_payment(self):
        order = self.order()
        kwargs = dict(channel="fixture", merchant_reference="fixture-shenzhen", transaction_reference="receipt-1", amount_minor=731, currency="CNY", paid_at=self.now, evidence_hash="a" * 64)
        first = record_confirmed_payment(self.account, order["id"], **kwargs)
        self.assertEqual(first, record_confirmed_payment(self.account, order["id"], **kwargs))
        self.assertEqual(CreditBatch.objects.count(), 1)
        self.assertEqual(PaymentFact.objects.count(), 1)
        kwargs["transaction_reference"] = "receipt-2"
        self.assertEqual(record_confirmed_payment(self.account, order["id"], **kwargs)["disposition"], "duplicate_payment_review")
        self.assertEqual(CreditBatch.objects.count(), 1)
        self.assertEqual(PaymentFact.objects.count(), 2)
        self.assertEqual(credits_summary(self.account)["available"], self.product.credits_quantity)

    def test_order_detail_exposes_duplicate_review_without_reversing_paid_delivery(self):
        from .billing import prepare_payment_attempt
        for with_attempt in (False, True):
            with self.subTest(with_attempt=with_attempt):
                quote = self.quote()
                order = create_order(self.account, self.user,
                    {'quote_id':quote['id'], 'agreement_acceptance':'fixture-1'},
                    f'review-order-{with_attempt}')
                if with_attempt:
                    prepare_payment_attempt(self.account, self.user, order['id'],
                        'review-intent', expires_at=self.now + timedelta(minutes=3))
                kwargs = dict(channel='fixture', merchant_reference='fixture-shenzhen',
                    transaction_reference=f'review-first-{with_attempt}', amount_minor=731,
                    currency='CNY', paid_at=self.now, evidence_hash='a' * 64)
                record_confirmed_payment(self.account, order['id'], **kwargs)
                clean = order_detail(self.account, order['id'])
                self.assertFalse(clean['needs_review'])
                self.assertEqual(clean['review_reasons'], [])
                kwargs['transaction_reference'] = f'review-second-{with_attempt}'
                record_confirmed_payment(self.account, order['id'], **kwargs)
                detail = order_detail(self.account, order['id'])
                self.assertTrue(detail['needs_review'])
                self.assertEqual(detail['review_reasons'], ['duplicate_payment_review'])
                self.assertEqual(detail['order']['status'], 'fulfilled')
                self.assertEqual(detail['delivery']['status'], 'completed')
                if with_attempt:
                    self.assertEqual(detail['payment_attempt']['status'], 'succeeded')
                self.assertEqual(len(detail['payments']), 2)
                self.assertNotIn('evidence_hash', str(detail))

    def test_order_detail_unknown_receipt_disposition_fails_into_safe_review_reason(self):
        order = self.order()
        PaymentFact.objects.create(order_id=order['id'], channel='fixture',
            merchant_reference='fixture-shenzhen', transaction_reference='unknown-disposition',
            amount_minor=731, currency='CNY', paid_at=self.now,
            evidence_hash='a' * 64, disposition='unexpected_internal_value')
        detail = order_detail(self.account, order['id'])
        self.assertTrue(detail['needs_review'])
        self.assertEqual(detail['review_reasons'], ['payment_review_required'])
        self.assertEqual(detail['payments'][0]['status'], 'payment_review_required')

    def test_order_detail_combines_receipt_and_attempt_review_reasons_read_only(self):
        from .billing import prepare_payment_attempt
        from .models import PaymentAttempt
        order = self.order()
        prepare_payment_attempt(self.account, self.user, order['id'],
            'reconcile-intent', expires_at=self.now + timedelta(minutes=3))
        PaymentAttempt.objects.filter(order_id=order['id']).update(needs_reconciliation=True)
        states = ['identity_mismatch_review', 'amount_mismatch_review', 'late_payment_review']
        for index, state in enumerate(states):
            PaymentFact.objects.create(order_id=order['id'], channel='fixture',
                merchant_reference='fixture-shenzhen', transaction_reference=f'review-state-{index}',
                amount_minor=731, currency='CNY', paid_at=self.now,
                evidence_hash='a' * 64, disposition=state)
        before = (PaymentFact.objects.count(), CreditEntry.objects.count())
        detail = order_detail(self.account, order['id'])
        self.assertTrue(detail['needs_review'])
        self.assertEqual(detail['review_reasons'], sorted(states + ['payment_reconciliation_required']))
        self.assertEqual((PaymentFact.objects.count(), CreditEntry.objects.count()), before)
        self.assertEqual(detail['order']['status'], 'pending_payment')

    def test_receipt_replay_mismatch_and_wrong_amount_never_grant(self):
        order = self.order()
        kwargs = dict(channel="fixture", merchant_reference="fixture-shenzhen", transaction_reference="wrong-amount", amount_minor=730, currency="CNY", paid_at=self.now, evidence_hash="b" * 64)
        result = record_confirmed_payment(self.account, order["id"], **kwargs)
        self.assertEqual(result["disposition"], "amount_mismatch_review")
        self.assertFalse(CreditBatch.objects.exists())
        kwargs["amount_minor"] = 731
        with self.assertRaisesRegex(ValueError, "RECEIPT_CONFLICT"):
            record_confirmed_payment(self.account, order["id"], **kwargs)

    def test_receipt_survives_delivery_failure_and_can_be_repaired(self):
        order = self.order()
        with patch("backend.commerce.billing.grant_credits", side_effect=ValueError("LEDGER_INCONSISTENT")):
            with self.assertRaisesRegex(ValueError, "LEDGER_INCONSISTENT"):
                record_confirmed_payment(self.account, order["id"], channel="fixture", merchant_reference="fixture-shenzhen", transaction_reference="delivery-failed", amount_minor=731, currency="CNY", paid_at=self.now, evidence_hash="d" * 64)
        self.assertEqual(PaymentFact.objects.count(), 1)
        self.assertEqual(Order.objects.get(pk=order["id"]).status, "paid_pending_entitlement")
        self.assertFalse(CreditBatch.objects.exists())
        fulfill_paid_order(self.account, order["id"])
        fulfill_paid_order(self.account, order["id"])
        self.assertEqual(CreditBatch.objects.count(), 1)
        self.assertEqual(Order.objects.get(pk=order["id"]).status, "fulfilled")

    def test_confirmed_payment_rejects_outer_transaction(self):
        order = self.order()
        with transaction.atomic():
            with self.assertRaisesRegex(ValueError, "PAYMENT_FACT_REQUIRES_AUTOCOMMIT"):
                record_confirmed_payment(self.account, order["id"], channel="fixture", merchant_reference="fixture", transaction_reference="outer-atomic", amount_minor=731, currency="CNY", paid_at=self.now, evidence_hash="e" * 64)
        self.assertFalse(PaymentFact.objects.exists())

    def test_read_contract_hides_provider_details_and_states_are_explicit(self):
        order = self.order()
        detail = order_detail(self.account, order["id"])
        self.assertEqual(detail["delivery"], {"status": "not_started"})
        self.assertEqual(detail["payments"], [])
        self.assertEqual(detail["order"]["plan_code"], "fixture_pack")
        self.assertEqual(detail["order"]["seat_quantity"], 1)
        self.assertEqual(detail["order"]["billing_cycle"], "")
        record_confirmed_payment(self.account, order["id"], channel="fixture", merchant_reference="fixture-shenzhen", transaction_reference="not-public", amount_minor=731, currency="CNY", paid_at=self.now, evidence_hash="f" * 64)
        detail = order_detail(self.account, order["id"])
        self.assertEqual(detail["delivery"], {"status": "completed"})
        self.assertEqual(set(detail["payments"][0]), {"id", "status", "amount_minor", "currency", "paid_at"})
        summary = account_summary(self.account)
        self.assertEqual(summary["credits"]["frozen"], "0")
        self.assertFalse(summary["capabilities"]["refunds"])
        self.assertTrue({"source", "available", "consumed", "reserved"} <= set(summary["credits"]["batches"][0]))

    def test_exact_reserve_settle_release_conservation(self):
        self.grant("100000000000000000000000000000.000000000001")
        reservation = reserve_credits(self.account, "task", "0.000000000001")
        self.assertEqual(reserve_credits(self.account, "task", "0.000000000001"), reservation)
        self.assertEqual(credits_summary(self.account)["available"], "100000000000000000000000000000")
        settle_credits(self.account, reservation["id"], "0.0000000000001")
        settle_credits(self.account, reservation["id"], "0.0000000000001")
        summary = credits_summary(self.account)
        self.assertEqual(summary["available"], "100000000000000000000000000000.0000000000009")
        self.assertEqual(summary["consumed"], "0.0000000000001")
        self.assertEqual(summary["reserved"], "0")
        with self.assertRaisesRegex(ValueError, "IDEMPOTENCY_CONFLICT"):
            settle_credits(self.account, reservation["id"], "0")

    def test_expiry_order_tie_subscription_and_insufficient_atomic(self):
        expiry = self.now + timedelta(days=1)
        purchased = self.grant("2", "purchase", expiry)
        subscription = self.grant("3", "monthly", expiry, "subscription")
        self.grant("8", "later", self.now + timedelta(days=3))
        reservation = reserve_credits(self.account, "task", "4")
        entries = CreditEntry.objects.filter(reservation_id=reservation["id"], kind="reserve")
        self.assertEqual(entries.get(batch_id=subscription["id"]).quantity, "3")
        self.assertEqual(entries.get(batch_id=purchased["id"]).quantity, "1")
        with self.assertRaisesRegex(ValueError, "INSUFFICIENT_CREDITS"):
            reserve_credits(self.account, "too-much", "10")
        self.assertFalse(Reservation.objects.filter(task_key="too-much").exists())
        release_credits(self.account, reservation["id"])
        release_credits(self.account, reservation["id"])
        self.assertEqual(credits_summary(self.account)["available"], "13")

    def test_cross_account_tasks_and_expired_settlement_are_blocked(self):
        batch = self.grant()
        reservation = reserve_credits(self.account, "task", "5")
        with self.assertRaisesRegex(ValueError, "NOT_FOUND"):
            settle_credits(self.other, reservation["id"], "2")
        CreditBatch.objects.filter(pk=batch["id"]).update(expires_at=self.now - timedelta(seconds=1))
        with self.assertRaisesRegex(ValueError, "CONFIG_INCOMPLETE"):
            settle_credits(self.account, reservation["id"], "2")
        summary = credits_summary(self.account)
        self.assertEqual((summary["available"], summary["reserved"], summary["expired"]), ("0", "5", "5"))

    def test_invalid_credit_values_and_over_settlement(self):
        for value in ("NaN", "Infinity", "-1", True, 1.5, "1e999999999"):
            with self.assertRaises(ValueError):
                self.grant(value)
        self.grant()
        reservation = reserve_credits(self.account, "task", "2")
        with self.assertRaisesRegex(ValueError, "OVER_RESERVATION"):
            settle_credits(self.account, reservation["id"], "3")
        self.assertEqual(credits_summary(self.account)["reserved"], "2")
        self.assertEqual(account_summary(self.other)["credits"]["available"], "0")


class BillingFactsTests(TransactionTestCase):
    """Explicit local fixtures for a read-only, account-scoped fact report."""

    def setUp(self):
        from datetime import datetime, timezone as datetime_timezone
        self.user, self.account = fixture_account('facts-buyer')
        self.other_user, self.other = fixture_account('facts-other')
        self.product = fixture_product()
        self.start = datetime(2026, 1, 1, tzinfo=datetime_timezone.utc)
        self.end = self.start + timedelta(days=1)
        self.sequence = 0

    def receipt(self, at, amount=731, disposition='accepted', account=None, currency='CNY'):
        from .models import Quote
        self.sequence += 1
        account = account or self.account
        snapshot = {'kind': 'credits', 'amount_minor': amount, 'currency': currency}
        quote = Quote.objects.create(account=account, product=self.product, snapshot=snapshot,
            amount_minor=amount, currency=currency, expires_at=self.end)
        order = Order.objects.create(account=account, created_by=self.user, quote=quote,
            idempotency_key=f'facts-{self.sequence}', request_hash='a'*64,
            snapshot=snapshot, amount_minor=amount, currency=currency,
            status='fulfilled', paid_at=at)
        return PaymentFact.objects.create(order=order, channel='fixture',
            merchant_reference='isolated-fixture', transaction_reference=f'facts-{self.sequence}',
            amount_minor=amount, currency=currency, paid_at=at, evidence_hash='b'*64,
            disposition=disposition)

    def report(self, **overrides):
        import json
        from io import StringIO
        from django.core.management import call_command
        options = {'account': str(self.account.pk), 'start': self.start.isoformat(),
                   'end': self.end.isoformat(), **overrides}
        output = StringIO()
        call_command('report_billing_facts', stdout=output, **options)
        return json.loads(output.getvalue())

    def cost_task(self):
        grant_credits(self.account, 'purchased', 'facts-cost', '10', timezone.now()+timedelta(days=2))
        return reserve_credits(self.account, 'facts-task', '5')['id']

    def test_task_lookup_observes_exact_categories_completions_without_exposing_events(self):
        from .billing import record_task_cost, complete_task_cost
        task = self.cost_task()
        values = dict(source='private-provider', resource='private-resource',
            usage={'input_tokens':'1'}, retry_index=0, outcome='failed',
            occurred_at=self.start, evidence_hash='a'*64)
        for key, category, amount, currency in (
            ('usage-a','task_usage','0.1','CNY'), ('usage-b','task_usage','0.2','CNY'),
            ('foreign','task_usage','2','USD'), ('unknown','task_usage',None,None),
            ('legacy',None,'0.000000000000000000000001','CNY'),
            ('completion','sage_fault',None,None)):
            event = record_task_cost(self.account, task, event_key=key, billing_category=category,
                cost_amount=amount, currency=currency, **values)
        complete_task_cost(self.account, event['id'], source='private-provider', request_key='private-receipt',
            cost_amount='0.123456789012345678901', currency='USD',
            occurred_at=self.start, evidence_hash='b'*64)
        result = self.report(task=task, start=None, end=None)
        self.assertEqual(result, {'account_id':str(self.account.pk), 'reservation_id':task,
            'scope':'current_internal_task_cost_observation_not_credits_charge',
            'event_count':6, 'unknown_cost_events':1,
            'costs':{'CNY':'0.300000000000000000000001','USD':'2.123456789012345678901'},
            'billing_breakdown':{
                'task_usage':{'event_count':4,'unknown_cost_events':1,'costs':{'CNY':'0.3','USD':'2'}},
                'sage_fault':{'event_count':1,'unknown_cost_events':0,'costs':{'USD':'0.123456789012345678901'}},
                'unclassified':{'event_count':1,'unknown_cost_events':0,'costs':{'CNY':'0.000000000000000000000001'}}}})

    def test_task_lookup_repeated_reads_are_select_only_and_do_not_change_credits(self):
        from django.test.utils import CaptureQueriesContext
        task = self.cost_task()
        before = (credits_summary(self.account), list(CreditEntry.objects.values()),
                  list(Reservation.objects.values()))
        with CaptureQueriesContext(connection) as queries:
            first = self.report(task=task, start=None, end=None)
            second = self.report(task=task, start=None, end=None)
        self.assertEqual(first, second)
        self.assertEqual(first['event_count'], 0)
        self.assertEqual(first['unknown_cost_events'], 0)
        self.assertEqual(first['costs'], {})
        self.assertEqual(set(first['billing_breakdown']), {'task_usage','sage_fault','unclassified'})
        self.assertTrue(queries.captured_queries)
        for query in queries.captured_queries:
            self.assertRegex(query['sql'].strip(), r'^SELECT\b')
            self.assertNotRegex(query['sql'], r'(?i)\bFOR\s+(?:UPDATE|SHARE|NO\s+KEY|KEY\s+SHARE)\b')
        self.assertEqual((credits_summary(self.account), list(CreditEntry.objects.values()),
                         list(Reservation.objects.values())), before)

    def test_task_lookup_rejects_missing_or_cross_account_tasks_and_invalid_ids(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from uuid import uuid4
        task = self.cost_task()
        for options, message in (
            ({'task':str(uuid4())}, 'LOOKUP_NOT_FOUND'),
            ({'task':task,'account':str(self.other.pk)}, 'LOOKUP_NOT_FOUND'),
            ({'task':'invalid'}, 'INVALID_LOOKUP_REFERENCE'),
            ({'task':task,'account':str(uuid4())}, 'ACCOUNT_NOT_FOUND'),
            ({'task':task,'account':'invalid'}, 'invalid UUID')):
            with self.subTest(options=options), self.assertRaisesRegex(CommandError, message):
                self.report(start=None, end=None, **options)
        with self.assertRaises(CommandError):
            call_command('report_billing_facts', task=task)

    def test_task_lookup_rejects_conflicting_selectors_and_window_options(self):
        from django.core.management.base import CommandError
        task = self.cost_task()
        for changes in ({'order':task}, {'refund':task}, {'transaction_reference':'private'},
                        {'start':self.start.isoformat()}, {'end':self.end.isoformat()},
                        {'include_age':True}):
            with self.subTest(changes=changes), self.assertRaisesRegex(CommandError, 'INVALID_LOOKUP_MODE'):
                self.report(**{'task':task,'start':None,'end':None,**changes})

    def test_task_lookup_rejects_corrupt_categories_as_command_errors(self):
        from django.core.management.base import CommandError
        from .billing import record_task_cost
        from .models import TaskCostEvent
        task = self.cost_task()
        event = record_task_cost(self.account, task, source='fixture', event_key='corrupt', resource='fixture',
            usage={'api_calls':'1'}, retry_index=0, outcome='succeeded', cost_amount=None,
            currency=None, occurred_at=self.start, evidence_hash='a'*64)
        row = TaskCostEvent.objects.get(pk=event['id'])
        for category in ('invalid', None, True, [], {}):
            TaskCostEvent.objects.filter(pk=row.pk).update(payload={**row.payload,'billing_category':category})
            with self.subTest(category=category), self.assertRaisesRegex(CommandError, 'INVALID_COST_EVENT'):
                self.report(task=task, start=None, end=None)

    def test_empty_account_has_explicit_scope_and_no_invented_rates(self):
        result = self.report()
        self.assertEqual(result['account_id'], str(self.account.pk))
        self.assertEqual(result['scope'], 'internal_ledger_facts_not_recognized_revenue')
        self.assertEqual(result['window']['bounds'], '[start,end)')
        self.assertEqual(result['currency'], 'CNY')
        self.assertEqual(result['period_flows'], {
            'accepted_receipt_amount_minor': '0', 'accepted_paid_order_count': 0,
            'confirmed_credit_refund_amount_minor': '0', 'confirmed_credit_refund_count': 0})
        self.assertEqual(result['current_cohort_state'], {
            'unknown_payment_attempt_count': 0, 'paid_pending_delivery_order_count': 0,
            'unresolved_issue_count': 0})
        self.assertNotIn('revenue', result['period_flows'])
        self.assertIn('rates', result['not_provided'])
        self.assertIn('live_vs_test_classification', result['not_provided'])

    def test_receipts_are_exact_half_open_and_exclude_other_accounts_and_review(self):
        self.receipt(self.start, 9007199254740993)
        self.receipt(self.end-timedelta(microseconds=1), 7)
        self.receipt(self.start-timedelta(microseconds=1), 101)
        self.receipt(self.end, 103)
        for state in ('duplicate_payment_review', 'identity_mismatch_review', 'amount_mismatch_review', 'late_payment_review'):
            self.receipt(self.start, 107, state)
        self.receipt(self.start, 109, account=self.other)
        self.receipt(self.start, 113, currency='USD')
        result = self.report()['period_flows']
        self.assertEqual(result['accepted_receipt_amount_minor'], '9007199254741000')
        self.assertEqual(result['accepted_paid_order_count'], 2)

    def test_refunds_count_only_confirmed_money_by_resolution_time(self):
        from .models import CreditFreeze, CreditRefund
        for state, resolved_at, account in (
            ('refunded', self.start, self.account),
            ('refunded', self.end-timedelta(microseconds=1), self.account),
            ('refunded', self.end, self.account),
            ('refunded', self.start-timedelta(microseconds=1), self.account),
            ('reserved', None, self.account), ('released', self.start, self.account),
            ('refunded', self.start, self.other)):
            fact = self.receipt(self.start-timedelta(days=5), account=account)
            batch = CreditBatch.objects.create(account=account, order=fact.order, source='purchased',
                source_key=f'facts-{self.sequence}', initial_quantity='10', expires_at=self.end)
            hold = CreditFreeze.objects.create(batch=batch, request_key='refund', quantity='10',
                state='frozen' if state == 'reserved' else state)
            CreditRefund.objects.create(payment=fact, freeze=hold, request_key='refund',
                amount_minor=731, review_evidence_hash='c'*64, state=state,
                resolved_at=resolved_at, evidence_hash='' if state == 'reserved' else 'd'*64)
        result = self.report()['period_flows']
        self.assertEqual(result['confirmed_credit_refund_amount_minor'], '1462')
        self.assertEqual(result['confirmed_credit_refund_count'], 2)
        self.assertEqual(result['accepted_receipt_amount_minor'], '0')

    def test_current_cohort_state_is_not_historical_state_and_is_account_scoped(self):
        from .models import BillingIssue, PaymentAttempt
        for at, account in ((self.start,self.account),(self.end,self.account),
                            (self.start-timedelta(microseconds=1),self.account),(self.start,self.other)):
            fact = self.receipt(at, account=account)
            Order.objects.filter(pk=fact.order_id).update(status='paid_pending_entitlement')
            attempt = PaymentAttempt.objects.create(account=account, order=fact.order,
                intent_key=f'facts-{self.sequence}', expires_at=self.end, state='unknown')
            PaymentAttempt.objects.filter(pk=attempt.pk).update(created_at=at)
            issue = BillingIssue.objects.create(account=account, code='PAID_DELIVERY_PENDING',
                object_type='order', object_id=fact.order_id, last_seen_at=at)
            BillingIssue.objects.filter(pk=issue.pk).update(first_seen_at=at)
        self.assertEqual(self.report()['current_cohort_state'], {
            'unknown_payment_attempt_count':1, 'paid_pending_delivery_order_count':1,
            'unresolved_issue_count':1})
        PaymentAttempt.objects.filter(account=self.account).update(state='succeeded')
        Order.objects.filter(account=self.account).update(status='fulfilled')
        BillingIssue.objects.filter(account=self.account).update(state='resolved')
        self.assertEqual(self.report()['current_cohort_state'], {
            'unknown_payment_attempt_count':0, 'paid_pending_delivery_order_count':0,
            'unresolved_issue_count':0})

    def test_report_is_repeatable_and_issues_no_write_sql(self):
        from django.test.utils import CaptureQueriesContext
        self.receipt(self.start)
        with CaptureQueriesContext(connection) as queries:
            first, second = self.report(), self.report()
        self.assertEqual(first, second)
        self.assertTrue(queries.captured_queries)
        self.assert_readonly_queries([query['sql'] for query in queries.captured_queries])

    def assert_readonly_queries(self, statements):
        import re
        # PostgreSQL iterator() wraps SELECT in a named server cursor. Never
        # accept arbitrary DECLARE/FETCH/CLOSE or multi-statement write SQL.
        cursor = r'"(_django_curs_\d+_(?:sync|\d+)_\d+)"'
        declared = set()
        for statement in statements:
            sql = statement.strip()
            self.assertNotRegex(sql, r'(?i);|--|/\*|\b(?:INSERT|UPDATE|DELETE|MERGE|CREATE|DROP|ALTER|TRUNCATE|COPY|CALL|DO|INTO|GRANT|REVOKE|LOCK)\b')
            declaration = re.fullmatch(r'DECLARE '+cursor+r' NO SCROLL CURSOR(?: WITH HOLD)? FOR (SELECT\b.+)', sql, re.DOTALL)
            if declaration:
                self.assertNotIn(declaration[1], declared)
                declared.add(declaration[1])
                continue
            fetch = re.fullmatch(r'FETCH FORWARD (?:[1-9]\d*|ALL) FROM '+cursor, sql)
            close = re.fullmatch(r'CLOSE '+cursor, sql)
            if fetch or close:
                name = (fetch or close)[1]
                self.assertIn(name, declared)
                if close:
                    declared.remove(name)
                continue
            self.assertRegex(sql, r'^SELECT\b', 'Only SELECT or a verified read-only cursor is permitted')

    def test_readonly_sql_assertion_rejects_writes_and_unbound_cursors(self):
        name = '"_django_curs_1_sync_1"'
        declaration = f'DECLARE {name} NO SCROLL CURSOR WITH HOLD FOR SELECT amount_minor FROM commerce_paymentfact'
        self.assert_readonly_queries(['SELECT 1', declaration,
                                     f'FETCH FORWARD 2000 FROM {name}', f'CLOSE {name}'])
        for statements in (["UPDATE commerce_order SET status='fulfilled'"],
            ['DELETE FROM commerce_order'], ['SELECT 1; DELETE FROM commerce_order'],
            ['SELECT 1 INTO copied_data'],
            ['WITH changed AS (DELETE FROM commerce_order RETURNING id) SELECT * FROM changed'],
            [f'DECLARE {name} NO SCROLL CURSOR WITH HOLD FOR DELETE FROM commerce_order'],
            [f'FETCH FORWARD 2000 FROM {name}'], [f'CLOSE {name}'],
            [declaration, f'CLOSE {name}', f'FETCH FORWARD ALL FROM {name}']):
            with self.subTest(verb=statements[-1].split()[0]), self.assertRaises(AssertionError):
                self.assert_readonly_queries(statements)

    def test_requires_existing_account_and_explicit_utc_increasing_window(self):
        from django.core.management.base import CommandError
        from uuid import uuid4
        for options, message in (({'start':'2026-01-01T00:00:00'},'UTC_WINDOW_REQUIRED'),
            ({'start':'2026-01-01T08:00:00+08:00'},'UTC_WINDOW_REQUIRED'),
            ({'start':'invalid'},'UTC_WINDOW_REQUIRED'),
            ({'start':self.end.isoformat()},'INVALID_WINDOW'),
            ({'end':self.start.isoformat()},'INVALID_WINDOW'),
            ({'account':str(uuid4())},'ACCOUNT_NOT_FOUND')):
            with self.subTest(options=options), self.assertRaisesRegex(CommandError,message):
                self.report(**options)


class ProductImportTests(TransactionTestCase):
    def product(self, code='fixture_import'):
        return dict(code=code, version=1, kind='subscription', audience='personal',
            billing_cycle='monthly', currency='CNY', unit_amount_minor=731,
            credits_quantity='10', minimum_seats=1,
            entitlements={'benefits': {'zh': ['仅供测试'], 'en': ['Fixture only']}},
            entitlement_version='fixture-1', credit_rule_version='fixture-1', terms_version='fixture-1')

    def run_import(self, rows, apply=False, raw=None):
        import json
        from io import StringIO
        from django.core.management import call_command
        output = StringIO()
        with patch('pathlib.Path.read_bytes', return_value=raw if raw is not None else json.dumps(rows).encode()):
            call_command('import_billing_products', 'fixture.json', apply=apply, stdout=output)
        return json.loads(output.getvalue())

    def test_default_validation_has_no_writes_and_apply_only_creates_drafts(self):
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as queries:
            result = self.run_import([self.product()])
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))
        self.assertEqual(result, {'mode': 'validate', 'create': 1, 'unchanged': 0})
        self.assertFalse(ProductVersion.objects.exists())
        self.assertEqual(self.run_import([self.product()], True)['create'], 1)
        product = ProductVersion.objects.get()
        self.assertEqual(product.state, 'draft')
        self.assertIsNone(product.effective_at)
        self.assertEqual(self.run_import([self.product()], True)['unchanged'], 1)
        self.assertEqual(ProductVersion.objects.count(), 1)

    def test_conflicting_version_rolls_back_entire_batch(self):
        from django.core.management.base import CommandError
        self.run_import([self.product()], True)
        changed = {**self.product(), 'unit_amount_minor': 732}
        for state in ('draft', 'published'):
            ProductVersion.objects.filter(code='fixture_import').update(state=state)
            with self.subTest(state=state), self.assertRaisesRegex(CommandError, 'PRODUCT_VERSION_CONFLICT'):
                self.run_import([self.product('fixture_other'), changed], True)
            self.assertEqual(ProductVersion.objects.count(), 1)
            self.assertEqual(ProductVersion.objects.get().unit_amount_minor, 731)

    def test_database_failure_rolls_back_and_matching_published_version_is_readonly(self):
        from django.core.management.base import CommandError
        from django.db import IntegrityError
        save = ProductVersion.save
        def fail_second(product, *args, **kwargs):
            if product.code == 'fixture_second':
                raise IntegrityError('fixture-only failure')
            return save(product, *args, **kwargs)
        with patch.object(ProductVersion, 'save', fail_second), self.assertRaisesRegex(CommandError, 'PRODUCT_IMPORT_INVALID'):
            self.run_import([self.product(), self.product('fixture_second')], True)
        self.assertFalse(ProductVersion.objects.exists())
        self.run_import([self.product()], True)
        ProductVersion.objects.filter(code='fixture_import').update(state='published', effective_at=timezone.now())
        before = ProductVersion.objects.values().get()
        self.assertEqual(self.run_import([self.product()], True)['unchanged'], 1)
        self.assertEqual(ProductVersion.objects.values().get(), before)

    def test_rejects_ambiguous_json_and_invalid_commercial_values(self):
        from django.core.management.base import CommandError
        invalid = [[], [self.product(), self.product()], [{**self.product(), 'state': 'published'}],
            [{**self.product(), 'version': True}], [{**self.product(), 'unit_amount_minor': 7.31}],
            [{**self.product(), 'unit_amount_minor': 9007199254740992}],
            [{**self.product(), 'currency': 'USD'}], [{**self.product(), 'credits_quantity': '1e3'}],
            [{**self.product(), 'credits_quantity': None}], [{**self.product(), 'terms_version': ''}],
            [{**self.product(), 'billing_cycle': 'week'}], [{**self.product(), 'audience': 'business'}],
            [{**self.product(), 'entitlements': {'benefits': {'zh': ['测试']}}}]]
        for rows in invalid:
            with self.subTest(rows=rows), self.assertRaises(CommandError):
                self.run_import(rows, True)
        for raw in (b'[{"code":"a","code":"b"}]', b'[NaN]', b'not-json'):
            with self.subTest(raw=raw), self.assertRaises(CommandError):
                self.run_import(None, True, raw)
        self.assertFalse(ProductVersion.objects.exists())

    def test_business_resource_modes_and_credits_pack_are_explicit(self):
        from django.core.management.base import CommandError
        enterprise = {**self.product(), 'audience': 'business', 'minimum_seats': 10,
            'credits_quantity': '10', 'entitlements': {**self.product()['entitlements'], 'resource_mode': 'shared_included'}}
        self.run_import([enterprise], True)
        for changes in ({'credits_quantity': '0'}, {'minimum_seats': 1}, {'minimum_seats':9},
                {'credits_quantity':'0','entitlements':{**enterprise['entitlements'],'resource_mode':'shared_only'}}):
            with self.assertRaises(CommandError):
                self.run_import([{**enterprise, 'code': 'fixture_invalid', **changes}], True)
        teams={**self.product('fixture_teams_import'),'audience':'business',
            'entitlements':{**self.product()['entitlements'],'resource_mode':'seat_included'}}
        self.run_import([teams],True)
        with self.assertRaises(CommandError):
            self.run_import([{**teams,'code':'fixture_empty_teams','credits_quantity':'0'}],True)
        pack = {**self.product('fixture_pack_import'), 'kind': 'credits', 'billing_cycle': ''}
        self.run_import([pack], True)
        with self.assertRaises(CommandError):
            self.run_import([{**pack, 'code': 'fixture_zero_pack', 'credits_quantity': '0'}], True)
        self.assertEqual(ProductVersion.objects.count(), 3)

    def test_named_business_plans_reject_inconsistent_identity_before_any_write(self):
        from django.core.management.base import CommandError
        for code, mode, wrong in (('teams','seat_included','shared_included'),
                                  ('enterprise','shared_included','seat_included')):
            valid={**self.product(code),'audience':'business','minimum_seats':10,
                'entitlements':{**self.product()['entitlements'],'resource_mode':mode}}
            for changes in ({'entitlements':{**valid['entitlements'],'resource_mode':wrong}},
                            {'audience':'personal','minimum_seats':1},
                            {'kind':'credits','billing_cycle':''}):
                with self.subTest(code=code,changes=changes):
                    with transaction.atomic():
                        try:
                            with self.assertRaisesRegex(CommandError,'INVALID_BUSINESS_RESOURCES'):
                                self.run_import([self.product('fixture_first'),{**valid,**changes}],True)
                            self.assertFalse(ProductVersion.objects.exists())
                        finally:
                            transaction.set_rollback(True)
            self.assertEqual(self.run_import([valid])['create'],1)

    @override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
    def test_named_business_sale_gate_rejects_personal_or_credit_products(self):
        from .billing import _sale_gate
        user,personal=fixture_account('named-plan-owner')
        business=BillingAccount.objects.create(kind='business',name='named-plan-business')
        for code,mode in (('teams','seat_included'),('enterprise','shared_included')):
            for kind,account in (('subscription',personal),('credits',business)):
                product=ProductVersion(**{**self.product(code),'kind':kind,'audience':account.kind,
                    'billing_cycle':'monthly' if kind=='subscription' else '',
                    'entitlements':{**self.product()['entitlements'],'resource_mode':mode}},
                    state='published',effective_at=timezone.now()-timedelta(days=1))
                with self.subTest(code=code,kind=kind), self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
                    _sale_gate(product,account,timezone.now(),'fixture','CN','fixture-shenzhen')


@override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
class CommerceConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user, self.account = fixture_account()
        self.product = fixture_product()

    def parallel(self, operation):
        if connection.vendor != "postgresql":
            self.skipTest("Row-lock concurrency requires PostgreSQL; SQLite is not evidence.")
        barrier = Barrier(2)

        def run():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operation()
            except ValueError as exc:
                return str(exc)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(lambda _: run(), range(2)))

    def test_parallel_same_order_has_one_result(self):
        quote = create_quote(self.account, self.user, self.product.pk, 1, timezone.now() + timedelta(minutes=5), channel="fixture", buyer_region="CN")
        payload = {"quote_id": quote["id"], "agreement_acceptance": "fixture-1"}
        results = self.parallel(lambda: create_order(self.account, self.user, payload, "parallel"))
        self.assertEqual(results[0]["id"], results[1]["id"])
        self.assertEqual(Order.objects.count(), 1)

    @override_settings(BILLING_TERMS_DOCUMENTS={'fixture-1':{'approved':True,
        'zh':{'title':'测试条款','paragraphs':['隔离测试。']},
        'en':{'title':'Test terms','paragraphs':['Isolated fixture.']}}})
    def test_parallel_public_subscription_orders_from_different_quotes_cannot_both_win(self):
        from .billing import purchase_terms
        product = ProductVersion.objects.create(code='pro',version=1,kind='subscription',audience='personal',
            billing_cycle='monthly',unit_amount_minor=19900,credits_quantity='10',entitlements={'fixture':True},
            entitlement_version='fixture-1',credit_rule_version='fixture-1',terms_version='fixture-1',
            state='published',effective_at=timezone.now()-timedelta(days=1))
        quotes = iter([create_quote(self.account,self.user,product.pk,1,timezone.now()+timedelta(minutes=5),
            channel='fixture',buyer_region='CN',terms_digest=purchase_terms('fixture-1')['digest']) for _ in range(2)])
        def buy():
            quote = next(quotes)
            return create_order(self.account,self.user,{'quote_id':quote['id'],
                'agreement_acceptance':'fixture-1'},f"public:{quote['id']}")
        results = self.parallel(buy)
        self.assertEqual(sum(isinstance(result,dict) for result in results),1)
        self.assertIn('CONFIG_INCOMPLETE',results)
        self.assertEqual(Order.objects.count(),1)

    def test_parallel_integrity_recording_creates_one_issue(self):
        from .billing import record_billing_findings
        from .models import BillingIssue
        batch = grant_credits(self.account,'purchased','integrity-parallel','10',timezone.now()+timedelta(days=1))
        CreditEntry.objects.filter(batch_id=batch['id']).update(kind='invalid')
        results = self.parallel(lambda: record_billing_findings(self.account))
        self.assertEqual(sum(item['created'] for item in results),1)
        self.assertEqual(BillingIssue.objects.count(),1)

    def test_parallel_refund_allocations_cannot_reserve_payment_twice(self):
        from uuid import uuid4
        from .billing import reserve_credit_refund
        from .models import CreditRefund
        now = timezone.now()
        quote = create_quote(self.account,self.user,self.product.pk,1,now+timedelta(minutes=5),channel='fixture',buyer_region='CN')
        order = create_order(self.account,self.user,{'quote_id':quote['id'],'agreement_acceptance':'fixture-1'},'refund-concurrency')
        record_confirmed_payment(self.account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',
            transaction_reference='refund-concurrent',amount_minor=731,currency='CNY',paid_at=now,evidence_hash='b'*64)
        results = self.parallel(lambda: reserve_credit_refund(self.account,order['id'],str(uuid4()),review_evidence_hash='c'*64))
        self.assertEqual(sum(isinstance(item,dict) for item in results),1)
        self.assertIn('REFUND_NOT_ELIGIBLE',results)
        self.assertEqual(CreditRefund.objects.get().amount_minor,731)
        self.assertEqual(credits_summary(self.account)['available'],'0')

    def test_parallel_reservations_cannot_overdraw(self):
        from uuid import uuid4
        grant_credits(self.account, "purchased", "source", "10", timezone.now() + timedelta(days=1))
        results = self.parallel(lambda: reserve_credits(self.account, str(uuid4()), "7"))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn("INSUFFICIENT_CREDITS", results)
        self.assertEqual(credits_summary(self.account)["available"], "3")

    def test_parallel_task_cost_replay_is_one_fact_not_a_debit(self):
        from .billing import record_task_cost, task_cost_summary
        grant_credits(self.account,'purchased','source','10',timezone.now()+timedelta(days=1))
        task = reserve_credits(self.account,'task-cost','5')
        happened = timezone.now()
        results = self.parallel(lambda: record_task_cost(self.account,task['id'],source='fixture-model',event_key='same',
            resource='fixture-resource',usage={'input_tokens':'100'},retry_index=0,outcome='succeeded',
            cost_amount='0.1',currency='CNY',occurred_at=happened,evidence_hash='a'*64,billing_category='sage_fault'))
        self.assertEqual(results[0]['id'],results[1]['id'])
        self.assertEqual(task_cost_summary(self.account,task['id'])['costs'],{'CNY':'0.1'})
        self.assertEqual(task_cost_summary(self.account,task['id'])['billing_breakdown']['sage_fault'],
            {'event_count':1,'unknown_cost_events':0,'costs':{'CNY':'0.1'}})
        self.assertEqual(credits_summary(self.account)['consumed'],'0')

    def test_parallel_conflicting_task_cost_categories_cannot_both_win(self):
        from .billing import record_task_cost, task_cost_summary
        from .models import TaskCostEvent
        grant_credits(self.account,'purchased','source','10',timezone.now()+timedelta(days=1))
        task = reserve_credits(self.account,'cost-category-race','5')
        happened = timezone.now()
        categories = iter(('task_usage','sage_fault'))
        results = self.parallel(lambda: record_task_cost(self.account,task['id'],source='fixture-model',event_key='same',
            resource='fixture-resource',usage={'input_tokens':'100'},retry_index=0,outcome='succeeded',
            cost_amount='0.1',currency='CNY',occurred_at=happened,evidence_hash='a'*64,
            billing_category=next(categories)))
        self.assertEqual(sum(isinstance(result,dict) for result in results),1)
        self.assertIn('IDEMPOTENCY_CONFLICT',results)
        self.assertEqual(TaskCostEvent.objects.count(),1)
        winner = TaskCostEvent.objects.get().payload['billing_category']
        summary = task_cost_summary(self.account,task['id'])
        self.assertEqual(summary['costs'],{'CNY':'0.1'})
        self.assertEqual(summary['billing_breakdown'][winner]['costs'],{'CNY':'0.1'})
        self.assertEqual(summary['event_count'],1)
        self.assertEqual(credits_summary(self.account)['consumed'],'0')

    def test_parallel_task_cost_completion_replay_creates_one_receipt(self):
        from .billing import record_task_cost, complete_task_cost, task_cost_summary
        from .models import TaskCostCompletion
        grant_credits(self.account,'purchased','source','10',timezone.now()+timedelta(days=1))
        task = reserve_credits(self.account,'cost-completion','5')
        happened = timezone.now()
        event = record_task_cost(self.account,task['id'],source='fixture-model',event_key='unknown',
            resource='fixture-resource',usage={'api_calls':'1'},retry_index=0,outcome='failed',
            cost_amount=None,currency=None,occurred_at=happened,evidence_hash='a'*64)
        results = self.parallel(lambda: complete_task_cost(self.account,event['id'],source='fixture-model',
            request_key='receipt',cost_amount='0.1',currency='CNY',occurred_at=happened,evidence_hash='b'*64))
        self.assertEqual(results[0]['id'],results[1]['id'])
        self.assertEqual(TaskCostCompletion.objects.count(),1)
        self.assertEqual(task_cost_summary(self.account,task['id']),
            {'event_count':1,'unknown_cost_events':0,'costs':{'CNY':'0.1'},'billing_breakdown':{
                'task_usage':{'event_count':0,'unknown_cost_events':0,'costs':{}},
                'sage_fault':{'event_count':0,'unknown_cost_events':0,'costs':{}},
                'unclassified':{'event_count':1,'unknown_cost_events':0,'costs':{'CNY':'0.1'}}}})
        self.assertEqual(credits_summary(self.account)['consumed'],'0')

    def test_parallel_conflicting_task_cost_completions_cannot_both_win(self):
        from uuid import uuid4
        from .billing import record_task_cost, complete_task_cost
        from .models import TaskCostCompletion
        grant_credits(self.account,'purchased','source','10',timezone.now()+timedelta(days=1))
        task = reserve_credits(self.account,'cost-completion','5')
        happened = timezone.now()
        event = record_task_cost(self.account,task['id'],source='fixture-model',event_key='unknown',
            resource='fixture-resource',usage={'api_calls':'1'},retry_index=0,outcome='failed',
            cost_amount=None,currency=None,occurred_at=happened,evidence_hash='a'*64)
        results = self.parallel(lambda: complete_task_cost(self.account,event['id'],source='fixture-model',
            request_key=str(uuid4()),cost_amount='0.1',currency='CNY',occurred_at=happened,evidence_hash='b'*64))
        self.assertEqual(sum(isinstance(result,dict) for result in results),1)
        self.assertIn('IDEMPOTENCY_CONFLICT',results)
        self.assertEqual(TaskCostCompletion.objects.count(),1)
        self.assertEqual(credits_summary(self.account)['consumed'],'0')

    def test_parallel_freezes_cannot_overdraw(self):
        from uuid import uuid4
        from .billing import freeze_credits
        batch = grant_credits(self.account,'purchased','source','10',timezone.now()+timedelta(days=1))
        results = self.parallel(lambda: freeze_credits(self.account,batch['id'],str(uuid4()),'7'))
        self.assertEqual(sum(isinstance(result,dict) for result in results),1)
        self.assertIn('INSUFFICIENT_CREDITS',results)
        self.assertEqual(credits_summary(self.account)['frozen'],'7')

    def test_parallel_same_freeze_and_resolution_are_once(self):
        from .billing import freeze_credits, resolve_credit_freeze
        batch = grant_credits(self.account,'purchased','source','10',timezone.now()+timedelta(days=1))
        results = self.parallel(lambda: freeze_credits(self.account,batch['id'],'same','7'))
        self.assertEqual(results[0]['id'],results[1]['id'])
        self.parallel(lambda: resolve_credit_freeze(self.account,results[0]['id'],'refunded',evidence_hash='a'*64))
        self.assertEqual(credits_summary(self.account)['refunded'],'7')
        self.assertEqual(CreditEntry.objects.filter(kind='refund').count(),1)

    def test_parallel_verified_receipts_grant_once(self):
        quote = create_quote(self.account, self.user, self.product.pk, 1, timezone.now() + timedelta(minutes=5), channel="fixture", buyer_region="CN")
        order = create_order(self.account, self.user, {"quote_id": quote["id"], "agreement_acceptance": "fixture-1"}, "payment")
        paid_at = timezone.now()
        results = self.parallel(lambda: record_confirmed_payment(self.account, order["id"], channel="fixture", merchant_reference="fixture-shenzhen", transaction_reference="same", amount_minor=731, currency="CNY", paid_at=paid_at, evidence_hash="c" * 64))
        self.assertEqual(results[0]["id"], results[1]["id"])
        self.assertEqual((PaymentFact.objects.count(), CreditBatch.objects.count()), (1, 1))
