// Read-only browser response fixtures; no account or financial write.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SAGE_URL || 'http://127.0.0.1:8914';
assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname));
const accountId='11111111-1111-4111-8111-111111111111', otherId='22222222-2222-4222-8222-222222222222';
const exact='9007199254740993.125';
const history=Array.from({length:51},(_,i)=>i%2 ? {id:`seat:${i+1}`,type:'seat_assignment',created_at:'2026-10-03T00:00:00Z',
  actor_masked_identifier:'138****0000',seat_id:'33333333-3333-4333-8333-333333333333',seat_position:1,
  before_membership_id:1,after_membership_id:2,before_version:i,version:i+1} :
  {id:`limit:${i+1}`,type:'shared_limit',created_at:'2026-10-03T00:00:00Z',actor_masked_identifier:i===4?'':'f***@example.test',
   membership_id:2,quantity:i===2?'0':i===4?null:exact,previous_known:i!==4,previous_quantity:i===2?exact:null,version:i+1});
history[7]={id:'removal:1',type:'member_removal',created_at:'2026-10-09T00:00:00Z',actor_masked_identifier:'f***@example.test',membership_id:2,handoff_pending:true,pending_task_count:1,executor_connected:false};
(async()=>{
  const browser=await chromium.launch({channel:'chrome'});
  try {
    const page=await browser.newPage(); page.setDefaultTimeout(7000);
    let mode='normal',release=null,active=accountId,kind='business',role='owner',removed=false,removeFail=true,heldRemoval=null,allowOtherRemoval=false;
    const removalWrites=[];
    const errors=[],requests=[],checks=[];
    page.on('pageerror',error=>errors.push(error.message));
    const sourceAssetHits = {'billing.js':0,'billing.css':0};
    for (const name of Object.keys(sourceAssetHits)) await page.route(new RegExp(`/${name.replace('.', '\\.')}(?:\\?.*)?$`),route=>{
      sourceAssetHits[name]++;
      return route.fulfill({contentType:name.endsWith('.js')?'text/javascript':'text/css',body:fs.readFileSync(path.join(__dirname,'../src/pages',name),'utf8')});
    });
    await page.route('**/api/**',async route=>{
      const req=route.request(),url=new URL(req.url()); requests.push([url.pathname,req.method()]);
      const json=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
      const sessionData=()=>({authenticated:true,user:{id:'fixture',display_name:'Fixture'},accounts:[accountId,otherId].map(id=>({id,name:'Fixture business',kind,role})),active_account_id:active,csrf_token:'fixture'});
      if(url.pathname==='/api/billing/accounts/select'){
        assert.equal(req.method(),'POST');assert.equal(req.headers()['x-csrftoken'],'fixture');
        const selected=req.postDataJSON().account_id;assert.ok([accountId,otherId].includes(selected));active=selected;return json(sessionData());
      }
      if(url.pathname==='/api/billing/organization/members/remove'){
        assert.equal(req.method(),'POST');assert.equal(req.headers()['x-billing-account'],accountId);assert.equal(req.headers()['x-csrftoken'],'fixture');const body=req.postDataJSON();assert.equal(body.membership_id,2);assert.equal(body.confirm,true);assert.match(body.request_key,/^[0-9a-f-]{36}$/);removalWrites.push(body);
        if(heldRemoval){const held=heldRemoval;heldRemoval=null;held.started();await held.promise;if(held.status!==200)return route.fulfill({status:held.status,contentType:'application/json',body:'{"error":{"code":"UNAVAILABLE"}}'});}
        if(removeFail){removeFail=false;return route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"UNAVAILABLE"}}'});}removed=true;return json({removed:true,membership_id:2,removal_id:'1',handoff_pending:true,executor_connected:false});
      }
      assert.equal(req.method(),'GET');
      const account={id:active,name:'Fixture business',kind,role};
      if(url.pathname==='/api/billing/catalog')return json(JSON.parse(fs.readFileSync(path.join(__dirname,'../src/pages/billing-catalog.json'),'utf8')));
      if(url.pathname==='/api/auth/session')return json(sessionData());
      if(url.pathname==='/api/billing/organization-history') {
        const capturedAccount=req.headers()['x-billing-account'], capturedMode=mode;
        assert.ok([accountId,otherId].includes(capturedAccount));
        const offset=Number(url.searchParams.get('offset'));
        if(capturedMode==='delay')await new Promise(resolve=>{release=resolve;});
        if(capturedMode==='failure'||role==='member')return route.fulfill({status:role==='member'?403:503,contentType:'application/json',body:JSON.stringify({error:{code:role==='member'?'FORBIDDEN':'UNAVAILABLE'}})});
        const items=capturedMode==='empty'||capturedAccount===otherId?[]:history.slice(offset,offset+50);
        return json({account_id:capturedMode==='wrong'?otherId:capturedAccount,offset,total:items.length?51:0,next_offset:items.length===50?50:null,
          items:capturedMode==='malformed'?[{...history[0],quantity:9007199254740993}]:capturedMode==='xss'?[{...history[0],type:'<img src=x onerror=alert(1)>'}]:items});
      }
      if(url.pathname==='/api/billing/account')return json({account,credits:{available:'0',reserved:'0',frozen:'0',batches:[]},orders:[],capabilities:{}});
      if(url.pathname==='/api/billing/organization')return json({account_id:active,members:[{id:1,masked_identifier:'o***@example.test',role:'owner',active:true,seat_position:1,shared_limit:null,shared_used:'0',shared_reserved:'0',can_remove:false},{id:2,masked_identifier:'m***@example.test',role:'member',active:!removed||active===otherId,seat_position:2,shared_limit:null,shared_used:'0',shared_reserved:'0',can_remove:role==='owner'&&!removed&&(active===accountId||allowOtherRemoval)}],total_members:2,seat_count:2,assigned_seats:2,subscription_active:false,period_start:null,period_end:null,next_offset:null,capabilities:{member_removal:role==='owner'}});
      return route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"UNAVAILABLE"}}'});
    });
    for(const lang of ['', '-en']) {
      mode='normal';active=accountId;kind='business';role='owner';
      await page.goto(`${base}/pages/billing${lang}.html#organization-history`);
      await page.locator('[data-organization-history-next]').waitFor();
      assert.equal(await page.locator('[data-organization-change]').count(),50);
      assert.ok((await page.locator('#account-live').innerText()).includes(exact));
      assert.match(await page.locator('[data-organization-change="limit:3"]').innerText(),lang?/Shared pool disabled/:/禁止使用共享池/);
      assert.match(await page.locator('[data-organization-change="limit:5"]').innerText(),lang?/Unknown/:/未知/);
      assert.match(await page.locator('[data-organization-change="seat:2"]').innerText(),lang?/Version 1 → 2/:/版本 1 → 2/);
      assert.match(await page.locator('[data-organization-change="removal:1"]').innerText(),lang?/Work handoff pending/:/工作交接待处理/);
      assert.match(await page.locator('#account-live').innerText(),/f\*\*\*@example.test/);
      for(const width of [320,390,768,1440]) {
        await page.setViewportSize({width,height:1100});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`${lang} ${width}`);
        await page.locator('main *').evaluateAll(nodes=>{const sizes=nodes.map(el=>parseFloat(getComputedStyle(el).fontSize));nodes.forEach((el,i)=>el.style.fontSize=`${sizes[i]*2}px`);});
        const overflow=await page.evaluate(()=>Array.from(document.querySelectorAll('body *')).filter(el=>!el.closest('.account-menu')&&el.getBoundingClientRect().right>innerWidth+1).slice(0,6).map(el=>({tag:el.tagName,class:el.className,text:el.textContent.slice(0,60),right:el.getBoundingClientRect().right})));
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`${lang} ${width} 200% text ${JSON.stringify(overflow)}`);
        fs.mkdirSync(path.join(__dirname,'../tmp/organization-history'),{recursive:true});
        await page.locator('#organization-history-title').scrollIntoViewIfNeeded();
        await page.screenshot({path:path.join(__dirname,`../tmp/organization-history/${lang||'zh'}-${width}-200.png`)});
        await page.locator('main *').evaluateAll(nodes=>nodes.forEach(el=>el.style.removeProperty('font-size')));
      }
      await page.locator('[data-organization-history-next]').focus();await page.keyboard.press('Enter');
      await page.waitForFunction(()=>document.querySelectorAll('[data-organization-change]').length===1);
      assert.equal(await page.locator('[data-organization-change]').getAttribute('data-organization-change'),'limit:51');
      assert.equal(await page.locator('#organization-history-title').evaluate(el=>el===document.activeElement),true);
      await page.locator('[data-organization-history-prev]').click();await page.locator('[data-organization-history-next]').waitFor();
      for(mode of ['failure','wrong','malformed','xss']) {
        await page.reload();await page.locator('[data-organization-history-retry]').waitFor();
        assert.equal(await page.locator('[data-organization-change]').count(),0);
        assert.equal(await page.locator('#account-live img').count(),0);
        assert.match(await page.locator('#account-live').innerText(),lang?/unknown/:/未知/);
      }
      mode='normal';await page.locator('[data-organization-history-retry]').click();await page.locator('[data-organization-history-next]').waitFor();
      mode='delay';release=null;await page.locator('[data-organization-history-next]').click();
      while(!release)await new Promise(resolve=>setTimeout(resolve,10));
      active=otherId;mode='normal';
      await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));
      await page.waitForFunction(()=>document.querySelector('#account-live')?.textContent.includes(document.documentElement.lang==='en'?'No business changes':'暂无企业变更记录'));
      release();await page.waitForTimeout(100);
      assert.equal(await page.locator('[data-organization-change]').count(),0);
      active=accountId;role='member';await page.reload();await page.locator('[data-organization-history-retry]').waitFor();
      assert.equal(await page.locator('[data-organization-change]').count(),0);
      assert.match(await page.locator('#account-live').innerText(),lang?/permission/:/权限/);
      role='owner';kind='personal';const personalRequests=requests.length;await page.reload();
      await page.locator('#billing-plan-summary').waitFor();
      assert.equal(await page.locator('#account-section-title').innerText(),lang?'My bills':'我的账单');
      assert.equal(await page.locator('[data-organization-change]').count(),0);
      assert.equal(requests.slice(personalRequests).some(([endpoint])=>endpoint==='/api/billing/organization-history'),false,'Personal fallback must not request business history');
      assert.equal(await page.locator('.account-menu a[href="#organization-history"]').count(),0);
      checks.push(`${lang||'zh'}: exact quantities, versions and masks; pagination and keyboard focus; failures; account race; member denied; personal hidden; 4 widths at 100/200% text`);
    }
    assert.deepEqual(errors,[]);assert.ok(requests.length>0);assert.ok(requests.every(([,method])=>method==='GET'));
    for(const lang of ['', '-en']){
      kind='business';role='owner';active=accountId;removed=false;removeFail=true;mode='normal';const labels={remove:lang?'Remove member':'移除成员',confirm:lang?'Confirm removal':'确认移除'};
      await page.goto(`${base}/pages/billing${lang}.html#organization`);await page.getByRole('button',{name:labels.remove,exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:labels.remove,exact:true}).count(),1);
      await page.getByRole('button',{name:labels.remove,exact:true}).click();assert.equal(await page.locator('dialog input[type=checkbox]').isChecked(),false);assert.equal(await page.getByRole('button',{name:labels.confirm,exact:true}).isDisabled(),true);
      for(const width of [320,768,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);assert.equal(await page.locator('dialog').evaluate(node=>node.scrollWidth<=node.clientWidth+1),true);}
      await page.keyboard.press('Escape');assert.equal(await page.locator('dialog').count(),0);assert.equal(await page.getByRole('button',{name:labels.remove,exact:true}).evaluate(node=>node===document.activeElement),true);
      await page.getByRole('button',{name:labels.remove,exact:true}).click();await page.locator('dialog input[type=checkbox]').check();await page.getByRole('button',{name:labels.confirm,exact:true}).click();await page.locator('[data-remove-feedback]').filter({hasText:lang?'Removal could not':'移除结果未能确认'}).waitFor();await page.getByRole('button',{name:labels.confirm,exact:true}).click();await page.waitForFunction(()=>!document.querySelector('dialog'));
      assert.equal(removalWrites.at(-1).request_key,removalWrites.at(-2).request_key);assert.equal(await page.getByRole('button',{name:labels.remove,exact:true}).count(),0);
      await page.locator('.account-menu a[href="#organization-history"]').click();await page.locator('[data-organization-change="removal:1"]').waitFor();
      active=otherId;await page.goto(`${base}/pages/billing${lang}.html#organization`);await page.reload();await page.locator('.organization-members').waitFor();assert.match(await page.locator('.organization-members').innerText(),lang?/Active member/:/有效成员/);
      active=accountId;role='billing_admin';removed=false;await page.reload();await page.locator('.organization-members').waitFor();assert.equal(await page.getByRole('button',{name:labels.remove,exact:true}).count(),0);
      checks.push(`${lang||'zh'}: owner-only removal, unchecked consent, csrf/account headers, same retry UUID, history and other-account isolation, Escape/focus and 3 responsive widths`);
      for(const status of [200,503]){
        active=accountId;role='owner';removed=false;removeFail=false;allowOtherRemoval=true;
        await page.goto(`${base}/pages/billing${lang}.html#organization`);await page.reload();await page.getByRole('button',{name:labels.remove,exact:true}).waitFor();
        let started,releaseRemoval;const waiting=new Promise(resolve=>{started=resolve;}),promise=new Promise(resolve=>{releaseRemoval=resolve;});heldRemoval={started,promise,status};
        const beforeWrites=removalWrites.length;
        await page.getByRole('button',{name:labels.remove,exact:true}).click();await page.locator('dialog input[type=checkbox]').check();await page.getByRole('button',{name:labels.confirm,exact:true}).click();await waiting;
        await page.locator('#billing-account-select').selectOption(otherId);await page.getByRole('button',{name:labels.remove,exact:true}).waitFor();
        assert.equal(await page.locator('dialog').count(),0,'Switching accounts closes the pending old-member dialog');
        await page.getByRole('button',{name:labels.remove,exact:true}).click();
        const oldResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/billing/organization/members/remove');releaseRemoval();await (await oldResponse).finished();
        await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
        assert.equal(await page.locator('dialog[open]').count(),1,'A late old-account removal result must not close the new-account dialog');
        assert.equal(await page.evaluate(()=>document.body.style.overflow),'hidden','The new dialog must retain scroll locking after the late result');
        assert.equal(await page.locator('dialog input[type=checkbox]').isChecked(),false,'Old confirmation must not transfer to the new member');
        await page.locator('#billing-account-select').selectOption(accountId);await page.locator('.organization-members').waitFor();
        assert.equal(await page.locator('dialog').count(),0,'The new dialog cleanup must survive and run on the next account switch');
        assert.equal(await page.evaluate(()=>document.body.style.overflow),'','Closing the current dialog restores scrolling');
        assert.equal(removalWrites.length,beforeWrites+1,'Account switches and late responses never submit a new member removal');
      }
      allowOtherRemoval=false;
      checks.push(`${lang||'zh'}: late successful/failed removal responses preserve the new account dialog, cleanup ownership and scroll lock; another switch closes it without a removal write`);
    }
    assert.deepEqual(errors,[]);for(const name of Object.keys(sourceAssetHits))assert.ok(sourceAssetHits[name]>0,`Versioned source asset must be intercepted: ${name}`);console.log(JSON.stringify({checks,requests:requests.length,writes:removalWrites.length,sourceAssetHits,pageErrors:errors.length}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
