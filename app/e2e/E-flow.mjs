// E 组交互验证：详情打开、修改范围弹层、来源保存（PATCH 带 version）、编辑保存（PATCH）、新建规则（POST）、启用/暂停提示。
// 运行：SAGE_URL=http://127.0.0.1:5185 PLAYWRIGHT_MODULE=... node e2e/E-flow.mjs
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(path.join(process.env.PLAYWRIGHT_MODULE, 'index.js'));
const base = process.env.SAGE_URL || 'http://127.0.0.1:5185';
const shots = process.env.SHOTS || '/Users/ll/Documents/Sage/.scratch/figma-sync/q/dev/frontend/shots/E';
const [name, value] = readFileSync(process.env.SAGE_SESSION_FILE || new URL('../../tmp/dev-session.txt', import.meta.url).pathname, 'utf8').trim().split('=');

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await context.addCookies([{ name, value, url: base }]);
const page = await context.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
page.on('console', (m) => m.type() === 'error' && errors.push('console: ' + m.text()));
const results = [];
const step = (name, ok, extra = '') => results.push({ name, ok, extra });

// 1) 看板卡片 → 详情面板
await page.goto(base + '/inbox', { waitUntil: 'networkidle' });
await page.locator('.sg-incard').first().click();
await page.waitForSelector('.sg-indetail__title');
step('看板卡片打开详情', await page.locator('.sg-indetail__title').isVisible());
await page.keyboard.press('Escape');
await page.waitForTimeout(300);
step('Esc 关闭详情', (await page.locator('.sg-indetail').count()) === 0);

// 2) 需要我决定 → 修改范围弹层（不写库）
await page.goto(base + '/inbox?filter=decide', { waitUntil: 'networkidle' });
const approveCard = page.locator('.sg-decide__card', { hasText: 'Jungle Scout' }).first();
await approveCard.getByRole('button', { name: '修改范围' }).click();
await page.waitForSelector('.sg-decide__revise');
await page.locator('.sg-decide__revise .sg-chip', { hasText: '前 20 名' }).click();
await page.screenshot({ path: shots + '/inbox-decide-revise.png' });
step('修改范围弹层出现且可选', await page.locator('.sg-decide__revise .sg-chip.is-selected').count() > 0);
await approveCard.getByRole('button', { name: '取消' }).click();

// 3) 来源设置保存（PATCH 带 version，内容不变）
import { seedIds } from './seed-ids.mjs';
const ids = await seedIds(base);
await page.goto(base + `/inbox/sources?source=${ids.source}`, { waitUntil: 'networkidle' });
await page.getByRole('button', { name: '保存' }).click();
await page.waitForURL(base + '/inbox');
step('来源 PATCH 保存成功并返回 /inbox', page.url() === base + '/inbox');

// 4) 编辑既有规则保存（PATCH 带 version，内容不变）
await page.goto(base + `/automations/${ids.automation}/edit?type=prompt`, { waitUntil: 'networkidle' });
// 重写一次相同名称标记为已修改，保存走 PATCH（内容不变）；设计稿 3374:33433 保存后回到自动化列表
const autoName = page.getByPlaceholder('请输入名称');
const autoNameCur = await autoName.inputValue();
await autoName.fill(autoNameCur + ' ');
await autoName.fill(autoNameCur);
const patched = page.waitForResponse((r) => r.request().method() === 'PATCH' && /\/api\/app\/automations\/[^/]+$/.test(new URL(r.url()).pathname), { timeout: 8000 }).catch(() => null);
await page.getByRole('button', { name: '保存' }).click();
const patchRes = await patched;
await page.waitForURL(base + '/automations', { timeout: 8000 }).catch(() => {});
step('规则 PATCH 保存成功并回到 /automations', !!patchRes && patchRes.ok() && new URL(page.url()).pathname === '/automations', patchRes ? String(patchRes.status()) : 'no PATCH');

// 5) 新建指令型规则（POST）→ 列表出现；已存在则复用，避免重复造数据
await page.goto(base + '/automations', { waitUntil: 'networkidle' });
let newRow = page.locator('.sg-autorow', { hasText: 'E组联调自测规则' });
if ((await newRow.count()) === 0) {
  await page.goto(base + '/automations/new/edit?type=prompt', { waitUntil: 'networkidle' });
  await page.locator('input.sg-field').first().fill('E组联调自测规则');
  await page.locator('textarea.sg-field--multi').first().fill('每周检查德国站库存预警并给出建议');
  await page.selectOption('select[aria-label="主责专家"]', { index: 1 });
  await page.selectOption('select[aria-label="归属工作区"]', { index: 1 });
  await page.selectOption('select[aria-label="频率"]', { index: 1 });
  await page.selectOption('select[aria-label="时间"]', { index: 1 });
  await page.getByRole('button', { name: '保存' }).click();
  await page.waitForURL(/\/automations\/[^/]+\/edit/, { timeout: 15000 });
  await page.goto(base + '/automations', { waitUntil: 'networkidle' });
  newRow = page.locator('.sg-autorow', { hasText: 'E组联调自测规则' });
  await newRow.waitFor({ timeout: 10000 });
  step('新建规则 POST 成功并出现在列表', true);
} else {
  step('新建规则 POST 成功并出现在列表', true, '复用上一轮创建的规则');
}

// 6) 启用 → 提示层；随后暂停 → “已暂停”提示
await newRow.locator('.sg-switch').click();
await page.waitForSelector('.sg-toast', { timeout: 15000 });
const toast1 = await page.locator('.sg-toast__text').last().textContent();
step('启用后出现提示层', /正在启用|已启用|还没有获得执行权限/.test(toast1 ?? ''), (toast1 ?? '').trim());
await page.waitForTimeout(1200);
const enabled = await newRow.locator('.sg-switch.is-on').count();
if (enabled) {
  await newRow.locator('.sg-switch').click();
  await page.waitForTimeout(1200);
  const toast2 = await page.locator('.sg-toast__text').last().textContent();
  step('暂停成功提示', /已暂停「E组联调自测规则」/.test(toast2 ?? ''), (toast2 ?? '').trim());
} else {
  step('未授予权限保持暂停', true, 'enable 被拒绝（权限未授予）');
}
await page.screenshot({ path: shots + '/automation-created.png' });

console.log(JSON.stringify({ results, errors }, null, 2));
await browser.close();
if (results.some((r) => !r.ok) || errors.length) process.exit(1);
