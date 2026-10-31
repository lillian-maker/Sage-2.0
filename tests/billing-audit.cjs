// Local HTTP + browser readiness checks. Response fixtures below test UI failures,
// precision and races only; they do not claim real payment or account integration.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SAGE_URL || 'http://127.0.0.1:8900';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'Use a local test server, never production');
(async () => {
  const browser = await chromium.launch({channel:'chrome'});
  const page = await browser.newPage();
  page.setDefaultTimeout(10000);
  const errors = [], writes = [], quoteRequests = [];
  const catalogRequests = [];
  page.on('response', response => {
    const pathname = new URL(response.url()).pathname;
    if (['/api/billing/catalog','/pages/billing-catalog.json'].includes(pathname))
      catalogRequests.push({path:pathname,status:response.status()});
  });
  page.on('requestfailed', request => {
    const pathname = new URL(request.url()).pathname;
    if (['/api/billing/catalog','/pages/billing-catalog.json'].includes(pathname))
      catalogRequests.push({path:pathname,failure:request.failure()?.errorText});
  });
  const report = { local_http: [], response_fixtures: [], layouts: [], text_scaling: [] };
  let sessionUnavailable = false, anonymousSessionReads = 0;
  // Authentication is a response fixture here; reference quotes still use local HTTP.
  await page.route('**/api/auth/session', route => {
    anonymousSessionReads++;
    return route.fulfill({status:sessionUnavailable ? 503 : 200,contentType:'application/json',body:JSON.stringify(sessionUnavailable ?
      {error:{code:'UNAVAILABLE'}} : {authenticated:false,user:null,accounts:[],active_account_id:null,csrf_token:'fixture-only-csrf',capabilities:{email_login:false,phone_login:false,password_login:false,email_registration:false,google_login:false,google_registration:false}})});
  });
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/billing/quotes') quoteRequests.push(request.postDataJSON());
    else if (!['GET', 'HEAD'].includes(request.method())) writes.push(`${request.method()} ${new URL(request.url()).pathname}`);
  });
  const visit = async (route, suffix = '') => {
    await page.goto(`${base}/pages/${route}${suffix}`);
    // The initial loading shell also contains main; wait for the rendered page.
    await page.waitForSelector('main h1');
  };
  const blockedPayment = async () => {
    assert.equal(await page.locator('button.channel:not(:disabled)').count(), 0);
    assert.ok(await page.locator('button.channel:disabled').count() >= 5);
    assert.equal(await page.locator('.consent input:checked').count(), 0);
    assert.equal(await page.locator('.purchase-unavailable:disabled').count(), 1);
  };
  const settledQuote = async () => {
    await page.waitForFunction(() => {
      const status = document.querySelector('#quote-status');
      const button = document.querySelector('#quote-refresh');
      return status && status.textContent && button && !button.disabled;
    });
  };
  const noOverflow = async label => assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `${label}: horizontal overflow`);
  const english = async label => assert.equal(await page.locator('body').evaluate(el => /[\u4e00-\u9fff]/.test(el.innerText.replaceAll('中文',''))), false, `${label}: untranslated English`);
  const organizationNavigation = async (lang, unavailable = false) => {
    const beforeWrites = writes.length;
    const link = page.locator('.billing-center-sidebar > nav').getByRole('link', {name:lang ? 'Business management' : '企业管理',exact:true});
    assert.equal(await link.getAttribute('href'), `profile${lang}.html#organization`, 'business management must open personal settings');
    await link.click();
    await page.waitForURL(url => url.pathname.endsWith(`/profile${lang}.html`) && url.hash === '#organization');
    const content = page.locator('#sage-profile-data');
    await content.filter({hasText:unavailable ? (lang ? /Unable to load/ : /暂时无法读取/) : (lang ? /Sign in to view/ : /登录后查看/)}).waitFor();
    assert.equal(await content.locator('input:not(:disabled),select:not(:disabled),textarea:not(:disabled)').count(), 0, 'unavailable account must not accept business management data');
    assert.equal(writes.length, beforeWrites, 'unavailable business management navigation performs no business writes');
  };
  try {
    // Invalid URL values must never be silently turned into a different purchase.
    for (const query of ['?cycle=weekly', '?cycle=', '?audience=other', '?audience=business&seats=0', '?audience=business&seats=2.5']) {
      await visit('pricing.html', query);
      assert.equal(await page.locator('[role="alert"]').count(), 1, `invalid pricing selection ${query} must be explained`);
    }
    for (const query of ['?plan=missing', '?plan=', '?cycle=weekly', '?cycle=', '?plan=pro&seats=2', '?plan=teams&seats=0', '?plan=enterprise&seats=1', '?plan=enterprise&seats=9', '?plan=teams&seats=2.5', '?plan=teams&seats=10001']) {
      await visit('checkout.html', query);
      assert.equal(await page.locator('[role="alert"]').count(), 1, `invalid checkout selection ${query}`);
    }
    report.local_http.push('invalid pricing and checkout URL values rejected');
    // A heading is also present on the error page. Exercise both catalog paths
    // and recovery explicitly; a loading or error page is never a valid grid.
    for (const lang of ['', '-en']) for (const mode of ['delayed','api-failure','both-fail']) {
      let release, failing = true;
      const gate = new Promise(resolve=>release=resolve);
      await page.route('**/api/billing/catalog',async route=>{
        if (mode === 'delayed') await gate;
        if (mode !== 'delayed' && failing) return route.fulfill({status:503,contentType:'application/json',body:'{}'});
        return route.continue();
      });
      await page.route('**/pages/billing-catalog.json',route=>mode === 'both-fail' && failing ?
        route.fulfill({status:503,body:'Unavailable'}) : route.continue());
      try {
        await Promise.all([
          page.waitForRequest('**/api/billing/catalog'),
          page.goto(`${base}/pages/pricing${lang}.html`,{waitUntil:'domcontentloaded'})
        ]);
        if (mode === 'delayed') {
          assert.equal(await page.locator('main h1').count(),0);
          assert.equal(await page.locator('.plan').count(),0);
          release();
        }
        await page.locator('main h1').waitFor();
        if (mode === 'both-fail') {
          assert.equal(await page.locator('.plan').count(),0);
          assert.equal(await page.locator('main [role="alert"]').count(),1);
          assert.equal(await page.locator('#retry').count(),1);
          failing = false;
          await page.locator('#retry').click();
        }
        await page.locator('.plan').first().waitFor();
        assert.equal(await page.locator('.plan').count(),4);
        assert.equal(await page.locator('main [role="alert"]').count(),0);
        report.response_fixtures.push(`${lang || 'zh'} catalog ${mode}: valid grid or explicit failure, retry restores 4 plans`);
      } finally {
        release();
        await page.unroute('**/api/billing/catalog');
        await page.unroute('**/pages/billing-catalog.json');
      }
    }
    for (const lang of ['', '-en']) {
      for (const count of [1,9,10,11]) {
        await visit(`pricing${lang}.html`, `?audience=business&seats=${count}`);
        assert.equal(await page.locator('#pricing-seats,.seat-selector').count(), 0, 'Seat quantity belongs in checkout, not the plan comparison');
        assert.equal(await page.locator('.plan-grid.business > .plan').count(), 2, 'Business pricing has Teams and Enterprise only');
        assert.equal(new URL(await page.locator('[data-plan-link="teams"]').getAttribute('href'), page.url()).searchParams.get('seats'), String(count));
        const enterprise = page.locator('[data-plan-link="enterprise"]');
        assert.equal(new URL(await enterprise.getAttribute('href'), page.url()).searchParams.get('seats'), String(Math.max(count,10)), 'Enterprise entry starts at its minimum seat quantity');
        const enterpriseCard=page.locator('.plan').filter({has:enterprise});
        assert.equal(await enterpriseCard.locator('.plan-actions a').count(), 2, 'Enterprise review and sales actions share one card');
        const sales=enterpriseCard.getByRole('link',{name:lang?'Contact sales':'联系销售',exact:true});
        assert.equal(await sales.getAttribute('href'),`billing${lang}.html#procurement`);
        assert.deepEqual(await page.locator('.segmented').evaluateAll(nodes=>nodes.map(node=>getComputedStyle(node).borderTopWidth)),['0px','0px'], 'Both pricing toggles are borderless');
        for (const code of ['teams','enterprise']) {
          const card=page.locator('.plan').filter({has:page.locator(`[data-plan-link="${code}"]`)});
          assert.equal(await card.locator('.business-benefits').count(),1);
          assert.deepEqual(await card.locator('.plan-pending dd').allTextContents(),Array(3).fill(lang?'Pending':'待定'));
          assert.equal(await card.locator('.plan-pending .ui-icon').count(),0);
          assert.ok((await card.locator('.plan-includes').innerText()).includes(code==='teams'?(lang?'Individual included Credits':'套餐点数按成员使用'):(lang?'Shared included Credits':'套餐点数全员共享')));
          assert.ok((await card.locator('ul').innerText()).includes(lang?'Company-purchased add-on Credits are shared':'企业加购点数由成员共享'));
          assert.ok((await card.locator('.plan-release-note').innerText()).includes(lang?'never use private Credits':'不扣员工私人点数'));
        }
      }
      for (const count of [1,9]) {
        await visit(`checkout${lang}.html`, `?plan=enterprise&seats=${count}`);
        assert.equal(await page.locator('[role="alert"]').count(), 1, `${lang || 'zh'} Enterprise rejects ${count} seats`);
      }
      for (const [plan,count] of [['teams',1],['teams',9],['enterprise',10],['enterprise',11]]) {
        await visit(`checkout${lang}.html`, `?plan=${plan}&seats=${count}`);
        await settledQuote(); await blockedPayment();
        assert.equal(await page.locator('#checkout-seats').inputValue(), String(count));
        assert.equal(await page.locator('#checkout-seats').getAttribute('min'), plan === 'enterprise' ? '10' : '1');
        assert.equal(await page.locator('#quote-amount').innerText(), '—', 'Unpriced plans cannot become payable');
        assert.deepEqual(await page.locator('.business-benefits .plan-pending dd').allTextContents(),Array(3).fill(lang?'Pending':'待定'));
        assert.ok((await page.locator('.business-benefits').innerText()).includes(lang?'available balance':'可用余额'));
        if (plan === 'enterprise') {
          assert.equal(await page.locator('.business-benefits .plan-includes').innerText(),lang?'Shared included Credits':'套餐点数全员共享');
          await page.locator('#checkout-seats').fill('9');
          assert.equal(await page.locator('#checkout-seats').getAttribute('aria-invalid'), 'true');
          assert.equal(await page.locator('#quote-refresh').isDisabled(), true);
          assert.match(await page.locator('#checkout-seat-error').innerText(), /10/);
        }
      }
      report.local_http.push(`${lang || 'zh'} Teams 1-seat / Enterprise 10-seat minimum; 9 rejected for Enterprise; 11 allowed, no fixed-pack restriction; no payment`);
      await visit(`pricing${lang}.html`);
      assert.equal(await page.locator('.plan').count(), 4);
      for (const [code, includes] of [['pro', lang ? 'Personal business features' : '个人业务功能'], ['pro_plus', lang ? 'Everything in Pro' : '包含 Pro 全部功能'], ['ultra', lang ? 'Everything in Pro+' : '包含 Pro+ 全部功能']]) {
        const card = page.locator('.plan').filter({has:page.locator(`[data-plan-link="${code}"]`)});
        assert.equal(await card.locator('.plan-includes').innerText(), includes);
        assert.equal(await card.locator('.plan-pending dd').count(), 3);
        assert.deepEqual(await card.locator('.plan-pending dd').allTextContents(), Array(3).fill(lang ? 'Pending' : '待定'));
        assert.equal(await card.locator('.plan-pending .ui-icon').count(), 0, 'Unconfirmed details must not have a confirmed checkmark');
        assert.equal((await card.locator('ul').innerText()).includes(lang ? 'Early access to new features' : '优先体验新功能'), code !== 'pro');
        assert.equal(await card.locator('[data-plan-link]').innerText(), lang ? 'Review plan' : '查看方案');
        assert.ok((await card.locator('.price-note').innerText()).includes(lang ? 'Final price pending' : '正式价格待定'));
      }
      await page.locator('[data-plan-link="pro_plus"]').click();
      await settledQuote(); await blockedPayment();
      assert.equal(await page.locator('.plan-includes').innerText(), lang ? 'Everything in Pro' : '包含 Pro 全部功能');
      assert.deepEqual(await page.locator('.plan-pending dd').allTextContents(), Array(3).fill(lang ? 'Pending' : '待定'));
      await page.locator('.plan-return').click();
      await page.waitForSelector('.plan');
      report.local_http.push(`${lang || 'zh'} personal inheritance and early access; three pending details per tier and review page; payment remains disabled`);
      await page.locator('[data-cycle="annual"]').click();
      await page.waitForURL(url => url.searchParams.get('cycle') === 'annual');
      assert.equal(await page.locator('.price.pending').count(), 3);
      assert.deepEqual(await page.locator('.price.pending').allTextContents(), Array(3).fill(lang ? 'Price pending' : '价格待定'));
      assert.equal(new URL(page.url()).searchParams.get('cycle'), 'annual');
      await page.reload(); await page.waitForSelector('.plan');
      assert.equal(await page.locator('[data-cycle="annual"]').getAttribute('aria-pressed'), 'true');
      await page.locator('[data-audience="business"]').click();
      assert.equal(await page.locator('.plan').count(), 2);
      assert.equal(await page.locator('#pricing-seats').count(), 0);
      await page.locator('[data-plan-link="teams"]').click();
      await settledQuote(); await blockedPayment();
      assert.equal(await page.locator('#checkout-seats').inputValue(), '1');
      assert.equal(await page.locator('#quote-amount').innerText(), '—');
      await page.locator('#checkout-seats').fill('0');
      assert.equal(await page.locator('#checkout-seats').getAttribute('aria-invalid'), 'true');
      assert.equal(await page.locator('#quote-refresh').isDisabled(), true);
      await page.locator('#checkout-seats').fill('4');
      await page.locator('.plan-return').click();
      await page.waitForSelector('[data-plan-link="teams"]');
      assert.equal(await page.locator('#pricing-seats').count(), 0);
      assert.equal(new URL(page.url()).searchParams.get('seats'), '4');
      assert.equal(new URL(await page.locator('[data-plan-link="teams"]').getAttribute('href'), page.url()).searchParams.get('seats'), '4');
      assert.equal(await page.locator('[data-cycle="annual"]').getAttribute('aria-pressed'), 'true');
      await page.locator('[data-audience="personal"]').click();
      await page.locator('[data-cycle="monthly"]').click();
      await page.locator('[data-plan-link="pro"]').click();
      await settledQuote(); await blockedPayment();
      assert.match(await page.locator('#quote-amount').innerText(), /199\.00/);
      assert.equal(quoteRequests.at(-1).seat_quantity, 1, 'personal price must not inherit business seats');
      assert.match(await page.locator('.reference-label').innerText(), lang ? /reference/i : /参考/);
      assert.equal(await page.locator('#checkout-seats').count(), 0);
      assert.equal(await page.locator('.nav-actions > a[lang]').count(),0,'Use the shared language menu');
      await page.locator('.sage-language-toggle').click();
      await page.locator(`.sage-language-menu a[lang="${lang ? 'zh-CN' : 'en'}"]`).click(); await settledQuote();
      assert.equal(new URL(page.url()).searchParams.get('plan'), 'pro');
      assert.equal(new URL(page.url()).searchParams.get('cycle'), 'monthly');
      assert.match(await page.locator('#quote-amount').innerText(), /199\.00/);
      report.local_http.push(`${lang || 'zh'} plan selection / seat validation / refresh / back / language / personal 1-seat quote`);

      for (const route of ['pricing', 'checkout', 'billing']) {
        for (const width of [320,390,768,1440]) {
          await page.setViewportSize({width,height:1000});
          await visit(`${route}${lang}.html`);
          if (route === 'checkout') await settledQuote();
          await noOverflow(`${route}${lang}:${width}`);
          assert.equal(await page.evaluate(() => getComputedStyle(document.body).backgroundColor), 'rgb(255, 255, 255)');
          if (lang) await english(`${route}:${width}`);
          assert.equal(await page.locator('body').evaluate(el => /开发预览|fixture|服务器端口|API_UNAVAILABLE|Development preview/.test(el.innerText)), false);
          await page.waitForFunction(() => [...document.images].every(img => img.complete && img.naturalWidth > 0));
          report.layouts.push(`${route}${lang}:${width}`);
        }
      }
      for (const width of [320,768,1440]) {
        await page.setViewportSize({width,height:1000});
        await visit(`pricing${lang}.html`, '?audience=business&seats=10');
        await page.locator('main *').evaluateAll(nodes=>{const sizes=nodes.map(node=>parseFloat(getComputedStyle(node).fontSize));nodes.forEach((node,i)=>node.style.fontSize=`${sizes[i]*2}px`);});
        await noOverflow(`business-pricing${lang}:${width}:200%`);
        report.text_scaling.push(`business-pricing${lang}:${width}px text-size 200%`);
      }
      for (const width of [320,1440]) for (const section of ['subscription','credits','credits-ledger','orders','invoices','payment-methods','procurement']) {
        await page.setViewportSize({width,height:1000});
        await visit(`billing${lang}.html`, `#${section}`);
        assert.equal(await page.locator('.billing-management .account-menu a[aria-current]').getAttribute('href'), `#${section}`);
        await noOverflow(`billing${lang}#${section}:${width}`);
        if (lang) await english(`billing#${section}`);
        assert.equal(await page.locator('input:not(:disabled)').count(), 0, 'unavailable account must not pretend to accept billing data');
      }
      for (const width of [320,1440]) {
        await page.setViewportSize({width,height:1000});
        await visit(`billing${lang}.html`, '#organization');
        assert.equal(await page.locator('#account-section-title').innerText(), lang ? 'Business settings' : '企业管理');
        await page.locator('#account-live').filter({hasText:lang ? /Sign in to view/ : /登录后查看/}).waitFor();
        assert.equal(await page.locator('#account-live input:not(:disabled),#account-live select:not(:disabled),#account-live textarea:not(:disabled)').count(), 0, 'legacy business settings hash remains a safe read-only view');
        await noOverflow(`billing${lang}#organization legacy:${width}`);
        if (lang) await english('billing#organization legacy');
        await organizationNavigation(lang);
        await noOverflow(`profile${lang}#organization:${width}`);
        if (lang) await english('profile#organization');
        report.local_http.push(`${lang || 'zh'}:${width} legacy billing business settings / sidebar to profile business management / anonymous read-only denial`);
      }
      await visit(`pricing${lang}.html`);
      await page.locator('.nav-login').waitFor({state:'visible'});
      assert.equal(await page.locator('.nav-account').isVisible(),false,'No signed-in menu without an authenticated session');
      await page.locator('.nav-login').focus();await page.keyboard.press('Enter');
      await page.waitForURL(url=>url.pathname.endsWith(`/auth${lang}.html`));
      assert.equal(new URL(page.url()).searchParams.get('return'),`/pages/pricing${lang}.html`);
      await noOverflow(`anonymous sign in ${lang}`);
      await visit(`billing${lang}.html`,'#payment-methods');
      await page.waitForSelector('#account-section-title');
      assert.equal(new URL(page.url()).hash, '#payment-methods');
      await page.locator('.account-menu a[href="#procurement"]').click();
      await page.waitForFunction(() => document.activeElement?.id === 'account-section-title');
      await page.locator('.policy-block a').click();
      assert.ok(new URL(page.url()).pathname.endsWith(`/enterprise${lang}.html`));
      await page.waitForSelector('#cooperation');
      await page.locator('#ep-contact-button').click();
      await page.locator('.sage-consult-dialog[open]').waitFor();
      assert.equal(await page.locator('.sage-consult-dialog [type="submit"]').textContent(),lang ? 'Submit request' : '提交预约');
      await page.locator('.sage-consult-close').click();
      assert.equal(await page.locator('#ep-contact-button').evaluate(node=>node===document.activeElement),true);
      report.local_http.push(`${lang || 'zh'} billing sections / anonymous sign-in navigation / procurement path / keyboard focus`);
    }
    assert.ok(anonymousSessionReads>0,'Anonymous navigation must follow a confirmed session fixture response');
    report.response_fixtures.push('Anonymous session response, not merely a visible sign-in link');
    sessionUnavailable = true;
    const beforeUnknownWrites = writes.length;
    for (const lang of ['', '-en']) {
      await visit(`billing${lang}.html`,'#procurement');
      await page.locator('#account-live').filter({hasText:lang ? 'unknown' : '未知'}).waitFor();
      assert.equal(await page.locator('.nav-account').isVisible(),false);
      assert.equal(await page.locator('.account-metrics,#procurement-form').count(),0);
      assert.equal(await page.locator('#account-live input:not(:disabled)').count(),0);
      assert.equal(await page.locator('#account-reconnect').isVisible(),true);
      await organizationNavigation(lang, true);
    }
    assert.equal(writes.length,beforeUnknownWrites,'Unknown authentication performs no account or payment writes');
    sessionUnavailable = false;
    report.response_fixtures.push('Unknown session is distinct from signed out; no user menu, funds, editable procurement or writes');

    // Deterministic response fixtures: browser behavior, not live payment success.
    await page.route('**/api/**', route => route.fulfill({status:404,body:'Not found'}));
    await visit('checkout.html', '?plan=pro'); await settledQuote();
    assert.match(await page.locator('#quote-amount').innerText(), /199\.00/);
    assert.match(await page.locator('#quote-status').innerText(), /参考金额/);
    await blockedPayment(); await page.unroute('**/api/**');
    report.response_fixtures.push('API unavailable: clearly labeled static reference, no payable quote');

    let failures = true;
    await page.route('**/api/billing/quotes', async route => {
      if (failures) return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{code:'UNAVAILABLE'}})});
      return route.continue();
    });
    await visit('checkout.html', '?plan=pro'); await settledQuote();
    assert.equal(await page.locator('#quote-amount').innerText(), '—');
    assert.equal(await page.locator('#quote-status.error').count(), 1);
    failures = false; await page.locator('#quote-refresh').click(); await settledQuote();
    assert.match(await page.locator('#quote-amount').innerText(), /199\.00/);
    await blockedPayment(); await page.unroute('**/api/billing/quotes');
    report.response_fixtures.push('lookup failure and retry to real local reference');

    const reference = payload => ({...payload,currency:'CNY',amount_minor:19901,preview_only:true,purchasable:false});
    await page.route('**/api/billing/quotes', route => route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(reference(route.request().postDataJSON()))}));
    await visit('checkout-en.html', '?plan=pro'); await settledQuote();
    assert.match(await page.locator('#quote-amount').innerText(), /199\.01/); await blockedPayment();
    await page.unroute('**/api/billing/quotes');
    report.response_fixtures.push('CNY one-cent precision preserved in English');
    for (const override of [{currency:'USD'},{amount_minor:-1},{amount_minor:19900.5},{seat_quantity:999},{plan_code:'ultra'}]) {
      await page.route('**/api/billing/quotes', route => route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({...reference(route.request().postDataJSON()),...override})}));
      await visit('checkout.html', '?plan=pro'); await settledQuote();
      assert.equal(await page.locator('#quote-amount').innerText(), '—'); await blockedPayment();
      await page.unroute('**/api/billing/quotes');
    }
    report.response_fixtures.push('wrong currency / negative or fractional minor amount / wrong seats / wrong plan rejected');
    let releaseOld;
    const oldReady = new Promise(resolve => releaseOld = resolve);
    let sawOld;
    const requestedOld = new Promise(resolve => sawOld = resolve);
    await page.route('**/api/billing/quotes', async route => {
      const payload = route.request().postDataJSON();
      if (payload.seat_quantity === 2) { sawOld(); await oldReady; }
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({...reference(payload),amount_minor:payload.seat_quantity * 10000 + 1})});
    });
    await visit('checkout.html', '?plan=teams&seats=2'); await requestedOld;
    await page.locator('#checkout-seats').fill('3');
    await page.locator('#quote-refresh').click(); await settledQuote();
    assert.match(await page.locator('#quote-amount').innerText(), /300\.01/);
    const oldResponse = page.waitForResponse(response => response.url().endsWith('/api/billing/quotes') && response.request().postDataJSON()?.seat_quantity === 2);
    releaseOld(); await oldResponse;
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.match(await page.locator('#quote-amount').innerText(), /300\.01/);
    assert.equal(new URL(page.url()).searchParams.get('seats'), '3'); await blockedPayment();
    await page.unroute('**/api/billing/quotes');
    report.response_fixtures.push('late 2-seat response cannot overwrite current 3-seat amount');

    for (const lang of ['', '-en']) for (const route of ['pricing','checkout','billing']) for (const width of [390,1440]) {
      await page.setViewportSize({width,height:1000});
      await visit(`${route}${lang}.html`);
      await page.evaluate(() => {
        // Explicit 200% text-size simulation; not a claim about browser zoom.
        const targets = [...document.querySelectorAll('body, body *')].map(el => [el, parseFloat(getComputedStyle(el).fontSize)]);
        for (const [el,size] of targets) el.style.fontSize = `${size * 2}px`;
      });
      await noOverflow(`200% text ${route}${lang}:${width}`);
      report.text_scaling.push(`${route}${lang}:${width}px text-size 200%`);
    }
    assert.deepEqual(errors, [], 'no browser runtime exceptions');
    assert.deepEqual(writes, [], 'never issue account, order or payment writes');
    report.local_http.push(`no payment/account writes; ${quoteRequests.length} reference-only POSTs observed`);
    await page.setViewportSize({width:1440,height:1050}); await visit('pricing.html');
    if (process.env.SAGE_SCREENSHOT) await page.screenshot({path:process.env.SAGE_SCREENSHOT,fullPage:true});
    console.log(JSON.stringify(report,null,2));
  } catch (error) {
    // Anonymous local fixtures only. Keep structural failure evidence, never
    // response bodies, cookies, storage, credentials or a complete HTML dump.
    try {
      const parent = path.join(__dirname,'../tmp/billing-design-qa');
      fs.mkdirSync(parent,{recursive:true});
      const directory = fs.mkdtempSync(path.join(parent,'pricing-failure-'));
      const state = await page.evaluate(() => ({
        path:location.pathname,readyState:document.readyState,
        selection:Object.fromEntries(['audience','cycle','seats','plan'].map(key=>[key,new URLSearchParams(location.search).get(key)])),
        heading:document.querySelector('main h1')?.textContent || null,
        plans:document.querySelectorAll('.plan').length,
        planGrid:!!document.querySelector('#plans'),
        alerts:[...document.querySelectorAll('main [role="alert"]')].map(node=>node.textContent)
      }));
      fs.writeFileSync(path.join(directory,'state.json'),JSON.stringify({state,catalogRequests:catalogRequests.slice(-40),pageErrors:errors},null,2));
      await page.screenshot({path:path.join(directory,'page.png'),fullPage:true});
      console.error(`Local failure evidence: ${directory}`);
    } catch (evidenceError) { console.error('Failure evidence could not be saved:',evidenceError.name); }
    throw error;
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode = 1;});
