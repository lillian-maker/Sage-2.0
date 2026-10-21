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
  let seats = /^[0-9]+$/.test(initial.get('seats') || '') ? Number(initial.get('seats')) : 1;
  if (!Number.isSafeInteger(seats) || seats < 1 || seats > 10000) seats = 1;
  const boundary = t('订阅服务暂未开放购买。你可以查看方案；开通后将明确展示权益和费用。','Subscriptions are not yet available to purchase. Explore the plans now; full benefits and charges will be shown before purchases open.');
  let session = null, sessionState = 'loading', sessionBusy = false, accountVersion = 0, sessionVersion = 0;
  let sessionNotice = '', logoutToken = null, logoutPending = false, logoutUserId = null;
  let purchaseRefresh = null, purchaseCatalog = null, closeMemberDialog = null;
  let googlePayScript = null;
  let pricingSubscriptions = null, pricingSubscriptionState = 'idle', pricingReadVersion = 0;
  const accountUnavailable = t('账户服务暂时无法连接，当前数据未知。请重新连接；不会把无法读取的余额当作 0。','Account services cannot be reached. Your data is unknown. Reconnect; unavailable balances are never shown as zero.');
  const authURL = () => `${page('auth')}?${new URLSearchParams({return:location.pathname + location.search + location.hash})}`;
  const creditsSubscriptionRequired=t('当前没有生效的已付套餐；续订后可购买新点数，未过期的已有点数仍可使用。','Your paid service is not active. Renew before purchasing new Credits; existing unexpired Credits remain usable.');
  const requestError = (error, fallback) => ({CREDITS_SUBSCRIPTION_REQUIRED:creditsSubscriptionRequired,VERIFICATION_CANCELLED:t('已取消身份验证，未提交本次操作。','Identity verification cancelled. This action was not submitted.'),FORBIDDEN:t('你没有办理此操作的权限，请切换有权限的账户或联系管理员。','You do not have permission for this action. Switch to an authorized account or contact your admin.'),CSRF_FAILED:t('页面验证已失效，请重新连接账户服务后再试。','Page verification has expired. Reconnect to account services and retry.'),ACCOUNT_CHANGED:t('计费账户已在其他页面变更，旧账户数据已清除。请重新连接并核对账户；操作不会自动重试。','The billing account changed on another page. Previous account data has been cleared. Reconnect and review the account; the action will not retry automatically.')})[error.message] || fallback;
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
    closeMemberDialog?.();
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
    window.dispatchEvent(new CustomEvent('sage:session',{detail:session || {authenticated:false}}));
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
      const context=document.createElement('section');context.id='billing-account-context';context.className='account-context';context.setAttribute('aria-label',t('计费账户','Billing account'));context.innerHTML=identity.replace(/<button[^>]+id="account-logout"[\s\S]*?<\/button>/,'');(document.querySelector('.billing-center-sidebar') || document.querySelector('#main')).prepend(context);
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
    root.innerHTML = `<a class="skip" href="#main">${t('跳至内容','Skip to content')}</a><header class="billing-nav"><a class="billing-brand" href="../${page('index')}"><img class="sage-logo" src="../assets/brand/sage-logo-product.svg" width="112" height="49" alt="Sage"></a><button id="r-menu-toggle" type="button" aria-expanded="false" aria-controls="r-menu" aria-label="${t('打开导航菜单','Open navigation menu')}">☰</button><nav id="r-menu" aria-label="${t('主导航','Main navigation')}"><a href="../${page('index')}#product">${t('产品','Product')}</a><a href="${page('team')}">${t('AI 团队','AI team')}</a><a href="${page('enterprise')}">${t('企业服务','Enterprise')}</a><a href="${page('pricing')}" ${view === 'pricing' ? 'aria-current="page"' : ''}>${t('价格','Pricing')}</a><a href="${page('docs')}">${t('文档','Docs')}</a></nav><div class="nav-actions"><a href="${escape(other)}" lang="${en ? 'zh-CN' : 'en'}">${en ? '中文' : 'English'}</a><a class="nav-login" href="${escape(authURL())}">${t('登录','Sign in')}</a><details class="nav-account" hidden><summary>${icon('person')}<span>${t('账户','Account')}</span></summary><div class="account-dropdown"><p>${t('账户服务暂未开放，当前无法登录。','Account services are not yet available. Sign-in is unavailable.')}</p><a href="${page('billing')}">${t('订阅与账单','Billing')}</a><a href="${page('billing')}#credits">${t('加购 Sage Credits','Buy Sage Credits')}</a><a href="${page('billing')}#payment-methods">${t('支付方式','Payment methods')}</a></div></details><a class="button primary" href="${page('download')}">${t('下载','Download')}</a></div></header><main id="main" class="page ${view}-page">${back}${content}</main><footer class="footer"><span>Sage · ${t('你的出海 AI 团队','Your AI team for global commerce')}</span><span>${t('深圳主体 · 人民币计价 · Sage Credits','Shenzhen company · CNY pricing · Sage Credits')}</span></footer>`;
    renderAccountChrome();
  }
  function faqs() {
    const entries = [
      [t('Sage Credits 何时发放和到期？','When are Sage Credits granted and when do they expire?'),t('订阅额度按服务月发放，当期有效、不结转；年付也逐月发放。每笔加购自确认到账起 12 个月有效，新购买不延长旧批次。个人账户优先使用最早到期的可用批次，同到期先用订阅额度。Teams 公司任务先用本人企业套餐内含点数，再用企业共享点数；各来源内优先使用最早到期批次。','Subscription credits are granted each service month, expire at its end and do not roll over, including on annual plans. Each purchase lasts 12 months from confirmed payment; new purchases do not extend older batches. Personal accounts use eligible Credits expiring first, with subscription Credits first when expiry matches. Teams company tasks use the member’s business plan allowance before shared business Credits; within each source, earliest expiry comes first.')],
      [t('用完后会自动扣费吗？','Will usage trigger another charge?'),t('个人账户仅主动加购或升级，不自动充值、不透支。企业自动充值默认关闭，管理员需从固定方案中选择并单独授权。只有任务需要使用企业共享点数，且共享可用余额低于触发线时才充值，不因共享余额偏低提前扣费。相关服务尚未开放。','Personal accounts require a manual purchase or upgrade, with no automatic top-ups or overdrafts. Business auto top-up is off by default and needs separate admin authorization of a fixed preset. It triggers only when a task needs shared business Credits and the available shared balance is below the threshold, not just because that balance is low. These services are not yet available.')],
      [t('Teams 和 Enterprise 如何分配额度？','How do Teams and Enterprise credits work?'),t('Teams 每个付费名额的月内含点数独享，不转给其他成员；企业加购点数共享。Enterprise 套餐内含点数按购买名额汇入企业共享池，每个服务月发放、当月有效；年付也逐月发放。公司任务先用本人企业套餐内含点数，再用企业共享点数，不扣私人点数，也不设置成员共享用量上限。具体额度与权益开售前公布。','Each Teams paid seat has an individual monthly allowance that cannot be transferred. Purchased business Credits are shared. Enterprise included Credits are pooled according to purchased seats, granted each service month and expire at its end, including on annual plans. Company tasks use the member’s business plan allowance before shared Credits, never private Credits. There are no per-member shared usage caps. Allowances and benefits will be published before sales open.')],
      [t('订阅结束后，已购买额度还能使用吗？','Can purchased credits be used after a subscription ends?'),t('尚未到期的已购 Sage Credits 仍可用于届时公布的基础任务，但不再发放订阅额度，也不保留原档位专属权益。','Unexpired purchased Sage Credits remain usable for the published basic tasks. Subscription credits stop and exclusive plan benefits do not continue.')],
      [t('如何关闭自动续费？','How do I stop automatic renewal?'),t('服务开放后，可在“订阅与账单”关闭自动续费。已付款周期内的权益保留；关闭续费不等于退款。企业自动充值独立管理，可单独关闭。','Once available, turn off renewal in Billing. Paid access remains through the current term; cancellation is separate from a refund. Business auto top-up is managed and cancelled separately.')],
      [t('退款如何计算？','How are refunds calculated?'),t('已开始的月度订阅原则不退，主动升档等约定例外除外。年付可退未来尚未开始的完整服务月，当前服务月保留。加购需在到期前按购买订单申请，退该单全部未用付费余额；按实付与未用比例计算，套餐内含点数不能单独折现。已开票、合同或异常订单由财务审核，法定权利不受影响。','Started monthly terms are generally non-refundable, except for agreed cases such as a paid upgrade. Annual refunds cover future complete service months, preserving the current month. Before expiry, request an order’s entire unused paid credit balance, proportional to its payment and unused amount; included plan Credits cannot be cashed out separately. Invoiced, contract and exceptional orders require finance review. Statutory rights remain unaffected.')]
    ];
    return `<section class="rules"><div><span class="eyebrow">SAGE CREDITS</span><h2>${t('订阅与用量，清楚分开','Clear plans. Clear usage.')}</h2><p>${t('了解有效期、企业额度，以及续费和退款规则。','Understand credit validity, business resources, renewals and refunds.')}</p></div><div>${entries.map(([q,a]) => `<details><summary>${q}</summary><p>${a}</p></details>`).join('')}</div></section>`;
  }

  function personalBenefits(code) {
    const includes = code === 'ultra' ? t('包含 Pro+ 全部功能','Everything in Pro+') : code === 'pro_plus' ? t('包含 Pro 全部功能','Everything in Pro') : t('个人业务功能','Personal business features');
    const features = [t('1 个真人使用席位','1 human seat')];
    if (code !== 'pro') features.push(t('优先体验新功能','Early access to new features'));
    return `<div class="plan-benefits personal-benefits"><p class="plan-includes">${includes}</p><ul>${features.map(text=>`<li>${icon('check2')}<span>${text}</span></li>`).join('')}</ul><dl class="plan-pending">${[t('每月内含点数','Monthly Credits'),t('同时运行任务数','Concurrent tasks'),t('具体功能清单','Feature details')].map(label=>`<div><dt>${label}</dt><dd>${t('待定','Pending')}</dd></div>`).join('')}</dl><p class="plan-release-note">${code === 'pro' ? t('具体功能以正式公布为准。','Specific features will be published before sales open.') : t('新功能优先体验随发布安排开放。','Early access follows the release schedule.')}</p></div>`;
  }
  function businessBenefits(plan) {
    const shared = plan.code === 'enterprise';
    const features = [
      t(`${plan.minimum_seats} 个付费名额起购`,`Starts at ${plan.minimum_seats} paid seat${plan.minimum_seats === 1 ? '' : 's'}`),
      shared ? t('内含点数按购买名额汇总共享','Included Credits pooled by purchased seats') : t('每个名额独享内含点数，不转给他人','Included Credits stay with each seat; not transferable'),
      t('企业加购点数由成员共享','Company-purchased add-on Credits are shared'),
      t('内含点数每月发放，当月有效、不结转','Included Credits refresh monthly and do not roll over'),
      t('管理员可查看成员用量','Admins can view member usage')
    ];
    return `<div class="plan-benefits business-benefits"><p class="plan-includes">${shared ? t('套餐点数全员共享','Shared included Credits') : t('套餐点数按成员使用','Individual included Credits')}</p><ul>${features.map(text=>`<li>${icon('check2')}<span>${escape(text)}</span></li>`).join('')}</ul><dl class="plan-pending">${[t('每个名额月内含点数','Monthly Credits per seat'),t('同时运行任务数','Concurrent tasks'),t('具体功能清单','Feature details')].map(label=>`<div><dt>${label}</dt><dd>${t('待定','Pending')}</dd></div>`).join('')}</dl><p class="plan-release-note">${t('年付也逐月发点。企业任务不扣员工私人点数；共享用量不设个人上限，仍受企业可用余额约束。','Annual plans also grant Credits monthly. Company tasks never use private Credits. Shared usage has no per-member cap and remains subject to the company’s available balance.')}</p></div>`;
  }
  function pricing() {
    document.title = t('价格 · Sage','Pricing · Sage');
    shell(`<header class="page-heading pricing-heading"><span class="eyebrow">SAGE MEMBERSHIP</span><h1>${t('选择适合你的 Sage 计划','Choose your Sage plan')}</h1><p>${t('从个人经营到企业协作，选择适合你的方案与使用节奏','Find the right plan for individual work or business collaboration.')}</p><div class="segmented audience-control" role="group" aria-label="${t('方案类型','Plan type')}"><button data-audience="personal" aria-pressed="${audience === 'personal'}">${t('个人版','Personal')}</button><button data-audience="business" aria-pressed="${audience === 'business'}">${t('企业版','Business')}</button></div></header><section id="pricing-subscription-status" class="policy-block" aria-live="polite"></section><div class="controls"><h2>${audience === 'personal' ? t('个人方案','Personal plans') : t('企业方案','Business plans')}</h2><div class="segmented" role="group" aria-label="${t('付费周期','Billing cycle')}"><button data-cycle="monthly" aria-pressed="${cycle === 'monthly'}">${t('连续包月','Monthly renewal')}</button><button data-cycle="annual" aria-pressed="${cycle === 'annual'}">${t('连续包年','Annual renewal')}</button></div></div>${audience === 'business' ? `<div class="seat-selector"><label for="pricing-seats">${t('真人席位数量','Human seats')}</label><input id="pricing-seats" type="number" min="1" max="10000" step="1" value="${seats}" inputmode="numeric" aria-describedby="pricing-seat-help pricing-seat-error"><p id="pricing-seat-help">${t('Teams 1 席起，Enterprise 10 席起；按购买的名额上限安排员工。企业价格与完整权益尚未公布，当前不接受付款。','Teams starts at 1 seat; Enterprise starts at 10. Assign members within the purchased capacity. Business prices and full benefits are pending; no payment is accepted.')}</p><p id="pricing-seat-error" class="field-error" role="status"></p></div>` : ''}<div id="plans" class="plan-grid ${audience === 'business' ? 'business' : ''}" aria-live="polite"></div><p class="notice">${icon('info-circle')}${boundary}</p><section class="credits-entry"><div>${icon('wallet2')}<div><h2>${t('按需加购 Sage Credits','Buy additional Sage Credits')}</h2><p>${t('每笔购买独立有效 12 个月，不改变订阅周期','Each purchase lasts 12 months and does not change your subscription term.')}</p></div></div><a class="button" href="${page('billing')}#credits">${t('了解加购','Explore add-ons')}</a></section>${faqs()}`);
    for (const button of document.querySelectorAll('[data-audience]')) button.onclick = () => { audience = button.dataset.audience; history.replaceState(null, '', selectionURL('pricing')); pricing(); document.querySelector(`[data-audience="${audience}"]`).focus(); };
    for (const button of document.querySelectorAll('[data-cycle]')) button.onclick = () => { cycle = button.dataset.cycle; history.replaceState(null, '', selectionURL('pricing')); pricing(); document.querySelector(`[data-cycle="${cycle}"]`).focus(); };
    const descriptions = {
      trial:t('先了解 Sage 如何处理一个经营问题。','Explore how Sage approaches a business question.'),
      pro:t('个人日常经营分析的起点。','A starting point for everyday business analysis.'),
      pro_plus:t('适合更频繁的个人业务分析。','For more frequent individual business work.'),
      ultra:t('面向高频、深入的个人业务分析。','For intensive individual business work.'),
      teams:t('每个付费名额独享月内含点数，企业加购点数共享。','Individual monthly Credits per paid seat; business add-ons are shared.'),
      enterprise:t('套餐内含点数与企业加购点数共享使用。','Included monthly Credits and business add-ons are shared.')
    };
    document.querySelector('#plans').innerHTML = catalog.plans.filter(p => p.audience === audience).map(p => {
      const trial = p.code === 'trial', price = trial ? 0 : p[`${cycle}_amount_minor`];
      const priced = Number.isSafeInteger(price) && price >= 0;
      const features = [t('VOC 机会洞察 / Shopify 店铺诊断二选一','Choose VOC insights or a Shopify diagnosis'),t('仅一次成功体验；当前可查看示例','One successful trial; examples are currently available'),t('无需银行卡，不自动转付费','No card required. No automatic paid conversion')];
      return `<article class="plan"><div class="plan-top"><h2>${escape(trial ? t('体验版','Trial') : p.name)}</h2>${p.code === 'pro' ? `<span class="tag">${t('日常经营','Everyday work')}</span>` : ''}</div><p class="plan-description">${descriptions[p.code] || ''}</p><div class="price ${priced ? '' : 'pending'}">${priced ? amountMarkup(price) : t('价格待定','Price pending')}${trial || !priced ? '' : `<small>/${p.audience === 'business' ? t('席位/','seat/') : ''}${cycle === 'annual' ? t('年','year') : t('月','month')}</small>`}</div><p class="price-note">${trial ? t('不绑定支付方式','No payment method required') : priced ? t('参考价 · 正式价格待定','Reference only · Final price pending') : t('未定价，不接受付款','Not priced. No payment accepted.')}</p>${trial ? `<a class="button" href="${page('trial')}">${t('查看体验示例','Explore trial examples')}</a>` : `<a class="button ${priced ? 'primary' : ''}" data-plan-link="${escape(p.code)}" data-minimum-seats="${p.minimum_seats}" href="${escape(selectionURL('checkout', {plan:p.code}))}">${t('查看方案','Review plan')}</a>`}${trial ? `<div class="plan-benefits"><ul>${features.map(f => `<li>${icon('check2')}<span>${f}</span></li>`).join('')}</ul></div>` : p.audience === 'personal' ? personalBenefits(p.code) : businessBenefits(p)}</article>`;
    }).join('') + (audience === 'business' ? `<article class="plan"><div class="plan-top"><h2>${t('合同采购','Contract')}</h2></div><p class="plan-description">${t('按企业需求约定席位、资源与服务。','Agree seats, resources and services for your business.')}</p><div class="price pending">${t('联系销售','Custom quote')}</div><p class="price-note">${t('对公转账 · 财务核验到账后开通','Corporate transfer · Access after verified receipt')}</p><a class="button primary" href="${page('billing')}#procurement">${t('查看采购说明','View procurement details')}</a><ul><li>${t('1 人也可提交采购咨询','Purchasing inquiries welcome from 1 person')}</li><li>${t('合同明确含税金额与服务期','Tax-inclusive amount and service term in contract')}</li><li>${t('对公采购不自动续费','Contract purchases do not renew automatically')}</li></ul></article>` : '');
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
        card.querySelector('.price').classList.remove('pending');
        card.querySelector('.plan-benefits').classList.remove('personal-benefits','business-benefits');
        card.querySelector('.plan-benefits').innerHTML = `<ul>${benefits.map(text=>`<li>${icon('check2')}<span>${escape(text)}</span></li>`).join('')}<li>${icon('wallet2')}<span>${escape(product.credits_quantity)} Sage Credits</span></li></ul>`;
      }
      if (document.querySelector('[data-purchase-product]')) {
        const notice = document.querySelector('.notice');
        notice.replaceChildren(document.createTextNode(t('已批准方案可获取报价并创建订单。创建订单不会扣款，支付与续费授权尚未接通。其他方案仍为参考信息。','Approved plans support quotes and order creation, without charging you. Payment and renewal authorization are not connected. Other plans remain reference information.')));
        const help = document.querySelector('#pricing-seat-help');
        if (help) help.textContent=t('Teams 1 席起，Enterprise 10 席起。已批准商品以正式报价为准；创建订单不等于付款或开通。','Teams starts at 1 seat; Enterprise starts at 10. Approved products use the confirmed quote; creating an order does not mean payment or activation.');
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
    const updateSeatSelection = (updateLocation = false) => {
      if (!input) return;
      const valid = input.validity.valid && /^[0-9]+$/.test(input.value);
      input.setAttribute('aria-invalid', String(!valid));
      document.querySelector('#pricing-seat-error').textContent = valid ? '' : t('请输入 1 至 10,000 的整数席位数；Enterprise 至少 10 席。','Enter a whole number from 1 to 10,000; Enterprise requires at least 10 seats.');
      if (valid) seats = Number(input.value);
      for (const link of document.querySelectorAll('[data-plan-link]')) {
        if (link.dataset.subscriptionManaged) continue;
        const eligible = valid && seats >= Number(link.dataset.minimumSeats);
        link.setAttribute('aria-disabled', String(!eligible));
        if (eligible) link.href = link.dataset.purchaseProduct ? `${page('checkout')}?${new URLSearchParams({product:link.dataset.purchaseProduct,seats:String(seats)})}` : selectionURL('checkout', {plan:link.dataset.planLink});
        else link.removeAttribute('href');
      }
      if (valid && updateLocation) {
        history.replaceState(null, '', selectionURL('pricing'));
        document.dispatchEvent(new Event('sage:location-updated'));
      }
    };
    updateSeatSelection();
    if (input) input.addEventListener('input', () => updateSeatSelection(true));
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
    if (!/^[0-9]+$/.test(raw) || !Number.isSafeInteger(quantity) || quantity < plan.minimum_seats || quantity > 10000 || (audience === 'personal' && quantity !== 1)) return fail(t(`席位数量无效。${plan.name} 至少 ${plan.minimum_seats} 席，个人方案限 1 席，请返回方案重新选择。`,`Invalid seat count. ${plan.name} requires at least ${plan.minimum_seats} seat(s); personal plans have exactly 1. Return to plans to choose again.`));
    seats = quantity;
    document.title = t('确认方案 · Sage','Review plan · Sage');
    const channelIcon = {alipay:'alipay',card:'credit-card',wechat:'wechat',google_pay:'google',apple_pay:'apple'};
    const channelRow = c => `<button class="channel ${escape(c.code)}" disabled aria-describedby="payment-unavailable">${c.code === 'google_pay' ? '<img class="ui-icon" src="icons/google-color.png" alt="" aria-hidden="true">' : icon(channelIcon[c.code] || 'credit-card')}<span>${escape(({alipay:t('支付宝','Alipay'),card:t('银行卡','Card'),wechat:t('微信支付','WeChat Pay')})[c.code] || c.name)}</span><small>${t('暂不可用','Unavailable')}</small></button>`;
    shell(`<a class="back-link plan-return" href="${escape(selectionURL('pricing'))}">${icon('arrow-left')}${t('返回方案','Back to plans')}</a><header class="page-heading checkout-heading"><h1>${t('确认你的 Sage 订阅','Review your Sage subscription')}</h1><p>${t('核对方案、席位与计费周期。订阅暂未开放，不会扣款。','Review your plan, seats and billing cycle. Subscriptions are not yet available; no charge will be made.')}</p></header><div class="checkout-grid"><section class="order-summary" aria-labelledby="summary-title"><p class="summary-label" id="summary-title">${t('你选择的方案','Your selected plan')}</p><h2 class="selected-plan">Sage ${escape(plan.name)}</h2><p class="reference-label">${t('方案参考金额 · 正式价格待定','Plan reference amount · Final price pending')}</p><div class="total"><strong id="quote-amount">—</strong><span>${period === 'monthly' ? t('/ 月','/ month') : t('/ 年','/ year')}</span></div><p class="summary-frequency">${period === 'monthly' ? t('连续包月 · 人民币计价','Monthly renewal · CNY') : t('连续包年 · 人民币计价','Annual renewal · CNY')}</p><div class="currency-pill">${icon('wallet2')} CNY <span>${t('人民币','Chinese yuan')}</span></div><div class="order-lines"><div class="line-item"><span>${icon('calendar3')}${t('订阅周期','Billing cycle')}</span><strong>${period === 'monthly' ? t('每月','Monthly') : t('每年','Annually')}</strong></div><div class="line-item seat-line">${audience === 'business' ? `<label for="checkout-seats">${icon('person')}${t('真人席位','Human seats')}</label><input id="checkout-seats" type="number" min="${plan.minimum_seats}" max="10000" step="1" value="${seats}" inputmode="numeric" aria-describedby="checkout-seat-error">` : `<span>${icon('person')}${t('真人席位','Human seats')}</span><strong>1</strong>`}</div><p id="checkout-seat-error" class="field-error" role="status"></p><div class="line-item"><span>${icon('receipt')}${t('费用与发票','Charges & invoices')}</span><strong>${t('含税计价，按实付申请发票','Tax-inclusive; invoice based on actual payment')}</strong></div><div class="line-item"><span>${t('下一次扣款','Next charge')}</span><strong>${t('未开通，无扣款计划','Not subscribed; no charge scheduled')}</strong></div></div>${audience === 'personal' ? personalBenefits(code) : businessBenefits(plan)}<p id="quote-status" class="checkout-note" role="status" aria-live="polite"></p><button type="button" class="button quote-refresh" id="quote-refresh">${t('重新查询方案金额','Refresh plan amount')}</button><div class="summary-assurance">${icon('shield-lock')}<p>${t('深圳主体提供服务并收款。<br>开通时将明确展示签约主体、费用和续费授权。','Service and payment collection by the Shenzhen company.<br>The legal entity, charges and renewal authorization will be shown before purchase.')}</p></div></section><section class="checkout-payment" aria-labelledby="payment-title"><div class="billing-identity"><h2>${t('计费账户','Billing account')}</h2><div class="identity-row">${icon('person')}<div><strong>${t('账户服务暂未开放','Account services unavailable')}</strong><p>${t('暂无法登录或确认本次购买归属，不接受付款','Sign-in and billing-account verification are unavailable; no payment is accepted')}</p></div>${icon('lock')}</div></div><h2 id="payment-title">${t('支付方式','Payment methods')}</h2><p class="payment-hint" id="payment-unavailable">${t('支付服务尚未开放。以下方式正在评估，当前均不能付款或签约自动续费。','Payment services are not yet available. The methods below are being evaluated and cannot accept payments or renewal authorizations.')}</p><div class="express-pay">${catalog.channels.filter(c => ['apple_pay','google_pay'].includes(c.code)).map(channelRow).join('')}</div><div class="payment-divider"><span>${t('银行卡与本地支付','Cards & local payments')}</span></div><div class="channel-list">${catalog.channels.filter(c => !['apple_pay','google_pay'].includes(c.code)).sort((a,b) => a.code === 'card' ? -1 : b.code === 'card' ? 1 : 0).map(channelRow).join('')}</div><label class="consent"><input type="checkbox" disabled><span>${t('购买前需主动同意自动续费条款，并确认后续扣款时间和金额。当前不能授权，也不会保存支付信息。','Before purchase, explicitly accept renewal terms and review future charge dates and amounts. Authorization and payment-detail storage are currently unavailable.')}</span></label><button class="button wide purchase-unavailable" disabled aria-describedby="payment-unavailable">${icon('lock')}${t('暂未开放订阅','Subscriptions not yet available')}</button><div class="checkout-support"><a class="text-link" href="${page('billing')}#procurement">${t('企业采购说明','Enterprise purchasing')}</a><a class="text-link" href="${page('billing')}#payment-methods">${t('支付方式管理说明','Managing payment methods')}</a></div></section></div><p class="notice">${icon('info-circle')}${boundary}</p>`);
    const status = document.querySelector('#quote-status'), amount = document.querySelector('#quote-amount'), refresh = document.querySelector('#quote-refresh'), input = document.querySelector('#checkout-seats');
    let requestId = 0;
    async function refreshQuote(useNetwork = true) {
      const id = ++requestId;
      const valid = !input || (input.validity.valid && /^[0-9]+$/.test(input.value));
      if (input) input.setAttribute('aria-invalid', String(!valid));
      document.querySelector('#checkout-seat-error').textContent = valid ? '' : t(`请输入 ${plan.minimum_seats} 至 10,000 的整数席位数。`,`Enter a whole number of seats from ${plan.minimum_seats} to 10,000.`);
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
    let requestVersion = 0, quoteData = null, product = null, options = [], busy = false, paymentRequested = null, quoteTimer = null, paymentVersion = 0, paymentTimer = null;
    let walletVersion = 0, walletPending = null, walletCleanup = null;
    function clearWalletTests() {walletVersion++;walletCleanup?.();document.querySelector('#wallet-tests')?.remove();}
    function clearPayment() { paymentVersion++;clearTimeout(paymentTimer);paymentTimer=null;document.querySelector('#purchase-payment')?.remove(); }
    const orderLink = id => `${page('billing')}?${new URLSearchParams({order:id})}#orders`;
    const message = error => requestError(error, ({TERMS_UNAVAILABLE:t('购买条款尚未批准，暂不能提交订单。','Purchase terms are not approved; orders are unavailable.'),TERMS_MISMATCH:t('条款版本已变更，请重新核对，不会自动提交。','The terms have changed. Review them again; nothing is submitted automatically.'),CONFIG_INCOMPLETE:t('商品或支付准入未完成，暂不能购买。','Product or payment approval is incomplete. Purchasing is unavailable.'),NOT_FOUND:t('未找到当前账户的购买记录，请核对计费账户。','This purchase record was not found in your current billing account.'),QUOTE_EXPIRED:t('报价已过期，不能提交新订单。原订单结果仍可查询。','This quote has expired. No new order can be submitted; the original order can still be checked.')})[error.message] || t('暂时无法读取购买记录。请刷新查询，不要重复付款。','The purchase record could not be loaded. Refresh its status; do not pay again.'));
    function draw() {
      clearInterval(quoteTimer);quoteTimer=null;
      clearWalletTests();
      clearPayment();
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
      clearInterval(quoteTimer);quoteTimer=null;
      summary(data.snapshot,data.amount_minor);
      const controls = document.querySelector('#purchase-controls');
      if (data.order_id) {
        controls.innerHTML = `<h2>${t('订单已创建','Order created')}</h2><p>${t('订单不等于支付成功。请在订单详情确认支付与权益状态，不要再次建单。','An order is not a successful payment. Check payment and benefit status in the order details; do not create another order.')}</p><a class="button primary" id="purchase-order-link" href="${escape(orderLink(data.order_id))}">${t('查看原订单','View original order')}</a>`;
        status(t('已恢复原订单；未再次提交或扣款。','The original order was recovered. No new order or charge was made.'));
        renderWalletTests(controls,data.order_id);
        renderPaymentStart(controls,data.order_id);
        return;
      }
      const approvedTerms = data.terms, content = approvedTerms?.[en ? 'en' : 'zh'];
      async function renewQuote(){
        if(busy)return;busy=true;const accountId=session?.active_account_id,version=requestVersion;
        try{const original=await api(`/api/billing/purchase-quotes/${encodeURIComponent(data.id)}`);if(version!==requestVersion||accountId!==session?.active_account_id)return;if(!validQuote(original,accountId))throw Error('INVALID_QUOTE');if(original.order_id){quoteData=original;showQuote(original);return;}const snapshot=original.snapshot;if(!snapshot.product_id||!snapshot.channel||!snapshot.buyer_region)throw Error('CONFIG_INCOMPLETE');const renewed=await api('/api/billing/purchase-quotes',{product_id:snapshot.product_id,seat_quantity:snapshot.seat_quantity,channel:snapshot.channel,buyer_region:snapshot.buyer_region});if(version!==requestVersion||accountId!==session?.active_account_id)return;if(!validQuote(renewed,accountId))throw Error('INVALID_QUOTE');history.replaceState(null,'',`${location.pathname}?${new URLSearchParams({quote:renewed.id})}`);quoteData=renewed;showQuote(renewed);}catch(error){status(message(error),true);}finally{busy=false;}
      }
      if (!data.can_order || Date.parse(data.expires_at) <= Date.now()) {
        controls.innerHTML = `<button class="button" id="purchase-recover">${t('刷新原报价与订单状态','Refresh original quote and order')}</button>`;
        controls.querySelector('button').onclick = load;
        if(Date.parse(data.expires_at)<=Date.now()){const renew=document.createElement('button');renew.type='button';renew.className='button primary';renew.id='purchase-renew-quote';renew.textContent=t('更新报价并重新确认','Update quote and review again');renew.onclick=renewQuote;controls.append(renew);}
        status(message(new Error(Date.parse(data.expires_at) <= Date.now() ? 'QUOTE_EXPIRED' : data.unavailable_reason)),true);
        return;
      }
      if (!approvedTerms || approvedTerms.version !== data.snapshot.terms_version || approvedTerms.digest !== data.snapshot.terms_document_digest || !content || typeof content.title !== 'string' || !Array.isArray(content.paragraphs) || !content.paragraphs.length || content.paragraphs.some(text=>typeof text !== 'string')) throw new Error('TERMS_UNAVAILABLE');
      controls.innerHTML = `<h2>${escape(content.title)}</h2><div class="policy-block" tabindex="0">${content.paragraphs.map(text=>`<p>${escape(text)}</p>`).join('')}</div><p>${t('报价有效至','Quote valid until')} ${dateLabel(data.expires_at)} <span id="purchase-countdown" aria-live="off"></span></p><label class="consent"><input id="purchase-agree" type="checkbox"><span>${t('我已阅读并同意以上服务条款，确认计费账户和本次方案费用。','I have read and accept these service terms and confirm the billing account and plan charges.')}</span></label><button class="button primary wide" id="purchase-submit" disabled>${t('确认并创建订单','Confirm and create order')}</button><button class="button" id="purchase-recover">${t('刷新原报价与订单状态','Refresh original quote and order')}</button>`;
      const submit = document.querySelector('#purchase-submit'), agree = document.querySelector('#purchase-agree');
      function countdown(){if(!controls.isConnected){clearInterval(quoteTimer);return;}const seconds=Math.max(0,Math.ceil((Date.parse(data.expires_at)-Date.now())/1000));const node=controls.querySelector('#purchase-countdown');if(node)node.textContent=t(`剩余 ${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`,`Remaining ${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`);submit.disabled=busy||!agree.checked||seconds===0;if(seconds===0){agree.checked=false;agree.disabled=true;clearInterval(quoteTimer);showQuote({...data,can_order:false});}}
      quoteTimer=setInterval(countdown,1000);countdown();
      agree.onchange=()=>{submit.disabled=!agree.checked||busy||Date.parse(data.expires_at)<=Date.now();};
      controls.querySelector('#purchase-recover').onclick=load;
      submit.onclick=submitOrder;
      status(t('请逐项核对。不会默认同意条款、扣款或开通订阅。','Review each item. Terms, payment and subscription activation are not accepted automatically.'));
    }
    async function renderPaymentStart(controls, orderId) {
      clearPayment();
      const version=requestVersion, paymentRead=paymentVersion, accountId=session?.active_account_id, accountRead=accountVersion;
      const current=()=>version===requestVersion && paymentRead===paymentVersion && document.visibilityState!=='hidden' && accountRead===accountVersion && accountId===session?.active_account_id && !sessionBusy && controls.isConnected;
      let detail;
      try { detail=await api(`/api/billing/orders/${encodeURIComponent(orderId)}`); }
      catch(error) {
        if(error.status!==503 && current())status(requestError(error,t('订单支付状态读取失败，请查看原订单；不会自动再次发起付款。','Payment status could not be read. Check the original order; no payment will be started automatically.')),true);
        return;
      }
      if(!current())return;
      const order=detail.order, capability=detail.payment_start;
      if(order?.id!==orderId || order.account_id!==accountId || order.currency!=='CNY' || !Number.isSafeInteger(order.amount_minor) || order.amount_minor<=0 || order.amount_minor!==quoteData?.amount_minor)return;
      const wechat=order.snapshot?.channel==='wechat' || detail.payment_attempt?.channel==='wechat' || capability?.channel==='wechat';
      const allowedChannel=wechat ? capability?.environment==='LOCAL_TEST' && capability.channel==='wechat' && order.snapshot?.channel==='wechat' : capability?.environment==='SANDBOX' && capability.channel==='alipay';
      const available=allowedChannel && capability.available===true && capability.order_id===orderId && capability.account_id===accountId && order.status==='pending_payment' && !order.paid_at && order.snapshot?.kind==='credits' && order.snapshot.billing_cycle==='' && (!detail.payment_attempt || detail.payment_attempt.status==='created') && paymentRequested!==orderId;
      if(!available && !detail.payment_attempt && paymentRequested!==orderId)return;
      const panel=document.createElement('section');panel.id='purchase-payment';panel.className='policy-block';
      panel.innerHTML=`<h3>${t('原订单支付状态','Original order payment status')}</h3><p id="purchase-payment-status" role="status" aria-live="polite"></p>${wechat?`<p>${t('隔离本地测试，不是已开通正式微信收款；付款与交付以原订单服务端记录为准。','Isolated local test; not enabled for live WeChat collection. Payment and delivery follow the original server order.')}</p>`:''}${available?`${!wechat?`<p>${t('仅用于获准的一次性 Credits 沙箱测试，不是正式收款或订阅续费。付款与交付状态以原订单服务端记录为准。','Approved one-time Credits sandbox test only; not live collection or subscription renewal. Payment and delivery status come from the original server order.')}</p>`:''}<button type="button" class="button" id="purchase-payment-start">${wechat?t('生成微信本地测试二维码','Generate WeChat local test QR'):t('支付宝沙箱测试付款','Alipay sandbox test payment')}</button>`:''}<button type="button" class="button" id="purchase-payment-refresh">${t('查询原交易状态','Check original payment status')}</button>`;
      controls.append(panel);
      let operation=0,querying=false;
      const active=()=>current() && panel.isConnected;
      const note=text=>{if(active())panel.querySelector('#purchase-payment-status').textContent=text;};
      const unconfirmed=t('付款结果未确认，请查询原交易；本页不会再次发起付款。','Payment is unconfirmed. Check the original payment; this page will not start another payment.');
      function showQR(result) {
        panel.querySelector('#purchase-payment-qr')?.remove();
        clearTimeout(paymentTimer);paymentTimer=null;
        const qr=result.payment_qr, attempt=result.payment_attempt;
        if(!wechat || !active() || order.status!=='pending_payment' || order.paid_at || detail.delivery?.status!=='not_started' || detail.needs_review===true || !Array.isArray(detail.payments) || detail.payments.length || !attempt || attempt.status!=='pending' || (attempt.channel!==undefined && attempt.channel!=='wechat') || attempt.needs_reconciliation!==false || !validId(attempt.id) || !qr || qr.channel!=='wechat' || qr.account_id!==accountId || qr.order_id!==orderId || qr.attempt_id!==attempt.id || qr.expires_at!==attempt.expires_at || !Number.isFinite(Date.parse(attempt.expires_at)))return;
        const expiry=Date.parse(attempt.expires_at);
        const expired=()=>note(t('二维码已过期，请查询原交易；过期不代表付款失败，不要重新付款。','The QR has expired. Check the original payment; expiry does not mean payment failed. Do not pay again.'));
        if(expiry<=Date.now()){expired();return;}
        // SVG stays in an image context. Accept only the QR library's inert geometry;
        // never insert provider markup into HTML or follow a payment code URL.
        const image=qr.image_svg_base64;
        if(typeof image!=='string' || !image.length || image.length>524288 || image.length%4!==0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(image))return;
        let svg;
        try {
          const source=atob(image);
          if(btoa(source)!==image || /<!DOCTYPE|<!ENTITY/i.test(source))return;
          svg=new DOMParser().parseFromString(source,'image/svg+xml');
          const attributes=new Set(['xmlns','width','height','viewBox','version','d','id','fill','fill-opacity','fill-rule','stroke','x','y']);
          if(svg.documentElement.localName!=='svg' || svg.documentElement.namespaceURI!=='http://www.w3.org/2000/svg' || svg.querySelector('parsererror') || [...svg.querySelectorAll('*')].some(node=>!['svg','path','rect'].includes(node.localName) || node.namespaceURI!=='http://www.w3.org/2000/svg' || [...node.attributes].some(attr=>!attributes.has(attr.name) || (attr.name==='xmlns' ? attr.value!=='http://www.w3.org/2000/svg' : attr.namespaceURI || (['fill','stroke'].includes(attr.name) && !/^(?:#[0-9a-f]{3,8}|black|white|none)$/i.test(attr.value)) || /[<>&"'()\\]|(?:https?|javascript|weixin):/i.test(attr.value)))) || svg.documentElement.textContent.trim())return;
        }catch(_){return;}
        const img=document.createElement('img');img.id='purchase-payment-qr';img.alt=t('微信原订单隔离本地测试二维码','WeChat original order isolated local test QR');img.setAttribute('aria-describedby','purchase-payment-status');img.width=240;img.height=240;img.style.cssText='display:block;width:240px;max-width:100%;height:auto;background:#fff';img.src=`data:image/svg+xml;base64,${image}`;
        panel.querySelector('#purchase-payment-refresh').before(img);
        note(t('请仅核对本地测试二维码；有效至 ','Review the local test QR only; valid until ')+dateLabel(attempt.expires_at)+t('。扫码不代表已付款，请查询原交易确认。','. Scanning does not confirm payment. Check the original payment.'));
        const expire=()=>{if(!active()){clearTimeout(paymentTimer);return;}if(expiry>Date.now()){paymentTimer=setTimeout(expire,Math.min(expiry-Date.now(),2147483647));return;}img.remove();expired();};
        paymentTimer=setTimeout(expire,Math.min(expiry-Date.now(),2147483647));
      }
      note(available?t('沙箱入口已由服务端允许；尚未发起付款。','The server allows this sandbox entry; payment has not been started.'):detail.payment_attempt?t('服务端已记录原交易，请查询原交易；不重新发起付款。','The server recorded the original payment. Check the original payment; do not start another payment.'):unconfirmed);
      if(wechat){
        if(order.status==='fulfilled' || detail.delivery?.status==='completed')note(t('服务端已确认付款与交付，请查看原订单。','The server confirmed payment and delivery. View the original order.'));
        else if(order.paid_at || order.status==='paid')note(t('付款已确认，交付仍在处理中；不要重新付款，请查询原订单。','Payment is confirmed; delivery is processing. Do not pay again. Check the original order.'));
        else if(available)note(t('服务端已允许隔离本地测试；尚未发起微信付款。','The server allows the isolated local test; WeChat payment has not been started.'));
        showQR(detail);
      }
      panel.querySelector('#purchase-payment-refresh').onclick=async()=>{
        if(!active() || querying)return;
        if(!wechat){renderPaymentStart(controls,orderId);return;}
        querying=true;operation++;paymentRequested=orderId;
        panel.querySelector('#purchase-payment-qr')?.remove();panel.querySelector('#purchase-payment-start')?.remove();clearTimeout(paymentTimer);paymentTimer=null;
        const refresh=panel.querySelector('#purchase-payment-refresh');refresh.disabled=true;note(unconfirmed);
        try {await api(`/api/billing/orders/${encodeURIComponent(orderId)}/payment-status`,{});if(active())await renderPaymentStart(controls,orderId);}
        catch(error){if(active()){note(requestError(error,unconfirmed));querying=false;refresh.disabled=false;}}
      };
      const button=panel.querySelector('#purchase-payment-start');
      if(button)button.onclick=async()=>{
        if(!active() || paymentRequested===orderId)return;
        paymentRequested=orderId;button.disabled=true;
        const paymentOperation=++operation;
        note(wechat?t('正在请求原订单的本地测试二维码，请勿重复点击。','Requesting the original order local test QR. Do not click again.'):t('正在请求沙箱付款入口，请勿重复点击。','Requesting the sandbox payment entry. Do not click again.'));
        try {
          const result=await api(`/api/billing/orders/${encodeURIComponent(orderId)}/pay`,{});
          if(!active() || paymentOperation!==operation)return;
          if(!result.payment_attempt || !['created','pending','unknown'].includes(result.payment_attempt.status))throw new Error('PAYMENT_UNCONFIRMED');
          if(wechat){note(unconfirmed);showQR(result);return;}
          if(result.payment_url===undefined || result.payment_attempt.status==='unknown'){note(unconfirmed);return;}
          if(typeof result.payment_url!=='string')throw new Error('INVALID_PAYMENT_URL');
          const url=new URL(result.payment_url);
          if(url.origin!=='https://openapi.alipaydev.com' || url.pathname!=='/gateway.do' || url.username || url.password || url.hash)throw new Error('INVALID_PAYMENT_URL');
          if(active())location.assign(url.href);
        }catch(error){if(active())note(requestError(error,unconfirmed));}
      };
    }
    async function renderWalletTests(controls, orderId) {
      clearWalletTests();
      const version=requestVersion, accountId=session?.active_account_id, accountRead=accountVersion, walletRead=walletVersion;
      const current=()=>walletRead===walletVersion && version===requestVersion && accountRead===accountVersion && accountId===session?.active_account_id && !sessionBusy && controls.isConnected;
      let result;
      try { result=await api(`/api/billing/orders/${encodeURIComponent(orderId)}/wallet-test`); }
      catch(error) {
        // Missing test capability is the normal production response.
        if(error.status!==503 && current())status(requestError(error,t('钱包测试配置读取失败，请重新读取原订单。','Wallet test configuration could not be loaded. Reload the original order.')),true);
        return;
      }
      if(!current() || !Array.isArray(result.wallet_tests) || !result.wallet_tests.length)return;
      const tests=result.wallet_tests;
      if(tests.some(item=>item.environment!=='TEST' || !['google_pay','apple_pay'].includes(item.channel) || item.account_id!==accountId || item.order_id!==orderId || item.currency!=='CNY' || typeof item.amount_minor!=='string' || !/^[1-9][0-9]{0,15}$/.test(item.amount_minor) || !['','monthly','annual'].includes(item.billing_cycle)) || new Set(tests.map(item=>item.channel)).size!==tests.length)return;
      const panel=document.createElement('section');panel.id='wallet-tests';panel.className='policy-block';
      panel.innerHTML=`<h3>${t('隔离钱包测试','Isolated wallet tests')}</h3><p>${t('仅测试付款面板和本地请求；不收款、不交付、不建立续费授权。Google 使用官方 example gateway，不代表任何处理机构联调。真实面板需要兼容浏览器、设备与账号；Apple 还需获准支付机构和有效商户会话。','Tests the payment sheet and local requests only. No payment, fulfillment or renewal authorization. Google uses the official example gateway, not a payment processor integration. Real sheets require a compatible browser, device and account; Apple also requires an approved processor and valid merchant session.')}</p><div data-wallet-items></div><button type="button" class="button" data-wallet-refresh>${t('重新检查钱包测试','Recheck wallet tests')}</button>`;
      controls.querySelector('#wallet-tests')?.remove();controls.append(panel);
      const active=()=>current() && panel.isConnected;
      panel.querySelector('[data-wallet-refresh]').onclick=()=>{if(active()){panel.remove();renderWalletTests(controls,orderId);}};
      const waiting=t('正在等待原测试面板关闭；关闭后请手动重新检查。','Waiting for the original test sheet to close. Recheck manually after it closes.');
      const closingUnknown=t('原 Apple Pay 会话关闭未确认；请先取消原测试面板，再手动重新检查。','The original Apple Pay session closure is unconfirmed. Cancel the original test sheet, then recheck manually.');
      for(const item of tests) {
        const target=document.createElement('section');target.dataset.walletTest=item.channel;
        target.innerHTML=`<h4>${item.channel==='google_pay'?'Google Pay TEST':'Apple Pay · '+t('本地准备','Local preparation')}</h4><p>${t('隔离测试金额','Isolated test amount')} ${escape(money(item.amount_minor,true))}</p><p data-wallet-status role="status" aria-live="polite"></p><div data-wallet-button></div>`;
        panel.querySelector('[data-wallet-items]').append(target);
        const note=text=>{if(active())target.querySelector('[data-wallet-status]').textContent=text;};
        const minor=BigInt(item.amount_minor), amount=`${minor/100n}.${String(minor%100n).padStart(2,'0')}`;
        const buttons=target.querySelector('[data-wallet-button]');
        if(walletPending){note(walletPending.closeUnknown?closingUnknown:waiting);continue;}
        if(item.channel==='google_pay') {
          note(t('正在检查 Google Pay TEST…','Checking Google Pay TEST…'));
          try {
            if(!window.google?.payments?.api?.PaymentsClient) {
              if(!googlePayScript)googlePayScript=new Promise((resolve,reject)=>{
                const script=document.createElement('script');script.src='https://pay.google.com/gp/p/js/pay.js';script.async=true;
                let deadline;
                const failed=()=>{clearTimeout(deadline);script.onload=script.onerror=null;script.remove();googlePayScript=null;reject(new Error('SDK_UNAVAILABLE'));};
                script.onload=()=>{if(window.google?.payments?.api?.PaymentsClient){clearTimeout(deadline);script.onload=script.onerror=null;resolve();}else failed();};script.onerror=failed;
                deadline=setTimeout(failed,8000);
                document.head.append(script);
              });
              await googlePayScript;
            }
            if(!active())return;
            const client=new window.google.payments.api.PaymentsClient({environment:'TEST'});
            const card={type:'CARD',parameters:{allowedAuthMethods:['PAN_ONLY','CRYPTOGRAM_3DS'],allowedCardNetworks:['VISA','MASTERCARD']}};
            const base={apiVersion:2,apiVersionMinor:0,allowedPaymentMethods:[card]};
            let deadline, ready;
            try {
              ready=await Promise.race([client.isReadyToPay(base),new Promise((_,reject)=>{deadline=setTimeout(()=>reject(new Error('SDK_UNAVAILABLE')),8000);})]);
            } finally {clearTimeout(deadline);}
            if(!active())return;
            if(ready.result!==true){note(t('此设备的 Google Pay TEST 不可用。','Google Pay TEST is unavailable on this device.'));continue;}
            const button=client.createButton({buttonLocale:en?'en':'zh',onClick:()=>{
              if(!active())return;
              if(walletPending){note(walletPending.closeUnknown?closingUnknown:waiting);return;}
              const owned={channel:'google_pay'};walletPending=owned;buttons.inert=true;note(t('正在打开测试面板；不会收款。','Opening the test sheet; no payment will be collected.'));
              const request={...base,allowedPaymentMethods:[{...card,tokenizationSpecification:{type:'PAYMENT_GATEWAY',parameters:{gateway:'example',gatewayMerchantId:'exampleGatewayMerchantId'}}}],transactionInfo:{currencyCode:'CNY',totalPriceStatus:'FINAL',totalPrice:amount,checkoutOption:'DEFAULT'},merchantInfo:{merchantName:'Sage TEST'}};
              // No acquirer is configured. The optional countryCode is not inferred from the legal entity.
              new Promise(resolve=>resolve(client.loadPaymentData(request))).then(()=>{
                note(t('测试面板已返回；未收款、未交付，也未建立续费授权。','The test sheet returned; no payment was collected, no benefits delivered and no renewal authorized.'));
              }).catch(error=>note(error?.statusCode==='CANCELED'?t('测试面板已取消；未收款或授权。','The test sheet was canceled; no payment or authorization.'):t('测试面板结果未确认；未收款，请手动重新检查。','The test sheet result is unconfirmed; no payment collected. Recheck manually.'))).finally(()=>{if(walletPending===owned)walletPending=null;if(active())buttons.inert=false;});
            }});
            buttons.append(button);note(t('可打开 Google Pay TEST 面板；仅 example gateway。','Google Pay TEST sheet is available; example gateway only.'));
          }catch(_){note(t('Google Pay SDK 加载失败或检查失败，请手动重新检查。','Google Pay SDK failed to load or check. Recheck manually.'));}
        }else {
          const Apple=window.ApplePaySession;
          let capable=false;
          try { capable=!!Apple && Apple.supportsVersion(3) && Apple.canMakePayments(); }
          catch(_){note(t('Apple Pay 能力检查不可用，请重新检查。','Apple Pay capability check is unavailable. Recheck manually.'));continue;}
          if(!capable){note(t('此设备的 Apple Pay 不可用。','Apple Pay is unavailable on this device.'));continue;}
          if(typeof item.country_code!=='string' || !/^[A-Z]{2}$/.test(item.country_code)){note(t('尚未配置测试收单国家；不能构造 Apple Pay 请求。','The test acquiring country is not configured; an Apple Pay request cannot be constructed.'));continue;}
          const unavailable=t('Apple Pay 会话不可用；未收款，请重新检查。','Apple Pay session is unavailable; no payment collected. Recheck manually.');
          const button=document.createElement('button');button.type='button';button.className='button';button.textContent=t('检查本地 Apple Pay 会话','Check local Apple Pay session');
          button.onclick=()=>{
            if(!active())return;
            if(walletPending){note(walletPending.closeUnknown?closingUnknown:waiting);return;}
            let apple;
            try {
              button.disabled=true;
              // This first-payment request labels the cycle; MIT/recurring authorization requires the future PSP contract.
              apple=new Apple(3,{countryCode:item.country_code,currencyCode:'CNY',supportedNetworks:['visa','masterCard'],merchantCapabilities:['supports3DS'],total:{label:`Sage TEST${item.billing_cycle?' · '+item.billing_cycle:''}`,amount,type:'final'}});
              const owned={channel:'apple_pay',session:apple,closeUnknown:false};walletPending=owned;
              const finish=text=>{if(walletPending!==owned)return;walletPending=null;walletCleanup=null;apple.onvalidatemerchant=apple.onpaymentauthorized=apple.oncancel=null;if(active())button.disabled=false;note(text);};
              const close=text=>{if(walletPending!==owned || owned.closeUnknown)return;apple.onvalidatemerchant=apple.onpaymentauthorized=null;try{apple.abort();finish(text);}catch(_){if(walletPending===owned){owned.closeUnknown=true;note(closingUnknown);}}};
              walletCleanup=()=>close(unavailable);
              const refuse=()=>{if(active())close(t('商户会话未配置，本地检查已终止；未收款或授权。','The merchant session is not configured. The local check was aborted; no payment or authorization.'));};
              apple.onvalidatemerchant=refuse;
              apple.onpaymentauthorized=refuse;
              apple.oncancel=()=>finish(t('Apple Pay 检查已取消；未收款或授权。','The Apple Pay check was canceled; no payment or authorization.'));
              apple.begin();
            }catch(_){if(apple && walletPending?.session===apple)walletCleanup?.();if(!walletPending && active())button.disabled=false;note(walletPending?.closeUnknown?closingUnknown:unavailable);}
          };
          buttons.append(button);note(t('仅检查本地请求与取消；商户会话及续费授权尚未配置。','Checks local requests and cancellation only. Merchant sessions and renewal authorization are not configured.'));
        }
      }
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
    window.addEventListener('pagehide',()=>{clearInterval(quoteTimer);clearPayment();clearWalletTests();},{once:true});
    document.addEventListener('visibilitychange',()=>{clearPayment();clearWalletTests();if(document.visibilityState==='visible')load();});
    purchaseRefresh=load;
    await load();
  }

  function account() {
    const sections = {overview:t('我的账单','My bills'),subscription:t('订阅管理','Subscription'),credits:'Sage Credits',orders:t('订单记录','Orders'),notifications:t('账务通知','Billing updates'),invoices:t('发票','Invoices'),'payment-methods':t('支付方式','Payment methods'),organization:t('企业管理','Business settings'),procurement:t('企业采购','Enterprise purchasing')};
    sections['credits-ledger'] = t('额度流水','Credits history');
    if (activeAccount()?.kind === 'business') sections['organization-history'] = t('企业变更记录','Business change history');

    const requested = location.hash.slice(1), selected = Object.hasOwn(sections, requested) ? requested : 'overview';
    const icons = {subscription:'calendar3',credits:'wallet2',orders:'receipt',notifications:'info-circle',invoices:'building','payment-methods':'credit-card',organization:'person',procurement:'building'};
    icons['credits-ledger'] = 'receipt';
    icons['organization-history'] = 'person';
    const policies = {
      subscription:t('取消自动续费不会退款或提前收回已付服务。Sage 先停止发起新扣款，再确认支付渠道解约；渠道确认前显示处理中。在途交易需另行核实，不代表已被撤回。新购、升级和支付方式变更尚未开放。','Cancelling automatic renewal does not refund or end paid service early. Sage stops initiating new charges, then confirms cancellation with the payment channel; the status remains processing until confirmed. In-flight transactions must be checked separately and are not automatically withdrawn. New purchases, upgrades and payment-method changes are not yet available.'),
      credits:t('订阅额度按服务月发放、当期有效。加购到账起 12 个月有效。Teams 公司任务先用本人企业套餐内含点数，再用企业共享点数，Enterprise 仅用企业共享点数；不扣员工私人点数。相同可用来源内优先使用最早到期批次。个人仅主动加购；企业自动充值默认关闭，需管理员另行授权。','Subscription Credits are granted by service month and expire at its end. Purchased Credits last 12 months from confirmed receipt. Teams company tasks use the member’s business plan allowance before shared business Credits; Enterprise uses shared business Credits only. Private Credits are never charged. Within each eligible source, earliest expiry comes first. Personal top-ups are manual; business auto top-up is off by default and requires separate admin authorization.'),
      orders:t('订单、支付确认与权益交付是不同状态。付款页面返回不等于支付成功，最终以服务端确认结果为准。','Orders, confirmed payments and benefit delivery have separate states. Returning from a payment page does not prove payment; server confirmation is authoritative.'),
      notifications:t('这里保留服务端确认的账务事件，不代表订单的最新状态。请进入对应订单或订阅管理核对后续结果。邮件、短信与扣款前提醒尚未接入。','This is a record of server-confirmed billing events, not an order’s latest status. Check the order or subscription for later results. Email, SMS and pre-charge reminders are not yet connected.'),
      invoices:t('从实付订单申请发票，财务审核开票后发送至指定邮箱。手机号登录也可填写独立收票邮箱。开票服务尚未接入。','Invoices are requested from paid orders, reviewed by finance and sent to a designated email, independently of phone sign-in. Invoice requests are not yet available.'),
      'payment-methods':t('订阅与企业自动充值分别授权。更换方式不重复收取当期费用；移除前先停止新扣款并查明在途交易。绑定、更换和移除暂不可用。','Renewal and business auto top-up have separate authorizations. Changing a method does not recharge the current term. Removal stops new charges and resolves in-flight payments first. Linking, changing and removal are not yet available.'),
      organization:t('Teams 1 个付费名额起购，Enterprise 10 个起购。Teams 每个付费名额独享月内含点数，企业加购点数共享；Enterprise 套餐月内含点数按购买名额汇入共享池，年付也逐月发放、当月到期。公司任务不扣员工私人点数，不设成员共享用量上限。仅管理的免费管理员和待分配成员没有付费使用权。移除成员立即撤销企业访问权限，保留个人账号及企业成果；余额不足时暂停任务，补充后手动继续。','Teams starts at 1 paid seat; Enterprise starts at 10. Teams provides an individual monthly allowance per paid seat; purchased business Credits are shared. Enterprise included monthly Credits are pooled by purchased seats, granted monthly even on annual plans and expire at the service month’s end. Company tasks never charge private Credits, and there are no per-member shared usage caps. Free management-only admins and members awaiting a seat have no paid access. Removal revokes business access immediately while retaining the personal account and business work. Tasks pause when Credits run out and continue manually after Credits are added.'),
      procurement:t('深圳主体签约与收款；合同明确席位、Sage Credits、服务范围、含税金额和服务期。申请不是订单，也不自动续费；财务确认实际到账后才开通，汇款回单不等于付款确认。','The Shenzhen company contracts and collects payment. Contracts define seats, Sage Credits, scope, tax-inclusive charges and term. An inquiry is not an order and does not renew automatically. Access follows verified bank receipt, not a transfer document alone.')
    };
    const version = ++accountVersion;
    policies['credits-ledger'] = t('这里展示实际记录的额度动作，不是当前余额或人民币账单。预占不代表消耗，释放预占不代表退款；到期余额请在 Sage Credits 查看。','These are recorded credit movements, not current balances or a currency bill. Reservations are not consumption and releases are not refunds. Check Sage Credits for expiring balances.');
    policies['organization-history'] = t('只读展示已记录的席位分配和成员共享上限变更，不代表当前配置。操作人标识已脱敏；缺失的历史版本显示未知，不视为不限制。此处不会修改成员、席位或额度。','This is a read-only record of seat assignments and member shared-limit changes, not the current configuration. Actor identifiers are masked. Missing historical versions are unknown, not unlimited. This page does not change members, seats or Credits.');
    document.title = t('我的账单 · Sage','My bills · Sage');
    shell(`<div class="account-layout billing-center"><aside class="billing-center-sidebar"><nav class="account-menu" aria-label="${t('个人设置导航','Personal settings navigation')}"><a href="${page('profile')}">${icon('person')}${t('个人信息','Personal information')}</a><a href="${page('profile')}#usage">${icon('bar-chart')}${t('用量明细','Usage details')}</a><a href="${page('billing')}" aria-current="page">${icon('receipt')}${t('我的账单','My bills')}</a><a href="${page('profile')}#organization">${icon('people')}${t('企业管理','Business management')}</a><a href="${page('profile')}#security">${icon('shield-lock')}${t('安全设置','Security')}</a></nav><details class="billing-management" ${selected !== 'overview' ? 'open' : ''}><summary>${t('账务管理','Billing management')}</summary><nav class="account-menu" aria-label="${t('账务管理导航','Billing management navigation')}">${Object.entries(sections).filter(([key]) => !['overview','organization','notifications','my-usage'].includes(key)).map(([key,label]) => `<a href="#${key}" ${selected === key ? 'aria-current="page"' : ''}>${icon(icons[key])}${label}</a>`).join('')}</nav></details></aside><section class="panel billing-center-content" aria-labelledby="account-section-title"><h1 id="account-section-title">${sections[selected]}</h1><div id="account-live" aria-live="polite"></div>${selected !== 'overview' ? `<div class="policy-block"><h3>${t('使用与办理说明','How it works')}</h3><p>${policies[selected]}</p>${selected === 'procurement' ? `<a class="button" href="${page('enterprise')}#contact">${t('查看企业服务与采购说明','View enterprise services and procurement')}</a>` : ''}</div>` : ''}${selected === 'credits' ? `<section id="credit-packs" aria-live="polite"></section>` : ''}</section></div>`);
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
      const data = await api('/api/billing/account');
      if (!current()) return;
      if (data.account?.id !== expectedAccount || typeof data.account.name !== 'string' || !['personal','business'].includes(data.account.kind) || !data.credits || !Array.isArray(data.orders)) throw new Error('INVALID_ACCOUNT');
      const prefix = `<p class="account-context">${escape(accountName(data.account))} · ${data.account.kind === 'personal' ? t('个人账户','Personal account') : t('企业账户','Business account')}</p>`;
      let content;
      if (selected === 'overview') {
        target.innerHTML = `<section class="billing-overview-card" aria-labelledby="billing-plan-title"><div id="billing-plan-summary"><h3 id="billing-plan-title">${t('当前订阅','Current plan')}</h3><p>${t('正在读取订阅…','Loading subscriptions…')}</p></div><div class="billing-plan-upgrade">${icon('lock')}<p>${t('查看适合你的订阅计划与 Sage Credits。','Explore subscription plans and Sage Credits for your needs.')}</p><a class="button primary" href="${page('pricing')}">${t('升级','Upgrade')}</a></div></section><section class="billing-overview-card billing-manage-card"><div><h3>${t('管理账单信息','Manage billing information')}</h3><p>${t('管理你的订阅、付款方式与账单信息。','Manage your subscriptions, payment methods and billing information.')}</p></div><a class="button primary" href="#subscription">${t('管理订阅','Manage subscription')}</a></section><section class="billing-overview-card billing-orders-card"><div id="billing-overview-orders"></div></section>`;
        const summary = target.querySelector('#billing-plan-summary');
        const readPlan = async () => {
          summary.innerHTML = `<h3 id="billing-plan-title">${t('当前订阅','Current plan')}</h3><p>${t('正在读取订阅…','Loading subscriptions…')}</p>`;
          try {
            const subscriptions = await api('/api/billing/subscriptions');
            if (!current()) return;
            if (!Array.isArray(subscriptions.items) || !subscriptions.items.every(validSubscription)) throw new Error('INVALID_SUBSCRIPTIONS');
            const items = subscriptions.items.filter(item => item.status !== 'expired');
            summary.innerHTML = items.length ? items.map(item => `<div class="billing-plan-item"><h3 ${item === items[0] ? 'id="billing-plan-title"' : ''}>${escape(({pro:'Pro',pro_plus:'Pro+',ultra:'Ultra',teams:'Teams',enterprise:'Enterprise'})[item.plan_code] || item.plan_code)}</h3><p>${item.billing_cycle === 'annual' ? t('按年订阅','Annual subscription') : t('按月订阅','Monthly subscription')} · ${item.status === 'scheduled' ? t('待生效','Scheduled') : statusLabel(item.status)}</p><p>${t('金额以订单记录为准','See orders for the confirmed amount')}</p></div>`).join('') : `<h3 id="billing-plan-title">${t('未订阅','No subscription')}</h3><p>${t('当前账户没有生效或待生效的付费订阅。','This account has no active or scheduled paid subscription.')}</p><a class="text-link" href="${page('trial')}">${t('免费体验','Free trial')}</a>`;
          } catch (error) {
            if (!current()) return;
            summary.innerHTML = `<h3 id="billing-plan-title">${t('当前订阅','Current plan')}</h3><p class="error" role="status">${requestError(error,t('订阅读取失败，当前方案未知。','Subscriptions could not be loaded. Your current plan is unknown.'))}</p><button type="button" class="text-link" data-plan-retry>${t('重新读取','Retry')}</button>`;
            summary.querySelector('[data-plan-retry]').onclick = readPlan;
          }
        };
        await Promise.all([readPlan(),renderOrders(target.querySelector('#billing-overview-orders'), '', current, expectedAccount, version, 0, true)]);
        return;
      } else if (selected === 'credits') {
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
        await renderInvoices(target,current,expectedAccount,data.orders);return;
      } else if (selected === 'payment-methods') {
        content = data.capabilities?.payment_methods !== true ? unavailableModule() : emptyText(t('支付方式管理尚未开放操作。','Payment-method management is not yet available.'));
      } else if (selected === 'organization') {
        content = emptyText(data.account.kind === 'personal' ? t('当前是个人账户。已有企业账户可在右上角切换。','This is a personal account. Switch to an existing business account from the top-right menu.') : t('请在个人设置的“企业管理”查看成员与用量。成员移除是否可用，以当前权限和成员状态为准。','View members and usage under Business management in Personal settings. Member removal depends on your current permissions and the member’s status.'));
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
            packs.innerHTML = `<h3>${t('Sage Credits 一次性加购','One-time Sage Credits purchase')}</h3><p class="checkout-note">${t('加购额度自确认到账起 12 个月有效。查看报价或创建订单不会立即收款，也不会开启自动充值。','Purchased credits last 12 months from confirmed receipt. Viewing a quote or creating an order does not charge you or enable auto top-up.')}</p>` + (available.length ? `<ul class="record-list">${available.map(item => `<li><strong>${escape(item.credits_quantity)} Sage Credits</strong><span>${money(item.unit_amount_minor,true)}</span><a class="button" data-credit-purchase href="${page('checkout')}?${new URLSearchParams({product:item.id,seats:'1'})}">${t('查看加购报价','Review purchase quote')}</a></li>`).join('')}</ul>` : `<p data-credit-packs-empty>${items.some(item=>item.unavailable_reason==='CREDITS_SUBSCRIPTION_REQUIRED')?creditsSubscriptionRequired:t('当前账户暂无可报价的额度包。可能尚未开放加购，或当前订阅不满足购买条件；不能付款。','No credit packs can be quoted for this account. Purchases may not be enabled or your subscription may not be eligible; no payment is available.')}</p>`);
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
        if (!item || !['seat_assignment','shared_limit','member_removal','member_role'].includes(item.type) ||
            typeof item.id !== 'string' || !(item.type === 'seat_assignment' ? /^seat:[1-9]\d*$/.test(item.id) : item.type === 'member_removal' ? /^removal:[1-9]\d*$/.test(item.id) : item.type === 'member_role' ? /^role:[1-9]\d*$/.test(item.id) : /^limit:[1-9]\d*$/.test(item.id)) || ids.has(item.id) ||
            typeof item.created_at !== 'string' || !Number.isFinite(Date.parse(item.created_at)) ||
            typeof item.actor_masked_identifier !== 'string' || (item.type !== 'member_removal' && !positive(item.version))) throw new Error('INVALID_ORGANIZATION_CHANGE');
        if (item.type === 'seat_assignment') {
          if (typeof item.seat_id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(item.seat_id) ||
              !positive(item.seat_position) || !Number.isSafeInteger(item.before_version) || item.before_version < 0 || item.version !== item.before_version+1 ||
              [item.before_membership_id,item.after_membership_id].some(value=>value !== null && !positive(value))) throw new Error('INVALID_SEAT_CHANGE');
        } else if(item.type === 'member_role'){if(!positive(item.membership_id)||!['member','billing_admin'].includes(item.before_role)||!['member','billing_admin'].includes(item.after_role)||!Number.isSafeInteger(item.before_version)||item.before_version<0||item.version!==item.before_version+1)throw Error('INVALID_MEMBER_ROLE_CHANGE');}
        else if(item.type === 'member_removal') {if(!positive(item.membership_id)||typeof item.handoff_pending!=='boolean'||!Number.isSafeInteger(item.pending_task_count)||item.pending_task_count<0||item.executor_connected!==false)throw Error('INVALID_REMOVAL_HISTORY');}
        else if (!positive(item.membership_id) || !quantity(item.quantity) || typeof item.previous_known !== 'boolean' ||
                   !quantity(item.previous_quantity) || (!item.previous_known && item.previous_quantity !== null)) throw new Error('INVALID_SHARED_LIMIT_CHANGE');
        ids.add(item.id);
      }
      target.innerHTML = `<h3 class="records-title" id="organization-history-title" tabindex="-1">${t('企业成员与名额历史','Business member and seat history')}</h3>` +
        (data.items.length ? `<ul class="record-list">${data.items.map(item=>{
          if(item.type==='member_role'){const role=value=>value==='billing_admin'?t('企业管理员','Business administrator'):t('普通员工','Employee');return `<li class="billing-notification" data-organization-change="${escape(item.id)}"><div><strong>${t('成员','Member')} #${item.membership_id} · ${t('管理员权限变更','Administrator permission change')}</strong><p>${t('变更前','Before')}：${role(item.before_role)}<br>${t('变更后','After')}：${role(item.after_role)}</p><small>${t('版本','Version')} ${item.before_version} → ${item.version} · ${dateLabel(item.created_at)}</small><p>${t('操作人','Changed by')}：${escape(item.actor_masked_identifier||unknown())}</p></div></li>`;}
          if(item.type==='member_removal')return `<li class="billing-notification" data-organization-change="${escape(item.id)}"><div><strong>${t('成员','Member')} #${item.membership_id} · ${t('企业权限已撤销','Business access revoked')}</strong><p>${item.handoff_pending?t('工作交接待处理；真实任务执行器尚未接通。','Work handoff pending; no live task executor is connected.'):t('个人账号与企业共有成果保留。','Personal account and shared business work retained.')}</p><small>${dateLabel(item.created_at)}</small><p>${t('操作人','Changed by')}：${escape(item.actor_masked_identifier||unknown())}</p></div></li>`;
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
      seats_purchased:[t('新增使用名额已开通','Additional seats activated'),t('这笔订单新增的名额已在本期生效，原到期日不变。请查看原订单及企业当前名额。','The additional seats from this order are active for the current term, with the original end date unchanged. Check the original order and current business seats.')],
      next_seats_purchased:[t('下一期新增名额已购买','Additional next-term seats purchased'),t('新增名额将在已付的下一期开始时生效，本期名额和点数不变。请查看原订单和企业名额。','The additional seats take effect when the prepaid next term starts. Current-term seats and Credits are unchanged. Check the original order and business seats.')],
      seats_reduced:[t('本期购买名额已减少','Current-term seats reduced'),t('本期购买名额已调整，已付下一期仍保留原购买数量。如有退款，请查看退款处理结果；这条通知不代表钱已退回。','Current-term capacity has changed; the prepaid next term keeps its purchased quantity. Check the refund status if applicable; this notice does not confirm money has been returned.')],
      member_role_changed:[t('成员权限已变更','Member permissions changed'),t('成员的企业管理权限已调整。请查看企业变更记录和当前权限；此操作不会增加购买名额或产生扣款。','The member’s business permissions have changed. Check organization history and current permissions; this action does not buy seats or create a charge.')],
      member_removed:[t('企业成员已移除','Business member removed'),t('该成员已失去企业访问权限，已有企业成果保留。请查看企业变更记录；移除成员不等于退还名额费。','The member no longer has business access; existing business work is retained. Check organization history. Removing a member does not refund seat fees.')],
      renewal_reminder:[t('订阅将在一天后续费','Subscription renews in one day'),t('此金额是本次续费预计扣款金额，不代表已经扣款。请在订阅页核对续费日期或关闭自动续费。','This is the expected renewal charge, not a completed payment. Check the renewal date or cancel automatic renewal on the subscription page.')],
      renewal_notice_blocked:[t('续费提醒未能发送','Renewal reminder could not be sent'),t('本次提醒未确认发送成功，自动扣款暂停。已付款服务保留至原到期日，请查看订阅状态，需要时主动续费。','Delivery of this reminder was not confirmed, so the automatic charge is paused. Paid service remains valid until its original end date. Check subscription status and renew manually if needed.')],
      invoice_requested:[t('发票申请已记录','Invoice request recorded'),t('开票申请已受理，尚不代表发票已开具或发送。请在发票页查看办理状态。','The invoice request has been recorded. This does not confirm that an invoice was issued or sent. Check its status on the invoices page.')],
      invoice_sent:[t('发票已发送','Invoice sent'),t('发票已发送至申请时填写的邮箱。请核对收件箱和发票记录；此事件不会再次扣款。','The invoice was sent to the email address supplied in the request. Check your inbox and invoice record; this event does not create another charge.')],
      account_security:[t('账号安全变更','Account security change'),t('账号发生了安全相关变更。请进入个人设置核对登录设备和安全设置，如非本人操作请及时处理。','A security-related account change was recorded. Check your sessions and security settings in your profile and act if you did not make this change.')],
      plan_changed:[t('套餐已变更','Plan changed'),t('新套餐已开通。显示金额为此次变更订单金额，请查看原订单及订阅的当前期限和点数；退款另行核对。','The new plan has been activated. The displayed amount belongs to this change order. Check the original order, current term and Credits; verify any refund separately.')],
      service_refund_requested:[t('订阅退款申请已记录','Subscription refund request recorded'),t('订阅退款申请已记录，尚未确认钱已退回。请查看订阅和退款处理状态。','The subscription refund request was recorded; no return of money is confirmed. Check the subscription and refund status.')],
      service_refund_refunded:[t('订阅退款已确认','Subscription refund confirmed'),t('已收到订阅退款成功确认，实际到账时间以支付渠道及银行为准。请查看退款记录核对金额。','Subscription refund success has been confirmed. Arrival depends on the payment channel and bank. Check the refund record for the amount.')],
      service_refund_failed:[t('订阅退款需要核对','Subscription refund requires review'),t('退款结果需要进一步核对，不能据此认定退款成功或重新申请退款。请查看原退款处理状态。','The refund outcome needs verification. Do not assume success or submit another refund from this notice. Check the original refund status.')],
      seat_capacity_ending:[t('本期新增名额即将到期','Additional seats expire soon'),t('这些新增名额未包含在已付的下一期内，到期后暂停使用付费功能，成员和已有成果保留。需要继续使用时，请管理员另行购买下一期名额。','These additional seats are not included in the prepaid next term. Paid access pauses when they expire; members and existing work are retained. Ask an administrator to purchase next-term seats if needed.')],
      topup_authorization_recorded:[t('自动充值授权已记录','Top-up authorization recorded'),t('此记录不代表已充值或已扣款。请核对授权与账务状态。','This record does not confirm a top-up or a charge. Check authorization and billing status.')],
      topup_authorization_paused:[t('自动充值已在本地暂停','Automatic top-up paused locally'),t('系统已暂停发起新的自动充值；这不代表支付渠道授权已撤销，也不改变在途交易的结果。','New automatic top-ups are paused locally. Channel authorization is not necessarily revoked and in-flight transactions are unchanged.')],
      topup_revocation_processing:[t('充值授权撤销处理中','Top-up revocation processing'),t('已在本地停用自动充值，支付渠道尚未确认授权撤销。','Automatic top-up is disabled locally; channel revocation has not yet been confirmed.')],
      topup_revocation_confirmed:[t('充值授权撤销已确认','Top-up revocation confirmed'),t('支付渠道已确认撤销此充值授权；既有充值与在途交易仍以各自结果为准。','The channel has confirmed revocation of this authorization. Existing top-ups and in-flight payments retain their own outcomes.')],
      seat_assignment_changed:[t('席位配置已记录','Seat configuration recorded'),t('席位分配配置已生成新的版本记录。请核对企业当前席位；此记录不代表已向成员发送通知。','A new seat assignment version has been recorded. Check current organization seats; this does not confirm delivery of a notice to members.')],
      shared_limit_changed:[t('历史共享额度设置记录','Historical shared Credits settings'),t('这是旧版共享额度设置的历史记录。当前企业额度共同使用，没有成员月上限；此记录不会产生新的 Credits 或扣款。','This is a historical record of earlier shared Credits settings. Business Credits are now shared without member monthly caps; this record creates no new Credits or charge.')],
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
        const monetary = ['payment_received','renewal_payment_received','topup_payment_received','seats_purchased','next_seats_purchased','seats_reduced','renewal_reminder','plan_changed','service_refund_requested','service_refund_refunded','credit_refund_reserved','credit_refund_refunded','credit_refund_released'].includes(item.type) || (item.type === 'service_refund_failed' && (item.currency !== null || item.amount_minor !== null));
        const zeroAllowed = ['seats_purchased','next_seats_purchased','seats_reduced','plan_changed','service_refund_failed'].includes(item.type);
        if (!validId(item.id) || ids.has(item.id) || !Object.hasOwn(messages,item.type) ||
            typeof item.occurred_at !== 'string' || !Number.isFinite(Date.parse(item.occurred_at)) ||
            ['order_id','subscription_id','refund_id'].some(key=>item[key] !== null && !validId(item[key])) ||
            (monetary ? item.currency !== 'CNY' || typeof item.amount_minor !== 'string' || !/^\d+$/.test(item.amount_minor) || (!zeroAllowed && BigInt(item.amount_minor) === 0n) : item.currency !== null || item.amount_minor !== null)) throw new Error('INVALID_NOTIFICATION');
        ids.add(item.id);
      }
      target.innerHTML = `<h3 class="records-title" id="notifications-title" tabindex="-1">${t('账务事件记录','Billing event history')}</h3>` +
        (data.items.length ? `<ul class="record-list">${data.items.map(item=>{
          const [title,note] = messages[item.type];
          const link = item.type === 'unknown' ? '' : (item.type.startsWith('cancellation_') || ['renewal_reminder','renewal_notice_blocked','plan_changed','service_refund_requested','service_refund_refunded','service_refund_failed'].includes(item.type)) ? `<a class="text-link" href="#subscription">${t('查看订阅','View subscription')}</a>` :
            ['seat_assignment_changed','shared_limit_changed','member_role_changed','member_removed'].includes(item.type) ? `<a class="text-link" href="#organization-history">${t('查看企业变更记录','View organization history')}</a>` :
            item.type.startsWith('topup_') && !item.order_id ? `<a class="text-link" href="#credits">${t('查看充值授权','View top-up authorization')}</a>` :
            ['invoice_requested','invoice_sent'].includes(item.type) ? `<a class="text-link" href="#invoices">${t('查看发票','View invoices')}</a>` :
            item.type === 'account_security' ? `<a class="text-link" href="${page('profile')}#security">${t('查看安全设置','View security settings')}</a>` :
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

  async function renderOrders(target, prefix, current, accountId, version, offset = 0, overview = false) {
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
      const kind = order => ['subscription','credits'].includes(order.snapshot?.kind) ? order.snapshot.kind : 'unknown';
      const kindLabel = value => ({subscription:t('订阅','Subscription'),credits:t('资源包','Credit pack'),unknown:unknown()})[value];
      const products = [...new Set(data.items.map(order => order.plan_code || ''))].filter(Boolean);
      const kinds = [...new Set(data.items.map(kind))];
      const table = `<div class="billing-orders-table-wrap"><table class="billing-orders-table"><thead><tr><th scope="col">${t('日期','Date')}</th><th scope="col">${t('金额','Amount')}</th><th scope="col">${t('状态','Status')}</th><th scope="col"><label>${t('商品','Product')}<select data-order-product aria-label="${t('筛选当前页商品','Filter products on this page')}"><option value="">${t('全部','All')}</option>${products.map(value => `<option value="${escape(value)}">${escape(value)}</option>`).join('')}</select></label></th><th scope="col"><label>${t('订单类型','Order type')}<select data-order-kind aria-label="${t('筛选当前页订单类型','Filter order types on this page')}"><option value="">${t('全部','All')}</option>${kinds.map(value => `<option value="${value}">${kindLabel(value)}</option>`).join('')}</select></label></th><th scope="col"><span class="sr-only">${t('操作','Actions')}</span></th></tr></thead><tbody></tbody></table></div><p class="billing-filter-scope">${t('筛选范围：当前页订单','Filters apply to orders on the current page')}</p>`;
      target.innerHTML = prefix + `<h3 class="records-title" id="orders-list-title" tabindex="-1">${overview ? t('订单','Orders') : t('付款与订单记录','Payments and orders')}</h3>` + (overview ? table : data.items.length ? `<ul class="record-list">${data.items.map(order => `<li><span>${escape(order.plan_code || t('订单','Order'))}</span><strong>${money(order.amount_minor, true)}</strong><span>${statusLabel(order.status)}</span><small>${dateLabel(order.created_at)}</small><button class="text-link" data-order="${escape(order.id)}">${t('查看详情','View details')}</button></li>`).join('')}</ul>` : emptyText(offset ? t('此页没有订单记录，请返回上一页。','There are no orders on this page. Return to the previous page.') : t('当前账户还没有订单。','This account has no orders.'))) + `<div class="organization-pagination">${offset ? `<button class="text-link" data-orders-prev>${t('上一页','Previous')}</button>` : ''}${data.next_offset !== null ? `<button class="text-link" data-orders-next>${t('下一页','Next')}</button>` : ''}<button class="text-link" data-orders-refresh>${t('刷新订单','Refresh orders')}</button></div><div id="order-detail" tabindex="-1"></div>`;
      if (overview) {
        const rows = () => {
          const product = target.querySelector('[data-order-product]').value, type = target.querySelector('[data-order-kind]').value;
          const items = data.items.filter(order => (!product || order.plan_code === product) && (!type || kind(order) === type));
          target.querySelector('tbody').innerHTML = items.length ? items.map(order => `<tr><td>${dateLabel(order.created_at)}</td><td>${money(order.amount_minor,true)}</td><td>${statusLabel(order.status)}</td><td>${escape(order.plan_code || unknown())}</td><td>${kindLabel(kind(order))}</td><td><button type="button" class="text-link" data-order="${escape(order.id)}">${t('查看详情','View details')}</button></td></tr>`).join('') : `<tr><td colspan="6" class="billing-orders-empty">${data.items.length ? t('没有符合筛选条件的订单。','No orders match these filters.') : t('未找到账单。','No bills found.')}</td></tr>`;
          for (const button of target.querySelectorAll('[data-order]')) button.onclick = () => loadOrder(button.dataset.order,accountId,version);
        };
        target.querySelectorAll('select').forEach(select => select.onchange = rows);
        rows();
      }
      const navigate = async next => {
        if (!latest()) return;
        await renderOrders(target, prefix, current, accountId, version, next, overview);
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
      target.querySelector('[data-orders-retry]').addEventListener('click', () => renderOrders(target,prefix,current,accountId,version,offset,overview));
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
      target.innerHTML = prefix + `<h3 class="records-title">${t('成员与席位','Members and seats')}</h3><dl class="account-metrics"><div><dt>${t('购买席位','Purchased seats')}</dt><dd>${data.seat_count}</dd></div><div><dt>${t('已分配','Assigned')}</dt><dd>${data.assigned_seats}</dd></div><div><dt>${t('成员总数','Total members')}</dt><dd>${data.total_members}</dd></div></dl><p class="account-context">${data.subscription_active ? `${t('当前额度周期','Current credit period')}：${dateLabel(data.period_start)} – ${dateLabel(data.period_end)}` : t('当前没有生效中的订阅，本期共享用量暂不计算；“—”不表示零。','There is no active subscription. Current-period shared usage is unavailable; “—” does not mean zero.')}</p><p class="account-context">${t('当前为只读视图。席位分配与上限修改尚未开放，不会在此产生变更。','This view is read-only. Seat assignments and limit changes are not available here.')}</p>${data.members.length ? `<ul class="record-list organization-members">${data.members.map(item => `<li><strong>${escape(item.masked_identifier || `${t('成员','Member')} #${item.id}`)}</strong><span>${escape(roles[item.role] || unknown())} · ${item.active ? t('有效成员','Active member') : t('已停用','Inactive')}</span><span>${item.seat_position === null ? t('未分配席位','No seat assigned') : `${t('席位','Seat')} ${escape(item.seat_position)}`}</span><small>${t('累计使用点数','Total Credits used')}：${item.total_credits===undefined?unknown():quantity(item.total_credits)}</small><small>${t('共享已用','Shared used')}：${quantity(item.shared_used)} · ${t('共享预占','Shared reserved')}：${quantity(item.shared_reserved)}</small></li>`).join('')}</ul>` : emptyText(t('此页没有成员记录。','There are no members on this page.'))}<div class="organization-pagination">${offset > 0 ? `<button class="text-link" data-org-offset="${Math.max(0,offset-50)}">${t('上一页','Previous')}</button>` : ''}${data.next_offset !== null && Number.isInteger(data.next_offset) ? `<button class="text-link" data-org-offset="${data.next_offset}">${t('下一页','Next')}</button>` : ''}<a class="text-link" href="#credits">${t('查看 Sage Credits 批次','View Sage Credits batches')}</a></div>`;
      const policy=target.querySelectorAll('.account-context')[1];
      if(policy){policy.textContent=t('Teams套餐点数按名额独享，Enterprise套餐点数由企业共享；企业加购点数共享，不设置成员用量上限。名额和管理员权限在企业管理中操作。','Teams plan Credits belong to individual seats; Enterprise plan Credits are shared. Purchased business Credits are shared, without per-member caps. Manage seats and administrator permissions in Business management.');const manage=document.createElement('a');manage.className='button';manage.href=page('profile')+'#organization';manage.textContent=t('打开企业管理','Open Business management');manage.dataset.businessManagement='';policy.after(manage);}
      for(const [index,item] of data.members.entries()){
        if(data.capabilities?.member_removal===true&&item.can_remove===true&&item.active&&item.role!=='owner'){
          const action=document.createElement('button');action.type='button';action.className='button';action.textContent=t('移除成员','Remove member');action.addEventListener('click',()=>removeMember(item,action));target.querySelectorAll('.organization-members>li')[index].append(action);
        }
      }
      function removeMember(item,trigger){
        const dialog=document.createElement('dialog');dialog.className='member-removal-dialog';dialog.setAttribute('aria-labelledby','member-removal-title');
        dialog.innerHTML=`<h2 id="member-removal-title">${t('移除企业成员','Remove business member')}</h2><p><strong>${escape(item.masked_identifier)}</strong></p><p>${t('确认后立即撤销此成员的企业访问权限。个人账号、其他企业身份及本企业共有成果不会删除。','Confirmation immediately revokes this member’s business access. Their personal account, other memberships and shared business work are not deleted.')}</p><p>${t('席位和额度按实际记录处理。运行中工作交接将留下待办记录；真实任务执行器尚未接通，不表示任务已完成交接。','Seats and Credits follow recorded state. Active work receives a pending handoff record. The task executor is not connected; this does not confirm completed task handoff.')}</p><label><input type="checkbox" data-remove-consent> ${t('我确认立即撤销该成员的企业权限，并保留其个人账号与企业共有成果。','I confirm immediate business-access revocation while preserving their personal account and shared business work.')}</label><p data-remove-feedback role="status"></p><div class="member-removal-actions"><button type="button" class="button" data-remove-cancel>${t('取消','Cancel')}</button><button type="button" class="button primary" data-remove-confirm disabled>${t('确认移除','Confirm removal')}</button></div>`;
        const consent=dialog.querySelector('[data-remove-consent]'),confirm=dialog.querySelector('[data-remove-confirm]'),cancel=dialog.querySelector('[data-remove-cancel]'),feedback=dialog.querySelector('[data-remove-feedback]');
        const key=crypto.randomUUID(),previousOverflow=document.body.style.overflow;let busy=false,closed=false;
        function close(){if(closed)return;closed=true;dialog.close();dialog.remove();if(closeMemberDialog===close){document.body.style.overflow=previousOverflow;closeMemberDialog=null;}if(trigger.isConnected)trigger.focus();}closeMemberDialog=close;
        consent.addEventListener('change',()=>confirm.disabled=!consent.checked||busy);cancel.addEventListener('click',close);
        dialog.addEventListener('cancel',event=>{event.preventDefault();if(!busy)close();});dialog.addEventListener('click',event=>{if(event.target===dialog&&!busy){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)close();}});
        confirm.addEventListener('click',async()=>{
          if(busy||!consent.checked)return;if(!current()||session?.active_account_id!==accountId){close();return;}busy=true;confirm.disabled=true;cancel.disabled=true;consent.disabled=true;
          try{const result=await api('/api/billing/organization/members/remove',{membership_id:item.id,request_key:key,confirm:true},session.csrf_token,{'X-Billing-Account':accountId});if(!current()){close();return;}if(result.removed!==true||result.membership_id!==item.id||typeof result.removal_id!=='string'||result.executor_connected!==false||typeof result.handoff_pending!=='boolean')throw Error('INVALID_REMOVAL');
            close();await renderOrganization(target,prefix,current,accountId,offset);if(current()){const note=document.createElement('p');note.setAttribute('role','status');note.className='account-context';note.textContent=result.handoff_pending?t('成员企业权限已撤销。工作交接已记录为待处理，未连接真实任务执行器。','Business access revoked. Work handoff is recorded as pending; no live executor is connected.'):t('成员企业权限已撤销，个人账号与企业共有成果保留。','Business access revoked. Personal account and shared business work retained.');target.prepend(note);note.tabIndex=-1;note.focus();}
          }catch(error){if(!current()){close();return;}feedback.textContent=requestError(error,t('移除结果未能确认。请刷新成员状态，或使用本弹窗重试；不要假定停权已完成。','Removal could not be confirmed. Refresh member status or retry in this dialog; do not assume access has been revoked.'));}
          finally{busy=false;if(dialog.isConnected){confirm.disabled=!consent.checked;cancel.disabled=false;consent.disabled=false;}}
        });document.body.append(dialog);document.body.style.overflow='hidden';dialog.showModal();cancel.focus();
      }
      for (const button of target.querySelectorAll('[data-org-offset]')) button.addEventListener('click', () => renderOrganization(target,prefix,current,accountId,Number(button.dataset.orgOffset)));
    } catch (error) {
      if (!current() || target.organizationRequest !== request) return;
      target.innerHTML = prefix + `<p class="error">${requestError(error,t('成员数据读取失败，请重试。','Members could not be loaded. Please retry.'))}</p><button class="text-link" data-org-retry>${t('重试','Retry')}</button>`;
      target.querySelector('[data-org-retry]').addEventListener('click', () => renderOrganization(target,prefix,current,accountId,offset));
    }
  }
  async function renderInvoices(target,current,accountId,orders){
    target.innerHTML=emptyText(t('正在读取发票…','Loading invoices…'));
    try{
      const data=await api('/api/account/invoices?'+new URLSearchParams({account_id:accountId}));if(!current())return;
      if(!Array.isArray(data.items)||typeof data.available!=='boolean')throw Error('INVALID_INVOICES');
      const states={requested:t('已申请','Requested'),reviewing:t('审核中','Under review'),issued:t('已开具','Issued'),sent:t('已发送','Sent'),failed:t('需要处理','Needs attention')};
      for(const item of data.items)if(typeof item.id!=='string'||typeof item.title!=='string'||typeof item.recipient_email!=='string'||!Object.hasOwn(states,item.status))throw Error('INVALID_INVOICE');
      target.innerHTML=`<h3>${t('发票记录','Invoices')}</h3>${data.items.length?`<ul class="record-list">${data.items.map(item=>`<li><strong>${escape(item.title)}</strong><span>${states[item.status]}</span><small>${escape(item.recipient_email)}</small><small>${dateLabel(item.created_at)}</small></li>`).join('')}</ul>`:emptyText(t('暂无发票申请。','No invoice requests.'))}<p>${t('收票邮箱仅用于发送发票，不修改或绑定登录邮箱。申请成功不代表已开具或已发送。','The invoice recipient email is independent of your sign-in email. An accepted request does not mean an invoice was issued or sent.')}</p>`;
      const paid=orders.filter(item=>item.status==='fulfilled');
      if(!data.available||!paid.length){target.insertAdjacentHTML('beforeend',`<button type="button" class="button" disabled>${t('申请发票','Request invoice')}</button><p>${data.available?t('当前没有可申请的已付款订单。','No eligible paid orders are available.'):t('开票申请服务尚未就绪，请稍后再试。','Invoice requests are not available yet. Retry later.')}</p>`);return;}
      const form=document.createElement('form');form.className='procurement-form';form.method='dialog';form.innerHTML=`<label>${t('已付款订单','Paid order')}<select name="order_id">${paid.map(item=>`<option value="${escape(item.id)}">${escape(item.plan_code||t('订单','Order'))} · ${money(item.amount_minor,true)} · ${escape(item.id)}</option>`).join('')}</select></label><label>${t('发票抬头','Invoice title')}<input name="title" maxlength="200" required autocomplete="organization"></label><label>${t('税号','Tax number')}<input name="tax_number" maxlength="80" required></label><label>${t('收票邮箱','Invoice recipient email')}<input name="recipient_email" type="email" required autocomplete="email" maxlength="254"></label><button type="submit" class="button primary">${t('提交发票申请','Submit invoice request')}</button><p role="status"></p>`;target.append(form);
      let busy=false,key=null,submitted=null;form.onsubmit=async event=>{event.preventDefault();if(busy||!current()||!form.reportValidity())return;const payload=Object.fromEntries(new FormData(form)),serialized=JSON.stringify(payload);if(serialized!==submitted){submitted=serialized;key=crypto.randomUUID();}const submit=form.querySelector('[type=submit]'),note=form.querySelector('[role=status]');busy=true;submit.disabled=true;
        try{if(activeAccount()?.kind==='business')await window.sageConfirmSensitive(accountId,session.csrf_token);if(!current())return;const saved=await api('/api/account/invoices',{...payload,request_key:key});if(!current())return;if(typeof saved.id!=='string'||saved.status!=='requested')throw Error('INVOICE_UNCONFIRMED');await renderInvoices(target,current,accountId,orders);}
        catch(error){if(current())note.textContent=requestError(error,t('申请结果未确认，请刷新记录核对或以相同内容重试；不会重复申请。','Request status is unconfirmed. Refresh records or retry the same details; the request will not be duplicated.'));}
        finally{busy=false;if(submit.isConnected)submit.disabled=false;}};
    }catch(error){if(current()){target.innerHTML=`<p class="error" role="status">${requestError(error,t('发票状态读取失败，当前状态未知。','Invoices could not be loaded; their status is unknown.'))}</p><button class="button" data-invoices-refresh>${t('重新读取','Retry')}</button>`;target.querySelector('button').onclick=()=>renderInvoices(target,current,accountId,orders);}}
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
        (a ? `<p>${states[a.state]}</p><dl class="account-metrics"><div><dt>${t('共享余额触发线','Shared balance threshold')}</dt><dd>${escape(a.threshold)}</dd></div><div><dt>${t('固定购买包','Fixed pack')}</dt><dd>${escape(a.pack_credits)} Sage Credits</dd></div><div><dt>${t('每次金额','Per top-up')}</dt><dd>${money(a.pack_amount_minor,true)}</dd></div><div><dt>${t('本服务月金额上限','Service-month spending cap')}</dt><dd>${money(a.monthly_cap_minor,true)}</dd></div></dl>` : emptyText(t('尚未记录独立充值授权。订阅续费授权不包含自动充值。','No separate top-up authorization is recorded. A renewal authorization does not include auto top-up.'))) +
        (b ? `<h4>${t('已记录预算窗口','Recorded budget window')}</h4><p>${dateLabel(b.starts_at)} — ${dateLabel(b.ends_at)}</p><dl class="account-metrics"><div><dt>${t('已用金额','Spent')}</dt><dd>${money(b.spent_minor,true)}</dd></div><div><dt>${t('在途预留','Pending reservation')}</dt><dd>${money(b.reserved_minor,true)}</dd></div><div><dt>${t('预算余量','Budget remaining')}</dt><dd>${b.remaining_minor === null ? '—' : money(b.remaining_minor,true)}</dd></div></dl>${b.needs_review ? `<p class="notice">${t('存在待核对交易，预算余量未知。查明原交易前不会开启下一笔充值。','A payment requires review, so the remaining budget is unknown. No further top-up may begin until it is resolved.')}</p>` : ''}` : emptyText(t('尚未建立当前预算窗口，不能据此判断本月已用金额为零。','No current budget window has been established. This does not mean monthly spending is zero.'))) +
        `<p class="account-context">${t('自动充值默认关闭，采用固定方案。只有任务需要使用企业共享点数，且共享可用余额低于触发线时才充值；本人套餐点数与私人点数不计入该余额。每次只购买一包，受本服务月金额上限约束。订阅费用单独计算，暂停或撤销不会清除历史支出。','Auto top-up is off by default and uses fixed presets. It triggers only when a task needs shared business Credits and the available shared balance is below the threshold. Individual plan and private Credits are excluded from this balance. Each trigger purchases one pack within the service-month spending cap. Subscription fees are separate; pausing or revoking authorization does not erase past spending.')}</p><button class="text-link" data-topup-refresh>${t('刷新状态','Refresh status')}</button>`;
      target.querySelector('[data-topup-refresh]').addEventListener('click',()=>renderAutomaticTopup(target,current,accountId));
      if(a&&['revoking','revoked'].includes(a.state)){
        const stopped=document.createElement('p');stopped.setAttribute('role','status');stopped.textContent=a.state==='revoking'?t('已停止新的自动充值扣款。渠道撤销仍待确认；原有在途付款仍需核对。','New auto top-up charges are stopped. Channel revocation is awaiting confirmation; existing in-flight payments still need checking.'):t('自动充值已关闭，渠道撤销已确认；原有在途付款仍需核对。','Auto top-up is off and channel revocation is confirmed. Existing in-flight payments still need checking.');target.append(stopped);
      }
      if(a&&typeof a.id==='string'&&/^[0-9a-f-]{36}$/i.test(a.id)&&['authorized','paused','superseded'].includes(a.state)&&['owner','billing_admin'].includes(activeAccount()?.role)){
        const controls=document.createElement('div');controls.innerHTML=`<button type="button" class="button" data-topup-disable aria-expanded="false">${t('关闭自动充值','Turn off auto top-up')}</button><section class="policy-block" data-topup-stop-confirm hidden><h4>${t('确认关闭自动充值？','Turn off auto top-up?')}</h4><p>${t('关闭后先停止新的自动充值扣款。支付渠道撤销仍需确认，原有在途付款继续核对，不会清除历史支出。','Turning off first stops new auto top-up charges. Channel revocation still needs confirmation; existing in-flight payments still need checking, and past spending is retained.')}</p><div class="action-row"><button type="button" class="button primary" data-topup-stop-submit>${t('确认关闭自动充值','Confirm turning off auto top-up')}</button><button type="button" class="button" data-topup-stop-back>${t('暂不关闭','Keep auto top-up')}</button></div><p role="status"></p></section>`;target.append(controls);
        const open=controls.querySelector('[data-topup-disable]'),confirm=controls.querySelector('[data-topup-stop-confirm]'),submit=controls.querySelector('[data-topup-stop-submit]'),back=controls.querySelector('[data-topup-stop-back]'),feedback=confirm.querySelector('[role=status]');let busy=false;const active=()=>current()&&target.isConnected&&target.topupRequest===request;
        open.onclick=()=>{if(busy||!active())return;confirm.hidden=false;open.setAttribute('aria-expanded','true');submit.focus();};back.onclick=()=>{if(busy||!active())return;confirm.hidden=true;open.setAttribute('aria-expanded','false');feedback.textContent='';open.focus();};
        submit.onclick=async()=>{if(busy||!active())return;busy=true;open.disabled=submit.disabled=back.disabled=true;feedback.textContent=t('正在关闭…','Turning off…');try{const saved=await api('/api/billing/automatic-topup/disable',{authorization_id:a.id,confirm:true});if(!active())return;if(saved.account_id!==accountId||saved.charging_enabled!==false||saved.authorization?.id!==a.id||!['revoking','revoked'].includes(saved.authorization.state))throw Error('TOPUP_DISABLE_UNCONFIRMED');await renderAutomaticTopup(target,current,accountId);}catch(error){if(active())feedback.textContent=requestError(error,t('关闭结果未确认。请刷新原授权状态核对，或主动重试；本页不会自动重试。','Turning off is unconfirmed. Read the original authorization status or retry explicitly; this request will not retry automatically.'));}finally{busy=false;if(controls.isConnected)open.disabled=submit.disabled=back.disabled=false;}};
      }
      const presets=data.presets;
      if(presets!==undefined){
        if(!Array.isArray(presets)||presets.some(item=>typeof item.key!=='string'||!quantity(item.threshold)||!quantity(item.pack_credits)||!amount(item.pack_amount_minor)||!amount(item.monthly_cap_minor)||item.currency!=='CNY'))throw Error('INVALID_TOPUP_PRESETS');
        const form=document.createElement('form');form.method='dialog';form.className='procurement-form';form.innerHTML=`<h4>${t('选择固定充值方案','Choose a fixed top-up preset')}</h4><label>${t('充值方案','Top-up preset')}<select name="preset_key">${presets.map(item=>`<option value="${escape(item.key)}">${t('低于','Below')} ${escape(item.threshold)} Credits · ${t('每次','Per top-up')} ${escape(item.pack_credits)} Credits (${money(item.pack_amount_minor,true)}) · ${t('本服务月最多','Service-month cap')} ${money(item.monthly_cap_minor,true)}</option>`).join('')}</select></label><label class="consent"><input type="checkbox" name="confirm"><span>${t('我单独授权：任务需要使用共享点数且共享余额低于上述触发线时，按此固定购买包和服务月金额上限自动充值。订阅续费授权不包含自动充值。','I separately authorize auto top-up when a task needs shared Credits and the shared balance is below this threshold, using this fixed pack and service-month spending cap. Subscription renewal does not authorize auto top-up.')}</span></label><button type="submit" class="button primary" disabled>${t('确认自动充值授权','Confirm auto top-up authorization')}</button><p role="status">${t('真实自动扣款授权尚未接通，当前不能开启。保存或展示方案不代表已授权扣款。','Recurring payment authorization is not connected, so auto top-up cannot be enabled. Displaying a preset does not authorize charges.')}</p>`;
        const submit=form.querySelector('[type=submit]'),consent=form.querySelector('[type=checkbox]');const available=data.can_authorize===true&&typeof data.payment_method_id==='string'&&presets.length>0;let busy=false,key=null;
        consent.onchange=()=>submit.disabled=busy||!available||!consent.checked;
        form.onsubmit=async event=>{event.preventDefault();if(submit.disabled||!current())return;busy=true;submit.disabled=true;key=key||crypto.randomUUID();try{await window.sageConfirmSensitive(accountId,session.csrf_token);if(!current())return;const saved=await api('/api/billing/automatic-topup',{preset_key:form.elements.preset_key.value,payment_method_id:data.payment_method_id,expected_version:data.authorization?.version??null,request_key:key,confirm:true});if(!current())return;if(typeof saved.id!=='string'||!Number.isSafeInteger(saved.version)||typeof saved.charging_enabled!=='boolean')throw Error('AUTHORIZATION_UNCONFIRMED');await renderAutomaticTopup(target,current,accountId);}catch(error){if(current())form.querySelector('[role=status]').textContent=requestError(error,t('授权结果未确认，请刷新原记录核对；不会自动再次授权。','Authorization is unconfirmed. Refresh the original record; authorization will not retry automatically.'));}finally{busy=false;if(submit.isConnected)submit.disabled=!available||!consent.checked;}};target.append(form);
      }

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
    const setup=item?.renewal_setup;
    if(setup!=null&&(typeof setup!=='object'||Array.isArray(setup)||setup.status!=='failed'||setup.separate_authorization_required!==true||item.auto_renew!==false||item.can_cancel!==false))return false;
    if (item?.renewal != null && (!['created','pending','unknown','succeeded','failed','expired','cancelled','review','missing'].includes(item.renewal.status) || !item.renewal.manual_payment_deadline || !date(item.renewal.manual_payment_deadline) || !['manual_payment_eligible','new_subscription_payment_eligible','query_original_payment','reauthorization_required'].every(key => typeof item.renewal[key] === 'boolean'))) return false;
    return item && typeof item.id === 'string' && /^[0-9a-f-]{36}$/i.test(item.id) && typeof item.plan_code === 'string' && item.plan_code.length > 0 && ['monthly','annual'].includes(item.billing_cycle) && ['active','expired','scheduled'].includes(item.status) && ['none','processing','completed'].includes(item.cancellation_state) && typeof item.auto_renew === 'boolean' && typeof item.can_cancel === 'boolean' && ['period_start','period_end','next_credit_grant_at','prepaid_through'].every(key => date(item[key])) && item.capabilities && typeof item.capabilities.cancel === 'boolean' && !(item.cancellation_state !== 'none' && item.auto_renew);
  }
  function annualRefund(target,item,current){
    const accountId=session?.active_account_id,subscriptionId=item.id,active=()=>current()&&target.isConnected&&session?.active_account_id===accountId;
    target.innerHTML=`<h4>${t('年度订阅退款','Annual subscription refund')}</h4><p>${t('退回未来尚未开始的完整服务月，包括已付款的下一期；当前服务月保留。提交申请立即关闭续费，到账需以退款记录为准。','Refund future full service months, including paid future terms; retain the current month. Submitting stops renewal immediately. Receipt of funds depends on the refund record.')}</p><button type="button" class="button" data-annual-review>${t('核对可退金额','Review refund amount')}</button><div data-annual-refund></div>`;
    const panel=target.querySelector('[data-annual-refund]');let key=crypto.randomUUID(),busy=false,refundId=null;
    const review=target.querySelector('[data-annual-review]');review.onclick=()=>assess();
    async function assess(restoreOnly=false){
      if(busy||!active())return;busy=true;review.disabled=true;if(!restoreOnly)panel.innerHTML=emptyText(t('正在核对…','Checking…'));
      try{const data=await api(`/api/billing/subscriptions/${encodeURIComponent(subscriptionId)}/refund-assessment`);if(!active())return;if(data.subscription_id!==subscriptionId||typeof data.eligible!=='boolean'||data.currency!=='CNY'||(data.amount_minor!==null&&(typeof data.amount_minor!=='string'||!/^\d+$/.test(data.amount_minor)))||(data.eligible&&data.amount_minor===null)||typeof data.automatic_review!=='boolean')throw Error('INVALID_ANNUAL_REFUND');
        if(data.existing_refund!==undefined&&data.existing_refund!==null){show(data.existing_refund);return;}if(restoreOnly)return;
        panel.innerHTML=`<dl class="order-detail-list"><dt>${t('可退金额','Refund amount')}</dt><dd>${data.amount_minor===null?t('暂不可计算','Unavailable'):money(data.amount_minor,true)}</dd><dt>${t('当前服务保留至','Current service retained until')}</dt><dd>${dateLabel(data.current_service_ends_at)}</dd></dl><p>${data.automatic_review?t('普通申请按规则由系统处理；实际到账时间以支付渠道为准。','Ordinary requests are processed under the rules. Posting time depends on the payment channel.'):t('此申请需要人工核对，不代表已经退款。','This request requires manual review; no refund has been confirmed.')}</p><label class="consent"><input type="checkbox" data-annual-agree><span>${t('我确认申请上述退款并立即关闭自动续费。','I confirm this refund request and immediate cancellation of renewal.')}</span></label><button type="button" class="button primary" data-annual-submit disabled>${t('提交年度退款申请','Submit annual refund request')}</button><p role="status"></p>`;
        const agree=panel.querySelector('[data-annual-agree]'),submit=panel.querySelector('[data-annual-submit]');if(!data.eligible){const reasons={no_future_months:t('没有尚未开始的完整服务月。','There are no future full service months.'),no_future_service_months:t('没有尚未开始的完整服务月。','There are no future full service months.'),not_annual:t('此订阅不是年度订阅。','This is not an annual subscription.'),task_settlement_required:t('相关任务需要先完成结算。','Related tasks must settle first.'),payment_review_required:t('原付款需要先核对。','The original payment requires review.')};panel.querySelector('[role=status]').textContent=reasons[data.reason]||t('当前不满足在线申请条件，请核对原订单及订阅状态。','Online refund requirements are not currently met. Check the original order and subscription.');agree.disabled=true;}agree.onchange=()=>submit.disabled=busy||!data.eligible||!agree.checked;
        submit.onclick=async()=>{if(busy||submit.disabled||!active())return;busy=true;submit.disabled=true;const feedback=panel.querySelector('[role=status]');try{if(activeAccount()?.kind==='business')await window.sageConfirmSensitive(accountId,session.csrf_token);if(!active())return;const saved=await api(`/api/billing/subscriptions/${encodeURIComponent(subscriptionId)}/refunds`,{request_key:key,confirm:true});if(!active())return;show(saved);}catch(error){if(active())feedback.textContent=requestError(error,t('申请结果未知。请使用本页原请求核对，不要新建另一笔申请。','Request status is unknown. Retry the original request here; do not create another request.'));}finally{busy=false;if(submit.isConnected)submit.disabled=!agree.checked||!data.eligible;}};
      }catch(error){if(active())panel.innerHTML=`<p class="error" role="status">${requestError(error,t('退款条件读取失败，状态未知，请重新核对。','Refund eligibility could not be loaded. Its state is unknown. Check again.'))}</p>`;}finally{busy=false;if(review.isConnected)review.disabled=false;}
    }
    assess(true);
    function show(data){
      const states={approved:t('系统已接受，尚未确认退款到账','Accepted; refund receipt is not yet confirmed'),review:t('等待人工核对','Awaiting manual review'),processing:t('退款处理中','Refund processing'),refunded:t('退款已确认，到账时间以渠道为准','Refund confirmed; posting time depends on the channel'),unknown:t('原退款结果未知，正在核对','Original refund outcome unknown'),terminated:t('本次退款已结束，自动续费仍关闭','This refund request has ended. Automatic renewal remains off.')};
      if(typeof data.id!=='string'||!Object.hasOwn(states,data.status)||data.currency!=='CNY'||typeof data.amount_minor!=='string'||!/^\d+$/.test(data.amount_minor)||data.auto_renew!==false)throw Error('INVALID_REFUND_RESULT');refundId=data.id;item.auto_renew=false;item.can_cancel=false;const card=target.closest('.subscription-card');if(card){const renewal=card.querySelector('.subscription-renewal>strong');if(renewal)renewal.textContent=t('自动续费已在系统关闭','Renewal stopped in the system');card.querySelector('[data-cancel-open]')?.remove();card.querySelector('.subscription-confirm')?.remove();}
      panel.innerHTML=`<p role="status">${states[data.status]}</p><p>${t('自动续费已在系统关闭；当前服务保留至','Renewal is stopped in the system; current service retained until')} ${dateLabel(data.current_service_ends_at)}</p><p>${money(data.amount_minor,true)}</p><button type="button" class="button" data-annual-refresh>${t('核对原退款状态','Check original refund status')}</button>`;
      panel.querySelector('button').onclick=async()=>{if(busy||!active())return;busy=true;try{const saved=await api(`/api/billing/refunds/${encodeURIComponent(refundId)}`);if(active()){if(saved.id!==refundId)throw Error('REFUND_MISMATCH');show(saved);}}catch(_){if(active())panel.querySelector('[role=status]').textContent=t('原退款状态读取失败，当前结果未知，请重试查询。','The original refund could not be loaded. Its outcome is unknown. Retry the read.');}finally{busy=false;}};
    }
  }

  async function renderSubscriptionChanges(target,item,current){
    target.innerHTML=`<h4>${t('更换套餐或周期','Change plan or billing cycle')}</h4><p>${t('正在读取可选方案…','Loading available plans…')}</p>`;
    const accountId=session?.active_account_id,subscriptionId=item.id;const active=()=>current()&&target.isConnected&&session?.active_account_id===accountId;
    try{
      const data=await api(`/api/billing/subscriptions/${encodeURIComponent(subscriptionId)}/change-options`);if(!active())return;
      if(data.subscription_id!==subscriptionId||typeof data.available!=='boolean'||!Array.isArray(data.items))throw Error('INVALID_CHANGE_OPTIONS');
      for(const option of data.items)if(typeof option.product_id!=='string'||typeof option.code!=='string'||!['monthly','annual'].includes(option.billing_cycle)||option.currency!=='CNY'||typeof option.amount_minor!=='string'||!/^\d+$/.test(option.amount_minor))throw Error('INVALID_CHANGE_OPTION');
      if(activeAccount()?.kind==='business')data.items=data.items.filter(option=>option.code===item.plan_code);
      target.innerHTML=`<h4>${t('更换套餐或周期','Change plan or billing cycle')}</h4><p>${t('按剩余服务时间抵扣。新套餐实际开通时开始完整周期，首月发放完整内含点数；旧套餐内含点数结束，加购点数保留。','Credit is based on remaining service time. A complete new term begins on activation with the full first-month allowance. Old included Credits end; purchased Credits remain.')}</p>`;
      if(!data.available||!data.items.length){target.insertAdjacentHTML('beforeend',`<button class="button" disabled>${t('更换套餐','Change plan')}</button><p>${t('可售方案、费用规则或支付服务尚未就绪，当前无法变更。','Approved plans, pricing rules or payment services are not ready; changes are unavailable.')}</p>`);return;}
      const form=document.createElement('form');form.method='dialog';form.innerHTML=`<label>${t('目标套餐','Target plan')}<select name="product_id">${data.items.map(option=>`<option value="${escape(option.product_id)}">${escape(option.code)} · ${option.billing_cycle==='annual'?t('年付','Annual'):t('月付','Monthly')} · ${money(option.amount_minor,true)}</option>`).join('')}</select></label><button type="submit" class="button">${t('核对变更费用','Review change charges')}</button><p role="status"></p><div data-change-quote></div>`;target.append(form);
      let quote=null,quoteKey=null,changeKey=null,busy=false,timer=null;const note=form.querySelector('[role=status]'),quotePanel=form.querySelector('[data-change-quote]'),select=form.querySelector('select');
      const submit=form.querySelector('[type=submit]');select.onchange=()=>{clearInterval(timer);quote=null;quoteKey=null;changeKey=null;quotePanel.replaceChildren();note.textContent='';};
      form.onsubmit=async event=>{event.preventDefault();if(busy||!active())return;busy=true;submit.disabled=true;quoteKey=quoteKey||crypto.randomUUID();try{const saved=await api(`/api/billing/subscriptions/${encodeURIComponent(subscriptionId)}/change-quotes`,{product_id:select.value},session.csrf_token,{'Idempotency-Key':quoteKey});if(!active())return;const terms=saved.terms,content=terms?.[en?'en':'zh']||terms;if(saved.subscription_id!==subscriptionId||saved.product_id!==select.value||saved.currency!=='CNY'||typeof saved.id!=='string'||!Number.isFinite(Date.parse(saved.expires_at))||['gross_minor','discount_minor','due_minor','refund_minor'].some(key=>typeof saved[key]!=='string'||!/^\d+$/.test(saved[key]))||typeof terms?.version!=='string'||typeof content.title!=='string'||!Array.isArray(content.paragraphs)||content.paragraphs.some(value=>typeof value!=='string'))throw Error('INVALID_CHANGE_QUOTE');quote=saved;changeKey=crypto.randomUUID();showQuote(content);}
        catch(error){if(active())note.textContent=requestError(error,t('变更报价未确认。可用相同选择重试，系统使用原请求，不会自动创建订单。','The change quote is unconfirmed. Retry the same selection with the original request; no order is automatically created.'));}finally{busy=false;if(submit.isConnected)submit.disabled=false;}};
      function showQuote(content){
        clearInterval(timer);quotePanel.innerHTML=`<dl class="order-detail-list"><dt>${t('新套餐金额','New plan price')}</dt><dd>${money(quote.gross_minor,true)}</dd><dt>${t('剩余服务抵扣','Remaining service credit')}</dt><dd>${money(quote.discount_minor,true)}</dd><dt>${t('本次应付','Amount due')}</dt><dd>${money(quote.due_minor,true)}</dd><dt>${t('另行退款','Separate refund')}</dt><dd>${money(quote.refund_minor,true)}</dd></dl><h4>${escape(content.title)}</h4>${content.paragraphs.map(value=>`<p>${escape(value)}</p>`).join('')}<p data-change-countdown></p><label class="consent"><input type="checkbox" data-change-agree><span>${t('我已阅读并同意变更条款，确认以上费用、点数处理与新服务周期。','I have read and accept the change terms and confirm these charges, Credits treatment and new term.')}</span></label><button type="button" class="button primary" data-change-confirm disabled>${t('确认变更','Confirm change')}</button>`;
        const agree=quotePanel.querySelector('[data-change-agree]'),confirm=quotePanel.querySelector('[data-change-confirm]');
        function countdown(){if(!active()||!quotePanel.isConnected){clearInterval(timer);return;}const seconds=Math.max(0,Math.ceil((Date.parse(quote.expires_at)-Date.now())/1000));quotePanel.querySelector('[data-change-countdown]').textContent=seconds?t(`报价剩余 ${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`,`Quote remaining ${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`):t('报价已过期，请更新费用并重新确认。','Quote expired. Update charges and review again.');confirm.disabled=busy||!agree.checked||seconds===0;if(seconds===0){agree.checked=false;agree.disabled=true;clearInterval(timer);quoteKey=null;submit.textContent=t('更新变更报价','Update change quote');}}
        agree.onchange=countdown;timer=setInterval(countdown,1000);busy=false;countdown();
        confirm.onclick=async()=>{if(confirm.disabled||!active())return;busy=true;confirm.disabled=true;select.disabled=true;submit.disabled=true;try{if(activeAccount()?.kind==='business')await window.sageConfirmSensitive(accountId,session.csrf_token);if(!active())return;const result=await api(`/api/billing/subscriptions/${encodeURIComponent(subscriptionId)}/changes`,{quote_id:quote.id,agreement_acceptance:quote.terms.version},session.csrf_token,{'Idempotency-Key':changeKey});if(!active())return;if(typeof result.id!=='string'||!['pending_payment','activated'].includes(result.status)||typeof result.payment_required!=='boolean'||typeof result.order_id!=='string')throw Error('CHANGE_UNCONFIRMED');clearInterval(timer);quotePanel.innerHTML=`<p role="status">${result.status==='activated'?t('服务端已确认变更生效，请刷新订阅核对。','The server confirms activation. Refresh subscriptions to verify.'):t('变更订单已创建，等待付款确认，当前尚未生效。','A change order was created and awaits confirmed payment. The change is not active.')}</p><a class="button" href="${page('billing')}?${new URLSearchParams({order:result.order_id})}#orders">${t('查看原订单','View original order')}</a>`;note.textContent='';}
          catch(error){if(active())note.textContent=requestError(error,t('变更结果未知。请核对原订阅或用本页相同请求重试；不会自动重复扣款。','The change result is unknown. Check the original subscription or retry the same request here; no automatic duplicate charge is made.'));}
          finally{busy=false;if(active()){select.disabled=false;submit.disabled=false;if(confirm.isConnected)countdown();}}};
      }
    }catch(error){if(active())target.innerHTML=`<h4>${t('更换套餐或周期','Change plan or billing cycle')}</h4><p role="status">${requestError(error,t('可变更方案读取失败，当前状态未知。','Change options could not be loaded; their state is unknown.'))}</p><button type="button" class="button" data-change-retry>${t('重新读取','Retry')}</button>`;target.querySelector('[data-change-retry]')?.addEventListener('click',()=>renderSubscriptionChanges(target,item,current));}
  }

  async function renderSubscriptions(target, prefix, current) {
    let items = [], pending = null, confirming = null, uncertain = null, readVersion = 0;
    const renewText = item => item.cancellation_state === 'processing' ? t('取消处理中','Cancellation processing') : item.cancellation_state === 'completed' ? t('自动续费已取消','Automatic renewal cancelled') : item.renewal_setup?.status==='failed'&&item.status==='active' ? t('已付服务已开通，自动续费未开启；需要时请另行开启','Paid service is active. Automatic renewal is off; enable it separately when needed.') : item.auto_renew ? t('自动续费已开启','Automatic renewal on') : t('自动续费未开启','Automatic renewal off');
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
      for(const item of items){if(item.status!=='active')continue;const panel=document.createElement('section');panel.className='policy-block subscription-change-options';target.querySelector(`[data-subscription="${item.id}"]`).append(panel);renderSubscriptionChanges(panel,item,current);if(item.billing_cycle==='annual'){const refund=document.createElement('section');refund.className='policy-block';target.querySelector(`[data-subscription="${item.id}"]`).append(refund);annualRefund(refund,item,current);}}
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
      if (data.order_id !== orderId || data.account_id !== accountId || typeof data.can_submit !== 'boolean' || data.currency !== 'CNY' || !Object.hasOwn(reasons,data.reason) || !['unavailable','assessment_ready','scope_ready'].includes(data.status)) throw new Error('INVALID_REFUND_ASSESSMENT');
      let metrics = '';
      if (data.status === 'assessment_ready') {
        if (data.scope !== 'all_unused_paid_credits' || !['policy_review_required','rounding_policy_pending'].includes(data.reason) || typeof data.unused_quantity !== 'string' || !/^\d+(\.\d+)?$/.test(data.unused_quantity) || typeof data.paid_minor !== 'string' || !/^\d+$/.test(data.paid_minor) || (data.reference_minor !== null && (typeof data.reference_minor !== 'string' || !/^\d+$/.test(data.reference_minor) || BigInt(data.reference_minor) > BigInt(data.paid_minor)))) throw new Error('INVALID_REFUND_AMOUNT');
        metrics = `<dl class="order-detail-list"><dt>${t('原单实付','Original payment')}</dt><dd>${money(data.paid_minor,true)}</dd><dt>${t('全部未用付费额度','Entire unused paid balance')}</dt><dd>${escape(data.unused_quantity)} Sage Credits</dd><dt>${t('退款参考金额','Reference refund amount')}</dt><dd>${data.reference_minor === null ? t('待确认','Not yet confirmed') : money(data.reference_minor,true)}</dd></dl>`;
      }
      if(data.scope==='annual_future_service_months'&&data.can_submit)throw Error('INVALID_ANNUAL_SCOPE');
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
      target.innerHTML = heading + metrics + `<p role="status">${data.can_submit?t('退款范围已核对，提交前请确认原订单及可退金额。','Refund scope checked. Review the original order and refund amount before submitting.'):reasons[data.reason]}</p><button type="button" class="button" data-refund-refresh>${t('重新核对','Check again')}</button>`;
      target.querySelector('[data-refund-refresh]').onclick = () => renderRefundAssessment(target,orderId,accountId,isCurrent);
      if(data.can_submit){
        const section=document.createElement('section');section.innerHTML=`<p>${t('按原订单申请全部未用付费点数退款。普通申请按规则由系统处理，异常情况转人工；申请不等于资金已到账。','Request a refund of this order’s entire unused paid Credits. Ordinary requests follow system rules; exceptions require manual review. A request does not confirm receipt of funds.')}</p><label class="consent"><input type="checkbox" data-credit-refund-agree><span>${t('我确认申请该订单全部未用付费点数退款。','I confirm a refund request for all unused paid Credits from this order.')}</span></label><button type="button" class="button primary" data-credit-refund-submit disabled>${t('提交退款申请','Submit refund request')}</button><p role="status"></p>`;
        target.append(section);let busy=false;const key=crypto.randomUUID(),agree=section.querySelector('input'),submit=section.querySelector('button'),feedback=section.querySelector('[role=status]');agree.onchange=()=>submit.disabled=busy||!agree.checked;
        submit.onclick=async()=>{if(busy||!agree.checked||!current())return;busy=true;submit.disabled=true;try{if(activeAccount()?.kind==='business')await window.sageConfirmSensitive(accountId,session.csrf_token);if(!current())return;const saved=await api(`/api/billing/orders/${encodeURIComponent(orderId)}/refunds`,{request_key:key,confirm:true});if(!current())return;if(typeof saved.id!=='string'||!['approved','review','processing','refunded','unknown'].includes(saved.status))throw Error('REFUND_UNCONFIRMED');section.innerHTML=`<p role="status">${saved.status==='refunded'?t('退款已由服务端确认，请以渠道到账记录为准。','The server confirmed the refund. Check channel posting records.'):saved.status==='review'?t('申请已记录，等待人工核对；尚未确认到账。','The request is recorded and awaits manual review. Receipt is not confirmed.'):t('申请已记录，退款处理中或待核对，尚未确认到账。','The request is recorded and processing or under review. Receipt is not confirmed.')}</p><button type="button" class="button" data-credit-refund-refresh>${t('核对原订单退款状态','Check original order refund status')}</button>`;section.querySelector('button').onclick=()=>target.querySelector('[data-refund-refresh]').click();}
          catch(error){if(current())feedback.textContent=requestError(error,t('退款申请结果未知。请核对原订单，或在此用同一请求重试，不要新建退款。','The refund request outcome is unknown. Check the original order or retry the same request here; do not create a new refund.'));}finally{busy=false;if(submit.isConnected)submit.disabled=!agree.checked;}};
      }

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
    target.innerHTML = prefix + `<form class="procurement-form" id="procurement-form"><h3>${t('提交企业采购需求','Submit a purchasing inquiry')}</h3><label for="purchase-company">${t('企业名称','Company name')}</label><input id="purchase-company" name="company" required maxlength="160" autocomplete="organization"><label for="purchase-seats">${t('预计真人席位','Expected human seats')}</label><input id="purchase-seats" name="seats" type="number" min="1" max="10000" step="1" value="1" required><label for="purchase-email">${t('联系邮箱','Contact email')}</label><input id="purchase-email" name="contact_email" type="email" required maxlength="254" autocomplete="email"><label for="purchase-requirements">${t('业务需求','Requirements')}</label><textarea id="purchase-requirements" name="requirements" required maxlength="2000" rows="4" placeholder="${t('请说明场景、使用规模与采购需求；不要填写密码、密钥或银行资料。','Describe your use cases, scale and purchasing needs. Do not include passwords, keys or bank details.')}"></textarea><p class="checkout-note">${t('仅提交采购需求，不会创建付款订单、开通订阅或扣款。','This sends an inquiry only. It does not create a payable order, subscription or charge.')}</p><p id="procurement-feedback" class="checkout-note" role="status"></p><button type="submit" class="button primary">${t('提交采购申请','Submit inquiry')}</button></form><h3 class="records-title">${t('已提交的申请','Submitted inquiries')}</h3><div id="procurement-records"></div>`;
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
            data.items.some(item => !item || !/^[0-9a-f-]{36}$/i.test(item.id) || typeof item.company !== 'string' || typeof item.requirements !== 'string' || !Number.isSafeInteger(item.seats) || item.seats < 1 || typeof item.status !== 'string' || !Number.isFinite(Date.parse(item.created_at)))) throw new Error('INVALID_INQUIRIES');
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
      if (!Number.isSafeInteger(quantity) || quantity < 1 || quantity > 10000) return;
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
        const invalidSeats = initial.has('seats') && (!/^[0-9]+$/.test(rawSeats) || !Number.isSafeInteger(Number(rawSeats)) || Number(rawSeats) > 10000 || (audience === 'business' ? Number(rawSeats) < 1 : Number(rawSeats) !== 1));
        if ((initial.has('cycle') && !['monthly','annual'].includes(initial.get('cycle'))) || (initial.has('audience') && !['personal','business'].includes(initial.get('audience'))) || invalidSeats) return fail(t('方案类型、周期或席位数量无效，请返回价格页重新选择。','The plan type, billing cycle or seat count is invalid. Return to Pricing to choose again.'));
        pricing(); loadPurchaseCatalog();
      } else if (view === 'checkout') await checkout(); else account();
    } catch (_) { fail(t('方案信息暂时无法加载。请重新加载，或返回首页。','Plan information could not be loaded. Retry or return to the homepage.')); }
  }
  window.addEventListener('hashchange', () => {closeMemberDialog?.(); if (view === 'account' && catalog) { account(); const heading = document.querySelector('#account-section-title'); heading.setAttribute('tabindex', '-1'); heading.focus(); } });
  window.addEventListener('pagehide', () => {
    closeMemberDialog?.();
    sessionVersion++; accountVersion++; session = null; sessionBusy = false; logoutToken = null; sessionState = 'loading';
    sessionNotice = logoutPending ? t('本页账户数据已清除，但服务端退出尚未确认。','Account data is cleared from this page, but server sign-out is not confirmed.') : '';
    refreshSessionView();
  });
  window.addEventListener('pageshow', event => { if (event.persisted) loadSession(); });
  init();
})();
