/* Actual loopback HTTP. Only the official CAPTCHA SDK supplier is replaced;
 * the parent patches Tencent's network call and uses in-memory email. */
const assert=require('node:assert/strict'),readline=require('node:readline');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE),base=new URL(process.env.SAGE_URL);
assert.equal(base.hostname,'127.0.0.1');
const input=readline.createInterface({input:process.stdin,terminal:false});
const outcomes=[];
const responseAt=(page,path)=>page.waitForResponse(r=>new URL(r.url()).pathname===path&&r.request().method()==='POST');
const ready=page=>page.waitForFunction(()=>!document.querySelector('#auth-verify').disabled);
async function supplier(context){
 await context.route('https://turing.captcha.qcloud.com/TJCaptcha.js',route=>route.fulfill({contentType:'text/javascript',body:'window.TencentCaptcha=class{constructor(app,callback){if(app!=="123")throw Error("Supplier fixture app mismatch");this.callback=callback}show(){this.callback({ret:0,ticket:crypto.randomUUID(),randstr:"supplier-fixture"})}destroy(){}};'}));
}
async function pasteCode(page,code){await page.evaluate(code=>{const data=new DataTransfer();data.setData('text',code);document.querySelector('[data-code-index="0"]').dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));},code);}
function readCode(){const code=new Promise(resolve=>input.once('line',resolve));process.stdout.write('code requested\n');return code;}
async function enterIdentity(page){await ready(page);await page.locator('#auth-contact').fill('website-browser@example.test');await page.locator('#auth-verify').click();}
async function signIn(page,password='Violet!431'){
 page.setDefaultTimeout(10000);await page.goto(new URL('/pages/auth.html?mode=password&return=%2Fpages%2Fprofile.html',base).href);await enterIdentity(page);await page.locator('#auth-password').fill(password);
 const start=responseAt(page,'/api/auth/password-login');await page.locator('#auth-verify').click();{const response=await start;assert.equal(response.status(),201,'Register/login start: '+(await response.json()).error?.code);}await page.locator('#auth-captcha').waitFor({state:'visible'});await ready(page);
 assert.equal((await page.context().request.get(new URL('/api/account/profile',base).href)).status(),401,'Password alone must not create a session');
 const proof=responseAt(page,'/api/auth/captcha');await page.locator('#auth-captcha').click();assert.equal((await proof).status(),200);await page.waitForURL('**/profile.html');
 try{await page.locator('.profile-email').waitFor();}catch(_){throw Error('Profile unavailable after CAPTCHA: '+(await page.context().request.get(new URL('/api/account/profile',base).href)).status());}
}
(async()=>{
 const browser=await chromium.launch({channel:'chrome'}),first=await browser.newContext(),second=await browser.newContext(),guest=await browser.newContext();
 await supplier(first);await supplier(second);await supplier(guest);
 const errors=[];
 try{
  const page=await first.newPage(),other=await second.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));other.on('pageerror',error=>errors.push(error.message));
  for(const current of [page,other])current.on('response',response=>{const url=new URL(response.url());if(url.pathname.startsWith('/api/'))outcomes.push({path:url.pathname,status:response.status()});});
  await page.goto(new URL('/pages/auth.html?mode=register&return=%2Fpages%2Fprofile.html',base).href);await ready(page);
  assert.equal(await page.locator('#auth-agreement').isChecked(),false);await page.locator('#auth-first-name').fill('Browser');await page.locator('#auth-contact').fill('website-browser@example.test');await page.locator('#auth-invite').fill('INVALID88');await page.locator('#auth-agreement').check();await page.locator('#auth-verify').click();await page.locator('#auth-password').fill('Violet!431');
  let start=responseAt(page,'/api/auth/register/start');await page.locator('#auth-verify').click();{const response=await start;assert.equal(response.status(),201,'Register/login start: '+(await response.json()).error?.code);}await page.locator('#auth-captcha').waitFor({state:'visible'});await ready(page);
  const cancelling=responseAt(page,'/api/auth/cancel');await page.locator('#auth-back').click();assert.equal((await cancelling).status(),200);assert.equal(await page.locator('#auth-password').inputValue(),'');await page.locator('#auth-agreement').check();await page.locator('#auth-verify').click();await page.locator('#auth-password').fill('Violet!431');start=responseAt(page,'/api/auth/register/start');await page.locator('#auth-verify').click();{const response=await start;assert.equal(response.status(),201,'Register/login start: '+(await response.json()).error?.code);}await page.locator('#auth-captcha').waitFor({state:'visible'});await ready(page);
  assert.equal((await first.request.get(new URL('/api/account/profile',base).href)).status(),401);
  const proof=responseAt(page,'/api/auth/captcha');await page.locator('#auth-captcha').click();assert.equal((await proof).status(),201);await page.locator('[data-code-index="0"]').waitFor();await ready(page);let code=await readCode();
  let verify=responseAt(page,'/api/auth/register/verify');await pasteCode(page,(code[0]==='0'?'1':'0')+code.slice(1));assert.equal((await verify).status(),422);await page.locator('#auth-code-error').filter({hasText:'验证码不正确'}).waitFor();await ready(page);
  verify=responseAt(page,'/api/auth/register/verify');await pasteCode(page,code);code=undefined;assert.equal((await verify).status(),422);await page.getByText('无效邀请码，请检查',{exact:true}).waitFor();await ready(page);assert.equal((await first.request.get(new URL('/api/account/profile',base).href)).status(),401);
  await page.locator('#auth-invite-final').fill('');verify=responseAt(page,'/api/auth/register/verify');await page.locator('#auth-verify').click();assert.equal((await verify).status(),200);await page.waitForURL('**/index.html');await page.goto(new URL('/pages/profile.html',base).href);await page.locator('.profile-email').waitFor();
  await signIn(other);await page.reload();await page.locator('.profile-email').waitFor();
  await page.locator('input[autocomplete=nickname]').fill('Browser fixture');await page.locator('input[autocomplete=nickname]').press('Enter');await page.getByText('名称已保存。').waitFor();
  await page.locator('input[role=switch]').uncheck();await page.getByText('通知偏好已保存。').waitFor();await page.locator('input[role=switch]').check();await page.getByText('通知偏好已保存。').waitFor();
  await page.getByRole('button',{name:'批量注销设备',exact:true}).click();await page.getByText('其他设备已注销，当前设备保留。').waitFor();assert.equal((await second.request.get(new URL('/api/account/profile',base).href)).status(),401);
  const image=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=','base64');await page.locator('input[type=file]').setInputFiles({name:'fixture.png',mimeType:'image/png',buffer:image});await page.getByRole('button',{name:'确认上传',exact:true}).click();await page.getByRole('img',{name:'当前头像',exact:true}).waitFor();await page.getByLabel('编辑头像',{exact:true}).click();await page.getByRole('button',{name:'移除头像',exact:true}).click();await page.locator('.profile-email').waitFor();
  await page.reload();await page.locator('.profile-email').waitFor();assert.equal(await page.locator('input[autocomplete=nickname]').inputValue(),'Browser fixture');assert.equal(await page.locator('input[role=switch]').isChecked(),true);
  await page.getByRole('button',{name:'重置密码',exact:true}).click();let sent=responseAt(page,'/api/auth/password-challenges');await page.getByRole('button',{name:'获取验证码',exact:true}).click();assert.equal((await sent).status(),201);await page.getByLabel('验证码',{exact:true}).fill(await readCode());await page.getByLabel('新密码',{exact:true}).fill('Violet!431');await page.getByRole('button',{name:'确认重置',exact:true}).click();await page.getByText('密码已重置',{exact:true}).waitFor();assert.equal((await first.request.get(new URL('/api/account/profile',base).href)).status(),401);
  await signIn(page);
  await page.goto(new URL('/pages/auth.html?mode=reset',base).href);
  await page.locator('#auth-feedback').filter({hasText:'你已登录，请在个人设置中重置密码。'}).waitFor();
  assert.equal(await page.locator('#auth-verify').isDisabled(),true);
  assert.equal(await page.getByRole('link',{name:'打开个人设置',exact:true}).getAttribute('href'),'profile.html');
  assert.equal(outcomes.filter(item=>item.path==='/api/auth/recovery/start').length,0,'Authenticated reset never submits anonymous recovery');
  // Recovery is anonymous; keep the successful re-login assertion, then leave
  // through the actual logout API rather than a seeded session or login bypass.
  const current=await (await first.request.get(new URL('/api/auth/session',base).href)).json();
  assert.equal((await first.request.post(new URL('/api/auth/logout',base).href,{headers:{'X-CSRFToken':current.csrf_token,'Content-Type':'application/json'},data:{}})).status(),200);
  assert.equal((await (await first.request.get(new URL('/api/auth/session',base).href)).json()).authenticated,false);
  await page.goto(new URL('/pages/auth.html?mode=reset&return=%2Fpages%2Fprofile.html',base).href);await ready(page);await page.locator('#auth-contact').fill('website-browser@example.test');sent=responseAt(page,'/api/auth/recovery/start');await page.locator('#auth-verify').click();assert.equal((await sent).status(),201);await page.locator('#auth-captcha').waitFor({state:'visible'});await ready(page);sent=responseAt(page,'/api/auth/captcha');await page.locator('#auth-captcha').click();assert.equal((await sent).status(),201);await page.locator('[data-code-index="0"]').waitFor();await ready(page);await pasteCode(page,await readCode());await page.locator('#auth-password').waitFor({state:'visible'});await ready(page);await page.locator('#auth-password').fill('Silver!864');await page.locator('#auth-verify').click();await page.getByText('密码已重置，全部设备已退出。请使用新密码登录。',{exact:true}).waitFor();assert.equal((await first.request.get(new URL('/api/account/profile',base).href)).status(),401);assert.equal((await first.request.get(new URL('/api/auth/session',base).href)).ok(),true);
  await enterIdentity(page);await page.locator('#auth-password').fill('Violet!431');await page.locator('#auth-verify').click();await page.getByText('邮箱或密码不正确，请重试或找回密码。',{exact:true}).waitFor();await page.locator('#auth-password').fill('Silver!864');await page.locator('#auth-verify').click();await page.locator('#auth-captcha').waitFor({state:'visible'});await ready(page);await page.locator('#auth-captcha').click();await page.waitForURL('**/profile.html');await page.locator('.profile-email').waitFor();
  await page.getByRole('link',{name:'用量明细',exact:true}).click();await page.getByText('示例数据 · 不产生实际扣费').waitFor();
  const anonymous=await guest.newPage();anonymous.on('pageerror',error=>errors.push(error.message));await anonymous.goto(new URL('/',base).href);const form=anonymous.locator('#r-contact-form');await form.locator('[name=company]').fill('Browser brand fixture');await form.locator('[name=phone]').fill('+8613800000000');await form.locator('[name=goal]').fill('Browser question fixture');await form.locator('[type=submit]').click();await form.getByText('预约申请已提交。').waitFor();
  const rejected=await guest.request.post(new URL('/api/auth/password-challenges',base).href,{headers:{'X-CSRFToken':(await (await guest.request.get(new URL('/api/auth/session',base).href)).json()).csrf_token,'Content-Type':'application/json'},data:{identifier:'website-browser@example.test'}});assert.equal(rejected.status(),403,'Old profile reset route cannot be used anonymously');assert.deepEqual(errors,[]);
 }finally{await browser.close();input.close();}
})().catch(error=>{console.error(String(error.message||'Browser failed').replace(/\b[0-9]{6}\b/g,'[redacted]'));console.error(JSON.stringify({apiOutcomes:outcomes}));process.exitCode=1;});
