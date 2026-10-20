"""Offline APIv3 contracts with real RSA/AES; no network or payment writes."""
import base64
import gzip
import hashlib
import io
import json
import os
import re
import uuid
from xml.etree import ElementTree
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from urllib3._collections import HTTPHeaderDict
from urllib3.exceptions import ProtocolError
from urllib3.response import HTTPResponse

from backend import wechat


class WechatQRTests(TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(wechat, 'wechat_qr_image', None)),
            'The local QR generation API is missing')
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            guard = patch(target, side_effect=AssertionError('Offline QR attempted network'))
            guard.start()
            self.addCleanup(guard.stop)

    def image(self, value):
        with self.assertNoLogs():
            return base64.b64decode(wechat.wechat_qr_image(value), validate=True)

    def test_native_payload_generates_svg_modules_with_white_quiet_zone(self):
        document = self.image('weixin://wxpay/bizpayurl?fixture=local-order-731')
        root = ElementTree.fromstring(document)
        self.assertEqual(root.tag, '{http://www.w3.org/2000/svg}svg')
        elements = list(root.iter())
        self.assertTrue(any(node.tag.endswith('}path') and node.get('d') for node in elements))
        self.assertTrue(any(node.tag.endswith('}rect') and node.get('fill') == 'white'
            for node in elements))
        self.assertTrue(any(node.get('fill') in ('black', '#000000') for node in elements))
        view_box = [float(value) for value in root.get('viewBox').split()]
        self.assertEqual(view_box[:2], [0, 0])
        self.assertEqual(view_box[2], view_box[3])
        coordinates = [float(value) for node in elements if node.tag.endswith('}path')
            for value in re.findall(r'[-+]?(?:\d*\.)?\d+', node.get('d', ''))]
        self.assertGreaterEqual(min(coordinates), 4)
        self.assertLessEqual(max(coordinates), view_box[2]-4)
        self.assertNotIn(b'weixin:', document)
        self.assertLess(len(document), 100000)

    def test_special_ascii_characters_are_encoded_as_modules_without_svg_injection(self):
        document = self.image('weixin://wxpay/path?fixture=<script>&quote="safe"&other=\'local\'')
        root = ElementTree.fromstring(document)
        for node in root.iter():
            self.assertIn(node.tag.rsplit('}', 1)[-1], ('svg', 'rect', 'path'))
            self.assertFalse(node.text and node.text.strip())
            for key, value in node.attrib.items():
                self.assertFalse(key.lower().startswith('on'))
                self.assertNotIn('href', key.lower())
                self.assertNotIn('url(', value.lower())
        self.assertNotIn(b'script', document)

    def test_qr_rejects_unsafe_native_payloads_with_fixed_error(self):
        invalid = [None, 123, b'weixin://wxpay/path', '', 'https://example.invalid/pay',
            'weixin://wxpay/', 'weixin://user@wxpay/path', 'weixin://wxpay:443/path',
            'weixin://wxpay/path#fragment', 'weixin://wxpay/path#', 'weixin://other/path',
            'weixin://wxpay/path with spaces', 'weixin://wxpay/path\n',
            'weixin://wxpay/二维码', 'weixin://wxpay/path?x=%0a',
            'weixin://wxpay/path?x=%20', 'weixin://wxpay/path?x=%7f',
            'weixin://wxpay/path?x=%5c', 'weixin://wxpay/path\\other',
            'weixin://wxpay/'+('a'*2048)]
        for value in invalid:
            with self.subTest(value_type=type(value).__name__), self.assertNoLogs():
                with self.assertRaisesRegex(ValueError, '^WECHAT_QR_INVALID$'):
                    wechat.wechat_qr_image(value)

    def test_long_legal_native_payload_is_bounded_and_valid_svg(self):
        document = self.image('weixin://wxpay/'+('a'*2033))
        root = ElementTree.fromstring(document)
        self.assertEqual(root.tag, '{http://www.w3.org/2000/svg}svg')
        self.assertLess(len(document), 1000000)

    def test_qr_capacity_failure_has_fixed_error_without_raw_details(self):
        from qrcode.exceptions import DataOverflowError

        with patch('qrcode.QRCode.make', side_effect=DataOverflowError('RAW_QR_MUST_NOT_LEAK')), \
                self.assertNoLogs(), self.assertRaisesRegex(ValueError, '^WECHAT_QR_INVALID$'):
            wechat.wechat_qr_image('weixin://wxpay/path?fixture=value')

    def test_svg_dimensions_fit_the_payment_panel_for_short_and_long_payloads(self):
        for payload in ('weixin://wxpay/path?fixture=value', 'weixin://wxpay/'+('a'*2033)):
            with self.subTest(length=len(payload)):
                root = ElementTree.fromstring(self.image(payload))
                self.assertEqual((root.get('width'), root.get('height')), ('320', '320'))


class WechatProtocolTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.merchant_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.platform_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_pem = cls.merchant_key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        cls.public_pem = cls.platform_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.api_key = AESGCM.generate_key(bit_length=256)

    def setUp(self):
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            guard = patch(target, side_effect=AssertionError('Offline protocol attempted network'))
            guard.start()
            self.addCleanup(guard.stop)
        self.reference = uuid.UUID('11111111-2222-4333-8444-555555555555')
        self.config = dict(mchid='1900000001', merchant_serial='A1B2C3',
            private_key=self.private_pem, public_key_id='PUB_KEY_ID_3000000001',
            timestamp=1800000000, nonce='fixture_nonce')
        self.verify_config = dict(public_key=self.public_pem,
            public_key_id='PUB_KEY_ID_3000000001', now=1800000000)

    def build(self, operation, **kwargs):
        return wechat.build_wechat_request(operation, self.reference, **(self.config | kwargs))

    def signed(self, body, *, timestamp=1800000000, nonce='fixture_response_nonce', key=None):
        content = str(timestamp).encode()+b'\n'+nonce.encode()+b'\n'+body+b'\n'
        signature = (key or self.platform_key).sign(content, padding.PKCS1v15(), hashes.SHA256())
        return [('Wechatpay-Timestamp', str(timestamp)), ('Wechatpay-Nonce', nonce),
            ('Wechatpay-Serial', 'PUB_KEY_ID_3000000001'),
            ('Wechatpay-Signature', base64.b64encode(signature).decode())]

    def verify(self, body, headers=None, **kwargs):
        return wechat.verify_wechat_response(body, headers or self.signed(body),
            **(self.verify_config | kwargs))

    def test_native_body_and_signature_use_identical_utf8_bytes(self):
        request = self.build('native', appid='wx_fixture', amount_minor=731,
            description='Sage 月度订单', notify_origin='https://sage.example',
            time_expire='2027-01-15T09:00:00+08:00')
        expected = ('{"appid":"wx_fixture","mchid":"1900000001",'
            '"description":"Sage 月度订单","out_trade_no":"11111111222243338444555555555555",'
            '"notify_url":"https://sage.example/api/payments/webhooks/wechat",'
            '"amount":{"total":731,"currency":"CNY"},'
            '"time_expire":"2027-01-15T09:00:00+08:00"}').encode()
        self.assertEqual(request['body'], expected)
        self.assertEqual(request['url'], 'https://api.mch.weixin.qq.com/v3/pay/transactions/native')
        auth = request['headers']['Authorization']
        self.assertTrue(auth.startswith('WECHATPAY2-SHA256-RSA2048 '))
        self.assertIn('serial_no="A1B2C3"', auth)
        self.assertIn('mchid="1900000001"', auth)
        self.assertEqual(request['headers']['Wechatpay-Serial'], 'PUB_KEY_ID_3000000001')
        signature = base64.b64decode(re.search(r'signature="([^"]+)"', auth)[1], validate=True)
        self.merchant_key.public_key().verify(signature,
            b'POST\n/v3/pay/transactions/native\n1800000000\nfixture_nonce\n'+expected+b'\n',
            padding.PKCS1v15(), hashes.SHA256())

    def test_query_close_refund_and_refund_query_contracts(self):
        cases = [
            ('query', {}, 'GET', '/v3/pay/transactions/out-trade-no/11111111222243338444555555555555?mchid=1900000001', b''),
            ('close', {}, 'POST', '/v3/pay/transactions/out-trade-no/11111111222243338444555555555555/close', b'{"mchid":"1900000001"}'),
            ('refund', dict(amount_minor=731, refund_minor=123, refund_reference='refund@001'),
                'POST', '/v3/refund/domestic/refunds',
                b'{"out_trade_no":"11111111222243338444555555555555","out_refund_no":"refund@001","amount":{"refund":123,"total":731,"currency":"CNY"}}'),
            ('refund_query', dict(refund_reference='refund@001'), 'GET',
                '/v3/refund/domestic/refunds/refund%40001', b''),
        ]
        for operation, kwargs, method, path, body in cases:
            with self.subTest(operation=operation):
                request = self.build(operation, **kwargs)
                self.assertEqual((request['method'], request['url'], request['body']),
                    (method, 'https://api.mch.weixin.qq.com'+path, body))
                signature = base64.b64decode(re.search(r'signature="([^"]+)"',
                    request['headers']['Authorization'])[1], validate=True)
                self.merchant_key.public_key().verify(signature,
                    f'{method}\n{path}\n1800000000\nfixture_nonce\n'.encode()+body+b'\n',
                    padding.PKCS1v15(), hashes.SHA256())

    def test_repeated_builds_preserve_order_and_refund_identifiers(self):
        first = self.build('refund', amount_minor=731, refund_minor=123, refund_reference=self.reference)
        second = self.build('refund', amount_minor=731, refund_minor=123, refund_reference=self.reference,
            nonce='other_nonce', timestamp=1800000001)
        self.assertEqual(first['body'], second['body'])
        self.assertEqual(json.loads(first['body'])['out_refund_no'], '11111111222243338444555555555555')

    def test_reference_path_characters_are_encoded_without_changing_id(self):
        self.assertEqual(wechat.build_wechat_request('query', 'order|*_-', **self.config)['url'],
            'https://api.mch.weixin.qq.com/v3/pay/transactions/out-trade-no/order%7C%2A_-?mchid=1900000001')

    def test_invalid_request_inputs_are_rejected(self):
        native = dict(appid='wx_fixture', amount_minor=731, description='Sage order',
            notify_origin='https://sage.example', time_expire='2027-01-15T09:00:00+08:00')
        for field, values in {
            'appid': [None, ''], 'mchid': ['', '1"\r\n'],
            'merchant_serial': ['', 'PUB_KEY_ID_3000000001'],
            'public_key_id': ['', 'A1B2C3'], 'nonce': ['', 'unsafe\n'],
            'timestamp': [True, 0, '1800000000'],
            'amount_minor': [True, 0, -1, 1.5, '731', 2147483648],
            'description': ['', 'x'*128],
            'notify_origin': [None, '', 'http://sage.example', 'https://evil.example/path',
                'https://sage.example?evil=1', 'https://user@sage.example', 'https://sage.example/'],
            'time_expire': [None, '', '2027-01-15T09:00:00', '2027-02-31T09:00:00Z'],
        }.items():
            for value in values:
                with self.subTest(field=field, case=type(value).__name__), self.assertRaises(ValueError):
                    self.build('native', **(native | {field: value}))
        for reference in ('short', str(self.reference), 'a'*33, '../order', 'order@1'):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                wechat.build_wechat_request('query', reference, **self.config)
        for operation, kwargs in [('h5', {}), ('query', {'amount_minor':731}),
                ('close', {'refund_reference':'refund1'}),
                ('refund', {'amount_minor':731, 'refund_minor':732, 'refund_reference':'refund1'}),
                ('refund_query', {'refund_reference':'x'*65})]:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.build(operation, **kwargs)

    def test_weak_or_wrong_private_key_is_rejected(self):
        weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        for key in (b'invalid', self.public_pem, weak.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption())):
            with self.subTest(case=len(key)), self.assertRaises(ValueError):
                self.build('query', private_key=key)

    def test_response_verifies_exact_body_before_parsing(self):
        body = b'{ "trade_state" : "NOTPAY" }\n'
        self.assertEqual(self.verify(body), {'trade_state':'NOTPAY'})
        with self.assertRaises(ValueError):
            self.verify(b'{"trade_state":"NOTPAY"}', self.signed(body))

    def test_empty_close_response_still_requires_signature(self):
        self.assertIsNone(self.verify(b''))
        with self.assertRaises(ValueError):
            wechat.verify_wechat_response(b'', [], **self.verify_config)

    def test_signature_headers_reject_missing_duplicate_unknown_and_probe(self):
        body = b'{}'
        original = self.signed(body)
        cases = [original[:index]+original[index+1:] for index in range(4)]
        cases += [original+[('wechatpay-serial', 'PUB_KEY_ID_3000000001')]]
        for name, value in [('Wechatpay-Serial', 'PUB_KEY_ID_9999999999'),
                ('Wechatpay-Signature','WECHATPAY/SIGNTEST/fixture'),
                ('Wechatpay-Signature','!invalid!'), ('Wechatpay-Timestamp','1800000000,1800000000')]:
            cases.append([(k, value if k == name else v) for k, v in original])
        for headers in cases:
            with self.subTest(case=len(headers)), self.assertRaises(ValueError):
                self.verify(body, headers)

    def test_timestamp_window_rejects_both_boundary_directions(self):
        for difference in (-301, -300, 300, 301):
            with self.subTest(difference=difference), self.assertRaises(ValueError):
                self.verify(b'{}', self.signed(b'{}', timestamp=1800000000+difference))
        for difference in (-299, 299):
            self.assertEqual(self.verify(b'{}', self.signed(b'{}', timestamp=1800000000+difference)), {})

    def test_untrusted_key_or_weak_key_cannot_authenticate_response(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        with self.assertRaises(ValueError):
            self.verify(b'{}', self.signed(b'{}', key=other))
        weak_pem = weak.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        for key in (weak_pem, self.private_pem, b'invalid'):
            with self.subTest(case=len(key)), self.assertRaises(ValueError):
                self.verify(b'{}', public_key=key)

    def test_sha1_signature_and_pkcs1_public_key_are_rejected(self):
        body = b'{}'
        headers = self.signed(body)
        signature = self.platform_key.sign(
            b'1800000000\nfixture_response_nonce\n{}\n', padding.PKCS1v15(), hashes.SHA1())
        headers[-1] = ('Wechatpay-Signature', base64.b64encode(signature).decode())
        with self.assertRaises(ValueError):
            self.verify(body, headers)
        pkcs1 = self.platform_key.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.PKCS1)
        with self.assertRaises(ValueError):
            self.verify(body, public_key=pkcs1)

    def test_rotation_requires_explicitly_reconfigured_key_and_id(self):
        body = b'{}'
        replacement = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        replacement_pem = replacement.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        headers = self.signed(body, key=replacement)
        headers[2] = ('Wechatpay-Serial', 'PUB_KEY_ID_3000000002')
        with self.assertRaises(ValueError):
            self.verify(body, headers)
        self.assertEqual(self.verify(body, headers, public_key=replacement_pem,
            public_key_id='PUB_KEY_ID_3000000002'), {})
        with self.assertRaises(ValueError):
            self.verify(body, self.signed(body), public_key=replacement_pem,
                public_key_id='PUB_KEY_ID_3000000002')

    def test_authenticated_json_rejects_duplicates_nonfinite_and_oversize(self):
        for body in (b'{"a":1,"a":2}', b'{"nested":{"a":1,"a":2}}', b'{"a":NaN}',
                b'{"a":Infinity}', b'{"a":1e999}', b'[]', b'null', b'\xff', b'{', b' '*65537):
            with self.subTest(case=len(body)), self.assertRaises(ValueError):
                self.verify(body)

    def transaction(self):
        return dict(appid='wx_fixture', mchid='1900000001',
            out_trade_no='11111111222243338444555555555555', transaction_id='420000000000000000001',
            trade_type='NATIVE', trade_state='SUCCESS', success_time='2027-01-15T09:00:00+08:00',
            amount=dict(total=731, currency='CNY', payer_total=700, payer_currency='CNY'))

    def notification(self, transaction=None, *, aad='transaction', **changes):
        nonce = 'abcdefghijkl'
        content = json.dumps(transaction or self.transaction()).encode()
        encrypted = AESGCM(self.api_key).encrypt(nonce.encode(), content, aad.encode())
        envelope = dict(id='fixture-event', create_time='2027-01-16T09:00:00+08:00',
            event_type='TRANSACTION.SUCCESS', resource_type='encrypt-resource',
            resource=dict(algorithm='AEAD_AES_256_GCM', original_type='transaction', nonce=nonce,
                associated_data=aad, ciphertext=base64.b64encode(encrypted).decode()))
        envelope.update(changes)
        return json.dumps(envelope).encode()

    def decrypt(self, body, headers=None, **kwargs):
        return wechat.verify_wechat_notification(body, headers or self.signed(body),
            api_v3_key=kwargs.pop('api_v3_key', self.api_key), **(self.verify_config | kwargs))

    def test_notification_authenticates_and_decrypts_aes_gcm_with_empty_aad(self):
        for aad in ('transaction', ''):
            self.assertEqual(self.decrypt(self.notification(aad=aad)), self.transaction())

    def test_notification_rejects_signature_gcm_and_envelope_tampering(self):
        body = self.notification()
        with self.assertRaises(ValueError):
            self.decrypt(body+b' ', self.signed(body))
        for key in (b'x'*31, b'x'*32, 'x'*32):
            with self.subTest(case=type(key).__name__), self.assertRaises(ValueError):
                self.decrypt(body, api_v3_key=key)
        for field, value in [('algorithm','AES_GCM'), ('ciphertext','!bad!'),
                ('associated_data','changed'), ('nonce','abcdefghijkm')]:
            envelope = json.loads(body)
            envelope['resource'][field] = value
            changed = json.dumps(envelope).encode()
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.decrypt(changed)
        for kwargs in ({'event_type':'REFUND.SUCCESS'}, {'resource_type':'transaction'}, {'resource':[]}):
            with self.subTest(case=tuple(kwargs)), self.assertRaises(ValueError):
                self.decrypt(self.notification(**kwargs))

    def test_decrypted_resource_rejects_duplicate_json_fields(self):
        envelope = json.loads(self.notification())
        ciphertext = AESGCM(self.api_key).encrypt(b'abcdefghijkl', b'{"amount":1,"amount":2}',
            b'transaction')
        envelope['resource']['ciphertext'] = base64.b64encode(ciphertext).decode()
        with self.assertRaises(ValueError):
            self.decrypt(json.dumps(envelope).encode())

    def test_missing_associated_data_uses_authenticated_empty_value(self):
        envelope = json.loads(self.notification(aad=''))
        del envelope['resource']['associated_data']
        self.assertEqual(self.decrypt(json.dumps(envelope).encode()), self.transaction())

    def fact(self, body, *, notification=False, **kwargs):
        return wechat.wechat_payment_fact(body, self.signed(body),
            **(self.verify_config | dict(appid='wx_fixture', mchid='1900000001',
                reference=self.reference, amount_minor=731, merchant_reference='fixture-shenzhen',
                notification=notification, api_v3_key=self.api_key) | kwargs))

    def test_confirmed_fact_uses_order_total_and_success_time(self):
        for notification in (False, True):
            body = self.notification() if notification else json.dumps(self.transaction()).encode()
            fact = self.fact(body, notification=notification)
            self.assertEqual(fact, dict(channel='wechat', merchant_reference='fixture-shenzhen',
                transaction_reference='420000000000000000001', amount_minor=731, currency='CNY',
                paid_at=datetime(2027, 1, 15, 9, tzinfo=timezone(timedelta(hours=8))),
                evidence_hash=hashlib.sha256(body).hexdigest()))
            self.assertEqual(self.fact(body, notification=notification), fact)

    def test_authenticated_wrong_identity_state_and_amount_are_not_funds(self):
        cases = [('appid','wx_other'), ('mchid','1900000002'), ('out_trade_no','other_order'),
            ('trade_type','JSAPI'), ('trade_state','REFUND'), ('trade_state','NOTPAY'),
            ('trade_state','CLOSED'), ('trade_state','USERPAYING'), ('trade_state','PAYERROR'),
            ('transaction_id',''), ('success_time','2027-01-15T09:00:00'),
            ('success_time','2027-01-15 09:00:00Z'), ('amount',{'total':700,'currency':'CNY'}),
            ('amount',{'total':731,'currency':'USD'}), ('amount',{'total':True,'currency':'CNY'}),
            ('amount',{'total':731.0,'currency':'CNY'}), ('amount',{'payer_total':731,'currency':'CNY'})]
        for field, value in cases:
            transaction = self.transaction() | {field:value}
            for notification in (False, True):
                body = self.notification(transaction) if notification else json.dumps(transaction).encode()
                with self.subTest(field=field, notification=notification), self.assertRaises(ValueError):
                    self.fact(body, notification=notification)

    def test_missing_frozen_server_expectation_cannot_produce_funds(self):
        body = json.dumps(self.transaction()).encode()
        for kwargs in ({'appid':''}, {'mchid':''}, {'reference':None},
                {'amount_minor':True}, {'merchant_reference':''}):
            with self.subTest(case=tuple(kwargs)), self.assertRaises(ValueError):
                self.fact(body, **kwargs)

    def test_rfc3339_rejects_normalized_invalid_timezone_offsets(self):
        for suffix in ('+08:60', '+24:00', '-23:60'):
            body = json.dumps(self.transaction() | {'success_time':'2027-01-15T09:00:00'+suffix}).encode()
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                self.fact(body)

    def call(self, operation='native', **kwargs):
        call = getattr(wechat, 'call_wechat_api', None)
        self.assertTrue(callable(call), 'A bounded authenticated WeChat transport is required')
        fields = dict(self.config, public_key=self.public_pem, now=1800000000)
        if operation == 'native':
            fields.update(appid='wx_fixture', amount_minor=731, description='Sage 订单',
                notify_origin='https://sage.example', time_expire='2027-01-15T09:00:00+08:00')
        if operation in ('refund', 'refund_query'):
            fields['refund_reference'] = 'fixture_refund'
        if operation == 'refund':
            fields.update(amount_minor=731, refund_minor=123)
        return call(operation, self.reference, **(fields | kwargs))

    def transport(self, body=b'{"code_url":"weixin://wxpay/bizpayurl?fixture=value"}', *,
            status=200, headers=None, compressed=False, failure=None, stream_failure=None):
        self.sent = []
        self.closed_responses = []
        raw_headers = HTTPHeaderDict(self.signed(body) if headers is None else headers)
        wire_body = gzip.compress(body) if compressed else body
        if compressed:
            raw_headers.add('Content-Encoding', 'gzip')

        def send(session, prepared, **kwargs):
            self.assertIsInstance(prepared, requests.PreparedRequest)
            self.assertFalse(session.trust_env)
            self.assertEqual(kwargs['proxies'], {})
            self.assertIs(kwargs['verify'], True)
            self.assertIs(kwargs['allow_redirects'], False)
            self.assertIs(kwargs['stream'], True)
            self.assertEqual(kwargs['timeout'], (5, 15))
            self.assertEqual(session.get_adapter(prepared.url).max_retries.total, 0)
            self.assertTrue(prepared.url.startswith('https://api.mch.weixin.qq.com/v3/'))
            self.assertEqual(prepared.headers['Wechatpay-Serial'], 'PUB_KEY_ID_3000000001')
            auth = prepared.headers['Authorization']
            self.assertTrue(auth.startswith('WECHATPAY2-SHA256-RSA2048 '))
            body_sent = prepared.body or b''
            self.assertIs(type(body_sent), bytes)
            signature = base64.b64decode(re.search(r'signature="([^"]+)"', auth)[1], validate=True)
            self.merchant_key.public_key().verify(signature,
                f'{prepared.method}\n{prepared.path_url}\n1800000000\nfixture_nonce\n'.encode()+body_sent+b'\n',
                padding.PKCS1v15(), hashes.SHA256())
            self.sent.append((prepared.method, prepared.path_url, body_sent))
            if failure:
                raise failure
            raw = HTTPResponse(body=io.BytesIO(wire_body), headers=raw_headers, preload_content=False)
            if stream_failure:
                original_read = raw._fp.read
                def broken_read(*args, **kw):
                    if raw._fp.tell():
                        raise stream_failure
                    return original_read(min(args[0] or 8192, 8))
                raw._fp.read = broken_read
            response = requests.Response()
            response.status_code, response.url, response.raw = status, prepared.url, raw
            response.headers.update(raw_headers)
            response.headers['Location'] = 'https://untrusted.example/redirect'
            original_close = response.close
            def close():
                self.closed_responses.append(response)
                original_close()
            response.close = close
            return response
        return patch.object(requests.Session, 'send', autospec=True, side_effect=send)

    def test_transport_native_returns_authenticated_code_url_and_evidence(self):
        body = b'{"code_url":"weixin://wxpay/bizpayurl?fixture=value"}'
        with self.transport(body):
            fields, evidence = self.call()
        self.assertEqual(fields, {'code_url': 'weixin://wxpay/bizpayurl?fixture=value'})
        self.assertEqual(evidence, hashlib.sha256(body).hexdigest())
        self.assertEqual(self.sent[0][:2], ('POST', '/v3/pay/transactions/native'))
        self.assertEqual(json.loads(self.sent[0][2])['amount'], {'total':731, 'currency':'CNY'})
        self.assertEqual(len(self.closed_responses), 1)

    def test_transport_all_five_operations_keep_original_references_and_bytes(self):
        cases = [('native', 200, b'{"code_url":"weixin://wxpay/path?any=parameter"}', 'POST', '/v3/pay/transactions/native'),
            ('query', 200, b'{"trade_state":"NOTPAY"}', 'GET',
                '/v3/pay/transactions/out-trade-no/11111111222243338444555555555555?mchid=1900000001'),
            ('close', 204, b'', 'POST',
                '/v3/pay/transactions/out-trade-no/11111111222243338444555555555555/close'),
            ('refund', 200, b'{"status":"PROCESSING"}', 'POST', '/v3/refund/domestic/refunds'),
            ('refund_query', 200, b'{"status":"PROCESSING"}', 'GET', '/v3/refund/domestic/refunds/fixture_refund')]
        for operation, status, body, method, path in cases:
            with self.subTest(operation=operation), self.transport(body, status=status):
                fields, evidence = self.call(operation)
            self.assertEqual(fields, json.loads(body) if body else None)
            self.assertEqual(evidence, hashlib.sha256(body).hexdigest())
            self.assertEqual(self.sent[0][:2], (method, path))
            self.assertEqual(len(self.sent), 1)
            self.assertEqual(len(self.closed_responses), 1)

    def test_transport_preserves_duplicate_raw_signature_headers(self):
        body = b'{"trade_state":"NOTPAY"}'
        for field in ('Wechatpay-Timestamp', 'Wechatpay-Nonce', 'Wechatpay-Serial', 'Wechatpay-Signature'):
            headers = self.signed(body)
            headers += [(field.lower(), dict(headers)[field])]
            with self.subTest(field=field), self.transport(body, headers=headers):
                with self.assertRaisesRegex(ValueError, '^WECHAT_CALL_UNKNOWN$'):
                    self.call('query')
                self.assertEqual(len(self.sent), 1)
            self.assertEqual(len(self.closed_responses), 1)

    def test_transport_rejects_unsigned_or_bad_signed_json_without_leaking_body(self):
        cases = [(b'{"secret":"RAW_RESPONSE_MUST_NOT_LEAK"}', []),
            (b'{"secret":"RAW_RESPONSE_MUST_NOT_LEAK"}', self.signed(b'other')),
            (b'{"x":1,"x":2}', None), (b'not JSON RAW_RESPONSE_MUST_NOT_LEAK', None)]
        for body, headers in cases:
            with self.subTest(body_length=len(body)), self.transport(body, headers=headers), self.assertNoLogs():
                with self.assertRaisesRegex(ValueError, '^WECHAT_CALL_UNKNOWN$'):
                    self.call('query')
            self.assertEqual(len(self.closed_responses), 1)

    def test_transport_compressed_response_is_bounded_after_decoding_and_hashed_exactly(self):
        body = b'{"trade_state":"NOTPAY"}'
        with self.transport(body, compressed=True):
            self.assertEqual(self.call('query'), (json.loads(body), hashlib.sha256(body).hexdigest()))
        for compressed in (False, True):
            with self.subTest(compressed=compressed), self.transport(b' '*65537, compressed=compressed):
                with self.assertRaisesRegex(ValueError, '^WECHAT_CALL_UNKNOWN$'):
                    self.call('query')
            self.assertEqual(len(self.closed_responses), 1)

    def test_transport_accepts_exact_64k_authenticated_json(self):
        body = b'{"padding":"'+b'a'*65522+b'"}'
        self.assertEqual(len(body), 65536)
        with self.transport(body):
            fields, evidence = self.call('query')
        self.assertEqual(len(fields['padding']), 65522)
        self.assertEqual(evidence, hashlib.sha256(body).hexdigest())

    def test_transport_status_errors_and_redirects_never_follow_or_retry(self):
        for status in (301, 302, 307, 400, 401, 429, 500, 503):
            with self.subTest(status=status), self.transport(b'RAW_RESPONSE_MUST_NOT_LEAK', status=status), self.assertNoLogs():
                with self.assertRaisesRegex(ValueError, '^WECHAT_CALL_UNKNOWN$'):
                    self.call('query')
            self.assertEqual(len(self.sent), 1)
            self.assertEqual(len(self.closed_responses), 1)

    def test_transport_timeout_and_stream_failure_do_not_retry_or_log_exception(self):
        for failure in (requests.Timeout('RAW_EXCEPTION_MUST_NOT_LEAK'),
                requests.ConnectionError('RAW_EXCEPTION_MUST_NOT_LEAK')):
            with self.transport(failure=failure), self.assertNoLogs():
                with self.assertRaisesRegex(ValueError, '^WECHAT_CALL_UNKNOWN$'):
                    self.call('query')
                self.assertEqual(len(self.sent), 1)
        with self.transport(stream_failure=OSError('RAW_EXCEPTION_MUST_NOT_LEAK')), self.assertNoLogs():
            with self.assertRaisesRegex(ValueError, '^WECHAT_CALL_UNKNOWN$'):
                self.call('query')
        self.assertEqual(len(self.closed_responses), 1)

    def test_transport_session_closes_on_success_and_failure_without_environment_credentials(self):
        original_close = requests.Session.close
        closed = []
        def close(session):
            closed.append(session)
            original_close(session)
        with patch.object(requests.Session, 'close', autospec=True, side_effect=close), \
                patch.dict(os.environ, {'HTTPS_PROXY':'http://proxy.invalid:1',
                    'REQUESTS_CA_BUNDLE':'/untrusted/ca-must-not-be-used'}):
            with self.transport():
                self.call()
            with self.transport(failure=requests.Timeout('RAW_EXCEPTION_MUST_NOT_LEAK')):
                with self.assertRaisesRegex(ValueError, '^WECHAT_CALL_UNKNOWN$'):
                    self.call()
        self.assertEqual(len(closed), 2)

    def test_transport_native_code_url_uses_local_safe_scheme_policy(self):
        invalid = [None, '', 'https://example.invalid/pay', 'weixin://wxpay/',
            'weixin://user@wxpay/path', 'weixin://wxpay:443/path', 'weixin://wxpay/path#fragment',
            'weixin://wxpay/path#', 'weixin://other/path', 'weixin://wxpay/path with spaces',
            'weixin://wxpay/path\n', 'weixin://wxpay/二维码', 'weixin://wxpay/path?x=%0a',
            'weixin://wxpay/'+('a'*2048), 123]
        for value in invalid:
            body = json.dumps({'code_url':value}).encode()
            with self.subTest(value_type=type(value).__name__), self.transport(body):
                with self.assertRaisesRegex(ValueError, '^WECHAT_CALL_UNKNOWN$'):
                    self.call()

    def test_transport_invalid_arguments_never_issue_requests(self):
        for kwargs in ({'url':'https://untrusted.example'}, {'session':object()},
                {'amount_minor':True}, {'private_key':b'not a key'}):
            with self.subTest(fields=list(kwargs)), self.transport():
                with self.assertRaisesRegex(ValueError, '^WECHAT_REQUEST_INVALID$'):
                    self.call(**kwargs)
                self.assertEqual(self.sent, [])

    def test_existing_protocol_build_still_never_sends(self):
        with self.transport():
            self.build('query')
            self.verify(b'{"trade_state":"NOTPAY"}')
            self.assertEqual(self.sent, [])

    def test_adapter_redirect_is_closed_before_session_reads_body_or_stream_error(self):
        for stream_error in (False, True):
            reads, sends = [], []
            raw = HTTPResponse(body=io.BytesIO(b'RAW_RESPONSE_MUST_NOT_LEAK'*50000), preload_content=False)
            original_read = raw.read
            def read(*args, **kwargs):
                reads.append(1)
                if stream_error:
                    raise ProtocolError('RAW_EXCEPTION_MUST_NOT_LEAK')
                return original_read(*args, **kwargs)
            raw.read = read
            response = requests.Response()
            response.status_code, response.raw = 302, raw
            response.headers['Location'] = 'https://untrusted.example/redirect'
            def send(adapter, prepared, **kwargs):
                sends.append(prepared)
                response.request, response.url = prepared, prepared.url
                self.assertTrue(kwargs['stream'])
                self.assertIs(kwargs['verify'], True)
                self.assertEqual(adapter.max_retries.total, 0)
                return response
            with self.subTest(stream_error=stream_error), \
                    patch.object(requests.adapters.HTTPAdapter, 'send', autospec=True, side_effect=send), \
                    self.assertNoLogs():
                try:
                    with self.assertRaisesRegex(ValueError, '^WECHAT_CALL_UNKNOWN$'):
                        self.call('query')
                    self.assertEqual(reads, [], 'Session redirect preparation must never consume the body')
                    self.assertTrue(raw.closed)
                    self.assertEqual(len(sends), 1)
                finally:
                    raw.close()
