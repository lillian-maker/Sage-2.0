// UI response fixtures only. No actual email, SMS, account write or payment call.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SAGE_URL || 'http://127.0.0.1:8914';
assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname));
const one = '11111111-1111-4111-8111-111111111111';
const two = '22222222-2222-4222-8222-222222222222';
const orderId = '33333333-3333-4333-8333-333333333333';
const subscriptionId = '44444444-4444-4444-8444-444444444444';
const lifecycleQuoteId = '77777777-7777-4777-8777-777777777777';
const makeSubscription = () => ({id:subscriptionId,plan_code:'pro',billing_cycle:'annual',status:'active',period_start:'2026-01-31T01:00:00+08:00',period_end:'2027-01-31T01:00:00+08:00',next_credit_grant_at:'2026-10-31T01:00:00+08:00',prepaid_through:'2028-01-31T01:00:00+08:00',auto_renew:true,cancellation_state:'none',can_cancel:true,capabilities:{cancel:true,upgrade:false,payment_method:false}});
const accounts = [{id:one,kind:'personal',name:'Fixture personal',role:'owner'},{id:two,kind:'business',name:'Fixture business',role:'billing_admin'}];
const makeSession = (authenticated = true, active = one) => ({authenticated,user:authenticated ? {id:'fixture-user',display_name:'Fixture user',masked_identifier:'f***@example.test'} : null,accounts:authenticated ? accounts : [],active_account_id:authenticated ? active : null,csrf_token:'fixture-only-csrf',capabilities:{email_login:true,phone_login:true,procurement:true}});
const makeAccount = (id = one) => ({account:accounts.find(item => item.id === id),subscription:null,credits:{available:id === one ? '0.00' : '27.25',reserved:'0.00',frozen:'0.00',consumed:'0.00',expired:'0.00',refunded:'0.00',batches:[]},orders:[],invoice_requests:[],payment_methods:[],capabilities:{subscription:false,invoices:false,payment_methods:false}});
(async () => {
  const browser = await chromium.launch({channel:'chrome'});
  const page = await browser.newPage(); page.setDefaultTimeout(7000);
  const errors = [], writes = [], checks = [], apiRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  let state = makeSession(), accountFails = false, logoutFails = false, wrongAccount = false, oldAccountDelay = false, withOrder = false, verifyFails = false, changedAccount = false, expiredPurchase = false, oldAccountUnauthorized = false, orderPhase = 'pending_payment';
  let inquiries = [];
  let delayedInquiry = null, releaseInquiry = null, inquiryPostUnknown = false;
  let subscriptionsEnabled = true, subscriptions = [makeSubscription()], subscriptionsFail = false, cancelFail = false, delaySubscriptions = false;
  let attemptState = null;
  let methodsFail = false, methodsWrongAccount = false, methodTopupMode = 'normal', methodsDelay = false, releaseMethod = null;
  let topupBudget = null, topupFails = false;
  let refundFails = false, refundReason = 'policy_review_required', refundDelay = false, refundWrongAccount = false;
  let refundHistory = [];
  let creditFixture = null;
  let wrongUsage = false;
  let trialRecord = {eligibility:'available',attempt:null}, trialFails = false, trialReads = 0, delayedTrial = null;
  let heldSelection = null, heldLogout = null, heldAccount = null, sessionReads = 0;
  const cancelPath = `/api/billing/subscriptions/${subscriptionId}/cancel`;
  await page.route('**/billing.js',route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../src/pages/billing.js'),'utf8')}));
  await page.route('**/billing.css',route=>route.fulfill({contentType:'text/css',body:fs.readFileSync(path.join(__dirname,'../src/pages/billing.css'),'utf8')}));
  await page.route(/\/experience(?:-en)?\.js(?:\?.*)?$/,route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../src/pages/experience.js'),'utf8')}));
  await page.route('**/experience.css',route=>route.fulfill({contentType:'text/css',body:fs.readFileSync(path.join(__dirname,'../src/pages/experience.css'),'utf8')}));
  await page.route('**/auth.js',route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../src/pages/auth.js'),'utf8')}));
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname, post = request.method() === 'POST';
    apiRequests.push({path,method:request.method()});
    const json = (value, status = 200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
    if (post) {
      writes.push(path);
      assert.equal(request.headers()['x-csrftoken'], state.csrf_token, `Current CSRF header for ${path}`);
    }
    if (path === '/api/billing/catalog' || path === '/api/billing/quotes') return route.fallback();
    if (path === '/api/auth/session') { sessionReads++; return json(state); }
    if (path === '/api/billing/purchase-catalog') return json({account_id:state.active_account_id,currency:'CNY',items:[]});
    if (path === `/api/billing/purchase-quotes/${lifecycleQuoteId}`) return json({id:lifecycleQuoteId,account_id:state.active_account_id,currency:'CNY',amount_minor:19900,
      expires_at:'2099-01-01T00:00:00Z',can_order:false,order_id:null,unavailable_reason:'CONFIG_INCOMPLETE',
      snapshot:{kind:'subscription',code:'pro',billing_cycle:'monthly',amount_minor:19900,credits_quantity:'731',version:1,seat_quantity:1,entitlements:{benefits:{zh:['Fixture private quote benefit'],en:['Fixture private quote benefit']}}}});
    if (path === '/api/billing/my-usage') return json({account_id:wrongUsage ? one : state.active_account_id,member:{id:1,user_id:state.user.id,active:true,seat_position:2,shared_limit:'50',shared_used:null,shared_reserved:null},subscription_active:false,period_start:null,period_end:null,can_edit:false});
    if (path === '/api/trial') {
      assert.equal(request.method(),'GET');
      trialReads++;
      const status = !state.authenticated ? 401 : trialFails ? 503 : 200;
      const data = status === 401 ? {error:{code:'AUTH_REQUIRED'}} : status === 503 ? {error:{code:'UNAVAILABLE'}} : structuredClone(trialRecord);
      if (delayedTrial) { const held = delayedTrial; delayedTrial = null; held.started(); await held.promise; }
      return json(data,status);
    }
    if (path === '/api/auth/challenges') return json({challenge_id:'fixture-challenge',expires_in:300,resend_after:60});
    if (path === '/api/auth/verify') {
      if (verifyFails) return json({error:{code:'INVALID_CODE',message:'fixture rejection'}},400);
      state = makeSession(); return json(state);
    }
    if (path === '/api/auth/logout') {
      const failed = logoutFails;
      if (!failed) state = makeSession(false);
      if (heldLogout) { const held=heldLogout;heldLogout=null;held.started();await held.promise; }
      return failed ? json({error:{code:'UNAVAILABLE',message:'fixture outage'}},503) : json({ok:true});
    }
    if (path === '/api/billing/accounts/select') {
      state = makeSession(true,request.postDataJSON().account_id);
      const captured = structuredClone(state);
      if (heldSelection) { const held=heldSelection;heldSelection=null;held.started();await held.promise;if(held.status===401)return json({error:{code:'AUTH_REQUIRED'}},401); }
      return json(captured);
    }
    if (path === '/api/billing/account') {
      assert.equal(request.headers()['x-billing-account'],state.active_account_id);
      const id = state.active_account_id, data = makeAccount(wrongAccount ? two : id);
      if (heldAccount) { const held=heldAccount;heldAccount=null;held.started();await held.promise;if(held.status===401)return json({error:{code:'AUTH_REQUIRED'}},401); }
      if (creditFixture) data.credits = creditFixture;
      data.capabilities.subscription = subscriptionsEnabled;
      if (oldAccountDelay && id === one) {
        oldAccountDelay = false; const unauthorized = oldAccountUnauthorized; oldAccountUnauthorized = false;
        await new Promise(resolve => setTimeout(resolve,500));
        if (unauthorized) return json({error:{code:'AUTH_REQUIRED',message:'fixture delayed expired request'}},401);
      }
      if (accountFails) return json({error:{code:'UNAVAILABLE',message:'fixture outage'}},503);
      if (withOrder) data.orders = [{id:orderId,plan_code:'pro',amount_minor:19901,currency:'CNY',status:'pending_payment',created_at:'2026-10-02T00:00:00Z'}];
      return json(data);
    }
    if (path === '/api/billing/orders') return json({account_id:state.active_account_id,
      items:withOrder?[{id:orderId,account_id:state.active_account_id,plan_code:'pro',amount_minor:19901,currency:'CNY',status:'pending_payment',created_at:'2026-10-02T00:00:00Z'}]:[],next_offset:null});
    if (path === `/api/billing/orders/${orderId}`) return json({order:{id:orderId,plan_code:'pro',amount_minor:19901,currency:'CNY',status:orderPhase,created_at:'2026-10-02T00:00:00Z'},payments:[],refunds:refundHistory,delivery:{status:orderPhase === 'fulfilled' ? 'completed' : orderPhase === 'paid_pending_entitlement' ? 'pending' : 'not_started'},payment_attempt:attemptState ? {status:attemptState,needs_reconciliation:attemptState === 'review'} : null});
    if (path === `/api/billing/orders/${orderId}/refund-assessment`) {
      const refundAccount = state.active_account_id;
      if (refundDelay) { refundDelay = false; await new Promise(resolve => setTimeout(resolve,500)); }
      if (refundFails) return json({error:{code:'UNAVAILABLE'}},503);
      if (refundHistory.some(item=>item.state !== 'released')) return json({order_id:orderId,account_id:refundAccount,can_submit:false,currency:'CNY',status:'unavailable',reason:'refund_review_required'});
      return json({order_id:orderId,account_id:refundWrongAccount ? two : refundAccount,can_submit:false,currency:'CNY',
        status:'assessment_ready',reason:refundReason,scope:'all_unused_paid_credits',
        unused_quantity:'100',paid_minor:'19901',reference_minor:refundReason === 'rounding_policy_pending' ? null : '19901'});
    }
    if (path === '/api/billing/subscriptions') {
      assert.equal(request.headers()['x-billing-account'],state.active_account_id);
      const items = state.active_account_id === one ? subscriptions : [];
      if (delaySubscriptions && state.active_account_id === one) { delaySubscriptions = false; await new Promise(resolve => setTimeout(resolve,500)); }
      return subscriptionsFail ? json({error:{code:'UNAVAILABLE'}},503) : json({items});
    }
    if (path === '/api/billing/automatic-topup') {
      assert.equal(request.headers()['x-billing-account'],two);
      if (topupFails) return json({error:{code:'UNAVAILABLE'}},503);
      return json({account_id:two,charging_enabled:false,
        authorization:{state:'paused',version:1,pause_reason:'credits_refund',threshold:'10',pack_credits:'100',
          pack_amount_minor:'731',monthly_cap_minor:'9007199254740993',currency:'CNY'},budget:topupBudget});
    }
    if (path === '/api/billing/payment-methods') {
      assert.equal(request.headers()['x-billing-account'],state.active_account_id);
      if (methodsFail) return json({error:{code:'UNAVAILABLE'}},503);
      const capturedAccount = state.active_account_id;
      const capturedTopupMode = methodTopupMode;
      if (methodsDelay) await new Promise(resolve=>{releaseMethod=resolve;});
      const offset = Number(new URL(request.url()).searchParams.get('offset') || 0);
      let topups = ['superseded','revoking','revoked','paused','authorized'].map((state,index)=>({
        id:`55555555-5555-4555-8555-${String(index+1).padStart(12,'0')}`,state,version:index===4?2147483647:index+1,
        created_at:'2026-10-01T00:00:00Z',revocation_requested_at:['revoking','revoked'].includes(state)?'2026-10-01T01:00:00Z':null,
        revoked_at:state==='revoked'?'2026-10-01T02:00:00Z':null}));
      if (capturedTopupMode === 'empty') topups=[];
      if (capturedTopupMode === 'missing') topups=undefined;
      if (capturedTopupMode === 'unsafe-version') topups[0].version=9007199254740992;
      if (capturedTopupMode === 'string-version') topups[0].version='1';
      if (capturedTopupMode === 'bad-date') topups[0].created_at='not-a-date';
      if (capturedTopupMode === 'missing-confirmation') topups[2].revoked_at=null;
      if (capturedTopupMode === 'duplicate') topups[1].id=topups[0].id;
      if (capturedTopupMode === 'unknown') topups[0].state='unrecognized';
      return json({account_id:methodsWrongAccount ? one : capturedAccount,total:51,offset,
        next_offset:offset === 0 ? 50 : null,can_edit:false,has_unbound_authorizations:true,automatic_topup_supported:false,
        items:[{id:orderId,channel:offset ? 'card' : 'alipay',state:offset ? 'removed' : 'removing',
          removal_requested_at:'2026-10-01T01:00:00Z',removed_at:offset ? '2026-10-01T02:00:00Z' : null,
          subscriptions:[{id:subscriptionId,cancellation_state:offset ? 'completed' : 'processing'}],
          retired_authorizations:[{id:one,subscription_id:subscriptionId,state:offset ? 'completed' : 'processing'}],
          topup_authorizations:topups,
          unresolved_payments:offset ? [] : [{id:one,status:'unknown'}]}]});
    }
    if (path === '/api/billing/organization') {
      assert.equal(request.headers()['x-billing-account'],two);
      const offset = Number(new URL(request.url()).searchParams.get('offset') || 0);
      return json({account_id:two,total_members:51,offset,next_offset:offset === 0 ? 50 : null,
        seat_count:2,assigned_seats:1,subscription_active:true,period_start:'2026-10-01T00:00:00Z',period_end:'2026-11-01T00:00:00Z',can_edit:false,
        members:[{id:offset+1,masked_identifier:offset ? 'z***@example.test' : 'f***@example.test',role:'member',active:true,
          seat_position:offset ? null : 1,shared_limit:offset ? '0' : '50',shared_used:'10',shared_reserved:'2'}]});
    }
    if (path === cancelPath && post) {
      assert.equal(request.headers()['x-billing-account'],one);
      assert.deepEqual(request.postDataJSON(),{});
      subscriptions = subscriptions.map(item => ({...item,auto_renew:false,cancellation_state:'processing',can_cancel:false}));
      // Accepted write followed by an unavailable response: never auto-resubmit.
      if (cancelFail) return json({error:{code:'UNAVAILABLE'}},503);
      return json({subscription:subscriptions[0]});
    }
    if (path === '/api/billing/purchase-inquiries') {
      assert.equal(request.headers()['x-billing-account'],state.active_account_id);
      if (post && changedAccount) return json({error:{code:'ACCOUNT_CHANGED',message:'fixture account switch'}},409);
      if (post && expiredPurchase) { state = makeSession(false); return json({error:{code:'AUTH_REQUIRED',message:'fixture expired session'}},401); }
      if (post && inquiryPostUnknown) return json({error:{code:'REQUEST_FAILED'}},500);
      if (post) { const inquiry = {...request.postDataJSON(),id:'22222222-2222-4222-8222-222222222222',status:'received',created_at:'2026-10-02T00:00:00Z'}; inquiries.unshift(inquiry); return json(inquiry); }
      const offset = Number(new URL(request.url()).searchParams.get('offset') || 0);
      const response = {account_id:state.active_account_id,offset,total:inquiries.length,next_offset:offset+50 < inquiries.length ? offset+50 : null,items:inquiries.slice(offset,offset+50)};
      if (delayedInquiry) { delayedInquiry(); delayedInquiry = null; await new Promise(resolve => { releaseInquiry = resolve; }); }
      return json(response);
    }
    return json({error:{code:'FIXTURE_NOT_IMPLEMENTED',message:'Unexpected fixture route'}},404);
  });
  let visitId = 0;
  // Each fixture scenario starts a fresh document; hash-only navigation must not
  // be mistaken for a session reload after changing the fixture server state.
  const visit = path => { const url = new URL(`${base}/pages/${path}`); url.searchParams.set('ui-audit',String(++visitId)); return page.goto(url.href); };
  const textContains = async (selector,text) => { await page.waitForFunction(({selector,text}) => document.querySelector(selector)?.textContent.includes(text), {selector,text}); };
  try {
    state = makeSession(false); state.capabilities.email_login = false; state.capabilities.phone_login = false;
    await visit('auth.html?return=https%3A%2F%2Fevil.example');
    await textContains('#auth-feedback','暂不可用');
    assert.ok(await page.locator('.auth-page').evaluate(el => el.getBoundingClientRect().width <= 550), 'Desktop auth form must remain a readable narrow column');
    assert.equal(await page.locator('#auth-contact').isDisabled(),true);
    assert.equal(await page.locator('#request-code').isDisabled(),true);
    assert.ok((await page.locator('.auth-back').getAttribute('href')).endsWith('billing.html'));
    assert.equal(writes.length,0); checks.push('Unavailable verification blocks contact entry/send; external return rejected');
    state = makeSession(false);
    await visit('auth.html?return=billing.html%23credits');
    await page.locator('#auth-contact').fill('not-an-email'); await page.locator('#request-code').click();
    assert.equal(await page.locator('#auth-contact').getAttribute('aria-invalid'),'true'); assert.equal(writes.length,0);
    await page.locator('#auth-contact').fill('fixture@example.test'); await page.locator('#request-code').click();
    await textContains('#auth-feedback','已发送'); assert.equal(await page.locator('#request-code').isDisabled(),true);
    assert.equal(await page.locator('#auth-code').evaluate(el => el === document.activeElement),true);
    verifyFails = true; await page.locator('#auth-code').fill('123456'); await page.locator('#auth-verify').click();
    await textContains('#auth-feedback','尚未登录'); assert.ok(page.url().includes('auth.html'));
    verifyFails = false; await page.locator('#auth-code').fill('123456'); await page.locator('#auth-verify').click();
    await page.waitForURL('**/billing.html#credits'); await textContains('.account-metrics','0.00');
    assert.deepEqual(await page.evaluate(() => ({local:localStorage.length,session:sessionStorage.length})),{local:0,session:0});
    checks.push('Validation, CSRF, cooldown, failed OTP and verified return; no browser credential/balance storage');
    accountFails = true; await page.reload(); await textContains('#account-live','未知');
    assert.equal(await page.locator('.account-metrics').count(),0);
    accountFails = false; await page.locator('#account-read-retry').click(); await textContains('.account-metrics','0.00');
    checks.push('Read failures are unknown, not zero; retry restores actual zero');
    wrongAccount = true; await page.reload(); await textContains('#account-live','未知'); assert.equal(await page.locator('.account-metrics').count(),0); wrongAccount = false;
    checks.push('Wrong-account response rejected');
    oldAccountDelay = true; state = makeSession(); await page.reload();
    await page.locator('.nav-account summary').click(); await page.locator('#billing-account-select').selectOption(two);
    await textContains('.account-metrics','27.25'); await page.waitForTimeout(650);
    assert.ok((await page.locator('#account-live').innerText()).includes('Fixture business'));
    assert.ok(!(await page.locator('#account-live').innerText()).includes('Fixture personal'));
    checks.push('Delayed old-account response cannot repopulate after switching');
    oldAccountDelay = true; oldAccountUnauthorized = true; state = makeSession(); await page.reload();
    await page.locator('.nav-account summary').click(); await page.locator('#billing-account-select').selectOption(two);
    await textContains('.account-metrics','27.25'); await page.waitForTimeout(650);
    assert.ok((await page.locator('#account-live').innerText()).includes('Fixture business'));
    assert.equal(await page.locator('#session-alert').count(),0);
    checks.push('Delayed unauthorized response from the old account cannot clear the new account');
    logoutFails = true; await page.locator('.nav-account summary').click(); await page.locator('#account-logout').click();
    await textContains('#session-alert','退出尚未确认'); assert.equal(await page.locator('.account-metrics').count(),0);
    logoutFails = false; await page.locator('#session-alert button').click(); await textContains('#account-live','登录后');
    checks.push('Logout clears data immediately; backend failure explicit and retryable');
    state = makeSession(); withOrder = true; await visit('billing.html#orders'); await textContains('.record-list','199.01');
    await textContains('.record-list','待付款'); await page.locator('[data-order]').click(); await textContains('#order-detail','199.01'); await textContains('#order-detail','尚未开始');
    orderPhase = 'paid_pending_entitlement'; await page.locator('[data-order]').click(); await textContains('#order-detail','权益处理中');
    orderPhase = 'fulfilled'; await page.locator('[data-order]').click(); await textContains('#order-detail','已交付');
    await visit('billing-en.html#orders'); await textContains('.record-list','Awaiting payment'); await page.locator('[data-order]').click(); await textContains('#order-detail','Fulfilled'); withOrder = false; orderPhase = 'pending_payment';
    checks.push('Order list/detail retain integer-minor-unit precision');
    await textContains('#refund-assessment','not an approved refund');
    refundFails = true; await page.locator('[data-refund-refresh]').click();
    await textContains('#refund-assessment','unknown, not ineligible');
    refundFails = false; refundReason = 'rounding_policy_pending';
    await page.locator('[data-refund-refresh]').click();
    await textContains('#refund-assessment','Not yet confirmed');
    assert.equal(await page.locator('#refund-assessment input').count(),0);
    refundReason = 'policy_review_required';
    refundWrongAccount = true; await page.locator('[data-refund-refresh]').click();
    await textContains('#refund-assessment','unknown, not ineligible'); refundWrongAccount = false;
    withOrder = true;
    orderPhase = 'fulfilled';
    refundHistory = [{id:orderId,state:'reserved',amount_minor:'19901',currency:'CNY',created_at:'2026-10-02T00:00:00Z',resolved_at:null}];
    for (const lang of ['', '-en']) {
      await page.setViewportSize({width:320,height:900});
      await visit(`billing${lang}.html#orders`); await page.locator('[data-order]').click();
      await textContains('#refund-assessment',lang ? 'existing refund status requires review' : '需要核对原退款处理状态');
      await textContains('#refund-history',lang ? 'amount reserved' : '金额已保留');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth+1),true);
      await page.screenshot({path:path.resolve(__dirname,`../tmp/billing-design-qa/refund-assessment-320${lang}.png`),fullPage:true});
    }
    refundHistory[0].state = 'refunded'; refundHistory[0].resolved_at = '2026-10-02T01:00:00Z';
    await page.locator('[data-refund-history-refresh]').click();
    await textContains('#refund-history','Refund confirmed');
    refundHistory[0].state = 'released'; await page.locator('[data-refund-history-refresh]').click();
    await textContains('#refund-history','original credit expiry date still applies');
    refundHistory = [];
    checks.push('Refund history distinguishes reserved, confirmed and terminated without resubmitting or exposing internal evidence');
    refundDelay = true; await page.locator('[data-refund-refresh]').click();
    await page.locator('a[href="#credits"]').click();
    await page.waitForTimeout(600);
    assert.equal(await page.locator('#refund-assessment').count(),0);
    await page.setViewportSize({width:1440,height:1000});
    checks.push('Refund assessment shows unapproved reference values, unknown errors and unresolved rounding without editable amounts or writes');
    withOrder = true; attemptState = 'unknown'; await visit('billing.html#orders'); await page.locator('[data-order]').click(); await textContains('#order-detail','请勿重复付款');
    const writesBeforeQuery = writes.length;
    attemptState = 'succeeded'; orderPhase = 'paid_pending_entitlement'; await page.locator('#order-status-refresh').click(); await textContains('#order-detail','权益仍在处理中');
    attemptState = 'review'; await page.locator('#order-status-refresh').click(); await textContains('#order-detail','待核实事项');
    attemptState = 'expired'; orderPhase = 'closed'; await page.locator('#order-status-refresh').click(); await textContains('#order-detail','系统不会自动重试');
    assert.equal(writes.length,writesBeforeQuery);
    attemptState = null; orderPhase = 'pending_payment'; withOrder = false;
    checks.push('Unknown payment, paid-pending-delivery, reconciliation and closed states; refresh never writes');
    subscriptionsEnabled = false; await visit('billing.html#subscription'); await textContains('#account-live','尚未接入'); subscriptionsEnabled = true;
    await visit('billing.html#procurement'); await page.locator('#purchase-company').fill('Fixture company'); await page.locator('#purchase-email').fill('fixture@example.test'); await page.locator('#purchase-requirements').fill('Fixture purchasing request');
    await page.locator('#procurement-form button[type=submit]').click(); await textContains('#procurement-feedback','服务端接收'); await textContains('#procurement-records','Fixture company');
    checks.push('Unavailable capability stays explicit; procurement server acknowledgment displayed');
    await textContains('#procurement-feedback','22222222-2222-4222-8222-222222222222');
    assert.equal(await page.locator('#purchase-company').getAttribute('maxlength'),'160');
    assert.equal(await page.locator('#purchase-requirements').getAttribute('maxlength'),'2000');
    const savedInquiries = inquiries;
    inquiries = Array.from({length:51},(_,i)=>({...savedInquiries[0],id:`33333333-3333-4333-8333-${String(i).padStart(12,'0')}`,company:`History ${i}`,requirements:'<img src=x onerror="window.fixtureXss=true">\nSaved requirements'}));
    await page.getByRole('button',{name:'刷新申请记录',exact:true}).click(); await textContains('#procurement-records','History 0');
    await page.locator('#procurement-records summary').first().click();
    await textContains('#procurement-records','<img src=x'); assert.equal(await page.locator('#procurement-records img').count(),0);
    await page.locator('[data-inquiries-next]').click(); await textContains('#procurement-records','History 50');
    assert.equal(await page.locator('#procurement-records li').count(),1);
    await page.locator('[data-inquiries-prev]').click(); await textContains('#procurement-records','History 0');
    const inquiryStarted = new Promise(resolve=>{delayedInquiry=resolve;});
    await page.getByRole('button',{name:'刷新申请记录',exact:true}).click(); await inquiryStarted;
    inquiries = [{...savedInquiries[0],company:'Latest saved inquiry'}];
    await page.getByRole('button',{name:'刷新申请记录',exact:true}).click(); await textContains('#procurement-records','Latest saved inquiry');
    releaseInquiry(); await page.waitForTimeout(100);
    assert.ok(!(await page.locator('#procurement-records').innerText()).includes('History 0'));
    inquiryPostUnknown = true;
    await page.locator('#purchase-company').fill('Unconfirmed draft'); await page.locator('#purchase-email').fill('fixture@example.test'); await page.locator('#purchase-requirements').fill('Draft stays available');
    const beforeUnknown = writes.length;
    await page.locator('#procurement-form button[type=submit]').click(); await textContains('#procurement-feedback','提交结果未确认');
    await page.getByRole('button',{name:'刷新申请记录',exact:true}).click(); await textContains('#procurement-records','Latest saved inquiry');
    assert.equal(writes.length,beforeUnknown+1); assert.equal(await page.locator('#purchase-company').inputValue(),'Unconfirmed draft');
    inquiryPostUnknown = false; inquiries = savedInquiries;
    for (const lang of ['', '-en']) {
      await page.setViewportSize({width:320,height:900}); await visit(`billing${lang}.html#procurement`);
      await textContains('#procurement-records','22222222-2222-4222-8222-222222222222');
      await page.locator('#procurement-records summary').first().click();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth+1),true,`procurement ${lang} 320`);
    }
    await page.setViewportSize({width:1440,height:1000}); await visit('billing.html#procurement');
    checks.push('Procurement receipt, escaped saved requirements, pagination, stale reads, unknown submission and bilingual 320px');
    changedAccount = true; await page.locator('#purchase-company').fill('Fixture company'); await page.locator('#purchase-email').fill('fixture@example.test'); await page.locator('#purchase-requirements').fill('Fixture conflict');
    await page.locator('#procurement-form button[type=submit]').click(); await textContains('#session-alert','其他页面变更');
    assert.equal(await page.locator('#procurement-form').count(),0); assert.equal(inquiries.length,1); changedAccount = false;
    checks.push('Cross-tab account mismatch clears stale form without retrying its write');
    state = makeSession(); expiredPurchase = true; await visit('billing.html#procurement');
    await page.locator('#purchase-company').fill('Fixture secret draft'); await page.locator('#purchase-email').fill('fixture@example.test'); await page.locator('#purchase-requirements').fill('Fixture expired request');
    await page.locator('#procurement-form button[type=submit]').click(); await textContains('#session-alert','登录已失效');
    assert.equal(await page.locator('#procurement-form').count(),0); assert.ok(!(await page.locator('body').innerText()).includes('Fixture secret draft')); assert.equal(inquiries.length,1); expiredPurchase = false;
    checks.push('Current-account 401 clears sensitive forms and requires sign-in');
    state = makeSession(true,two); state.accounts = accounts.map(item => item.id === two ? {...item,role:'member'} : item);
    const savedRole = accounts[1].role; accounts[1].role = 'member';
    await visit('billing.html#procurement'); await textContains('#account-live','仅企业管理员'); assert.equal(await page.locator('#procurement-form').count(),0); accounts[1].role = savedRole;
    checks.push('Business member cannot open procurement submission');
    state = makeSession(); state.user.display_name = ''; const originalName = accounts[0].name; accounts[0].name = '个人账户';
    await visit('billing-en.html#credits'); await textContains('.account-context','Personal account');
    assert.equal(await page.locator('.nav-account summary span').textContent(),'f***@example.test'); accounts[0].name = originalName;
    checks.push('English localizes the known default account name and uses masked identity without inventing a user name');
    state = makeSession(false); await visit('auth-en.html?return=billing.html%3Fcycle%3Dmonthly%23credits');
    await page.locator('#auth-contact').fill('fixture@example.test'); await page.locator('#request-code').click();
    await textContains('#auth-feedback','Code sent'); await page.locator('#auth-code').fill('123456'); await page.locator('#auth-verify').click();
    await page.waitForURL(url => url.pathname.endsWith('/billing-en.html') && url.searchParams.get('cycle') === 'monthly' && url.hash === '#credits');
    await textContains('.account-metrics','0.00'); checks.push('English verification localizes a Chinese return route and retains query/hash');
    state = makeSession(); await visit('billing.html#subscription'); await textContains('.subscription-card','按年计费');
    assert.equal(await page.locator('.subscription-card').evaluate(el => getComputedStyle(el).borderRadius),'18px');
    const beforeCancel = writes.filter(path => path === cancelPath).length;
    await page.locator('[data-cancel-open]').click();
    assert.equal(await page.locator('.subscription-confirm h4').evaluate(el => el === document.activeElement),true);
    await page.locator('[data-cancel-back]').click();
    assert.equal(writes.filter(path => path === cancelPath).length,beforeCancel);
    assert.equal(await page.locator('[data-cancel-open]').evaluate(el => el === document.activeElement),true);
    await page.locator('[data-cancel-open]').click(); await page.locator('[data-cancel-confirm]').click();
    await textContains('.subscription-renewal','取消处理中');
    assert.equal(writes.filter(path => path === cancelPath).length,beforeCancel+1);
    assert.equal(await page.locator('.subscription-service').textContent(),'生效中');
    assert.ok(!(await page.locator('.subscription-renewal').innerText()).includes('已取消'));
    assert.equal(await page.locator('[data-cancel-open]').count(),0);
    subscriptions[0].cancellation_state = 'completed'; await page.locator('#subscriptions-refresh').click(); await textContains('.subscription-renewal','自动续费已取消');
    checks.push('Cancellation requires confirmation; focus restored; one request; processing distinct from channel-confirmed cancellation; service retained');
    subscriptions = [makeSubscription()]; cancelFail = true;
    await visit('billing-en.html#subscription'); await page.locator('[data-cancel-open]').click(); await page.locator('[data-cancel-confirm]').click();
    await textContains('.subscription-renewal','Cancellation unconfirmed');
    const afterUnknown = writes.length;
    assert.equal(await page.locator('[data-cancel-open]').count(),0);
    cancelFail = false; await page.locator('#subscriptions-refresh').click(); await textContains('.subscription-renewal','Cancellation processing');
    assert.equal(writes.length,afterUnknown);
    checks.push('Lost cancellation response becomes unknown; refresh only reads and never resubmits');
    subscriptionsFail = true; await page.locator('#subscriptions-refresh').click(); await textContains('#account-live','could not be confirmed');
    assert.equal(await page.locator('.subscription-card').count(),0);
    subscriptionsFail = false; subscriptions = []; await page.locator('#subscriptions-refresh').click(); await textContains('#account-live','has no subscriptions');
    subscriptions = [makeSubscription(),makeSubscription()]; await page.locator('#subscriptions-refresh').click(); await textContains('#account-live','could not be confirmed');
    checks.push('Subscription errors and malformed duplicate IDs are not mistaken for an empty account');
    subscriptions = [{...makeSubscription(),status:'expired',auto_renew:false,cancellation_state:'completed',can_cancel:false,next_credit_grant_at:null}];
    await visit('billing.html#subscription'); await textContains('.subscription-service','已到期'); assert.equal(await page.locator('[data-cancel-open]').count(),0);
    subscriptions = [{...makeSubscription(),status:'scheduled'}]; await page.locator('#subscriptions-refresh').click(); await textContains('.subscription-service','待生效');
    checks.push('Expired and scheduled paid terms remain distinct');
    const renewal = {status:'failed',manual_payment_deadline:'2027-02-03T01:00:00+08:00',manual_payment_eligible:true,new_subscription_payment_eligible:false,query_original_payment:false,reauthorization_required:false};
    subscriptions = [{...makeSubscription(),status:'expired',next_credit_grant_at:null,renewal}];
    await visit('billing.html#subscription'); await textContains('.subscription-renewal','主动补缴窗口截止');
    await textContains('.subscription-renewal','当前支付渠道尚未开放');
    assert.equal(await page.getByRole('button',{name:'立即补缴',exact:true}).count(),0);
    const beforeReviewRefresh = writes.length;
    subscriptions[0].renewal = {...renewal,status:'unknown',manual_payment_eligible:false,query_original_payment:true};
    await page.locator('#subscriptions-refresh').click(); await textContains('.subscription-renewal','请勿重复付款');
    assert.equal(writes.length,beforeReviewRefresh);
    subscriptions[0].renewal = {...renewal,manual_payment_eligible:false,reauthorization_required:true};
    await visit('billing-en.html#subscription'); await textContains('.subscription-renewal','Manual renewal window ended');
    checks.push('Failed/unknown/closed renewal states are bilingual, read-only and never offer unconnected payment');
    const beforeDuplicateReview = writes.length;
    for (const lang of ['', '-en']) {
      subscriptions = [{...makeSubscription(),renewal:{...renewal,status:'review',manual_payment_eligible:false,query_original_payment:true}}];
      await visit(`billing${lang}.html#subscription`);
      await textContains('.renewal-payment-title',lang ? 'Renewal payment under review' : '续费结果待核实');
      await textContains('.subscription-renewal',lang ? 'Do not pay again' : '请勿重复付款');
      assert.equal(await page.getByRole('button',{name:lang ? 'Pay now' : '立即补缴',exact:true}).count(),0);
      await page.locator('#subscriptions-refresh').click();
      await textContains('.renewal-payment-title',lang ? 'Renewal payment under review' : '续费结果待核实');
      subscriptions[0].renewal = {...renewal,status:'succeeded',manual_payment_eligible:false,query_original_payment:false};
      await page.locator('#subscriptions-refresh').click();
      await textContains('.renewal-payment-title',lang ? 'Renewal payment confirmed' : '续费款项已确认');
      assert.equal(writes.length,beforeDuplicateReview);
    }
    checks.push('Renewal collection review and normal accepted payment remain distinct in both languages; refresh performs no write or repeated charge');
    subscriptions = [makeSubscription()]; delaySubscriptions = true;
    const delayedRead = page.waitForRequest(request => new URL(request.url()).pathname === '/api/billing/subscriptions');
    await visit('billing.html#subscription'); await delayedRead;
    await page.locator('.nav-account summary').click(); await page.locator('#billing-account-select').selectOption(two);
    await textContains('#account-live','尚无订阅'); await page.waitForTimeout(650);
    assert.equal(await page.locator('.subscription-card').count(),0);
    checks.push('Old-account subscription response cannot reveal an old subscription after switching');
    state = makeSession();
    subscriptions[0].renewal = {...renewal,status:'unknown',manual_payment_eligible:false,query_original_payment:true};
    for (const width of [320,390,768,1440]) for (const lang of ['', '-en']) {
      await page.setViewportSize({width,height:1000}); await visit(`billing${lang}.html#subscription`); await page.locator('[data-cancel-open]').click();
      if ((width === 390 && lang === '-en') || (width === 1440 && !lang)) {
        const directory = path.resolve(__dirname,'../tmp/billing-design-qa'); fs.mkdirSync(directory,{recursive:true});
        assert.ok(await page.locator('.skip').evaluate(el => el.getBoundingClientRect().bottom <= 0), 'Skip link stays outside the viewport until keyboard focus');
        await page.evaluate(() => scrollTo(0,0));
        await page.screenshot({path:path.join(directory,`subscription-${width}${lang}.png`),fullPage:true});
      }
      // Match the existing product's text zoom check; geometry remains real CSS pixels.
      await page.locator('.subscription-card *').evaluateAll(nodes => { const sizes = nodes.map(el => parseFloat(getComputedStyle(el).fontSize)); nodes.forEach((el,i) => el.style.fontSize = `${sizes[i]*2}px`); });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),true,`subscription ${lang} ${width} 200% text`);
    }
    checks.push('Subscription and confirmation fit Chinese/English at 320/390/768/1440 with 200% text');
    for (const route of ['auth.html','auth-en.html','billing.html#credits','billing-en.html#credits','pricing.html','checkout.html?plan=pro&cycle=monthly']) {
      state = route.startsWith('auth') ? makeSession(false) : makeSession();
      await page.setViewportSize({width:390,height:844}); await visit(route); await page.waitForSelector('main');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),true,route);
    }
    checks.push('Six mobile account/billing routes stay within viewport');
    state = makeSession(true,two);
    const beforeOrg = writes.length;
    for (const width of [320,1440]) for (const lang of ['', '-en']) {
      await page.setViewportSize({width,height:1000}); await visit(`billing${lang}.html#organization`);
      await textContains('.organization-members','f***@example.test');
      await page.locator('[data-org-offset="50"]').click();
      await textContains('.organization-members','z***@example.test');
      await page.locator('[data-org-offset="0"]').click();
      await textContains('.organization-members','f***@example.test');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),true,`organization ${lang} ${width}`);
      const directory = path.resolve(__dirname,'../tmp/billing-design-qa'); fs.mkdirSync(directory,{recursive:true});
      await page.screenshot({path:path.join(directory,`organization-${width}${lang}.png`),fullPage:true});
    }
    assert.equal(writes.length,beforeOrg);
    checks.push('Organization read-only members and pagination work in Chinese/English on mobile and desktop without writes');
    const beforeMethods = writes.length;
    for (const width of [320,390,768,1440]) for (const lang of ['', '-en']) {
      await page.setViewportSize({width,height:1000}); await visit(`billing${lang}.html#payment-methods`);
      await textContains('#account-live',lang ? 'Removal in progress' : '移除处理中');
      await textContains('#account-live',lang ? 'This list is incomplete' : '当前列表不完整');
      await textContains('#account-live',lang ? 'Previous authorizations' : '更换前的授权');
      await textContains('#account-live',lang ? 'does not confirm revocation' : '不代表旧授权已撤销');
      await textContains('#account-live',lang ? 'Automatic top-up authorizations' : '自动充值授权');
      await textContains('#account-live',lang ? 'Superseded; revocation not confirmed' : '已被新授权替代，未确认撤销');
      await textContains('#account-live',lang ? 'Version 2147483647' : '版本 2147483647');
      await textContains('#account-live',lang ? 'Automatic charges are not enabled' : '自动扣款尚未启用');
      assert.equal(await page.locator('[data-method-topup]').count(),5);
      assert.equal(await page.locator('[data-method-topup] button').count(),0);
      await page.locator('[data-method-offset="50"]').focus();
      await page.keyboard.press('Enter');
      await textContains('#account-live',lang ? 'Revocation confirmed' : '撤销已确认');
      assert.equal(await page.locator('#payment-methods-title').evaluate(el=>el===document.activeElement),true);
      await page.locator('[data-method-offset="0"]').focus();
      await page.keyboard.press('Enter');
      await textContains('#account-live',lang ? 'Payments requiring review' : '待核对交易');
      assert.equal(await page.locator('#payment-methods-title').evaluate(el=>el===document.activeElement),true);
      await page.locator('[data-method-refresh]').click();
      await textContains('#account-live',lang ? 'Removal in progress' : '移除处理中');
      assert.equal(await page.locator('#payment-methods-title').evaluate(el=>el===document.activeElement),true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),true);
      await page.locator('main *').evaluateAll(nodes=>{const sizes=nodes.map(el=>parseFloat(getComputedStyle(el).fontSize));nodes.forEach((el,i)=>el.style.fontSize=`${sizes[i]*2}px`);});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`payment methods ${width} ${lang} 200% text`);
      await page.locator('main *').evaluateAll(nodes=>nodes.forEach(el=>el.style.removeProperty('font-size')));
      const directory = path.resolve(__dirname,'../tmp/billing-design-qa');
      await page.locator('body').click({position:{x:1,y:1}});
      await page.screenshot({path:path.join(directory,`payment-methods-${width}${lang}.png`),fullPage:true});
    }
    methodsFail = true; await visit('billing.html#payment-methods');
    await textContains('#account-live','状态未知');
    methodsFail = false; await page.locator('[data-method-retry]').click();
    await textContains('#account-live','移除处理中');
    methodsWrongAccount = true; await page.locator('[data-method-refresh]').click();
    await textContains('#account-live','状态未知');
    methodsWrongAccount = false;
    for (methodTopupMode of ['missing','unsafe-version','string-version','bad-date','missing-confirmation','duplicate','unknown']) {
      await visit('billing.html#payment-methods');
      await textContains('#account-live','状态未知');
      assert.equal(await page.locator('[data-method-topup]').count(),0);
    }
    methodTopupMode='empty';await page.locator('[data-method-retry]').click();
    await textContains('#account-live','没有已关联的自动充值授权记录');
    methodTopupMode='normal';methodsDelay=true;releaseMethod=null;
    await page.locator('[data-method-refresh]').click();
    while(!releaseMethod) await new Promise(resolve=>setTimeout(resolve,10));
    state=makeSession(true,one);methodsDelay=false;methodTopupMode='empty';
    await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));
    await textContains('#account-live','没有已关联的自动充值授权记录');
    releaseMethod();await page.waitForTimeout(100);
    assert.equal(await page.locator('[data-method-topup]').count(),0);
    state=makeSession(true,two);methodTopupMode='normal';
    assert.equal(writes.length,beforeMethods);
    checks.push('Payment-method top-up records: independent states/dates/versions, empty vs unknown, stale account response, 8 bilingual layouts/200% text; no writes');
    for (const lang of ['', '-en']) {
      await page.setViewportSize({width:320,height:1000});
      await visit(`billing${lang}.html#payment-methods`);
      await textContains('#account-live',lang ? 'Removal in progress' : '移除处理中');
      await page.locator('[data-method-offset="50"]').focus();
      methodsFail=true;await page.keyboard.press('Enter');
      await textContains('#payment-methods-error',lang ? 'status is unknown' : '状态未知');
      assert.equal(await page.locator('#payment-methods-error').getAttribute('role'),'status');
      assert.equal(await page.locator('#payment-methods-error').evaluate(el=>el===document.activeElement),true);
      await page.keyboard.press('Tab');
      assert.equal(await page.locator('[data-method-retry]').evaluate(el=>el===document.activeElement),true);
      methodsFail=false;await page.keyboard.press('Space');
      await textContains('#account-live',lang ? 'Revocation confirmed' : '撤销已确认');
      assert.equal(await page.locator('#payment-methods-title').evaluate(el=>el===document.activeElement),true);
      await page.keyboard.press('Tab');
      assert.equal(await page.locator('[data-method-offset="0"]').evaluate(el=>el===document.activeElement),true);
      await page.keyboard.press('Shift+Tab');
      assert.ok(await page.evaluate(()=>document.activeElement!==document.body));
      assert.equal(writes.length,beforeMethods);
    }
    checks.push('Payment-method pagination uses Enter, Tab and Space; failure focuses a announced result, retry restores the result title, and keyboard navigation remains within a logical page order in both languages without writes');
    for (const width of [320,1440]) for (const lang of ['', '-en']) {
      await page.setViewportSize({width,height:1000}); await visit(`billing${lang}.html#credits`);
      await textContains('#account-live',lang ? 'Business auto top-up' : '企业自动充值');
      await textContains('#account-live',lang ? 'Paused' : '已暂停');
      await textContains('#account-live','90,071,992,547,409.93');
      await textContains('#account-live',lang ? 'does not mean monthly spending is zero' : '不能据此判断本月已用金额为零');
      await page.locator('[data-topup-refresh]').click();
      await textContains('#account-live',lang ? 'Automatic charges are not enabled' : '自动扣款尚未启用');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth+1),true);
      await page.locator('body').click({position:{x:1,y:1}});
      await page.screenshot({path:path.resolve(__dirname,`../tmp/billing-design-qa/automatic-topup-${width}${lang}.png`),fullPage:true});
    }
    checks.push('Business auto top-up consent is read-only, bilingual, responsive and never treats a missing budget as zero');
    topupBudget = {starts_at:'2026-10-01T00:00:00Z',ends_at:'2026-11-01T00:00:00Z',spent_minor:'731',reserved_minor:'731',remaining_minor:null,needs_review:true};
    await page.locator('[data-topup-refresh]').click();
    await textContains('#account-live','remaining budget is unknown');
    topupFails = true; await page.locator('[data-topup-refresh]').click();
    await textContains('#account-live','Authorization and budget status are unknown');
    topupFails = false; await page.locator('[data-topup-retry]').click();
    await textContains('#account-live','remaining budget is unknown');
    assert.equal(writes.length,beforeOrg);
    checks.push('Payment-method reads, incomplete-history notice, pagination, retry and account isolation work bilingually without writes');
    state = makeSession(); subscriptions = [makeSubscription()]; subscriptionsFail = false;
    await visit('billing.html#subscription');
    await textContains('.subscription-change-guide','连续包月');
    await textContains('.subscription-change-guide','2028');
    subscriptions[0].capabilities.cancel = false;
    await page.locator('#subscriptions-refresh').click();
    await textContains('.subscription-change-guide','取消入口不可用');
    assert.equal(await page.locator('[data-cancel-open]').count(),0);
    subscriptions[0].capabilities.cancel = true;
    subscriptions[0].cancellation_state = 'completed'; subscriptions[0].auto_renew = false;
    await page.locator('#subscriptions-refresh').click();
    await textContains('.subscription-change-guide','无需重复取消');
    subscriptions[0].plan_code = 'ultra';
    await visit('billing-en.html#subscription');
    await textContains('.subscription-change-guide','Pro / Pro+');
    await textContains('.subscription-change-guide','No downgrade is scheduled');
    checks.push('Plan-change guidance uses the paid-through date, distinguishes cancellation state and never schedules or charges a downgrade');
    const beforeUsage = writes.length;
    state = makeSession(true,two);
    for (const lang of ['', '-en']) {
      await visit(`billing${lang}.html#my-usage`);
      await textContains('#account-live',lang ? 'Your seat and shared pool' : '本人席位与共享池');
      await textContains('#account-live',lang ? 'unknown, not zero' : '未知，不表示零');
      await page.locator('#usage-refresh').click();
      await textContains('#account-live','50');
    }
    wrongUsage = true; await page.locator('#usage-refresh').click();
    await textContains('#account-live','unknown');
    assert.equal(await page.locator('#usage-refresh').count(),0);
    wrongUsage = false;
    assert.equal(writes.length,beforeUsage);
    checks.push('Own member limits are bilingual, refresh read-only, unknown when expired and reject wrong-account responses');
    const beforeCredits = writes.length;
    const amounts = {available:'10.125',reserved:'2',frozen:'3',consumed:'4',expired:'5',refunded:'6'};
    creditFixture = {...amounts,batches:[{...amounts,source:'purchased',expires_at:'2026-12-01T00:00:00Z'}]};
    for (const lang of ['', '-en']) {
      await page.setViewportSize({width:320,height:900});
      await visit(`billing${lang}.html#credits`);
      await textContains('.account-metrics',lang ? 'Refunded credits' : '已退款核销');
      assert.equal(await page.locator('.account-metrics').first().locator('dd').count(),6);
      assert.deepEqual(await page.locator('.account-metrics').first().locator('dd').allTextContents(),Object.values(amounts));
      await textContains('.record-list',lang ? 'Expired 5' : '已过期 5');
      await textContains('.record-list',lang ? 'Frozen 3' : '退款冻结 3');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth+1),true);
    }
    delete creditFixture.expired;
    await visit('billing.html#credits');
    assert.equal(await page.locator('.account-metrics').count(),0);
    await textContains('#account-live','未知');
    creditFixture = null;
    assert.equal(writes.length,beforeCredits);
    checks.push('All six credit totals and batch quantities render exactly; missing fields fail closed instead of becoming zero');
    const beforeTrial = writes.length;
    for (const lang of ['', '-en']) {
      state = makeSession(false);
      await visit(`trial${lang}.html`);
      await textContains('#saved-trial',lang ? 'Sign in' : '登录后');
      state = makeSession();
      await page.locator('#refresh-trial').click();
      await textContains('#saved-trial',lang ? 'one free trial available' : '一次免费体验资格');
      trialRecord = {eligibility:'locked',attempt:{scenario:'voc',input_value:'<img src=x onerror=alert(1)>',task_state:'running',report:null}};
      await page.locator('#refresh-trial').click();
      await textContains('#saved-trial',lang ? 'in progress' : '处理中');
      assert.equal(await page.locator('#saved-trial img').count(),0);
      trialRecord.eligibility = 'consumed'; trialRecord.attempt.task_state = 'completed';
      trialRecord.attempt.report = {title:'Saved fixture only',sections:[{finding:'Not a live analysis'}]};
      await page.locator('#refresh-trial').click();
      await textContains('#saved-trial','JSON');
      const downloading = page.waitForEvent('download');
      await page.locator('#saved-trial button').click();
      const download = await downloading;
      assert.equal(download.suggestedFilename(),'sage-trial-report.json');
      assert.deepEqual(JSON.parse(fs.readFileSync(await download.path(),'utf8')),trialRecord.attempt.report);
      assert.equal(await page.locator('.trial-report').count(),1);
      await textContains('.trial-report','Not a live analysis');
      trialRecord.attempt.report = Object.fromEntries(Array.from({length:1200},(_,i)=>[`field-${i}`,i]));
      await page.locator('#refresh-trial').click();
      await page.locator('.trial-report-raw').waitFor();
      assert.ok(await page.locator('.trial-report *').count()<20,'Wide report must not create an unbounded DOM');
      assert.deepEqual(JSON.parse(await page.locator('.trial-report-raw').textContent()),trialRecord.attempt.report);
      await page.setViewportSize({width:320,height:900});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth+1),true);
      await page.screenshot({path:path.resolve(__dirname,`../tmp/billing-design-qa/trial-recovery-320${lang}.png`),fullPage:true});
      trialFails = true; await page.locator('#refresh-trial').click();
      await textContains('#saved-trial',lang ? 'unknown' : '未知');
      assert.equal(await page.locator('#saved-trial button').count(),0);
      trialFails = false; trialRecord = {eligibility:'available',attempt:null};
      assert.equal(await page.locator('#start-trial').isDisabled(),true);
    }
    assert.equal(writes.length,beforeTrial);
    checks.push('Trial recovery is authenticated, bilingual, read-only, HTML-safe and downloads the stored report without creating a task');
    for (const lang of ['', '-en']) {
      state = makeSession(false);
      await visit(`trial${lang}.html?mission=shopify&example=shopify&result=obsolete&step=obsolete#trial-example`);
      await textContains('#saved-trial',lang ? 'Sign in' : '登录后');
      const context = new URL(page.url());
      assert.equal(context.searchParams.get('example'),'shopify','Expanded example is part of the current return context');
      assert.equal(context.searchParams.has('result'),false);
      assert.equal(context.searchParams.has('step'),false);
      await page.locator('[data-scene="voc"]').click();
      assert.equal(new URL(page.url()).searchParams.get('example'),'voc');
      await page.locator('#view-example').click();
      assert.equal(new URL(page.url()).searchParams.has('example'),false);
      await page.locator('#view-example').click();
      const expected = new URL(page.url());
      await page.locator('#saved-trial a').click();
      const requested = new URL(page.url()).searchParams.get('return');
      assert.equal(requested,expected.pathname+expected.search+expected.hash);
      await textContains('#auth-feedback',lang ? 'request a code' : '获取验证码');
      // A fixture-authenticated continuation exercises the existing safeReturn
      // allowlist without adding any verification or trial writes.
      state = makeSession(); await page.locator('#auth-retry').click();
      await page.locator('#auth-signed-in a').click();
      await page.waitForURL(url=>url.pathname===expected.pathname && url.search===expected.search && url.hash===expected.hash);
      await textContains('#saved-trial',lang ? 'one free trial available' : '一次免费体验资格');
      assert.equal(await page.locator('[data-scene="voc"]').getAttribute('aria-pressed'),'true');
      assert.equal(await page.locator('#trial-example').isVisible(),true);
      assert.equal(await page.locator('#start-trial').isDisabled(),true);
      await page.setViewportSize({width:320,height:900});
      await page.locator('#trial-root *').evaluateAll(nodes=>{
        const sizes=nodes.map(node=>parseFloat(getComputedStyle(node).fontSize));
        nodes.forEach((node,index)=>node.style.fontSize=`${sizes[index]*2}px`);
      });
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`Trial ${lang} 320px 200% text`);
      await page.screenshot({path:path.resolve(__dirname,`../tmp/billing-design-qa/trial-return-320-200${lang}.png`),fullPage:true});
    }
    for (const lang of ['', '-en']) {
      state = makeSession();
      for (const unsafe of ['https://example.invalid/','//example.invalid/','javascript:alert(1)','\\example.invalid','../not-a-page.html','trial.html\nhttps://example.invalid/']) {
        await visit(`auth${lang}.html?return=${encodeURIComponent(unsafe)}`);
        await page.locator('#auth-signed-in a').waitFor();
        assert.ok((await page.locator('#auth-signed-in a').getAttribute('href')).endsWith(`billing${lang}.html`));
      }
      await visit(`auth${lang}.html?return=${encodeURIComponent('trial.html?mission=shopify&example=shopify#trial-example')}`);
      await page.locator('#auth-signed-in a').click();
      await page.waitForURL(url=>url.pathname.endsWith(`trial${lang}.html`) && url.searchParams.get('mission')==='shopify' && url.hash==='#trial-example');
      assert.equal(await page.locator('#trial-example').isVisible(),true);
    }
    assert.equal(writes.length,beforeTrial);
    checks.push('Trial sign-in preserves the latest scene, expanded example and hash through the existing bilingual return allowlist; malicious returns rejected, obsolete task IDs removed, 320px/200% text');
    const holdTrial = () => {
      let started, release;
      const waiting = new Promise(resolve=>{started=resolve;});
      const promise = new Promise(resolve=>{release=resolve;});
      delayedTrial={started,promise}; return {waiting,release};
    };
    const restoreTrial = () => page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));
    const finishTrial = async held => {
      const receiving=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/trial');
      held.release(); const response=await receiving; await response.finished();
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    };
    for (const lang of ['', '-en']) {
      state=makeSession();
      trialRecord={eligibility:'consumed',attempt:{scenario:'voc',input_value:'Fixture old subject',task_state:'completed',report:{title:'Old saved report'}}};
      await visit(`trial${lang}.html`); await textContains('.trial-report','Old saved report');
      const cleared=await page.evaluate(()=>{
        const originalCreate=URL.createObjectURL.bind(URL),originalRevoke=URL.revokeObjectURL.bind(URL);
        let created=null; const revoked=[];
        URL.createObjectURL=blob=>(created=originalCreate(blob));
        URL.revokeObjectURL=url=>{revoked.push(url); originalRevoke(url);};
        const originalClick=HTMLAnchorElement.prototype.click;
        HTMLAnchorElement.prototype.click=function(){if(this.download && this.href.startsWith('blob:'))return; return originalClick.call(this);};
        document.querySelector('#saved-trial button').click();
        window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}));
        return {report:document.querySelectorAll('.trial-report').length,buttons:document.querySelectorAll('#saved-trial button').length,revoked:created!==null && revoked.includes(created)};
      });
      assert.deepEqual(cleared,{report:0,buttons:0,revoked:true},'pagehide clears the report and revokes pending report URLs synchronously');
      state=makeSession(false); const beforeRestore=trialReads; await restoreTrial();
      await textContains('#saved-trial',lang ? 'Sign in' : '登录后');
      assert.equal(trialReads,beforeRestore+1);
      // Restore checks are synthetic PageTransitionEvents, not a claim that
      // Chrome's actual BFCache was used by this fixture test.
      state=makeSession(); const oldReport=holdTrial();
      await page.locator('#refresh-trial').click(); await oldReport.waiting;
      await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true})));
      state=makeSession(false); await restoreTrial();
      await textContains('#saved-trial',lang ? 'Sign in' : '登录后');
      await finishTrial(oldReport);
      assert.equal(await page.locator('.trial-report').count(),0);
      await textContains('#saved-trial',lang ? 'Sign in' : '登录后');
      const oldUnauthorized=holdTrial(); await page.locator('#refresh-trial').click(); await oldUnauthorized.waiting;
      state=makeSession(); trialRecord={eligibility:'available',attempt:null};
      await restoreTrial(); await textContains('#saved-trial',lang ? 'one free trial available' : '一次免费体验资格');
      await finishTrial(oldUnauthorized);
      await textContains('#saved-trial',lang ? 'one free trial available' : '一次免费体验资格');
      assert.equal(await page.locator('#saved-trial a').count(),0);
      trialFails=true; await restoreTrial();
      await textContains('#saved-trial',lang ? 'unknown' : '未知');
      assert.equal(await page.locator('#saved-trial button').count(),0);
      trialFails=false;
      assert.equal(await page.locator('#start-trial').isDisabled(),true);
    }
    assert.equal(writes.length,beforeTrial);
    checks.push('Trial pagehide clears sensitive content/blob handles; persisted pageshow revalidates, late old 200/401 responses cannot replace current state, failures remain unknown; zero trial writes');
    const lifecycleStart=writes.length;
    const holdSessionOperation = (kind,status=200) => {
      let started,release;const waiting=new Promise(resolve=>{started=resolve;});
      const promise=new Promise(resolve=>{release=resolve;});const held={started,promise,status};
      if(kind==='select')heldSelection=held;else if(kind==='logout')heldLogout=held;else heldAccount=held;
      return {waiting,release};
    };
    const finishSessionOperation = async (held,endpoint) => {
      const receiving=page.waitForResponse(response=>new URL(response.url()).pathname===endpoint);
      held.release();await (await receiving).finished();
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    };
    const hideBilling = () => page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true})));
    const restoreBilling = () => page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));
    const views = ['billing','pricing','checkout'];
    const viewPath = (name,lang) => `${name}${lang}.html${name==='checkout'?`?quote=${lifecycleQuoteId}`:name==='billing'?'#credits':''}`;
    const readyView = async name => {
      if(name==='billing')await textContains('.account-metrics','0.00');
      else if(name==='pricing')await page.locator('#pricing-subscription-status[data-ready="true"]').waitFor();
      else await textContains('#purchase-summary','Fixture private quote benefit');
    };
    subscriptions=[makeSubscription()];subscriptionsEnabled=true;
    for(const lang of ['', '-en']) for(const name of views) {
      for(const status of [200,401]) {
        state=makeSession();await visit(viewPath(name,lang));await readyView(name);
        const before=writes.length,held=holdSessionOperation('select',status);
        await page.locator('.nav-account summary').click();
        await page.locator('#billing-account-select').selectOption(two);await held.waiting;
        await hideBilling();state=makeSession();await restoreBilling();
        await page.locator('#billing-account-select').waitFor({state:'attached'});
        assert.equal(await page.locator('#billing-account-select').isDisabled(),false,'Restoring a pending selection must reset busy state');
        await readyView(name);await finishSessionOperation(held,'/api/billing/accounts/select');
        assert.equal(await page.locator('#billing-account-select').inputValue(),one);
        assert.equal(await page.locator('#billing-account-select').isDisabled(),false);
        assert.equal(await page.locator('#session-alert').count(),0);
        assert.equal(writes.length,before+1,'Restore never repeats account-selection POST');
        const beforeHideRequests=apiRequests.length;
        const cleared=await page.evaluate(()=>{
          window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}));
          return {identity:document.querySelector('.nav-account summary')?.textContent.includes('Fixture user'),
            metrics:document.querySelectorAll('.account-metrics').length,
            subscription:document.querySelectorAll('#pricing-subscription-status .record-list').length,
            quote:document.querySelector('#purchase-summary')?.textContent.includes('Fixture private quote benefit')||false};
        });
        assert.deepEqual(cleared,{identity:false,metrics:0,subscription:0,quote:false},`${name} clears user-specific DOM on pagehide`);
        await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
        assert.equal(apiRequests.length,beforeHideRequests,'pagehide clears local state without starting any GET or POST');
        await restoreBilling();await readyView(name);
      }
    }
    checks.push('All three billing views restore after pending account selection; late 200/401/finally cannot switch or disable the new session, pagehide clears private DOM, no automatic selection POST');
    for(const lang of ['', '-en']) for(const name of views) {
      state=makeSession();await visit(viewPath(name,lang));await readyView(name);
      const before=writes.length;logoutFails=true;const held=holdSessionOperation('logout');
      await page.locator('.nav-account summary').click();await page.locator('#account-logout').click();await held.waiting;
      await hideBilling();state.csrf_token='fixture-restored-csrf';await restoreBilling();
      await textContains('#session-alert',lang?'sign-out':'退出');
      await textContains('#session-alert','f***@example.test');
      assert.equal(await page.locator('.account-metrics').count(),0);
      assert.equal(await page.locator('#purchase-summary').filter({hasText:'Fixture private quote benefit'}).count(),0);
      assert.equal(await page.locator('#pricing-subscription-status .record-list').count(),0);
      assert.equal(await page.locator('#session-alert button').isDisabled(),false);
      await finishSessionOperation(held,'/api/auth/logout');
      await textContains('#session-alert',lang?'sign-out':'退出');
      assert.equal(writes.length,before+1,'Restore and late failure never automatically retry sign-out');
      await page.setViewportSize({width:320,height:1100});
      await page.locator('main, main *').evaluateAll(nodes=>{
        const sizes=nodes.map(node=>parseFloat(getComputedStyle(node).fontSize));
        nodes.forEach((node,index)=>node.style.fontSize=`${sizes[index]*2}px`);
      });
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`${name} ${lang} 320px 200% content including sign-out notice`);
      await page.locator('#session-alert').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.resolve(__dirname,`../tmp/billing-design-qa/session-recovery-${name}-${lang||'zh'}-320-200.png`)});
      logoutFails=false;state.csrf_token='fixture-retry-csrf';const beforeRetryReads=sessionReads;
      await page.locator('#session-alert button').click();
      await page.locator('.account-login').waitFor({state:'attached'});
      await page.waitForFunction(()=>!document.querySelector('#session-alert'));
      assert.equal(writes.length,before+2,'Only an explicit retry adds one sign-out POST');
      assert.equal(sessionReads,beforeRetryReads+1,'An explicit retry must recheck the current session and use its current CSRF token');
    }
    checks.push('Pending failed sign-out survives page restoration on all three views with hidden finances and an explicit retry using freshly checked CSRF; bilingual 320px 200% main content, no automatic POST');
    state=makeSession();await visit('billing.html#credits');await readyView('billing');
    const confirmedStart=writes.length,confirmedLogout=holdSessionOperation('logout');
    await page.locator('.nav-account summary').click();await page.locator('#account-logout').click();await confirmedLogout.waiting;
    await hideBilling();await restoreBilling();await page.waitForFunction(()=>!document.querySelector('#session-alert') && document.querySelector('.account-login'));
    await finishSessionOperation(confirmedLogout,'/api/auth/logout');
    assert.equal(await page.locator('#logout-retry').count(),0);
    assert.equal(writes.length,confirmedStart+1);
    state=makeSession();await visit('billing.html#credits');await readyView('billing');
    const changedStart=writes.length;logoutFails=true;const changedLogout=holdSessionOperation('logout');
    await page.locator('.nav-account summary').click();await page.locator('#account-logout').click();await changedLogout.waiting;
    await hideBilling();state=makeSession();state.user={id:'fixture-other-user',display_name:'Fixture other identity',masked_identifier:'o***@example.test'};
    await restoreBilling();await textContains('#session-alert','o***@example.test');
    await finishSessionOperation(changedLogout,'/api/auth/logout');
    assert.equal(writes.length,changedStart+1);
    assert.equal(await page.locator('.account-metrics').count(),0);
    // Another identity change during the explicitly requested recheck must not
    // silently sign out that different person.
    state.user={id:'fixture-third-user',display_name:'Fixture third identity',masked_identifier:'t***@example.test'};
    logoutFails=false;await page.locator('#session-alert button').click();await textContains('#session-alert','t***@example.test');
    assert.equal(writes.length,changedStart+1);
    await page.locator('#session-alert button').click();await page.waitForFunction(()=>!document.querySelector('#session-alert'));
    assert.equal(writes.length,changedStart+2);
    checks.push('A restored signed-out GET clears the pending intent; a changed sign-out identity is disclosed and requires a fresh explicit confirmation');
    state=makeSession();const heldRead=holdSessionOperation('account',401);
    await visit('billing.html#credits');await heldRead.waiting;
    await hideBilling();await restoreBilling();await readyView('billing');
    await finishSessionOperation(heldRead,'/api/billing/account');
    await readyView('billing');assert.equal(await page.locator('#session-alert').count(),0);
    assert.equal(await page.locator('#billing-account-select').isDisabled(),false);
    const lifecycleWrites=writes.slice(lifecycleStart);
    assert.ok(lifecycleWrites.every(endpoint=>['/api/auth/logout','/api/billing/accounts/select'].includes(endpoint)));
    checks.push('A late 401 from the same account but previous session epoch cannot clear the restored session; lifecycle coverage adds zero financial writes');
    assert.deepEqual(errors,[]);
    assert.ok(writes.every(path => ['/api/auth/challenges','/api/auth/verify','/api/auth/logout','/api/billing/accounts/select','/api/billing/purchase-inquiries','/api/billing/quotes',cancelPath].includes(path)));
    console.log(JSON.stringify({layer:'UI response fixtures; synthetic page lifecycle, no real verification delivery or payments',checks,fixtureWrites:writes.length,lifecycleAccountWrites:lifecycleWrites.length,lifecycleFinancialWrites:lifecycleWrites.filter(endpoint=>!['/api/auth/logout','/api/billing/accounts/select'].includes(endpoint)).length,pageErrors:errors.length},null,2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
