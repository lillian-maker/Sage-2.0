(() => {
  'use strict';
  const data = window.SAGE_CATALOG;
  if (!data) return;
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const person = id => data.people.find(p => p.id === id);
  const params = new URLSearchParams(location.search);
  const sceneById = id => data.scenes.find(s => s.id === id) || data.scenes[1];
  const pageURL = (file, query = {}) => { const u = new URL(file, location.href); Object.entries(query).forEach(([k,v]) => {if(v)u.searchParams.set(k,v)}); if(params.get('lang'))u.searchParams.set('lang',params.get('lang')); return u.pathname.split('/').pop()+u.search+u.hash; };
  const portrait = (p, size = '') => `<img src="${esc(p.avatar)}" alt="" width="80" height="80" loading="lazy" class="${size}">`;
  const personButton = id => { const p = person(id); return p ? `<button type="button" class="participant" data-person="${p.id}">${portrait(p)}<span><strong>${esc(p.name)}</strong><small>${esc(p.role)}</small></span></button>` : ''; };
  const closeDialog = () => $('#catalog-dialog')?.close();
  function dialog(content) { const d=$('#catalog-dialog'); if(!d)return; $('#catalog-dialog-content').innerHTML=content; d.showModal(); $('.dialog-close',d).focus(); }
  function personDetails(id) {
    const p=person(id); if(!p)return;
    const related=data.scenes.filter(s=>s.owner===id||s.team.some(t=>[t.lead,...t.contributors,...t.assurance].includes(id)));
    dialog(`<div class="person-detail-title">${portrait(p)}<div><h2>${esc(p.name)}</h2><p>${esc(p.role)}</p><span class="catalog-status">岗位设计</span></div></div><dl class="person-facts"><div><dt>组织平面</dt><dd>${esc(p.plane)}</dd></div><div><dt>专业域</dt><dd>${esc(p.domain)}</dd></div><div><dt>主要交付</dt><dd>${esc(p.output)}</dd></div></dl><h3>参与的方案</h3><div class="related-scenes">${related.map(s=>`<a href="${pageURL('product.html',{scene:s.id,view:'team'})}">${esc(s.title)}</a>`).join('')||'<p>关联方案待补充。</p>'}</div><a class="text-link" href="${pageURL('team.html',{role:id,tab:'assets',from:params.get('scene')})}">查看这位员工的分身资产</a>`);
  }
  document.addEventListener('click', e => { const p=e.target.closest('[data-person]');if(p){ const orbit=p.closest('.roster-orbit');if(orbit){orbit.classList.add('paused');$('#orbit-pause').setAttribute('aria-pressed','true');$('#orbit-pause').textContent='继续环绕';}personDetails(p.dataset.person);}if(e.target.closest('.dialog-close'))closeDialog(); });
  $('#catalog-dialog')?.addEventListener('click', e => {if(e.target===e.currentTarget){const r=e.currentTarget.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closeDialog();}});
  document.addEventListener('error',e=>{if(e.target instanceof HTMLImageElement&&e.target.src.includes('/people/')){e.target.hidden=true;const label=document.createElement('span');label.className='portrait-fallback';label.textContent=e.target.closest('[data-person]')?person(e.target.closest('[data-person]').dataset.person)?.name?.slice(0,1)||'人':'人';e.target.after(label);}},true);
  const arrows='<span class="diagram-arrow" aria-hidden="true"><svg viewBox="0 0 44 20"><path d="M1 10h39m-8-7 8 7-8 7"/></svg></span>';
  const paper=(title,items,cls='')=>`<div class="diagram-paper ${cls}"><strong>${title}</strong>${items.map(x=>`<span>${x}</span>`).join('')}</div>`;
  const visuals = {
    'BVS-01':()=>`<div class="constraint-cluster"><span>流量</span><span>内容</span><span>价格</span><span>库存</span><strong>增长约束</strong></div>${arrows}${paper('经营行动包',['优先动作','预算与资源','停止条件'],'tilted-paper')}${arrows}<div class="diagram-result"><strong>验证，而非猜测</strong><span>比较方法</span><span>影响因素</span><span>继续 / 停止</span></div>`,
    'BVS-02':()=>`<div class="evidence-pair"><div><strong>认可的体验</strong><span>舒适 · 易用</span></div><div><strong>尚未满足的期待</strong><span>情境差异 · 反证</span></div></div>${arrows}<div class="attribute-cloud"><span>产品属性</span><span>使用情境</span><span>购买旅程</span><strong>证据关系</strong></div>${arrows}${paper('待验证机会',['谁遇到什么问题','支持与反证','下一步如何验证'],'tilted-paper')}`,
    'BVS-03':()=>`<div class="supply-object"><strong>同一商品</strong><span>SKU · 需求版本</span><div>现货 → 在途 → 可售</div></div>${arrows}<div class="constraints"><span>库存约束</span><span>现金约束</span><span>交期约束</span></div>${arrows}${paper('约束补货计划',['候选方案与取舍','到货里程碑','经营核对'],'tilted-paper')}`,
    'BVS-04':()=>`${paper('目标市场',['产品与渠道','语言与内容','物流与税务'])}${arrows}<div class="entry-matrix"><strong>准入检查</strong><span>要求 <b>资料</b> 缺口</span><span>产品 <b>待核对</b> 责任人</span><span>内容 <b>待核对</b> 责任人</span></div>${arrows}${paper('市场发布包',['可发布范围','对象与权限','首周期验证'],'tilted-paper')}`,
    'BVS-05':()=>`<div class="journey-track"><span>购买前</span><span>使用</span><span>售后</span><span>复购</span><strong>在哪一步遇到问题？</strong></div>${arrows}${paper('体验修复包',['受影响的客群','根因与假设','内容 / 产品 / 服务'],'tilted-paper')}${arrows}<div class="diagram-result"><strong>授权触达</strong><span>逐对象回执</span><span>后续观察</span></div>`,
    'BVS-06':()=>`${paper('经营决策问题',['使用人','对象与口径','验收标准'])}${arrows}<div class="data-contract"><strong>同一事实，同一口径</strong><span>来源 → 质量 → 版本</span><span>缺失有原因，不填假零</span></div>${arrows}${paper('最小工具候选',['明确读写范围','独立验收证据','运行反馈'],'tilted-paper')}`,
    'BVS-07':()=>`<div class="incident-signal"><strong>风险信号</strong><span>来源可信度</span><span>影响范围</span></div>${arrows}${paper('最小必要保护',['限定对象与动作','保留证据','逐项处置回执'],'tilted-paper')}${arrows}<div class="recovery-gate"><strong>恢复条件</strong><span>根因已复核？</span><span>风险已处置？</span><span>策略允许恢复？</span></div>`,
    'BVS-08':()=>`${paper('经营事实账本',['可比经营事实','财务与风险','能力运行记录'])}${arrows}<div class="portfolio-choices"><span>继续</span><span>纠偏</span><span>停止</span><strong>依据与资源约束</strong></div>${arrows}${paper('能力组合变更',['岗位 / 知识 / 工具','版本评估','下一周期再验证'],'tilted-paper')}`,
    'SHOPIFY-OPS':()=>`<div class="shop-page"><div class="shop-window"><i></i><i></i><i></i></div><strong>已有店铺</strong><span>首页 + 商品页</span><div class="shop-product"><span>商品事实</span><span>购买信息</span></div></div>${arrows}<div class="shop-diagnosis"><strong>可定位的问题</strong><span>页面依据</span><span>待补信息</span><span>需后台验证</span></div>${arrows}${paper('优先行动',['内容优化草稿','验证与实验','另行确认授权'],'tilted-paper')}`
  };
  const productArtifacts={
    'BVS-01':['机会信号','增长诊断','经营行动包','动作回执集','增量评估记录'],
    'BVS-02':['机会证据包','新品机会命题','可行性与准入档案','上市实验方案','组合决策记录'],
    'BVS-03':['需求快照','约束供应计划','供应承诺回执','履约里程碑账本','可售核对记录'],
    'BVS-04':['市场进入命题','准入控制矩阵','市场发布包','上线回执','市场验证记录'],
    'BVS-05':['旅程事件','旅程诊断','体验修复包','客户触达回执','体验学习记录'],
    'BVS-06':['数据产品需求','版本化数据契约','工具与界面候选','验收证据','产品运行复盘'],
    'BVS-07':['事件信号记录','最小保护计划','处置回执','根因与整改记录','恢复证据'],
    'BVS-08':['经营证据账本','能力缺口地图','组合决策','版本化变更集','周期复盘'],
    'SHOPIFY-OPS':['店铺与访问范围卡','经营快照','带证据的问题清单','优先行动与实验方案','实施回执与效果复核']
  };
  const handoffArtifacts={
    'BVS-01':['对象与口径快照','渠道诊断与反证','供需可行性清单','逐对象行动包','结果与归因复核'],
    'BVS-02':['机会证据包','产品定义与验证设计','质量与供应验证档案','上市实验记录','投资组合决策依据'],
    'BVS-03':['需求基线','供应与资金约束','逐SKU候选计划','履约与异常回执','经营核对记录'],
    'BVS-04':['机会与范围卡','产品与内容准备包','渠道发布设计','上线与可售核对','首周期复盘'],
    'BVS-05':['旅程事件包','体验根因假设','跨域修复包','授权触达回执','结果复核记录'],
    'BVS-06':['业务需求契约','数据设计契约','最小工具候选','验收与发布记录','业务价值复盘'],
    'BVS-07':['权威信号记录','最小保护意图','事件处置回执','根因与整改记录','恢复裁决依据'],
    'BVS-08':['经营事实账本','风险财务复核','能力缺口地图','版本化变更记录','周期决策依据'],
    'SHOPIFY-OPS':['店铺与访问范围','经营快照','证据化诊断清单','行动与实验方案','回执与复核']
  };
  const selectOptions=(select,selected)=>{select.innerHTML=data.scenes.map(s=>`<option value="${s.id}"${s.id===selected?' selected':''}>${esc(s.title)}</option>`).join('');};
  function teamChain(scene,active=0){return `<div class="handoff-chain">${scene.team.map((t,i)=>{const p=person(t.lead);return `<button type="button" class="handoff-node${i===active?' active':''}" data-handoff="${i}" aria-pressed="${i===active}">${portrait(p)}<strong>${esc(p.name)}</strong><span>${esc(t.label)}</span><small>${esc(handoffArtifacts[scene.id][i])}</small></button>`;}).join('')}</div>`;}
  function handoffDetail(scene,i){const t=scene.team[i],next=scene.team[i+1];return `<div class="handoff-detail"><div><span class="detail-label">阶段交付</span><h3>${esc(handoffArtifacts[scene.id][i])}</h3><p>${esc(person(t.lead).name)} 提交${next?' · '+esc(person(next.lead).name)+' 接收':' · 交由结果负责人核对'}</p></div><div><span class="detail-label">接受标准</span><p>${esc(t.accept)}</p><span class="handoff-reject">缺少依据 → 退回补充，不跳过验收</span></div></div><div class="handoff-contributors"><div><h4>贡献岗位</h4><div class="compact-people">${t.contributors.map(personButton).join('')||'<p>该阶段未列额外贡献岗位。</p>'}</div></div><div><h4>独立保障</h4><div class="compact-people">${t.assurance.map(personButton).join('')||'<p>按任务风险适用平台门禁，不代表豁免权限检查。</p>'}</div></div></div>`;}
  function productPage(){
    let scene=sceneById(params.get('scene')),view=params.get('view')==='team'?'team':'product',step=0,handoff=0;
    const sceneSelect=$('#scene-select');selectOptions(sceneSelect,scene.id);
    function render(){
      $('#scene-title').textContent=scene.title;$('#scene-question').textContent=scene.question;$('#scene-input').textContent=scene.input;$('#scene-output').textContent=scene.output;$('#scene-boundary').textContent=scene.boundary;$('#recording-script').textContent=scene.script;
      const back=pageURL('index.html',{scene:scene.id})+'#product';$('#product-back').href=back;$('#scene-return').href=back;
      $$('[data-solution-view]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.solutionView===view));
      $('#solution-owner').innerHTML=view==='team'?`<div class="case-owner"><span>结果负责人</span>${personButton(scene.owner)}<p>${esc(scene.output)}${scene.id==='BVS-01'?'<small>当前展示 Shopify 版本；其他渠道由对应经营岗位负责。</small>':''}</p></div>`:'';
      const stages=view==='product'?scene.steps:scene.team,idx=view==='product'?step:handoff;
      $('#solution-steps').innerHTML=stages.map((s,i)=>`<button type="button" data-step="${i}" aria-pressed="${i===idx}"><span>${i+1}</span>${esc(s.label)}</button>`).join('');
      $('#solution-canvas').classList.toggle('collaboration-canvas',view==='team');
      if(view==='product'){
        const current=scene.steps[step];
        const previous=scene.steps[step-1],next=scene.steps[step+1],artifactName=productArtifacts[scene.id][step];
        const fields=current.detail.split(/[、，；]/).filter(Boolean);
        $('#solution-canvas').innerHTML=`<div class="scene-canvas-stage" data-scene="${scene.id}" data-product-stage="${step+1}"><div class="scene-diagram diagram-${scene.id.toLowerCase()}">${visuals[scene.id]()}</div><div class="stage-projection" aria-label="当前阶段的输入、产物与交接"><div class="stage-source"><span>${previous?'上一阶段交付':'任务输入'}</span><strong>${esc(previous?productArtifacts[scene.id][step-1]:'确认对象与范围')}</strong><p>${esc(previous?.detail||scene.input)}</p></div>${arrows}<div class="stage-document"><div class="stage-document-heading"><span>${step+1} / 5</span><strong>${esc(current.label)}</strong></div><p class="stage-artifact-name">${esc(artifactName)}</p><ul>${fields.map(field=>`<li>${esc(field)}</li>`).join('')}</ul></div>${arrows}<div class="stage-destination"><span>${next?'交给下一阶段':'最终交付'}</span><strong>${esc(next?.label||scene.output)}</strong><p>${next?'以本阶段产物为依据，形成：'+esc(next.detail):'与任务范围核对，保留依据、版本和未决事项。'}</p></div></div></div>`;
        $('#solution-detail').innerHTML=`<div class="artifact-explainer"><div><span class="detail-label">当前产物</span><h3>${esc(current.label)}</h3></div><button class="artifact-open" type="button" id="open-artifact"><svg viewBox="0 0 40 48" aria-hidden="true"><path d="M8 2h16l10 10v34H8zM24 2v12h10M14 24h14M14 31h14M14 38h8"/></svg><span>展开这一步的产物</span></button></div>`;
      }else{
        $('#solution-canvas').innerHTML=teamChain(scene,handoff);
        $('#solution-detail').innerHTML=handoffDetail(scene,handoff)+`<div class="handoff-stepper"><span>协作结构示意，不是正在运行的任务</span><button type="button" id="next-handoff"${handoff===4?' disabled':''}>${handoff===4?'已到最终交接':'查看下一次交接'}</button></div>`;
      }
      $('#recording-story').innerHTML=`<div class="recording-frame"><span>产品录屏</span><strong>${esc(scene.title)}</strong><div>${scene.steps.map((s,i)=>`<span>${i+1}. ${esc(s.label)}</span>`).join('')}</div><p>真实界面、版本与演示素材将在此补充。</p></div>`;
      const ids=[...new Set([scene.owner,...scene.team.flatMap(t=>[t.lead,...t.contributors,...t.assurance])])];$('#scene-people').innerHTML=ids.map(personButton).join('');
    }
    sceneSelect.addEventListener('change',()=>{scene=sceneById(sceneSelect.value);step=handoff=0;const u=new URL(location.href);u.searchParams.set('scene',scene.id);history.replaceState(null,'',u);render();});
    $$('[data-solution-view]').forEach(b=>b.addEventListener('click',()=>{view=b.dataset.solutionView;render();}));
    $('.solution-section').addEventListener('click',e=>{const b=e.target.closest('[data-step],[data-handoff]');if(b){const i=Number(b.dataset.step??b.dataset.handoff);if(view==='product')step=i;else handoff=i;render();const target=$(`[${view==='product'?'data-step':'data-handoff'}="${i}"]`);target?.focus({preventScroll:true});}if(e.target.closest('#next-handoff')){handoff=Math.min(4,handoff+1);render();$('#next-handoff').focus({preventScroll:true});}if(e.target.closest('#open-artifact')){const s=scene.steps[step];dialog(`<span class="catalog-status">产物结构 · 待接入真实成果</span><h2>${esc(s.label)}</h2><p class="artifact-subtitle">${esc(s.artifact)}</p><ul class="artifact-fields">${s.detail.split(/[、，；]/).map(x=>`<li>${esc(x)}</li>`).join('')}</ul><h3>范围与依据</h3><p>${esc(scene.input)}</p><h3>缺口处理</h3><p>${esc(scene.boundary)}</p>`);}});
    render();
  }
  const governance=[
    ['Draft','草稿','定义职责、输入、交付与边界。','岗位契约、预设蓝图；此时不等于可运行。'],
    ['Evaluated','已评估','用明确用例检查能力和风险。','评估记录、失败项、所用版本。'],
    ['Shadow','影子验证','在受限模式下观察，不把候选输出当正式动作。','验证范围、观察记录与差异说明。'],
    ['Limited','有限运行','限定对象、动作与资源，逐步扩大。','限定范围、停止条件、审批与回执。'],
    ['Production','生产发布','只有发布契约允许的能力进入正式运行。','固定版本、权限、保障证据与运行记录。'],
    ['Quarantined','异常隔离','停止受影响的能力，保留问题与证据。','隔离原因、影响范围、恢复条件。'],
    ['Retired','退出使用','停止新任务使用，保留可追溯历史。','替代方案、迁移边界与历史记录。']
  ];
  function teamPage(){
    const requestedTab=params.get('tab')||params.get('view')||(person(params.get('role'))?'assets':'organization');
    let tab=['organization','assets','collaboration','governance'].includes(requestedTab)?requestedTab:'organization',group='plane',mode='list',query='',selectedPerson=person(params.get('role'))?.id||'AGT-006',collabScene=sceneById(params.get('from')||params.get('scene')),collabStep=0;
    const sourceScene=params.get('from');if(data.scenes.some(s=>s.id===sourceScene)){const r=$('#team-return-scene');r.hidden=false;r.href=pageURL('product.html',{scene:sourceScene,view:'team'});}
    function showTab(){ $$('[data-team-view]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.teamView===tab));$$('.team-view').forEach(v=>v.hidden=v.id!=='team-'+tab);const u=new URL(location.href);u.searchParams.set('tab',tab);history.replaceState(null,'',u); }
    function renderRoster(){
      const matches=data.people.filter(p=>[p.name,p.role,p.output,p.id,p.domain,p.plane].join(' ').toLowerCase().includes(query.toLowerCase()));
      $('#roster-count').textContent=query?`找到 ${matches.length} / 50 位员工`:'50 位员工 · 全部岗位';$('#clear-search').hidden=!query;
      const groups=group==='plane'?data.planes:data.domains;
      $('#people-roster').innerHTML=matches.length?groups.map(g=>{const ps=matches.filter(p=>p[group]===g);return ps.length?`<section class="roster-group"><h2>${esc(g)} <span>${ps.length}</span></h2><div class="roster-grid">${ps.map(p=>`<button type="button" class="roster-person" data-person="${p.id}">${portrait(p)}<strong>${esc(p.name)}</strong><span>${esc(p.role)}</span></button>`).join('')}</div></section>`:'';}).join(''):`<div class="roster-empty"><h2>没有找到相关岗位</h2><p>试试姓名、职责或交付物，也可以清空筛选查看全员。</p><button type="button" data-clear-search>查看全部 50 位员工</button></div>`;
      $('#people-roster').hidden=mode==='orbit'&&!query;$('#roster-orbit').hidden=mode!=='orbit'||!!query;
    }
    function buildOrbit(){ const orbit=$('#orbit-map');orbit.innerHTML=data.people.map((p,i)=>{const ring=i<10?0:i<26?1:2,start=[0,10,26][ring],count=[10,16,24][ring],angle=(i-start)*360/count;return `<button type="button" class="orbit-person orbit-ring-${ring}" data-person="${p.id}" style="--orbit-angle:${angle}deg;--orbit-start:${(i-start)*100/count}%;--static-x:${Math.cos(angle*Math.PI/180)*[155,295,435][ring]}px;--static-y:${Math.sin(angle*Math.PI/180)*[105,187,270][ring]}px;--delay:${-(i-start)*120/count}s" aria-label="${esc(p.name+'，'+p.role)}">${portrait(p)}<span>${esc(p.name)}</span></button>`;}).join(''); }
    function renderAssets(){const p=person(selectedPerson);$('#asset-person-summary').innerHTML=`<div class="asset-person-head">${portrait(p)}<div><h3>${esc(p.name)}</h3><p>${esc(p.role)}</p><span>主要交付：${esc(p.output)}</span></div></div>`;$('#asset-deck').innerHTML=`<article><span class="asset-sheet-number">Soul</span><h3>岗位身份</h3><dl><dt>职责</dt><dd>${esc(p.role)}</dd><dt>交付</dt><dd>${esc(p.output)}</dd><dt>完整责任边界</dt><dd>岗位正文待核对</dd></dl><a href="https://github.com/zjgulai/AI-Native-Organization/tree/main/docs/05-agents/roles/souls" target="_blank" rel="noopener">查看来源目录</a></article><article><span class="asset-sheet-number">Playbook</span><h3>工作方法</h3><div class="asset-method"><span>输入</span><span>方法</span><span>交付</span><span>验收</span></div><p>对应 ${p.id} 的具体方法、停止条件与版本待核对；不借用其他岗位内容。</p><a href="https://github.com/zjgulai/AI-Native-Organization/blob/main/docs/06-playbooks/role-playbooks/index.json" target="_blank" rel="noopener">查看方法索引</a></article><article><span class="asset-sheet-number">Preset</span><h3>运行预设</h3><dl><dt>知识 · 技能 · 工具</dt><dd>实际绑定待核对</dd><dt>版本与访问范围</dt><dd>以发布契约为准</dd><dt>发布状态</dt><dd>岗位设计，未核验生产状态</dd></dl><a href="https://github.com/zjgulai/AI-Native-Organization/blob/main/docs/10-platform/deepseek-harness/preset-blueprints/manifest.json" target="_blank" rel="noopener">查看预设清单</a></article>`;}
    function renderCollab(){ $('#collaboration-preview').innerHTML=`<div class="case-owner"><span>结果负责人</span>${personButton(collabScene.owner)}<p>${esc(collabScene.output)}</p></div>${teamChain(collabScene,collabStep)}${handoffDetail(collabScene,collabStep)}`;$('#collaboration-open').href=pageURL('product.html',{scene:collabScene.id,view:'team'}); }
    function renderGovernance(index){$('#governance-states').innerHTML=governance.map((s,i)=>`<button type="button" data-governance="${i}" aria-pressed="${i===index}"><strong>${s[1]}</strong><span>${s[0]}</span></button>`).join('');const s=governance[index];$('#governance-detail').innerHTML=`<h3>${s[1]}</h3><p>${s[2]}</p><dl><dt>需要的依据</dt><dd>${s[3]}</dd></dl>`;}
    $$('[data-team-view]').forEach(b=>b.addEventListener('click',()=>{tab=b.dataset.teamView;showTab();}));
    $$('[data-group]').forEach(b=>b.addEventListener('click',()=>{group=b.dataset.group;$$('[data-group]').forEach(x=>x.setAttribute('aria-pressed',x===b));renderRoster();}));
    $$('[data-roster-mode]').forEach(b=>b.addEventListener('click',()=>{mode=b.dataset.rosterMode;$$('[data-roster-mode]').forEach(x=>x.setAttribute('aria-pressed',x===b));renderRoster();}));
    $('#people-search').addEventListener('input',e=>{query=e.target.value.trim();renderRoster();});
    function clearSearch(){query='';$('#people-search').value='';renderRoster();$('#people-search').focus();}$('#clear-search').addEventListener('click',clearSearch);$('#people-roster').addEventListener('click',e=>{if(e.target.closest('[data-clear-search]'))clearSearch();});
    $('#orbit-pause').addEventListener('click',e=>{const paused=$('#roster-orbit').classList.toggle('paused');e.currentTarget.setAttribute('aria-pressed',paused);e.currentTarget.textContent=paused?'继续环绕':'暂停环绕';});
    $('#asset-person').innerHTML=data.people.map(p=>`<option value="${p.id}"${p.id===selectedPerson?' selected':''}>${esc(p.name+' · '+p.role)}</option>`).join('');$('#asset-person').addEventListener('change',e=>{selectedPerson=e.target.value;const u=new URL(location.href);u.searchParams.set('role',selectedPerson);history.replaceState(null,'',u);renderAssets();});
    selectOptions($('#collaboration-scene'),collabScene.id);$('#collaboration-scene').addEventListener('change',e=>{collabScene=sceneById(e.target.value);collabStep=0;renderCollab();});$('#collaboration-preview').addEventListener('click',e=>{const b=e.target.closest('[data-handoff]');if(b){collabStep=Number(b.dataset.handoff);renderCollab();$(`[data-handoff="${collabStep}"]`,$('#collaboration-preview')).focus({preventScroll:true});}});
    $('#governance-states').addEventListener('click',e=>{const b=e.target.closest('[data-governance]');if(b){const i=Number(b.dataset.governance);renderGovernance(i);$(`[data-governance="${i}"]`).focus({preventScroll:true});}});
    buildOrbit();renderRoster();renderAssets();renderCollab();renderGovernance(2);showTab();
    if(matchMedia('(prefers-reduced-motion: reduce)').matches){$('#roster-orbit').classList.add('paused');$('#orbit-pause').textContent='减少动态效果已开启';$('#orbit-pause').disabled=true;}
  }
  if($('.product-catalog'))productPage();
  if($('.team-catalog'))teamPage();
})();
