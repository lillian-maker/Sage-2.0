(() => {
  'use strict';
  const en = document.documentElement.lang === 'en';
  const t = (zh, english) => en ? english : zh;
  const params = new URLSearchParams(location.search);
  const mode = params.get('mode') === 'register' ? 'register' : 'login';
  const card = document.querySelector('.auth-card');
  const form = document.querySelector('#auth-form');
  if (!card || !form) return;

  const panel = document.createElement('div');
  panel.id = 'auth-oidc-panel';
  panel.hidden = true;
  const button = document.createElement('button');
  button.id = 'auth-oidc';
  button.type = 'button';
  button.className = 'auth-alternative';
  button.textContent = mode === 'register' ? t('使用 Sage 账号Sign up', 'Register with Sage') : t('使用 Sage 账号Log in', 'Sign in with Sage');
  const note = document.createElement('p');
  note.id = 'auth-oidc-note';
  note.className = 'auth-intro';
  panel.append(button, note);
  card.insertBefore(panel, form);

  let session = null;
  let busy = false;

  function showNote(text, error = false) {
    note.textContent = text;
    note.classList.toggle('error', error);
  }

  function errorText(code) {
    return ({
      invalid_request: t('Log in请求已失效, 请重新开始.', 'The sign-in request expired. Start again.'),
      provider_error: t('Log in提供方取消了本次Log in.', 'The sign-in provider cancelled this request.'),
      rejected: t('身份验证未Approve, 请重新开始.', 'Identity verification failed. Start again.'),
      unavailable: t('统一Log in暂不可用, 请稍后再试.', 'Unified sign-in is unavailable. Try again later.'),
      identity_ambiguous: t('该身份匹配到多个账号, 已停止自动绑定.', 'This identity matches multiple accounts. Automatic linking stopped.'),
      account_disabled: t('该账号已停用.', 'This account is disabled.'),
      account_unavailable: t('账号暂时不可用, 请稍后再试.', 'The account is unavailable. Try again later.'),
      terms_unavailable: t('Sign up条款Not released, 暂时不能Sign up.', 'Registration terms are not published yet.'),
      OIDC_UNAVAILABLE: t('统一Log in尚未就绪.', 'Unified sign-in is not ready.'),
      TERMS_UNAVAILABLE: t('Sign up条款Not released, 暂时不能Sign up.', 'Registration terms are not published yet.'),
    })[code] || t('Log in未完成, 请重试.', 'Sign-in did not complete. Try again.');
  }

  async function readSession() {
    const response = await fetch('/api/auth/session', { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
    if (!response.ok) throw Error('SESSION_UNAVAILABLE');
    return response.json();
  }

  async function start() {
    if (busy || !session?.capabilities?.oidc_login) return;
    busy = true;
    button.disabled = true;
    showNote('');
    try {
      const body = {
        return_to: params.get('return') || '/index-en.html',
        language: en ? 'en' : 'zh',
        intent: mode,
      };
      if (mode === 'register') {
        const version = session.capabilities.registration_terms_version;
        if (typeof version !== 'string' || !version) throw Error('TERMS_UNAVAILABLE');
        body.agreement_acceptance = version;
      }
      const response = await fetch('/api/auth/oidc/start', {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-CSRFToken': session.csrf_token,
        },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(15000),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw Error(result.error?.code || 'REQUEST_FAILED');
      const url = new URL(result.authorization_url);
      if (url.protocol !== 'https:' || url.username || url.password) throw Error('OIDC_REJECTED');
      location.assign(url.href);
    } catch (error) {
      showNote(errorText(error.message), true);
      busy = false;
      button.disabled = false;
    }
  }

  button.addEventListener('click', start);

  readSession().then((value) => {
    session = value;
    if (value.authenticated) return;
    if (value.capabilities?.legacy_login === false) {
      form.hidden = true;
      panel.hidden = false;
      if (value.capabilities?.oidc_login !== true) {
        button.disabled = true;
        showNote(t('统一Log in尚未就绪, 请稍后再试.', 'Unified sign-in is not ready. Try again later.'), true);
        return;
      }
      showNote(t('旧Log in入口已Close, 请使用统一Log in.', 'Legacy sign-in is closed. Use unified sign-in.'));
    }
    if (value.capabilities?.oidc_login !== true) return;
    panel.hidden = false;
    if (mode === 'register' && value.capabilities?.oidc_registration !== true) {
      button.disabled = true;
      showNote(t('Sign up条款Not released, 暂时不能Sign up.', 'Registration terms are not published yet.'), true);
    }
    const code = params.get('oidc');
    if (code) {
      showNote(errorText(code), true);
      const clean = new URL(location.href);
      clean.searchParams.delete('oidc');
      history.replaceState(null, '', clean);
    }
  }).catch(() => {
    panel.hidden = false;
    button.disabled = true;
    showNote(t('无法连接Account服务, 请稍后再试.', 'Account services could not be reached. Try again later.'), true);
  });
})();
