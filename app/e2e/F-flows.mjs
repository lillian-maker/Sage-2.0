// Usage: SAGE_URL=... PLAYWRIGHT_MODULE=... node e2e/F-flows.mjs <outDir>
// Interaction checks for 企业管理 pages. Opens menus and dialogs only; never submits a write.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(path.join(process.env.PLAYWRIGHT_MODULE, 'index.js'));
const outDir = process.argv[2] || '.';
const base = process.env.SAGE_URL || 'http://127.0.0.1:5186';
const cookieFile = process.env.SAGE_SESSION_FILE || new URL('../../tmp/dev-session.txt', import.meta.url).pathname;
const [name, value] = readFileSync(cookieFile, 'utf8').trim().split('=');

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
await context.addCookies([{ name, value, url: base }]);
const page = await context.newPage();
const errors = [];
const writes = [];
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
page.on('console', (m) => m.type() === 'error' && errors.push('console: ' + m.text()));
page.on('response', (r) => r.url().includes('/api/') && r.status() >= 400 && errors.push(`api ${r.status()} ${r.url()}`));
page.on('request', (r) => r.url().includes('/api/') && r.method() !== 'GET' && writes.push(`${r.method()} ${r.url()}`));
const steps = [];
const step = async (label, fn) => {
  try {
    const note = await fn();
    steps.push({ step: label, ok: true, note });
  } catch (e) {
    steps.push({ step: label, ok: false, note: String(e.message || e).split('\n')[0] });
  }
};

await step('members: open row menu', async () => {
  await page.goto(base + '/settings/org/members', { waitUntil: 'networkidle' });
  const row = page.getByRole('row').filter({ hasText: 'Mark Weber' });
  await row.getByRole('button', { name: /的操作$/ }).click();
  const items = await page.getByRole('menu', { name: '成员操作' }).getByRole('menuitem').allInnerTexts();
  await page.screenshot({ path: `${outDir}/758-members-menu.png` });
  return items.join(' / ');
});
await step('members: 办理离职 opens 78 panel', async () => {
  // membership ids depend on seed order, so resolve Mark Weber's id instead of assuming one
  const people = await (await fetch(base + '/api/app/org/people', { headers: { cookie: `${name}=${value}` } })).json();
  const mark = (people.members || []).find((m) => m.display_name === 'Mark Weber');
  if (!mark) throw new Error('Mark Weber not in GET app/org/people');
  await page.getByRole('menuitem', { name: '办理离职' }).click();
  await page.waitForURL(`**/settings/org/members/${mark.membership_id}/offboard`);
  const dialog = page.getByRole('dialog');
  await dialog.getByText('其他交接').waitFor();
  const confirm = dialog.getByRole('button', { name: '确认离职' });
  const before = await confirm.isDisabled();
  const select = dialog.getByLabel('接手人');
  const first = await select.locator('option').nth(1).getAttribute('value');
  await select.selectOption(first);
  // the visible .sg-checkbox__box sits over the visually hidden input; clicking it through the label is the user path
  await dialog.getByRole('checkbox').check({ force: true });
  const after = await confirm.isDisabled();
  await page.screenshot({ path: `${outDir}/760-offboard-ready.png` });
  return `title=${await dialog.getByRole('heading').innerText()} disabledBefore=${before} disabledAfter=${after} (not submitted)`;
});
await step('offboard: 取消 returns to 75', async () => {
  await page.getByRole('dialog').getByRole('button', { name: '取消' }).click();
  await page.waitForURL('**/settings/org/members');
  return page.url();
});
await step('members: department filter', async () => {
  await page.getByRole('button', { name: /^运营部/ }).click();
  await page.waitForLoadState('networkidle');
  const rows = await page.getByRole('table', { name: '成员' }).getByRole('row').count();
  return `运营部 rows (incl. head)=${rows}`;
});
await step('members: 新建部门 dialog (cancelled)', async () => {
  await page.getByRole('button', { name: '新建部门' }).click();
  await page.getByLabel('部门名称').fill('测试部门');
  const enabled = await page.getByRole('dialog').getByRole('button', { name: '创建' }).isEnabled();
  await page.screenshot({ path: `${outDir}/758-new-department.png` });
  await page.getByRole('dialog').getByRole('button', { name: '取消' }).click();
  return `create enabled=${enabled}`;
});
await step('quota: select 市场部 and open 设置部门额度 (cancelled)', async () => {
  await page.goto(base + '/settings/org/quota', { waitUntil: 'networkidle' });
  await page.getByRole('row').filter({ hasText: '市场部' }).first().click();
  await page.getByRole('heading', { name: '市场部 · 成员额度' }).waitFor();
  await page.getByRole('button', { name: '设置部门额度' }).click();
  const title = await page.getByRole('dialog').getByRole('heading').innerText();
  await page.screenshot({ path: `${outDir}/745-dept-quota-dialog.png` });
  await page.keyboard.press('Escape');
  return title;
});
await step('policy: open 需要审批的操作 menu', async () => {
  await page.goto(base + '/settings/org/policy', { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: /支付|对外发布|安装第三方扩展|不需要审批/ }).first().click();
  const items = await page.getByRole('menu', { name: '需要审批的操作' }).getByRole('menuitemradio').evaluateAll((els) =>
    els.map((e) => `${e.textContent.trim()}:${e.getAttribute('aria-checked')}`),
  );
  await page.screenshot({ path: `${outDir}/759-approval-menu.png` });
  await page.keyboard.press('Escape');
  return items.join(' ');
});

console.log(JSON.stringify({ steps, writes, errors }, null, 1));
await browser.close();
