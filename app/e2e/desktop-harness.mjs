// Launches the Sage Electron client against a running Vite server with an isolated profile:
// temporary HOME (so folders display as ~/Documents/...), temporary userData, a stubbed folder picker,
// an optional local update feed and an optional fixed main-process clock.
import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const APP = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const REPO = path.resolve(APP, '..');
const ELECTRON = path.join(APP, 'node_modules/electron/dist/Electron.app/Contents/MacOS/Electron');

export const SAMPLE_FOLDERS = {
  德国市场: {
    'EPR 包装法合规清单.md': '# EPR\n包装登记号、年度申报。\n',
    '德国站关税与 VAT.xlsx': 'PK\x03\x04 sample workbook\n',
    // Not a real PDF: the index reports it as KB_INDEX_PARSE_FAILED (帮助与诊断 “1 份失败”).
    'LUCID 操作手册.pdf': 'scanned copy missing, placeholder text\n',
    '法规/WEEE 电子电气.pdf': '%PDF-1.4 sample\n',
    '法规/电池法 BattG.pdf': '%PDF-1.4 sample\n',
  },
  竞品资料: {
    '竞品价格带.csv': 'brand,price\nA,29.9\n',
    '竞品评论摘要.md': '- 插头不兼容\n',
  },
};

function writeTree(root, tree) {
  for (const [rel, body] of Object.entries(tree)) {
    const file = path.join(root, rel);
    mkdirSync(path.dirname(file), { recursive: true });
    writeFileSync(file, body);
  }
}

/**
 * Local electron-updater "generic" release feed: latest-mac.yml (and latest.yml / latest-linux.yml) plus a fake
 * installer zip. The download first sends 36% at once and, after the updater's 1s progress interval, another 1% so
 * the client reports 37%; it then holds until release(). badChecksum publishes a sha512 that does not match.
 */
async function startFeed({ version = '0.2.0', size = 1_000_000, stallAt = 0.37, badChecksum = false } = {}) {
  const payload = Buffer.alloc(size, 7);
  const sha512 = createHash('sha512')
    .update(badChecksum ? Buffer.alloc(size, 8) : payload)
    .digest('base64');
  const file = `Sage-${version}-mac.zip`;
  const yml = [
    `version: ${version}`,
    'files:',
    `  - url: ${file}`,
    `    sha512: ${sha512}`,
    `    size: ${size}`,
    `path: ${file}`,
    `sha512: ${sha512}`,
    `releaseDate: '2026-11-18T08:00:00.000Z'`,
    '',
  ].join('\n');
  const held = [];
  const timers = new Set();
  let released = false;
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://x').pathname;
    if (/^\/latest(-mac|-linux)?\.yml$/.test(pathname)) {
      res.writeHead(200, { 'content-type': 'text/yaml' });
      res.end(yml);
      return;
    }
    if (pathname === `/${file}`) {
      res.writeHead(200, { 'content-type': 'application/zip', 'content-length': String(size) });
      if (released) return res.end(payload);
      const first = Math.floor(size * (stallAt - 0.01));
      const cut = Math.floor(size * stallAt);
      let sent = first;
      res.write(payload.subarray(0, first));
      const t = setTimeout(() => {
        timers.delete(t);
        res.write(payload.subarray(first, cut));
        sent = cut;
      }, 1300);
      timers.add(t);
      held.push(() => {
        clearTimeout(t);
        res.end(payload.subarray(sent));
      });
      return;
    }
    res.writeHead(404).end();
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return {
    url: `http://127.0.0.1:${server.address().port}/`,
    version,
    release() {
      released = true;
      held.splice(0).forEach((fn) => fn());
    },
    close: () =>
      new Promise((r) => {
        timers.forEach(clearTimeout);
        server.closeAllConnections?.();
        server.close(r);
      }),
  };
}
export function playwright() {
  const mod = process.env.PLAYWRIGHT_MODULE;
  if (!mod) throw new Error('PLAYWRIGHT_MODULE is required (path to a playwright package)');
  return require(path.join(mod, 'index.js'));
}

export function readCookie(file = process.env.SAGE_SESSION_FILE || path.join(REPO, 'tmp', 'desktop-session.txt')) {
  const raw = readFileSync(file, 'utf8').trim();
  const eq = raw.indexOf('=');
  return { raw, name: raw.slice(0, eq), value: raw.slice(eq + 1) };
}

export async function api(base, cookie, p) {
  const r = await fetch(`${base}/api/${p}`, { headers: { cookie: cookie.raw } });
  if (!r.ok) throw new Error(`api ${p} -> ${r.status}`);
  return r.json();
}

/**
 * @param {{ base: string, feed?: boolean | { version?: string, badChecksum?: boolean }, clock?: string, mobileUrl?: string | null }} opts
 * clock: local wall time "HH:MM" of today that the main process should see (index timestamps).
 */
export async function launchDesktop({ base, feed = false, clock, mobileUrl = 'https://sagegogo.com/pages/download.html' }) {
  const root = mkdtempSync(path.join(realpathSync(os.tmpdir()), 'sage-dsk-'));
  const home = path.join(root, 'home');
  const docs = path.join(home, 'Documents');
  for (const [name, tree] of Object.entries(SAMPLE_FOLDERS)) writeTree(path.join(docs, name), tree);
  const feedServer = feed ? await startFeed(typeof feed === 'object' ? feed : {}) : null;
  const { _electron } = playwright();
  const env = {
    ...process.env,
    HOME: home,
    SAGE_APP_URL: base,
    SAGE_DESKTOP_USER_DATA: path.join(root, 'user-data'),
    ...(feedServer ? { SAGE_UPDATE_FEED_URL: feedServer.url } : {}),
    ...(mobileUrl ? { SAGE_MOBILE_APP_URL: mobileUrl } : {}),
  };
  // Launched as an app directory so app.getVersion() is package.json's version, which electron-updater compares.
  const app = await _electron.launch({ executablePath: ELECTRON, args: ['.'], cwd: APP, env });
  // Main-process clock: shifted to today HH:MM, optionally frozen so durations are fully simulated.
  const setClock = async (hhmm, { frozen = false } = {}) => {
    const [h, m] = hhmm.split(':').map(Number);
    const t = new Date();
    t.setHours(h, m, 0, 0);
    await app.evaluate((_e, [iso, freeze]) => {
      if (!globalThis.__sageClock) {
        const Real = Date;
        const clock = (globalThis.__sageClock = { Real, offset: 0, frozenAt: null });
        const now = () => (clock.frozenAt ?? Real.now() + clock.offset);
        class FixedDate extends Real {
          constructor(...a) {
            if (a.length === 0) super(now());
            else super(...a);
          }
          static now() {
            return now();
          }
        }
        globalThis.Date = FixedDate;
      }
      const c = globalThis.__sageClock;
      const target = new c.Real(iso).getTime();
      c.offset = target - c.Real.now();
      c.frozenAt = freeze ? target : null;
    }, [t.toISOString(), frozen]);
  };
  if (clock) await setClock(clock);
  // With a frozen clock, each net.fetch from the main process takes exactly `ms` of simulated time.
  const simulateLatency = (ms) =>
    app.evaluate(({ net }, delay) => {
      if (!globalThis.__sageFetch) globalThis.__sageFetch = net.fetch.bind(net);
      net.fetch = async (...a) => {
        const res = await globalThis.__sageFetch(...a);
        const c = globalThis.__sageClock;
        if (c && c.frozenAt !== null) c.frozenAt += delay;
        return res;
      };
    }, ms);
  // The native folder dialog cannot be driven, so picks are answered from a queue.
  await app.evaluate(({ dialog }) => {
    globalThis.__sagePicks = [];
    dialog.showOpenDialog = async () => {
      const p = globalThis.__sagePicks.shift();
      return p ? { canceled: false, filePaths: [p] } : { canceled: true, filePaths: [] };
    };
  });
  const cookie = readCookie();
  await app.context().addCookies([{ name: cookie.name, value: cookie.value, url: base }]);
  const win = await app.firstWindow();
  await win.waitForLoadState('domcontentloaded');
  return {
    app,
    win,
    cookie,
    home,
    docs,
    userData: env.SAGE_DESKTOP_USER_DATA,
    feed: feedServer,
    setClock,
    simulateLatency,
    queuePick: (p) => app.evaluate((_e, x) => globalThis.__sagePicks.push(x), p),
    async close() {
      await app.close().catch(() => undefined);
      await feedServer?.close();
      rmSync(root, { recursive: true, force: true });
    },
  };
}
