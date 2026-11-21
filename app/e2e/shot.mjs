// Usage: PLAYWRIGHT_MODULE=... node e2e/shot.mjs <route> <out.png> [width] [height] [theme]
// Reads the dev session cookie from ../tmp/dev-session.txt (see manage.py dev_session).
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(path.join(process.env.PLAYWRIGHT_MODULE, 'index.js'));
const [route = '/', out = 'shot.png', width = '1440', height = '900', theme] = process.argv.slice(2);
const base = process.env.SAGE_URL || 'http://127.0.0.1:4173';
// SAGE_NO_SESSION=1 shoots signed-out screens (login) without touching the shared session file.
const noSession = process.env.SAGE_NO_SESSION === '1';
const cookieFile = process.env.SAGE_SESSION_FILE || new URL('../../tmp/dev-session.txt', import.meta.url).pathname;
const [name, value] = noSession ? [] : readFileSync(cookieFile, 'utf8').trim().split('=');

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: +width, height: +height }, deviceScaleFactor: 1 });
if (!noSession) await context.addCookies([{ name, value, url: base }]);
const page = await context.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
page.on('console', (m) => m.type() === 'error' && errors.push('console: ' + m.text()));
page.on('response', (r) => r.url().includes('/api/') && r.status() >= 400 && errors.push(`api ${r.status()} ${r.url()}`));
// theme = <theme_style>[:light|dark]. ThemeProvider takes the server profile over its local cache
// ('sage.theme-prefs'), so the profile read is rewritten too; nothing is saved.
if (theme) {
  const [style, mode = 'light'] = theme.split(':');
  const prefs = { theme_style: style, appearance_mode: mode, density: 'theme' };
  await page.addInitScript((p) => localStorage.setItem('sage.theme-prefs', JSON.stringify(p)), prefs);
  await page.route('**/api/account/profile', async (r) => {
    if (r.request().method() !== 'GET') return r.continue();
    const res = await r.fetch();
    const body = await res.json();
    Object.assign(body.profile ?? body, prefs);
    await r.fulfill({ response: res, json: body });
  });
}
await page.goto(base + route, { waitUntil: 'networkidle' });
await page.screenshot({ path: out });
console.log(JSON.stringify({ route, out, url: page.url(), errors }));
await browser.close();
