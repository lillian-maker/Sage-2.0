// Usage: SAGE_URL=... PLAYWRIGHT_MODULE=... node e2e/B-flow.mjs <outDir>
// Walks the 02/03/05 task flows on a freshly created task; the main seeded task is only read.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(path.join(process.env.PLAYWRIGHT_MODULE, 'index.js'));
const out = process.argv[2] || '.';
const base = process.env.SAGE_URL || 'http://127.0.0.1:5182';
const cookieFile = process.env.SAGE_SESSION_FILE || new URL('../../tmp/dev-session.txt', import.meta.url).pathname;
const [name, value] = readFileSync(cookieFile, 'utf8').trim().split('=');
import { seedIds } from './seed-ids.mjs';
const ids = await seedIds(base);
const MAIN = ids.acceptedTask;
const SIDE = ids.richTask;

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
await context.addCookies([{ name, value, url: base }]);
const page = await context.newPage();
const errors = [];
const log = [];
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
page.on('console', (m) => m.type() === 'error' && errors.push('console: ' + m.text()));
page.on('response', (r) => r.url().includes('/api/') && r.status() >= 400 && errors.push(`api ${r.status()} ${r.request().method()} ${r.url()}`));
const until = async (fn, ms = 15000) => {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (fn(new URL(page.url()))) return true;
    await page.waitForTimeout(200);
  }
  return false;
};
const shot = async (n) => {
  await page.waitForLoadState('networkidle');
  await page.screenshot({ path: path.join(out, n + '.png') });
  log.push({ shot: n, url: page.url() });
};

// Picker: pick one expert, confirm, chip appears.
await page.goto(base + '/tasks/new', { waitUntil: 'networkidle' });
await page.getByRole('button', { name: '专家' }).click();
await page.waitForSelector('.sg-picker__row');
await page.locator('.sg-picker__row').first().click();
await shot('2520-163-experts-selected');
await page.getByRole('button', { name: '确认', exact: true }).click();
await shot('630-2-with-chip');
log.push({ chips: await page.locator('.sg-newtask__chips .sg-tag').count() });

// Create a task.
await page.locator('.sg-composer__input').fill('B组联调：整理德国站退货政策要点，给出一页摘要');
await page.keyboard.press('Enter');
if (!(await until((u) => !u.pathname.endsWith('/tasks/new') || u.hash === '#confirm'))) {
  await shot('create-failed');
  console.log(JSON.stringify({ log, errors }, null, 1));
  process.exit(1);
}
await page.waitForTimeout(800);
await shot('713-2-after-create');
const url = new URL(page.url());
let taskId = url.searchParams.get('task') || url.pathname.split('/')[2];
log.push({ taskId, confirm: url.hash === '#confirm' });
if (url.hash === '#confirm') {
  await page.getByRole('button', { name: '开始执行' }).click();
  const ok = await until((u) => u.pathname === `/tasks/${taskId}`, 6000);
  await shot(ok ? '2020-2-started' : '713-2-start-error');
  log.push({ started: ok });
  if (!ok) await page.goto(base + `/tasks/${taskId}`, { waitUntil: 'networkidle' });
}

// Send a follow-up, then open edit mode on it.
await page.locator('.sg-composer__input').fill('补充：只看 2026 年的新规');
await page.keyboard.press('Enter');
await page.waitForTimeout(1500);
await shot('633-2-new-task-after-send');
await page.goto(base + `/tasks/${taskId}#editing`, { waitUntil: 'networkidle' });
await page.waitForTimeout(500);
await shot('684-2-editing');
log.push({ editBox: await page.locator('.sg-conv__edit').count() });

// Orchestration of the new task.
await page.goto(base + `/tasks/${taskId}/orchestration`, { waitUntil: 'networkidle' });
await shot('658-2-new-task');

// Return-reason validation on the main task without submitting anything.
await page.goto(base + `/tasks/${MAIN}#acceptance`, { waitUntil: 'networkidle' });
await page.waitForTimeout(500);
const ret = page.getByRole('button', { name: '退回修改' });
if (await ret.count()) {
  await ret.click();
  await shot('2089-2924-return-input');
  await page.getByRole('button', { name: '确认退回' }).click();
  await shot('2089-3380-required');
} else log.push({ acceptance: 'no delivery card' });

// Side chat panel deep link.
await page.goto(base + `/tasks/${SIDE}?panel=side-chat`, { waitUntil: 'networkidle' });
await shot('653-2-side-chat');
await page.goto(base + `/tasks/${SIDE}?panel=tabs`, { waitUntil: 'networkidle' });
await shot('683-2-tabs');
await page.goto(base + `/tasks/${SIDE}#summary`, { waitUntil: 'networkidle' });
await shot('681-2-summary');
await page.goto(base + `/tasks/${SIDE}#parallel`, { waitUntil: 'networkidle' });
await shot('680-2-parallel');

console.log(JSON.stringify({ log, errors }, null, 1));
await browser.close();
