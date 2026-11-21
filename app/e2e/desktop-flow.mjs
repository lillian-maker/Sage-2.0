// Electron desktop flow: local folders + local index (16/19), sidebar hover card and 编辑工作区 (24),
// update button / phone card / 检查更新 (25), bridge security checks, and in a second client launch
// 设置 · 安全 device sessions (74) and 帮助与诊断 (79).
// Run: SAGE_URL=http://127.0.0.1:5189 SAGE_SESSION_FILE=../tmp/desktop-session.txt PLAYWRIGHT_MODULE=... node e2e/desktop-flow.mjs
import { renameSync, statSync } from 'node:fs';
import path from 'node:path';
import { api, launchDesktop, readCookie } from './desktop-harness.mjs';

const base = process.env.SAGE_URL || 'http://127.0.0.1:5189';
const results = [];
const step = (name, ok, extra = '') => results.push({ name, ok: !!ok, ...(extra ? { extra: String(extra).slice(0, 200) } : {}) });
const wss = await api(base, readCookie(), 'app/workspaces');
const ws = (wss.items || []).find((w) => w.name === '德国市场进入');
if (!ws) throw new Error('seed workspace 德国市场进入 not found');

const d = await launchDesktop({ base, feed: true, clock: '09:42' });
const { win } = d;
const errors = [];
win.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
win.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && errors.push('console: ' + m.text()));
const body = () => win.evaluate(() => document.body.innerText);
// The card opens on mouseenter after a 400ms intent delay, so the pointer must leave the row first.
async function hoverCard() {
  for (let i = 0; i < 3; i++) {
    await win.mouse.move(700, 450);
    await win.waitForTimeout(300);
    await win.locator('.sg-sidebar__ws', { hasText: '德国市场进入' }).hover();
    if (await win.waitForSelector('.sg-dsk-card', { timeout: 2000 }).then(() => true, () => false)) return;
  }
  throw new Error('hover card did not open');
}

try {
  // Bridge surface
  const surface = await win.evaluate(() => ({
    keys: Object.keys(window.sageDesktop || {}).sort(),
    frozen: Object.isFrozen(window.sageDesktop),
    ipc: typeof window.ipcRenderer,
    req: typeof window.require,
    proc: typeof window.process,
  }));
  step('bridge 暴露 18 个方法且冻结', surface.keys.length === 18 && surface.frozen, surface.keys.join(','));
  step('渲染进程无 ipcRenderer/require/process', surface.ipc === 'undefined' && surface.req === 'undefined' && surface.proc === 'undefined');

  // 16: add the first folder from the empty state, index builds
  await win.goto(`${base}/workspaces/${ws.id}`, { waitUntil: 'networkidle' });
  await win.waitForSelector('.sg-dsk-index[data-index-state="empty"]');
  step('空态显示“尚未添加本机目录”', (await body()).includes('尚未添加本机目录'));
  await d.queuePick(path.join(d.docs, '德国市场'));
  await win.locator('.sg-dsk-index').getByRole('button', { name: '添加文件夹' }).click();
  await win.waitForSelector('.sg-dsk-index[data-index-state="built"]', { timeout: 15000 });
  let text = await body();
  step('索引完成显示“已建立”', text.includes('已建立'));
  step('显示“上次更新 今天 09:42”', text.includes('上次更新 今天 09:42'));
  await win.getByRole('button', { name: '本机目录与登记' }).click();
  text = await body();
  step('登记列表显示 ~/Documents/德国市场 与主要标签', text.includes('~/Documents/德国市场') && text.includes('主要'));
  step('登记显示文件数统计', /5 个文件/.test(text), text.match(/\d+ 个文件[^\n]*/)?.[0]);
  await win.locator('.sg-dsk-folder__path', { hasText: '~/Documents/德国市场' }).click();
  await win.waitForSelector('.sg-dsk-dir');
  step('浏览文件夹列出文件', (await body()).includes('EPR 包装法合规清单.md'));

  // 24: hover card and 编辑工作区 → add the second folder
  await hoverCard();
  const card = await win.locator('.sg-dsk-card').innerText();
  step('悬停卡显示本机文件夹摘要', card.includes('本机文件夹 1 个（主要：德国市场）'), card.replace(/\n/g, ' | '));
  await win.locator('.sg-dsk-card').getByRole('button', { name: '编辑工作区' }).click();
  await win.waitForSelector('.sg-dsk-edit');
  await win.getByRole('button', { name: '添加文件夹或知识库' }).click();
  await d.queuePick(path.join(d.docs, '竞品资料'));
  await win.locator('.sg-dsk-sources__choices').getByRole('button', { name: '本机文件夹' }).click();
  await win.waitForFunction(() => document.querySelector('.sg-dsk-edit')?.textContent?.includes('~/Documents/竞品资料'));
  const edit = await win.locator('.sg-dsk-edit').innerText();
  step('编辑框列出两个本机文件夹', edit.includes('~/Documents/德国市场') && edit.includes('~/Documents/竞品资料'));
  step('编辑框显示成员与工作区类型', /\d+ 位成员 · (企业|个人)工作区/.test(edit), edit.match(/\d+ 位成员[^\n]*/)?.[0]);
  await win.locator('.sg-dsk-edit').getByRole('button', { name: '取消' }).click();
  await win.waitForSelector('.sg-dsk-edit', { state: 'detached' });
  await hoverCard();
  await win.waitForFunction(() => document.querySelector('.sg-dsk-card')?.textContent?.includes('本机文件夹 2 个'));
  step('悬停卡更新为“本机文件夹 2 个（主要：德国市场）”', (await win.locator('.sg-dsk-card').innerText()).includes('本机文件夹 2 个（主要：德国市场）'));
  await win.mouse.move(700, 450);

  // Bridge guards
  const guards = await win.evaluate(async () => {
    const b = window.sageDesktop;
    const [f] = await b.listFolders();
    const err = (p) => p.then(() => 'ok', (e) => String(e.message));
    return {
      traversal: await err(b.listDir(f.id, '../..')),
      absolute: await err(b.listDir(f.id, '/etc')),
      badId: await err(b.listDir('../x', '')),
      fileUrl: await err(b.openExternal('file:///etc/passwd')),
      httpUrl: await err(b.openExternal('http://example.com')),
    };
  });
  step('目录穿越被拒绝', /PATH_OUTSIDE_FOLDER/.test(guards.traversal) && /PATH_OUTSIDE_FOLDER/.test(guards.absolute), JSON.stringify(guards));
  step('非法文件夹 id 被拒绝', /FOLDER_NOT_FOUND|INVALID/.test(guards.badId), guards.badId);
  step('openExternal 只允许 https/mailto', /INVALID_URL/.test(guards.fileUrl) && /INVALID_URL/.test(guards.httpUrl));
  await d.queuePick('/');
  const broad = await win.evaluate((id) => window.sageDesktop.pickFolder(id).then(() => 'ok', (e) => String(e.message)), ws.id);
  step('拒绝添加磁盘根目录', /FOLDER_TOO_BROAD/.test(broad), broad);

  // Unavailable folder → 无法访问本机目录, then recover
  renameSync(path.join(d.docs, '竞品资料'), path.join(d.docs, '竞品资料-moved'));
  await win.reload({ waitUntil: 'networkidle' });
  await win.waitForSelector('.sg-dsk-index[data-index-state="unavailable"]', { timeout: 10000 });
  step('目录被移走显示“无法访问本机目录”', (await body()).includes('无法访问本机目录'));
  renameSync(path.join(d.docs, '竞品资料-moved'), path.join(d.docs, '竞品资料'));
  await win.locator('.sg-dsk-index').getByRole('button', { name: '重试' }).click();
  await win.waitForSelector('.sg-dsk-index[data-index-state="built"]', { timeout: 15000 });
  step('目录恢复后重试回到“已建立”', (await body()).includes('上次更新 今天 09:42'));

  // 19 reuses the rail behind the dialog
  await win.goto(`${base}/workspaces/new?from=${ws.id}`, { waitUntil: 'networkidle' });
  await win.waitForSelector('.sg-dsk-index[data-index-state="built"]');
  step('19 新建工作区背后的右栏显示本地索引', (await body()).includes('上次更新 今天 09:42'));

  // 25: update button 下载中 37% → 更新
  await win.goto(`${base}/workspaces/${ws.id}`, { waitUntil: 'networkidle' });
  await win.waitForSelector('.sg-dsk-update[data-update-state="downloading"]', { timeout: 15000 });
  await win.waitForFunction(() => document.querySelector('.sg-dsk-update')?.textContent?.includes('37%'), null, { timeout: 10000 });
  step('标题栏显示“下载中 37%”', (await win.locator('.sg-dsk-update').innerText()).includes('下载中 37%'));
  d.feed.release();
  await win.waitForSelector('.sg-dsk-update[data-update-state="ready"]', { timeout: 15000 });
  step('下载完成后显示“更新”', (await win.locator('.sg-dsk-update').innerText()).trim() === '更新');
  await win.locator('.sg-dsk-update').getByText('更新').click();
  await win.waitForSelector('text=安装更新？');
  step('点击“更新”弹出安装确认', true);
  await win.getByRole('button', { name: '稍后' }).click();

  // 25 ③ account menu: 检查更新 with version hint
  await win.locator('.sg-accountbar__who').click();
  const menuText = await win.getByRole('menu', { name: '账号菜单' }).innerText();
  step('账号菜单“检查更新”显示 v0.1.0', menuText.includes('检查更新') && menuText.includes('v0.1.0'));
  await win.getByRole('menuitem', { name: /检查更新/ }).click();
  await win.waitForSelector('.sg-shell__toast');
  step('检查更新给出结果提示', /新版本 0\.2\.0 已就绪/.test(await win.locator('.sg-shell__toast').innerText()));

  // 25 ② phone card
  await win.getByRole('button', { name: '手机端' }).click();
  const mobile = await win.locator('.sg-dsk-mobile').innerText();
  step('手机图标弹出下载卡', mobile.includes('下载 Sage 手机 App') && mobile.includes('随时查看任务进度、处理审批'));
  await win.keyboard.press('Escape');

  // 732:17 / 732:297: the Dock icon follows the chosen theme style.
  await d.app.evaluate(({ app }) => {
    globalThis.__sageIcons = [];
    const real = app.dock.setIcon.bind(app.dock);
    app.dock.setIcon = (img) => {
      globalThis.__sageIcons.push(img.getSize().width);
      return real(img);
    };
  });
  const startIcon = (await win.evaluate(() => window.sageDesktop.getInfo())).appIconStyle;
  await win.goto(`${base}/settings/appearance`, { waitUntil: 'networkidle' });
  const startStyle = await win.evaluate(() => document.documentElement.dataset.theme);
  const pickStyle = async (label, style) => {
    const card = win.locator('.sg-themecard', { hasText: label });
    if ((await card.getAttribute('aria-checked')) !== 'true') await card.click();
    await win.waitForFunction((s) => window.sageDesktop.getInfo().then((i) => i.appIconStyle === s), style, { timeout: 10000, polling: 200 }).catch(() => undefined);
    return (await win.evaluate(() => window.sageDesktop.getInfo())).appIconStyle;
  };
  const neo = await pickStyle('新锐 · 电光', 'neo_electric');
  const back = await pickStyle('商务 · 稳重', 'business_steady');
  const sizes = await d.app.evaluate(() => globalThis.__sageIcons);
  const badStyle = await win.evaluate(() => window.sageDesktop.setAppIcon('../x').then(() => 'ok', (e) => String(e.message)));
  step(
    'App 图标随主题切换（Dock 图标替换为对应风格）',
    startIcon === startStyle && neo === 'neo_electric' && back === 'business_steady' && sizes.length >= 2 && sizes.every((w) => w === 1024) && /INVALID_ICON_STYLE/.test(badStyle),
    JSON.stringify({ startIcon, startStyle, neo, back, sizes, badStyle }),
  );

  // Persistence across restarts is file-based; the store file stays outside the web origin.
  const stored = statSync(path.join(d.userData, 'sage-desktop.json')).mode & 0o777;
  step('登记文件权限 0600', stored === 0o600, stored.toString(8));
} catch (e) {
  step('流程异常', false, e.message);
  if (process.env.FAIL_SHOT) await win.screenshot({ path: process.env.FAIL_SHOT }).catch(() => undefined);
} finally {
  await d.close();
}
// Second launch: the release feed reports the running version, so the client is on the latest version.
const d2 = await launchDesktop({ base, feed: { version: '0.1.0' }, clock: '09:42' });
const w2 = d2.win;
w2.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
const body2 = () => w2.evaluate(() => document.body.innerText);
try {
  await w2.goto(`${base}/workspaces/${ws.id}`, { waitUntil: 'networkidle' });
  await d2.queuePick(path.join(d2.docs, '德国市场'));
  await w2.evaluate((id) => window.sageDesktop.pickFolder(id), ws.id);
  await w2.waitForSelector('.sg-dsk-index[data-index-state="built"]', { timeout: 15000 });
  await d2.setClock('10:41', { frozen: true });
  await d2.simulateLatency(86);

  // 79 帮助与诊断
  await w2.goto(`${base}/settings/help`, { waitUntil: 'networkidle' });
  await w2.waitForFunction(() => document.body.innerText.includes('上次成功连接'), null, { timeout: 10000 });
  await w2.waitForFunction(() => document.body.innerText.includes('已是最新版本'), null, { timeout: 10000 });
  let help = await body2();
  step('79 版本行“11月3日 发布 · 已是最新版本”', help.includes('11月3日 发布 · 已是最新版本'));
  step('79 连接“上次成功连接 10:41”与“正常”', help.includes('上次成功连接 10:41') && help.includes('正常'));
  step('79 本机运行时“运行中”', help.includes('运行中'));
  step('79 知识索引“最近一次 09:42 · 1 份失败”', help.includes('最近一次 09:42 · 1 份失败'));
  const diag = await w2.locator('.sg-diag').innerText();
  const info = await w2.evaluate(() => window.sageDesktop.getInfo());
  const osShort = info.osVersion.split('.').slice(0, 2).join('.');
  step(
    '79 诊断信息三行',
    diag.startsWith(`app=0.1.0 build=1103 os=${info.osName} ${osShort} arch=${info.arch}\nruntime=connected latency=86ms\nlast_error=KB_INDEX_PARSE_FAILED (LUCID 操作手册.pdf) 09:42`),
    diag.split('\n').slice(0, 3).join(' | '),
  );
  await w2.getByRole('button', { name: '部分失败 · 查看' }).click();
  await w2.waitForURL(`${base}/workspaces/${ws.id}`);
  step('79 “部分失败 · 查看”打开出错文件夹所在工作区', true);

  // 79 检查更新 → stays latest
  await w2.goto(`${base}/settings/help`, { waitUntil: 'networkidle' });
  await w2.getByRole('button', { name: '检查更新' }).click();
  await w2.waitForFunction(() => document.body.innerText.includes('11月3日 发布 · 已是最新版本'), null, { timeout: 10000 });
  step('79 检查更新后仍为最新', true);

  // 74 设置 · 安全
  await w2.goto(`${base}/settings/security`, { waitUntil: 'networkidle' });
  await w2.waitForFunction(() => document.body.innerText.includes('当前设备'), null, { timeout: 10000 });
  const sec = await body2();
  const title = `${info.deviceName} · Sage ${info.version}`;
  step('74 当前设备显示机型与客户端版本', sec.includes(title), title);
  const tz = await w2.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone);
  step('74 当前设备位置与时间', tz !== 'Asia/Shanghai' || sec.includes('上海 · 刚刚'), tz);
  step('74 授权记录显示请求原文“读取订单”', sec.includes('Amazon Seller Central · 读取订单') && sec.includes('Jungle Scout · 只读查询'));
  const sessions = await w2.evaluate(() => fetch('/api/account/sessions').then((r) => r.json()));
  const cur = (sessions.items || []).find((s) => s.is_current);
  step('74 服务端记录了 X-Sage-Client', cur && cur.device_name === info.deviceName && cur.app_version === info.version, JSON.stringify(cur && [cur.device_name, cur.app_version]));
} catch (e) {
  step('第二次启动流程异常', false, e.message);
  if (process.env.FAIL_SHOT) await w2.screenshot({ path: process.env.FAIL_SHOT }).catch(() => undefined);
} finally {
  await d2.close();
}

// Third launch: the feed's sha512 does not match the installer, so electron-updater rejects the download.
const d3 = await launchDesktop({ base, feed: { badChecksum: true } });
d3.feed.release();
const w3 = d3.win;
w3.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
try {
  await w3.goto(`${base}/tasks`, { waitUntil: 'networkidle' });
  await w3.waitForSelector('.sg-dsk-update[data-update-state="failed"]', { timeout: 20000 });
  step('sha512 不符时标题栏显示“更新失败”', (await w3.locator('.sg-dsk-update').innerText()).trim() === '更新失败');
  const st = await w3.evaluate(() => window.sageDesktop.getUpdateState());
  step('失败原因为 UPDATE_CHECKSUM_MISMATCH', st.error === 'UPDATE_CHECKSUM_MISMATCH', st.error);
  await w3.locator('.sg-dsk-update').getByText('更新失败').click();
  step('点击“更新失败”说明安装包校验未通过', await w3.getByText('安装包校验未通过').isVisible());
  await w3.getByRole('button', { name: '关闭' }).click();
  const install = await w3.evaluate(() => window.sageDesktop.installUpdate().then(() => 'ok', (e) => String(e.message)));
  step('未下载完成时拒绝安装', install === 'UPDATE_NOT_READY', install);
} catch (e) {
  step('第三次启动流程异常', false, e.message);
  if (process.env.FAIL_SHOT) await w3.screenshot({ path: process.env.FAIL_SHOT }).catch(() => undefined);
} finally {
  await d3.close();
}

step('三次启动均无页面错误', errors.length === 0, errors.join(' | '));
console.log(JSON.stringify({ passed: results.filter((r) => r.ok).length, total: results.length, results }, null, 1));
process.exit(results.every((r) => r.ok) ? 0 : 1);
