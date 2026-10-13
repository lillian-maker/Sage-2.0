/* Real loopback HTTP only. Parent LiveServerTestCase supplies a random OTP over
 * stdin from locmem mail; do not add route fixtures, code endpoints or logging. */
const assert = require('node:assert/strict');
const readline = require('node:readline');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = new URL(process.env.SAGE_URL);
assert.equal(base.protocol, 'http:');
assert.ok(['127.0.0.1', 'localhost'].includes(base.hostname));
const input = readline.createInterface({input: process.stdin, terminal: false});
// Register before asking the parent: stdin can arrive before a later listener.
const codeInput = new Promise((resolve, reject) => {
  input.once('line', resolve);
  input.once('close', () => reject(new Error('Verification handoff closed before a code arrived')));
});
codeInput.catch(() => {}); // The awaited promise still rejects; avoid an orphan on early browser startup failure.

(async () => {
  const browser = await chromium.launch({channel: 'chrome'});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  await context.route('https://turing.captcha.qcloud.com/TJCaptcha.js',route=>route.fulfill({contentType:'text/javascript',body:'window.TencentCaptcha=class{constructor(app,callback){if(app!=="123")throw Error("Supplier fixture app mismatch");this.callback=callback}show(){this.callback({ret:0,ticket:crypto.randomUUID(),randstr:"supplier-fixture"})}destroy(){}};'}));
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const pageErrors = [], writes = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('request', request => {
    if (request.method() === 'POST') writes.push(new URL(request.url()).pathname);
  });
  const textContains = (selector, text) => page.waitForFunction(
    ({selector, text}) => document.querySelector(selector)?.textContent.includes(text), {selector, text});
  const responseAt = path => page.waitForResponse(response => new URL(response.url()).pathname === path && response.request().method() === 'POST');
  try {
    await page.goto(new URL('/pages/auth.html?mode=register&return=billing.html%23credits', base).href);
    await page.waitForFunction(()=>!document.querySelector('#auth-verify').disabled);
    await page.locator('#auth-first-name').fill('Browser');
    await page.locator('#auth-contact').fill('browser-smoke@example.test');
    await page.locator('#auth-agreement').check();
    await page.locator('#auth-verify').click();
    await page.locator('#auth-password').fill('Violet!431');
    const starting=responseAt('/api/auth/register/start');
    await page.locator('#auth-verify').click();
    assert.equal((await starting).status(),201);
    await page.locator('#auth-captcha').waitFor({state:'visible'});
    await page.waitForFunction(()=>!document.querySelector('#auth-captcha').disabled);
    assert.equal((await context.request.get(new URL('/api/account/profile',base).href)).status(),401,'No account before CAPTCHA/OTP');
    const challengeResponse = responseAt('/api/auth/captcha');
    await page.locator('#auth-captcha').click();
    assert.equal((await challengeResponse).status(), 201, 'Real challenge request must succeed');
    await textContains('#auth-feedback', '已发送');
    process.stdout.write('code requested\n');
    let code = await codeInput;
    assert.ok(/^[0-9]{6}$/.test(code), 'Expected the in-memory generated verification code');
    await page.locator('[data-code-index="0"]').waitFor();
    await page.waitForFunction(()=>!document.querySelector('#auth-verify').disabled);
    const verificationResponse = responseAt('/api/auth/register/verify');
    await page.locator('[data-code-index="0"]').fill(code);
    code = undefined;
    assert.equal((await verificationResponse).status(), 200, 'Real verification must succeed');
    await page.waitForURL('**/index.html');
    await page.goto(new URL('/pages/billing.html#credits',base).href);
    await page.locator('.account-metrics').waitFor();
    assert.deepEqual(await page.locator('.account-metrics dd').allTextContents(), ['0', '0', '0', '0', '0', '0']);
    await textContains('#account-live', '还没有额度批次');
    await textContains('#account-live .account-context', '个人账户');

    await page.locator('.account-menu a[href="#orders"]').click();
    await textContains('#account-live', '还没有订单');
    assert.equal(await page.locator('[data-order]').count(), 0);

    let cancelPath;
    if (process.env.SAGE_BROWSER_PURCHASE_FIXTURE === '1') {
      const ready = new Promise(resolve => input.once('line', resolve));
      process.stdout.write('purchase fixture requested\n');
      const product = await ready;
      assert.match(product,/^[0-9a-f-]{36}$/);
      await page.goto(new URL('/pages/pricing.html',base).href);
      try{await page.locator(`[data-purchase-product="${product}"]`).waitFor();}catch(_){const response=await context.request.get(new URL('/api/billing/purchase-catalog',base).href);throw Error('Purchase product did not render: '+response.status());}
      await page.locator(`[data-purchase-product="${product}"]`).click();
      const preparing = responseAt('/api/billing/purchase-quotes');
      await page.locator('#purchase-prepare').click();
      const quoteResponse = await preparing;
      const prepared = await quoteResponse.json();
      assert.equal(quoteResponse.status(),201,`Quote rejected: ${prepared.error?.code || 'unknown'}`);
      assert.equal(Number.isSafeInteger(prepared.amount_minor),true,'Quote amount is not a safe integer');
      assert.equal(prepared.snapshot.seat_quantity,1,'Quote seats mismatch');
      assert.equal(prepared.can_order,true,`Quote unavailable: ${prepared.unavailable_reason}`);
      assert.equal(prepared.terms.digest,prepared.snapshot.terms_document_digest,'Terms digest mismatch');
      try { await page.locator('#purchase-agree').waitFor(); }
      catch (_) { throw new Error(`Purchase confirmation did not render: ${await page.locator('#purchase-status').textContent()}`); }
      assert.equal(await page.locator('#purchase-submit').isDisabled(),true);
      await page.locator('#purchase-agree').check();
      const creating = responseAt('/api/billing/orders');
      await page.locator('#purchase-submit').click();
      assert.equal((await creating).status(),201);
      await page.locator('#purchase-order-link').waitFor();
      await page.reload();
      await page.locator('#purchase-order-link').waitFor();
      await page.locator('#purchase-order-link').click();
      await textContains('#order-detail','待付款');
      await textContains('#order-detail','7.31');
      assert.equal(await page.locator('[data-order]').count(),1);
    }
    if (process.env.SAGE_BROWSER_SUBSCRIPTION_FIXTURE === '1') {
      const ready = new Promise(resolve => input.once('line', resolve));
      process.stdout.write('subscription fixture requested\n');
      assert.equal(await ready,'subscription fixture ready');
      await page.locator('.account-menu a[href="#subscription"]').click();
      await textContains('.subscription-card','fixture_plan');
      assert.equal(await page.locator('.subscription-service').textContent(),'生效中');
      const id = await page.locator('.subscription-card').getAttribute('data-subscription');
      cancelPath = `/api/billing/subscriptions/${id}/cancel`;
      await page.locator('[data-cancel-open]').click();
      const cancelResponse = responseAt(cancelPath);
      await page.locator('[data-cancel-confirm]').click();
      assert.equal((await cancelResponse).status(),200);
      await textContains('.subscription-renewal','取消处理中');
      await page.reload(); await textContains('.subscription-renewal','取消处理中');
      assert.equal(await page.locator('.subscription-service').textContent(),'生效中');
      assert.equal(await page.locator('[data-cancel-open]').count(),0);
      await page.locator('.account-menu a[href="#credits"]').click();
      await textContains('.account-metrics','17.25');
    }

    await page.locator('.account-menu a[href="#procurement"]').click();
    await page.locator('#purchase-company').fill('Sage browser integration fixture');
    await page.locator('#purchase-seats').fill('2');
    await page.locator('#purchase-email').fill('browser-smoke@example.test');
    await page.locator('#purchase-requirements').fill('Isolated browser integration inquiry. No payment requested.');
    const inquiryResponse = responseAt('/api/billing/purchase-inquiries');
    await page.locator('#procurement-form button[type=submit]').click();
    assert.equal((await inquiryResponse).status(), 201, 'Purchase inquiry must be created by the live server');
    await textContains('#procurement-feedback', '服务端接收');
    await textContains('#procurement-records', 'Sage browser integration fixture');
    await page.reload();
    await textContains('#procurement-records', 'Sage browser integration fixture');

    await page.locator('.nav-account summary').click();
    const logoutResponse = responseAt('/api/auth/logout');
    await page.locator('#account-logout').click();
    assert.equal((await logoutResponse).status(), 200);
    await textContains('#account-live', '登录后');
    assert.equal(await page.locator('#procurement-form').count(), 0);
    assert.equal(await page.locator('#procurement-records').count(), 0);
    assert.equal(await page.locator('.account-metrics').count(), 0);
    const afterLogout = await page.evaluate(async () => {
      const session = await fetch('/api/auth/session', {credentials: 'same-origin', cache: 'no-store'}).then(response => response.json());
      const account = await fetch('/api/billing/account', {credentials: 'same-origin', cache: 'no-store'});
      return {authenticated: session.authenticated, accountStatus: account.status,
        code: (await account.json()).error?.code,
        localKeys: localStorage.length, sessionKeys: sessionStorage.length};
    });
    assert.equal(afterLogout.authenticated,false);
    assert.equal(afterLogout.accountStatus,401);
    assert.equal(afterLogout.code,'AUTH_REQUIRED');
    assert.equal(afterLogout.localKeys,0);
    assert.equal(afterLogout.sessionKeys,0);
    await page.goBack();
    await textContains('#account-live', '登录后');
    assert.equal(await page.locator('.account-metrics').count(), 0);
    assert.equal(await page.locator('#procurement-records').count(), 0);
    assert.equal(await page.locator('#account-logout').count(), 0);
    assert.deepEqual(writes, ['/api/auth/register/start', '/api/auth/captcha', '/api/auth/register/verify', ...(process.env.SAGE_BROWSER_PURCHASE_FIXTURE === '1' ? ['/api/billing/purchase-quotes','/api/billing/orders'] : []), ...(cancelPath ? [cancelPath] : []), '/api/billing/purchase-inquiries', '/api/auth/logout']);
    assert.deepEqual(pageErrors, []);
  } finally {
    input.close();
    await browser.close();
  }
})().catch(error => {
  // Playwright error messages can include a fill value. Never emit OTP digits.
  console.error(String(error.message || 'Browser integration failed').replace(/\b[0-9]{6}\b/g, '[redacted]'));
  input.close();
  process.exitCode = 1;
});
