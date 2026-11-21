'use strict';
// Client updates through electron-updater's generic provider. The release feed (SAGE_UPDATE_FEED_URL) is the
// directory holding latest-mac.yml / latest.yml and the installers; electron-updater verifies each download
// against the sha512 in that file. Without a feed electron-updater is never loaded and the client reports that it
// is on the latest version it knows about.
const fs = require('node:fs');
const path = require('node:path');

function allowedUrl(raw) {
  let u;
  try {
    u = new URL(raw);
  } catch {
    return null;
  }
  if (u.protocol === 'https:') return u;
  // Plain http is accepted only for a feed served from this machine (local release testing).
  if (u.protocol === 'http:' && (u.hostname === '127.0.0.1' || u.hostname === 'localhost')) return u;
  return null;
}

/** electron-updater / builder-util-runtime error → stable code shown by the renderer. */
function errorCode(err) {
  const code = (err && err.code) || '';
  const msg = String((err && err.message) || err || '');
  if (code === 'ERR_CHECKSUM_MISMATCH' || /checksum mismatch/i.test(msg)) return 'UPDATE_CHECKSUM_MISMATCH';
  if (/^ERR_UPDATER_(INVALID_UPDATE_INFO|ZIP_FILE_NOT_FOUND|NO_FILES_PROVIDED|INVALID_VERSION|LATEST_VERSION_NOT_FOUND|CHANNEL_FILE_NOT_FOUND)$/.test(code) || err?.name === 'YAMLException') {
    return 'UPDATE_FEED_INVALID';
  }
  if (/timed? ?out|ETIMEDOUT|ERR_TIMED_OUT/i.test(msg)) return 'UPDATE_TIMEOUT';
  if (err && (err.statusCode || /net::ERR_|ECONNREFUSED|ECONNRESET|ENOTFOUND|HttpError/.test(msg))) return 'UPDATE_HTTP_ERROR';
  if (/code signature|signature/i.test(msg)) return 'UPDATE_SIGNATURE_INVALID';
  return 'UPDATE_FAILED';
}

class Updater {
  /**
   * @param {{ currentVersion: string, feedUrl: string, userDataDir: string, broadcast: (s: object) => void, isPackaged: boolean }} opts
   */
  constructor({ currentVersion, feedUrl, userDataDir, broadcast, isPackaged }) {
    this.feed = feedUrl ? allowedUrl(feedUrl) : null;
    this.feedConfigured = !!feedUrl;
    this.userDataDir = userDataDir;
    this.isPackaged = isPackaged;
    this.broadcast = broadcast;
    this.client = null;
    this.running = null;
    this.available = null;
    this.state = { state: 'idle', currentVersion, latestVersion: null, percent: 0, error: null, checkedAt: null, feedConfigured: this.feedConfigured };
  }

  set(patch) {
    this.state = { ...this.state, ...patch };
    this.broadcast({ ...this.state });
  }

  updater() {
    if (this.client) return this.client;
    const { autoUpdater } = require('electron-updater');
    // The feed comes from the environment rather than a packaged app-update.yml; electron-updater still reads its
    // cache directory name from a config file, so the same settings are written to userData.
    const base = this.feed.toString().replace(/\/?$/, '/');
    const configPath = path.join(this.userDataDir, 'sage-update.yml');
    fs.mkdirSync(this.userDataDir, { recursive: true });
    fs.writeFileSync(configPath, `provider: generic\nurl: ${JSON.stringify(base)}\nupdaterCacheDirName: sage-updater\n`, { mode: 0o600 });
    autoUpdater.updateConfigPath = configPath;
    autoUpdater.setFeedURL({ provider: 'generic', url: base });
    autoUpdater.autoDownload = false;
    autoUpdater.autoInstallOnAppQuit = false;
    autoUpdater.allowDowngrade = false;
    // An unpackaged build (npm run electron) has no app-update.yml; the explicit feed above is used instead.
    autoUpdater.forceDevUpdateConfig = !this.isPackaged;
    autoUpdater.logger = { info() {}, debug() {}, warn: (m) => console.warn('[update]', m), error: (m) => console.warn('[update]', m) };
    autoUpdater.on('checking-for-update', () => this.set({ state: 'checking', error: null }));
    autoUpdater.on('update-not-available', (info) => this.set({ state: 'latest', latestVersion: info?.version ?? this.state.currentVersion, checkedAt: new Date().toISOString() }));
    autoUpdater.on('update-available', (info) => {
      this.available = info;
      this.set({ state: 'available', latestVersion: info.version, percent: 0, checkedAt: new Date().toISOString() });
    });
    autoUpdater.on('download-progress', (p) => this.set({ state: 'downloading', percent: Math.min(99, Math.floor(p.percent || 0)) }));
    autoUpdater.on('update-downloaded', (info) => this.set({ state: 'ready', percent: 100, latestVersion: info?.version ?? this.state.latestVersion }));
    autoUpdater.on('error', (err) => this.set({ state: 'failed', error: errorCode(err), checkedAt: new Date().toISOString() }));
    this.client = autoUpdater;
    return autoUpdater;
  }

  check() {
    if (this.running) return this.running;
    if (['ready', 'downloading', 'available'].includes(this.state.state)) return Promise.resolve({ ...this.state });
    this.running = this.run().finally(() => {
      this.running = null;
    });
    return this.running;
  }

  async run() {
    const checkedAt = new Date().toISOString();
    if (!this.feedConfigured) {
      this.set({ state: 'latest', latestVersion: this.state.currentVersion, error: null, checkedAt });
      return { ...this.state };
    }
    if (!this.feed) {
      this.set({ state: 'failed', error: 'UPDATE_URL_REJECTED', checkedAt });
      return { ...this.state };
    }
    try {
      const r = await this.updater().checkForUpdates();
      if (!r) this.set({ state: 'failed', error: 'UPDATE_FAILED', checkedAt });
    } catch (err) {
      this.set({ state: 'failed', error: errorCode(err), checkedAt });
    }
    return { ...this.state };
  }

  /** Started by the renderer once a newer version is reported (autoDownload is off). */
  async download() {
    if (this.state.state === 'downloading' || this.state.state === 'ready') return { ...this.state };
    if (this.state.state !== 'available' || !this.available || !this.client) throw Object.assign(new Error('UPDATE_NOT_AVAILABLE'), { code: 'UPDATE_NOT_AVAILABLE' });
    const base = this.feed.toString().replace(/\/?$/, '/');
    const files = Array.isArray(this.available.files) ? this.available.files : [];
    // Installer URLs listed in the feed may be absolute; they must meet the same rule as the feed itself.
    if (!files.length || files.some((f) => !f || typeof f.url !== 'string' || !allowedUrl(new URL(f.url, base).toString()))) {
      this.set({ state: 'failed', error: 'UPDATE_URL_REJECTED' });
      return { ...this.state };
    }
    this.set({ state: 'downloading', percent: 0, error: null });
    this.client.downloadUpdate().catch((err) => this.set({ state: 'failed', error: errorCode(err) }));
    return { ...this.state };
  }

  install() {
    if (this.state.state !== 'ready' || !this.client) throw Object.assign(new Error('UPDATE_NOT_READY'), { code: 'UPDATE_NOT_READY' });
    // Restarts into the new version; on macOS this goes through Squirrel.Mac, which checks the app signature.
    setImmediate(() => this.client.quitAndInstall(false, true));
    return true;
  }
}

module.exports = { Updater, allowedUrl, errorCode };
