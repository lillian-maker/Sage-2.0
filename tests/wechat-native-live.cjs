/* Real loopback website and HTTP; merchant keys stay in the parent process. */
const assert = require('node:assert/strict');
const readline = require('node:readline');
const path = require('node:path');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const base = new URL(process.env.SAGE_URL);
assert.equal(base.protocol, 'http:');
assert.equal(base.hostname, '127.0.0.1');
const input = readline.createInterface({input:process.stdin, terminal:false});
const queued = [], readers = [];
input.on('line', line => readers.length ? readers.shift()(line) : queued.push(line));
const read = () => queued.length ? Promise.resolve(queued.shift()) : new Promise(resolve => readers.push(resolve));

(async () => {
  const fixture = JSON.parse(await read());
  const browser = await chromium.launch({channel:'chrome'});
  const context = await browser.newContext();
  const errors = [], external = [], outcomes = [];
  try {
    await context.addCookies([fixture.cookie]);
    await context.route('**/*', async route => {
      if (new URL(route.request().url()).origin === base.origin) return route.continue();
      external.push(new URL(route.request().url()).origin);
      return route.abort('blockedbyclient');
    });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      const url = new URL(request.url());
      if (url.pathname.endsWith('/pay') || url.pathname.endsWith('/payment-status')) {
        assert.equal(request.method(), 'POST');
        assert.deepEqual(request.postDataJSON(), {});
        assert.equal(request.headers()['x-billing-account'], fixture.account_id);
        assert.ok(request.headers()['x-csrftoken']);
        outcomes.push(url.pathname.endsWith('/pay') ? 'pay' : 'query');
      }
    });
    const checkout = new URL('/pages/checkout.html?quote='+encodeURIComponent(fixture.quote_id),base).href;
    await page.goto(checkout, {waitUntil:'domcontentloaded'});
    const csrfRejected = await context.request.post(new URL('/api/billing/orders/'+fixture.order_id+'/pay',base).href,
      {headers:{'Content-Type':'application/json','X-Billing-Account':fixture.account_id},data:{}});
    assert.equal(csrfRejected.status(),403);
    await page.locator('#purchase-payment-start').click();
    const qr = page.locator('#purchase-payment-qr');
    await qr.waitFor();
    await page.waitForFunction(() => {
      const image = document.querySelector('#purchase-payment-qr');
      return image?.complete && image.naturalWidth > 0;
    });
    const original = await qr.getAttribute('src');
    assert.match(original, /^data:image\/svg\+xml;base64,[A-Za-z0-9+/]+=*$/);
    assert.match(Buffer.from(original.split(',')[1], 'base64').toString(), /<svg/);
    assert.deepEqual(outcomes, ['pay']);
    await page.reload({waitUntil:'domcontentloaded'});
    await qr.waitFor();
    assert.equal(await qr.getAttribute('src'), original);
    assert.equal(await page.locator('#purchase-payment-start').count(), 0);
    assert.deepEqual(outcomes, ['pay']);
    const evidence = path.join(process.cwd(),'tmp/payment-handoff-evidence');
    fs.mkdirSync(evidence,{recursive:true});
    await qr.screenshot({path:path.join(evidence,'wechat-native-live-qr.png')});
    process.stdout.write('qr recovered\n');
    const notification = JSON.parse(await read());
    const webhook = new URL('/api/payments/webhooks/wechat',base).href;
    const headers = {...notification.headers,'Content-Type':'application/json'};
    const data = Buffer.from(notification.body,'base64');
    for (let replay=0; replay<2; replay++) {
      const response = await context.request.post(webhook,{headers,data});
      assert.equal(response.status(),204);
      assert.equal((await response.body()).length,0);
    }
    const query = page.waitForResponse(response => response.url().endsWith('/payment-status'));
    await page.locator('#purchase-payment-refresh').click();
    const refreshed = await query;
    assert.equal(refreshed.status(),200);
    const result = await refreshed.json();
    assert.equal(result.order.id,fixture.order_id);
    assert.equal(result.order.status,'fulfilled');
    assert.equal(result.delivery.status,'completed');
    await page.waitForFunction(() => /已确认付款与交付/.test(document.querySelector('#purchase-payment-status')?.textContent || ''));
    assert.equal(await qr.count(),0);
    assert.deepEqual(outcomes,['pay','query']);
    assert.deepEqual(errors,[]);
    assert.deepEqual(external,[]);
  } finally {
    await browser.close();
    input.close();
  }
})().catch(error => {
  console.error('WeChat browser integration failed: '+String(error.message || 'unknown'));
  input.close();
  process.exitCode=1;
});
