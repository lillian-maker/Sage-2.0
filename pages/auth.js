(() => {
  'use strict';
  const en = document.documentElement.lang === 'en';
  const t = (zh, english) => en ? english : zh;
  const root = document.querySelector('#auth-root');
  const page = name => `${name}${en ? '-en' : ''}.html`;
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const requested = new URLSearchParams(location.search).get('return');
  function safeReturn(value) {
    if (!value || /[\\\u0000-\u0020]/.test(value) || value.startsWith('//') || /^[a-z][a-z0-9+.-]*:/i.test(value)) return page('billing');
    if (['trial', 'pricing'].includes(value)) value = page(value);
    try {
      const url = new URL(value, location.href), directory = new URL('./', location.href).pathname;
      const pages = ['billing','checkout','pricing','trial','profile','team','enterprise','docs','download'];
      const allowed = pages.flatMap(name => [directory + name + '.html', directory + name + '-en.html']);
      const home = new URL('../', location.href).pathname;
      allowed.push(home + 'index.html', home + 'index-en.html', home);
      if (url.origin !== location.origin || !allowed.includes(url.pathname)) return page('billing');
      // The chosen UI language follows the user through verification, even when
      // the return parameter was created on the other language's page.
      const localized = url.pathname === home ? home + page('index') : url.pathname.replace(/(?:-en)?\.html$/, en ? '-en.html' : '.html');
      return localized + url.search + url.hash;
    } catch (_) { return page('billing'); }
  }
  const destination = safeReturn(requested);
  let session = null, method = 'email', challenge = null, busy = false, available = false, cooldownUntil = 0, expiresAt = 0;
  let requestVersion = 0;
  const unavailable = t('验证服务暂不可用，不会发送验证码或上传联系方式。请稍后重试。','Verification is unavailable. No code will be sent and no contact details will be uploaded. Try again later.');
  function failureMessage(error, fallback) {
    return ({FORBIDDEN:t('当前请求不被允许，请重新连接账户服务后再试。','This request is not permitted. Reconnect to account services and retry.'),CSRF_FAILED:t('页面验证已失效，请重新连接账户服务后再试。','Page verification has expired. Reconnect to account services and retry.'),RATE_LIMITED:t('请求过于频繁，请稍后重试。','Too many requests. Try again later.')})[error.message] || fallback;
  }
  root.innerHTML = `<a class="skip" href="#main">${t('跳至内容','Skip to content')}</a><header class="billing-nav"><a class="billing-brand" href="../${page('index')}"><img src="../assets/favicon.svg" width="29" height="29" alt="">Sage</a><div class="nav-actions"><a href="auth${en ? '' : '-en'}.html${escape(location.search)}" lang="${en ? 'zh-CN' : 'en'}">${en ? '中文' : 'English'}</a><a href="${escape(destination)}">${t('返回','Back')}</a></div></header><main id="main" class="page auth-page"><header class="page-heading"><h1>${t('登录或注册 Sage','Sign in or join Sage')}</h1><p>${t('首次验证成功后创建账户，已有账户直接登录。','Your first successful verification creates an account. Existing users sign in directly.')}</p></header><section class="auth-card"><div id="auth-signed-in" hidden></div><form id="auth-form" novalidate><div class="segmented auth-methods" role="group" aria-label="${t('验证方式','Verification method')}"><button type="button" data-auth-method="email" aria-pressed="true">${t('邮箱验证','Email')}</button><button type="button" data-auth-method="phone" aria-pressed="false">${t('手机号验证','Phone')}</button></div><label for="auth-contact" id="contact-label">${t('邮箱','Email address')}</label><input id="auth-contact" name="contact" spellcheck="false" type="email" autocomplete="email" maxlength="254" placeholder="you@company.com" aria-describedby="auth-contact-error auth-feedback" required disabled><p id="auth-contact-error" class="field-error"></p><label for="auth-code">${t('验证码','Verification code')}</label><div class="auth-code-row"><input id="auth-code" name="code" spellcheck="false" autocomplete="one-time-code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" placeholder="${t('输入 6 位验证码','Enter the 6-digit code')}" aria-describedby="auth-code-error" required disabled><button type="button" class="button" id="request-code" disabled>${t('获取验证码','Send code')}</button></div><p id="auth-code-error" class="field-error"></p><p id="auth-feedback" class="checkout-note" role="status" aria-live="polite">${t('正在检查验证服务…','Checking verification availability…')}</p><button type="submit" id="auth-verify" class="button primary wide" disabled>${t('验证并继续','Verify and continue')}</button><p class="auth-privacy">${t('仅用于身份验证。账户信息以验证成功后的服务端结果为准；验证不会订阅、充值或扣款。','Used for identity verification only. Your account is confirmed by the server after verification; signing in does not subscribe, top up or charge you.')}</p></form><button type="button" class="text-link auth-retry" id="auth-retry">${t('重新连接账户服务','Reconnect to account services')}</button></section><p class="auth-rules"><a href="${page('docs')}?article=account-security">${t('账号安全','Account security')}</a><span aria-hidden="true"> · </span><a href="${page('docs')}?article=platform-rules">${t('平台使用规则','Platform rules')}</a></p></main>`;
  const form = document.querySelector('#auth-form'), contact = document.querySelector('#auth-contact'), code = document.querySelector('#auth-code');
  const send = document.querySelector('#request-code'), verify = document.querySelector('#auth-verify'), feedback = document.querySelector('#auth-feedback');
  function message(text, error = false) { feedback.textContent = text; feedback.classList.toggle('error', error); }
  function fieldError(input, text) {
    input.setAttribute('aria-invalid', String(Boolean(text)));
    document.querySelector(`#${input.id}-error`).textContent = text;
  }
  async function api(path, body) {
    const options = {credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(10000),headers:{Accept:'application/json'}};
    if (body !== undefined) {
      if (!session?.csrf_token) throw new Error('SESSION_UNAVAILABLE');
      options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.headers['X-CSRFToken'] = session.csrf_token; options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('SERVICE_UNAVAILABLE');
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.code || 'REQUEST_FAILED');
    return result;
  }
  function isSession(value) { return value && typeof value.authenticated === 'boolean' && typeof value.csrf_token === 'string' && Array.isArray(value.accounts) && value.capabilities && (!value.authenticated || value.user?.id); }
  function updateControls() {
    const enabled = available && session?.capabilities?.[method + '_login'] === true && !session.authenticated;
    contact.disabled = !enabled || busy;
    code.disabled = !enabled || busy || !challenge;
    const seconds = Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000));
    send.disabled = !enabled || busy || seconds > 0;
    send.textContent = seconds ? t(`${seconds} 秒后重发`, `Resend in ${seconds}s`) : t('获取验证码','Send code');
    verify.disabled = !enabled || busy || !challenge || Date.now() >= expiresAt;
    document.querySelector('#auth-retry').disabled = busy;
    for (const button of document.querySelectorAll('[data-auth-method]')) button.disabled = busy;
    if (challenge && Date.now() >= expiresAt) {
      challenge = null; code.value = ''; code.disabled = true;
      message(t('验证码已过期，请重新获取。','The code has expired. Request a new code.'), true);
    }
  }
  function invalidateChallenge() { challenge = null; expiresAt = 0; code.value = ''; fieldError(code, ''); updateControls(); }
  async function loadSession() {
    const version = ++requestVersion; available = false; session = null; invalidateChallenge(); busy = true; updateControls();
    form.hidden = false; document.querySelector('#auth-signed-in').hidden = true;
    message(t('正在连接账户服务…','Connecting to account services…'));
    document.querySelector('#auth-retry').disabled = true;
    try {
      const result = await api('/api/auth/session');
      if (version !== requestVersion) return;
      if (!isSession(result)) throw new Error('INVALID_SESSION');
      session = result; available = true;
      if (session.authenticated) {
        form.hidden = true;
        const signed = document.querySelector('#auth-signed-in'); signed.hidden = false;
        signed.innerHTML = `<h2>${t('你已登录','You are signed in')}</h2><p>${escape(session.user.display_name || session.user.masked_identifier || t('Sage 账户','Sage account'))}</p><a class="button primary wide" href="${escape(destination)}">${t('继续前往','Continue')}</a>`;
      } else message(session.capabilities[method + '_login'] ? t('输入邮箱或手机号，获取验证码后继续。','Enter your email or phone number and request a code to continue.') : unavailable);
    } catch (_) { if (version === requestVersion) message(unavailable, true); }
    finally { if (version === requestVersion) { busy = false; updateControls(); document.querySelector('#auth-retry').disabled = false; } }
  }
  for (const button of document.querySelectorAll('[data-auth-method]')) button.addEventListener('click', () => {
    method = button.dataset.authMethod; contact.value = ''; fieldError(contact, ''); invalidateChallenge();
    for (const other of document.querySelectorAll('[data-auth-method]')) other.setAttribute('aria-pressed', String(other === button));
    contact.type = method === 'email' ? 'email' : 'tel'; contact.autocomplete = method === 'email' ? 'email' : 'tel';
    contact.placeholder = method === 'email' ? 'you@company.com' : '+86 13800000000';
    document.querySelector('#contact-label').textContent = method === 'email' ? t('邮箱','Email address') : t('手机号码（含国家区号）','Phone number (with country code)');
    message(available && session?.capabilities[method + '_login'] ? t('请输入联系方式并获取验证码。','Enter your contact details and request a code.') : unavailable);
    updateControls(); if (!contact.disabled) contact.focus();
  });
  contact.addEventListener('input', () => { invalidateChallenge(); fieldError(contact, ''); });
  send.addEventListener('click', async () => {
    if (send.disabled || !available || !session?.capabilities[method + '_login']) return;
    const identifier = contact.value.trim();
    const valid = method === 'email' ? /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(identifier) && identifier.length <= 254 : /^\+[1-9][0-9]{7,14}$/.test(identifier);
    if (!valid) { fieldError(contact, method === 'email' ? t('请输入有效的邮箱地址。','Enter a valid email address.') : t('请输入含国家区号的手机号，例如 +8613800000000。','Enter a phone number with its country code, such as +8613800000000.')); contact.focus(); return; }
    const version = ++requestVersion; busy = true; updateControls(); message(t('正在发送验证码…','Sending a verification code…'));
    try {
      const result = await api('/api/auth/challenges', {method,identifier});
      if (version !== requestVersion) return;
      if (typeof result.challenge_id !== 'string' || !Number.isFinite(result.expires_in) || result.expires_in <= 0 || !Number.isFinite(result.resend_after) || result.resend_after < 0) throw new Error('INVALID_CHALLENGE');
      challenge = result.challenge_id; expiresAt = Date.now() + result.expires_in * 1000; cooldownUntil = Date.now() + result.resend_after * 1000;
      message(t('验证码已发送，请在有效期内填写。未收到可在倒计时结束后重试。','Code sent. Enter it before it expires. If it does not arrive, retry after the countdown.'));
    } catch (error) {
      if (version !== requestVersion) return;
      invalidateChallenge();
      message(failureMessage(error, t('未能确认验证码已发送，请检查服务状态后重试。','Code delivery could not be confirmed. Check service availability and try again.')), true);
    } finally { if (version === requestVersion) { busy = false; updateControls(); if (challenge) code.focus(); } }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (verify.disabled || !challenge || busy) return;
    if (!/^[0-9]{6}$/.test(code.value)) { fieldError(code, t('请输入 6 位验证码。','Enter the 6-digit code.')); code.focus(); return; }
    fieldError(code, ''); const version = ++requestVersion; busy = true; updateControls(); message(t('正在验证身份…','Verifying your identity…'));
    try {
      const result = await api('/api/auth/verify', {challenge_id:challenge,code:code.value});
      if (version !== requestVersion) return;
      if (!isSession(result) || !result.authenticated) throw new Error('VERIFICATION_FAILED');
      code.value = ''; contact.value = ''; session = result;
      message(t('验证成功，正在返回…','Verified. Returning to your page…'));
      location.assign(destination);
    } catch (error) {
      if (version !== requestVersion) return;
      code.value = ''; fieldError(code, t('验证未完成，验证码可能无效或已过期。请检查后重试。','Verification failed. The code may be invalid or expired. Check it and try again.'));
      message(failureMessage(error, t('尚未登录，不会开通订阅或扣款。','You are not signed in. No subscription or charge has been created.')), true);
      if (['CHALLENGE_EXPIRED','CHALLENGE_LOCKED'].includes(error.message)) invalidateChallenge();
    } finally { if (version === requestVersion) { busy = false; updateControls(); if (!code.disabled) code.focus(); } }
  });
  document.querySelector('#auth-retry').addEventListener('click', loadSession);
  setInterval(updateControls, 1000);
  window.addEventListener('pageshow', event => { if (event.persisted) loadSession(); });
  loadSession();
})();
