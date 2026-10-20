/* Sage-card browser fixtures only. No Google page imitation or OAuth network. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..'),source=name=>fs.readFileSync(path.join(root,'src/pages',name));
(async()=>{
 const browser=await chromium.launch({channel:'chrome'}),page=await browser.newPage();page.setDefaultTimeout(5000);
 let stage='register',en=false,rejectInvite=false,cancelled=false,cancelFailure=false,authed=false,returnTo='/pages/profile.html',resumeReads=0,sessionReads=0,ttl=300,failResume=0,failSession=0,resumeTerminal='',csrf='fixture-csrf',googleManaged=false,resumeFailureMode='service';
 const errors=[],writes=[],providerRequests=[],screenshots=path.join(root,'tmp/auth-implementation/google-ui');fs.mkdirSync(screenshots,{recursive:true});page.on('pageerror',e=>errors.push(e.message));
 const session=()=>({authenticated:authed,user:authed?{id:'original-fixture',display_name:'Original'}:null,accounts:[],csrf_token:csrf,capabilities:{google_login:true,google_registration:true,captcha:false,password_login:false,email_registration:false,password_reset:false,password_managed_by_google:googleManaged,registration_terms_version:'NONPRODUCTION-FIXTURE-ONLY',registration_terms_url:'/legal/nonproduction-terms',registration_privacy_url:'/legal/nonproduction-privacy'}});
 const flow=()=>({flow_id:'fixture-google-flow',purpose:'google',stage,expires_in:ttl,email:'fixture@example.test',first_name:'Provided name',last_name:'',intent:'login',bind_email_available:true,...(stage==='bind_code'?{challenge_id:'fixture-google-code',resend_after:60}:{})});
 const result=()=>({status:'authenticated',session:session(),return_to:returnTo});
 try{
 await page.route('**/*',route=>{
  const req=route.request(),url=new URL(req.url()),json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
  if(url.origin==='https://accounts.google.com'){providerRequests.push(req.url());return route.abort();}
  if(url.pathname.startsWith('/api/')){
   if(req.method()!=='GET'){writes.push(url.pathname);assert.equal(req.headers()['x-csrftoken'],csrf);}
   if(url.pathname==='/api/account/notifications/summary'){assert.equal(req.method(),'GET');assert.equal(url.search,'');if(!authed)return json({error:{code:'AUTH_REQUIRED'}},401);return json({user_id:session().user.id,unread_count:0});}
   if(url.pathname==='/api/auth/session'){sessionReads++;if(failSession-->0)return json({error:{code:'SERVICE_UNAVAILABLE'}},503);csrf='fixture-csrf-'+sessionReads;return json(session());}
   if(url.pathname==='/api/auth/google/resume'){resumeReads++;assert.deepEqual(req.postDataJSON(),{});if(failResume-->0){if(resumeFailureMode==='transport')return route.abort();if(resumeFailureMode==='malformed')return json({});return json({error:{code:resumeFailureMode==='csrf'?'CSRF_FAILED':resumeFailureMode==='unavailable'?'GOOGLE_UNAVAILABLE':'SERVICE_UNAVAILABLE'}},resumeFailureMode==='csrf'?403:503);}return cancelled||resumeTerminal?json({error:{code:resumeTerminal||'GOOGLE_CANCELLED'}},422):json(stage==='done'?result():flow());}
   if(url.pathname==='/api/auth/google/start'){assert.deepEqual(req.postDataJSON(),{intent:'login',return_to:en?'/index-en.html':'/index.html',language:en?'en':'zh'});return json({authorization_url:'https://accounts.google.com/o/oauth2/v2/auth?scope=openid%20email%20profile&prompt=select_account&state=fixture&nonce=fixture'},201);}
   if(url.pathname==='/api/auth/google/register'){const data=req.postDataJSON();assert.deepEqual(Object.keys(data).sort(),['agreement_acceptance','first_name','flow_id','invitation_code','last_name']);assert.equal(data.agreement_acceptance,'NONPRODUCTION-FIXTURE-ONLY');if(rejectInvite&&data.invitation_code)return json({error:{code:'INVALID_INVITATION_CODE'}},422);authed=true;returnTo=en?'/index-en.html':'/index.html';return json(result());}
   if(url.pathname==='/api/auth/google/bind-password'){assert.deepEqual(req.postDataJSON(),{flow_id:'fixture-google-flow',password:'historical original fixture password'});stage='bind_confirm';return json(flow());}
   if(url.pathname==='/api/auth/google/bind-code/start'){assert.deepEqual(req.postDataJSON(),{flow_id:'fixture-google-flow'});stage='bind_code';return json(flow(),201);}
   if(url.pathname==='/api/auth/google/bind-code/verify'){assert.deepEqual(req.postDataJSON(),{flow_id:'fixture-google-flow',challenge_id:'fixture-google-code',code:'123456'});stage='bind_confirm';return json(flow());}
   if(url.pathname==='/api/auth/google/bind'){assert.deepEqual(req.postDataJSON(),{flow_id:'fixture-google-flow',confirm:true});authed=true;return json(result());}
   if(url.pathname==='/api/auth/cancel')return cancelFailure?json({error:{code:'SERVICE_UNAVAILABLE'}},503):json({status:'cancelled'});
   if(url.pathname==='/api/account/profile')return json({display_name:'Original',email:'fixture@example.test',avatar_url:null});
   if(url.pathname==='/api/account/sessions')return json({items:[],next_offset:null});
   if(url.pathname==='/api/account/preferences')return json({product_updates:false});
   if(url.pathname==='/api/account/deletion-eligibility')return json({available:false,eligible:true,blockers:[],policy_version:'',request:null});
   throw Error('Unexpected API '+url.pathname);
  }
  if(/\/auth(?:-en)?\.html$/.test(url.pathname))return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'dist/pages',path.basename(url.pathname)))});
  if(/\/profile(?:-en)?\.html$/.test(url.pathname))return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'dist/pages',path.basename(url.pathname)))});
  if(/\.(js|css)$/.test(url.pathname)){const name=path.basename(url.pathname);return route.fulfill({contentType:name.endsWith('.js')?'text/javascript':'text/css',body:source(name)});}
  if(url.pathname.endsWith('google-color.png'))return route.fulfill({contentType:'image/png',body:source('icons/google-color.png')});
  if(url.pathname.includes('/icons/')&&url.pathname.endsWith('.svg'))return route.fulfill({contentType:'image/svg+xml',body:source('icons/'+path.basename(url.pathname))});
  if(url.pathname.endsWith('favicon.svg'))return route.fulfill({contentType:'image/svg+xml',body:fs.readFileSync(path.join(root,'assets/favicon.svg'))});
  return route.fulfill({contentType:'text/html',body:'<!doctype html><p>Local destination fixture</p>'});
 });
 async function visit(s,language=false,width=390,scale=1){stage=s;en=language;authed=s==='done';cancelled=false;returnTo=en?'/pages/profile-en.html':'/pages/profile.html';await page.setViewportSize({width,height:1400});await page.goto(`http://127.0.0.1/pages/auth${en?'-en':''}.html?google=resume`);if(s==='done'){await page.waitForURL('**/pages/profile*.html');return;}await page.locator(`[data-auth-step="${s==='register'?'google-register':s==='bind_proof'?'google-proof':s==='bind_code'?'code':'google-confirm'}"]`).waitFor({state:'visible'});await page.waitForFunction(()=>!document.querySelector('#auth-verify').disabled);if(scale===2)await page.locator('#auth-root,#auth-root *').evaluateAll(nodes=>{const sizes=nodes.map(n=>parseFloat(getComputedStyle(n).fontSize));nodes.forEach((n,i)=>n.style.fontSize=sizes[i]*2+'px');});}
 await visit('bind_proof');const revealCount=await page.locator('[data-auth-step="google-proof"] button[aria-pressed]').count();
 failResume=1;await page.goto('http://127.0.0.1/pages/auth.html?google=resume');await page.locator('#auth-feedback.error').waitFor({state:'visible'});const retryVisible=await page.locator('#auth-retry').isVisible();
 assert.deepEqual({revealCount,retryVisible},{revealCount:1,retryVisible:true},'R1 original reveal and recoverable resume: '+JSON.stringify({revealCount,retryVisible}));
 const reads=sessionReads,resumesBefore=resumeReads,writesBefore=writes.length;await page.locator('#auth-retry').click();await page.locator('[data-auth-step="google-proof"]').waitFor({state:'visible'});await page.waitForFunction(()=>!document.querySelector('#auth-verify').disabled);assert.equal(sessionReads,reads+1);assert.equal(resumeReads,resumesBefore+1);assert.deepEqual(writes.slice(writesBefore),['/api/auth/google/resume']);
 failResume=1;await page.goto('http://127.0.0.1/pages/auth.html?google=resume');await page.locator('#auth-retry').waitFor({state:'visible'});await page.locator('#auth-back').click();await page.locator('[data-auth-step="details"]').waitFor({state:'visible'});assert.equal(await page.locator('#auth-back').isVisible(),false,'Explicit restart clears resume intent in memory and history');assert.equal(await page.evaluate(()=>history.state?.sageGoogleResume===true),false);
 let layouts=0;
 for(const language of [false,true])for(const width of [320,390,768,1440])for(const scale of [1,2])for(const s of ['register','bind_proof','bind_code','bind_confirm']){
  await visit(s,language,width,scale);assert.equal(new URL(page.url()).searchParams.has('google'),false);
  assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
  const bounds=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,width:innerWidth,card:document.querySelector('.auth-card').getBoundingClientRect().width}));
  assert.ok(bounds.scroll<=bounds.width+1,`${s}/${language}/${width}/${scale} overflow`);assert.ok(bounds.card<=440+1);
  if(s==='register')assert.equal(await page.locator('#google-agreement').isChecked(),false);
  if(s==='bind_proof'){
   const input=page.locator('#google-password'),toggle=page.locator('#google-reveal');assert.equal(await input.getAttribute('autocomplete'),'current-password');assert.equal(await page.locator('#auth-password').isDisabled(),true);assert.equal(await page.locator('#auth-username').isDisabled(),true);assert.equal(await page.locator('#google-username').isDisabled(),false);
   await input.fill('native fixture password');await toggle.click();assert.equal(await input.getAttribute('type'),'text');assert.equal(await input.inputValue(),'native fixture password');assert.equal(await toggle.getAttribute('aria-pressed'),'true');await toggle.click();assert.equal(await input.getAttribute('type'),'password');assert.equal(await input.inputValue(),'native fixture password');assert.equal(await toggle.getAttribute('aria-pressed'),'false');
  }else{assert.equal(await page.locator('#google-password').isDisabled(),true);assert.equal(await page.locator('#google-username').isDisabled(),true);}
  if(s==='bind_code')assert.equal(await page.locator('[data-code-index="0"]').getAttribute('autocomplete'),'one-time-code');
  if(width===390&&scale===1&&['register','bind_proof'].includes(s))await page.screenshot({path:path.join(screenshots,`${language?'en':'zh'}-${s}.png`),fullPage:true});
  layouts++;
 }
 await visit('register');const before=writes.length;await page.locator('#auth-verify').click();assert.equal(writes.length,before,'Unchecked terms never submit registration');
 await page.locator('#google-first-name').fill('Chosen name');await page.locator('#google-agreement').check();await page.locator('#google-invite').fill('BADCODE');rejectInvite=true;
 await page.locator('#auth-verify').click();await page.locator('#google-invite-error').filter({hasText:'无效邀请码，请检查'}).waitFor();assert.equal(await page.locator('#google-first-name').inputValue(),'Chosen name');
 await page.locator('#google-invite').fill('');await page.locator('#auth-verify').click();await page.waitForURL('**/index.html');rejectInvite=false;
 await visit('bind_proof');await page.locator('#google-password').fill('historical original fixture password');await page.locator('#auth-verify').click();await page.locator('[data-auth-step="google-confirm"]').waitFor({state:'visible'});assert.equal(authed,false);assert.equal(await page.locator('#google-password').inputValue(),'');
 assert.equal(writes.filter(x=>x==='/api/auth/google/bind').length,0);await page.locator('#auth-verify').click();await page.waitForURL('**/pages/profile.html');
 await visit('bind_proof',true);await page.locator('#google-use-code').click();await page.locator('[data-auth-step="code"]').waitFor({state:'visible'});await page.locator('[data-code-index="0"]').fill('123456');await page.locator('[data-auth-step="google-confirm"]').waitFor({state:'visible'});assert.equal(authed,false);assert.equal(writes.filter(x=>x==='/api/auth/google/bind-code/verify').length,1);await page.locator('#auth-verify').click();await page.waitForURL('**/pages/profile-en.html');
 await visit('done');
 for(const outcome of ['pending','authenticated','consumed','expired','cancelled','authenticated-expired-receipt']){
  stage='bind_proof';authed=false;failResume=1;resumeTerminal='';const start=writes.length;await page.goto('http://127.0.0.1/pages/auth.html?google=resume');await page.locator('#auth-retry').waitFor({state:'visible'});assert.equal(new URL(page.url()).searchParams.has('google'),false);
  if(outcome==='authenticated'){stage='done';authed=true;returnTo='/pages/profile.html?tab=security';}
  if(outcome==='authenticated-expired-receipt'){stage='done';authed=true;resumeTerminal='INVALID_FLOW';}
  if(['consumed','expired'].includes(outcome))resumeTerminal='INVALID_FLOW';if(outcome==='cancelled')resumeTerminal='GOOGLE_CANCELLED';
  await page.locator('#auth-retry').click();
  if(outcome==='pending')await page.locator('[data-auth-step="google-proof"]').waitFor({state:'visible'});
  else if(outcome==='authenticated')await page.waitForURL('**/pages/profile.html?tab=security');
  else if(outcome==='authenticated-expired-receipt')await page.waitForURL('**/index.html');
  else{await page.locator('#auth-feedback').filter({hasText:outcome==='cancelled'?'已取消 Google 授权':'验证已失效'}).waitFor();assert.equal(await page.locator('#auth-retry').isVisible(),false);}
  assert.deepEqual(writes.slice(start),['/api/auth/google/resume','/api/auth/google/resume'],'Resume reads cannot replay proof/register/bind');assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
 }
 resumeTerminal='';stage='bind_proof';authed=false;failSession=1;await page.goto('http://127.0.0.1/pages/auth.html?google=resume');await page.locator('#auth-retry').waitFor({state:'visible'});await page.locator('#auth-retry').click();await page.locator('[data-auth-step="google-proof"]').waitFor({state:'visible'});
 failResume=2;const unknownWrites=writes.length;await page.goto('http://127.0.0.1/pages/auth.html?google=resume');await page.locator('#auth-retry').waitFor({state:'visible'});assert.deepEqual(await page.evaluate(()=>history.state),{sageGoogleResume:true});await page.reload();await page.locator('#auth-retry').waitFor({state:'visible'});assert.equal(new URL(page.url()).searchParams.has('google'),false);await page.locator('#auth-retry').click();await page.locator('[data-auth-step="google-proof"]').waitFor({state:'visible'});assert.equal(await page.evaluate(()=>history.state?.sageGoogleResume===true),false);assert.deepEqual(writes.slice(unknownWrites),['/api/auth/google/resume','/api/auth/google/resume','/api/auth/google/resume']);
 for(const mode of ['transport','malformed','unavailable','csrf']){resumeFailureMode=mode;failResume=1;const before=writes.length;await page.goto('http://127.0.0.1/pages/auth.html?google=resume');await page.locator('#auth-retry').waitFor({state:'visible'});await page.getByText('未能确认操作结果。请重新连接读取 Google 登录状态，或返回重新开始。',{exact:true}).waitFor();await page.locator('#auth-retry').click();await page.locator('[data-auth-step="google-proof"]').waitFor({state:'visible'});assert.deepEqual(writes.slice(before),['/api/auth/google/resume','/api/auth/google/resume']);}resumeFailureMode='service';
 ttl=1;await visit('bind_proof');await page.locator('#google-password').fill('must be scrubbed on expiry');await page.locator('#google-reveal').click();await page.waitForFunction(()=>document.querySelector('#auth-verify').disabled);assert.equal(await page.locator('#google-password').inputValue(),'','Expired Google proof must be scrubbed');assert.equal(await page.locator('#google-password').getAttribute('type'),'password');assert.equal(await page.locator('#google-reveal').getAttribute('aria-pressed'),'false');ttl=300;
 await visit('bind_proof');await page.locator('#google-password').fill('must be scrubbed before cancellation');await page.locator('#google-reveal').click();cancelFailure=true;await page.locator('#auth-back').click();await page.locator('#auth-feedback').filter({hasText:'取消未能确认'}).waitFor();assert.equal(await page.locator('#google-password').inputValue(),'','Uncertain cancellation cannot retain original password');assert.equal(await page.locator('#google-password').getAttribute('type'),'password');assert.equal(await page.locator('#google-reveal').getAttribute('aria-pressed'),'false');cancelFailure=false;
 stage='register';authed=false;cancelled=true;await page.goto('http://127.0.0.1/pages/auth.html?google=resume');await page.locator('#auth-feedback').filter({hasText:'已取消 Google 授权'}).waitFor();assert.equal(authed,false);
 cancelled=false;const resumes=resumeReads;await page.reload();await page.locator('[data-auth-step="details"]').waitFor({state:'visible'});assert.equal(resumeReads,resumes,'Reload cannot restore consumed marker or staged browser proof');
 await page.locator('#auth-google').click();await page.waitForTimeout(100);assert.equal(providerRequests.length,1);assert.ok(providerRequests[0].startsWith('https://accounts.google.com/o/oauth2/v2/auth?'));
 for(const language of [false,true]){en=language;authed=true;googleManaged=true;await page.goto(`http://127.0.0.1/pages/profile${en?'-en':''}.html`);await page.getByText(en?'Your password is managed by Google. No original email password reset is available.':'密码由 Google 管理；此账号没有原邮箱密码重置通道。',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:en?'Reset password':'重置密码',exact:true}).count(),0);assert.equal(await page.locator('.profile-email').textContent(),'fixture@example.test');}
 assert.deepEqual(errors,[]);console.log(JSON.stringify({result:'Google Sage-card fixtures passed',googleLayouts:layouts,threeBranches:true,explicitBind:true,credentialRevealLayouts:16,resumeOutcomes:6,resumeFailureModes:4,googleProfileLanguages:2,pageErrors:errors.length,realOAuthCalls:0,screenshots:'tmp/auth-implementation/google-ui'}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
