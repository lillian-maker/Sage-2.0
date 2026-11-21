// Usage: PLAYWRIGHT_MODULE=... node e2e/A-steps.mjs <route> <out.png> '<steps json>' [width] [height]
// Steps: [{"click":"text=…"}, {"fill":["selector","value"]}, {"press":"Meta+K"}, {"hover":"selector"}, {"wait":300}, {"shot":"path.png"}]
// Session handling matches shot.mjs (SAGE_NO_SESSION=1, SAGE_SESSION_FILE).
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(path.join(process.env.PLAYWRIGHT_MODULE, 'index.js'));
const [route = '/', out = 'shot.png', stepsJson = '[]', width = '1440', height = '900'] = process.argv.slice(2);
const base = process.env.SAGE_URL || 'http://127.0.0.1:4173';
const noSession = process.env.SAGE_NO_SESSION === '1';
const cookieFile = process.env.SAGE_SESSION_FILE || new URL('../../tmp/dev-session.txt', import.meta.url).pathname;
const [name, value] = noSession ? [] : readFileSync(cookieFile, 'utf8').trim().split('=');

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: +width, height: +height }, deviceScaleFactor: 1 });
if (!noSession) await context.addCookies([{ name, value, url: base }]);
const page = await context.newPage();
const errors = [];
const api = [];
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
page.on('console', (m) => m.type() === 'error' && errors.push('console: ' + m.text()));
page.on('response', (r) => {
  if (!new URL(r.url()).pathname.startsWith('/api/')) return;
  api.push(`${r.request().method()} ${r.status()} ${new URL(r.url()).pathname}`);
  if (r.status() >= 400) errors.push(`api ${r.status()} ${r.url()}`);
});
await page.goto(base + route, { waitUntil: 'networkidle' });
for (const step of JSON.parse(stepsJson)) {
  if (step.click) await page.locator(step.click).first().click();
  if (step.rightclick) await page.locator(step.rightclick).first().click({ button: 'right' });
  if (step.fill) await page.locator(step.fill[0]).first().fill(step.fill[1]);
  if (step.press) await page.keyboard.press(step.press);
  if (step.hover) await page.locator(step.hover).first().hover();
  if (step.wait) await page.waitForTimeout(step.wait);
  if (step.shot) await page.screenshot({ path: step.shot });
  await page.waitForLoadState('networkidle');
}
await page.screenshot({ path: out });
console.log(JSON.stringify({ route, out, url: page.url(), errors, api }));
await browser.close();
