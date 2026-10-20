/* Isolated PostgreSQL integration gate. Credentials exist only in this process. */
const {spawnSync} = require('node:child_process');
const {randomBytes, createHash} = require('node:crypto');
const assert = require('node:assert/strict');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const python = path.join(root, '.venv/bin/python');
const name = `sage-billing-pg-test-${process.pid}`;
const password = randomBytes(32).toString('base64url');
const env = {...process.env, POSTGRES_PASSWORD: password, SAGE_ENV: 'test',
  SAGE_DB_NAME: 'sage_test', SAGE_DB_USER: 'sage_test', SAGE_DB_PASSWORD: password,
  SAGE_DB_HOST: '127.0.0.1'};
let started = false;

function run(command, args, print = true, timeout = 180000) {
  const result = spawnSync(command, args, {cwd: root, env, encoding: 'utf8', timeout});
  if (print) process.stdout.write((result.stdout || '') + (result.stderr || ''));
  if (result.error || result.status !== 0) {
    // Hidden stdout can contain a dump or identifiers; retain stderr for diagnosis.
    if (!print && result.stderr) process.stderr.write(result.stderr);
    throw new Error(`${command} failed (${result.status ?? 'timeout'})`);
  }
  return result.stdout.trim();
}

function normalizedSchema(sql) {
  // pg_restore reparses literal varchar[] casts into per-element text casts.
  // Preserve every literal and all other DDL; do not sort SQL lines or erase casts.
  const literal = "'(?:[^']|'')*'::character varying";
  const array = new RegExp(`\\(ARRAY\\[(${literal}(?:, ${literal})*)\\]\\)::text\\[\\]`, 'g');
  return sql.replace(/^    CONSTRAINT [a-z_][a-z0-9_]* CHECK .*$/gm, line => {
    if (line.includes('$') || /\bE'/.test(line)) return line; // Unsupported quoting fails comparison.
    return line.replace(array, (match, values, offset) => {
      let quote = null;
      for (let i = 0; i < offset; i++) {
        if (quote && line[i] === quote && line[i+1] === quote) { i++; continue; }
        if (quote && line[i] === quote) quote = null;
        else if (!quote && (line[i] === "'" || line[i] === '"')) quote = line[i];
      }
      if (quote) return match;
      return `ARRAY[${values.replace(new RegExp(literal, 'g'), value => `(${value})::text`)}]`;
    });
  });
}

const castSource = "    CONSTRAINT fixture_state_valid CHECK (state = ANY ((ARRAY['running'::character varying, 'O''Brien'::character varying])::text[]))";
const castRestored = "    CONSTRAINT fixture_state_valid CHECK (state = ANY (ARRAY[('running'::character varying)::text, ('O''Brien'::character varying)::text]))";
assert.equal(normalizedSchema(castSource), castRestored);
assert.notEqual(normalizedSchema(castSource), normalizedSchema(castRestored.replace('running', 'missing')));
assert.notEqual(normalizedSchema(castSource), normalizedSchema(castRestored.replaceAll('character varying', 'character varying(3)')));
assert.notEqual(normalizedSchema(castSource), normalizedSchema(''));
assert.equal(normalizedSchema(`COMMENT ON TABLE fixture IS '${castSource.replaceAll("'", "''")}';`), `COMMENT ON TABLE fixture IS '${castSource.replaceAll("'", "''")}';`);
assert.equal(normalizedSchema(castSource.replace('CHECK (state', "CHECK ('example")), castSource.replace('CHECK (state', "CHECK ('example"));
assert.notEqual(normalizedSchema(castSource), normalizedSchema(castRestored.replace("'running'", "'O''Brien'").replace("('O''Brien'::character varying)::text]))", "('running'::character varying)::text]))")));

function recoveryAudit() {
  // Only the unique container created above is used. No caller-supplied database.
  const shell = code => run(python, ['manage.py', 'shell', '--command', code], false);
  const fingerprint = () => shell(`
import hashlib, json
from django.db import connection
data = {}
with connection.cursor() as cursor:
    cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
    for (table,) in cursor.fetchall():
        cursor.execute('SELECT to_jsonb(t)::text FROM '+connection.ops.quote_name(table)+' t ORDER BY 1')
        data[table] = [row[0] for row in cursor.fetchall()]
    cursor.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")
    for (sequence,) in cursor.fetchall():
        cursor.execute('SELECT last_value, is_called FROM '+connection.ops.quote_name(sequence))
        data['sequence:'+sequence] = cursor.fetchone()
print(hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest())
`).split('\n').at(-1);
  shell(`
from datetime import timedelta
import secrets
from django.test import override_settings
from django.utils import timezone
from backend.models import Membership
from backend.commerce.tests import fixture_account, fixture_product, FIXTURE_CHANNELS
from backend.commerce.billing import (create_quote, create_order, record_confirmed_payment,
    prepare_payment_attempt, claim_payment_dispatch, record_attempt_event,
    reserve_credits, settle_credits, freeze_credits)
with override_settings(BILLING_SALES_ENABLED=True,BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS):
    user, account = fixture_account('isolated-backup-buyer')
    Membership.objects.create(account=account,user=user,role='owner')
    product = fixture_product()
    now = timezone.now()
    for key in ('backup-paid','backup-unknown'):
        quote = create_quote(account,user,product.pk,1,now+timedelta(minutes=5),channel='fixture',buyer_region='CN')
        order = create_order(account,user,{'quote_id':quote['id'],'agreement_acceptance':'fixture-1'},key)
        if key == 'backup-paid':
            for reference in ('backup-receipt','backup-duplicate'):
                record_confirmed_payment(account,order['id'],channel='fixture',merchant_reference='fixture-shenzhen',
                    transaction_reference=reference,amount_minor=731,currency='CNY',paid_at=now,evidence_hash='a'*64)
        else:
            attempt = prepare_payment_attempt(account,user,order['id'],'backup-intent',expires_at=now+timedelta(minutes=15))
            assert claim_payment_dispatch(account,attempt['id'])['dispatch']
            record_attempt_event(account,attempt['id'],'backup-timeout','unknown',source='local',evidence_hash='')
    reservation = reserve_credits(account,'backup-settled-task','2')
    settle_credits(account,reservation['id'],'1')
    from backend.commerce.models import CreditBatch
    freeze_credits(account,CreditBatch.objects.get(account=account).pk,'backup-freeze','1')
    reserve_credits(account,'backup-pending-task','2')
from backend.models import InvitationCode, RegistrationAttribution
from backend.invitations import create_registered_user
with override_settings(INVITATION_CODES_READY=True):
    InvitationCode.objects.create(code='BACKUP01',blogger='isolated-backup-blogger')
    invited = create_registered_user(invitation_code='backup01',username='isolated-backup-invited',
        password=secrets.token_hex(5)+'Aa!1')
    InvitationCode.objects.filter(pk='BACKUP01').update(is_active=False)
    assert RegistrationAttribution.objects.filter(user=invited,invitation_code_id='BACKUP01').count() == 1
from django.contrib.auth.hashers import make_password
from backend.models import AccountProfile, AuthFlow, LoginChallenge, LoginIdentity
original = LoginIdentity.objects.create(user=invited,method='email',identifier='isolated-backup-original@example.invalid')
LoginIdentity.objects.create(user=invited,method='google',identifier='isolated-backup-google-sub',provider_email='isolated-backup-original@example.invalid')
LoginIdentity.objects.create(user=invited,method='phone',identifier='+8613800138000')
AccountProfile.objects.create(user=invited,agreement_version='NONPRODUCTION-BACKUP-FIXTURE',agreement_accepted_at=now)
challenge = LoginChallenge.objects.create(purpose='registration',method='phone',identifier='+8613800138000',
    code_digest=secrets.token_hex(32),session_digest=secrets.token_hex(32),expires_at=now+timedelta(minutes=5),
    delivered=True,attempts=1,consumed_at=now)
AuthFlow.objects.create(purpose='registration',method='phone',stage='code',identifier=challenge.identifier,
    session_digest=challenge.session_digest,challenge=challenge,user=invited,
    agreement_version='NONPRODUCTION-BACKUP-FIXTURE',agreement_accepted_at=now,
    expires_at=challenge.expires_at,consumed_at=now)
AuthFlow.objects.create(purpose='google',stage='bind_confirm',identifier=original.identifier,user=invited,
    original_identity=original,password_hash=invited.password,google_sub='isolated-backup-unbound-sub',google_intent='login',
    session_digest=secrets.token_hex(32),
    expires_at=now+timedelta(minutes=5),return_to='/pages/billing.html')
AuthFlow.objects.create(purpose='google',stage='oauth',identifier='',google_intent='register',
    session_digest=secrets.token_hex(32),google_state_digest=secrets.token_hex(32),google_nonce_digest=secrets.token_hex(32),
    expires_at=now+timedelta(minutes=5),return_to='/index-en.html',language='en')
AuthFlow.objects.create(purpose='registration',method='email',identifier='isolated-backup-pending@example.invalid',
    session_digest=secrets.token_hex(32),password_hash=make_password(secrets.token_hex(5)+'Aa!1'),
    agreement_version='NONPRODUCTION-BACKUP-FIXTURE',agreement_accepted_at=now,expires_at=now+timedelta(minutes=5))
`);
  const sourceData = fingerprint();
  const schema = database => run('docker', ['exec', name,
    'pg_dump', '-U', 'sage_test', '--schema-only', '--no-owner', '--no-acl', database], false)
    .split('\n').filter(line => !line.startsWith('--') && !line.startsWith('\\restrict') && !line.startsWith('\\unrestrict')).join('\n');
  const sourceSchema = normalizedSchema(schema('sage_test'));
  assert.equal(run('docker', ['exec', name, 'psql', '-U', 'sage_test', '-d', 'sage_test', '-At', '-c',
    "SELECT bool_and((s = ANY ((ARRAY['running'::varchar,'O''Brien'::varchar])::text[])) IS NOT DISTINCT FROM (s = ANY (ARRAY[('running'::varchar)::text,('O''Brien'::varchar)::text]))) AND ((ARRAY['running'::varchar,'O''Brien'::varchar])::text[] = ARRAY[('running'::varchar)::text,('O''Brien'::varchar)::text]) AND pg_typeof((ARRAY['running'::varchar])::text[]) = pg_typeof(ARRAY[('running'::varchar)::text]) FROM (VALUES (NULL::text),('running'),('O''Brien'),('absent')) probe(s);"], false), 't',
    'Literal array canonicalization must preserve PostgreSQL values and type');
  run('docker', ['exec', name, 'pg_dump', '-U', 'sage_test', '--format=custom',
    '--file=/tmp/sage-backup.dump', 'sage_test'], false);
  run('docker', ['exec', name, 'createdb', '-U', 'sage_test', '--template=template0', 'sage_restore'], false);
  run('docker', ['exec', name, 'pg_restore', '-U', 'sage_test', '--single-transaction',
    '--exit-on-error', '--no-owner', '--no-acl', '--dbname=sage_restore', '/tmp/sage-backup.dump'], false);
  env.SAGE_DB_NAME = 'sage_restore';
  try {
    assert.equal(fingerprint(), sourceData, 'Restored rows and sequence states must match');
    shell("from backend.models import InvitationCode, RegistrationAttribution; assert not InvitationCode.objects.get(pk='BACKUP01').is_active; assert RegistrationAttribution.objects.filter(invitation_code_id='BACKUP01').count() == 1");
    shell(`
from backend.models import AccountProfile, AuthFlow, LoginChallenge, LoginIdentity
profile = AccountProfile.objects.get(agreement_version='NONPRODUCTION-BACKUP-FIXTURE')
assert profile.agreement_accepted_at is not None
identity = LoginIdentity.objects.get(user=profile.user,method='google')
assert identity.identifier == 'isolated-backup-google-sub'
assert LoginIdentity.objects.filter(user=profile.user,method='email',identifier=identity.provider_email).exists()
closed = AuthFlow.objects.get(purpose='registration',method='phone')
assert closed.consumed_at is not None and closed.stage == 'code'
assert LoginIdentity.objects.filter(user=closed.user,method='phone',identifier=closed.identifier).exists()
assert closed.challenge.method == 'phone' and closed.challenge.consumed_at == closed.consumed_at
assert closed.challenge.delivered and closed.challenge.attempts == 1
binding = AuthFlow.objects.get(purpose='google',stage='bind_confirm')
assert binding.original_identity.user_id == profile.user_id and binding.user_id == profile.user_id
assert binding.google_nonce_digest == '' and binding.google_state_digest is None
assert binding.password_hash == profile.user.password
oauth = AuthFlow.objects.get(purpose='google',stage='oauth')
assert oauth.google_nonce_digest and oauth.google_state_digest and oauth.user_id is None
assert oauth.original_identity_id is None and oauth.google_intent == 'register' and oauth.language == 'en'
pending = AuthFlow.objects.get(identifier='isolated-backup-pending@example.invalid')
assert pending.method == 'email' and pending.password_hash and pending.consumed_at is None
`);
    const restoredSchema = normalizedSchema(schema('sage_restore'));
    if (restoredSchema !== sourceSchema) {
      const sourceLines = sourceSchema.split('\n'), restoredLines = restoredSchema.split('\n');
      console.error(JSON.stringify({schema_difference: {
        source_only: sourceLines.filter(line => !restoredLines.includes(line)).slice(0, 5),
        restored_only: restoredLines.filter(line => !sourceLines.includes(line)).slice(0, 5),
        equal_line_multisets: JSON.stringify(sourceLines.slice().sort()) === JSON.stringify(restoredLines.slice().sort())
      }}));
    }
    assert.equal(createHash('sha256').update(restoredSchema).digest('hex'),
      createHash('sha256').update(sourceSchema).digest('hex'), 'Restored schema and constraints must match');
    // Prove that the data comparator notices a changed business state, not just counts.
    shell("from backend.commerce.models import Order; Order.objects.filter(idempotency_key='backup-paid').update(status='closed')");
    assert.notEqual(fingerprint(), sourceData, 'A corrupt restored order must fail the comparison');
    shell("from backend.commerce.models import Order; Order.objects.filter(idempotency_key='backup-paid').update(status='fulfilled')");
    assert.equal(fingerprint(), sourceData, 'Negative control must restore the original fixture');
    shell("from backend.models import AuthFlow; AuthFlow.objects.filter(purpose='registration',method='phone').update(method='email')");
    assert.notEqual(fingerprint(), sourceData, 'A corrupt restored auth method must fail the comparison');
    shell("from backend.models import AuthFlow; AuthFlow.objects.filter(purpose='registration',identifier='+8613800138000').update(method='phone')");
    assert.equal(fingerprint(), sourceData, 'Auth negative control must restore the original fixture');
    const beforeReplay = fingerprint();
    shell(`
from django.test import override_settings
from backend.models import BillingAccount
from backend.commerce.tests import FIXTURE_CHANNELS
from backend.commerce.models import Order, PaymentFact, PaymentAttempt
from backend.commerce.billing import (create_order, record_confirmed_payment, fulfill_paid_order,
    claim_payment_dispatch, prepare_payment_attempt)
account = BillingAccount.objects.get(name='isolated-backup-buyer')
user = account.personal_owner
with override_settings(BILLING_SALES_ENABLED=True,BILLING_APPROVED_CHANNELS=FIXTURE_CHANNELS):
    for order in Order.objects.filter(account=account):
        assert create_order(account,user,{'quote_id':str(order.quote_id),'agreement_acceptance':'fixture-1'},order.idempotency_key)['id'] == str(order.pk)
    order = Order.objects.get(account=account,idempotency_key='backup-paid')
    for fact in PaymentFact.objects.filter(order=order):
        result = record_confirmed_payment(account,order.pk,channel=fact.channel,merchant_reference=fact.merchant_reference,
            transaction_reference=fact.transaction_reference,amount_minor=fact.amount_minor,currency=fact.currency,
            paid_at=fact.paid_at,evidence_hash=fact.evidence_hash)
        assert result['disposition'] == fact.disposition
    fulfill_paid_order(account,order.pk)
    attempt = PaymentAttempt.objects.get(account=account)
    prepare_payment_attempt(account,user,attempt.order_id,'backup-intent',expires_at=attempt.expires_at)
    assert not claim_payment_dispatch(account,attempt.pk)['dispatch']
`);
    assert.equal(fingerprint(), beforeReplay, 'Recovery replay must not mutate money, credits or dispatch');
    console.log('Isolated pg_dump/pg_restore passed: full rows, sequences, schema and replay invariants. No channel called.');
  } finally { env.SAGE_DB_NAME = 'sage_test'; }
}

(async () => {
  try {
    run('docker', ['run', '--detach', '--rm', '--name', name, '--env', 'POSTGRES_PASSWORD',
      '--env', 'POSTGRES_USER=sage_test', '--env', 'POSTGRES_DB=sage_test',
      '--publish', '127.0.0.1::5432', 'postgres:16'], false);
    started = true;
    const port = run('docker', ['port', name, '5432/tcp'], false);
    if (!/^127\.0\.0\.1:\d+$/.test(port)) throw new Error('Database must bind loopback only');
    env.SAGE_DB_PORT = port.split(':').at(-1);
    let ready = false;
    for (let i = 0; i < 30; i++) {
      const result = spawnSync('docker', ['exec', name, 'pg_isready', '-U', 'sage_test', '-d', 'sage_test'],
        {env, stdio: 'ignore', timeout: 5000});
      if (result.status === 0) { ready = true; break; }
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    if (!ready) throw new Error('PostgreSQL startup timed out');
    run(python, ['manage.py', 'migrate', '--noinput']);
    run(python, ['manage.py', 'migrate', 'backend', 'zero', '--noinput']);
    run(python, ['manage.py', 'migrate', '--noinput']);
    run(python, ['manage.py', 'makemigrations', '--check', '--dry-run']);
    const suites = ['backend.authentication_tests', 'backend.google_auth_tests', 'backend.tests', 'backend.commerce.tests', 'backend.commerce.subscription_tests', 'backend.commerce.attempt_tests', 'backend.payment_channel_tests', 'backend.payment_flow_tests', 'backend.alipay_transport_tests', 'backend.wechat_tests', 'backend.trial_tests', 'backend.email_tests', 'backend.account_tests', 'backend.account_deletion_tests', 'backend.consultation_tests', 'backend.sms_tests', 'backend.billing_session_tests', 'backend.invitation_tests'];
    suites.push('backend.security_tests', 'backend.notification_tests', 'backend.operations_tests',
      'backend.auth_readiness_tests', 'backend.commerce.refund_tests', 'backend.wechat_flow_tests');
    if (process.env.PLAYWRIGHT_MODULE) suites.push('backend.browser_tests', 'backend.website_browser_tests');
    // The full suite with real browsers exceeded 180s; keep other commands bounded.
    run(python, ['manage.py', 'test', ...suites, '--verbosity', '2'], true, 600000);
    recoveryAudit();
    console.log('PostgreSQL migration round-trip and integration tests passed. No production data touched.');
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  } finally {
    if (started) {
      try { run('docker', ['stop', name], false); }
      catch { console.error(`Test container cleanup failed: ${name}`); process.exitCode = 1; }
    }
  }
})();
