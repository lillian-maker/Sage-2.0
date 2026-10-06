"""Real browser/HTTP contracts on an isolated database and in-memory email."""
import os
import re
import selectors
import subprocess
import tempfile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import LiveServerTestCase, override_settings

from .models import AccountProfile, AccountSession, Consultation


@override_settings(LOCAL=True, AUTH_EMAIL_ENABLED=True, AUTH_EMAIL_TRANSPORT='smtp',
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_HOST='fixture.invalid', DEFAULT_FROM_EMAIL='sender@example.test',
    AUTH_RESEND_SECONDS=0, BILLING_SALES_ENABLED=False)
class WebsiteBrowserTests(LiveServerTestCase):
    host = '127.0.0.1'

    def test_account_and_anonymous_consultation_persist_with_no_financial_write(self):
        self.assertEqual(settings.ENVIRONMENT, 'test')
        module = os.environ.get('PLAYWRIGHT_MODULE')
        self.assertTrue(module and os.path.isdir(module))
        env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR') if key in os.environ}
        env.update(PLAYWRIGHT_MODULE=module, SAGE_URL=self.live_server_url)
        with tempfile.TemporaryDirectory(prefix='sage-avatar-browser-') as media:
            with override_settings(MEDIA_ROOT=media):
                process = subprocess.Popen(['node', str(settings.BASE_DIR / 'tests/website-account-live.cjs')],
                    cwd=settings.BASE_DIR, env=env, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
                try:
                    for index in range(2):
                        with selectors.DefaultSelector() as selector:
                            selector.register(process.stdout, selectors.EVENT_READ)
                            self.assertTrue(selector.select(timeout=30), 'Browser OTP handoff timed out')
                            event = process.stdout.readline().strip()
                            if event != 'code requested':
                                _, error = process.communicate(timeout=5)
                                self.fail('Browser failed before handoff: '+error)
                        self.assertEqual(len(mail.outbox), index+1)
                        code = re.search(r'code: ([0-9]{6})', mail.outbox[-1].body)
                        self.assertIsNotNone(code)
                        process.stdin.write(code.group(1)+'\n')
                        process.stdin.flush()
                    output, error = process.communicate(timeout=60)
                    self.assertEqual(output.strip(), '')
                    self.assertEqual(process.returncode, 0, error)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.communicate(timeout=5)
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream and not stream.closed:
                            stream.close()
        user = get_user_model().objects.get()
        self.assertEqual(user.first_name, 'Browser fixture')
        self.assertTrue(AccountProfile.objects.get(user=user).product_updates)
        self.assertEqual(AccountProfile.objects.get(user=user).avatar_name, '')
        self.assertEqual(AccountSession.objects.filter(user=user, revoked_at__isnull=False).count(), 1)
        self.assertEqual(Consultation.objects.count(), 1)
        self.assertEqual(Consultation.objects.get().status, 'pending')
        from .commerce.models import Order, CreditBatch, PaymentFact
        self.assertEqual((Order.objects.count(), CreditBatch.objects.count(), PaymentFact.objects.count()), (0, 0, 0))
