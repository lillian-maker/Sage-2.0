/* Real local HTTP, authenticated sessions and invitation records; no API substitutions. */
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
  const fixture=await fixtureInput,browser=await chromium.launch({channel:'chrome'});
  try{
    for(const target of fixture.targets){
      const contexts=[],errors=[],external=[];
      async function open(cookie){
        const context=await browser.newContext({viewport:{width:target.language==='zh'?320:1280,height:1000}});
        contexts.push(context);await context.addCookies([cookie]);
        // force_login creates the server session only; establish its real CSRF cookie before concurrent page reads.
        const established=await context.request.get(new URL('/api/auth/session',base).href);
        assert.equal(established.status(),200);assert.equal((await established.json()).authenticated,true);
        await context.route('**/*',route=>{const url=new URL(route.request().url());if(url.origin===base.origin)return route.continue();external.push(url.origin);return route.abort('blockedbyclient');});
        const page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));
        await page.goto(new URL('/pages/profile'+(target.language==='en'?'-en':'')+'.html#organization',base).href);
        return {context,page};
      }
      const admin=await open(fixture.admin_cookie),recipient=await open(target.cookie);
      async function summary(){const response=await admin.context.request.get(new URL('/api/billing/organization',base).href,{headers:{'X-Billing-Account':fixture.account_id}});assert.equal(response.status(),200);return response.json();}
      const initial=await summary();
      async function create(identifier){
        await admin.page.locator('[data-invitation-create]').click();
        const dialog=admin.page.locator('[data-invitation-dialog="create"]');
        await dialog.locator('[name="identifier"]').fill(identifier);
        assert.equal(await dialog.locator('[name="paid"]').isChecked(),true);
        const result=admin.page.waitForResponse(response=>new URL(response.url()).pathname==='/api/billing/organization/invitations'&&response.request().method()==='POST');
        await dialog.locator('[data-invitation-submit]').click();
        const response=await result;const data=await response.json();assert.equal(response.status(),201,'Create invitation: '+(data.error?.code||'unexpected HTTP status'));
        assert.equal(data.delivery,'not_sent');assert.equal(data.status,'pending');assert.equal(data.paid,true);
        await dialog.waitFor({state:'detached'});
        await admin.page.locator('[data-invitation-action="revoke"][data-invitation-id="'+data.id+'"]').waitFor();
        return data;
      }
      const withdrawn=await create('withdraw-'+target.language+'@example.invalid');
      assert.equal((await summary()).available_seats,initial.available_seats-1);
      await admin.page.locator('[data-invitation-action="revoke"][data-invitation-id="'+withdrawn.id+'"]').click();
      const revoke=admin.page.waitForResponse(response=>new URL(response.url()).pathname==='/api/billing/organization/invitations/revoke');
      await admin.page.locator('[data-invitation-dialog="revoke"] [data-invitation-submit]').click();
      assert.equal((await revoke).status(),200);
      await admin.page.locator('[data-invitation-dialog="revoke"]').waitFor({state:'detached'});
      assert.equal((await summary()).available_seats,initial.available_seats);
      const invitation=await create(target.identifier);
      await recipient.page.reload();
      const accept=recipient.page.locator('[data-business-invitations="received"] [data-invitation-action="accept"][data-invitation-id="'+invitation.id+'"]');
      await accept.waitFor();
      assert.equal(await recipient.page.locator('[data-invitation-create]').count(),0);
      const noCsrf=await recipient.context.request.post(new URL('/api/account/business-invitations/accept',base).href,{data:{invitation_id:invitation.id,confirm:true}});
      assert.equal(noCsrf.status(),403);
      await accept.click();
      const accepted=recipient.page.waitForResponse(response=>new URL(response.url()).pathname==='/api/account/business-invitations/accept'&&response.request().method()==='POST');
      await recipient.page.locator('[data-invitation-dialog="accept"] [data-invitation-submit]').click();
      const response=await accepted;assert.equal(response.status(),200);const result=await response.json();
      assert.equal(result.account_id,fixture.account_id);assert.equal(result.invitation.status,'accepted');
      await recipient.page.locator('[data-invitation-dialog="accept"]').waitFor({state:'detached'});
      await recipient.page.waitForFunction(id=>Array.from(document.querySelectorAll('#sage-profile-data select option')).some(option=>option.value===id),fixture.account_id);
      const sessionResponse=await recipient.context.request.get(new URL('/api/auth/session',base).href);
      assert.equal(sessionResponse.status(),200);const session=await sessionResponse.json();
      assert.equal(session.active_account_id,target.personal_account_id);
      assert.ok(session.accounts.some(account=>account.id===fixture.account_id));
      const replay=await recipient.context.request.post(new URL('/api/account/business-invitations/accept',base).href,{headers:{'X-CSRFToken':session.csrf_token},data:{invitation_id:invitation.id,confirm:true}});
      assert.equal(replay.status(),200);assert.equal((await replay.json()).membership_id,result.membership_id);
      const final=await summary();assert.equal(final.available_seats,initial.available_seats-1);assert.equal(final.reserved_seats,0);assert.equal(final.assigned_seats,initial.assigned_seats+1);
      for(const item of [admin,recipient])assert.equal(await item.page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
      assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
      for(const context of contexts)await context.close();
    }
  }finally{await browser.close();input.close();}
})().catch(error=>{console.error('Business invitation local HTTP integration failed: '+error.message);input.close();process.exitCode=1;});
