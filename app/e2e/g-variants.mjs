// 注入新变体元素做视觉核对（danger 按钮、Tag 15、Kbd 数组、Checkbox、Select prefix 的静态样式）。
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE + '/index.mjs');
import { mkdirSync, readFileSync } from 'node:fs';
const BASE = process.env.SAGE_URL;
const OUT = process.env.SHOTS || new URL('../../tmp/e2e-shots/G', import.meta.url).pathname;
mkdirSync(OUT, { recursive: true });
const [name, value] = readFileSync(process.env.SAGE_SESSION_FILE || new URL('../../tmp/dev-session.txt', import.meta.url).pathname, 'utf8').trim().split('=');
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
await ctx.addCookies([{ name, value, url: BASE }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));

await page.goto(BASE + '/inbox', { waitUntil: 'networkidle' });
await page.evaluate(() => {
  const host = document.createElement('div');
  host.id = 'g-demo';
  host.style.cssText = 'position:fixed;inset:0;z-index:999;background:#fff;padding:32px;display:flex;flex-direction:column;gap:16px;align-items:flex-start;';
  host.innerHTML = `
    <div style="display:flex;gap:12px;align-items:center">
      <button class="sg-btn sg-btn--danger sg-btn--s32"><span class="sg-btn__label">确认离职</span></button>
      <button class="sg-btn sg-btn--danger sg-btn--s32" disabled><span class="sg-btn__label">确认离职（disabled）</span></button>
      <span class="sg-tag sg-tag--neutral sg-tag--s15"><span class="sg-tag__label">所有者</span></span>
      <span class="sg-tag sg-tag--info sg-tag--s15"><span class="sg-tag__label">部门负责人</span></span>
      <span class="sg-kbd-group"><kbd class="sg-kbd">⌘</kbd><kbd class="sg-kbd">⇧</kbd><kbd class="sg-kbd">K</kbd></span>
    </div>
    <label class="sg-checkbox">
      <input type="checkbox" checked />
      <span class="sg-checkbox__box" aria-hidden="true"></span>
      <span class="sg-checkbox__label">我已知晓：该成员的个人记忆将被永久删除</span>
    </label>`;
  document.body.appendChild(host);
});
await page.waitForTimeout(300);
await page.screenshot({ path: OUT + '/new-variants.png', clip: { x: 0, y: 0, width: 900, height: 220 } });
await page.evaluate(() => document.getElementById('g-demo')?.remove());

// 离职对话框（Checkbox 实际使用处）
await page.goto(BASE + '/settings/org/members', { waitUntil: 'networkidle' });
await page.waitForTimeout(500);
const more = page.locator('table [aria-label], .sg-orgtable button').last();
// 点击成员行的 “...” 菜单
const dots = page.locator('button:has-text("...")');
await dots.first().click().catch(() => {});
await page.waitForTimeout(400);
await page.screenshot({ path: OUT + '/org-members-menu.png' });
const off = page.locator('text=办理离职');
if (await off.count()) {
  await off.first().click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: OUT + '/org-offboard-dialog.png' });
}
console.log(JSON.stringify({ errors, offboard: await off.count() }));
await browser.close();
