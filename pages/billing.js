(() => {
  'use strict';
  const en = document.documentElement.lang === 'en';
  const t = (zh, english) => en ? english : zh;
  const root = document.querySelector('#billing-root');
  const view = document.body.dataset.billingPage;
  const page = name => `${name}${en ? '-en' : ''}.html`;
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon = name => `<img class="ui-icon" src="icons/${name}.svg" alt="" aria-hidden="true">`;
  const digits = (value, exact) => ({minimumFractionDigits:exact || value % 100 ? 2 : 0, maximumFractionDigits:2});
  const money = (value, exact = false) => {
    if (typeof value === 'string' && /^\d+$/.test(value)) {
      const minor = BigInt(value), fraction = minor % 100n;
      return `${en ? 'CN¥' : '¥'}${new Intl.NumberFormat(en ? 'en-US' : 'zh-CN').format(minor / 100n)}${exact || fraction ? '.' + String(fraction).padStart(2,'0') : ''}`;
    }
    return new Intl.NumberFormat(en ? 'en-US' : 'zh-CN', {style:'currency',currency:'CNY',...digits(value, exact)}).format(value / 100);
  };
  const amountMarkup = (value, exact = false) => `<span class="money" aria-label="${money(value, exact)}"><span class="currency-symbol" aria-hidden="true">¥</span><span aria-hidden="true">${money(value, exact).replace(/^(CN¥|¥)/,'')}</span></span>`;
  const initial = new URLSearchParams(location.search);
  let catalog, online = false;
  let audience = initial.get('audience') === 'business' ? 'business' : 'personal';
  let cycle = initial.get('cycle') === 'annual' ? 'annual' : 'monthly';
  let seats = /^[0-9]+$/.test(initial.get('seats') || '') ? Number(initial.get('seats')) : 2;
  if (!Number.isSafeInteger(seats) || seats < 2 || seats > 10000) seats = 2;
  const boundary = t('订阅服务暂未开放购买。你可以查看方案；开通后将明确展示权益和费用。','Subscriptions are not yet available to purchase. Explore the plans now; full benefits and charges will be shown before purchases open.');
  let session = null, sessionState = 'loading', sessionBusy = false, accountVersion = 0, sessionVersion = 0;
  let sessionNotice = '', logoutToken = null, logoutPending = false, logoutUserId = null;
  let purchaseRefresh = null, purchaseCatalog = null;
  let pricingSubscriptions = null, pricingSubscriptionState = 'idle', pricingReadVersion = 0;
  const accountUnavailable = t('账户服务暂时无法连接，当前数据未知。请重新连接；不会把无法读取的余额当作 0。','Account services cannot be reached. Your data is unknown. Reconnect; unavailable balances are never shown as zero.');
  const authURL = () => `${page('auth')}?${new URLSearchParams({return:location.pathname + location.search + location.hash})}`;
  const requestError = (error, fallback) => ({FORBIDDEN:t('你没有办理此操作的权限，请切换有权限的账户或联系管理员。','You do not have permission for this action. Switch to an authorized account or contact your admin.'),CSRF_FAILED:t('页面验证已失效，请重新连接账户服务后再试。','Page verification has expired. Reconnect to account services and retry.'),ACCOUNT_CHANGED:t('计费账户已在其他页面变更，旧账户数据已清除。请重新连接并核对账户；操作不会自动重试。','The billing account changed on another page. Previous account data has been cleared. Reconnect and review the account; the action will not retry automatically.')})[error.message] || fallback;
  const activeAccount = () => session?.accounts.find(item => item.id === session.active_account_id);
  const accountName = account => account.kind === 'personal' && account.name === '个人账户' ? t('个人账户','Personal account') : account.name;
  function validSession(value) {
    return value && typeof value.authenticated === 'boolean' && typeof value.csrf_token === 'string' && Array.isArray(value.accounts) && value.accounts.every(item => typeof item.id === 'string' && ['personal','business'].includes(item.kind)) && (!value.authenticated || value.user?.id);
  }
  async function api(path, body, token = session?.csrf_token, headers = {}) {
    const requestSessionVersion = sessionVersion;
    const options = {credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(10000),headers:{Accept:'application/json'}};
    if (path.startsWith('/api/billing/') && path !== '/api/billing/accounts/select' && session?.active_account_id) options.headers['X-Billing-Account'] = session.active_account_id;
    if (body !== undefined) {
      if (!token) throw new Error('SESSION_UNAVAILABLE');
      options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.headers['X-CSRFToken'] = token; options.body = JSON.stringify(body);
      Object.assign(options.headers, headers);
    }
    const response = await fetch(path, options);
    if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('SERVICE_UNAVAILABLE');
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error?.code || 'REQUEST_FAILED'); error.status = response.status;
      const currentAccount = requestSessionVersion === sessionVersion && options.headers['X-Billing-Account'] && options.headers['X-Billing-Account'] === session?.active_account_id;
      if (currentAccount && (error.message === 'ACCOUNT_CHANGED' || response.status === 401)) {
        accountVersion++; sessionVersion++; session = null;
        sessionState = response.status === 401 ? 'signed-out' : 'unavailable';
        sessionNotice = response.status === 401 ? t('登录已失效，账户数据与未提交表单已清除。请重新登录后继续。','Your session has expired. Account data and unsubmitted forms have been cleared. Sign in again to continue.') : requestError(error, '');
        refreshSessionView();
      }
      throw error;
    }
    return result;
  }
  async function loadSession() {
    const version = ++sessionVersion; accountVersion++; session = null; sessionBusy = false; logoutToken = null; sessionState = 'loading';
    sessionNotice = logoutPending ? t('本页账户数据已清除，正在重新确认退出状态。','Account data is cleared from this page. Rechecking sign-out status.') : ''; refreshSessionView();
    try {
      const result = await api('/api/auth/session');
      if (version !== sessionVersion) return;
      if (!validSession(result)) throw new Error('INVALID_SESSION');
      if (logoutPending && result.authenticated) {
        logoutToken = result.csrf_token; logoutUserId = result.user.id; sessionState = 'unavailable';
        const identity = result.user.masked_identifier || result.user.display_name || t('当前账户','the current account');
        sessionNotice = t(`当前仍登录 ${identity}，退出尚未确认。确认这是你要退出的账户后，请重试退出登录。`, `Still signed in as ${identity}; sign-out is not confirmed. Check that this is the account you intend to sign out, then retry sign-out.`);
      } else {
        session = result; sessionState = result.authenticated ? 'authenticated' : 'signed-out'; sessionNotice = '';
        if (logoutPending) { logoutPending = false; logoutUserId = null; clearOrderRecovery(); }
      }
    } catch (_) {
      if (version === sessionVersion) {
        sessionState = 'unavailable';
        if (logoutPending) sessionNotice = t('本页账户数据已清除，但服务端退出尚未确认，请重试退出。','Account data is cleared from this page, but server sign-out is not confirmed. Retry sign-out.');
      }
    }
    finally { if (version === sessionVersion) refreshSessionView(); }
  }
  function refreshSessionView() {
    renderAccountChrome();
    if (view === 'account' && catalog) account();
    if (view === 'checkout' && purchaseRefresh) purchaseRefresh();
    if (view === 'pricing' && catalog) {
      purchaseCatalog = null; pricingSubscriptions = null; pricingReadVersion++;
      pricingSubscriptionState = session?.authenticated && activeAccount() && !sessionBusy ? 'loading' : 'idle';
      pricing(); loadPurchaseCatalog();
    }
  }
  function renderAccountChrome() {
    const menu = document.querySelector('.account-dropdown');
    if (!menu) return;
    const account = activeAccount();
    document.querySelector('.nav-account').hidden = !session?.authenticated;
    document.querySelector('.nav-login').hidden = Boolean(session?.authenticated);
    document.querySelector('.nav-login').textContent = t('登录','Sign in');
    const label = document.querySelector('.nav-account summary span');
    label.textContent = session?.authenticated ? (session.user.display_name || session.user.masked_identifier || t('已登录','Signed in')) : t('登录','Sign in');
    label.title = label.textContent;
    let identity;
    if (session?.authenticated) {
      identity = `<p class="account-identifier">${escape(session.user.masked_identifier || '')}</p><label for="billing-account-select">${t('当前计费账户','Current billing account')}</label><select id="billing-account-select" ${sessionBusy ? 'disabled' : ''}>${session.accounts.map(item => `<option value="${escape(item.id)}" ${item.id === session.active_account_id ? 'selected' : ''}>${escape(accountName(item))} · ${item.kind === 'personal' ? t('个人','Personal') : t('企业','Business')}</option>`).join('')}</select><button type="button" class="text-link" id="account-logout" ${sessionBusy ? 'disabled' : ''}>${t('退出登录','Sign out')}</button>`;
    } else {
      const stateText = sessionState === 'loading' ? t('正在检查登录状态…','Checking sign-in status…') : sessionState === 'signed-out' ? t('登录后查看你的订阅与账单。','Sign in to view your subscriptions and billing.') : accountUnavailable;
      identity = `<p>${stateText}</p><a class="account-login" href="${escape(authURL())}">${t('登录 / 注册','Sign in / Register')}</a>${sessionState === 'unavailable' ? `<button type="button" class="text-link" id="session-retry">${t('重新连接','Reconnect')}</button>` : ''}`;
    }
    document.querySelector('#billing-account-context')?.remove();
    if(session?.authenticated){
      const context=document.createElement('section');context.id='billing-account-context';context.className='account-context';context.setAttribute('aria-label',t('计费账户','Billing account'));context.innerHTML=identity.replace(/<button[^>]+id="account-logout"[\s\S]*?<\/button>/,'');document.querySelector('#main').prepend(context);
      identity=`<a href="${page('profile')}">${t('个人设置','Personal settings')}</a><button type="button" id="account-logout" ${sessionBusy?'disabled':''}>${t('退出登录','Sign out')}</button>`;
    }
    menu.innerHTML = `${identity}<p class="field-error" id="session-notice" role="status">${escape(sessionNotice)}</p>${logoutPending ? `<button type="button" class="text-link" id="logout-retry" ${sessionBusy || sessionState === 'loading' ? 'disabled' : ''}>${t('重试退出登录','Retry sign-out')}</button>` : ''}`;
    document.querySelector('#session-retry')?.addEventListener('click', loadSession);
    document.querySelector('#account-logout')?.addEventListener('click', logout);
    document.querySelector('#logout-retry')?.addEventListener('click', logout);
    document.querySelector('#billing-account-select')?.addEventListener('change', selectAccount);
    document.querySelector('#session-alert')?.remove();
    if (sessionNotice) {
      const alert = document.createElement('div'); alert.id = 'session-alert'; alert.className = 'notice error'; alert.setAttribute('role', 'alert');
      alert.textContent = sessionNotice;
      if (logoutPending) {
        const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'button'; retry.textContent = t('重试退出登录','Retry sign-out'); retry.disabled = sessionBusy || sessionState === 'loading'; retry.onclick = logout; alert.append(retry);
      }
      document.querySelector('main')?.prepend(alert);
    }
    const row = document.querySelector('.billing-identity .identity-row');
    if (row) row.innerHTML = `${icon('person')}<div><strong>${escape(account ? accountName(account) : t('尚未确认计费账户','Billing account not confirmed'))}</strong><p>${session?.authenticated ? t('当前账户仅用于查看方案；订阅支付尚未开放。','This account is used to review plans; subscription payment is not yet available.') : t('请先登录并确认归属，当前不接受付款。','Sign in to confirm your billing account. No payment is accepted.')}</p>${!session?.authenticated ? `<a class="text-link" href="${escape(authURL())}">${t('登录 / 注册','Sign in / Register')}</a>` : ''}</div>${icon('lock')}`;
  }
  async function selectAccount(event) {
    const id = event.target.value;
    if (sessionBusy || !session?.accounts.some(item => item.id === id)) return;
    const version = ++sessionVersion; accountVersion++; sessionBusy = true; sessionNotice = '';
    const token = session.csrf_token;
    // Clear the prior account immediately; never keep it visible while a selection is pending.
    session = {...session,active_account_id:null}; refreshSessionView();
    try {
      const result = await api('/api/billing/accounts/select', {account_id:id}, token);
      if (version !== sessionVersion) return;
      if (!validSession(result) || !result.authenticated || result.active_account_id !== id) throw new Error('INVALID_SESSION');
      session = result; sessionState = 'authenticated';
    } catch (error) {
      if (version !== sessionVersion) return;
      session = null; sessionState = 'unavailable'; sessionNotice = requestError(error, t('账户切换未能确认，请重新连接。原账户数据已从本页清除。','Account switching could not be confirmed. Reconnect; the previous account data has been cleared.'));
    } finally { if (version === sessionVersion) { sessionBusy = false; refreshSessionView(); } }
  }
  async function logout() {
    if (sessionBusy || sessionState === 'loading') return;
    if (logoutPending) {
      const target = logoutUserId, recheckVersion = sessionVersion + 1;
      // An explicit retry first confirms the current identity and a fresh CSRF token.
      // If identity changed, disclose it and require another explicit confirmation.
      await loadSession();
      if (sessionVersion !== recheckVersion || !logoutPending || !logoutToken || target !== logoutUserId) return;
    } else logoutUserId = session?.user?.id || null;
    const token = logoutToken || session?.csrf_token;
    if (!token) return;
    const version = ++sessionVersion; accountVersion++; logoutPending = true; logoutToken = null; session = null; sessionBusy = true; sessionState = 'loading'; sessionNotice = ''; refreshSessionView();
    try {
      const result = await api('/api/auth/logout', {}, token);
      if (version !== sessionVersion) return;
      if (result.ok !== true) throw new Error('LOGOUT_UNCONFIRMED');
      logoutPending = false; logoutUserId = null; sessionState = 'signed-out'; clearOrderRecovery();
    } catch (_) {
      if (version !== sessionVersion) return;
      sessionState = 'unavailable'; sessionNotice = t('本页账户数据已清除，但服务端退出尚未确认，请重试退出。','Account data is cleared from this page, but server sign-out is not confirmed. Retry sign-out.');
    } finally { if (version === sessionVersion) { sessionBusy = false; refreshSessionView(); } }
  }
  function clearOrderRecovery() {
    try {
      const keys = [];
      for (let i=0;i<sessionStorage.length;i++) { const key=sessionStorage.key(i); if(key?.startsWith('sage.order.intent:'))keys.push(key); }
      for (const key of keys) sessionStorage.removeItem(key);
    } catch (_) { sessionNotice=t('当前已退出账户；浏览器未允许清除本地订单恢复标识，请关闭当前标签页。','You are currently signed out, but the browser prevented local order-recovery identifiers from being cleared. Close this tab.'); }
  }

  function selectionURL(name, extra = {}) {
    const params = new URLSearchParams({audience, cycle});
    if (audience === 'business') params.set('seats', seats);
    for (const key of ['return','mission','step','result']) if (initial.has(key)) params.set(key, initial.get(key));
    for (const [key,value] of Object.entries(extra)) params.set(key, value);
    return `${page(name)}?${params}`;
  }
  function fail(message) {
    root.innerHTML = `<main class="page"><h1>${t('暂时无法加载','Unable to load')}</h1><p class="notice" role="alert">${escape(message)}</p><div class="action-row"><button class="button" id="retry">${t('重新加载','Try again')}</button><a class="button" href="${page('pricing')}">${t('返回方案','Back to plans')}</a><a class="text-link" href="../${page('index')}">${t('返回首页','Back to home')}</a></div></main>`;
    document.querySelector('#retry').onclick = () => location.reload();
  }
  function shell(content) {
    const other = `${view === 'account' ? 'billing' : view}${en ? '' : '-en'}.html${location.search}${location.hash}`;
    const back = initial.get('return') === 'trial' ? `<a class="back-link task-return" href="${page('trial')}">${icon('arrow-left')}${t('返回体验','Back to trial')}</a>` : '';
    root.innerHTML = `<a class="skip" href="#main">${t('跳至内容','Skip to content')}</a><header class="billing-nav"><a class="billing-brand" href="../${page('index')}"><img src="../assets/favicon.svg" width="29" height="29" alt="">Sage</a><button id="r-menu-toggle" type="button" aria-expanded="false" aria-controls="r-menu" aria-label="${t('打开导航菜单','Open navigation menu')}">☰</button><nav id="r-menu" aria-label="${t('主导航','Main navigation')}"><a href="../${page('index')}#product">${t('产品','Product')}</a><a href="${page('team')}">${t('AI 团队','AI team')}</a><a href="${page('enterprise')}">${t('企业服务','Enterprise')}</a><a href="${page('pricing')}" ${view === 'pricing' ? 'aria-current="page"' : ''}>${t('价格','Pricing')}</a><a href="${page('docs')}">${t('文档','Docs')}</a></nav><div class="nav-actions"><a href="${escape(other)}" lang="${en ? 'zh-CN' : 'en'}">${en ? '中文' : 'English'}</a><a class="nav-login" href="${escape(authURL())}">${t('登录','Sign in')}</a><details class="nav-account" hidden><summary>${icon('person')}<span>${t('账户','Account')}</span></summary><div class="account-dropdown"><p>${t('账户服务暂未开放，当前无法登录。','Account services are not yet available. Sign-in is unavailable.')}</p><a href="${page('billing')}">${t('订阅与账单','Billing')}</a><a href="${page('billing')}#credits">${t('加购 Sage Credits','Buy Sage Credits')}</a><a href="${page('billing')}#payment-methods">${t('支付方式','Payment methods')}</a></div></details><a class="button primary" href="${page('enterprise')}#contact">${t('咨询和演示','Contact sales')}</a></div></header><main id="main" class="page ${view}-page">${back}${content}</main><footer class="footer"><span>Sage · ${t('你的出海 AI 团队','Your AI team for global commerce')}</span><span>${t('深圳主体 · 人民币计价 · Sage Credits','Shenzhen company · CNY pricing · Sage Credits')}</span></footer>`;
    renderAccountChrome();
  }
  function faqs() {
    const entries = [
      [t('Sage Credits 何时发放和到期？','When are Sage Credits granted and when do they expire?'),t('订阅额度按服务月发放，当期有效、不结转；年付也逐月发放。每笔加购自确认到账起 12 个月有效，新购买不延长旧批次。优先使用最早到期的可用批次；同一到期时间，先订阅额度再加购。','Subscription credits are granted each service month, expire at its end and do not roll over, including on annual plans. Each purchase lasts 12 months from confirmed payment; new purchases do not extend older batches. Eligible credits expiring first are used first; subscription credits precede purchased credits when expiry times match.')],
      [t('用完后会自动扣费吗？','Will usage trigger another charge?'),t('个人账户仅主动加购或升级，不自动充值、不透支。企业管理员可单独开启并授权自动充值，设置触发阈值、固定购买包和月度金额上限；默认关闭，不随订阅自动开启。相关服务尚未开放。','Personal accounts require a manual purchase or upgrade, with no automatic top-ups or overdrafts. Business admins may separately enable and authorize auto top-up with a threshold, fixed pack and monthly spending cap. It is off by default and not enabled with a subscription. These services are not yet available.')],
      [t('Teams 和 Enterprise 如何分配额度？','How do Teams and Enterprise credits work?'),t('Teams 每个真人席位的内含额度独立使用，额外购买进入企业共享池。Enterprise 席位不含 Sage Credits，需另购共享池。管理员可设置成员使用共享池的月上限，不占用席位内含额度。具体额度与权益开售前公布。','Teams included credits belong to each human seat; purchased credits enter the business shared pool. Enterprise seats exclude Sage Credits and use a separately purchased shared pool. Admins can cap each member’s monthly shared-pool usage without reducing included seat credits. Allowances and benefits will be published before sales open.')],
      [t('订阅结束后，已购买额度还能使用吗？','Can purchased credits be used after a subscription ends?'),t('尚未到期的已购 Sage Credits 仍可用于届时公布的基础任务，但不再发放订阅额度，也不保留原档位专属权益。','Unexpired purchased Sage Credits remain usable for the published basic tasks. Subscription credits stop and exclusive plan benefits do not continue.')],
      [t('如何关闭自动续费？','How do I stop automatic renewal?'),t('服务开放后，可在“订阅与账单”关闭自动续费。已付款周期内的权益保留；关闭续费不等于退款。企业自动充值独立管理，可单独关闭。','Once available, turn off renewal in Billing. Paid access remains through the current term; cancellation is separate from a refund. Business auto top-up is managed and cancelled separately.')],
      [t('退款如何计算？','How are refunds calculated?'),t('已开始的月度订阅原则不退，主动升档等约定例外除外。年付可退未来尚未开始的完整服务月，当前服务月保留。加购需在到期前按购买订单申请，退该单全部未用付费余额；按实付与未用比例计算，赠送额度不折现。已开票、合同或异常订单由财务审核，法定权利不受影响。','Started monthly terms are generally non-refundable, except for agreed cases such as a paid upgrade. Annual refunds cover future complete service months, preserving the current month. Before expiry, request an order’s entire unused paid credit balance, proportional to its payment and unused amount; promotional credits have no cash value. Invoiced, contract and exceptional orders require finance review. Statutory rights remain unaffected.')]
    ];
    return `<section class="rules"><div><span class="eyebrow">SAGE CREDITS</span><h2>${t('订阅与用量，清楚分开','Clear plans. Clear usage.')}</h2><p>${t('了解有效期、企业额度，以及续费和退款规则。','Understand credit validity, business resources, renewals and refunds.')}</p></div><div>${entries.map(([q,a]) => `<details><summary>${q}</summary><p>${a}</p></details>`).join('')}</div></section>`;
  }

  function pricing() {
    document.title = t('价格 · Sage','Pricing · Sage');
    shell(`<header class="page-heading pricing-heading"><span class="eyebrow">SAGE MEMBERSHIP</span><h1>${t('选择适合你的 Sage 计划','Choose your Sage plan')}</h1><p>${t('从个人经营到企业协作，选择适合你的方案与使用节奏','Find the right plan for individual work or business collaboration.')}</p><div class="segmented audience-control" role="group" aria-label="${t('方案类型','Plan type')}"><button data-audience="personal" aria-pressed="${audience === 'personal'}">${t('个人版','Personal')}</button><button data-audience="business" aria-pressed="${audience === 'business'}">${t('企业版','Business')}</button></div></header><section id="pricing-subscription-status" class="policy-block" aria-live="polite"></section><div class="controls"><h2>${audience === 'personal' ? t('个人方案','Personal plans') : t('企业方案','Business plans')}</h2><div class="segmented" role="group" aria-label="${t('付费周期','Billing cycle')}"><button data-cycle="monthly" aria-pressed="${cycle === 'monthly'}">${t('连续包月','Monthly renewal')}</button><button data-cycle="annual" aria-pressed="${cycle === 'annual'}">${t('连续包年','Annual renewal')}</button></div></div>${audience === 'business' ? `<div class="seat-selector"><label for="pricing-seats">${t('真人席位数量','Human seats')}</label><input id="pricing-seats" type="number" min="2" max="10000" step="1" value="${seats}" inputmode="numeric" aria-describedby="pricing-seat-help pricing-seat-error"><p id="pricing-seat-help">${t('2 个席位起购。企业价格与完整权益尚未公布，当前不接受付款。','Minimum 2 seats. Business prices and full benefits are pending; no payment is accepted.')}</p><p id="pricing-seat-error" class="field-error" role="status"></p></div>` : ''}<div id="plans" class="plan-grid ${audience === 'business' ? 'business' : ''}" aria-live="polite"></div><p class="notice">${icon('info-circle')}${boundary}</p><section class="credits-entry"><div>${icon('wallet2')}<div><h2>${t('按需加购 Sage Credits','Buy additional Sage Credits')}</h2><p>${t('每笔购买独立有效 12 个月，不改变订阅周期','Each purchase lasts 12 months and does not change your subscription term.')}</p></div></div><a class="button" href="${page('billing')}#credits">${t('了解加购','Explore add-ons')}</a></section>${faqs()}`);
    for (const button of document.querySelectorAll('[data-audience]')) button.onclick = () => { audience = button.dataset.audience; history.replaceState(null, '', selectionURL('pricing')); pricing(); document.querySelector(`[data-audience="${audience}"]`).focus(); };
    for (const button of document.querySelectorAll('[data-cycle]')) button.onclick = () => { cycle = button.dataset.cycle; history.replaceState(null, '', selectionURL('pricing')); pricing(); document.querySelector(`[data-cycle="${cycle}"]`).focus(); };
    const descriptions = {
      trial:t('先了解 Sage 如何处理一个经营问题。','Explore how Sage approaches a business question.'),
      pro:t('个人日常经营分析的起点。','A starting point for everyday business analysis.'),
      pro_plus:t('适合更频繁的个人经营工作。','For more frequent individual business work.'),
      ultra:t('面向高频、深入的个人经营工作。','For intensive individual business work.'),
      teams:t('席位内含额度独立，加购额度共享。','Included credits per seat; purchased credits shared.'),
      enterprise:t('席位与 Sage Credits 分开采购。','Purchase seats and Sage Credits separately.')
    };
    document.querySelector('#plans').innerHTML = catalog.plans.filter(p => p.audience === audience).map(p => {
      const trial = p.code === 'trial', price = trial ? 0 : p[`${cycle}_amount_minor`];
      const priced = Number.isSafeInteger(price) && price >= 0;
      const features = trial ? [t('VOC 机会洞察 / Shopify 店铺诊断二选一','Choose VOC insights or a Shopify diagnosis'),t('仅一次成功体验；当前可查看示例','One successful trial; examples are currently available'),t('无需银行卡，不自动转付费','No card required. No automatic paid conversion')]
        : [t(p.audience === 'personal' ? '1 个真人使用席位' : '2 个真人席位起购',p.audience === 'personal' ? '1 human seat' : 'Minimum 2 human seats'),p.code === 'enterprise' ? t('席位不含 Sage Credits，需单独购买','Sage Credits purchased separately') : p.code === 'teams' ? t('各席位内含额度独立使用，数量待公布','Included credits are private to each seat; amounts pending') : t('包含 Sage Credits 数量待公布','Included Sage Credits to be announced'),t('任务执行前确认预算','Approve each task budget'),t('完整权益开售前公布','Full benefits published before sales open')];
      return `<article class="plan"><div class="plan-top"><h2>${escape(trial ? t('体验版','Trial') : p.name)}</h2>${p.code === 'pro' ? `<span class="tag">${t('日常经营','Everyday work')}</span>` : ''}</div><p class="plan-description">${descriptions[p.code] || ''}</p><div class="price ${priced ? '' : 'pending'}">${priced ? amountMarkup(price) : t('价格待公布','Price pending')}${trial || !priced ? '' : `<small>/${p.audience === 'business' ? t('席位/','seat/') : ''}${cycle === 'annual' ? t('年','year') : t('月','month')}</small>`}</div><p class="price-note">${trial ? t('不绑定支付方式','No payment method required') : priced ? t('人民币参考价 · 完整权益尚未开售','CNY reference price · Full plan not yet on sale') : t('未定价，不接受付款','Not priced. No payment accepted.')}</p>${trial ? `<a class="button" href="${page('trial')}">${t('查看体验示例','Explore trial examples')}</a>` : `<a class="button ${priced ? 'primary' : ''}" data-plan-link="${escape(p.code)}" href="${escape(selectionURL('checkout', {plan:p.code}))}">${priced ? t('订阅','Subscribe') : t('查看方案','Review plan')}</a>`}<ul>${features.map(f => `<li>${icon('check2')}<span>${f}</span></li>`).join('')}</ul></article>`;
    }).join('') + (audience === 'business' ? `<article class="plan"><div class="plan-top"><h2>${t('合同采购','Contract')}</h2></div><p class="plan-description">${t('按企业需求约定席位、资源与服务。','Agree seats, resources and services for your business.')}</p><div class="price pending">${t('联系销售','Custom quote')}</div><p class="price-note">${t('对公转账 · 财务核验到账后开通','Corporate transfer · Access after verified receipt')}</p><a class="button primary" href="${page('billing')}#procurement">${t('查看采购说明','View procurement details')}</a><ul><li>${t('2 个真人席位起购','Minimum 2 human seats')}</li><li>${t('合同明确含税金额与服务期','Tax-inclusive amount and service term in contract')}</li><li>${t('对公采购不自动续费','Contract purchases do not renew automatically')}</li></ul></article>` : '');
    if (purchaseCatalog && purchaseCatalog.account_id === session?.active_account_id) {
      for (const link of document.querySelectorAll('[data-plan-link]')) {
        const product = purchaseCatalog.items.find(item=>item.code === link.dataset.planLink && item.billing_cycle === cycle && item.audience === audience && item.quote_available === true);
        const benefits = product?.entitlements?.benefits?.[en ? 'en' : 'zh'];
        if (!product || typeof product.unit_amount_minor !== 'string' || !/^\d+$/.test(product.unit_amount_minor) || !Array.isArray(benefits) || !benefits.length || benefits.some(text=>typeof text !== 'string')) continue;
        link.dataset.purchaseProduct = product.id;
        link.href = `${page('checkout')}?${new URLSearchParams({product:product.id,seats:String(audience === 'business' ? seats : 1)})}`;
        link.textContent = t('订阅','Subscribe');
        const card = link.closest('.plan');
        card.querySelector('.price').innerHTML = `${amountMarkup(product.unit_amount_minor)}<small>/${audience === 'business' ? t('席位/','seat/') : ''}${cycle === 'annual' ? t('年','year') : t('月','month')}</small>`;
        card.querySelector('.price-note').textContent = t('已批准商品价 · 付款前以服务端报价为准','Approved product price · Confirm the server quote before payment');
        card.querySelector('ul').innerHTML = benefits.map(text=>`<li>${icon('check2')}<span>${escape(text)}</span></li>`).join('') + `<li>${icon('wallet2')}<span>${escape(product.credits_quantity)} Sage Credits</span></li>`;
      }
      if (document.querySelector('[data-purchase-product]')) {
        const notice = document.querySelector('.notice');
        notice.replaceChildren(document.createTextNode(t('已批准方案可获取报价并创建订单。创建订单不会扣款，支付与续费授权尚未接通。其他方案仍为参考信息。','Approved plans support quotes and order creation, without charging you. Payment and renewal authorization are not connected. Other plans remain reference information.')));
        const help = document.querySelector('#pricing-seat-help');
        if (help) help.textContent=t('2 个真人席位起购。已批准商品以正式报价为准；创建订单不等于付款或开通。','Minimum 2 human seats. Approved products use the confirmed quote; creating an order does not mean payment or activation.');
      }
    }
    const subscriptionStatus = document.querySelector('#pricing-subscription-status');
    if (session?.authenticated && activeAccount() && !sessionBusy) {
      const names = {pro:'Pro',pro_plus:'Pro+',ultra:'Ultra',teams:'Teams',enterprise:'Enterprise'};
      const records = pricingSubscriptions || [];
      const paid = item => item.prepaid_through !== null && Date.parse(item.prepaid_through) > Date.now();
      const protectedRecords = records.filter(item=>paid(item) || item.auto_renew || item.cancellation_state === 'processing');
      const unknownState = pricingSubscriptionState !== 'ready';
      for (const link of document.querySelectorAll('[data-plan-link]')) if (!link.dataset.purchaseProduct) link.textContent = t('查看方案','Review plan');
      if (unknownState || protectedRecords.length) {
        for (const link of document.querySelectorAll('[data-plan-link]')) {
          const current = protectedRecords.some(item=>paid(item) && item.plan_code === link.dataset.planLink && item.billing_cycle === cycle && activeAccount().kind === audience);
          link.textContent = current ? t('当前方案','Current plan') : t('查看方案','Review plan');
          link.href = `${page('billing')}#subscription`;
          link.dataset.subscriptionManaged = 'true';
          delete link.dataset.purchaseProduct;
        }
      }
      if (pricingSubscriptionState === 'loading') {
        subscriptionStatus.innerHTML = `<p>${t('正在确认当前账户的订阅，请稍候…','Checking this account’s subscriptions…')}</p>`;
      } else if (pricingSubscriptionState !== 'ready') {
        subscriptionStatus.innerHTML = `<p class="error" role="status">${t('当前订阅读取失败，状态未知。不能据此判断没有订阅，请先重新查询或管理原订阅。','Subscription status could not be loaded and is unknown. This does not mean you have no subscription. Retry or manage your existing subscription.')}</p><button type="button" class="button" id="pricing-subscription-retry">${t('重新查询订阅','Retry subscription status')}</button><a class="text-link" href="${page('billing')}#subscription">${t('管理订阅','Manage subscriptions')}</a>`;
        subscriptionStatus.querySelector('button').onclick = loadPurchaseCatalog;
      } else {
        subscriptionStatus.dataset.ready = 'true';
        const status = {active:t('生效中','Active'),scheduled:t('已付，待生效','Paid, scheduled'),expired:t('已到期','Expired')};
        const renewal = item=>item.cancellation_state === 'processing' ? t('取消处理中，尚未确认完成','Cancellation processing; not yet confirmed') : item.cancellation_state === 'completed' ? t('自动续费已取消','Automatic renewal cancelled') : item.auto_renew ? t('自动续费已开启','Automatic renewal on') : t('自动续费未开启','Automatic renewal off');
        const note = protectedRecords.length ? t('请先在订阅管理核对已付服务和续费状态，不要重复新购。此处不直接升档或切换周期。','First check paid service and renewal status in subscription management. Do not purchase again. This page does not upgrade plans or switch billing cycles.') : t('新购资格仍以服务端正式报价为准。存在未结订单或未完成商品审批时不能新购；请在账单管理核对原订单。','New-purchase eligibility still depends on the server quote. An unsettled order or incomplete product approval can prevent a purchase. Check original orders in Billing.');
        subscriptionStatus.innerHTML = `<h2>${t('当前账户的订阅','Subscriptions for this account')}</h2>` + (records.length ? `<ul class="record-list">${records.map(item=>`<li><strong>Sage ${escape(names[item.plan_code] || item.plan_code)} · ${item.billing_cycle === 'annual' ? t('连续包年','Annual renewal') : t('连续包月','Monthly renewal')}</strong><span>${status[item.status]} · ${renewal(item)}</span><small>${t('已付服务截止','Paid service through')} ${item.prepaid_through === null ? unknown() : dateLabel(item.prepaid_through)}</small></li>`).join('')}</ul>` : `<p>${t('未查询到当前账户的订阅记录。','No subscription records were returned for this account.')}</p>`) + `<p class="checkout-note">${note}</p><a class="button" href="${page('billing')}#subscription">${t('管理订阅与账单','Manage subscriptions and billing')}</a>`;
      }
    } else subscriptionStatus.remove();
    const input = document.querySelector('#pricing-seats');
    if (input) input.addEventListener('input', () => {
      const valid = input.validity.valid && /^[0-9]+$/.test(input.value);
      input.setAttribute('aria-invalid', String(!valid));
      document.querySelector('#pricing-seat-error').textContent = valid ? '' : t('请输入 2 至 10,000 的整数席位数。','Enter a whole number of seats from 2 to 10,000.');
      for (const link of document.querySelectorAll('[data-plan-link]')) {
        link.setAttribute('aria-disabled', String(!valid));
        if (!valid) link.removeAttribute('href');
      }
      if (valid) {
        seats = Number(input.value); history.replaceState(null, '', selectionURL('pricing'));
        for (const link of document.querySelectorAll('[data-plan-link]')) if (!link.dataset.subscriptionManaged) link.href = link.dataset.purchaseProduct ? `${page('checkout')}?${new URLSearchParams({product:link.dataset.purchaseProduct,seats:String(seats)})}` : selectionURL('checkout', {plan:link.dataset.planLink});
        document.dispatchEvent(new Event('sage:location-updated'));
      }
    });
  }

  async function checkout() {
    const params = new URLSearchParams(location.search), code = params.get('plan') ?? 'pro', period = params.get('cycle') ?? 'monthly';
    if (params.has('product') || params.has('quote')) return purchaseCheckout(params);
    if (session?.authenticated && activeAccount()) {
      try {
        const data = await api('/api/billing/purchase-catalog');
        const product = data.account_id === session.active_account_id && Array.isArray(data.items) ? data.items.find(item=>item.kind === 'subscription' && item.code === code && item.billing_cycle === period && item.quote_available === true) : null;
        if (product) { params.set('product',product.id); return purchaseCheckout(params); }
      } catch (_) { /* Reference-only checkout remains explicitly unavailable. */ }
    }
    const plan = catalog.plans.find(p => p.code === code && p.code !== 'trial');
    if (!plan || !['monthly','annual'].includes(period)) return fail(t('方案信息无效，请返回价格页重新选择。','Invalid plan details. Return to Pricing and choose a plan.'));
    audience = plan.audience; cycle = period;
    const raw = params.get('seats') ?? String(plan.minimum_seats), quantity = Number(raw);
    if (!/^[0-9]+$/.test(raw) || !Number.isSafeInteger(quantity) || quantity < plan.minimum_seats || quantity > 10000 || (audience === 'personal' && quantity !== 1)) return fail(t('席位数量无效。个人方案限 1 席，企业方案至少 2 席，请返回方案重新选择。','Invalid seat count. Personal plans have 1 seat and business plans require at least 2. Return to plans to choose again.'));
    seats = quantity;
    document.title = t('确认方案 · Sage','Review plan · Sage');
    const channelIcon = {alipay:'alipay',card:'credit-card',wechat:'wechat',google_pay:'google',apple_pay:'apple'};
    const channelRow = c => `<button class="channel ${escape(c.code)}" disabled aria-describedby="payment-unavailable">${c.code === 'google_pay' ? '<img class="ui-icon" src="icons/google-color.png" alt="" aria-hidden="true">' : icon(channelIcon[c.code] || 'credit-card')}<span>${escape(({alipay:t('支付宝','Alipay'),card:t('银行卡','Card'),wechat:t('微信支付','WeChat Pay')})[c.code] || c.name)}</span><small>${t('暂不可用','Unavailable')}</small></button>`;
    shell(`<a class="back-link plan-return" href="${escape(selectionURL('pricing'))}">${icon('arrow-left')}${t('返回方案','Back to plans')}</a><header class="page-heading checkout-heading"><h1>${t('确认你的 Sage 订阅','Review your Sage subscription')}</h1><p>${t('核对方案、席位与计费周期。订阅暂未开放，不会扣款。','Review your plan, seats and billing cycle. Subscriptions are not yet available; no charge will be made.')}</p></header><div class="checkout-grid"><section class="order-summary" aria-labelledby="summary-title"><p class="summary-label" id="summary-title">${t('你选择的方案','Your selected plan')}</p><h2 class="selected-plan">Sage ${escape(plan.name)}</h2><p class="reference-label">${t('方案参考金额，不是待付款账单','Plan reference amount — not a payable bill')}</p><div class="total"><strong id="quote-amount">—</strong><span>${period === 'monthly' ? t('/ 月','/ month') : t('/ 年','/ year')}</span></div><p class="summary-frequency">${period === 'monthly' ? t('连续包月 · 人民币计价','Monthly renewal · CNY') : t('连续包年 · 人民币计价','Annual renewal · CNY')}</p><div class="currency-pill">${icon('wallet2')} CNY <span>${t('人民币','Chinese yuan')}</span></div><div class="order-lines"><div class="line-item"><span>${icon('calendar3')}${t('订阅周期','Billing cycle')}</span><strong>${period === 'monthly' ? t('每月','Monthly') : t('每年','Annually')}</strong></div><div class="line-item seat-line">${audience === 'business' ? `<label for="checkout-seats">${icon('person')}${t('真人席位','Human seats')}</label><input id="checkout-seats" type="number" min="2" max="10000" step="1" value="${seats}" inputmode="numeric" aria-describedby="checkout-seat-error">` : `<span>${icon('person')}${t('真人席位','Human seats')}</span><strong>1</strong>`}</div><p id="checkout-seat-error" class="field-error" role="status"></p><div class="line-item"><span>${icon('wallet2')}Sage Credits</span><strong>${plan.code === 'enterprise' ? t('单独购买','Sold separately') : t('额度待公布','Allowance pending')}</strong></div><div class="line-item"><span>${icon('receipt')}${t('费用与发票','Charges & invoices')}</span><strong>${t('含税计价，按实付申请发票','Tax-inclusive; invoice based on actual payment')}</strong></div><div class="line-item"><span>${t('下一次扣款','Next charge')}</span><strong>${t('未开通，无扣款计划','Not subscribed; no charge scheduled')}</strong></div></div><p id="quote-status" class="checkout-note" role="status" aria-live="polite"></p><button type="button" class="button quote-refresh" id="quote-refresh">${t('重新查询方案金额','Refresh plan amount')}</button><div class="summary-assurance">${icon('shield-lock')}<p>${t('深圳主体提供服务并收款。<br>开通时将明确展示签约主体、费用和续费授权。','Service and payment collection by the Shenzhen company.<br>The legal entity, charges and renewal authorization will be shown before purchase.')}</p></div></section><section class="checkout-payment" aria-labelledby="payment-title"><div class="billing-identity"><h2>${t('计费账户','Billing account')}</h2><div class="identity-row">${icon('person')}<div><strong>${t('账户服务暂未开放','Account services unavailable')}</strong><p>${t('暂无法登录或确认本次购买归属，不接受付款','Sign-in and billing-account verification are unavailable; no payment is accepted')}</p></div>${icon('lock')}</div></div><h2 id="payment-title">${t('支付方式','Payment methods')}</h2><p class="payment-hint" id="payment-unavailable">${t('支付服务尚未开放。以下方式正在评估，当前均不能付款或签约自动续费。','Payment services are not yet available. The methods below are being evaluated and cannot accept payments or renewal authorizations.')}</p><div class="express-pay">${catalog.channels.filter(c => ['apple_pay','google_pay'].includes(c.code)).map(channelRow).join('')}</div><div class="payment-divider"><span>${t('银行卡与本地支付','Cards & local payments')}</span></div><div class="channel-list">${catalog.channels.filter(c => !['apple_pay','google_pay'].includes(c.code)).sort((a,b) => a.code === 'card' ? -1 : b.code === 'card' ? 1 : 0).map(channelRow).join('')}</div><label class="consent"><input type="checkbox" disabled><span>${t('购买前需主动同意自动续费条款，并确认后续扣款时间和金额。当前不能授权，也不会保存支付信息。','Before purchase, explicitly accept renewal terms and review future charge dates and amounts. Authorization and payment-detail storage are currently unavailable.')}</span></label><button class="button wide purchase-unavailable" disabled aria-describedby="payment-unavailable">${icon('lock')}${t('暂未开放订阅','Subscriptions not yet available')}</button><div class="checkout-support"><a class="text-link" href="${page('billing')}#procurement">${t('企业采购说明','Enterprise purchasing')}</a><a class="text-link" href="${page('billing')}#payment-methods">${t('支付方式管理说明','Managing payment methods')}</a></div></section></div><p class="notice">${icon('info-circle')}${boundary}</p>`);
    const status = document.querySelector('#quote-status'), amount = document.querySelector('#quote-amount'), refresh = document.querySelector('#quote-refresh'), input = document.querySelector('#checkout-seats');
    let requestId = 0;
    async function refreshQuote(useNetwork = true) {
      const id = ++requestId;
      const valid = !input || (input.validity.valid && /^[0-9]+$/.test(input.value));
      if (input) input.setAttribute('aria-invalid', String(!valid));
      document.querySelector('#checkout-seat-error').textContent = valid ? '' : t('请输入 2 至 10,000 的整数席位数。','Enter a whole number of seats from 2 to 10,000.');
      if (!valid) { amount.textContent = '—'; status.textContent = t('请先修正席位数量，再查询参考金额。','Correct the seat count before refreshing the reference amount.'); refresh.disabled = true; return; }
      if (input) seats = Number(input.value);
      history.replaceState(null, '', selectionURL('checkout', {plan:code}));
      document.querySelector('.plan-return').href = selectionURL('pricing');
      document.dispatchEvent(new Event('sage:location-updated'));
      const unit = plan[`${period}_amount_minor`], total = Number.isSafeInteger(unit) && unit >= 0 ? unit * seats : null;
      amount.innerHTML = Number.isSafeInteger(total) ? amountMarkup(total, true) : '—';
      status.classList.remove('error');
      if (!useNetwork) { status.textContent = t('以上仅为方案参考金额，不是最终报价；未创建订单。可重新查询，未定价方案显示“—”。','These are plan reference amounts, not a final quote; no order was created. Refresh to check again; unpriced plans show “—”.'); refresh.disabled = false; return; }
      status.textContent = t('正在查询方案金额，不会创建订单…','Checking the plan amount. No order will be created…'); refresh.disabled = true;
      try {
        const response = await fetch('/api/billing/quotes', {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json',...(session?.csrf_token ? {'X-CSRFToken':session.csrf_token} : {}),...(session?.active_account_id ? {'X-Billing-Account':session.active_account_id} : {})},body:JSON.stringify({plan_code:code,billing_cycle:period,seat_quantity:seats}),signal:AbortSignal.timeout(8000)});
        const result = await response.json();
        if (id !== requestId) return;
        if (!response.ok) throw new Error(result.error?.code || 'QUOTE_UNAVAILABLE');
        if (result.currency !== 'CNY' || !Number.isSafeInteger(result.amount_minor) || result.amount_minor < 0 || result.plan_code !== code || result.billing_cycle !== period || result.seat_quantity !== seats) throw new Error('INVALID_QUOTE');
        amount.innerHTML = amountMarkup(result.amount_minor, true);
        status.textContent = t('已更新方案参考金额。完整权益尚未开放购买；这不是可付款报价，未创建订单或订阅。','Plan reference amount updated. The full plan is not yet available to purchase; this is not a payable quote and no order or subscription was created.');
      } catch (error) {
        if (id !== requestId) return;
        amount.textContent = '—'; status.classList.add('error');
        status.textContent = error.message === 'PRICE_NOT_CONFIGURED' ? t('该方案的价格尚未公布，暂不能提供金额或接受付款。你可以返回选择其他方案。','This plan’s price has not been published. No amount or payment is available; return to explore other plans.') : t('暂时无法确认方案金额。请重新查询，或返回价格页查看参考价；未创建订单，不会扣款。','The plan amount could not be confirmed. Retry or return to Pricing for reference prices. No order or charge was created.');
      } finally { if (id === requestId) refresh.disabled = false; }
    }
    refresh.onclick = () => refreshQuote();
    if (input) input.addEventListener('input', () => refreshQuote(false));
    await refreshQuote(online);
  }

  async function loadPurchaseCatalog() {
    if (!session?.authenticated || sessionBusy || !activeAccount()) return;
    const version = accountVersion, accountId = session.active_account_id, request = ++pricingReadVersion;
    pricingSubscriptionState = 'loading';
    if (view === 'pricing') pricing();
    const [products, subscriptions] = await Promise.allSettled([
      api('/api/billing/purchase-catalog'), api('/api/billing/subscriptions')]);
    if (version !== accountVersion || accountId !== session?.active_account_id || request !== pricingReadVersion || sessionBusy) return;
    const data = products.status === 'fulfilled' ? products.value : null;
    purchaseCatalog = data?.account_id === accountId && Array.isArray(data.items) && data.currency === 'CNY' ? data : null;
    const items = subscriptions.status === 'fulfilled' ? subscriptions.value.items : null;
    if (Array.isArray(items) && items.every(validSubscription) && new Set(items.map(item=>item.id)).size === items.length) {
      pricingSubscriptions = items; pricingSubscriptionState = 'ready';
    } else { pricingSubscriptions = null; pricingSubscriptionState = 'unknown'; }
    if (view === 'pricing') pricing();
  }

  async function purchaseCheckout(params) {
    const validId = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
    if ((params.has('product') && !validId(params.get('product'))) || (params.has('quote') && !validId(params.get('quote')))) return fail(t('购买信息无效，请返回方案页重新选择。','Invalid purchase details. Return to the plans page.'));
    let requestVersion = 0, quoteData = null, product = null, options = [], busy = false;
    const orderLink = id => `${page('billing')}?${new URLSearchParams({order:id})}#orders`;
    const message = error => requestError(error, ({TERMS_UNAVAILABLE:t('购买条款尚未批准，暂不能提交订单。','Purchase terms are not approved; orders are unavailable.'),TERMS_MISMATCH:t('条款版本已变更，请重新核对，不会自动提交。','The terms have changed. Review them again; nothing is submitted automatically.'),CONFIG_INCOMPLETE:t('商品或支付准入未完成，暂不能购买。','Product or payment approval is incomplete. Purchasing is unavailable.'),NOT_FOUND:t('未找到当前账户的购买记录，请核对计费账户。','This purchase record was not found in your current billing account.'),QUOTE_EXPIRED:t('报价已过期，不能提交新订单。原订单结果仍可查询。','This quote has expired. No new order can be submitted; the original order can still be checked.')})[error.message] || t('暂时无法读取购买记录。请刷新查询，不要重复付款。','The purchase record could not be loaded. Refresh its status; do not pay again.'));
    function draw() {
      shell(`<a class="back-link" href="${page('pricing')}">${icon('arrow-left')}${t('返回方案','Back to plans')}</a><header class="page-heading checkout-heading"><h1>${t('确认你的 Sage 方案','Review your Sage plan')}</h1><p>${t('先确认账户、服务内容与费用，再创建订单','Review your account, benefits and charges before creating an order.')}</p></header><div class="checkout-grid"><section class="order-summary" id="purchase-summary"><p>${t('正在读取服务端方案…','Loading the server-approved plan…')}</p></section><section class="checkout-payment"><div class="billing-identity"><h2>${t('计费账户','Billing account')}</h2><div class="identity-row"></div></div><div id="purchase-controls"></div><p id="purchase-status" class="checkout-note" role="status" aria-live="polite"></p></section></div>`);
    }
    function status(text, failed = false) { const node = document.querySelector('#purchase-status'); if (node) {node.textContent=text;node.classList.toggle('error',failed);} }
    function summary(snapshot, amount) {
      document.querySelector('.checkout-heading h1').textContent = snapshot.kind === 'credits' ? t('购买 Sage Credits','Purchase Sage Credits') : t('确认你的 Sage 方案','Review your Sage plan');
      const name = ({pro:'Pro',pro_plus:'Pro+',ultra:'Ultra',teams:'Teams',enterprise:'Enterprise'})[snapshot.code] || snapshot.code;
      const benefits = snapshot.entitlements?.benefits?.[en ? 'en' : 'zh'];
      const features = Array.isArray(benefits) && benefits.length && benefits.every(text=>typeof text === 'string' && text.trim()) ? `<ul>${benefits.map(text=>`<li>${escape(text)}</li>`).join('')}</ul>` : `<p>${t('服务权益尚未完整配置，暂不能确认购买。','Benefits have not been fully configured; purchasing cannot be confirmed.')}</p>`;
      document.querySelector('#purchase-summary').innerHTML = `<p class="summary-label">${t('服务端确认的方案','Server-confirmed plan')}</p><h2 class="selected-plan">${snapshot.kind === 'credits' ? 'Sage Credits' : `Sage ${escape(name)}`}</h2><div class="total"><strong>${amountMarkup(amount,true)}</strong></div><dl class="order-detail-list"><dt>${t('计费周期','Billing cycle')}</dt><dd>${snapshot.kind === 'credits' ? t('一次购买','One-time purchase') : snapshot.billing_cycle === 'annual' ? t('连续包年','Annual renewal') : t('连续包月','Monthly renewal')}</dd>${snapshot.kind === 'credits' ? `<dt>${t('有效期','Validity')}</dt><dd>${t('确认到账起 12 个月','12 months from confirmed receipt')}</dd>` : `<dt>${t('真人席位','Human seats')}</dt><dd>${escape(snapshot.seat_quantity ?? seats)}</dd>`}<dt>Sage Credits</dt><dd>${escape(snapshot.credits_quantity)}</dd><dt>${t('方案版本','Plan version')}</dt><dd>${escape(snapshot.version)}</dd></dl><div class="policy-block"><h3>${t('服务权益','Benefits')}</h3>${features}</div><p class="checkout-note">${t('创建订单不会扣款或授权自动续费；支付与渠道签约需要后续单独确认。','Creating an order does not charge you or authorize renewal. Payment and channel authorization require a separate confirmation.')}</p>`;
      const price = document.querySelector('#purchase-summary .total strong');
      price.style.maxWidth = '100%'; price.style.minWidth = '0';
      price.querySelector('.money').style.maxWidth = '100%';
      const number = price.querySelector('.money > span:last-child');
      number.style.minWidth = '0'; number.style.overflowWrap = 'anywhere';
    }
    function validQuote(data, accountId) {
      return data && validId(data.id) && data.account_id === accountId && data.currency === 'CNY' && Number.isSafeInteger(data.amount_minor) && data.amount_minor > 0 && data.snapshot && data.snapshot.amount_minor === data.amount_minor && Number.isFinite(Date.parse(data.expires_at)) && typeof data.can_order === 'boolean' && (data.order_id === null || validId(data.order_id));
    }
    function showQuote(data) {
      summary(data.snapshot,data.amount_minor);
      const controls = document.querySelector('#purchase-controls');
      if (data.order_id) {
        controls.innerHTML = `<h2>${t('订单已创建','Order created')}</h2><p>${t('订单不等于支付成功。请在订单详情确认支付与权益状态，不要再次建单。','An order is not a successful payment. Check payment and benefit status in the order details; do not create another order.')}</p><a class="button primary" id="purchase-order-link" href="${escape(orderLink(data.order_id))}">${t('查看原订单','View original order')}</a>`;
        status(t('已恢复原订单；未再次提交或扣款。','The original order was recovered. No new order or charge was made.'));
        return;
      }
      const approvedTerms = data.terms, content = approvedTerms?.[en ? 'en' : 'zh'];
      if (!data.can_order || Date.parse(data.expires_at) <= Date.now()) {
        controls.innerHTML = `<button class="button" id="purchase-recover">${t('刷新原报价与订单状态','Refresh original quote and order')}</button>`;
        controls.querySelector('button').onclick = load;
        status(message(new Error(Date.parse(data.expires_at) <= Date.now() ? 'QUOTE_EXPIRED' : data.unavailable_reason)),true);
        return;
      }
      if (!approvedTerms || approvedTerms.version !== data.snapshot.terms_version || approvedTerms.digest !== data.snapshot.terms_document_digest || !content || typeof content.title !== 'string' || !Array.isArray(content.paragraphs) || !content.paragraphs.length || content.paragraphs.some(text=>typeof text !== 'string')) throw new Error('TERMS_UNAVAILABLE');
      controls.innerHTML = `<h2>${escape(content.title)}</h2><div class="policy-block" tabindex="0">${content.paragraphs.map(text=>`<p>${escape(text)}</p>`).join('')}</div><p>${t('报价有效至','Quote valid until')} ${dateLabel(data.expires_at)}</p><label class="consent"><input id="purchase-agree" type="checkbox"><span>${t('我已阅读并同意以上服务条款，确认计费账户和本次方案费用。','I have read and accept these service terms and confirm the billing account and plan charges.')}</span></label><button class="button primary wide" id="purchase-submit" disabled>${t('确认并创建订单','Confirm and create order')}</button><button class="button" id="purchase-recover">${t('刷新原报价与订单状态','Refresh original quote and order')}</button>`;
      const submit = document.querySelector('#purchase-submit'), agree = document.querySelector('#purchase-agree');
      agree.onchange=()=>{submit.disabled=!agree.checked||busy;};
      controls.querySelector('#purchase-recover').onclick=load;
      submit.onclick=submitOrder;
      status(t('请逐项核对。不会默认同意条款、扣款或开通订阅。','Review each item. Terms, payment and subscription activation are not accepted automatically.'));
    }
    async function load() {
      const version = ++requestVersion, accountId = session?.active_account_id;
      quoteData=null; product=null; options=[]; busy=false; draw();
      const current=()=>version===requestVersion && accountId===session?.active_account_id && !sessionBusy;
      if (!session?.authenticated || !activeAccount() || sessionBusy) {
        document.querySelector('#purchase-controls').innerHTML=`<a class="button primary" href="${escape(authURL())}">${t('登录并确认计费账户','Sign in and confirm billing account')}</a>`;
        status(t('当前未确认购买账户，不接受订单或付款。','The billing account is not confirmed. No order or payment is accepted.'));
        return;
      }
      try {
        const quoteId = new URLSearchParams(location.search).get('quote');
        if (quoteId) {
          const data=await api(`/api/billing/purchase-quotes/${encodeURIComponent(quoteId)}`);
          if(!current())return;
        if(!validQuote(data,accountId) || data.id!==quoteId)throw new Error('INVALID_QUOTE');
          quoteData=data; showQuote(data); return;
        }
        const data=await api('/api/billing/purchase-catalog');
        if(!current())return;
        if(data.account_id!==accountId || !Array.isArray(data.items))throw new Error('INVALID_CATALOG');
        product=data.items.find(item=>item.id===params.get('product'));
        if(!product)throw new Error('NOT_FOUND');
        const quantity=Number(params.get('seats') ?? product.minimum_seats);
        if(!Number.isSafeInteger(quantity) || quantity<product.minimum_seats || quantity>10000 || ((product.audience==='personal'||product.kind==='credits')&&quantity!==1))throw new Error('INVALID_SEATS');
        seats=quantity;
        if(product.currency!=='CNY' || typeof product.unit_amount_minor!=='string' || !/^\d+$/.test(product.unit_amount_minor))throw new Error('CONFIG_INCOMPLETE');
        summary({...product,seat_quantity:quantity},String(BigInt(product.unit_amount_minor)*BigInt(quantity)));
        if(product.quote_available!==true)throw new Error(product.unavailable_reason||'CONFIG_INCOMPLETE');
        options=product.purchase_options;
        if(!Array.isArray(options)||!options.length||options.some(item=>typeof item.channel!=='string'||typeof item.buyer_region!=='string'))throw new Error('CONFIG_INCOMPLETE');
        document.querySelector('#purchase-controls').innerHTML=`<h2>${t('报价渠道与地区','Quote channel and region')}</h2><label for="purchase-option">${t('按实际购买地区选择','Select your actual purchasing region')}</label><select id="purchase-option">${options.map((option,index)=>`<option value="${index}">${escape(({alipay:t('支付宝','Alipay'),card:t('银行卡','Card'),wechat:t('微信支付','WeChat Pay')})[option.channel]||option.channel)} · ${escape(option.buyer_region)}</option>`).join('')}</select><button class="button primary" id="purchase-prepare">${t('获取正式报价','Get a confirmed quote')}</button>`;
        document.querySelector('#purchase-prepare').onclick=async()=>{
          if(busy||!current())return;
          const option=options[Number(document.querySelector('#purchase-option').value)];
          if(!option)return;
          busy=true; document.querySelector('#purchase-prepare').disabled=true;
          try {
            const result=await api('/api/billing/purchase-quotes',{product_id:product.id,seat_quantity:quantity,...option});
            if(!current())return;
            if(!validQuote(result,accountId) || result.snapshot.product_id!==product.id || result.snapshot.seat_quantity!==quantity)throw new Error('INVALID_QUOTE');
            history.replaceState(null,'',`${location.pathname}?${new URLSearchParams({quote:result.id})}`);
            document.dispatchEvent(new Event('sage:location-updated'));
            quoteData=result; busy=false; showQuote(result);
          }catch(error){if(current()){status(message(error),true);const button=document.querySelector('#purchase-prepare');if(button)button.disabled=false;}}
          finally{if(current())busy=false;}
        };
        status(t('主动获取报价后再核对条款；此操作不会创建付款订单。','Request a quote, then review its terms. This action does not create a payment order.'));
      }catch(error){if(current()){document.querySelector('#purchase-controls').innerHTML=`<button class="button" id="purchase-recover">${t('重新读取','Reload record')}</button>`;document.querySelector('#purchase-recover').onclick=load;status(message(error),true);}}
    }
    async function submitOrder() {
      if(busy||!quoteData||!document.querySelector('#purchase-agree')?.checked)return;
      const data=quoteData, accountId=session.active_account_id, version=requestVersion;
      if(!data.can_order||Date.parse(data.expires_at)<=Date.now())return load();
      busy=true; document.querySelector('#purchase-submit').disabled=true;
      const current=()=>version===requestVersion && accountId===session?.active_account_id && !sessionBusy;
      try {
        const storageKey=`sage.order.intent:${accountId}:${data.id}`;
        let key=sessionStorage.getItem(storageKey);
        if(!key){key=crypto.randomUUID();sessionStorage.setItem(storageKey,key);}
        const result=await api('/api/billing/orders',{quote_id:data.id,agreement_acceptance:data.snapshot.terms_version},session.csrf_token,{'Idempotency-Key':key});
        if(!current())return;
        if(!validId(result.id)||result.account_id!==accountId||result.currency!=='CNY'||result.amount_minor!==data.amount_minor)throw new Error('INVALID_ORDER');
        quoteData={...data,order_id:result.id,can_order:false}; showQuote(quoteData);
      }catch(error){if(current()){document.querySelector('#purchase-controls').innerHTML=`<button class="button" id="purchase-recover">${t('查询原订单结果','Check original order result')}</button>`;document.querySelector('#purchase-recover').onclick=load;status(requestError(error,t('提交结果尚未确认。请先查询原订单；重试只使用同一报价和幂等键，不会自动新建或扣款。','The submission result is unconfirmed. Check the original order first. Any retry uses the same quote and idempotency key; no new order or charge is started automatically.')),true);}}
      finally{if(current())busy=false;}
    }
    purchaseRefresh=load;
    await load();
  }

  function account() {
    const sections = {subscription:t('订阅管理','Subscription'),credits:'Sage Credits',orders:t('订单记录','Orders'),notifications:t('账务通知','Billing updates'),invoices:t('发票','Invoices'),'payment-methods':t('支付方式','Payment methods'),organization:t('企业管理','Business settings'),procurement:t('企业采购','Enterprise purchasing')};
    sections['credits-ledger'] = t('额度流水','Credits history');
    if (activeAccount()?.kind === 'business') sections['organization-history'] = t('企业变更记录','Business change history');
    if (activeAccount()?.kind === 'business') sections['my-usage'] = t('我的使用限制','My usage limits');
    const requested = location.hash.slice(1), selected = Object.hasOwn(sections, requested) ? requested : 'subscription';
    const icons = {subscription:'calendar3',credits:'wallet2',orders:'receipt',notifications:'info-circle',invoices:'building','payment-methods':'credit-card',organization:'person',procurement:'building'};
    icons['my-usage'] = 'person';
    icons['credits-ledger'] = 'receipt';
    icons['organization-history'] = 'person';
    const policies = {
      subscription:t('取消自动续费不会退款或提前收回已付服务。Sage 先停止发起新扣款，再确认支付渠道解约；渠道确认前显示处理中。在途交易需另行核实，不代表已被撤回。新购、升级和支付方式变更尚未开放。','Cancelling automatic renewal does not refund or end paid service early. Sage stops initiating new charges, then confirms cancellation with the payment channel; the status remains processing until confirmed. In-flight transactions must be checked separately and are not automatically withdrawn. New purchases, upgrades and payment-method changes are not yet available.'),
      credits:t('订阅额度按服务月发放、当期有效。加购到账起 12 个月有效，优先使用最早到期批次。个人仅主动加购；企业自动充值默认关闭，需管理员另行授权。','Subscription credits are granted by service month and expire at its end. Purchased credits last 12 months from confirmed receipt; earliest expiry is used first. Personal top-ups are manual; business auto top-up is off by default and requires separate admin authorization.'),
      orders:t('订单、支付确认与权益交付是不同状态。付款页面返回不等于支付成功，最终以服务端确认结果为准。','Orders, confirmed payments and benefit delivery have separate states. Returning from a payment page does not prove payment; server confirmation is authoritative.'),
      notifications:t('这里保留服务端确认的账务事件，不代表订单的最新状态。请进入对应订单或订阅管理核对后续结果。邮件、短信与扣款前提醒尚未接入。','This is a record of server-confirmed billing events, not an order’s latest status. Check the order or subscription for later results. Email, SMS and pre-charge reminders are not yet connected.'),
      invoices:t('从实付订单申请发票，财务审核开票后发送至指定邮箱。手机号登录也可填写独立收票邮箱。开票服务尚未接入。','Invoices are requested from paid orders, reviewed by finance and sent to a designated email, independently of phone sign-in. Invoice requests are not yet available.'),
      'payment-methods':t('订阅与企业自动充值分别授权。更换方式不重复收取当期费用；移除前先停止新扣款并查明在途交易。绑定、更换和移除暂不可用。','Renewal and business auto top-up have separate authorizations. Changing a method does not recharge the current term. Removal stops new charges and resolves in-flight payments first. Linking, changing and removal are not yet available.'),
      organization:t('企业 2 个真人席位起购。Teams 每席内含额度独立，加购额度共享；Enterprise 另购共享额度。成员共享池月上限由管理员设置。成员管理与席位变更尚未接入。','Business plans start at 2 human seats. Teams includes separate seat credits and shared purchased credits; Enterprise purchases shared credits separately. Admins set monthly member caps. Member and seat management are not yet available.'),
      procurement:t('深圳主体签约与收款；合同明确席位、Sage Credits、服务范围、含税金额和服务期。申请不是订单，也不自动续费；财务确认实际到账后才开通，汇款回单不等于付款确认。','The Shenzhen company contracts and collects payment. Contracts define seats, Sage Credits, scope, tax-inclusive charges and term. An inquiry is not an order and does not renew automatically. Access follows verified bank receipt, not a transfer document alone.')
    };
    const version = ++accountVersion;
    policies['my-usage'] = t('仅展示你在当前企业的席位与共享池使用限制，不展示其他成员或企业资金。共享上限不占用席位内含额度；如需调整，请联系企业管理员。','Only your seat and shared-pool usage limits in this business are shown, not other members or business funds. Shared caps do not reduce included seat credits. Contact your administrator for changes.');
    policies['credits-ledger'] = t('这里展示实际记录的额度动作，不是当前余额或人民币账单。预占不代表消耗，释放预占不代表退款；到期余额请在 Sage Credits 查看。','These are recorded credit movements, not current balances or a currency bill. Reservations are not consumption and releases are not refunds. Check Sage Credits for expiring balances.');
    policies['organization-history'] = t('只读展示已记录的席位分配和成员共享上限变更，不代表当前配置。操作人标识已脱敏；缺失的历史版本显示未知，不视为不限制。此处不会修改成员、席位或额度。','This is a read-only record of seat assignments and member shared-limit changes, not the current configuration. Actor identifiers are masked. Missing historical versions are unknown, not unlimited. This page does not change members, seats or Credits.');
    document.title = t('订阅与账单 · Sage','Billing · Sage');
    shell(`<header class="page-heading"><span class="eyebrow">SAGE / BILLING</span><h1>${t('订阅与账单','Subscription and billing')}</h1><p>${t('管理方案、用量、支付方式与付款记录','Manage plans, usage, payment methods and payment records.')}</p></header><div class="account-layout"><nav class="account-menu" aria-label="${t('账单导航','Billing navigation')}">${Object.entries(sections).map(([key,label]) => `<a href="#${key}" ${selected === key ? 'aria-current="page"' : ''}>${icon(icons[key])}${label}</a>`).join('')}</nav><section class="panel" aria-labelledby="account-section-title"><h2 id="account-section-title">${sections[selected]}</h2><div id="account-live" aria-live="polite"></div><div class="policy-block"><h3>${t('使用与办理说明','How it works')}</h3><p>${policies[selected]}</p>${selected === 'procurement' ? `<a class="button" href="${page('enterprise')}#contact">${t('查看企业服务与采购说明','View enterprise services and procurement')}</a>` : ''}</div>${selected === 'credits' ? `<section id="credit-packs" aria-live="polite"></section>` : ''}</section></div><p class="notice">${boundary}</p>`);
    const target = document.querySelector('#account-live');
    if (!session?.authenticated || sessionBusy || !activeAccount()) {
      const reason = sessionBusy || sessionState === 'loading' ? t('正在确认账户，请稍候…','Confirming your account…') : sessionState === 'signed-out' ? t('登录后查看当前账户的数据。','Sign in to view your account data.') : accountUnavailable;
      target.innerHTML = `<div class="empty"><div class="empty-symbol">${icon('shield-lock')}</div><p>${reason}</p>${!sessionBusy && sessionState !== 'loading' ? `<a class="button primary" href="${escape(authURL())}">${t('登录 / 注册','Sign in / Register')}</a>` : ''}${sessionState === 'unavailable' ? `<button class="button" id="account-reconnect">${t('重新连接','Reconnect')}</button>` : ''}</div>`;
      document.querySelector('#account-reconnect')?.addEventListener('click', loadSession);
      return;
    }
    target.innerHTML = `<p class="notice">${t('正在读取当前账户…','Loading your current account…')}</p>`;
    loadAccountSection(selected, target, version, session.active_account_id);
  }

  const unknown = () => t('未知','Unknown');
  function dateLabel(value) {
    if (typeof value !== 'string' || !Number.isFinite(Date.parse(value))) return unknown();
    return new Intl.DateTimeFormat(en ? 'en-GB' : 'zh-CN', {dateStyle:'medium',timeStyle:'short'}).format(new Date(value));
  }
  function statusLabel(value) {
    if (value === 'closed') return t('已关闭','Closed');
    return ({pending_payment:t('待付款','Awaiting payment'),paid_pending_entitlement:t('已付款，权益处理中','Paid; benefits processing'),fulfilled:t('已交付','Fulfilled'),not_started:t('尚未开始','Not started'),received:t('已接收，待跟进','Received; awaiting follow-up'),pending:t('待处理','Pending'),created:t('已创建','Created'),paid:t('已确认支付','Payment confirmed'),completed:t('已完成','Completed'),failed:t('失败','Failed'),cancelled:t('已取消','Cancelled'),expired:t('已过期','Expired'),processing:t('处理中','Processing'),active:t('生效中','Active'),delivered:t('已交付','Delivered'),unavailable:t('暂不可用','Unavailable'),refunded:t('已退款','Refunded'),partially_refunded:t('部分退款','Partially refunded')})[value] || unknown();
  }
  function emptyText(text) { return `<p class="account-empty">${text}</p>`; }
  function unavailableModule() { return emptyText(t('此服务尚未接入，数据暂不可查询。','This service is not connected; its records are unavailable.')); }
  async function loadAccountSection(selected, target, version, expectedAccount) {
    const current = () => version === accountVersion && session?.active_account_id === expectedAccount && target.isConnected;
    try {
      if (selected === 'organization-history') {
        await renderOrganizationHistory(target,current,expectedAccount);
        return;
      }
      if (selected === 'credits-ledger') {
        await renderCreditTransactions(target,current,expectedAccount);
        return;
      }
      if (selected === 'notifications') {
        await renderNotifications(target, current, expectedAccount);
        return;
      }
      if (selected === 'my-usage') {
        const data = await api('/api/billing/my-usage');
        if (!current()) return;
        const member = data.member;
        if (data.account_id !== expectedAccount || !member || String(member.user_id) !== String(session.user?.id) || member.active !== true || data.can_edit !== false) throw new Error('INVALID_MEMBER_USAGE');
        for (const key of ['shared_limit','shared_used','shared_reserved']) if (member[key] !== null && (typeof member[key] !== 'string' || !/^\d+(\.\d+)?$/.test(member[key]))) throw new Error('INVALID_MEMBER_USAGE');
        if (member.seat_position !== null && (!Number.isInteger(member.seat_position) || member.seat_position < 1)) throw new Error('INVALID_MEMBER_USAGE');
        target.innerHTML = `<h3 class="records-title">${t('本人席位与共享池','Your seat and shared pool')}</h3><p>${member.seat_position === null ? t('未分配席位','No seat assigned') : `${t('席位','Seat')} ${member.seat_position}`}</p><dl class="account-metrics"><div><dt>${t('共享上限','Shared limit')}</dt><dd>${member.shared_limit === null ? t('不限制','Unlimited') : member.shared_limit === '0' ? t('禁止使用','Disabled') : escape(member.shared_limit)}</dd></div><div><dt>${t('共享已用','Shared used')}</dt><dd>${member.shared_used === null ? unknown() : escape(member.shared_used)}</dd></div><div><dt>${t('共享预占','Shared reserved')}</dt><dd>${member.shared_reserved === null ? unknown() : escape(member.shared_reserved)}</dd></div></dl><p class="account-context">${data.subscription_active === true ? `${t('当前额度周期','Current credit period')}：${dateLabel(data.period_start)} – ${dateLabel(data.period_end)}` : t('当前没有生效订阅，周期用量未知，不表示零。','There is no active subscription. Period usage is unknown, not zero.')}</p><button class="button" id="usage-refresh">${t('刷新','Refresh')}</button>`;
        target.querySelector('#usage-refresh').addEventListener('click',account);
        return;
      }
      const data = await api('/api/billing/account');
      if (!current()) return;
      if (data.account?.id !== expectedAccount || typeof data.account.name !== 'string' || !['personal','business'].includes(data.account.kind) || !data.credits || !Array.isArray(data.orders)) throw new Error('INVALID_ACCOUNT');
      const prefix = `<p class="account-context">${escape(accountName(data.account))} · ${data.account.kind === 'personal' ? t('个人账户','Personal account') : t('企业账户','Business account')}</p>`;
      let content;
      if (selected === 'credits') {
        const balances = [['available',t('可用','Available')],['reserved',t('任务预占','Reserved')],['frozen',t('退款冻结','Frozen')],['consumed',t('已消耗','Consumed')],['expired',t('已过期','Expired')],['refunded',t('已退款核销','Refunded credits')]];
        for (const [key] of balances) if (typeof data.credits[key] !== 'string' || !/^\d+(\.\d+)?$/.test(data.credits[key])) throw new Error('INVALID_BALANCE');
        if (!Array.isArray(data.credits.batches)) throw new Error('INVALID_BALANCE');
        for (const batch of data.credits.batches) for (const [key] of balances) if (typeof batch[key] !== 'string' || !/^\d+(\.\d+)?$/.test(batch[key])) throw new Error('INVALID_BATCH');
        content = `<dl class="account-metrics">${balances.map(([key,label]) => `<div><dt>${label}</dt><dd>${escape(data.credits[key])}</dd></div>`).join('')}</dl><p class="account-context">${t('以上均为 Sage Credits 数量；退款核销不代表资金已到账。','All values are quantities of Sage Credits. Refunded credits do not confirm receipt of funds.')}</p><h3 class="records-title">${t('额度批次','Credit batches')}</h3>${data.credits.batches.length ? `<ul class="record-list">${data.credits.batches.map(batch => `<li><span>${batch.source === 'subscription' ? t('订阅额度','Subscription credits') : batch.source === 'purchased' ? t('加购额度','Purchased credits') : unknown()}</span>${balances.map(([key,label]) => `<small>${label} ${escape(batch[key])}</small>`).join('')}<small>${t('到期','Expires')} ${dateLabel(batch.expires_at)}</small></li>`).join('')}</ul>` : emptyText(t('当前账户还没有额度批次。','This account has no credit batches.'))}`;
      } else if (selected === 'orders') {
        await renderOrders(target, prefix, current, expectedAccount, version);
        return;
      } else if (selected === 'subscription') {
        content = data.capabilities?.subscription !== true ? unavailableModule() : '';
      } else if (selected === 'invoices') {
        content = data.capabilities?.invoices !== true ? unavailableModule() : emptyText(t('请在已付款订单中查看可申请的发票。','Check your paid orders for invoice eligibility.'));
      } else if (selected === 'payment-methods') {
        content = data.capabilities?.payment_methods !== true ? unavailableModule() : emptyText(t('支付方式管理尚未开放操作。','Payment-method management is not yet available.'));
      } else if (selected === 'organization') {
        content = emptyText(data.account.kind === 'personal' ? t('当前是个人账户。已有企业账户可在右上角切换。','This is a personal account. Switch to an existing business account from the top-right menu.') : t('当前企业账户已连接；成员、席位和使用上限管理尚未开放。','Your business account is connected; member, seat and usage-cap management is not yet available.'));
      } else content = '';
      target.innerHTML = prefix + content;
      if (selected === 'credits') {
        const packs = document.querySelector('#credit-packs');
        const loadPacks = async () => {
          if (!current() || !packs?.isConnected) return;
          const request = Symbol(); packs.request = request;
          const latest = () => current() && packs.isConnected && packs.request === request;
          packs.innerHTML = `<h3>Sage Credits</h3><p>${t('正在查询当前账户可购买的额度包…','Loading approved credit packs for this account…')}</p>`;
          try {
            const result = await api('/api/billing/purchase-catalog');
            if (!latest()) return;
            if (result.account_id !== expectedAccount || result.currency !== 'CNY' || !Array.isArray(result.items)) throw new Error('INVALID_CATALOG');
            const items = result.items.filter(item => item.kind === 'credits');
            if (items.some(item => typeof item.quote_available !== 'boolean')) throw new Error('INVALID_CREDIT_PACK');
            const available = items.filter(item => item.quote_available);
            for (const item of available) if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(item.id) || item.currency !== 'CNY' || typeof item.unit_amount_minor !== 'string' || !/^\d+$/.test(item.unit_amount_minor) || BigInt(item.unit_amount_minor) <= 0n || typeof item.credits_quantity !== 'string' || !/^\d+(\.\d+)?$/.test(item.credits_quantity) || !/[1-9]/.test(item.credits_quantity) || !Array.isArray(item.purchase_options) || !item.purchase_options.length) throw new Error('INVALID_CREDIT_PACK');
            packs.innerHTML = `<h3>${t('Sage Credits 一次性加购','One-time Sage Credits purchase')}</h3><p class="checkout-note">${t('加购额度自确认到账起 12 个月有效。查看报价或创建订单不会立即收款，也不会开启自动充值。','Purchased credits last 12 months from confirmed receipt. Viewing a quote or creating an order does not charge you or enable auto top-up.')}</p>` + (available.length ? `<ul class="record-list">${available.map(item => `<li><strong>${escape(item.credits_quantity)} Sage Credits</strong><span>${money(item.unit_amount_minor,true)}</span><a class="button" data-credit-purchase href="${page('checkout')}?${new URLSearchParams({product:item.id,seats:'1'})}">${t('查看加购报价','Review purchase quote')}</a></li>`).join('')}</ul>` : `<p data-credit-packs-empty>${t('当前账户暂无可报价的额度包。可能尚未开放加购，或当前订阅不满足购买条件；不能付款。','No credit packs can be quoted for this account. Purchases may not be enabled or your subscription may not be eligible; no payment is available.')}</p>`);
          } catch (error) {
            if (!latest()) return;
            packs.innerHTML = `<h3>Sage Credits</h3><p class="error" role="status">${requestError(error,t('额度包读取失败，可购买状态未知，不能据此判断没有额度包。','Credit packs could not be loaded. Availability is unknown; this does not mean there are no packs.'))}</p><button class="button" data-credit-packs-retry>${t('重新查询额度包','Retry credit packs')}</button>`;
            packs.querySelector('button').onclick = loadPacks;
          }
        };
        await loadPacks();
      }
      if (selected === 'subscription' && data.capabilities?.subscription === true) await renderSubscriptions(target, prefix, current);
      if (selected === 'organization' && data.account.kind === 'business') await renderOrganization(target, prefix, current, expectedAccount);
      if (selected === 'credits' && data.account.kind === 'business') {
        const topup = document.createElement('section');
        topup.className = 'subscription-card topup-status';
        target.append(topup);
        await renderAutomaticTopup(topup,current,expectedAccount);
      }
      if (selected === 'payment-methods') await renderPaymentMethods(target, prefix, current, expectedAccount);
      if (selected === 'procurement') await renderProcurement(target, prefix, data.account, current);
    } catch (error) {
      if (!current()) return;
      if (error.status === 401) { session = null; sessionState = 'signed-out'; accountVersion++; refreshSessionView(); return; }
      target.innerHTML = `<div class="empty"><p class="error">${requestError(error, t('无法读取当前账户的数据，余额与记录状态未知。请重试。','This account could not be loaded. Balances and records are unknown. Retry.'))}</p><button class="button" id="account-read-retry">${t('重新读取','Retry')}</button></div>`;
      document.querySelector('#account-read-retry').addEventListener('click', account);
    }
  }

  async function renderCreditTransactions(target, current, accountId, offset = 0) {
    const request = Symbol(); target.creditHistoryRequest = request;
    const latest = () => current() && target.creditHistoryRequest === request;
    target.innerHTML = emptyText(t('正在读取额度流水…','Loading Credits history…'));
    const messages = {
      grant:[t('额度发放','Credits granted'),t('额度已发放到对应批次，实际可用量仍受有效期与冻结限制。','Credits were granted to this batch. Expiry and freezes still affect availability.')],
      reserve:[t('任务预占','Task reservation'),t('为任务预算预占，不代表已消耗。','Reserved for a task budget; not consumption.')],
      settle:[t('任务消耗','Task consumption'),t('按已确认的任务用量结算。','Settled from confirmed task usage.')],
      release:[t('预占释放','Reservation released'),t('未用预占已释放，不代表人民币退款。','Unused reservation released; not a currency refund.')],
      freeze:[t('退款冻结','Refund freeze'),t('为退款核查冻结额度，不代表已退款。','Credits frozen for refund review; not a confirmed refund.')],
      unfreeze:[t('冻结解除','Credits unfrozen'),t('退款冻结已解除，未确认退款；可用量仍以当前批次为准。','Refund freeze released without a confirmed refund. Current batch availability remains authoritative.')],
      refund:[t('额度退款扣回','Credits removed for refund'),t('对应额度因已确认退款而扣回，不是任务消耗；资金到账以原订单处理结果为准。','Credits removed for a confirmed refund, not task consumption. Check the original order for the money refund status.')],
      unknown:[t('未知额度动作','Unknown Credits movement'),t('动作类型未能确认，不显示消耗数量或成功结果。请重新读取或联系支持。','The movement type is unconfirmed. No consumption quantity or success result is shown. Retry or contact support.')]
    };
    const uuid = value => typeof value === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value);
    try {
      const data = await api(`/api/billing/credits/transactions?offset=${offset}`);
      if (!latest()) return;
      if (data.account_id !== accountId || data.offset !== offset || !Number.isSafeInteger(data.total) || data.total < 0 ||
          !Array.isArray(data.items) || data.items.length > 50 || data.items.length > data.total ||
          (data.next_offset !== null && (data.next_offset !== offset+50 || data.items.length !== 50 || data.next_offset >= data.total))) throw new Error('INVALID_CREDIT_HISTORY');
      const seen = new Set();
      for (const item of data.items) {
        if (!item || typeof item.id !== 'string' || !/^[1-9][0-9]*$/.test(item.id) || seen.has(item.id) ||
            !Object.hasOwn(messages,item.kind) || !uuid(item.batch_id) || !['subscription','purchased','unknown'].includes(item.source) ||
            (item.kind === 'unknown' ? item.quantity !== null : typeof item.quantity !== 'string' || !/^[0-9]+(?:\.[0-9]+)?$/.test(item.quantity)) ||
            ['order_id','reservation_id','seat_id'].some(key=>item[key] !== null && !uuid(item[key])) ||
            typeof item.occurred_at !== 'string' || !Number.isFinite(Date.parse(item.occurred_at))) throw new Error('INVALID_CREDIT_HISTORY');
        seen.add(item.id);
      }
      target.innerHTML = `<h3 class="records-title" id="credit-history-title" tabindex="-1">${t('额度变动记录','Credits movement history')}</h3>` +
        (data.items.length ? `<ul class="record-list">${data.items.map(item=>{
          const [title,note] = messages[item.kind], known = item.kind !== 'unknown';
          const refs = known ? `<p><small>${t('批次','Batch')} ${escape(item.batch_id)}</small></p>${item.reservation_id ? `<p><small>${t('任务记录','Task record')} ${escape(item.reservation_id)}</small></p>` : ''}${item.seat_id ? `<p><small>${t('席位','Seat')} ${escape(item.seat_id)}</small></p>` : ''}` : '';
          return `<li class="billing-notification" data-credit-entry="${escape(item.id)}" data-credit-kind="${item.kind}"><div><strong>${title}</strong>${known ? `<span class="notification-amount" data-credit-quantity>${escape(item.quantity)} Sage Credits</span>` : ''}<p>${note}</p><p><small>${t('来源','Source')} ${item.source === 'subscription' ? t('订阅额度','Subscription credits') : item.source === 'purchased' ? t('加购额度','Purchased credits') : unknown()}</small></p>${refs}<p><small>${dateLabel(item.occurred_at)}</small></p></div>${known && item.order_id ? `<a class="text-link" href="${page('billing')}?order=${encodeURIComponent(item.order_id)}#orders">${t('查看原订单','View original order')}</a>` : ''}</li>`;
        }).join('')}</ul>` : emptyText(offset ? t('此页没有额度流水，请返回上一页。','No Credits movements on this page. Return to the previous page.') : t('暂无额度流水。实际发放、预占与结算后会在这里显示记录。','No Credits movements yet. Actual grants, reservations and settlements will appear here.'))) +
        `<div class="organization-pagination">${offset ? `<button class="text-link" data-credit-history-prev>${t('上一页','Previous')}</button>` : ''}${data.next_offset !== null ? `<button class="text-link" data-credit-history-next>${t('下一页','Next')}</button>` : ''}<button class="text-link" data-credit-history-refresh>${t('刷新流水','Refresh history')}</button></div>`;
      const navigate = async next => {
        if (!latest()) return;
        await renderCreditTransactions(target,current,accountId,next);
        if (current()) target.querySelector('#credit-history-title')?.focus();
      };
      target.querySelector('[data-credit-history-prev]')?.addEventListener('click',()=>navigate(Math.max(0,offset-50)));
      target.querySelector('[data-credit-history-next]')?.addEventListener('click',()=>navigate(data.next_offset));
      target.querySelector('[data-credit-history-refresh]').addEventListener('click',()=>navigate(offset));
    } catch (error) {
      if (!latest()) return;
      target.innerHTML = `<p class="error" role="status">${requestError(error,t('额度流水读取失败，记录状态未知；这不表示没有记录。请重新读取。','Credits history could not be loaded. Its status is unknown; this does not mean there are no records. Retry the read.'))}</p><button class="button" data-credit-history-retry>${t('重新读取流水','Retry history')}</button>`;
      target.querySelector('[data-credit-history-retry]').addEventListener('click',()=>renderCreditTransactions(target,current,accountId,offset));
    }
  }

  async function renderOrganizationHistory(target, current, accountId, offset = 0) {
    const request = Symbol(); target.organizationHistoryRequest = request;
    const latest = () => current() && target.organizationHistoryRequest === request;
    target.innerHTML = emptyText(t('正在读取企业变更记录…','Loading business change history…'));
    const positive = value => Number.isSafeInteger(value) && value > 0;
    const quantity = value => value === null || (typeof value === 'string' && /^\d+(\.\d+)?$/.test(value));
    const member = value => value === null ? t('未分配','Unassigned') : `${t('成员','Member')} #${escape(value)}`;
    const limit = value => value === null ? t('不限制','Unlimited') : /^0+(\.0+)?$/.test(value) ? t('禁止使用共享池','Shared pool disabled') : `${escape(value)} Sage Credits`;
    try {
      const data = await api(`/api/billing/organization-history?offset=${offset}`);
      if (!latest()) return;
      if (data.account_id !== accountId || data.offset !== offset || !Number.isSafeInteger(data.total) || data.total < 0 ||
          !Array.isArray(data.items) || data.items.length > 50 ||
          (data.next_offset !== null && (data.next_offset !== offset+50 || data.items.length !== 50)) ||
          (data.items.length && data.total < offset+data.items.length)) throw new Error('INVALID_ORGANIZATION_HISTORY');
      const ids = new Set();
      for (const item of data.items) {
        if (!item || !['seat_assignment','shared_limit'].includes(item.type) ||
            typeof item.id !== 'string' || !(item.type === 'seat_assignment' ? /^seat:[1-9]\d*$/.test(item.id) : /^limit:[1-9]\d*$/.test(item.id)) || ids.has(item.id) ||
            typeof item.created_at !== 'string' || !Number.isFinite(Date.parse(item.created_at)) ||
            typeof item.actor_masked_identifier !== 'string' || !positive(item.version)) throw new Error('INVALID_ORGANIZATION_CHANGE');
        if (item.type === 'seat_assignment') {
          if (typeof item.seat_id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(item.seat_id) ||
              !positive(item.seat_position) || !Number.isSafeInteger(item.before_version) || item.before_version < 0 || item.version !== item.before_version+1 ||
              [item.before_membership_id,item.after_membership_id].some(value=>value !== null && !positive(value))) throw new Error('INVALID_SEAT_CHANGE');
        } else if (!positive(item.membership_id) || !quantity(item.quantity) || typeof item.previous_known !== 'boolean' ||
                   !quantity(item.previous_quantity) || (!item.previous_known && item.previous_quantity !== null)) throw new Error('INVALID_SHARED_LIMIT_CHANGE');
        ids.add(item.id);
      }
      target.innerHTML = `<h3 class="records-title" id="organization-history-title" tabindex="-1">${t('席位与共享上限历史','Seat and shared-limit history')}</h3>` +
        (data.items.length ? `<ul class="record-list">${data.items.map(item=>{
          const seat = item.type === 'seat_assignment';
          const title = seat ? `${t('席位','Seat')} ${item.seat_position} · ${t('分配变更','Assignment change')}` : `${t('成员','Member')} #${item.membership_id} · ${t('共享上限变更','Shared limit change')}`;
          const previous = seat ? member(item.before_membership_id) : item.previous_known ? limit(item.previous_quantity) : unknown();
          const next = seat ? member(item.after_membership_id) : limit(item.quantity);
          return `<li class="billing-notification" data-organization-change="${escape(item.id)}"><div><strong>${title}</strong><p>${t('变更前','Before')}：${previous}<br>${t('变更后','After')}：${next}</p><small>${t('版本','Version')} ${seat ? `${item.before_version} → ` : ''}${item.version} · ${dateLabel(item.created_at)}</small><p>${t('操作人','Changed by')}：${escape(item.actor_masked_identifier || unknown())}</p></div></li>`;
        }).join('')}</ul>` : emptyText(offset ? t('此页没有企业变更记录，请返回上一页。','There are no business changes on this page. Return to the previous page.') : t('暂无企业变更记录。后续已记录的席位与共享上限变更会展示在这里。','No business changes are recorded yet. Recorded seat and shared-limit changes will appear here.'))) +
        `<div class="organization-pagination">${offset ? `<button class="text-link" data-organization-history-prev>${t('上一页','Previous')}</button>` : ''}${data.next_offset !== null ? `<button class="text-link" data-organization-history-next>${t('下一页','Next')}</button>` : ''}<button class="text-link" data-organization-history-refresh>${t('刷新记录','Refresh history')}</button><a class="text-link" href="#organization">${t('查看当前企业配置','View current business settings')}</a></div>`;
      const navigate = async next => {
        if (!latest()) return;
        await renderOrganizationHistory(target,current,accountId,next);
        if (current()) target.querySelector('#organization-history-title')?.focus();
      };
      target.querySelector('[data-organization-history-prev]')?.addEventListener('click',()=>navigate(Math.max(0,offset-50)));
      target.querySelector('[data-organization-history-next]')?.addEventListener('click',()=>navigate(data.next_offset));
      target.querySelector('[data-organization-history-refresh]').addEventListener('click',()=>navigate(offset));
    } catch (error) {
      if (!latest()) return;
      target.innerHTML = `<p class="error" role="status">${requestError(error,t('企业变更记录读取失败，历史状态未知；这不表示没有变更。请重新读取。','Business history could not be loaded. Its status is unknown; this does not mean no changes occurred. Retry the read.'))}</p><button class="button" data-organization-history-retry>${t('重新读取记录','Retry history')}</button>`;
      target.querySelector('[data-organization-history-retry]').addEventListener('click',()=>renderOrganizationHistory(target,current,accountId,offset));
    }
  }

  async function renderNotifications(target, current, accountId, offset = 0) {
    const request = Symbol(); target.notificationsRequest = request;
    const latest = () => current() && target.notificationsRequest === request;
    target.innerHTML = emptyText(t('正在读取账务通知…','Loading billing updates…'));
    const messages = {
      payment_received:[t('付款已确认','Payment confirmed'),t('此事件只确认收款，不代表权益已完成交付。请查看订单的最新结果。','This event confirms payment, not benefit delivery. Check the order for the latest result.')],
      renewal_payment_received:[t('续费付款已确认','Renewal payment confirmed'),t('已确认收到此笔续费款项，不代表续费服务期或额度已完成交付。请查看原订单及订阅的最新状态。','This renewal payment has been received. It does not confirm delivery of the new service term or Credits. Check the original order and latest subscription status.')],
      topup_payment_received:[t('自动充值付款已确认','Automatic top-up payment confirmed'),t('已确认收到此笔自动充值款项，不代表 Sage Credits 已到账。请查看原订单的额度交付结果。','This automatic top-up payment has been received. It does not confirm that Sage Credits were delivered. Check the original order’s delivery result.')],
      topup_payment_failed:[t('自动充值付款失败已确认','Automatic top-up payment failed'),t('此笔自动充值付款已确认失败，不会自动重扣。已预占的任务和剩余额度不因此取消；请核对原订单及充值授权状态。','This automatic top-up payment is confirmed failed and will not be automatically retried. Existing task reservations and remaining Credits are not cancelled. Check the original order and top-up authorization.')],
      cancellation_processing:[t('解约处理中','Cancellation processing'),t('已停止发起新的续费扣款，支付渠道尚未确认解约。在途交易仍需另行核实。','New renewal charges have stopped, but the payment channel has not confirmed cancellation. In-flight payments still need verification.')],
      cancellation_confirmed:[t('解约已确认','Cancellation confirmed'),t('支付渠道已确认取消自动续费。已付款的服务期不会因此提前结束或自动退款。','The payment channel has confirmed cancellation of automatic renewal. Paid service terms do not end early and are not automatically refunded.')],
      payment_failed:[t('支付失败已确认','Payment failure confirmed'),t('此笔支付已确认失败，不代表其他订单的付款结果。请查看原订单；不要将结果未知的交易当作失败重新付款。','This payment is confirmed failed; other orders are unaffected. Check the original order. Do not repay a transaction whose outcome is unknown.')],
      renewal_failed:[t('续费失败已确认','Renewal failure confirmed'),t('此笔续费已确认失败，未因此延长服务期。请核对原订单及订阅的当前状态。','This renewal is confirmed failed and has not extended the service term. Check the original order and current subscription status.')],
      credit_batch_expired:[t('一个 Credits 批次已到期','A Credits batch has expired'),t('对应批次的有效期已结束；这不表示其他批次也已到期。请查看 Credits 的当前余额和批次记录。','This batch has reached its expiry. Other batches may still be valid; check current Credits balances and batch records.')],
      service_period_ended:[t('一个已付款服务期已结束','A paid service term has ended'),t('此记录对应原订单的服务期终点，不表示后续已付款期间结束，也不代表整个账户已停服。请查看订阅当前状态。','This records the end of the original order’s service term. Later paid terms may still be valid; it does not mean the entire account has lost service. Check current subscription status.')],
      topup_authorization_recorded:[t('自动充值授权已记录','Top-up authorization recorded'),t('此记录不代表已充值或已扣款。请核对授权与账务状态。','This record does not confirm a top-up or a charge. Check authorization and billing status.')],
      topup_authorization_paused:[t('自动充值已在本地暂停','Automatic top-up paused locally'),t('系统已暂停发起新的自动充值；这不代表支付渠道授权已撤销，也不改变在途交易的结果。','New automatic top-ups are paused locally. Channel authorization is not necessarily revoked and in-flight transactions are unchanged.')],
      topup_revocation_processing:[t('充值授权撤销处理中','Top-up revocation processing'),t('已在本地停用自动充值，支付渠道尚未确认授权撤销。','Automatic top-up is disabled locally; channel revocation has not yet been confirmed.')],
      topup_revocation_confirmed:[t('充值授权撤销已确认','Top-up revocation confirmed'),t('支付渠道已确认撤销此充值授权；既有充值与在途交易仍以各自结果为准。','The channel has confirmed revocation of this authorization. Existing top-ups and in-flight payments retain their own outcomes.')],
      seat_assignment_changed:[t('席位配置已记录','Seat configuration recorded'),t('席位分配配置已生成新的版本记录。请核对企业当前席位；此记录不代表已向成员发送通知。','A new seat assignment version has been recorded. Check current organization seats; this does not confirm delivery of a notice to members.')],
      shared_limit_changed:[t('共享额度上限配置已记录','Shared limit configuration recorded'),t('企业共享额度上限已生成新的版本记录；这不会产生新的 Credits 或扣款。','A new shared limit configuration version has been recorded. It creates no new Credits or charge.')],
      credit_refund_reserved:[t('退款处理中','Refund processing'),t('已为此申请冻结对应 Credits，尚未确认资金退款。请查看订单中的后续结果。','The corresponding Credits have been frozen for this request. No money refund has been confirmed; check the order for later results.')],
      credit_refund_refunded:[t('退款已确认','Refund confirmed'),t('已收到退款成功确认；实际到账时间以支付渠道及银行为准。','Refund success has been confirmed. Arrival in your account depends on the payment channel and bank.')],
      credit_refund_released:[t('退款已终止，未退回款项','Refund ended without payment'),t('已确认此次申请未退款并结束处理，对应 Credits 冻结已解除；可用额度仍受批次有效期限制。','The request ended with no refund; its Credits freeze was released. Available Credits remain subject to batch expiry.')],
      unknown:[t('未知账务事件','Unknown billing event'),t('此事件暂无法识别，不能据此判断付款、解约或退款成功。请核对订单及订阅状态。','This event is not recognized. It does not confirm payment, cancellation or a refund. Check your orders and subscription.')]
    };
    try {
      const data = await api(`/api/billing/notifications?offset=${offset}`);
      if (!latest()) return;
      const validId = id => typeof id === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);
      if (data.account_id !== accountId || data.offset !== offset || !Number.isSafeInteger(data.total) || data.total < 0 ||
          !Array.isArray(data.items) || data.items.length > 50 ||
          (data.next_offset !== null && (data.next_offset !== offset+50 || data.items.length !== 50)) ||
          (data.items.length && data.total < offset+data.items.length)) throw new Error('INVALID_NOTIFICATIONS');
      const ids = new Set();
      for (const item of data.items) {
        const monetary = ['payment_received','renewal_payment_received','topup_payment_received','credit_refund_reserved','credit_refund_refunded','credit_refund_released'].includes(item.type);
        if (!validId(item.id) || ids.has(item.id) || !Object.hasOwn(messages,item.type) ||
            typeof item.occurred_at !== 'string' || !Number.isFinite(Date.parse(item.occurred_at)) ||
            ['order_id','subscription_id','refund_id'].some(key=>item[key] !== null && !validId(item[key])) ||
            (monetary ? item.currency !== 'CNY' || typeof item.amount_minor !== 'string' || !/^\d+$/.test(item.amount_minor) || BigInt(item.amount_minor) <= 0n : item.currency !== null || item.amount_minor !== null)) throw new Error('INVALID_NOTIFICATION');
        ids.add(item.id);
      }
      target.innerHTML = `<h3 class="records-title" id="notifications-title" tabindex="-1">${t('账务事件记录','Billing event history')}</h3>` +
        (data.items.length ? `<ul class="record-list">${data.items.map(item=>{
          const [title,note] = messages[item.type];
          const link = item.type === 'unknown' ? '' : item.type.startsWith('cancellation_') ? `<a class="text-link" href="#subscription">${t('查看订阅','View subscription')}</a>` :
            ['seat_assignment_changed','shared_limit_changed'].includes(item.type) ? `<a class="text-link" href="#organization-history">${t('查看企业变更记录','View organization history')}</a>` :
            item.type.startsWith('topup_') && !item.order_id ? `<a class="text-link" href="#credits">${t('查看充值授权','View top-up authorization')}</a>` :
            item.type === 'credit_batch_expired' ? `<a class="text-link" href="#credits">${t('查看额度批次','View Credits batches')}</a>` :
            item.order_id ? `<a class="text-link" href="${page('billing')}?${new URLSearchParams({order:item.order_id})}#orders">${t('查看订单','View order')}</a>` : '';
          return `<li class="billing-notification"><div><strong>${title}</strong>${item.amount_minor === null ? '' : `<span class="notification-amount">${money(item.amount_minor,true)}</span>`}<p>${note}</p><small>${dateLabel(item.occurred_at)}</small></div>${link}</li>`;
        }).join('')}</ul>` : emptyText(offset ? t('此页没有通知，请返回上一页。','There are no updates on this page. Return to the previous page.') : t('暂无账务通知。后续已确认的付款、解约及退款事件会保留在这里。','No billing updates yet. Confirmed payments, cancellations and refund events will be recorded here.'))) +
        `<div class="organization-pagination">${offset ? `<button class="text-link" data-notifications-prev>${t('上一页','Previous')}</button>` : ''}${data.next_offset !== null ? `<button class="text-link" data-notifications-next>${t('下一页','Next')}</button>` : ''}<button class="text-link" data-notifications-refresh>${t('刷新通知','Refresh updates')}</button></div>`;
      const navigate = async next => {
        if (!latest()) return;
        await renderNotifications(target,current,accountId,next);
        if (current()) target.querySelector('#notifications-title')?.focus();
      };
      target.querySelector('[data-notifications-prev]')?.addEventListener('click',()=>navigate(Math.max(0,offset-50)));
      target.querySelector('[data-notifications-next]')?.addEventListener('click',()=>navigate(data.next_offset));
      target.querySelector('[data-notifications-refresh]').addEventListener('click',()=>navigate(offset));
    } catch (error) {
      if (!latest()) return;
      target.innerHTML = `<p class="error" role="status">${requestError(error,t('通知读取失败，记录状态未知；这不表示没有账务事件。请重新读取。','Updates could not be loaded. Their status is unknown; this does not mean there are no billing events. Retry the read.'))}</p><button class="button" data-notifications-retry>${t('重新读取通知','Retry updates')}</button>`;
      target.querySelector('[data-notifications-retry]').addEventListener('click',()=>renderNotifications(target,current,accountId,offset));
    }
  }

  async function renderOrders(target, prefix, current, accountId, version, offset = 0) {
    const request = Symbol(); target.ordersRequest = request;
    const latest = () => current() && target.ordersRequest === request;
    target.innerHTML = prefix + emptyText(t('正在读取订单…','Loading orders…'));
    try {
      const data = await api(`/api/billing/orders?offset=${offset}`);
      if (!latest()) return;
      const validId = id => typeof id === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);
      if (data.account_id !== accountId || !Array.isArray(data.items) || data.items.length > 50 ||
          (data.next_offset !== null && (!Number.isSafeInteger(data.next_offset) || data.next_offset !== offset + 50 || data.items.length !== 50))) throw new Error('INVALID_ORDERS');
      const ids = new Set();
      for (const item of data.items) {
        if (!validId(item.id) || ids.has(item.id) || item.account_id !== accountId || item.currency !== 'CNY' ||
            !Number.isSafeInteger(item.amount_minor) || item.amount_minor <= 0 ||
            !['pending_payment','paid_pending_entitlement','fulfilled','closed'].includes(item.status) ||
            !Number.isFinite(Date.parse(item.created_at))) throw new Error('INVALID_ORDERS');
        ids.add(item.id);
      }
      target.innerHTML = prefix + `<h3 class="records-title" id="orders-list-title" tabindex="-1">${t('付款与订单记录','Payments and orders')}</h3>` + (data.items.length ? `<ul class="record-list">${data.items.map(order => `<li><span>${escape(order.plan_code || t('订单','Order'))}</span><strong>${money(order.amount_minor, true)}</strong><span>${statusLabel(order.status)}</span><small>${dateLabel(order.created_at)}</small><button class="text-link" data-order="${escape(order.id)}">${t('查看详情','View details')}</button></li>`).join('')}</ul>` : emptyText(offset ? t('此页没有订单记录，请返回上一页。','There are no orders on this page. Return to the previous page.') : t('当前账户还没有订单。','This account has no orders.'))) + `<div class="organization-pagination">${offset ? `<button class="text-link" data-orders-prev>${t('上一页','Previous')}</button>` : ''}${data.next_offset !== null ? `<button class="text-link" data-orders-next>${t('下一页','Next')}</button>` : ''}<button class="text-link" data-orders-refresh>${t('刷新订单','Refresh orders')}</button></div><div id="order-detail" tabindex="-1"></div>`;
      const navigate = async next => {
        if (!latest()) return;
        await renderOrders(target, prefix, current, accountId, version, next);
        if (current()) target.querySelector('#orders-list-title')?.focus();
      };
      target.querySelector('[data-orders-prev]')?.addEventListener('click', () => navigate(Math.max(0,offset-50)));
      target.querySelector('[data-orders-next]')?.addEventListener('click', () => navigate(data.next_offset));
      target.querySelector('[data-orders-refresh]').addEventListener('click', () => navigate(offset));
      for (const button of target.querySelectorAll('[data-order]')) button.addEventListener('click', () => loadOrder(button.dataset.order,accountId,version));
      const requestedOrder = initial.get('order');
      // The requested owned order may be older than the first page. The detail
      // endpoint, not membership in a truncated list, verifies its ownership.
      if (offset === 0 && validId(requestedOrder)) await loadOrder(requestedOrder,accountId,version);
    } catch (error) {
      if (!latest()) return;
      target.innerHTML = prefix + `<p class="error" role="status">${requestError(error,t('订单记录读取失败，当前状态未知。请重新查询，不要重复付款。','Orders could not be loaded. Their status is unknown. Retry the read; do not pay again.'))}</p><button class="button" data-orders-retry>${t('重新读取订单','Retry orders')}</button>`;
      target.querySelector('[data-orders-retry]').addEventListener('click', () => renderOrders(target,prefix,current,accountId,version,offset));
    }
  }

  async function renderOrganization(target, prefix, current, accountId, offset = 0) {
    const request = Symbol();
    target.organizationRequest = request;
    target.innerHTML = prefix + emptyText(t('正在读取成员与席位…','Loading members and seats…'));
    try {
      const data = await api(`/api/billing/organization?offset=${offset}`);
      if (!current() || target.organizationRequest !== request) return;
      if (data.account_id !== accountId || !Array.isArray(data.members) || !Number.isInteger(data.total_members) || !Number.isInteger(data.seat_count) || !Number.isInteger(data.assigned_seats)) throw new Error('INVALID_ORGANIZATION');
      for (const item of data.members) {
        if (!Number.isInteger(item.id) || typeof item.masked_identifier !== 'string') throw new Error('INVALID_MEMBER');
        for (const key of ['shared_limit','shared_used','shared_reserved']) if (item[key] !== null && (typeof item[key] !== 'string' || !/^\d+(\.\d+)?$/.test(item[key]))) throw new Error('INVALID_USAGE');
      }
      const quantity = value => value === null ? '—' : `${escape(value)} Sage Credits`;
      const roles = {owner:t('所有者','Owner'),billing_admin:t('账务管理员','Billing admin'),member:t('成员','Member')};
      target.innerHTML = prefix + `<h3 class="records-title">${t('成员与席位','Members and seats')}</h3><dl class="account-metrics"><div><dt>${t('购买席位','Purchased seats')}</dt><dd>${data.seat_count}</dd></div><div><dt>${t('已分配','Assigned')}</dt><dd>${data.assigned_seats}</dd></div><div><dt>${t('成员总数','Total members')}</dt><dd>${data.total_members}</dd></div></dl><p class="account-context">${data.subscription_active ? `${t('当前额度周期','Current credit period')}：${dateLabel(data.period_start)} – ${dateLabel(data.period_end)}` : t('当前没有生效中的订阅，本期共享用量暂不计算；“—”不表示零。','There is no active subscription. Current-period shared usage is unavailable; “—” does not mean zero.')}</p><p class="account-context">${t('当前为只读视图。席位分配与上限修改尚未开放，不会在此产生变更。','This view is read-only. Seat assignments and limit changes are not available here.')}</p>${data.members.length ? `<ul class="record-list organization-members">${data.members.map(item => `<li><strong>${escape(item.masked_identifier || `${t('成员','Member')} #${item.id}`)}</strong><span>${escape(roles[item.role] || unknown())} · ${item.active ? t('有效成员','Active member') : t('已停用','Inactive')}</span><span>${item.seat_position === null ? t('未分配席位','No seat assigned') : `${t('席位','Seat')} ${escape(item.seat_position)}`}</span><small>${t('共享上限','Shared limit')}：${item.shared_limit === null ? t('不限制','Unlimited') : item.shared_limit === '0' ? t('禁止使用共享池','Shared pool disabled') : quantity(item.shared_limit)}</small><small>${t('共享已用','Shared used')}：${quantity(item.shared_used)} · ${t('共享预占','Shared reserved')}：${quantity(item.shared_reserved)}</small></li>`).join('')}</ul>` : emptyText(t('此页没有成员记录。','There are no members on this page.'))}<div class="organization-pagination">${offset > 0 ? `<button class="text-link" data-org-offset="${Math.max(0,offset-50)}">${t('上一页','Previous')}</button>` : ''}${data.next_offset !== null && Number.isInteger(data.next_offset) ? `<button class="text-link" data-org-offset="${data.next_offset}">${t('下一页','Next')}</button>` : ''}<a class="text-link" href="#credits">${t('查看 Sage Credits 批次','View Sage Credits batches')}</a></div>`;
      for (const button of target.querySelectorAll('[data-org-offset]')) button.addEventListener('click', () => renderOrganization(target,prefix,current,accountId,Number(button.dataset.orgOffset)));
    } catch (error) {
      if (!current() || target.organizationRequest !== request) return;
      target.innerHTML = prefix + `<p class="error">${requestError(error,t('成员数据读取失败，请重试。','Members could not be loaded. Please retry.'))}</p><button class="text-link" data-org-retry>${t('重试','Retry')}</button>`;
      target.querySelector('[data-org-retry]').addEventListener('click', () => renderOrganization(target,prefix,current,accountId,offset));
    }
  }
  async function renderAutomaticTopup(target,current,accountId) {
    const request = Symbol(); target.topupRequest = request;
    const heading = `<h3>${t('企业自动充值','Business auto top-up')}</h3>`;
    target.innerHTML = heading + emptyText(t('正在读取授权与预算…','Loading authorization and budget…'));
    try {
      const data = await api('/api/billing/automatic-topup');
      if (!current() || !target.isConnected || target.topupRequest !== request) return;
      const states = {authorized:t('已记录授权','Authorization recorded'),paused:t('已暂停','Paused'),
        superseded:t('已替换','Replaced'),revoking:t('撤销处理中','Revocation in progress'),revoked:t('撤销已确认','Revocation confirmed')};
      const amount = value => typeof value === 'string' && /^\d+$/.test(value);
      const quantity = value => typeof value === 'string' && /^\d+(\.\d+)?$/.test(value);
      const a = data.authorization, b = data.budget;
      if (data.account_id !== accountId || data.charging_enabled !== false ||
          (a !== null && (!a || !Object.hasOwn(states,a.state) || a.currency !== 'CNY' ||
            !quantity(a.threshold) || !quantity(a.pack_credits) || !amount(a.pack_amount_minor) || !amount(a.monthly_cap_minor))) ||
          (b !== null && (!b || !a || !Number.isFinite(Date.parse(b.starts_at)) || !Number.isFinite(Date.parse(b.ends_at)) ||
            !amount(b.spent_minor) || !amount(b.reserved_minor) || typeof b.needs_review !== 'boolean' ||
            (b.remaining_minor !== null && !amount(b.remaining_minor))))) throw new Error('INVALID_TOPUP_STATUS');
      target.innerHTML = heading + `<p class="notice">${t('自动扣款尚未启用。这里仅展示授权记录与预算，不会触发充值。','Automatic charges are not enabled. This view shows authorization and budget records only; it cannot trigger a top-up.')}</p>` +
        (a ? `<p>${states[a.state]}</p><dl class="account-metrics"><div><dt>${t('余额阈值','Balance threshold')}</dt><dd>${escape(a.threshold)}</dd></div><div><dt>${t('固定购买包','Fixed pack')}</dt><dd>${escape(a.pack_credits)} Sage Credits</dd></div><div><dt>${t('每次金额','Per top-up')}</dt><dd>${money(a.pack_amount_minor,true)}</dd></div><div><dt>${t('授权月金额上限','Authorized monthly cap')}</dt><dd>${money(a.monthly_cap_minor,true)}</dd></div></dl>` : emptyText(t('尚未记录独立充值授权。订阅续费授权不包含自动充值。','No separate top-up authorization is recorded. A renewal authorization does not include auto top-up.'))) +
        (b ? `<h4>${t('已记录预算窗口','Recorded budget window')}</h4><p>${dateLabel(b.starts_at)} — ${dateLabel(b.ends_at)}</p><dl class="account-metrics"><div><dt>${t('已用金额','Spent')}</dt><dd>${money(b.spent_minor,true)}</dd></div><div><dt>${t('在途预留','Pending reservation')}</dt><dd>${money(b.reserved_minor,true)}</dd></div><div><dt>${t('预算余量','Budget remaining')}</dt><dd>${b.remaining_minor === null ? '—' : money(b.remaining_minor,true)}</dd></div></dl>${b.needs_review ? `<p class="notice">${t('存在待核对交易，预算余量未知。查明原交易前不会开启下一笔充值。','A payment requires review, so the remaining budget is unknown. No further top-up may begin until it is resolved.')}</p>` : ''}` : emptyText(t('尚未建立当前预算窗口，不能据此判断本月已用金额为零。','No current budget window has been established. This does not mean monthly spending is zero.'))) +
        `<p class="account-context">${t('订阅费用与自动充值预算分别计算。暂停或撤销不会清除历史支出。','Subscription fees and auto top-up budgets are separate. Pausing or revoking authorization does not erase past spending.')}</p><button class="text-link" data-topup-refresh>${t('刷新状态','Refresh status')}</button>`;
      target.querySelector('[data-topup-refresh]').addEventListener('click',()=>renderAutomaticTopup(target,current,accountId));
    } catch (error) {
      if (!current() || !target.isConnected || target.topupRequest !== request) return;
      target.innerHTML = heading + `<p class="error">${requestError(error,t('授权与预算状态未知，请重试。','Authorization and budget status are unknown. Please retry.'))}</p><button class="button" data-topup-retry>${t('重试','Retry')}</button>`;
      target.querySelector('[data-topup-retry]').addEventListener('click',()=>renderAutomaticTopup(target,current,accountId));
    }
  }

  async function renderPaymentMethods(target, prefix, current, accountId, offset = 0, focusAfterRead = false) {
    const request = Symbol();
    target.paymentMethodsRequest = request;
    target.innerHTML = prefix + emptyText(t('正在读取支付方式状态…','Loading payment-method status…'));
    try {
      const data = await api(`/api/billing/payment-methods?offset=${offset}`);
      if (!current() || target.paymentMethodsRequest !== request) return;
      const states = {active:t('已记录','Recorded'),removing:t('移除处理中','Removal in progress'),removed:t('已移除','Removed')};
      const cancellation = {none:t('授权未撤销','Authorization not revoked'),processing:t('撤销处理中','Revocation in progress'),completed:t('撤销已确认','Revocation confirmed')};
      const topupStates = {authorized:t('授权已记录','Authorization recorded'),paused:t('已暂停','Paused'),superseded:t('已被新授权替代，未确认撤销','Superseded; revocation not confirmed'),revoking:t('撤销处理中','Revocation in progress'),revoked:t('撤销已确认','Revocation confirmed')};
      const channels = {alipay:t('支付宝','Alipay'),card:t('银行卡','Card'),wechat:t('微信支付','WeChat Pay'),google_pay:'Google Pay',apple_pay:'Apple Pay'};
      const icons = {alipay:'alipay',card:'credit-card',wechat:'wechat',apple_pay:'apple'};
      const validDate = value => value === null || (typeof value === 'string' && Number.isFinite(Date.parse(value)));
      if (data.account_id !== accountId || !Array.isArray(data.items) || !Number.isInteger(data.total) || data.total < 0 || typeof data.has_unbound_authorizations !== 'boolean' || typeof data.can_edit !== 'boolean' || typeof data.automatic_topup_supported !== 'boolean') throw new Error('INVALID_PAYMENT_METHODS');
      const ids = new Set();
      for (const item of data.items) {
        if (!item || typeof item.id !== 'string' || !/^[a-f0-9-]{36}$/i.test(item.id) || ids.has(item.id) || !Object.hasOwn(states,item.state) || typeof item.channel !== 'string' || !Array.isArray(item.subscriptions) || !Array.isArray(item.unresolved_payments) || !validDate(item.removal_requested_at) || !validDate(item.removed_at)) throw new Error('INVALID_PAYMENT_METHODS');
        ids.add(item.id);
        if (!Array.isArray(item.retired_authorizations) || item.retired_authorizations.some(change => !change ||
            typeof change.id !== 'string' || typeof change.subscription_id !== 'string' ||
            !['processing','completed'].includes(change.state))) throw new Error('INVALID_PAYMENT_METHODS');
        if (!Array.isArray(item.topup_authorizations)) throw new Error('INVALID_TOPUP_AUTHORIZATIONS');
        const topupIds = new Set(), versions = new Set();
        for (const authorization of item.topup_authorizations) {
          if (!authorization || typeof authorization.id !== 'string' || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(authorization.id) ||
              topupIds.has(authorization.id) || !Object.hasOwn(topupStates,authorization.state) ||
              !Number.isSafeInteger(authorization.version) || authorization.version < 1 || versions.has(authorization.version) ||
              authorization.created_at === null || !validDate(authorization.created_at) ||
              !validDate(authorization.revocation_requested_at) || !validDate(authorization.revoked_at) ||
              (['revoking','revoked'].includes(authorization.state) && authorization.revocation_requested_at === null) ||
              (authorization.state === 'revoked' ? authorization.revoked_at === null : authorization.revoked_at !== null)) throw new Error('INVALID_TOPUP_AUTHORIZATIONS');
          topupIds.add(authorization.id); versions.add(authorization.version);
        }
        if (item.subscriptions.some(sub => typeof sub.id !== 'string' || !Object.hasOwn(cancellation,sub.cancellation_state)) || item.unresolved_payments.some(payment => typeof payment.id !== 'string' || typeof payment.status !== 'string')) throw new Error('INVALID_PAYMENT_METHODS');
      }
      target.innerHTML = prefix + `<p class="account-context">${t('当前可查看状态，新增、更换与移除操作尚未开放。已记录支付方式不代表已允许扣款。','Status is available to view. Adding, replacing and removing methods are not yet available. A recorded method does not mean charges are enabled.')}</p>${data.has_unbound_authorizations ? `<p class="notice" role="status">${t('部分订阅授权尚未关联支付方式，当前列表不完整。请在订阅管理中核对，不能据此判断全部授权已解除。','Some subscription authorizations are not linked to a payment method. This list is incomplete; check subscription management before assuming all authorizations are revoked.')}</p>` : ''}${data.items.length ? data.items.map(item => `<article class="subscription-card"><header><h3>${item.channel === 'google_pay' ? '<img class="ui-icon" src="icons/google-color.png" alt="">' : icon(icons[item.channel] || 'credit-card')} ${escape(channels[item.channel] || item.channel)}</h3><span>${states[item.state]}</span></header><p class="account-context">${t('记录编号','Record ID')} · ${escape(item.id.slice(0,8))}</p>${item.state === 'removing' ? `<p>${t('已停止关联订阅的新续费扣款；授权撤销或在途交易尚待确认。已付服务不受影响。','New renewal charges for linked subscriptions have been stopped. Revocation or in-flight payments still need confirmation. Paid service is unaffected.')}</p>` : ''}${item.removal_requested_at ? `<p>${t('提交移除','Removal requested')} · ${dateLabel(item.removal_requested_at)}</p>` : ''}${item.removed_at ? `<p>${t('完成移除','Removal completed')} · ${dateLabel(item.removed_at)}</p>` : ''}<h4>${t('关联订阅','Linked subscriptions')}</h4>${item.subscriptions.length ? `<ul class="record-list">${item.subscriptions.map(sub => `<li><span>${escape(sub.id.slice(0,8))}</span><span>${cancellation[sub.cancellation_state]}</span></li>`).join('')}</ul>` : emptyText(t('没有已关联的订阅记录。','No linked subscriptions are recorded.'))}${item.unresolved_payments.length ? `<h4>${t('待核对交易','Payments requiring review')}</h4><ul class="record-list">${item.unresolved_payments.map(payment => `<li><span>${escape(payment.id.slice(0,8))}</span><span>${statusLabel(payment.status)}</span></li>`).join('')}</ul><p>${t('等待原交易查明，不会在这里重新扣款。','The original payment needs to be resolved. This page will not charge again.')}</p>` : ''}</article>`).join('') : emptyText(t('当前没有已关联的支付方式记录。','There are no linked payment-method records.'))}<div class="organization-pagination">${offset > 0 ? `<button data-method-offset="${Math.max(0,offset-50)}">${t('上一页','Previous')}</button>` : ''}${Number.isInteger(data.next_offset) && data.next_offset > offset ? `<button data-method-offset="${data.next_offset}">${t('下一页','Next')}</button>` : ''}<a class="text-link" href="#subscription">${t('查看关联订阅状态','View subscription status')}</a><button data-method-refresh>${t('刷新状态','Refresh status')}</button></div>${!data.automatic_topup_supported ? `<p class="checkout-note">${t('自动扣款尚未启用；这里只读取授权记录，不会扣款或更改授权。','Automatic charges are not enabled. This page only reads authorization records; it does not charge or change authorizations.')}</p>` : ''}`;
      target.querySelectorAll('.subscription-card').forEach((card,index) => {
        const topups = data.items[index].topup_authorizations;
        card.insertAdjacentHTML('beforeend', `<h4>${t('自动充值授权','Automatic top-up authorizations')}</h4><p class="account-context">${t('以下为此支付方式关联的独立授权记录，不代表已允许自动扣款。旧授权被替代不等于已撤销。','These independent authorizations are linked to this payment method; their presence does not enable automatic charges. A superseded authorization is not necessarily revoked.')}</p>${topups.length ? `<ul class="record-list">${topups.map(authorization=>`<li data-method-topup="${escape(authorization.id)}"><strong>${t('版本','Version')} ${authorization.version}</strong><span>${topupStates[authorization.state]}</span><small>${t('记录时间','Recorded')} · ${dateLabel(authorization.created_at)}</small>${authorization.revocation_requested_at ? `<small>${t('申请撤销','Revocation requested')} · ${dateLabel(authorization.revocation_requested_at)}</small>` : ''}${authorization.revoked_at ? `<small>${t('确认撤销','Revocation confirmed')} · ${dateLabel(authorization.revoked_at)}</small>` : ''}</li>`).join('')}</ul>` : emptyText(t('此支付方式没有已关联的自动充值授权记录。','No automatic top-up authorizations are linked to this payment method.'))}`);
        const retired = data.items[index].retired_authorizations;
        if (!retired.length) return;
        card.insertAdjacentHTML('beforeend', `<h4>${t('更换前的授权','Previous authorizations')}</h4><p class="account-context">${t('新支付方式生效，不代表旧授权已撤销。以下记录独立核对，不影响新授权及已付服务。','Activating a new payment method does not confirm revocation of the previous authorization. These records are checked separately without changing the new authorization or paid service.')}</p><ul class="record-list">${retired.map(change => `<li><span>${t('订阅','Subscription')} · ${escape(change.subscription_id.slice(0,8))}</span><span>${cancellation[change.state]}</span></li>`).join('')}</ul>`);
      });
      target.insertAdjacentHTML('afterbegin', `<h3 class="records-title" id="payment-methods-title" tabindex="-1">${t('支付方式记录','Payment-method records')}</h3>`);
      if (focusAfterRead) target.querySelector('#payment-methods-title').focus();
      for (const button of target.querySelectorAll('[data-method-offset]')) button.addEventListener('click', () => renderPaymentMethods(target,prefix,current,accountId,Number(button.dataset.methodOffset),true));
      target.querySelector('[data-method-refresh]').addEventListener('click', () => renderPaymentMethods(target,prefix,current,accountId,offset,true));
    } catch (error) {
      if (!current() || target.paymentMethodsRequest !== request) return;
      target.innerHTML = prefix + `<p class="error" id="payment-methods-error" tabindex="-1" role="status">${requestError(error,t('支付方式状态未知，读取失败。请重试。','Payment-method status is unknown because loading failed. Please retry.'))}</p><button class="button" data-method-retry>${t('重试','Retry')}</button>`;
      if (focusAfterRead) target.querySelector('#payment-methods-error').focus();
      target.querySelector('[data-method-retry]').addEventListener('click', () => renderPaymentMethods(target,prefix,current,accountId,offset,true));
    }
  }
  function validSubscription(item) {
    const date = value => value === null || (typeof value === 'string' && Number.isFinite(Date.parse(value)));
    if (item?.renewal != null && (!['created','pending','unknown','succeeded','failed','expired','cancelled','review','missing'].includes(item.renewal.status) || !item.renewal.manual_payment_deadline || !date(item.renewal.manual_payment_deadline) || !['manual_payment_eligible','new_subscription_payment_eligible','query_original_payment','reauthorization_required'].every(key => typeof item.renewal[key] === 'boolean'))) return false;
    return item && typeof item.id === 'string' && /^[0-9a-f-]{36}$/i.test(item.id) && typeof item.plan_code === 'string' && item.plan_code.length > 0 && ['monthly','annual'].includes(item.billing_cycle) && ['active','expired','scheduled'].includes(item.status) && ['none','processing','completed'].includes(item.cancellation_state) && typeof item.auto_renew === 'boolean' && typeof item.can_cancel === 'boolean' && ['period_start','period_end','next_credit_grant_at','prepaid_through'].every(key => date(item[key])) && item.capabilities && typeof item.capabilities.cancel === 'boolean' && !(item.cancellation_state !== 'none' && item.auto_renew);
  }
  async function renderSubscriptions(target, prefix, current) {
    let items = [], pending = null, confirming = null, uncertain = null, readVersion = 0;
    const renewText = item => item.cancellation_state === 'processing' ? t('取消处理中','Cancellation processing') : item.cancellation_state === 'completed' ? t('自动续费已取消','Automatic renewal cancelled') : item.auto_renew ? t('自动续费已开启','Automatic renewal on') : t('自动续费未开启','Automatic renewal off');
    const serviceText = item => ({active:t('生效中','Active'),expired:t('已到期','Expired'),scheduled:t('待生效','Scheduled')})[item.status];
    const planName = code => ({pro:'Pro',pro_plus:'Pro+',ultra:'Ultra',teams:'Teams',enterprise:'Enterprise'})[code] || code;
    const dateText = value => value === null ? t('未提供','Not available') : dateLabel(value);
    const renewalNotice = item => {
      const renewal = item.renewal;
      if (!renewal) return '';
      let title = '', detail = '';
      if (renewal.status === 'missing') {
        title = t('续费状态待核查','Renewal status needs review');
        detail = t('尚无可确认的续费记录。再次付款前需核实支付授权与渠道状态。','No confirmed renewal record is available. Payment authorization and channel status must be checked before another payment.');
      } else if (renewal.query_original_payment) {
        title = t('续费结果待核实','Renewal payment under review');
        detail = t('请勿重复付款。刷新状态只查询当前记录，不会再次扣款。','Do not pay again. Refreshing only reads the current record and will not charge you again.');
      } else if (renewal.status === 'succeeded') {
        title = t('续费款项已确认','Renewal payment confirmed');
        detail = t('正在处理服务期与权益，请勿重复付款。','Your service term and benefits are being processed. Do not pay again.');
      } else if (renewal.reauthorization_required) {
        title = t('主动补缴窗口已结束','Manual renewal window ended');
        detail = t('再次订阅需要重新确认自动续费授权。未到期的加购 Sage Credits 不受影响。','A new subscription requires a new automatic-renewal authorization. Unexpired purchased Sage Credits remain available.');
      } else if (['failed','expired'].includes(renewal.status)) {
        title = t('本次续费未成功','This renewal was unsuccessful');
        detail = item.status === 'active' ? t('当前已付服务仍有效，不会自动重扣。到期后不延长免费权益。','Your paid service remains active. No automatic retry will be made, and service will not be extended for free.') : t('订阅权益已到期，不会自动重扣。未到期的加购 Sage Credits 仍可使用。','Subscription benefits have expired. There will be no automatic retry. Unexpired purchased Sage Credits remain available.');
        if (renewal.manual_payment_eligible) detail += t(` 主动补缴窗口截止 ${dateLabel(renewal.manual_payment_deadline)}；当前支付渠道尚未开放。`,` The manual renewal window ends ${dateLabel(renewal.manual_payment_deadline)}; payment is not available yet.`);
      }
      return title ? `<br><strong class="renewal-payment-title">${escape(title)}</strong><br>${escape(detail)}` : '';
    };
    const paidTerm = item => {
      const end = item.prepaid_through || item.period_end;
      if (item.status === 'expired') return t('已结束的服务期不会因取消操作延长。','Cancellation does not extend an ended service term.');
      return end ? t(`已付服务保留至 ${dateLabel(end)}。`,`Paid service remains available through ${dateLabel(end)}.`) : t('已付服务仍按原约定期限保留，当前未提供到期时间。','Paid service keeps its agreed term; its end date is not available here.');
    };
    function paint(focusId) {
      if (!current()) return;
      target.innerHTML = prefix + `<div class="subscription-toolbar"><p>${t('服务期与续费状态分别展示，不因取消提前收回已付服务。','Service access and renewal are shown separately. Cancelling renewal does not end paid service early.')}</p><button type="button" class="button" id="subscriptions-refresh" ${pending ? 'disabled' : ''}>${t('刷新订阅状态','Refresh subscriptions')}</button></div>` + (items.length ? `<div class="subscription-list">${items.map(item => {
        const id = escape(item.id), canCancel = item.can_cancel && item.capabilities.cancel && item.cancellation_state === 'none' && uncertain !== item.id;
        const message = (uncertain === item.id ? t('取消结果尚未确认。请刷新订阅状态后再决定下一步；系统不会自动重提取消申请。','The cancellation result is unconfirmed. Refresh the subscription before taking further action; the request will not be resubmitted automatically.') : item.cancellation_state === 'processing' ? t('Sage 已停止发起新的续费扣款，支付渠道解约仍在处理中。已付服务保留，在途交易需另行核实。','Sage has stopped initiating new renewal charges. Cancellation with the payment channel is still processing. Paid service remains; in-flight transactions require separate verification.') : item.cancellation_state === 'completed' ? t('支付渠道解约已确认，不再自动续费。取消本身不产生退款。','Cancellation with the payment channel is confirmed. Automatic renewal has stopped. Cancellation itself does not issue a refund.') : paidTerm(item)) + renewalNotice(item);
        return `<article class="subscription-card" data-subscription="${id}"><div class="subscription-card-head"><div><h3>Sage ${escape(planName(item.plan_code))}</h3><p>${item.billing_cycle === 'monthly' ? t('按月计费','Billed monthly') : t('按年计费','Billed annually')}</p></div><span class="subscription-service">${serviceText(item)}</span></div><dl class="subscription-dates"><div><dt>${t('当前服务期','Current service term')}</dt><dd>${dateText(item.period_start)} — ${dateText(item.period_end)}</dd></div><div><dt>${t('下一次 Credits 发放','Next Credits grant')}</dt><dd>${item.next_credit_grant_at === null ? t('未安排','Not scheduled') : dateText(item.next_credit_grant_at)}</dd></div>${item.prepaid_through ? `<div><dt>${t('已付款服务截止','Paid through')}</dt><dd>${dateText(item.prepaid_through)}</dd></div>` : ''}</dl><div class="subscription-renewal"><strong>${uncertain === item.id ? t('取消结果待确认','Cancellation unconfirmed') : renewText(item)}</strong><p id="subscription-status-${id}" class="${uncertain === item.id ? 'error' : ''}" role="status" tabindex="-1">${message}</p></div>${canCancel ? `<button type="button" class="button subscription-cancel" data-cancel-open="${id}" aria-expanded="${confirming === item.id}" aria-controls="cancel-confirm-${id}" ${pending ? 'disabled' : ''}>${t('取消自动续费','Cancel automatic renewal')}</button><section class="subscription-confirm" id="cancel-confirm-${id}" aria-labelledby="cancel-title-${id}" ${confirming === item.id ? '' : 'hidden'}><h4 id="cancel-title-${id}" tabindex="-1">${t('确认取消自动续费？','Cancel automatic renewal?')}</h4><p>${paidTerm(item)} ${t('不会自动退款，也不会取消已付款的后续服务期。提交后先阻断新的续费扣款；支付渠道确认前仍显示处理中。','No refund will be issued automatically, and prepaid future service terms will not be cancelled. Submission first blocks new renewal charges; the status remains processing until the payment channel confirms.')}</p><div class="action-row"><button type="button" class="button primary" data-cancel-confirm="${id}" ${pending ? 'disabled' : ''}>${pending === item.id ? t('正在提交…','Submitting…') : t('确认取消自动续费','Confirm cancellation')}</button><button type="button" class="button" data-cancel-back="${id}" ${pending ? 'disabled' : ''}>${t('暂不取消','Keep automatic renewal')}</button></div></section>` : ''}</article>`;
      }).join('')}</div>` : emptyText(t('当前账户尚无订阅。','This account has no subscriptions.')));
      target.querySelector('#subscriptions-refresh').onclick = () => load(true);
      for (const item of items) {
        if (!['pro','pro_plus','ultra'].includes(item.plan_code)) continue;
        const lower = ({pro:[],pro_plus:['Pro'],ultra:['Pro','Pro+']})[item.plan_code];
        if (!lower.length && item.billing_cycle !== 'annual') continue;
        const guide = document.createElement('section');
        guide.className = 'policy-block subscription-change-guide';
        const heading = document.createElement('h4'), detail = document.createElement('p');
        heading.textContent = t('改用其他方案', 'Changing your plan');
        const choices = [lower.length ? t(`如需降至 ${lower.join(' / ')}，`,`To downgrade to ${lower.join(' / ')}, `) : '', item.billing_cycle === 'annual' ? t('如需改为连续包月，','To switch to monthly billing, ') : ''].filter(Boolean).join(t('或','or '));
        const next = uncertain === item.id ? t('先刷新确认取消结果。','first refresh to confirm the cancellation result. ') : item.cancellation_state === 'processing' ? t('当前取消仍在处理中，请等待渠道确认；不要重复提交。','cancellation is processing. Wait for channel confirmation; do not resubmit. ') : item.auto_renew ? t('先通过上方入口取消自动续费。','first cancel automatic renewal using the control above. ') : t('当前自动续费已关闭，无需重复取消。','automatic renewal is already off; no further cancellation is needed. ');
        const instruction = item.auto_renew && (!item.can_cancel || !item.capabilities.cancel) ? t('需先取消自动续费，但当前取消入口不可用，请联系支持核实；不能认为续费已停止。','automatic renewal must first be cancelled, but cancellation is currently unavailable. Contact support to verify; renewal has not been confirmed stopped. ') : next;
        detail.textContent = choices + instruction + ' ' + paidTerm(item) + ' ' + t('已付服务全部结束后，再主动购买目标方案。不会预约自动降档或中途退差价；年转月不直接中途切换。当前新购尚未开放。','After all paid service ends, purchase your preferred plan yourself. No downgrade is scheduled and no mid-term price difference is refunded; annual plans do not switch to monthly mid-term. New purchases are not available yet.');
        guide.append(heading,detail);
        target.querySelector(`[data-subscription="${item.id}"]`).append(guide);
      }
      for (const button of target.querySelectorAll('[data-cancel-open]')) button.onclick = () => { if (!current() || pending) return; confirming = button.dataset.cancelOpen; paint(`cancel-title-${confirming}`); };
      for (const button of target.querySelectorAll('[data-cancel-back]')) button.onclick = () => { const id = button.dataset.cancelBack; confirming = null; paint(); target.querySelector(`[data-cancel-open="${id}"]`)?.focus(); };
      for (const button of target.querySelectorAll('[data-cancel-confirm]')) button.onclick = () => cancel(button.dataset.cancelConfirm);
      if (focusId) document.getElementById(focusId)?.focus();
    }
    async function load(focus = false) {
      if (!current() || pending) return;
      const version = ++readVersion;
      target.innerHTML = prefix + `<p class="notice">${t('正在查询订阅状态…','Loading subscription status…')}</p>`;
      try {
        const result = await api('/api/billing/subscriptions');
        if (!current() || version !== readVersion) return;
        if (!Array.isArray(result.items) || !result.items.every(validSubscription) || new Set(result.items.map(item => item.id)).size !== result.items.length) throw new Error('INVALID_SUBSCRIPTIONS');
        items = result.items; uncertain = null; confirming = null; paint();
        if (focus) target.querySelector('#subscriptions-refresh')?.focus();
      } catch (error) {
        if (!current() || version !== readVersion) return;
        target.innerHTML = prefix + `<p class="notice error" role="status">${requestError(error, t('订阅状态暂时无法确认，不能据此判断没有订阅或取消成功。请重新查询。','Subscription status could not be confirmed. This does not mean there is no subscription or that cancellation succeeded. Refresh to check.'))}</p><button type="button" class="button" id="subscriptions-refresh">${t('刷新订阅状态','Refresh subscriptions')}</button>`;
        target.querySelector('#subscriptions-refresh').onclick = () => load(true);
        if (focus) target.querySelector('#subscriptions-refresh').focus();
      }
    }
    async function cancel(id) {
      if (!current() || pending || confirming !== id) return;
      const item = items.find(value => value.id === id);
      if (!item?.can_cancel || !item.capabilities.cancel || item.cancellation_state !== 'none' || uncertain === id) return;
      pending = id; paint(`cancel-title-${id}`);
      try {
        const result = await api(`/api/billing/subscriptions/${encodeURIComponent(id)}/cancel`, {});
        if (!current()) return;
        if (!validSubscription(result.subscription) || result.subscription.id !== id || !['processing','completed'].includes(result.subscription.cancellation_state)) throw new Error('INVALID_CANCELLATION');
        items = items.map(value => value.id === id ? result.subscription : value); confirming = null;
      } catch (error) {
        if (!current()) return;
        // Even a timeout may follow an accepted request. Do not re-POST it.
        uncertain = id; confirming = null;
      } finally { if (current()) { pending = null; paint(`subscription-status-${id}`); } }
    }
    await load();
  }
  let orderRequest = 0;
  async function loadOrder(id, expectedAccount, version) {
    const target = document.querySelector('#order-detail');
    if (!target || !/^[0-9a-f-]{36}$/i.test(id)) return;
    const sequence = ++orderRequest;
    target.innerHTML = `<p class="notice">${t('正在读取订单…','Loading order…')}</p>`; target.focus();
    try {
      const data = await api(`/api/billing/orders/${encodeURIComponent(id)}`);
      if (sequence !== orderRequest || version !== accountVersion || session?.active_account_id !== expectedAccount || !target.isConnected) return;
      if (data.order?.id !== id || data.order.currency !== 'CNY' || !Number.isSafeInteger(data.order.amount_minor)) throw new Error('INVALID_ORDER');
      target.innerHTML = `<h3>${t('订单详情','Order details')}</h3><dl class="order-detail-list"><dt>${t('订单号','Order ID')}</dt><dd>${escape(id)}</dd><dt>${t('金额','Amount')}</dt><dd>${money(data.order.amount_minor, true)}</dd><dt>${t('订单状态','Order status')}</dt><dd>${statusLabel(data.order.status)}</dd><dt>${t('权益交付','Benefit delivery')}</dt><dd>${statusLabel(data.delivery?.status)}</dd><dt>${t('创建时间','Created')}</dt><dd>${dateLabel(data.order.created_at)}</dd></dl>`;
      const reviewNotes = {
        duplicate_payment_review:t('此订单存在额外收款，正在核对。已确认的付款与已交付权益保留；请勿再次付款。退款尚未确认。','An additional payment for this order requires verification. Your confirmed payment and delivered benefits remain valid. Do not pay again; no refund has been confirmed.'),
        identity_mismatch_review:t('收款归属需要核对，请勿再次付款。','Payment attribution requires verification. Do not pay again.'),
        amount_mismatch_review:t('收款金额或币种需要核对，请勿再次付款。','The payment amount or currency requires verification. Do not pay again.'),
        late_payment_review:t('订单关闭后收到的付款需要核对，请勿再次付款。','A payment received after the order closed requires verification. Do not pay again.'),
        payment_review_required:t('此订单存在需要核对的收款记录，请勿再次付款。','This order has a payment record requiring verification. Do not pay again.'),
        payment_reconciliation_required:t('交易结果存在待核实事项，请勿再次付款。','The transaction result requires reconciliation. Do not pay again.')
      };
      if (!Array.isArray(data.payments)) throw new Error('INVALID_PAYMENT_HISTORY');
      // Older responses still carry receipt dispositions; do not hide an extra receipt.
      const reasons = data.review_reasons ?? [...new Set(data.payments.filter(item => item.status !== 'accepted').map(item => Object.hasOwn(reviewNotes,item.status) ? item.status : 'payment_review_required'))];
      const needsReview = data.needs_review ?? reasons.length > 0;
      if (typeof needsReview !== 'boolean' || !Array.isArray(reasons) || reasons.some(reason => !Object.hasOwn(reviewNotes,reason)) || needsReview !== (reasons.length > 0)) throw new Error('INVALID_PAYMENT_REVIEW');
      if (needsReview) {
        target.insertAdjacentHTML('beforeend',`<section class="policy-block" id="order-payment-review" aria-labelledby="payment-review-heading"><h3 id="payment-review-heading">${t('收款待核查','Payment review required')}</h3>${reasons.map(reason => `<p role="status">${reviewNotes[reason]}</p>`).join('')}<button type="button" class="button" id="order-review-refresh">${t('刷新核查状态','Refresh review status')}</button></section>`);
        target.querySelector('#order-review-refresh').onclick = () => loadOrder(id,expectedAccount,version);
      }
      if (data.payment_attempt) {
        const state = data.payment_attempt.status;
        const labels = {created:t('待发起','Not submitted'),pending:t('支付处理中','Payment processing'),unknown:t('支付结果待确认','Payment unconfirmed'),succeeded:t('支付已确认','Payment confirmed'),failed:t('支付失败','Payment failed'),cancelled:t('支付已取消','Payment cancelled'),expired:t('支付已关闭','Payment closed'),review:t('交易待核实','Transaction under review')};
        if (!Object.hasOwn(labels,state) || typeof data.payment_attempt.needs_reconciliation !== 'boolean') throw new Error('INVALID_PAYMENT_ATTEMPT');
        const note = data.payment_attempt.needs_reconciliation || state === 'review' ? t('交易记录存在待核实事项，请勿再次付款。核实结果会更新在此订单中。','This transaction requires verification. Do not pay again. Its status will be updated in this order.') : ['pending','unknown'].includes(state) ? t('正在确认原交易结果，请勿重复付款。刷新只查询状态，不会再次发起扣款。','The original transaction is being checked. Do not pay again. Refresh only checks its status and never starts another charge.') : state === 'succeeded' && data.delivery?.status !== 'completed' ? t('付款已确认，权益仍在处理中。请勿重新购买。','Payment is confirmed; benefits are still processing. Do not purchase again.') : ['failed','cancelled','expired'].includes(state) ? t('此次支付已结束，系统不会自动重试扣款。','This payment has ended. No automatic charging retry will be made.') : '';
        target.insertAdjacentHTML('beforeend',`<section class="policy-block" aria-labelledby="payment-attempt-heading"><h3 id="payment-attempt-heading">${labels[state]}</h3><p role="status">${note}</p><button class="button" type="button" id="order-status-refresh">${t('刷新订单状态','Refresh order status')}</button></section>`);
        target.querySelector('#order-status-refresh').onclick = () => loadOrder(id,expectedAccount,version);
      }
      if (!Array.isArray(data.refunds)) throw new Error('INVALID_REFUND_HISTORY');
      if (data.refunds.length) {
        const states = {reserved:t('处理中 · 金额已保留','Processing · amount reserved'),refunded:t('退款已确认','Refund confirmed'),released:t('已终止 · 未退款','Terminated · not refunded')};
        const notes = {
          reserved:t('对应额度已冻结。此状态不表示款项已退回；结果确认前，请勿重复申请。','The corresponding credits are frozen. This does not mean money has been returned. Do not submit a duplicate request while the result is being confirmed.'),
          refunded:t('退款成功已确认，对应冻结额度已核销。实际入账时间以支付渠道为准。','Refund success has been confirmed and the frozen credits have been deducted. Posting time depends on the payment channel.'),
          released:t('已确认未退款并终止处理。冻结已解除，额度仍按原有效期计算，不延长有效期。','No refund was made and processing has ended. The freeze has been released; the original credit expiry date still applies.')
        };
        for (const item of data.refunds) if (typeof item.id !== 'string' || !Object.hasOwn(states,item.state) || item.currency !== 'CNY' || typeof item.amount_minor !== 'string' || !/^\d+$/.test(item.amount_minor)) throw new Error('INVALID_REFUND_HISTORY');
        target.insertAdjacentHTML('beforeend',`<section class="policy-block" id="refund-history"><h3>${t('退款处理记录','Refund history')}</h3><ul class="record-list">${data.refunds.map(item=>`<li><strong>${states[item.state]}</strong><span>${money(item.amount_minor,true)}</span><small>${t('处理编号','Reference')} ${escape(item.id)}</small><small>${t('创建时间','Created')} ${dateLabel(item.created_at)}</small>${item.resolved_at ? `<small>${t('结果确认时间','Result confirmed')} ${dateLabel(item.resolved_at)}</small>` : ''}<p>${notes[item.state]}</p></li>`).join('')}</ul><button class="button" type="button" data-refund-history-refresh>${t('刷新处理状态','Refresh refund status')}</button></section>`);
        target.querySelector('[data-refund-history-refresh]').onclick = () => loadOrder(id,expectedAccount,version);
      }
      const assessment = document.createElement('section');
      assessment.className = 'policy-block';
      assessment.id = 'refund-assessment';
      target.append(assessment);
      const current = () => sequence === orderRequest && version === accountVersion && session?.active_account_id === expectedAccount && assessment.isConnected;
      await renderRefundAssessment(assessment,id,expectedAccount,current);
    } catch (_) {
      if (sequence === orderRequest && version === accountVersion && target.isConnected) target.innerHTML = `<p class="error">${t('订单详情读取失败，支付与交付状态未知。请再次点击查看详情。','Order details could not be loaded. Payment and delivery status are unknown. Choose View details again to retry.')}</p>`;
    }
  }
  async function renderRefundAssessment(target,orderId,accountId,isCurrent) {
    const request = Symbol();
    target.refundRequest = request;
    const current = () => isCurrent() && target.refundRequest === request;
    const heading = `<h3>${t('退款范围核对','Refund eligibility review')}</h3>`;
    target.innerHTML = heading + `<p role="status">${t('正在核对原订单…','Checking the original order…')}</p>`;
    const reasons = {
      unsupported_order:t('此订单暂不支持在线退款评估。订阅及特殊订单需按相应规则另行核对。','Online refund assessment is not available for this order. Subscriptions and special orders require a separate review.'),
      payment_not_fulfilled:t('订单尚未完成支付与权益交付，暂不能评估退款。','Payment and benefit delivery must be completed before refund eligibility can be assessed.'),
      payment_review_required:t('原交易存在待核实事项，需先完成收款核对。','The original transaction requires verification before a refund can be assessed.'),
      ledger_review_required:t('额度批次需要核对，目前无法确认可退范围。','The credit batch requires review. Its refundable scope cannot currently be confirmed.'),
      credits_expired:t('该订单购买的 Sage Credits 已过期，不适用未用额度退款规则。','The Sage Credits purchased in this order have expired and are not eligible under the unused-credit refund policy.'),
      task_settlement_required:t('部分额度被进行中的任务预占，请待任务结算后重新核对。','Some credits are reserved by active tasks. Check again after those tasks have settled.'),
      refund_review_required:t('该批次已有退款冻结或核销记录，需要核对原退款处理状态。','This batch already has frozen or refunded credits. Its existing refund status requires review.'),
      no_unused_credits:t('该订单没有未用付费额度。','This order has no unused paid credits.'),
      annual_source_review_required:t('年度订单的原始服务期或费用来源需要核对，目前无法确认未来服务月范围。','The annual order’s original service term or payment sources require review. Its future service-month scope cannot currently be confirmed.'),
      annual_service_not_started:t('此年度服务期尚未开始，需按未开始服务的退款规则另行核对。','This annual service term has not started. A separate review is required under the rules for service that has not begun.'),
      annual_service_ended:t('此年度服务期已结束，未来完整服务月范围不再适用。','This annual service term has ended, so the future full service-month scope no longer applies.'),
      annual_refund_policy_review_required:t('以下仅核对原单未来完整服务月，不是已批准退款。退款金额与提交功能尚未开放；已开票、合同及异常订单仍需财务审核。','This reviews future full service months from the original order; it is not an approved refund. Refund amounts and submission are not yet available. Invoiced, contract and exceptional orders still require finance review.'),
      annual_no_future_months:t('本服务期没有未来完整服务月；当前服务月保留。本页未批准或发起退款。','This term has no future full service months; the current service month is retained. No refund has been approved or initiated.'),
      policy_review_required:t('这是按原订单计算的参考值，不是已批准退款。提交退款功能尚未开放；已开票、合同及异常订单还需财务审核。','This is a calculation from the original order, not an approved refund. Refund submission is not yet available. Invoiced, contract and exceptional orders also require finance review.'),
      rounding_policy_pending:t('未用额度已核对，退款金额仍待确认，暂不显示舍入后的金额。退款申请尚未开放。','Unused credits have been checked, but the refund amount is not yet confirmed. No rounded amount is shown. Refund requests are not yet available.')
    };
    try {
      const data = await api(`/api/billing/orders/${encodeURIComponent(orderId)}/refund-assessment`);
      if (!current()) return;
      if (data.order_id !== orderId || data.account_id !== accountId || data.can_submit !== false || data.currency !== 'CNY' || !Object.hasOwn(reasons,data.reason) || !['unavailable','assessment_ready','scope_ready'].includes(data.status)) throw new Error('INVALID_REFUND_ASSESSMENT');
      let metrics = '';
      if (data.status === 'assessment_ready') {
        if (data.scope !== 'all_unused_paid_credits' || !['policy_review_required','rounding_policy_pending'].includes(data.reason) || typeof data.unused_quantity !== 'string' || !/^\d+(\.\d+)?$/.test(data.unused_quantity) || typeof data.paid_minor !== 'string' || !/^\d+$/.test(data.paid_minor) || (data.reference_minor !== null && (typeof data.reference_minor !== 'string' || !/^\d+$/.test(data.reference_minor) || BigInt(data.reference_minor) > BigInt(data.paid_minor)))) throw new Error('INVALID_REFUND_AMOUNT');
        metrics = `<dl class="order-detail-list"><dt>${t('原单实付','Original payment')}</dt><dd>${money(data.paid_minor,true)}</dd><dt>${t('全部未用付费额度','Entire unused paid balance')}</dt><dd>${escape(data.unused_quantity)} Sage Credits</dd><dt>${t('退款参考金额','Reference refund amount')}</dt><dd>${data.reference_minor === null ? t('待确认','Not yet confirmed') : money(data.reference_minor,true)}</dd></dl>`;
      }
      if (data.status === 'scope_ready') {
        if (data.scope !== 'annual_future_service_months' || !Number.isInteger(data.future_month_count) || data.future_month_count < 0 || data.future_month_count > 11 || !Array.isArray(data.future_service_months) || data.future_service_months.length !== data.future_month_count || data.reason !== (data.future_month_count ? 'annual_refund_policy_review_required' : 'annual_no_future_months')) throw new Error('INVALID_ANNUAL_SCOPE');
        // Compare the API's exact microseconds, not Date.parse's truncated
        // milliseconds. Calendar overflow and missing offsets are invalid.
        const instant = value => {
          const parts = typeof value === 'string' && value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/);
          if (!parts) throw new Error('INVALID_ANNUAL_DATE');
          const [year,month,day,hour,minute,second] = parts.slice(1,7).map(Number);
          const calendar = new Date(0); calendar.setUTCFullYear(year,month-1,day); calendar.setUTCHours(hour,minute,second,0);
          const zone = parts[8], offsetHours = zone === 'Z' ? 0 : Number(zone.slice(1,3)), offsetMinutes = zone === 'Z' ? 0 : Number(zone.slice(4,6));
          if (year < 1 || calendar.getUTCFullYear() !== year || calendar.getUTCMonth() !== month-1 || calendar.getUTCDate() !== day || calendar.getUTCHours() !== hour || calendar.getUTCMinutes() !== minute || calendar.getUTCSeconds() !== second || offsetHours > 23 || offsetMinutes > 59 || !Number.isFinite(Date.parse(value))) throw new Error('INVALID_ANNUAL_DATE');
          const offset = BigInt((offsetHours*60+offsetMinutes)*(zone.startsWith('-')?-1:1));
          return BigInt(calendar.getTime())*1000n + BigInt((parts[7] || '').padEnd(6,'0')) - offset*60000000n;
        };
        const period = value => {
          if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('INVALID_ANNUAL_PERIOD');
          const start = instant(value.starts_at), end = instant(value.ends_at);
          if (start >= end) throw new Error('INVALID_ANNUAL_PERIOD');
          return {start,end};
        };
        const service = period(data.service_period), retained = period(data.current_service_month);
        if (retained.start < service.start || retained.end > service.end) throw new Error('INVALID_ANNUAL_PERIOD');
        let next = retained.end;
        for (const value of data.future_service_months) {
          const future = period(value);
          if (future.start !== next || future.end > service.end) throw new Error('INVALID_ANNUAL_PERIOD');
          next = future.end;
        }
        if (next !== service.end) throw new Error('INVALID_ANNUAL_PERIOD');
        const format = value => new Intl.DateTimeFormat(en ? 'en-GB' : 'zh-CN', {dateStyle:'medium',timeStyle:'medium',timeZone:'Asia/Shanghai'}).format(new Date(value)) + (value.match(/\.\d+/)?.[0] || '');
        const range = value => `${escape(format(value.starts_at))} → ${escape(format(value.ends_at))}`;
        metrics = `<dl class="order-detail-list"><dt>${t('原年度服务期','Original annual service term')}</dt><dd>${range(data.service_period)}</dd><dt>${t('当前服务月','Current service month')}</dt><dd>${range(data.current_service_month)}</dd><dt>${t('未来完整服务月','Future full service months')}</dt><dd data-annual-month-count>${data.future_month_count}</dd>${data.future_service_months.map((value,index)=>`<dt>${t(`未来服务月 ${index+1}`,`Future service month ${index+1}`)}</dt><dd data-annual-future-month>${range(value)}</dd>`).join('')}</dl><p>${t('当前服务月保留，不计入退款范围。时间统一为北京时间（UTC+08:00），含起点、不含终点。仅展示范围，不表示已解约、冻结或停止服务。','The current service month is retained and excluded from the refund scope. All times use Beijing time (UTC+08:00), including the start and excluding the end. This shows scope only; no cancellation, freeze or service termination is implied.')}</p>`;
      } else if (['annual_refund_policy_review_required','annual_no_future_months'].includes(data.reason)) throw new Error('INVALID_ANNUAL_SCOPE');
      target.innerHTML = heading + metrics + `<p role="status">${reasons[data.reason]}</p><button type="button" class="button" data-refund-refresh>${t('重新核对','Check again')}</button>`;
      target.querySelector('[data-refund-refresh]').onclick = () => renderRefundAssessment(target,orderId,accountId,isCurrent);
    } catch (_) {
      if (!current()) return;
      target.innerHTML = heading + `<p class="error" role="status">${t('退款范围读取失败，目前状态未知，不代表不可退款。请重试。','Refund eligibility could not be loaded. Its status is unknown, not ineligible. Please retry.')}</p><button type="button" class="button" data-refund-refresh>${t('重试','Retry')}</button>`;
      target.querySelector('[data-refund-refresh]').onclick = () => renderRefundAssessment(target,orderId,accountId,isCurrent);
    }
  }
  async function renderProcurement(target, prefix, accountData, current) {
    const permitted = accountData.role === 'owner' || (accountData.kind === 'business' && accountData.role === 'billing_admin');
    if (session?.capabilities?.procurement !== true || !permitted) {
      target.innerHTML = prefix + emptyText(!permitted ? t('仅企业管理员可提交与查看采购申请，请联系你的管理员。','Only business admins can submit or view purchasing inquiries. Contact your admin.') : t('采购申请接收服务尚未接入，不能提交申请或上传汇款资料。','Procurement requests are not connected. Inquiries and transfer documents cannot be submitted.'));
      return;
    }
    target.innerHTML = prefix + `<form class="procurement-form" id="procurement-form"><h3>${t('提交企业采购需求','Submit a purchasing inquiry')}</h3><label for="purchase-company">${t('企业名称','Company name')}</label><input id="purchase-company" name="company" required maxlength="160" autocomplete="organization"><label for="purchase-seats">${t('预计真人席位','Expected human seats')}</label><input id="purchase-seats" name="seats" type="number" min="2" max="10000" step="1" value="2" required><label for="purchase-email">${t('联系邮箱','Contact email')}</label><input id="purchase-email" name="contact_email" type="email" required maxlength="254" autocomplete="email"><label for="purchase-requirements">${t('业务需求','Requirements')}</label><textarea id="purchase-requirements" name="requirements" required maxlength="2000" rows="4" placeholder="${t('请说明场景、使用规模与采购需求；不要填写密码、密钥或银行资料。','Describe your use cases, scale and purchasing needs. Do not include passwords, keys or bank details.')}"></textarea><p class="checkout-note">${t('仅提交采购需求，不会创建付款订单、开通订阅或扣款。','This sends an inquiry only. It does not create a payable order, subscription or charge.')}</p><p id="procurement-feedback" class="checkout-note" role="status"></p><button type="submit" class="button primary">${t('提交采购申请','Submit inquiry')}</button></form><h3 class="records-title">${t('已提交的申请','Submitted inquiries')}</h3><div id="procurement-records"></div>`;
    const form = target.querySelector('#procurement-form'), records = target.querySelector('#procurement-records'), feedback = target.querySelector('#procurement-feedback');
    let recordsOffset = 0, recordsRequest = 0;
    async function loadInquiries(offset = recordsOffset) {
      const request = ++recordsRequest;
      records.innerHTML = emptyText(t('正在读取申请…','Loading inquiries…'));
      try {
        const data = await api(`/api/billing/purchase-inquiries?offset=${offset}`);
        if (!current() || !records.isConnected || recordsRequest !== request) return;
        if (data.account_id !== accountData.id || data.offset !== offset || !Number.isSafeInteger(data.total) || data.total < 0 ||
            (data.next_offset !== null && data.next_offset !== offset+50) || !Array.isArray(data.items) || data.items.length > 50 ||
            data.items.some(item => !item || !/^[0-9a-f-]{36}$/i.test(item.id) || typeof item.company !== 'string' || typeof item.requirements !== 'string' || !Number.isSafeInteger(item.seats) || item.seats < 2 || typeof item.status !== 'string' || !Number.isFinite(Date.parse(item.created_at)))) throw new Error('INVALID_INQUIRIES');
        recordsOffset = offset;
        records.innerHTML = data.items.length ? `<ul class="record-list">${data.items.map(item => `<li><strong>${escape(item.company)}</strong><small style="overflow-wrap:anywhere">${t('申请编号','Request ID')} ${escape(item.id)}</small><span>${escape(item.seats)} ${t('席位','seats')}</span><span>${statusLabel(item.status)}</span><small>${dateLabel(item.created_at)}</small><details><summary>${t('查看已提交需求','View submitted requirements')}</summary><p style="white-space:pre-wrap;overflow-wrap:anywhere">${escape(item.requirements)}</p></details></li>`).join('')}</ul>` : emptyText(offset ? t('此页没有申请，请返回上一页。','No inquiries on this page. Go to the previous page.') : t('当前账户还没有采购申请。','This account has no purchasing inquiries.'));
        records.insertAdjacentHTML('beforeend', `<div class="account-actions">${offset ? `<button type="button" class="button" data-inquiries-prev>${t('上一页','Previous')}</button>` : ''}${data.next_offset !== null ? `<button type="button" class="button" data-inquiries-next>${t('下一页','Next')}</button>` : ''}</div>`);
        records.querySelector('[data-inquiries-prev]')?.addEventListener('click', () => loadInquiries(Math.max(0,offset-50)));
        records.querySelector('[data-inquiries-next]')?.addEventListener('click', () => loadInquiries(data.next_offset));
      } catch (_) {
        if (current() && records.isConnected && recordsRequest === request) { records.innerHTML = `<p class="error">${t('申请记录读取失败，记录状态未知。','Inquiries could not be loaded. Their status is unknown.')}</p><button type="button" class="button" id="inquiries-retry">${t('重新读取','Retry')}</button>`; records.querySelector('button').onclick = () => loadInquiries(offset); }
      }
    }
    const refresh = document.createElement('button'); refresh.type = 'button'; refresh.className = 'button'; refresh.textContent = t('刷新申请记录','Refresh inquiries'); refresh.onclick = () => loadInquiries(); records.before(refresh);
    let submitting = false;
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (submitting || !current() || !form.reportValidity()) return;
      const fields = new FormData(form), quantity = Number(fields.get('seats'));
      if (!Number.isSafeInteger(quantity) || quantity < 2 || quantity > 10000) return;
      const payload = {company:fields.get('company').trim(),seats:quantity,contact_email:fields.get('contact_email').trim(),requirements:fields.get('requirements').trim()};
      if (!payload.company || !payload.requirements || [...payload.company].length > 160 || [...payload.requirements].length > 2000) { feedback.textContent = t('企业名称请填写 1–160 字，业务需求请填写 1–2000 字。','Enter 1–160 characters for the company and 1–2000 for requirements.'); return; }
      submitting = true; for (const control of form.elements) control.disabled = true;
      feedback.classList.remove('error'); feedback.textContent = t('正在提交采购申请…','Submitting your inquiry…');
      try {
        const result = await api('/api/billing/purchase-inquiries', payload);
        if (!current()) return;
        if (typeof result.id !== 'string' || !/^[0-9a-f-]{36}$/i.test(result.id) || result.status !== 'received') throw new Error('INQUIRY_UNCONFIRMED');
        form.reset(); feedback.style.overflowWrap = 'anywhere'; feedback.textContent = t(`采购申请已由服务端接收，申请编号：${result.id}。后续跟进不代表订阅已经开通。`,`Your inquiry was received by the server. Request ID: ${result.id}. Follow-up does not mean a subscription is active.`);
        await loadInquiries(0);
      } catch (error) {
        if (current()) { feedback.classList.add('error'); feedback.textContent = requestError(error, t('提交结果未确认。请先刷新申请记录核对，避免重复提交；内容仍保留在当前页面。','Submission could not be confirmed. Refresh inquiries before retrying to avoid duplicates. Your form remains on this page.')); }
      } finally { if (current()) { submitting = false; for (const control of form.elements) control.disabled = false; feedback.tabIndex = -1; feedback.focus(); } }
    });
    await loadInquiries();
  }

  async function init() {
    const sessionReady = loadSession();
    try {
      let response;
      try {
        response = await fetch('/api/billing/catalog', {signal:AbortSignal.timeout(4000)});
        if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('CATALOG_UNAVAILABLE');
        catalog = await response.json(); online = true;
      } catch (_) {
        response = await fetch('billing-catalog.json', {signal:AbortSignal.timeout(4000)});
        if (!response.ok) throw new Error('CATALOG_UNAVAILABLE');
        catalog = await response.json(); online = false;
      }
      if (catalog.currency !== 'CNY' || !Array.isArray(catalog.plans) || !Array.isArray(catalog.channels)) throw new Error('INVALID_CATALOG');
      await sessionReady;
      if (view === 'pricing') {
        const rawSeats = initial.get('seats');
        const invalidSeats = initial.has('seats') && (!/^[0-9]+$/.test(rawSeats) || !Number.isSafeInteger(Number(rawSeats)) || Number(rawSeats) > 10000 || (audience === 'business' ? Number(rawSeats) < 2 : Number(rawSeats) !== 1));
        if ((initial.has('cycle') && !['monthly','annual'].includes(initial.get('cycle'))) || (initial.has('audience') && !['personal','business'].includes(initial.get('audience'))) || invalidSeats) return fail(t('方案类型、周期或席位数量无效，请返回价格页重新选择。','The plan type, billing cycle or seat count is invalid. Return to Pricing to choose again.'));
        pricing(); loadPurchaseCatalog();
      } else if (view === 'checkout') await checkout(); else account();
    } catch (_) { fail(t('方案信息暂时无法加载。请重新加载，或返回首页。','Plan information could not be loaded. Retry or return to the homepage.')); }
  }
  window.addEventListener('hashchange', () => { if (view === 'account' && catalog) { account(); const heading = document.querySelector('#account-section-title'); heading.setAttribute('tabindex', '-1'); heading.focus(); } });
  window.addEventListener('pagehide', () => {
    sessionVersion++; accountVersion++; session = null; sessionBusy = false; logoutToken = null; sessionState = 'loading';
    sessionNotice = logoutPending ? t('本页账户数据已清除，但服务端退出尚未确认。','Account data is cleared from this page, but server sign-out is not confirmed.') : '';
    refreshSessionView();
  });
  window.addEventListener('pageshow', event => { if (event.persisted) loadSession(); });
  init();
})();
