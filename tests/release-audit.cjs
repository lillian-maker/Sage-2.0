// PLAYWRIGHT_MODULE may point to an existing Playwright installation.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SAGE_URL || 'http://127.0.0.1:8896';
(async () => {
  const browser = await chromium.launch({ channel: 'chrome' });
  const errors = [];
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(() => {
    window.auditTimers = [];
    const original = window.setInterval;
    window.setInterval = (fn, ms, ...args) => {
      // Batch coverage has one clock source; real-time behavior is tested separately.
      if (ms === 3000) {
        window.auditTimers.push(() => fn(...args));
        return 0;
      }
      return original(fn, ms, ...args);
    };
  });
  const report = { batches: 0, responsive: [], links: [], realTime: [], keyboard: [], consultations: [] };
  // Catch a mobile menu placed before its trigger: forward Tab must enter it.
  async function navigationKeyboard(url) {
    for (const width of [320, 390, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.goto(url);
      await page.mouse.move(0, 0);
      const language = page.locator('.sage-language-toggle');
      if (await language.count()) {
        await language.focus();
        await page.keyboard.press('ArrowDown');
        assert.equal(await page.locator('.sage-language-menu a').first().evaluate(node => node === document.activeElement), true, `${url}/${width}: enter language links`);
        await page.keyboard.press('Escape');
        assert.equal(await language.evaluate(node => node === document.activeElement), true, `${url}/${width}: restore language focus`);
        assert.equal(await page.locator('.sage-language-menu').isVisible(), false);
      }
      const toggle = page.locator('.menu-toggle,.cp-menu,#r-menu-toggle');
      if (await toggle.count() && await toggle.isVisible()) {
        await toggle.focus();
        await page.keyboard.press('Enter');
        assert.equal(await toggle.getAttribute('aria-expanded'), 'true', `${url}/${width}: keyboard opens navigation`);
        await page.keyboard.press('Tab');
        assert.equal(await page.locator('.mobile-nav a,.cp-links a,#r-menu a').first().evaluate(node => node === document.activeElement), true, `${url}/${width}: Tab enters first navigation link`);
        await page.keyboard.press('Escape');
        assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
        assert.equal(await toggle.evaluate(node => node === document.activeElement), true, `${url}/${width}: restore navigation focus`);
      }
      const consultCandidates = page.locator('a,button').filter({ hasText: /^(咨询和演示|Contact sales)\s*[→↗]?$/ });
      let consult;
      for (const candidate of await consultCandidates.all()) {
        if (await candidate.isVisible()) { consult = candidate; break; }
      }
      if (consult && !url.includes('index')) {
        await consult.focus();
        await page.keyboard.press('Enter');
        const dialog = page.locator('dialog.sage-consult-dialog');
        await dialog.waitFor({ state: 'visible' });
        assert.equal(await dialog.evaluate(node => node.matches(':modal') && node.contains(document.activeElement)), true, `${url}/${width}: consultation opens modally with focus inside`);
        for (const key of ['Shift+Tab', ...Array(10).fill('Tab')]) {
          await page.keyboard.press(key);
          // Native dialogs may pass through browser chrome (activeElement is
          // the non-focusable body); they must never enter background controls.
          assert.equal(await dialog.evaluate(node => node.contains(document.activeElement) || document.activeElement === document.body && document.body.tabIndex === -1 && !document.hasFocus()), true, `${url}/${width}: consultation excludes background focus on ${key}`);
        }
        await page.keyboard.press('Escape');
        await dialog.waitFor({ state: 'hidden' });
        assert.equal(await consult.evaluate(node => node === document.activeElement), true, `${url}/${width}: restore consultation focus`);
        report.consultations.push(`${new URL(url).pathname}:${width}`);
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, `${url}/${width}: no horizontal overflow`);
      report.keyboard.push(`${new URL(url).pathname}:${width}`);
    }
  }
  try {
    if (process.argv.includes('--keyboard')) {
      for (const entry of ['index.html', 'index-en.html', ...['team', 'enterprise', 'docs', 'pricing', 'trial', 'download', 'checkout', 'billing', 'profile'].flatMap(name => [`pages/${name}.html`, `pages/${name}-en.html`])]) {
        await navigationKeyboard(`${base}/${entry}`);
      }
      assert.deepEqual(errors, []);
      console.log(JSON.stringify({ keyboard: report.keyboard, consultations: report.consultations, pageErrors: errors.length }, null, 2));
      return;
    }
    await page.route('**/assets/people/**', route => route.abort());
    for (const entry of ['index.html', 'index-en.html']) {
      await page.goto(`${base}/${entry}`);
      const languageButton = page.locator('.sage-language-toggle');
      const languageMenu = page.locator('.sage-language-menu');
      await languageButton.click();
      assert.equal(await languageMenu.isVisible(), true);
      assert.equal(await languageMenu.locator('a').count(), 2);
      await page.keyboard.press('Escape');
      assert.equal(await languageMenu.isVisible(), false);
      await languageButton.focus();
      await page.keyboard.press('ArrowDown');
      assert.equal(await page.evaluate(() => document.activeElement.textContent), 'English');
      await page.keyboard.press('ArrowDown');
      assert.equal(await page.evaluate(() => document.activeElement.textContent), '中文');
      await page.keyboard.press('Escape');
      await page.locator('#scene-stage').scrollIntoViewIfNeeded();
      await page.mouse.move(0, 0);
      await page.waitForTimeout(400);
      assert.equal(await page.evaluate(() => window.auditTimers.length), 1, `${entry}: one 3000ms workflow timer`);
      const stages = new Set();
      for (let i = 0; i < 45; i++) {
        await page.waitForFunction(() => [...document.querySelectorAll('#relay-people img')]
          .every(img => img.complete && img.naturalWidth > 0));
        stages.add(await page.evaluate(() => document.querySelector('#scene-title').textContent + ':' + document.querySelector('#relay-title').textContent));
        await page.evaluate(() => window.auditTimers.forEach(fn => fn()));
        await page.waitForTimeout(30);
        report.batches++;
      }
      assert.equal(stages.size, 45, `${entry}: all workflow batches must be exercised`);
      await page.emulateMedia({ reducedMotion: 'reduce' });
      for (const width of [320, 390, 768, 1024, 1440, 1920]) {
        await page.setViewportSize({ width, height: 1000 });
        await page.evaluate(() => scrollTo(0, 0));
        const layout = await page.evaluate(() => {
          const title = document.querySelector('#hero-title').getBoundingClientRect();
          const globe = document.querySelector('#globe-stage').getBoundingClientRect();
          const range = document.createRange();range.selectNodeContents(document.querySelector('#hero-title'));
          const text = range.getBoundingClientRect();
          return { overflow: document.documentElement.scrollWidth > innerWidth + 1,
            titleOverflow: text.right > title.right + 2,
            overlap: text.right > globe.left && text.left < globe.right && text.bottom > globe.top && text.top < globe.bottom };
        });
        assert.equal(layout.overflow, false, `${entry} ${width}: horizontal overflow`);
        assert.equal(layout.titleOverflow, false, `${entry} ${width}: title exceeds its container`);
        assert.equal(layout.overlap, false, `${entry} ${width}: title overlaps globe`);
        report.responsive.push(`${entry}:${width}`);
      }
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      await page.locator('#contact').scrollIntoViewIfNeeded();
      await page.waitForFunction(() => document.querySelector('.nav').classList.contains('nav-light'));
      await page.locator('.nav .brand').focus();
      const focus = await page.locator('.nav .brand').evaluate(node => {
        const style = getComputedStyle(node);
        const luminance = color => {
          const values = color.match(/[\d.]+/g).slice(0, 3).map(Number).map(value => {
            const s = value / 255; return s <= .04045 ? s / 12.92 : ((s + .055) / 1.055) ** 2.4;
          });
          return values[0] * .2126 + values[1] * .7152 + values[2] * .0722;
        };
        // The light header is white at 97% opacity over the white content.
        return { visible: node.matches(':focus-visible'), width: parseFloat(style.outlineWidth), style: style.outlineStyle,
          contrastOnWhite: 1.05 / (luminance(style.outlineColor) + .05), color: style.outlineColor };
      });
      assert.equal(focus.visible, true);
      assert.ok(focus.width >= 2 && focus.style !== 'none' && focus.contrastOnWhite >= 3, `${entry}: light header focus must remain visible: ${JSON.stringify(focus)}`);
    }
    await page.unroute('**/assets/people/**');
    const links = await page.locator('a[href]').evaluateAll(nodes => [...new Set(nodes.map(x => x.href).filter(x => x.startsWith(location.origin) && !x.includes('#')))]);
    // The last homepage is English; its links alone omit the Chinese pages and
    // account/checkout entry points. Verify the complete shared release routes.
    const routes = ['team','enterprise','docs','pricing','trial','download','checkout','billing','auth','profile']
      .flatMap(name => [name, name + '-en']).concat('product');
    for (const url of new Set([...links, ...routes.map(name => `${base}/pages/${name}.html`)])) {
      const response = await page.goto(url);
      assert.equal(response.status(), 200, url);
      await page.waitForTimeout(150);
      assert.ok(await page.locator('main').count(), `${url}: missing main`);
      await page.locator('img').evaluateAll(nodes => nodes.forEach(img => img.loading = 'eager'));
      await page.waitForFunction(() => [...document.images].every(img => img.complete && img.naturalWidth > 0));
      report.links.push(url);
    }
    for (const entry of ['index.html', 'index-en.html', ...routes.filter(name => name !== 'product').map(name => `pages/${name}.html`)]) {
      await navigationKeyboard(`${base}/${entry}`);
    }
    await page.goto(`${base}/index.html`);
    await page.locator('.sage-language-toggle').click();
    await page.locator('.sage-language-menu a[lang="en"]').click();
    await page.waitForURL('**/index-en.html');
    assert.equal(await page.locator('html').getAttribute('lang'), 'en');
    await page.locator('.sage-language-toggle').click();
    await page.locator('.sage-language-menu a[lang="zh-CN"]').click();
    await page.waitForURL('**/index.html');
    assert.equal(await page.locator('html').getAttribute('lang'), 'zh-CN');

    // Observe native intervals without manually invoking callbacks or changing time.
    const livePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'no-preference' });
    livePage.on('pageerror', e => errors.push(e.message));
    await livePage.addInitScript(() => {
      window.auditNativeTicks = [];
      window.auditNativePeriods = [];
      const original = window.setInterval;
      const state = () => document.querySelector('#scene-title')?.textContent + ':' + document.querySelector('#relay-title')?.textContent;
      window.setInterval = (fn, ms, ...args) => {
        if (ms !== 3000) return original(fn, ms, ...args);
        window.auditNativePeriods.push(ms);
        return original(() => {
          const before = state(), at = performance.now();
          const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
          fn(...args);
          window.auditNativeTicks.push({ at, before, after: state(), reduced, hidden: document.hidden });
        }, ms);
      };
    });
    try {
      for (const entry of ['index.html', 'index-en.html']) {
        await livePage.goto(`${base}/${entry}`);
        await livePage.bringToFront();
        await livePage.locator('#scene-stage').scrollIntoViewIfNeeded();
        await livePage.mouse.move(0, 0);
        assert.deepEqual(await livePage.evaluate(() => window.auditNativePeriods), [3000], `${entry}: native interval is 3 seconds`);
        await livePage.waitForFunction(() => window.auditNativeTicks.filter(t => !t.reduced && t.before !== t.after).length >= 2,
          null, { timeout: 15000, polling: 100 });
        const running = await livePage.evaluate(() => window.auditNativeTicks.filter(t => !t.reduced && t.before !== t.after).slice(0, 2));
        const interval = running[1].at - running[0].at;
        assert.ok(interval >= 2800 && interval <= 6000, `${entry}: native interval was ${Math.round(interval)}ms`);

        await livePage.emulateMedia({ reducedMotion: 'reduce' });
        const pausedState = await livePage.evaluate(() => document.querySelector('#scene-title').textContent + ':' + document.querySelector('#relay-title').textContent);
        await livePage.waitForFunction(() => window.auditNativeTicks.filter(t => t.reduced).length >= 2,
          null, { timeout: 15000, polling: 100 });
        const paused = await livePage.evaluate(() => window.auditNativeTicks.filter(t => t.reduced));
        assert.ok(paused.every(t => !t.hidden && t.before === pausedState && t.after === pausedState), `${entry}: reduced motion must pause native ticks`);

        await livePage.emulateMedia({ reducedMotion: 'no-preference' });
        const resumeFrom = await livePage.evaluate(() => window.auditNativeTicks.length);
        await livePage.waitForFunction(start => window.auditNativeTicks.slice(start).some(t => !t.reduced && !t.hidden && t.before !== t.after),
          resumeFrom, { timeout: 15000, polling: 100 });
        report.realTime.push({ entry, intervalMs: Math.round(interval), autoAdvances: running.length, pausedTicks: paused.length, resumed: true });
      }
    } finally { await livePage.close(); }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify(report, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
