/* Phone Sage-card fixtures: no actual CAPTCHA or SMS calls. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..');
(async()=>{
 const browser=await chromium.launch({channel:'chrome'}),page=await browser.newPage();page.setDefaultTimeout(5000);
 let mode='register',authed=false,enabled=true,profilePhone=true,stage='captcha',failCompleteSession=false,reads=0;
 const errors=[],writes=[];page.on('pageerror',error=>errors.push(error.message));
 const session=()=>({authenticated:authed,user:authed?{id:'phone-user',display_name:'Phone'}:null,accounts:[],csrf_token:'fixture-csrf',capabilities:{captcha:true,captcha_app_id:'123',email_registration:true,password_login:true,password_reset:false,phone_registration:enabled,phone_password_login:enabled,phone_password_reset:enabled,password_managed_by_google:false,registration_terms_version:'NONPRODUCTION-FIXTURE',registration_terms_url:'/legal/fixture',registration_privacy_url:'/legal/fixture'}});
 const flow=()=>({flow_id:'phone-flow',method:'phone',purpose:mode==='register'?'registration':mode==='reset'?'recovery':'login',stage,expires_in:300,captcha_app_id:'123',...(stage==='code'?{challenge_id:'phone-challenge',resend_after:0}:{})});
 await page.route('**/*',route=>{
  const req=route.request(),url=new URL(req.url()),json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
  if(url.host==='turing.captcha.qcloud.com')return route.fulfill({contentType:'text/javascript',body:'window.TencentCaptcha=function(id,callback){this.show=()=>callback({ret:0,ticket:"fixture-ticket",randstr:"fixture-random"});this.destroy=()=>{}}'});
  if(url.pathname.startsWith('/api/')){
   if(req.method()!=='GET'){assert.equal(req.headers()['x-csrftoken'],'fixture-csrf');writes.push([url.pathname,req.postDataJSON()]);}
   if(url.pathname==='/api/auth/session'){reads++;if(failCompleteSession){failCompleteSession=false;return json({error:{code:'SERVICE_UNAVAILABLE'}},503);}return json(session());}
   if(['/api/auth/register/start','/api/auth/password-login','/api/auth/recovery/start'].includes(url.pathname)){const body=req.postDataJSON();assert.equal(body.method,'phone');assert.equal(body.identifier,'+8613800000000');if(mode!=='reset')assert.equal(body.password,'Silver!864');stage='captcha';return json(flow(),201);}
   if(url.pathname==='/api/auth/captcha'){if(mode==='login'){authed=true;return json(session());}stage='code';return json(flow(),201);}
   if(url.pathname==='/api/auth/register/verify'){assert.equal(req.postDataJSON().code,'123456');authed=true;return json(session());}
   if(url.pathname==='/api/auth/recovery/verify'){assert.equal(req.postDataJSON().code,'123456');stage='password';return json(flow());}
   if(url.pathname==='/api/auth/recovery/complete'){assert.equal(req.postDataJSON().password,'Copper!975');return json({status:'password_reset'});}
   if(url.pathname==='/api/auth/resend'){stage='code';return json(flow(),201);}
   if(url.pathname==='/api/auth/cancel')return json({status:'cancelled'});
   if(url.pathname==='/api/account/profile')return json({display_name:'Phone',email:'',phone:profilePhone?'+8613800000000':'',password_reset_method:profilePhone?'phone':null,avatar_url:null});
   if(url.pathname==='/api/account/sessions')return json({items:[],next_offset:null});
   if(url.pathname==='/api/account/preferences')return json({product_updates:false});
   if(url.pathname==='/api/account/deletion-eligibility')return json({available:false,eligible:false,blockers:['EMAIL_VERIFICATION_REQUIRED'],policy_version:'',request:null});
   if(url.pathname==='/api/auth/password-challenges'){assert.deepEqual(req.postDataJSON(),{identifier:'+8613800000000',method:'phone'});return json({challenge_id:'settings-phone',expires_in:300,resend_after:0},201);}
   if(url.pathname==='/api/auth/password-reset'){assert.deepEqual(req.postDataJSON(),{challenge_id:'settings-phone',code:'123456',password:'Copper!975'});authed=false;return json({status:'password_reset'});}
   throw Error('Unexpected API '+url.pathname);
  }
  let filename;
  if(/\/(auth|profile)(-en)?\.html$/.test(url.pathname))filename=path.join(root,'dist/pages',path.basename(url.pathname));
  else if(/\.(js|css)$/.test(url.pathname))filename=path.join(root,'src/pages',path.basename(url.pathname));
  else if(url.pathname.includes('/icons/'))filename=path.join(root,'src/pages/icons',path.basename(url.pathname));
  else if(url.pathname.endsWith('favicon.svg'))filename=path.join(root,'assets/favicon.svg');
  return route.fulfill({contentType:filename?.endsWith('.js')?'text/javascript':filename?.endsWith('.css')?'text/css':filename?.endsWith('.png')?'image/png':filename?.endsWith('.svg')?'image/svg+xml':'text/html',body:filename?fs.readFileSync(filename):'<p>Fixture destination</p>'});
 });
 const locator=step=>page.locator(`[data-auth-step="${step}"]`),submit=()=>page.locator('#auth-verify').click();
 async function visit(intent,language=false,width=390,scale=1){mode=intent;authed=false;stage='captcha';await page.setViewportSize({width,height:1200});await page.goto(`http://127.0.0.1/pages/auth${language?'-en':''}.html?mode=${mode}&method=phone&return=profile${language?'-en':''}.html`);await locator('details').waitFor({state:'visible'});await page.waitForFunction(()=>!document.querySelector('#auth-contact').disabled);if(scale===2)await page.locator('#auth-root,#auth-root *').evaluateAll(nodes=>{const sizes=nodes.map(n=>parseFloat(getComputedStyle(n).fontSize));nodes.forEach((n,i)=>n.style.fontSize=sizes[i]*2+'px');});}
 async function details(){await page.locator('#auth-contact').fill('13800000000');if(mode==='register'){await page.locator('#auth-first-name').fill('Phone');await page.locator('#auth-agreement').check();}await submit();}
 async function password(){await page.locator('#auth-password').fill('Silver!864');await submit();}
 let layouts=0;
 try{
  await visit('register');assert.equal(await page.locator('#auth-contact').getAttribute('type'),'tel');assert.equal(await page.locator('#auth-phone-prefix').textContent(),'+86');
  const before=writes.length;await page.locator('#auth-first-name').fill('Phone');await page.locator('#auth-agreement').check();await page.locator('#auth-contact').fill('1３800000000');await submit();assert.equal(writes.length,before);await page.locator('#auth-contact-error').filter({hasText:'11'}).waitFor();
  for(const language of [false,true])for(const width of [320,390,768,1440])for(const scale of [1,2]){
   await visit('register',language,width,scale);
   for(const step of ['details','password','captcha','code']){
    if(step==='password')await details();if(step==='captcha')await password();if(step==='code')await page.locator('#auth-captcha').click();await locator(step).waitFor({state:'visible'});
    const box=await page.evaluate(()=>{const card=document.querySelector('.auth-card'),r=card.getBoundingClientRect(),style=getComputedStyle(card),left=r.left+parseFloat(style.paddingLeft),right=r.right-parseFloat(style.paddingRight);return{width:innerWidth,scroll:document.documentElement.scrollWidth,within:[...document.querySelectorAll('[data-auth-step]:not([hidden]) input')].every(n=>{const b=n.getBoundingClientRect();return !b.width||b.left>=left-1&&b.right<=right+1;})};});assert.ok(box.scroll<=box.width+1&&box.within,`${language}/${width}/${scale}/${step} content overflow`);layouts++;
   }
  }
  await visit('register');await details();await password();assert.equal(await page.locator('#auth-username').inputValue(),'+8613800000000');assert.equal(await page.locator('#auth-password').getAttribute('autocomplete'),'new-password');await page.locator('#auth-captcha').click();await page.locator('[data-code-index="0"]').fill('123456');await page.waitForURL('**/index.html');
  await visit('login',true);await details();assert.equal(await page.locator('#auth-password').getAttribute('autocomplete'),'current-password');await password();await page.locator('#auth-captcha').click();await page.waitForURL('**/pages/profile-en.html');
  await visit('login');await details();await page.locator('#auth-reset-link').click();mode='reset';await page.locator('#auth-title').filter({hasText:'重置密码'}).waitFor();assert.equal(await page.locator('#auth-contact').inputValue(),'13800000000');await submit();await page.locator('#auth-captcha').click();await page.locator('[data-code-index="0"]').fill('123456');await locator('password').waitFor({state:'visible'});assert.equal(authed,false);await page.locator('#auth-password').fill('Copper!975');failCompleteSession=true;await submit();await page.locator('#auth-reset-success').waitFor({state:'visible'});assert.equal(await page.locator('#auth-password').inputValue(),'');assert.equal(authed,false);const signIn=new URL(await page.locator('#auth-sign-in').getAttribute('href'),page.url());assert.equal(signIn.searchParams.get('method'),'phone');assert.equal(signIn.searchParams.get('return'),'/pages/profile.html');
  for(const language of [false,true]){authed=true;await page.goto(`http://127.0.0.1/pages/profile${language?'-en':''}.html`);await page.locator('.profile-phone').filter({hasText:'+8613800000000'}).waitFor();await page.getByRole('button',{name:language?'Reset password':'重置密码',exact:true}).click();const contact=page.locator('dialog input[autocomplete="username"]');assert.equal(await contact.inputValue(),'+8613800000000');assert.equal(await contact.getAttribute('type'),'tel');assert.equal(await contact.getAttribute('readonly'),'');await page.getByRole('button',{name:language?'Send code':'获取验证码',exact:true}).click();await page.locator('dialog input[autocomplete="one-time-code"]').fill('123456');await page.locator('dialog input[autocomplete="new-password"]').fill('Copper!975');await page.locator('dialog').getByRole('button',{name:language?'Reset password':'确认重置',exact:true}).click();await page.getByRole('heading',{name:language?'Password reset':'密码已重置',exact:true}).waitFor();assert.equal(authed,false);const loginURL=new URL(await page.getByRole('link',{name:language?'Sign in again':'重新登录',exact:true}).getAttribute('href'),page.url());assert.equal(loginURL.searchParams.get('method'),'phone');assert.equal(loginURL.searchParams.get('return'),new URL(page.url()).pathname);}
  enabled=false;authed=false;await page.goto('http://127.0.0.1/pages/auth.html?method=phone');await page.locator('#auth-feedback').filter({hasText:'手机号'}).waitFor();assert.equal(await page.locator('#auth-verify').isDisabled(),true);
  assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);console.log(JSON.stringify({result:'Phone fixtures passed',layouts,profileLanguages:2,pageErrors:0,realSmsCalls:0}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
