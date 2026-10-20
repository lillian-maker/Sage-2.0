"""Django HTTP and ledger integration using synthetic APIv3 keys and no sockets."""
import base64
import io
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from unittest.mock import patch
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.db import close_old_connections, connection, transaction
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone
from urllib3.response import HTTPResponse
from urllib3._collections import HTTPHeaderDict

from backend.commerce import billing
from backend.commerce.models import CreditBatch, Order, PaymentAttempt, PaymentFact
from backend.commerce.tests import fixture_account, fixture_product
from backend.models import AccountSession, Membership


@override_settings(ENVIRONMENT='test', BILLING_SALES_ENABLED=True,
    BILLING_APPROVED_CHANNELS={'wechat': {'enabled': True, 'currency': 'CNY',
        'merchant_entity': 'fixture-shenzhen', 'regions': ['CN'], 'capabilities': ['one_time']}})
class WechatNativeHTTPTests(TransactionTestCase):
    """Catch duplicate dispatch, untrusted receipts and missing financial isolation."""
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
            'notify_origin': 'https://fixture.example', 'payment_ttl_seconds': 900}
        self.enterContext(override_settings(BILLING_WECHAT_NATIVE=self.configuration))
        self.user, self.account = fixture_account('wechat-native-fixture')
        self.member = Membership.objects.create(user=self.user, account=self.account, role='owner')
        self.product = fixture_product()
        quote = billing.create_quote(self.account, self.user, self.product.pk, 1,
            timezone.now()+timedelta(minutes=5), channel='wechat', buyer_region='CN')
        result = billing.create_order(self.account, self.user,
            {'quote_id': quote['id'], 'agreement_acceptance': 'fixture-1'}, 'wechat-native-order')
        self.order = Order.objects.get(pk=result['id'])
        self.path = '/api/billing/orders/'+str(self.order.pk)
        self.client.force_login(self.user)
        session = self.client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        self.metadata = AccountSession.objects.create(user=self.user,
            session_key=session.session_key, client='Synthetic fixture')
        self.sent, self.responses = [], []
        self.enterContext(patch.object(requests.adapters.HTTPAdapter, 'send', autospec=True,
            side_effect=self.transport))
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            self.enterContext(patch(target, side_effect=AssertionError('Wechat HTTP tests forbid sockets')))

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
        self.assertEqual(kwargs['verify'], True)
        self.assertEqual(kwargs['timeout'], (5, 15))
        self.assertFalse(kwargs.get('proxies'))
        self.sent.append((prepared.method, prepared.url, prepared.body))
        authorization = dict(re.findall(r'(\w+)="([^"]*)"', prepared.headers['Authorization']))
        body = prepared.body or b''
        canonical = (prepared.method+'\n'+prepared.path_url+'\n'+authorization['timestamp']+'\n'+
            authorization['nonce_str']+'\n').encode()+body+b'\n'
        self.merchant_key.public_key().verify(base64.b64decode(authorization['signature']),
            canonical, padding.PKCS1v15(), hashes.SHA256())
        self.assertEqual(authorization['mchid'], '1900000001')
        self.assertEqual(authorization['serial_no'], 'AABB01')
        attempt = PaymentAttempt.objects.get(order=self.order)
        self.assertIsNotNone(attempt.dispatched_at)
        self.assertEqual(attempt.dispatch_authorization, {'channel':'wechat', 'environment':'LIVE',
            'app_id':'wxSynthetic2026', 'mch_id':'1900000001', 'merchant_entity':'fixture-shenzhen'})
        status, fields = (self.response_router(prepared) if hasattr(self, 'response_router')
            else self.responses.pop(0))
        if isinstance(fields, BaseException):
            raise fields
        raw = fields if isinstance(fields, bytes) else json.dumps(fields, separators=(',', ':')).encode()
        headers = HTTPHeaderDict(self.signed_headers(raw))
        response = requests.Response()
        response.status_code, response.url, response.request = status, prepared.url, prepared
        response.raw = HTTPResponse(body=io.BytesIO(raw), headers=headers, preload_content=False)
        response.headers.update(headers)
        return response

    def post(self, action='pay', body='{}', client=None, header=True):
        return (client or self.client).post(self.path+'/'+action, body, content_type='application/json',
            **({'HTTP_X_BILLING_ACCOUNT':str(self.account.pk)} if header else {}))

    def detail(self):
        return self.client.get(self.path, HTTP_X_BILLING_ACCOUNT=str(self.account.pk))

    def native(self):
        self.responses.append((200, {'code_url':'weixin://wxpay/bizpayurl?pr=synthetic2026'}))
        response = self.post()
        self.assertEqual(response.status_code, 200, response.content)
        return PaymentAttempt.objects.get(order=self.order), response.json()

    def fields(self, state='SUCCESS', **changes):
        attempt = PaymentAttempt.objects.get(order=self.order)
        data = {'appid':'wxSynthetic2026', 'mchid':'1900000001', 'out_trade_no':attempt.pk.hex,
            'trade_type':'NATIVE', 'trade_state':state,
            'amount':{'total':731, 'currency':'CNY'}}
        if state == 'SUCCESS':
            data['transaction_id'] = '420000Synthetic2026'
            data['success_time'] = timezone.now().isoformat()
        return {**data, **changes}

    def notify(self, fields=None, *, mutate_signature=False):
        fields = fields or self.fields()
        nonce, aad = 'synthetic123', 'transaction'
        ciphertext = AESGCM(self.configuration['api_v3_key']).encrypt(nonce.encode(),
            json.dumps(fields, separators=(',', ':')).encode(), aad.encode())
        body = json.dumps({'id':'synthetic-notify', 'event_type':'TRANSACTION.SUCCESS',
            'resource_type':'encrypt-resource', 'resource':{'algorithm':'AEAD_AES_256_GCM',
                'nonce':nonce, 'associated_data':aad, 'ciphertext':base64.b64encode(ciphertext).decode()}},
            separators=(',', ':')).encode()
        headers = self.signed_headers(body)
        if mutate_signature:
            headers['Wechatpay-Signature'] = base64.b64encode(b'wrong').decode()
        return Client(enforce_csrf_checks=True, raise_request_exception=False).post('/api/payments/webhooks/wechat', body,
            content_type='application/json', **{'HTTP_'+key.upper().replace('-', '_'):value
                for key, value in headers.items()})

    def assert_empty_money(self):
        self.assertFalse(PaymentFact.objects.exists())
        self.assertFalse(CreditBatch.objects.exists())

    def test_native_commits_claim_and_persists_qr_for_read_only_order_recovery(self):
        before = self.detail()
        self.assertIn('payment_start', before.json())
        self.assertEqual(before.json()['payment_start'], {'channel':'wechat', 'environment':'LOCAL_TEST',
            'account_id':str(self.account.pk), 'order_id':str(self.order.pk), 'available':True})
        attempt, data = self.native()
        self.assertEqual(attempt.wechat_code_url, 'weixin://wxpay/bizpayurl?pr=synthetic2026')
        request = json.loads(self.sent[0][2])
        self.assertEqual(request['out_trade_no'], attempt.pk.hex)
        self.assertEqual(request['amount'], {'total':731, 'currency':'CNY'})
        self.assertEqual(request['notify_url'], 'https://fixture.example/api/payments/webhooks/wechat')
        qr = data['payment_qr']
        self.assertEqual(qr['attempt_id'], str(attempt.pk))
        self.assertEqual(qr['account_id'], str(self.account.pk))
        self.assertEqual(qr['order_id'], str(self.order.pk))
        self.assertIn(b'<svg', base64.b64decode(qr['image_svg_base64']))
        self.assertNotIn('weixin://', json.dumps(data))
        self.assertEqual(self.detail().json()['payment_qr'], qr)
        self.assertEqual(self.detail().json()['payment_attempt'].get('id'), str(attempt.pk))
        self.assertEqual(len(self.sent), 1)
        self.assert_empty_money()

    def test_query_success_creates_one_receipt_and_delivery_before_duplicate_notification(self):
        attempt, _ = self.native()
        fields = self.fields()
        self.responses.append((200, fields))
        response = self.post('payment-status')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['delivery']['status'], 'completed')
        self.assertNotIn('payment_qr', response.json())
        self.assertEqual(self.notify(fields).status_code, 204)
        self.assertEqual(self.notify(fields).status_code, 204)
        self.assertEqual(PaymentFact.objects.count(), 1)
        self.assertEqual(CreditBatch.objects.count(), 1)
        self.assertEqual(PaymentFact.objects.get().paid_at.isoformat(), fields['success_time'])
        attempt.refresh_from_db()
        self.assertEqual(attempt.state, 'succeeded')

    def test_duplicate_pay_queries_original_without_second_native(self):
        attempt, _ = self.native()
        self.responses.append((200, self.fields('NOTPAY')))
        response = self.post()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([entry[0] for entry in self.sent], ['POST', 'GET'])
        self.assertIn(attempt.pk.hex, self.sent[1][1])
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assert_empty_money()

    def test_default_and_production_reject_all_outbound_requests(self):
        for environment, configuration in (('test', {}), ('development', self.configuration),
                ('production', self.configuration)):
            with self.subTest(environment=environment), override_settings(ENVIRONMENT=environment,
                    BILLING_WECHAT_NATIVE=configuration):
                self.assertEqual(self.post().status_code, 503)
                self.assertEqual(self.post('payment-status').status_code, 503)
                self.assertEqual(self.notify({'out_trade_no':uuid.uuid4().hex}).status_code, 503)
        self.assertEqual(self.sent, [])
        self.assertFalse(PaymentAttempt.objects.exists())

    def test_anonymous_csrf_financial_role_and_account_context_are_required(self):
        for action in ('pay', 'payment-status'):
            self.assertEqual(self.post(action, client=Client()).status_code, 401)
            strict = Client(enforce_csrf_checks=True)
            strict.force_login(self.user)
            self.assertEqual(self.post(action, client=strict).status_code, 403)
            self.assertEqual(self.post(action, header=False).status_code, 409)
            Membership.objects.filter(pk=self.member.pk).update(role='member')
            self.assertEqual(self.post(action).status_code, 403)
            Membership.objects.filter(pk=self.member.pk).update(role='owner')
        self.assertEqual(self.sent, [])
        self.assert_empty_money()

    def test_notification_rejects_signature_amount_identity_reference_and_time_mismatch(self):
        self.native()
        self.assertEqual(self.notify(mutate_signature=True).status_code, 400)
        cases = [{'appid':'wxOther'}, {'mchid':'1900000002'}, {'out_trade_no':uuid.uuid4().hex},
            {'amount':{'total':732, 'currency':'CNY'}}, {'amount':{'total':731, 'currency':'USD'}},
            {'success_time':(timezone.now()-timedelta(hours=1)).isoformat()},
            {'success_time':(timezone.now()+timedelta(hours=1)).isoformat()}, {'trade_state':'NOTPAY'}]
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertEqual(self.notify(self.fields(**changes)).status_code, 400)
                self.assert_empty_money()

    def test_delivery_failure_keeps_receipt_and_notification_retry_recovers_once(self):
        self.native()
        fields = self.fields()
        with self.assertLogs('sage', level='ERROR') as captured, \
                patch.object(billing, 'fulfill_paid_order', side_effect=RuntimeError('synthetic failure')):
            response = self.notify(fields)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(b'synthetic failure', response.content)
        self.assertEqual(len(captured.output), 1)
        self.assertNotIn('synthetic failure', captured.output[0])
        self.assertEqual(PaymentFact.objects.count(), 1)
        self.assertEqual(CreditBatch.objects.count(), 0)
        self.assertEqual(self.notify(fields).status_code, 204)
        self.assertEqual(PaymentFact.objects.count(), 1)
        self.assertEqual(CreditBatch.objects.count(), 1)

    def test_stopping_new_sales_preserves_notification_and_original_query(self):
        self.native()
        fields = self.fields()
        with override_settings(BILLING_SALES_ENABLED=False, BILLING_APPROVED_CHANNELS={}):
            self.assertEqual(self.notify(fields).status_code, 204)
            self.responses.append((200, fields))
            self.assertEqual(self.post('payment-status').status_code, 200)
        self.assertEqual(PaymentFact.objects.count(), 1)
        self.assertEqual(CreditBatch.objects.count(), 1)

    def test_native_timeout_is_unknown_and_reentry_only_queries_original(self):
        self.responses.append((200, requests.Timeout('synthetic raw exception')))
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b'synthetic raw exception', response.content)
        self.assertTrue(PaymentAttempt.objects.filter(order=self.order).exists())
        attempt = PaymentAttempt.objects.get(order=self.order)
        self.assertEqual(attempt.state, 'unknown')
        self.assertIsNone(attempt.wechat_code_url)
        self.responses.append((200, self.fields('NOTPAY')))
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual([entry[0] for entry in self.sent], ['POST', 'GET'])
        self.assertNotIn('payment_qr', self.detail().json())
        self.assert_empty_money()

    def test_expiry_queries_then_claims_close_then_requires_independent_closed_query(self):
        attempt, _ = self.native()
        with patch('backend.payments.timezone.now', return_value=attempt.expires_at+timedelta(seconds=1)):
            self.assertNotIn('payment_qr', self.detail().json())
            self.responses.extend([(200, self.fields('NOTPAY')), (204, b''), (200, self.fields('CLOSED'))])
            response = self.post('payment-status')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([entry[0] for entry in self.sent], ['POST','GET','POST','GET'])
        self.assertTrue(self.sent[2][1].endswith('/'+attempt.pk.hex+'/close'))
        attempt.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual((attempt.state, self.order.status), ('cancelled','closed'))
        self.assertTrue(attempt.events.filter(event_key='wechat-close-claim').exists())
        self.assertTrue(attempt.events.filter(event_key='wechat-close-accepted', outcome='unknown').exists())
        self.assert_empty_money()

    def test_expiry_close_timeout_never_recloses_or_reissues_native(self):
        attempt, _ = self.native()
        with patch('backend.payments.timezone.now', return_value=attempt.expires_at+timedelta(seconds=1)):
            self.responses.extend([(200, self.fields('NOTPAY')), (200, requests.Timeout()),
                (200, self.fields('NOTPAY', trade_state_desc='Awaiting close')),
                (200, self.fields('NOTPAY', trade_state_desc='Still awaiting close'))])
            self.assertEqual(self.post('payment-status').status_code, 200)
            self.assertEqual(self.post('payment-status').status_code, 200)
        self.assertEqual([entry[0] for entry in self.sent], ['POST','GET','POST','GET','GET'])
        attempt.refresh_from_db()
        self.assertEqual(attempt.state, 'unknown')
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assert_empty_money()

    def test_late_success_after_confirmed_close_preserves_funds_for_review_without_delivery(self):
        attempt, _ = self.native()
        with patch('backend.payments.timezone.now', return_value=attempt.expires_at+timedelta(seconds=1)):
            self.responses.extend([(200,self.fields('NOTPAY')), (204,b''), (200,self.fields('CLOSED'))])
            self.assertEqual(self.post('payment-status').status_code, 200)
            self.assertEqual(self.notify().status_code, 204)
        self.assertEqual(PaymentFact.objects.get().disposition, 'late_payment_review')
        self.assertFalse(CreditBatch.objects.exists())
        self.assertNotIn('payment_qr', self.detail().json())

    def test_notification_first_and_stale_closed_query_never_erase_money_or_redeliver(self):
        self.native()
        fields = self.fields()
        self.assertEqual(self.notify(fields).status_code, 204)
        self.responses.append((200, self.fields('CLOSED')))
        self.assertEqual(self.post('payment-status').status_code, 200)
        self.responses.append((200, fields))
        self.assertEqual(self.post('payment-status').status_code, 200)
        self.assertEqual(PaymentFact.objects.count(), 1)
        self.assertEqual(CreditBatch.objects.count(), 1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'fulfilled')
        self.assertNotIn('payment_qr', self.detail().json())

    def test_signed_query_identity_amount_time_and_reference_mismatch_never_create_money(self):
        self.native()
        for changes in ({'appid':'wrong'}, {'mchid':'1900000002'}, {'out_trade_no':uuid.uuid4().hex},
                {'trade_type':'JSAPI'}, {'amount':{'total':True,'currency':'CNY'}},
                {'amount':{'total':732,'currency':'CNY'}}, {'success_time':'2020-01-01T00:00:00Z'}):
            self.responses.append((200, self.fields(**changes)))
            self.assertEqual(self.post('payment-status').status_code, 200)
            self.assert_empty_money()
            self.assertNotIn('payment_qr', self.detail().json())

    def test_qr_cannot_be_overwritten_or_disclosed_to_another_active_account(self):
        attempt, _ = self.native()
        attempt.wechat_code_url = 'weixin://wxpay/different'
        with self.assertRaisesRegex(ValueError, '^IMMUTABLE_BILLING_SNAPSHOT$'):
            attempt.save(update_fields=['wechat_code_url'])
        _, account = fixture_account('wechat-other-account')
        Membership.objects.create(user=self.user, account=account, role='owner')
        session = self.client.session
        session['active_account_id'] = str(account.pk)
        session.save()
        response = self.client.get(self.path, HTTP_X_BILLING_ACCOUNT=str(account.pk))
        self.assertEqual(response.status_code, 404)
        response = self.client.post(self.path+'/payment-status', '{}', content_type='application/json',
            HTTP_X_BILLING_ACCOUNT=str(account.pk))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(self.sent), 1)

    def test_invalid_configuration_and_order_identity_fail_before_dispatch(self):
        cases = [{'enabled':False}, {'unknown':True}, {'app_id':'bad app'}, {'mch_id':'abc'},
            {'merchant_serial':'not hex'}, {'public_key_id':'wrong'}, {'api_v3_key':b'x'*31},
            {'notify_origin':'http://fixture.example'}, {'notify_origin':'https://fixture.example/path'},
            {'payment_ttl_seconds':True}, {'payment_ttl_seconds':0}, {'public_key':b'not a key'},
            {'private_key':b'not a key'}]
        for changes in cases:
            with self.subTest(changes=list(changes)), override_settings(
                    BILLING_WECHAT_NATIVE={**self.configuration, **changes}):
                self.assertEqual(self.post().status_code, 503)
                self.assertNotIn('payment_start', self.detail().json())
        self.assertEqual(self.sent, [])
        self.assertFalse(PaymentAttempt.objects.exists())

    def test_claim_rechecks_session_revocation_before_native_transport(self):
        original = billing.claim_payment_dispatch
        def revoke_then_claim(*args, **kwargs):
            self.metadata.revoked_at = timezone.now()
            self.metadata.save(update_fields=['revoked_at'])
            return original(*args, **kwargs)
        with patch.object(billing, 'claim_payment_dispatch', side_effect=revoke_then_claim):
            self.assertEqual(self.post().status_code, 401)
        attempt = PaymentAttempt.objects.get(order=self.order)
        self.assertEqual(attempt.state, 'created')
        self.assertIsNone(attempt.dispatched_at)
        self.assertEqual(self.sent, [])

    def test_query_delivery_failure_returns_fixed_error_and_query_retry_recovers(self):
        self.responses.append((200, SystemExit('synthetic process exit')))
        with self.assertRaises(SystemExit):
            self.post()
        fields = self.fields()
        self.responses.append((200, fields))
        self.client.raise_request_exception = False
        with self.assertLogs('sage', level='ERROR') as captured, \
                patch.object(billing, 'fulfill_paid_order', side_effect=RuntimeError('QUERY_RAW_MUST_NOT_LEAK')):
            response = self.post('payment-status')
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['error']['code'], 'PAYMENT_QUERY_FAILED')
        self.assertNotIn(b'QUERY_RAW_MUST_NOT_LEAK', response.content)
        self.assertEqual(len(captured.output), 1)
        self.assertNotIn('QUERY_RAW_MUST_NOT_LEAK', captured.output[0])
        self.assertEqual(PaymentFact.objects.count(), 1)
        self.assertFalse(CreditBatch.objects.exists())
        self.responses.append((200, fields))
        self.assertEqual(self.post('payment-status').status_code, 200)
        self.assertEqual(CreditBatch.objects.count(), 1)

    def test_callback_lookup_failure_returns_fixed_error_without_exception_details(self):
        self.responses.append((200, SystemExit('synthetic process exit')))
        with self.assertRaises(SystemExit):
            self.post()
        with self.assertLogs('sage', level='ERROR') as captured, \
                patch.object(PaymentAttempt.objects, 'select_related', side_effect=RuntimeError('LOOKUP_RAW_MUST_NOT_LEAK')):
            response = self.notify()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['error']['code'], 'PAYMENT_CALLBACK_FAILED')
        self.assertNotIn(b'LOOKUP_RAW_MUST_NOT_LEAK', response.content)
        self.assertEqual(len(captured.output), 1)
        self.assertNotIn('LOOKUP_RAW_MUST_NOT_LEAK', captured.output[0])
        self.assert_empty_money()

    def test_parallel_http_pay_requests_commit_only_one_native_dispatch(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Real row-lock concurrency requires isolated PostgreSQL.')
        from backend import payments
        ready = Barrier(2)
        original = payments.finish_payment
        def finish(request, prepared):
            ready.wait(timeout=10)
            return original(request, prepared)
        self.response_router = lambda request: ((200, {'code_url':'weixin://wxpay/synthetic'})
            if request.url.endswith('/native') else (200,self.fields('NOTPAY')))
        def worker():
            close_old_connections()
            client = Client()
            client.cookies = self.client.cookies.copy()
            try:
                return self.post(client=client).status_code
            finally:
                close_old_connections()
        with patch.object(payments, 'finish_payment', side_effect=finish), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:worker(), range(2)))
        self.assertEqual(results, [200,200])
        self.assertEqual(sum(entry[1].endswith('/native') for entry in self.sent), 1)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assert_empty_money()

    def test_committed_session_revocation_wins_native_dispatch_lock_race(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Real row-lock revocation requires isolated PostgreSQL.')
        from backend import payments
        ready, release, waiting = Event(), Event(), Event()
        original = payments.finish_payment
        def finish(request, prepared):
            ready.set()
            if not release.wait(timeout=10):
                raise AssertionError('Revocation transaction did not release dispatch')
            return original(request, prepared)
        def worker():
            close_old_connections()
            client = Client()
            client.cookies = self.client.cookies.copy()
            def signal(execute, sql, params, many, context):
                if ready.is_set() and 'FOR UPDATE' in sql and 'backend_accountsession' in sql:
                    waiting.set()
                return execute(sql, params, many, context)
            try:
                with connection.execute_wrapper(signal):
                    return self.post(client=client).status_code
            finally:
                close_old_connections()
        with patch.object(payments, 'finish_payment', side_effect=finish), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker)
            self.assertTrue(ready.wait(timeout=10))
            self.assertEqual(PaymentAttempt.objects.get(order=self.order).state, 'created')
            with transaction.atomic():
                AccountSession.objects.select_for_update().get(pk=self.metadata.pk)
                release.set()
                self.assertTrue(waiting.wait(timeout=10))
                AccountSession.objects.filter(pk=self.metadata.pk).update(revoked_at=timezone.now())
            self.assertEqual(future.result(timeout=10), 401)
        self.assertEqual(self.sent, [])
        attempt = PaymentAttempt.objects.get(order=self.order)
        self.assertIsNone(attempt.dispatched_at)
        self.assert_empty_money()

    def test_native_process_exit_leaves_original_claim_for_query_only_recovery(self):
        self.responses.append((200, SystemExit('synthetic process exit')))
        with self.assertRaises(SystemExit):
            self.post()
        attempt = PaymentAttempt.objects.get(order=self.order)
        self.assertEqual(attempt.state, 'pending')
        self.assertIsNotNone(attempt.dispatched_at)
        self.assertIsNone(attempt.wechat_code_url)
        self.responses.append((200, self.fields('NOTPAY')))
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual([entry[0] for entry in self.sent], ['POST','GET'])
        self.assertNotIn('payment_qr', self.detail().json())
        self.assert_empty_money()

    def test_only_exact_webhook_path_is_csrf_exempt_and_raw_body_is_bounded(self):
        client = Client(enforce_csrf_checks=True)
        for path in ('/api/payments/webhooks/wechat/', '/api/payments/webhooks/other'):
            self.assertEqual(client.post(path, '{}', content_type='application/json').status_code, 403)
        path = '/api/payments/webhooks/wechat'
        self.assertEqual(client.post(path, '{}', content_type='application/json').status_code, 400)
        self.assertEqual(client.post(path, b'x'*65537, content_type='application/json').status_code, 400)
        self.assertEqual(client.post(path, '{}', content_type='text/plain').status_code, 400)
        self.assertEqual(client.post(path+'?override=1', '{}', content_type='application/json').status_code, 400)
        self.assertEqual(self.sent, [])
        self.assert_empty_money()

    def test_browser_cannot_supply_payment_identity_amount_or_query_parameters(self):
        for action in ('pay','payment-status'):
            for body in ('{"amount_minor":1}', '{"app_id":"wrong"}', '[]'):
                self.assertEqual(self.post(action, body).status_code, 422)
            response = self.client.post(self.path+'/'+action+'?paid=true', '{}',
                content_type='application/json', HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.sent, [])
        self.assertFalse(PaymentAttempt.objects.exists())

    def test_callback_rejects_attempt_bound_to_a_different_order_account(self):
        self.responses.append((200, SystemExit('synthetic process exit')))
        with self.assertRaises(SystemExit):
            self.post()
        fields = self.fields()
        _, other = fixture_account('wechat-corrupted-account')
        PaymentAttempt.objects.filter(order=self.order).update(account=other)
        self.assertEqual(self.notify(fields).status_code, 400)
        self.assert_empty_money()

    def test_notification_racing_native_response_keeps_paid_state_and_hides_qr(self):
        def respond(prepared):
            self.assertEqual(self.notify().status_code, 204)
            return 200, {'code_url':'weixin://wxpay/synthetic'}
        self.response_router = respond
        response = self.post()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['payment_attempt']['status'], 'succeeded')
        self.assertNotIn('payment_qr', response.json())
        attempt = PaymentAttempt.objects.get(order=self.order)
        self.assertIsNone(attempt.wechat_code_url)
        self.assertEqual(PaymentFact.objects.count(), 1)
        self.assertEqual(CreditBatch.objects.count(), 1)

    def fractional_second_dispatch(self):
        dispatched_at = timezone.now().replace(microsecond=750000)-timedelta(seconds=1)
        with patch('backend.payments.timezone.now', return_value=dispatched_at):
            attempt, _ = self.native()
        self.assertEqual(attempt.dispatched_at.microsecond, 750000)
        return dispatched_at.replace(microsecond=0)

    def test_query_accepts_provider_seconds_in_dispatch_second_without_changing_paid_at(self):
        paid_at = self.fractional_second_dispatch()
        self.responses.append((200, self.fields(success_time=paid_at.isoformat(timespec='seconds'))))
        response = self.post('payment-status')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['delivery']['status'], 'completed')
        self.assertEqual(PaymentFact.objects.get().paid_at, paid_at)
        self.assertEqual(CreditBatch.objects.count(), 1)

    def test_notification_accepts_provider_seconds_in_dispatch_second_without_changing_paid_at(self):
        paid_at = self.fractional_second_dispatch()
        response = self.notify(self.fields(success_time=paid_at.isoformat(timespec='seconds')))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(PaymentFact.objects.get().paid_at, paid_at)
        self.assertEqual(CreditBatch.objects.count(), 1)

    def test_query_rejects_success_time_in_second_before_dispatch(self):
        paid_at = self.fractional_second_dispatch()-timedelta(seconds=1)
        self.responses.append((200, self.fields(success_time=paid_at.isoformat(timespec='seconds'))))
        response = self.post('payment-status')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['delivery']['status'], 'not_started')
        self.assert_empty_money()

    def test_notification_rejects_success_time_in_second_before_dispatch(self):
        paid_at = self.fractional_second_dispatch()-timedelta(seconds=1)
        response = self.notify(self.fields(success_time=paid_at.isoformat(timespec='seconds')))
        self.assertEqual(response.status_code, 400)
        self.assert_empty_money()
