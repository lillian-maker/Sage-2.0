"""HTTP boundaries and isolated synthetic signed payment evidence; no live transport."""
import json
import uuid
import base64
from datetime import datetime, timedelta, timezone as datetime_timezone
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.apps import apps
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from backend.commerce.models import BillingNotification, CreditBatch, CreditEntry, Order, PaymentAttempt, PaymentFact, Quote
from backend.commerce.tests import fixture_account, fixture_product
from backend.models import AccountSession, Membership
from backend.views import api
from backend import payments


class PaymentEntryTests(TestCase):
    def setUp(self):
        self.user, self.account = fixture_account('payment-entry-fixture')
        self.member = Membership.objects.create(user=self.user, account=self.account, role='owner')
        product = fixture_product()
        snapshot = {'channel': 'alipay', 'merchant_entity': 'fixture-shenzhen',
                    'amount_minor': 731, 'currency': 'CNY'}
        quote = Quote.objects.create(account=self.account, product=product, snapshot=snapshot,
            amount_minor=731, currency='CNY', expires_at=timezone.now()+timedelta(minutes=5))
        self.order = Order.objects.create(account=self.account, created_by=self.user, quote=quote,
            idempotency_key='payment-entry-fixture', request_hash='a'*64,
            snapshot=snapshot, amount_minor=731, currency='CNY')
        self.url = f'/api/billing/orders/{self.order.pk}/pay'
        self.client.force_login(self.user)
        session = self.client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        self.session_key = session.session_key
        # A missing route must fail through HTTP, not through a test substitute.
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            guard = patch(target, side_effect=AssertionError('Payment entry must not use network'))
            guard.start()
            self.addCleanup(guard.stop)

    def commerce_state(self):
        return {model._meta.label: list(model.objects.order_by('pk').values())
                for model in apps.get_app_config('commerce').get_models()}

    def post(self, *, client=None, body='{}', suffix='', header=True, url=None,
             content_type='application/json'):
        before = self.commerce_state()
        headers = {'HTTP_X_BILLING_ACCOUNT': str(self.account.pk)} if header is True else (
            {} if header is False else {'HTTP_X_BILLING_ACCOUNT': header})
        headers['CONTENT_TYPE'] = content_type
        response = (client or self.client).post((url or self.url)+suffix, data=body,
                                               content_type=content_type, **headers)
        self.assertEqual(self.commerce_state(), before)
        self.assertEqual(response['Cache-Control'], 'no-store')
        return response

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        data = json.loads(response.content)
        self.assertEqual(data['error']['code'], code)
        self.assertNotIn('order', data)
        self.assertNotIn('payment_url', data)

    def test_anonymous_cannot_dispatch_payment(self):
        self.assert_error(self.post(client=Client()), 401, 'AUTH_REQUIRED')

    def test_authorized_order_returns_unavailable_without_financial_writes(self):
        self.assert_error(self.post(), 503, 'CHANNEL_UNAVAILABLE')

    def test_repeated_clicks_create_no_attempts_or_funds(self):
        for _ in range(2):
            self.assert_error(self.post(), 503, 'CHANNEL_UNAVAILABLE')

    def test_static_channel_approval_does_not_enable_transport(self):
        with override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS={
                'alipay': {'enabled': True, 'currency': 'CNY',
                    'merchant_entity': 'fixture-shenzhen', 'regions': ['CN'],
                    'capabilities': ['one_time']}}):
            self.assert_error(self.post(), 503, 'CHANNEL_UNAVAILABLE')

    def test_client_cannot_choose_amount_channel_merchant_or_payment_result(self):
        for field, value in (('amount_minor', 1), ('currency', 'USD'), ('channel', 'wechat'),
                ('merchant_entity', 'other'), ('merchant_reference', 'other'),
                ('expires_at', '2099-01-01'), ('paid', True), ('payment_url', 'https://example.invalid')):
            with self.subTest(field=field):
                self.assert_error(self.post(body=json.dumps({field: value})), 422, 'INVALID_REQUEST')

    def test_query_parameters_are_rejected(self):
        self.assert_error(self.post(suffix='?amount_minor=1'), 422, 'INVALID_REQUEST')

    def test_body_must_be_empty_json_object(self):
        for body, code in (('[]', 'INVALID_REQUEST'), ('null', 'INVALID_REQUEST'),
                           ('""', 'INVALID_REQUEST'), ('{', 'INVALID_JSON'), ('', 'INVALID_JSON')):
            with self.subTest(body=body):
                self.assert_error(self.post(body=body), 422, code)
        self.assert_error(self.post(content_type='text/plain'), 422, 'JSON_REQUIRED')

    def test_missing_or_stale_billing_header_is_rejected(self):
        for header in (False, str(uuid.uuid4())):
            with self.subTest(header=header):
                self.assert_error(self.post(header=header), 409, 'ACCOUNT_CHANGED')

    def test_cross_account_order_is_hidden_before_channel_unavailable(self):
        _, other = fixture_account('other-payment-entry')
        Membership.objects.create(user=self.user, account=other, role='owner')
        session = self.client.session
        session['active_account_id'] = str(other.pk)
        session.save()
        self.assert_error(self.post(header=str(other.pk)), 404, 'NOT_FOUND')

    def test_nonfinancial_and_inactive_members_are_denied(self):
        for fields in ({'role': 'member'}, {'role': 'owner', 'is_active': False}):
            with self.subTest(fields=fields):
                Membership.objects.filter(pk=self.member.pk).update(**fields)
                self.assert_error(self.post(), 403, 'FORBIDDEN')

    def test_billing_admin_can_reach_unavailable_channel(self):
        Membership.objects.filter(pk=self.member.pk).update(role='billing_admin')
        self.assert_error(self.post(), 503, 'CHANNEL_UNAVAILABLE')

    def test_missing_and_malformed_order_ids_are_hidden(self):
        for order_id in (str(uuid.uuid4()), 'not-a-uuid', str(self.order.pk)+'/extra'):
            with self.subTest(order_id=order_id):
                self.assert_error(self.post(url=f'/api/billing/orders/{order_id}/pay'), 404, 'NOT_FOUND')

    def test_revoked_session_cannot_dispatch(self):
        AccountSession.objects.create(user=self.user, session_key=self.session_key,
                                     client='Fixture', revoked_at=timezone.now())
        self.assert_error(self.post(), 401, 'AUTH_REQUIRED')

    def test_cached_inflight_session_cannot_follow_account_switch(self):
        request = RequestFactory().post(self.url, '{}', content_type='application/json',
            HTTP_X_BILLING_ACCOUNT=str(self.account.pk))
        request.user, request.request_id = self.user, uuid.uuid4()
        request.session = SessionStore(session_key=self.session_key)
        request.session['active_account_id']  # Cache the old selection before the switch.
        _, other = fixture_account('switched-payment-entry')
        session = self.client.session
        session['active_account_id'] = str(other.pk)
        session.save()
        before = self.commerce_state()
        response = api(request, self.url.removeprefix('/api/'))
        self.assert_error(response, 409, 'ACCOUNT_CHANGED')
        self.assertEqual(self.commerce_state(), before)

    def test_csrf_required_and_valid_csrf_reaches_payment_handler(self):
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.user)
        session = secure.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        self.assert_error(self.post(client=secure), 403, 'CSRF_FAILED')
        token = secure.get('/api/auth/session').json()['csrf_token']
        before = self.commerce_state()
        response = secure.post(self.url, '{}', content_type='application/json',
            HTTP_X_BILLING_ACCOUNT=str(self.account.pk), HTTP_X_CSRFTOKEN=token)
        self.assert_error(response, 503, 'CHANNEL_UNAVAILABLE')
        self.assertEqual(self.commerce_state(), before)

    def test_payment_entry_is_post_only_and_order_detail_remains_readable(self):
        response = self.client.get(self.url)
        self.assert_error(response, 405, 'METHOD_NOT_ALLOWED')
        self.assertEqual(response['Allow'], 'POST')
        response = self.client.get(self.url.removesuffix('/pay'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['order']['id'], str(self.order.pk))

    def test_existing_unknown_attempt_is_not_retried_or_rewritten(self):
        PaymentAttempt.objects.create(account=self.account, order=self.order,
            intent_key='existing-payment-fixture', expires_at=timezone.now()+timedelta(minutes=5),
            state='unknown', needs_reconciliation=True)
        self.assert_error(self.post(), 503, 'CHANNEL_UNAVAILABLE')


class AlipayOfflineProtocolTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_pem = cls.private.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode('ascii')
        cls.public_pem = cls.private.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)

    def setUp(self):
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            guard = patch(target, side_effect=AssertionError('Offline payment must not use network'))
            guard.start()
            self.addCleanup(guard.stop)
        before = PaymentEntryTests.commerce_state(self)
        self.addCleanup(lambda: self.assertEqual(PaymentEntryTests.commerce_state(self), before))

    def signed_notification(self, *, additions=None, algorithm=None, private=None, ignored=()):
        fields = {'app_id': '2026000000000001', 'out_trade_no': 'order_1',
            'trade_no': '202610160000001', 'trade_status': 'TRADE_SUCCESS',
            'total_amount': '7.31', 'charset': 'utf-8',
            'subject': 'Sage + 中文 & 100% / %26', 'unknown_extension': 'preserve_me',
            'empty_extension': '', '': 'ignored', 'sign_type': 'RSA2'}
        fields.update(additions or {})
        fields = {key:value for key,value in fields.items() if value is not None}
        # Hand-constructed canonical protocol string, independent of production.
        content = '&'.join(f'{key}={fields[key]}' for key in sorted(fields)
                           if key and fields[key] and key not in ('sign', 'sign_type')+tuple(ignored))
        fields['sign'] = base64.b64encode((private or self.private).sign(content.encode('utf-8'),
            padding.PKCS1v15(), algorithm or hashes.SHA256())).decode('ascii')
        return urlencode(fields).encode('ascii')

    def test_integer_minor_amount_has_exact_decimal_yuan(self):
        for amount, expected in ((1, '0.01'), (10, '0.10'), (100, '1.00'),
                                 (731, '7.31'), (10000000000, '100000000.00')):
            with self.subTest(amount=amount):
                self.assertEqual(payments.alipay_amount(amount), expected)

    def test_invalid_minor_amount_cannot_build_money(self):
        for amount in (True, False, 7.31, '731', None, 0, -1, 10000000001):
            with self.subTest(amount=amount):
                with self.assertRaisesRegex(ValueError, '^INVALID_AMOUNT$'):
                    payments.alipay_amount(amount)

    def test_official_requests_use_stable_order_and_refund_references(self):
        from alipay.aop.api.request.AlipayTradePagePayRequest import AlipayTradePagePayRequest
        from alipay.aop.api.request.AlipayTradeQueryRequest import AlipayTradeQueryRequest
        from alipay.aop.api.request.AlipayTradeCloseRequest import AlipayTradeCloseRequest
        from alipay.aop.api.request.AlipayTradeRefundRequest import AlipayTradeRefundRequest
        from alipay.aop.api.request.AlipayTradeFastpayRefundQueryRequest import AlipayTradeFastpayRefundQueryRequest
        cases = (
            ('page', AlipayTradePagePayRequest, 'alipay.trade.page.pay', {'amount_minor':731},
             {'out_trade_no':'order_1', 'total_amount':'7.31', 'subject':'Sage order order_1',
              'product_code':'FAST_INSTANT_TRADE_PAY'}),
            ('query', AlipayTradeQueryRequest, 'alipay.trade.query', {}, {'out_trade_no':'order_1'}),
            ('close', AlipayTradeCloseRequest, 'alipay.trade.close', {}, {'out_trade_no':'order_1'}),
            ('refund', AlipayTradeRefundRequest, 'alipay.trade.refund',
             {'amount_minor':1, 'refund_reference':'refund_1'},
             {'out_trade_no':'order_1', 'refund_amount':'0.01', 'out_request_no':'refund_1'}),
            ('refund_query', AlipayTradeFastpayRefundQueryRequest, 'alipay.trade.fastpay.refund.query',
             {'refund_reference':'refund_1'}, {'out_trade_no':'order_1', 'out_request_no':'refund_1'}))
        for operation, expected_type, method, arguments, expected in cases:
            with self.subTest(operation=operation):
                first = payments.build_alipay_request(operation, 'order_1', **arguments)
                second = payments.build_alipay_request(operation, 'order_1', **arguments)
                self.assertIsInstance(first, expected_type)
                self.assertEqual(first.get_params()['method'], method)
                self.assertEqual(json.loads(first.get_params()['biz_content']), expected)
                self.assertEqual(first.get_params(), second.get_params())
                self.assertNotIn('return_url', first.get_params())
                self.assertNotIn('notify_url', first.get_params())

    def test_uuid_reference_keeps_canonical_value(self):
        reference = uuid.UUID('00000000-0000-0000-0000-000000000001')
        request = payments.build_alipay_request('query', reference)
        self.assertEqual(json.loads(request.get_params()['biz_content']),
                         {'out_trade_no':'00000000-0000-0000-0000-000000000001'})

    def test_references_and_operation_arguments_are_strict(self):
        for reference in ('', 'bad reference', 'a'*65, '中文', 'x&amount=1', None, 12):
            with self.subTest(reference=reference):
                with self.assertRaisesRegex(ValueError, '^INVALID_REFERENCE$'):
                    payments.build_alipay_request('query', reference)
        for operation, arguments in (
                ('unknown', {}), ('page', {}), ('refund', {'amount_minor':1}),
                ('refund_query', {}), ('query', {'amount_minor':1}),
                ('close', {'refund_reference':'refund_1'}),
                ('refund_query', {'amount_minor':1,'refund_reference':'refund_1'})):
            with self.subTest(operation=operation, arguments=arguments):
                with self.assertRaises(ValueError):
                    payments.build_alipay_request(operation, 'order_1', **arguments)
        with self.assertRaisesRegex(ValueError, '^INVALID_REFERENCE$'):
            payments.build_alipay_request('refund', 'order_1', amount_minor=1,
                                         refund_reference='bad&refund')

    @override_settings(ENVIRONMENT='test')
    def test_sandbox_page_url_is_signed_by_sdk_without_transport(self):
        url = payments.alipay_sandbox_page_url('order_1', 731,
            app_id='2026000000000001', private_key=self.private_pem)
        parsed = urlsplit(url)
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path),
                         ('https','openapi.alipaydev.com','/gateway.do'))
        params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        self.assertEqual(params['app_id'], '2026000000000001')
        self.assertEqual(params['sign_type'], 'RSA2')
        self.assertEqual(params['method'], 'alipay.trade.page.pay')
        self.assertEqual(json.loads(params['biz_content'])['total_amount'], '7.31')
        self.assertNotIn('return_url', params)
        self.assertNotIn('notify_url', params)
        signature = base64.b64decode(params.pop('sign'), validate=True)
        content = '&'.join(f'{key}={params[key]}' for key in sorted(params))
        self.private.public_key().verify(signature, content.encode('utf-8'),
                                         padding.PKCS1v15(), hashes.SHA256())

    def test_sandbox_signing_is_closed_outside_isolated_test_environment(self):
        for environment in ('production', 'development'):
            with self.subTest(environment=environment), override_settings(ENVIRONMENT=environment):
                with self.assertRaisesRegex(ValueError, '^CHANNEL_UNAVAILABLE$'):
                    payments.alipay_sandbox_page_url('order_1', 731,
                        app_id='2026000000000001', private_key=self.private_pem)

    @override_settings(ENVIRONMENT='test')
    def test_sandbox_signing_failure_exposes_no_key_material(self):
        marker = 'PRIVATE_MATERIAL_MUST_NOT_ESCAPE'
        with self.assertRaisesRegex(ValueError, '^ALIPAY_SIGNING_FAILED$') as caught:
            payments.alipay_sandbox_page_url('order_1', 731,
                app_id='2026000000000001', private_key=marker)
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)

    @override_settings(ENVIRONMENT='test')
    def test_sandbox_signing_rejects_missing_invalid_or_oversized_app_id(self):
        for app_id in (None, '', 123, 'not-numeric', '中', '1'*65):
            with self.subTest(app_id_type=type(app_id).__name__, length=len(str(app_id))):
                with self.assertRaisesRegex(ValueError, '^ALIPAY_SIGNING_FAILED$'):
                    payments.alipay_sandbox_page_url('order_1', 731,
                        app_id=app_id, private_key=self.private_pem)

    @override_settings(ENVIRONMENT='test')
    def test_sandbox_signing_rejects_weak_nonrsa_and_nonpkcs1_private_keys(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        nonrsa = ec.generate_private_key(ec.SECP256R1())
        keys = [key.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,
                    serialization.NoEncryption()).decode('ascii') for key in (weak, nonrsa)]
        keys.append(self.private.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode('ascii'))
        for key in keys:
            with self.subTest(size=len(key)):
                with self.assertRaisesRegex(ValueError, '^ALIPAY_SIGNING_FAILED$'):
                    payments.alipay_sandbox_page_url('order_1', 731,
                        app_id='2026000000000001', private_key=key)

    def test_notification_decodes_once_keeps_unknown_fields_and_only_verifies_signature(self):
        fields = payments.verify_alipay_notification(self.signed_notification(), self.public_pem)
        self.assertEqual(fields['subject'], 'Sage + 中文 & 100% / %26')
        self.assertEqual(fields['unknown_extension'], 'preserve_me')
        self.assertNotIn('empty_extension', fields)
        self.assertNotIn('', fields)
        self.assertNotIn('sign', fields)
        self.assertNotIn('sign_type', fields)
        self.assertNotIn('paid', fields)
        self.assertNotIn('currency', fields)  # No undocumented required callback field.

    def test_notification_unknown_field_tamper_breaks_signature(self):
        body = self.signed_notification().replace(b'preserve_me', b'forged_value')
        with self.assertRaisesRegex(ValueError, '^ALIPAY_NOTIFICATION_INVALID$'):
            payments.verify_alipay_notification(body, self.public_pem)

    def test_notification_missing_charset_keeps_strict_utf8_signature_semantics(self):
        fields = payments.verify_alipay_notification(
            self.signed_notification(additions={'charset':None}), self.public_pem)
        self.assertNotIn('charset', fields)
        self.assertEqual(fields['subject'], 'Sage + 中文 & 100% / %26')

    def test_notification_ignores_java_whitespace_without_trimming_signed_values(self):
        ignored = ('empty_extension', ' \t', 'unicode_whitespace')
        body = self.signed_notification(additions={'empty_extension':' \t\r\n',
            ' \t':'ignored', 'unicode_whitespace':'\u1680\u2000\u2028\u3000',
            'unknown_extension':' a ', 'nonbreaking_space':'\u00a0\u2007\u202f'}, ignored=ignored)
        try:
            fields = payments.verify_alipay_notification(body, self.public_pem)
        except ValueError:
            self.fail('A correctly signed notification using Java whitespace semantics must verify')
        for key in ignored:
            self.assertNotIn(key, fields)
        self.assertEqual(fields['unknown_extension'], ' a ')
        self.assertEqual(fields['nonbreaking_space'], '\u00a0\u2007\u202f')
        with self.assertRaisesRegex(ValueError, '^ALIPAY_NOTIFICATION_INVALID$'):
            payments.verify_alipay_notification(body.replace(b'+a+', b'a'), self.public_pem)

    def test_offline_signing_and_verification_emit_no_logs(self):
        with self.assertNoLogs(level='DEBUG'), override_settings(ENVIRONMENT='test'):
            payments.alipay_sandbox_page_url('order_1', 731,
                app_id='2026000000000001', private_key=self.private_pem)
            payments.verify_alipay_notification(self.signed_notification(), self.public_pem)

    def test_notification_duplicates_bad_encoding_size_and_missing_sign_are_rejected(self):
        valid = self.signed_notification()
        bodies = (valid+b'&app_id=other', valid+b'&%61pp_id=other', valid+b'&sign=bad',
                  valid+b'&x=%GG', valid+b'&x=%', valid+b'&x=%FF', valid+b'&x=\xff',
                  b'x='+b'a'*65536, b'&'.join([b'x=1']*129), b'app_id=1', b'', 'not bytes')
        for body in bodies:
            with self.subTest(case=type(body).__name__, size=len(body)):
                with self.assertRaisesRegex(ValueError, '^ALIPAY_NOTIFICATION_INVALID$') as caught:
                    payments.verify_alipay_notification(body, self.public_pem)
                self.assertIsNone(caught.exception.__cause__)
                self.assertTrue(caught.exception.__suppress_context__)

    def test_notification_non_rsa2_wrong_charset_and_sha1_are_rejected(self):
        for body in (self.signed_notification(additions={'sign_type':'RSA'}),
                     self.signed_notification(additions={'charset':'gbk'}),
                     self.signed_notification(algorithm=hashes.SHA1())):
            with self.subTest(size=len(body)):
                with self.assertRaisesRegex(ValueError, '^ALIPAY_NOTIFICATION_INVALID$'):
                    payments.verify_alipay_notification(body, self.public_pem)

    def test_notification_wrong_weak_nonrsa_and_nonspki_keys_are_rejected(self):
        wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        from cryptography.hazmat.primitives.asymmetric import ec
        nonrsa = ec.generate_private_key(ec.SECP256R1())
        keys = [key.public_key().public_bytes(serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo) for key in (wrong, weak, nonrsa)]
        keys.append(self.private.public_key().public_bytes(serialization.Encoding.PEM,
                    serialization.PublicFormat.PKCS1))
        keys.append(b'INVALID_PUBLIC_MATERIAL')
        for key in keys:
            with self.subTest(size=len(key)):
                with self.assertRaisesRegex(ValueError, '^ALIPAY_NOTIFICATION_INVALID$'):
                    payments.verify_alipay_notification(self.signed_notification(), key)
        with self.assertRaisesRegex(ValueError, '^ALIPAY_NOTIFICATION_INVALID$'):
            payments.verify_alipay_notification(self.signed_notification(private=weak), keys[1])

    def test_notification_unsupported_spki_algorithm_has_fixed_error(self):
        der = self.private.public_key().public_bytes(serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        rsa_oid = bytes.fromhex('06092a864886f70d010101')
        self.assertIn(rsa_oid, der)
        unsupported = der.replace(rsa_oid, bytes.fromhex('06092a864886f70d010163'), 1)
        public_key = (b'-----BEGIN PUBLIC KEY-----\n' + base64.encodebytes(unsupported)
                      + b'-----END PUBLIC KEY-----\n')
        try:
            payments.verify_alipay_notification(self.signed_notification(), public_key)
        except ValueError as exc:
            self.assertEqual(str(exc), 'ALIPAY_NOTIFICATION_INVALID')
            self.assertIsNone(exc.__cause__)
            self.assertTrue(exc.__suppress_context__)
        except Exception as exc:
            self.fail('Unsupported key algorithm must use the fixed error, not '+type(exc).__name__)
        else:
            self.fail('An unsupported public-key algorithm must be rejected')

    def test_official_sdk_execute_checks_synthetic_signed_response(self):
        from alipay.aop.api.AlipayClientConfig import AlipayClientConfig
        from alipay.aop.api.DefaultAlipayClient import DefaultAlipayClient
        from alipay.aop.api.exception.Exception import ResponseException
        config = AlipayClientConfig(sandbox_debug=True)
        config.app_id, config.app_private_key = '2026000000000001', self.private_pem
        config.alipay_public_key = self.public_pem.decode('ascii')
        client = DefaultAlipayClient(config, logger=None)
        request = payments.build_alipay_request('query', 'order_1')
        content = b'{"code":"10000","out_trade_no":"order_1","trade_status":"WAIT_BUYER_PAY"}'
        signature = base64.b64encode(self.private.sign(content, padding.PKCS1v15(), hashes.SHA256()))
        response = b'{"alipay_trade_query_response":'+content+b',"sign":"'+signature+b'"}'
        with patch('alipay.aop.api.DefaultAlipayClient.do_post', return_value=response):
            self.assertEqual(json.loads(client.execute(request))['trade_status'], 'WAIT_BUYER_PAY')
        for invalid in (response.replace(b'WAIT_BUYER_PAY', b'TRADE_SUCCESS'),
                        b'{"alipay_trade_query_response":'+content+b'}'):
            with patch('alipay.aop.api.DefaultAlipayClient.do_post', return_value=invalid):
                with self.assertRaises(ResponseException):
                    client.execute(request)
        # SDK rsa.verify accepts SHA1 despite config RSA2. This is an open gap
        # for future raw-response verification, not a strict RSA2 success claim.
        sha1 = base64.b64encode(self.private.sign(content, padding.PKCS1v15(), hashes.SHA1()))
        weak = b'{"alipay_trade_query_response":'+content+b',"sign":"'+sha1+b'"}'
        with patch('alipay.aop.api.DefaultAlipayClient.do_post', return_value=weak):
            self.assertEqual(json.loads(client.execute(request))['trade_status'], 'WAIT_BUYER_PAY')

    def signed_response(self, content, *, method='alipay.trade.query', algorithm=None, private=None):
        signature = base64.b64encode((private or self.private).sign(content,
            padding.PKCS1v15(), algorithm or hashes.SHA256()))
        return (b'{"'+method.replace('.', '_').encode('ascii')+b'_response":'+content+
                b',"sign":"'+signature+b'"}')

    def test_raw_response_verifies_original_json_whitespace_escapes_and_nested_fields(self):
        # Reserializing JSON, extracting a decoy key, or trimming signed content breaks this.
        content = (b'{ "code" : "10000", "out_trade_no":"order_1", '
            b'"extension":{"text":"} \\" sign", "list":[true,null,3]}, '
            b'"subject":"\\u4e2d\\u6587", "trade_status":"WAIT_BUYER_PAY" }')
        response = self.signed_response(content)
        fields = payments.verify_alipay_response(response, self.public_pem, method='alipay.trade.query')
        self.assertEqual(fields['subject'], '中文')
        self.assertEqual(fields['extension'], {'text': '} " sign', 'list': [True,None,3]})
        self.assertEqual(fields['trade_status'], 'WAIT_BUYER_PAY')
        self.assertNotIn('paid', fields)
        with self.assertRaisesRegex(ValueError, '^ALIPAY_RESPONSE_INVALID$'):
            payments.verify_alipay_response(response.replace(b'10000', b'20000'),
                                            self.public_pem, method='alipay.trade.query')

    def test_raw_response_rejects_sha1_unsigned_unknown_method_and_wrong_key(self):
        content = b'{"code":"10000","trade_status":"TRADE_SUCCESS"}'
        valid = self.signed_response(content)
        wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cases = [(self.signed_response(content, algorithm=hashes.SHA1()), self.public_pem, 'alipay.trade.query'),
            (b'{"alipay_trade_query_response":'+content+b'}', self.public_pem, 'alipay.trade.query'),
            (valid, self.public_pem, 'alipay.trade.close'),
            (valid, self.public_pem, 'unknown.method'),
            (self.signed_response(content, private=wrong), self.public_pem, 'alipay.trade.query'),
            (valid, b'INVALID_PUBLIC_MATERIAL', 'alipay.trade.query')]
        for response, key, method in cases:
            with self.subTest(method=method, size=len(response)):
                with self.assertRaisesRegex(ValueError, '^ALIPAY_RESPONSE_INVALID$') as caught:
                    payments.verify_alipay_response(response, key, method=method)
                self.assertIsNone(caught.exception.__cause__)
                self.assertTrue(caught.exception.__suppress_context__)

    def test_raw_response_rejects_duplicate_keys_at_every_level_and_ambiguous_envelopes(self):
        content = b'{"code":"10000","trade_status":"TRADE_SUCCESS"}'
        valid = self.signed_response(content)
        invalid = [valid[:-1]+b',"sign":"duplicate"}',
            valid[:-1]+b',"alipay_trade_query_response":{}}',
            valid[:-1]+b',"unknown_unsigned_key":"forged"}',
            valid[:-1]+b',"sign_type":"RSA"}',
            self.signed_response(b'{"code":"10000","code":"40004"}'),
            self.signed_response(b'{"code":"10000","nested":{"a":1,"a":2}}'),
            self.signed_response(b'{"code":"10000","nested":{"a":1,"\\u0061":2}}'),
            self.signed_response(b'{"code":NaN}'), self.signed_response(b'[]'),
            valid+b'{}', valid+b'\xff', b' '*65537, b'', b'{', 'not bytes']
        for response in invalid:
            with self.subTest(kind=type(response).__name__, size=len(response)):
                with self.assertRaisesRegex(ValueError, '^ALIPAY_RESPONSE_INVALID$'):
                    payments.verify_alipay_response(response, self.public_pem, method='alipay.trade.query')

    def test_raw_response_other_supported_methods_verify_without_claiming_payment(self):
        for method in ('alipay.trade.close','alipay.trade.refund','alipay.trade.fastpay.refund.query'):
            with self.subTest(method=method), self.assertNoLogs(level='DEBUG'):
                response = self.signed_response(b'{"code":"10000"}', method=method)
                self.assertEqual(payments.verify_alipay_response(response, self.public_pem,
                                                                method=method), {'code':'10000'})


class AlipayVerifiedFactTests(TransactionTestCase):
    signed_notification = AlipayOfflineProtocolTests.signed_notification

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public_pem = cls.private.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)

    def setUp(self):
        from backend.commerce.billing import create_quote, create_order, prepare_payment_attempt, claim_payment_dispatch
        self.configuration = {'enabled':True, 'currency':'CNY', 'merchant_entity':'fixture-shenzhen',
            'regions':['CN'], 'capabilities':['one_time'], 'app_id':'2026000000000001',
            'seller_id':'2088000000000001', 'public_key':self.public_pem.decode('ascii')}
        self.configuration_override = override_settings(BILLING_SALES_ENABLED=True,
            BILLING_APPROVED_CHANNELS={'alipay':self.configuration})
        self.configuration_override.enable()
        self.addCleanup(self.configuration_override.disable)
        self.now = datetime(2026,10,16,0,2,tzinfo=datetime_timezone.utc)
        clock = patch('django.utils.timezone.now', return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            guard = patch(target, side_effect=AssertionError('Verified payment tests must not use network'))
            guard.start()
            self.addCleanup(guard.stop)
        self.user, self.account = fixture_account('verified-alipay-fixture')
        self.product = fixture_product()
        quote = create_quote(self.account,self.user,self.product.pk,1,self.now+timedelta(minutes=5),
            channel='alipay',buyer_region='CN')
        order = create_order(self.account,self.user,{'quote_id':quote['id'],
            'agreement_acceptance':'fixture-1'},'verified-alipay-order')
        self.order = Order.objects.get(pk=order['id'])
        attempt = prepare_payment_attempt(self.account,self.user,self.order.pk,'verified-alipay-intent',
                                         expires_at=self.now+timedelta(minutes=3))
        with patch('django.utils.timezone.now',return_value=self.now-timedelta(minutes=2)):
            self.assertTrue(claim_payment_dispatch(self.account,attempt['id'])['dispatch'])
        self.attempt = PaymentAttempt.objects.get(pk=attempt['id'])

    def notification(self, **changes):
        fields = {'out_trade_no':str(self.attempt.pk), 'seller_id':'2088000000000001',
            'gmt_payment':'2026-10-16 08:01:00', 'notify_type':'trade_status_sync'}
        return self.signed_notification(additions={**fields,**changes})

    def fact(self, body=None, *, account=None, attempt_id=None):
        return payments.alipay_notification_fact(account or self.account,
            attempt_id or self.attempt.pk, self.notification() if body is None else body)

    def test_signed_notification_binds_approved_identity_attempt_money_and_payment_time(self):
        import hashlib
        body = self.notification()
        before = PaymentEntryTests.commerce_state(self)
        with self.assertNoLogs(level='DEBUG'):
            fact = self.fact(body)
        self.assertEqual(fact, {'channel':'alipay','merchant_reference':'fixture-shenzhen',
            'transaction_reference':'202610160000001','amount_minor':731,'currency':'CNY',
            'paid_at':datetime(2026,10,16,0,1,tzinfo=datetime_timezone.utc),
            'evidence_hash':hashlib.sha256(body).hexdigest()})
        self.assertEqual(PaymentEntryTests.commerce_state(self),before)
        self.assertNotIn('subject',fact)
        self.assertNotIn('seller_id',fact)

    def test_signed_identity_reference_amount_and_nonpaid_status_mismatches_do_not_make_facts(self):
        before = PaymentEntryTests.commerce_state(self)
        for field,value in (('app_id','2026000000000002'),('seller_id','2088000000000002'),
                ('out_trade_no',str(self.order.pk)),('trade_no',''),('trade_no','bad&transaction'),
                ('total_amount','7.30'),('total_amount','7.310'),('total_amount','7e0'),
                ('total_amount','+7.31'),('total_amount',' 7.31'),('total_amount','NaN'),
                ('trade_status','WAIT_BUYER_PAY'),('trade_status','TRADE_CLOSED'),
                ('notify_type','other'),('currency','USD'),('trans_currency','USD')):
            with self.subTest(field=field,value=value):
                with self.assertRaisesRegex(ValueError,'^ALIPAY_PAYMENT_INVALID$'):
                    self.fact(self.notification(**{field:value}))
        self.assertEqual(PaymentEntryTests.commerce_state(self),before)

    def test_missing_identity_money_reference_or_payment_time_cannot_make_fact(self):
        for field in ('app_id','seller_id','out_trade_no','trade_no','total_amount',
                      'trade_status','gmt_payment','notify_type'):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError,'^ALIPAY_PAYMENT_INVALID$'):
                    self.fact(self.notification(**{field:None}))

    def test_invalid_calendar_future_or_predispatch_payment_time_is_rejected(self):
        for value in ('2026-02-30 08:01:00','2026-10-16T08:01:00','2026-10-16 08:01:00+08:00',
                      '2026-10-16 8:01:00','2026-10-16 08:03:00','2026-10-16 07:59:59'):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError,'^ALIPAY_PAYMENT_INVALID$'):
                    self.fact(self.notification(gmt_payment=value))

    def test_unapproved_identity_or_key_cannot_verify_money(self):
        wrong = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        wrong_key = wrong.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode('ascii')
        for configuration in ({}, {**self.configuration,'enabled':False},
                {**self.configuration,'currency':'USD'}, {**self.configuration,'app_id':'other'},
                {**self.configuration,'seller_id':'other'}, {**self.configuration,'public_key':wrong_key},
                {**self.configuration,'merchant_entity':'other'}):
            with self.subTest(keys=list(configuration)), override_settings(
                    BILLING_APPROVED_CHANNELS={'alipay':configuration}):
                with self.assertRaisesRegex(ValueError,'^ALIPAY_PAYMENT_INVALID$'):
                    self.fact()

    def test_cross_account_missing_attempt_and_undispatched_attempt_are_rejected(self):
        _,other = fixture_account('other-verified-alipay')
        for account,attempt_id in ((other,self.attempt.pk),(self.account,uuid.uuid4()),
                                   (self.account,'invalid-uuid')):
            with self.subTest(account=str(account.pk),attempt=str(attempt_id)):
                with self.assertRaisesRegex(ValueError,'^ALIPAY_PAYMENT_INVALID$'):
                    self.fact(account=account,attempt_id=attempt_id)
        PaymentAttempt.objects.filter(pk=self.attempt.pk).update(dispatched_at=None)
        with self.assertRaisesRegex(ValueError,'^ALIPAY_PAYMENT_INVALID$'):
            self.fact()

    def test_nonidentifier_merchant_configuration_cannot_produce_a_ledger_fact(self):
        invalid_merchant = ['not-a-ledger-identifier']
        snapshot = {**self.order.snapshot,'merchant_entity':invalid_merchant}
        Order.objects.filter(pk=self.order.pk).update(snapshot=snapshot)
        Quote.objects.filter(pk=self.order.quote_id).update(snapshot=snapshot)
        with override_settings(BILLING_APPROVED_CHANNELS={'alipay':{
                **self.configuration,'merchant_entity':invalid_merchant}}):
            with self.assertRaisesRegex(ValueError,'^ALIPAY_PAYMENT_INVALID$'):
                self.fact()

    def test_damaged_snapshot_or_cross_account_order_cannot_produce_a_fact(self):
        for changes in ({'amount_minor':730},{'currency':'USD'},
                        {'snapshot':{**self.order.snapshot,'channel':'wechat'}},
                        {'snapshot':{**self.order.snapshot,'amount_minor':True}}):
            with self.subTest(fields=list(changes)):
                Order.objects.filter(pk=self.order.pk).update(**changes)
                with self.assertRaisesRegex(ValueError,'^ALIPAY_PAYMENT_INVALID$'):
                    self.fact()
                Order.objects.filter(pk=self.order.pk).update(amount_minor=731,currency='CNY',
                                                            snapshot=self.order.snapshot)
        _,other = fixture_account('cross-owned-alipay-order')
        Order.objects.filter(pk=self.order.pk).update(account=other)
        with self.assertRaisesRegex(ValueError,'^ALIPAY_PAYMENT_INVALID$'):
            self.fact()

    def test_untrusted_channel_and_return_values_never_choose_the_ledger_identity(self):
        fact = self.fact(self.notification(channel='wechat',paid='true',
            payment_url='https://example.invalid/?paid=true'))
        self.assertEqual((fact['channel'],fact['merchant_reference']),('alipay','fixture-shenzhen'))
        self.assertEqual(set(fact),{'channel','merchant_reference','transaction_reference',
                                   'amount_minor','currency','paid_at','evidence_hash'})

    def test_query_response_cannot_enter_notification_fact_boundary(self):
        response = AlipayOfflineProtocolTests.signed_response(self,
            b'{"code":"10000","trade_status":"TRADE_SUCCESS","total_amount":"7.31"}')
        with self.assertRaisesRegex(ValueError,'^ALIPAY_PAYMENT_INVALID$'):
            self.fact(response)

    def test_tampered_signed_body_cannot_map_even_when_identity_is_expected(self):
        body = self.notification().replace(b'7.31',b'7.30')
        with self.assertRaisesRegex(ValueError,'^ALIPAY_PAYMENT_INVALID$') as caught:
            self.fact(body)
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertFalse(PaymentFact.objects.exists())

    def test_duplicate_notifications_and_finished_status_keep_one_receipt_and_one_delivery(self):
        from backend.commerce.billing import record_confirmed_payment
        first = record_confirmed_payment(self.account,self.order.pk,**self.fact())
        repeat = record_confirmed_payment(self.account,self.order.pk,
            **self.fact(self.notification(trade_status='TRADE_FINISHED',currency='CNY')))
        self.assertEqual(first,repeat)
        self.assertEqual(first['disposition'],'accepted')
        self.assertEqual(PaymentFact.objects.count(),1)
        self.assertEqual(CreditBatch.objects.count(),1)
        self.assertEqual(CreditEntry.objects.filter(kind='grant').count(),1)
        self.assertEqual(BillingNotification.objects.filter(type='payment_received').count(),1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status,'fulfilled')

    def test_late_verified_payment_is_preserved_for_review_without_delivery(self):
        from backend.commerce.billing import record_confirmed_payment
        Order.objects.filter(pk=self.order.pk).update(status='closed')
        PaymentAttempt.objects.filter(pk=self.attempt.pk).update(state='expired')
        result = record_confirmed_payment(self.account,self.order.pk,**self.fact())
        self.assertEqual(result['disposition'],'late_payment_review')
        self.assertEqual(PaymentFact.objects.count(),1)
        self.assertFalse(CreditBatch.objects.exists())

    def test_delivery_failure_keeps_verified_money_and_replay_repairs_only_once(self):
        from backend.commerce.billing import record_confirmed_payment
        with patch('backend.commerce.billing.grant_credits',side_effect=ValueError('LEDGER_INCONSISTENT')):
            with self.assertRaisesRegex(ValueError,'^LEDGER_INCONSISTENT$'):
                record_confirmed_payment(self.account,self.order.pk,**self.fact())
        self.assertEqual(PaymentFact.objects.count(),1)
        self.assertFalse(CreditBatch.objects.exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status,'paid_pending_entitlement')
        for _ in range(2):
            record_confirmed_payment(self.account,self.order.pk,**self.fact())
        self.assertEqual(PaymentFact.objects.count(),1)
        self.assertEqual(CreditBatch.objects.count(),1)
        self.assertEqual(CreditEntry.objects.filter(kind='grant').count(),1)
