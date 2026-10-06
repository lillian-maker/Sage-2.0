// Read-only browser response fixtures; no account or financial write.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SAGE_URL || 'http://127.0.0.1:8914';
assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname));
const accountId = '11111111-1111-4111-8111-111111111111';
const otherAccountId = '22222222-2222-4222-8222-222222222222';
const uuid = '33333333-3333-4333-8333-333333333333';
const kinds = ['grant','reserve','settle','release','freeze','unfreeze','refund','unknown'];
const exact = '9007199254740993.000000000000000000000000000001';
const history = Array.from({length:51},(_,i)=>({id:String(i+1),kind:kinds[i%8],
  quantity:i%8===7?null:exact,batch_id:uuid,source:'purchased',order_id:uuid,
  reservation_id:uuid,seat_id:null,occurred_at:'2026-10-03T00:00:00Z'}));
(async()=>{
  const browser = await chromium.launch({channel:'chrome'});
  try {
    const page = await browser.newPage(); page.setDefaultTimeout(7000);
    let mode='normal', release=null, sessionAccount=accountId; const errors=[],requests=[],checks=[];
    page.on('pageerror',err=>errors.push(err.message));
    await page.route('**/billing.js',route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../src/pages/billing.js'),'utf8')}));
    await page.route('**/billing.css',route=>route.fulfill({contentType:'text/css',body:fs.readFileSync(path.join(__dirname,'../src/pages/billing.css'),'utf8')}));
    await page.route('**/api/**',async route=>{
      const req=route.request(),url=new URL(req.url()); requests.push([url.pathname,req.method()]);
      assert.equal(req.method(),'GET');
      const json=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
      if(url.pathname==='/api/billing/catalog')return json(JSON.parse(fs.readFileSync(path.join(__dirname,'../src/pages/billing-catalog.json'),'utf8')));
      if(url.pathname==='/api/auth/session')return json({authenticated:true,user:{id:'fixture',display_name:'Fixture'},accounts:[{id:accountId,name:'Fixture',kind:'personal',role:'owner'},{id:otherAccountId,name:'Other fixture',kind:'personal',role:'owner'}],active_account_id:sessionAccount,csrf_token:'fixture'});
      if(url.pathname==='/api/billing/credits/transactions') {
        const offset=Number(url.searchParams.get('offset'));
        const responseAccount=sessionAccount;
        if(mode==='delay' || (mode==='account-delay' && responseAccount===accountId))await new Promise(resolve=>{release=resolve;});
        if(mode==='failure')return route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"UNAVAILABLE"}}'});
        const items=mode==='empty'||responseAccount===otherAccountId?[]:history.slice(offset,offset+50);
        return json({account_id:mode==='wrong'?otherAccountId:responseAccount,
          offset,total:mode==='empty'||responseAccount===otherAccountId?0:51,next_offset:items.length===50?50:null,
          items:mode==='malformed'?[{...history[0],quantity:9007199254740993}]:mode==='xss'?[{...history[0],kind:'<img src=x onerror=alert(1)>'}]:items});
      }
      if(url.pathname==='/api/billing/account')return json({account:{id:accountId,kind:'personal',role:'owner'},credits:{available:'1'},orders:[],capabilities:{}});
      if(url.pathname==='/api/billing/subscriptions')return json({items:[]});
      return route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"UNAVAILABLE"}}'});
    });
    for(const lang of ['', '-en']) {
      mode='normal'; sessionAccount=accountId;
      await page.goto(`${base}/pages/billing${lang}.html#credits-ledger`);
      await page.locator('[data-credit-history-next]').waitFor();
      assert.equal(await page.locator('[data-credit-entry]').count(),50);
      assert.ok((await page.locator('#account-live').innerText()).includes(exact));
      assert.ok((await page.locator('[data-credit-kind="grant"] a').first().getAttribute('href')).includes(`order=${uuid}#orders`));
      assert.match(await page.locator('[data-credit-kind="reserve"]').first().innerText(),lang?/not consumption/:/不代表已消耗/);
      assert.equal(await page.locator('[data-credit-kind="unknown"] a').count(),0);
      assert.equal(await page.locator('[data-credit-kind="unknown"] [data-credit-quantity]').count(),0);
      for(const width of [320,390,768,1440]) {
        await page.setViewportSize({width,height:1100});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`${lang} ${width}`);
        await page.locator('main *').evaluateAll(nodes=>{const sizes=nodes.map(el=>parseFloat(getComputedStyle(el).fontSize));nodes.forEach((el,i)=>el.style.fontSize=`${sizes[i]*2}px`);});
        fs.mkdirSync(path.join(__dirname,'../tmp/credit-history'),{recursive:true});
        await page.screenshot({path:path.join(__dirname,`../tmp/credit-history/${lang||'zh'}-${width}-200.png`)});
        if(width===320)await page.locator('[data-credit-entry]').first().screenshot({path:path.join(__dirname,`../tmp/credit-history/${lang||'zh'}-320-entry-200.png`)});
        const overflow = await page.evaluate(()=>Array.from(document.querySelectorAll('body *')).filter(el=>el.getBoundingClientRect().right>innerWidth+1).slice(0,8).map(el=>({tag:el.tagName,class:el.className,text:el.textContent.slice(0,80),right:el.getBoundingClientRect().right})));
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`${lang} ${width} 200% text ${JSON.stringify(overflow)}`);
        await page.locator('main *').evaluateAll(nodes=>nodes.forEach(el=>el.style.removeProperty('font-size')));
      }
      await page.locator('[data-credit-history-next]').click();
      await page.waitForFunction(()=>document.querySelectorAll('[data-credit-entry]').length===1);
      assert.equal(await page.locator('[data-credit-entry]').getAttribute('data-credit-entry'),'51');
      await page.locator('[data-credit-history-prev]').click();
      await page.locator('[data-credit-history-next]').waitFor();
      assert.equal(await page.locator('#credit-history-title').evaluate(el=>el===document.activeElement),true);
      for(mode of ['failure','wrong','malformed','xss']) {
        await page.reload(); await page.locator('[data-credit-history-retry]').waitFor();
        assert.equal(await page.locator('[data-credit-entry]').count(),0);
        assert.equal(await page.locator('#account-live img').count(),0);
        assert.equal(await page.locator('[data-credit-history-next]').count(),0);
      }
      mode='normal'; await page.locator('[data-credit-history-retry]').click();
      await page.locator('[data-credit-history-next]').waitFor();
      mode='delay'; release=null; await page.locator('[data-credit-history-next]').click();
      await page.waitForFunction(()=>document.querySelector('#account-live')?.textContent.includes(document.documentElement.lang==='en'?'Loading Credits':'正在读取额度'));
      while(!release)await new Promise(resolve=>setTimeout(resolve,10));
      await page.locator('.account-menu a[href="#invoices"]').click();
      release(); await page.waitForTimeout(100);
      assert.equal(await page.locator('[data-credit-entry]').count(),0);
      mode='empty'; await page.locator('.account-menu a[href="#credits-ledger"]').click();
      await page.waitForFunction(()=>document.querySelector('#account-live')?.textContent.includes(document.documentElement.lang==='en'?'No Credits movements':'暂无额度流水'));
      mode='normal'; await page.locator('[data-credit-history-refresh]').click();
      await page.locator('[data-credit-history-next]').waitFor();
      mode='account-delay'; release=null; await page.locator('[data-credit-history-refresh]').click();
      while(!release)await new Promise(resolve=>setTimeout(resolve,10));
      sessionAccount=otherAccountId;
      await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));
      await page.waitForFunction(()=>document.querySelector('#account-live')?.textContent.includes(document.documentElement.lang==='en'?'No Credits movements':'暂无额度流水'));
      assert.equal(await page.locator('#billing-account-select').inputValue(),otherAccountId);
      release(); await page.waitForTimeout(100);
      assert.equal(await page.locator('[data-credit-entry]').count(),0);
      checks.push(`${lang||'zh'}: precise quantity, action semantics, pagination, retry, stale response, empty, 4 viewports and 200% text`);
    }
    assert.deepEqual(errors,[]); assert.ok(requests.length>0); assert.ok(requests.every(([,method])=>method==='GET'));
    console.log(JSON.stringify({checks,requests:requests.length,writes:0,pageErrors:errors.length}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
