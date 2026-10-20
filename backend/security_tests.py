"""Synthetic keys and identities only; standard WebAuthn verifier, no real delivery."""
import hashlib
import json
from datetime import datetime,timedelta
from io import StringIO
from unittest.mock import patch

import cbor2
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command,CommandError
from django.test import Client,TestCase,TransactionTestCase,override_settings,skipUnlessDBFeature
from django.utils import timezone
from webauthn.helpers import bytes_to_base64url

from .account import authorize_passkey_recovery,_business_due,_retention_due
from .models import (AccountPasskey,PasskeyChallenge,PasskeyRecovery,AccountSession,
    LoginIdentity,BillingAccount,Membership,LoginChallenge,InvoiceRequest,InvoiceAudit,
    AccountDeletionRequest,TrialAttempt,TrialIdentityMarker)

ORIGIN='https://sage.example.invalid'
RP='sage.example.invalid'
PHONE_SETTINGS=dict(AUTH_PHONE_ENABLED=True,SMS_TEMPLATE_APPROVED=True,
    SMS_SECRET_ID='fixture-id',SMS_SECRET_KEY='fixture-key',SMS_REGION='ap-guangzhou',
    SMS_SDK_APP_ID='1400000000',SMS_SIGN='Fixture',SMS_TEMPLATE_ID='123',
    SMS_ALLOWED_COUNTRY_CODES=('86',),SMS_REGISTRATION_TEMPLATE_ID='124',
    SMS_REGISTRATION_TEMPLATE_APPROVED=True,SMS_PASSWORD_TEMPLATE_ID='125',
    SMS_PASSWORD_TEMPLATE_APPROVED=True,SMS_PASSWORD_CHANGED_TEMPLATE_ID='126',
    SMS_PASSWORD_CHANGED_TEMPLATE_APPROVED=True,SMS_DELETION_TEMPLATE_ID='127',
    SMS_DELETION_TEMPLATE_APPROVED=True,SMS_BUSINESS_TEMPLATE_ID='128',
    SMS_BUSINESS_TEMPLATE_APPROVED=True,SMS_REAL_DELIVERY_VERIFIED=True)


@override_settings(**PHONE_SETTINGS,SMS_BINDING_TEMPLATE_ID='129',SMS_BINDING_TEMPLATE_APPROVED=True,
    AUTH_RESEND_SECONDS=0,AUTH_IDENTIFIER_LIMIT=20)
class PhoneBindingTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user(username='phone-binding-fixture',password='Fixture!82A')
        LoginIdentity.objects.create(user=self.user,method='email',identifier='binding@example.invalid')
        self.client.force_login(self.user);self.client.get('/api/account/profile')
        for target in ('socket.create_connection','socket.getaddrinfo','socket.socket.connect'):
            probe=patch(target,side_effect=AssertionError('NO_EXTERNAL_NETWORK'));probe.start();self.addCleanup(probe.stop)

    def post(self,path,data,client=None):
        return (client or self.client).post('/api/account/phone/'+path,json.dumps(data),content_type='application/json')

    def challenge(self,client=None,**fields):
        data={'phone':'+8613800000000','password':'Fixture!82A',**fields}
        with patch('backend.sms.send_code',return_value=1) as send:
            response=self.post('challenges',data,client)
        self.assertEqual(response.status_code,201,response.content)
        send.assert_called_once();self.assertEqual(send.call_args.kwargs,{'purpose':'phone_binding'})
        return response.json()['challenge_id'],send.call_args.args[1]

    def google_only(self):
        LoginIdentity.objects.filter(user=self.user).delete()
        LoginIdentity.objects.create(user=self.user,method='google',identifier='binding-google-fixture',
            provider_email='binding@example.invalid')
        self.user.set_unusable_password();self.user.save(update_fields=['password'])
        self.client.force_login(self.user);self.client.get('/api/account/profile')

    def test_email_password_and_dedicated_sms_bind_once_and_clear_all_sensitive_proofs(self):
        other=Client();other.force_login(self.user);other.get('/api/account/profile')
        AccountSession.objects.filter(user=self.user).update(sensitive_verified_at=timezone.now(),sensitive_method='phone')
        challenge,code=self.challenge()
        self.assertFalse(LoginIdentity.objects.filter(user=self.user,method='phone').exists())
        response=self.post('verify',{'challenge_id':challenge,'code':code})
        self.assertEqual(response.status_code,200,response.content);self.assertTrue(response.json()['bound'])
        self.assertEqual(LoginIdentity.objects.get(user=self.user,method='phone').identifier,'+8613800000000')
        self.assertFalse(AccountSession.objects.filter(user=self.user,sensitive_verified_at__isnull=False).exists())
        self.assertEqual(self.post('verify',{'challenge_id':challenge,'code':code}).status_code,422)
        self.assertFalse(self.client.get('/api/account/security').json()['phone_binding']['available'])
        with patch('backend.sms.send_code') as send:
            self.assertEqual(self.post('challenges',{'phone':'+8613900000000','password':'Fixture!82A'}).status_code,422)
            send.assert_not_called()

    def test_wrong_password_missing_purpose_and_unknown_provider_never_bind(self):
        with patch('backend.sms.send_code') as send:
            self.assertEqual(self.post('challenges',{'phone':'+8613800000000','password':'incorrect'}).status_code,401)
            send.assert_not_called()
        with override_settings(SMS_BINDING_TEMPLATE_APPROVED=False),patch('backend.sms.send_code') as send:
            self.assertFalse(self.client.get('/api/account/security').json()['phone_binding']['available'])
            self.assertEqual(self.post('challenges',{'phone':'+8613800000000','password':'Fixture!82A'}).status_code,503)
            send.assert_not_called()
        with patch('backend.sms.send_code',return_value=0):
            self.assertEqual(self.post('challenges',{'phone':'+8613800000000','password':'Fixture!82A'}).status_code,503)
        self.assertFalse(LoginChallenge.objects.get(purpose='phone_binding').delivered)
        self.assertFalse(LoginIdentity.objects.filter(method='phone').exists())

    def test_occupied_phone_does_not_merge_users_or_change_password(self):
        other=get_user_model().objects.create_user(username='occupied-phone-fixture',password='Other!82A')
        LoginIdentity.objects.create(user=other,method='phone',identifier='+8613800000000')
        old=self.user.password;challenge,code=self.challenge()
        response=self.post('verify',{'challenge_id':challenge,'code':code})
        self.assertEqual(response.status_code,409,response.content)
        self.assertEqual(response.json()['error']['code'],'PHONE_ALREADY_REGISTERED')
        self.assertEqual(LoginIdentity.objects.get(method='phone').user_id,other.pk)
        self.user.refresh_from_db();self.assertEqual(self.user.password,old)
        self.assertIsNotNone(LoginChallenge.objects.get(pk=challenge).consumed_at)

    def test_code_cannot_cross_session_user_purpose_or_expiration(self):
        challenge,code=self.challenge();other=Client();other.force_login(self.user);other.get('/api/account/profile')
        value={'challenge_id':challenge,'code':code}
        self.assertEqual(self.post('verify',value,other).status_code,422)
        another=get_user_model().objects.create_user(username='binding-other',password='Fixture!82A')
        LoginIdentity.objects.create(user=another,method='email',identifier='other-binding@example.invalid')
        unrelated=Client();unrelated.force_login(another);unrelated.get('/api/account/profile')
        self.assertEqual(self.post('verify',value,unrelated).status_code,422)
        LoginChallenge.objects.filter(pk=challenge).update(purpose='password_reset')
        self.assertEqual(self.post('verify',value).status_code,422)
        LoginChallenge.objects.filter(pk=challenge).update(purpose='phone_binding',expires_at=timezone.now())
        self.assertEqual(self.post('verify',value).status_code,422)
        self.assertFalse(LoginIdentity.objects.filter(method='phone').exists())

    def test_wrong_codes_are_limited_and_late_delivery_is_not_accepted(self):
        challenge,code=self.challenge();wrong='999999' if code!='999999' else '000000'
        for _ in range(5):
            self.assertEqual(self.post('verify',{'challenge_id':challenge,'code':wrong}).status_code,422)
        self.assertEqual(self.post('verify',{'challenge_id':challenge,'code':code}).status_code,422)
        def late(*args,**kwargs):
            LoginChallenge.objects.filter(purpose='phone_binding',consumed_at__isnull=True).update(expires_at=timezone.now())
            return 1
        with patch('backend.sms.send_code',side_effect=late):
            self.assertEqual(self.post('challenges',{'phone':'+8613800000000','password':'Fixture!82A'}).status_code,503)
        self.assertFalse(LoginChallenge.objects.filter(delivered=True,consumed_at__isnull=True,expires_at__gt=timezone.now()).exists())

    def test_google_only_requires_recent_server_marker_in_this_session_and_standard_password(self):
        self.google_only();data={'phone':'+8613800000000','new_password':'Violet!431'}
        flags=self.client.get('/api/account/security').json()['phone_binding']
        self.assertTrue(flags['requires_google_reauthentication']);self.assertFalse(flags['available'])
        with patch('backend.sms.send_code') as send:
            self.assertEqual(self.post('challenges',data).status_code,403);send.assert_not_called()
        AccountSession.objects.filter(user=self.user).update(google_verified_at=timezone.now()-timedelta(minutes=5))
        self.assertEqual(self.post('challenges',data).status_code,403)
        AccountSession.objects.filter(session_key=self.client.session.session_key).update(google_verified_at=timezone.now())
        with patch('backend.sms.send_code') as send:
            self.assertEqual(self.post('challenges',{**data,'new_password':'weak'}).status_code,422);send.assert_not_called()
        other=Client();other.force_login(self.user);other.get('/api/account/profile')
        self.assertEqual(self.post('challenges',data,other).status_code,403)
        with patch('backend.sms.send_code',return_value=1) as send:
            response=self.post('challenges',data)
        self.assertEqual(response.status_code,201,response.content)
        old_session=self.client.session.session_key
        result=self.post('verify',{'challenge_id':response.json()['challenge_id'],'code':send.call_args.args[1]})
        self.assertEqual(result.status_code,200,result.content)
        self.user.refresh_from_db();self.assertTrue(self.user.check_password('Violet!431'))
        self.assertNotEqual(self.client.session.session_key,old_session)
        self.assertEqual(self.client.get('/api/account/profile').status_code,200)
        self.assertEqual(other.get('/api/account/profile').status_code,401)
        self.assertEqual(LoginChallenge.objects.get(pk=response.json()['challenge_id']).password_hash,'')
        self.assertFalse(AccountSession.objects.filter(user=self.user,google_verified_at__isnull=False).exists())

    def test_google_proof_must_still_be_fresh_when_sms_is_confirmed(self):
        self.google_only()
        AccountSession.objects.filter(session_key=self.client.session.session_key).update(google_verified_at=timezone.now())
        with patch('backend.sms.send_code',return_value=1) as send:
            response=self.post('challenges',{'phone':'+8613800000000','new_password':'Violet!431'})
        self.assertEqual(response.status_code,201,response.content)
        AccountSession.objects.filter(user=self.user).update(google_verified_at=timezone.now()-timedelta(minutes=5))
        self.assertEqual(self.post('verify',{'challenge_id':response.json()['challenge_id'],'code':send.call_args.args[1]}).status_code,403)
        self.user.refresh_from_db();self.assertFalse(self.user.has_usable_password())
        self.assertFalse(LoginIdentity.objects.filter(method='phone').exists())

    def test_expired_pending_password_hash_is_scrubbed_by_existing_cleanup(self):
        from .authentication import cleanup_auth_data
        row=LoginChallenge.objects.create(password_user=self.user,purpose='phone_binding',method='phone',
            identifier='',code_digest='',session_digest='',password_hash='nonproduction-password-hash',expires_at=timezone.now())
        self.assertEqual(cleanup_auth_data()['challenges'],1)
        cleanup_auth_data(apply=True);row.refresh_from_db();self.assertEqual(row.password_hash,'')


@skipUnlessDBFeature('has_select_for_update')
@override_settings(**PHONE_SETTINGS,SMS_BINDING_TEMPLATE_ID='129',SMS_BINDING_TEMPLATE_APPROVED=True)
class PhoneBindingConcurrencyTests(TransactionTestCase):
    def test_two_browsers_bind_and_reject_without_crossed_session_locks(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections,connections
        from .views import digest
        user=get_user_model().objects.create_user(username='binding-concurrency',password='Violet!431')
        LoginIdentity.objects.create(user=user,method='email',identifier='concurrency@example.invalid')
        first,second=Client(),Client()
        for client in (first,second):
            client.force_login(user);client.get('/api/account/profile')
        row=LoginChallenge.objects.create(password_user=user,purpose='phone_binding',method='phone',
            identifier='+8613800000000',session_digest=digest(first.session.session_key),
            expires_at=timezone.now()+timedelta(minutes=5),delivered=True)
        row.code_digest=digest(str(row.pk)+':123456');row.save(update_fields=['code_digest'])
        gate=Barrier(2)
        def verify(client):
            close_old_connections()
            try:
                gate.wait(timeout=5)
                return client.post('/api/account/phone/verify',json.dumps({'challenge_id':str(row.pk),'code':'123456'}),
                    content_type='application/json').status_code
            finally:
                connections['default'].close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs=[pool.submit(verify,client) for client in (first,second)]
            self.assertEqual(sorted(job.result(timeout=10) for job in jobs),[200,422])
        self.assertEqual(LoginIdentity.objects.filter(user=user,method='phone').count(),1)


def registration_response(options,*,seed=b'a',origin=ORIGIN,uv=True):
    """Construct a test authenticator; all signature verification uses the installed library."""
    private=ed25519.Ed25519PrivateKey.generate()
    public=private.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    credential_id=seed*32
    client_data=json.dumps({'type':'webauthn.create','challenge':options['challenge'],
        'origin':origin,'crossOrigin':False},separators=(',',':')).encode()
    cose=cbor2.dumps({1:1,3:-8,-1:6,-2:public})
    auth_data=hashlib.sha256(RP.encode()).digest()+bytes([0x59|(4 if uv else 0)])+(0).to_bytes(4,'big')+bytes(16)+len(credential_id).to_bytes(2,'big')+credential_id+cose
    encoded=bytes_to_base64url(credential_id)
    return {'id':encoded,'rawId':encoded,'type':'public-key',
        'response':{'clientDataJSON':bytes_to_base64url(client_data),
                    'attestationObject':bytes_to_base64url(cbor2.dumps({'fmt':'none','attStmt':{},'authData':auth_data}))}},private


def authentication_response(options,credential_id,private,*,origin=ORIGIN,uv=True,counter=1):
    client_data=json.dumps({'type':'webauthn.get','challenge':options['challenge'],
        'origin':origin,'crossOrigin':False},separators=(',',':')).encode()
    auth_data=hashlib.sha256(RP.encode()).digest()+bytes([0x19|(4 if uv else 0)])+counter.to_bytes(4,'big')
    signature=private.sign(auth_data+hashlib.sha256(client_data).digest())
    return {'id':credential_id,'rawId':credential_id,'type':'public-key','response':{
        'clientDataJSON':bytes_to_base64url(client_data),'authenticatorData':bytes_to_base64url(auth_data),
        'signature':bytes_to_base64url(signature)}}


class AccountSecurityFixture:
    def setUp(self):
        self.user=get_user_model().objects.create_user(username='security-fixture',password='Fixture!82A',is_staff=True)
        self.account=BillingAccount.objects.create(kind='personal',personal_owner=self.user,name='Synthetic')
        Membership.objects.create(account=self.account,user=self.user,role='owner')
        self.client.force_login(self.user)
        self.client.get('/api/account/profile')
        for target in ('socket.create_connection','socket.getaddrinfo','socket.socket.connect'):
            probe=patch(target,side_effect=AssertionError('NO_EXTERNAL_NETWORK'));probe.start();self.addCleanup(probe.stop)

    def post(self,path,data,client=None):
        return (client or self.client).post('/api/account/'+path,json.dumps(data),content_type='application/json')

    def authorize(self):
        session=AccountSession.objects.get(user=self.user,session_key=self.client.session.session_key)
        authorize_passkey_recovery(self.user,reason='isolated-fixture',session_id=session.pk)

    def bind(self,seed=b'a'):
        result=self.post('passkeys/registration-options',{'label':'Synthetic device'})
        self.assertEqual(result.status_code,200)
        payload=result.json()
        credential,private=registration_response(payload['public_key'],seed=seed)
        response=self.post('passkeys/register',{'challenge_id':payload['challenge_id'],'credential':credential})
        self.assertEqual(response.status_code,201,response.content)
        return AccountPasskey.objects.get(pk=response.json()['id']),private


@override_settings(PASSKEY_RP_ID=RP,PASSKEY_ORIGIN=ORIGIN)
class PasskeySecurityTests(AccountSecurityFixture,TestCase):
    def test_server_authorization_is_rebind_only_and_registration_verifies_real_standard_proof(self):
        self.assertEqual(self.post('passkeys/registration-options',{'label':'Device'}).status_code,403)
        self.authorize()
        before=self.client.get('/api/account/security').json()
        self.assertIsNone(before['verified_until'])
        self.assertTrue(before['capabilities']['passkey_registration'])
        key,_=self.bind()
        self.assertTrue(key.synced)
        state=self.client.get('/api/account/security').json()
        self.assertEqual(state['verified_method'],'passkey')
        self.assertFalse(state['recovery_binding_available'])
        self.assertTrue(PasskeyRecovery.objects.get(user=self.user).used_at)

    def test_origin_user_verification_and_nonce_replay_are_rejected_by_standard_verifier(self):
        self.authorize()
        for kwargs in ({'origin':'https://evil.example.invalid'},{'uv':False}):
            response=self.post('passkeys/registration-options',{'label':'Synthetic'})
            payload=response.json();credential,_=registration_response(payload['public_key'],**kwargs)
            data={'challenge_id':payload['challenge_id'],'credential':credential}
            self.assertEqual(self.post('passkeys/register',data).status_code,422)
            self.assertIsNotNone(PasskeyChallenge.objects.get(pk=payload['challenge_id']).consumed_at)
            self.assertEqual(self.post('passkeys/register',data).status_code,422)
        self.assertEqual(AccountPasskey.objects.count(),0)

    def test_real_authentication_signature_counter_and_replay(self):
        self.authorize();key,private=self.bind()
        payload=self.post('passkeys/authentication-options',{}).json()
        credential=authentication_response(payload['public_key'],key.credential_id,private)
        data={'challenge_id':payload['challenge_id'],'credential':credential}
        response=self.post('passkeys/verify',data)
        self.assertEqual(response.status_code,200,response.content)
        key.refresh_from_db();self.assertEqual(key.sign_count,1)
        self.assertEqual(self.post('passkeys/verify',data).status_code,422)

    def test_cross_session_and_cross_user_proofs_cannot_authorize(self):
        self.authorize();key,private=self.bind()
        payload=self.post('passkeys/authentication-options',{}).json()
        credential=authentication_response(payload['public_key'],key.credential_id,private)
        other=Client();other.force_login(self.user);other.get('/api/account/profile')
        data={'challenge_id':payload['challenge_id'],'credential':credential}
        self.assertEqual(self.post('passkeys/verify',data,other).status_code,422)
        target=get_user_model().objects.create_user(username='another-staff',is_staff=True)
        other.force_login(target)
        self.assertEqual(self.post('passkeys/verify',data,other).status_code,422)

    def test_fifteen_minutes_are_fixed_not_sliding_and_logout_clears_confirmation(self):
        self.authorize();self.bind()
        row=AccountSession.objects.get(user=self.user)
        confirmed=row.sensitive_verified_at
        self.client.get('/api/account/security');row.refresh_from_db()
        self.assertEqual(row.sensitive_verified_at,confirmed)
        with patch('backend.account.timezone.now',return_value=confirmed+timedelta(minutes=15)):
            self.assertIsNone(self.client.get('/api/account/security').json()['verified_until'])
            self.assertEqual(self.post('passkeys/backup-codes',{}).status_code,403)
        self.client.post('/api/auth/logout','{}',content_type='application/json')
        row.refresh_from_db();self.assertIsNone(row.sensitive_verified_at)
        self.assertEqual(self.client.get('/api/account/security').status_code,401)

    def test_live_staff_revocation_and_last_key_protection(self):
        self.authorize();key,_=self.bind()
        self.assertEqual(self.post('passkeys/remove',{'id':str(key.pk)}).status_code,422)
        get_user_model().objects.filter(pk=self.user.pk).update(is_staff=False)
        self.assertEqual(self.post('passkeys/authentication-options',{}).status_code,403)

    def test_recovery_command_requires_internal_recent_specific_session_and_revokes_old_credentials(self):
        self.authorize();key,_=self.bind()
        other=Client();other.force_login(self.user);other.get('/api/account/profile')
        current=AccountSession.objects.get(session_key=other.session.session_key)
        output=StringIO()
        call_command('authorize_passkey_recovery',user_id=self.user.pk,reason_code='fixture-incident',stdout=output)
        self.assertFalse(json.loads(output.getvalue())['sensitive_actions_authorized'])
        call_command('authorize_passkey_recovery',user_id=self.user.pk,reason_code='fixture-incident',
            session_id=str(current.pk),apply=True,stdout=StringIO())
        key.refresh_from_db();self.assertIsNotNone(key.revoked_at)
        self.assertEqual(self.client.get('/api/account/security').status_code,401)
        state=other.get('/api/account/security').json()
        self.assertTrue(state['recovery_binding_available']);self.assertIsNone(state['verified_until'])
        self.assertEqual(self.post('passkeys/backup-codes',{},other).status_code,403)
        get_user_model().objects.filter(pk=self.user.pk).update(is_staff=False)
        with self.assertRaisesMessage(CommandError,'ACTIVE_INTERNAL_ACCOUNT_REQUIRED'):
            call_command('authorize_passkey_recovery',user_id=self.user.pk,reason_code='fixture-incident',apply=True)

    def test_recovery_authorization_is_not_available_to_another_session(self):
        self.authorize()
        other=Client();other.force_login(self.user);other.get('/api/account/profile')
        self.assertFalse(other.get('/api/account/security').json()['recovery_binding_available'])
        self.assertEqual(self.post('passkeys/registration-options',{'label':'Synthetic'},other).status_code,403)

    def test_optional_recovery_codes_are_single_use_and_only_allow_rebinding(self):
        self.authorize();key,_=self.bind()
        result=self.post('passkeys/backup-codes',{})
        self.assertEqual(result.status_code,201)
        self.assertEqual(result.json()['purpose'],'rebind_only')
        code=result.json()['codes'][0]
        response=self.post('passkeys/recover',{'password':'Fixture!82A','code':code})
        self.assertEqual(response.status_code,200,response.content)
        key.refresh_from_db();self.assertIsNotNone(key.revoked_at)
        state=self.client.get('/api/account/security').json()
        self.assertIsNone(state['verified_until']);self.assertTrue(state['recovery_binding_available'])
        self.assertEqual(self.post('passkeys/backup-codes',{}).status_code,403)
        self.assertEqual(self.post('passkeys/recover',{'password':'Fixture!82A','code':code}).status_code,401)
        self.bind(seed=b'b')

    def test_forged_signature_and_expired_ceremony_cannot_refresh_confirmation(self):
        self.authorize();key,private=self.bind()
        before=AccountSession.objects.get(user=self.user).sensitive_verified_at
        payload=self.post('passkeys/authentication-options',{}).json()
        credential=authentication_response(payload['public_key'],key.credential_id,private)
        credential['response']['signature']=bytes_to_base64url(bytes(64))
        data={'challenge_id':payload['challenge_id'],'credential':credential}
        self.assertEqual(self.post('passkeys/verify',data).status_code,422)
        self.assertEqual(AccountSession.objects.get(user=self.user).sensitive_verified_at,before)
        from .authentication import cleanup_auth_data
        self.assertTrue(PasskeyChallenge.objects.exists())
        cleanup_auth_data(apply=True)
        self.assertFalse(PasskeyChallenge.objects.filter(expires_at__lte=timezone.now()).exists())
        payload=self.post('passkeys/authentication-options',{}).json()
        credential=authentication_response(payload['public_key'],key.credential_id,private)
        PasskeyChallenge.objects.filter(pk=payload['challenge_id']).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.post('passkeys/verify',{'challenge_id':payload['challenge_id'],'credential':credential}).status_code,422)
        self.assertEqual(AccountSession.objects.get(user=self.user).sensitive_verified_at,before)


@override_settings(**PHONE_SETTINGS)
class BusinessPhoneConfirmationTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user(username='phone-security',password='Fixture!82A')
        LoginIdentity.objects.create(user=self.user,method='phone',identifier='+8613800000000')
        self.account=BillingAccount.objects.create(kind='business',name='Synthetic')
        self.member=Membership.objects.create(account=self.account,user=self.user,role='owner')
        self.client.force_login(self.user);self.client.get('/api/account/profile')

    def post(self,path,data):
        return self.client.post('/api/account/'+path,json.dumps(data),content_type='application/json')

    def challenge(self):
        with patch('backend.sms.send_code',return_value=1) as sender:
            response=self.post('sensitive-challenges',{'account_id':str(self.account.pk),'password':'Fixture!82A'})
        self.assertEqual(response.status_code,201,response.content)
        self.assertEqual(sender.call_args.kwargs['purpose'],'business_sensitive')
        return response.json()['challenge_id'],sender.call_args.args[1]

    def test_bound_phone_success_fixed_scope_and_one_use(self):
        challenge,code=self.challenge()
        response=self.post('sensitive-verify',{'challenge_id':challenge,'code':code})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['account_id'],str(self.account.pk))
        before=AccountSession.objects.get(user=self.user).sensitive_verified_at
        self.client.get('/api/account/security')
        self.assertEqual(AccountSession.objects.get(user=self.user).sensitive_verified_at,before)
        self.assertEqual(self.post('sensitive-verify',{'challenge_id':challenge,'code':code}).status_code,422)

    def test_unknown_provider_never_delivers_or_confirms(self):
        with patch('backend.sms.send_code',return_value=0):
            response=self.post('sensitive-challenges',{'account_id':str(self.account.pk),'password':'Fixture!82A'})
        self.assertEqual(response.status_code,503)
        self.assertFalse(LoginChallenge.objects.get().delivered)
        self.assertIsNone(AccountSession.objects.get(user=self.user).sensitive_verified_at)

    def test_each_of_five_purposes_and_real_acceptance_are_required(self):
        from .sms import sms_auth_ready
        self.assertTrue(sms_auth_ready())
        for field in ('SMS_REGISTRATION_TEMPLATE_APPROVED','SMS_PASSWORD_TEMPLATE_APPROVED',
                'SMS_PASSWORD_CHANGED_TEMPLATE_APPROVED','SMS_DELETION_TEMPLATE_APPROVED',
                'SMS_BUSINESS_TEMPLATE_APPROVED','SMS_REAL_DELIVERY_VERIFIED'):
            with self.subTest(field=field),override_settings(**{field:False}),patch('backend.sms.send_code') as sender:
                self.assertFalse(sms_auth_ready())
                self.assertEqual(self.post('sensitive-challenges',{'account_id':str(self.account.pk),'password':'Fixture!82A'}).status_code,503)
                sender.assert_not_called()

    def test_live_removal_wrong_password_expired_and_cross_account_are_rejected(self):
        self.assertEqual(self.post('sensitive-challenges',{'account_id':str(self.account.pk),'password':'wrong'}).status_code,401)
        # Password failure has an intentional persistent rate limit; do not wait on the clock.
        from .models import AuthThrottle
        AuthThrottle.objects.all().delete()
        challenge,code=self.challenge()
        self.member.is_active=False;self.member.save()
        self.assertEqual(self.post('sensitive-verify',{'challenge_id':challenge,'code':code}).status_code,403)
        self.member.is_active=True;self.member.save()
        LoginChallenge.objects.filter(pk=challenge).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.post('sensitive-verify',{'challenge_id':challenge,'code':code}).status_code,422)

    def test_expiry_during_session_lock_wait_cannot_grant_confirmation(self):
        from .account import _lock_current
        challenge,code=self.challenge()
        def lock_then_expire(request):
            _lock_current(request)
            LoginChallenge.objects.filter(pk=challenge).update(expires_at=timezone.now())
        with patch('backend.account._lock_current',side_effect=lock_then_expire):
            response=self.post('sensitive-verify',{'challenge_id':challenge,'code':code})
        self.assertEqual(response.status_code,422)
        self.assertIsNone(AccountSession.objects.get(user=self.user).sensitive_verified_at)

    def test_supplier_response_after_otp_deadline_is_not_marked_delivered(self):
        def late_acceptance(*args,**kwargs):
            deadline=LoginChallenge.objects.get().expires_at
            clock=patch('backend.account.timezone.now',return_value=deadline+timedelta(seconds=1))
            clock.start();self.addCleanup(clock.stop)
            return 1
        with patch('backend.sms.send_code',side_effect=late_acceptance):
            response=self.post('sensitive-challenges',{'account_id':str(self.account.pk),'password':'Fixture!82A'})
        self.assertEqual(response.status_code,503)
        self.assertFalse(LoginChallenge.objects.get().delivered)


@override_settings(**PHONE_SETTINGS,ACCOUNT_DELETION_ENABLED=True,
    ACCOUNT_DELETION_POLICY_VERSION='fixture-reviewed-v1',ACCOUNT_DELETION_POLICY_REVIEWED_AT='2026-01-01',
    ACCOUNT_DELETION_RETENTION_DAYS=365,ACCOUNT_DELETION_MARKER_RETENTION_DAYS=365,
    ACCOUNT_DELETION_MARKER_KEY='synthetic-only-'*6,AUTH_EMAIL_ENABLED=False)
class PhoneDeletionAndCalendarTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user(username='phone-delete',password='Fixture!82A')
        LoginIdentity.objects.create(user=self.user,method='phone',identifier='+8613800000000')
        self.account=BillingAccount.objects.create(kind='personal',personal_owner=self.user,name='Synthetic')
        Membership.objects.create(account=self.account,user=self.user,role='owner')
        self.client.force_login(self.user);self.client.get('/api/account/profile')

    def post(self,path,data):
        return self.client.post('/api/account/'+path,json.dumps(data),content_type='application/json')

    def challenge(self):
        with patch('backend.sms.send_code',return_value=1) as sender:
            response=self.post('deletion-challenges',{'password':'Fixture!82A'})
        self.assertEqual(response.status_code,201,response.content)
        self.assertEqual(sender.call_args.kwargs['purpose'],'account_deletion')
        return {'challenge_id':response.json()['challenge_id'],'code':sender.call_args.args[1],
            'confirm':True,'policy_version':'fixture-reviewed-v1'}

    def test_phone_only_password_and_dedicated_code_delete_without_email(self):
        TrialAttempt.objects.create(user=self.user,scenario='voc',input_value='Synthetic',input_key='fixture',
            request_key='fixture',task_state='completed',report={'fixture':True})
        eligibility=self.client.get('/api/account/deletion-eligibility').json()
        self.assertTrue(eligibility['available']);self.assertEqual(eligibility['verification_method'],'phone')
        self.assertEqual(self.post('deletion-challenges',{'password':'wrong'}).status_code,401)
        from .models import AuthThrottle
        AuthThrottle.objects.all().delete()
        data=self.challenge();self.assertEqual(self.post('delete',data).status_code,200)
        self.user.refresh_from_db();self.assertFalse(self.user.is_active)
        marker=TrialIdentityMarker.objects.get();row=AccountDeletionRequest.objects.get()
        self.assertEqual(marker.expires_at,_retention_due(row.completed_at))
        self.assertEqual(row.retain_until,_retention_due(row.completed_at))

    def test_unpublished_calendar_routes_to_manual_without_guessing_or_deleting(self):
        data=self.challenge()
        with patch('backend.account._business_due',side_effect=ValueError('CALENDAR_YEAR_UNAVAILABLE')):
            response=self.post('delete',data)
        self.assertEqual(response.status_code,202,response.content)
        row=AccountDeletionRequest.objects.get();self.assertIsNone(row.first_response_due_at)
        self.assertIsNone(row.completion_due_at);self.assertIn('CALENDAR_YEAR_UNAVAILABLE',row.blockers)
        self.user.refresh_from_db();self.assertTrue(self.user.is_active)

    def test_official_holiday_adjusted_workdays_and_unpublished_year(self):
        for start,days,end in (('2026-10-09',2,'2026-10-12'),('2026-09-30',2,'2026-10-09'),
                ('2026-02-13',2,'2026-02-24'),('2026-04-30',2,'2026-05-07')):
            now=datetime.fromisoformat(start+'T10:00:00+08:00')
            self.assertEqual(_business_due(now,days).date().isoformat(),end)
        with self.assertRaisesMessage(ValueError,'CALENDAR_YEAR_UNAVAILABLE'):
            _business_due(datetime.fromisoformat('2026-12-31T10:00:00+08:00'),2)
        self.assertEqual(_retention_due(datetime.fromisoformat('2024-02-29T10:00:00+08:00')).date().isoformat(),'2025-02-28')


@override_settings(PASSKEY_RP_ID=RP,PASSKEY_ORIGIN=ORIGIN,INVOICE_REQUEST_ENABLED=True)
class InvoiceIntakeTests(AccountSecurityFixture,TestCase):
    def setUp(self):
        super().setUp()
        from .commerce.tests import fixture_product
        from .commerce.models import Quote,Order,PaymentFact
        product=fixture_product();now=timezone.now()
        quote=Quote.objects.create(account=self.account,product=product,snapshot={},amount_minor=731,
            currency='CNY',expires_at=now+timedelta(minutes=10))
        self.order=Order.objects.create(account=self.account,created_by=self.user,quote=quote,
            idempotency_key='invoice-fixture',request_hash='a'*64,snapshot={},amount_minor=731,
            currency='CNY',status='fulfilled',paid_at=now)
        PaymentFact.objects.create(order=self.order,channel='fixture',merchant_reference='fixture',
            transaction_reference='invoice-fixture',amount_minor=731,currency='CNY',paid_at=now,
            evidence_hash='b'*64,disposition='accepted')

    def request_data(self):
        return {'order_id':str(self.order.pk),'recipient_email':'invoice@example.invalid',
            'title':'Synthetic invoice','tax_number':'','request_key':'invoice-request-1'}

    def test_email_does_not_need_to_be_login_identity_and_replay_is_exact(self):
        data=self.request_data();response=self.post('invoices',data)
        self.assertEqual(response.status_code,201,response.content)
        self.assertFalse(LoginIdentity.objects.filter(user=self.user,method='email').exists())
        self.assertEqual(self.post('invoices',data).json()['id'],response.json()['id'])
        self.assertEqual(InvoiceRequest.objects.count(),1)
        self.assertEqual(self.post('invoices',{**data,'recipient_email':'other@example.invalid'}).status_code,422)

    def test_other_account_cannot_read_or_request_invoice(self):
        other=get_user_model().objects.create_user(username='invoice-other')
        self.client.force_login(other)
        self.assertEqual(self.post('invoices',self.request_data()).status_code,403)
        self.assertEqual(self.client.get('/api/account/invoices',{'account_id':str(self.account.pk)}).status_code,403)

    def test_intake_and_issue_policy_are_separate_and_sensitive_admin_confirmation_is_required(self):
        row=self.post('invoices',self.request_data()).json()
        self.user.user_permissions.add(Permission.objects.get(codename='manage_invoice'))
        self.assertEqual(self.post('invoices/handle',{'id':row['id'],'action':'accept'}).status_code,403)
        self.authorize();self.bind()
        self.assertEqual(self.post('invoices/handle',{'id':row['id'],'action':'accept'}).status_code,200)
        with patch('backend.account._send_invoice_email') as sender:
            for action in ('record_issued','send'):
                self.assertEqual(self.post('invoices/handle',{'id':row['id'],'action':action,
                    'invoice_reference':'synthetic-invoice'}).status_code,503)
            sender.assert_not_called()
        self.assertEqual(InvoiceRequest.objects.get().status,'accepted')
        self.assertEqual(InvoiceAudit.objects.count(),2)

    def test_disabled_intake_and_unpaid_order_do_not_create_request(self):
        with override_settings(INVOICE_REQUEST_ENABLED=False):
            self.assertEqual(self.post('invoices',self.request_data()).status_code,503)
        from .commerce.models import Order
        Order.objects.filter(pk=self.order.pk).update(status='pending',paid_at=None)
        self.assertEqual(self.post('invoices',self.request_data()).status_code,422)
        self.assertFalse(InvoiceRequest.objects.exists())

    @override_settings(INVOICE_FULFILLMENT_APPROVED=True,AUTH_EMAIL_ENABLED=True,AUTH_EMAIL_TRANSPORT='ses',
        DEFAULT_FROM_EMAIL='sender@example.invalid',SES_SECRET_ID='fixture',SES_SECRET_KEY='fixture',
        SES_REGION='ap-hongkong',INVOICE_SES_TEMPLATE_ID='123')
    def test_sending_unknown_does_not_claim_sent_or_allow_blind_retry(self):
        row=self.post('invoices',self.request_data()).json()
        self.user.user_permissions.add(Permission.objects.get(codename='manage_invoice'))
        self.authorize();self.bind()
        self.assertEqual(self.post('invoices/handle',{'id':row['id'],'action':'accept'}).status_code,200)
        self.assertEqual(self.post('invoices/handle',{'id':row['id'],'action':'record_issued',
            'invoice_reference':'synthetic-issued-reference'}).status_code,200)
        with patch('backend.account._send_invoice_email',side_effect=OSError('SYNTHETIC_UNKNOWN')) as sender:
            result=self.post('invoices/handle',{'id':row['id'],'action':'send'})
            self.assertEqual(result.status_code,200,result.content)
            self.assertEqual(result.json()['status'],'unknown')
            self.assertEqual(self.post('invoices/handle',{'id':row['id'],'action':'send'}).status_code,422)
            self.assertEqual(sender.call_count,1)
        self.assertEqual(InvoiceRequest.objects.get().provider_reference,'')

    @override_settings(INVOICE_FULFILLMENT_APPROVED=True)
    def test_open_refund_prevents_full_amount_issue(self):
        from .commerce.models import CreditBatch,CreditFreeze,CreditRefund,PaymentFact
        row=self.post('invoices',self.request_data()).json()
        self.user.user_permissions.add(Permission.objects.get(codename='manage_invoice'))
        self.authorize();self.bind()
        self.post('invoices/handle',{'id':row['id'],'action':'accept'})
        batch=CreditBatch.objects.create(account=self.account,source='purchased',source_key='invoice-fixture',
            initial_quantity='1',expires_at=timezone.now()+timedelta(days=2))
        freeze=CreditFreeze.objects.create(batch=batch,quantity='1',request_key='invoice-fixture',state='frozen')
        CreditRefund.objects.create(payment=PaymentFact.objects.get(order=self.order),freeze=freeze,
            request_key='invoice-fixture',amount_minor=1,review_evidence_hash='c'*64,state='reserved')
        response=self.post('invoices/handle',{'id':row['id'],'action':'record_issued',
            'invoice_reference':'synthetic-issued-reference'})
        self.assertEqual(response.status_code,422,response.content)
        self.assertEqual(response.json()['error']['code'],'REFUND_REVIEW_REQUIRED')
        self.assertEqual(InvoiceRequest.objects.get().status,'accepted')


@override_settings(PASSKEY_RP_ID=RP,PASSKEY_ORIGIN=ORIGIN)
class RefundAdminSecurityTests(AccountSecurityFixture,TestCase):
    def setUp(self):
        super().setUp()
        from .commerce.tests import fixture_product
        from .commerce.models import (Quote,Order,PaymentFact,Subscription,ServiceRefund,
            CreditBatch,CreditFreeze,CreditRefund)
        product=fixture_product();now=timezone.now()
        quote=Quote.objects.create(account=self.account,product=product,snapshot={},amount_minor=731,
            currency='CNY',expires_at=now+timedelta(minutes=10))
        order=Order.objects.create(account=self.account,created_by=self.user,quote=quote,
            idempotency_key='refund-admin-fixture',request_hash='a'*64,snapshot={},amount_minor=731,
            currency='CNY',status='fulfilled',paid_at=now)
        payment=PaymentFact.objects.create(order=order,channel='fixture',merchant_reference='fixture',
            transaction_reference='refund-admin-fixture',amount_minor=731,currency='CNY',paid_at=now,
            evidence_hash='b'*64,disposition='accepted')
        subscription=Subscription.objects.create(account=self.account,initial_order=order,original_anchor=now,
            billing_timezone='Asia/Shanghai',calendar_rule_version='fixture',billing_cycle='annual',mandate={})
        self.service=ServiceRefund.objects.create(account=self.account,subscription=subscription,created_by=self.user,
            request_key='service-admin-review',request_hash='c'*64,status='review',amount_minor=500,
            allocations=[{'payment_id':str(payment.pk),'amount_minor':500}],pending_from=now)
        batch=CreditBatch.objects.create(account=self.account,source='purchased',source_key='refund-admin-fixture',
            initial_quantity='2',expires_at=now+timedelta(days=2))
        freeze=CreditFreeze.objects.create(batch=batch,quantity='2',request_key='credit-admin-freeze',state='frozen')
        self.credit=CreditRefund.objects.create(payment=payment,freeze=freeze,request_key='credit-admin-review',
            amount_minor=100,review_evidence_hash='d'*64,review_state='review',state='reserved')

    def permit(self,*,approve):
        names=['view_servicerefund','view_creditrefund']
        if approve:
            names+=['change_servicerefund','change_creditrefund']
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label='commerce',codename__in=names))

    def approve(self,row):
        return self.client.post('/sales/commerce/'+row._meta.model_name+'/',
            {'action':'approve_requests','_selected_action':str(row.pk),'index':'0'})

    def unchanged(self):
        self.service.refresh_from_db();self.credit.refresh_from_db()
        self.assertEqual(self.service.status,'review')
        self.assertEqual((self.credit.review_state,self.credit.state),('review','reserved'))

    def test_view_only_admin_cannot_submit_or_directly_invoke_approval(self):
        from django.contrib import admin
        self.permit(approve=False);self.authorize();self.bind()
        for row in (self.service,self.credit):
            response=self.approve(row)
            self.assertEqual(response.status_code,200,response.content)
            registered=admin.site._registry[type(row)]
            self.assertNotIn('approve_requests',registered.get_actions(response.wsgi_request))
            refused=registered.approve_requests(response.wsgi_request,type(row).objects.filter(pk=row.pk))
            self.assertEqual(refused.status_code,403)
        self.unchanged()

    def test_approval_permission_without_device_confirmation_is_denied(self):
        self.permit(approve=True)
        for row in (self.service,self.credit):
            self.assertEqual(self.approve(row).status_code,403)
        self.unchanged()

    def test_fixed_fifteen_minute_expired_confirmation_cannot_approve(self):
        self.permit(approve=True);self.authorize();self.bind()
        AccountSession.objects.filter(session_key=self.client.session.session_key).update(
            sensitive_verified_at=timezone.now()-timedelta(minutes=15))
        for row in (self.service,self.credit):
            self.assertEqual(self.approve(row).status_code,403)
        self.unchanged()

    def test_real_local_passkey_confirmation_and_permissions_approve_only_review_state(self):
        from .models import AuditEvent
        self.permit(approve=True);self.authorize();self.bind()
        for row in (self.service,self.credit):
            self.assertEqual(self.approve(row).status_code,302)
        self.service.refresh_from_db();self.credit.refresh_from_db()
        self.assertEqual(self.service.status,'approved')
        self.assertEqual((self.credit.review_state,self.credit.state),('approved','reserved'))
        self.assertEqual(len(self.service.evidence_hash),64)
        self.assertEqual(self.credit.evidence_hash,'');self.assertIsNone(self.credit.resolved_at)
        self.assertFalse(self.service.receipts.exists())
        self.assertEqual(AuditEvent.objects.filter(action='service_refund.approved',actor=self.user).count(),1)
        self.assertEqual(AuditEvent.objects.filter(action='credit_refund.approved',actor=self.user).count(),1)
        for row in (self.service,self.credit):
            self.assertEqual(self.approve(row).status_code,302)
        self.assertEqual(AuditEvent.objects.filter(action__in=('service_refund.approved','credit_refund.approved')).count(),2)
