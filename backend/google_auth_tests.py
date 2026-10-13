"""Local signed OIDC fixtures; only token/certificate HTTP is replaced."""
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from datetime import timedelta
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import crypt, jwt
import requests
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import PBKDF2PasswordHasher, verify_password
from django.core import mail
from django.db import DatabaseError, connection, connections, close_old_connections, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from .authentication_tests import AUTH_FIXTURE
from .google_auth import CERTS_URL, TOKEN_URL, safe_return
from .models import (AccountProfile, AccountSession, AuditEvent, AuthFlow, AuthThrottle,
    BillingAccount, InvitationCode, LoginChallenge, LoginIdentity, Membership, RegistrationAttribution)

GOOGLE_FIXTURE = {**AUTH_FIXTURE,'AUTH_GOOGLE_ENABLED':True,'GOOGLE_BRAND_READY':True,
    'GOOGLE_CLIENT_ID':'nonproduction.apps.googleusercontent.com','GOOGLE_CLIENT_SECRET':'nonproduction',
    'GOOGLE_REDIRECT_URI':'https://sage.example.invalid/api/auth/google/callback'}


class GoogleFixture:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Private material exists only in memory and is never logged or written.
        key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.signer = crypt.RSASigner.from_string(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,serialization.NoEncryption()),key_id='local-fixture')
        cls.public = key.public_key().public_bytes(serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.csrf = self.client.get('/api/auth/session').json()['csrf_token']
        self.claim_changes = {}
        self.token_failure = False
        self.provider_delay = None
        self.network_calls = []
        self.token_network = patch('backend.google_auth.requests.post',side_effect=self.token_response)
        self.cert_network = patch('backend.google_auth.requests.Session.request',side_effect=self.cert_response)
        self.token_network.start();self.cert_network.start()
        self.addCleanup(self.token_network.stop);self.addCleanup(self.cert_network.stop)

    def post(self,path,data,client=None,csrf=None):
        return (client or self.client).post('/api/auth/'+path,json.dumps(data),content_type='application/json',
            HTTP_X_CSRFTOKEN=csrf or self.csrf)

    def token_response(self,url,**kwargs):
        self.assertEqual(url,TOKEN_URL)
        self.assertEqual(kwargs['timeout'],10);self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(kwargs['data']['grant_type'],'authorization_code')
        self.network_calls.append('token')
        if self.provider_delay:
            self.provider_delay()
        if self.token_failure:
            raise requests.Timeout('fixture')
        now = int(timezone.now().timestamp())
        claims = {'iss':'https://accounts.google.com','aud':GOOGLE_FIXTURE['GOOGLE_CLIENT_ID'],
            'iat':now-1,'exp':now+300,'sub':'local-google-sub','email':'new@example.invalid',
            'email_verified':True,'nonce':self.nonce,'given_name':'Google name','family_name':'Family'}
        claims.update(self.claim_changes)
        encoded = jwt.encode(self.signer,claims).decode()
        if self.claim_changes.get('unsigned'):
            encoded = encoded.rsplit('.',1)[0]+'.'
        if self.claim_changes.get('bad_signature'):
            body,signature = encoded.rsplit('.',1)
            encoded = body+'.'+('A' if signature[0]!='A' else 'B')+signature[1:]
        return Mock(status_code=200,json=Mock(return_value={'id_token':encoded,'access_token':'unused-fixture'}))

    def cert_response(self,method,url,**kwargs):
        self.assertEqual(method,'GET');self.assertEqual(url,CERTS_URL)
        self.assertEqual(kwargs['timeout'],10);self.assertFalse(kwargs['allow_redirects'])
        self.network_calls.append('cert')
        response = requests.Response();response.status_code=200
        response._content=json.dumps({'local-fixture':self.public}).encode()
        return response

    def begin(self,intent='login',return_to='/pages/profile.html',language='zh'):
        response = self.post('google/start',dict(intent=intent,return_to=return_to,language=language))
        self.assertEqual(response.status_code,201)
        query = parse_qs(urlsplit(response.json()['authorization_url']).query)
        self.assertEqual(query['scope'],['openid email profile'])
        self.assertEqual(query['prompt'],['select_account'])
        self.state,self.nonce = query['state'][0],query['nonce'][0]
        return AuthFlow.objects.get(pk=self.client.session['google_flow_id'])

    def callback(self,**query):
        values={'state':self.state,'code':'local-one-time-code',**query}
        response = self.client.get('/api/auth/google/callback?'+urlencode(values))
        self.assertEqual(response.status_code,303)
        self.assertEqual(response['Referrer-Policy'],'no-referrer')
        self.assertEqual(response['Cache-Control'],'no-store')
        self.assertNotIn('local-one-time-code',response['Location']);self.assertNotIn(self.state,response['Location'])
        self.csrf=self.client.get('/api/auth/session').json()['csrf_token']
        return response

    def pending(self,**claims):
        self.claim_changes.update(claims)
        self.begin();self.callback()
        response = self.post('google/resume',{})
        self.assertEqual(response.status_code,200)
        self.assertNotIn('sub',response.json());self.assertNotIn('local-google-sub',response.content.decode())
        return response.json()

    def original(self,password='Old historical password with spaces'):
        user=get_user_model().objects.create_user(username='original',first_name='Original',password=password)
        identity=LoginIdentity.objects.create(user=user,method='email',identifier='new@example.invalid')
        profile=AccountProfile.objects.create(user=user,product_updates=False,agreement_version='original-terms')
        account=BillingAccount.objects.create(kind='personal',name='Original account',personal_owner=user)
        Membership.objects.create(user=user,account=account,role='owner')
        return user,identity,profile,account

    def registration(self,flow,**fields):
        response=self.post('google/register',{'flow_id':flow['flow_id'],'first_name':'Chosen','last_name':'',
            'agreement_acceptance':GOOGLE_FIXTURE['AUTH_REGISTRATION_TERMS_VERSION'],'invitation_code':'',**fields})
        if response.status_code==200:self.csrf=response.json()['session']['csrf_token']
        return response


@override_settings(**GOOGLE_FIXTURE)
class GoogleAuthenticationTests(GoogleFixture,TestCase):
    def test_r1_google_only_display_email_does_not_grant_profile_password_reset(self):
        flow=self.pending(email='assertion@example.invalid')
        self.assertEqual(self.registration(flow).status_code,200)
        self.assertFalse(LoginIdentity.objects.filter(method='email').exists())
        self.assertEqual(self.client.get('/api/account/profile').json()['email'],'assertion@example.invalid')
        capabilities=self.client.get('/api/auth/session').json()['capabilities']
        self.assertFalse(capabilities['password_reset'])
        self.assertTrue(capabilities['password_managed_by_google'])
        anonymous=Client().get('/api/auth/session').json()['capabilities']
        self.assertTrue(anonymous['password_reset'])
        self.assertFalse(anonymous['password_managed_by_google'])

    def test_r1_original_email_and_bound_google_keep_actual_reset_qualification(self):
        user,_,_,_=self.original()
        self.client.force_login(user)
        current=self.client.get('/api/auth/session').json()['capabilities']
        self.assertTrue(current['password_reset']);self.assertFalse(current['password_managed_by_google'])
        LoginIdentity.objects.create(user=user,method='google',identifier='local-google-sub',provider_email='assertion@example.invalid')
        self.assertTrue(self.client.get('/api/auth/session').json()['capabilities']['password_reset'])
        with override_settings(AUTH_EMAIL_ENABLED=False):
            self.assertFalse(self.client.get('/api/auth/session').json()['capabilities']['password_reset'])

    def test_r1_hash_failure_retains_committed_attempt_and_quota(self):
        self.original();flow=self.pending()
        counts=dict(AuthThrottle.objects.values_list('key','count'))
        with patch('django.contrib.auth.hashers.verify_password',side_effect=DatabaseError('nonproduction fault')):
            response=self.post('google/bind-password',{'flow_id':flow['flow_id'],'password':'fixture input'})
        self.assertEqual(response.status_code,503)
        row=AuthFlow.objects.get(pk=flow['flow_id'])
        self.assertEqual(row.attempts,1);self.assertEqual(row.stage,'bind_proof')
        self.assertEqual(sum(count-counts.get(key,0) for key,count in AuthThrottle.objects.values_list('key','count')),2)
        self.assertFalse(AccountSession.objects.exists())

    def test_r1_original_hash_is_verified_without_unlocked_upgrade_write(self):
        user,_,_,_=self.original()
        old=PBKDF2PasswordHasher().encode('Old historical password with spaces','fixture-salt',iterations=1)
        get_user_model().objects.filter(pk=user.pk).update(password=old)
        flow=self.pending()
        response=self.post('google/bind-password',{'flow_id':flow['flow_id'],'password':'Old historical password with spaces'})
        self.assertEqual(response.json()['stage'],'bind_confirm')
        user.refresh_from_db();self.assertEqual(user.password,old)
        self.assertFalse(AccountSession.objects.exists())

    def test_r1_authenticated_resume_receipt_can_be_read_after_lost_response(self):
        user,_,_,_=self.original()
        LoginIdentity.objects.create(user=user,method='google',identifier='local-google-sub',provider_email='new@example.invalid')
        self.begin(return_to='/pages/profile.html?tab=security');self.callback()
        first=self.post('google/resume',{})
        audits=AuditEvent.objects.count();sessions=AccountSession.objects.count()
        second=self.post('google/resume',{})
        self.assertEqual(first.status_code,200);self.assertEqual(second.status_code,200)
        self.assertEqual(second.json()['session']['user']['id'],str(user.pk))
        self.assertEqual(second.json()['return_to'],'/pages/profile.html?tab=security')
        self.assertEqual(AuditEvent.objects.count(),audits);self.assertEqual(AccountSession.objects.count(),sessions)

    def test_r1_bound_callback_database_fault_has_no_ghost_session(self):
        user,_,_,_=self.original()
        LoginIdentity.objects.create(user=user,method='google',identifier='local-google-sub',provider_email='new@example.invalid')
        self.begin()
        with patch('backend.google_auth.AuditEvent.objects.create',side_effect=DatabaseError('nonproduction fault')):
            self.callback()
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        self.assertFalse(AccountSession.objects.exists())

    def test_r1_resume_receipt_expires_and_cannot_cross_session_user_or_logout(self):
        user,_,_,_=self.original()
        LoginIdentity.objects.create(user=user,method='google',identifier='local-google-sub',provider_email='new@example.invalid')
        self.begin();self.callback()
        other=Client();other.force_login(user)
        other_csrf=other.get('/api/auth/session').json()['csrf_token']
        self.assertEqual(self.post('google/resume',{},other,other_csrf).status_code,422)
        stored=self.client.session;receipt=stored['google_result']
        self.assertEqual(set(receipt),{'user_id','return_to','expires_at'})
        receipt['return_to']='//evil.invalid';stored['google_result']=receipt;stored.save()
        self.assertEqual(self.post('google/resume',{}).json()['return_to'],'/index.html')
        stored=self.client.session;receipt=stored['google_result']
        receipt['expires_at']=timezone.now().timestamp()-1;stored['google_result']=receipt;stored.save()
        self.assertEqual(self.post('google/resume',{}).status_code,422)
        self.assertNotIn('google_result',self.client.session)
        self.assertTrue(self.client.get('/api/auth/session').json()['authenticated'])
        stored=self.client.session;stored['google_result']={'user_id':'other-user','return_to':'//evil.invalid',
            'expires_at':timezone.now().timestamp()+300};stored.save()
        self.assertEqual(self.post('google/resume',{}).status_code,422)
        self.client.logout();self.csrf=self.client.get('/api/auth/session').json()['csrf_token']
        self.assertEqual(self.post('google/resume',{}).status_code,422)

    def test_r1_pending_resume_rejects_consumed_expired_and_known_cancelled(self):
        for outcome in ('expired','consumed','cancelled'):
            with self.subTest(outcome=outcome):
                flow=self.pending()
                if outcome=='expired':AuthFlow.objects.filter(pk=flow['flow_id']).update(expires_at=timezone.now())
                elif outcome=='consumed':self.post('cancel',{'flow_id':flow['flow_id']})
                else:
                    self.begin();self.client.get('/api/auth/google/callback',{'state':self.state,'error':'access_denied'})
                response=self.post('google/resume',{})
                self.assertEqual(response.status_code,422)
                self.assertEqual(response.json()['error']['code'],'GOOGLE_CANCELLED' if outcome=='cancelled' else 'INVALID_FLOW')
                self.assertFalse(get_user_model().objects.exists());self.assertFalse(AccountSession.objects.exists())

    def test_new_google_registration_has_one_account_terms_invite_and_no_email_password_identity(self):
        code=InvitationCode.objects.create(code='BLOGGER7',blogger='Fixture blogger')
        with override_settings(INVITATION_CODES_READY=True):
            flow=self.pending()
            self.assertEqual(flow['stage'],'register');self.assertFalse(get_user_model().objects.exists())
            response=self.registration(flow,invitation_code=code.pk)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['return_to'],'/index.html')
        user=get_user_model().objects.get();self.assertFalse(user.has_usable_password())
        self.assertEqual(user.first_name,'Chosen')
        identity=LoginIdentity.objects.get();self.assertEqual(identity.method,'google')
        self.assertEqual(identity.provider_email,'new@example.invalid')
        profile=AccountProfile.objects.get();self.assertIsNotNone(profile.agreement_accepted_at)
        self.assertEqual(profile.agreement_version,GOOGLE_FIXTURE['AUTH_REGISTRATION_TERMS_VERSION'])
        self.assertEqual(BillingAccount.objects.count(),1);self.assertEqual(Membership.objects.count(),1)
        self.assertEqual(RegistrationAttribution.objects.get().invitation_code,code)
        self.assertEqual(self.registration(flow).status_code,422)
        self.assertEqual(get_user_model().objects.count(),1)
        self.assertNotIn('local-google-sub',self.client.get('/api/auth/session').content.decode())

    def test_bound_sub_register_intent_signs_in_original_without_legal_or_delivery_gate(self):
        user,_,profile,account=self.original()
        LoginIdentity.objects.create(user=user,method='google',identifier='local-google-sub',provider_email='new@example.invalid')
        with override_settings(AUTH_CAPTCHA_ENABLED=False,AUTH_EMAIL_ENABLED=False,AUTH_REGISTRATION_TERMS_READY=False):
            self.begin(intent='register');self.callback()
        result=self.post('google/resume',{}).json()
        self.assertEqual(result['session']['user']['id'],str(user.pk));self.assertEqual(result['return_to'],'/pages/profile.html')
        self.assertEqual(get_user_model().objects.count(),1);self.assertEqual(BillingAccount.objects.get(),account)
        profile.refresh_from_db();self.assertEqual(profile.agreement_version,'original-terms')

    def test_same_email_needs_actual_original_password_and_explicit_bind(self):
        user,identity,profile,account=self.original()
        flow=self.pending();self.assertEqual(flow['stage'],'bind_proof')
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        self.assertEqual(self.post('google/bind',{'flow_id':flow['flow_id'],'confirm':True}).status_code,422)
        self.assertEqual(self.post('google/bind-password',{'flow_id':flow['flow_id'],'password':'wrong'}).status_code,401)
        response=self.post('google/bind-password',{'flow_id':flow['flow_id'],'password':'Old historical password with spaces'})
        self.assertEqual(response.json()['stage'],'bind_confirm')
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        self.assertEqual(LoginIdentity.objects.count(),1)
        self.assertEqual(self.post('google/bind',{'flow_id':flow['flow_id'],'confirm':False}).status_code,422)
        response=self.post('google/bind',{'flow_id':flow['flow_id'],'confirm':True})
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['session']['user']['id'],str(user.pk))
        user.refresh_from_db();profile.refresh_from_db()
        self.assertEqual(user.first_name,'Original');self.assertEqual(profile.agreement_version,'original-terms')
        self.assertEqual(LoginIdentity.objects.get(pk=identity.pk).user,user);self.assertEqual(BillingAccount.objects.get(),account)
        self.assertFalse(RegistrationAttribution.objects.exists())

    def test_original_verified_email_otp_only_grants_explicit_bind_stage(self):
        user,_,_,_=self.original(password=None)
        flow=self.pending()
        code=self.post('google/bind-code/start',{'flow_id':flow['flow_id']})
        self.assertEqual(code.status_code,201)
        item=LoginChallenge.objects.get();self.assertEqual(item.purpose,'google_bind')
        otp=re.search(r'\b\d{6}\b',mail.outbox[-1].body).group()
        self.assertEqual(self.post('recovery/verify',{'flow_id':flow['flow_id'],'challenge_id':str(item.pk),'code':otp}).status_code,422)
        response=self.post('google/bind-code/verify',{'flow_id':flow['flow_id'],'challenge_id':str(item.pk),'code':otp})
        self.assertEqual(response.json()['stage'],'bind_confirm')
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        self.assertEqual(self.post('google/bind',{'flow_id':flow['flow_id'],'confirm':True}).status_code,200)
        user.refresh_from_db();self.assertFalse(user.has_usable_password())

    def test_bind_code_exhaustion_rejects_even_correct_code(self):
        self.original(password=None);flow=self.pending()
        sent=self.post('google/bind-code/start',{'flow_id':flow['flow_id']})
        self.assertEqual(sent.status_code,201)
        item=LoginChallenge.objects.get()
        otp=re.search(r'\b\d{6}\b',mail.outbox[-1].body).group()
        body={'flow_id':flow['flow_id'],'challenge_id':str(item.pk),'code':'000000' if otp!='000000' else '111111'}
        for _ in range(5):
            response=self.post('google/bind-code/verify',body)
            self.assertEqual(response.status_code,422)
            self.assertEqual(response.json()['error']['code'],'INVALID_CODE')
        response=self.post('google/bind-code/verify',{**body,'code':otp})
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()['error']['code'],'INVALID_CODE')
        item.refresh_from_db();self.assertEqual(item.attempts,5);self.assertIsNone(item.consumed_at)
        self.assertEqual(AuthFlow.objects.get(pk=flow['flow_id']).stage,'bind_code')
        self.assertFalse(LoginIdentity.objects.filter(method='google').exists())
        self.assertFalse(AccountSession.objects.exists())

    def test_bind_code_resend_invalidates_old_challenge(self):
        self.original(password=None);flow=self.pending()
        self.assertEqual(self.post('google/bind-code/start',{'flow_id':flow['flow_id']}).status_code,201)
        old=LoginChallenge.objects.get()
        old_code=re.search(r'\b\d{6}\b',mail.outbox[-1].body).group()
        with override_settings(AUTH_RESEND_SECONDS=0):
            sent=self.post('google/bind-code/start',{'flow_id':flow['flow_id']})
        self.assertEqual(sent.status_code,201)
        current=LoginChallenge.objects.get(pk=sent.json()['challenge_id'])
        current_code=re.search(r'\b\d{6}\b',mail.outbox[-1].body).group()
        response=self.post('google/bind-code/verify',{'flow_id':flow['flow_id'],'challenge_id':str(old.pk),'code':old_code})
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()['error']['code'],'INVALID_CODE')
        old.refresh_from_db();self.assertIsNotNone(old.consumed_at);self.assertEqual(old.attempts,0)
        current.refresh_from_db();self.assertEqual(current.attempts,0);self.assertIsNone(current.consumed_at)
        response=self.post('google/bind-code/verify',{'flow_id':flow['flow_id'],'challenge_id':str(current.pk),'code':current_code})
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['stage'],'bind_confirm')
        self.assertFalse(LoginIdentity.objects.filter(method='google').exists())
        self.assertFalse(AccountSession.objects.exists())

    def test_bind_code_expired_challenge_cannot_prove_original_identity(self):
        self.original(password=None);flow=self.pending()
        self.assertEqual(self.post('google/bind-code/start',{'flow_id':flow['flow_id']}).status_code,201)
        item=LoginChallenge.objects.get()
        otp=re.search(r'\b\d{6}\b',mail.outbox[-1].body).group()
        LoginChallenge.objects.filter(pk=item.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        response=self.post('google/bind-code/verify',{'flow_id':flow['flow_id'],'challenge_id':str(item.pk),'code':otp})
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()['error']['code'],'INVALID_CODE')
        item.refresh_from_db();self.assertEqual(item.attempts,0);self.assertIsNone(item.consumed_at)
        self.assertEqual(AuthFlow.objects.get(pk=flow['flow_id']).stage,'bind_code')
        self.assertFalse(LoginIdentity.objects.filter(method='google').exists())
        self.assertFalse(AccountSession.objects.exists())

    def test_bind_code_undelivered_challenge_cannot_prove_original_identity(self):
        self.original(password=None);flow=self.pending()
        with patch('backend.google_auth.send_mail',return_value=0),patch('backend.google_auth.secrets.randbelow',return_value=123456):
            sent=self.post('google/bind-code/start',{'flow_id':flow['flow_id']})
        self.assertEqual(sent.status_code,503)
        self.assertEqual(sent.json()['error']['code'],'DELIVERY_FAILED')
        item=LoginChallenge.objects.get();self.assertFalse(item.delivered)
        response=self.post('google/bind-code/verify',{'flow_id':flow['flow_id'],'challenge_id':str(item.pk),'code':'123456'})
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()['error']['code'],'INVALID_CODE')
        item.refresh_from_db();self.assertEqual(item.attempts,0);self.assertIsNone(item.consumed_at)
        self.assertEqual(AuthFlow.objects.get(pk=flow['flow_id']).stage,'bind_code')
        self.assertFalse(LoginIdentity.objects.filter(method='google').exists())
        self.assertFalse(AccountSession.objects.exists())

    def test_bind_code_other_browser_cannot_prove_or_spend_original_challenge(self):
        self.original(password=None);flow=self.pending()
        self.assertEqual(self.post('google/bind-code/start',{'flow_id':flow['flow_id']}).status_code,201)
        item=LoginChallenge.objects.get()
        body={'flow_id':flow['flow_id'],'challenge_id':str(item.pk),'code':re.search(r'\b\d{6}\b',mail.outbox[-1].body).group()}
        other=Client(enforce_csrf_checks=True);other_csrf=other.get('/api/auth/session').json()['csrf_token']
        response=self.post('google/bind-code/verify',body,other,other_csrf)
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()['error']['code'],'INVALID_FLOW')
        item.refresh_from_db();self.assertEqual(item.attempts,0);self.assertIsNone(item.consumed_at)
        self.assertEqual(AuthFlow.objects.get(pk=flow['flow_id']).stage,'bind_code')
        self.assertFalse(other.get('/api/auth/session').json()['authenticated'])
        self.assertFalse(LoginIdentity.objects.filter(method='google').exists())
        response=self.post('google/bind-code/verify',body)
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['stage'],'bind_confirm')
        item.refresh_from_db();self.assertEqual(item.attempts,1);self.assertIsNotNone(item.consumed_at)
        self.assertFalse(AccountSession.objects.exists())

    def test_bind_code_successful_verify_cannot_be_replayed(self):
        self.original(password=None);flow=self.pending()
        self.assertEqual(self.post('google/bind-code/start',{'flow_id':flow['flow_id']}).status_code,201)
        item=LoginChallenge.objects.get()
        body={'flow_id':flow['flow_id'],'challenge_id':str(item.pk),'code':re.search(r'\b\d{6}\b',mail.outbox[-1].body).group()}
        response=self.post('google/bind-code/verify',body)
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['stage'],'bind_confirm')
        item.refresh_from_db();consumed_at=item.consumed_at
        self.assertIsNotNone(consumed_at);self.assertEqual(item.attempts,1)
        response=self.post('google/bind-code/verify',body)
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()['error']['code'],'INVALID_FLOW')
        item.refresh_from_db();self.assertEqual(item.attempts,1);self.assertEqual(item.consumed_at,consumed_at)
        self.assertEqual(AuthFlow.objects.get(pk=flow['flow_id']).stage,'bind_confirm')
        self.assertFalse(LoginIdentity.objects.filter(method='google').exists())
        self.assertFalse(AccountSession.objects.exists())

    def test_real_verifier_rejects_signature_audience_issuer_expiry_nonce_sub_email_claims(self):
        for changes in ({'unsigned':True},{'bad_signature':True},{'aud':'another-client'},{'iss':'https://evil.invalid'},
                {'exp':1},{'nonce':'different'},{'sub':''},{'sub':None},{'email_verified':False},
                {'email_verified':'true'},{'email':'bad'},{'iat':int(timezone.now().timestamp())+3600}):
            with self.subTest(claims=list(changes)):
                self.claim_changes=changes;self.begin();self.callback()
                self.assertEqual(self.post('google/resume',{}).status_code,422)
                self.assertFalse(get_user_model().objects.exists())
                self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])
        self.assertIn('cert',self.network_calls)

    def test_provider_metadata_ignored_but_duplicate_critical_fields_rejected(self):
        self.begin();self.callback(scope='openid email profile',authuser='0',unknown='ignored')
        self.assertEqual(self.post('google/resume',{}).json()['stage'],'register')
        self.begin();response=self.client.get('/api/auth/google/callback?'+urlencode(
            [('state',self.state),('state',self.state),('code','fixture')]))
        self.assertEqual(response.status_code,303)
        self.assertEqual(self.post('google/resume',{}).status_code,422)

    def test_callback_cross_session_mismatched_state_and_replay_do_not_exchange(self):
        self.begin();other=Client();other.get('/api/auth/session')
        other.get('/api/auth/google/callback',{'state':self.state,'code':'fixture'})
        self.assertEqual(self.network_calls,[])
        self.callback(state='wrong');self.assertEqual(self.network_calls,[])
        self.callback();count=len(self.network_calls)
        self.callback();self.assertEqual(len(self.network_calls),count)
        self.assertFalse(get_user_model().objects.exists())

    def test_cancel_network_failure_and_missing_config_have_no_account_or_session(self):
        flow=self.begin();response=self.client.get('/api/auth/google/callback',{'state':self.state,'error':'access_denied','error_description':'never echo'})
        self.assertEqual(response.status_code,303);self.assertEqual(self.network_calls,[])
        flow.refresh_from_db();self.assertIsNotNone(flow.consumed_at);self.assertEqual(flow.google_nonce_digest,'')
        self.assertEqual(self.post('google/resume',{}).json()['error']['code'],'GOOGLE_CANCELLED')
        self.begin();self.token_failure=True;self.callback()
        self.assertEqual(self.post('google/resume',{}).status_code,503)
        with override_settings(GOOGLE_CLIENT_SECRET=''):
            self.assertFalse(self.client.get('/api/auth/session').json()['capabilities']['google_login'])
            self.assertEqual(self.post('google/start',dict(intent='login',return_to='/',language='zh')).status_code,503)
        self.assertFalse(get_user_model().objects.exists());self.assertFalse(AccountSession.objects.exists())

    def test_callback_claim_cancel_during_network_cannot_restore(self):
        flow=self.begin()
        def cancel():
            row=AuthFlow.objects.get(pk=flow.pk)
            self.assertEqual(row.stage,'exchange');self.assertTrue(row.google_claim)
            self.assertEqual(self.post('cancel',{'flow_id':str(flow.pk)}).status_code,200)
        self.provider_delay=cancel;self.callback()
        self.assertEqual(self.post('google/resume',{}).status_code,422)
        self.assertFalse(get_user_model().objects.exists())

    def test_token_exchange_wait_expiry_invalidates_without_account(self):
        flow=self.begin()
        self.provider_delay=lambda:AuthFlow.objects.filter(pk=flow.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.callback();self.assertEqual(self.post('google/resume',{}).status_code,422)
        self.assertFalse(get_user_model().objects.exists())

    def test_google_only_provider_email_never_becomes_original_recovery_identity(self):
        flow=self.pending(email='third-party@example.invalid');self.registration(flow)
        self.assertFalse(LoginIdentity.objects.filter(method='email').exists())
        self.post('logout',{});self.csrf=self.client.get('/api/auth/session').json()['csrf_token']
        response=self.post('recovery/start',{'identifier':'third-party@example.invalid'})
        recovery=AuthFlow.objects.get(pk=response.json()['flow_id']);self.assertIsNone(recovery.user_id)
        self.assertFalse(LoginIdentity.objects.filter(method='email').exists())

    def test_stale_original_disabled_conflict_and_changed_password_cannot_bind(self):
        user,identity,_,_=self.original();flow=self.pending()
        LoginIdentity.objects.filter(pk=identity.pk).update(identifier='changed@example.invalid')
        self.assertEqual(self.post('google/bind-password',{'flow_id':flow['flow_id'],'password':'Old historical password with spaces'}).status_code,422)
        LoginIdentity.objects.filter(pk=identity.pk).update(identifier='new@example.invalid')
        response=self.post('google/bind-password',{'flow_id':flow['flow_id'],'password':'Old historical password with spaces'})
        self.assertEqual(response.status_code,200)
        user.set_password('Another historical password');user.save()
        self.assertEqual(self.post('google/bind',{'flow_id':flow['flow_id'],'confirm':True}).status_code,422)
        other=get_user_model().objects.create_user(username='other')
        LoginIdentity.objects.create(user=other,method='google',identifier='local-google-sub')
        self.assertEqual(self.post('google/bind',{'flow_id':flow['flow_id'],'confirm':True}).status_code,409)
        self.assertEqual(get_user_model().objects.count(),2);self.assertFalse(AccountSession.objects.exists())

    def test_bound_inactive_user_is_not_restored(self):
        user,_,_,_=self.original();user.is_active=False;user.save()
        LoginIdentity.objects.create(user=user,method='google',identifier='local-google-sub')
        self.begin();self.callback();self.assertEqual(self.post('google/resume',{}).status_code,422)
        user.refresh_from_db();self.assertFalse(user.is_active);self.assertFalse(AccountSession.objects.exists())

    def test_terms_unchecked_invitation_disabled_and_database_failure_leave_pending_no_rewards(self):
        InvitationCode.objects.create(code='BLOGGER7',blogger='Fixture',is_active=False)
        flow=self.pending()
        self.assertEqual(self.registration(flow,agreement_acceptance=False).status_code,422)
        with override_settings(INVITATION_CODES_READY=True):
            response=self.registration(flow,invitation_code='BLOGGER7')
            self.assertEqual(response.status_code,422);self.assertEqual(response.json()['error']['message'],'无效邀请码，请检查')
        with patch('backend.google_auth.Membership.objects.create',side_effect=DatabaseError('fixture')):
            self.assertEqual(self.registration(flow).status_code,503)
        self.assertFalse(get_user_model().objects.exists());self.assertFalse(BillingAccount.objects.exists())
        self.assertEqual(self.registration(flow).status_code,200)

    def test_final_audit_expiry_rolls_back_login_and_user_writes(self):
        flow=self.pending();original=AuditEvent.objects.create;now=timezone.now
        expired=[False];future=now()+timedelta(seconds=600)
        def late(*args,**kwargs):
            result=original(*args,**kwargs)
            expired[0]=True
            return result
        with patch('backend.google_auth.AuditEvent.objects.create',side_effect=late),patch(
                'backend.google_auth.timezone.now',side_effect=lambda:future if expired[0] else now()):
            self.assertEqual(self.registration(flow).status_code,422)
        self.assertFalse(get_user_model().objects.exists());self.assertFalse(BillingAccount.objects.exists())
        self.assertFalse(self.client.get('/api/auth/session').json()['authenticated'])

    def test_scrub_cancel_cleanup_and_user_bound_deletion_without_other_email_flow(self):
        user,identity,_,_=self.original();flow=self.pending()
        row=AuthFlow.objects.get(pk=flow['flow_id'])
        self.assertEqual(self.post('cancel',{'flow_id':flow['flow_id']}).status_code,200)
        row.refresh_from_db()
        for field in ('google_sub','google_claim','google_nonce_digest','first_name','last_name','identifier','password_hash'):
            self.assertEqual(getattr(row,field),'')
        self.assertIsNone(row.original_identity_id)
        row=AuthFlow.objects.create(purpose='google',stage='bind_proof',identifier='another-asserted@example.invalid',
            google_sub='another-sub',user=user,session_digest='fixture',expires_at=timezone.now()+timedelta(seconds=300))
        unrelated=AuthFlow.objects.create(purpose='google',stage='register',identifier=identity.identifier,
            google_sub='unrelated-sub',session_digest='other',expires_at=timezone.now()+timedelta(seconds=300))
        from .account import _erase_account
        from .models import AccountDeletionRequest
        request=AccountDeletionRequest.objects.create(user=user,policy_version='fixture',status='requested',
            first_response_due_at=timezone.now(),completion_due_at=timezone.now(),retain_until=timezone.now())
        with override_settings(ACCOUNT_DELETION_RETENTION_DAYS=1):_erase_account(user,request)
        self.assertFalse(AuthFlow.objects.filter(pk=row.pk).exists())
        self.assertTrue(AuthFlow.objects.filter(pk=unrelated.pk).exists())

    def test_rate_attempts_strict_fields_csrf_and_safe_return(self):
        self.original();flow=self.pending()
        for _ in range(5):self.assertEqual(self.post('google/bind-password',{'flow_id':flow['flow_id'],'password':'wrong'}).status_code,401)
        self.assertEqual(self.post('google/bind-password',{'flow_id':flow['flow_id'],'password':'wrong'}).status_code,429)
        self.assertEqual(AuthFlow.objects.get(pk=flow['flow_id']).attempts,5)
        self.assertEqual(self.post('google/resume',{'token':'extra'}).status_code,422)
        self.assertEqual(self.client.post('/api/auth/google/resume','{}',content_type='application/json').status_code,403)
        for bad in ('https://evil.invalid','//evil.invalid','/pages/profile.html\\x','/not-approved','/pages/profile.html?email=private'):
            self.assertEqual(safe_return(bad),'/index.html')
        self.assertEqual(safe_return('/pages/pricing.html?plan=pro'),'/pages/pricing.html?plan=pro')

    def test_callback_logs_and_wsgi_environ_redact_queries(self):
        from .http import OAuthQueryFilter
        record=logging.LogRecord('django.server',logging.INFO,'fixture',1,'"%s" %s %s',
            ('GET /api/auth/google/callback?code=secret&state=secret HTTP/1.1','303','0'),None)
        OAuthQueryFilter().filter(record);self.assertNotIn('secret',record.getMessage())
        atoms={'r':'GET /api/auth/google/callback?code=secret HTTP/1.1','q':'code=secret'}
        record=logging.LogRecord('gunicorn.access',logging.INFO,'fixture',1,'%(r)s %(q)s',atoms,None)
        OAuthQueryFilter().filter(record);self.assertNotIn('secret',record.getMessage())
        self.begin();response=self.callback()
        self.assertEqual(response.wsgi_request.META['QUERY_STRING'],'')
        self.assertEqual(response.content,b'')


@override_settings(**GOOGLE_FIXTURE)
@skipUnlessDBFeature('has_select_for_update')
class GoogleConcurrencyTests(GoogleFixture,TransactionTestCase):
    def worker(self,action):
        close_old_connections()
        try:
            with connection.cursor() as cursor:cursor.execute("SET lock_timeout = '5s'")
            return action()
        finally:connections['default'].close()

    def test_r1_attempt_commits_before_hash_and_concurrent_limit_does_not_hash(self):
        self.original();flow=self.pending();AuthFlow.objects.filter(pk=flow['flow_id']).update(attempts=4)
        entered,release=Event(),Event();hashed=[]
        def pause(password,encoded,**kwargs):
            self.assertFalse(connection.in_atomic_block)
            hashed.append(True);entered.set();self.assertTrue(release.wait(timeout=5))
            return verify_password(password,encoded,**kwargs)
        body={'flow_id':flow['flow_id'],'password':'Old historical password with spaces'}
        with patch('django.contrib.auth.hashers.verify_password',side_effect=pause),ThreadPoolExecutor(max_workers=1) as pool:
            job=pool.submit(self.worker,lambda:self.post('google/bind-password',body).status_code)
            try:
                self.assertTrue(entered.wait(timeout=5))
                self.assertEqual(AuthFlow.objects.get(pk=flow['flow_id']).attempts,5)
                self.assertEqual(self.post('google/bind-password',body).status_code,429)
                self.assertEqual(len(hashed),1)
            finally:release.set()
            self.assertEqual(job.result(timeout=10),200)
        self.assertFalse(AccountSession.objects.exists())

    def test_r1_durable_hash_fault_and_concurrent_proof_invalidations_never_bind(self):
        user,identity,_,_=self.original();original_password=user.password
        for outcome in ('hash_fault','cancel','expiry','disabled','password','identity','session'):
            with self.subTest(outcome=outcome):
                # Independent races must not share the default five-attempt bucket.
                AuthThrottle.objects.all().delete()
                get_user_model().objects.filter(pk=user.pk).update(is_active=True,password=original_password)
                LoginIdentity.objects.filter(pk=identity.pk).update(identifier='new@example.invalid')
                flow=self.pending();entered,release=Event(),Event()
                def pause(password,encoded,**kwargs):
                    self.assertFalse(connection.in_atomic_block)
                    entered.set();self.assertTrue(release.wait(timeout=5))
                    if outcome=='hash_fault':raise DatabaseError('nonproduction fault')
                    return verify_password(password,encoded,**kwargs)
                with patch('django.contrib.auth.hashers.verify_password',side_effect=pause),ThreadPoolExecutor(max_workers=1) as pool:
                    job=pool.submit(self.worker,lambda:self.post('google/bind-password',{
                        'flow_id':flow['flow_id'],'password':'Old historical password with spaces'}).status_code)
                    try:
                        self.assertTrue(entered.wait(timeout=5))
                        self.assertEqual(AuthFlow.objects.get(pk=flow['flow_id']).attempts,1)
                        if outcome=='cancel':self.assertEqual(self.post('cancel',{'flow_id':flow['flow_id']}).status_code,200)
                        elif outcome=='expiry':AuthFlow.objects.filter(pk=flow['flow_id']).update(expires_at=timezone.now())
                        elif outcome=='disabled':get_user_model().objects.filter(pk=user.pk).update(is_active=False)
                        elif outcome=='password':get_user_model().objects.filter(pk=user.pk).update(password='!changed-fixture')
                        elif outcome=='identity':LoginIdentity.objects.filter(pk=identity.pk).update(identifier='changed@example.invalid')
                        elif outcome=='session':AuthFlow.objects.filter(pk=flow['flow_id']).update(session_digest='changed-session')
                    finally:release.set()
                    self.assertEqual(job.result(timeout=10),503 if outcome=='hash_fault' else 422)
                row=AuthFlow.objects.get(pk=flow['flow_id']);self.assertEqual(row.attempts,1)
                self.assertNotEqual(row.stage,'bind_confirm')
                self.assertFalse(AccountSession.objects.exists());self.assertFalse(LoginIdentity.objects.filter(method='google').exists())

    def test_two_browsers_same_sub_register_exactly_one_account(self):
        first=self.pending();client1,csrf1=self.client,self.csrf
        self.client=Client(enforce_csrf_checks=True);self.csrf=self.client.get('/api/auth/session').json()['csrf_token']
        second=self.pending();client2,csrf2=self.client,self.csrf
        gate=Barrier(2)
        def create(client,csrf,flow):
            gate.wait(timeout=5)
            return self.post('google/register',dict(flow_id=flow['flow_id'],first_name='Fixture',last_name='',
                agreement_acceptance=GOOGLE_FIXTURE['AUTH_REGISTRATION_TERMS_VERSION'],invitation_code=''),client,csrf).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs=[pool.submit(self.worker,lambda c=c,t=t,f=f:create(c,t,f)) for c,t,f in ((client1,csrf1,first),(client2,csrf2,second))]
            statuses=sorted(job.result(timeout=10) for job in jobs)
        self.assertEqual(statuses,[200,409]);self.assertEqual(get_user_model().objects.count(),1)
        self.assertEqual(BillingAccount.objects.count(),1);self.assertEqual(Membership.objects.count(),1)
        self.assertEqual(LoginIdentity.objects.count(),1)

    def test_two_browsers_same_sub_bind_preserves_original_and_has_one_winner(self):
        user,identity,profile,account=self.original()
        first=self.pending();self.post('google/bind-password',dict(flow_id=first['flow_id'],password='Old historical password with spaces'))
        client1,csrf1=self.client,self.csrf
        self.client=Client(enforce_csrf_checks=True);self.csrf=self.client.get('/api/auth/session').json()['csrf_token']
        second=self.pending();self.post('google/bind-password',dict(flow_id=second['flow_id'],password='Old historical password with spaces'))
        client2,csrf2=self.client,self.csrf;gate=Barrier(2)
        def bind(client,csrf,flow):
            gate.wait(timeout=5)
            return self.post('google/bind',dict(flow_id=flow['flow_id'],confirm=True),client,csrf).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs=[pool.submit(self.worker,lambda c=c,t=t,f=f:bind(c,t,f)) for c,t,f in ((client1,csrf1,first),(client2,csrf2,second))]
            statuses=sorted(job.result(timeout=10) for job in jobs)
        self.assertEqual(statuses,[200,409]);self.assertEqual(get_user_model().objects.count(),1)
        self.assertEqual(LoginIdentity.objects.filter(user=user,method='google').count(),1)
        self.assertEqual(BillingAccount.objects.get(),account);profile.refresh_from_db()
        self.assertEqual(profile.agreement_version,'original-terms');self.assertEqual(LoginIdentity.objects.get(pk=identity.pk).user,user)

    def test_callback_claim_really_commits_before_network_and_cancel_wins(self):
        flow=self.begin();entered=Event();release=Event()
        def pause():
            self.assertFalse(connection.in_atomic_block)
            entered.set();self.assertTrue(release.wait(timeout=5))
        self.provider_delay=pause
        with ThreadPoolExecutor(max_workers=1) as pool:
            job=pool.submit(self.worker,lambda:self.client.get('/api/auth/google/callback',dict(state=self.state,code='fixture')).status_code)
            try:
                self.assertTrue(entered.wait(timeout=5));flow.refresh_from_db()
                self.assertEqual(flow.stage,'exchange');self.assertTrue(flow.google_claim)
                self.assertIsNone(flow.google_state_digest)
                self.assertEqual(self.post('cancel',dict(flow_id=str(flow.pk))).status_code,200)
            finally:release.set()
            self.assertEqual(job.result(timeout=10),303)
        self.assertFalse(get_user_model().objects.exists());self.assertFalse(AccountSession.objects.exists())
        flow.refresh_from_db();self.assertEqual(flow.google_claim,'');self.assertIsNotNone(flow.consumed_at)

    def test_cleanup_skips_busy_google_flow_and_retains_challenge_then_scrubs(self):
        from .authentication import cleanup_auth_data
        now=timezone.now()-timedelta(seconds=1)
        item=LoginChallenge.objects.create(purpose='google_bind',method='email',identifier='fixture@example.invalid',
            session_digest='fixture',code_digest='a'*64,expires_at=now)
        flow=AuthFlow.objects.create(purpose='google',stage='bind_code',identifier=item.identifier,
            session_digest='fixture',google_sub='fixture-sub',google_claim='b'*64,
            google_nonce_digest='c'*64,google_state_digest='d'*64,challenge=item,expires_at=now)
        entered=Event();release=Event()
        def hold():
            with transaction.atomic():
                AuthFlow.objects.select_for_update().get(pk=flow.pk)
                entered.set();self.assertTrue(release.wait(timeout=5))
        with ThreadPoolExecutor(max_workers=1) as pool:
            job=pool.submit(self.worker,hold)
            try:
                self.assertTrue(entered.wait(timeout=5));self.assertEqual(cleanup_auth_data(apply=True),{'flows':0,'challenges':0})
                item.refresh_from_db();self.assertEqual(item.code_digest,'a'*64)
            finally:release.set()
            job.result(timeout=10)
        self.assertEqual(cleanup_auth_data(apply=True),{'flows':1,'challenges':1})
        flow.refresh_from_db();item.refresh_from_db()
        self.assertEqual(flow.google_sub,'');self.assertEqual(flow.google_claim,'');self.assertIsNone(flow.google_state_digest)
        self.assertEqual(item.code_digest,'');self.assertEqual(cleanup_auth_data(apply=True),{'flows':0,'challenges':0})
