// Regression checks for the second independent review (q/review/review2.md).
// Usage: PLAYWRIGHT_MODULE=... SAGE_URL=http://127.0.0.1:5189 SAGE_SESSION_FILE=... node e2e/review2-fix.mjs [--only wsdialog,sources,toastcard,leave,rewrite,themes,inbox] [--shots DIR]
// Only GET requests reach the backend unless a check explicitly allows or fakes a write, so the seed stays intact.
import { mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { apiGet, cookie } from './seed-ids.mjs';

const require = createRequire(import.meta.url);
const { chromium } = require(path.join(process.env.PLAYWRIGHT_MODULE, 'index.js'));
const BASE = process.env.SAGE_URL || 'http://127.0.0.1:4173';
const arg = (n) => (process.argv.includes(n) ? process.argv[process.argv.indexOf(n) + 1] : '');
const ONLY = arg('--only') ? arg('--only').split(',') : null;
const SHOTS = arg('--shots');
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

const results = [];
const check = (group, name, ok, detail = '') => results.push({ group, name, ok: !!ok, detail });
const want = (g) => !ONLY || ONLY.includes(g);

const [cName, cValue] = cookie().split('=');
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, locale: 'zh-CN' });
await ctx.addCookies([{ name: cName, value: cValue, url: BASE }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
// Handlers registered later take precedence; this default blocks every write.
await page.route('**/api/**', (r) => (['GET', 'HEAD'].includes(r.request().method()) ? r.continue() : r.fulfill({ status: 200, contentType: 'application/json', body: '{}' })));

async function open(route, wait = 600) {
  await page.goto(BASE + route, { waitUntil: 'networkidle' });
  await page.waitForTimeout(wait);
}
async function shot(name) {
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, name + '.png') });
}
const has = async (t) => (await page.getByText(t, { exact: true }).count()) > 0;
const btn = (l) => page.getByRole('button', { name: l, exact: true });
const css = (sel, props) => page.locator(sel).first().evaluate((e, ps) => Object.fromEntries(ps.map((p) => [p, getComputedStyle(e)[p]])), props);

const tasks = await apiGet(BASE, 'tasks');
const taskId = (t) => tasks.items.find((x) => x.title.includes(t))?.id;

// 727:2: 编辑工作区 opened from a non-workspace page keeps the dialog styles.
if (want('wsdialog')) {
  await open('/inbox');
  await page.locator('.sg-sidebar').getByText('德国市场进入', { exact: true }).first().click({ button: 'right' });
  await page.waitForTimeout(300);
  await page.getByRole('menuitem', { name: '编辑工作区' }).click();
  await page.waitForSelector('.sg-dsk-edit', { timeout: 5000 });
  await page.waitForTimeout(300);
  const field = await css('.sg-dsk-edit .sg-wsdialog__field', ['display', 'flexDirection', 'rowGap']);
  const label = await css('.sg-dsk-edit .sg-wsdialog__label', ['fontSize', 'color']);
  check('wsdialog', '/inbox 编辑工作区 字段为纵向 flex、间距 8px', field.display === 'flex' && field.flexDirection === 'column' && field.rowGap === '8px', JSON.stringify(field));
  check('wsdialog', '/inbox 编辑工作区 标签 12px', label.fontSize === '12px', JSON.stringify(label));
  await shot('727-edit-inbox');
  await page.keyboard.press('Escape');
}

// 727:2 (D106): 资料来源 lists only the referenced base (德国市场法规库); its × removes the reference via
// DELETE app/workspaces/{id}/knowledge-bases/{kb_id} and the list refreshes. The write is faked, so the seed stays intact.
if (want('sources')) {
  const wsList = await apiGet(BASE, 'workspaces');
  const wsId = wsList.items.find((w) => w.name === '德国市场进入')?.id;
  const listed = await apiGet(BASE, `workspaces/${wsId}/knowledge-bases`);
  check('sources', 'GET 资料来源只返回德国市场法规库', JSON.stringify(listed.items.map((k) => k.name)) === '["德国市场法规库"]' && listed.can_manage === true, JSON.stringify(listed.items.map((k) => k.name)));
  const kbId = listed.items[0]?.id;
  await open('/tasks/active');
  await page.locator('.sg-sidebar').getByText('德国市场进入', { exact: true }).first().hover();
  await page.waitForSelector('.sg-dsk-card', { timeout: 5000 });
  await page.waitForTimeout(400);
  const cardKbs = await page.locator('.sg-dsk-card .sg-dsk-card__row', { hasText: '知识库：' }).allInnerTexts();
  check('sources', '悬停卡只有“知识库：德国市场法规库”', JSON.stringify(cardKbs.map((t) => t.trim())) === '["知识库：德国市场法规库"]', JSON.stringify(cardKbs));
  await shot('727-card-sources');
  await page.locator('.sg-dsk-card').getByRole('button', { name: '编辑工作区' }).click();
  await page.waitForSelector('.sg-dsk-edit', { timeout: 5000 });
  await page.waitForTimeout(400);
  const kbRows = () => page.locator('.sg-dsk-edit .sg-dsk-sources__row', { hasText: '知识库：' });
  const rowTexts = (await kbRows().allInnerTexts()).map((t) => t.trim());
  check('sources', '弹窗资料来源只有“知识库：德国市场法规库”', JSON.stringify(rowTexts) === '["知识库：德国市场法规库"]', JSON.stringify(rowTexts));
  const x = page.locator('.sg-dsk-edit').getByRole('button', { name: '移除 知识库：德国市场法规库', exact: true });
  check('sources', '知识库行有 ×（工作区所有者）', (await x.count()) === 1);
  await shot('727-edit-sources');
  let deleted = '';
  await page.route('**/api/app/workspaces/*/knowledge-bases**', async (r) => {
    const req = r.request();
    if (req.method() === 'DELETE') {
      deleted = new URL(req.url()).pathname;
      return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ knowledge_base: { id: kbId, name: '德国市场法规库' } }) });
    }
    if (!deleted) return r.continue();
    const res = await r.fetch();
    const body = await res.json();
    body.items = body.items.filter((k) => k.id !== kbId);
    body.total = body.items.length;
    return r.fulfill({ response: res, json: body });
  });
  await x.click();
  await page.waitForTimeout(800);
  check('sources', '× 调用 DELETE app/workspaces/{id}/knowledge-bases/{kb_id}', deleted === `/api/app/workspaces/${wsId}/knowledge-bases/${kbId}`, deleted);
  check('sources', '移除后弹窗刷新、知识库行消失', (await kbRows().count()) === 0 && (await page.locator('.sg-dsk-edit').count()) === 1);
  await page.keyboard.press('Escape');
  await page.unroute('**/api/app/workspaces/*/knowledge-bases**');
}

// 3235:94: failed enable shows the white alert card centred over the main column.
if (want('toastcard')) {
  const autos = await apiGet(BASE, 'automations');
  const paused = autos.items.find((a) => a.state !== 'enabled');
  await page.route('**/api/app/automations/*/enable', (r) => r.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ error: { code: 'FORBIDDEN', message: 'no' } }) }));
  await open('/automations');
  const row = page.locator('.sg-autorow', { hasText: paused.name }).first();
  await row.getByRole('switch').click();
  await page.waitForSelector('.sg-toastcard', { timeout: 5000 });
  const card = await page.locator('.sg-toastcard').boundingBox();
  const st = await css('.sg-toastcard', ['backgroundColor', 'borderTopColor', 'borderTopLeftRadius']);
  const title = await css('.sg-toastcard__title', ['color', 'fontSize']);
  const action = await css('.sg-toastcard__action', ['borderTopColor', 'color']);
  check('toastcard', '文案', (await has(`未能启用「${paused.name}」`)) && (await has('这条规则还没有获得执行权限，已恢复为暂停，没有运行。')) && (await btn('知道了').count()) === 1);
  check('toastcard', '白底卡片 #F1C4BE 描边 圆角 10', st.backgroundColor === 'rgb(255, 255, 255)' && st.borderTopColor === 'rgb(241, 196, 190)' && st.borderTopLeftRadius === '10px', JSON.stringify(st));
  check('toastcard', '红色标题 #B42318 15px', title.color === 'rgb(180, 35, 24)' && title.fontSize === '15px', JSON.stringify(title));
  check('toastcard', '描边按钮“知道了”', action.borderTopColor === 'rgb(208, 213, 221)' && action.color === 'rgb(52, 64, 84)', JSON.stringify(action));
  check('toastcard', '位置：宽 420，水平居中于主栏，距底 48', card && Math.abs(card.width - 420) < 1 && Math.abs(card.x + card.width / 2 - 840) < 2 && Math.abs(900 - (card.y + card.height) - 48) < 2, JSON.stringify(card));
  check('toastcard', '无关闭 ×', (await page.locator('.sg-toastcard [aria-label="关闭"]').count()) === 0);
  await shot('3235-94');
  await btn('知道了').click();
  check('toastcard', '“知道了”关闭', (await page.locator('.sg-toastcard').count()) === 0);
  await page.unroute('**/api/app/automations/*/enable');
}

// 3380:52295: leaving the editor with unsaved changes.
if (want('leave')) {
  const autos = await apiGet(BASE, 'automations');
  const a = autos.items.find((x) => x.content_kind === 'prompt') ?? autos.items[0];
  await open(`/automations/${a.id}/edit?type=${a.content_kind}`);
  const name = page.locator('input').first();
  await name.fill((await name.inputValue()) + ' 改');
  await btn('取消').first().click();
  await page.waitForTimeout(300);
  const dlg = page.getByRole('alertdialog');
  check('leave', '标题与正文', (await dlg.getByText('放弃未保存的更改？', { exact: true }).count()) === 1 && (await dlg.getByText('退出后，本次未保存的更改将丢失。', { exact: true }).count()) === 1);
  const buttons = await dlg.locator('button').evaluateAll((els) => els.map((e) => [e.textContent.trim(), e.className]));
  check('leave', '主按钮“继续编辑”在前、次按钮“放弃更改”', buttons[0]?.[0] === '继续编辑' && /primary/.test(buttons[0]?.[1]) && buttons[1]?.[0] === '放弃更改', JSON.stringify(buttons));
  check('leave', '无警示图标（设计稿中隐藏）', (await dlg.locator('.sg-dialog__icon').count()) === 0);
  await shot('3380-52295');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(200);
  check('leave', 'Esc 留在编辑页', (await page.getByRole('alertdialog').count()) === 0 && page.url().includes('/edit'));
  await btn('取消').first().click();
  await btn('继续编辑').click();
  check('leave', '继续编辑 留在编辑页', page.url().includes('/edit'));
  await btn('取消').first().click();
  await btn('放弃更改').click();
  await page.waitForTimeout(400);
  check('leave', '放弃更改 → /automations', new URL(page.url()).pathname === '/automations', page.url());
}

// 2294:1361 (D55): “正在应用全部改写” is visible while POST apply-all is in flight.
if (want('rewrite')) {
  const id = taskId('X1 德语 Listing') ?? taskId('德语 Listing');
  let release;
  const gate = new Promise((r) => (release = r));
  let seen = 0;
  await page.route('**/api/app/reviews/*/apply-all', async (r) => {
    seen += 1;
    await gate;
    await r.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });
  await open(`/tasks/${id}#review-card`, 1200);
  const applyBtn = btn('应用全部改写');
  await applyBtn.first().scrollIntoViewIfNeeded();
  await applyBtn.first().click();
  await page.waitForTimeout(1500);
  check('rewrite', '请求已发出且未返回', seen === 1, `seen=${seen}`);
  check('rewrite', '提示“正在应用全部改写，完成后将生成新版本。”', await has('正在应用全部改写，完成后将生成新版本。'));
  check('rewrite', '按钮“正在应用…”且禁用', (await btn('正在应用…').count()) === 1 && (await btn('正在应用…').isDisabled()));
  await shot('2294-1361');
  release();
  await page.waitForTimeout(1500);
  check('rewrite', '返回后离开进行中', !(await has('正在应用全部改写，完成后将生成新版本。')));
  await page.unroute('**/api/app/reviews/*/apply-all');
}

// Theme boards 692/695/698/701: sidebar count badge and task progress tag colours (light).
if (want('themes')) {
  const WANT = {
    neo_electric: { badge: ['rgb(17, 17, 17)', 'rgb(198, 244, 50)'], tag: ['rgb(91, 61, 245)', 'rgb(239, 235, 255)'] },
    fashion_editorial: { badge: ['rgb(255, 255, 255)', 'rgb(200, 16, 46)'], tag: ['rgb(17, 17, 17)', 'rgb(236, 232, 224)'] },
    comfort_readable: { badge: ['rgb(255, 255, 255)', 'rgb(15, 94, 107)'], tag: ['rgb(15, 94, 107)', 'rgb(230, 242, 243)'] },
    minimal_focus: { badge: ['rgb(17, 17, 17)', 'rgb(255, 91, 31)'], tag: ['rgb(17, 17, 17)', 'rgb(242, 242, 242)'] },
    business_steady: { badge: ['rgb(57, 99, 151)', 'rgb(235, 241, 248)'], tag: ['rgb(57, 99, 151)', 'rgb(235, 241, 248)'] },
  };
  for (const [style, exp] of Object.entries(WANT)) {
    const tctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' });
    await tctx.addCookies([{ name: cName, value: cValue, url: BASE }]);
    const p = await tctx.newPage();
    const prefs = { theme_style: style, appearance_mode: 'light', density: 'theme' };
    await p.addInitScript((v) => localStorage.setItem('sage.theme-prefs', JSON.stringify(v)), prefs);
    await p.route('**/api/**', async (r) => {
      if (!['GET', 'HEAD'].includes(r.request().method())) return r.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      if (!r.request().url().includes('/api/account/profile')) return r.continue();
      const res = await r.fetch();
      const body = await res.json();
      Object.assign(body.profile ?? body, prefs);
      return r.fulfill({ response: res, json: body });
    });
    await p.goto(BASE + '/tasks/new', { waitUntil: 'networkidle' });
    await p.waitForTimeout(600);
    const m = await p.evaluate(() => {
      const pick = (sel) => { const e = document.querySelector(sel); if (!e) return null; const s = getComputedStyle(e); return [s.color, s.backgroundColor]; };
      return { theme: document.documentElement.dataset.theme, badge: pick('.sg-count--brand'), tag: pick('.sg-tasklist__state.is-info') };
    });
    check('themes', `${style} 侧栏计数徽标`, m.theme === style && JSON.stringify(m.badge) === JSON.stringify(exp.badge), JSON.stringify(m));
    check('themes', `${style} 任务进度标签`, m.theme === style && JSON.stringify(m.tag) === JSON.stringify(exp.tag), JSON.stringify(m));
    if (SHOTS) await p.screenshot({ path: path.join(SHOTS, `theme-${style}.png`) });
    await tctx.close();
  }
}

// 638:2 / 2732:2305 / 3128:43: 收件 · 需要我决定 uses the title + tabs header and a list / detail split; a
// rejected request stays in the detail pane. Runs on the seed clock so list times match the design.
if (want('inbox')) {
  const ictx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, locale: 'zh-CN' });
  await ictx.addCookies([{ name: cName, value: cValue, url: BASE }]);
  const p = await ictx.newPage();
  p.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  await p.clock.install({ time: new Date('2026-11-19T10:53:00+08:00') });
  let rejected = false;
  await p.route('**/api/**', async (r) => {
    const req = r.request();
    if (!['GET', 'HEAD'].includes(req.method())) {
      if (/\/authorizations\/[^/]+\/reject$/.test(new URL(req.url()).pathname)) rejected = true;
      return r.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    }
    if (!rejected || !new URL(req.url()).pathname.endsWith('/app/inbox/decisions')) return r.continue();
    const res = await r.fetch();
    const body = await res.json();
    body.items = body.items.filter((d) => d.type !== 'approve');
    return r.fulfill({ response: res, json: body });
  });
  const pHas = async (t) => (await p.getByText(t, { exact: true }).count()) > 0;
  await p.goto(BASE + '/inbox?filter=decide', { waitUntil: 'networkidle' });
  await p.waitForTimeout(800);
  check('inbox', '标题、说明与横向标签', (await p.locator('h1.sg-inq__h1').innerText()) === '收件' && (await pHas('所有在等你处理的事，都在这里')) && (await p.locator('.sg-inq__tabs [role=tab]').allInnerTexts()).map((t) => t.split('\n')[0]).join('|') === '全部|需要我决定|@ 与指派|外部消息');
  check('inbox', '没有左侧二级导航', (await p.locator('.sg-inbox__rail').count()) === 0);
  const rows = (await p.locator('.sg-inq__row').allInnerTexts()).map((t) => t.replace(/\n/g, '｜'));
  check('inbox', '左列表 4 条且时间按种子时钟', rows.length === 4 && rows[0].endsWith('10 分钟前') && rows[1].endsWith('1 小时前') && rows[2].endsWith('昨天') && rows[3].endsWith('昨天'), JSON.stringify(rows));
  const side = await p.locator('.sg-inq__side').boundingBox();
  const detail = await p.locator('.sg-inq__detail').boundingBox();
  check('inbox', '两栏：左 420 宽，右侧详情紧邻', side && detail && Math.round(side.width) === 420 && Math.round(detail.x) === Math.round(side.x + side.width), JSON.stringify([side, detail]));
  check('inbox', '详情：面包屑、标题与发起行', (await pHas('德国市场进入')) && (await p.locator('h2.sg-inq__title').innerText()) === '使用 Jungle Scout 读取竞品销量' && (await pHas('望野（主责专家）· 任务负责人 Lillian Li')) && (await pHas('需要你确认')));
  if (SHOTS) await p.screenshot({ path: path.join(SHOTS, '638-2.png') });
  await p.locator('.sg-inq__detail').getByRole('button', { name: '拒绝', exact: true }).click();
  await p.waitForTimeout(1200);
  const rows2 = await p.locator('.sg-inq__row').count();
  check('inbox', '拒绝后条目离开列表、详情保留已拒绝', rows2 === 3 && (await pHas('已拒绝')) && (await pHas('已拒绝，已从待处理队列移除。')) && (await p.locator('h2.sg-inq__title').innerText()) === '使用 Jungle Scout 读取竞品销量', String(rows2));
  check('inbox', '已拒绝卡只剩“在任务中打开”', (await p.locator('.sg-inq__detail').getByRole('button', { name: '批准' }).count()) === 0 && (await p.locator('.sg-inq__detail .sg-approval__open').count()) === 1);
  if (SHOTS) await p.screenshot({ path: path.join(SHOTS, '2732-2305.png') });
  await p.locator('.sg-inq__row').first().click();
  await p.waitForTimeout(500);
  check('inbox', '点选其它条目切换详情', (await p.locator('h2.sg-inq__title').innerText()) === '需要确认目标价格带');
  await p.goto(BASE + '/inbox?filter=decide&demo=recovery', { waitUntil: 'networkidle' });
  await p.waitForTimeout(600);
  check('inbox', '3128:43 演示同样两栏且标签不显示计数', (await p.locator('.sg-inq__side .sg-recover__head').count()) === 1 && (await p.locator('.sg-inq__detail [data-demo-phase=recovery]').count()) === 1 && (await p.locator('.sg-inq__tabs .sg-tab__count').count()) === 0);
  if (SHOTS) await p.screenshot({ path: path.join(SHOTS, '3128-43.png') });
  await p.getByRole('tab', { name: /外部消息/ }).click();
  await p.waitForTimeout(600);
  check('inbox', '外部消息标签回到看板', new URL(p.url()).pathname === '/inbox' && (await p.locator('.sg-inbox__board').count()) === 1);
  await ictx.close();
}
await browser.close();
const failed = results.filter((r) => !r.ok);
for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'} [${r.group}] ${r.name}${r.detail && !r.ok ? ' — ' + r.detail : ''}`);
console.log(`TOTAL ${results.length - failed.length}/${results.length}; pageerrors=${errors.length}`);
for (const e of errors) console.log(e);
process.exit(failed.length || errors.length ? 1 : 0);
