import { Fragment, useEffect, useState } from 'react';
import { useShell } from '../../app/AppShell';
import type { ScreenProps } from '../../app/router';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Avatar } from '../../components/Avatar/Avatar';
import { BackLink } from '../../components/BackLink/BackLink';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { IconButton } from '../../components/IconButton/IconButton';
import { ErrorBanner, errorMessage, QueryGate } from '../../components/States/States';
import { PageHeader } from '../../components/PageHeader/PageHeader';
import { Tag } from '../../components/Tag/Tag';
import { DPage, KvRow, MutedLabel } from '../../features/experts/parts';
import { relativeDay } from '../../features/experts/api';
import { knowledgeApi, fileSize, type PageBlock } from '../../features/knowledge/api';
import '../../features/experts/frame.css';
import '../../features/knowledge/kb.css';

function BlockText({ block, onEntity }: { block: PageBlock; onEntity: (id: string) => void }) {
  const spans = [...(block.spans ?? [])].sort((a, b) => a.start - b.start);
  if (spans.length === 0) return <>{block.text}</>;
  const out: React.ReactNode[] = [];
  let pos = 0;
  spans.forEach((sp, i) => {
    if (sp.start > pos) out.push(<Fragment key={`t${i}`}>{block.text.slice(pos, sp.start)}</Fragment>);
    const seg = block.text.slice(sp.start, sp.end);
    if (sp.kind === 'entity' && sp.entity_id)
      out.push(
        <button key={`s${i}`} type="button" className="sg-entity sg-dlink" style={{ padding: 0, font: 'inherit' }} onClick={() => onEntity(sp.entity_id!)}>
          {seg}
        </button>,
      );
    else out.push(<mark key={`s${i}`}>{seg}</mark>);
    pos = sp.end;
  });
  if (pos < block.text.length) out.push(<Fragment key="tail">{block.text.slice(pos)}</Fragment>);
  return <>{out}</>;
}

export default function Screen({ params, query, navigate }: ScreenProps) {
  const { kbId, docId } = params;
  const fromSearch = query.get('from') === 'search';
  const q = query.get('q') ?? '';
  const { toast, openSearch } = useShell();
  const [pageNo, setPageNo] = useState(1);

  const doc = useApiQuery((signal) => knowledgeApi.document(docId!, signal), [docId]);
  const page = useApiQuery(
    (signal) => (doc.data?.index_state === 'indexed' ? knowledgeApi.page(docId!, pageNo, signal) : Promise.resolve(null)),
    [docId, pageNo, doc.data?.index_state],
  );
  const citations = useApiQuery((signal) => knowledgeApi.citations(docId!, signal), [docId]);

  // from=search with q comes from the knowledge search page; without q it comes from the global search layer.
  const back = () => {
    if (fromSearch && !q) {
      openSearch();
      return;
    }
    navigate(fromSearch ? `/knowledge?q=${encodeURIComponent(q)}` : `/knowledge/${encodeURIComponent(kbId!)}`);
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') back();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const reindex = useSubmit(async () => {
    await knowledgeApi.reindex(docId!);
    toast({ tone: 'undo', message: '已重新加入索引队列' });
    doc.reload();
  });

  const d = doc.data;
  const backLabel = fromSearch ? `返回搜索 · ${d?.knowledge_base.name ?? ''}` : (d?.knowledge_base.name ?? '知识库');
  const citeEnabled = d?.knowledge_base.expert_cite_enabled !== false;
  const pageCount = d?.page_count ?? page.data?.page_count ?? 1;

  const openEntity = (entityId: string) => navigate(`/knowledge/graph?entity=${encodeURIComponent(entityId)}`);

  return (
    <DPage>
      <BackLink label={backLabel} size={30} onBack={back} />
      {!d ? (
        <div className="sg-dstate">
          <QueryGate q={doc} label="资料" rows={3} />
        </div>
      ) : (
        <>
          <PageHeader
            title={d.title}
            description={[d.page_count ? `${d.page_count} 页` : null, d.index_state === 'indexed' ? '已索引' : d.index_state === 'indexing' ? `索引中 ${d.index_progress ?? 0}%` : d.index_state === 'failed' ? '索引失败' : '待索引', `被引用 ${d.citation_count} 次`]
              .filter(Boolean)
              .join(' · ')}
            actions={<Button variant="secondary" size={32} icon="download" label="下载" onClick={() => window.open(knowledgeApi.downloadUrl(d.id), '_blank')} />}
          />
          {!citeEnabled && (
            <div className="sg-dnote" style={{ marginTop: 16 }}>
              <Icon name="info" size={14} />
              <span>所属知识库已关闭专家引用：这份资料仍可查看，但专家不会再引用它。</span>
            </div>
          )}
          <div className="sg-docpage">
            <div className="sg-docpage__cols">
              <div className="sg-docpage__blocks sg-docpreview" style={{ maxWidth: 'none' }}>
                {d.index_state !== 'indexed' ? (
                  <>
                    <ErrorBanner
                      message={d.index_state === 'failed' ? '这份资料索引失败，暂时无法预览正文' : '这份资料还在索引中，完成后就能预览正文'}
                      {...(d.can_edit && d.index_state === 'failed' ? { onRetry: () => void reindex.submit() } : {})}
                    />
                    {d.can_edit && d.index_state === 'failed' && (
                      <div style={{ marginTop: 12 }}>
                        <Button variant="secondary" size={32} icon="refresh" label="重建索引" busy={reindex.submitting} onClick={() => void reindex.submit()} />
                      </div>
                    )}
                  </>
                ) : !page.data ? (
                  <QueryGate q={page} label="正文" rows={4} />
                ) : (
                  <>
                    {page.data.blocks.map((b, i) =>
                      b.kind === 'heading' ? (
                        <h3 key={i}>
                          <BlockText block={b} onEntity={openEntity} />
                        </h3>
                      ) : (
                        <p key={i}>
                          <BlockText block={b} onEntity={openEntity} />
                        </p>
                      ),
                    )}
                    <div className="sg-docpage__pager">
                      <IconButton icon="chevron-left" size={28} variant="ghost" label="上一页" disabled={pageNo <= 1} onClick={() => setPageNo((n) => Math.max(1, n - 1))} />
                      第 {pageNo} / {pageCount} 页
                      <IconButton icon="chevron-right" size={28} variant="ghost" label="下一页" disabled={pageNo >= pageCount} onClick={() => setPageNo((n) => Math.min(pageCount, n + 1))} />
                    </div>
                  </>
                )}
              </div>
              <aside className="sg-docpage__side">
                <MutedLabel>资料信息</MutedLabel>
                <KvRow k="来源">
                  {d.source_kind === 'web' ? '网页' : `${d.uploaded_by?.name ?? '—'} 上传`} · {relativeDay(d.created_at)}
                </KvRow>
                <KvRow k="大小">{[fileSize(d.size_bytes), d.page_count ? `${d.page_count} 页` : null].filter(Boolean).join(' · ') || '—'}</KvRow>
                <div className="sg-dkv">
                  <span className="sg-dkv__k">索引</span>
                  <span className="sg-dkv__v">
                    {d.index_state === 'indexed' ? <Tag label="已索引" tone="success" size={18} /> : d.index_state === 'failed' ? <Tag label="索引失败" tone="danger" size={18} /> : <Tag label="索引中" tone="info" size={18} />}
                  </span>
                </div>
                <KvRow k="可见范围">{(d.knowledge_base.scope === 'workspace' && d.knowledge_base.workspace?.name) || d.knowledge_base.name}（{d.knowledge_base.scope === 'workspace' ? '工作区' : d.knowledge_base.scope === 'personal' ? '个人' : '企业'}）</KvRow>
                <div className="sg-ddivider" />
                <MutedLabel>被引用 · {d.citation_count} 次</MutedLabel>
                {!citations.data ? (
                  <QueryGate q={citations} label="引用" rows={2} />
                ) : citations.data.items.length === 0 ? (
                  <p className="sg-dmuted">还没有被引用</p>
                ) : (
                  citations.data.items.slice(0, 6).map((c) => (
                    <button key={c.id} type="button" className="sg-cite" onClick={() => c.task && navigate(`/tasks/${encodeURIComponent(c.task.id)}`)}>
                      <Avatar size={20} src={c.task?.lead_expert?.avatar_ref ?? null} alt={c.task?.lead_expert?.name ?? '专家'} />
                      <span className="sg-cite__text">
                        <span className="sg-cite__title truncate">{c.task?.title ?? '任务'}</span>
                      </span>
                      <span className="sg-cite__meta">
                        {c.locator ?? ''} · {relativeDay(c.created_at)}
                      </span>
                    </button>
                  ))
                )}
                <div className="sg-ddivider" />
                <MutedLabel>在知识图谱中</MutedLabel>
                {d.related_entities.length === 0 ? (
                  <p className="sg-dmuted">没有关联实体</p>
                ) : (
                  <p className="sg-dmuted" style={{ margin: 0 }}>
                    关联实体：{d.related_entities.map((e) => e.name).join('、')} ·{' '}
                    <button type="button" className="sg-dlink" onClick={() => openEntity(d.related_entities[0]!.id)}>
                      在图谱中查看
                    </button>
                  </p>
                )}
              </aside>
            </div>
          </div>
        </>
      )}
      {doc.status === 'error' && doc.data && <ErrorBanner message={errorMessage(doc.error, '资料刷新失败')} onRetry={doc.reload} />}
      {reindex.error && <ErrorBanner message={errorMessage(reindex.error, '重建索引失败，请重试')} />}
    </DPage>
  );
}
