'use strict';
// Local folder index built with the Node standard library: walk, stat and sha1 each file.
// Re-runs are incremental (files whose size and mtime are unchanged keep their previous hash) and cancellable.
const fs = require('node:fs');
const fsp = fs.promises;
const path = require('node:path');
const crypto = require('node:crypto');

const MAX_FILES = 200000;
const MAX_HASH_BYTES = 256 * 1024 * 1024;
const PROGRESS_INTERVAL_MS = 80;
const MAX_ERRORS = 20;
// Containers whose first bytes must match before a file can be parsed for the knowledge index.
const SIGNATURES = {
  pdf: Buffer.from('%PDF-'),
  docx: Buffer.from('PK\x03\x04', 'latin1'),
  xlsx: Buffer.from('PK\x03\x04', 'latin1'),
  pptx: Buffer.from('PK\x03\x04', 'latin1'),
};

class Cancelled extends Error {
  constructor() {
    super('INDEX_CANCELLED');
    this.code = 'INDEX_CANCELLED';
  }
}

function extOf(name) {
  const e = path.extname(name).slice(1).toLowerCase();
  return e || '无扩展名';
}

async function walk(root, signal, onFile) {
  const stack = [''];
  let skipped = 0;
  let count = 0;
  while (stack.length) {
    const rel = stack.pop();
    let dir;
    try {
      dir = await fsp.opendir(rel ? path.join(root, rel) : root);
    } catch (err) {
      if (!rel) throw err;
      skipped++;
      continue;
    }
    for await (const ent of dir) {
      if (signal.aborted) {
        await dir.close().catch(() => undefined);
        throw new Cancelled();
      }
      // Hidden entries (.git, .DS_Store) and symlinks are skipped so the index never leaves the chosen folder.
      if (ent.name.startsWith('.') || ent.isSymbolicLink()) continue;
      const childRel = rel ? rel + '/' + ent.name : ent.name;
      if (ent.isDirectory()) stack.push(childRel);
      else if (ent.isFile()) {
        let st;
        try {
          st = await fsp.stat(path.join(root, childRel));
        } catch {
          skipped++;
          continue;
        }
        onFile({ rel: childRel, size: st.size, mtimeMs: Math.floor(st.mtimeMs) });
        if (++count >= MAX_FILES) return { skipped, truncated: true };
      }
    }
  }
  return { skipped, truncated: false };
}

/** sha1 of the file plus its first bytes, which are checked against the expected container signature. */
function hashFile(file, signal) {
  return new Promise((resolve, reject) => {
    const h = crypto.createHash('sha1');
    const s = fs.createReadStream(file);
    let head = Buffer.alloc(0);
    s.on('data', (chunk) => {
      if (signal.aborted) {
        s.destroy();
        reject(new Cancelled());
        return;
      }
      if (head.length < 8) head = Buffer.concat([head, chunk.subarray(0, 8 - head.length)]);
      h.update(chunk);
    });
    s.on('error', reject);
    s.on('end', () => resolve({ hash: h.digest('hex'), head }));
  });
}

function parseError(rel, head) {
  const sig = SIGNATURES[extOf(rel)];
  return sig && !head.subarray(0, sig.length).equals(sig) ? 'KB_INDEX_PARSE_FAILED' : null;
}

/**
 * @param {object} opts { folder, previous: {rel: [size, mtimeMs, hash]}, previousSummary, signal, onProgress }
 * @returns {Promise<{summary, entries}>}
 */
async function buildIndex({ folder, previous, previousSummary, signal, onProgress }) {
  const startedAt = new Date().toISOString();
  const base = {
    state: 'indexing',
    phase: 'scan',
    done: 0,
    total: 0,
    percent: 0,
    files: 0,
    bytes: 0,
    startedAt,
    finishedAt: null,
    lastSuccessAt: previousSummary ? previousSummary.lastSuccessAt || null : null,
    error: null,
  };
  let last = 0;
  const emit = (patch, force) => {
    Object.assign(base, patch);
    const now = Date.now();
    if (force || now - last >= PROGRESS_INTERVAL_MS) {
      last = now;
      onProgress({ ...base });
    }
  };
  emit({}, true);

  const files = [];
  const walked = await walk(folder.path, signal, (f) => {
    files.push(f);
    emit({ files: files.length, bytes: base.bytes + f.size });
  });
  const total = files.length;
  emit({ phase: 'hash', total, files: total }, true);

  const entries = {};
  const types = {};
  let added = 0;
  let changed = 0;
  let unchanged = 0;
  let bytes = 0;
  let failed = 0;
  const errors = [];
  for (let i = 0; i < total; i++) {
    if (signal.aborted) throw new Cancelled();
    const f = files[i];
    const prev = previous[f.rel];
    let hash = null;
    let code = null;
    if (prev && prev[0] === f.size && prev[1] === f.mtimeMs) {
      hash = prev[2];
      code = prev[3] || null;
      unchanged++;
    } else {
      if (f.size <= MAX_HASH_BYTES) {
        try {
          const r = await hashFile(path.join(folder.path, f.rel), signal);
          hash = r.hash;
          code = parseError(f.rel, r.head);
        } catch (err) {
          if (err instanceof Cancelled) throw err;
          code = 'KB_INDEX_READ_FAILED';
        }
      }
      if (prev) changed++;
      else added++;
    }
    if (code) {
      failed++;
      if (errors.length < MAX_ERRORS) errors.push({ code, file: path.basename(f.rel), rel: f.rel, at: new Date().toISOString() });
    }
    entries[f.rel] = code ? [f.size, f.mtimeMs, hash, code] : [f.size, f.mtimeMs, hash];
    const t = extOf(f.rel);
    types[t] = (types[t] || 0) + 1;
    bytes += f.size;
    emit({ done: i + 1, percent: Math.floor(((i + 1) / Math.max(total, 1)) * 100), bytes });
  }
  const removed = Object.keys(previous).filter((rel) => !(rel in entries)).length;
  const finishedAt = new Date().toISOString();
  const summary = {
    state: 'done',
    phase: null,
    done: total,
    total,
    percent: 100,
    files: total,
    bytes,
    types: Object.entries(types).sort((a, b) => b[1] - a[1]).slice(0, 8),
    added,
    changed,
    removed,
    unchanged,
    failed,
    errors,
    skipped: walked.skipped,
    truncated: walked.truncated,
    startedAt,
    finishedAt,
    lastSuccessAt: finishedAt,
    error: null,
  };
  return { summary, entries };
}

class Indexer {
  constructor(store, broadcast) {
    this.store = store;
    this.broadcast = broadcast;
    this.jobs = new Map();
  }

  isRunning(id) {
    return this.jobs.has(id);
  }

  start(id) {
    const folder = this.store.folder(id);
    if (this.jobs.has(id)) return this.jobs.get(id).promise;
    const controller = new AbortController();
    const prevSummary = this.store.data.index[id] || null;
    const send = (summary) => {
      if (summary.state === 'indexing') this.store.live.set(id, summary);
      else this.store.live.delete(id);
      this.broadcast({ folderId: id, workspaceId: folder.workspaceId, ...summary });
    };
    const promise = (async () => {
      try {
        const { summary, entries } = await buildIndex({
          folder,
          previous: this.store.readEntries(id),
          previousSummary: prevSummary,
          signal: controller.signal,
          onProgress: send,
        });
        if (this.store.data.folders.some((f) => f.id === id)) this.store.writeEntries(id, folder.path, entries);
        this.store.setIndex(id, summary);
        send(summary);
        return summary;
      } catch (err) {
        const cancelled = err instanceof Cancelled;
        const summary = {
          ...(prevSummary || { files: 0, bytes: 0, types: [], added: 0, changed: 0, removed: 0, unchanged: 0 }),
          state: cancelled ? 'cancelled' : 'error',
          phase: null,
          error: cancelled ? null : err && (err.code === 'ENOENT' || err.code === 'EACCES' || err.code === 'EPERM' || err.code === 'ENOTDIR') ? 'FOLDER_UNAVAILABLE' : 'INDEX_FAILED',
          finishedAt: new Date().toISOString(),
          lastSuccessAt: prevSummary ? prevSummary.lastSuccessAt || null : null,
        };
        this.store.setIndex(id, summary);
        send(summary);
        return summary;
      } finally {
        this.jobs.delete(id);
        this.store.live.delete(id);
      }
    })();
    this.jobs.set(id, { controller, promise });
    return promise;
  }

  cancel(id) {
    const job = this.jobs.get(id);
    if (job) job.controller.abort();
    return !!job;
  }

  cancelAll() {
    for (const job of this.jobs.values()) job.controller.abort();
  }
}

module.exports = { Indexer, buildIndex };
