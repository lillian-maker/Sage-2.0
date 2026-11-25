"""Manual (early) renewal: same plan and cycle, appended after the last paid term."""
from datetime import datetime, timedelta

from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from backend.models import Membership
from .billing import record_confirmed_payment
from .models import BillingIssue, Order, PaymentAttempt, ProductVersion, Subscription
from . import subscription_tests
from .subscriptions import (append_manual_renewal, create_manual_renewal_order, manual_renewal_offer,
    prepare_renewal_attempt, schedule_due_renewals)
from .tests import FIXTURE_CHANNELS

TERMS = {'fixture-1': {'approved': True, 'zh': {'title': '测试', 'paragraphs': ['仅供本地测试']},
    'en': {'title': 'Fixture', 'paragraphs': ['Local test only']}}}


@override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS, BILLING_TERMS_DOCUMENTS=TERMS)
class ManualRenewalTests(TransactionTestCase):
    def setUp(self):
        subscription_tests.SubscriptionTests.setUp(self)
        self.product = ProductVersion.objects.create(code='fixture_monthly', version=1, kind='subscription',
            audience='personal', billing_cycle='monthly', unit_amount_minor=9900, credits_quantity='10',
            entitlements={'fixture': True}, entitlement_version='fixture-1', credit_rule_version='fixture-1',
            terms_version='fixture-1', state='published', effective_at=self.anchor-timedelta(days=1))
        self.sub = Subscription.objects.get(pk=subscription_tests.SubscriptionTests.activate(self)['id'])
        self.keys = 0

    def paid(self):
        from .billing import create_quote, create_order
        quote = create_quote(self.account, self.user, self.product.pk, 1, timezone.now()+timedelta(minutes=5),
            channel='fixture', buyer_region='CN')
        order = create_order(self.account, self.user, {'quote_id': quote['id'], 'agreement_acceptance': 'fixture-1'}, 'initial')
        record_confirmed_payment(self.account, order['id'], channel='fixture', merchant_reference='fixture-shenzhen',
            transaction_reference='initial', amount_minor=9900, currency='CNY', paid_at=timezone.now(), evidence_hash='b'*64)
        return order['id']

    def order(self, key=None, **overrides):
        self.keys += 1
        values = {'channel': 'fixture', 'agreement_acceptance': 'fixture-1', **overrides}
        return create_manual_renewal_order(self.account, self.user, self.sub.pk,
            idempotency_key=key or f'manual-{self.keys}', **values)

    def pay(self, order, at=None):
        record_confirmed_payment(self.account, order['id'], channel='fixture', merchant_reference='fixture-shenzhen',
            transaction_reference='manual-'+order['id'], amount_minor=int(order['amount_minor']), currency='CNY',
            paid_at=at or timezone.now(), evidence_hash='c'*64)
        return Order.objects.get(pk=order['id'])

    def last(self):
        return self.sub.periods.order_by('-cycle_index').first()

    def test_each_manual_renewal_appends_one_more_term_and_moves_automatic_renewal(self):
        first = self.last()
        from .subscriptions import list_subscriptions
        self.assertTrue(list_subscriptions(self.account)[0]['capabilities']['manual_payment'])
        offer = manual_renewal_offer(self.account, self.user, self.sub.pk)
        self.assertEqual((offer['amount_minor'], offer['billing_cycle'], offer['auto_renew']), ('9900', 'monthly', True))
        self.assertEqual(datetime.fromisoformat(offer['starts_at']), first.ends_at)
        self.assertEqual((offer['channels'], offer['pending_order_id']), (['fixture'], None))
        order = self.order()
        self.assertEqual(manual_renewal_offer(self.account, self.user, self.sub.pk)['pending_order_id'], order['id'])
        self.assertEqual((order['status'], order['amount_minor']), ('pending_payment', 9900))
        self.assertEqual(order['snapshot']['manual_renewal'], {'subscription_id': str(self.sub.pk)})
        self.assertEqual(order['snapshot']['renewal'], 'none')
        self.assertEqual(self.pay(order).status, 'fulfilled')
        second = self.last()
        self.assertEqual((second.cycle_index, second.starts_at, str(second.order_id)), (1, first.ends_at, order['id']))
        self.assertEqual(self.pay(self.order()).status, 'fulfilled')
        third = self.last()
        self.assertEqual((third.cycle_index, third.starts_at), (2, second.ends_at))
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.auto_renew)
        # Automatic renewal now waits for the end of the last paid term.
        self.now.return_value = first.ends_at-timedelta(hours=12)
        self.assertEqual(schedule_due_renewals(self.account)['prepared'], 0)
        self.now.return_value = third.ends_at-timedelta(days=2)
        self.assertEqual(schedule_due_renewals(self.account)['prepared'], 1)
        self.assertEqual(PaymentAttempt.objects.get(subscription=self.sub).renewal_cycle_index, 3)

    def test_cancelled_renewal_can_still_be_extended_by_hand_without_turning_it_back_on(self):
        from .subscriptions import cancel_subscription
        cancel_subscription(self.account, self.user, self.sub.pk)
        self.assertEqual(self.pay(self.order()).status, 'fulfilled')
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.auto_renew)
        self.assertEqual(self.last().cycle_index, 1)

    def test_replay_and_conflicting_reuse_of_a_request_key(self):
        order = self.order('same-key')
        self.assertEqual(self.order('same-key')['id'], order['id'])
        with self.assertRaisesRegex(ValueError, 'IDEMPOTENCY_CONFLICT'):
            self.order('same-key', agreement_acceptance='other')
        with self.assertRaisesRegex(ValueError, 'INVALID_REQUEST'):
            self.order('renewal:spoof')

    def test_one_unpaid_order_at_a_time_and_it_holds_back_the_automatic_charge(self):
        self.order()
        with self.assertRaisesRegex(ValueError, 'UNPAID_ORDER_EXISTS'):
            self.order()
        end = self.last().ends_at
        with self.assertRaisesRegex(ValueError, 'PAYMENT_RECONCILIATION_REQUIRED'):
            prepare_renewal_attempt(self.account, self.sub.pk, cycle_index=1,
                dispatch_not_before=end-timedelta(days=1), expires_at=end)

    def test_unpaid_order_closes_after_fifteen_minutes_and_frees_the_slot(self):
        first = self.order()
        self.now.return_value += timedelta(minutes=16)
        second = self.order()
        self.assertEqual(Order.objects.get(pk=first['id']).status, 'closed')
        self.assertNotEqual(first['id'], second['id'])

    def test_gates_block_ordering_when_the_term_cannot_be_extended_safely(self):
        with self.assertRaisesRegex(ValueError, 'TERMS_MISMATCH'):
            self.order(agreement_acceptance='fixture-0')
        # A channel that only takes one-time Credits payments cannot carry a subscription term.
        credits_only = {**FIXTURE_CHANNELS, 'wechat': {**FIXTURE_CHANNELS['fixture'], 'capabilities': ['one_time']}}
        with override_settings(BILLING_APPROVED_CHANNELS=credits_only):
            self.assertEqual(manual_renewal_offer(self.account, self.user, self.sub.pk)['channels'], ['fixture'])
            with self.assertRaisesRegex(ValueError, 'CONFIG_INCOMPLETE'):
                self.order(channel='wechat')
        end = self.last().ends_at
        attempt = prepare_renewal_attempt(self.account, self.sub.pk, cycle_index=1,
            dispatch_not_before=end-timedelta(days=1), expires_at=end)
        with self.assertRaisesRegex(ValueError, 'PAYMENT_RECONCILIATION_REQUIRED'):
            self.order()
        PaymentAttempt.objects.filter(pk=attempt['id']).delete()
        Order.objects.filter(idempotency_key=f'renewal:{self.sub.pk}:1').delete()
        self.now.return_value = end
        with self.assertRaisesRegex(ValueError, 'SERVICE_NOT_ACTIVE'):
            self.order()
        self.assertFalse(Order.objects.filter(snapshot__manual_renewal__isnull=False).exists())

    def test_refund_in_progress_blocks_manual_renewal(self):
        from .refunds import request_service_refund
        request_service_refund(self.account, self.user, self.sub.pk, 'refund-first')
        with self.assertRaisesRegex(ValueError, 'REFUND_PENDING'):
            self.order()

    def test_payment_landing_after_service_ended_is_held_for_review_not_delivered(self):
        order = self.order()
        end = self.last().ends_at
        self.now.return_value = end+timedelta(minutes=1)
        paid = self.pay(order, at=end+timedelta(seconds=30))
        self.assertEqual(paid.status, 'paid_pending_entitlement')
        self.assertEqual(self.last().cycle_index, 0)
        self.assertTrue(BillingIssue.objects.filter(code='MANUAL_RENEWAL_REVIEW', object_id=order['id']).exists())
        append_manual_renewal(self.account, order['id'])
        self.assertEqual(self.sub.periods.count(), 1)

    def test_prepaid_manual_term_is_fully_refundable_before_it_starts(self):
        from .refunds import service_refund_assessment
        self.now.return_value += timedelta(days=3)
        order = self.order()
        self.pay(order)
        self.now.return_value += timedelta(days=2)
        result = service_refund_assessment(self.account, self.user, self.sub.pk)
        self.assertEqual((result['amount_minor'], result['policy']), ('9900', 'future_terms'))
        self.assertEqual(result['items'], [{'order_id': order['id'], 'amount_minor': '9900'}])

    def test_manual_renewal_order_can_start_an_alipay_page_payment(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from backend.payments import payment_start_capability
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        sandbox = {'enabled': True, 'app_id': '2026000000000001', 'seller_id': '2088000000000001',
            'merchant_entity': 'fixture-shenzhen', 'payment_ttl_seconds': 900,
            'private_key': key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption()).decode('ascii'),
            'public_key': key.public_key().public_bytes(serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo)}
        channels = {**FIXTURE_CHANNELS, 'alipay': {**FIXTURE_CHANNELS['fixture'], 'capabilities': ['one_time', 'term_monthly', 'term_annual']}}
        with override_settings(ENVIRONMENT='test', BILLING_ALIPAY_SANDBOX=sandbox, BILLING_APPROVED_CHANNELS=channels):
            self.assertEqual(manual_renewal_offer(self.account, self.user, self.sub.pk)['channels'], ['fixture', 'alipay'])
            order = self.order(channel='alipay')
            self.assertEqual((order['snapshot']['channel'], order['snapshot']['renewal']), ('alipay', 'none'))
            self.assertEqual(payment_start_capability(self.account, order['id']), {'environment': 'SANDBOX',
                'channel': 'alipay', 'account_id': str(self.account.pk), 'order_id': order['id'], 'available': True})

    def test_http_offer_and_order_are_account_scoped(self):
        client = Client()
        client.force_login(self.user)
        session = client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        base = f'/api/billing/subscriptions/{self.sub.pk}'
        headers = {'HTTP_X_BILLING_ACCOUNT': str(self.account.pk)}
        offer = client.get(base+'/manual-renewal', **headers)
        self.assertEqual(offer.status_code, 200, offer.content)
        self.assertEqual(offer.json()['amount_minor'], '9900')
        body = {'channel': 'fixture', 'agreement_acceptance': 'fixture-1'}
        created = client.post(base+'/manual-renewals', body, content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='http-manual', **headers)
        self.assertEqual(created.status_code, 201, created.content)
        again = client.post(base+'/manual-renewals', body, content_type='application/json',
            HTTP_IDEMPOTENCY_KEY='http-manual-2', **headers)
        self.assertEqual((again.status_code, again.json()['error']['code']), (409, 'UNPAID_ORDER_EXISTS'))
        self.assertEqual(client.get(base+'/manual-renewals', **headers).status_code, 405)
        Membership.objects.create(account=self.other, user=self.user, role='member')
        outsider = client.get(f'/api/billing/subscriptions/{self.sub.pk}/manual-renewal',
            HTTP_X_BILLING_ACCOUNT=str(self.other.pk))
        self.assertIn(outsider.status_code, (403, 404, 409))
