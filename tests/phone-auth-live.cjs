/* Actual loopback browser -> Django -> isolated PG, SMS network fixture only. */
const assert=require('node:assert/strict'),readline=require('node:readline');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE),base=new URL(process.env.SAGE_URL);
assert.equal(base.hostname,'127.0.0.1');
const input=readline.createInterface({input:process.stdin,terminal:false});
const responseAt=(page,path)=>page.waitForResponse(r=>new URL(r.url()).pathname===path&&r.request().method()==='POST');
const ready=page=>page.waitForFunction(()=>!document.querySelector('#auth-verify').disabled);
function readCode(){const result=new Promise(resolve=>input.once('line',resolve));process.stdout.write('code requested\n');return result;}
(async()=>{
 const browser=await chromium.launch({channel:'chrome'}),first=await browser.newContext(),second=await browser.newContext(),errors=[];
 for(const context of [first,second])await context.route('https://turing.captcha.qcloud.com/TJCaptcha.js',route=>route.fulfill({contentType:'text/javascript',body:'window.TencentCaptcha=class{constructor(app,callback){this.callback=callback}show(){this.callback({ret:0,ticket:crypto.randomUUID(),randstr:"fixture"})}destroy(){}}'}));
 try{
  const page=await first.newPage(),other=await second.newPage();for(const p of [page,other]){p.setDefaultTimeout(10000);p.on('pageerror',error=>errors.push(error.message));}
  const visit=async(p,mode)=>{await p.goto(new URL('/pages/auth.html?method=phone&mode='+mode+'&return=%2Fpages%2Fprofile.html',base).href);await ready(p);await p.locator('#auth-contact').fill('13800000000');};
  const submit=async(p,path,status)=>{const result=responseAt(p,path);await p.locator('#auth-verify').click();assert.equal((await result).status(),status);};
  const prove=async(p,status)=>{await p.locator('#auth-captcha').waitFor({state:'visible'});await ready(p);const result=responseAt(p,'/api/auth/captcha');await p.locator('#auth-captcha').click();assert.equal((await result).status(),status);};
  async function signIn(p,password){await visit(p,'password');await p.locator('#auth-verify').click();await p.locator('#auth-password').fill(password);await submit(p,'/api/auth/password-login',201);assert.equal((await p.context().request.get(new URL('/api/account/profile',base).href)).status(),401);await prove(p,200);await p.waitForURL('**/profile.html');await p.locator('.profile-phone').waitFor();}
  await visit(page,'register');await page.locator('#auth-first-name').fill('Phone browser');await page.locator('#auth-agreement').check();await page.locator('#auth-verify').click();await page.locator('#auth-password').fill('Silver!864');await submit(page,'/api/auth/register/start',201);await prove(page,201);
  await page.locator('[data-code-index="0"]').fill(await readCode());await page.waitForURL('**/index.html');await page.goto(new URL('/pages/profile.html',base).href);await page.locator('.profile-phone').filter({hasText:'+8613800000000'}).waitFor();
  assert.equal((await (await first.request.get(new URL('/api/account/profile',base).href)).json()).email,'');
  const deletion=await (await first.request.get(new URL('/api/account/deletion-eligibility',base).href)).json();assert.equal(deletion.available,true,'Phone deletion requires reviewed retention and verified SMS fixtures');assert.equal(deletion.verification_method,'phone');assert.equal(await page.getByRole('button',{name:'删除账号',exact:true}).isEnabled(),true,'Reviewed phone deletion must be available in the browser');
  await signIn(other,'Silver!864');
  await page.getByRole('button',{name:'重置密码',exact:true}).click();const username=page.locator('dialog input[autocomplete="username"]');assert.equal(await username.inputValue(),'+8613800000000');assert.equal(await username.getAttribute('type'),'tel');
  let sent=responseAt(page,'/api/auth/password-challenges');await page.getByRole('button',{name:'获取验证码',exact:true}).click();assert.equal((await sent).status(),201);await page.locator('dialog input[autocomplete="one-time-code"]').fill(await readCode());await page.locator('dialog input[autocomplete="new-password"]').fill('Copper!975');const reset=responseAt(page,'/api/auth/password-reset');await page.getByRole('button',{name:'确认重置',exact:true}).click();const resetResponse=await reset;assert.equal(resetResponse.status(),200,'Settings reset: '+JSON.stringify(await resetResponse.json()));await page.getByRole('heading',{name:'密码已重置',exact:true}).waitFor();
  assert.equal((await first.request.get(new URL('/api/account/profile',base).href)).status(),401);assert.equal((await second.request.get(new URL('/api/account/profile',base).href)).status(),401);
  await visit(page,'reset');await submit(page,'/api/auth/recovery/start',201);await prove(page,201);await page.locator('[data-code-index="0"]').fill(await readCode());await page.locator('#auth-password').waitFor({state:'visible'});assert.equal((await first.request.get(new URL('/api/account/profile',base).href)).status(),401);await ready(page);await page.locator('#auth-password').fill('Violet!431');await submit(page,'/api/auth/recovery/complete',200);await page.locator('#auth-reset-success').waitFor({state:'visible'});assert.equal((await first.request.get(new URL('/api/account/profile',base).href)).status(),401);
  await signIn(page,'Violet!431');assert.deepEqual(errors,[]);
 }finally{await browser.close();input.close();}
})().catch(error=>{console.error(String(error.message).replace(/\b[0-9]{6}\b/g,'[redacted]'));process.exitCode=1;});
