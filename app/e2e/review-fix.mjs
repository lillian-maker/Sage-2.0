// Regression checks for the issues found by the step-7 review (q/review/review.md).
// Usage: PLAYWRIGHT_MODULE=... SAGE_URL=http://127.0.0.1:5187 SAGE_SESSION_FILE=... node e2e/review-fix.mjs [--only bell,avatar,...] [--shots DIR]
// Only GET requests reach the backend unless a check explicitly allows a write, so the seed stays intact.
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

async function ids() {
  const [tasks, ws, arts, autos, exts, kbs] = await Promise.all([
    apiGet(BASE, 'tasks'), apiGet(BASE, 'workspaces'), apiGet(BASE, 'artifacts'), apiGet(BASE, 'automations'), apiGet(BASE, 'extensions'), apiGet(BASE, 'knowledge-bases'),
  ]);
  const task = (t) => tasks.items.find((x) => x.title.includes(t))?.id;
  return {
    richTask: task('德国站竞品格局调研'),
    deliveredTask: task('德语 Listing 本地化'),
    workspace: ws.items.find((w) => w.name.includes('德国市场进入'))?.id ?? ws.items[0].id,
    artifact: arts.items[0]?.id,
    automation: autos.items[0]?.id,
    extension: exts.items.find((x) => x.name === 'Jungle Scout')?.id ?? exts.items[0]?.id,
    kb: kbs.items[0]?.id,
  };
}

const [cName, cValue] = cookie().split('=');
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, locale: 'zh-CN' });
await ctx.addCookies([{ name: cName, value: cValue, url: BASE }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
await page.route('**/api/**', (r) => (['GET', 'HEAD'].includes(r.request().method()) ? r.continue() : r.fulfill({ status: 200, contentType: 'application/json', body: '{}' })));

async function open(route, wait = 500) {
  await page.goto(BASE + route, { waitUntil: 'networkidle' });
  await page.waitForTimeout(wait);
}
async function shot(name) {
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, name + '.png') });
}
const want = (g) => !ONLY || ONLY.includes(g);

const I = await ids();

// 2: one visible bell on every shell page (review issue 2).
if (want('bell')) {
  const routes = [
    '/artifacts', `/artifacts/${I.artifact}/versions`, '/automations', `/automations/${I.automation}/edit?type=prompt`, `/automations/${I.automation}/runs`,
    '/billing', '/billing/invoices', '/experts', '/experts/network', '/extensions', '/extensions/market', `/extensions/${I.extension}`,
    '/inbox', '/inbox/setup', '/inbox/sources', '/knowledge', '/knowledge/graph', `/knowledge/${I.kb}`,
    '/settings/profile', '/settings/appearance', '/settings/extensions', '/settings/org/members', '/settings/notifications',
    '/tasks', '/tasks/active', '/tasks/new', `/tasks/${I.richTask}`, `/workspaces/${I.workspace}`, `/workspaces/${I.workspace}/rules`, `/workspaces/${I.workspace}/members`, '/notifications',
  ];
  for (const r of routes) {
    await open(r);
    const n = await page.locator('[aria-label="通知"]:visible').count();
    check('bell', r, n === 1, `visible bells=${n}`);
    // The shell bell is fixed in the corner; nothing clickable may sit underneath it.
    const hit = await page.evaluate(() => {
      const bell = [...document.querySelectorAll('[aria-label="通知"]')].find((e) => e.getClientRects().length);
      if (!bell) return '';
      const b = bell.getBoundingClientRect();
      return [...document.querySelectorAll('button, a, input, [role="button"], [role="switch"]')]
        .filter((e) => e !== bell && !bell.contains(e) && !e.contains(bell) && e.getClientRects().length)
        .filter((e) => { const r = e.getBoundingClientRect(); return r.left < b.right && r.right > b.left && r.top < b.bottom && r.bottom > b.top; })
        .map((e) => (e.getAttribute('aria-label') || e.textContent || e.tagName).trim().slice(0, 20)).join('|');
    });
    check('bell', r + ' 不遮挡', hit === '', hit);
  }
  await open('/billing');
  await page.locator('[aria-label="通知"]:visible').first().click();
  await page.waitForTimeout(400);
  check('bell', 'click → /notifications', new URL(page.url()).pathname === '/notifications', page.url());
}

// 3: Keepa 确认恢复演示（review issue 1）。演示不得发出任何写请求。
if (want('recovery')) {
  const autos = await apiGet(BASE, 'automations');
  const scan = autos.items.find((a) => a.name === '每月新市场机会扫描')?.id;
  const writes = [];
  const onReq = (r) => { if (!['GET', 'HEAD'].includes(r.method()) && r.url().includes('/api/')) writes.push(r.method() + ' ' + r.url()); };
  page.on('request', onReq);
  const phase = () => page.locator('[data-demo-phase]').getAttribute('data-demo-phase');
  const has = async (t) => (await page.getByText(t, { exact: true }).count()) > 0;
  const btn = (l) => page.getByRole('button', { name: l, exact: true });

  await open('/inbox?filter=decide&demo=recovery');
  check('recovery', '3128:43 三个情景按钮', (await btn('未提交情景').count()) === 1 && (await btn('结果未知情景').count()) === 1 && (await btn('结束演示').count()) === 1);
  check('recovery', '3128:43 文案', (await has('恢复情景 · 虚构测试示例')) && (await has('仅展示未提交及结果未知的恢复路径。')) && (await has('示例发起人')) && (await has('Amazon.fr · 家用小家电 · 虚构商品 A、B、C · 最近 30 天')) && (await has('12 Credits（虚构示例）')) && (await has('仅本次示例运行')));
  await shot('3128-43');

  await btn('结果未知情景').click();
  check('recovery', '结果未知情景 → 3128:1962', (await phase()) === 'unknown' && (await has('结果尚未确认，请先核对，避免重复提交。')));
  await shot('3128-1962');
  await btn('核对状态').click();
  check('recovery', '核对状态 → 3128:2347', (await phase()) === 'checking' && (await has('正在核对结果，请勿重复操作。')) && (await btn('批准').isDisabled()));
  await page.waitForTimeout(1900);
  check('recovery', '核对结束 → 3128:1962', (await phase()) === 'unknown');
  await btn('返回运行详情').click();
  await page.waitForTimeout(800);
  const u1 = new URL(page.url());
  check('recovery', '返回运行详情 → 3128:3472', u1.pathname === `/automations/${scan}/runs` && u1.searchParams.get('demo') === 'unknown', page.url());
  check('recovery', '3128:3472 结果未知标签与步骤说明', (await has('结果未知 · 虚构示例')) && (await has('结果未知 · 虚构示例：使用 Keepa 读取历史价格（只读）')) && (await btn('结束演示').count()) === 1);
  await shot('3128-3472');
  await page.getByText('结果未知 · 虚构示例', { exact: true }).click();
  await page.waitForTimeout(500);
  check('recovery', '3128:3472 标签 → 3128:1962', new URL(page.url()).searchParams.get('demo') === 'unknown' && (await phase()) === 'unknown');
  await open(`/automations/${scan}/runs?demo=unknown`);
  await btn('结束演示').click();
  await page.waitForTimeout(500);
  check('recovery', '3128:3472 结束演示 → 3128:43', new URL(page.url()).pathname === '/inbox' && (await phase()) === 'recovery');

  await btn('未提交情景').click();
  check('recovery', '未提交情景 → 3128:428', (await phase()) === 'not_submitted' && (await has('已确认未提交，决定未生效，可重新选择。')));
  await btn('重新选择').click();
  check('recovery', '重新选择 → 3128:813', (await phase()) === 'decide' && (await has('提交未成功，决定未生效；可重新选择。')));
  await btn('批准').click();
  check('recovery', '批准 → 3128:1196', (await phase()) === 'approving' && (await has('批准处理中 · 虚构测试示例')));
  await page.waitForTimeout(1900);
  check('recovery', '批准超时 → 3128:428', (await phase()) === 'not_submitted');
  await btn('重新选择').click();
  await btn('拒绝').click();
  check('recovery', '拒绝 → 3128:1579', (await phase()) === 'rejecting' && (await has('拒绝处理中 · 虚构测试示例')));
  await page.waitForTimeout(1900);
  await btn('重新选择').click();
  await btn('返回未提交').click();
  check('recovery', '返回未提交 → 3128:428', (await phase()) === 'not_submitted');
  await btn('返回运行详情').click();
  await page.waitForTimeout(800);
  check('recovery', '返回运行详情 → 3128:3115', new URL(page.url()).searchParams.get('demo') === 'not_submitted' && (await has('未提交 · 虚构示例')));

  await open('/inbox?filter=decide&demo=recovery');
  await btn('结束演示').click();
  check('recovery', '3128:43 结束演示 → 3128:813', (await phase()) === 'decide');

  await open(`/automations/${scan}/runs?demo=rejected`);
  check('recovery', '3311:3761 已拒绝', (await has('已拒绝 · 后续处理待确认：使用 Keepa 读取历史价格（只读）')));
  await page.getByText('已拒绝', { exact: true }).click();
  await page.waitForTimeout(500);
  check('recovery', '已拒绝 → 3311:681', (await phase()) === 'rejected_processing' && (await has('正在处理，请勿重复操作。')) && (await page.getByText('待确认', { exact: true }).count()) === 4 && (await btn('拒绝').isDisabled()));
  await shot('3311-681');
  page.off('request', onReq);
  check('recovery', '演示期间没有写请求', writes.length === 0, writes.join(' | '));
}

// 3235:92: 暂停自动化后的提示层（写请求被拦截返回 200）。
if (want('toast')) {
  await open('/automations');
  const sw = page.getByRole('switch', { name: '暂停 每周德国站竞品价格监控' });
  const n = await sw.count();
  if (n) await sw.click();
  await page.waitForTimeout(600);
  const toast = page.locator('.sg-shell__toast');
  const t = n ? await toast.innerText().catch(() => '') : '';
  check('toast', '3235:92 暂停提示', t.includes('已暂停「每周德国站竞品价格监控」，不会按计划运行'), `switch=${n} text=${t.slice(0, 60)}`);
  await shot('3235-92');
  const close = toast.getByRole('button', { name: '关闭' });
  if (await close.count()) await close.click();
  await page.waitForTimeout(400);
  check('toast', '3235:92 关闭', (await toast.count()) === 0);
}
// D69/D75: after 修改范围 the request waits for 重新估算 (estimate_state=checking, credits cleared). The authorization
// GETs are rewritten here so the seed keeps its estimated request.
if (want('revised')) {
  let estimatedNow = false;
  const revised = (v) => {
    if (Array.isArray(v)) return v.forEach(revised);
    if (!v || typeof v !== 'object') return;
    if (v.decision === 'pending' && typeof v.estimate_state === 'string' && v.scope_params && [20, 50].includes(v.scope_params.top_n)) {
      v.estimate_state = estimatedNow ? 'estimated' : 'checking';
      v.estimated_credits = estimatedNow ? 20 : null;
      v.scope_params = { ...v.scope_params, top_n: 20 };
      if (typeof v.scope_text === 'string') v.scope_text = v.scope_text.replace(/前\s*50\s*名/, '前 20 名');
    }
    Object.values(v).forEach(revised);
  };
  const rewrite = async (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    const resp = await route.fetch();
    const json = await resp.json().catch(() => null);
    if (json == null) return route.fulfill({ response: resp });
    revised(json);
    return route.fulfill({ response: resp, json });
  };
  const pattern = /\/api\/app\/(authorizations|inbox)/;
  await page.route(pattern, rewrite);
  // D75: 重新估算 answers after a delay (正在核对 meanwhile); later reads carry the dev fixed estimate (前 20 名约 20 Credits).
  const estimateUrl = /\/api\/app\/authorizations\/[^/]+\/estimate$/;
  const estimateRoute = async (route) => {
    await new Promise((r) => setTimeout(r, 1500));
    estimatedNow = true;
    return route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ authorization: { id: 'x', estimate_state: 'estimated', version: 3 } }) });
  };
  await page.route(estimateUrl, estimateRoute);
  const cardState = async (card) => ({
    text: (await card.innerText().catch(() => '')).replace(/\s+/g, ' '),
    approve: await card.getByRole('button', { name: '批准', exact: true }).first().isDisabled().catch(() => null),
    revise: await card.getByRole('button', { name: '修改范围', exact: true }).count(),
    retry: await card.getByRole('button', { name: '重试估算', exact: true }).count(),
    again: await card.getByRole('button', { name: '重新估算', exact: true }).count(),
  });
  const flow = async (where, card, sid) => {
    estimatedNow = false;
    await open(where, 900);
    const before = await cardState(page.locator(card).first());
    check('revised', `${sid} 说明句随范围变为前 20 名`, before.text.includes('前 20 名竞品的销量估算') && !before.text.includes('前 50 名竞品'), before.text.slice(0, 120));
    check('revised', `${sid} 预计消耗“待重新估算”`, before.text.includes('待重新估算'), before.text.slice(0, 200));
    check('revised', `${sid} 状态“需要你确认”`, before.text.startsWith('需要你确认'), before.text.slice(0, 40));
    check('revised', `${sid} 批准禁用、修改范围保留、有“重新估算”、无“重试估算”`, before.approve === true && before.revise === 1 && before.again === 1 && before.retry === 0,
      JSON.stringify({ approve: before.approve, revise: before.revise, again: before.again, retry: before.retry }));
    await shot(`d75-${sid.replace(':', '-')}-before`);
    await page.locator(card).first().getByRole('button', { name: '重新估算', exact: true }).click();
    await page.waitForTimeout(400);
    const during = await cardState(page.locator(card).first());
    check('revised', `${sid} 重新估算进行中显示“正在核对”、批准禁用`, during.text.includes('正在核对') && during.approve === true && during.again === 0, during.text.slice(0, 200));
    await page.waitForTimeout(2600);
    const after = await cardState(page.locator(card).first());
    check('revised', `${sid} 估算后显示“约 20 Credits”、批准可点、按钮收起`, after.text.includes('约 20 Credits') && after.approve === false && after.again === 0 && after.retry === 0,
      JSON.stringify({ text: after.text.slice(0, 160), approve: after.approve, again: after.again }));
    await shot(`d75-${sid.replace(':', '-')}-after`);
  };
  await flow(`/tasks/${I.richTask}`, '.sg-authblock .sg-approval', '2736:1011');
  await flow('/inbox?filter=decide', '.sg-decide__card .sg-approval', '2732:797');
  await page.unroute(estimateUrl, estimateRoute);
  await page.unroute(pattern, rewrite);
}
// D79: before kickoff the card is 开工确认 with the design's subtitle / plan summary / capability rows; once started
// (task and kickoff GETs rewritten to running, no step moved yet) it stays read-only as 任务说明 under 任务已开始.
if (want('kickoff')) {
  const list = await apiGet(BASE, 'tasks?status=awaiting_kickoff').catch(() => ({ items: [] }));
  const kid = (list.items.find((x) => x.status === 'awaiting_kickoff') || list.items[0] || {}).id;
  check('kickoff', '种子里有待开工任务', !!kid, String(kid));
  if (kid) {
    await open(`/tasks/new?task=${kid}#confirm`, 900);
    const pre = (await page.locator('.sg-brief').first().innerText().catch(() => '')).replace(/\s+/g, ' ');
    check('kickoff', '开工前标题“开工确认”与副标题', pre.startsWith('开工确认') && pre.includes('复杂任务 · 预计 5 位专家 · 约 25 分钟'), pre.slice(0, 80));
    check('kickoff', '开工前能力行“技能 / 连接器 · 只读”与“安装 / 连接”', pre.includes('技能') && pre.includes('连接器 · 只读') && pre.includes('安装') && pre.includes('连接'), pre.slice(0, 400));
    check('kickoff', '开工前计划摘要与提示', pre.includes('执行计划 · 8 步 · 5 位专家（展开查看与调整）') && pre.includes('不安装也可以开始，相关步骤会标为“缺少能力”'), pre.slice(-200));
    check('kickoff', '开工前有“开始执行”', (await page.locator('.sg-brief').getByRole('button', { name: '开始执行', exact: true }).count()) === 1);
    const started = async (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      const resp = await route.fetch();
      const json = await resp.json().catch(() => null);
      if (json == null) return route.fulfill({ response: resp });
      const t = json.task && json.task.id ? json.task : json;
      if ((t.id === kid || t.task_id === kid) && t.status === 'awaiting_kickoff') t.status = 'running';
      return route.fulfill({ response: resp, json });
    };
    const pattern = new RegExp(`/api/app/tasks/${kid}(/kickoff)?$`);
    await page.route(pattern, started);
    await open(`/tasks/${kid}`, 900);
    const body = (await page.locator('.sg-taskpage, main').first().innerText().catch(() => '')).replace(/\s+/g, ' ');
    const card = page.locator('.sg-brief').first();
    const post = (await card.innerText().catch(() => '')).replace(/\s+/g, ' ');
    check('kickoff', '开工后显示“任务已开始，进度将在右侧更新。”', body.includes('任务已开始，进度将在右侧更新。') && !body.includes('开始前先和你确认几件事'), body.slice(0, 200));
    check('kickoff', '开工后卡片标题“任务说明”、问题已确认', post.startsWith('任务说明') && post.includes('1 · 已确认的问题') && post.includes('重点评估哪个渠道？'), post.slice(0, 120));
    check('kickoff', '开工后保留任务说明与验收清单', post.includes('判断家用小家电是否值得在 12 月前进入德国站') && post.includes('覆盖前 20 名竞品与价格带') && post.includes('关键数字由清账复核'), post.slice(0, 400));
    const ro = {
      start: await card.getByRole('button', { name: '开始执行', exact: true }).count(),
      cancel: await card.getByRole('button', { name: '取消', exact: true }).count(),
      chip: await card.getByRole('radio').first().isDisabled().catch(() => null),
      add: await card.getByRole('button', { name: /添加一条/ }).first().isDisabled().catch(() => null),
    };
    check('kickoff', '开工后只读：无“开始执行 / 取消”，选项与“添加一条”禁用', ro.start === 0 && ro.cancel === 0 && ro.chip === true && ro.add === true, JSON.stringify(ro));
    await shot('d79-kickoff-started');
    await page.unroute(pattern, started);
  }
}// 4: 2736:43 / 2089:2924 改为模态弹窗（review issue 4），有效期文案（issue 5）。
if (want('modal')) {
  await open(`/tasks/${I.richTask}`, 900);
  const validity = await page.locator('.sg-authblock .sg-approval__grid').first().innerText().catch(() => '');
  check('modal', '有效期显示“仅本次任务”', validity.includes('仅本次任务') && !validity.includes('仅本次请求'), validity.replace(/\s+/g, ' ').slice(0, 120));
  await page.locator('.sg-authblock').getByRole('button', { name: '修改范围', exact: true }).first().click();
  await page.waitForTimeout(400);
  const dlg = page.locator('.sg-modal-backdrop [role="dialog"].sg-formdialog');
  const txt = (await dlg.innerText().catch(() => '')).replace(/\s+/g, ' ');
  check('modal', '2736:43 模态弹窗', (await dlg.count()) === 1 && (await dlg.getAttribute('aria-modal')) === 'true');
  check('modal', '2736:43 文案', ['调整本次查询范围', '当前范围：Amazon.de · 小家电类目 · 前 50 名', '动作：只读查询，不写入任何外部系统', '选择查询数量', '前 50 名', '前 20 名', '确认范围不会批准，费用将重新估算。', '取消', '确认范围'].every((t) => txt.includes(t)), txt.slice(0, 200));
  check('modal', '2736:43 未改选时“确认范围”不可点', await dlg.getByRole('button', { name: '确认范围' }).isDisabled());
  await shot('2736-43');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
  check('modal', '2736:43 Esc 关闭', (await dlg.count()) === 0);

  let found = null;
  for (const t of [I.deliveredTask, I.richTask].filter(Boolean)) {
    await open(`/tasks/${t}`, 900);
    if (await page.getByRole('button', { name: '退回修改', exact: true }).count()) { found = t; break; }
  }
  check('modal', '找到待验收任务', !!found);
  if (found) {
    await page.getByRole('button', { name: '退回修改', exact: true }).first().click();
    await page.waitForTimeout(400);
    const rd = page.locator('.sg-modal-backdrop [role="dialog"].sg-formdialog');
    const rt = (await rd.innerText().catch(() => '')).replace(/\s+/g, ' ');
    check('modal', '2089:2924 模态弹窗', (await rd.count()) === 1);
    check('modal', '2089:2924 文案', ['填写退回原因', '请说明需要修改的内容。', '退回原因（必填）', '取消', '提交退回'].every((t) => rt.includes(t)) && (await rd.locator('input[placeholder="请填写退回原因"]').count()) === 1, rt.slice(0, 160));
    check('modal', '2089:2924 输入框获得焦点', await rd.locator('input').evaluate((el) => el === document.activeElement));
    await shot('2089-2924');
    await rd.getByRole('button', { name: '提交退回' }).click();
    await page.waitForTimeout(300);
    check('modal', '2089:2924 空原因提示', (await rd.getByText('请填写退回原因后再提交。').count()) === 1);
    await rd.getByRole('button', { name: '取消', exact: true }).click();
    await page.waitForTimeout(300);
    check('modal', '2089:2924 取消关闭', (await rd.count()) === 0);
  }
}
// 1b: 专家头像在任务和工作区页显示照片（review issue 3）。
if (want('avatar')) {
  for (const r of [`/tasks/${I.richTask}`, '/tasks', '/tasks/active', `/workspaces/${I.workspace}`]) {
    await open(r, 900);
    const n = await page.evaluate(() => {
      const all = [...document.querySelectorAll('.sg-avatar')].filter((e) => e.getClientRects().length);
      const imgs = all.filter((e) => { const i = e.querySelector('img'); return i && i.complete && i.naturalWidth > 0; });
      return { all: all.length, imgs: imgs.length };
    });
    check('avatar', r, n.imgs > 0, `imgs=${n.imgs}/${n.all}`);
  }
  const lil = await page.evaluate(() => [...document.querySelectorAll('.sg-avatar[title="Lillian Li"] span')].map((e) => e.textContent).filter(Boolean)[0] ?? '');
  check('avatar', 'Lillian Li 首字母为 L', lil === '' || lil === 'L', lil);
}
// 7: 任务对话主责专家消息带编号列表、附件和来源（review issue 7）。
if (want('conv')) {
  for (const [w, h, name] of [[1440, 900, '2736-conv'], [1024, 900, '709-2'], [800, 900, '712-2']]) {
    await page.setViewportSize({ width: w, height: h });
    await open(`/tasks/${I.richTask}`, 900);
    const msg = page.locator('.sg-msg', { hasText: '我把任务拆成 5 步' }).first();
    const items = await msg.locator('.sg-msg__list > li').allInnerTexts().catch(() => []);
    const cites = await msg.locator('.sg-conv__cite').allInnerTexts().catch(() => []);
    check('conv', `${w} 编号列表 3 条`, items.length === 3 && items[2].includes('WEEE'), items.join(' | ').slice(0, 80));
    check('conv', `${w} 来源 3 条`, cites.join('|') === '竞品清单.xlsx|Amazon.de 类目页 · 11月3日|德国 EPR 法规摘要.pdf', cites.join('|'));
    check('conv', `${w} 查看过程 3/5`, (await msg.innerText()).includes('3/5'));
    await page.evaluate(() => document.querySelector('.sg-conv__pin')?.click());
    await page.waitForTimeout(200);
    const files = await page.locator('.sg-msg__file').allInnerTexts();
    const pin = await page.locator('.sg-conv__pin').innerText().catch(() => '');
    check('conv', `${w} 首条消息附件`, files.some((f) => f.includes('竞品清单.xlsx')) || pin.includes('竞品清单.xlsx'), (files.join('|') || pin).slice(0, 80));
    await msg.scrollIntoViewIfNeeded().catch(() => undefined);
    await shot(name);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}
// 8: 自动化列表 640:2 布局（review issue 8）。
if (want('autolist')) {
  await open('/automations', 900);
  const tabs = await page.locator('.sg-tabs [role="tab"], .sg-tabs button').allInnerTexts();
  check('autolist', '模板标签存在', tabs.some((t) => t.replace(/\d/g, '').trim() === '模板'), tabs.join('|'));
  const head = await page.locator('.sg-autorow--head').innerText();
  check('autolist', '表头 规则/下次运行/最近结果/启用', ['规则', '下次运行', '最近结果', '启用'].every((t) => head.includes(t)), head.replace(/\s+/g, ' '));
  const layout = await page.evaluate(() => {
    const row = [...document.querySelectorAll('.sg-autorow:not(.sg-autorow--head)')][0];
    const sw = row?.querySelector('.sg-autorow__col--toggle [role="switch"]');
    const name = row?.querySelector('.sg-autorow__name');
    const avatarInName = !!row?.querySelector('.sg-autorow__main .sg-avatar');
    const avatarInSide = !!row?.querySelector('.sg-autorow__side > .sg-avatar');
    const menu = row?.querySelector('.sg-autorow__side [aria-label$="更多操作"]');
    const page = document.querySelector('.sg-autopage');
    return {
      switchRight: !!sw && !!name && sw.getBoundingClientRect().left > name.getBoundingClientRect().left,
      avatarInName, avatarInSide, menu: !!menu,
      maxWidth: page ? getComputedStyle(page).maxWidth : '',
      marginLeft: page ? getComputedStyle(page).marginLeft : '',
    };
  });
  check('autolist', '开关在右侧“启用”列', layout.switchRight);
  check('autolist', '头像在名称行内、无头像列', layout.avatarInName && !layout.avatarInSide, JSON.stringify(layout));
  check('autolist', '每行有“…”菜单', layout.menu);
  check('autolist', '内容区不限宽居中', layout.maxWidth === 'none' && layout.marginLeft === '0px', `${layout.maxWidth}/${layout.marginLeft}`);
  const cells = await page.evaluate(() => [...document.querySelectorAll('.sg-autorow:not(.sg-autorow--head)')].map((r) => {
    const name = r.querySelector('.sg-autorow__name')?.textContent ?? '';
    const cols = [...r.querySelectorAll('.sg-autorow__col')].map((c) => c.textContent?.trim());
    return { name, cols };
  }));
  const bf = cells.find((c) => c.name.includes('黑五库存预警'));
  const mth = cells.find((c) => c.name.includes('月度经营复盘'));
  check('autolist', '黑五库存预警：已暂停 / —', !!bf && bf.cols[0] === '已暂停' && bf.cols[1] === '—', JSON.stringify(bf?.cols));
  check('autolist', '月度经营复盘：已错过 · 立即运行', !!mth && (mth.cols[1] ?? '').includes('已错过 · 立即运行'), JSON.stringify(mth?.cols));
  await shot('640-2');
  await page.locator('.sg-autorow:not(.sg-autorow--head) [aria-label$="更多操作"]').first().click();
  await page.waitForTimeout(300);
  const menuText = await page.locator('[role="menu"], .sg-menu').first().innerText().catch(() => '');
  check('autolist', '…菜单项', ['编辑', '查看运行记录'].every((t) => menuText.includes(t)), menuText.replace(/\s+/g, ' '));
  await page.keyboard.press('Escape');
  await page.getByRole('tab', { name: '模板' }).click();
  await page.waitForTimeout(700);
  const tpl = await page.locator('.sg-autolist').innerText().catch(() => '');
  check('autolist', '模板页列出 Playbook', tpl.includes('新市场进入评估') && tpl.includes('用它新建自动化'), tpl.replace(/\s+/g, ' ').slice(0, 120));
}
// 14: 通知中心 642:2 左对齐、齿轮图标、无整行底色（review 轻微 14）。
if (want('notif')) {
  await open('/notifications', 900);
  const layout = await page.evaluate(() => {
    const el = document.querySelector('.sg-notifpage');
    if (!el) return null;
    const cs = getComputedStyle(el);
    return { maxWidth: cs.maxWidth, marginLeft: cs.marginLeft, text: '' };
  });
  check('notif', '内容区左对齐不限宽', !!layout && layout.maxWidth === 'none' && layout.marginLeft === '0px', JSON.stringify(layout));
  const gear = await page.evaluate(() => {
    const btn = [...document.querySelectorAll('button')].find((b) => (b.textContent || '').includes('通知设置'));
    return btn ? !!btn.querySelector('.sg-icon, svg') : null;
  });
  check('notif', '通知设置带齿轮图标', gear === true, String(gear));
  const rowBg = await page.evaluate(() => {
    const row = [...document.querySelectorAll('.sg-notif')].find((e) => e.getClientRects().length);
    return row ? getComputedStyle(row).backgroundColor : null;
  });
  check('notif', '通知行无底色', rowBg === 'rgba(0, 0, 0, 0)' || rowBg === 'transparent', String(rowBg));
  await shot('642-2');
}
// 15: 用量与账单 641:2 数字分级、进度条读“剩余”比例、重置日读服务月终点（review 轻微 15 + 台账修正）。
if (want('usage')) {
  const summary = await apiGet(BASE, 'usage/summary');
  const remaining = Math.max(0, Number(summary.period_total) - Number(summary.period_used));
  await open('/billing', 900);
  const big = await page.evaluate(() => {
    const rest = document.querySelector('.sg-bill__big-total');
    const bigEl = document.querySelector('.sg-bill__big');
    if (!rest || !bigEl) return null;
    const big = parseFloat(getComputedStyle(bigEl).fontSize);
    const small = parseFloat(getComputedStyle(rest).fontSize);
    return { big, small, text: bigEl.textContent };
  });
  check('usage', '“/ 总量 Credits”字号小于剩余数字', !!big && big.small < big.big * 0.7, JSON.stringify(big));
  const meter = await page.evaluate(() => {
    const m = document.querySelector('.sg-bill__meter[role="meter"]');
    const fill = m?.querySelector('span');
    return m && fill ? { label: m.getAttribute('aria-label'), now: m.getAttribute('aria-valuenow'), width: fill.style.width } : null;
  });
  const expectRatio = Math.round(Math.min(1, remaining / Number(summary.period_total)) * 100);
  check('usage', '进度条按剩余比例填充', !!meter && meter.label === '本期剩余' && meter.now === String(expectRatio), JSON.stringify(meter) + ' expect=' + expectRatio);
  const end = new Date(new Date(summary.period_end).getTime() - 1);
  const expectReset = `${end.getMonth() + 1}月${end.getDate()}日重置`;
  const foot = await page.locator('.sg-bill__cardfoot').first().innerText().catch(() => '');
  check('usage', '重置日读 usage/summary 的 period_end', foot.includes(expectReset), `${foot.replace(/\s+/g, ' ')} expect=${expectReset}`);
  await shot('641-2');
}
// 16: 审计日志事件码显示中文描述（review 轻微 16）。
if (want('audit')) {
  await open('/settings/org/policy', 900);
  const cells = await page.locator('.sg-orgtable--audit .sg-orgtable__row:not(.is-head) [role="cell"]:nth-child(3)').allInnerTexts();
  const raw = cells.filter((c) => /^[a-z_]+(\.[a-z_]+)+$/.test(c.trim()));
  check('audit', '操作列没有原始事件码', raw.length === 0, raw.join('|') + ' cells=' + cells.join('|'));
  check('audit', 'business_invitation.created 显示为“发出邀请”', cells.some((c) => c.trim() === '发出邀请'), cells.join('|'));
  await shot('759-2');
}
// 17: 资料详情 2468:1129 正文卡片边框；两份 EPR 副本都挂同样的关联实体（review 轻微 17）。
if (want('doc')) {
  const kbs = await apiGet(BASE, 'knowledge-bases');
  const byName = (n) => kbs.items.find((k) => k.name === n)?.id;
  const docs = {};
  for (const n of ['德国市场进入资料库', '德国市场法规库']) {
    const list = await apiGet(BASE, `documents?knowledge_base=${encodeURIComponent(byName(n))}`);
    docs[n] = list.items.find((d) => d.title === '德国 EPR 法规摘要.pdf')?.id;
  }
  check('doc', '两份 EPR 副本都能找到', !!docs['德国市场进入资料库'] && !!docs['德国市场法规库'], JSON.stringify(docs));
  for (const [n, id] of Object.entries(docs)) {
    if (!id) continue;
    const detail = await apiGet(BASE, `documents/${encodeURIComponent(id)}`);
    const names = (detail.related_entities ?? []).map((e) => e.name).sort().join('、');
    check('doc', `${n} 关联实体与图谱一致`, names === 'Amazon.de 合规核验、VerpackG、WEEE 注册', names);
    if (n === '德国市场进入资料库') check('doc', '进入资料库副本被引用 18 次', detail.citation_count === 18, String(detail.citation_count));
  }
  const id = docs['德国市场法规库'] ?? docs['德国市场进入资料库'];
  if (id) {
    await open(`/knowledge/${encodeURIComponent(byName('德国市场法规库'))}/docs/${encodeURIComponent(id)}`, 900);
    const border = await page.evaluate(() => {
      const el = document.querySelector('.sg-docpage .sg-docpreview');
      if (!el) return null;
      const cs = getComputedStyle(el);
      return { width: cs.borderTopWidth, style: cs.borderTopStyle, radius: cs.borderRadius };
    });
    check('doc', '正文有卡片边框', !!border && border.width === '1px' && border.style === 'solid', JSON.stringify(border));
    await shot('2468-1129');
  }
}
// 台账修正：68 外观页 730:2 主题卡预览补 Sage 标志与头像。
if (want('theme')) {
  await open('/settings/appearance', 900);
  const cards = await page.evaluate(() =>
    [...document.querySelectorAll('.sg-themecard__preview')].map((p) => ({
      logo: !!p.querySelector('[data-icon="sage-logo"] svg, [data-icon="sage-logo"]'),
      avatar: (() => { const i = p.querySelector('img.sg-themecard__avatar'); return !!i && i.complete && i.naturalWidth > 0; })(),
    })),
  );
  check('theme', '5 张主题卡都有 Sage 标志', cards.length === 5 && cards.every((c) => c.logo), JSON.stringify(cards));
  check('theme', '5 张主题卡都有头像且加载成功', cards.length === 5 && cards.every((c) => c.avatar), JSON.stringify(cards));
  await shot('730-2');
}
// D81: the save-as-playbook dialog starts from the task's playbook_template; the automation editor shows the
// "你创建" meta, the example-variables label and 待确认 rows for lead expert, permissions and run estimate.
if (want('playbook')) {
  const tid = I.richTask;
  const tpl = await apiGet(BASE, `tasks/${tid}`).then((t) => t.playbook_template).catch(() => null);
  check('playbook', '主任务详情返回 playbook_template', !!tpl && tpl.name === '新市场进入评估' && tpl.steps.length === 6 && tpl.variables.length === 3, JSON.stringify(tpl).slice(0, 200));
  const accepted = async (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    const resp = await route.fetch();
    const json = await resp.json().catch(() => null);
    if (json && json.id === tid) json.status = 'accepted';
    return route.fulfill({ response: resp, json: json ?? undefined });
  };
  const detailUrl = new RegExp(`/api/app/tasks/${tid}$`);
  await page.route(detailUrl, accepted);
  let posted = null;
  const capture = (route) => { posted = route.request().postDataJSON(); return route.fulfill({ status: 201, contentType: 'application/json', body: '{"playbook":{"id":"x"}}' }); };
  await page.route('**/api/app/playbooks', capture);
  await open(`/tasks/${tid}#save-playbook`, 1200);
  const dlg = page.locator('.sg-pbdialog').first();
  const txt = (await dlg.innerText().catch(() => '')).replace(/\s+/g, ' ');
  const nameVal = await dlg.getByRole('textbox', { name: 'Playbook 名称' }).inputValue().catch(() => '');
  check('playbook', '对话框名称默认“新市场进入评估”', nameVal === '新市场进入评估', nameVal);
  check('playbook', '默认勾选 6 步', txt.includes('步骤 · 6（来自本次执行图，可以删减）'), txt.slice(0, 160));
  check('playbook', '步骤行显示变量与节点名', txt.includes('{市场} · 竞品格局分析') && txt.includes('{平台} · 关键词研究') && txt.includes('· 报告汇总'), txt.slice(0, 400));
  check('playbook', '变量默认值', txt.includes('变量 · 运行时填写') && txt.includes('{市场} = 德国') && txt.includes('{平台} = Amazon.de') && txt.includes('{品类} = 家用小家电'), txt.slice(-300));
  await shot('d81-687-2');
  await dlg.getByRole('button', { name: '保存 Playbook', exact: true }).click().catch(() => undefined);
  await page.waitForTimeout(800);
  const titles = (posted?.steps ?? []).map((s) => s.title);
  check('playbook', '保存请求：6 步、标题带变量、只提交被引用的变量', titles.length === 6 && titles.includes('{市场} 竞品格局分析') && titles.some((t) => t.includes('{平台}'))
    && JSON.stringify((posted?.variables ?? []).map((v) => v.name)) === JSON.stringify(['市场', '平台']), JSON.stringify(posted).slice(0, 400));
  await page.unroute('**/api/app/playbooks', capture);
  await page.unroute(detailUrl, accepted);

  await open('/automations/new/edit?type=playbook', 900);
  const pre = (await page.locator('main').innerText().catch(() => '')).replace(/\s+/g, ' ');
  check('playbook', '新建 Playbook 型：未选时主责、权限与预计都显示“待确认”', pre.includes('主责专家 待确认') && pre.includes('完成设置后确认 选择内容后显示 待确认') && (pre.match(/待确认/g) || []).length >= 5, pre.slice(0, 600));
  await page.getByRole('button', { name: /Playbook$/ }).click();
  await page.getByRole('dialog', { name: '选择 Playbook' }).locator('.sg-autopick__row', { hasText: '新市场进入评估' }).first().click();
  await page.waitForTimeout(400);
  const card = (await page.locator('.sg-playbook').first().innerText().catch(() => '')).replace(/\s+/g, ' ');
  check('playbook', 'Playbook 卡片“6 步 · 5 位专家 · 你创建”与变量示例标签', card.includes('6 步 · 5 位专家 · 你创建') && card.includes('变量示例（运行前规则待确认）：'), card);
  await page.getByPlaceholder('请输入名称').fill('新品上架清单');
  const sel = (l, v) => page.locator(`select[aria-label="${l}"]`).first().selectOption({ label: v });
  await sel('归属工作区', '德国市场进入'); await sel('频率', '每周'); await sel('日期', '周一'); await sel('时间', '09:00');
  await page.waitForTimeout(300);
  const full = (await page.locator('main').innerText().catch(() => '')).replace(/\s+/g, ' ');
  check('playbook', '填写完成：计划下“保存后仍需启用”，权限“启用时确认 / 具体权限 / 待确认”', full.includes('保存后仍需启用') && full.includes('启用时确认 具体权限 待确认') && full.includes('填写完成，可以保存'), full.slice(0, 900));
  await shot('d81-2909-43');
  await page.locator('.sg-inchips').getByRole('radio', { name: '一段指令' }).click();
  await page.getByPlaceholder('请输入要运行的指令').fill('整理本周上架的新品并生成清单。');
  await page.locator('select[aria-label="主责专家"]').selectOption({ index: 1 });
  await page.waitForTimeout(300);
  const side = (await page.locator('.sg-sidecard').first().innerText().catch(() => '')).replace(/\s+/g, ' ');
  check('playbook', '指令态填写完成后预计显示“待估算”', (side.match(/待估算/g) || []).length === 3, side);
}if (want('report')) {
  // placeholder group so --only report prints the summary only
}

const failed = results.filter((r) => !r.ok);
for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'}\t${r.group}\t${r.name}\t${r.detail}`);
console.log(JSON.stringify({ total: results.length, passed: results.length - failed.length, failed: failed.length, pageerrors: errors.length }));
await browser.close();
process.exit(failed.length ? 1 : 0);
