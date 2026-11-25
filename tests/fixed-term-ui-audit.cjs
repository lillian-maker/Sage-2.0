// Fixed-term plan checkout and the return from the payment provider, on isolated response fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SAGE_URL || 'http://127.0.0.1:8914';
const account = '11111111-1111-4111-8111-111111111111';
const product = '22222222-2222-4222-8222-222222222222';
const quote = '33333333-3333-4333-8333-333333333333';
const order = '44444444-4444-4444-8444-444444444444';
const terms = {version:'isolated-1',digest:'a'.repeat(64),zh:{title:'测试条款',paragraphs:['仅用于测试。']},en:{title:'Test terms',paragraphs:['Isolated fixture only.']}};
const item = {id:product,code:'pro',version:1,kind:'subscription',audience:'personal',billing_cycle:'monthly',currency:'CNY',unit_amount_minor:'731',minimum_seats:1,credits_quantity:'100',entitlements:{benefits:{zh:['隔离测试权益。'],en:['Isolated benefits.']}},terms_version:'isolated-1'};
const liveURL = 'https://openapi.alipay.com/gateway.do?app_id=fixture&method=alipay.trade.page.pay&sign=fixture';
const attemptId = '55555555-5555-4555-8555-555555555555';
(async () => {
  assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname));
  // Real QR renderer, synthetic payload; all provider HTTP remains intercepted.
  const rendered = spawnSync(process.env.SAGE_PYTHON || path.join(__dirname,'../.venv/bin/python'), ['-B','-c',
    'from backend.wechat import wechat_qr_image; print(wechat_qr_image("weixin://wxpay/bizpayurl?pr=synthetic2026"))'],
    {cwd:path.join(__dirname,'..'),encoding:'utf8',timeout:10000});
  assert.equal(rendered.status,0,'The installed backend QR renderer must create the synthetic fixture');
  const image = rendered.stdout.trim();
  const browser = await chromium.launch({channel:'chrome'});
  let checks = 0;
  try {
    for (const channel of ['alipay', 'wechat']) for (const language of ['', '-en']) {
      const wechat = channel === 'wechat';
      const page = await browser.newPage();
      page.setDefaultTimeout(7000);
      const errors = [], navigation = [], writes = [];
      let ordered = false, snapshotRenewal = 'none', orderState = 'pending_payment', attempt = null, qr = null, orderReads = 0, settleAfter = Infinity;
      page.on('pageerror', error => errors.push(error.message));
      await page.route(/\/billing\.js(?:\?.*)?$/, route => route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../src/pages/billing.js'),'utf8')}));
      await page.route('https://openapi.alipay.com/**', route => {navigation.push(route.request().url());return route.fulfill({contentType:'text/html',body:'<p>Navigation intercepted; no provider request.</p>'});});
      await page.route('**/api/**', async route => {
        const request = route.request(), endpoint = new URL(request.url()).pathname;
        const json = (value,status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
        if (request.method() !== 'GET') writes.push([endpoint,request.postDataJSON()]);
        const snapshot = {...item,product_id:product,amount_minor:731,seat_quantity:1,channel,buyer_region:'CN',terms_document_digest:terms.digest,...(snapshotRenewal?{renewal:snapshotRenewal}:{})};
        if (endpoint === '/api/auth/session') return json({authenticated:true,user:{id:'fixture-user'},accounts:[{id:account,name:'个人账户',kind:'personal',role:'owner'}],active_account_id:account,csrf_token:'fixture-csrf'});
        if (endpoint === '/api/account/notifications/summary') return json({user_id:'fixture-user',unread_count:0});
        if (endpoint === '/api/account/invoices') return json({available:false,items:[]});
        if (endpoint === '/api/billing/catalog') return json(JSON.parse(fs.readFileSync(path.join(__dirname,'../src/pages/billing-catalog.json'),'utf8')));
        if (endpoint === '/api/billing/purchase-catalog') return json({account_id:account,currency:'CNY',items:[{...item,quote_available:true,unavailable_reason:null,terms,purchase_options:[{channel,buyer_region:'CN',renewal:'none'}]}]});
        if (endpoint === '/api/billing/purchase-quotes' || endpoint === `/api/billing/purchase-quotes/${quote}`)
          return json({id:quote,account_id:account,amount_minor:731,currency:'CNY',expires_at:'2099-01-01T00:00:00Z',snapshot,terms,can_order:!ordered,unavailable_reason:ordered?'QUOTE_ALREADY_ORDERED':null,order_id:ordered?order:null},request.method()==='POST'?201:200);
        if (endpoint === '/api/billing/orders' && request.method() === 'POST') {ordered = true;return json({id:order,account_id:account,status:'pending_payment',amount_minor:731,currency:'CNY',snapshot},201);}
        if (endpoint === '/api/billing/orders') return json({account_id:account,items:[{id:order,account_id:account,plan_code:'pro',amount_minor:731,currency:'CNY',status:orderState,created_at:'2026-11-18T00:00:00Z',snapshot:{kind:'subscription'}}],next_offset:null});
        if (endpoint === `/api/billing/orders/${order}`) {
          if (++orderReads >= settleAfter) {orderState = 'fulfilled';attempt = {...attempt,status:'succeeded'};}
          const settled = orderState === 'fulfilled';
          return json({order:{id:order,account_id:account,status:orderState,amount_minor:731,currency:'CNY',paid_at:settled?'2026-11-18T00:01:00Z':null,created_at:'2026-11-18T00:00:00Z',snapshot},
            payments:settled?[{status:'accepted'}]:[],delivery:{status:settled?'completed':'not_started'},payment_attempt:attempt,refunds:[],
            ...(!settled?{cancelable:true,payment_deadline:'2099-01-01T00:00:00Z'}:{}),
            ...(!settled&&qr?{payment_qr:qr}:{}),
            ...(!settled&&!attempt?{payment_start:{environment:'LIVE',channel,available:true,order_id:order,account_id:account}}:{})});
        }
        if (endpoint === `/api/billing/orders/${order}/pay` && wechat) {
          attempt = {id:attemptId,channel:'wechat',status:'pending',expires_at:new Date(Date.now()+600000).toISOString(),needs_reconciliation:false};
          qr = {environment:'LIVE',channel:'wechat',account_id:account,order_id:order,attempt_id:attemptId,expires_at:attempt.expires_at,image_svg_base64:image};
          return json({payment_attempt:attempt,payment_qr:qr});
        }
        if (endpoint === `/api/billing/orders/${order}/cancel`) {
          return json({order:{id:order,account_id:account,status:'pending_payment',amount_minor:731,currency:'CNY',paid_at:null,snapshot},cancel_status:'pending'});
        }
        if (endpoint === `/api/billing/orders/${order}/pay`) {attempt = {id:product,status:'pending',expires_at:'2099-01-01T00:00:00Z',needs_reconciliation:false};return json({payment_attempt:attempt,payment_url:liveURL});}
        if (endpoint === `/api/billing/orders/${order}/refund-assessment`) return json({order_id:order,account_id:account,can_submit:false,currency:'CNY',status:'unavailable',reason:'unsupported_order'});
        if (endpoint === '/api/billing/subscriptions') return json({items:[]});
        if (endpoint === '/api/billing/account') return json({account:{id:account,name:'个人账户',kind:'personal',role:'owner'},credits:{available:'10'},orders:[],capabilities:{}});
        return json({error:{code:'CHANNEL_UNAVAILABLE'}},503);
      });
      const termText = language ? 'One month, no automatic renewal' : '一个月，到期不自动续费';

      // Product page: the term is disclosed before a quote, and the quote request carries only channel and region.
      await page.goto(`${base}/pages/checkout${language}.html?product=${product}`);
      await page.locator('#purchase-prepare').waitFor();
      assert.ok((await page.locator('#purchase-summary').innerText()).includes(termText));
      assert.match(await page.locator('#purchase-option').innerText(),wechat?(language?/WeChat Pay · CN/:/微信支付 · CN/):(language?/Alipay · CN/:/支付宝 · CN/));
      await page.locator('#purchase-prepare').click();
      await page.locator('#purchase-agree').waitFor();
      assert.deepEqual(writes,[['/api/billing/purchase-quotes',{product_id:product,seat_quantity:1,channel,buyer_region:'CN'}]]);
      const summary = await page.locator('#purchase-summary').innerText();
      assert.ok(summary.includes(termText));
      assert.ok(!/连续包月|Monthly renewal/.test(summary));
      assert.match(summary,language?/single payment/i:/一次性付款/);
      checks++;

      // Order, then exactly one payment request that leaves for the production gateway.
      await page.locator('#purchase-agree').check();
      await page.locator('#purchase-submit').click();
      await page.locator('#purchase-payment-start').waitFor();
      if (wechat) {
        // A fixed-term plan is paid by the original order's Native QR; nothing leaves the page.
        assert.match(await page.locator('#purchase-payment-start').innerText(),language?/Generate WeChat payment QR/:/生成微信支付二维码/);
        await page.locator('#purchase-payment-start').click();
        await page.locator('#purchase-payment-qr').waitFor();
        assert.match(await page.locator('#purchase-payment-status').innerText(),language?/Scan with WeChat to pay/:/请使用微信扫码付款/);
        assert.deepEqual(navigation,[]);
        assert.deepEqual(writes.slice(1).map(entry=>entry[0]),['/api/billing/orders',`/api/billing/orders/${order}/pay`]);
        checks++;
        // Cancelling names the channel that must confirm the close.
        await page.locator('[data-order-cancel]').click();
        assert.match(await page.locator('[data-order-cancel-status]').innerText(),language?/paid with WeChat Pay/:/已在微信支付完成付款/);
        await page.locator('[data-order-cancel]').click();
        await page.waitForFunction(() => /WeChat Pay to confirm|向微信支付确认/.test(document.querySelector('[data-order-cancel-status]')?.textContent || ''));
        assert.ok(!/Alipay|支付宝/.test(await page.locator('[data-order-cancel-status]').innerText()));
        assert.equal(writes.at(-1)[0],`/api/billing/orders/${order}/cancel`);
        checks++;
      } else {
      assert.match(await page.locator('#purchase-payment-start').innerText(),language?/Alipay/:/支付宝付款/);
      const destination = page.waitForURL('https://openapi.alipay.com/gateway.do?*');
      await page.locator('#purchase-payment-start').click();await destination;
      assert.deepEqual(navigation,[liveURL]);
      assert.deepEqual(writes.slice(1).map(entry=>entry[0]),['/api/billing/orders',`/api/billing/orders/${order}/pay`]);
      checks++;

      // Return from the provider: its parameters are dropped and only server reads decide the result.
      const before = writes.length;settleAfter = orderReads+3;
      await page.goto(`${base}/pages/billing${language}.html?order=${order}&charset=utf-8&out_trade_no=${product}&trade_no=fixture&sign=fixture`);
      await page.locator('#order-detail #payment-attempt-heading').waitFor();
      assert.equal(new URL(page.url()).search,`?order=${order}`);
      assert.equal(new URL(page.url()).hash,'#orders');
      assert.equal(await page.locator('#order-payment-result').count(),0);
      await page.locator('#order-payment-result').waitFor({timeout:15000});
      assert.match(await page.locator('#order-payment-result').innerText(),language?/Payment successful/:/支付成功/);
      assert.equal(writes.length,before);
      checks++;
      }

      // A renewing plan still has no page-payment button.
      ordered = true;attempt = null;qr = null;orderState = 'pending_payment';settleAfter = Infinity;snapshotRenewal = null;
      await page.goto(`${base}/pages/checkout${language}.html?quote=${quote}`);
      await page.locator('#purchase-order-link').waitFor();
      await page.waitForTimeout(400);
      assert.equal(await page.locator('#purchase-payment-start').count(),0);
      assert.ok(!(await page.locator('#purchase-summary').innerText()).includes(termText));
      checks++;
      assert.deepEqual(errors,[]);
      await page.close();
    }
  } finally { await browser.close(); }
  console.log(JSON.stringify({layer:'Isolated browser response fixtures, no provider request or actual payment',checks,pageErrors:0}));
})().catch(error => { console.error(error); process.exit(1); });
