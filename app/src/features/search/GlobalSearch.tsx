import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { request } from '../../api/client';
import { BackLink } from '../../components/BackLink/BackLink';
import { Icon } from '../../components/Icon/Icon';
import { NotificationBell } from '../../components/NotificationBell/NotificationBell';
import { relativeTime } from '../notifications/format';
import { SearchResultRow, type SearchResultRowProps } from './SearchResultRow';
import './search.css';

/** Shape actually returned by GET app/search (api/types.ts lags behind the backend). */
interface Hit {
  type: string;
  id: string;
  title: string;
  context?: string | null;
  occurred_at?: string | null;
  snippet?: string | null;
  extra_hits?: number | null;
  knowledge_base_id?: string | null;
}
interface Category {
  key: string;
  state: 'ok' | 'unavailable';
  total: number | null;
  items: Hit[];
}
interface SearchResponse {
  q: string;
  categories: Category[];
  next_offset?: number | null;
}

type Key = 'tasks' | 'workspaces' | 'files' | 'experts' | 'knowledge' | 'extensions' | 'inbox' | 'automations';
const CATEGORIES: Array<{ key: Key; label: string }> = [
  { key: 'tasks', label: '任务' },
  { key: 'workspaces', label: '工作区' },
  { key: 'files', label: '文件与成果' },
  { key: 'experts', label: '专家' },
  { key: 'knowledge', label: '知识资料' },
  { key: 'extensions', label: '扩展' },
  { key: 'inbox', label: '收件' },
  { key: 'automations', label: '自动化' },
];
const LABEL = Object.fromEntries(CATEGORIES.map((c) => [c.key, c.label])) as Record<Key, string>;
const PREVIEW = 3;
const DEBOUNCE_MS = 180;

const KIND: Record<string, SearchResultRowProps['kind']> = {
  task: 'task',
  workspace: 'workspace',
  artifact: 'artifact',
  document: 'document',
  expert: 'expert',
  extension: 'extension',
  inbox_item: 'inbox',
  automation: 'automation',
};

/** Where a hit opens. A document without its knowledge-base id falls back to the knowledge search. */
export function searchTarget(hit: Pick<Hit, 'type' | 'id' | 'title' | 'knowledge_base_id'>): string {
  const id = encodeURIComponent(hit.id);
  switch (hit.type) {
    case 'task':
      return `/tasks/${id}?from=search`;
    case 'workspace':
      return `/workspaces/${id}`;
    case 'artifact':
      return `/artifacts/${id}`;
    case 'expert':
      return `/experts/${id}`;
    case 'extension':
      return `/extensions/${id}`;
    case 'automation':
      return `/automations/${id}/runs`;
    case 'inbox_item':
      return `/inbox?item=${id}`;
    default:
      return hit.type === 'document' && hit.knowledge_base_id
        ? `/knowledge/${encodeURIComponent(hit.knowledge_base_id)}/docs/${id}?from=search`
        : `/knowledge?q=${encodeURIComponent(hit.title)}`;
  }
}

const fetchSearch = (q: string, category: string, offset?: number, signal?: AbortSignal) =>
  request<SearchResponse>('GET', 'app/search', { query: { q, category, ...(offset ? { offset } : {}) }, signal });

export interface GlobalSearchProps {
  /** “返回新任务” / the name of the page the layer was opened from. */
  backLabel: string;
  onClose: () => void;
  onOpen: (to: string) => void;
  onNotifications?: () => void;
  /** Offset inside a scrolled host. */
  top?: number;
}

type Load = { status: 'idle' | 'loading' | 'ready' | 'error'; data?: SearchResponse };

/** The last query and category, so “返回搜索” from an opened hit reopens the same results. */
const lastSearch: { q: string; category: 'all' | Key } = { q: '', category: 'all' };

export function GlobalSearch({ backLabel, onClose, onOpen, onNotifications, top = 0 }: GlobalSearchProps) {
  const [q, setQ] = useState(lastSearch.q);
  const [category, setCategory] = useState<'all' | Key>(lastSearch.category);
  lastSearch.q = q;
  lastSearch.category = category;
  const [overview, setOverview] = useState<Load>({ status: 'idle' });
  const [detail, setDetail] = useState<Load & { more?: boolean }>({ status: 'idle' });
  /** Categories re-tried after “暂时无法搜索”: 'busy' while in flight, 'still' when the retry failed again. */
  const [retry, setRetry] = useState<Record<string, 'busy' | 'still'>>({});
  const [activeIndex, setActiveIndex] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const term = q.trim();

  useEffect(() => inputRef.current?.focus(), []);

  useEffect(() => {
    setRetry({});
    setActiveIndex(0);
    if (!term) {
      setOverview({ status: 'idle' });
      return;
    }
    const ctl = new AbortController();
    setOverview((s) => ({ status: 'loading', ...(s.data ? { data: s.data } : {}) }));
    const t = window.setTimeout(() => {
      fetchSearch(term, 'all', undefined, ctl.signal).then(
        (data) => setOverview({ status: 'ready', data }),
        () => !ctl.signal.aborted && setOverview({ status: 'error' }),
      );
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(t);
      ctl.abort();
    };
  }, [term, attempt]);

  useEffect(() => {
    setActiveIndex(0);
    if (!term || category === 'all') {
      setDetail({ status: 'idle' });
      return;
    }
    const ctl = new AbortController();
    setDetail({ status: 'loading' });
    const t = window.setTimeout(() => {
      fetchSearch(term, category, undefined, ctl.signal).then(
        (data) => setDetail({ status: 'ready', data }),
        () => !ctl.signal.aborted && setDetail({ status: 'error' }),
      );
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(t);
      ctl.abort();
    };
  }, [term, category]);

  const byKey = useMemo(() => {
    const m = new Map<string, Category>();
    overview.data?.categories.forEach((c) => m.set(c.key, c));
    return m;
  }, [overview.data]);

  const retryCategory = useCallback(
    async (key: Key) => {
      setRetry((r) => ({ ...r, [key]: 'busy' }));
      try {
        const res = await fetchSearch(term, key);
        const got = res.categories[0];
        if (!got || got.state !== 'ok') throw new Error('unavailable');
        setOverview((s) =>
          s.data
            ? {
                ...s,
                data: { ...s.data, categories: s.data.categories.map((c) => (c.key === key ? { ...got, items: got.items.slice(0, PREVIEW) } : c)) },
              }
            : s,
        );
        if (category === key) setDetail({ status: 'ready', data: res });
        setRetry((r) => {
          const { [key]: _drop, ...rest } = r;
          return rest;
        });
      } catch {
        setRetry((r) => ({ ...r, [key]: 'still' }));
      }
    },
    [term, category],
  );

  const loadMore = async () => {
    const next = detail.data?.next_offset;
    if (category === 'all' || !next || !detail.data) return;
    setDetail((d) => ({ ...d, more: true }));
    try {
      const res = await fetchSearch(term, category, next);
      setDetail((d) => {
        const prev = d.data?.categories[0];
        const add = res.categories[0];
        if (!d.data || !prev || !add) return { ...d, more: false };
        return { status: 'ready', data: { ...res, categories: [{ ...add, items: [...prev.items, ...add.items] }] } };
      });
    } catch {
      setDetail((d) => ({ ...d, more: false }));
    }
  };

  // Sections to render, plus the flat hit list keyboard navigation walks.
  const sections: Array<{ key: Key; cat: Category | undefined }> =
    category === 'all'
      ? CATEGORIES.map((c) => ({ key: c.key, cat: byKey.get(c.key) })).filter((s) => s.cat && (s.cat.state !== 'ok' || (s.cat.total ?? 0) > 0))
      : [{ key: category, cat: detail.data?.categories[0] ?? (detail.status === 'loading' ? undefined : byKey.get(category)) }];
  const flat = sections.flatMap((s) => (s.cat?.state === 'ok' ? s.cat.items.slice(0, category === 'all' ? PREVIEW : undefined) : []));

  const cycleCategory = (dir: 1 | -1) => {
    const keys: Array<'all' | Key> = ['all', ...CATEGORIES.map((c) => c.key)];
    const i = keys.indexOf(category);
    setCategory(keys[(i + dir + keys.length) % keys.length] ?? 'all');
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.nativeEvent.isComposing) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (flat.length) setActiveIndex((i) => (i + (e.key === 'ArrowDown' ? 1 : flat.length - 1)) % flat.length);
    } else if (e.key === 'Enter') {
      const hit = flat[activeIndex];
      if (hit) {
        e.preventDefault();
        onOpen(searchTarget(hit));
      }
    } else if (e.key === 'Tab' && term) {
      e.preventDefault();
      cycleCategory(e.shiftKey ? -1 : 1);
    }
  };

  const count = (key: 'all' | Key): string => {
    const c = byKey.get(key);
    if (!c) return '';
    if (c.state !== 'ok' || c.total === null) return '—';
    return String(c.total);
  };

  let index = -1;
  const renderHit = (hit: Hit) => {
    index += 1;
    const i = index;
    const when = relativeTime(hit.occurred_at);
    const meta = [hit.context, when].filter(Boolean).join(' · ');
    return (
      <SearchResultRow
        key={`${hit.type}:${hit.id}`}
        kind={KIND[hit.type] ?? 'document'}
        title={hit.title}
        meta={meta}
        highlight={term}
        active={i === activeIndex}
        onHover={() => setActiveIndex(i)}
        onOpen={() => onOpen(searchTarget(hit))}
        {...(hit.snippet ? { snippet: hit.snippet } : {})}
        {...(hit.extra_hits ? { extraHits: hit.extra_hits } : {})}
      />
    );
  };

  let body;
  if (!term) {
    body = <div className="sg-gsearch__note">搜索任务、工作区、文件与成果、专家、知识资料、扩展、收件与自动化</div>;
  } else if (overview.status === 'error' && !overview.data) {
    body = (
      <div className="sg-gsearch__unavail" role="alert">
        <Icon name="info" size={14} />
        暂时无法搜索 ·
        <button type="button" onClick={() => setAttempt((n) => n + 1)}>
          重试
        </button>
      </div>
    );
  } else if (!overview.data || (category !== 'all' && detail.status === 'loading' && !detail.data)) {
    body = <div className="sg-gsearch__note">正在搜索…</div>;
  } else if (sections.length === 0 || (category !== 'all' && sections[0]?.cat?.state === 'ok' && !sections[0].cat.total)) {
    body = <div className="sg-gsearch__note">没有找到与“{term}”相关的{category === 'all' ? '内容' : LABEL[category]}</div>;
  } else {
    body = sections.map(({ key, cat }) => {
      const label = LABEL[key];
      if (!cat) return null;
      if (cat.state !== 'ok') {
        const r = retry[key];
        return (
          <section key={key} aria-label={label}>
            <div className="sg-gsearch__head">{label}</div>
            <div className="sg-gsearch__unavail" role="status">
              <Icon name="info" size={14} />
              <span>{`${label}${r === 'still' ? '仍暂时无法搜索' : '暂时无法搜索'} ·`}</span>
              <button type="button" disabled={r === 'busy'} onClick={() => void retryCategory(key)}>
                {r === 'busy' ? '正在重试…' : '重试'}
              </button>
            </div>
          </section>
        );
      }
      const items = category === 'all' ? cat.items.slice(0, PREVIEW) : cat.items;
      return (
        <section key={key} aria-label={label}>
          <div className="sg-gsearch__head">
            <span>
              {label} · {cat.total ?? items.length}
            </span>
            {category === 'all' && (cat.total ?? 0) > PREVIEW && (
              <button type="button" className="sg-gsearch__all" onClick={() => setCategory(key)}>
                查看全部
                <Icon name="chevron-right" size={12} />
              </button>
            )}
          </div>
          {items.map(renderHit)}
          {category !== 'all' && detail.data?.next_offset && (
            <button type="button" className="sg-gsearch__loadmore" disabled={detail.more} onClick={() => void loadMore()}>
              {detail.more ? '正在加载…' : '加载更多'}
            </button>
          )}
        </section>
      );
    });
  }

  return (
    <div className="sg-gsearch" style={{ top }} role="dialog" aria-modal="true" aria-label="全局搜索" onKeyDown={onKeyDown}>
      <div className="sg-gsearch__top">
        {onNotifications && <NotificationBell onClick={onNotifications} />}
      </div>
      <div className="sg-gsearch__body">
        <BackLink label={backLabel} size={30} onBack={onClose} />
        <div className="sg-gsearch__box">
          <Icon name="search" size={20} />
          <input
            ref={inputRef}
            className="sg-gsearch__input"
            value={q}
            placeholder="搜索任务、文件、专家…"
            aria-label="搜索"
            onChange={(e) => setQ(e.target.value)}
          />
          {q && (
            <button
              type="button"
              className="sg-gsearch__clear"
              aria-label="清除"
              onClick={() => {
                setQ('');
                setCategory('all');
                inputRef.current?.focus();
              }}
            >
              <Icon name="x" size={16} />
            </button>
          )}
          <span className="sg-gsearch__esc">Esc</span>
        </div>
        {term && (
          <div className="sg-gsearch__chips" role="tablist" aria-label="搜索分类">
            {(['all', ...CATEGORIES.map((c) => c.key)] as Array<'all' | Key>).map((key) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={category === key}
                className={`sg-gsearch__chip${category === key ? ' is-active' : ''}`}
                onClick={() => setCategory(key)}
              >
                {key === 'all' ? '全部' : LABEL[key]}
                {count(key) && <b>{count(key)}</b>}
              </button>
            ))}
          </div>
        )}
        {body}
        <div className="sg-gsearch__keys" aria-hidden="true">
          <span>↑↓ 选择</span>
          <span>↵ 打开</span>
          <span>Tab 切换分类</span>
          <span>Esc 返回原页面</span>
        </div>
      </div>
    </div>
  );
}
