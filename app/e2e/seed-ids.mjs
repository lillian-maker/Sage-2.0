// Resolve seeded object ids at runtime (seed ids are random ULIDs per database).
import { readFileSync } from 'node:fs';

export const COOKIE_FILE = process.env.SAGE_SESSION_FILE || new URL('../../tmp/dev-session.txt', import.meta.url).pathname;

export function cookie() {
  return readFileSync(COOKIE_FILE, 'utf8').trim();
}

export async function apiGet(base, path) {
  const r = await fetch(base + '/api/app/' + path, { headers: { cookie: cookie() } });
  if (!r.ok) throw new Error(`api ${path} -> ${r.status}`);
  return r.json();
}

export async function seedIds(base) {
  const [tasks, kbs, exts, autos, sources] = await Promise.all([
    apiGet(base, 'tasks'), apiGet(base, 'knowledge-bases'), apiGet(base, 'extensions'),
    apiGet(base, 'automations'), apiGet(base, 'inbox/sources'),
  ]);
  const task = (title) => {
    const t = tasks.items.find((x) => x.title.includes(title));
    if (!t) throw new Error('seed task not found: ' + title);
    return t.id;
  };
  const richTask = task('德国站竞品格局调研');
  const acceptedTask = task('物流商 11 月报价确认');
  const kb = (kbs.items.find((x) => x.name.includes('法规')) || kbs.items[0]).id;
  const docs = (await apiGet(base, 'documents')).items;
  const doc = docs.find((d) => (d.title || d.name || '').includes('EPR')) || docs[0];
  const conn = (exts.items.find((x) => x.name === 'Jungle Scout') || {}).id;
  if (!conn) throw new Error('seed extension Jungle Scout not found');
  return {
    richTask, acceptedTask, kb, doc: doc.id, conn,
    automation: autos.items[0].id,
    source: sources.items[0].id,
  };
}
