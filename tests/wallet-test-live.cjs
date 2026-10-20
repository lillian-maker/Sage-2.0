/* Real Django HTTP and order; only external SDK/native-wallet boundaries are replaced. */
const assert=require('node:assert/strict');
const readline=require('node:readline');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const base=new URL(process.env.SAGE_URL);
assert.equal(base.protocol,'http:');assert.equal(base.hostname,'127.0.0.1');
const input=readline.createInterface({input:process.stdin,terminal:false});
const fixtureInput=new Promise((resolve,reject)=>{
  input.once('line',line=>{try{resolve(JSON.parse(line));}catch{reject(Error('Invalid synthetic fixture'));}});
  input.once('close',()=>reject(Error('Missing synthetic fixture')));
});
fixtureInput.catch(()=>{});
(async()=>{
  const fixture=await fixtureInput;
  const browser=await chromium.launch({channel:'chrome'});
  try {
    for(const language of ['', '-en']){
      const context=await browser.newContext({viewport:{width:390,height:1000}});
      await context.addCookies([fixture.cookie]);
      const errors=[],external=[],writes=[],capabilities=[],httpStates=[];
      await context.addInitScript(()=>{
        window.walletBoundary={requests:[],aborts:0};
        window.ApplePaySession=class {
          static supportsVersion(version){return version===3;}
          static canMakePayments(){return true;}
          constructor(version,request){window.walletBoundary.requests.push({version,request});}
          begin(){this.onvalidatemerchant({validationURL:'http://127.0.0.1/private-never-fetch'});}
          abort(){window.walletBoundary.aborts++;}
        };
      });
      await context.route('**/*',async route=>{
        const url=new URL(route.request().url());
        if(url.href==='https://pay.google.com/gp/p/js/pay.js')return route.fulfill({contentType:'text/javascript',body:`
          window.google={payments:{api:{PaymentsClient:class {
            constructor(options){if(options.environment!=='TEST')throw Error('Only TEST');}
            isReadyToPay(){return Promise.resolve({result:true});}
            createButton(options){const button=document.createElement('button');button.textContent='Google Pay TEST';button.onclick=options.onClick;return button;}
            loadPaymentData(request){if(request.transactionInfo.totalPrice!=='7.31'||request.allowedPaymentMethods[0].tokenizationSpecification.parameters.gateway!=='example')throw Error('Wrong synthetic request');return Promise.resolve({get paymentMethodData(){throw Error('Wallet result must not be read');}});}
          }}}};`});
        if(url.origin===base.origin && url.pathname!=='/private-never-fetch')return route.continue();
        external.push(url.origin);return route.abort('blockedbyclient');
      });
      const page=await context.newPage();page.setDefaultTimeout(10000);
      page.on('pageerror',e=>errors.push(e.message));
      page.on('request',request=>{if(request.method()!=='GET')writes.push(new URL(request.url()).pathname);});
      page.on('response',response=>{
        if(response.url().endsWith('/wallet-test'))capabilities.push(response);
        const url=new URL(response.url());
        if(url.pathname.startsWith('/api/'))httpStates.push([url.pathname.replace(/[0-9a-f-]{36}/g,'[fixture-id]'),response.status()]);
      });
      await page.goto(new URL('/pages/checkout'+language+'.html?quote='+fixture.quote_id,base).href);
      try {await page.locator('[data-wallet-test="google_pay"] button').click();}
      catch {throw Error('Missing Google TEST control; capability statuses='+JSON.stringify(capabilities.map(item=>item.status()))+'; pageErrors='+JSON.stringify(errors)+'; status='+(await page.locator('#purchase-controls').innerText()).slice(0,500));}
      await page.waitForFunction(()=>/未收款|no payment was collected/i.test(document.querySelector('[data-wallet-test="google_pay"] [data-wallet-status]')?.textContent||''));
      const response=capabilities[0];assert.ok(response);assert.equal(response.status(),200);
      const publicConfiguration=await response.json();
      assert.deepEqual(publicConfiguration.wallet_tests.map(item=>item.channel),['google_pay','apple_pay']);
      for(const item of publicConfiguration.wallet_tests){
        assert.equal(item.environment,'TEST');assert.equal(item.account_id,fixture.account_id);
        assert.equal(item.order_id,fixture.order_id);assert.equal(item.amount_minor,'731');assert.equal(item.currency,'CNY');
      }
      await page.locator('[data-wallet-test="apple_pay"] button').click();
      await page.waitForFunction(()=>/商户会话|merchant session/i.test(document.querySelector('[data-wallet-test="apple_pay"] [data-wallet-status]')?.textContent||''));
      assert.equal(await page.evaluate(()=>window.walletBoundary.aborts),1);
      const apple=await page.evaluate(()=>window.walletBoundary.requests[0]);
      assert.equal(apple.request.countryCode,'US');assert.equal(apple.request.currencyCode,'CNY');
      assert.equal(apple.request.total.amount,'7.31');assert.equal(apple.request.recurringPaymentRequest,undefined);
      await page.reload();
      try {await page.locator('#wallet-tests').waitFor();}
      catch {throw Error('Wallet controls absent after reload; HTTP='+JSON.stringify(httpStates)+'; pageErrors='+JSON.stringify(errors)+'; status='+(await page.locator('#purchase-controls').innerText()).slice(0,500));}
      assert.deepEqual(writes,[]);assert.deepEqual(external,[]);assert.deepEqual(errors,[]);
      const detail=await context.request.get(new URL('/api/billing/orders/'+fixture.order_id,base).href,
        {headers:{'X-Billing-Account':fixture.account_id}});
      assert.equal(detail.status(),200);const data=await detail.json();
      assert.equal(data.order.status,'pending_payment');assert.equal(data.order.paid_at,null);
      assert.equal(data.delivery.status,'not_started');assert.deepEqual(data.payments,[]);
      await context.close();
    }
  } finally {await browser.close();input.close();}
})().catch(error=>{console.error('Wallet local HTTP integration failed: '+error.message);input.close();process.exitCode=1;});
