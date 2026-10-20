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
const terms = {version:'isolated-1',digest:'a'.repeat(64),zh:{title:'测试条款',paragraphs:['仅用于测试。<script>window.injected=true</script>']},en:{title:'Test terms',paragraphs:['Isolated fixture only. <script>window.injected=true</script>']}};
const item = {id:product,code:'pro',version:1,kind:'subscription',audience:'personal',billing_cycle:'monthly',currency:'CNY',unit_amount_minor:'731',minimum_seats:1,credits_quantity:'100',entitlements:{benefits:{zh:['隔离测试权益，不销售。'],en:['Isolated benefits, not for sale.']}},terms_version:'isolated-1',quote_available:true,unavailable_reason:null,purchase_options:[{channel:'alipay',buyer_region:'CN'}],terms};
async function walletTestAudit(browser) {
  assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname));
  const sdk = `window.walletBoundary={clients:[],ready:[],payments:[],activations:[],synchronous:[],appleRequests:[],aborts:0,tokenReads:0};
    window.google={payments:{api:{PaymentsClient:class {
      constructor(options){if(window.walletCase==='constructor-error')throw Error('never-display-provider-data');walletBoundary.clients.push(options);}
      async isReadyToPay(request){walletBoundary.ready.push(request);if(window.walletCase==='ready-pending')return new Promise(resolve=>window.releaseReady=()=>resolve({result:true}));if(window.walletCase==='ready-error')throw Error('never-display-provider-data');return {result:window.walletCase!=='unavailable'};}
      createButton(options){const button=document.createElement('button');button.textContent='Google Pay';button.onclick=()=>{walletBoundary.inClick=true;try{options.onClick();}finally{walletBoundary.inClick=false;}};return button;}
      loadPaymentData(request){walletBoundary.payments.push(request);walletBoundary.activations.push(navigator.userActivation.isActive);walletBoundary.synchronous.push(walletBoundary.inClick);
        if(window.walletCase==='throw')throw Error('never-display-provider-data');
        if(window.walletCase==='cancel')return Promise.reject({statusCode:'CANCELED'});
        if(window.walletCase==='error')return Promise.reject({statusCode:'DEVELOPER_ERROR',statusMessage:'never-display-provider-data'});
        if(window.walletCase==='pending')return new Promise(resolve=>window.releaseWallet=()=>resolve({apiVersion:2,apiVersionMinor:0,paymentMethodData:{type:'CARD',description:'Test card',info:{cardNetwork:'VISA',cardDetails:'1111'},tokenizationData:{type:'PAYMENT_GATEWAY',get token(){walletBoundary.tokenReads++;return 'test-token-never-persist';}}}}));
        return Promise.resolve({apiVersion:2,apiVersionMinor:0,paymentMethodData:{type:'CARD',description:'Test card',info:{cardNetwork:'VISA',cardDetails:'1111'},tokenizationData:{type:'PAYMENT_GATEWAY',get token(){walletBoundary.tokenReads++;return 'test-token-never-persist';}}}});
      }
    }}}};
    window.ApplePaySession=class {
      static supportsVersion(version){return window.walletCase!=='unsupported'&&version===3;}
      static canMakePayments(){if(window.walletCase==='capability-error')throw new Error('Blocked capability');return window.walletCase!=='unavailable';}
      constructor(version,request){if(window.walletCase==='constructor-error')throw Error('never-display-provider-data');walletBoundary.appleRequests.push({version,request});window.currentApple=this;}
      begin(){if(window.walletCase==='begin-error')throw Error('never-display-provider-data');if(window.walletCase==='pending')return;queueMicrotask(()=>{if(window.walletCase==='cancel')this.oncancel({});else if(window.walletCase==='authorized')this.onpaymentauthorized({payment:{get token(){walletBoundary.tokenReads++;return {paymentData:'test-token-never-persist'};}}});else this.onvalidatemerchant({validationURL:'http://127.0.0.1/private-never-fetch'});});}
      abort(){walletBoundary.aborts++;if(window.walletCase==='abort-error')throw Error('never-display-provider-data');}
    };`;
  let checks=0;
  for(const language of ['', '-en']) {
    const page=await browser.newPage({viewport:{width:320,height:1000}});
    page.setDefaultTimeout(5000);
    await page.clock.install();
    const errors=[],posts=[],logs=[],unexpectedRequests=[],unexpectedAPI=[];
    let mode='ready',channel='google_pay',enabled=true,wrong=false,cycle='monthly',country=null,sdkFailure=false,sdkMode='ready',heldScript=null,releaseConfig=null,configReads=0;
    page.on('pageerror',error=>errors.push(error.message));
    page.on('console',message=>logs.push(message.text()));
    page.on('request',request=>{
      if(request.method()!=='GET')posts.push([new URL(request.url()).pathname,request.postData()]);
      const url=new URL(request.url());
      if((url.origin!==new URL(base).origin && request.url()!=='https://pay.google.com/gp/p/js/pay.js') || url.pathname==='/private-never-fetch')unexpectedRequests.push(url.pathname);
    });
    await page.addInitScript(source=>{window.walletCase='ready';eval(source);delete window.google;},sdk);
    await page.route(/\/billing\.js(?:\?.*)?$/,route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../src/pages/billing.js'),'utf8')}));
    await page.route('https://pay.google.com/gp/p/js/pay.js',route=>{
      if(sdkMode==='pending'){heldScript=route;return;}
      return sdkFailure?route.abort('failed'):route.fulfill({contentType:'text/javascript',body:sdkMode==='empty'?'/* SDK namespace missing. */':sdk});
    });
    await page.route('**/api/**',async route=>{
      const request=route.request(),endpoint=new URL(request.url()).pathname;
      const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
      const session=active=>({authenticated:true,user:{id:'fixture-wallet-user'},accounts:[{id:account,name:'个人账户',kind:'personal',role:'owner'},{id:product,name:'Second',kind:'personal',role:'owner'}],active_account_id:active,csrf_token:'fixture-csrf'});
      if(endpoint==='/api/auth/session')return json(session(account));
      if(endpoint==='/api/billing/accounts/select')return json(session(request.postDataJSON().account_id));
      if(endpoint==='/api/auth/logout')return json({authenticated:false,accounts:[],active_account_id:null,csrf_token:'fixture-csrf'});
      if(endpoint==='/api/account/notifications/summary'){assert.equal(request.method(),'GET');return json({user_id:'fixture-wallet-user',unread_count:0});}
      if(endpoint==='/api/billing/catalog')return json(JSON.parse(fs.readFileSync(path.join(__dirname,'../src/pages/billing-catalog.json'),'utf8')));
      if(endpoint===`/api/billing/purchase-quotes/${quote}`)return json({id:quote,account_id:account,amount_minor:731,currency:'CNY',expires_at:'2099-01-01T00:00:00Z',snapshot:{...item,product_id:product,amount_minor:731,seat_quantity:1},terms,can_order:false,unavailable_reason:'QUOTE_ALREADY_ORDERED',order_id:order});
      if(endpoint===`/api/billing/orders/${order}/wallet-test`){
        configReads++;assert.equal(request.headers()['x-billing-account'],account);
        if(mode==='delayed')await new Promise(resolve=>{releaseConfig=resolve;});
        if(!enabled)return route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"CHANNEL_UNAVAILABLE"}}'});
        return json({wallet_tests:(channel==='mixed'?['google_pay','apple_pay']:[channel]).map(channel=>({environment:mode==='production'?'PRODUCTION':'TEST',channel,currency:'CNY',amount_minor:'731',billing_cycle:cycle,account_id:wrong?product:account,order_id:order,...(country?{country_code:country}:{})}))});
      }
      if(endpoint!==`/api/billing/orders/${order}`)unexpectedAPI.push(endpoint);
      return route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"CHANNEL_UNAVAILABLE"}}'});
    });
    const visit=async()=>{await page.goto(`${base}/pages/checkout${language}.html?quote=${quote}&wallet_test=1`,{waitUntil:'domcontentloaded'});await page.locator('#purchase-order-link').waitFor();};
    // Break caught: an authorized existing order never consumes server TEST capability.
    await visit();
    await page.locator('[data-wallet-test="google_pay"] button').waitFor();
    await page.locator('[data-wallet-test="google_pay"] button').click();
    await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/未收款|no payment was collected/i));
    assert.match(await page.locator('#wallet-tests').innerText(),language?/example gateway/:/example gateway/);
    assert.equal(await page.locator('#purchase-order-link').getAttribute('href'),`billing${language}.html?order=${order}#orders`);
    const boundary=await page.evaluate(()=>walletBoundary);
    assert.deepEqual(boundary.clients,[{environment:'TEST'}]);
    assert.equal(boundary.tokenReads,0,'The returned Google token property is never read');
    assert.equal(boundary.activations[0],true,'The SDK is invoked within the user click activation');
    assert.equal(boundary.synchronous[0],true,'The SDK must be invoked before the click handler returns');
    assert.deepEqual(boundary.payments[0].transactionInfo,{currencyCode:'CNY',totalPriceStatus:'FINAL',totalPrice:'7.31',checkoutOption:'DEFAULT'});
    assert.deepEqual(boundary.payments[0].allowedPaymentMethods[0].tokenizationSpecification,{type:'PAYMENT_GATEWAY',parameters:{gateway:'example',gatewayMerchantId:'exampleGatewayMerchantId'}});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
    checks++;
    for(const width of [320,1440]){
      await page.setViewportSize({width,height:1000});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`Google ${language||'zh'} ${width}`);checks++;
      await page.locator('main *').evaluateAll(nodes=>{const sizes=nodes.map(el=>parseFloat(getComputedStyle(el).fontSize));nodes.forEach((el,i)=>el.style.fontSize=`${sizes[i]*2}px`);});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`Google ${language||'zh'} ${width} 200% text`);checks++;
      await page.locator('main *').evaluateAll(nodes=>nodes.forEach(el=>el.style.removeProperty('font-size')));
    }
    await page.evaluate(()=>document.documentElement.style.zoom='200%');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`Google ${language||'zh'} 1440 CSS zoom 200% (not native browser zoom)`);checks++;
    await page.evaluate(()=>document.documentElement.style.removeProperty('zoom'));await page.setViewportSize({width:320,height:1000});
    // Break caught: canceled/failed SDK promises imply payment or leak error/token data.
    for(const outcome of ['cancel','error']){
      await page.evaluate(value=>window.walletCase=value,outcome);
      await page.locator('[data-wallet-test="google_pay"] button').click();
      await page.waitForFunction(outcome=>document.querySelector('[data-wallet-status]')?.textContent.match(outcome==='cancel'?/取消|cancel/i:/未确认|unconfirmed/i),outcome);
      checks++;
    }
    // Break caught: a second click opens another sheet before the first resolves.
    await page.evaluate(()=>window.walletCase='pending');
    const before=await page.evaluate(()=>walletBoundary.payments.length);
    await page.locator('[data-wallet-test="google_pay"] button').evaluate(button=>{button.click();button.click();});
    assert.equal(await page.evaluate(()=>walletBoundary.payments.length),before+1);
    await page.evaluate(()=>releaseWallet());
    await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/未收款|no payment was collected/i));
    checks++;
    // Break caught: refreshing the panel bypasses the outstanding Google sheet lock.
    await page.locator('[data-wallet-test="google_pay"] button').click();
    const pendingCalls=await page.evaluate(()=>walletBoundary.payments.length);
    await page.locator('[data-wallet-refresh]').click();
    await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/等待.*关闭|waiting.*close/i));
    await page.locator('[data-wallet-refresh]').click();
    await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/等待.*关闭|waiting.*close/i));
    assert.equal(await page.locator('[data-wallet-test="google_pay"] button').count(),0);
    assert.equal(await page.evaluate(()=>walletBoundary.payments.length),pendingCalls);
    const waiting=await page.locator('[data-wallet-status]').innerText();
    await page.evaluate(()=>releaseWallet());
    await page.waitForTimeout(30);
    assert.equal(await page.locator('[data-wallet-status]').innerText(),waiting,'Old sheet callbacks cannot overwrite a new panel');
    await page.evaluate(()=>window.walletCase='ready');
    await page.locator('[data-wallet-refresh]').click();
    await page.locator('[data-wallet-test="google_pay"] button').waitFor();checks++;
    // Break caught: readiness hangs forever or a late result revives an expired check.
    await page.evaluate(()=>window.walletCase='ready-pending');
    await page.locator('[data-wallet-refresh]').click();
    await page.waitForFunction(()=>typeof window.releaseReady==='function');
    const readinessCalls=await page.evaluate(()=>walletBoundary.ready.length);
    await page.clock.fastForward(8001);
    await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/检查失败|failed to load or check/i));
    assert.equal(await page.locator('[data-wallet-test="google_pay"] button').count(),0);
    await page.clock.fastForward(16001);
    assert.equal(await page.evaluate(()=>walletBoundary.ready.length),readinessCalls,'Readiness never retries automatically');
    await page.evaluate(()=>window.walletCase='unavailable');
    await page.locator('[data-wallet-refresh]').click();
    await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/不可用|unavailable/i));
    await page.evaluate(()=>releaseReady());await page.waitForTimeout(30);
    assert.equal(await page.locator('[data-wallet-test="google_pay"] button').count(),0);checks++;
    for(const outcome of ['constructor-error','ready-error','throw']){
      await page.evaluate(()=>window.walletCase='ready');await page.locator('[data-wallet-refresh]').click();
      await page.locator('[data-wallet-test="google_pay"] button').waitFor();
      await page.evaluate(value=>window.walletCase=value,outcome);
      if(outcome==='throw')await page.locator('[data-wallet-test="google_pay"] button').click();else await page.locator('[data-wallet-refresh]').click();
      await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/失败|未确认|failed|unconfirmed/i));
      assert.ok(!(await page.locator('#wallet-tests').innerText()).includes('never-display-provider-data'));checks++;
    }
    assert.equal(await page.evaluate(()=>walletBoundary.tokenReads),0,'Pending Google returns never read a token');
    for(const method of ['isReadyToPay','createButton','loadPaymentData']){
      await visit();await page.locator('[data-wallet-test="google_pay"] button').waitFor();
      await page.evaluate(key=>{window.google.payments.api.PaymentsClient.prototype[key]=undefined;},method);
      if(method==='loadPaymentData')await page.locator('[data-wallet-test="google_pay"] button').click();else await page.locator('[data-wallet-refresh]').click();
      await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/失败|未确认|failed|unconfirmed/i));checks++;
    }
    await page.evaluate(()=>window.walletCase='unavailable');
    await page.locator('[data-wallet-refresh]').click();
    await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/不可用|unavailable/i));
    assert.equal(await page.locator('[data-wallet-test="google_pay"] button').count(),0);
    checks++;
    // Break caught: a client flag or production/wrong-account response enables tests.
    for(const bad of ['disabled','production','wrong']){
      enabled=bad!=='disabled';mode=bad==='production'?'production':'ready';wrong=bad==='wrong';
      await visit();
      await page.waitForFunction(()=>document.querySelector('#purchase-order-link'));
      await page.waitForTimeout(100);
      assert.equal(await page.locator('#wallet-tests').count(),0);
      checks++;
    }
    enabled=true;wrong=false;mode='ready';
    // Break caught: SDK failure has no user-driven recovery path.
    sdkFailure=true;await visit();
    await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/加载失败|failed to load/i));
    sdkFailure=false;await page.locator('[data-wallet-refresh]').click();
    await page.locator('[data-wallet-test="google_pay"] button').waitFor();
    checks++;
    // Break caught: a loaded script without PaymentsClient leaves a resolved promise that prevents manual reload.
    sdkMode='empty';await visit();
    await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/加载失败|failed to load/i));
    sdkMode='ready';await page.locator('[data-wallet-refresh]').click();
    await page.locator('[data-wallet-test="google_pay"] button').waitFor();checks++;
    // Break caught: a stalled external script leaves checking permanently pending.
    sdkMode='pending';await visit();
    await page.locator('[data-wallet-test="google_pay"]').waitFor();
    for(let n=0;!heldScript&&n<100;n++)await page.waitForTimeout(10);
    assert.ok(heldScript);await page.clock.fastForward(8001);
    await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/加载失败|failed to load/i));
    await heldScript.abort();heldScript=null;sdkMode='ready';await page.locator('[data-wallet-refresh]').click();
    await page.locator('[data-wallet-test="google_pay"] button').waitFor();checks++;
    // Break caught: late capability responses revive cleared account data.
    mode='delayed';await visit();
    for(let n=0;!releaseConfig&&n<100;n++)await page.waitForTimeout(10);
    assert.ok(releaseConfig);
    await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
    releaseConfig();await page.waitForTimeout(100);
    assert.equal(await page.locator('#wallet-tests').count(),0);
    mode='ready';checks++;
    // Break caught: missing Apple country is fabricated from company domicile.
    channel='apple_pay';country=null;
    await visit();await page.locator('[data-wallet-test="apple_pay"]').waitFor();
    assert.equal(await page.locator('[data-wallet-test="apple_pay"] button').count(),0);
    assert.match(await page.locator('[data-wallet-test="apple_pay"]').innerText(),language?/country/:/国家/);
    checks++;
    for(cycle of ['monthly','annual']){
      country='US';await visit();
      await page.locator('[data-wallet-test="apple_pay"] button').click();
      await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/商户会话|merchant session/i));
      const apple=await page.evaluate(()=>walletBoundary);
      assert.equal(apple.aborts,1);
      assert.equal(apple.appleRequests[0].request.countryCode,'US');
      assert.equal(apple.appleRequests[0].request.currencyCode,'CNY');
      assert.equal(apple.appleRequests[0].request.total.amount,'7.31');
      assert.match(apple.appleRequests[0].request.total.label,cycle==='annual'?/annual/:/monthly/);
      assert.equal(apple.appleRequests[0].request.recurringPaymentRequest,undefined);
      checks++;
    }
    await page.evaluate(()=>window.walletCase='cancel');
    await page.locator('[data-wallet-test="apple_pay"] button').click();
    await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/取消|cancel/i));
    checks++;
    await page.evaluate(()=>window.walletCase='unavailable');
    await page.locator('[data-wallet-refresh]').click();
    await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/不可用|unavailable/i));
    assert.equal(await page.locator('[data-wallet-test="apple_pay"] button').count(),0);
    checks++;
    // Break caught: capability checks throw outside the local error boundary.
    await page.evaluate(()=>window.walletCase='capability-error');
    await page.locator('[data-wallet-refresh]').click();
    await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/不可用|unavailable/i));
    assert.equal(await page.locator('[data-wallet-test="apple_pay"] button').count(),0);
    checks++;
    // Break caught: unsupported or incomplete Apple APIs expose a button or uncaught exception.
    for(const outcome of ['unsupported','missing-api','missing-can','missing-support']){
      await page.evaluate(value=>{window.walletCase=value;if(value==='missing-api')window.ApplePaySession=undefined;if(value==='missing-can')window.ApplePaySession.canMakePayments=undefined;if(value==='missing-support')window.ApplePaySession.supportsVersion=undefined;},outcome);
      await page.locator('[data-wallet-refresh]').click();
      await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/不可用|unavailable/i));
      assert.equal(await page.locator('[data-wallet-test="apple_pay"] button').count(),0);checks++;
      await visit();await page.locator('[data-wallet-test="apple_pay"] button').waitFor();
    }
    // Break caught: constructor/begin/abort exceptions or an authorization callback escape the safety boundary.
    for(const outcome of ['constructor-error','begin-error','abort-error','authorized','missing-begin','missing-abort']){
      await page.evaluate(value=>{window.walletCase=value;if(value==='missing-begin')window.ApplePaySession.prototype.begin=undefined;if(value==='missing-abort')window.ApplePaySession.prototype.abort=undefined;},outcome);
      await page.locator('[data-wallet-test="apple_pay"] button').click();
      await page.waitForFunction(()=>document.querySelector('[data-wallet-status]')?.textContent.match(/会话不可用|商户会话|关闭未确认|session is unavailable|merchant session|closure is unconfirmed/i));
      if(['abort-error','missing-abort'].includes(outcome)){
        assert.equal(await page.locator('[data-wallet-test="apple_pay"] button').isDisabled(),true,'An uncertain close cannot unlock another sheet');
        await page.evaluate(()=>currentApple.oncancel({}));
      }
      assert.equal(await page.locator('[data-wallet-test="apple_pay"] button:not([disabled])').count(),1);
      assert.ok(!(await page.locator('#wallet-tests').innerText()).includes('never-display-provider-data'));checks++;
      assert.equal(await page.evaluate(()=>walletBoundary.tokenReads),0,'Apple authorization callbacks never read a token');
      await visit();await page.locator('[data-wallet-test="apple_pay"] button').waitFor();
    }
    // Break caught: two available wallets open simultaneous sheets, or abort failure loses ownership.
    channel='mixed';country='US';
    const mixedResults=[];
    await visit();await page.locator('[data-wallet-test="google_pay"] button').waitFor();await page.locator('[data-wallet-test="apple_pay"] button').waitFor();
    await page.evaluate(()=>window.walletCase='pending');
    await page.locator('[data-wallet-test="google_pay"] button').click();
    await page.locator('[data-wallet-test="apple_pay"] button').click();
    mixedResults.push({case:'Google blocks Apple',apple:await page.evaluate(()=>walletBoundary.appleRequests.length)});
    await page.evaluate(()=>releaseWallet());
    await visit();await page.locator('[data-wallet-test="google_pay"] button').waitFor();await page.locator('[data-wallet-test="apple_pay"] button').waitFor();
    await page.evaluate(()=>window.walletCase='pending');
    await page.locator('[data-wallet-test="apple_pay"] button').click();
    await page.locator('[data-wallet-test="google_pay"] button').click();
    mixedResults.push({case:'Apple blocks Google',google:await page.evaluate(()=>walletBoundary.payments.length)});
    await visit();await page.locator('[data-wallet-test="google_pay"] button').waitFor();await page.locator('[data-wallet-test="apple_pay"] button').waitFor();
    await page.evaluate(()=>window.walletCase='pending');
    await page.locator('[data-wallet-test="apple_pay"] button').click();
    await page.evaluate(()=>{window.originalApple=currentApple;window.originalAppleCancel=currentApple.oncancel;window.walletCase='abort-error';});
    await page.locator('[data-wallet-refresh]').click();
    await page.locator('[data-wallet-test="apple_pay"]').waitFor();
    await page.locator('[data-wallet-test="google_pay"]').waitFor();
    await page.waitForTimeout(30);
    const priorApple=await page.evaluate(()=>walletBoundary.appleRequests.length),priorGoogle=await page.evaluate(()=>walletBoundary.payments.length);
    await page.locator('[data-wallet-test] button').evaluateAll(buttons=>buttons.forEach(button=>button.click()));
    await page.waitForTimeout(30);
    mixedResults.push({case:'Unconfirmed abort blocks both',apple:await page.evaluate(()=>walletBoundary.appleRequests.length)-priorApple,google:await page.evaluate(()=>walletBoundary.payments.length)-priorGoogle});
    console.log(JSON.stringify({layer:'Mixed-wallet mutual exclusion and abort ownership',language:language||'zh',observed:mixedResults}));
    assert.deepEqual(mixedResults,[{case:'Google blocks Apple',apple:0},{case:'Apple blocks Google',google:0},{case:'Unconfirmed abort blocks both',apple:0,google:0}]);checks+=3;
    assert.equal(await page.evaluate(()=>typeof originalApple.oncancel),'function','The original cancellation channel survives an unconfirmed abort');
    assert.match(await page.locator('#wallet-tests').innerText(),language?/closure is unconfirmed/:/关闭未确认/);
    await page.locator('[data-wallet-refresh]').click();
    assert.equal(await page.evaluate(()=>walletBoundary.aborts),1,'Panel refresh cannot silently retry an unknown close');
    await page.evaluate(()=>originalApple.oncancel({}));
    await page.locator('[data-wallet-refresh]').click();
    await page.locator('[data-wallet-test="apple_pay"] button').waitFor();
    await page.evaluate(()=>window.walletCase='pending');await page.locator('[data-wallet-test="apple_pay"] button').click();
    assert.equal(await page.evaluate(()=>walletBoundary.appleRequests.length),priorApple+1,'A confirmed cancellation permits a user-driven new session');
    await page.evaluate(()=>originalAppleCancel({}));
    assert.equal(await page.locator('[data-wallet-test="apple_pay"] button').isDisabled(),true,'An old cancellation cannot release a replacement session');checks++;
    channel='apple_pay';
    // Break caught: an active Apple sheet survives replacement, sign-out, account change or pagehide.
    for(const action of ['refresh','account','logout','pagehide']){
      await visit();await page.locator('[data-wallet-test="apple_pay"] button').waitFor();
      await page.evaluate(()=>window.walletCase='pending');
      await page.locator('[data-wallet-test="apple_pay"] button').click();
      await page.evaluate(()=>{window.staleAppleCancel=currentApple.oncancel;window.staleAppleMerchant=currentApple.onvalidatemerchant;});
      const aborts=await page.evaluate(()=>walletBoundary.aborts);
      if(action==='refresh')await page.locator('[data-wallet-refresh]').click();
      if(action==='account')await page.locator('#billing-account-select').selectOption(product);
      if(action==='logout'){await page.locator('.nav-account summary').click();await page.locator('#account-logout').click();}
      if(action==='pagehide')await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
      await page.waitForFunction(expected=>walletBoundary.aborts===expected,aborts+1);
      if(action==='refresh'){await page.locator('[data-wallet-test="apple_pay"] button').waitFor();await page.locator('[data-wallet-test="apple_pay"] button').click();}
      if(action==='account')await page.locator('#purchase-recover').waitFor();
      const markup=await page.locator('#purchase-controls').innerText();
      await page.evaluate(()=>{staleAppleCancel({});staleAppleMerchant({validationURL:'http://127.0.0.1/private-never-fetch'});});
      await page.waitForTimeout(30);
      assert.equal(await page.locator('#purchase-controls').innerText(),markup,`${language||'zh'} stale Apple callbacks after ${action}`);
      assert.equal(await page.evaluate(()=>walletBoundary.aborts),aborts+1,'Stale callbacks cannot abort a replacement session');checks++;
    }
    await visit();await page.locator('[data-wallet-test="apple_pay"] button').waitFor();
    for(const width of [320,1440]){
      await page.setViewportSize({width,height:1000});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`${language||'zh'} ${width}`);checks++;
      await page.locator('main *').evaluateAll(nodes=>{const sizes=nodes.map(el=>parseFloat(getComputedStyle(el).fontSize));nodes.forEach((el,i)=>el.style.fontSize=`${sizes[i]*2}px`);});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`${language||'zh'} ${width} 200% text`);checks++;
      await page.locator('main *').evaluateAll(nodes=>nodes.forEach(el=>el.style.removeProperty('font-size')));
    }
    await page.evaluate(()=>document.documentElement.style.zoom='200%');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`${language||'zh'} 1440 CSS zoom 200% (not native browser zoom)`);checks++;
    assert.ok(posts.every(([endpoint])=>['/api/billing/accounts/select','/api/auth/logout'].includes(endpoint)),'Wallet callbacks cannot send token, pay or fulfill an order');
    assert.deepEqual(unexpectedRequests,[],'Only the fixed official SDK URL may leave the isolated site; no validation URL is followed');
    assert.deepEqual(unexpectedAPI,[],'Unknown API requests are rejected, never silently accepted');
    assert.equal(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}).includes('test-token-never-persist')),false);
    assert.ok(!logs.join('\n').includes('test-token-never-persist'));
    assert.ok(!logs.join('\n').includes('never-display-provider-data'));
    assert.ok(configReads>=8);
    assert.deepEqual(errors,[]);
    await page.close();
  }
  console.log(JSON.stringify({layer:'Wallet SDK boundary fixtures in real checkout DOM; no official sheet/PSP/charge',checks}));
}
async function wechatNativeAudit(browser) {
  assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname));
  const attemptId='55555555-5555-4555-8555-555555555555';
  // Real QR renderer, synthetic payload; all provider HTTP remains intercepted.
  const rendered=spawnSync(process.env.SAGE_PYTHON||path.join(__dirname,'../.venv/bin/python'),['-B','-c',
    'from backend.wechat import wechat_qr_image; print(wechat_qr_image("weixin://wxpay/bizpayurl?pr=synthetic2026"))'],
    {cwd:path.join(__dirname,'..'),encoding:'utf8',timeout:10000});
  assert.equal(rendered.status,0,'The installed backend QR renderer must create the synthetic fixture');
  const image=rendered.stdout.trim();
  let checks=0;
  for(const language of ['', '-en']) for(const width of [320,1440]) {
    const page=await browser.newPage({viewport:{width,height:1000}});page.setDefaultTimeout(5000);
    let enabled=true,attempt=null,qr=null,orderState='pending_payment',delivery='not_started',mode='ready',invalid='',releasePayment=null,releaseRead=null,reads=0,pays=0,queries=0,active=account,signedIn=true;
    const errors=[],logs=[],external=[],writes=[];
    const expires=()=>new Date(Date.now()+60000).toISOString();
    const start=()=>({environment:'LOCAL_TEST',channel:'wechat',available:true,account_id:account,order_id:order});
    const dispatched=()=>{attempt={id:attemptId,channel:'wechat',status:'pending',expires_at:expires(),needs_reconciliation:false};qr={channel:'wechat',account_id:account,order_id:order,attempt_id:attemptId,expires_at:attempt.expires_at,image_svg_base64:image};};
    const reset=()=>{enabled=true;attempt=null;qr=null;orderState='pending_payment';delivery='not_started';mode='ready';invalid='';active=account;signedIn=true;};
    page.on('pageerror',error=>errors.push(error.message));page.on('console',message=>logs.push(message.text()));
    page.on('request',request=>{if(request.method()!=='GET')writes.push(new URL(request.url()).pathname);});
    await page.route('**/*',route=>{
      if(new URL(route.request().url()).origin===new URL(base).origin)return route.fallback();
      external.push(route.request().url());return route.abort('blockedbyclient');
    });
    await page.route(/\/billing\.js(?:\?.*)?$/,route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../src/pages/billing.js'),'utf8')}));
    await page.route('**/api/**',async route=>{
      const request=route.request(),endpoint=new URL(request.url()).pathname;
      const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
      const session=()=>({authenticated:signedIn,user:signedIn?{id:'fixture-wechat-user'}:null,accounts:[{id:account,name:'个人账户',kind:'personal',role:'owner'},{id:product,name:'Other fixture',kind:'business',role:'owner'}],active_account_id:active,csrf_token:'fixture-csrf'});
      if(endpoint==='/api/auth/session')return json(session());
      if(endpoint==='/api/auth/logout'){signedIn=false;return json({authenticated:false});}
      if(endpoint==='/api/billing/accounts/select'){active=request.postDataJSON().account_id;return json(session());}
      if(endpoint==='/api/account/notifications/summary')return json({user_id:'fixture-wechat-user',unread_count:0});
      if(endpoint==='/api/billing/catalog')return json(JSON.parse(fs.readFileSync(path.join(__dirname,'../src/pages/billing-catalog.json'),'utf8')));
      if(endpoint===`/api/billing/purchase-quotes/${quote}`)return json({id:quote,account_id:active,amount_minor:731,currency:'CNY',expires_at:'2099-01-01T00:00:00Z',snapshot:{...item,kind:'credits',billing_cycle:'',channel:'wechat',product_id:product,amount_minor:731,seat_quantity:1},terms,can_order:false,unavailable_reason:'QUOTE_ALREADY_ORDERED',order_id:order});
      if(endpoint===`/api/billing/orders/${order}`){
        reads++;assert.equal(request.method(),'GET');
        if(mode==='read-failure')return route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"CHANNEL_UNAVAILABLE"}}'});
        const detail={order:{id:order,account_id:account,status:orderState,amount_minor:invalid==='amount'?732:731,currency:invalid==='currency'?'USD':'CNY',paid_at:orderState==='paid'||orderState==='fulfilled'?'2026-10-16T00:00:00Z':null,snapshot:{kind:'credits',billing_cycle:'',channel:'wechat'}},payment_attempt:attempt,payments:[],delivery:{status:delivery},refunds:[],...(enabled&&!attempt?{payment_start:start()}:{}),...(enabled&&qr?{payment_qr:{...qr}}:{})};
        if(detail.payment_qr){
          if(invalid==='account')detail.payment_qr.account_id=product;
          if(invalid==='order')detail.payment_qr.order_id=product;
          if(invalid==='attempt')detail.payment_qr.attempt_id=product;
          if(invalid==='expires')detail.payment_qr.expires_at='2099-01-01T00:00:00Z';
          if(invalid==='channel')detail.payment_qr.channel='alipay';
          if(invalid==='url')detail.payment_qr.image_svg_base64='https://evil.invalid/image.svg';
          if(invalid==='base64')detail.payment_qr.image_svg_base64=image+'\n';
          if(invalid==='svg')detail.payment_qr.image_svg_base64=Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><image href="https://evil.invalid/secret"/><script>alert(1)</script></svg>').toString('base64');
          if(invalid==='non-svg')detail.payment_qr.image_svg_base64=Buffer.from('<html>invalid</html>').toString('base64');
          if(invalid==='delivery')detail.delivery.status='completed';
          if(invalid==='attempt-channel')detail.payment_attempt={...attempt,channel:'alipay'};
          if(invalid==='large-image')detail.payment_qr.image_svg_base64='A'.repeat(524288);
          if(invalid==='review')detail.needs_review=true;
          if(invalid==='payment-fact')detail.payments=[{id:product,status:'accepted',amount_minor:731,currency:'CNY',paid_at:'2026-10-16T00:00:00Z'}];
        }
        if(mode==='delayed-read')await new Promise(resolve=>{releaseRead=resolve;});
        return json(detail);
      }
      if(endpoint===`/api/billing/orders/${order}/pay`||endpoint===`/api/billing/orders/${order}/payment-status`){
        assert.equal(request.method(),'POST');assert.deepEqual(request.postDataJSON(),{});assert.equal(request.headers()['x-csrftoken'],'fixture-csrf');assert.equal(request.headers()['x-billing-account'],account);
        if(endpoint.endsWith('/payment-status')){queries++;if(mode==='query-failure')return route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"CHANNEL_UNAVAILABLE"}}'});return json({payment_attempt:attempt});}
        pays++;dispatched();
        if(mode==='delayed-pay'||mode==='timeout')await new Promise(resolve=>{releasePayment=resolve;});
        if(request.failure())return;
        if(mode==='lost')return route.abort('failed');
        return json({payment_attempt:attempt,payment_qr:qr});
      }
      return route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"CHANNEL_UNAVAILABLE"}}'});
    });
    const visit=async()=>{await page.goto(`${base}/pages/checkout${language}.html?quote=${quote}`,{waitUntil:'domcontentloaded'});await page.locator('#purchase-order-link').waitFor();};
    const noQR=async()=>assert.equal(await page.locator('#purchase-payment-qr').count(),0);
    const loaded=async()=>{await page.locator('#purchase-payment-qr').waitFor();await page.waitForFunction(()=>{const image=document.querySelector('#purchase-payment-qr');return image?.complete&&image.naturalWidth>0;});};
    // Break caught: authorized Native entry never consumes its capability or uses a navigation URL.
    await visit();await page.locator('#purchase-payment-start').waitFor();assert.equal(pays,0);checks++;
    const before=pays;await page.locator('#purchase-payment-start').evaluate(button=>{button.click();button.click();});await loaded();assert.equal(pays,before+1);checks++;
    assert.match(await page.locator('#purchase-payment').innerText(),language?/isolated local test.*not.*live/i:/隔离本地测试.*不是已开通正式微信收款/);
    assert.match(await page.locator('#purchase-payment-qr').getAttribute('alt'),language?/WeChat/:/微信/);
    assert.match(await page.locator('#purchase-payment-qr').getAttribute('src'),/^data:image\/svg\+xml;base64,[A-Za-z0-9+/]+=*$/);
    assert.equal(await page.locator('#purchase-payment-qr').evaluate(image=>getComputedStyle(image).backgroundColor),'rgb(255, 255, 255)');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);checks++;
    // Break caught: reload dispatches another Native instead of recovering the persistent original QR.
    await page.reload();await loaded();assert.equal(pays,before+1);checks++;
    const beforeQuery=queries;await page.locator('#purchase-payment-refresh').focus();await page.keyboard.press('Enter');await loaded();assert.equal(queries,beforeQuery+1);assert.equal(pays,before+1);checks++;
    // Break caught: expiry leaves an actionable stale image or treats elapsed time as failed payment.
    await page.clock.install();dispatched();await visit();await loaded();await page.clock.fastForward(61001);await noQR();assert.match(await page.locator('#purchase-payment-status').innerText(),language?/expired.*original/i:/过期.*原交易/);assert.equal(pays,before+1);await page.clock.setSystemTime(Date.now());checks++;
    // Break caught: lost POST response unlocks another pay, or status uses GET without provider query.
    reset();mode='lost';await visit();const lostPays=pays;await page.locator('#purchase-payment-start').click();await page.waitForFunction(()=>document.querySelector('#purchase-payment-status')?.textContent.match(/未确认|unconfirmed/i));
    await noQR();assert.equal(await page.locator('#purchase-payment-start:not([disabled])').count(),0);const lostQuery=queries;mode='ready';await page.locator('#purchase-payment-refresh').click();await loaded();assert.equal(queries,lostQuery+1);assert.equal(pays,lostPays+1);checks++;
    if(!language&&width===320){
      // Break caught: the real fetch deadline leaves pay unlocked or creates another Native.
      reset();mode='timeout';await visit();const timedPays=pays;await page.locator('#purchase-payment-start').click();
      await page.waitForFunction(()=>document.querySelector('#purchase-payment-status')?.textContent.match(/未确认|unconfirmed/i),null,{timeout:15000});
      assert.ok(releasePayment);releasePayment();releasePayment=null;await noQR();assert.equal(await page.locator('#purchase-payment-start:not([disabled])').count(),0);mode='ready';await page.locator('#purchase-payment-refresh').click();await loaded();assert.equal(pays,timedPays+1);checks++;
    }
    // Break caught: revoked capability or failed reread leaves the old QR on screen.
    for(const revoked of ['disabled','read-failure']){reset();dispatched();await visit();await loaded();if(revoked==='disabled')enabled=false;else mode=revoked;const prior=reads;await page.locator('#purchase-payment-refresh').click();for(let n=0;reads===prior&&n<100;n++)await page.waitForTimeout(10);await noQR();assert.equal(await page.locator('#purchase-payment-start:not([disabled])').count(),0);checks++;}
    // Break caught: malformed image or mismatched financial identity exposes an unverified payment image.
    for(invalid of ['account','order','attempt','expires','channel','amount','currency','url','base64','svg','non-svg','delivery','attempt-channel','large-image','review','payment-fact']){attempt=null;qr=null;enabled=true;mode='ready';dispatched();const prior=reads;await visit();for(let n=0;reads===prior&&n<100;n++)await page.waitForTimeout(10);await page.waitForTimeout(30);await noQR();assert.equal(await page.locator('#purchase-payment-start:not([disabled])').count(),0);checks++;}
    // Break caught: a failed query revives a stale image or removes the only safe recovery action.
    reset();dispatched();await visit();await loaded();mode='query-failure';await page.locator('#purchase-payment-refresh').click();await noQR();await page.waitForFunction(()=>!document.querySelector('#purchase-payment-refresh')?.disabled);assert.equal(await page.locator('#purchase-payment-start').count(),0);mode='ready';await page.locator('#purchase-payment-refresh').click();await loaded();checks++;
    // Break caught: terminal/review states retain a payable QR; client invents fulfillment.
    for(const state of ['paid','fulfilled','unknown','review','closed']){reset();dispatched();if(['unknown','review'].includes(state))attempt={...attempt,status:state,needs_reconciliation:true};else orderState=state;delivery=state==='fulfilled'?'completed':state==='paid'?'pending':'not_started';await visit();await page.locator('#purchase-payment-status').waitFor();await noQR();assert.equal(await page.locator('#purchase-payment-start:not([disabled])').count(),0);if(state==='paid')assert.match(await page.locator('#purchase-payment-status').innerText(),language?/delivery.*processing.*do not pay/i:/交付.*处理中.*不要.*付款/);checks++;}
    // Break caught: background page or late account response restores revoked screen capability.
    reset();dispatched();await visit();await loaded();await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});document.dispatchEvent(new Event('visibilitychange'));});await noQR();checks++;
    await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});document.dispatchEvent(new Event('visibilitychange'));});await loaded();checks++;
    reset();mode='delayed-pay';await visit();await page.locator('#purchase-payment-start').click();for(let n=0;!releasePayment&&n<100;n++)await page.waitForTimeout(10);assert.ok(releasePayment);mode='ready';await page.locator('.nav-account summary').click();await page.locator('#billing-account-select').selectOption(product);releasePayment();releasePayment=null;await page.waitForTimeout(100);await noQR();checks++;
    reset();dispatched();mode='delayed-read';await visit();for(let n=0;!releaseRead&&n<100;n++)await page.waitForTimeout(10);assert.ok(releaseRead);await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));releaseRead();releaseRead=null;await page.waitForTimeout(100);await noQR();assert.equal(await page.locator('#purchase-payment-start:not([disabled])').count(),0);checks++;
    reset();dispatched();await visit();await loaded();await page.locator('.nav-account summary').click();await page.locator('#account-logout').click();await noQR();checks++;
    assert.deepEqual(external,[]);assert.deepEqual(errors,[]);assert.ok(!logs.join('\n').includes(image));assert.ok(!logs.join('\n').includes('weixin:'));
    assert.deepEqual(await page.evaluate(()=>({local:{...localStorage},session:{...sessionStorage}})),{local:{},session:{}});
    assert.ok(writes.every(endpoint=>[...['pay','payment-status'].map(action=>`/api/billing/orders/${order}/${action}`),'/api/billing/accounts/select','/api/auth/logout'].includes(endpoint)),'No fulfillment or other business mutation');
    await page.close();
  }
  console.log(JSON.stringify({layer:'WeChat Native real DOM and local HTTP fixtures, no provider request or actual payment',checks,pageErrors:0}));
}
async function alipaySandboxAudit(browser) {
  assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname));
  let checks=0;
  for(const language of ['', '-en']) {
    const page=await browser.newPage({viewport:{width:320,height:1000}});
    page.setDefaultTimeout(5000);
    let capability='ready',response='ready',attempt=null,posts=0,reads=0,releasePayment=null;
    const errors=[],navigation=[];
    const sandboxURL='https://openapi.alipaydev.com/gateway.do?app_id=fixture&method=alipay.trade.page.pay&sign=fixture';
    let paymentURL=sandboxURL;
    page.on('pageerror',error=>errors.push(error.message));
    await page.route(/\/billing\.js(?:\?.*)?$/,route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../src/pages/billing.js'),'utf8')}));
    await page.route('https://openapi.alipaydev.com/**',route=>{navigation.push(route.request().url());return route.fulfill({contentType:'text/html',body:'<p>Navigation intercepted; no provider request.</p>'});});
    await page.route('**/api/**',async route=>{
      const request=route.request(),endpoint=new URL(request.url()).pathname;
      const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
      if(endpoint==='/api/auth/session')return json({authenticated:true,user:{id:'fixture-sandbox-user'},accounts:[{id:account,name:'个人账户',kind:'personal',role:'owner'}],active_account_id:account,csrf_token:'fixture-csrf'});
      if(endpoint==='/api/account/notifications/summary'){assert.equal(request.method(),'GET');return json({user_id:'fixture-sandbox-user',unread_count:0});}
      if(endpoint==='/api/billing/catalog')return json(JSON.parse(fs.readFileSync(path.join(__dirname,'../src/pages/billing-catalog.json'),'utf8')));
      if(endpoint===`/api/billing/purchase-quotes/${quote}`)return json({id:quote,account_id:account,amount_minor:731,currency:'CNY',expires_at:'2099-01-01T00:00:00Z',snapshot:{...item,kind:'credits',billing_cycle:'',product_id:product,amount_minor:731,seat_quantity:1},terms,can_order:false,unavailable_reason:'QUOTE_ALREADY_ORDERED',order_id:order});
      if(endpoint===`/api/billing/orders/${order}`){
        reads++;assert.equal(request.method(),'GET');assert.equal(request.headers()['x-billing-account'],account);
        if(capability==='read-failure')return route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"CHANNEL_UNAVAILABLE"}}'});
        const start={environment:capability==='production'?'PRODUCTION':'SANDBOX',channel:'alipay',available:true,order_id:capability==='wrong-order'?product:order,account_id:capability==='wrong-account'?product:account};
        return json({order:{id:order,account_id:account,status:'pending_payment',amount_minor:731,currency:'CNY',paid_at:null,snapshot:{kind:capability==='subscription'?'subscription':'credits',billing_cycle:capability==='subscription'?'monthly':''}},payments:[],delivery:{status:'not_started'},payment_attempt:attempt,refunds:[],...(capability==='disabled'||(attempt&&attempt.status!=='created'&&capability!=='force-cap')?{}:{payment_start:start})});
      }
      if(endpoint===`/api/billing/orders/${order}/pay`){
        posts++;assert.equal(request.method(),'POST');assert.deepEqual(request.postDataJSON(),{});
        assert.equal(request.headers()['x-csrftoken'],'fixture-csrf');assert.equal(request.headers()['x-billing-account'],account);
        attempt={id:product,status:response.startsWith('unknown')?'unknown':response==='lost-created'?'created':'pending',expires_at:'2099-01-01T00:00:00Z',needs_reconciliation:false};
        if(response==='delayed')await new Promise(resolve=>{releasePayment=resolve;});
        if(response==='lost'||response==='lost-created')return route.abort('failed');
        if(response==='error')return route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"CHANNEL_UNAVAILABLE"}}'});
        return json({payment_attempt:attempt,...(['pending','unknown'].includes(response)?{}:{payment_url:paymentURL})});
      }
      return route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"CHANNEL_UNAVAILABLE"}}'});
    });
    const visit=async()=>{await page.goto(`${base}/pages/checkout${language}.html?quote=${quote}&sandbox=1`);await page.locator('#purchase-order-link').waitFor();};
    // Break caught: a server-approved one-time sandbox order has no actual consumer.
    await visit();await page.locator('#purchase-payment-start').waitFor();
    assert.match(await page.locator('#purchase-payment-start').innerText(),language?/sandbox/i:/沙箱/);
    assert.equal(posts,0);
    // Break caught: revoked capability leaves the previous enabled button after a new GET.
    for(const revoked of ['disabled','read-failure']){
      capability='ready';await visit();await page.locator('#purchase-payment-start').waitFor();
      capability=revoked;const beforeRevocation=reads;
      await page.locator('#purchase-payment-refresh').click();
      for(let n=0;reads===beforeRevocation&&n<100;n++)await page.waitForTimeout(10);
      await page.waitForTimeout(50);
      assert.equal(await page.locator('#purchase-payment-start:not([disabled])').count(),0);
      assert.equal(posts,0);checks++;
    }
    // Break caught: a prepared but undispatched created attempt cannot resume through a valid server capability.
    capability='ready';attempt={id:product,status:'created',expires_at:'2099-01-01T00:00:00Z',needs_reconciliation:false};
    await visit();await page.locator('#purchase-payment-start').waitFor();assert.equal(posts,0);checks++;
    const destination=page.waitForURL('https://openapi.alipaydev.com/gateway.do?*');
    await page.locator('#purchase-payment-start').click();await destination;
    assert.deepEqual(navigation,[sandboxURL]);assert.equal(posts,1);checks++;
    await page.goBack({waitUntil:'domcontentloaded'});await page.locator('#purchase-payment-status').waitFor();
    assert.equal(await page.locator('#purchase-payment-start').count(),0);assert.equal(posts,1);
    await page.reload();await page.locator('#purchase-payment-status').waitFor();
    assert.equal(await page.locator('#purchase-payment-start').count(),0);assert.equal(posts,1);checks++;
    // Break caught: production/client flags, mismatched identities or subscriptions expose a payment button.
    for(capability of ['disabled','production','wrong-account','wrong-order','subscription']){
      attempt=null;const previous=reads;await visit();
      await page.waitForFunction(()=>document.querySelector('#purchase-order-link'));
      for(let n=0;reads===previous&&n<100;n++)await page.waitForTimeout(10);
      await page.waitForTimeout(50);
      assert.equal(await page.locator('#purchase-payment-start').count(),0);checks++;
    }
    capability='ready';
    for(const state of ['pending','unknown']){
      capability='force-cap';attempt={id:product,status:state,expires_at:'2099-01-01T00:00:00Z',needs_reconciliation:false};
      await visit();await page.locator('#purchase-payment-status').waitFor();
      assert.equal(await page.locator('#purchase-payment-start').count(),0);checks++;
    }
    capability='ready';
    // Break caught: a URL returned after dispatch redirects outside the official fixed sandbox endpoint.
    for(paymentURL of ['https://openapi.alipay.com/gateway.do','https://openapi.alipaydev.com.evil.example/gateway.do','http://openapi.alipaydev.com/gateway.do','https://user@openapi.alipaydev.com/gateway.do','https://openapi.alipaydev.com:444/gateway.do','https://openapi.alipaydev.com/other','https://openapi.alipaydev.com/gateway.do#fragment','javascript:alert(1)']){
      attempt=null;response='ready';await visit();await page.locator('#purchase-payment-start').click();
      await page.waitForFunction(()=>document.querySelector('#purchase-payment-status')?.textContent.match(/未确认|unconfirmed/i));
      assert.equal(await page.locator('#purchase-payment-start:not([disabled])').count(),0);
      assert.equal(navigation.length,1);checks++;
    }
    paymentURL=sandboxURL;
    // Break caught: an unknown POST unlocks a second POST when GET returns a created attempt with capability.
    attempt=null;response='lost-created';await visit();await page.locator('#purchase-payment-start').click();
    await page.waitForFunction(()=>document.querySelector('#purchase-payment-status')?.textContent.match(/原交易|original payment/i));
    const lockedPosts=posts;await page.locator('#purchase-payment-refresh').click();
    await page.locator('#purchase-payment-status').waitFor();
    assert.equal(await page.locator('#purchase-payment-start').count(),0);assert.equal(posts,lockedPosts);checks++;
    // Break caught: duplicate/unknown/lost responses allow another POST instead of reading the original attempt.
    for(response of ['pending','unknown','unknown-url','lost','error']){
      attempt=null;await visit();const before=posts;
      await page.locator('#purchase-payment-start').evaluate(button=>{button.click();button.click();});
      await page.waitForFunction(()=>document.querySelector('#purchase-payment-status')?.textContent.match(/原交易|original payment/i));
      assert.equal(posts,before+1);
      assert.equal(await page.locator('#purchase-payment-start:not([disabled])').count(),0);
      await page.locator('#purchase-payment-refresh').click();
      await page.waitForFunction(()=>document.querySelector('#purchase-payment-status')?.textContent.match(/原交易|original payment/i));
      assert.equal(posts,before+1);
      await visit();assert.equal(posts,before+1);assert.equal(await page.locator('#purchase-payment-start').count(),0);checks++;
    }
    // Break caught: a late URL navigates after the billing account was cleared.
    attempt=null;response='delayed';await visit();await page.locator('#purchase-payment-start').click();
    for(let n=0;!releasePayment&&n<100;n++)await page.waitForTimeout(10);
    assert.ok(releasePayment);await page.evaluate(()=>window.dispatchEvent(new Event('pagehide')));releasePayment();
    await page.waitForTimeout(100);assert.equal(navigation.length,1);checks++;
    assert.deepEqual(errors,[]);await page.close();
  }
  console.log(JSON.stringify({layer:'Alipay sandbox checkout DOM and intercepted navigation; no real gateway request',checks}));
}
(async()=>{
  const browser = await chromium.launch({channel:'chrome',headless:true});
  const checks = [];
  try {
    if(process.env.SAGE_WALLET_ONLY==='1'){await walletTestAudit(browser);return;}
    if(process.env.SAGE_ALIPAY_ONLY==='1'){await alipaySandboxAudit(browser);return;}
    if(process.env.SAGE_WECHAT_ONLY==='1'){await wechatNativeAudit(browser);return;}
    for (const language of ['', '-en']) {
      const page = await browser.newPage({viewport:{width:320,height:900}});
      let created=false, orders=0, quotes=0, key, wrongAccount=false, expired=false;
      const errors=[]; page.on('pageerror',error=>errors.push(error.message));
      await page.route(/\/billing\.js(?:\?.*)?$/,route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../src/pages/billing.js'),'utf8')}));
      await page.route('**/api/**',async route=>{
        const request=route.request(), path=new URL(request.url()).pathname;
        const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
        const detail=()=>({id:quote,account_id:wrongAccount ? product : account,amount_minor:731,currency:'CNY',expires_at:expired ? '2020-01-01T00:00:00Z' : '2099-01-01T00:00:00Z',snapshot:{...item,product_id:product,seat_quantity:1,terms_document_digest:terms.digest,amount_minor:731,channel:'alipay'},terms,can_order:!created&&!expired,unavailable_reason:created?'QUOTE_ALREADY_ORDERED':expired?'QUOTE_EXPIRED':null,order_id:created?order:null});
        if(path==='/api/auth/session') return json({authenticated:true,user:{id:'1',masked_identifier:'fixture'},accounts:[{id:account,name:'个人账户',kind:'personal',role:'owner'}],active_account_id:account,csrf_token:'fixture-csrf'});
        if(path==='/api/account/notifications/summary'){assert.equal(request.method(),'GET');return json({user_id:'1',unread_count:0});}
        if(path==='/api/billing/catalog') return route.fallback();
        if(path==='/api/billing/purchase-catalog') return json({account_id:account,currency:'CNY',items:[item]});
        if(path==='/api/billing/purchase-quotes') {
          quotes++;
          assert.equal(request.method(),'POST');
          assert.equal(request.headers()['x-csrftoken'],'fixture-csrf');
          assert.equal(request.headers()['x-billing-account'],account);
          assert.deepEqual(request.postDataJSON(),{product_id:product,seat_quantity:1,channel:'alipay',buyer_region:'CN'});
          return json(detail());
        }
        if(path===`/api/billing/purchase-quotes/${quote}`) return json(detail());
        if(path==='/api/billing/orders') {
          orders++; created=true;
          assert.equal(request.headers()['x-billing-account'],account);
          assert.equal(request.headers()['x-csrftoken'],'fixture-csrf');
          assert.deepEqual(request.postDataJSON(),{quote_id:quote,agreement_acceptance:'isolated-1'});
          assert.ok(request.headers()['idempotency-key']);
          key=request.headers()['idempotency-key'];
          return route.abort('failed'); // Order exists, response lost: query instead of new order.
        }
        return route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"UNAVAILABLE"}}'});
      });
      await page.goto(`${base}/pages/checkout${language}.html?product=${product}&seats=1`);
      await page.locator('#purchase-prepare').waitFor();
      assert.equal(quotes,0);
      await page.locator('#purchase-prepare').click();
      await page.locator('#purchase-agree').waitFor();
      assert.equal(await page.locator('.nav-actions > a[lang]').count(),0,'Shared language menu must replace the obsolete direct link');
      await page.locator('.sage-language-toggle').click();
      const translated=await page.locator(`.sage-language-menu a[lang="${language ? 'zh-CN' : 'en'}"]`).getAttribute('href');
      assert.equal(new URL(translated,base).search,`?quote=${quote}`,'Language menu must retain the confirmed quote');
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#purchase-submit').isDisabled(),true);
      assert.equal(await page.evaluate(()=>window.injected),undefined);
      for (const width of [320,390,768,1440]) {
        await page.setViewportSize({width,height:1000});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`${language} ${width}`);
        await page.locator('main *').evaluateAll(nodes=>{ const sizes=nodes.map(el=>parseFloat(getComputedStyle(el).fontSize));nodes.forEach((el,i)=>el.style.fontSize=`${sizes[i]*2}px`); });
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`${language} ${width} 200% text`);
        await page.locator('main *').evaluateAll(nodes=>nodes.forEach(el=>el.style.removeProperty('font-size')));
      }
      await page.locator('#purchase-agree').check();
      assert.equal(await page.locator('#purchase-submit').isDisabled(),false);
      await page.locator('#purchase-submit').click();
      await page.locator('#purchase-recover').waitFor();
      await page.locator('#purchase-recover').click();
      await page.locator('#purchase-order-link').waitFor();
      assert.equal(orders,1); assert.equal(quotes,1); assert.ok(key);
      assert.ok((await page.locator('#purchase-order-link').getAttribute('href')).includes(order));
      await page.reload();
      await page.locator('#purchase-order-link').waitFor();
      assert.equal(orders,1); assert.equal(quotes,1);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
      checks.push(`${language||'zh'}: consent, exact server quote, lost-response recovery, reload and 320px`);
      wrongAccount=true;
      await page.reload();
      await page.locator('#purchase-status').waitFor();
      await page.waitForFunction(()=>document.querySelector('#purchase-status')?.classList.contains('error'));
      assert.equal(await page.locator('#purchase-order-link').count(),0);
      assert.equal(await page.locator('#purchase-submit').count(),0);
      wrongAccount=false; created=false; expired=true;
      await page.reload();
      await page.locator('#purchase-status').waitFor();
      await page.waitForFunction(()=>document.querySelector('#purchase-status')?.textContent.includes('expired')||document.querySelector('#purchase-status')?.textContent.includes('过期'));
      assert.equal(await page.locator('#purchase-submit').count(),0);
      assert.deepEqual(errors,[]);
      await page.close();
    }
    for(const language of ['', '-en']) {
      const page=await browser.newPage({viewport:{width:320,height:900}});
      page.setDefaultTimeout(7000);
      const errors=[]; page.on('pageerror',error=>errors.push(error.message));
      let mode='available', active=account, release;
      const pack={...item,kind:'credits',code:'fixture_pack',billing_cycle:'',unit_amount_minor:'9007199254740993',credits_quantity:'10.000000000000000000000001'};
      await page.route(/\/billing\.js(?:\?.*)?$/,route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../src/pages/billing.js'),'utf8')}));
      await page.route('**/api/**',async route=>{
        const request=route.request(),endpoint=new URL(request.url()).pathname;
        const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
        if(endpoint==='/api/auth/session')return json({authenticated:true,user:{id:'1'},accounts:[{id:account,name:'First',kind:'personal',role:'owner'},{id:order,name:'Second',kind:'personal',role:'owner'}],active_account_id:active,csrf_token:'fixture'});
        if(endpoint==='/api/account/notifications/summary'){assert.equal(request.method(),'GET');return json({user_id:'1',unread_count:0});}
        if(endpoint==='/api/billing/accounts/select'){active=request.postDataJSON().account_id;return json({authenticated:true,user:{id:'1'},accounts:[{id:account,name:'First',kind:'personal',role:'owner'},{id:order,name:'Second',kind:'personal',role:'owner'}],active_account_id:active,csrf_token:'fixture'});}
        assert.equal(request.method(),'GET');
        if(endpoint==='/api/billing/catalog')return route.fallback();
        if(endpoint==='/api/billing/account')return json({account:{id:active,name:'Fixture',kind:'personal'},credits:{available:'0',reserved:'0',frozen:'0',consumed:'0',expired:'0',refunded:'0',batches:[]},orders:[],capabilities:{}});
        if(endpoint==='/api/billing/purchase-catalog'){
          const owner=active, selected=mode;
          if(selected==='delayed'&&owner===account)await new Promise(resolve=>{release=resolve;});
          if(selected==='error')return route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"UNAVAILABLE"}}'});
          return json({account_id:selected==='wrong'?order:owner,currency:'CNY',items:selected==='empty'||owner===order?[]:[selected==='unavailable'?{...pack,quote_available:false,unit_amount_minor:null,credits_quantity:null,purchase_options:[]}:selected==='malformed'?{...pack,unit_amount_minor:'1e3'}:pack]});
        }
        throw new Error(endpoint);
      });
      await page.goto(`${base}/pages/billing${language}.html#credits`);
      await page.locator('[data-credit-purchase]').waitFor();
      assert.ok((await page.locator('#credit-packs').innerText()).includes('90,071,992,547,409.93'));
      assert.ok((await page.locator('#credit-packs').innerText()).includes(pack.credits_quantity));
      assert.ok((await page.locator('[data-credit-purchase]').getAttribute('href')).includes(`product=${product}`));
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
      await page.locator('[data-credit-purchase]').click();
      await page.locator('#purchase-prepare').waitFor();
      assert.equal(await page.locator('.checkout-heading h1').innerText(),language?'Purchase Sage Credits':'购买 Sage Credits');
      assert.equal(await page.locator('.selected-plan').innerText(),'Sage Credits');
      assert.ok((await page.locator('#purchase-summary').innerText()).includes(language?'12 months':'12 个月'));
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
      await page.goto(`${base}/pages/billing${language}.html#credits`);
      for(mode of ['empty','unavailable','error','wrong','malformed']){
        await page.reload();
        const unavailable=['empty','unavailable'].includes(mode);
        await page.locator(unavailable?'[data-credit-packs-empty]':'[data-credit-packs-retry]').waitFor().catch(error=>{throw new Error(`${language||'zh'} ${mode}: ${error.message}`);});
        assert.equal(await page.locator('[data-credit-purchase]').count(),0);
        if(!unavailable)assert.equal(await page.locator('[data-credit-packs-empty]').count(),0);
      }
      mode='delayed'; await page.reload();
      await page.waitForFunction(()=>document.querySelector('#billing-account-select'));
      while(!release)await new Promise(resolve=>setTimeout(resolve,10));
      await page.locator('.nav-account summary').click();
      await page.locator('#billing-account-select').selectOption(order);
      await page.locator('[data-credit-packs-empty]').waitFor();
      release(); await page.waitForTimeout(100);
      assert.equal(await page.locator('[data-credit-purchase]').count(),0);
      assert.deepEqual(errors,[]);
      checks.push(`${language||'zh'}: exact Credits packs, empty/error isolation and delayed account response`);
      await page.close();
    }
    for(const language of ['', '-en']) {
      const page=await browser.newPage({viewport:{width:320,height:900}});page.setDefaultTimeout(7000);
      const errors=[];page.on('pageerror',error=>errors.push(error.message));
      let mode='active',active=account,release,business=false;
      const subscriptions=()=> mode==='empty'||mode==='pending'||active===order?[]:[{id:quote,plan_code:'pro',billing_cycle:'monthly',status:mode==='scheduled'?'scheduled':mode.startsWith('expired')?'expired':'active',period_start:mode==='scheduled'?'2098-01-01T00:00:00Z':'2020-01-01T00:00:00Z',period_end:mode.startsWith('expired')?'2020-02-01T00:00:00Z':'2099-01-01T00:00:00Z',prepaid_through:mode.startsWith('expired')?'2020-02-01T00:00:00Z':'2099-01-01T00:00:00Z',next_credit_grant_at:null,auto_renew:mode==='expired-renewing'||mode==='active',cancellation_state:mode==='cancelling'?'processing':'none',can_cancel:mode==='active',capabilities:{cancel:true,upgrade:false,payment_method:false}}];
      await page.route(/\/billing\.js(?:\?.*)?$/,route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../src/pages/billing.js'),'utf8')}));
      await page.route('**/api/**',async route=>{
        const request=route.request(),endpoint=new URL(request.url()).pathname;
        const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
        const session=()=>({authenticated:true,user:{id:'1'},accounts:[{id:account,name:'First',kind:business?'business':'personal',role:'owner'},{id:order,name:'Second',kind:'personal',role:'owner'}],active_account_id:active,csrf_token:'fixture'});
        if(endpoint==='/api/auth/session')return json(session());
        if(endpoint==='/api/account/notifications/summary'){assert.equal(request.method(),'GET');return json({user_id:session().user.id,unread_count:0});}
        if(endpoint==='/api/billing/accounts/select'){active=request.postDataJSON().account_id;return json(session());}
        assert.equal(request.method(),'GET');
        if(endpoint==='/api/billing/catalog')return route.fallback();
        if(endpoint==='/api/billing/purchase-catalog')return json({account_id:active,currency:'CNY',items:[{...item,...(business?{code:'teams',audience:'business'}:{}),quote_available:mode!=='pending'}]});
        if(endpoint==='/api/billing/subscriptions'){
          const data=subscriptions().map(value=>({...value,plan_code:business?'teams':value.plan_code}));
          if(mode==='delayed'&&active===account)await new Promise(resolve=>{release=resolve;});
          if(mode==='error')return route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"UNAVAILABLE"}}'});
          return json({items:mode==='invalid'?[{...data[0],prepaid_through:'not-a-date'}]:data});
        }
        throw new Error(endpoint);
      });
      for(mode of ['active','scheduled','cancelling','expired-renewing','expired','empty','pending','error','invalid']) {
        await page.goto(`${base}/pages/pricing${language}.html?audit=${mode}`);
        await page.locator(mode==='error'||mode==='invalid'?'#pricing-subscription-retry':'#pricing-subscription-status[data-ready="true"]').waitFor();
        const proCard = page.locator('.plan').filter({has:page.locator('[data-plan-link="pro"]')});
        if (mode !== 'pending') {
          assert.equal(await proCard.locator('.plan-pending').count(), 0, 'Approved benefits replace all pending reference details');
          assert.equal(await proCard.locator('.plan-includes').count(), 0, 'Reference benefits must not override the approved product');
          assert.ok((await proCard.locator('.plan-benefits').innerText()).includes(language ? 'Isolated benefits, not for sale.' : '隔离测试权益，不销售。'));
          assert.ok((await proCard.locator('.plan-benefits').innerText()).includes('100 Sage Credits'));
        } else assert.equal(await proCard.locator('.plan-pending dd').count(), 3);
        assert.equal(await page.locator('.plan').filter({has:page.locator('[data-plan-link="pro_plus"]')}).locator('.plan-pending dd').count(), 3);
        const blocked=['active','scheduled','cancelling','expired-renewing','error','invalid'].includes(mode);
        for(const link of await page.locator('[data-plan-link]').all()){
          const href=await link.getAttribute('href');
          if(blocked)assert.ok(href.includes('billing')&&href.includes('#subscription'),`${language} ${mode} ${href}`);
        }
        if(mode==='active'||mode==='scheduled'||mode==='cancelling')assert.equal(await page.locator('[data-plan-link="pro"]').innerText(),language?'Current plan':'当前方案');
        if(mode==='pending'){
          assert.equal(await page.locator('[data-plan-link="pro"]').innerText(),language?'Review plan':'查看方案');
          assert.ok((await page.locator('#pricing-subscription-status').innerText()).includes(language?'order':'订单'));
        }
        if(mode==='empty'||mode==='expired')assert.ok((await page.locator('[data-plan-link="pro"]').getAttribute('href')).includes(`product=${product}`));
        await page.locator('[data-cycle="annual"]').click();
        assert.equal(await page.locator('[data-cycle="annual"]').getAttribute('aria-pressed'),'true');
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
        await page.locator('main *').evaluateAll(nodes=>{const sizes=nodes.map(el=>parseFloat(getComputedStyle(el).fontSize));nodes.forEach((el,i)=>el.style.fontSize=`${sizes[i]*2}px`);});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`${language} ${mode} 200% ${JSON.stringify(await page.locator('main *').evaluateAll(nodes=>nodes.filter(el=>el.getBoundingClientRect().right>innerWidth+1).map(el=>[el.className,el.textContent.slice(0,50),el.getBoundingClientRect().right])))}`);
      }
      mode='active';business=true;
      await page.goto(`${base}/pages/pricing${language}.html?audience=business&audit=seat-change`);
      await page.locator('#pricing-subscription-status[data-ready="true"]').waitFor();
      assert.equal(await page.locator('[data-plan-link="teams"]').innerText(),language?'Current plan':'当前方案');
      const teamsCard=page.locator('.plan').filter({has:page.locator('[data-plan-link="teams"]')});
      assert.equal(await teamsCard.locator('.business-benefits,.plan-pending').count(),0,'Approved business product replaces the entire reference block');
      assert.ok((await teamsCard.locator('.plan-benefits').innerText()).includes(language?'Isolated benefits, not for sale.':'隔离测试权益，不销售。'));
      assert.equal(await page.locator('.plan').filter({has:page.locator('[data-plan-link="enterprise"]')}).locator('.plan-pending dd').count(),3,'Unapproved Enterprise stays pending');
      await page.locator('#pricing-seats').fill('3');
      for(const link of await page.locator('[data-plan-link]').all())assert.ok((await link.getAttribute('href')).includes('billing')&&(await link.getAttribute('href')).includes('#subscription'));
      business=false;
      mode='delayed';await page.goto(`${base}/pages/pricing${language}.html?audit=delayed`);
      for(let i=0;!release&&i<100;i++)await page.waitForTimeout(10);
      assert.ok(release,'Delayed subscription read was requested');
      await page.locator('[data-cycle="annual"]').click();
      await page.locator('.nav-account summary').click();await page.locator('#billing-account-select').selectOption(order);
      await page.locator('#pricing-subscription-status[data-ready="true"]').waitFor();
      release();await page.waitForTimeout(100);
      assert.equal(await page.locator('[data-plan-link="pro"]').innerText(),language?'Review plan':'查看方案');
      assert.equal(await page.locator('[data-cycle="annual"]').getAttribute('aria-pressed'),'true');
      assert.deepEqual(errors,[]);
      checks.push(`${language||'zh'}: current subscriptions, paid/scheduled/cancelled/expired, read failure and account/cycle isolation`);
      await page.close();
    }
    await walletTestAudit(browser);
    await alipaySandboxAudit(browser);
    await wechatNativeAudit(browser);
    console.log(JSON.stringify({layer:'Isolated browser response fixtures, no actual sales',checks,pageErrors:0}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
