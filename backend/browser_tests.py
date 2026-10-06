"""Real HTTP/browser smoke, isolated test DB + in-memory email, never real PSP.

Run explicitly after the release build with SAGE_ENV=test and PLAYWRIGHT_MODULE
pointing at the existing Playwright installation. No test-only HTTP endpoint,
fixed OTP, production secret, external email, or mocked browser response is used.
"""

import os
import re
import selectors
import shutil
import subprocess
from datetime import timedelta

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
        self.assertEqual(AuditEvent.objects.filter(action="auth.verified").count(), 1)
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
