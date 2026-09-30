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
        {name:'Preview', amount:0, audience:'First, see whether a deliverable is useful.', points:['Limited-scope VOC and Shopify examples','Review complete example deliverables','No registration required'], action:'Try Sage', trial:true},
        {name:'Basic', amount:20, audience:'Keep a single business task moving forward.', points:['Proposed: single-task analysis and follow-up','Proposed: save and retrieve deliverables','Proposed: task-linked credit records'], action:'View plan'},
        {name:'Professional', amount:60, audience:'Connect business questions into a workflow.', points:['Proposed: connected workflows','Proposed: evidence and deliverable versions','Proposed: broader compute scope'], action:'View plan', highlight:true},
        {name:'Advanced', amount:200, audience:'Manage more complex business work.', points:['Proposed: higher task concurrency','Proposed: ongoing business analysis','Proposed: finer-grained usage governance'], action:'View plan'}
      ],
      business: [
        {name:'Team', amount:40, audience:'Bring people and AI roles together around shared goals.', points:['Proposed: organizational workspaces','Proposed: member permissions and collaboration','Proposed: shared compute budgets and audit'], action:'Discuss a team plan'},
        {name:'FDE', amount:20, audience:'Define dedicated deliverables around enterprise data, systems and workflows.', points:['Proposed: data and systems integration assessment','Proposed: controlled pilots and delivery acceptance','Proposed: ongoing service scope'], action:'Discuss FDE deployment'}
      ]
    };
    const dimensions = [
      ['Workspace','Organization and project boundaries to be confirmed'],['Human seats','User counts, roles and collaboration scope to be confirmed'],['Credits','Compute budgets, sharing and usage rules to be confirmed'],['Product scope','Workflows, depth and available capabilities to be confirmed'],['Data connections','Connection counts, integration types and read/write levels to be confirmed'],['Concurrency and task limits','Concurrent task counts and per-task limits to be confirmed'],['Deliverable retention','Retention periods, versions and export scope to be confirmed'],['Permissions and governance','Authorization, evidence and audit scope to be confirmed'],['Dedicated FDE services','Implementation, acceptance and ongoing services agreed separately']
    ];
    let type = 'self';
    const tabs = all('[data-plan-type]');
    const check = '<svg viewBox="0 0 18 22" aria-hidden="true"><path d="m3 11 4 4 8-9"/></svg>';
    function render() {
      tabs.forEach(button => { const selected = button.dataset.planType === type; button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1; });
      $('#plan-panel').setAttribute('aria-labelledby', type === 'self' ? 'plan-tab-self' : 'plan-tab-business');
      $('#info-plan-cards').classList.toggle('business', type === 'business');
      $('#info-plan-cards').innerHTML = plans[type].map((plan, index) => `<article class="info-plan${plan.highlight ? ' highlight' : ''}"><h2>${plan.name}</h2><p>${plan.audience}</p><div class="info-plan-price"><strong>${plan.amount}</strong><span>Price to be confirmed</span></div><ul>${plan.points.map(point => `<li>${check}<span>${point}</span></li>`).join('')}</ul>${plan.trial ? `<a class="r-button secondary" href="${withLanguage('trial-en.html')}">${plan.action}</a>` : `<button class="r-button${plan.highlight ? '' : ' secondary'}" type="button" data-plan-index="${index}">${plan.action}</button>`}<p class="info-fine">${plan.trial ? 'Example access; official trial credits to be confirmed.' : 'Draft plan for discussion, not a service commitment.'}</p></article>`).join('');
      $('#info-plan-table thead').innerHTML = `<tr><th scope="col">Plan dimension</th>${plans[type].map(plan => `<th scope="col">${plan.name}</th>`).join('')}</tr>`;
      $('#info-plan-table tbody').innerHTML = dimensions.map(([name, detail]) => `<tr><th scope="row">${name}</th>${plans[type].map(plan => `<td>${plan.trial && name === 'Product scope' ? 'Local VOC / Shopify examples' : plan.trial && name === 'Deliverable retention' ? 'Current browser session only' : name === 'Dedicated FDE services' && plan.name !== 'FDE' ? 'Separately scoped; unlimited customization not included' : detail}</td>`).join('')}</tr>`).join('');
    }
    tabs.forEach(button => button.addEventListener('click', () => {type = button.dataset.planType; $('#info-plan-selection').hidden = true; render();}));
    keyboardTabs(tabs);
    $('#info-plan-cards').addEventListener('click', event => {
      const button = event.target.closest('[data-plan-index]');
      if (!button) return;
      const plan = plans[type][Number(button.dataset.planIndex)];
      $('#info-plan-selection-title').textContent = `${plan.name} · Draft plan for discussion`;
      $('#info-plan-selection-copy').textContent = 'Payments are not connected. No order or charge was created. Final pricing, credits and service boundaries require confirmation. Discuss your needs or return to your task to review the results.';
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
      $('#info-task-title').textContent = `${mission === 'voc' ? 'VOC opportunity insights' : 'Shopify store diagnosis'} · Original task`;
      $('#info-task-note').textContent = 'Return to your task to check available credits and result status. This page does not change credits or execute tasks. The task page checks the current session record.';
      $('#info-task-back').href = `trial-en.html?${back}`;
      $('#info-task-back').textContent = back.has('result') ? 'Return to the original result' : 'Return to task confirmation';
    }
    render();
  }
  if ($('#enterprise-route-panel')) {
    const routes = {
      product:{title:'Start with a defined workflow.',copy:'Confirm product, organizational and data boundaries, then use acceptance cases to guide deployment.',steps:[['Choose a workflow','Define the business problem'],['Organization and data','Define the scope of use'],['Permissions and credits','Set execution boundaries'],['Accept and launch','Verify cases and evidence']],files:[['Statement of scope',[['Business goal','One clearly defined workflow'],['Recipients','Accountable owner and user team'],['Out of scope','System writes require separate agreement']]],['Permission matrix',[['Target','Organization, data and tasks'],['Action','Read, generate and submit'],['Boundary','Scope and escalation rules']]],['Acceptance checklist',[['Test case','Inputs and expected deliverables'],['Evidence','Reviewable process records'],['Responsible parties','Jointly agreed by the customer and Sage']]]]},
      fde:{title:'Deploy together around a real problem.',copy:'Start with a testable problem. Use a controlled pilot to validate the fit of data, systems and workflows.',steps:[['Problem definition','Goals and acceptance criteria'],['System assessment','Current data and authorization'],['Controlled pilot','Limited-scope validation'],['Delivery acceptance','Deliverable and evidence review'],['Ongoing operation','Responsibilities and iteration process']],files:[['Pilot scope',[['Problem','Business decision to validate'],['Boundary','Data, systems and costs'],['Exit criteria','Stop and recovery procedures']]],['Integration and permission matrix',[['Source','Authorized data objects'],['Action','Permitted reads and actions'],['Responsibility','Exception escalation and audit']]],['Delivery acceptance package',[['Deliverable','Pilot results and known limitations'],['Test case','Review steps and evidence'],['Operation','Handoff and service scope']]]]}
    };
    const tabs = all('[data-service-route]');
    function showRoute(key) {
      const route = routes[key];
      tabs.forEach(button => {const selected = button.dataset.serviceRoute === key; button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;});
      $('#enterprise-route-panel').setAttribute('aria-labelledby', `route-tab-${key}`);
      $('#enterprise-route-title').textContent = route.title;
      $('#enterprise-route-copy').textContent = route.copy;
      $('#enterprise-route-steps').innerHTML = route.steps.map(([title, copy]) => `<li><span>${title}</span><small>${copy}</small></li>`).join('');
      $('#enterprise-deliverables').innerHTML = route.files.map(([title, rows]) => `<article class="info-deliverable"><header><h4>${title}</h4><svg viewBox="0 0 24 28" aria-hidden="true"><path d="M4 2h10l6 6v18H4zM14 2v7h6M8 14h8M8 19h6"/></svg></header><dl>${rows.map(([name, value]) => `<div><dt>${name}</dt><dd>${value}</dd></div>`).join('')}</dl><p class="info-fine">Structure illustration · Not a customer delivery record</p></article>`).join('');
      $('#enterprise-intent').value = key;
    }
    tabs.forEach(button => button.addEventListener('click', () => showRoute(button.dataset.serviceRoute)));
    keyboardTabs(tabs);
    $('#enterprise-contact-form').addEventListener('submit', event => {
      event.preventDefault();
      $('#enterprise-form-status').textContent = 'The inquiry service is not connected. Nothing was sent or saved. Only local format validation was completed; you can continue editing this page.';
    });
    showRoute((params.get('service') || params.get('path')) === 'fde' ? 'fde' : 'product');
  }
})();
