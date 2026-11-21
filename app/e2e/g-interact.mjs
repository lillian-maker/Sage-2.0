// G 组交互截图：侧栏用量浮层、/usage 状态页、成员表 15px 角色标签。
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE + '/index.mjs');
import { readFileSync } from 'node:fs';

const BASE = process.env.SAGE_URL || 'http://127.0.0.1:5187';
const OUT = '/Users/ll/Documents/Sage/.scratch/figma-sync/q/dev/frontend/shots/G';
const [name, value] = readFileSync(process.env.SAGE_SESSION_FILE || '/Users/ll/Documents/Sage/Sage-2.0/tmp/dev-session.txt', 'utf8').trim().split('=');
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
await ctx.addCookies([{ name, value, url: BASE }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
page.on('response', (r) => r.status() >= 400 && errors.push(r.status() + ' ' + r.url()));

// 1. shell page: click the sidebar usage button -> popover
await page.goto(BASE + '/inbox', { waitUntil: 'networkidle' });
await page.click('button[aria-label="用量"]');
await page.waitForSelector('.sg-usagepop', { timeout: 5000 });
await page.waitForTimeout(600);
await page.screenshot({ path: OUT + '/sidebar-usage-popover.png' });

// 2. /usage states board
await page.goto(BASE + '/usage', { waitUntil: 'networkidle' });
await page.waitForTimeout(600);
await page.screenshot({ path: OUT + '/usage-board.png' });

// 3. members table with 15px role tags
await page.goto(BASE + '/settings/org/members', { waitUntil: 'networkidle' });
await page.waitForTimeout(600);
await page.screenshot({ path: OUT + '/org-members.png' });

console.log(JSON.stringify({ errors }, null, 2));
await browser.close();
