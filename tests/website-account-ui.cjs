// Browser response fixtures only: no real customer data, email or database write.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const source=name=>fs.readFileSync(path.join(__dirname,'../src/pages',name),'utf8');
(async()=>{
 const browser=await chromium.launch({channel:'chrome'}),page=await browser.newPage();page.setDefaultTimeout(5000);
 const writes=[],errors=[];let sessionReads=0,sessionReadReject=false,rejectReadAfterReset=false;page.on('pageerror',error=>errors.push(error.message));
 let profile={display_name:'Fixture user',email:'fixture@example.test',avatar_url:null},prefs={product_updates:false},deletionMode='closed',deletionRequest=null,authPhase=false,loginAccepted=false,resetRejected=false;
 let authFlow=null,flowCounter=0,rejectCode=false,rejectInvite=false,captchaReject=false,cancelReject=false,cancelInvalid=false,registrationStarts=0,codeVerifications=0,captchaLoads=0;
 const nextFlow=purpose=>(authFlow={flow_id:'fixture-flow-'+(++flowCounter),purpose,stage:'captcha',expires_in:300,captcha_app_id:'123'});
 const image=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=','base64'),consultationKeys=[];
 let devices=[{id:'current',client:'Chrome',location:'未知',ip:'127.0.0.1',created_at:'2026-10-06',is_current:true},{id:'other',client:'Safari',location:'未知',ip:'127.0.0.2',created_at:'2026-10-05',is_current:false}];
 const session={authenticated:true,user:{id:'fixture',display_name:'Fixture user'},csrf_token:'fixture-csrf',accounts:[],capabilities:{password_reset:true,password_login:true,email_registration:true,captcha:true,captcha_app_id:'123',registration_terms_version:'NONPRODUCTION-FIXTURE-ONLY',registration_terms_url:'/legal/nonproduction-terms',registration_privacy_url:'/legal/nonproduction-privacy'}};
 try{
 await page.route('**/*',route=>{
  const req=route.request(),url=new URL(req.url()),json=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  if(url.pathname.startsWith('/api/')){
   const method=req.method();if(method!=='GET'){writes.push(url.pathname);if(url.pathname==='/api/auth/cancel'&&req.headers()['x-csrftoken']!==session.csrf_token)return json({error:{code:'CSRF_FAILED'}},403);assert.equal(req.headers()['x-csrftoken'],session.csrf_token);}
   if(url.pathname==='/api/auth/session'){sessionReads++;return sessionReadReject?json({error:{code:'SERVICE_UNAVAILABLE'}},503):json(session);}
   if(url.pathname==='/api/account/profile'){if(method==='PATCH')profile.display_name=req.postDataJSON().display_name;return json(profile);}
   if(url.pathname==='/api/account/preferences'){if(method==='PATCH')prefs=req.postDataJSON();return json(prefs);}
   if(url.pathname==='/api/account/deletion-eligibility')return json({available:deletionMode!=='closed',eligible:deletionMode!=='complex',blockers:deletionMode==='complex'?['PAID_CREDITS_REMAINING']:[],policy_version:'fixture-policy',request:deletionRequest});
   if(url.pathname==='/api/account/deletion-challenges'){assert.deepEqual(req.postDataJSON(),{});return json({challenge_id:'fixture-deletion-challenge',expires_in:600,resend_after:60});}
   if(url.pathname==='/api/account/delete'){const payload=req.postDataJSON();assert.deepEqual(payload,{challenge_id:'fixture-deletion-challenge',code:'123456',confirm:true,policy_version:'fixture-policy'});if(deletionMode==='complex'){deletionRequest={id:'fixture-request',status:'requested',created_at:'2026-10-09',first_response_due_at:'2026-10-13',completion_due_at:'2026-10-20',delay_reason:'',expected_completion_at:null};return json({status:'requested',request:deletionRequest},202);}return json({status:'deleted'});}
   if(url.pathname==='/api/account/avatar'){assert.ok(method==='DELETE'||req.headers()['content-type'].startsWith('multipart/form-data;'));profile.avatar_url=method==='DELETE'?null:'/api/account/avatar-image?v=fixture';return json({avatar_url:profile.avatar_url});}
   if(url.pathname==='/api/account/avatar-image')return route.fulfill({contentType:'image/png',body:image});
   if(url.pathname==='/api/account/sessions'){const offset=Number(url.searchParams.get('offset')||0);return json({items:devices.slice(offset,offset+1),next_offset:devices.length>offset+1?offset+1:null});}
   if(url.pathname==='/api/auth/password-challenges')return json({challenge_id:'fixture-password-challenge',expires_in:300,resend_after:60});
   if(url.pathname==='/api/auth/password-reset'){assert.deepEqual(req.postDataJSON(),{challenge_id:'fixture-password-challenge',code:'123456',password:'Violet!431'});return resetRejected?json({error:{code:'INVALID_CODE'}},422):json({status:'password_reset'});}
   if(url.pathname==='/api/auth/password-login'){assert.equal(authPhase,true);assert.deepEqual(Object.keys(req.postDataJSON()).sort(),['identifier','password']);return loginAccepted?json(nextFlow('login'),201):json({error:{code:'INVALID_CREDENTIALS'}},401);}
   if(url.pathname==='/api/auth/register/start'){const data=req.postDataJSON();assert.deepEqual(Object.keys(data).sort(),['agreement_acceptance','first_name','identifier','invitation_code','last_name','password']);assert.equal(data.agreement_acceptance,'NONPRODUCTION-FIXTURE-ONLY');registrationStarts++;return json(nextFlow('registration'),201);}
   if(url.pathname==='/api/auth/recovery/start'){assert.deepEqual(Object.keys(req.postDataJSON()),['identifier']);return json(nextFlow('recovery'),201);}
   if(url.pathname==='/api/auth/captcha'){assert.deepEqual(Object.keys(req.postDataJSON()).sort(),['flow_id','randstr','ticket']);assert.equal(req.postDataJSON().flow_id,authFlow.flow_id);if(captchaReject)return json({error:{code:'CAPTCHA_REJECTED'}},422);if(authFlow.purpose==='login')return json({...session,authenticated:true});authFlow={...authFlow,stage:'code',challenge_id:'fixture-code-'+flowCounter,resend_after:60};return json(authFlow,201);}
   if(url.pathname==='/api/auth/register/verify'||url.pathname==='/api/auth/recovery/verify'){const data=req.postDataJSON();assert.equal(data.flow_id,authFlow.flow_id);assert.equal(data.challenge_id,authFlow.challenge_id);codeVerifications++;if(rejectCode||data.code!=='123456')return json({error:{code:'INVALID_CODE'}},422);if(authFlow.purpose==='registration'){assert.deepEqual(Object.keys(data).sort(),['challenge_id','code','flow_id','invitation_code']);if(rejectInvite&&data.invitation_code)return json({error:{code:'INVALID_INVITATION_CODE'}},422);return json({...session,authenticated:true});}return json(authFlow={...authFlow,stage:'password'});}
   if(url.pathname==='/api/auth/recovery/complete'){assert.deepEqual(Object.keys(req.postDataJSON()).sort(),['flow_id','password']);session.authenticated=false;if(rejectReadAfterReset)sessionReadReject=true;return json({status:'password_reset'});}
   if(url.pathname==='/api/auth/resend')return json(authFlow={...authFlow,challenge_id:'fixture-resend-code',resend_after:60},201);
   if(url.pathname==='/api/auth/cancel'){if(cancelInvalid)return json({error:{code:'INVALID_FLOW'}},422);if(cancelReject)return json({error:{code:'SERVICE_UNAVAILABLE'}},503);return json({status:'cancelled'});}
   if(url.pathname==='/api/account/sessions/revoke'){devices=devices.filter(item=>item.is_current);return json({revoked_count:1});}
   if(url.pathname==='/api/consultations'){consultationKeys.push(req.headers()['idempotency-key']);assert.ok(req.headers()['idempotency-key']);assert.equal(req.postDataJSON().question,'Fixture question');if(consultationKeys.length===1)return json({error:{code:'UNAVAILABLE'}},503);return json({receipt_id:'fixture-receipt'},201);}
   return json({error:{code:'NOT_FOUND'}},404);
  }
  if(/\/profile(?:-en)?\.html$/.test(url.pathname))return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'../dist/pages',path.basename(url.pathname)),'utf8')});
  if(/\/auth(?:-en)?\.html$/.test(url.pathname))return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'../dist/pages',path.basename(url.pathname)),'utf8')});
  if(url.pathname.endsWith('consultation.html'))return route.fulfill({contentType:'text/html; charset=utf-8',body:'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body><form id="r-contact-form"><input name="company" required><input name="phone" required><input name="email"><textarea name="goal" required></textarea><button type="submit">提交预约</button><p role="status"></p></form><script src="site-shell.js"></script></body></html>'});
  if(req.url()==='https://turing.captcha.qcloud.com/TJCaptcha.js'){captchaLoads++;return route.fulfill({contentType:'text/javascript',body:'window.TencentCaptcha=class{constructor(app,callback){if(app!=="123")throw Error("Fixture app mismatch");this.callback=callback}show(){const result=window.captchaFixtureResult||{ret:0,ticket:crypto.randomUUID(),randstr:"supplier-fixture"};this.callback(result);this.callback(result)}destroy(){}};'});}
  const name=path.basename(url.pathname);if(['profile.js','auth.js','site-shell.js'].includes(name))return route.fulfill({contentType:'text/javascript',body:source(name)});
  if(['redesign.css','experience.css','content-pages.css','billing.css','auth.css'].includes(name))return route.fulfill({contentType:'text/css',body:source(name)});
  if(name==='google-color.png')return route.fulfill({contentType:'image/png',body:fs.readFileSync(path.join(__dirname,'../src/pages/icons',name))});
  if(url.pathname.includes('/icons/')&&name.endsWith('.svg'))return route.fulfill({contentType:'image/svg+xml',body:fs.readFileSync(path.join(__dirname,'../src/pages/icons',name))});
  if(name==='favicon.svg')return route.fulfill({contentType:'image/svg+xml',body:fs.readFileSync(path.join(__dirname,'../assets/favicon.svg'))});
  return route.fulfill({status:200,body:''});
 });
 async function contentBounds(){
  const result=await page.locator('.auth-password-label').evaluate(row=>{const content=document.querySelector('#auth-form').getBoundingClientRect();const children=[...row.children].filter(node=>node.getClientRects().length).map(node=>{const rect=node.getBoundingClientRect();return {left:rect.left,right:rect.right};});const range=document.createRange();range.selectNodeContents(row.querySelector('label'));return {left:content.left,right:content.right,children,labelLines:range.getClientRects().length};});
  assert.ok(result.children.every(rect=>rect.left>=result.left-1&&rect.right<=result.right+1),'Password labels/forgot action stay inside card content: '+JSON.stringify(result));
  assert.equal(result.labelLines,1,'Required star stays with password label at 200% text');
 }
 async function reviewCase(name){
  authPhase=true;session.authenticated=false;session.csrf_token='fixture-csrf';sessionReadReject=false;rejectReadAfterReset=false;cancelReject=false;cancelInvalid=false;
  const ready=()=>page.waitForFunction(()=>!document.querySelector('#auth-verify').disabled);
  const details=async()=>{await ready();await page.locator('#auth-contact').fill('fixture@example.test');await page.locator('#auth-verify').click();await page.locator('#auth-password').waitFor({state:'visible'});};
  const start=async()=>{loginAccepted=true;await details();await page.locator('#auth-password').fill('Violet!431');await page.locator('#auth-verify').click();await page.locator('#auth-captcha').waitFor({state:'visible'});await ready();};
  await page.setViewportSize({width:320,height:1100});await page.goto('http://127.0.0.1/pages/auth-en.html?mode=password');await ready();
  if(name==='label-bounds'){
   await details();await page.locator('#auth-root,#auth-root *').evaluateAll(nodes=>{const sizes=nodes.map(node=>parseFloat(getComputedStyle(node).fontSize));nodes.forEach((node,i)=>node.style.fontSize=sizes[i]*2+'px');});await contentBounds();
  }else if(name==='csrf-reconnect'){
   await start();session.csrf_token='review-fresh-csrf';await page.locator('#auth-back').click();await page.locator('#auth-feedback').filter({hasText:'Cancellation could not be confirmed'}).waitFor();assert.equal(await page.locator('#auth-captcha').isDisabled(),true);
   const reads=sessionReads,cancels=writes.filter(value=>value==='/api/auth/cancel').length;await page.locator('#auth-retry').click();await page.waitForFunction(()=>!document.querySelector('#auth-back').disabled);
   assert.ok(sessionReads>reads,'Reconnect must read session/CSRF before retrying a pending cancellation');assert.equal(writes.filter(value=>value==='/api/auth/cancel').length,cancels,'Reconnect read does not silently retry cancellation');assert.equal(await page.locator('#auth-captcha').isDisabled(),true,'Read refresh does not revive unknown proof');
   await page.locator('#auth-back').click();await page.locator('[data-auth-step="details"]').waitFor({state:'visible'});await ready();assert.equal(await page.locator('#auth-retry').isVisible(),false,'Healthy ready does not show Reconnect');
  }else if(name==='reset-read-failure'){
   await details();await page.locator('#auth-reset-link').click();await page.locator('#auth-verify').click();await page.locator('#auth-captcha').waitFor({state:'visible'});await ready();await page.locator('#auth-captcha').click();await page.locator('[data-auth-step="code"]').waitFor({state:'visible'});await ready();await page.locator('[data-code-index="0"]').fill('123456');await page.locator('#auth-password').waitFor({state:'visible'});await ready();rejectReadAfterReset=true;await page.locator('#auth-password').fill('Violet!431');await page.locator('#auth-verify').click();await page.waitForFunction(()=>document.querySelector('#auth-verify').textContent!=='Please wait…');
   assert.equal(await page.locator('[data-auth-step="details"]').isVisible(),true,'Confirmed reset renders login even when the following read fails');assert.equal(await page.locator('[data-auth-step="password"]').isVisible(),false);assert.equal(await page.getByText('Password reset. All devices are signed out. Sign in with your new password.',{exact:true}).count(),1,'Read failure cannot erase confirmed mutation success');assert.equal(await page.locator('#auth-password').inputValue(),'');assert.equal(await page.locator('#auth-verify').isDisabled(),true,'Login waits for valid fresh session/CSRF');assert.equal(await page.getByRole('link',{name:'Go to sign in',exact:true}).getAttribute('href'),'/pages/auth-en.html?mode=password&return=%2Findex-en.html');
   assert.equal(await page.locator('#auth-retry').isVisible(),true);sessionReadReject=false;rejectReadAfterReset=false;await page.locator('#auth-retry').click();await ready();assert.equal(await page.locator('#auth-retry').isVisible(),false);assert.equal(await page.getByText('Password reset. All devices are signed out. Sign in with your new password.',{exact:true}).count(),1);
  }else throw Error('Unknown review case');
  loginAccepted=false;sessionReadReject=false;rejectReadAfterReset=false;session.csrf_token='fixture-csrf';
 }
 const reviewNames=['label-bounds','csrf-reconnect','reset-read-failure'];
 if(process.env.AUTH_REVIEW_CASE){const failures=[];for(const name of process.env.AUTH_REVIEW_CASE==='all'?reviewNames:[process.env.AUTH_REVIEW_CASE]){try{await reviewCase(name);}catch(error){failures.push({case:name,message:error.message});}}console.log(JSON.stringify({reviewCases:reviewNames.length,failures}));assert.equal(failures.length,0,'Review counterexamples must pass');return;}
 // Read the generated fallback before scripts replace it: partial translations
 // must not leave a mixed-language accessible name or loading message.
 for(const [suffix,language,name,loading] of [['','zh-CN','个人设置导航','正在读取账户资料…'],['-en','en','Personal settings navigation','Loading account data…']]){
  const initial=await browser.newPage({javaScriptEnabled:false});
  try{
   await initial.route('**/*',route=>new URL(route.request().url()).pathname===`/pages/profile${suffix}.html`
    ?route.fulfill({contentType:'text/html; charset=utf-8',body:fs.readFileSync(path.join(__dirname,`../dist/pages/profile${suffix}.html`),'utf8')})
    :route.abort());
   await initial.goto(`http://127.0.0.1/pages/profile${suffix}.html`);
   assert.equal(await initial.locator('html').getAttribute('lang'),language);
   assert.equal(await initial.getByRole('complementary',{name,exact:true}).count(),1,`Profile sidebar has a fully localized accessible name (${language})`);
   assert.equal(await initial.getByText(loading,{exact:true}).count(),1,`Profile loading fallback is fully localized (${language})`);
  }finally{await initial.close();}
 }
 console.log('Two generated profile fallback languages passed; no scripts or API calls.');
 await page.goto('http://127.0.0.1/pages/profile.html');await page.locator('.profile-email').waitFor();
 assert.equal(await page.locator('main h1,.profile-heading').count(),0,'Remove the duplicated personal settings heading');
 assert.equal(await page.locator('.profile-avatar').evaluate(node=>node.getBoundingClientRect().width),48,'Compact 48px avatar');
 assert.equal(await page.locator('.profile-row').first().evaluate(node=>getComputedStyle(node).fontSize),'14px','Option copy follows the 14px account UI scale');
 assert.equal(await page.locator('img:not([src]),img[src=""]').count(),0,'Unused avatar preview must not create an empty image');
 const accountMenu=page.locator('.sage-account-menu'),languageMenu=page.locator('.sage-language-menu');
 await accountMenu.hover();await accountMenu.getByRole('link',{name:'个人设置',exact:true}).waitFor();
 await accountMenu.locator('summary').click();assert.equal(await accountMenu.evaluate(node=>node.open),true,'Mouse click must not undo hover-open account menu');
 const accountPanel=await page.locator('.sage-account-panel').boundingBox(),accountToggle=await accountMenu.locator('summary').boundingBox();
 await page.mouse.move(accountPanel.x+accountPanel.width/2,(accountToggle.y+accountToggle.height+accountPanel.y)/2);assert.equal(await accountMenu.evaluate(node=>node.open),true,'Hover menu stays open across its gap');
 await page.mouse.move(0,0);await page.waitForFunction(()=>!document.querySelector('.sage-account-menu').open);
 await accountMenu.locator('summary').focus();await page.keyboard.press('Enter');await accountMenu.getByRole('link',{name:'个人设置',exact:true}).waitFor();await page.keyboard.press('Escape');assert.equal(await accountMenu.evaluate(node=>node.open),false,'Account Escape closes the keyboard menu');
 assert.ok(['none','normal'].includes(await accountMenu.locator('summary').evaluate(node=>getComputedStyle(node,'::after').content)),'No user dropdown arrow');
 await page.locator('.sage-language-toggle').hover();await languageMenu.waitFor({state:'visible'});
 await page.locator('.sage-language-toggle').click();assert.equal(await languageMenu.isVisible(),true,'Mouse click must not undo hover-open language menu');
 await page.keyboard.press('Escape');await languageMenu.waitFor({state:'hidden'});await page.mouse.move(0,0);await page.locator('.sage-language-toggle').hover();await languageMenu.waitFor({state:'visible'});
 const languagePanel=await languageMenu.boundingBox(),languageToggle=await page.locator('.sage-language-toggle').boundingBox();
 await page.mouse.move(languagePanel.x+languagePanel.width/2,(languageToggle.y+languageToggle.height+languagePanel.y)/2);assert.equal(await languageMenu.isVisible(),true,'Language hover menu stays open across its gap');
 await page.mouse.move(0,0);await languageMenu.waitFor({state:'hidden'});await page.locator('.sage-language-toggle').focus();await page.keyboard.press('ArrowDown');assert.equal(await languageMenu.isVisible(),true);await page.keyboard.press('Escape');assert.equal(await languageMenu.isVisible(),false);
 assert.equal(await page.locator('.r-tools>.r-button.small').getAttribute('href'),'download.html');assert.equal(await page.locator('.r-tools>.r-button.small').textContent(),'下载');
 await page.locator('input[autocomplete=nickname]').fill('Updated fixture');await page.locator('input[autocomplete=nickname]').press('Enter');await page.getByText('名称已保存。').waitFor();assert.equal(profile.display_name,'Updated fixture');
 await page.locator('input[role=switch]').check();await page.getByText('通知偏好已保存。').waitFor();assert.equal(prefs.product_updates,true);
 await page.getByRole('button',{name:'下一页',exact:true}).click();await page.getByRole('cell',{name:'Safari',exact:true}).waitFor();assert.equal(await page.locator('.profile-sessions tbody tr').count(),2);
 const deviceMenu=page.locator('.profile-device-menu');await deviceMenu.hover();await deviceMenu.getByRole('button',{name:'注销设备',exact:true}).waitFor();await page.mouse.move(0,0);await deviceMenu.getByRole('button',{name:'设备操作',exact:true}).focus();await page.keyboard.press('Enter');await page.keyboard.press('Escape');assert.equal(await deviceMenu.getByRole('button',{name:'注销设备',exact:true}).isVisible(),false,'Escape must dismiss the keyboard device menu');
 await page.getByRole('button',{name:'批量注销设备'}).click();await page.getByText('其他设备已注销，当前设备保留。').waitFor();assert.equal(devices.length,1);
 assert.equal(await page.getByRole('button',{name:'删除账号',exact:true}).isDisabled(),true);
 await page.locator('input[type=file]').setInputFiles({name:'fixture.png',mimeType:'image/png',buffer:image});await page.getByRole('img',{name:'中心方形裁剪预览',exact:true}).waitFor();await page.getByRole('button',{name:'取消',exact:true}).click();assert.equal(await page.locator('.profile-avatar-preview').count(),0,'Cancel removes the revoked preview image');
 await page.locator('input[type=file]').setInputFiles({name:'fixture.png',mimeType:'image/png',buffer:image});await page.getByRole('button',{name:'确认上传',exact:true}).click();await page.getByRole('img',{name:'当前头像',exact:true}).waitFor();assert.ok(profile.avatar_url);await page.getByLabel('编辑头像',{exact:true}).click();await page.getByRole('button',{name:'移除头像',exact:true}).click();await page.locator('.profile-email').waitFor();assert.equal(profile.avatar_url,null);
 await page.getByRole('button',{name:'重置密码',exact:true}).click();assert.equal(await page.locator('dialog input[autocomplete=username]').inputValue(),'fixture@example.test');await page.getByRole('button',{name:'获取验证码',exact:true}).click();await page.getByText('如该邮箱支持密码重置，将收到验证码。请检查收件箱。').waitFor();await page.getByLabel('验证码',{exact:true}).fill('123456');assert.equal(await page.locator('dialog input[autocomplete=new-password]').count(),1);await page.getByLabel('新密码',{exact:true}).fill('Violet!431');await page.getByRole('button',{name:'显示密码',exact:true}).click();assert.equal(await page.getByLabel('新密码',{exact:true}).getAttribute('type'),'text');await page.getByRole('button',{name:'隐藏密码',exact:true}).click();await page.getByRole('button',{name:'确认重置',exact:true}).click();await page.getByText('密码已重置',{exact:true}).waitFor();assert.equal(await page.locator('dialog').count(),0);await page.reload();await page.locator('.profile-email').waitFor();
 const count=writes.length;await page.getByRole('link',{name:'用量明细',exact:true}).click();await page.getByText('示例数据 · 不产生实际扣费').waitFor();assert.equal(writes.length,count,'Example usage is read-only');
 deletionMode='complex';await page.goto('http://127.0.0.1/pages/profile.html');await page.getByRole('button',{name:'删除账号',exact:true}).click();await page.getByText('剩余付费额度',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'提交删除申请',exact:true}).isDisabled(),true);assert.equal(await page.locator('dialog input[type=checkbox]').isChecked(),false);
 await page.keyboard.press('Escape');assert.equal(await page.locator('dialog').count(),0);assert.equal(await page.getByRole('button',{name:'删除账号',exact:true}).evaluate(node=>node===document.activeElement),true);
 await page.getByRole('button',{name:'删除账号',exact:true}).click();await page.getByRole('button',{name:'发送删除验证码',exact:true}).click();await page.getByText('验证码已发送至账号邮箱。').waitFor();await page.getByLabel('专用邮箱验证码',{exact:true}).fill('123456');await page.locator('dialog input[type=checkbox]').check();await page.getByRole('button',{name:'提交删除申请',exact:true}).click();await page.getByText('删除申请进度',{exact:true}).waitFor();assert.equal(await page.locator('dialog').count(),0);
 deletionMode='simple';deletionRequest=null;await page.goto('http://127.0.0.1/pages/profile.html');await page.getByRole('button',{name:'删除账号',exact:true}).click();await page.getByRole('button',{name:'发送删除验证码',exact:true}).click();await page.getByText('验证码已发送至账号邮箱。').waitFor();await page.getByLabel('专用邮箱验证码',{exact:true}).fill('123456');await page.locator('dialog input[type=checkbox]').check();await page.getByRole('button',{name:'确认永久删除',exact:true}).click();await page.getByText('账号已删除',{exact:true}).waitFor();assert.equal(await page.locator('dialog').count(),0);
 await page.goto('http://127.0.0.1/pages/consultation.html');await page.locator('[name=company]').fill('Fixture');await page.locator('[name=phone]').fill('+8613800000000');await page.locator('[name=goal]').fill('Fixture question');await page.locator('#r-contact-form [type=submit]').click();await page.getByText('未能确认提交结果，请重试；相同内容不会重复创建申请。').waitFor();await page.locator('#r-contact-form [type=submit]').click();await page.getByText('预约申请已提交。').waitFor();assert.equal(consultationKeys[0],consultationKeys[1]);assert.deepEqual(errors,[]);
 const layouts=[];for(const lang of ['', '-en'])for(const mode of ['simple','complex'])for(const width of [320,768,1440]){
  deletionMode=mode;deletionRequest=null;await page.setViewportSize({width,height:1000});await page.goto(`http://127.0.0.1/pages/profile${lang}.html`);await page.getByRole('button',{name:lang?(mode==='simple'?'Delete account':'Delete account'):(mode==='simple'?'删除账号':'删除账号'),exact:true}).click();
  assert.equal(await page.locator('dialog').evaluate(node=>getComputedStyle(node).borderRadius),'16px','Real dialog CSS must load');
  assert.equal(await page.locator('.r-brand img').evaluate(node=>node.complete&&node.naturalWidth>0),true,'Brand asset must render');
  for(const scale of [1,2]){
   if(scale===2)await page.locator('dialog,dialog *').evaluateAll(nodes=>{const sizes=nodes.map(node=>parseFloat(getComputedStyle(node).fontSize));nodes.forEach((node,i)=>node.style.fontSize=sizes[i]*2+'px');});
   const bounds=await page.locator('dialog').evaluate(node=>({scroll:node.scrollWidth,client:node.clientWidth,left:node.getBoundingClientRect().left,right:node.getBoundingClientRect().right}));assert.ok(bounds.scroll<=bounds.client+1,JSON.stringify({lang,mode,width,scale,bounds}));assert.ok(bounds.left>=0&&bounds.right<=width+1);const overflow=await page.evaluate(()=>Array.from(document.querySelectorAll('body *')).filter(node=>node.getBoundingClientRect().right>innerWidth+1).slice(0,8).map(node=>({tag:node.tagName,class:node.className,right:node.getBoundingClientRect().right})));assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,JSON.stringify({lang,mode,width,scale,overflow}));
   fs.mkdirSync(path.join(__dirname,'../tmp/account-deletion-ui'),{recursive:true});await page.screenshot({path:path.join(__dirname,`../tmp/account-deletion-ui/${lang||'zh'}-${mode}-${width}-${scale}x.png`)});layouts.push({language:lang||'zh',mode,width,textScale:scale});
  }
  await page.keyboard.press('Escape');assert.equal(await page.locator('dialog').count(),0);
 }
 profile={display_name:'演示用户',email:'demo@example.test',avatar_url:null};session.user.display_name='演示用户';prefs={product_updates:true};deletionMode='closed';deletionRequest=null;
 devices=[{id:'current',client:'Chrome',location:'未知',ip:'192.0.2.10',created_at:'2026-10-10T09:00:00+08:00',is_current:true},{id:'other',client:'Safari',location:'未知',ip:'192.0.2.11',created_at:'2026-10-09T14:30:00+08:00',is_current:false}];
 const capture=path.join(__dirname,'../tmp/account-settings-ui');fs.mkdirSync(capture,{recursive:true});
 for(const lang of ['', '-en'])for(const width of [320,768,1113,1440]){
  await page.setViewportSize({width,height:1413});await page.goto(`http://127.0.0.1/pages/profile${lang}.html`);await page.locator('input[role=switch]').waitFor();
  assert.equal(await page.getByText(lang?'Back to website':'返回官网',{exact:true}).count(),0);
  assert.equal(await page.locator('#r-menu a:not(.mobile-download)').count(),5);assert.equal(await page.locator('#r-menu .mobile-download').count(),1);assert.equal(await page.locator('input[role=switch]').isChecked(),true);
  assert.equal(await page.locator('#r-menu .mobile-download').getAttribute('href'),`download${lang}.html`);
  if(width===320){await page.locator('#r-menu-toggle').click();assert.equal(await page.locator('#r-menu .mobile-download').isVisible(),true);await page.keyboard.press('Escape');}
  if(width===1440)assert.equal(await page.locator('#r-menu .mobile-download').isVisible(),false);
  await page.getByRole('button',{name:lang?'Next page':'下一页',exact:true}).click();await page.getByRole('cell',{name:'Safari',exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`Profile overflow ${lang}/${width}`);
  if(width===320){const tools=await page.locator('.r-tools').evaluate(node=>node.getBoundingClientRect().height);assert.ok(tools<=48,'Mobile header controls stay on one line');}
  if(width===1440){const box=await page.locator('.profile-information .profile-row-value').evaluateAll(nodes=>nodes.map(node=>node.getBoundingClientRect().right));assert.ok(Math.max(...box)-Math.min(...box)<1,'Profile controls share the same right edge');const menuBox=await page.locator('#r-menu').boundingBox();assert.ok(Math.abs(menuBox.x+menuBox.width/2-width/2)<1,'Original navigation stays centered');}
  if(width>=1113)await page.locator('.profile-device-menu').hover();
  await page.screenshot({path:path.join(capture,`profile-${lang?'en':'zh'}-${width}.png`),fullPage:true});await page.mouse.move(0,0);
  await page.getByRole('button',{name:lang?'Reset password':'重置密码',exact:true}).click();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`Reset dialog overflow ${lang}/${width}`);await page.screenshot({path:path.join(capture,`reset-${lang?'en':'zh'}-${width}.png`)});await page.keyboard.press('Escape');
 }
 const usageWrites=writes.length;
 for(const lang of ['', '-en'])for(const width of [320,768,1440]){
  await page.setViewportSize({width,height:1100});await page.goto(`http://127.0.0.1/pages/profile${lang}.html#usage`);await page.locator('.usage-records tbody tr').first().waitFor();
  assert.equal(await page.locator('.usage-records tbody tr').count(),2);assert.equal(await page.locator('.usage-chart-top strong').textContent(),'20 Credits');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`Usage overflow ${lang}/${width}`);
  await page.locator('[data-range="1"]').click();assert.equal(await page.locator('.usage-chart-top strong').textContent(),'0 Credits');await page.getByRole('cell',{name:lang?'No data.':'暂无数据。',exact:true}).waitFor();
  await page.locator('[data-range="7"]').click();await page.locator('[data-tab="obtained"]').click();assert.equal(await page.locator('.usage-records tbody tr').count(),1);await page.getByRole('cell',{name:'100',exact:true}).waitFor();
  await page.locator('[data-category="cloud"]').click();await page.getByRole('cell',{name:lang?'No data.':'暂无数据。',exact:true}).waitFor();await page.locator('[data-category="model"]').click();await page.locator('[data-tab="used"]').click();
  await page.getByLabel(lang?'Resource type':'资源类型',{exact:true}).selectOption('purchased');assert.equal(await page.locator('.usage-chart-top strong').textContent(),'0 Credits');await page.getByLabel(lang?'Resource type':'资源类型',{exact:true}).selectOption('all');
  await page.locator('.usage-date-menu summary').click();const dates=page.locator('.usage-date-panel input');await dates.nth(0).fill('1900-01-01');await page.locator('.usage-date-panel button').click();await page.getByText(lang?'Choose a valid date range within the past year.':'请选择近一年内有效的日期范围。',{exact:true}).waitFor();await page.locator('.usage-date-menu summary').click();
  await page.getByRole('button',{name:lang?'Refresh':'刷新',exact:true}).click();await page.getByText(lang?'Example data refreshed. No actual charges.':'示例数据已刷新，不产生实际扣费。',{exact:true}).waitFor();
  assert.equal(writes.length,usageWrites,'Usage filters and refresh never write real billing data');
  await page.evaluate(()=>{document.activeElement?.blur();scrollTo({top:0,behavior:'instant'});});
  await page.screenshot({path:path.join(capture,`usage-${lang?'en':'zh'}-${width}.png`),fullPage:true});
 }
 authPhase=true;session.authenticated=false;
 const authCapture=path.join(__dirname,'../tmp/auth-implementation/ui');fs.mkdirSync(authCapture,{recursive:true});
 const waitReady=()=>page.waitForFunction(()=>!document.querySelector('#auth-verify').disabled);
 const enterDetails=async(register=false)=>{await page.locator('#auth-contact').fill('fixture@example.test');if(register){await page.locator('#auth-first-name').fill('Fixture');await page.locator('#auth-agreement').check();}await page.locator('#auth-verify').click();await page.locator('#auth-password').waitFor({state:'visible'});};
 const prove=async()=>{await page.locator('#auth-captcha').click();await page.locator('[data-auth-step="code"]').waitFor({state:'visible'});await waitReady();};
 const captchaNotice=async(lang)=>{
  const step=page.locator('[data-auth-step="captcha"]');
  assert.equal(await step.locator('input[type="checkbox"]').count(),0,'CAPTCHA adds no checkbox');
  assert.equal(await page.locator('#auth-captcha-notice').isVisible(),true);
  assert.ok((await page.locator('#auth-captcha-notice').innerText()).includes(lang?'Tencent Cloud':'腾讯云'));
  assert.equal(await page.locator('#auth-captcha').getAttribute('aria-describedby'),'auth-captcha-notice');
  const link=page.locator('#auth-captcha-notice a');
  assert.equal(await link.getAttribute('href'),'https://rule.tencent.com/rule/202402210001');
  assert.equal(await link.getAttribute('target'),'_blank');
  assert.ok((await link.getAttribute('rel')).includes('noopener'));
  assert.equal(await page.locator('.auth-legal a').count(),2,'Keep the existing terms and privacy footer');
  assert.equal(await page.locator('#auth-captcha').isEnabled(),true,'Notice does not add a checkbox gate');
 };
 const paste=async(value)=>page.evaluate(value=>{const data=new DataTransfer();data.setData('text',value);document.querySelector('[data-code-index="0"]').dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));},value);
 const captureAuth=async(label,lang,width,scale)=>{assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,'Auth overflow '+label+'/'+lang+'/'+width+'/'+scale);assert.equal(await page.locator('.auth-card').evaluate(node=>getComputedStyle(node).borderRadius),'16px');await page.screenshot({path:path.join(authCapture,label+'-'+(lang?'en':'zh')+'-'+width+'-'+scale+'x.png'),fullPage:true});};
 let authLayouts=0;
 for(const lang of ['', '-en'])for(const width of [320,390,768,1440])for(const scale of [1,2]){
  const loadsBeforePage=captchaLoads;
  await page.setViewportSize({width,height:1100});await page.goto('http://127.0.0.1/pages/auth'+lang+'.html?mode=register');await waitReady();
  assert.equal(await page.locator('#auth-retry').isVisible(),false,'Healthy ready must not show connection recovery');
  assert.equal(await page.locator('#auth-agreement').isChecked(),false);
  assert.equal(await page.locator('.billing-nav,.r-nav').count(),0);
  assert.equal(await page.locator('.auth-card').evaluate(node=>Math.round(node.getBoundingClientRect().width)),width<488?width-40:440);
  assert.equal(await page.locator('#auth-contact').evaluate(node=>node.getBoundingClientRect().height>=44),true);
  assert.equal(await page.locator('#auth-verify').evaluate(node=>getComputedStyle(node).backgroundColor),'rgb(174, 200, 226)');
  assert.equal(await page.locator('#auth-google img').evaluate(node=>node.complete&&node.naturalWidth>0),true,'Actual Google asset must load');
  assert.ok((await page.locator('[data-legal="terms"] a').first().getAttribute('href')).endsWith('/legal/nonproduction-terms'));
  if(scale===2)await page.locator('#auth-root,#auth-root *').evaluateAll(nodes=>{const sizes=nodes.map(node=>parseFloat(getComputedStyle(node).fontSize));nodes.forEach((node,i)=>node.style.fontSize=sizes[i]*2+'px');});
  await captureAuth('register-details',lang,width,scale);authLayouts++;
  await enterDetails(true);assert.equal(await page.locator('#auth-password').getAttribute('autocomplete'),'new-password');assert.equal(await page.locator('#auth-password').getAttribute('maxlength'),null);assert.equal(await page.locator('#auth-username').inputValue(),'fixture@example.test');
  await captureAuth('register-password',lang,width,scale);authLayouts++;
  assert.equal(await page.locator('#auth-reveal').evaluate(node=>node.scrollWidth<=node.clientWidth),true,'Scaled reveal text must fit its button');
  assert.equal(await page.locator('#auth-reveal').evaluate(node=>node.getBoundingClientRect().right<=node.parentElement.getBoundingClientRect().right),true,'Reveal stays inside password field');
  await page.locator('#auth-password').fill('Violet!431');await page.locator('#auth-verify').click();await page.locator('#auth-captcha').waitFor({state:'visible'});await waitReady();
  assert.equal(await page.locator('#auth-password').inputValue(),'');
  await captchaNotice(lang);
  assert.equal(captchaLoads,loadsBeforePage,'Entering registration and CAPTCHA step must not load the supplier SDK');
  assert.equal(await page.evaluate(()=>typeof window.TencentCaptcha),'undefined');
  await captureAuth('register-captcha',lang,width,scale);authLayouts++;
  await prove();assert.equal(await page.locator('#auth-resend').isDisabled(),true);assert.equal(await page.locator('[data-code-index="0"]').evaluate(node=>node===document.activeElement),true);
  assert.equal(captchaLoads,loadsBeforePage+1,'Load the supplier SDK only after Start verification is clicked');
  await captureAuth('register-code',lang,width,scale);authLayouts++;
  assert.equal(await page.locator('[data-code-index="0"]').evaluate(node=>getComputedStyle(node).fontSize),scale===2?'44px':'22px','OTP digits participate in actual 200% text scaling');
  await page.locator('#auth-back').click();await page.locator('[data-auth-step="details"]').waitFor({state:'visible'});
  assert.equal(await page.locator('#auth-password').inputValue(),'');assert.equal(await page.locator('#auth-agreement').isChecked(),false);
  await page.locator('[data-auth-mode]').click();
  await captureAuth('login-details',lang,width,scale);authLayouts++;
  await enterDetails(false);
  assert.equal(await page.locator('#auth-password').getAttribute('autocomplete'),'current-password');
  await captureAuth('login-password',lang,width,scale);await contentBounds();authLayouts++;
  loginAccepted=true;await page.locator('#auth-password').fill('A historical password only');await page.locator('#auth-verify').click();await page.locator('#auth-captcha').waitFor({state:'visible'});await waitReady();
  await captchaNotice(lang);
  await captureAuth('login-captcha',lang,width,scale);authLayouts++;
  await page.locator('#auth-back').click();loginAccepted=false;await enterDetails(false);
  await page.locator('#auth-reset-link').click();
  await captureAuth('recovery-details',lang,width,scale);authLayouts++;
  await page.locator('#auth-verify').click();await page.locator('#auth-captcha').waitFor({state:'visible'});await waitReady();
  await captchaNotice(lang);
  await captureAuth('recovery-captcha',lang,width,scale);authLayouts++;
  await prove();await captureAuth('recovery-code',lang,width,scale);authLayouts++;
  await paste('123456');await page.locator('#auth-password').waitFor({state:'visible'});await waitReady();
  assert.equal(await page.locator('input[autocomplete="new-password"]').count(),1);
  await captureAuth('recovery-password',lang,width,scale);authLayouts++;
  await page.locator('#auth-back').click();
 }
 for(const lang of ['', '-en']){
  session.authenticated=true;const authenticatedWrites=writes.length;
  await page.goto('http://127.0.0.1/pages/auth'+lang+'.html?mode=reset');
  await page.locator('#auth-feedback').filter({hasText:lang?'You are signed in. Reset your password in profile settings.':'你已登录，请在个人设置中重置密码。'}).waitFor();
  assert.equal(await page.locator('#auth-verify').isDisabled(),true);
  assert.equal(await page.getByRole('link',{name:lang?'Open profile settings':'打开个人设置',exact:true}).getAttribute('href'),'profile'+lang+'.html');
  assert.equal(writes.length,authenticatedWrites,'Authenticated reset must not start anonymous recovery or auto logout');session.authenticated=false;
  await page.goto('http://127.0.0.1/pages/auth'+lang+'.html?mode=password&return=https%3A%2F%2Foutside.invalid');await waitReady();await enterDetails();
  await page.locator('#auth-password').fill('A historical password only');await page.locator('#auth-verify').click();await page.getByText(lang?'The email or password is incorrect. Retry or reset your password.':'邮箱或密码不正确，请重试或找回密码。',{exact:true}).waitFor();assert.equal(await page.locator('#auth-password').inputValue(),'');
  await page.locator('#auth-reset-link').click();await page.locator('#auth-verify').click();await page.locator('#auth-captcha').waitFor({state:'visible'});await waitReady();
  await page.evaluate(()=>window.captchaFixtureResult={ret:0,ticket:'trerror_fixture',randstr:'fixture'});
  const before=writes.filter(path=>path==='/api/auth/captcha').length;await page.locator('#auth-captcha').click();await page.getByText(lang?'The security challenge is unavailable. Retry or come back later.':'人机验证暂不可用，请重试或稍后再来。',{exact:true}).waitFor();assert.equal(writes.filter(path=>path==='/api/auth/captcha').length,before);
  await page.evaluate(()=>window.captchaFixtureResult={ret:2});await page.locator('#auth-captcha').click();await page.getByText(lang?'Security verification cancelled. You can try again.':'已取消人机验证，可以重新尝试。',{exact:true}).waitFor();
  await page.evaluate(()=>delete window.captchaFixtureResult);captchaReject=true;await page.locator('#auth-captcha').click();await page.getByText(lang?'The security challenge was not accepted. Try again.':'人机验证未通过，请重新验证。',{exact:true}).waitFor();captchaReject=false;await prove();
  await page.locator('[data-code-index="0"]').fill('1');await page.locator('[data-code-index="1"]').press('ArrowLeft');assert.equal(await page.locator('[data-code-index="0"]').evaluate(node=>node===document.activeElement),true);await page.keyboard.press('ArrowRight');await page.keyboard.press('Backspace');assert.equal(await page.locator('[data-code-index="0"]').inputValue(),'');
  rejectCode=true;const submitted=codeVerifications;await paste('123456');await page.locator('#auth-code-error').filter({hasText:lang?'incorrect':'不正确'}).waitFor();assert.equal(codeVerifications,submitted+1,'Duplicate CAPTCHA callback and paste generate only one verify');assert.deepEqual(await page.locator('[data-code-index]').evaluateAll(nodes=>nodes.map(n=>n.value)),['','','','','','']);rejectCode=false;await paste('123456');await page.locator('#auth-password').waitFor({state:'visible'});await waitReady();
  const invalid=['Aa9!pqr','Aa9!pqrstuvwxyz012','aa9!pqrs','AA9!PQRS','Aa!!pqrs','Aa99pqrs','Aa9!p qr','Aa9!p\tqr','Aa9!p\nqr','Aa9!p\u00a0qr','Aa9!p\x7fqr','Aa9!中文qr','Aa9!😀pqr'];
  for(const value of invalid){await page.locator('#auth-password').fill(value);const entered=await page.locator('#auth-password').inputValue();if(!/[\r\n]/.test(value))assert.equal(entered,value);const count=writes.length;await page.locator('#auth-verify').click();assert.equal(writes.length,count);assert.equal(await page.locator('#auth-password').inputValue(),entered);assert.equal(await page.locator('#auth-password').getAttribute('aria-invalid'),'true');}
  await page.locator('#auth-password').fill('Violet!431');await page.getByRole('button',{name:lang?'Show password':'显示密码',exact:true}).click();assert.equal(await page.locator('#auth-password').getAttribute('type'),'text');await page.getByRole('button',{name:lang?'Hide password':'隐藏密码',exact:true}).click();await page.locator('#auth-verify').click();await page.getByText(lang?'Password reset. All devices are signed out. Sign in with your new password.':'密码已重置，全部设备已退出。请使用新密码登录。',{exact:true}).waitFor();assert.equal(session.authenticated,false);
  loginAccepted=true;await enterDetails();await page.locator('#auth-password').fill('A historical password only');await page.locator('#auth-verify').click();await page.locator('#auth-captcha').waitFor({state:'visible'});await waitReady();await page.locator('#auth-captcha').click();await page.waitForURL('**/index'+lang+'.html');loginAccepted=false;
  for(const repair of ['VALIDCODE','']){
   await page.goto('http://127.0.0.1/pages/auth'+lang+'.html?mode=register');await waitReady();await page.locator('#auth-invite').fill('INVALID');await enterDetails(true);await page.locator('#auth-password').fill('Violet!431');await page.locator('#auth-verify').click();await page.locator('#auth-captcha').waitFor({state:'visible'});await waitReady();await prove();rejectInvite=true;await paste('123456');await page.locator('#auth-invite-final-error').filter({hasText:lang?'Invalid invitation':'无效邀请码，请检查'}).waitFor();
   const startCount=registrationStarts,proof=authFlow.flow_id;await page.locator('#auth-invite-final').fill(repair);rejectInvite=false;await page.locator('#auth-verify').click();await page.waitForURL('**/index'+lang+'.html');assert.equal(registrationStarts,startCount);assert.equal(authFlow.flow_id,proof);
  }
  await page.goto('http://127.0.0.1/pages/auth'+lang+'.html?mode=register');await waitReady();await enterDetails(true);await page.locator('#auth-password').fill('Violet!431');await page.locator('#auth-verify').click();await page.locator('#auth-captcha').waitFor({state:'visible'});await waitReady();cancelReject=true;await page.locator('#auth-back').click();await page.locator('#auth-feedback').filter({hasText:lang?'Cancellation could not':'取消未能确认'}).waitFor();assert.equal(await page.locator('[data-auth-step="captcha"]').isVisible(),true);assert.equal(await page.locator('#auth-captcha').isDisabled(),true,'Unknown cancellation blocks reuse of old proof');const uncertainWrites=writes.length;await page.locator('#auth-captcha').evaluate(node=>node.click());assert.equal(writes.length,uncertainWrites);cancelReject=false;cancelInvalid=true;await page.evaluate(()=>{const now=Date.now;Date.now=()=>now()+301000;});await page.locator('#auth-feedback').filter({hasText:lang?'Verification expired':'验证已过期'}).waitFor();await page.locator('#auth-back').click();await page.locator('[data-auth-step="details"]').waitFor({state:'visible'});await page.locator('#auth-feedback').filter({hasText:lang?'Previous verification is invalid':'旧验证已失效'}).waitFor();assert.equal(await page.locator('#auth-password').inputValue(),'');assert.equal(await page.locator('#auth-agreement').isChecked(),false);cancelInvalid=false;
  await page.locator('#auth-google').click();await page.getByText(lang?'Google authentication is not available. Use email.':'Google 认证暂未接通，请使用邮箱。',{exact:true}).waitFor();await page.locator('#auth-method').click();await page.getByText(lang?'Phone authentication is not available. Choose email.':'手机号认证暂未接通，请选择邮箱。',{exact:true}).waitFor();assert.equal(await page.locator('#auth-verify').isDisabled(),true);await page.locator('#auth-method').click();
  const terms=session.capabilities.registration_terms_url;session.capabilities.registration_terms_url=null;await page.reload();await page.getByText(lang?'Registration is unavailable until the legal documents are published. Try again later.':'正式协议尚未发布，注册暂不可用。请稍后重试。',{exact:true}).waitFor();assert.equal(await page.locator('#auth-agreement').isChecked(),false);assert.equal(await page.locator('#auth-agreement').isDisabled(),true);assert.equal(await page.locator('#auth-verify').isDisabled(),true);session.capabilities.registration_terms_url=terms;
 }
 for(const name of reviewNames)await reviewCase(name);
 assert.deepEqual(errors,[]);console.log(JSON.stringify({reviewCases:reviewNames.length,result:'Website account, usage, stepped authentication and consultation browser fixtures passed',deletionLayouts:layouts.length,profileLayouts:8,usageLayouts:6,authLayouts,passwordLanguages:2,captchaSupplierLoads:captchaLoads,pageErrors:errors.length,screenshots:'tmp/auth-implementation/ui'}));
 }catch(error){console.error({pageErrors:errors,status:await page.locator('[role=status]').allTextContents(),writes});throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
