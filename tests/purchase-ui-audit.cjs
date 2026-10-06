const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SAGE_URL || 'http://127.0.0.1:8914';
const account = '11111111-1111-4111-8111-111111111111';
const product = '22222222-2222-4222-8222-222222222222';
const quote = '33333333-3333-4333-8333-333333333333';
const order = '44444444-4444-4444-8444-444444444444';
const terms = {version:'isolated-1',digest:'a'.repeat(64),zh:{title:'测试条款',paragraphs:['仅用于测试。<script>window.injected=true</script>']},en:{title:'Test terms',paragraphs:['Isolated fixture only. <script>window.injected=true</script>']}};
const item = {id:product,code:'pro',version:1,kind:'subscription',audience:'personal',billing_cycle:'monthly',currency:'CNY',unit_amount_minor:'731',minimum_seats:1,credits_quantity:'100',entitlements:{benefits:{zh:['隔离测试权益，不销售。'],en:['Isolated benefits, not for sale.']}},terms_version:'isolated-1',quote_available:true,unavailable_reason:null,purchase_options:[{channel:'alipay',buyer_region:'CN'}],terms};
(async()=>{
  const browser = await chromium.launch({channel:'chrome',headless:true});
  const checks = [];
  try {
    for (const language of ['', '-en']) {
      const page = await browser.newPage({viewport:{width:320,height:900}});
      let created=false, orders=0, quotes=0, key, wrongAccount=false, expired=false;
      const errors=[]; page.on('pageerror',error=>errors.push(error.message));
      await page.route('**/billing.js',route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../src/pages/billing.js'),'utf8')}));
      await page.route('**/api/**',async route=>{
        const request=route.request(), path=new URL(request.url()).pathname;
        const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
        const detail=()=>({id:quote,account_id:wrongAccount ? product : account,amount_minor:731,currency:'CNY',expires_at:expired ? '2020-01-01T00:00:00Z' : '2099-01-01T00:00:00Z',snapshot:{...item,product_id:product,seat_quantity:1,terms_document_digest:terms.digest,amount_minor:731,channel:'alipay'},terms,can_order:!created&&!expired,unavailable_reason:created?'QUOTE_ALREADY_ORDERED':expired?'QUOTE_EXPIRED':null,order_id:created?order:null});
        if(path==='/api/auth/session') return json({authenticated:true,user:{id:'1',masked_identifier:'fixture'},accounts:[{id:account,name:'个人账户',kind:'personal',role:'owner'}],active_account_id:account,csrf_token:'fixture-csrf'});
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
      await page.route('**/billing.js',route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../src/pages/billing.js'),'utf8')}));
      await page.route('**/api/**',async route=>{
        const request=route.request(),endpoint=new URL(request.url()).pathname;
        const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
        if(endpoint==='/api/auth/session')return json({authenticated:true,user:{id:'1'},accounts:[{id:account,name:'First',kind:'personal',role:'owner'},{id:order,name:'Second',kind:'personal',role:'owner'}],active_account_id:active,csrf_token:'fixture'});
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
      await page.route('**/billing.js',route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../src/pages/billing.js'),'utf8')}));
      await page.route('**/api/**',async route=>{
        const request=route.request(),endpoint=new URL(request.url()).pathname;
        const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
        const session=()=>({authenticated:true,user:{id:'1'},accounts:[{id:account,name:'First',kind:business?'business':'personal',role:'owner'},{id:order,name:'Second',kind:'personal',role:'owner'}],active_account_id:active,csrf_token:'fixture'});
        if(endpoint==='/api/auth/session')return json(session());
        if(endpoint==='/api/billing/accounts/select'){active=request.postDataJSON().account_id;return json(session());}
        assert.equal(request.method(),'GET');
        if(endpoint==='/api/billing/catalog')return route.fallback();
        if(endpoint==='/api/billing/purchase-catalog')return json({account_id:active,currency:'CNY',items:[{...item,quote_available:mode!=='pending'}]});
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
    console.log(JSON.stringify({layer:'Isolated browser response fixtures, no actual sales',checks,pageErrors:0}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
