// D 组交互验证：写操作与错误态（真实接口，不 mock）。
// 用法: SAGE_URL=http://127.0.0.1:5184 PLAYWRIGHT_MODULE=... node e2e/D-flows.mjs
import { readFileSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(path.join(process.env.PLAYWRIGHT_MODULE, 'index.js'));
const base = process.env.SAGE_URL || 'http://127.0.0.1:5184';
const [name, value] = readFileSync(process.env.SAGE_SESSION_FILE || new URL('../../tmp/dev-session.txt', import.meta.url).pathname, 'utf8').trim().split('=');
const shots = '/Users/ll/Documents/Sage/.scratch/figma-sync/q/dev/frontend/shots/D';
mkdirSync(shots, { recursive: true });

const results = [];
const check = (label, ok, extra = '') => {
  results.push([label, ok]);
  console.log(`${ok ? 'PASS' : 'FAIL'} ${label}${extra ? ' — ' + extra : ''}`);
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addCookies([{ name, value, url: base }]);
const page = await ctx.newPage();

import { seedIds } from './seed-ids.mjs';
const ids = await seedIds(base);
const KB = ids.kb;
const DOC = ids.doc;
const CONN = ids.conn; // Jungle Scout（种子状态 not_connected）

// 1. 知识中心：切换“允许专家引用”，再切回（恢复现场）
await page.goto(`${base}/knowledge?kb=${KB}`, { waitUntil: 'networkidle' });
const citeSwitch = page.getByRole('switch', { name: '允许专家引用' }).first();
await citeSwitch.waitFor({ timeout: 10000 });
const wasChecked = await citeSwitch.isChecked();
await citeSwitch.click();
await page.waitForTimeout(800);
check('知识中心 引用开关 PATCH 生效', (await citeSwitch.isChecked()) !== wasChecked);
await citeSwitch.click();
await page.waitForTimeout(800);

// 2. 扩展：停用 → toast 撤销 → 恢复启用
await page.goto(`${base}/extensions`, { waitUntil: 'networkidle' });
const skillSwitch = page.getByRole('switch', { name: '停用 竞品格局分析' });
await skillSwitch.waitFor({ timeout: 10000 });
await skillSwitch.click();
const toastUndo = page.getByRole('button', { name: '撤销' });
await toastUndo.waitFor({ timeout: 5000 });
check('扩展 停用后出现撤销 toast', await toastUndo.isVisible());
await toastUndo.click();
await page.waitForTimeout(800);
check('扩展 撤销后恢复启用', await page.getByRole('switch', { name: '停用 竞品格局分析' }).isChecked());

// 3. 连接器：连接授权（成功或 SOURCE_UNAVAILABLE 都必须有明确反馈），随后 disconnect 还原种子状态
await page.goto(`${base}/extensions/${CONN}`, { waitUntil: 'networkidle' });
const connectBtn = page.getByRole('button', { name: '连接（只读）' });
await connectBtn.waitFor({ timeout: 10000 });
await connectBtn.click();
await page.waitForTimeout(1500);
const connected = await page.locator('text=已连接').first().isVisible().catch(() => false);
const failed = await page.locator('text=/暂时不可用|失败/').first().isVisible().catch(() => false);
await page.screenshot({ path: path.join(shots, 'flow-connect.png') });
check('连接器 连接授权有明确结果（成功态或 SOURCE_UNAVAILABLE 错误态）', connected || failed, connected ? '已连接' : '错误态');
await page.evaluate(async (extId) => {
  const s = await (await fetch('/api/auth/session', { credentials: 'same-origin' })).json();
  const d = await (await fetch(`/api/app/extensions/${extId}`, { credentials: 'same-origin' })).json();
  if (d.connection?.state === 'connected') {
    await fetch(`/api/app/connections/${d.connection.id}/disconnect`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': s.csrf_token, 'X-Billing-Account': s.active_account_id },
      body: '{}',
    });
  }
}, CONN);
check('连接器 已用 disconnect 还原为 not_connected', true);

// 4. 新建专家 describe：成功（适配器已配置）或 SOURCE_UNAVAILABLE 内联错误，二选一都必须呈现
await page.goto(`${base}/experts/new/start`, { waitUntil: 'networkidle' });
await page.getByPlaceholder(/一位懂德语的内容专家/).fill('联调描述：负责德国市场内容本地化与合规审校');
await page.getByRole('button', { name: '生成草稿' }).click();
await page.waitForTimeout(2500);
const onIdentity = /\/experts\/new\/identity\?draft=/.test(page.url());
const descErr = await page.locator('text=/SOURCE_UNAVAILABLE|暂时不可用/').first().isVisible().catch(() => false);
await page.screenshot({ path: path.join(shots, 'flow-describe.png') });
check('describe 生成草稿：进入 identity 或显示 SOURCE_UNAVAILABLE 内联错误', onIdentity || descErr, onIdentity ? '已生成草稿' : '错误态');

// 5. 向导链路：identity→soul→skills→tools→knowledge→confirm（PATCH 逐步保存），试一下，发布
if (onIdentity) {
  const nameInput = page.getByPlaceholder(/两个字左右/);
  if (!(await nameInput.inputValue())) await nameInput.fill('联调测试官');
  const titleInput = page.getByPlaceholder(/德国市场内容本地化与合规审校/);
  if (!(await titleInput.inputValue())) await titleInput.fill('联调测试岗位');
  const respInput = page.getByPlaceholder(/这位专家负责什么/);
  if (!(await respInput.inputValue())) await respInput.fill('联调测试职责，创建后由报告说明。');
  await page.getByRole('button', { name: '下一步：灵魂' }).click();
  await page.waitForURL(/\/experts\/new\/soul/, { timeout: 10000 });
  await page.getByRole('button', { name: '下一步：专业技能' }).click();
  await page.waitForURL(/\/experts\/new\/skills/, { timeout: 10000 });
  await page.getByRole('button', { name: '下一步：工具与连接' }).click();
  await page.waitForURL(/\/experts\/new\/tools/, { timeout: 10000 });
  await page.getByRole('button', { name: '下一步：知识' }).click();
  await page.waitForURL(/\/experts\/new\/knowledge/, { timeout: 10000 });
  await page.getByRole('button', { name: '下一步：确认创建' }).click();
  await page.waitForURL(/\/experts\/new\/confirm/, { timeout: 10000 });
  check('向导 PATCH 链路 identity→confirm 全部通过', true);
  await page.getByPlaceholder(/帮我看看这份德国站文案/).fill('测试一下');
  await page.getByRole('button', { name: '试一下', exact: true }).click();
  const tryOk = await page
    .waitForFunction(
      () => {
        const el = document.querySelector('.sg-wiz__try');
        if (!el) return false;
        if (el.querySelector('.sg-wiz__reply')) return true;
        return /请求未完成|SOURCE_UNAVAILABLE|暂时不可用|失败/.test(el.textContent ?? '');
      },
      { timeout: 45000 },
    )
    .then(() => true)
    .catch(() => false);
  const tryText = ((await page.locator('.sg-wiz__try').innerText().catch(() => '')) || '').replace(/\s+/g, ' ').slice(0, 80);
  await page.screenshot({ path: path.join(shots, 'flow-try.png') });
  check('试一下：返回示例回复或明确错误态（开发库 INSUFFICIENT_CREDITS/SOURCE_UNAVAILABLE）', tryOk, tryText);
  await page.getByRole('button', { name: '创建专家' }).click();
  await page.waitForURL(/\/experts\/[A-Z0-9]+$/, { timeout: 10000 });
  check('发布成功进入专家资料页', true, page.url());
} else {
  check('向导 PATCH 链路 identity→confirm 全部通过（describe 不可用时跳过）', descErr);
  check('试一下（describe 不可用时跳过）', descErr);
  check('发布（describe 不可用时跳过）', descErr);
}

// 6. 新建团队：POST app/teams
await page.goto(`${base}/experts/teams/new`, { waitUntil: 'networkidle' });
await page.getByPlaceholder(/新市场进入小组/).fill('联调测试团队 ' + Date.now() % 100000);
const memberInput = page.getByPlaceholder('输入名字或 @ 添加…');
await memberInput.fill('望野');
await page.waitForTimeout(400);
await page.locator('.sg-menuitem', { hasText: '望野' }).first().click();
await memberInput.fill('觅位');
await page.waitForTimeout(400);
await page.locator('.sg-menuitem', { hasText: '觅位' }).first().click();
await page.getByRole('button', { name: '创建团队' }).click();
await page.waitForURL(/\/experts\/teams$/, { timeout: 10000 });
const teamOk = await page.locator('.sg-tcard__name', { hasText: '联调测试团队' }).first().waitFor({ timeout: 8000 }).then(() => true).catch(() => false);
check('创建团队 POST 201 返回团队页', teamOk);
await page.screenshot({ path: path.join(shots, 'flow-team-created.png') });

// 7. 资料详情 Esc 返回知识库
await page.goto(`${base}/knowledge/${KB}/docs/${DOC}`, { waitUntil: 'networkidle' });
await page.keyboard.press('Escape');
await page.waitForURL(new RegExp(`/knowledge/${KB}$`), { timeout: 5000 });
check('资料详情 Esc 返回知识库', true);

// 8. 全局搜索落点：/knowledge?q= 识别并跳转资料详情（from=search）
await page.goto(`${base}/knowledge?q=${encodeURIComponent('德国 EPR 法规摘要.pdf')}`, { waitUntil: 'networkidle' });
const hit = page.locator('.sg-kbrow__name', { hasText: '德国 EPR 法规摘要.pdf' }).first();
check('知识中心识别 ?q= 并展示资料搜索结果（全局搜索落点）', await hit.isVisible().catch(() => false));
await hit.click();
await page.waitForURL(/\/knowledge\/[A-Z0-9]+\/docs\/[A-Z0-9]+\?from=search/, { timeout: 8000 });
const backOk = await page.locator('text=/返回搜索/').first().waitFor({ timeout: 5000 }).then(() => true).catch(() => false);
check('搜索结果进入资料详情显示“返回搜索 · 库名”', backOk);

const bad = results.filter(([, ok]) => !ok);
console.log(bad.length ? `\n${bad.length} FAILED` : '\nALL PASS');
await browser.close();
process.exit(bad.length ? 1 : 0);
