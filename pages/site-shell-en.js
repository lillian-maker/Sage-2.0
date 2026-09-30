(() => {
 'use strict';
 const menu=document.querySelector('#r-menu'),toggle=document.querySelector('#r-menu-toggle');
 document.querySelectorAll('.mobile-download,.r-tools a.r-button.small[href="download-en.html"]').forEach(link=>{link.textContent='Talk to us';link.href='../index-en.html#contact';});
 function close(){menu?.classList.remove('open');toggle?.setAttribute('aria-expanded','false');}
 toggle?.addEventListener('click',()=>{const opened=menu.classList.toggle('open');toggle.setAttribute('aria-expanded',String(opened));});
 menu?.addEventListener('click',e=>{if(e.target.closest('a'))close();});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&menu?.classList.contains('open')){close();toggle.focus();}});
 const language=document.querySelector('#r-language'),status=document.querySelector('#language-status');
 language?.addEventListener('click',()=>{location.href=location.pathname.replace('-en.html','.html')+location.search+location.hash;});
 const dialog=document.querySelector('#site-dialog');
 document.querySelectorAll('[data-legal]').forEach(b=>b.addEventListener('click',()=>{document.querySelector('#site-dialog-title').textContent=b.dataset.legal==='privacy'?'隐私说明':'使用条款';document.querySelector('#site-dialog-copy').textContent='当前为本地产品预览, 不接入认证, 支付, 店铺或销售服务.正式条款与隐私政策将在真实服务开放前提供.演示成果只在当前浏览器会话留存, 请勿提交敏感资料.';dialog.showModal();}));
 dialog?.querySelector('.dialog-close')?.addEventListener('click',()=>dialog.close());
 document.addEventListener('error',e=>{if(e.target instanceof HTMLImageElement){e.target.classList.add('image-unavailable');e.target.alt=e.target.alt||'头像暂不可用';}},true);
})();
