/* Sage UI: local presentation behavior only. Business actions are explicit host events. */
(function () {
  'use strict';
  let openMenu = null, menuTrigger = null, menuFocusReturn = null, toastTimer = null;
  const dialogTriggers = new WeakMap();
  function setTheme(theme, root = document.documentElement) {
    if (!['light', 'dark', 'system'].includes(theme)) throw new Error('SageUI: unknown theme ' + theme);
    const actual = theme === 'system' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : theme;
    root.dataset.theme = actual;
    root.dataset.themePreference = theme;
    root.querySelectorAll('[data-theme-value]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.themeValue === theme)));
    root.querySelectorAll('[data-theme-label]').forEach(label => label.textContent = theme === 'system' ? '跟随系统' : actual === 'dark' ? '深色' : '浅色');
    root.querySelectorAll('img[data-theme-logo]').forEach(img => img.src = actual === 'dark' ? img.dataset.logoDark : img.dataset.logoLight);
    root.dispatchEvent(new CustomEvent('sage:themechange', {bubbles: true, detail: {theme, actual}}));
  }
  function closeMenu(restoreFocus = false) {
    if (!openMenu) return;
    openMenu.hidden = true;
    menuTrigger?.setAttribute('aria-expanded', 'false');
    if (restoreFocus && menuFocusReturn?.isConnected) menuFocusReturn.focus();
    openMenu = null; menuTrigger = null; menuFocusReturn = null;
  }
  function toggleMenu(id, trigger, keyboard = false) {
    const menu = document.getElementById(id);
    if (!menu) throw new Error('SageUI: missing menu #' + id);
    const triggerRect = trigger.getBoundingClientRect();
    const focusReturn = openMenu?.contains(trigger) ? menuFocusReturn : trigger;
    const wasOpen = !menu.hidden && (openMenu === menu || trigger.getAttribute('aria-expanded') === 'true');
    closeMenu();
    if (wasOpen) { menu.hidden = true; trigger.setAttribute('aria-expanded', 'false'); return; }
    menu.hidden = false; openMenu = menu; menuTrigger = trigger; menuFocusReturn = focusReturn;
    trigger.setAttribute('aria-expanded', 'true');
    // Position within the viewport; the markup remains portable across app shells.
    const r = triggerRect, m = menu.getBoundingClientRect();
    menu.style.position = 'fixed'; menu.style.zIndex = '60';
    menu.style.left = Math.max(12, Math.min(r.left, innerWidth - m.width - 12)) + 'px';
    const below = r.bottom + 8;
    menu.style.top = (below + m.height <= innerHeight - 12 ? below : Math.max(12, r.top - m.height - 8)) + 'px';
    menu.style.bottom = 'auto';
    if (keyboard) menu.querySelector('[role^="menuitem"]:not(:disabled)')?.focus();
  }
  function openDialog(id, trigger = document.activeElement) {
    const dialog = document.getElementById(id);
    if (!(dialog instanceof HTMLDialogElement)) throw new Error('SageUI: missing dialog #' + id);
    const returnTarget = openMenu?.contains(trigger) ? menuFocusReturn : trigger;
    closeMenu(); dialogTriggers.set(dialog, returnTarget);
    if (!dialog.open) dialog.showModal();
    dialog.querySelector('[data-close-dialog],button:not(:disabled),input:not(:disabled)')?.focus();
  }
  function closeDialog(dialog) {
    if (typeof dialog === 'string') dialog = document.getElementById(dialog);
    if (!(dialog instanceof HTMLDialogElement)) throw new Error('SageUI: invalid dialog');
    dialog.close();
  }
  function toast(message, tone = 'info') {
    let node = document.getElementById('sage-ui-toast');
    if (!node) { node = document.createElement('div'); node.id = 'sage-ui-toast'; node.className = 'sg-toast'; node.setAttribute('aria-live', 'polite'); document.body.append(node); }
    node.setAttribute('role', tone === 'danger' ? 'alert' : 'status');
    node.dataset.tone = tone; node.textContent = message;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { node.textContent = ''; }, tone === 'danger' ? 5000 : 3200);
  }
  function chooseTab(button) {
    const tabs = button.closest('[data-tabs]');
    for (const item of tabs.querySelectorAll('[role=tab]')) {
      const selected = item === button;
      item.setAttribute('aria-selected', String(selected)); item.tabIndex = selected ? 0 : -1;
      const panel = document.getElementById(item.getAttribute('aria-controls'));
      if (panel) panel.hidden = !selected;
    }
    tabs.dispatchEvent(new CustomEvent('sage:tabchange', {bubbles: true, detail: {value: button.dataset.value || button.textContent.trim(), tab: button}}));
  }
  function init() {
    const root = document, element = document.documentElement;
    if (element.dataset.sageInitialized === 'true') return;
    element.dataset.sageInitialized = 'true';
    root.addEventListener('click', async event => {
      const button = event.target.closest('button,a');
      if (openMenu && !openMenu.contains(event.target) && !menuTrigger?.contains(event.target)) closeMenu();
      if (!button || button.disabled) return;
      if (button.dataset.themeValue) { setTheme(button.dataset.themeValue); closeMenu(true); return; }
      if (button.dataset.openDialog) { openDialog(button.dataset.openDialog, button); return; }
      if (button.hasAttribute('data-close-dialog')) { closeDialog(button.closest('dialog')); return; }
      if (button.dataset.menuToggle) { toggleMenu(button.dataset.menuToggle, button, event.detail === 0); return; }
      if (button.matches('[data-tabs] [role=tab]')) { chooseTab(button); return; }
      if (button.dataset.clear) {
        const input = document.getElementById(button.dataset.clear);
        if (!input) throw new Error('SageUI: missing input #' + button.dataset.clear);
        input.value = ''; input.dispatchEvent(new Event('input', {bubbles: true})); input.focus(); return;
      }
      if (button.dataset.copyTarget) {
        const source = document.getElementById(button.dataset.copyTarget);
        if (!source) throw new Error('SageUI: missing copy source #' + button.dataset.copyTarget);
        try { await navigator.clipboard.writeText(source.textContent); toast('HTML 已复制'); }
        catch (error) { toast('无法复制，请展开 HTML 后手动选择。', 'danger'); console.error('SageUI clipboard:', error.name); }
        return;
      }
      if (button.dataset.toast) { if (button.matches('a')) event.preventDefault(); toast(button.dataset.toast); }
      if (button.matches('[role^=menuitem]') && !button.dataset.menuToggle) closeMenu();
    });
    root.addEventListener('keydown', event => {
      if (event.key === 'Escape' && openMenu) { event.preventDefault(); closeMenu(true); return; }
      const tabs = event.target.closest('[data-tabs]');
      if (tabs && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
        const buttons = [...tabs.querySelectorAll('[role=tab]:not(:disabled)')];
        let index = buttons.indexOf(document.activeElement);
        index = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
        event.preventDefault(); chooseTab(buttons[index]); buttons[index].focus(); return;
      }
      if (openMenu?.contains(event.target) && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        const buttons = [...openMenu.querySelectorAll('[role^=menuitem]:not(:disabled)')].filter(b => b.getClientRects().length);
        if (!buttons.length) return;
        let index = buttons.indexOf(document.activeElement);
        index = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
        event.preventDefault(); buttons[index].focus();
      }
    });
    root.addEventListener('submit', event => {
      const form = event.target.closest('[data-composer]');
      if (!form) return;
      event.preventDefault();
      const input = form.querySelector('textarea');
      if (!input || !input.value.trim()) { input?.focus(); return; }
      form.dispatchEvent(new CustomEvent('sage:send', {bubbles: true, cancelable: true, detail: {text: input.value.trim(), form}}));
    });
    root.addEventListener('close', event => {
      if (!event.target.matches('dialog.sg-dialog')) return;
      const trigger = dialogTriggers.get(event.target);
      if (trigger?.isConnected) trigger.focus();
    }, true);
    root.addEventListener('click', event => {
      const dialog = event.target;
      if (!dialog.matches('dialog.sg-dialog')) return;
      const r = dialog.getBoundingClientRect();
      if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) closeDialog(dialog);
    });
    // A form sample never submits secrets or navigates accidentally.
    root.querySelectorAll('[data-login-example],[data-composer-example]').forEach(form => form.addEventListener('submit', event => event.preventDefault()));
  }
  window.SageUI = {setTheme, openDialog, closeDialog, toast, init};
  function ready() {
    init();
    const requested = new URLSearchParams(location.search).get('theme');
    setTheme(['dark', 'light', 'system'].includes(requested) ? requested : document.documentElement.dataset.theme || 'light');
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready, {once: true}); else ready();
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (document.documentElement.dataset.themePreference === 'system') setTheme('system'); });
  addEventListener('resize', () => closeMenu());
})(window);
