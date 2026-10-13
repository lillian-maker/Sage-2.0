"""Real browser/HTTP contracts on an isolated database and in-memory email."""
import os
import re
import selectors
import subprocess
import tempfile
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import LiveServerTestCase, override_settings

from .models import AccountProfile, AccountSession, Consultation, InvitationCode
from .authentication_tests import AUTH_FIXTURE, PHONE_FIXTURE


@override_settings(**AUTH_FIXTURE)
@override_settings(LOCAL=True, INVITATION_CODES_READY=True,
    # This combined scenario compresses registration, password logins, two resets and three
    # password attempts into seconds. Production's 5/15min identity gate is
    # covered separately; do not change that production setting for this fixture.
    AUTH_RESEND_SECONDS=0, AUTH_IDENTIFIER_LIMIT=20, BILLING_SALES_ENABLED=False)
class WebsiteBrowserTests(LiveServerTestCase):
    host = '127.0.0.1'

    def test_account_and_anonymous_consultation_persist_with_no_financial_write(self):
        self.assertEqual(settings.ENVIRONMENT, 'test')
        InvitationCode.objects.create(code='INVALID88', blogger='NONPRODUCTION-FIXTURE-ONLY')
        supplier = patch('backend.authentication.CommonClient.call_json', return_value={'Response': {'CaptchaCode': 1}})
        network = supplier.start()
        self.addCleanup(supplier.stop)
        module = os.environ.get('PLAYWRIGHT_MODULE')
        self.assertTrue(module and os.path.isdir(module))
        env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR') if key in os.environ}
        env.update(PLAYWRIGHT_MODULE=module, SAGE_URL=self.live_server_url)
        with tempfile.TemporaryDirectory(prefix='sage-avatar-browser-') as media:
            with override_settings(MEDIA_ROOT=media, AVATAR_LOCAL_FLOCK_VERIFIED=True):
                process = subprocess.Popen(['node', str(settings.BASE_DIR / 'tests/website-account-live.cjs')],
                    cwd=settings.BASE_DIR, env=env, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
                try:
                    for index in range(3):
                        with selectors.DefaultSelector() as selector:
                            selector.register(process.stdout, selectors.EVENT_READ)
                            self.assertTrue(selector.select(timeout=30), 'Browser OTP handoff timed out')
                            event = process.stdout.readline().strip()
                            if event != 'code requested':
                                _, error = process.communicate(timeout=5)
                                self.fail('Browser failed before handoff: '+error)
                        self.assertEqual(len(mail.outbox), index+1)
                        if index == 0:
                            # Real final recheck: it was valid at start, then
                            # the trusted fixture source disables it before OTP.
                            InvitationCode.objects.filter(pk='INVALID88').update(is_active=False)
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
        self.assertEqual(network.call_count, 5)
        self.assertEqual(AccountProfile.objects.get(user=user).agreement_version, 'NONPRODUCTION-FIXTURE-ONLY')
        self.assertEqual(user.first_name, 'Browser fixture')
        self.assertTrue(AccountProfile.objects.get(user=user).product_updates)
        self.assertEqual(AccountProfile.objects.get(user=user).avatar_name, '')
        self.assertEqual(AccountSession.objects.filter(user=user, revoked_at__isnull=False).count(), 3)
        self.assertTrue(user.check_password('Silver!864'))
        from .models import AuditEvent
        self.assertEqual(AuditEvent.objects.filter(actor=user, action='auth.password_reset').count(), 2)
        self.assertEqual(Consultation.objects.count(), 1)
        self.assertEqual(Consultation.objects.get().status, 'pending')
        from .commerce.models import Order, CreditBatch, PaymentFact
        self.assertEqual((Order.objects.count(), CreditBatch.objects.count(), PaymentFact.objects.count()), (0, 0, 0))


@override_settings(**dict(PHONE_FIXTURE,LOCAL=True,AUTH_RESEND_SECONDS=0,AUTH_IDENTIFIER_LIMIT=20,
    SMS_PASSWORD_CHANGED_TEMPLATE_APPROVED=False))
class PhoneWebsiteBrowserTests(LiveServerTestCase):
    host='127.0.0.1'

    def test_phone_registration_login_settings_reset_and_recovery_real_http(self):
        from queue import Queue
        from unittest.mock import Mock
        from django.core.signals import got_request_exception
        import sys,traceback
        from .models import LoginIdentity,AuthFlow,AuditEvent,BillingAccount,Membership
        self.assertEqual(settings.ENVIRONMENT,'test')
        codes=Queue()
        errors=[]
        def record_error(sender,**kwargs):
            kind,_,stack=sys.exc_info()
            errors.append((kind.__name__,[(os.path.basename(frame.filename),frame.lineno,frame.name) for frame in traceback.extract_tb(stack)]))
        got_request_exception.connect(record_error,weak=False)
        self.addCleanup(lambda:got_request_exception.disconnect(record_error))
        def receipt(client,request):
            self.assertIn(request.TemplateId,('124','125'))
            codes.put(request.TemplateParamSet[0])
            return Mock(SendStatusSet=[Mock(Code='Ok',SerialNo='fixture',PhoneNumber=request.PhoneNumberSet[0])])
        module=os.environ.get('PLAYWRIGHT_MODULE');self.assertTrue(module and os.path.isdir(module))
        env={key:os.environ[key] for key in ('PATH','HOME','TMPDIR') if key in os.environ}
        env.update(PLAYWRIGHT_MODULE=module,SAGE_URL=self.live_server_url)
        with patch('tencentcloud.sms.v20210111.sms_client.SmsClient.SendSms',autospec=True,side_effect=receipt) as sms,patch('backend.authentication.CommonClient.call_json',return_value={'Response':{'CaptchaCode':1}}) as captcha:
            process=subprocess.Popen(['node',str(settings.BASE_DIR/'tests/phone-auth-live.cjs')],cwd=settings.BASE_DIR,env=env,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
            try:
                for _ in range(3):
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout,selectors.EVENT_READ)
                        self.assertTrue(selector.select(timeout=30),'Phone browser OTP handoff timed out')
                        event=process.stdout.readline().strip()
                    if event!='code requested':
                        _,error=process.communicate(timeout=5);self.assertFalse(errors,errors);self.fail('Phone browser failed: '+error)
                    process.stdin.write(codes.get(timeout=5)+'\n');process.stdin.flush()
                output,error=process.communicate(timeout=45)
                self.assertEqual(output.strip(),'');self.assertFalse(errors,errors);self.assertEqual(process.returncode,0,error)
            finally:
                if process.poll() is None:process.terminate();process.communicate(timeout=5)
                for stream in (process.stdin,process.stdout,process.stderr):
                    if stream and not stream.closed:stream.close()
            self.assertEqual(sms.call_count,3);self.assertEqual(captcha.call_count,4)
        user=get_user_model().objects.get();self.assertTrue(user.check_password('Violet!431'));self.assertEqual(user.email,'')
        self.assertEqual(LoginIdentity.objects.get().method,'phone');self.assertEqual(BillingAccount.objects.count(),1)
        self.assertEqual(Membership.objects.get().role,'owner');self.assertEqual(AccountProfile.objects.get().agreement_version,'NONPRODUCTION-FIXTURE-ONLY')
        self.assertEqual(AuditEvent.objects.filter(action='auth.password_reset').count(),2)
        self.assertFalse(AuthFlow.objects.exclude(password_hash='').exists());self.assertEqual(len(mail.outbox),0)
