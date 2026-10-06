(() => {
  'use strict';
  const en = document.documentElement.lang.startsWith('en');
  const t = (zh, english) => en ? english : zh;
  const page = name => name + (en ? '-en' : '') + '.html';
  const params = new URLSearchParams(location.search);

  // Legacy URLs share the server-backed account; no local wallet is restored.
  if (document.body.classList.contains('page-profile')) {
    const aliases = { personal: 'subscription', billing: 'subscription', credits: 'credits',
      orders: 'orders', invoices: 'invoices', organization: 'organization',
      procurement: 'procurement', 'payment-methods': 'payment-methods' };
    const requested = location.hash.slice(1) || params.get('tab');
    const target = Object.prototype.hasOwnProperty.call(aliases, requested) ? aliases[requested] : 'subscription';
    location.replace(page('billing') + '#' + target);
    return;
  }

  const root = document.querySelector('#trial-root');
  if (!root) return;
  const $ = selector => root.querySelector(selector);
  let scene = params.get('mission') === 'shopify' || params.get('example') === 'shopify' ? 'shopify' : 'voc';
  const scenes = {
    voc: {
      name: t('VOC 机会洞察', 'VOC opportunity insights'),
      description: t('从消费者反馈找到待验证的机会', 'Find opportunities to validate in customer feedback'),
      input: t('品牌名称', 'Brand name'),
      scope: t('读取公开可访问的消费者反馈，区分体验、使用情境、支持证据与反证。', 'Review publicly accessible customer feedback, separating experiences, context, supporting evidence and counter-evidence.'),
      example: [
        [t('原始线索', 'Source material'), t('反馈来源、读取时间与使用情境；无法确认的条件标为未知。', 'Feedback sources, access dates and usage context. Unverified conditions remain unknown.')],
        [t('证据对照', 'Evidence comparison'), t('认可与顾虑并排呈现，不把单条反馈换算为市场占比。', 'Present praise and concerns together. A single comment cannot establish market prevalence.')],
        [t('待验证机会', 'Opportunity to validate'), t('形成假设、反证、下一步验证与岗位交接，不直接承诺销量改善。', 'Set out hypotheses, counter-evidence, next checks and team handoffs, without claiming a sales uplift.')]
      ]
    },
    shopify: {
      name: t('Shopify 店铺诊断', 'Shopify store diagnosis'),
      description: t('梳理购买信息与页面优化顺序', 'Review buying information and page improvements'),
      input: t('Shopify 店铺链接', 'Shopify store URL'),
      scope: t('检查公开可访问的店铺页面，无需店铺后台授权；不据此推断实际订单、转化率、广告或库存。', 'Review publicly accessible storefront pages without admin access. These pages cannot establish actual orders, conversion rates, ad performance or inventory.'),
      example: [
        [t('页面依据', 'Page evidence'), t('每项观察对应页面位置、URL 与读取时间；受限页面不绕过访问限制。', 'Link observations to page locations, URLs and access dates. Do not bypass access restrictions.')],
        [t('购买信息', 'Buying information'), t('检查商品说明、规格、配送与退换信息，区分缺失信息和待商家确认的事实。', 'Review product details, specifications, shipping and returns, distinguishing missing information from facts the merchant must confirm.')],
        [t('优化建议', 'Recommendations'), t('给出有证据的建议和验证顺序；改价、发布与广告操作需另行授权。', 'Provide evidence-led recommendations and validation priorities. Price changes, publishing and advertising require separate authorization.')]
      ]
    }
  };

  root.innerHTML = `<div class="xp-toolbar"><a href="${page('pricing')}">${t('查看订阅方案', 'View subscription plans')}</a></div>
    <section class="xp-entry" aria-labelledby="trial-title">
      <span class="xp-eyebrow">${t('一次免费体验 · 两个场景任选其一', 'One free trial · Choose one of two scenarios')}</span>
      <h1 id="trial-title">${t('让一个问题，成为下一步行动', 'Turn one question into your next step')}</h1>
      <p class="xp-intro">${t('从消费者的真实体验，或一家正在经营的店铺开始。', 'Start with customer experiences or an active storefront.')}</p>
      <div class="xp-scene-choice" role="group" aria-label="${t('选择体验场景', 'Choose a trial scenario')}">
        ${Object.entries(scenes).map(([key, item]) => `<button type="button" class="xp-scene" data-scene="${key}" aria-pressed="false"><img class="xp-scene-symbol" src="icons/${key === 'voc' ? 'search' : 'boxes'}.svg" width="30" height="30" alt=""><strong>${item.name}</strong><span>${item.description}</span></button>`).join('')}
      </div>
      <section class="xp-prompt trial-scope" aria-labelledby="scope-title">
        <span class="xp-eyebrow">${t('你需要准备', 'What to prepare')}</span>
        <h2 id="scope-title"></h2><p id="scope-description"></p>
        <div class="xp-note" id="trial-availability" role="status"><strong>${t('真实体验暂未开放', 'Live trials are not available yet')}</strong>
          <p>${t('分析服务尚未开放，当前不接收品牌或店铺链接，也不会创建任务或占用体验资格。你可以先了解报告结构，或进入账户与账单。', 'The analysis service is not available yet. This page does not accept brand names or store URLs, create tasks or use a trial entitlement. You can review the report structure or visit account and billing.')}</p></div>
        <div class="xp-actions"><button type="button" class="r-button" id="start-trial" disabled aria-describedby="trial-availability">${t('暂不可开始', 'Not available yet')}</button><button type="button" class="r-button secondary" id="view-example" aria-expanded="false" aria-controls="trial-example">${t('查看示例结构', 'View example structure')}</button><a class="xp-text-button" href="${page('billing')}">${t('进入账户与账单', 'Go to account and billing')} →</a></div>
      </section>
      <section class="trial-policy" aria-labelledby="trial-policy-title"><h2 id="trial-policy-title">${t('免费体验如何计算', 'How the free trial works')}</h2>
        <ul><li>${t('每位用户仅一次成功体验：VOC 机会洞察与 Shopify 店铺诊断二选一，并非每个场景各一次。', 'Each user gets one successful trial: either VOC opportunity insights or Shopify store diagnosis, not one of each.')}</li>
        <li>${t('正式启动后，场景和品牌／店铺对象锁定。可恢复的任务继续原任务；完整报告生成、保存且可查看后才核销资格。', 'Once a live task starts, its scenario and brand or store are locked. Recoverable tasks resume the same run; the entitlement is used only after a complete report is saved and available to view.')}</li>
        <li>${t('体验资格不等于 Sage Credits。查看下方示例不会创建任务、扣减资格或扣除 Sage Credits。', 'A trial entitlement is not Sage Credits. Viewing the examples below creates no task and uses neither your entitlement nor Sage Credits.')}</li></ul>
      </section>
    </section>
    <section id="trial-example" class="trial-example" aria-labelledby="example-title" hidden>
      <span class="xp-eyebrow">${t('静态结构示例 · 不是实际分析结果', 'Static structure example · Not an analysis result')}</span>
      <h2 id="example-title" tabindex="-1"></h2>
      <p>${t('以下只说明正式报告将如何组织信息，未读取任何品牌或店铺，也不代表你已完成免费体验。', 'This illustrates how a report is organized. No brand or store has been analyzed, and no free trial has been completed.')}</p>
      <div class="xp-chain" id="example-content"></div>
    </section>
    <section class="xp-note trial-record" aria-labelledby="saved-trial-title"><h2 id="saved-trial-title">${t('我的体验记录', 'My trial')}</h2><div id="saved-trial" aria-live="polite"></div><button type="button" class="xp-text-button" id="refresh-trial">${t('刷新记录', 'Refresh record')}</button></section>`;

  function setScene(next) {
    scene = next;
    const item = scenes[scene];
    for (const button of root.querySelectorAll('[data-scene]')) {
      const active = button.dataset.scene === scene;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    }
    $('#scope-title').textContent = item.input;
    $('#scope-description').textContent = item.scope;
    $('#example-title').textContent = item.name;
    $('#example-content').replaceChildren(...item.example.map(([title, detail]) => {
      const card = document.createElement('div'), heading = document.createElement('h3'), text = document.createElement('p');
      heading.textContent = title; text.textContent = detail; card.append(heading, text); return card;
    }));
    const url = new URL(location.href);
    url.searchParams.set('mission', scene);
    if (['voc', 'shopify'].includes(url.searchParams.get('example'))) url.searchParams.set('example', scene);
    else url.searchParams.delete('example');
    // Old demo task IDs are not server-backed and cannot restore an entitlement.
    for (const key of ['result', 'step']) url.searchParams.delete(key);
    history.replaceState(null, '', url);
  }

  for (const button of root.querySelectorAll('[data-scene]')) button.addEventListener('click', () => setScene(button.dataset.scene));
  $('#view-example').addEventListener('click', () => {
    const example = $('#trial-example');
    example.hidden = !example.hidden;
    $('#view-example').setAttribute('aria-expanded', String(!example.hidden));
    const url = new URL(location.href);
    if (example.hidden) url.searchParams.delete('example');
    else url.searchParams.set('example', scene);
    history.replaceState(null, '', url);
    if (!example.hidden) $('#example-title').focus();
  });
  setScene(scene);
  function reportReading(report) {
    const article = document.createElement('article'), title = document.createElement('h3');
    article.className = 'trial-report';
    title.textContent = t('已存报告', 'Saved report');
    article.append(title);
    const note = document.createElement('p');
    note.className = 'trial-report-note';
    note.textContent = t('以下按原报告字段展示，不补写结论；JSON 下载保留完整原始内容。', 'The fields below preserve the saved report without adding conclusions. The JSON download contains the complete original content.');
    article.append(note);
    let nodes = 0;
    function content(value, depth) {
      // Keep large or deeply nested reports readable without creating an unbounded DOM.
      // The fallback retains the complete subtree, rather than silently truncating it.
      if (++nodes > 500 || depth > 8 || (value !== null && typeof value === 'object' &&
          Object.keys(value).length > 500 - nodes)) {
        const raw = document.createElement('pre');
        raw.className = 'trial-report-raw';
        raw.textContent = JSON.stringify(value, null, 2);
        return raw;
      }
      if (Array.isArray(value)) {
        if (!value.length) return content(t('空列表', 'Empty list'), depth + 1);
        const list = document.createElement('ol');
        for (const item of value) {
          const entry = document.createElement('li');
          entry.append(content(item, depth + 1)); list.append(entry);
        }
        return list;
      }
      if (value !== null && typeof value === 'object') {
        if (!Object.keys(value).length) return content(t('空对象', 'Empty object'), depth + 1);
        const fields = document.createElement('dl');
        for (const [key, item] of Object.entries(value)) {
          const name = document.createElement('dt'), detail = document.createElement('dd');
          name.textContent = key; detail.append(content(item, depth + 1));
          fields.append(name, detail);
        }
        return fields;
      }
      const text = document.createElement('p');
      text.textContent = value === null ? t('未提供（null）', 'Not provided (null)') :
        value === '' ? t('空文本', 'Empty text') : String(value);
      return text;
    }
    if (Object.keys(report).length > 500) {
      article.append(content(report, 0));
      return article;
    }
    for (const [key, value] of Object.entries(report)) {
      const section = document.createElement('section'), heading = document.createElement('h4');
      heading.textContent = key;
      section.append(heading, content(value, 0)); article.append(section);
    }
    return article;
  }
  let trialReadVersion = 0, reportDownloadUrl = null, reportDownloadTimer = null;
  function clearReportDownload() {
    if (reportDownloadTimer !== null) clearTimeout(reportDownloadTimer);
    if (reportDownloadUrl !== null) URL.revokeObjectURL(reportDownloadUrl);
    reportDownloadTimer = null; reportDownloadUrl = null;
  }
  async function loadTrial() {
    const version = ++trialReadVersion;
    const target = $('#saved-trial'), refresh = $('#refresh-trial');
    clearReportDownload();
    refresh.disabled = true;
    target.textContent = t('正在读取你的体验记录…', 'Loading your trial…');
    try {
      const response = await fetch('/api/trial', {credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(15000)});
      if (version !== trialReadVersion) return;
      if (response.status === 401) {
        const link = document.createElement('a');
        // Use only this page's current context; auth.safeReturn remains the
        // destination allowlist. Never forward a caller-supplied return URL.
        const updateReturn = () => { link.href = page('auth') + '?return=' + encodeURIComponent(location.pathname + location.search + location.hash); };
        updateReturn(); link.addEventListener('click', updateReturn);
        link.textContent = t('登录后查看你的体验资格与原任务', 'Sign in to view your trial entitlement and original task');
        target.replaceChildren(link); return;
      }
      if (!response.ok) throw new Error('TRIAL_READ_FAILED');
      const data = await response.json();
      if (version !== trialReadVersion) return;
      if (!['available', 'locked', 'consumed'].includes(data.eligibility)) throw new Error('INVALID_TRIAL_RESPONSE');
      const attempt = data.attempt;
      const states = {
        running: t('原任务处理中，刷新可查看最新状态。', 'Your original task is in progress. Refresh for an update.'),
        cancelled: t('原任务已取消，体验对象仍锁定。当前暂不支持恢复任务。', 'The original task was cancelled. Its subject remains locked; resuming is not available yet.'),
        recoverable_failure: t('原任务遇到可恢复问题，尚未核销资格。当前暂不支持重试。', 'The original task has a recoverable issue. Your entitlement has not been used; retry is not available yet.'),
        completed: t('报告已保存，本次免费体验已完成。', 'Your report is saved and your free trial is complete.')
      };
      if (!attempt) {
        if (data.eligibility !== 'available') throw new Error('INVALID_TRIAL_RESPONSE');
        target.textContent = t('你仍有一次免费体验资格。分析服务开放后，可选择一个场景开始。', 'You have one free trial available. Choose a scenario when the analysis service opens.');
        return;
      }
      if (!states[attempt.task_state] || typeof attempt.input_value !== 'string' ||
          !['voc', 'shopify_diagnosis'].includes(attempt.scenario) ||
          data.eligibility !== (attempt.task_state === 'completed' ? 'consumed' : 'locked')) throw new Error('INVALID_TRIAL_RESPONSE');
      if (attempt.task_state === 'completed' && (!attempt.report || typeof attempt.report !== 'object' ||
          Array.isArray(attempt.report) || !Object.keys(attempt.report).length)) throw new Error('INVALID_TRIAL_RESPONSE');
      const heading = document.createElement('h3'), subject = document.createElement('p'), status = document.createElement('p');
      heading.textContent = scenes[attempt.scenario === 'voc' ? 'voc' : 'shopify'].name;
      subject.textContent = attempt.input_value;
      status.textContent = states[attempt.task_state];
      target.replaceChildren(heading, subject, status);
      if (attempt.task_state === 'completed') {
        const download = document.createElement('button');
        download.type = 'button'; download.className = 'xp-text-button';
        download.textContent = t('下载已存报告（JSON）', 'Download saved report (JSON)');
        download.addEventListener('click', () => {
          if (version !== trialReadVersion) return;
          clearReportDownload();
          reportDownloadUrl = URL.createObjectURL(new Blob([JSON.stringify(attempt.report, null, 2)], {type: 'application/json'}));
          const link = document.createElement('a'); link.href = reportDownloadUrl; link.download = 'sage-trial-report.json';
          link.click(); reportDownloadTimer = setTimeout(clearReportDownload, 1000);
        });
        target.append(download, reportReading(attempt.report));
      }
    } catch {
      if (version !== trialReadVersion) return;
      target.textContent = t('暂时无法读取体验记录，资格状态未知。请刷新重试；这不会创建任务或消耗资格。', 'Your trial record could not be loaded, so entitlement status is unknown. Refresh to retry; this will not create a task or use your entitlement.');
    } finally { if (version === trialReadVersion) refresh.disabled = false; }
  }
  $('#refresh-trial').addEventListener('click', loadTrial);
  window.addEventListener('pagehide', () => {
    ++trialReadVersion;
    clearReportDownload();
    $('#saved-trial').replaceChildren();
    $('#refresh-trial').disabled = true;
  });
  window.addEventListener('pageshow', event => { if (event.persisted) loadTrial(); });
  loadTrial();
  if (['voc', 'shopify'].includes(params.get('example'))) {
    $('#trial-example').hidden = false;
    $('#view-example').setAttribute('aria-expanded', 'true');
  }
})();
