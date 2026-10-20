/* Real local HTTP and database. Python supplies synthetic trusted payment facts between phases. */
const assert=require('node:assert/strict'),readline=require('node:readline');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE),base=new URL(process.env.SAGE_URL);
assert.equal(base.protocol,'http:');assert.equal(base.hostname,'127.0.0.1');
const input=readline.createInterface({input:process.stdin,terminal:false});
const lines=input[Symbol.asyncIterator]();
(async()=>{
  const fixture=JSON.parse((await lines.next()).value),browser=await chromium.launch({channel:'chrome'});
  try{
    for(const [index,language] of ['', '-en'].entries()){
      const context=await browser.newContext({viewport:{width:index?1280:320,height:1000}});
      await context.addCookies([fixture.cookie]);
      const established=await context.request.get(new URL('/api/auth/session',base).href);
      assert.equal(established.status(),200);assert.equal((await established.json()).authenticated,true);
      const errors=[],external=[],writes=[];
      await context.route('**/*',route=>{const url=new URL(route.request().url());if(url.origin===base.origin)return route.continue();external.push(url.origin);return route.abort('blockedbyclient');});
      const page=await context.newPage();page.setDefaultTimeout(10000);
      page.on('pageerror',error=>errors.push(error.message));
      page.on('request',request=>{if(request.method()==='POST')writes.push(new URL(request.url()).pathname);});
      const profile=new URL('/pages/profile'+language+'.html#organization',base).href;
      await page.goto(profile);
      const panel=page.locator('[data-seat-purchase]');await panel.locator('[name="quantity"]').fill('2');
      const quoted=page.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/seat-quotes')&&response.request().method()==='POST');
      await panel.locator('[data-seat-quote]').click();
      const response=await quoted;assert.equal(response.status(),201);const quote=await response.json();
      assert.equal(quote.seat_count_before,2+index*2);assert.equal(quote.seat_count_after,4+index*2);
      assert.equal(quote.amount_minor,'12345');assert.equal(quote.first_month_credits_per_seat,'8.63');assert.equal(quote.first_month_credits_total,'17.26');
      await panel.locator('[data-seat-agree]').check();
      const confirmed=page.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/seat-purchases')&&response.request().method()==='POST');
      await panel.locator('[data-seat-confirm]').click();
      const savedResponse=await confirmed;assert.equal(savedResponse.status(),201);const saved=await savedResponse.json();
      assert.equal(saved.status,'pending_payment');assert.ok(saved.order_id);
      await panel.locator('[data-seat-order]').waitFor();
      const link=new URL(await panel.locator('[data-seat-order]').getAttribute('href'),profile);
      assert.equal(link.searchParams.get('order'),saved.order_id);
      const pending=await context.request.get(new URL('/api/billing/orders/'+saved.order_id,base).href,{headers:{'X-Billing-Account':fixture.account_id}});
      assert.equal(pending.status(),200);assert.equal((await pending.json()).order.status,'pending_payment');
      await page.reload();await panel.locator('[data-seat-order]').waitFor();
      assert.equal(writes.filter(path=>path.endsWith('/seat-purchases')).length,1);
      assert.ok(writes.every(path=>path.endsWith('/seat-quotes')||path.endsWith('/seat-purchases')));
      process.stdout.write('seat fixture requested\n');
      assert.equal((await lines.next()).value,'seat fixture ready');
      await panel.locator('[data-seat-check]').click();
      await page.waitForFunction(()=>/新增名额生效|additional seats are active/i.test(document.querySelector('[data-seat-purchase]')?.textContent||''));
      const summary=await context.request.get(new URL('/api/billing/organization',base).href,{headers:{'X-Billing-Account':fixture.account_id}});
      assert.equal(summary.status(),200);assert.equal((await summary.json()).seat_count,4+index*2);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
      await context.close();
    }
  }finally{await browser.close();input.close();}
})().catch(error=>{console.error('Seat purchase local HTTP integration failed: '+error.message);input.close();process.exitCode=1;});
