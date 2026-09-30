(() => {
'use strict';
const D=window.SAGE_CATALOG;
if(!D||!document.querySelector('.team-page'))return;
const $=s=>document.querySelector(s),esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const person=id=>D.people.find(p=>p.id===id),img=p=>'<img src="'+esc(p.avatar)+'" alt="'+esc(p.name)+'" width="92" height="92" loading="lazy">';
const params=new URLSearchParams(location.search);
let domain='',query='',selected=person(params.get('role'))||person('AGT-006'),scene=D.scenes.find(s=>s.id===(params.get('from')||params.get('scene')))||D.scenes[1],step=0;
const captions=['目标与资源','专业执行','独立复核','数据与运行'];
$('#team-constellation').innerHTML=D.planes.map((p,i)=>'<div class="tp-plane"><div class="tp-plane-portraits">'+D.people.filter(x=>x.plane===p).slice(0,3).map(img).join('')+'</div><h3>'+esc(p)+'</h3><p>'+captions[i]+'</p></div>').join('');
function roster(){
$('#team-domains').innerHTML=['',...D.domains].map(d=>'<button type="button" data-domain="'+esc(d)+'" aria-pressed="'+(domain===d)+'">'+esc(d||'全部员工')+'</button>').join('');
const list=D.people.filter(p=>(!domain||p.domain===domain)&&(!query||[p.name,p.role,p.output,p.domain,p.id].join(' ').toLowerCase().includes(query)));
$('#roster-count').textContent=list.length+' / 50 位数字员工';
$('#people-roster').innerHTML=list.length?list.map(p=>'<button class="tp-person" type="button" data-person="'+p.id+'" aria-expanded="false">'+img(p)+'<strong>'+esc(p.name)+'</strong><span>'+esc(p.role)+'</span></button>').join(''):'<div class="tp-empty"><p>没有找到匹配的员工。试试姓名、职责或交付物。</p><button type="button" id="clear-search">清空筛选</button></div>';
}
function asset(){ $('#asset-person-summary').innerHTML=img(selected)+'<h3>'+esc(selected.name)+'</h3><p>'+esc(selected.role)+'</p><a href="#people">从名录选择其他员工</a>'; }
function showPerson(id,focus=true){
selected=person(id);if(!selected)return;
const matching=D.scenes.filter(s=>s.team.some(t=>[t.lead,...t.contributors,...t.assurance].includes(id)));
$('#person-detail').hidden=false;
$('#person-detail').innerHTML='<button class="tp-detail-close" type="button">关闭</button>'+img(selected)+'<div><h3>'+esc(selected.name)+'</h3><p>'+esc(selected.role)+' · '+esc(selected.plane)+'</p><p><strong>主要交付</strong> '+esc(selected.output)+'</p><p><strong>参与场景</strong> '+esc(matching.map(s=>s.title).join('、')||'按任务范围参与')+'</p><div class="tp-detail-links"><a href="#assets">查看分身资产</a><a href="#collaboration">了解团队协作</a></div></div>';
document.querySelectorAll('[data-person]').forEach(b=>b.setAttribute('aria-expanded',String(b.dataset.person===id)));
asset();
const u=new URL(location.href);u.searchParams.set('role',id);history.replaceState(null,'',u);
if(focus){$('#person-detail').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth',block:'center'});$('#person-detail').focus({preventScroll:true});}
}
$('#team-domains').addEventListener('click',e=>{const b=e.target.closest('[data-domain]');if(b){domain=b.dataset.domain;roster();}});
$('#people-search').addEventListener('input',e=>{query=e.target.value.trim().toLowerCase();roster();});
$('#people-roster').addEventListener('click',e=>{const b=e.target.closest('[data-person]');if(b)showPerson(b.dataset.person);if(e.target.closest('#clear-search')){domain='';query='';$('#people-search').value='';roster();}});
$('#person-detail').addEventListener('click',e=>{if(e.target.closest('.tp-detail-close')){$('#person-detail').hidden=true;const b=document.querySelector('[data-person="'+selected.id+'"]');b?.setAttribute('aria-expanded','false');b?.focus({preventScroll:true});}});
const sceneNames=D.scenes.map(s=>s.title);
const sceneGroups=[['01—03',[0,1,2]],['04—06',[3,4,5]],['07—09',[6,7,8]]];
$('#collaboration-scene').innerHTML=sceneGroups.map(([label,indices],g)=>'<div class="tp-scene-group" role="group" aria-labelledby="scene-group-'+g+'"><h3 id="scene-group-'+g+'">'+label+'</h3><div class="tp-scene-options">'+indices.map(i=>{const s=D.scenes[i];return '<button type="button" class="tp-scene-button" data-scene="'+s.id+'" aria-controls="collaboration-preview handoff-detail" aria-pressed="false"><strong>'+sceneNames[i]+'</strong><span>'+esc(s.output)+'</span></button>';}).join('')+'</div></div>').join('');
function collaboration(){
document.querySelectorAll('[data-scene]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.scene===scene.id)));
$('#collaboration-current').textContent=scene.title+' · 五步协作';
$('#collaboration-preview').innerHTML=scene.team.map((t,i)=>'<button type="button" class="tp-handoff" data-handoff="'+i+'" aria-pressed="'+(i===step)+'">'+img(person(t.lead))+'<strong>'+esc(t.label)+'</strong><span>'+esc(person(t.lead).name)+' · 主责</span></button>').join('');
const t=scene.team[step],people=[...new Set([...t.contributors,...t.assurance])];
$('#handoff-detail').innerHTML='<div><h3>'+esc(t.label)+'</h3><p><strong>交接要求</strong> '+esc(t.accept)+'</p><p><strong>结果负责</strong> '+esc(person(scene.owner).name)+(scene.id==='BVS-01'?' · 独立站示例，按渠道指定':'')+'</p></div><div><p>参与与保障</p><div class="tp-contributors">'+people.map(id=>'<button type="button" data-member="'+id+'">'+img(person(id))+esc(person(id).name)+(t.assurance.includes(id)?' · 复核':'')+'</button>').join('')+'</div></div>';
}
$('#collaboration-scene').addEventListener('click',e=>{const b=e.target.closest('[data-scene]');if(!b)return;scene=D.scenes.find(s=>s.id===b.dataset.scene);step=0;collaboration();});
$('#collaboration-preview').addEventListener('click',e=>{const b=e.target.closest('[data-handoff]');if(b){step=Number(b.dataset.handoff);collaboration();document.querySelector('[data-handoff="'+step+'"]').focus({preventScroll:true});}});
$('#handoff-detail').addEventListener('click',e=>{const b=e.target.closest('[data-member]');if(b)showPerson(b.dataset.member);});
const states=[['草稿','明确职责、输入、交付与边界；尚不代表可运行。','岗位契约与预设蓝图'],['已评估','用明确用例检查能力与风险，保留失败项。','评估记录、用例与版本'],['影子验证','受限观察候选输出，不将其作为正式动作。','观察范围与差异记录'],['有限运行','限定对象、动作与资源，按验证逐步扩大。','停止条件、审批与回执'],['生产发布','仅发布契约允许的能力进入正式运行。','固定版本、权限与保障证据'],['异常隔离','停止受影响能力，保留问题与恢复条件。','隔离原因、影响范围与证据'],['退出使用','停止新任务调用，同时保留可追溯历史。','替代方案与迁移边界']];
function governance(i){$('#governance-states').innerHTML=states.map((s,n)=>'<button type="button" data-governance="'+n+'" aria-pressed="'+(n===i)+'">'+s[0]+'</button>').join('');$('#governance-detail').innerHTML='<h3>'+states[i][0]+'</h3><div><p>'+states[i][1]+'</p><p>依据：'+states[i][2]+'</p></div>';}
$('#governance-states').addEventListener('click',e=>{const b=e.target.closest('[data-governance]');if(b){const i=Number(b.dataset.governance);governance(i);document.querySelector('[data-governance="'+i+'"]').focus({preventScroll:true});}});
roster();asset();collaboration();governance(0);
if(params.get('role'))showPerson(params.get('role'),false);
const requested=params.get('view')||params.get('tab');
if(['organization','assets','collaboration','governance'].includes(requested))setTimeout(()=>document.getElementById(requested).scrollIntoView(),120);
else if(params.get('role'))setTimeout(()=>$('#person-detail').scrollIntoView({block:'center'}),120);
})();
