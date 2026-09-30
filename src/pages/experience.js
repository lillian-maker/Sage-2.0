(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const params = new URLSearchParams(location.search);
  const KEY = 'sage.experience.v1';
  const costs = { voc: 30, shopify: 50 };
  const names = { voc: 'VOC 机会洞察', shopify: 'Shopify 店铺诊断' };
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
    catch (_) { storageOK = false; message('浏览器会话存储不可用，无法恢复演示任务。请保持当前页面；免费案例仍可查看。'); return false; }
  }
  function message(text) { if ($('#xp-message')) $('#xp-message').textContent = text; }
  function reserved() { return state.task?.status === 'running' ? costs[state.task.scene] : 0; }
  function available() { return 100 - state.spent - reserved(); }
  function credits() {
    if ($('#credit-count')) $('#credit-count').textContent = String(available());
    if ($('#credit-detail')) $('#credit-detail').textContent = `预留积分 ${reserved()} · 累计演示消耗 ${state.spent} 积分。演示状态仅在当前浏览器会话保留。`;
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
    $('#scope-summary').textContent = scene === 'voc' ? '查看消费者体验结构、证据对照与待验证机会。当前使用脱敏定性样例。' : '查看已有店铺的购买信息诊断、优化建议与授权边界。当前使用通用检查示例。';
    $('#confirm-available').textContent = String(available());
    $('#confirm-estimate').textContent = `${costs[scene]} 积分`;
    $('#confirm-maximum').textContent = `${costs[scene]} 积分`;
    $('#confirm-after').textContent = available() >= costs[scene] ? `${available() - costs[scene]} 积分` : '积分不足';
    $('#start-demo').disabled = available() < costs[scene] || !storageOK;
    $('#confirmation-limit').textContent = !storageOK ? '会话存储不可用，暂停积分交互演示；可以免费查看案例。' : available() < costs[scene] ? `还需 ${costs[scene] - available()} 积分才能继续本次演示。已有成果保留可读；查看会员方案不会自动充值。` : '点击开始后先预留最高消耗；只有点击完成演示才结算。停止演示全额退还预留积分。';
    $('#confirm-pricing').href = `pricing.html?${new URLSearchParams({ return: 'trial', mission: scene, step: 'confirm' })}`;
    show('trial-confirm');
  }
  function start() {
    if (state.task?.status === 'running' || !storageOK || available() < costs[scene]) return;
    const task = { id: `demo-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, scene, status: 'running' };
    rememberTask(); state.task = task;
    state.ledger.push({ task: task.id, scene, type: '预留积分', amount: costs[scene], at: new Date().toISOString() });
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
    const title = document.createElement('strong'); title.textContent = '当前会话的成果'; box.append(title);
    all.slice(-6).reverse().forEach(t => {
      const row = document.createElement('div'); const button = document.createElement('button'); button.className = 'xp-text-button';
      button.textContent = `${names[t.scene]} · ${t.status === 'complete' ? '演示已完成' : '免费案例'} →`;
      button.addEventListener('click', () => { rememberTask(); state.task = { ...t }; persist(); renderResult(t.scene, t.status === 'example'); }); row.append(button); box.append(row);
    });
  }
  function running() {
    scene = state.task.scene;
    $('#running-reserved').textContent = `${reserved()} 积分`;
    $('#running-available').textContent = `${available()} 积分`;
    show('trial-running');
  }
  function finish() {
    if (state.task?.status !== 'running') return;
    const cost = costs[state.task.scene];
    state.spent += cost;
    state.task.status = 'complete';
    state.ledger.push({ task: state.task.id, scene: state.task.scene, type: '本次消耗', amount: cost, at: new Date().toISOString() });
    persist(); renderResult(state.task.scene, false);
    message(`演示已完成，本次消耗 ${cost} 积分。刷新或返回本任务不会再次扣除。`);
  }
  function stop() {
    if (state.task?.status !== 'running') return;
    const cost = costs[state.task.scene];
    state.task.status = 'stopped';
    state.ledger.push({ task: state.task.id, scene: state.task.scene, type: '退还预留积分', amount: cost, at: new Date().toISOString() });
    persist(); show('trial-input');
    message(`演示已停止，退还预留积分 ${cost}，本次消耗 0 积分。可以继续免费查看案例。`);
  }
  const vocViews = {
    summary: `<article class="xp-story"><span class="xp-eyebrow">核心洞察</span><h2>舒适得到认可，<br>不意味着支撑期待已被满足。</h2><p>把正负感受分开看，才能发现体验之间的落差。以下是脱敏定性样例，不代表市场结论。</p><div class="xp-duo"><div class="xp-paper"><h3>被认可的体验</h3><span class="xp-tag">柔软</span><span class="xp-tag">透气</span><span class="xp-tag">衣下低可见</span><p>产品在触感和日常穿搭上形成了正向体验。</p></div><div class="xp-paper"><h3>尚未满足的期待</h3><span class="xp-tag">支撑感</span><span class="xp-tag">上带稳定</span><p>期待落差需要结合佩戴方式与使用情境验证。</p></div></div><div class="xp-insight"><h3>先验证适配与使用条件，再决定是否改产品。</h3><p>对照不同佩戴条件、适配范围和使用目的，区分产品限制、使用理解与个体差异。</p></div><button class="xp-text-button" data-evidence>查看这条判断的依据 →</button></article>`,
    experience: `<article class="xp-story"><h2>同一条反馈，保留多种感受。</h2><p>整体情绪不能代替属性分析；“支撑不足”也不应抹掉舒适性优势。</p><div class="xp-table-wrap"><table class="xp-table"><thead><tr><th>体验属性</th><th>反馈方向</th><th>下一步判断</th></tr></thead><tbody><tr><td>柔软 / 透气</td><td>正向体验</td><td>作为候选优势，检查跨来源是否重复出现</td></tr><tr><td>衣下低可见</td><td>正向体验</td><td>区分服装条件，不推广为普遍效果</td></tr><tr><td>总体支撑</td><td>低于期待</td><td>比较使用目的、尺码与佩戴方式</td></tr><tr><td>上带位置</td><td>移动顾虑</td><td>确认是观察到的问题还是预期担忧</td></tr></tbody></table></div><div class="xp-note">一条评论可有多条属性编码；编码数量不是用户人数或市场占比。</div><button class="xp-text-button" data-evidence>查看证据与编码边界 →</button></article>`,
    context: `<article class="xp-story"><h2>把体验，放回使用情境。</h2><p>并非所有差异都来自产品。先补齐影响体验的条件，不从人物图片推断年龄、身体或健康信息。</p><div class="xp-chain"><div><strong>购买之前</strong><p>期待什么支撑？原先依据了哪些商品信息？</p></div><div><strong>穿戴过程</strong><p>尺码、调整方法与佩戴位置是否明确？未提供的条件保留未知。</p></div><div><strong>持续使用</strong><p>衣着与活动变化时，舒适和稳定是否变化？需要补充观察。</p></div></div><div class="xp-paper"><h3>当前不能回答</h3><p>人群代表性、问题发生比例、产品改版效果，以及任何医疗或健康功效。</p></div></article>`,
    opportunity: `<article class="xp-story"><span class="xp-eyebrow">待验证机会，不是既定结论</span><h2>把“支撑期待落差”<br>变成一个可验证的问题。</h2><div class="xp-insight"><h3>佩戴引导能否减少适配与支撑理解偏差？</h3><p>观察：舒适认可与支撑落差并存。反证：另有反馈认可背部支撑，却仍担心适配。先验证条件，不直接推出“加强支撑”的改款结论。</p></div><div class="xp-duo"><div class="xp-paper"><h3>验证任务</h3><ul><li>复核正反向证据的来源与去重关系</li><li>补充佩戴位置、尺码与使用目的</li><li>比较现有引导与用户理解</li></ul></div><div class="xp-paper"><h3>团队交接</h3><ul><li>消费者研究：整理证据与适用边界</li><li>产品研究：区分设计与使用问题</li><li>内容与服务：形成待审核的引导稿</li></ul></div></div><p>交付：证据包、待验证假设、验证计划。未经验证，不发布效果承诺。</p></article>`,
    evidence: `<article class="xp-story"><h2>每个判断，都留一扇回看证据的门。</h2><p>当前内容经过脱敏与改写，展示分析结构；原始材料及内部经营数据不在此公开。</p><div class="xp-paper"><h3>支持与反证并排看</h3><p>支持线索：柔软、透气与衣下低可见获得认可，支撑与上带位置存在顾虑。</p><p>对照线索：另有正向支撑体验伴随适配顾虑，需要按条件区分。</p><button class="xp-text-button" data-evidence>打开证据详情 →</button></div><div class="xp-note"><strong>证据限制</strong><p>单人编码，未完成一致性复核；定性样例不能转化为销量预测、总体满意率或改善比例。</p></div></article>`
  };
  const shopViews = {
    summary: `<article class="xp-story"><span class="xp-eyebrow">通用诊断示例 · 未抓取实际店铺</span><h2>先让购买依据清楚，<br>再判断增长问题。</h2><p>将页面上能观察的问题，与需要后台数据验证的问题分开。此处不展示伪造的店铺截图或转化数据。</p><div class="xp-duo"><div class="xp-paper"><h3>页面可检查</h3><ul><li>产品适用对象和差异是否清楚</li><li>规格、使用方式是否便于找到</li><li>配送与退换承诺是否明确</li></ul></div><div class="xp-paper"><h3>需要额外数据</h3><ul><li>流量来源与购买路径</li><li>库存和履约实际情况</li><li>订单、广告与复购表现</li></ul></div></div><div class="xp-insight"><h3>输出建议稿，不替商家直接发布。</h3><p>商品事实先确认，经营结论先验证。改价、上架、广告及订单动作都需要单独授权。</p></div></article>`,
    experience: `<article class="xp-story"><h2>问题清单，与证据位置对应。</h2><div class="xp-table-wrap"><table class="xp-table"><thead><tr><th>检查位置</th><th>检查内容</th><th>需要的证据</th></tr></thead><tbody><tr><td>商品首屏</td><td>适用对象、差异与价格信息</td><td>实际页面 URL 与抓取时间，当前未接入</td></tr><tr><td>购买区</td><td>规格、配送与退换信息</td><td>商家确认的商品事实与政策</td></tr><tr><td>购买路径</td><td>是否存在信息缺口</td><td>经过授权的分析数据，不能仅凭页面推断</td></tr></tbody></table></div></article>`,
    context: `<article class="xp-story"><h2>从页面观察，到经营验证。</h2><div class="xp-chain"><div><strong>进入店铺</strong><p>用户是否知道品牌提供什么，以及为什么适合自己。</p></div><div><strong>评估商品</strong><p>产品事实、证明材料与适用边界是否易于理解。</p></div><div><strong>决定购买</strong><p>价格、物流、退换与信任信息是否齐全。</p></div></div><div class="xp-note">以上是检查框架，不是已发现的店铺问题。实际诊断需读取授权范围内的页面。</div></article>`,
    opportunity: `<article class="xp-story"><h2>优化建议，分三类推进。</h2><div class="xp-chain"><div><strong>可人工修改</strong><p>补齐经商家核实的内容，形成修改稿与审核清单。</p></div><div><strong>需数据验证</strong><p>通过真实路径与订单数据判断优先级，而非猜测转化损失。</p></div><div><strong>需单独授权</strong><p>发布、调价、广告和订单动作不在本次公开页面体验中执行。</p></div></div><div class="xp-paper"><h3>团队分工</h3><p>店铺经营负责人确认目标；内容岗位组织建议；数据岗位核对证据；涉及写入时由权限与独立复核环节把关。</p></div></article>`,
    evidence: `<article class="xp-story"><h2>能看到什么，就只判断什么。</h2><p>实际店铺的页面证据、URL、读取时间与后台数据均未接入。这里展示验证规则，不冒充你的店铺报告。</p><div class="xp-paper"><h3>正式诊断的证据要求</h3><ul><li>页面问题附原页面定位与读取时间</li><li>商品功效、价格、库存来自商家核实资料</li><li>经营判断注明数据来源、时间窗口与限制</li><li>抓取受限时明确停止，不绕过访问限制</li></ul></div></article>`
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
    $('#result-kind').textContent = example ? '预置案例 · 免费查看' : '积分交互演示完成 · 预置案例';
    $('#result-title').textContent = next === 'voc' ? '用户认可什么，又为什么没有完全满意？' : '看清店铺的下一步优化顺序。';
    $('#result-scope-title').textContent = next === 'voc' ? '穿戴支撑类产品' : '已有店铺 · 通用检查';
    $('#result-scope-text').textContent = next === 'voc' ? '脱敏定性样例 · 单条反馈的多维编码 · 不用于市场比例或销量推断' : '首页与商品页的检查框架 · 未读取实际店铺 · 无订单、广告或库存数据';
    $('#result-cost').textContent = `本次消耗 ${example ? 0 : costs[next]} 积分 · 剩余积分 ${available()}`;
    $('#save-result').href = `auth.html?${returnParams()}&mode=register`;
    $('#upgrade-result').href = `pricing.html?${returnParams()}`;
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
      if (params.get('result') && params.get('result') !== task.id) message('指定任务在当前会话中不可用，未恢复其他任务。可以重新查看免费案例。');
      else renderResult(task.scene, task.status === 'example');
    } else if (params.get('step') === 'confirm') confirm();
    if (!storageOK) message('会话存储不可用或演示记录损坏。积分交互演示暂停，免费案例仍可查看。');
    recentTasks();
  }
  function safeReturn() {
    const requested = params.get('return');
    if (requested === 'trial' || requested === 'trial.html') {
      const p = new URLSearchParams();
      ['mission', 'result', 'step', 'lang'].forEach(k => { if (params.has(k)) p.set(k, params.get(k)); });
      return `trial.html?${p}`;
    }
    return requested === 'pricing' || requested === 'pricing.html' ? 'pricing.html' : 'trial.html';
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
      const input = $('#auth-contact'); input.value = ''; input.type = method === 'email' ? 'email' : 'tel'; input.autocomplete = method === 'email' ? 'email' : 'tel'; input.placeholder = method === 'email' ? 'you@company.com' : '+86 请输入手机号码'; $('#contact-label').textContent = method === 'email' ? '邮箱' : '手机号码（含区号）';
      if (method === 'phone') input.pattern = '[+0-9 ()-]{7,22}'; else input.removeAttribute('pattern');
    }));
    $('#no-invite').addEventListener('change', () => { $('#invite-code').disabled = $('#no-invite').checked; updateMode(); });
    $('#request-code').addEventListener('click', () => { if ($('#auth-contact').reportValidity()) feedback('验证服务尚未接通，未发送验证码。你的联系方式未上传，也未保存在浏览器记录中。'); });
    $('#auth-form').addEventListener('submit', e => { e.preventDefault(); feedback('当前无法验证身份或保存到账户。请返回原任务，已有预览不会因此扣除积分。'); });
    updateMode();
  }
  if ($('#account-content')) {
    const titles = { personal: '个人信息', results: '我的成果', credits: '积分明细', billing: '订阅与账单', security: '会话与安全' };
    const empty = (title, text, link, label) => `<div class="xp-empty"><h2>${title}</h2><p>${text}</p><a class="r-button small" href="${link}">${label}</a></div>`;
    const views = {
      personal: `<div class="xp-account-row"><strong>账户名称</strong><span>尚未登录</span></div><div class="xp-account-row"><strong>邮箱或手机号</strong><span>尚未验证</span></div><div class="xp-account-row"><strong>当前方案</strong><span>未开通</span></div><div class="xp-note">真实个人信息需要身份验证。仅填写邮箱不能访问任何账户。</div><a class="r-button small" href="auth.html">前往登录</a>`,
      results: empty('还没有已保存成果', '体验结果尚未保存到云端。当前会话的预置案例可返回体验页查看。', 'trial.html', '返回免费体验'),
      credits: empty('账户积分暂不可用', '尚未接通账户账本，不将未知的剩余积分显示为 0。体验演示积分仅保存在当前浏览器会话。', 'trial.html', '查看体验演示'),
      billing: empty('尚未开通订阅', '支付与账单服务尚未接通，不会产生订单或扣款。', 'pricing.html', '查看方案权益'),
      security: empty('暂无已验证会话', '当前页面不是登录状态，不展示虚构设备、IP 或位置。', 'auth.html', '前往登录')
    };
    function account(tab) { $('#account-title').textContent = titles[tab]; $('#account-content').innerHTML = views[tab]; $$('[data-account]').forEach(b => { b.classList.toggle('active', b.dataset.account === tab); b.setAttribute('aria-pressed', String(b.dataset.account === tab)); }); }
    $$('[data-account]').forEach(b => b.addEventListener('click', () => account(b.dataset.account)));
    account('personal');
  }
})();
