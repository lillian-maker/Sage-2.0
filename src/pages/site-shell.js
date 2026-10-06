(() => {
 'use strict';
 const isHome=document.body.classList.contains('v1'), en=document.documentElement.lang==='en';
 const pageURL=name=>(isHome?'pages/':'')+name+(en?'-en':'')+'.html';
 const text=(zh,english)=>en?english:zh;
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
   panel.append(settings,logout,status);menu.append(summary,panel);link.replaceWith(menu);
  });
 }
 window.addEventListener('sage:session',event=>accountMenu(event.detail));
 if(window.sageSession)accountMenu(window.sageSession);
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
   consult.innerHTML=`<button type="button" class="sage-consult-close" aria-label="${text('关闭','Close')}">×</button><h2 id="sage-consult-title">${text('咨询和演示','Consultation and demo')}</h2><form><label>${text('公司 / 品牌','Company / brand')}<input name="company" required autocomplete="organization" maxlength="100"></label><div class="sage-consult-pair"><label>${text('访客手机号','Phone number')}<input name="phone" type="tel" required autocomplete="tel" maxlength="30"></label><label>${text('工作邮箱（选填）','Work email (optional)')}<input name="email" type="email" autocomplete="email" maxlength="254"></label></div><label>${text('想解决的经营问题','Your business question')}<textarea name="goal" rows="3" required maxlength="2000"></textarea></label><p class="sage-consult-note">${text('联系方式仅用于业务咨询。当前预览未接通接收服务，填写内容不会发送或保存。','Contact details are for business inquiries only. The receiving service is not connected; your input is not sent or saved.')}</p><button type="submit" class="sage-consult-submit">${text('提交预约','Submit request')}</button><p role="status" class="sage-consult-status"></p></form>`;
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
 }
 const homeConsultation=document.querySelector('#r-contact-form');if(homeConsultation)wireConsultation(homeConsultation);
 document.querySelectorAll('#hero-actions a[href*="download"]').forEach(link=>{link.setAttribute('aria-disabled','true');link.addEventListener('click',event=>event.preventDefault());});
 function alignNavigation(){
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
 document.querySelectorAll('.mobile-download,.r-tools a.r-button.small[href="download.html"]').forEach(link=>{link.textContent='咨询和演示';link.href='../index.html#contact';});
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
