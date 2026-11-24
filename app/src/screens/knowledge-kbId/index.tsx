import { useMemo, useState } from 'react';
import { useShell } from '../../app/AppShell';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Avatar } from '../../components/Avatar/Avatar';
import { BackLink } from '../../components/BackLink/BackLink';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { Menu, MenuItem, Popover } from '../../components/Menu/MenuItem';
import { EmptyState, ErrorBanner, errorMessage, QueryGate } from '../../components/States/States';
import { PageHeader } from '../../components/PageHeader/PageHeader';
import { Switch } from '../../components/Switch/Switch';
import { Tab } from '../../components/Tabs/Tab';
import { Tag } from '../../components/Tag/Tag';
import { DPage, KvRow } from '../../features/experts/parts';
import { relativeDay } from '../../features/experts/api';
import { AddDocumentDialog } from '../../features/knowledge/AddDocumentDialog';
import { knowledgeApi, scopeLine, fileSize, type Citation, type DocumentItem } from '../../features/knowledge/api';
import '../../features/experts/frame.css';
import '../../features/knowledge/kb.css';

function statusTag(d: DocumentItem, staleDays?: number) {
  if (d.is_stale) return <Tag label={staleDays ? `已过期 · 超过 ${staleDays} 天` : '已过期'} tone="warning" size={20} />;
  if (d.index_state === 'indexed') return <Tag label="已索引" tone="success" size={20} />;
  if (d.index_state === 'failed') return <Tag label="索引失败" tone="danger" size={20} />;
  if (d.index_state === 'indexing') return <Tag label={`索引中 ${d.index_progress ?? 0}%`} tone="info" size={20} />;
  return <Tag label="待索引" tone="neutral" size={20} />;
}

function sourceText(d: DocumentItem) {
  if (d.source_kind === 'web') return '网页';
  if (d.source_expert) return `${d.source_expert.name}（专家产出）`;
  return d.uploaded_by?.name ?? '—';
}

const INTERVAL_LABEL: Record<string, string> = { daily: '每天更新', weekly: '每周更新', monthly: '每月更新' };

type TabKey = 'docs' | 'citations';

export default function Screen({ params, navigate }: ScreenProps) {
  const kbId = params.kbId!;
  const { toast } = useShell();
  const [tab, setTab] = useState<TabKey>('docs');
  const [addOpen, setAddOpen] = useState(false);
  const [permAt, setPermAt] = useState<{ x: number; y: number } | null>(null);

  const kb = useApiQuery((signal) => knowledgeApi.base(kbId, signal), [kbId]);
  const docs = useApiQuery((signal) => knowledgeApi.documents({ knowledge_base: kbId, sort: 'updated' }, signal), [kbId]);

  // 引用记录 has no per-kb endpoint; it is aggregated across the kb's documents.
  const citations = useApiQuery(async (signal) => {
    const list = await knowledgeApi.documents({ knowledge_base: kbId }, signal);
    const perDoc = await Promise.all(list.items.map((d) => knowledgeApi.citations(d.id, signal).catch(() => null)));
    return perDoc.flatMap((p) => p?.items ?? []).sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  }, [kbId]);

  const k = kb.data;
  const docItems = docs.data?.items ?? [];
  const staleDays = k?.stale_policy?.threshold_days;

  const toggleCite = async (v: boolean) => {
    if (!k) return;
    try {
      await knowledgeApi.updateBase(kbId, k.version, { expert_cite_enabled: v });
      kb.reload();
      toast({ tone: 'undo', message: v ? '已允许专家引用' : '已关闭专家引用' });
    } catch (err) {
      toast({ tone: 'failure', message: errorMessage(err, '更新失败，请重试') });
    }
  };

  const header = useMemo(
    () => (
      <>
        <BackLink label="知识中心" size={30} onBack={() => navigate('/knowledge')} />
        <PageHeader
          title={k?.name ?? '知识库'}
          description={k ? `${k.document_count} 份资料 · ${k.scope === 'workspace' && k.workspace ? `工作区“${k.workspace.name}”` : scopeLine(k)}` : undefined}
          actions={
            k && (
              <>
                <Button
                  variant="secondary"
                  size={32}
                  icon="settings"
                  label="权限"
                  onClick={(e) => {
                    const r = e.currentTarget.getBoundingClientRect();
                    setPermAt({ x: r.right - 260, y: r.bottom + 6 });
                  }}
                />
                <Button variant="primary" size={32} icon="plus" label="添加资料" disabled={!k.can_edit} onClick={() => setAddOpen(true)} />
              </>
            )
          }
        />
        <div className="sg-tabs" role="tablist" aria-label="知识库">
          <Tab size={37} label="资料" {...(docs.data ? { count: docs.data.total } : {})} selected={tab === 'docs'} onSelect={() => setTab('docs')} />
          <Tab size={37} label="知识图谱" selected={false} onSelect={() => navigate('/knowledge/graph')} />
          <Tab size={37} label="引用记录" {...(citations.data ? { count: citations.data.length } : {})} selected={tab === 'citations'} onSelect={() => setTab('citations')} />
        </div>
      </>
    ),
    [k, docs.data, citations.data, tab, navigate],
  );

  if (!k)
    return (
      <DPage>
        {header}
        <div className="sg-dstate">
          <QueryGate q={kb} label="知识库" rows={3} />
        </div>
      </DPage>
    );

  return (
    <DPage>
      {header}
      {kb.status === 'error' && <ErrorBanner message={errorMessage(kb.error, '知识库刷新失败')} onRetry={kb.reload} />}
      {!k.expert_cite_enabled && (
        <div className="sg-dnote" style={{ marginTop: 16 }}>
          <Icon name="info" size={14} />
          <span>已关闭专家引用：专家不能再引用这个知识库的资料，已有引用记录保留。</span>
        </div>
      )}
      {tab === 'docs' ? (
        !docs.data ? (
          <div className="sg-dstate">
            <QueryGate q={docs} label="资料" rows={4} />
          </div>
        ) : docItems.length === 0 ? (
          <EmptyState icon="file" title="这个知识库还没有资料" {...(k.can_edit ? { action: { label: '添加资料', onClick: () => setAddOpen(true) } } : {})} />
        ) : (
          <>
            <div className="sg-doctable">
              <div className="sg-doctable__head">
                <span>名称</span>
                <span>来源</span>
                <span>状态</span>
                <span style={{ textAlign: 'right' }}>被引用</span>
                <span style={{ textAlign: 'right' }}>更新</span>
              </div>
              {docItems.map((d) => (
                <div key={d.id} className="sg-docrow" role="button" tabIndex={0} onClick={() => navigate(`/knowledge/${encodeURIComponent(kbId)}/docs/${encodeURIComponent(d.id)}`)} onKeyDown={(e) => e.key === 'Enter' && navigate(`/knowledge/${encodeURIComponent(kbId)}/docs/${encodeURIComponent(d.id)}`)}>
                  <span className="sg-docrow__name">
                    <Icon name={d.source_kind === 'web' ? 'globe' : 'file'} size={16} />
                    <span className="sg-docrow__title">
                      <b className="truncate">{d.title}</b>
                      <span>{d.source_kind === 'web' ? `网页${d.refresh_interval ? ` · ${INTERVAL_LABEL[d.refresh_interval] ?? d.refresh_interval}` : ''}` : fileSize(d.size_bytes)}</span>
                    </span>
                  </span>
                  <span className="sg-docrow__cell truncate">{sourceText(d)}</span>
                  <span className="sg-docrow__cell">{statusTag(d, staleDays)}</span>
                  <span className="sg-docrow__right">{d.citation_count}</span>
                  <span className="sg-docrow__right">{relativeDay(d.updated_at)}</span>
                </div>
              ))}
            </div>
            {staleDays && (
              <p className="sg-kbfoot">
                过期资料仍可查看，但专家引用时会提示“数据可能过期”；工作区规则要求竞品数据在 {staleDays} 天内
              </p>
            )}
          </>
        )
      ) : !citations.data ? (
        <div className="sg-dstate">
          <QueryGate q={citations} label="引用记录" rows={3} />
        </div>
      ) : citations.data.length === 0 ? (
        <EmptyState icon="book-badge" title="还没有被引用的记录" description="专家在任务中引用这里的资料后，记录会出现在这里" />
      ) : (
        <div style={{ maxWidth: 720, marginTop: 16 }}>
          {citations.data.map((c: Citation) => (
            <button key={c.id} type="button" className="sg-cite" onClick={() => c.task && navigate(`/tasks/${encodeURIComponent(c.task.id)}`)}>
              <Avatar size={20} src={c.task?.lead_expert?.avatar_ref ?? null} alt={c.task?.lead_expert?.name ?? '专家'} />
              <span className="sg-cite__text">
                <span className="sg-cite__title truncate">{c.task?.title ?? '任务'}</span>
              </span>
              <span className="sg-cite__meta">
                {c.locator ?? ''} · {relativeDay(c.created_at)}
              </span>
            </button>
          ))}
        </div>
      )}
      {permAt && (
        <Popover at={permAt} width={260} onClose={() => setPermAt(null)}>
          <Menu label="权限">
            <div style={{ padding: '8px 12px' }}>
              <KvRow k="共享范围">{scopeLine(k)}</KvRow>
              <div className="sg-dkv">
                <span className="sg-dkv__k">允许专家引用</span>
                <Switch checked={k.expert_cite_enabled} disabled={!k.can_edit} onChange={(v) => void toggleCite(v)} label="允许专家引用" />
              </div>
              <KvRow k="编辑权限">{k.editors.length ? k.editors.map((e) => e.name).join('、') : k.owner?.name ?? '—'}</KvRow>
            </div>
            <MenuItem icon="users" label="成员与权限在设置中管理" onSelect={() => navigate('/settings/org-members')} />
          </Menu>
        </Popover>
      )}
      {addOpen && (
        <AddDocumentDialog
          bases={[k]}
          fixedKbId={k.id}
          onClose={() => setAddOpen(false)}
          onAdded={(doc) => {
            setAddOpen(false);
            docs.reload();
            navigate(`/knowledge/${encodeURIComponent(kbId)}/docs/${encodeURIComponent(doc.id)}`);
          }}
        />
      )}
    </DPage>
  );
}
