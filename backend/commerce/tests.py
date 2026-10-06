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


def fixture_product():
    return ProductVersion.objects.create(
        code="fixture_pack", version=1, kind="credits", audience="personal",
        unit_amount_minor=731, credits_quantity="10.000000000000000000000000000001",
        entitlements={"fixture": True, "benefits": {"zh": ["隔离测试权益，不对外销售。"],
                      "en": ["Isolated test benefits, not for sale."]}}, entitlement_version="fixture-1",
        credit_rule_version="fixture-1", terms_version="fixture-1", state="published",
        effective_at=timezone.now() - timedelta(days=1),
    )


FIXTURE_CHANNELS = {"fixture": {"enabled": True, "currency": "CNY", "merchant_entity": "fixture-shenzhen", "regions": ["CN"], "capabilities": ["one_time", "recurring_monthly", "recurring_annual"]}}


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
        return create_quote(self.account,self.user,(product or self.product).pk,1,
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
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            self.quote(pack)
        self.assertFalse(next(item for item in purchase_catalog(self.account)['items'] if item['kind']=='credits')['quote_available'])
        self.assertIn('id',self.quote(pack,public=False))
        self.subscribed(future=True)
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            self.quote(pack)
        from .models import SubscriptionPeriod
        SubscriptionPeriod.objects.update(starts_at=self.now-timedelta(days=1))
        quote = self.quote(pack)
        order = self.order(quote,'credits')
        delayed = self.quote(pack)
        SubscriptionPeriod.objects.update(ends_at=self.now-timedelta(seconds=1))
        self.assertEqual(self.order(quote,'credits'),order)
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            self.order(delayed,'expired-credits')
        with self.assertRaisesRegex(ValueError,'CONFIG_INCOMPLETE'):
            self.quote(pack)

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
        self.assertEqual(partial['reason'],'rounding_policy_pending')
        self.assertIsNone(partial['reference_minor'])
        from fractions import Fraction
        expected = Fraction(731)*(Fraction(self.product.credits_quantity)-1)/Fraction(self.product.credits_quantity)
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
        with self.assertRaisesRegex(ValueError,'ROUNDING_POLICY_REQUIRED'):
            reserve_credit_refund(self.account,order['id'],'reassess',review_evidence_hash='e'*64)
        self.assertEqual(CreditRefund.objects.count(),1)

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
            'costs':{'CNY':'0.100000000000000000000000000001','USD':'2'}})

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
        enterprise = {**self.product(), 'audience': 'business', 'minimum_seats': 2,
            'credits_quantity': '0', 'entitlements': {**self.product()['entitlements'], 'resource_mode': 'shared_only'}}
        self.run_import([enterprise], True)
        for changes in ({'credits_quantity': '1'}, {'minimum_seats': 1}):
            with self.assertRaises(CommandError):
                self.run_import([{**enterprise, 'code': 'fixture_invalid', **changes}], True)
        pack = {**self.product('fixture_pack_import'), 'kind': 'credits', 'billing_cycle': ''}
        self.run_import([pack], True)
        with self.assertRaises(CommandError):
            self.run_import([{**pack, 'code': 'fixture_zero_pack', 'credits_quantity': '0'}], True)
        self.assertEqual(ProductVersion.objects.count(), 2)


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
            cost_amount='0.1',currency='CNY',occurred_at=happened,evidence_hash='a'*64))
        self.assertEqual(results[0]['id'],results[1]['id'])
        self.assertEqual(task_cost_summary(self.account,task['id'])['costs'],{'CNY':'0.1'})
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
            {'event_count':1,'unknown_cost_events':0,'costs':{'CNY':'0.1'}})
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
