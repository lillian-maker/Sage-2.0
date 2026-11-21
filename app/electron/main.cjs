const { app, BrowserWindow, dialog, ipcMain, nativeImage, net, session, shell } = require('electron');
const { execFile } = require('node:child_process');
const os = require('node:os');
const path = require('node:path');
const { DesktopStore, DesktopError, assertWorkspaceId } = require('./desktop-store.cjs');
const { Indexer } = require('./desktop-index.cjs');
const { Updater, allowedUrl } = require('./desktop-update.cjs');

// The renderer calls /api on the same origin, so Electron loads the Vite dev/preview server
// (which proxies /api to the backend) instead of file://, where relative /api requests cannot resolve.
const APP_URL = process.env.SAGE_APP_URL || 'http://localhost:5173';
const APP_ORIGIN = new URL(APP_URL).origin;
const MOBILE_APP_URL = process.env.SAGE_MOBILE_APP_URL && allowedUrl(process.env.SAGE_MOBILE_APP_URL) ? process.env.SAGE_MOBILE_APP_URL : null;
const UPDATE_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;

// `electron electron/main.cjs` (instead of `electron .`) makes getVersion() report Electron's own version.
function appVersion() {
  const v = app.getVersion();
  if (v !== process.versions.electron) return v;
  try {
    return require('../package.json').version || v;
  } catch {
    return v;
  }
}

// Build number and release date are written by the release step; a missing file only blanks those fields.
function releaseInfo() {
  try {
    const r = require('./release.json');
    return { build: typeof r.build === 'string' ? r.build : null, releasedAt: typeof r.releasedAt === 'string' ? r.releasedAt : null };
  } catch {
    return { build: null, releasedAt: null };
  }
}

const OS_NAMES = { darwin: 'macOS', win32: 'Windows', linux: 'Linux' };
// Header values must stay printable ASCII without ';' (the service splits "<device>; <version>" on it).
const DEVICE_FALLBACK = { darwin: 'Mac', win32: 'Windows PC', linux: 'Linux PC' };
const SAFE_DEVICE = /^[\x20-\x3a\x3c-\x7e]{1,60}$/;

/** Marketing model name such as “MacBook Pro”, shown in 设置 · 安全 → 已登录的设备. */
function readDeviceName() {
  const fallback = DEVICE_FALLBACK[process.platform] || 'Computer';
  if (process.platform !== 'darwin') return Promise.resolve(fallback);
  return new Promise((resolve) => {
    execFile('/usr/sbin/system_profiler', ['SPHardwareDataType', '-json'], { timeout: 3000 }, (err, stdout) => {
      if (err) return resolve(fallback);
      try {
        const name = JSON.parse(stdout).SPHardwareDataType[0].machine_name;
        resolve(typeof name === 'string' && SAFE_DEVICE.test(name.trim()) ? name.trim() : fallback);
      } catch {
        resolve(fallback);
      }
    });
  });
}

// App icon follows the chosen theme style (732:17 / 732:297); icons/<style>.png are exported from the design board.
// The installer's own icon stays the business_steady one, which is also what shows before the renderer reports a style.
const ICON_STYLES = ['business_steady', 'neo_electric', 'fashion_editorial', 'comfort_readable', 'minimal_focus'];
const DEFAULT_ICON_STYLE = 'business_steady';
let appIconStyle = null;

function setAppIcon(style) {
  if (!ICON_STYLES.includes(style)) throw new DesktopError('INVALID_ICON_STYLE');
  const image = nativeImage.createFromPath(path.join(__dirname, 'icons', style + '.png'));
  if (image.isEmpty()) throw new DesktopError('ICON_UNAVAILABLE');
  if (process.platform === 'darwin' && app.dock) app.dock.setIcon(image);
  else for (const w of BrowserWindow.getAllWindows()) if (!w.isDestroyed()) w.setIcon(image);
  appIconStyle = style;
  return style;
}

let deviceName = DEVICE_FALLBACK[process.platform] || 'Computer';
const connection = { state: 'unknown', latencyMs: null, lastOkAt: null, checkedAt: null, error: null };

let connectionCheck = null;
/** Reachability of the Sage service from the main process (帮助与诊断 → 连接状态); concurrent callers share one probe. */
function checkConnection() {
  if (!connectionCheck) connectionCheck = probeConnection().finally(() => (connectionCheck = null));
  return connectionCheck;
}

async function probeConnection() {
  const started = Date.now();
  try {
    const res = await net.fetch(APP_ORIGIN + '/api/auth/session', { method: 'GET', cache: 'no-store' });
    if (!res.ok) throw new DesktopError('HTTP_' + res.status);
    await res.arrayBuffer();
    const now = new Date();
    Object.assign(connection, { state: 'connected', latencyMs: Date.now() - started, lastOkAt: now.toISOString(), checkedAt: now.toISOString(), error: null });
  } catch (err) {
    Object.assign(connection, {
      state: 'disconnected',
      latencyMs: null,
      checkedAt: new Date().toISOString(),
      error: err instanceof DesktopError ? err.code : 'NETWORK_ERROR',
    });
  }
  return { ...connection };
}

// Isolated profiles (e2e runs, a second local install) point userData elsewhere before anything reads it.
if (process.env.SAGE_DESKTOP_USER_DATA) app.setPath('userData', path.resolve(process.env.SAGE_DESKTOP_USER_DATA));

let store;
let indexer;
let updater;

function broadcast(channel, payload) {
  for (const w of BrowserWindow.getAllWindows()) {
    if (!w.isDestroyed() && w.webContents.getURL().startsWith(APP_ORIGIN)) w.webContents.send('sage-desktop:' + channel, payload);
  }
}

function trusted(event) {
  try {
    return !!event.senderFrame && new URL(event.senderFrame.url).origin === APP_ORIGIN;
  } catch {
    return false;
  }
}

function handle(channel, fn) {
  ipcMain.handle('sage-desktop:' + channel, async (event, ...args) => {
    if (!trusted(event)) return { ok: false, error: 'FORBIDDEN_ORIGIN' };
    try {
      return { ok: true, data: await fn(event, ...args) };
    } catch (err) {
      return { ok: false, error: err instanceof DesktopError ? err.code : 'DESKTOP_ERROR' };
    }
  });
}

function foldersFor(target) {
  if (target && typeof target === 'object' && typeof target.folderId === 'string') return [store.folder(target.folderId).id];
  if (target && typeof target === 'object' && typeof target.workspaceId === 'string') {
    assertWorkspaceId(target.workspaceId);
    return store.data.folders.filter((f) => f.workspaceId === target.workspaceId).map((f) => f.id);
  }
  throw new DesktopError('INVALID_TARGET');
}

function registerIpc() {
  handle('info', () => ({
    version: appVersion(),
    ...releaseInfo(),
    deviceName,
    osName: OS_NAMES[process.platform] || process.platform,
    platform: process.platform,
    arch: process.arch,
    osVersion: typeof process.getSystemVersion === 'function' ? process.getSystemVersion() : os.release(),
    electron: process.versions.electron,
    mobileAppUrl: MOBILE_APP_URL,
    updateFeedConfigured: updater.feedConfigured,
    appIconStyle,
  }));

  handle('app-icon:set', (_e, style) => (style === appIconStyle ? style : setAppIcon(style)));

  handle('folders:list', (_e, workspaceId) => store.list(workspaceId));

  // Directories become readable only after the user picks them in the native dialog.
  handle('folders:pick', async (event, workspaceId) => {
    assertWorkspaceId(workspaceId);
    const win = BrowserWindow.fromWebContents(event.sender);
    const r = await dialog.showOpenDialog(win, { title: '添加本机文件夹', buttonLabel: '添加', properties: ['openDirectory', 'createDirectory'] });
    if (r.canceled || !r.filePaths || !r.filePaths[0]) return null;
    const { folder, created } = await store.add(workspaceId, r.filePaths[0]);
    if (created) void indexer.start(folder.id);
    return folder;
  });

  handle('folders:remove', (_e, folderId) => {
    indexer.cancel(folderId);
    store.remove(folderId);
    return true;
  });

  handle('folders:primary', (_e, folderId) => {
    store.setPrimary(folderId);
    return true;
  });

  handle('folders:open', async (_e, folderId) => {
    const { target } = await store.resolve(folderId, '');
    const err = await shell.openPath(target);
    if (err) throw new DesktopError('FOLDER_UNAVAILABLE');
    return true;
  });

  handle('folders:readdir', (_e, folderId, relPath) => store.readDir(folderId, relPath));

  handle('index:start', async (_e, target) => {
    const ids = foldersFor(target);
    if (ids.length === 0) return [];
    // Folders of one workspace are indexed one after another so a large folder does not starve the disk.
    const [first, ...rest] = ids;
    void rest.reduce((p, id) => p.then(() => indexer.start(id)), indexer.start(first));
    return store.list(store.folder(first).workspaceId);
  });

  handle('index:cancel', (_e, target) => foldersFor(target).map((id) => indexer.cancel(id)).some(Boolean));

  handle('connection:check', () => checkConnection());
  handle('update:check', () => updater.check());
  handle('update:state', () => ({ ...updater.state }));
  handle('update:download', async () => {
    try {
      return await updater.download();
    } catch (err) {
      throw new DesktopError(err.code || 'UPDATE_FAILED');
    }
  });
  handle('update:install', () => {
    try {
      return updater.install();
    } catch (err) {
      throw new DesktopError(err.code || 'UPDATE_FAILED');
    }
  });

  handle('open-external', async (_e, url) => {
    let u;
    try {
      u = new URL(String(url));
    } catch {
      throw new DesktopError('INVALID_URL');
    }
    if (u.protocol !== 'https:' && u.protocol !== 'mailto:') throw new DesktopError('INVALID_URL');
    await shell.openExternal(u.toString());
    return true;
  });
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 800,
    minHeight: 600,
    titleBarStyle: 'hiddenInset',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https:\/\//.test(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith(APP_ORIGIN + '/') && url !== APP_ORIGIN) {
      e.preventDefault();
      if (/^https:\/\//.test(url)) void shell.openExternal(url);
    }
  });
  win.loadURL(APP_URL);
}

app.whenReady().then(async () => {
  deviceName = await readDeviceName();
  // The service labels this login with the device and client version (X-Sage-Client: "<device>; <version>").
  session.defaultSession.webRequest.onBeforeSendHeaders({ urls: [APP_ORIGIN + '/api/*'] }, (details, callback) => {
    callback({ requestHeaders: { ...details.requestHeaders, 'X-Sage-Client': `${deviceName}; ${appVersion()}` } });
  });
  store = new DesktopStore(app.getPath('userData'));
  indexer = new Indexer(store, (p) => broadcast('index:progress', p));
  updater = new Updater({
    currentVersion: appVersion(),
    feedUrl: process.env.SAGE_UPDATE_FEED_URL || '',
    userDataDir: app.getPath('userData'),
    isPackaged: app.isPackaged,
    broadcast: (s) => broadcast('update:state', s),
  });
  registerIpc();
  try {
    setAppIcon(DEFAULT_ICON_STYLE);
  } catch {
    /* a missing icon file keeps Electron's default icon */
  }
  createWindow();
  if (updater.feedConfigured) {
    setTimeout(() => void updater.check(), 3000);
    setInterval(() => void updater.check(), UPDATE_CHECK_INTERVAL_MS).unref();
  }
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('before-quit', () => indexer && indexer.cancelAll());

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
