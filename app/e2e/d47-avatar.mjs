// D47: expert avatars follow the theme style (assets/avatars/<set>/<employee ID>.webp) without a reload; real people's
// avatars do not change. Also checks each 外观 theme card previews its own avatar set, and saves screenshots for
// comparison with the theme variant screens 84–98.
// Usage: PLAYWRIGHT_MODULE=... SAGE_URL=http://127.0.0.1:5188 SAGE_SESSION_FILE=../tmp/d47-session.txt node e2e/d47-avatar.mjs [--shots DIR]
// Writes: uploads one profile avatar for the signed-in user and PATCHes theme_style (restored to the starting style).
import { mkdirSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { apiGet, cookie } from './seed-ids.mjs';

const require = createRequire(import.meta.url);
const { chromium } = require(path.join(process.env.PLAYWRIGHT_MODULE, 'index.js'));
const BASE = process.env.SAGE_URL || 'http://127.0.0.1:5188';
const arg = (n) => (process.argv.includes(n) ? process.argv[process.argv.indexOf(n) + 1] : '');
const SHOTS = arg('--shots');
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

const STYLES = [
  ['business_steady', '商务 · 稳重', 'photo'],
  ['neo_electric', '新锐 · 电光', 'iso'],
  ['fashion_editorial', '时尚 · 编辑', 'collage'],
  ['comfort_readable', '舒适 · 易读', 'flat'],
  ['minimal_focus', '极简 · 专注', 'line'],
];
const results = [];
const check = (name, ok, detail = '') => results.push({ name, ok: !!ok, ...(detail ? { detail: String(detail).slice(0, 300) } : {}) });

const [tasks, wss] = await Promise.all([apiGet(BASE, 'tasks'), apiGet(BASE, 'workspaces')]);
const taskId = tasks.items.find((t) => t.title.includes('德国站竞品格局调研'))?.id;
const wsId = wss.items.find((w) => w.name.includes('德国市场进入'))?.id;
if (!taskId || !wsId) throw new Error('seed task / workspace not found');
const ROUTES = { task: `/tasks/${taskId}`, workspace: `/workspaces/${wsId}`, newTask: '/tasks/new' };

const raw = cookie();
const eq = raw.indexOf('=');
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, locale: 'zh-CN' });
await ctx.addCookies([{ name: raw.slice(0, eq), value: raw.slice(eq + 1), url: BASE }]);
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));

// A real person's own avatar: upload one profile photo so the account bar shows an image, not initials.
await page.goto(BASE + '/settings/appearance', { waitUntil: 'networkidle' });
const png = readFileSync(new URL('../electron/icons/business_steady.png', import.meta.url)).toString('base64');
const upload = await page.evaluate(async (b64) => {
  const s = await fetch('/api/auth/session').then((r) => r.json());
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const form = new FormData();
  form.append('avatar', new File([bytes], 'me.png', { type: 'image/png' }));
  const r = await fetch('/api/account/avatar', { method: 'POST', body: form, headers: { 'X-CSRFToken': s.csrf_token } });
  return { status: r.status, body: await r.json().catch(() => null), name: s.user?.display_name || s.user?.name || '' };
}, png);
check('真人头像上传成功', upload.status === 200 && upload.body?.avatar_url, JSON.stringify(upload));
const startStyle = (await page.evaluate(() => fetch('/api/account/profile').then((r) => r.json()))).theme_style;

await page.goto(BASE + '/settings/appearance', { waitUntil: 'networkidle' });
await page.evaluate(() => (window.__d47 = 'same-document'));

// 外观 theme cards: each previews its own set.
const cards = await page.evaluate(() =>
  [...document.querySelectorAll('.sg-themecard')].map((c) => {
    const i = c.querySelector('img.sg-themecard__avatar');
    return { theme: c.querySelector('.sg-themecard__preview')?.getAttribute('data-theme'), src: i?.getAttribute('src'), w: i?.naturalWidth ?? 0 };
  }),
);
check(
  '外观页 5 张主题卡各显示自己风格的 AGT-007 头像且已加载',
  cards.length === 5 && STYLES.every(([s, , set]) => cards.some((c) => c.theme === s && c.src === `/api/app/avatars/assets/avatars/${set}/AGT-007.webp` && c.w > 0)),
  JSON.stringify(cards),
);

async function spaGo(route) {
  await page.evaluate((r) => {
    history.pushState(null, '', r);
    dispatchEvent(new PopStateEvent('popstate'));
  }, route);
  await page.waitForLoadState('networkidle');
  // Lists render in several fetches; wait until the avatar count stops changing and every image settled.
  let last = -1;
  for (let i = 0; i < 20; i++) {
    await page.waitForTimeout(250);
    const n = await page.evaluate(() => {
      const imgs = [...document.querySelectorAll('.sg-avatar img')];
      return imgs.every((x) => x.complete) ? document.querySelectorAll('.sg-avatar').length : -2;
    });
    if (n === last && n >= 0) break;
    last = n;
  }
}
const avatars = () =>
  page.evaluate(() =>
    [...document.querySelectorAll('.sg-avatar')]
      .filter((e) => e.getClientRects().length)
      .map((e) => {
        const i = e.querySelector('img');
        return { name: e.getAttribute('title'), src: i?.getAttribute('src') ?? null, w: i?.naturalWidth ?? 0, initials: i ? null : e.textContent };
      }),
  );

const seen = {};
let expert = null;
for (const [style, label, set] of STYLES) {
  if (page.url() !== BASE + '/settings/appearance') await spaGo('/settings/appearance');
  const card = page.locator('.sg-themecard', { hasText: label });
  if ((await card.getAttribute('aria-checked')) !== 'true') {
    const saved = page.waitForResponse((r) => r.url().includes('/api/account/profile') && r.request().method() === 'PATCH');
    await card.click();
    await saved;
  }
  await page.waitForFunction((s) => document.documentElement.dataset.theme === s, style);
  for (const [key, route] of Object.entries(ROUTES)) {
    await spaGo(route);
    const list = await avatars();
    if (!expert) expert = list.find((a) => a.src?.includes('/assets/avatars/'))?.name ?? null;
    (seen[key] ||= {})[style] = list;
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${style}-${key}.png`) });
  }
  if (SHOTS) {
    await spaGo('/settings/appearance');
    await page.screenshot({ path: path.join(SHOTS, `${style}-appearance.png`) });
  }
  const themed = Object.values(seen).flatMap((byStyle) => byStyle[style]).filter((a) => a.src?.includes('/assets/avatars/'));
  check(`${label}：专家头像都取 ${set} 套且 naturalWidth>0`, themed.length > 0 && themed.every((a) => a.src.includes(`/assets/avatars/${set}/`) && a.w > 0), `n=${themed.length} bad=${JSON.stringify(themed.filter((a) => !a.src.includes(`/${set}/`) || !a.w).slice(0, 3))}`);
}

check('切换主题全程没有整页刷新', (await page.evaluate(() => window.__d47)) === 'same-document');
check('找到用于跟踪的专家', !!expert, expert);
for (const key of Object.keys(ROUTES)) {
  const srcs = STYLES.map(([s]) => seen[key][s].find((a) => a.name === expert)?.src ?? null);
  if (srcs.every((x) => x === null)) continue;
  check(`${key}：专家「${expert}」头像随 5 种主题变化`, new Set(srcs).size === 5 && srcs.every((x, i) => x?.includes(`/assets/avatars/${STYLES[i][2]}/`)), srcs.join(' | '));
}
// Real people: the uploaded photo and initials-only members stay the same in every style.
const people = (list) =>
  list
    .filter((a) => !a.src?.includes('/api/app/avatars/'))
    .map((a) => `${a.name}=${a.src ?? 'initials:' + a.initials}`)
    .sort()
    .join(',');
for (const key of Object.keys(ROUTES)) {
  const sets = STYLES.map(([s]) => people(seen[key][s]));
  check(`${key}：真人成员头像 5 种主题下不变`, new Set(sets).size === 1, sets[0]);
}
const me = STYLES.map(([s]) => seen.task[s].find((a) => a.src === '/api/account/avatar-image'));
check('真人上传头像 src 不变且已加载', me.every((a) => a && a.w > 0), JSON.stringify(me.map((a) => a && [a.name, a.src, a.w])));

// Restore the starting style.
await page.evaluate((s) => fetch('/api/auth/session').then((r) => r.json()).then((x) => fetch('/api/account/profile', {
  method: 'PATCH', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': x.csrf_token }, body: JSON.stringify({ theme_style: s }),
})), startStyle);
check('无页面错误', errors.length === 0, errors.join(' | '));
await browser.close();
console.log(JSON.stringify({ passed: results.filter((r) => r.ok).length, total: results.length, results }, null, 1));
process.exit(results.every((r) => r.ok) ? 0 : 1);
