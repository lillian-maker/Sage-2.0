import { useMemo } from 'react';
import { galleryEntries, type GalleryEntry } from './galleryData';
import { renderers } from './renderers';
import './gallery.css';

function Item({ entry }: { entry: GalleryEntry }) {
  const render = entry.code ? renderers[entry.code] : undefined;
  return (
    <figure className="sg-gallery__cell">
      <div
        className="sg-gallery__item"
        data-figma-key={entry.key}
        data-code={entry.code ?? 'excluded'}
        {...(entry.theme ? { 'data-theme': entry.theme } : {})}
        {...(entry.appearance ? { 'data-appearance': entry.appearance } : {})}
        style={{ width: entry.w, height: entry.h }}
      >
        {render ? render(entry.props, entry.key) : <span className="sg-gallery__excluded">注释卡片（不实现）</span>}
      </div>
      <figcaption>
        {entry.figma}
        <span>
          {entry.key} · {entry.code ?? '排除'} · {entry.w}×{entry.h}
          {entry.theme && ` · ${entry.theme}`}
          {entry.appearance && ` · ${entry.appearance}`}
        </span>
      </figcaption>
    </figure>
  );
}

/** /__components: every component-map.csv row rendered at its Figma master size. ?code=Button or ?key=3374:1 filters. */
export default function ComponentGallery() {
  const params = new URLSearchParams(window.location.search);
  const code = params.get('code');
  const keys = params.getAll('key');
  const groups = useMemo(() => {
    const list = galleryEntries.filter((e) => (!code || e.code === code) && (keys.length === 0 || keys.includes(e.key)));
    const map = new Map<string, GalleryEntry[]>();
    for (const e of list) {
      const g = e.code ?? '排除';
      map.set(g, [...(map.get(g) ?? []), e]);
    }
    return [...map.entries()];
  }, [code, keys.join(',')]);
  return (
    <div className="sg-gallery">
      <h1>组件陈列 · {galleryEntries.length} 行</h1>
      {groups.map(([name, entries]) => (
        <section key={name}>
          <h2>
            {name} <span>{entries.length}</span>
          </h2>
          <div className="sg-gallery__grid">
            {entries.map((e) => (
              <Item key={e.key} entry={e} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
