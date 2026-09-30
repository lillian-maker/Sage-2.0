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
      if (ms === 3000) window.auditTimers.push(fn);
      return original(fn, ms, ...args);
    };
  });
  const report = { batches: 0, responsive: [], links: [] };
  try {
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
    }
    await page.unroute('**/assets/people/**');
    const links = await page.locator('a[href]').evaluateAll(nodes => [...new Set(nodes.map(x => x.href).filter(x => x.startsWith(location.origin) && !x.includes('#')))]);
    for (const url of links) {
      const response = await page.goto(url);
      assert.equal(response.status(), 200, url);
      await page.waitForTimeout(150);
      assert.ok(await page.locator('main').count(), `${url}: missing main`);
      await page.locator('img').evaluateAll(nodes => nodes.forEach(img => img.loading = 'eager'));
      await page.waitForFunction(() => [...document.images].every(img => img.complete && img.naturalWidth > 0));
      report.links.push(url);
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
    assert.deepEqual(errors, []);
    console.log(JSON.stringify(report, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
