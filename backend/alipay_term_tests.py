"""Fixed-term plans and Credits packs through the production Alipay page-payment path."""
import json
from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from backend.commerce.models import (CreditBatch, Order, PaymentAttempt, PaymentFact, ProductVersion,
    Subscription, SubscriptionPeriod)
from backend.commerce.tests import fixture_account
from backend.models import AccountSession, Membership

TERMS = {'fixture-1': {'approved': True,
    'zh': {'title': '测试购买条款', 'paragraphs': ['仅用于隔离测试。']},
    'en': {'title': 'Test purchase terms', 'paragraphs': ['Isolated tests only.']}}}
BENEFITS = {'benefits': {'zh': ['测试权益'], 'en': ['Test benefit']}}


def channel(*capabilities):
    return {'alipay': {'enabled': True, 'currency': 'CNY', 'merchant_entity': 'fixture-shenzhen',
        'regions': ['CN'], 'capabilities': list(capabilities)}}


@override_settings(ENVIRONMENT='production', BILLING_SALES_ENABLED=True, BILLING_ALIPAY_LIVE_ENABLED=True,
    BILLING_APPROVED_CHANNELS=channel('one_time', 'term_monthly', 'term_annual'),
    BILLING_TERMS_DOCUMENTS=TERMS, BILLING_CREDITS_PURCHASE_ENABLED=True)
class AlipayFixedTermTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_pem = cls.private.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode('ascii')
        cls.public_pem = cls.private.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode('ascii')

    def setUp(self):
        self.enterContext(override_settings(BILLING_ALIPAY_LIVE={'enabled': True,
            'app_id': '2026000000000001', 'seller_id': '2088000000000001',
            'merchant_entity': 'fixture-shenzhen', 'private_key': self.private_pem,
            'public_key': self.public_pem, 'payment_ttl_seconds': 900,
            'notify_origin': 'https://sage.example'}))
        self.user, self.account = fixture_account('term-buyer')
        Membership.objects.create(user=self.user, account=self.account, role='owner')
        common = dict(audience='personal', entitlements=BENEFITS, entitlement_version='fixture-1',
            credit_rule_version='fixture-1', terms_version='fixture-1', state='published',
            effective_at=timezone.now()-timedelta(days=1))
        self.monthly = ProductVersion.objects.create(code='pro', version=1, kind='subscription',
            billing_cycle='monthly', unit_amount_minor=731, credits_quantity='10', **common)
        self.annual = ProductVersion.objects.create(code='pro', version=2, kind='subscription',
            billing_cycle='annual', unit_amount_minor=7310, credits_quantity='10', **common)
        self.pack = ProductVersion.objects.create(code='pack', version=1, kind='credits',
            unit_amount_minor=500, credits_quantity='50', **common)
        self.client.force_login(self.user)
        session = self.client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        AccountSession.objects.create(user=self.user, session_key=session.session_key, client='Fixture')
        self.headers = {'HTTP_X_BILLING_ACCOUNT': str(self.account.pk)}
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            self.enterContext(patch(target, side_effect=AssertionError('Must stay offline')))

    def api(self, path, body=None, **extra):
        if body is None:
            return self.client.get('/api/'+path, **self.headers)
        return self.client.post('/api/'+path, json.dumps(body), content_type='application/json',
            **self.headers, **extra)

    def catalog(self):
        response = self.api('billing/purchase-catalog')
        self.assertEqual(response.status_code, 200, response.content)
        return {(item['code'], item['billing_cycle']): item for item in response.json()['items']}

    def order(self, product, key):
        quote = self.api('billing/purchase-quotes', {'product_id': str(product.pk), 'seat_quantity': 1,
            'channel': 'alipay', 'buyer_region': 'CN'})
        self.assertEqual(quote.status_code, 201, quote.content)
        order = self.api('billing/orders', {'quote_id': quote.json()['id'],
            'agreement_acceptance': 'fixture-1'}, HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(order.status_code, 201, order.content)
        return quote.json(), Order.objects.get(pk=order.json()['id'])

    def pay(self, order):
        response = self.api(f'billing/orders/{order.pk}/pay', {})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def notify(self, order, **changes):
        from backend.payment_channel_tests import AlipayOfflineProtocolTests
        attempt = PaymentAttempt.objects.get(order=order)
        paid = timezone.now().astimezone(datetime_timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
        body = AlipayOfflineProtocolTests.signed_notification(self, additions={
            'out_trade_no': str(attempt.pk), 'seller_id': '2088000000000001',
            'notify_type': 'trade_status_sync', 'gmt_payment': paid,
            'total_amount': f'{order.amount_minor//100}.{order.amount_minor%100:02d}',
            'trade_no': 'term_fixture_'+str(order.amount_minor), **changes})
        return Client().post('/api/payments/webhooks/alipay', body,
            content_type='application/x-www-form-urlencoded')

    def assert_next_beijing_midnight_after_one_month(self, paid_at, ends_at):
        from backend.billing_rules import month_boundary
        zone = ZoneInfo('Asia/Shanghai')
        boundary = month_boundary(paid_at.astimezone(zone), 1)
        expected = boundary.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        self.assertEqual(ends_at, expected)

    def test_fixed_term_plan_is_paid_once_and_activates_one_period_without_renewal(self):
        item = self.catalog()[('pro', 'monthly')]
        self.assertTrue(item['quote_available'])
        self.assertEqual(item['purchase_options'],
            [{'channel': 'alipay', 'buyer_region': 'CN', 'renewal': 'none'}])
        quote, order = self.order(self.monthly, 'term-order')
        self.assertEqual(quote['snapshot']['renewal'], 'none')
        self.assertEqual(order.snapshot['renewal'], 'none')
        detail = self.api(f'billing/orders/{order.pk}')
        self.assertEqual(detail.status_code, 200, detail.content)
        paid = self.pay(order)
        url = urlsplit(paid['payment_url'])
        params = {key: value[0] for key, value in parse_qs(url.query).items()}
        self.assertEqual((url.scheme, url.netloc, url.path), ('https', 'openapi.alipay.com', '/gateway.do'))
        self.assertEqual(params['notify_url'], 'https://sage.example/api/payments/webhooks/alipay')
        self.assertEqual(params['return_url'], f'https://sage.example/pages/billing.html?order={order.pk}')
        self.assertEqual(json.loads(params['biz_content'])['total_amount'], '7.31')
        self.assertFalse(Subscription.objects.exists())

        self.assertEqual(self.notify(order, total_amount='7.30').status_code, 400)
        self.assertFalse(PaymentFact.objects.exists())
        response = self.notify(order)
        self.assertEqual(response.content, b'success', response.content)
        self.assertEqual(self.notify(order).content, b'success')
        order.refresh_from_db()
        self.assertEqual(order.status, 'fulfilled')
        subscription = Subscription.objects.get()
        self.assertFalse(subscription.auto_renew)
        self.assertEqual(subscription.mandate, {'renewal_setup_failure': {'status': 'not_offered'}})
        self.assertEqual(subscription.billing_timezone, 'Asia/Shanghai')
        period = SubscriptionPeriod.objects.get()
        self.assertEqual(period.starts_at, order.paid_at)
        self.assertTrue(timedelta(days=28) < period.ends_at-period.starts_at <= timedelta(days=32))
        self.assert_next_beijing_midnight_after_one_month(order.paid_at, period.ends_at)
        self.assertEqual(PaymentFact.objects.count(), 1)

        listed = self.api('billing/subscriptions').json()['items']
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['status'], 'active')
        self.assertFalse(listed[0]['auto_renew'])
        self.assertEqual(listed[0]['renewal_setup'],
            {'status': 'not_offered', 'separate_authorization_required': True})
        status = self.api(f'billing/orders/{order.pk}')
        self.assertEqual(status.json()['order']['status'], 'fulfilled')

        # A running term blocks a second plan purchase but unlocks Credits packs.
        catalog = self.catalog()
        self.assertFalse(catalog[('pro', 'annual')]['quote_available'])
        self.assertTrue(catalog[('pack', '')]['quote_available'])
        _, pack_order = self.order(self.pack, 'pack-order')
        self.assertNotIn('renewal', pack_order.snapshot)
        self.assertIn('payment_url', self.pay(pack_order))
        self.assertEqual(self.notify(pack_order).content, b'success')
        pack_order.refresh_from_db()
        self.assertEqual(pack_order.status, 'fulfilled')
        batch = CreditBatch.objects.get(order=pack_order, source='purchased')
        self.assert_next_beijing_midnight_after_one_month(pack_order.paid_at, batch.expires_at)

    def test_annual_term_runs_one_year_and_expired_term_can_be_bought_again(self):
        _, order = self.order(self.annual, 'annual-order')
        self.pay(order)
        self.assertEqual(self.notify(order).content, b'success')
        period = SubscriptionPeriod.objects.get()
        self.assertTrue(timedelta(days=365) < period.ends_at-period.starts_at <= timedelta(days=367))
        local_end = period.ends_at.astimezone(ZoneInfo('Asia/Shanghai'))
        self.assertEqual((local_end.hour, local_end.minute, local_end.second, local_end.microsecond), (0, 0, 0, 0))
        SubscriptionPeriod.objects.update(starts_at=timezone.now()-timedelta(days=400),
            ends_at=timezone.now()-timedelta(days=1))
        self.assertTrue(self.catalog()[('pro', 'monthly')]['quote_available'])

    def test_recurring_only_channel_never_settles_a_plan_by_page_payment(self):
        with override_settings(BILLING_APPROVED_CHANNELS=channel('one_time', 'recurring_monthly')):
            item = self.catalog()[('pro', 'monthly')]
            self.assertEqual(item['purchase_options'], [{'channel': 'alipay', 'buyer_region': 'CN'}])
            _, order = self.order(self.monthly, 'recurring-order')
            self.assertNotIn('renewal', order.snapshot)
            self.assertEqual(self.api(f'billing/orders/{order.pk}/pay', {}).status_code, 503)
        self.assertFalse(PaymentAttempt.objects.exists())

    def test_term_marker_cannot_be_added_to_credits_or_forged_after_quoting(self):
        _, order = self.order(self.monthly, 'forged-order')
        for snapshot in ({**order.snapshot, 'renewal': 'auto'},
                         {key: value for key, value in order.snapshot.items() if key != 'renewal'}):
            Order.objects.filter(pk=order.pk).update(snapshot=snapshot)
            self.assertEqual(self.api(f'billing/orders/{order.pk}/pay', {}).status_code, 503)
        self.assertFalse(PaymentAttempt.objects.exists())

    def test_credits_pack_requires_active_term(self):
        item = self.catalog()[('pack', '')]
        self.assertFalse(item['quote_available'])
        self.assertEqual(item['unavailable_reason'], 'CREDITS_SUBSCRIPTION_REQUIRED')
        quote = self.api('billing/purchase-quotes', {'product_id': str(self.pack.pk), 'seat_quantity': 1,
            'channel': 'alipay', 'buyer_region': 'CN'})
        self.assertNotEqual(quote.status_code, 201)
        self.assertFalse(Order.objects.exists())

    def test_period_and_pack_end_at_the_midnight_after_the_month_boundary(self):
        from backend.billing_rules import credits_pack_expiry, month_boundary, next_midnight
        zone = ZoneInfo('Asia/Shanghai')
        paid = datetime(2026, 9, 30, 5, 0, tzinfo=zone)
        self.assertEqual(credits_pack_expiry(paid), datetime(2026, 10, 31, 0, 0, tzinfo=zone))
        late_january = datetime(2027, 1, 31, 23, 59, 59, tzinfo=zone)
        self.assertEqual(next_midnight(month_boundary(late_january, 1)), datetime(2027, 3, 1, 0, 0, tzinfo=zone))
        self.assertEqual(next_midnight(month_boundary(late_january, 2)), datetime(2027, 4, 1, 0, 0, tzinfo=zone))
        self.assertEqual(next_midnight(month_boundary(paid, 12)), datetime(2027, 10, 1, 0, 0, tzinfo=zone))


class BillingSalesConfigurationTests(TransactionTestCase):
    def config(self, **changes):
        return {'sales_enabled': True, 'alipay_live_enabled': True, 'alipay_egress_proxy': True,
            'credits_purchase_enabled': True,
            'approved_channels': channel('one_time', 'term_monthly', 'term_annual'),
            'terms_documents': TERMS, **changes}

    def load(self, config, mode=0o440):
        import tempfile
        from pathlib import Path
        from backend.billing_sales_config import load_billing_sales
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'billing_sales.json'
            path.write_text(config if isinstance(config, str) else json.dumps(config), encoding='utf-8')
            path.chmod(mode)
            return load_billing_sales(path)

    def test_reviewed_file_maps_to_settings_that_open_the_fixed_term_sale(self):
        loaded = self.load(self.config())
        self.assertEqual(loaded, {'BILLING_SALES_ENABLED': True,
            'BILLING_APPROVED_CHANNELS': channel('one_time', 'term_monthly', 'term_annual'),
            'BILLING_ALIPAY_LIVE_ENABLED': True, 'BILLING_ALIPAY_EGRESS_PROXY': 'http://egress:3128',
            'BILLING_CREDITS_PURCHASE_ENABLED': True,
            'BILLING_TERMS_DOCUMENTS': TERMS})
        self.assertEqual(self.load(self.config(alipay_egress_proxy=False))['BILLING_ALIPAY_EGRESS_PROXY'], '')
        from backend.commerce.billing import purchase_terms
        with override_settings(**loaded):
            self.assertEqual(len(purchase_terms('fixture-1')['digest']), 64)

    def test_unexpected_content_fails_instead_of_partly_opening_sales(self):
        from backend.billing_sales_config import load_billing_sales
        base = self.config()
        invalid = [
            {**base, 'extra': 1}, {key: value for key, value in base.items() if key != 'sales_enabled'},
            {**base, 'sales_enabled': 1}, {**base, 'approved_channels': channel('recurring_monthly')},
            {**base, 'approved_channels': channel('one_time', 'one_time')},
            {**base, 'approved_channels': {'wechat': channel('one_time')['alipay']}},
            {**base, 'approved_channels': {'alipay': {**channel('one_time')['alipay'], 'regions': ['US']}}},
            {**base, 'approved_channels': {'alipay': {**channel('one_time')['alipay'], 'private_key': 'x'}}},
            {**base, 'terms_documents': {'v1': {'approved': True, 'zh': TERMS['fixture-1']['zh']}}},
            {**base, 'terms_documents': {'v1': {**TERMS['fixture-1'], 'zh': {'title': ' ', 'paragraphs': ['x']}}}},
            '{"sales_enabled": true, "sales_enabled": false}', '[]', 'not json']
        for config in invalid:
            with self.subTest(config=str(config)[:80]), self.assertRaises(ValueError):
                self.load(config)
        with self.assertRaises(ValueError):
            self.load(base, mode=0o666)
        with self.assertRaises(ValueError):
            load_billing_sales('/nonexistent/billing_sales.json')


class ProductPublishTests(TransactionTestCase):
    def publish(self, *products, apply=False):
        from io import StringIO
        from django.core.management import call_command
        output = StringIO()
        call_command('publish_billing_products', *products, apply=apply, stdout=output)
        return json.loads(output.getvalue())

    def draft(self, **changes):
        return ProductVersion.objects.create(**{**dict(code='pro', version=1, kind='subscription',
            audience='personal', billing_cycle='monthly', unit_amount_minor=731, credits_quantity='10',
            entitlements=BENEFITS, entitlement_version='fixture-1', credit_rule_version='fixture-1',
            terms_version='fixture-1'), **changes})

    @override_settings(BILLING_TERMS_DOCUMENTS=TERMS)
    def test_validation_writes_nothing_and_apply_publishes_only_complete_drafts(self):
        from django.core.management.base import CommandError
        product = self.draft()
        self.assertEqual(self.publish('pro:1'), {'mode': 'validate', 'publish': ['pro:1'], 'unchanged': 0})
        product.refresh_from_db()
        self.assertEqual((product.state, product.effective_at), ('draft', None))
        self.draft(code='broken', entitlements={'x': 1})
        with self.assertRaisesRegex(CommandError, 'CONFIG_INCOMPLETE'):
            self.publish('pro:1', 'broken:1', apply=True)
        product.refresh_from_db()
        self.assertEqual(product.state, 'draft')
        before = timezone.now()
        self.assertEqual(self.publish('pro:1', apply=True)['publish'], ['pro:1'])
        product.refresh_from_db()
        self.assertEqual(product.state, 'published')
        self.assertTrue(before <= product.effective_at <= timezone.now())
        effective = product.effective_at
        self.assertEqual(self.publish('pro:1', apply=True), {'mode': 'apply', 'publish': [], 'unchanged': 1})
        product.refresh_from_db()
        self.assertEqual(product.effective_at, effective)
        for reference in ('missing:1', 'pro', 'pro:0'):
            with self.subTest(reference=reference), self.assertRaises(CommandError):
                self.publish(reference, apply=True)

    def test_unapproved_terms_block_publication(self):
        from django.core.management.base import CommandError
        self.draft()
        with self.assertRaisesRegex(CommandError, 'TERMS_UNAVAILABLE'):
            self.publish('pro:1', apply=True)
        self.assertEqual(ProductVersion.objects.get().state, 'draft')
