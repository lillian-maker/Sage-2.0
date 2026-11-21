import { useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { IconTile } from '../../components/IconTile/IconTile';
import { PageHeader } from '../../components/PageHeader/PageHeader';
import { SearchField } from '../../components/SearchField/SearchField';
import { EmptyState, ErrorBanner, errorMessage, QueryGate } from '../../components/States/States';
import { Tab } from '../../components/Tabs/Tab';
import { DPage, MutedLabel } from '../../features/experts/parts';
import { allPages, relativeDay } from '../../features/experts/api';
import { knowledgeApi, ENTITY_TYPES, type EntityItem, type EntityType, type Fact, type Neighborhood } from '../../features/knowledge/api';
import '../../features/experts/frame.css';
import '../../features/knowledge/kb.css';

const EDGE_LABEL: Record<string, string> = { requires: '要求', cites: '引用', belongs_to: '属于', competes: '头部竞品', sells_on: '渠道', plans: '计划进入' };

function typeColor(t: EntityType): string {
  return ENTITY_TYPES.find((x) => x.key === t)?.color ?? 'var(--color-slate-540)';
}

function GraphCanvas({ nb, centerName, onPick }: { nb: Neighborhood; centerName: string; onPick: (id: string) => void }) {
  const others = nb.nodes.filter((n) => !n.is_center);
  // Left column = nodes with an edge into the center, right column = the rest.
  const intoCenter = new Set(nb.edges.filter((e) => e.to === nb.center_id).map((e) => e.from));
  const left = others.filter((n) => intoCenter.has(n.id));
  const right = others.filter((n) => !intoCenter.has(n.id));
  const H = 520;
  const W = 720;
  const pos = new Map<string, { x: number; y: number }>();
  pos.set(nb.center_id, { x: W / 2, y: H / 2 });
  const place = (nodes: typeof others, x: number) =>
    nodes.forEach((n, i) => pos.set(n.id, { x, y: ((i + 1) / (nodes.length + 1)) * H }));
  place(left, W * 0.16);
  place(right, W * 0.84);
  const labelFor = (from: string, to: string) => {
    const e = nb.edges.find((x) => x.from === from && x.to === to);
    return e ? (EDGE_LABEL[e.kind] ?? e.kind) : '';
  };
  return (
    <div className="sg-graph__canvas">
      <svg viewBox={`0 0 ${W} ${H}`} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} aria-hidden="true">
        {others.map((n) => {
          const a = pos.get(nb.center_id)!;
          const b = pos.get(n.id)!;
          const label = intoCenter.has(n.id) ? labelFor(n.id, nb.center_id) : labelFor(nb.center_id, n.id);
          return (
            <g key={n.id}>
              <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="sg-graph__edge" />
              {label && (
                <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 4} className="sg-graph__edgelabel" textAnchor="middle">
                  {label}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {nb.nodes.map((n) => {
        const p = pos.get(n.id);
        if (!p) return null;
        return (
          <button
            key={n.id}
            type="button"
            className={`sg-gnode${n.is_center ? ' is-center' : ''}${n.has_conflict ? ' is-conflict' : ''}`}
            style={{ left: `${(p.x / W) * 100}%`, top: `${(p.y / H) * 100}%` }}
            onClick={() => !n.is_center && onPick(n.id)}
          >
            <span className="sg-gnode__dot" style={{ background: typeColor(n.entity_type) }} />
            {n.name}
            {n.has_conflict && ' · 有冲突'}
          </button>
        );
      })}
      {others.length === 0 && <p className="sg-dmuted" style={{ position: 'absolute', left: '50%', top: '12%', transform: 'translateX(-50%)' }}>“{centerName}” 还没有关联实体</p>}
    </div>
  );
}

function monthOf(d: string | null | undefined): string {
  return d ? d.slice(0, 7) : '日期未知';
}

/** “注册周期有两种说法。较新的来源为…” — the shared statement prefix is the topic, the remainders are the values. */
function conflictSentence(facts: Fact[], preferredId: string | null): string {
  const sorted = [...facts].sort((a, b) => (b.id === preferredId ? 1 : 0) - (a.id === preferredId ? 1 : 0) || (b.source_date ?? '').localeCompare(a.source_date ?? ''));
  const [newer, older] = sorted;
  if (!newer || !older) return facts.map((f) => `“${f.statement}”（${f.source_label ?? '来源未知'} · ${monthOf(f.source_date)}）`).join('');
  let n = 0;
  while (n < newer.statement.length && newer.statement[n] === older.statement[n]) n++;
  const topic = newer.statement.slice(0, n).trim();
  const value = (f: Fact) => (topic ? f.statement.slice(n).trim() : f.statement);
  const src = (f: Fact) => `${f.source_label ?? '来源未知'} · ${monthOf(f.source_date)}`;
  const head = topic ? `${topic}有${facts.length === 2 ? '两' : facts.length}种说法。` : '';
  return `${head}较新的来源为“${value(newer)}”（${src(newer)}），较旧的为“${value(older)}”（${src(older)}）。编排时优先采用较新的来源，并在成果中注明。`;
}

export default function Screen({ query, navigate }: ScreenProps) {
  const type = (query.get('type') ?? '') as EntityType | '';
  const q = query.get('q') ?? '';
  const includeOld = query.get('superseded') === '1';
  const selectedId = query.get('entity');
  const [draft, setDraft] = useState(q);

  const entities = useApiQuery(async (signal) => {
    const items = await allPages<EntityItem>((offset) =>
      knowledgeApi.entities({ type: type || undefined, q: q || undefined, include_superseded: includeOld, offset: offset || undefined }, signal),
    );
    const head = await knowledgeApi.entities({ type: type || undefined, q: q || undefined, include_superseded: includeOld }, signal);
    return { items, type_counts: head.type_counts, total: head.total };
  }, [type, q, includeOld]);

  const selected = selectedId ?? entities.data?.items.find((e) => e.has_conflict)?.id ?? entities.data?.items[0]?.id;
  // The panel always lists superseded facts (greyed) next to current ones; the time filter narrows the entity list only.
  const detail = useApiQuery((signal) => (selected ? knowledgeApi.entity(selected, true, signal) : Promise.resolve(null)), [selected]);
  const neighborhood = useApiQuery((signal) => (selected ? knowledgeApi.neighborhood(selected, signal) : Promise.resolve(null)), [selected]);

  const setParams = (next: Record<string, string | null>) => {
    const p = new URLSearchParams(query);
    for (const [k, v] of Object.entries(next)) (v ? p.set(k, v) : p.delete(k));
    const s = p.toString();
    navigate('/knowledge/graph' + (s ? '?' + s : ''), { replace: true });
  };

  const conflict = detail.data?.conflicts[0];
  const preferred = conflict?.facts.find((f) => f.id === conflict.preferred_fact_id);
  const [resolving, setResolving] = useState(false);
  const [resolveError, setResolveError] = useState<unknown>(null);
  const resolve = async () => {
    if (!selected || !conflict?.preferred_fact_id) return;
    setResolving(true);
    setResolveError(null);
    try {
      await knowledgeApi.resolveConflict(selected, conflict.conflict_key, conflict.preferred_fact_id);
      detail.reload();
      entities.reload();
      neighborhood.reload();
    } catch (e) {
      setResolveError(e);
    } finally {
      setResolving(false);
    }
  };

  return (
    <DPage className="sg-graphpage">
      <PageHeader
        title="知识中心"
        description="知识图谱从你的知识库与已验收成果中整理，专家编排时按权限引用"
        actions={
          <SearchField
            size={32}
            width={240}
            placeholder="搜索实体，如“WEEE”"
            value={draft}
            clearable
            onValueChange={(v) => {
              setDraft(v);
              if (!v) setParams({ q: null });
            }}
            onSubmit={(v) => setParams({ q: v.trim() || null, entity: null })}
          />
        }
      />
      <div className="sg-tabs" role="tablist" aria-label="知识中心">
        <Tab size={37} label="知识库" selected={false} onSelect={() => navigate('/knowledge')} />
        <Tab size={37} label="知识图谱" {...(entities.data ? { count: `${entities.data.total} 个实体` } : {})} selected onSelect={() => undefined} />
        <span className="sg-tabs__right sg-dmuted">只读 · 首期</span>
      </div>
      {entities.status === 'error' && <ErrorBanner message={errorMessage(entities.error, '知识图谱刷新失败')} onRetry={entities.reload} />}
      <div className="sg-graph">
        <div className="sg-graph__filters">
          <MutedLabel>实体类型</MutedLabel>
          <button type="button" className={`sg-graph__filter${!type ? ' is-selected' : ''}`} onClick={() => setParams({ type: null, entity: null })}>
            全部
          </button>
          {ENTITY_TYPES.filter((t) => (entities.data?.type_counts[t.key] ?? 0) > 0).map((t) => (
            <button key={t.key} type="button" className={`sg-graph__filter${type === t.key ? ' is-selected' : ''}`} onClick={() => setParams({ type: t.key, entity: null })}>
              <span className="sg-gnode__dot" style={{ background: t.color }} />
              {t.label}
              <b>{entities.data?.type_counts[t.key] ?? 0}</b>
            </button>
          ))}
          <MutedLabel>时间</MutedLabel>
          <button type="button" className={`sg-graph__filter${!includeOld ? ' is-selected' : ''}`} onClick={() => setParams({ superseded: null })}>
            当前有效
          </button>
          <button type="button" className={`sg-graph__filter${includeOld ? ' is-selected' : ''}`} onClick={() => setParams({ superseded: '1' })}>
            包含已被取代的事实
          </button>
        </div>
        {!entities.data ? (
          <div className="sg-graph__canvas" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <QueryGate q={entities} label="知识图谱" rows={3} />
          </div>
        ) : entities.data.items.length === 0 ? (
          <div className="sg-graph__canvas" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <EmptyState icon="layers" title={q ? `没有找到与“${q}”相关的实体` : '还没有实体'} description="知识库资料完成索引后，实体会自动整理到这里" />
          </div>
        ) : neighborhood.data ? (
          <GraphCanvas nb={neighborhood.data} centerName={detail.data?.name ?? ''} onPick={(id) => setParams({ entity: id })} />
        ) : (
          <div className="sg-graph__canvas" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <QueryGate q={neighborhood} label="关联" rows={3} />
          </div>
        )}
        <div className="sg-graph__panel">
          {!selected ? (
            <p className="sg-dmuted">选择一个实体查看详情</p>
          ) : !detail.data ? (
            <QueryGate q={detail} label="实体" rows={3} />
          ) : (
            <>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                <IconTile icon="book" size={36} tone={detail.data.has_conflict ? 'warning' : 'neutral'} shape="rounded" />
                <div>
                  <div style={{ color: 'var(--color-blue-840)', font: 'var(--type-sans-sc-15-500-lh18)' }}>{detail.data.name}</div>
                  <div className="sg-dmuted">
                    {detail.data.subtitle} · 被 {detail.data.cited_task_count} 个任务引用
                  </div>
                </div>
              </div>
              {conflict && (
                <div className="sg-conflict" style={{ marginTop: 16 }}>
                  <span>
                    <b style={{ color: 'var(--color-blue-840)' }}>事实冲突：</b>
                    {conflictSentence(conflict.facts, conflict.preferred_fact_id)}
                  </span>
                  <div className="sg-conflict__actions">
                    <Button variant="secondary" size={28} label="以较新为准" disabled={resolving} onClick={() => void resolve()} />
                    {preferred?.source_document_id && (
                      <Button variant="ghost" size={28} label="查看来源" onClick={() => void openDoc(preferred.source_document_id!)} />
                    )}
                  </div>
                </div>
              )}
              {resolveError != null && <ErrorBanner message={errorMessage(resolveError, '冲突处理失败')} />}
              <MutedLabel>事实 · {detail.data.facts_total}</MutedLabel>
              {detail.data.facts.map((f) => (
                <div key={f.id} className={`sg-fact${f.validity === 'superseded' ? ' is-old' : ''}`}>
                  <span className="sg-fact__stmt">{f.statement}</span>
                  <span className="sg-fact__meta">
                    {f.source_document_id ? (
                      <button type="button" className="sg-dlink" title="查看资料" onClick={() => void openDoc(f.source_document_id!)}>
                        {f.source_label}
                      </button>
                    ) : (
                      f.source_label
                    )}
                    {' · '}
                    {monthOf(f.source_date)} · {f.validity === 'current' ? '当前有效' : '已被取代'}
                  </span>
                </div>
              ))}
              <MutedLabel>引用它的任务</MutedLabel>
              {detail.data.cited_tasks.length === 0 && <p className="sg-dmuted">还没有任务引用</p>}
              {detail.data.cited_tasks.map((t) => (
                <button key={t.id} type="button" className="sg-drow" onClick={() => navigate(`/tasks/${encodeURIComponent(t.id)}`)}>
                  <Icon name="message-square" size={15} />
                  <span className="sg-drow__text">
                    <span className="sg-drow__name">{t.title}</span>
                  </span>
                  <span className="sg-drow__right">{relativeDay(t.last_cited_at)}</span>
                </button>
              ))}
            </>
          )}
        </div>
      </div>
    </DPage>
  );

  async function openDoc(docId: string) {
    const doc = await knowledgeApi.document(docId);
    navigate(`/knowledge/${encodeURIComponent(doc.knowledge_base.id)}/docs/${encodeURIComponent(docId)}`);
  }
}
