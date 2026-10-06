(() => {
  'use strict';
  const menu = document.querySelector('.cp-menu'), links = document.querySelector('.cp-links');
  function closeMenu() { links.classList.remove('open'); menu.setAttribute('aria-expanded','false'); }
  menu.addEventListener('click', () => { const open = menu.getAttribute('aria-expanded') !== 'true'; links.classList.toggle('open',open); menu.setAttribute('aria-expanded',String(open)); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && links.classList.contains('open')) { closeMenu(); menu.focus(); } });
  document.addEventListener('click', e => { if (!e.target.closest('.cp-nav')) closeMenu(); });
  links.addEventListener('click', e => { if (e.target.closest('a')) closeMenu(); });
  const account = document.querySelector('.cp-account');
  account.href = 'auth-en.html?return=' + encodeURIComponent(location.pathname + location.search + location.hash);
  fetch('/api/auth/session',{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'},signal:AbortSignal.timeout(8000)}).then(async response => {
    if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('ACCOUNT_SERVICE_UNAVAILABLE');
    const session = await response.json();
    if (typeof session.authenticated !== 'boolean' || (session.authenticated && !session.user?.id)) throw new Error('INVALID_SESSION');
    if (session.authenticated) { account.textContent = session.user.display_name || session.user.masked_identifier || 'My account'; account.title = 'Manage your account and subscription'; account.href = 'profile-en.html'; }
    else { account.textContent = 'Log in'; account.title = 'Sign in with email or phone verification'; }
    window.sageSession=session;window.dispatchEvent(new CustomEvent('sage:session',{detail:session}));
  }).catch(() => { account.textContent = 'Log in'; account.title = 'Sign in with email or phone verification'; });
})();
