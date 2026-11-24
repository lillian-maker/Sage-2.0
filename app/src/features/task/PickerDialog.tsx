import { useEffect, useState } from 'react';
import { request } from '../../api/client';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { SearchField } from '../../components/SearchField/SearchField';
import { LoadingState } from '../../components/States/States';
import { scopeShortLine, type KnowledgeBase } from '../knowledge/api';
import './picker.css';
import { Modal } from '../../components/Modal/Modal';

export type PickerKind = 'materials' | 'experts' | 'skills';
export interface PickedItem {
  id: string;
  title: string;
  sub?: string;
  /** Index state of a single material (就绪 / 索引中 …), shown on the composer chip. */
  state?: string;
}

interface Row extends PickedItem {
  side: string;
}

const TITLE: Record<PickerKind, { title: string; search: string; noun: string; empty: string; failed: string }> = {
  materials: { title: '选择资料', search: '搜索资料', noun: '资料', empty: '没有找到匹配的资料', failed: '暂时无法加载资料，请重试' },
  experts: { title: '选择专家', search: '搜索专家', noun: '专家', empty: '没有找到匹配的专家', failed: '暂时无法加载专家，请重试' },
  skills: { title: '选择技能', search: '搜索技能', noun: '技能', empty: '没有找到匹配的技能', failed: '暂时无法加载技能，请重试' },
};

const INDEX: Record<string, string> = { indexed: '就绪', indexing: '索引中', failed: '索引失败', queued: '排队中' };
const INDEX_LABELS = new Set(Object.values(INDEX));

async function load(kind: PickerKind, q: string, signal: AbortSignal): Promise<Row[]> {
  const query = q ? { q } : undefined;
  if (kind === 'materials') {
    const [r, kbs] = await Promise.all([
      request<{ items: Array<{ id: string; title: string; page_count: number | null; index_state: string; uploaded_by: { name: string } | null; source_expert: { name: string } | null }> }>(
        'GET',
        'app/documents',
        { query, signal },
      ),
      request<{ items: KnowledgeBase[] }>('GET', 'app/knowledge-bases', { signal }),
    ]);
    // 整库选择（03）：知识库排在单份资料之前，选中后创建任务时带上 attached_knowledge_base_ids
    const kbRows = kbs.items
      .filter((kb) => !q || kb.name.includes(q))
      .map((kb) => ({
        id: `kb:${kb.id}`,
        title: kb.name,
        sub: `${scopeShortLine(kb)} · ${kb.document_count} 份`,
        side: '可引用',
      }));
    return kbRows.concat(
      r.items.map((d) => ({
        id: d.id,
        title: d.title,
        sub: [d.uploaded_by?.name ?? d.source_expert?.name, d.page_count ? `${d.page_count} 页` : ''].filter(Boolean).join(' · '),
        side: INDEX[d.index_state] ?? '',
      })),
    );
  }
  if (kind === 'experts') {
    const r = await request<{ items: Array<{ id: string; name: string; title: string | null; specialty: string | null; assignable: boolean }> }>('GET', 'app/experts', { query, signal });
    return r.items.filter((e) => e.assignable).map((e) => ({ id: e.id, title: e.name, sub: e.specialty ?? e.title ?? '', side: '专家' }));
  }
  const r = await request<{ items: Array<{ id: string; name: string; description: string | null; install_state: string; enabled: boolean }> }>('GET', 'app/extensions', {
    query: { kind: 'skill', ...(q ? { q } : {}) },
    signal,
  });
  return r.items
    .filter((s) => s.install_state === 'installed' && s.enabled)
    .map((s) => ({ id: s.id, title: s.name, sub: s.description ?? '', side: '技能' }));
}

export interface PickerDialogProps {
  kind: PickerKind;
  selected: PickedItem[];
  onConfirm: (items: PickedItem[]) => void;
  onCancel: () => void;
}

export function PickerDialog({ kind, selected, onConfirm, onCancel }: PickerDialogProps) {
  const [q, setQ] = useState('');
  const [term, setTerm] = useState('');
  const [picked, setPicked] = useState<Map<string, PickedItem>>(() => new Map(selected.map((s) => [s.id, s])));
  useEffect(() => {
    const t = window.setTimeout(() => setTerm(q.trim()), 250);
    return () => window.clearTimeout(t);
  }, [q]);
  const rows = useApiQuery((signal) => load(kind, term, signal), [kind, term]);
  const copy = TITLE[kind];
  const failed = rows.status === 'error';
  // 2520:243: an empty search result offers only 清除搜索; the field's × and 确认 are not drawn.
  const emptySearch = !failed && !!term && !!rows.data && rows.data.length === 0;
  const toggle = (r: Row) =>
    setPicked((p) => {
      const next = new Map(p);
      if (next.has(r.id)) next.delete(r.id);
      else next.set(r.id, { id: r.id, title: r.title, ...(r.sub ? { sub: r.sub } : {}), ...(kind === 'materials' && INDEX_LABELS.has(r.side) ? { state: r.side } : {}) });
      return next;
    });

  return (
    <Modal className="sg-picker" labelledBy="sg-picker-title" onClose={onCancel}>
        <h2 id="sg-picker-title">
          {copy.title}
          {picked.size > 0 && <span className="sg-picker__count">已选 {picked.size}</span>}
        </h2>
        <SearchField size={32} value={q} placeholder={copy.search} onValueChange={setQ} autoFocus clearable={!emptySearch} />
        <div className="sg-picker__list">
          {failed ? (
            <>
              <p className="sg-picker__msg">{copy.failed}</p>
              <button type="button" className="sg-picker__link" onClick={rows.reload}>
                重试
              </button>
            </>
          ) : rows.status === 'loading' && !rows.data ? (
            <LoadingState rows={2} />
          ) : rows.data && rows.data.length === 0 ? (
            term ? (
              <>
                <p className="sg-picker__msg">{copy.empty}</p>
                <button type="button" className="sg-picker__link" onClick={() => setQ('')}>
                  清除搜索
                </button>
              </>
            ) : (
              <p className="sg-picker__msg">{`还没有可选的${copy.noun}`}</p>
            )
          ) : (
            <>
              {rows.data?.map((r) => {
                const on = picked.has(r.id);
                return (
                  <button key={r.id} type="button" role="checkbox" aria-checked={on} className="sg-picker__row" onClick={() => toggle(r)}>
                    <span className={`sg-picker__box${on ? ' is-checked' : ''}`}>{on && <Icon name="check" size={12} />}</span>
                    <span className="sg-picker__text">
                      <span className="sg-picker__title truncate">{r.title}</span>
                      {r.sub && <span className="sg-picker__sub truncate">{r.sub}</span>}
                    </span>
                    <span className="sg-picker__side">{r.side}</span>
                  </button>
                );
              })}
              <button type="button" className="sg-picker__link" onClick={rows.reload}>
                刷新
              </button>
            </>
          )}
        </div>
        <div className="sg-picker__footer">
          <Button variant="secondary" size={32} label="取消" onClick={onCancel} />
          {!failed && !(emptySearch && picked.size === 0) && <Button variant="primary" size={32} label="确认" disabled={rows.status === 'loading' && !rows.data} onClick={() => onConfirm([...picked.values()])} />}
        </div>
    </Modal>
  );
}
