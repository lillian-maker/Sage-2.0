"""Real HTTP/browser smoke, isolated test DB + in-memory email, never real PSP.

Run explicitly after the release build with SAGE_ENV=test and PLAYWRIGHT_MODULE
pointing at the existing Playwright installation. No test-only HTTP endpoint,
fixed OTP, production secret or external email is used. Wallet browser tests
replace only external wallet APIs, never local website HTTP responses.
"""

import os
import re
import selectors
import shutil
import subprocess
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone

from .commerce.models import CreditBatch, Order, PaymentFact, ProductVersion, Subscription
from .commerce.billing import create_quote, create_order, record_confirmed_payment
from .commerce.subscriptions import activate_subscription
from .commerce.tests import FIXTURE_CHANNELS
from .models import AuditEvent, BillingAccount, LoginChallenge, LoginIdentity, PurchaseInquiry
from .authentication_tests import AUTH_FIXTURE


@override_settings(**AUTH_FIXTURE)
@override_settings(
    LOCAL=True,
    AUTH_EMAIL_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_HOST="in-memory-test-only.invalid",
    DEFAULT_FROM_EMAIL="no-reply@example.test",
    STATIC_URL="/__unused_test_static__/",
    MEDIA_URL="/__unused_test_media__/",
    BILLING_SALES_ENABLED=False,
    BILLING_APPROVED_CHANNELS={},
)
class AccountBrowserTests(LiveServerTestCase):
    host = "127.0.0.1"

    def test_browser_login_billing_procurement_and_logout(self):
        self._run_browser()

    def test_browser_subscription_read_cancel_and_persistence(self):
        self._run_browser(subscription_fixture=True)

    @override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS,
                       BILLING_QUOTE_TTL_SECONDS=300,
                       BILLING_TERMS_DOCUMENTS={'fixture-1': {'approved': True,
                           'zh': {'title': '隔离测试条款', 'paragraphs': ['仅用于测试，不是真实订阅合同。']},
                           'en': {'title': 'Isolated test terms', 'paragraphs': ['Fixture only, not a real contract.']}}})
    def test_browser_purchase_quote_consent_order_and_recovery(self):
        self._run_browser(purchase_fixture=True)

    def _run_browser(self, subscription_fixture=False, purchase_fixture=False):
        self.assertEqual(settings.ENVIRONMENT, "test", "Use SAGE_ENV=test for this isolated browser suite.")
        supplier=patch('backend.authentication.CommonClient.call_json',return_value={'Response':{'CaptchaCode':1}})
        network=supplier.start()
        self.addCleanup(supplier.stop)
        self.assertTrue((settings.BASE_DIR / "dist/pages/auth.js").is_file(), "Run the release build first.")
        module = os.environ.get("PLAYWRIGHT_MODULE")
        self.assertTrue(module and os.path.isdir(module), "PLAYWRIGHT_MODULE must point to the existing installation.")
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required for the browser smoke.")
        # Do not pass database passwords, mail settings or application secrets to
        # the child browser runner. The OTP travels only over this private pipe.
        env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "USER", "LOGNAME") if key in os.environ}
        env.update({"PLAYWRIGHT_MODULE": module, "SAGE_URL": self.live_server_url})
        if subscription_fixture:
            env['SAGE_BROWSER_SUBSCRIPTION_FIXTURE'] = '1'
        if purchase_fixture:
            env['SAGE_BROWSER_PURCHASE_FIXTURE'] = '1'
        process = subprocess.Popen(
            [node, str(settings.BASE_DIR / "tests/account-live.cjs")],
            cwd=settings.BASE_DIR, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=30), "Browser did not request an in-memory code within 30 seconds.")
                event = process.stdout.readline().strip()
            if event != "code requested":
                _, error = process.communicate(timeout=5)
                self.fail("Browser failed before OTP handoff: " + self._redact(error))
            self.assertEqual(len(mail.outbox), 1, "Exactly one in-memory email must be delivered.")
            message = mail.outbox[0]
            self.assertEqual(message.to, ["browser-smoke@example.test"])
            match = re.search(r"code: ([0-9]{6})", message.body)
            self.assertTrue(match, "The real login email must contain its generated code.")
            process.stdin.write(match.group(1) + "\n")
            process.stdin.flush()
            if subscription_fixture:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=30), 'Browser did not reach subscription fixture handoff.')
                    self.assertEqual(process.stdout.readline().strip(), 'subscription fixture requested')
                self._seed_subscription_fixture()
                process.stdin.write('subscription fixture ready\n')
                process.stdin.flush()
            if purchase_fixture:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=30), 'Browser did not reach purchase fixture handoff.')
                    self.assertEqual(process.stdout.readline().strip(), 'purchase fixture requested')
                product = ProductVersion.objects.create(code='pro', version=1, kind='subscription',
                    audience='personal', billing_cycle='monthly', unit_amount_minor=731, credits_quantity='100',
                    entitlements={'benefits': {'zh': ['隔离测试权益，不销售。'], 'en': ['Fixture benefits, not for sale.']}},
                    entitlement_version='fixture-1', credit_rule_version='fixture-1', terms_version='fixture-1',
                    state='published', effective_at=timezone.now()-timedelta(days=1))
                process.stdin.write(str(product.pk)+'\n')
                process.stdin.flush()
            output, error = process.communicate(timeout=45)
            self.assertEqual(self._redact(output).strip() if output.strip() else "", "", "The browser runner must not print account or verification data.")
            self.assertEqual(process.returncode, 0, self._redact(error))
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()

        # Persisted test-database evidence, not just a success toast. The second
        # test explicitly seeds an internal payment/mandate fixture, never a PSP.
        self.assertEqual(get_user_model().objects.count(), 1)
        identity = LoginIdentity.objects.get(identifier="browser-smoke@example.test")
        self.assertEqual(LoginChallenge.objects.filter(consumed_at__isnull=False).count(), 1)
        inquiry = PurchaseInquiry.objects.get()
        self.assertEqual(inquiry.created_by_id, identity.user_id)
        self.assertEqual(inquiry.account.personal_owner_id, identity.user_id)
        self.assertEqual(inquiry.company, "Sage browser integration fixture")
        self.assertEqual(inquiry.seats, 2)
        self.assertEqual(inquiry.status, "received")
        self.assertEqual(AuditEvent.objects.filter(action="auth.registered").count(), 1)
        self.assertEqual(network.call_count,1)
        self.assertTrue(identity.user.check_password('Violet!431'))
        self.assertEqual(AuditEvent.objects.filter(action="procurement.received").count(), 1)
        self.assertEqual((Order.objects.count(), PaymentFact.objects.count(), CreditBatch.objects.count()),
                         (1, 1, 1) if subscription_fixture else (1, 0, 0) if purchase_fixture else (0, 0, 0))
        if purchase_fixture:
            order = Order.objects.get()
            self.assertEqual(order.status, 'pending_payment')
            self.assertIsNone(order.paid_at)
            self.assertEqual(order.amount_minor, 731)
            self.assertEqual(order.created_by_id, identity.user_id)
            self.assertIn('terms_document_digest', order.snapshot)
        if subscription_fixture:
            subscription = Subscription.objects.get()
            self.assertFalse(subscription.auto_renew)
            self.assertEqual(subscription.cancellation_state, 'processing')
            self.assertIsNone(subscription.cancel_confirmed_at)
            self.assertEqual(AuditEvent.objects.filter(action='subscription.cancel_requested').count(), 1)

    def _seed_subscription_fixture(self):
        identity = LoginIdentity.objects.get(identifier='browser-smoke@example.test')
        account = BillingAccount.objects.get(personal_owner=identity.user)
        now = timezone.now()
        with override_settings(BILLING_SALES_ENABLED=True, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS):
            product = ProductVersion.objects.create(code='fixture_plan', version=1, kind='subscription',
                audience='personal', billing_cycle='annual', unit_amount_minor=12345, credits_quantity='17.25',
                entitlements={'fixture': True}, entitlement_version='fixture-1', credit_rule_version='fixture-1',
                terms_version='fixture-1', state='published', effective_at=now-timedelta(days=1))
            quote = create_quote(account, identity.user, product.pk, 1, now+timedelta(minutes=5), channel='fixture', buyer_region='CN')
            order = create_order(account, identity.user, {'quote_id':quote['id'], 'agreement_acceptance':'fixture-1'}, 'browser-fixture')
            record_confirmed_payment(account, order['id'], channel='fixture', merchant_reference='fixture-shenzhen',
                transaction_reference='browser-fixture', amount_minor=12345, currency='CNY', paid_at=now, evidence_hash='b'*64)
            activate_subscription(account, order['id'], activated_at=now, billing_timezone='Asia/Shanghai',
                calendar_rule_version='fixture-calendar-v1', mandate=dict(channel='fixture', merchant_reference='fixture-shenzhen',
                agreement_reference='fixture-agreement', evidence_hash='a'*64, executor='sage', automatic_retry=False))

    @staticmethod
    def _redact(value):
        return re.sub(r"\b[0-9]{6}\b", "[redacted]", value or "Browser process failed without diagnostics.")


@override_settings(LOCAL=True, BILLING_SALES_ENABLED=True, AUTH_EMAIL_ENABLED=False,
    AUTH_PHONE_ENABLED=False, BILLING_WALLET_TESTS={}, BILLING_APPROVED_CHANNELS={
        'wechat': {'enabled':True, 'currency':'CNY', 'merchant_entity':'fixture-shenzhen',
            'regions':['CN'], 'capabilities':['one_time']}})
class WechatNativeBrowserTests(LiveServerTestCase):
    """Real browser/HTTP/ledger; only the external APIv3 transport is replaced."""
    host = '127.0.0.1'

    def test_browser_native_qr_recovery_and_signed_notification_deliver_once(self):
        import base64
        import io
        import json
        import socket
        from urllib.parse import urlsplit
        import requests
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from django.db import connection
        from urllib3.response import HTTPResponse
        from urllib3._collections import HTTPHeaderDict
        from .commerce.models import PaymentAttempt
        from .commerce.tests import fixture_account, fixture_product
        from .models import AccountSession, Membership

        self.assertEqual(settings.ENVIRONMENT, 'test')
        module = os.environ.get('PLAYWRIGHT_MODULE')
        self.assertTrue(module and os.path.isdir(module), 'PLAYWRIGHT_MODULE must point to the existing installation.')
        node = shutil.which('node')
        self.assertIsNotNone(node)
        merchant = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        platform = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        configuration = {'enabled':True, 'app_id':'wxSyntheticBrowser2026', 'mch_id':'1900000001',
            'merchant_entity':'fixture-shenzhen', 'merchant_serial':'AABB01',
            'private_key':merchant.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
            'public_key':platform.public_key().public_bytes(serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo), 'public_key_id':'PUB_KEY_ID_20261016',
            'api_v3_key':b'b'*32, 'notify_origin':'https://fixture.example', 'payment_ttl_seconds':900}
        self.enterContext(override_settings(BILLING_WECHAT_NATIVE=configuration))
        user, account = fixture_account('wechat-browser-fixture')
        Membership.objects.create(user=user, account=account, role='owner')
        product = fixture_product()
        quote = create_quote(account, user, product.pk, 1, timezone.now()+timedelta(minutes=5),
            channel='wechat', buyer_region='CN')
        order = create_order(account, user, {'quote_id':quote['id'], 'agreement_acceptance':'fixture-1'},
            'wechat-browser-order')
        self.client.force_login(user)
        session = self.client.session
        session['active_account_id'] = str(account.pk)
        session.save()
        AccountSession.objects.create(user=user, session_key=session.session_key, client='Synthetic browser')
        code_url = 'weixin://wxpay/bizpayurl?pr=synthetic_browser_20261016'
        sent, verified_success = [], []

        def signed(body):
            timestamp, nonce = str(int(timezone.now().timestamp())), 'synthetic_browser_nonce'
            signature = platform.sign(timestamp.encode()+b'\n'+nonce.encode()+b'\n'+body+b'\n',
                padding.PKCS1v15(), hashes.SHA256())
            return {'Wechatpay-Timestamp':timestamp, 'Wechatpay-Nonce':nonce,
                'Wechatpay-Serial':configuration['public_key_id'],
                'Wechatpay-Signature':base64.b64encode(signature).decode()}

        def transport(adapter, prepared, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            self.assertEqual(urlsplit(prepared.url).netloc, 'api.mch.weixin.qq.com')
            self.assertEqual(kwargs['verify'], True)
            self.assertEqual(kwargs['timeout'], (5,15))
            self.assertFalse(kwargs.get('proxies'))
            authorization = dict(re.findall(r'(\w+)="([^"]*)"', prepared.headers['Authorization']))
            canonical = (prepared.method+'\n'+prepared.path_url+'\n'+authorization['timestamp']+'\n'+
                authorization['nonce_str']+'\n').encode()+(prepared.body or b'')+b'\n'
            merchant.public_key().verify(base64.b64decode(authorization['signature']), canonical,
                padding.PKCS1v15(), hashes.SHA256())
            attempt = PaymentAttempt.objects.get(order_id=order['id'])
            self.assertIsNotNone(attempt.dispatched_at)
            self.assertEqual(attempt.dispatch_authorization, {'channel':'wechat', 'environment':'LIVE',
                'app_id':configuration['app_id'], 'mch_id':configuration['mch_id'],
                'merchant_entity':'fixture-shenzhen'})
            sent.append((prepared.method, prepared.path_url))
            if prepared.method == 'POST':
                self.assertEqual(prepared.path_url, '/v3/pay/transactions/native')
                content = json.loads(prepared.body)
                self.assertEqual(content['out_trade_no'], attempt.pk.hex)
                self.assertEqual(content['amount'], {'total':731,'currency':'CNY'})
                fields = {'code_url':code_url}
            else:
                self.assertEqual(prepared.method, 'GET')
                self.assertEqual(prepared.path_url,
                    '/v3/pay/transactions/out-trade-no/'+attempt.pk.hex+'?mchid=1900000001')
                self.assertEqual(len(verified_success), 1)
                fields = verified_success[0]
            body = json.dumps(fields, separators=(',', ':')).encode()
            headers = HTTPHeaderDict(signed(body))
            response = requests.Response()
            response.status_code, response.url, response.request = 200, prepared.url, prepared
            response.raw = HTTPResponse(body=io.BytesIO(body), headers=headers, preload_content=False)
            response.headers.update(headers)
            return response

        # LiveServer's own HTTP connections remain loopback-only. All provider
        # HTTP reaches the real Requests pipeline and this terminal transport.
        original_connect, original_resolve = socket.socket.connect, socket.getaddrinfo
        def connect(sock, address):
            self.assertIn(address[0], ('127.0.0.1','::1'))
            return original_connect(sock, address)
        def resolve(host, *args, **kwargs):
            self.assertIn(host, ('127.0.0.1','::1','localhost'))
            return original_resolve(host, *args, **kwargs)
        self.enterContext(patch('socket.socket.connect', new=connect))
        self.enterContext(patch('socket.getaddrinfo', side_effect=resolve))
        self.enterContext(patch.object(requests.adapters.HTTPAdapter, 'send', autospec=True, side_effect=transport))
        env = {key:os.environ[key] for key in ('PATH','HOME','TMPDIR') if key in os.environ}
        env.update(PLAYWRIGHT_MODULE=module, SAGE_URL=self.live_server_url)
        process = subprocess.Popen([node, str(settings.BASE_DIR/'tests/wechat-native-live.cjs')],
            cwd=settings.BASE_DIR, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1)
        try:
            process.stdin.write(json.dumps({'cookie':{'name':settings.SESSION_COOKIE_NAME,
                'value':session.session_key, 'url':self.live_server_url}, 'quote_id':quote['id'],
                'order_id':order['id'], 'account_id':str(account.pk)})+'\n')
            process.stdin.flush()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=35), 'Browser did not recover the persisted Native QR.')
                event = process.stdout.readline().strip()
            if event != 'qr recovered':
                _, diagnostic = process.communicate(timeout=5)
                self.fail('WeChat browser failed before QR handoff: '+diagnostic)
            attempt = PaymentAttempt.objects.get(order_id=order['id'])
            self.assertEqual(attempt.wechat_code_url, code_url)
            self.assertEqual(len(sent), 1)
            self.assertFalse(PaymentFact.objects.exists())
            self.assertFalse(CreditBatch.objects.exists())
            fields = {'appid':configuration['app_id'], 'mchid':configuration['mch_id'],
                'out_trade_no':attempt.pk.hex, 'transaction_id':'420000SyntheticBrowser2026',
                'trade_type':'NATIVE', 'trade_state':'SUCCESS', 'amount':{'total':731,'currency':'CNY'},
                'success_time':timezone.now().replace(microsecond=0).isoformat(timespec='seconds')}
            verified_success.append(fields)
            nonce, aad = 'browser12345', 'transaction'
            ciphertext = AESGCM(configuration['api_v3_key']).encrypt(nonce.encode(),
                json.dumps(fields,separators=(',', ':')).encode(), aad.encode())
            body = json.dumps({'id':'synthetic-browser-notify', 'event_type':'TRANSACTION.SUCCESS',
                'resource_type':'encrypt-resource', 'resource':{'algorithm':'AEAD_AES_256_GCM',
                    'nonce':nonce,'associated_data':aad,'ciphertext':base64.b64encode(ciphertext).decode()}},
                separators=(',', ':')).encode()
            process.stdin.write(json.dumps({'body':base64.b64encode(body).decode(), 'headers':signed(body)})+'\n')
            process.stdin.flush()
            output, diagnostic = process.communicate(timeout=40)
            self.assertEqual(output.strip(), '')
            self.assertEqual(process.returncode, 0, diagnostic)
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=5)
            for stream in (process.stdin,process.stdout,process.stderr):
                if stream and not stream.closed:
                    stream.close()
        current = Order.objects.get(pk=order['id'])
        self.assertEqual(current.status, 'fulfilled')
        self.assertEqual((PaymentFact.objects.count(),CreditBatch.objects.count()), (1,1))
        self.assertEqual(PaymentFact.objects.get().paid_at.isoformat(timespec='seconds'), fields['success_time'])
        self.assertEqual([method for method,_ in sent], ['POST','GET'])


@override_settings(LOCAL=True, BILLING_SALES_ENABLED=True, AUTH_EMAIL_ENABLED=False,
    AUTH_PHONE_ENABLED=False, BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS,
    BILLING_WALLET_TESTS={'google_pay':{'enabled':True},
        'apple_pay':{'enabled':True,'country_code':'US'}})
class WalletPreparationBrowserTests(LiveServerTestCase):
    """Real order-bound website HTTP; wallet SDKs alone are offline substitutes."""
    host = '127.0.0.1'

    def test_wallet_sdk_results_cannot_dispatch_or_deliver_real_local_order(self):
        import json
        import socket
        from .commerce.models import PaymentAttempt
        from .commerce.tests import fixture_account, fixture_product
        from .models import AccountSession, Membership

        self.assertEqual(settings.ENVIRONMENT, 'test')
        module = os.environ.get('PLAYWRIGHT_MODULE')
        self.assertTrue(module and os.path.isdir(module))
        node = shutil.which('node')
        self.assertIsNotNone(node)
        user, account = fixture_account('wallet-browser-fixture')
        Membership.objects.create(user=user,account=account,role='owner')
        product = fixture_product()
        quote = create_quote(account,user,product.pk,1,timezone.now()+timedelta(minutes=5),
            channel='fixture',buyer_region='CN')
        order = create_order(account,user,{'quote_id':quote['id'],'agreement_acceptance':'fixture-1'},
            'wallet-browser-order')
        self.client.force_login(user)
        session = self.client.session
        session['active_account_id'] = str(account.pk)
        session.save()
        AccountSession.objects.create(user=user,session_key=session.session_key,client='Synthetic browser')
        original_connect, original_resolve = socket.socket.connect, socket.getaddrinfo
        def connect(sock,address):
            self.assertIn(address[0],('127.0.0.1','::1'))
            return original_connect(sock,address)
        def resolve(host,*args,**kwargs):
            self.assertIn(host,('127.0.0.1','::1','localhost'))
            return original_resolve(host,*args,**kwargs)
        self.enterContext(patch('socket.socket.connect',new=connect))
        self.enterContext(patch('socket.getaddrinfo',side_effect=resolve))
        env = {key:os.environ[key] for key in ('PATH','HOME','TMPDIR') if key in os.environ}
        env.update(PLAYWRIGHT_MODULE=module,SAGE_URL=self.live_server_url)
        process = subprocess.Popen([node,str(settings.BASE_DIR/'tests/wallet-test-live.cjs')],
            cwd=settings.BASE_DIR,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,text=True)
        fixture = {'cookie':{'name':settings.SESSION_COOKIE_NAME,'value':session.session_key,
            'url':self.live_server_url},'quote_id':quote['id'],'order_id':order['id'],
            'account_id':str(account.pk)}
        try:
            output, diagnostic = process.communicate(json.dumps(fixture)+'\n',timeout=60)
            self.assertEqual(output.strip(),'')
            self.assertEqual(process.returncode,0,diagnostic)
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=5)
        current = Order.objects.get(pk=order['id'])
        self.assertEqual(current.status,'pending_payment')
        self.assertIsNone(current.paid_at)
        self.assertFalse(PaymentAttempt.objects.exists())
        self.assertFalse(PaymentFact.objects.exists())
        self.assertFalse(CreditBatch.objects.exists())


@override_settings(LOCAL=True, BILLING_SALES_ENABLED=True, BILLING_SEAT_PURCHASE_ENABLED=True,
    BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS,
    BILLING_TERMS_DOCUMENTS={'fixture-1': {'approved': True,
        'zh': {'title': '测试', 'paragraphs': ['仅供本地测试']},
        'en': {'title': 'Fixture', 'paragraphs': ['Local test only']}}})
class BusinessSeatPurchaseBrowserTests(LiveServerTestCase):
    """Real website reads/writes; trusted funds are explicitly synthetic fixture input."""
    host = '127.0.0.1'

    def test_original_order_delivers_added_seats_after_synthetic_payment_only(self):
        import json
        import socket
        from .commerce.models import PaidSeat, PaymentAttempt, SeatPurchaseChange, SubscriptionPeriod
        from .commerce.subscription_tests import SubscriptionTests
        from .models import AccountSession
        from .security_tests import PHONE_SETTINGS

        self.assertEqual(settings.ENVIRONMENT, 'test')
        self.enterContext(override_settings(**PHONE_SETTINGS))
        SubscriptionTests.setUp(self)
        order_id = SubscriptionTests.business_paid(self, 'seat_included', 'monthly')
        activate_subscription(self.account, order_id, activated_at=self.anchor,
            billing_timezone='Asia/Shanghai', calendar_rule_version='fixture-calendar-v1', mandate=self.mandate)
        period = SubscriptionPeriod.objects.get(subscription__account=self.account)
        original_period = dict(period.snapshot)
        self.now.return_value = self.anchor+(period.ends_at-self.anchor)/2
        LoginIdentity.objects.create(user=self.user, method='phone', identifier='+8613800000000')
        self.client.force_login(self.user)
        session = self.client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        AccountSession.objects.create(user=self.user, session_key=session.session_key,
            client='Synthetic seat browser', sensitive_verified_at=timezone.now(),
            sensitive_method='phone', sensitive_account_id=self.account.pk)
        module = os.environ.get('PLAYWRIGHT_MODULE')
        self.assertTrue(module and os.path.isdir(module))
        node = shutil.which('node')
        self.assertIsNotNone(node)
        original_connect, original_resolve = socket.socket.connect, socket.getaddrinfo
        def connect(sock, address):
            self.assertIn(address[0], ('127.0.0.1', '::1'))
            return original_connect(sock, address)
        def resolve(host, *args, **kwargs):
            self.assertIn(host, ('127.0.0.1', '::1', 'localhost'))
            return original_resolve(host, *args, **kwargs)
        self.enterContext(patch('socket.socket.connect', new=connect))
        self.enterContext(patch('socket.getaddrinfo', side_effect=resolve))
        env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR') if key in os.environ}
        env.update(PLAYWRIGHT_MODULE=module, SAGE_URL=self.live_server_url)
        process = subprocess.Popen([node, str(settings.BASE_DIR/'tests/business-seat-purchase-live.cjs')],
            cwd=settings.BASE_DIR, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        try:
            fixture = {'cookie': {'name': settings.SESSION_COOKIE_NAME, 'value': session.session_key,
                'url': self.live_server_url}, 'account_id': str(self.account.pk)}
            process.stdin.write(json.dumps(fixture)+'\n'); process.stdin.flush()
            for index in range(2):
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=40), 'Browser did not reach the synthetic seat payment boundary.')
                    event = process.stdout.readline().strip()
                if event != 'seat fixture requested':
                    output, diagnostic = process.communicate(timeout=5)
                    self.fail('Seat browser stopped before payment: '+diagnostic)
                change = SeatPurchaseChange.objects.get(account=self.account, state='pending_payment')
                self.assertEqual(PaidSeat.objects.filter(subscription__account=self.account).count(), 2+index*2)
                self.assertEqual(PaymentFact.objects.count(), 1+index)
                self.assertFalse(PaymentAttempt.objects.exists())
                fact = dict(channel='fixture', merchant_reference='fixture-shenzhen',
                    transaction_reference='seat-browser-'+str(index), amount_minor=12345, currency='CNY',
                    paid_at=timezone.now(), evidence_hash='c'*64)
                record_confirmed_payment(self.account, change.order_id, **fact)
                record_confirmed_payment(self.account, change.order_id, **fact)
                process.stdin.write('seat fixture ready\n'); process.stdin.flush()
            output, diagnostic = process.communicate(timeout=30)
            self.assertEqual(output.strip(), '')
            self.assertEqual(process.returncode, 0, diagnostic)
        finally:
            if process.poll() is None:
                process.terminate(); process.communicate(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()
        period.refresh_from_db()
        self.assertEqual(period.snapshot, original_period)
        self.assertEqual(SeatPurchaseChange.objects.filter(state='activated').count(), 2)
        self.assertEqual(PaidSeat.objects.filter(subscription__account=self.account).count(), 6)
        self.assertEqual(PaymentFact.objects.count(), 3)
        self.assertEqual(Order.objects.count(), 3)
        self.assertEqual(CreditBatch.objects.count(), 6)
        self.assertFalse(PaymentAttempt.objects.exists())


@override_settings(LOCAL=True, BILLING_SALES_ENABLED=True,
    BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS)
class BusinessInvitationBrowserTests(LiveServerTestCase):
    """Real invitation HTTP, sessions and ledger; synthetic identities only."""
    host = '127.0.0.1'

    def test_admin_invites_target_accepts_and_revoke_releases_seat(self):
        import json
        import socket
        from django.test import Client
        from .commerce.models import BusinessInvitation, PaidSeat, PaymentAttempt
        from .commerce.subscription_tests import SubscriptionTests
        from .models import AccountSession, Membership
        from .security_tests import PHONE_SETTINGS

        self.assertEqual(settings.ENVIRONMENT, 'test')
        self.enterContext(override_settings(**PHONE_SETTINGS))
        SubscriptionTests.setUp(self)
        order_id = SubscriptionTests.business_paid(self, 'seat_included', 'monthly')
        activate_subscription(self.account, order_id, activated_at=self.anchor,
            billing_timezone='Asia/Shanghai', calendar_rule_version='fixture-calendar-v1',
            mandate=self.mandate)
        LoginIdentity.objects.create(user=self.user, method='phone', identifier='+8613800000000')
        original_counts = (CreditBatch.objects.count(), PaymentFact.objects.count(), Order.objects.count())

        def session_cookie(user, account, *, administrator=False):
            client = Client()
            client.force_login(user)
            session = client.session
            session['active_account_id'] = str(account.pk)
            session.save()
            AccountSession.objects.create(user=user, session_key=session.session_key,
                client='Synthetic invitation browser',
                sensitive_verified_at=self.anchor if administrator else None,
                sensitive_method='phone' if administrator else '',
                sensitive_account_id=self.account.pk if administrator else None)
            return {'name': settings.SESSION_COOKIE_NAME, 'value': session.session_key,
                    'url': self.live_server_url}

        admin_cookie = session_cookie(self.user, self.account, administrator=True)
        targets = []
        for language in ('zh', 'en'):
            target = get_user_model().objects.create_user(username='invitation-browser-'+language)
            personal = BillingAccount.objects.create(kind='personal', name='Invitation fixture',
                                                     personal_owner=target)
            Membership.objects.create(account=personal, user=target, role='owner')
            identifier = 'invitation-browser-'+language+'@example.invalid'
            LoginIdentity.objects.create(user=target, method='email', identifier=identifier)
            targets.append({'language': language, 'identifier': identifier,
                'cookie': session_cookie(target, personal), 'personal_account_id': str(personal.pk)})

        module = os.environ.get('PLAYWRIGHT_MODULE')
        self.assertTrue(module and os.path.isdir(module))
        node = shutil.which('node')
        self.assertIsNotNone(node)
        original_connect, original_resolve = socket.socket.connect, socket.getaddrinfo
        def connect(sock, address):
            self.assertIn(address[0], ('127.0.0.1', '::1'))
            return original_connect(sock, address)
        def resolve(host, *args, **kwargs):
            self.assertIn(host, ('127.0.0.1', '::1', 'localhost'))
            return original_resolve(host, *args, **kwargs)
        self.enterContext(patch('socket.socket.connect', new=connect))
        self.enterContext(patch('socket.getaddrinfo', side_effect=resolve))
        env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR') if key in os.environ}
        env.update(PLAYWRIGHT_MODULE=module, SAGE_URL=self.live_server_url)
        process = subprocess.Popen([node, str(settings.BASE_DIR/'tests/business-invitation-live.cjs')],
            cwd=settings.BASE_DIR, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        fixture = {'admin_cookie': admin_cookie, 'account_id': str(self.account.pk), 'targets': targets}
        try:
            output, diagnostic = process.communicate(json.dumps(fixture)+'\n', timeout=90)
            self.assertEqual(output.strip(), '')
            self.assertEqual(process.returncode, 0, diagnostic)
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=5)
        self.assertEqual(BusinessInvitation.objects.filter(state='accepted').count(), 2)
        self.assertEqual(BusinessInvitation.objects.filter(state='revoked').count(), 2)
        self.assertFalse(BusinessInvitation.objects.filter(state='pending').exists())
        self.assertEqual(PaidSeat.objects.filter(subscription__account=self.account,
                                                membership__isnull=False).count(), 2)
        self.assertEqual((CreditBatch.objects.count(), PaymentFact.objects.count(), Order.objects.count()),
                         original_counts)
        self.assertFalse(PaymentAttempt.objects.exists())


@override_settings(LOCAL=True, BILLING_SALES_ENABLED=True, BILLING_SEAT_PURCHASE_ENABLED=True,
    BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS,
    BILLING_TERMS_DOCUMENTS={'fixture-1': {'approved': True,
        'zh': {'title': '测试', 'paragraphs': ['仅供本地测试']},
        'en': {'title': 'Fixture', 'paragraphs': ['Local test only']}}})
class BusinessManagementBrowserTests(LiveServerTestCase):
    """Four real website operations on synthetic company state; no external sends."""
    from .commerce.subscription_tests import SeatPurchaseTests as _fixture

    host = '127.0.0.1'
    source = _fixture.source
    base_order = _fixture.base_order
    receipt = _fixture.receipt
    quote = _fixture.quote
    confirm = _fixture.confirm
    purchase = _fixture.purchase

    def test_chinese_management_operations_keep_paid_periods_separate(self):
        self._management('zh')

    def test_english_management_operations_keep_paid_periods_separate(self):
        self._management('en')

    def _management(self, language):
        import json
        import socket
        from .commerce.models import (PaidSeat, PaymentAttempt, SeatPurchaseChange,
            SeatReduction, MembershipRoleChange, SeatChange, ServiceRefund)
        from .commerce.subscriptions import append_paid_period, organization_summary
        from .models import AccountSession, Membership
        from .security_tests import PHONE_SETTINGS

        self.assertEqual(settings.ENVIRONMENT, 'test')
        self.enterContext(override_settings(**PHONE_SETTINGS))
        self._fixture.setUp(self)
        self.source(cycle='monthly', mode='shared_included', seats=10)
        future_order = self.base_order(10, 'future-management')
        append_paid_period(self.account, self.subscription.pk, future_order['id'], cycle_index=1)
        self.now.return_value = self.anchor+timedelta(days=1)
        self.purchase(2)
        member = Membership.objects.create(account=self.account, user=self.other_user, role='member')
        seats = list(PaidSeat.objects.filter(subscription=self.subscription).order_by('position'))
        original_snapshots = list(self.subscription.periods.order_by('cycle_index').values_list('snapshot', flat=True))
        original_credits = list(CreditBatch.objects.order_by('id').values('id', 'initial_quantity', 'expires_at'))
        original_counts = (PaymentFact.objects.count(), Order.objects.count())
        LoginIdentity.objects.create(user=self.user, method='phone', identifier='+8613800000000')
        self.client.force_login(self.user)
        session = self.client.session
        session['active_account_id'] = str(self.account.pk)
        session.save()
        AccountSession.objects.create(user=self.user, session_key=session.session_key,
            client='Synthetic management browser', sensitive_verified_at=timezone.now(),
            sensitive_method='phone', sensitive_account_id=self.account.pk)
        module = os.environ.get('PLAYWRIGHT_MODULE')
        self.assertTrue(module and os.path.isdir(module))
        node = shutil.which('node')
        self.assertIsNotNone(node)
        original_connect, original_resolve = socket.socket.connect, socket.getaddrinfo
        def connect(sock, address):
            self.assertIn(address[0], ('127.0.0.1', '::1'))
            return original_connect(sock, address)
        def resolve(host, *args, **kwargs):
            self.assertIn(host, ('127.0.0.1', '::1', 'localhost'))
            return original_resolve(host, *args, **kwargs)
        self.enterContext(patch('socket.socket.connect', new=connect))
        self.enterContext(patch('socket.getaddrinfo', side_effect=resolve))
        env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR') if key in os.environ}
        env.update(PLAYWRIGHT_MODULE=module, SAGE_URL=self.live_server_url)
        fixture = {'targets': [{'language': language, 'cookie': {
            'name': settings.SESSION_COOKIE_NAME, 'value': session.session_key, 'url': self.live_server_url},
            'account_id': str(self.account.pk), 'member_id': member.pk,
            'reduce_seat_id': str(seats[0].pk), 'cover_seat_id': str(seats[-1].pk)}]}
        process = subprocess.Popen([node, str(settings.BASE_DIR/'tests/business-management-live.cjs')],
            cwd=settings.BASE_DIR, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        try:
            output, diagnostic = process.communicate(json.dumps(fixture)+'\n', timeout=100)
            self.assertEqual(process.returncode, 0, diagnostic)
            result = json.loads(output)['results'][0]
        finally:
            if process.poll() is None:
                process.terminate(); process.communicate(timeout=5)
        self.assertEqual(result['language'], language)
        member.refresh_from_db()
        self.assertEqual(member.role, 'member')
        self.assertTrue(member.is_active)
        self.assertFalse(PaidSeat.objects.filter(membership=member).exists())
        self.assertEqual(MembershipRoleChange.objects.filter(membership=member).count(), 2)
        self.assertEqual(SeatChange.objects.filter(seat__subscription=self.subscription).count(), 2)
        reduction = SeatReduction.objects.get(pk=result['reduction_id'], account=self.account)
        self.assertEqual(reduction.state, 'applied')
        self.assertFalse(ServiceRefund.objects.filter(account=self.account).exists())
        next_purchase = SeatPurchaseChange.objects.get(pk=result['next_purchase_id'], account=self.account)
        self.assertEqual(next_purchase.order_id, Order.objects.get(pk=result['order_id']).pk)
        self.assertEqual(next_purchase.state, 'pending_payment')
        self.assertEqual(next_purchase.snapshot['scope'], 'next_paid')
        self.assertEqual(next_purchase.snapshot['cover_seat_ids'], [str(seats[-1].pk)])
        self.assertEqual(organization_summary(self.account, admin_actor=self.user)['seat_count'], 11)
        self.assertEqual(list(self.subscription.periods.order_by('cycle_index').values_list('snapshot', flat=True)), original_snapshots)
        self.assertEqual(list(CreditBatch.objects.order_by('id').values('id', 'initial_quantity', 'expires_at')), original_credits)
        self.assertEqual((PaymentFact.objects.count(), Order.objects.count()), (original_counts[0], original_counts[1]+1))
        self.assertFalse(PaymentAttempt.objects.exists())
