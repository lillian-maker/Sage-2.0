// C 组写入流验收：新建工作区 → 加规则 → 邀请/改角色/移除成员 → 删临时工作区 → 成果重命名/回收站撤销。
// 只动本脚本创建的临时工作区与一条可还原的成果名称，不碰种子数据。
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(path.join(process.env.PLAYWRIGHT_MODULE, 'index.js'));
const base = process.env.SAGE_URL || 'http://127.0.0.1:5183';
const [name, value] = readFileSync(process.env.SAGE_SESSION_FILE || new URL('../../tmp/dev-session.txt', import.meta.url).pathname, 'utf8').trim().split('=');

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await context.addCookies([{ name, value, url: base }]);
const page = await context.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
const step = (ok, label) => {
  console.log((ok ? 'PASS' : 'FAIL') + ' ' + label);
  if (!ok) process.exitCode = 1;
};

// 1. 新建工作区（空白）
await page.goto(base + '/workspaces/new', { waitUntil: 'networkidle' });
await page.getByPlaceholder('例如：法国市场进入').fill('C组验收临时');
await page.getByRole('button', { name: '创建', exact: true }).click();
await page.waitForURL(/\/workspaces\/(?!new)[^/]+$/, { timeout: 10000 });
const wsUrl = page.url();
const wsId = wsUrl.split('/').pop();
step(/\/workspaces\//.test(wsUrl), 'create workspace -> ' + wsUrl);
await page.waitForSelector('.sg-ws__head h1:text-is("C组验收临时")', { timeout: 10000 });
step(true, 'workspace header shows name');

// 2. 规则：新增一条，生成 v1
await page.goto(base + `/workspaces/${wsId}/rules`, { waitUntil: 'networkidle' });
await page.getByLabel('添加一条规则').fill('所有输出使用简体中文');
await page.getByLabel('添加一条规则').press('Enter');
await page.waitForSelector('.sg-rules__version:has-text("v1")', { timeout: 10000 });
step(true, 'rule added, version v1 listed');
step((await page.locator('.sg-rules__item .sg-rules__text').innerText()) === '所有输出使用简体中文', 'rule text rendered');

// 3. 成员：邀请 → 改角色 → 移除
await page.goto(base + `/workspaces/${wsId}/members`, { waitUntil: 'networkidle' });
await page.getByRole('button', { name: '邀请成员' }).click();
await page.getByLabel('邀请邮箱').fill('chen@sage.demo');
await page.getByRole('button', { name: '发送邀请' }).click();
const chenRow = page.locator('.sg-members__row', { hasText: 'chen@sage.demo' }).first();
await chenRow.waitFor({ timeout: 10000 });
step(true, 'member invited');
await chenRow.locator('select').selectOption('viewer');
await page.waitForResponse((r) => r.url().includes('/members') && r.request().method() === 'GET' && r.status() === 200);
step(true, 'role changed to viewer');
await chenRow.getByLabel(/的操作/).click();
await page.getByRole('menuitem', { name: /移出工作区/ }).click();
await page.getByRole('button', { name: '移出', exact: true }).click();
await page.waitForTimeout(800);
step((await page.locator('.sg-members__row', { hasText: 'chen@sage.demo' }).count()) === 0, 'member removed');

// 4. 删除临时工作区
await page.goto(base + `/workspaces/${wsId}`, { waitUntil: 'networkidle' });
await page.getByRole('button', { name: '更多', exact: true }).click();
await page.getByRole('menuitem', { name: '删除工作区' }).click();
await page.getByRole('button', { name: '删除工作区', exact: true }).click();
await page.waitForURL(/\/tasks/, { timeout: 10000 });
step(true, 'temp workspace deleted, landed on /tasks');

// 5. 成果：重命名往返 + 回收站撤销
await page.goto(base + '/artifacts', { waitUntil: 'networkidle' });
const row = page.locator('.sg-arts__row', { hasText: '竞品月销量估算' }).first();
await row.hover();
await row.getByLabel(/的更多操作/).click();
await page.getByRole('menuitem', { name: '重命名' }).click();
await page.getByLabel('成果名称').fill('C组临时名称');
await page.getByRole('button', { name: '保存', exact: true }).click();
await page.waitForSelector('.sg-arts__row:has-text("C组临时名称")', { timeout: 10000 });
step(true, 'artifact renamed');
const row2 = page.locator('.sg-arts__row', { hasText: 'C组临时名称' }).first();
await row2.hover();
await row2.getByLabel(/的更多操作/).click();
await page.getByRole('menuitem', { name: '重命名' }).click();
await page.getByLabel('成果名称').fill('竞品月销量估算.xlsx');
await page.getByRole('button', { name: '保存', exact: true }).click();
await page.waitForSelector('.sg-arts__row:has-text("竞品月销量估算")', { timeout: 10000 });
step(true, 'artifact renamed back');
const row3 = page.locator('.sg-arts__row', { hasText: '竞品月销量估算' }).first();
await row3.hover();
await row3.getByLabel(/的更多操作/).click();
await page.getByRole('menuitem', { name: /移到回收站/ }).click();
await page.waitForSelector('.sg-toast:has-text("30 天内可恢复")', { timeout: 10000 });
step(true, 'artifact trashed, undo toast shown');
await page.getByRole('button', { name: '撤销' }).click();
await page.waitForSelector('.sg-arts__row:has-text("竞品月销量估算")', { timeout: 10000 });
step(true, 'trash undone via toast');

console.log(JSON.stringify({ errors }));
await browser.close();
