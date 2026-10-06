// Read-only response fixtures. No actual account, email or payment operation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SAGE_URL || 'http://127.0.0.1:8914';
assert.ok(['127.0.0.1','localhost'].includes(new URL(base).hostname));
const accountId = '11111111-1111-4111-8111-111111111111';
const otherAccountId = '22222222-2222-4222-8222-222222222222';
const orderId = '33333333-3333-4333-8333-333333333333';
const account = {id:accountId,kind:'personal',name:'Fixture personal',role:'owner'};
const order = {id:orderId,account_id:accountId,plan_code:'pro',amount_minor:19901,currency:'CNY',status:'fulfilled',created_at:'2026-10-02T00:00:00Z'};
const annualAssessment = () => ({order_id:orderId,account_id:accountId,can_submit:false,currency:'CNY',status:'scope_ready',
  scope:'annual_future_service_months',reason:'annual_refund_policy_review_required',future_month_count:2,
  service_period:{starts_at:'2026-01-31T09:30:00.123456+08:00',ends_at:'2027-01-31T09:30:00.123456+08:00'},
  current_service_month:{starts_at:'2026-10-31T09:30:00.123456+08:00',ends_at:'2026-11-30T09:30:00.123456+08:00'},
  future_service_months:[{starts_at:'2026-11-30T09:30:00.123456+08:00',ends_at:'2026-12-31T09:30:00.123456+08:00'},
    {starts_at:'2026-12-31T09:30:00.123456+08:00',ends_at:'2027-01-31T09:30:00.123456+08:00'}]});
(async () => {
  const browser = await chromium.launch({channel:'chrome'});
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(7000);
    const errors = [], requests = [];
    let review = true, attempt = null, malformed = false, listMode = 'single', releaseList = null;
    let notificationMode = 'normal', releaseNotification = null;
    let refundAssessment = null, heldRefund = null, activeAccount = accountId;
    const notificationTypes = ['payment_received','cancellation_processing','cancellation_confirmed','credit_refund_reserved','credit_refund_refunded','credit_refund_released','unknown',
      'payment_failed','renewal_failed','topup_authorization_recorded','topup_authorization_paused',
      'topup_revocation_processing','topup_revocation_confirmed','seat_assignment_changed','shared_limit_changed',
      'credit_batch_expired','service_period_ended','renewal_payment_received',
      'topup_payment_received','topup_payment_failed'];
    const notices = Array.from({length:51},(_,index)=>({id:`44444444-4444-4444-8444-${String(index+1).padStart(12,'0')}`,
      type:notificationTypes[index%notificationTypes.length],order_id:orderId,subscription_id:null,refund_id:null,
      amount_minor:[0,3,4,5,17,18].includes(index%notificationTypes.length)?'19900':null,
      currency:[0,3,4,5,17,18].includes(index%notificationTypes.length)?'CNY':null,occurred_at:'2026-10-02T00:00:00Z'}));
    const history = Array.from({length:51}, (_,index) => ({...order,
      id:`33333333-3333-4333-8333-${String(index+1).padStart(12,'0')}`}));
    page.on('pageerror', error => errors.push(error.message));
    // Exercise current source without writing or rebuilding generated artifacts.
    await page.route('**/billing.js', route => route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(__dirname,'../src/pages/billing.js'),'utf8')}));
    await page.route('**/billing.css', route => route.fulfill({contentType:'text/css',body:fs.readFileSync(path.join(__dirname,'../src/pages/billing.css'),'utf8')}));
    await page.route('**/api/**', async route => {
      const request = route.request(), endpoint = new URL(request.url()).pathname;
      requests.push([endpoint,request.method()]);
      assert.equal(request.method(),'GET');
      const json = value => route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
      if (endpoint === '/api/billing/catalog') return json(JSON.parse(fs.readFileSync(path.join(__dirname,'../src/pages/billing-catalog.json'),'utf8')));
      if (endpoint === '/api/auth/session') return json({authenticated:true,user:{id:'fixture-user',display_name:'Fixture user'},accounts:[account,{...account,id:otherAccountId,name:'Fixture other'}],active_account_id:activeAccount,csrf_token:'fixture-only-csrf'});
      if (endpoint === '/api/billing/account') return json({account:activeAccount===accountId?account:{...account,id:otherAccountId,name:'Fixture other'},credits:{available:'10'},orders:activeAccount===accountId?[order]:[],capabilities:{}});
      if (endpoint === '/api/billing/notifications') {
        const offset=Number(new URL(request.url()).searchParams.get('offset')||0);
        if(notificationMode==='delayed') await new Promise(resolve=>{releaseNotification=resolve;});
        if(notificationMode==='failed') return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{code:'UNAVAILABLE'}})});
        const items=notificationMode==='empty'?[]:notices.slice(offset,offset+50);
        return json({account_id:notificationMode==='wrong'?'22222222-2222-4222-8222-222222222222':accountId,
          offset,total:notificationMode==='empty'?0:51,next_offset:items.length===50?50:null,
          items:notificationMode==='malformed'?[{...notices[0],type:'<img src=x onerror=alert(1)>'}]:items});
      }
      if (endpoint === '/api/billing/orders') {
        if (activeAccount!==accountId) return json({account_id:activeAccount,items:[],next_offset:null});
        const offset=Number(new URL(request.url()).searchParams.get('offset')||0);
        if (listMode==='delayed') await new Promise(resolve=>{releaseList=resolve;});
        if (listMode==='failed') return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{code:'UNAVAILABLE'}})});
        return json({account_id:listMode==='wrong'?'22222222-2222-4222-8222-222222222222':accountId,
          items:listMode==='single'?[order]:history.slice(offset,offset+50),next_offset:listMode==='single'||offset===50?null:50});
      }
      if (endpoint.startsWith('/api/billing/orders/') && activeAccount!==accountId) return route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({error:{code:'NOT_FOUND'}})});
      if (history.some(item=>endpoint===`/api/billing/orders/${item.id}`)) return json({order:history.find(item=>endpoint.endsWith(item.id)),delivery:{status:'completed'},payments:[],refunds:[],payment_attempt:null});
      if (endpoint === `/api/billing/orders/${orderId}`) return json({order,delivery:{status:'completed'},payment_attempt:attempt,refunds:[],payments:[{status:'accepted'},...(review ? [{status:'duplicate_payment_review'}] : [])],needs_review:review,review_reasons:review ? [malformed ? '<img src=x onerror=alert(1)>' : 'duplicate_payment_review'] : []});
      if (endpoint.endsWith('/refund-assessment')) {
        const value=structuredClone(refundAssessment||{order_id:orderId,account_id:accountId,can_submit:false,currency:'CNY',status:'unavailable',reason:'payment_review_required'});
        if (heldRefund) { const held=heldRefund; heldRefund=null; held.started(); await held.promise; }
        return json(value);
      }
      throw new Error(`Unexpected read: ${endpoint}`);
    });
    let count = 0;
    for (const language of ['', '-en']) {
      for (const width of [320,1440]) {
        await page.setViewportSize({width,height:1100});
        for (const withAttempt of [false,true]) {
          review = true; malformed = false;
          attempt = withAttempt ? {status:'succeeded',needs_reconciliation:false} : null;
          await page.goto(`${base}/pages/billing${language}.html#orders`);
          await page.locator('[data-order]').click();
          await page.locator('#order-payment-review').waitFor();
          assert.match(await page.locator('#order-payment-review').innerText(),language ? /additional payment/ : /额外收款/);
          assert.match(await page.locator('#order-detail').innerText(),language ? /Completed/ : /已完成/);
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),true);
          const before = requests.length;
          await page.locator('#order-review-refresh').click();
          await page.locator('#order-payment-review').waitFor();
          assert.ok(requests.length > before);
          review = false;
          await page.locator('#order-review-refresh').click();
          await page.waitForFunction(() => !document.querySelector('#order-payment-review') && document.querySelector('#refund-assessment'));
          count++;
        }
      }
    }
    review = true; malformed = true;
    await page.goto(`${base}/pages/billing.html#orders`);
    await page.locator('[data-order]').click();
    await page.waitForFunction(() => document.querySelector('#order-detail')?.textContent.includes('订单详情读取失败'));
    assert.equal(await page.locator('#order-detail img').count(),0);
    for(const language of ['', '-en']) {
      listMode='paged'; review=false; malformed=false;
      await page.setViewportSize({width:320,height:1100});
      await page.goto(`${base}/pages/billing${language}.html#orders`);
      await page.reload();
      await page.locator('[data-orders-next]').waitFor();
      assert.equal(await page.locator('[data-order]').count(),50);
      await page.locator('[data-orders-next]').click();
      await page.waitForFunction(()=>document.querySelectorAll('[data-order]').length===1);
      assert.equal(await page.locator('[data-order]').getAttribute('data-order'),history[50].id);
      assert.equal(await page.locator('[data-orders-next]').count(),0);
      assert.equal(await page.locator('[data-orders-prev]').count(),1);
      await page.locator('[data-orders-prev]').click();
      await page.locator('[data-orders-next]').waitFor();
      assert.equal(await page.locator('[data-order]').count(),50);
      assert.equal(await page.locator('#orders-list-title').evaluate(el=>el===document.activeElement),true);
      await page.goto(`${base}/pages/billing${language}.html?order=${history[50].id}#orders`);
      await page.waitForFunction(()=>document.querySelector('#order-detail')?.textContent.includes('33333333-3333-4333-8333-000000000051'));
      assert.equal(await page.locator('[data-order]').count(),50);
      await page.evaluate(()=>document.documentElement.style.fontSize='200%');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
      fs.mkdirSync(path.join(__dirname,'../tmp/order-pagination'),{recursive:true});
      await page.locator('#orders-list-title').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(__dirname,`../tmp/order-pagination/${language||'zh'}-320.png`)});
      await page.locator('#order-detail').screenshot({path:path.join(__dirname,`../tmp/order-pagination/${language||'zh'}-detail.png`)});
      await page.evaluate(()=>document.documentElement.style.fontSize='');
      for(listMode of ['failed','wrong']) {
        await page.goto(`${base}/pages/billing${language}.html#orders`); await page.reload();
        await page.locator('[data-orders-retry]').waitFor();
        assert.equal(await page.locator('[data-order]').count(),0);
        assert.equal(await page.locator('[data-orders-next]').count(),0);
      }
      listMode='paged'; await page.locator('[data-orders-retry]').click();
      await page.locator('[data-orders-next]').waitFor();
      listMode='delayed'; releaseList=null; await page.locator('[data-orders-next]').click();
      await page.waitForFunction(()=>document.querySelector('#account-live')?.textContent.includes(document.documentElement.lang==='en'?'Loading orders':'正在读取订单'));
      while(!releaseList)await new Promise(resolve=>setTimeout(resolve,10));
      await page.locator('.account-menu a[href="#invoices"]').click();
      releaseList(); await page.waitForTimeout(100);
      assert.equal(await page.locator('[data-order]').count(),0);
      count++;
    }
    for(const language of ['', '-en']) {
      notificationMode='normal';
      await page.setViewportSize({width:320,height:1100});
      await page.goto(`${base}/pages/billing${language}.html#notifications`); await page.reload();
      await page.locator('[data-notifications-next]').waitFor();
      assert.equal(await page.locator('.billing-notification').count(),50);
      const text=await page.locator('#account-live').innerText();
      assert.match(text,language ? /Payment confirmed/ : /付款已确认/);
      assert.match(text,language ? /Cancellation processing/ : /解约处理中/);
      assert.match(text,language ? /Refund ended without payment/ : /退款已终止，未退回款项/);
      assert.match(text,language ? /Unknown billing event/ : /未知账务事件/);
      assert.match(text,language ? /Renewal failure confirmed/ : /续费失败已确认/);
      assert.match(text,language ? /Top-up authorization recorded/ : /自动充值授权已记录/);
      assert.match(text,language ? /A Credits batch has expired/ : /一个 Credits 批次已到期/);
      assert.match(text,language ? /A paid service term has ended/ : /一个已付款服务期已结束/);
      assert.match(text,language ? /Renewal payment confirmed/ : /续费付款已确认/);
      assert.match(text,language ? /Automatic top-up payment confirmed/ : /自动充值付款已确认/);
      assert.match(text,language ? /Automatic top-up payment failed/ : /自动充值付款失败已确认/);
      assert.equal(await page.locator('.billing-notification').nth(6).locator('a').count(),0);
      for(const width of [320,390,768,1440]) {
        await page.setViewportSize({width,height:1100});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
      }
      fs.mkdirSync(path.join(__dirname,'../tmp/billing-notifications'),{recursive:true});
      await page.locator('.billing-notification').first().scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(__dirname,`../tmp/billing-notifications/${language||'zh'}-1440.png`)});
      await page.setViewportSize({width:320,height:1100});
      await page.locator('[data-notifications-next]').click();
      await page.waitForFunction(()=>document.querySelectorAll('.billing-notification').length===1);
      assert.equal(await page.locator('#notifications-title').evaluate(el=>el===document.activeElement),true);
      await page.locator('[data-notifications-prev]').click();
      await page.locator('[data-notifications-next]').waitFor();
      const sizes=await page.locator('#account-live, .account-menu').evaluateAll(nodes=>nodes.flatMap(node=>[node,...node.querySelectorAll('*')]).map(node=>[node.tagName,node.className,getComputedStyle(node).fontSize]));
      await page.locator('#account-live, .account-menu').evaluateAll(nodes=>{
        const all=nodes.flatMap(node=>[node,...node.querySelectorAll('*')]);
        const sizes=all.map(node=>parseFloat(getComputedStyle(node).fontSize));
        all.forEach((node,index)=>node.style.fontSize=`${sizes[index]*2}px`);
      });
      assert.ok(sizes.length>0);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
      fs.mkdirSync(path.join(__dirname,'../tmp/billing-notifications'),{recursive:true});
      await page.screenshot({path:path.join(__dirname,`../tmp/billing-notifications/${language||'zh'}-320.png`)});
      for(notificationMode of ['empty','failed','wrong','malformed']) {
        await page.reload();
        await page.waitForFunction(()=>document.querySelector('#account-live')?.textContent.match(/暂无账务通知|No billing updates|通知读取失败|Updates could not be loaded/));
        assert.equal(await page.locator('.billing-notification').count(),0);
        if(notificationMode!=='empty') assert.equal(await page.locator('[data-notifications-retry]').count(),1);
        assert.equal(await page.locator('#account-live img').count(),0);
      }
      notificationMode='normal'; await page.locator('[data-notifications-retry]').click();
      await page.locator('[data-notifications-next]').waitFor();
      notificationMode='delayed';releaseNotification=null;
      await page.locator('[data-notifications-next]').click();
      while(!releaseNotification) await new Promise(resolve=>setTimeout(resolve,10));
      await page.locator('.account-menu a[href="#invoices"]').click();
      releaseNotification();
      await page.waitForTimeout(100);
      assert.equal(await page.locator('.billing-notification').count(),0);
      count++;
    }
    const showAssessment = async language => {
      await page.goto(`${base}/pages/billing${language}.html?order=${orderId}&assessment=${requests.length}#orders`);
      await page.locator('[data-refund-refresh]').waitFor();
    };
    listMode='single'; review=false; malformed=false; attempt=null;
    for (const language of ['', '-en']) {
      for (const width of [320,390,768,1440]) {
        refundAssessment=annualAssessment();
        await page.setViewportSize({width,height:1100});
        await showAssessment(language);
        const section=page.locator('#refund-assessment');
        assert.match(await section.innerText(),language ? /Future full service months/ : /未来完整服务月/);
        assert.equal(await section.locator('[data-annual-month-count]').textContent(),'2');
        assert.equal(await section.locator('[data-annual-future-month]').count(),2);
        assert.equal(await section.locator('dt').nth(1).textContent(),language ? 'Current service month' : '当前服务月');
        assert.match(await section.innerText(),language ? /The current service month is retained and excluded from the refund scope\./ : /当前服务月保留，不计入退款范围。/);
        assert.match(await section.innerText(),/UTC\+08:00/);
        assert.match(await section.innerText(),/123456/);
        assert.match(await section.innerText(),language ? /not an approved refund/ : /不是已批准退款/);
        assert.equal(await section.locator('input, select, textarea, button[type="submit"]').count(),0);
        assert.equal(await section.locator('button').count(),1);
        assert.doesNotMatch(await section.innerText(),/¥|199\.01/);
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
        await section.locator('*').evaluateAll(nodes=>{
          const sizes=nodes.map(node=>parseFloat(getComputedStyle(node).fontSize));
          nodes.forEach((node,index)=>node.style.fontSize=`${sizes[index]*2}px`);
        });
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`annual ${language} ${width} 200% text`);
        const directory=path.join(__dirname,'../tmp/billing-design-qa');fs.mkdirSync(directory,{recursive:true});
        if (width===320 || width===1440) {
          await section.evaluate(el=>el.scrollIntoView({block:'start'}));
          assert.equal(await page.locator('.skip').evaluate(el=>el.getBoundingClientRect().bottom<=0 && document.activeElement!==el),true,'Skip link is outside the actual viewport until focused');
          await page.screenshot({path:path.join(directory,`annual-refund-${language||'zh'}-${width}-200-viewport.png`)});
        }
        await section.screenshot({path:path.join(directory,`annual-refund-${language||'zh'}-${width}-200.png`)});
      }
      // The same instant may use a different explicit offset; microseconds
      // remain exact even though Date.parse alone would drop their last digits.
      refundAssessment=annualAssessment();
      refundAssessment.future_service_months[0].starts_at='2026-11-30T01:30:00.123456Z';
      await showAssessment(language);
      assert.equal(await page.locator('[data-annual-month-count]').textContent(),'2');
      refundAssessment=annualAssessment();refundAssessment.reason='annual_no_future_months';
      refundAssessment.current_service_month=refundAssessment.future_service_months[1];
      refundAssessment.future_service_months=[];refundAssessment.future_month_count=0;
      await showAssessment(language);
      assert.equal(await page.locator('[data-annual-month-count]').textContent(),'0');
      assert.equal(await page.locator('[data-annual-future-month]').count(),0);
      assert.match(await page.locator('#refund-assessment').innerText(),language ? /no future full service months/ : /没有未来完整服务月/);
      for (const reason of ['annual_source_review_required','annual_service_not_started','annual_service_ended']) {
        refundAssessment={order_id:orderId,account_id:accountId,can_submit:false,currency:'CNY',status:'unavailable',reason};
        await showAssessment(language);
        assert.equal(await page.locator('#refund-assessment dl').count(),0);
        assert.equal(await page.locator('#refund-assessment .error').count(),0);
        assert.equal(await page.locator('#refund-assessment button').count(),1);
      }
      // Keep the original Credits assessment display and exact integer values.
      refundAssessment={order_id:orderId,account_id:accountId,can_submit:false,currency:'CNY',status:'assessment_ready',scope:'all_unused_paid_credits',reason:'policy_review_required',unused_quantity:'10.125',paid_minor:'19901',reference_minor:'731'};
      await showAssessment(language);
      assert.match(await page.locator('#refund-assessment').innerText(),/10\.125 Sage Credits/);
      assert.match(await page.locator('#refund-assessment').innerText(),/199\.01/);
      assert.match(await page.locator('#refund-assessment').innerText(),/7\.31/);
      count++;
    }
    const invalidAnnual = [
      value=>delete value.future_month_count,
      value=>{value.future_month_count='2';},
      value=>{value.future_month_count=1.5;},
      value=>{value.future_month_count=12;},
      value=>{value.future_month_count=0;},
      value=>delete value.current_service_month,
      value=>{value.future_service_months=null;},
      value=>{value.future_service_months.reverse();},
      value=>{value.future_service_months[0].starts_at='2026-11-30T09:30:00.123457+08:00';},
      value=>{value.future_service_months[1].ends_at='2027-01-30T09:30:00.123456+08:00';},
      value=>{value.current_service_month.starts_at='2025-10-31T09:30:00.123456+08:00';},
      value=>{value.current_service_month.ends_at=value.current_service_month.starts_at;},
      value=>{value.current_service_month.starts_at='2026-02-30T09:30:00+08:00';},
      value=>{value.current_service_month.starts_at='2026-10-31T09:30:00';},
      value=>{value.current_service_month.starts_at='2026-10-31T09:30:00+25:00';},
      value=>{value.account_id=otherAccountId;},
      value=>{value.order_id=otherAccountId;},
      value=>{value.can_submit=true;},
      value=>{value.reason='annual_no_future_months';},
      value=>{value.reason='annual_unknown';},
      value=>{value.scope='unknown';},
      value=>{value.status='unknown';}
    ];
    for (const language of ['', '-en']) {
      for (const change of invalidAnnual) {
        refundAssessment=annualAssessment();change(refundAssessment);
        await showAssessment(language);
        assert.match(await page.locator('#refund-assessment').innerText(),language ? /unknown, not ineligible/ : /状态未知，不代表不可退款/);
        assert.equal(await page.locator('#refund-assessment dl').count(),0);
        assert.equal(await page.locator('[data-annual-month-count]').count(),0);
        assert.equal(await page.locator('#refund-assessment input, #refund-assessment button[type="submit"]').count(),0);
      }
    }
    count++;
    const holdAssessment = () => {
      let started,release;const waiting=new Promise(resolve=>{started=resolve;});
      const promise=new Promise(resolve=>{release=resolve;});heldRefund={started,promise};
      return {waiting,release};
    };
    const finishAssessment = async held => {
      const receiving=page.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/refund-assessment'));
      held.release();await (await receiving).finished();
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    };
    refundAssessment=annualAssessment();await showAssessment('');
    const oldRange=holdAssessment();await page.locator('[data-refund-refresh]').click();await oldRange.waiting;
    refundAssessment={order_id:orderId,account_id:accountId,can_submit:false,currency:'CNY',status:'unavailable',reason:'annual_source_review_required'};
    await page.locator('[data-order]').click();await page.locator('[data-refund-refresh]').waitFor();
    await finishAssessment(oldRange);
    assert.equal(await page.locator('[data-annual-month-count]').count(),0);
    refundAssessment=annualAssessment();
    const otherAccountRange=holdAssessment();await page.locator('[data-refund-refresh]').click();await otherAccountRange.waiting;
    activeAccount=otherAccountId;
    await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));
    await page.waitForFunction(()=>document.querySelector('#account-live')?.textContent.includes('当前账户还没有订单'));
    await finishAssessment(otherAccountRange);
    assert.equal(await page.locator('#refund-assessment').count(),0);
    assert.equal(await page.locator('[data-annual-month-count]').count(),0);
    count++;
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({groups:count+1,notification_layouts:8,notification_text_200_percent:2,annual_layouts:8,annual_text_200_percent:8,annual_invalid_responses:invalidAnnual.length*2,page_errors:errors.length,requests:requests.length,write_requests:requests.filter(([,method])=>method !== 'GET').length}));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });
