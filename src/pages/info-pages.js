(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const all = selector => [...document.querySelectorAll(selector)];
  const params = new URLSearchParams(location.search);
  const withLanguage = url => {
    if (params.get('lang') !== 'en') return url;
    const parsed = new URL(url, location.href);
    parsed.searchParams.set('lang', 'en');
    return parsed.pathname.split('/').pop() + parsed.search + parsed.hash;
  };
  function keyboardTabs(buttons) {
    buttons.forEach(button => button.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const index = buttons.indexOf(button);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next].click();
      buttons[next].focus();
    }));
  }
  if ($('#info-plan-cards')) {
    const plans = {
      self: [
        {name:'体验', amount:0, audience:'先判断一份成果是否有用。', points:['VOC 与 Shopify 有限范围示例','查看完整示例成果','无需先注册'], action:'免费体验', trial:true},
        {name:'基础', amount:20, audience:'围绕单个经营任务持续推进。', points:['候选：单任务分析与追问','候选：成果保存与找回','候选：任务关联积分明细'], action:'查看方案'},
        {name:'专业', amount:60, audience:'把多个经营问题连成工作流。', points:['候选：多场景衔接','候选：证据与成果版本','候选：更大的计算范围'], action:'查看方案', highlight:true},
        {name:'高级', amount:200, audience:'让更复杂的经营工作有序展开。', points:['候选：更高任务并发','候选：持续经营分析','候选：更细的使用治理'], action:'查看方案'}
      ],
      business: [
        {name:'团队', amount:40, audience:'围绕共同目标，组织人类成员与 AI 岗位。', points:['候选：组织工作空间','候选：成员权限与协作','候选：共享计算预算与审计'], action:'讨论团队方案'},
        {name:'FDE', amount:20, audience:'围绕企业数据、系统与流程，定义专项交付。', points:['候选：数据与系统接入评估','候选：受控试点与交付验收','候选：持续服务范围'], action:'讨论 FDE 落地'}
      ]
    };
    const dimensions = [
      ['工作空间','组织与项目边界待确认'],['人类席位','登录人数、角色与协作范围待确认'],['积分','计算预算、共享与消耗规则待确认'],['产品范围','场景、深度及实际开放能力待确认'],['数据连接','连接数、接入类型与读写级别待确认'],['并发与任务限制','同时任务数与单任务上限待确认'],['成果留存','保存期限、版本与导出范围待确认'],['权限与治理','授权、证据与审计范围待确认'],['FDE 专项服务','实施、验收及持续服务独立确认']
    ];
    let type = 'self';
    const tabs = all('[data-plan-type]');
    const check = '<svg viewBox="0 0 18 22" aria-hidden="true"><path d="m3 11 4 4 8-9"/></svg>';
    function render() {
      tabs.forEach(button => { const selected = button.dataset.planType === type; button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1; });
      $('#plan-panel').setAttribute('aria-labelledby', type === 'self' ? 'plan-tab-self' : 'plan-tab-business');
      $('#info-plan-cards').classList.toggle('business', type === 'business');
      $('#info-plan-cards').innerHTML = plans[type].map((plan, index) => `<article class="info-plan${plan.highlight ? ' highlight' : ''}"><h2>${plan.name}</h2><p>${plan.audience}</p><div class="info-plan-price"><strong>${plan.amount}</strong><span>金额待确认</span></div><ul>${plan.points.map(point => `<li>${check}<span>${point}</span></li>`).join('')}</ul>${plan.trial ? `<a class="r-button secondary" href="${withLanguage('trial.html')}">${plan.action}</a>` : `<button class="r-button${plan.highlight ? '' : ' secondary'}" type="button" data-plan-index="${index}">${plan.action}</button>`}<p class="info-fine">${plan.trial ? '示例入口；正式体验积分待确认。' : '权益讨论稿，不构成服务承诺。'}</p></article>`).join('');
      $('#info-plan-table thead').innerHTML = `<tr><th scope="col">权益维度</th>${plans[type].map(plan => `<th scope="col">${plan.name}</th>`).join('')}</tr>`;
      $('#info-plan-table tbody').innerHTML = dimensions.map(([name, detail]) => `<tr><th scope="row">${name}</th>${plans[type].map(plan => `<td>${plan.trial && name === '产品范围' ? 'VOC / Shopify 本地示例' : plan.trial && name === '成果留存' ? '仅当前浏览器会话' : name === 'FDE 专项服务' && plan.name !== 'FDE' ? '单独洽谈，不包含无限定制' : detail}</td>`).join('')}</tr>`).join('');
    }
    tabs.forEach(button => button.addEventListener('click', () => {type = button.dataset.planType; $('#info-plan-selection').hidden = true; render();}));
    keyboardTabs(tabs);
    $('#info-plan-cards').addEventListener('click', event => {
      const button = event.target.closest('[data-plan-index]');
      if (!button) return;
      const plan = plans[type][Number(button.dataset.planIndex)];
      $('#info-plan-selection-title').textContent = `${plan.name} · 权益讨论稿`;
      $('#info-plan-selection-copy').textContent = '当前未接通支付，没有创建订单或扣费。正式价格、积分和服务边界需要确认；你可以讨论需求，或返回原任务继续查看成果。';
      $('#info-plan-selection').hidden = false;
      $('#info-plan-selection').focus();
    });
    $('#info-plan-dismiss').addEventListener('click', () => {$('#info-plan-selection').hidden = true; $('#plan-panel').focus();});
    $('#info-pack-details').addEventListener('click', () => {const open = $('#info-pack-note').hidden; $('#info-pack-note').hidden = !open; $('#info-pack-details').setAttribute('aria-expanded', String(open));});
    if (params.get('return') === 'trial') {
      const mission = params.get('mission') === 'shopify' ? 'shopify' : 'voc';
      const result = params.get('result') || '';
      const back = new URLSearchParams({mission, step:'confirm'});
      if (/^[A-Za-z0-9_-]{1,80}$/.test(result)) back.set('result', result);
      if (params.get('lang') === 'en') back.set('lang', 'en');
      $('#info-task-return').hidden = false;
      $('#info-task-title').textContent = `${mission === 'voc' ? 'VOC 机会洞察' : 'Shopify 店铺诊断'} · 原任务`;
      $('#info-task-note').textContent = '剩余积分与成果状态：返回任务查看。此页不改积分、不自动执行；任务页面会核对本会话记录。';
      $('#info-task-back').href = `trial.html?${back}`;
      $('#info-task-back').textContent = back.has('result') ? '返回原成果' : '返回任务确认';
    }
    render();
  }
  if ($('#enterprise-route-panel')) {
    const routes = {
      product:{title:'从明确的场景开始。',copy:'先确认产品、组织和数据边界，再以验收用例决定如何启用。',steps:[['选择场景','确定经营问题'],['组织与数据','明确使用范围'],['权限与积分','配置执行边界'],['验收启用','用例与证据核对']],files:[['使用范围书',[['经营目标','一个明确的使用场景'],['交付对象','负责人和使用团队'],['不含范围','另行确认的系统写入']]],['权限矩阵',[['对象','组织、数据与任务'],['动作','读取、生成、提交'],['边界','范围与接管规则']]],['验收清单',[['用例','输入与预期产物'],['证据','可复核的过程记录'],['负责方','客户与 Sage 共同确认']]]]},
      fde:{title:'围绕真实问题，共同落地。',copy:'从可检验的问题出发，用受控试点确认数据、系统和流程是否适合。',steps:[['问题界定','目标与验收条件'],['系统检查','数据和授权现状'],['受控试点','有限范围验证'],['交付验收','产物与证据确认'],['持续运行','责任与迭代机制']],files:[['试点范围书',[['问题','需要验证的经营判断'],['边界','数据、系统与成本'],['退出条件','停止与恢复方式']]],['接入与权限矩阵',[['来源','经授权的数据对象'],['动作','允许的读取与执行'],['责任','异常接管与审计']]],['交付验收包',[['产物','试点结果与已知限制'],['用例','复核步骤与证据'],['运行','交接与服务范围']]]]}
    };
    const tabs = all('[data-service-route]');
    function showRoute(key) {
      const route = routes[key];
      tabs.forEach(button => {const selected = button.dataset.serviceRoute === key; button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;});
      $('#enterprise-route-panel').setAttribute('aria-labelledby', `route-tab-${key}`);
      $('#enterprise-route-title').textContent = route.title;
      $('#enterprise-route-copy').textContent = route.copy;
      $('#enterprise-route-steps').innerHTML = route.steps.map(([title, copy]) => `<li><span>${title}</span><small>${copy}</small></li>`).join('');
      $('#enterprise-deliverables').innerHTML = route.files.map(([title, rows]) => `<article class="info-deliverable"><header><h4>${title}</h4><svg viewBox="0 0 24 28" aria-hidden="true"><path d="M4 2h10l6 6v18H4zM14 2v7h6M8 14h8M8 19h6"/></svg></header><dl>${rows.map(([name, value]) => `<div><dt>${name}</dt><dd>${value}</dd></div>`).join('')}</dl><p class="info-fine">结构示意 · 非客户交付记录</p></article>`).join('');
      $('#enterprise-intent').value = key;
    }
    tabs.forEach(button => button.addEventListener('click', () => showRoute(button.dataset.serviceRoute)));
    keyboardTabs(tabs);
    $('#enterprise-contact-form').addEventListener('submit', event => {
      event.preventDefault();
      $('#enterprise-form-status').textContent = '咨询接收服务尚未开放，未发送或保存任何填写内容。当前仅完成本地格式检查；输入保留在本页，可继续修改。';
    });
    showRoute((params.get('service') || params.get('path')) === 'fde' ? 'fde' : 'product');
  }
})();
