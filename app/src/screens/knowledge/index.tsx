import { useMemo, useState } from 'react';
import { useShell } from '../../app/AppShell';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { PageHeader } from '../../components/PageHeader/PageHeader';
import { SearchField } from '../../components/SearchField/SearchField';
import { StatusDot } from '../../components/StatusDot/StatusDot';
import { EmptyState, ErrorBanner, errorMessage, QueryGate } from '../../components/States/States';
import { Switch } from '../../components/Switch/Switch';
import { Tab } from '../../components/Tabs/Tab';
import { DPage, KvRow, MutedLabel, TextMenu } from '../../features/experts/parts';
import { relativeDay } from '../../features/experts/api';
import { AddDocumentDialog } from '../../features/knowledge/AddDocumentDialog';
import { AUDIENCE_LABEL, knowledgeApi, scopeLine, scopeListLine, type DocumentItem, type KnowledgeBase } from '../../features/knowledge/api';
import '../../features/experts/frame.css';
import '../../features/knowledge/kb.css';

function statusOf(kb: KnowledgeBase) {
  if (kb.failed_count > 0) return { color: 'color.red.500' as const, text: `${kb.failed_count} 份失败 · 重试`, cls: 'sg-ddanger' };
  if (kb.processing_count > 0) return { color: 'color.orange.660' as const, text: `${kb.processing_count} 份处理中`, cls: 'sg-dwarn' };
  return { color: 'color.green.660' as const, text: '全部就绪', cls: 'sg-dok' };
}

function docState(d: DocumentItem) {
  if (d.index_state === 'indexed') return { text: '就绪', cls: 'sg-dok', progress: null as number | null };
  if (d.index_state === 'failed') return { text: '失败', cls: 'sg-ddanger', progress: null };
  return { text: '处理中', cls: 'sg-dwarn', progress: d.index_progress };
}

function Rail({ kb, onToggleCite, onOpenDoc, onAddDoc, navigate }: { kb: KnowledgeBase; onToggleCite: (v: boolean) => Promise<void>; onOpenDoc: (id: string) => void; onAddDoc: () => void; navigate: ScreenProps['navigate'] }) {
  const docs = useApiQuery((signal) => knowledgeApi.documents({ knowledge_base: kb.id }, signal), [kb.id]);
  const detail = useApiQuery((signal) => knowledgeApi.base(kb.id, signal), [kb.id]);
  const audit = useApiQuery((signal) => (detail.data?.can_edit ? knowledgeApi.audit(kb.id, signal) : Promise.resolve(null)), [kb.id, detail.data?.can_edit]);
  const [auditOpen, setAuditOpen] = useState(false);
  const d = detail.data;
  return (
    <>
      <h2 className="sg-kbrail__title">{kb.name}</h2>
      <p className="sg-kbrail__sub">
        {scopeLine(kb)} · {kb.document_count} 份资料
      </p>
      <MutedLabel right={<button type="button" className="sg-dlink" onClick={onAddDoc}>+ 添加资料</button>}>资料</MutedLabel>
      {!docs.data ? (
        <QueryGate q={docs} label="资料" rows={2} />
      ) : docs.data.items.length === 0 ? (
        <p className="sg-dmuted">还没有资料</p>
      ) : (
        docs.data.items.slice(0, 5).map((doc) => {
          const st = docState(doc);
          return (
            <button key={doc.id} type="button" className="sg-kbdoc" onClick={() => onOpenDoc(doc.id)}>
              <Icon name={doc.source_kind === 'web' ? 'globe' : 'file'} size={15} />
              <span className="sg-kbdoc__text">
                <span className="sg-kbdoc__name truncate">{doc.title}</span>
                {st.progress !== null && (
                  <span className="sg-kbdoc__bar">
                    <span style={{ width: `${st.progress}%` }} />
                  </span>
                )}
              </span>
              <span className={`sg-drow__right ${st.cls}`}>{st.text}</span>
            </button>
          );
        })
      )}
      {docs.data && docs.data.total > 0 && (
        <button type="button" className="sg-dlink" style={{ marginTop: 4 }} onClick={() => navigate(`/knowledge/${encodeURIComponent(kb.id)}`)}>
          查看全部 {docs.data.total} 份
        </button>
      )}
      <div className="sg-ddivider" />
      <MutedLabel>访问与引用</MutedLabel>
      <KvRow k="共享范围">{AUDIENCE_LABEL[kb.cite_audience] ?? scopeLine(kb)} · 可引用</KvRow>
      <div className="sg-dkv">
        <span className="sg-dkv__k">允许专家引用</span>
        <Switch checked={kb.expert_cite_enabled} disabled={!kb.can_edit} onChange={(v) => void onToggleCite(v)} label="允许专家引用" />
      </div>
      <KvRow k="编辑权限">{d ? (d.editors.length ? d.editors.map((e) => e.name).join('、') : d.owner?.name ?? '—') : '…'}</KvRow>
      <div className="sg-ddivider" />
      <button type="button" className="sg-dlabel" style={{ width: '100%', cursor: 'pointer' }} onClick={() => setAuditOpen((o) => !o)}>
        <span>版本与审计记录</span>
        <span className="sg-dlabel__right">
          <Icon name={auditOpen ? 'chevron-down' : 'chevron-right'} size={12} />
        </span>
      </button>
      {auditOpen &&
        (audit.data === null ? (
          <p className="sg-dmuted">只有可编辑的成员能查看审计记录</p>
        ) : !audit.data ? (
          <QueryGate q={audit} label="审计记录" rows={1} />
        ) : audit.data.items.length === 0 ? (
          <p className="sg-dmuted">暂无记录</p>
        ) : (
          audit.data.items.map((a, i) => (
            <p key={i} className="sg-dmuted" style={{ margin: '4px 0' }}>
              {a.summary ?? String(a.action ?? '')} · {relativeDay(a.created_at as string | undefined)}
            </p>
          ))
        ))}
    </>
  );
}

export default function Screen({ query, navigate }: ScreenProps) {
  const { toast } = useShell();
  const q = query.get('q') ?? '';
  const scope = query.get('scope') ?? '';
  const selectedId = query.get('kb');
  const [draft, setDraft] = useState(q);
  const [addOpen, setAddOpen] = useState(false);

  const bases = useApiQuery((signal) => knowledgeApi.bases({ scope: scope || undefined, q: q || undefined }, signal), [scope, q]);
  const docResults = useApiQuery(
    (signal) => (q ? knowledgeApi.documents({ q, sort: 'updated' }, signal) : Promise.resolve(null)),
    [q],
  );

  const items = bases.data?.items ?? [];
  const selected = useMemo(() => items.find((k) => k.id === selectedId) ?? items[0], [items, selectedId]);

  const setParams = (next: Record<string, string | null>, replace = true) => {
    const p = new URLSearchParams(query);
    for (const [k, v] of Object.entries(next)) (v ? p.set(k, v) : p.delete(k));
    const s = p.toString();
    navigate('/knowledge' + (s ? '?' + s : ''), { replace });
  };

  const toggleCite = async (kb: KnowledgeBase, v: boolean) => {
    try {
      const detail = await knowledgeApi.base(kb.id);
      await knowledgeApi.updateBase(kb.id, detail.version, { expert_cite_enabled: v });
      bases.reload();
      toast({ tone: 'undo', message: v ? `已允许专家引用“${kb.name}”` : `已关闭“${kb.name}”的专家引用` });
    } catch (err) {
      toast({ tone: 'failure', message: errorMessage(err, '更新失败，请重试') });
    }
  };

  const openDoc = async (docId: string) => {
    try {
      const doc = await knowledgeApi.document(docId);
      const params = q ? `?from=search&q=${encodeURIComponent(q)}` : '';
      navigate(`/knowledge/${encodeURIComponent(doc.knowledge_base.id)}/docs/${encodeURIComponent(docId)}${params}`);
    } catch (err) {
      toast({ tone: 'failure', message: errorMessage(err, '资料暂时无法打开') });
    }
  };

  const rail = selected ? (
    <Rail kb={selected} onToggleCite={(v) => toggleCite(selected, v)} onOpenDoc={(id) => openDoc(id)} onAddDoc={() => setAddOpen(true)} navigate={navigate} />
  ) : undefined;

  return (
    <DPage railWidth={360} rail={rail}>
      <PageHeader
        title="知识中心"
        description="专家只会在你授权的范围内引用这些资料"
        actions={
          <>
            <SearchField
              size={32}
              width={240}
              placeholder="搜索资料名称或正文"
              value={draft}
              clearable
              onValueChange={(v) => {
                setDraft(v);
                if (!v) setParams({ q: null });
              }}
              onSubmit={(v) => setParams({ q: v.trim() || null })}
            />
            <Button variant="primary" size={32} icon="plus" label="添加资料" onClick={() => setAddOpen(true)} />
          </>
        }
      />
      <div className="sg-tabs" role="tablist" aria-label="知识中心">
        <Tab size={37} label="知识库" {...(bases.data ? { count: bases.data.total } : {})} selected onSelect={() => undefined} />
        <Tab size={37} label="知识图谱" selected={false} onSelect={() => navigate('/knowledge/graph')} />
        <span className="sg-tabs__right">
          <TextMenu
            prefix="范围："
            value={scope}
            options={[
              { value: '', label: '全部可访问' },
              { value: 'personal', label: '个人' },
              { value: 'workspace', label: '工作区' },
              { value: 'account', label: '企业' },
            ]}
            onChange={(v) => setParams({ scope: v || null, kb: null })}
          />
        </span>
      </div>
      {bases.status === 'error' && <ErrorBanner message={errorMessage(bases.error, '知识库列表刷新失败')} onRetry={bases.reload} />}
      {!bases.data ? (
        <div className="sg-dstate">
          <QueryGate q={bases} label="知识库" rows={4} />
        </div>
      ) : items.length === 0 && !q ? (
        <EmptyState icon="book" title="还没有知识库" description="添加资料后，专家就能在你授权的范围内引用" />
      ) : (
        <>
          {items.length > 0 && (
            <div className="sg-kblist">
              {items.map((kb) => {
                const st = statusOf(kb);
                return (
                  <button key={kb.id} type="button" className={`sg-kbrow${selected?.id === kb.id ? ' is-selected' : ''}`} onClick={() => setParams({ kb: kb.id })}>
                    <Icon name="book" size={16} />
                    <span className="sg-kbrow__text">
                      <span className="sg-kbrow__name truncate">{kb.name}</span>
                      <span className="sg-kbrow__sub">{scopeListLine(kb)}</span>
                    </span>
                    <span className="sg-kbrow__count">{kb.document_count} 份</span>
                    <span className={`sg-kbrow__status ${st.cls}`}>
                      <StatusDot color={st.color} size={6} /> {st.text}
                    </span>
                    <span className="sg-kbrow__time">{relativeDay(kb.content_updated_at)}</span>
                  </button>
                );
              })}
            </div>
          )}
          {q && (
            <>
              <MutedLabel>{docResults.data ? `资料 ${docResults.data.total} 份` : '资料'}</MutedLabel>
              {docResults.data === null ? null : !docResults.data ? (
                <QueryGate q={docResults} label="资料" rows={2} />
              ) : docResults.data.items.length === 0 ? (
                <EmptyState icon="file" title={`没有找到与“${q}”相关的资料`} />
              ) : (
                <div className="sg-kblist">
                  {docResults.data.items.map((d) => (
                    <button key={d.id} type="button" className="sg-kbrow" onClick={() => void openDoc(d.id)}>
                      <Icon name={d.source_kind === 'web' ? 'globe' : 'file'} size={16} />
                      <span className="sg-kbrow__text">
                        <span className="sg-kbrow__name truncate">{d.title}</span>
                        <span className="sg-kbrow__sub">被引用 {d.citation_count} 次</span>
                      </span>
                      <span className="sg-kbrow__time">{relativeDay(d.updated_at)}</span>
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
        </>
      )}
      {addOpen && (
        <AddDocumentDialog
          bases={bases.data?.items ?? []}
          {...(selected ? { fixedKbId: undefined } : {})}
          onClose={() => setAddOpen(false)}
          onAdded={(doc) => {
            setAddOpen(false);
            bases.reload();
            navigate(`/knowledge/${encodeURIComponent(doc.knowledge_base.id)}/docs/${encodeURIComponent(doc.id)}`);
          }}
        />
      )}
    </DPage>
  );
}
