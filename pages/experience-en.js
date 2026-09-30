(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const params = new URLSearchParams(location.search);
  const KEY = 'sage.experience.v1';
  const costs = { voc: 30, shopify: 50 };
  const names = { voc: 'VOC opportunity insights', shopify: 'Shopify store diagnosis' };
  let state = { version: 1, spent: 0, task: null, ledger: [], history: [] };
  let storageOK = true;
  try {
    const raw = sessionStorage.getItem(KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed.version !== 1 || !Number.isFinite(parsed.spent) || parsed.spent < 0 || parsed.spent > 100 || !Array.isArray(parsed.ledger)) throw new Error('Invalid demo state');
      if (parsed.task && (!costs[parsed.task.scene] || !['running', 'complete', 'stopped', 'example'].includes(parsed.task.status) || typeof parsed.task.id !== 'string')) throw new Error('Invalid demo task');
      state = parsed;
      state.history = Array.isArray(parsed.history) ? parsed.history.filter(t => costs[t.scene] && ['complete', 'example'].includes(t.status) && typeof t.id === 'string') : [];
    }
  } catch (_) { storageOK = false; }
  let scene = params.get('mission') === 'shopify' ? 'shopify' : 'voc';
  let resultScene = scene;
  function persist() {
    try { sessionStorage.setItem(KEY, JSON.stringify(state)); return true; }
    catch (_) { storageOK = false; message('Browser session storage is unavailable, so this demo task cannot be restored. Keep this page open; free examples remain available.'); return false; }
  }
  function message(text) { if ($('#xp-message')) $('#xp-message').textContent = text; }
  function reserved() { return state.task?.status === 'running' ? costs[state.task.scene] : 0; }
  function available() { return 100 - state.spent - reserved(); }
  function credits() {
    if ($('#credit-count')) $('#credit-count').textContent = String(available());
    if ($('#credit-detail')) $('#credit-detail').textContent = `Reserved: ${reserved()} credits · Demo credits used: ${state.spent}. Demo progress is retained only in this browser session.`;
  }
  function show(id) {
    ['trial-input', 'trial-confirm', 'trial-running', 'trial-result'].forEach(x => { if ($('#' + x)) $('#' + x).hidden = x !== id; });
    credits();
    window.scrollTo({ top: 0, behavior: 'instant' });
  }
  function returnParams() {
    const p = new URLSearchParams({ return: 'trial', mission: resultScene });
    if (state.task) p.set('result', state.task.id);
    p.set('lang', document.documentElement.lang.startsWith('en') ? 'en' : 'zh');
    return p.toString();
  }
  function setScene(next) {
    scene = next;
    $$('.xp-scene').forEach(b => { const yes = b.dataset.scene === next; b.classList.toggle('active', yes); b.setAttribute('aria-pressed', String(yes)); });
  }
  function confirm() {
    if (state.task?.status === 'running') { running(); return; }
    $('#confirm-title').textContent = names[scene];
    $('#scope-summary').textContent = scene === 'voc' ? 'Explore customer experiences, supporting and opposing evidence, and opportunities to validate. This preview uses anonymized qualitative examples.' : 'Explore purchase-information checks, improvement recommendations and authorization boundaries for an existing store. This preview uses a generic assessment example.';
    $('#confirm-available').textContent = String(available());
    $('#confirm-estimate').textContent = `${costs[scene]} credits`;
    $('#confirm-maximum').textContent = `${costs[scene]} credits`;
    $('#confirm-after').textContent = available() >= costs[scene] ? `${available() - costs[scene]} credits` : 'Insufficient credits';
    $('#start-demo').disabled = available() < costs[scene] || !storageOK;
    $('#confirmation-limit').textContent = !storageOK ? 'Session storage is unavailable. The credit demo is paused; free examples remain available.' : available() < costs[scene] ? `You need ${costs[scene] - available()} more credits to continue this demo. Existing results remain readable. Viewing plans does not add credits or trigger a payment.` : 'Starting reserves the maximum credit amount. Credits are settled only when you choose to complete the demo. Stopping releases the full reservation.';
    $('#confirm-pricing').href = `pricing-en.html?${new URLSearchParams({ return: 'trial', mission: scene, step: 'confirm' })}`;
    show('trial-confirm');
  }
  function start() {
    if (state.task?.status === 'running' || !storageOK || available() < costs[scene]) return;
    const task = { id: `demo-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, scene, status: 'running' };
    rememberTask(); state.task = task;
    state.ledger.push({ task: task.id, scene, type: 'Reserved credits', amount: costs[scene], at: new Date().toISOString() });
    persist(); running();
  }
  function rememberTask() {
    if (state.task && ['complete', 'example'].includes(state.task.status) && !state.history.some(t => t.id === state.task.id)) state.history.push({ ...state.task });
  }
  function recentTasks() {
    const box = $('#saved-task');
    if (!box) return;
    const all = [...state.history];
    if (state.task && ['complete', 'example'].includes(state.task.status) && !all.some(t => t.id === state.task.id)) all.push(state.task);
    box.replaceChildren(); box.hidden = !all.length;
    if (!all.length) return;
    const title = document.createElement('strong'); title.textContent = 'Results in this session'; box.append(title);
    all.slice(-6).reverse().forEach(t => {
      const row = document.createElement('div'); const button = document.createElement('button'); button.className = 'xp-text-button';
      button.textContent = `${names[t.scene]} · ${t.status === 'complete' ? 'Demo completed' : 'Free example'} →`;
      button.addEventListener('click', () => { rememberTask(); state.task = { ...t }; persist(); renderResult(t.scene, t.status === 'example'); }); row.append(button); box.append(row);
    });
  }
  function running() {
    scene = state.task.scene;
    $('#running-reserved').textContent = `${reserved()} credits`;
    $('#running-available').textContent = `${available()} credits`;
    show('trial-running');
  }
  function finish() {
    if (state.task?.status !== 'running') return;
    const cost = costs[state.task.scene];
    state.spent += cost;
    state.task.status = 'complete';
    state.ledger.push({ task: state.task.id, scene: state.task.scene, type: 'Credits used', amount: cost, at: new Date().toISOString() });
    persist(); renderResult(state.task.scene, false);
    message(`Demo completed. ${cost} credits used. Refreshing or reopening this task will not deduct credits again.`);
  }
  function stop() {
    if (state.task?.status !== 'running') return;
    const cost = costs[state.task.scene];
    state.task.status = 'stopped';
    state.ledger.push({ task: state.task.id, scene: state.task.scene, type: 'Reserved credits released', amount: cost, at: new Date().toISOString() });
    persist(); show('trial-input');
    message(`Demo stopped. ${cost} reserved credits released; 0 credits used. You can still view examples for free.`);
  }
  const vocViews = {
    summary: `<article class="xp-story"><span class="xp-eyebrow">Key insight</span><h2>Comfort earns praise.<br>That does not mean support meets expectations.</h2><p>Separate positive and negative experiences to identify expectation gaps. These anonymized qualitative examples do not establish market-wide conclusions.</p><div class="xp-duo"><div class="xp-paper"><h3>What customers value</h3><span class="xp-tag">Softness</span><span class="xp-tag">Breathability</span><span class="xp-tag">Discreet under clothing</span><p>Feedback describes positive experiences with feel and everyday wear.</p></div><div class="xp-paper"><h3>Unmet expectations</h3><span class="xp-tag">Support</span><span class="xp-tag">Upper-strap stability</span><p>Validate expectation gaps against fit, wearing method and use context.</p></div></div><div class="xp-insight"><h3>Validate fit and use conditions before changing the product.</h3><p>Compare wearing conditions, fit range and intended use to distinguish product limitations, user understanding and individual differences.</p></div><button class="xp-text-button" data-evidence>Review the supporting evidence →</button></article>`,
    experience: `<article class="xp-story"><h2>One review can contain several experiences.</h2><p>Overall sentiment cannot replace attribute-level analysis. Limited support should not erase positive feedback about comfort.</p><div class="xp-table-wrap"><table class="xp-table"><thead><tr><th>Experience attribute</th><th>Feedback direction</th><th>Next assessment</th></tr></thead><tbody><tr><td>Softness / Breathability</td><td>Positive experience</td><td>Treat as a potential strength; check whether it recurs across sources</td></tr><tr><td>Discreet under clothing</td><td>Positive experience</td><td>Distinguish clothing conditions; do not generalize the result</td></tr><tr><td>Overall support</td><td>Below expectations</td><td>Compare intended use, size and wearing method</td></tr><tr><td>Upper-strap position</td><td>Concern about movement</td><td>Distinguish an observed issue from an anticipated concern</td></tr></tbody></table></div><div class="xp-note">One review may receive multiple attribute codes. Code counts are not customer counts or market proportions.</div><button class="xp-text-button" data-evidence>Review evidence and coding limitations →</button></article>`,
    context: `<article class="xp-story"><h2>Put the experience back in context.</h2><p>Not every difference comes from the product. Establish the conditions affecting each experience. Do not infer age, body characteristics or health information from images of people.</p><div class="xp-chain"><div><strong>Before purchase</strong><p>What support was expected? Which product information shaped that expectation?</p></div><div><strong>During fitting</strong><p>Are size, adjustment instructions and wearing position clear? Leave unspecified conditions unknown.</p></div><div><strong>Continued use</strong><p>Do comfort and stability change with clothing or activity? Further observation is needed.</p></div></div><div class="xp-paper"><h3>What this example cannot establish</h3><p>Population representativeness, issue prevalence, the impact of product changes, or any medical or health benefits.</p></div></article>`,
    opportunity: `<article class="xp-story"><span class="xp-eyebrow">An opportunity to validate—not an established conclusion</span><h2>Turn the support expectation gap<br>into a testable question.</h2><div class="xp-insight"><h3>Could fitting guidance reduce misunderstandings about fit and support?</h3><p>Observation: comfort praise coexists with unmet support expectations. Counterevidence: other feedback praises back support while raising fit concerns. Validate the conditions before recommending a redesign for greater support.</p></div><div class="xp-duo"><div class="xp-paper"><h3>Validation tasks</h3><ul><li>Review sources and deduplication of supporting and opposing evidence</li><li>Establish wearing position, size and intended use</li><li>Compare current guidance with customer understanding</li></ul></div><div class="xp-paper"><h3>Team handoff</h3><ul><li>Consumer research: organize evidence and its scope of application</li><li>Product research: distinguish design issues from use-related issues</li><li>Content and service: draft guidance for review</li></ul></div></div><p>Deliverables: an evidence package, hypotheses and a validation plan. Do not publish performance claims before validation.</p></article>`,
    evidence: `<article class="xp-story"><h2>Make every conclusion traceable to evidence.</h2><p>Content has been anonymized and paraphrased to illustrate the analysis structure. Original materials and internal business data are not published here.</p><div class="xp-paper"><h3>Compare supporting and opposing evidence</h3><p>Supporting signals: customers value softness, breathability and discretion under clothing, while raising concerns about support and upper-strap position.</p><p>Contrasting signals: positive support experiences also accompany fit concerns. Distinguish the conditions behind each response.</p><button class="xp-text-button" data-evidence>Open evidence details →</button></div><div class="xp-note"><strong>Evidence limitations</strong><p>Coded by one person; consistency has not been independently reviewed. Qualitative examples cannot support sales forecasts, overall satisfaction rates or improvement percentages.</p></div></article>`
  };
  const shopViews = {
    summary: `<article class="xp-story"><span class="xp-eyebrow">Generic assessment example · No live store accessed</span><h2>Make the purchase decision clear<br>before diagnosing growth barriers.</h2><p>Separate visible page issues from questions that require backend data. No fabricated store screenshots or conversion figures are presented here.</p><div class="xp-duo"><div class="xp-paper"><h3>What a page review can assess</h3><ul><li>Whether the intended customer and product differences are clear</li><li>Whether specifications and usage guidance are easy to find</li><li>Whether shipping and return policies are clear</li></ul></div><div class="xp-paper"><h3>What requires additional data</h3><ul><li>Traffic sources and purchase journeys</li><li>Actual inventory and fulfillment performance</li><li>Order, advertising and repeat-purchase performance</li></ul></div></div><div class="xp-insight"><h3>Prepare recommendations. Do not publish on the merchant's behalf.</h3><p>Confirm product facts and validate business conclusions first. Price changes, listings, advertising and order actions each require separate authorization.</p></div></article>`,
    experience: `<article class="xp-story"><h2>Link every issue to its evidence.</h2><div class="xp-table-wrap"><table class="xp-table"><thead><tr><th>Review area</th><th>Review focus</th><th>Required evidence</th></tr></thead><tbody><tr><td>Product page hero</td><td>Intended customers, differentiation and pricing</td><td>Actual page URL and access time; not connected in this preview</td></tr><tr><td>Purchase section</td><td>Specifications, shipping and returns</td><td>Merchant-verified product facts and policies</td></tr><tr><td>Purchase journey</td><td>Whether information gaps exist</td><td>Authorized analytics data; the page alone is not sufficient</td></tr></tbody></table></div></article>`,
    context: `<article class="xp-story"><h2>From page observations to business validation.</h2><div class="xp-chain"><div><strong>Arriving at the store</strong><p>Can customers understand what the brand offers and why it is relevant to them?</p></div><div><strong>Evaluating the product</strong><p>Are product facts, supporting evidence and limitations easy to understand?</p></div><div><strong>Deciding to buy</strong><p>Are pricing, delivery, returns and trust information complete?</p></div></div><div class="xp-note">This is an assessment framework, not a list of issues found in your store. An actual diagnosis requires access to pages within the authorized scope.</div></article>`,
    opportunity: `<article class="xp-story"><h2>Three paths from recommendations to action.</h2><div class="xp-chain"><div><strong>Ready for manual editing</strong><p>Add merchant-verified content and prepare revised copy with a review checklist.</p></div><div><strong>Requires data validation</strong><p>Prioritize using actual journey and order data rather than estimating conversion losses without evidence.</p></div><div><strong>Requires separate authorization</strong><p>Publishing, price changes, advertising and order actions are not executed in this public preview.</p></div></div><div class="xp-paper"><h3>Team responsibilities</h3><p>The store operations lead confirms the objective. Content specialists prepare recommendations, data specialists verify evidence, and permission checks plus independent review control any write actions.</p></div></article>`,
    evidence: `<article class="xp-story"><h2>Keep conclusions within the evidence.</h2><p>Live store evidence, URLs, access times and backend data are not connected. This example illustrates validation rules; it is not a report on your store.</p><div class="xp-paper"><h3>Evidence required for an actual diagnosis</h3><ul><li>Locate page issues precisely and record the access time</li><li>Source product benefits, prices and inventory from merchant-verified records</li><li>State data sources, time windows and limitations for business conclusions</li><li>Stop when access is restricted; do not bypass access controls</li></ul></div></article>`
  };
  function resultTab(tab) {
    $$('#result-tabs button').forEach(b => { b.classList.toggle('active', b.dataset.tab === tab); b.setAttribute('aria-pressed', String(b.dataset.tab === tab)); });
    $('#result-content').innerHTML = (resultScene === 'voc' ? vocViews : shopViews)[tab];
    $$('[data-evidence]').forEach(b => b.addEventListener('click', () => $('#evidence-dialog').showModal()));
  }
  function renderResult(next, example = true) {
    resultScene = next; scene = next;
    if (example && state.task?.status !== 'running') {
      rememberTask(); state.task = { id: `example-${next}`, scene: next, status: 'example' }; persist();
    }
    $('#result-kind').textContent = example ? 'Prepared example · Free to view' : 'Credit demo completed · Prepared example';
    $('#result-title').textContent = next === 'voc' ? 'What do customers value—and what still falls short?' : 'See what to improve next in your store.';
    $('#result-scope-title').textContent = next === 'voc' ? 'Wearable support products' : 'Existing store · Generic assessment';
    $('#result-scope-text').textContent = next === 'voc' ? 'Anonymized qualitative example · Multidimensional coding of individual feedback · Not a basis for market-share or sales estimates' : 'Homepage and product-page review framework · No live store accessed · No order, advertising or inventory data';
    $('#result-cost').textContent = `Credits used: ${example ? 0 : costs[next]} · Remaining: ${available()}`;
    $('#save-result').href = `auth-en.html?${returnParams()}&mode=register`;
    $('#upgrade-result').href = `pricing-en.html?${returnParams()}`;
    resultTab('summary'); show('trial-result');
  }
  if ($('#trial-input')) {
    setScene(scene); credits();
    $$('.xp-scene').forEach(b => b.addEventListener('click', () => setScene(b.dataset.scene)));
    $('#scope-form').addEventListener('submit', e => { e.preventDefault(); confirm(); });
    $('#view-example').addEventListener('click', () => renderResult(scene));
    $('#confirm-example').addEventListener('click', () => renderResult(scene));
    $('#back-input').addEventListener('click', () => show('trial-input'));
    $('#start-demo').addEventListener('click', start);
    $('#finish-demo').addEventListener('click', finish);
    $('#stop-demo').addEventListener('click', stop);
    $('#new-task').addEventListener('click', () => { recentTasks(); show('trial-input'); });
    $('#change-scene').addEventListener('click', () => { recentTasks(); show('trial-input'); });
    $('#continue-analysis').addEventListener('click', confirm);
    $$('#result-tabs button').forEach(b => b.addEventListener('click', () => resultTab(b.dataset.tab)));
    $('#credit-toggle').addEventListener('click', () => { $('#credit-panel').hidden = !$('#credit-panel').hidden; $('#credit-toggle').setAttribute('aria-expanded', String(!$('#credit-panel').hidden)); });
    $('.xp-dialog-close').addEventListener('click', () => $('#evidence-dialog').close());
    const returned = state.history.find(t => t.id === params.get('result'));
    if (returned && state.task?.status !== 'running') { rememberTask(); state.task = { ...returned }; persist(); }
    const task = state.task;
    if (task?.status === 'running') running();
    else if (['voc', 'shopify'].includes(params.get('example'))) renderResult(params.get('example'), true);
    else if (task && ['complete', 'example'].includes(task.status)) {
      if (params.get('result') && params.get('result') !== task.id) message('The requested task is unavailable in this session. No other task was restored. You can reopen a free example.');
      else renderResult(task.scene, task.status === 'example');
    } else if (params.get('step') === 'confirm') confirm();
    if (!storageOK) message('Session storage is unavailable or demo records are invalid. The credit demo is paused; free examples remain available.');
    recentTasks();
  }
  function safeReturn() {
    const requested = params.get('return');
    if (requested === 'trial' || requested === 'trial-en.html') {
      const p = new URLSearchParams();
      ['mission', 'result', 'step', 'lang'].forEach(k => { if (params.has(k)) p.set(k, params.get(k)); });
      return `trial-en.html?${p}`;
    }
    return requested === 'pricing' || requested === 'pricing-en.html' ? 'pricing-en.html' : 'trial-en.html';
  }
  if ($('#auth-form')) {
    let method = 'email';
    let mode = params.get('mode') === 'register' ? 'register' : 'login';
    const feedback = text => { $('#auth-feedback').textContent = text; };
    function updateMode() {
      $$('[data-auth-mode]').forEach(b => { const active = b.dataset.authMode === mode; b.classList.toggle('active', active); b.setAttribute('aria-pressed', String(active)); });
      $('#invite-fields').hidden = mode !== 'register';
      $('#invite-code').required = mode === 'register' && !$('#no-invite').checked;
    }
    $$('.xp-return').forEach(a => a.href = safeReturn());
    $$('[data-auth-mode]').forEach(b => b.addEventListener('click', () => { mode = b.dataset.authMode; updateMode(); }));
    $$('[data-auth-method]').forEach(b => b.addEventListener('click', () => {
      method = b.dataset.authMethod;
      $$('[data-auth-method]').forEach(x => { x.classList.toggle('active', x === b); x.setAttribute('aria-pressed', String(x === b)); });
      const input = $('#auth-contact'); input.value = ''; input.type = method === 'email' ? 'email' : 'tel'; input.autocomplete = method === 'email' ? 'email' : 'tel'; input.placeholder = method === 'email' ? 'you@company.com' : '+Country code and phone number'; $('#contact-label').textContent = method === 'email' ? 'Email' : 'Phone number (with country code)';
      if (method === 'phone') input.pattern = '[+0-9 ()-]{7,22}'; else input.removeAttribute('pattern');
    }));
    $('#no-invite').addEventListener('change', () => { $('#invite-code').disabled = $('#no-invite').checked; updateMode(); });
    $('#request-code').addEventListener('click', () => { if ($('#auth-contact').reportValidity()) feedback('Verification is not connected. No code was sent. Your contact details were neither uploaded nor saved in browser records.'); });
    $('#auth-form').addEventListener('submit', e => { e.preventDefault(); feedback('Identity verification and account saving are unavailable. Return to your task; your existing preview will not incur additional credits.'); });
    updateMode();
  }
  if ($('#account-content')) {
    const titles = { personal: 'Personal details', results: 'My results', credits: 'Credit history', billing: 'Subscription and billing', security: 'Sessions and security' };
    const empty = (title, text, link, label) => `<div class="xp-empty"><h2>${title}</h2><p>${text}</p><a class="r-button small" href="${link}">${label}</a></div>`;
    const views = {
      personal: `<div class="xp-account-row"><strong>Account name</strong><span>Not signed in</span></div><div class="xp-account-row"><strong>Email or phone number</strong><span>Not verified</span></div><div class="xp-account-row"><strong>Current plan</strong><span>Not activated</span></div><div class="xp-note">Access to personal information requires identity verification. Entering an email address alone does not grant account access.</div><a class="r-button small" href="auth-en.html">Go to login</a>`,
      results: empty('No saved results yet', 'Preview results have not been saved to the cloud. Return to the demo to view prepared examples from this session.', 'trial-en.html', 'Back to the demo'),
      credits: empty('Account credits unavailable', 'The account ledger is not connected. An unknown balance is not displayed as zero. Demo credits are retained only in this browser session.', 'trial-en.html', 'View the demo'),
      billing: empty('No active subscription', 'Payments and billing are not connected. No orders or charges will be created.', 'pricing-en.html', 'Explore plan features'),
      security: empty('No verified sessions', 'You are not signed in. No fabricated devices, IP addresses or locations are shown.', 'auth-en.html', 'Go to login')
    };
    function account(tab) { $('#account-title').textContent = titles[tab]; $('#account-content').innerHTML = views[tab]; $$('[data-account]').forEach(b => { b.classList.toggle('active', b.dataset.account === tab); b.setAttribute('aria-pressed', String(b.dataset.account === tab)); }); }
    $$('[data-account]').forEach(b => b.addEventListener('click', () => account(b.dataset.account)));
    account('personal');
  }
})();
