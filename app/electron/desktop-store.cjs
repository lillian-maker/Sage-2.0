'use strict';
// Local folder registry and per-folder index state, kept only on this machine (Q-D3 A: paths are never uploaded).
const fs = require('node:fs');
const fsp = fs.promises;
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');

const WORKSPACE_ID = /^[A-Za-z0-9_-]{1,64}$/;
const FOLDER_ID = /^[a-f0-9]{16}$/;

class DesktopError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

function assertWorkspaceId(id) {
  if (typeof id !== 'string' || !WORKSPACE_ID.test(id)) throw new DesktopError('INVALID_WORKSPACE');
}

function assertFolderId(id) {
  if (typeof id !== 'string' || !FOLDER_ID.test(id)) throw new DesktopError('FOLDER_NOT_FOUND');
}

function displayPath(p) {
  const home = os.homedir();
  if (p === home) return '~';
  if (home && p.startsWith(home + path.sep)) return '~/' + p.slice(home.length + 1).split(path.sep).join('/');
  return p;
}

function isInside(root, target) {
  const rel = path.relative(root, target);
  return rel === '' || (!path.isAbsolute(rel) && rel !== '..' && !rel.startsWith('..' + path.sep));
}

class DesktopStore {
  constructor(userDataDir) {
    this.dir = userDataDir;
    this.file = path.join(userDataDir, 'sage-desktop.json');
    this.indexDir = path.join(userDataDir, 'local-index');
    this.data = this.load();
    // Progress of runs in flight; persisted state only changes when a run ends.
    this.live = new Map();
  }

  load() {
    try {
      const raw = JSON.parse(fs.readFileSync(this.file, 'utf8'));
      const folders = Array.isArray(raw.folders) ? raw.folders.filter((f) => f && FOLDER_ID.test(f.id) && typeof f.path === 'string' && path.isAbsolute(f.path)) : [];
      const index = raw.index && typeof raw.index === 'object' ? raw.index : {};
      // A run that was in flight when the app quit cannot resume, so it is reported as interrupted.
      for (const s of Object.values(index)) if (s && s.state === 'indexing') s.state = 'cancelled';
      return { version: 1, folders, index };
    } catch {
      return { version: 1, folders: [], index: {} };
    }
  }

  save() {
    fs.mkdirSync(this.dir, { recursive: true });
    const tmp = this.file + '.' + process.pid + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(this.data, null, 1), { mode: 0o600 });
    fs.renameSync(tmp, this.file);
  }

  folder(id) {
    assertFolderId(id);
    const f = this.data.folders.find((x) => x.id === id);
    if (!f) throw new DesktopError('FOLDER_NOT_FOUND');
    return f;
  }

  async describe(f) {
    let available = true;
    try {
      const st = await fsp.stat(f.path);
      available = st.isDirectory();
      if (available) await fsp.access(f.path, fs.constants.R_OK);
    } catch {
      available = false;
    }
    return {
      id: f.id,
      workspaceId: f.workspaceId,
      path: f.path,
      displayPath: displayPath(f.path),
      name: path.basename(f.path),
      primary: !!f.primary,
      addedAt: f.addedAt,
      available,
      index: this.live.get(f.id) || this.data.index[f.id] || null,
    };
  }

  async list(workspaceId) {
    if (workspaceId !== undefined && workspaceId !== null) assertWorkspaceId(workspaceId);
    const rows = this.data.folders.filter((f) => !workspaceId || f.workspaceId === workspaceId);
    return Promise.all(rows.map((f) => this.describe(f)));
  }

  async add(workspaceId, chosen) {
    assertWorkspaceId(workspaceId);
    if (typeof chosen !== 'string' || !path.isAbsolute(chosen)) throw new DesktopError('INVALID_FOLDER');
    let real;
    try {
      real = await fsp.realpath(chosen);
      if (!(await fsp.stat(real)).isDirectory()) throw new Error('not a directory');
    } catch {
      throw new DesktopError('FOLDER_UNAVAILABLE');
    }
    if (path.parse(real).root === real) throw new DesktopError('FOLDER_TOO_BROAD');
    const existing = this.data.folders.find((f) => f.workspaceId === workspaceId && f.path === real);
    if (existing) return { folder: await this.describe(existing), created: false };
    const hasPrimary = this.data.folders.some((f) => f.workspaceId === workspaceId && f.primary);
    const f = { id: crypto.randomBytes(8).toString('hex'), workspaceId, path: real, primary: !hasPrimary, addedAt: new Date().toISOString() };
    this.data.folders.push(f);
    this.save();
    return { folder: await this.describe(f), created: true };
  }

  remove(id) {
    const f = this.folder(id);
    this.data.folders = this.data.folders.filter((x) => x.id !== id);
    delete this.data.index[id];
    if (f.primary) {
      const next = this.data.folders.find((x) => x.workspaceId === f.workspaceId);
      if (next) next.primary = true;
    }
    this.save();
    fs.rmSync(this.entriesFile(id), { force: true });
  }

  setPrimary(id) {
    const f = this.folder(id);
    for (const x of this.data.folders) if (x.workspaceId === f.workspaceId) x.primary = x.id === id;
    this.save();
  }

  setIndex(id, summary) {
    if (!this.data.folders.some((f) => f.id === id)) return;
    this.data.index[id] = summary;
    this.save();
  }

  entriesFile(id) {
    assertFolderId(id);
    return path.join(this.indexDir, id + '.json');
  }

  readEntries(id) {
    try {
      const raw = JSON.parse(fs.readFileSync(this.entriesFile(id), 'utf8'));
      return raw && raw.entries && typeof raw.entries === 'object' ? raw.entries : {};
    } catch {
      return {};
    }
  }

  writeEntries(id, root, entries) {
    fs.mkdirSync(this.indexDir, { recursive: true });
    const file = this.entriesFile(id);
    const tmp = file + '.' + process.pid + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify({ version: 1, root, entries }), { mode: 0o600 });
    fs.renameSync(tmp, file);
  }

  /** Resolves `rel` inside a registered folder; rejects traversal and symlinks that leave the folder. */
  async resolve(id, rel) {
    const f = this.folder(id);
    if (rel === undefined || rel === null) rel = '';
    if (typeof rel !== 'string' || rel.includes('\0') || path.isAbsolute(rel)) throw new DesktopError('PATH_OUTSIDE_FOLDER');
    const target = path.resolve(f.path, rel);
    if (!isInside(f.path, target)) throw new DesktopError('PATH_OUTSIDE_FOLDER');
    let realRoot;
    let realTarget;
    try {
      realRoot = await fsp.realpath(f.path);
      realTarget = await fsp.realpath(target);
    } catch {
      throw new DesktopError('FOLDER_UNAVAILABLE');
    }
    if (!isInside(realRoot, realTarget)) throw new DesktopError('PATH_OUTSIDE_FOLDER');
    return { folder: f, root: realRoot, target: realTarget, rel: path.relative(realRoot, realTarget).split(path.sep).join('/') };
  }

  async readDir(id, rel, limit = 200) {
    const { target, rel: cleanRel } = await this.resolve(id, rel);
    let dirents;
    try {
      dirents = await fsp.readdir(target, { withFileTypes: true });
    } catch {
      throw new DesktopError('FOLDER_UNAVAILABLE');
    }
    const visible = dirents.filter((d) => !d.name.startsWith('.') && (d.isDirectory() || d.isFile()));
    visible.sort((a, b) => Number(b.isDirectory()) - Number(a.isDirectory()) || a.name.localeCompare(b.name, 'zh-CN'));
    const entries = [];
    for (const d of visible.slice(0, limit)) {
      let size = 0;
      let mtimeMs = 0;
      try {
        const st = await fsp.stat(path.join(target, d.name));
        size = st.isFile() ? st.size : 0;
        mtimeMs = st.mtimeMs;
      } catch {
        continue;
      }
      entries.push({ name: d.name, kind: d.isDirectory() ? 'dir' : 'file', size, mtimeMs });
    }
    return { rel: cleanRel, entries, total: visible.length, truncated: visible.length > limit };
  }
}

module.exports = { DesktopStore, DesktopError, displayPath, isInside, assertWorkspaceId, assertFolderId };
