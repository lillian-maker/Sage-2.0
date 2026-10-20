(() => {
 'use strict';
 const isHome=document.body.classList.contains('v1'), en=document.documentElement.lang==='en';
 const pageURL=name=>(isHome?'pages/':'')+name+(en?'-en':'')+'.html';
 const text=(zh,english)=>en?english:zh;
 let notificationUser=null,notificationCount=null,notificationVersion=0;
 function notificationBell(){
  document.querySelectorAll('.cp-tools,.r-tools,.nav-tools,.nav-actions').forEach(tools=>{
   let link=tools.querySelector('.sage-notification-bell');
   if(!notificationUser){link?.remove();return;}
   if(!link){link=document.createElement('a');link.className='sage-notification-bell';link.href=pageURL('notifications');link.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M9 21h6"/></svg><span class="sage-unread-count" aria-hidden="true"></span>';tools.prepend(link);}
   const label=notificationCount===null?text('通知状态无法读取，打开通知中心重试','Notification status unavailable. Open notifications to retry'):text(`通知，${notificationCount}条未读`,`Notifications, ${notificationCount} unread`);
   link.setAttribute('aria-label',label);link.title=label;
   const count=link.querySelector('span'),value=notificationCount===null?'?':notificationCount>99?'99+':String(notificationCount);
   if(count.textContent!==value)count.textContent=value;
   count.hidden=notificationCount===0;
  });
 }
 async function readNotificationCount(){
  const user=notificationUser,version=++notificationVersion;notificationCount=null;notificationBell();if(!user)return;
  try{const response=await fetch('/api/account/notifications/summary',{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'},signal:AbortSignal.timeout(10000)});if(!response.ok)throw Error('NOTIFICATIONS_UNAVAILABLE');const data=await response.json();if(String(data.user_id)!==user||!Number.isSafeInteger(data.unread_count)||data.unread_count<0)throw Error('INVALID_UNREAD_COUNT');if(version===notificationVersion&&user===notificationUser){notificationCount=data.unread_count;notificationBell();}}
  catch(_){if(version===notificationVersion){notificationCount=null;notificationBell();}}
 }
 window.addEventListener('sage:session',event=>{const next=event.detail?.authenticated&&event.detail.user?.id?String(event.detail.user.id):null;if(next!==notificationUser){notificationUser=next;readNotificationCount();}notificationBell();});
 window.addEventListener('sage:notifications-updated',readNotificationCount);
 const bytes=value=>Uint8Array.from(atob(value.replace(/-/g,'+').replace(/_/g,'/').padEnd(Math.ceil(value.length/4)*4,'=')),char=>char.charCodeAt(0));
 const base64=value=>btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
 async function securityRequest(path,body,csrf){
  const response=await fetch('/api/account/'+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json',...(body===undefined?{}:{'Content-Type':'application/json','X-CSRFToken':csrf})},...(body===undefined?{}:{body:JSON.stringify(body)}),signal:AbortSignal.timeout(10000)});
  if(!response.headers.get('content-type')?.includes('application/json'))throw Error('SECURITY_UNAVAILABLE');const result=await response.json();if(!response.ok)throw Error(result.error?.code||'SECURITY_UNAVAILABLE');return result;
 }
 async function passkey(kind,csrf,label){
  if(!window.isSecureContext||!navigator.credentials)throw Error('PASSKEY_UNAVAILABLE');
  const data=await securityRequest('passkeys/'+kind+'-options',kind==='registration'?{label}:{},csrf),options=data.public_key;
  if(typeof data.challenge_id!=='string'||!options||typeof options.challenge!=='string')throw Error('INVALID_PASSKEY_OPTIONS');
  const publicKey={...options,challenge:bytes(options.challenge)};
  if(options.user)publicKey.user={...options.user,id:bytes(options.user.id)};
  for(const field of ['allowCredentials','excludeCredentials'])if(options[field])publicKey[field]=options[field].map(item=>({...item,id:bytes(item.id)}));
  const credential=kind==='registration'?await navigator.credentials.create({publicKey}):await navigator.credentials.get({publicKey});if(!credential)throw Error('PASSKEY_CANCELLED');
  const response={};for(const field of ['clientDataJSON','attestationObject','authenticatorData','signature','userHandle'])if(field in credential.response)response[field]=credential.response[field]===null?null:base64(credential.response[field]);
  if(credential.response.getTransports)response.transports=credential.response.getTransports();
  const serialized={id:credential.id,rawId:base64(credential.rawId),type:credential.type,response,clientExtensionResults:credential.getClientExtensionResults()};
  return securityRequest(kind==='registration'?'passkeys/register':'passkeys/verify',{challenge_id:data.challenge_id,credential:serialized},csrf);
 }
 window.sageRegisterPasskey=(label,csrf)=>passkey('registration',csrf,label);
 window.sageConfirmPasskey=async csrf=>{const result=await passkey('authentication',csrf);if(result.method!=='passkey'||!Number.isFinite(Date.parse(result.verified_until))||Date.parse(result.verified_until)<=Date.now()||Date.parse(result.verified_until)>Date.now()+901000)throw Error('VERIFICATION_UNCONFIRMED');return result;};
 window.sageConfirmSensitive=async(accountId,csrf)=>{
  const security=await securityRequest('security');
  if(security.verified_method==='phone'&&security.verified_account_id===accountId&&Date.parse(security.verified_until)>Date.now())return;
  return new Promise((resolve,reject)=>{
   const dialog=document.createElement('dialog');dialog.className='profile-deletion-dialog';dialog.setAttribute('aria-labelledby','sensitive-title');
   dialog.innerHTML=`<h2 id="sensitive-title">${text('确认是你本人操作','Confirm your identity')}</h2><p>${text('请输入账号密码，并通过原手机号短信验证。验证通过后15分钟内有效，仅限当前企业。','Enter your account password and verify your original phone number. Confirmation lasts 15 minutes for this business only.')}</p><form method="dialog"><label>${text('账号密码','Account password')}<input type="password" name="password" autocomplete="current-password" required></label><label>${text('短信验证码','SMS code')}<input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6"></label><p role="status"></p><div class="profile-deletion-actions"><button type="button" data-sensitive-send>${text('获取验证码','Send code')}</button><button type="button" data-sensitive-cancel>${text('取消','Cancel')}</button><button type="submit" disabled>${text('验证并继续','Verify and continue')}</button></div></form>`;
   const form=dialog.querySelector('form'),password=form.querySelector('[name=password]'),code=form.querySelector('[name=code]'),send=form.querySelector('[data-sensitive-send]'),cancel=form.querySelector('[data-sensitive-cancel]'),submit=form.querySelector('[type=submit]'),status=form.querySelector('[role=status]');
   let challenge=null,busy=false,closed=false,expires=0,resend=0;const timer=setInterval(update,1000),previous=document.activeElement;
   function update(){send.disabled=busy||Date.now()<resend;cancel.disabled=busy;submit.disabled=busy||!challenge||Date.now()>=expires||!/^\d{6}$/.test(code.value);}
   function close(error){if(closed)return;closed=true;clearInterval(timer);window.removeEventListener('pagehide',onHide);form.reset();dialog.close();dialog.remove();previous?.focus();if(error)reject(error);else resolve();}
   const onHide=()=>close(Error('VERIFICATION_CANCELLED'));window.addEventListener('pagehide',onHide,{once:true});
   cancel.onclick=()=>close(Error('VERIFICATION_CANCELLED'));dialog.addEventListener('cancel',event=>{event.preventDefault();if(!busy)close(Error('VERIFICATION_CANCELLED'));});code.oninput=update;
   send.onclick=async()=>{if(busy||Date.now()<resend)return;password.disabled=false;password.required=true;if(!password.reportValidity())return;busy=true;update();try{const result=await securityRequest('sensitive-challenges',{account_id:accountId,password:password.value},csrf);password.value='';if(typeof result.challenge_id!=='string'||!Number.isFinite(result.expires_in)||result.expires_in<=0||!Number.isFinite(result.resend_after)||result.resend_after<0)throw Error('INVALID_CHALLENGE');password.required=false;password.disabled=true;challenge=result.challenge_id;expires=Date.now()+result.expires_in*1000;resend=Date.now()+result.resend_after*1000;status.textContent=text('请查看原手机号收到的短信验证码。','Check the SMS code sent to your original phone number.');code.focus();}catch(_){password.value='';status.textContent=text('验证请求未能确认，请核对密码、手机号和服务状态后重试。','The verification request is unconfirmed. Check your password, phone and service status before retrying.');}finally{busy=false;update();}};
   form.onsubmit=async event=>{event.preventDefault();if(busy||submit.disabled)return;busy=true;update();try{const result=await securityRequest('sensitive-verify',{challenge_id:challenge,code:code.value},csrf);if(result.method!=='phone'||result.account_id!==accountId||!Number.isFinite(Date.parse(result.verified_until))||Date.parse(result.verified_until)<=Date.now()||Date.parse(result.verified_until)>Date.now()+901000)throw Error('INVALID_VERIFICATION');close();}catch(_){code.value='';status.textContent=text('验证未完成，请重新核对验证码。未执行原操作。','Verification is incomplete. Check your code. The original action has not run.');}finally{busy=false;if(dialog.isConnected)update();}};
   document.body.append(dialog);dialog.showModal();password.focus();update();
  });
 };
 window.sagePasswordError=value=>{
  if(typeof value!=='string'||!value)return text('请输入密码。','Enter a password.');
  if(/[^\x21-\x7e]/.test(value))return /\s/.test(value)?text('密码不能包含空格、制表符或换行。','Passwords cannot contain spaces, tabs or line breaks.'):text('密码仅支持英文字母、数字和英文标点，不支持中文或 emoji。','Use English letters, digits and ASCII punctuation only; no Chinese characters or emoji.');
  if(value.length<8||value.length>16)return text('密码须为8–16位，不会截断输入。','Passwords must be 8–16 characters. Your input is not truncated.');
  const missing=[[/[A-Z]/,text('大写字母','uppercase letters')],[/[a-z]/,text('小写字母','lowercase letters')],[/[0-9]/,text('数字','digits')],[/[^A-Za-z0-9]/,text('符号','symbols')]].filter(([pattern])=>!pattern.test(value)).map(([,label])=>label);
  return missing.length?text('密码须同时包含大写字母、小写字母、数字和符号；缺少：'+missing.join('、')+'。','Include uppercase letters, lowercase letters, digits and symbols; missing: '+missing.join(', ')+'.'):'';
 };
 function accountMenu(session){
  if(!session.authenticated||!session.user?.id)return;
  document.querySelectorAll('[data-account-entry],.cp-account,.trial-account').forEach(link=>{
   if(link.closest('.mobile-nav'))return;
   const menu=document.createElement('details');menu.className='sage-account-menu';
   const summary=document.createElement('summary');summary.textContent=session.user.display_name||session.user.masked_identifier||text('我的账户','My account');
   const panel=document.createElement('div');panel.className='sage-account-panel';
   const settings=document.createElement('a');settings.href=pageURL('profile');settings.textContent=text('个人设置','Personal settings');
   const logout=document.createElement('button');logout.type='button';logout.textContent=text('退出登录','Sign out');
   const status=document.createElement('p');status.setAttribute('role','status');status.hidden=true;
   logout.addEventListener('click',async()=>{
    logout.disabled=true;status.hidden=true;
    try{
     if(!session.csrf_token)throw Error('SESSION_UNAVAILABLE');
     const response=await fetch('/api/auth/logout',{method:'POST',credentials:'same-origin',headers:{Accept:'application/json','Content-Type':'application/json','X-CSRFToken':session.csrf_token},body:'{}',signal:AbortSignal.timeout(10000)});
     if(!response.ok||(await response.json()).ok!==true)throw Error('LOGOUT_UNCONFIRMED');
     const check=await fetch('/api/auth/session',{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'},signal:AbortSignal.timeout(10000)});
     if(!check.ok||(await check.json()).authenticated!==false)throw Error('LOGOUT_UNCONFIRMED');
     location.reload();
    }catch(_){status.hidden=false;status.textContent=text('退出未能确认，请重试。','Sign-out could not be confirmed. Try again.');logout.disabled=false;}
   });
   panel.append(settings,logout,status);menu.append(summary,panel);link.replaceWith(menu);wireAccountHover(menu);
  });
 }
 window.addEventListener('sage:session',event=>accountMenu(event.detail));
 if(window.sageSession)accountMenu(window.sageSession);
 function wireAccountHover(menu){
  if(menu.dataset.hoverReady)return;menu.dataset.hoverReady='true';
  const summary=menu.querySelector('summary');summary.setAttribute('aria-expanded',String(menu.open));
  summary.addEventListener('click',event=>{if(event.pointerType==='mouse'){event.preventDefault();menu.open=true;}});
  menu.addEventListener('toggle',()=>summary.setAttribute('aria-expanded',String(menu.open)));
  menu.addEventListener('pointerenter',event=>{if(event.pointerType==='mouse')menu.open=true;});
  menu.addEventListener('pointerleave',event=>{if(event.pointerType==='mouse'&&!menu.querySelector('.sage-account-panel,.account-dropdown')?.contains(document.activeElement))menu.open=false;});
  menu.addEventListener('focusout',event=>{if(event.relatedTarget&&!menu.contains(event.relatedTarget))menu.open=false;});
 }
 document.addEventListener('click',event=>document.querySelectorAll('.sage-account-menu[open],.nav-account[open]').forEach(menu=>{if(!menu.contains(event.target))menu.open=false;}));
 document.addEventListener('keydown',event=>{if(event.key==='Escape')document.querySelectorAll('.sage-account-menu[open],.nav-account[open]').forEach(menu=>{menu.open=false;menu.querySelector('summary').focus();});});
 if(isHome){
  const nav=document.querySelector('.nav'),hero=document.querySelector('.hero');let pending=false;
  function updateNav(){nav.classList.toggle('nav-light',hero.getBoundingClientRect().bottom<=nav.offsetHeight);pending=false;}
  addEventListener('scroll',()=>{if(!pending){pending=true;requestAnimationFrame(updateNav);}},{passive:true});addEventListener('resize',updateNav);updateNav();
 }
 let consult,consultTrigger;
 document.addEventListener('click',event=>{
  const trigger=event.target.closest('a,button');
  if(!trigger||!['咨询和演示','Contact sales'].includes(trigger.textContent.replace(/[→↗]/g,'').trim()))return;
  if(trigger.closest('form'))return;
  event.preventDefault();event.stopImmediatePropagation();
  close();document.querySelector('.cp-links')?.classList.remove('open');document.querySelector('.cp-menu')?.setAttribute('aria-expanded','false');
  const homeMenu=document.querySelector('.mobile-nav');if(homeMenu)homeMenu.hidden=true;document.querySelector('.menu-toggle')?.setAttribute('aria-expanded','false');
  if(isHome){document.querySelector('#contact').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});return;}
  consultTrigger=trigger;
  if(!consult){
   consult=document.createElement('dialog');consult.className='sage-consult-dialog';consult.setAttribute('aria-labelledby','sage-consult-title');
   consult.innerHTML=`<button type="button" class="sage-consult-close" aria-label="${text('关闭','Close')}">×</button><h2 id="sage-consult-title">${text('咨询和演示','Consultation and demo')}</h2><form method="dialog"><label>${text('公司 / 品牌','Company / brand')}<input name="company" required autocomplete="organization" maxlength="100"></label><div class="sage-consult-pair"><label>${text('访客手机号','Phone number')}<input name="phone" type="tel" required autocomplete="tel" maxlength="30"></label><label>${text('工作邮箱（选填）','Work email (optional)')}<input name="email" type="email" autocomplete="email" maxlength="254"></label></div><label>${text('想解决的经营问题','Your business question')}<textarea name="goal" rows="3" required maxlength="2000"></textarea></label><p class="sage-consult-note">${text('联系方式仅用于业务咨询。当前预览未接通接收服务，填写内容不会发送或保存。','Contact details are for business inquiries only. The receiving service is not connected; your input is not sent or saved.')}</p><button type="submit" class="sage-consult-submit" disabled>${text('提交预约','Submit request')}</button><p role="status" class="sage-consult-status"></p></form>`;
   document.body.append(consult);
   consult.querySelector('.sage-consult-close').onclick=()=>consult.close();
   consult.addEventListener('click',e=>{if(e.target===consult){const r=consult.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)consult.close();}});
   consult.addEventListener('close',()=>{consult.querySelector('form').reset();consult.querySelector('[role=status]').textContent='';consultTrigger?.focus();});
   wireConsultation(consult.querySelector('form'));
  }
  consult.showModal();
 },true);
 function wireConsultation(form){
  let requestKey=null,submittedPayload=null,busy=false;
  const status=form.querySelector('[role=status]'),button=form.querySelector('[type=submit]');
  const notice=form.querySelector('.sage-consult-note,.contact-data-notice');
  const phone=form.querySelector('[name=phone]');if(phone){phone.placeholder='+86 138 0000 0000';phone.setAttribute('aria-description',text('请包含国际区号，例如 +86。','Include the country calling code, for example +86.'));}
  if(notice)notice.textContent=text('联系方式仅用于处理本次咨询。已完成咨询的联系方式和问题内容将在完成后90天清除。','Contact details are used to handle this inquiry. Contact details and question content are cleared 90 days after the inquiry is completed.');
  form.addEventListener('submit',async event=>{
   event.preventDefault();if(busy||!form.reportValidity())return;
   const fields=new FormData(form),payload={company:fields.get('company'),phone:fields.get('phone'),email:fields.get('email')||'',question:fields.get('goal')||fields.get('question')};
   const serialized=JSON.stringify(payload);
   if(serialized!==submittedPayload){requestKey=crypto.randomUUID();submittedPayload=serialized;}
   busy=true;button.disabled=true;status.textContent=text('正在提交…','Submitting…');
   try{
    const sessionResponse=await fetch('/api/auth/session',{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'},signal:AbortSignal.timeout(10000)});
    if(!sessionResponse.ok)throw Error('SERVICE_UNAVAILABLE');
    const session=await sessionResponse.json();if(!session.csrf_token)throw Error('CSRF_UNAVAILABLE');
    const response=await fetch('/api/consultations',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json','Content-Type':'application/json','X-CSRFToken':session.csrf_token,'Idempotency-Key':requestKey},body:serialized,signal:AbortSignal.timeout(10000)});
    const result=await response.json();
    if(!response.ok){const error=Error(result.error?.code||'REQUEST_FAILED');error.status=response.status;throw error;}
    if(typeof result.receipt_id!=='string'||!result.receipt_id)throw Error('INVALID_RECEIPT');
    status.textContent=text('预约申请已提交。','Your request has been submitted.');form.reset();requestKey=null;submittedPayload=null;
   }catch(error){status.textContent=error.status===429?text('提交过于频繁，请稍后再试。','Too many requests. Please try again later.'):error.status===400?text('请检查填写内容后重试。','Check your input and try again.'):text('未能确认提交结果，请重试；相同内容不会重复创建申请。','Submission could not be confirmed. Retry with the same details to avoid creating a duplicate request.');}
   finally{busy=false;button.disabled=false;}
  });
  button.disabled=false;
 }
 const homeConsultation=document.querySelector('#r-contact-form');if(homeConsultation)wireConsultation(homeConsultation);
 document.querySelectorAll('#hero-actions a[href*="download"]').forEach(link=>{link.setAttribute('aria-disabled','true');link.addEventListener('click',event=>event.preventDefault());});
 function alignNavigation(){
  notificationBell();
  document.querySelectorAll('.sage-account-menu,.nav-account').forEach(wireAccountHover);
  document.querySelectorAll('.cp-nav,.r-nav,.billing-nav').forEach(header=>{
   const links=header.querySelector('.cp-links,#r-menu');if(!links||links.querySelector('.mobile-download'))return;
   const download=document.createElement('a');download.className='mobile-download';download.href=pageURL('download');download.textContent=text('下载','Download');links.append(download);
  });
  document.querySelectorAll('.cp-menu,#r-menu-toggle').forEach(button=>{if(button.dataset.iconReady)return;button.dataset.iconReady='true';button.setAttribute('aria-label',document.documentElement.lang==='en'?'Open navigation menu':'打开导航菜单');button.innerHTML='<svg viewBox="0 0 18 18" fill="none" aria-hidden="true"><path d="M2 4.5h14M2 9h14M2 13.5h14" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"></path></svg>';});
  document.querySelectorAll('.cp-tools,.r-tools,.nav-actions').forEach(tools=>{const language=tools.closest('header')?.querySelector('.sage-language');if(language&&tools.firstElementChild!==language)tools.prepend(language);});
 }
 alignNavigation();
 new MutationObserver(alignNavigation).observe(document.body,{childList:true,subtree:true});
 const trialAccount=document.querySelector('.trial-account');
 if(trialAccount){
  const en=document.documentElement.lang==='en';
  trialAccount.href='auth'+(en?'-en':'')+'.html?return='+encodeURIComponent(location.pathname+location.search+location.hash);
  fetch('/api/auth/session',{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'},signal:AbortSignal.timeout(8000)}).then(async response=>{if(!response.ok||!response.headers.get('content-type')?.includes('application/json'))throw Error('ACCOUNT_SERVICE_UNAVAILABLE');const session=await response.json();if(typeof session.authenticated!=='boolean'||(session.authenticated&&!session.user?.id))throw Error('INVALID_SESSION');trialAccount.textContent=session.authenticated?(session.user.display_name||session.user.masked_identifier||(en?'My account':'我的账户')):(en?'Sign in':'登录');window.dispatchEvent(new CustomEvent('sage:session',{detail:session}));if(session.authenticated)trialAccount.href='profile'+(en?'-en':'')+'.html';}).catch(()=>{trialAccount.textContent=en?'Sign in':'登录';trialAccount.title=en?'Sign in with email or phone':'使用邮箱或手机号验证登录';});
 }
 function close(){document.querySelector('#r-menu')?.classList.remove('open');document.querySelector('#r-menu-toggle')?.setAttribute('aria-expanded','false');}
 document.addEventListener('click',e=>{const menu=document.querySelector('#r-menu'),toggle=document.querySelector('#r-menu-toggle');if(!menu||!toggle)return;if(e.target.closest('#r-menu-toggle')){const open=!menu.classList.contains('open');menu.classList.toggle('open',open);toggle.setAttribute('aria-expanded',String(open));}else if(e.target.closest('#r-menu a')||!e.target.closest('.r-nav,.billing-nav'))close();});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&document.querySelector('#r-menu')?.classList.contains('open')){close();document.querySelector('#r-menu-toggle').focus();}});
 const language=document.querySelector('#r-language'),status=document.querySelector('#language-status');
 language?.addEventListener('click',()=>{status.hidden=!status.hidden;status.textContent='当前提供中文预览。英文内容尚未完成校对，不切换为不完整的翻译。';});
 const dialog=document.querySelector('#site-dialog');
 document.querySelectorAll('[data-legal]').forEach(b=>b.addEventListener('click',()=>{document.querySelector('#site-dialog-title').textContent=b.dataset.legal==='privacy'?'隐私说明':'使用条款';document.querySelector('#site-dialog-copy').textContent='当前为本地产品预览，不接入认证、支付、店铺或销售服务。正式条款与隐私政策将在真实服务开放前提供。演示成果只在当前浏览器会话留存，请勿提交敏感资料。';dialog.showModal();}));
 dialog?.querySelector('.dialog-close')?.addEventListener('click',()=>dialog.close());
 document.addEventListener('error',e=>{if(e.target instanceof HTMLImageElement){e.target.classList.add('image-unavailable');e.target.alt=e.target.alt||'头像暂不可用';}},true);
})();
