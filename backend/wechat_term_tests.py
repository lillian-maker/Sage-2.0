"""WeChat Pay Native for fixed-term plans and Credits packs, cancellation, sweeping and refunds."""
import base64
import io
import json
import re
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.management import call_command
from django.db import connection
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone
from urllib3._collections import HTTPHeaderDict
from urllib3.response import HTTPResponse

from backend import payments
from backend.commerce import billing
from backend.commerce.models import (CreditBatch, CreditRefund, Order, PaymentAttempt, PaymentFact,
    ProductVersion, Subscription, SubscriptionPeriod)
from backend.commerce.tests import fixture_account
from backend.models import AccountSession, Membership

TERMS = {'fixture-1': {'approved': True,
    'zh': {'title': '测试购买条款', 'paragraphs': ['仅用于隔离测试。']},
    'en': {'title': 'Test purchase terms', 'paragraphs': ['Isolated tests only.']}}}
BENEFITS = {'benefits': {'zh': ['测试权益'], 'en': ['Test benefit']}}


def channel(*capabilities):
    return {'wechat': {'enabled': True, 'currency': 'CNY', 'merchant_entity': 'fixture-shenzhen',
        'regions': ['CN'], 'capabilities': list(capabilities)}}


@override_settings(ENVIRONMENT='production', BILLING_SALES_ENABLED=True, BILLING_WECHAT_LIVE_ENABLED=True,
    BILLING_APPROVED_CHANNELS=channel('one_time', 'term_monthly', 'term_annual'),
    BILLING_TERMS_DOCUMENTS=TERMS, BILLING_CREDITS_PURCHASE_ENABLED=True)
class WechatFixedTermTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.merchant_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.platform_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_pem = cls.merchant_key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        cls.public_pem = cls.platform_key.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)

    def setUp(self):
        self.configuration = {'enabled': True, 'app_id': 'wxSynthetic2026', 'mch_id': '1900000001',
            'merchant_entity': 'fixture-shenzhen', 'merchant_serial': 'AABB01',
            'private_key': self.private_pem, 'public_key': self.public_pem,
            'public_key_id': 'PUB_KEY_ID_20261016', 'api_v3_key': b'x'*32,
            'notify_origin': 'https://sage.example', 'payment_ttl_seconds': 900}
        self.enterContext(override_settings(BILLING_WECHAT_NATIVE=self.configuration))
        self.user, self.account = fixture_account('wechat-term-buyer')
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
        self.sent, self.responses, self.paid_times = [], [], {}
        self.enterContext(patch.object(requests.adapters.HTTPAdapter, 'send', autospec=True,
            side_effect=self.transport))
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            self.enterContext(patch(target, side_effect=AssertionError('Must stay offline')))

    def signed_headers(self, body):
        timestamp, nonce = str(int(timezone.now().timestamp())), 'synthetic_response_nonce'
        signature = self.platform_key.sign(timestamp.encode()+b'\n'+nonce.encode()+b'\n'+body+b'\n',
            padding.PKCS1v15(), hashes.SHA256())
        return {'Wechatpay-Timestamp': timestamp, 'Wechatpay-Nonce': nonce,
            'Wechatpay-Serial': self.configuration['public_key_id'],
            'Wechatpay-Signature': base64.b64encode(signature).decode()}

    def transport(self, adapter, prepared, **kwargs):
        self.assertFalse(connection.in_atomic_block, 'Money transport must follow transaction commit')
        self.assertEqual(urlsplit(prepared.url).netloc, 'api.mch.weixin.qq.com')
        authorization = dict(re.findall(r'(\w+)="([^"]*)"', prepared.headers['Authorization']))
        body = prepared.body or b''
        canonical = (prepared.method+'\n'+prepared.path_url+'\n'+authorization['timestamp']+'\n'+
            authorization['nonce_str']+'\n').encode()+body+b'\n'
        self.merchant_key.public_key().verify(base64.b64decode(authorization['signature']),
            canonical, padding.PKCS1v15(), hashes.SHA256())
        self.sent.append((prepared.method, urlsplit(prepared.url).path, body))
        status, fields = self.responses.pop(0)
        if isinstance(fields, BaseException):
            raise fields
        raw = fields if isinstance(fields, bytes) else json.dumps(fields, separators=(',', ':')).encode()
        headers = HTTPHeaderDict(self.signed_headers(raw))
        response = requests.Response()
        response.status_code, response.url, response.request = status, prepared.url, prepared
        response.raw = HTTPResponse(body=io.BytesIO(raw), headers=headers, preload_content=False)
        response.headers.update(headers)
        return response

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
            'channel': 'wechat', 'buyer_region': 'CN'})
        self.assertEqual(quote.status_code, 201, quote.content)
        order = self.api('billing/orders', {'quote_id': quote.json()['id'],
            'agreement_acceptance': 'fixture-1'}, HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(order.status_code, 201, order.content)
        return Order.objects.get(pk=order.json()['id'])

    def pay(self, order):
        self.responses.append((200, {'code_url': 'weixin://wxpay/bizpayurl?pr=synthetic2026'}))
        response = self.api(f'billing/orders/{order.pk}/pay', {})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('payment_qr', response.json())
        return PaymentAttempt.objects.get(order=order)

    def trade(self, order, state='SUCCESS', **changes):
        attempt = PaymentAttempt.objects.get(order=order)
        data = {'appid': 'wxSynthetic2026', 'mchid': '1900000001', 'out_trade_no': attempt.pk.hex,
            'trade_type': 'NATIVE', 'trade_state': state,
            'amount': {'total': order.amount_minor, 'currency': 'CNY'}}
        if state == 'SUCCESS':
            data['transaction_id'] = '4200'+str(order.amount_minor)
            # A provider retry repeats the original success_time.
            data['success_time'] = self.paid_times.setdefault(order.pk, timezone.now().isoformat())
        return {**data, **changes}

    def notify(self, order):
        nonce, aad = 'synthetic123', 'transaction'
        ciphertext = AESGCM(self.configuration['api_v3_key']).encrypt(nonce.encode(),
            json.dumps(self.trade(order), separators=(',', ':')).encode(), aad.encode())
        body = json.dumps({'id': 'synthetic-notify', 'event_type': 'TRANSACTION.SUCCESS',
            'resource_type': 'encrypt-resource', 'resource': {'algorithm': 'AEAD_AES_256_GCM',
                'nonce': nonce, 'associated_data': aad, 'ciphertext': base64.b64encode(ciphertext).decode()}},
            separators=(',', ':')).encode()
        return Client(enforce_csrf_checks=True).post('/api/payments/webhooks/wechat', body,
            content_type='application/json', **{'HTTP_'+key.upper().replace('-', '_'): value
                for key, value in self.signed_headers(body).items()})

    def paid_term(self):
        order = self.order(self.monthly, 'term-order')
        self.pay(order)
        self.assertEqual(self.notify(order).status_code, 204)
        return order

    def test_fixed_term_plan_paid_by_native_activates_one_period_without_renewal(self):
        item = self.catalog()[('pro', 'monthly')]
        self.assertTrue(item['quote_available'])
        self.assertEqual(item['purchase_options'],
            [{'channel': 'wechat', 'buyer_region': 'CN', 'renewal': 'none'}])
        order = self.order(self.monthly, 'term-order')
        self.assertEqual(order.snapshot['renewal'], 'none')
        detail = self.api(f'billing/orders/{order.pk}').json()
        self.assertEqual(detail['payment_start']['channel'], 'wechat')
        self.assertEqual(detail['payment_start']['environment'], 'LIVE')
        attempt = self.pay(order)
        method, path, body = self.sent[0]
        request = json.loads(body)
        self.assertEqual((method, path), ('POST', '/v3/pay/transactions/native'))
        self.assertEqual(request['amount'], {'total': 731, 'currency': 'CNY'})
        self.assertEqual(request['out_trade_no'], attempt.pk.hex)
        self.assertEqual(request['notify_url'], 'https://sage.example/api/payments/webhooks/wechat')
        self.assertFalse(Subscription.objects.exists())
        self.assertEqual(self.notify(order).status_code, 204)
        self.assertEqual(self.notify(order).status_code, 204)
        order.refresh_from_db()
        self.assertEqual(order.status, 'fulfilled')
        subscription = Subscription.objects.get()
        self.assertFalse(subscription.auto_renew)
        period = SubscriptionPeriod.objects.get()
        self.assertEqual(period.starts_at, order.paid_at)
        self.assertTrue(timedelta(days=28) < period.ends_at-period.starts_at <= timedelta(days=32))
        self.assertEqual(PaymentFact.objects.get().channel, 'wechat')

        catalog = self.catalog()
        self.assertFalse(catalog[('pro', 'annual')]['quote_available'])
        self.assertTrue(catalog[('pack', '')]['quote_available'])
        pack = self.order(self.pack, 'pack-order')
        self.assertNotIn('renewal', pack.snapshot)
        self.pay(pack)
        self.assertEqual(self.notify(pack).status_code, 204)
        pack.refresh_from_db()
        self.assertEqual(pack.status, 'fulfilled')
        self.assertTrue(CreditBatch.objects.filter(order=pack, source='purchased').exists())

    def test_annual_term_is_paid_by_query(self):
        order = self.order(self.annual, 'annual-order')
        self.pay(order)
        self.responses.append((200, self.trade(order)))
        response = self.api(f'billing/orders/{order.pk}/payment-status', {})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['order']['status'], 'fulfilled')
        period = SubscriptionPeriod.objects.get()
        self.assertTrue(timedelta(days=365) < period.ends_at-period.starts_at <= timedelta(days=367))

    def test_recurring_only_channel_and_forged_terms_never_reach_native(self):
        with override_settings(BILLING_APPROVED_CHANNELS=channel('one_time', 'recurring_monthly')):
            order = self.order(self.monthly, 'recurring-order')
            self.assertNotIn('renewal', order.snapshot)
            self.assertEqual(self.api(f'billing/orders/{order.pk}/pay', {}).status_code, 503)
        Order.objects.filter(pk=order.pk).update(status='closed')
        order = self.order(self.monthly, 'forged-order')
        for snapshot in ({**order.snapshot, 'renewal': 'auto'},
                         {key: value for key, value in order.snapshot.items() if key != 'renewal'},
                         {**order.snapshot, 'billing_cycle': 'annual'}):
            Order.objects.filter(pk=order.pk).update(snapshot=snapshot)
            self.assertEqual(self.api(f'billing/orders/{order.pk}/pay', {}).status_code, 503)
        self.assertFalse(PaymentAttempt.objects.exists())
        self.assertEqual(self.sent, [])

    def test_cancel_closes_the_native_trade_on_independent_closed_query(self):
        order = self.order(self.monthly, 'cancel-order')
        attempt = self.pay(order)
        self.responses.extend([(204, b''), (200, self.trade(order, 'CLOSED'))])
        response = self.api(f'billing/orders/{order.pk}/cancel', {})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['cancel_status'], 'cancelled')
        self.assertEqual([(m, p) for m, p, _ in self.sent[1:]], [
            ('POST', f'/v3/pay/transactions/out-trade-no/{attempt.pk.hex}/close'),
            ('GET', f'/v3/pay/transactions/out-trade-no/{attempt.pk.hex}')])
        order.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual((order.status, attempt.state), ('closed', 'cancelled'))
        self.assertTrue(self.catalog()[('pro', 'monthly')]['quote_available'])

    def test_unconfirmed_close_is_resent_only_by_cancel_never_by_query(self):
        order = self.order(self.monthly, 'retry-order')
        attempt = self.pay(order)
        self.responses.extend([(200, requests.Timeout()), (200, self.trade(order, 'NOTPAY'))])
        self.assertEqual(self.api(f'billing/orders/{order.pk}/cancel', {}).json()['cancel_status'], 'pending')
        self.responses.append((200, self.trade(order, 'NOTPAY')))
        self.assertEqual(self.api(f'billing/orders/{order.pk}/payment-status', {}).status_code, 200)
        self.assertEqual([m for m, _, _ in self.sent], ['POST', 'POST', 'GET', 'GET'])
        self.responses.extend([(204, b''), (200, self.trade(order, 'CLOSED'))])
        self.assertEqual(self.api(f'billing/orders/{order.pk}/cancel', {}).json()['cancel_status'], 'cancelled')
        self.assertEqual([m for m, _, _ in self.sent], ['POST', 'POST', 'GET', 'GET', 'POST', 'GET'])
        self.assertEqual(attempt.events.filter(event_key='wechat-close-claim').count(), 1)
        self.assertEqual(attempt.events.filter(event_key__startswith='wechat-close-retry:').count(), 1)
        # A confirmed close is never sent again.
        self.assertEqual(self.api(f'billing/orders/{order.pk}/cancel', {}).json()['cancel_status'], 'cancelled')
        self.assertEqual(len(self.sent), 6)

    def test_cancel_racing_payment_delivers_the_paid_order(self):
        order = self.order(self.monthly, 'race-order')
        self.pay(order)
        self.responses.extend([(400, b'{"code":"ORDERPAID"}'), (200, self.trade(order))])
        response = self.api(f'billing/orders/{order.pk}/cancel', {})
        self.assertEqual(response.json()['cancel_status'], 'paid')
        order.refresh_from_db()
        self.assertEqual(order.status, 'fulfilled')

    def test_sweeper_closes_an_overdue_native_trade(self):
        order = self.order(self.monthly, 'overdue-order')
        attempt = self.pay(order)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now()-timedelta(minutes=30))
        PaymentAttempt.objects.filter(pk=attempt.pk).update(expires_at=timezone.now()-timedelta(minutes=5))
        self.responses.extend([(204, b''), (200, self.trade(order, 'CLOSED'))])
        out = StringIO()
        call_command('close_unpaid_orders', stdout=out)
        self.assertIn('channel_closed: 1', out.getvalue())
        self.assertIn('unsupported_channel: 0', out.getvalue())
        self.assertEqual(Order.objects.get(pk=order.pk).status, 'closed')

    def refundable_pack(self):
        self.paid_term()
        pack = self.order(self.pack, 'refund-pack')
        attempt = self.pay(pack)
        self.assertEqual(self.notify(pack).status_code, 204)
        reserved = billing.reserve_credit_refund(self.account, pack.pk, 'reviewed-wechat-refund',
            review_evidence_hash='b'*64)
        self.sent.clear()
        return pack, attempt, CreditRefund.objects.get(pk=reserved['id'])

    def refund_fields(self, pack, attempt, refund, **changes):
        return {'refund_id': '5030synthetic', 'out_refund_no': refund.pk.hex, 'transaction_id': '4200500',
            'out_trade_no': attempt.pk.hex, 'status': 'SUCCESS', 'success_time': timezone.now().isoformat(),
            'amount': {'total': 500, 'refund': refund.amount_minor, 'currency': 'CNY'}, **changes}

    def test_refund_is_claimed_once_and_resolved_only_by_verified_query(self):
        pack, attempt, refund = self.refundable_pack()
        with self.assertRaisesRegex(ValueError, '^CHANNEL_UNAVAILABLE$'):
            payments.submit_wechat_refund(self.account, refund.pk)
        self.assertEqual(self.sent, [])
        with override_settings(BILLING_WECHAT_LIVE_REFUND_ENABLED=True):
            self.responses.extend([(200, {'status': 'PROCESSING'}),
                (200, self.refund_fields(pack, attempt, refund, status='PROCESSING'))])
            self.assertEqual(payments.submit_wechat_refund(self.account, refund.pk)['query_status'], 'unknown')
            request = json.loads(self.sent[0][2])
            self.assertEqual(self.sent[0][1], '/v3/refund/domestic/refunds')
            self.assertEqual(request, {'out_trade_no': attempt.pk.hex, 'out_refund_no': refund.pk.hex,
                'amount': {'refund': refund.amount_minor, 'total': 500, 'currency': 'CNY'}})
            self.assertEqual(self.sent[1][1], '/v3/refund/domestic/refunds/'+refund.pk.hex)
            with self.assertRaisesRegex(ValueError, '^REFUND_RECONCILIATION_REQUIRED$'):
                billing.resolve_credit_refund(self.account, refund.pk, 'released', evidence_hash='c'*64)
            for changes in ({'amount': {'total': 500, 'refund': refund.amount_minor+1, 'currency': 'CNY'}},
                            {'out_trade_no': 'f'*32}, {'transaction_id': 'other'}, {'out_refund_no': 'other'}):
                self.responses.append((200, self.refund_fields(pack, attempt, refund, **changes)))
                self.assertEqual(payments.submit_wechat_refund(self.account, refund.pk)['query_status'], 'unknown')
            refund.refresh_from_db()
            self.assertEqual((refund.state, refund.freeze.state), ('reserved', 'frozen'))
            self.responses.append((200, self.refund_fields(pack, attempt, refund)))
            self.assertEqual(payments.submit_wechat_refund(self.account, refund.pk)['state'], 'refunded')
            self.assertEqual(payments.query_wechat_refund(self.account, refund.pk)['state'], 'refunded')
        self.assertEqual([m for m, _, _ in self.sent], ['POST']+['GET']*6)
        refund.refresh_from_db()
        self.assertEqual((refund.state, refund.freeze.state), ('refunded', 'refunded'))

    def test_refund_submit_timeout_never_resubmits(self):
        pack, attempt, refund = self.refundable_pack()
        with override_settings(BILLING_WECHAT_LIVE_REFUND_ENABLED=True):
            self.responses.extend([(200, requests.Timeout()), (200, requests.Timeout())])
            self.assertEqual(payments.submit_wechat_refund(self.account, refund.pk)['query_status'], 'unknown')
            self.responses.append((200, self.refund_fields(pack, attempt, refund)))
            self.assertEqual(payments.submit_wechat_refund(self.account, refund.pk)['state'], 'refunded')
        self.assertEqual([m for m, _, _ in self.sent], ['POST', 'GET', 'GET'])
        self.assertTrue(attempt.events.filter(event_key='wechat-refund-submit-unknown:'+str(refund.pk)).exists())


class WechatSalesConfigurationTests(TransactionTestCase):
    def config(self, **changes):
        return {'sales_enabled': True, 'alipay_live_enabled': True, 'alipay_egress_proxy': True,
            'credits_purchase_enabled': True, 'wechat_live_enabled': True, 'wechat_egress_proxy': True,
            'approved_channels': channel('one_time', 'term_monthly', 'term_annual'),
            'terms_documents': TERMS, **changes}

    def load(self, config):
        import tempfile
        from pathlib import Path
        from backend.billing_sales_config import load_billing_sales
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'billing_sales.json'
            path.write_text(json.dumps(config), encoding='utf-8')
            path.chmod(0o440)
            return load_billing_sales(path)

    def test_wechat_switches_map_to_settings(self):
        loaded = self.load(self.config())
        self.assertEqual(loaded['BILLING_APPROVED_CHANNELS'], channel('one_time', 'term_monthly', 'term_annual'))
        self.assertIs(loaded['BILLING_WECHAT_LIVE_ENABLED'], True)
        self.assertEqual(loaded['BILLING_WECHAT_EGRESS_PROXY'], 'http://egress:3128')
        self.assertEqual(self.load(self.config(wechat_egress_proxy=False))['BILLING_WECHAT_EGRESS_PROXY'], '')

    def test_wechat_channel_requires_both_switches(self):
        base = self.config()
        for config in ({key: value for key, value in base.items() if key != 'wechat_egress_proxy'},
                       {key: value for key, value in base.items() if key not in WECHAT_KEYS},
                       {**base, 'wechat_live_enabled': 1},
                       {**base, 'approved_channels': channel('recurring_monthly')}):
            with self.subTest(config=sorted(config)), self.assertRaises(ValueError):
                self.load(config)
        without = {key: value for key, value in base.items() if key not in WECHAT_KEYS}
        without['approved_channels'] = {'alipay': channel('one_time')['wechat']}
        self.assertNotIn('BILLING_WECHAT_LIVE_ENABLED', self.load(without))


WECHAT_KEYS = ('wechat_live_enabled', 'wechat_egress_proxy')
