import { useEffect, useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Icon, type IconName } from '../../components/Icon/Icon';
import { IconButton } from '../../components/IconButton/IconButton';
import { PageHeader } from '../../components/PageHeader/PageHeader';
import { SearchField } from '../../components/SearchField/SearchField';
import { EmptyState, ErrorBanner, LoadingState, Toast, errorMessage } from '../../components/States/States';
import { Tab } from '../../components/Tabs/Tab';
import { Tag } from '../../components/Tag/Tag';
import { KIND_ICON, KIND_LABEL, artifactApi, downloadUrl, versionTag, type Artifact, type ArtifactKind, type ArtifactQuery } from '../../features/artifacts/model';
import { ArtifactMenu, ShareCell, type MoveTarget, type Notice } from '../../features/artifacts/parts';
import { timeAgo, wsApi } from '../../features/workspaces/model';

const KINDS: ArtifactKind[] = ['document', 'spreadsheet', 'presentation', 'webpage', 'image'];
type Scope = 'all' | 'mine' | 'shared';
const SCOPES: Array<{ key: Scope; label: string }> = [
  { key: 'all', label: '最近更新' },
  { key: 'mine', label: '我创建的' },
  { key: 'shared', label: '共享给我' },
];

function NavItem({ icon, label, count, active, onClick }: { icon: IconName; label: string; count?: number | undefined; active: boolean; onClick: () => void }) {
  return (
    <button type="button" className={`sg-arts__navitem${active ? ' is-active' : ''}`} aria-current={active || undefined} onClick={onClick}>
      <Icon name={icon} size={14} />
      <span className="truncate">{label}</span>
      {count !== undefined && <span className="sg-arts__count">{count}</span>}
    </button>
  );
}

export default function Screen({ query, navigate }: ScreenProps) {
  const kindRaw = query.get('kind') as ArtifactKind | null;
  const kind = kindRaw && KINDS.includes(kindRaw) ? kindRaw : null;
  const workspace = query.get('workspace');
  const trashed = query.get('trashed') === '1';
  const scopeRaw = query.get('scope') as Scope | null;
  const scope: Scope = scopeRaw === 'mine' || scopeRaw === 'shared' ? scopeRaw : 'all';
  const q = query.get('q') ?? '';
  const [search, setSearch] = useState(q);
  useEffect(() => setSearch(q), [q]);

  const go = (next: { kind?: ArtifactKind | null; workspace?: string | null; trashed?: boolean; scope?: Scope; q?: string }) => {
    const p = new URLSearchParams();
    const k = 'kind' in next ? next.kind : kind;
    const w = 'workspace' in next ? next.workspace : workspace;
    const t = next.trashed ?? trashed;
    const s = next.scope ?? scope;
    const text = next.q ?? q;
    if (k) p.set('kind', k);
    if (w) p.set('workspace', w);
    if (t) p.set('trashed', '1');
    if (s !== 'all') p.set('scope', s);
    if (text) p.set('q', text);
    const qs = p.toString();
    navigate(qs ? `/artifacts?${qs}` : '/artifacts', { replace: true });
  };
  const select = (next: { kind?: ArtifactKind | null; workspace?: string | null; trashed?: boolean }) =>
    go({ kind: null, workspace: null, trashed: false, ...next });

  useEffect(() => {
    if (search === q) return;
    const t = window.setTimeout(() => go({ q: search.trim() }), 300);
    return () => window.clearTimeout(t);
  }, [search]);

  const params: ArtifactQuery = {
    sort: 'updated',
    ...(kind ? { kind } : {}),
    ...(workspace ? { workspace } : {}),
    ...(trashed ? { trashed: 1 as const } : {}),
    ...(scope !== 'all' ? { scope } : {}),
    ...(q ? { q } : {}),
  };
  const list = useApiQuery((s) => artifactApi.list(params, s), [kind, workspace, trashed, scope, q]);
  const recent = useApiQuery((s) => artifactApi.list({ sort: 'opened' }, s), []);
  const workspaces = useApiQuery((s) => wsApi.list(s), []);
  const [counts, setCounts] = useState(list.data?.counts);
  useEffect(() => {
    if (list.data) setCounts(list.data.counts);
  }, [list.data]);

  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const reload = () => {
    list.reload();
    recent.reload();
  };

  const targets: MoveTarget[] = [
    ...(workspaces.data?.items ?? []).map((w) => ({ id: w.id, name: w.name })),
    { id: null, name: '未关联工作区' },
  ];
  const wsName = workspace === 'none' ? '未关联工作区' : counts?.workspaces.find((w) => w.id === workspace)?.name;
  const title = trashed ? '回收站' : kind ? `全部${KIND_LABEL[kind]}` : workspace ? (wsName ?? '工作区成果') : '全部成果';
  const description = trashed ? '移到回收站的成果会保留 30 天，期间可以恢复' : '专家在任务中产出的文档、表格、演示、网页和图像，都集中在这里管理';
  const recentItems = (recent.data?.items ?? []).filter((a) => a.last_opened_at).slice(0, 3);
  const isAll = !kind && !workspace && !trashed;

  const row = (a: Artifact) => {
    const v = a.current_version;
    const sub = [a.source_task?.title, a.workspace?.name ?? '未关联工作区'].filter(Boolean).join(' · ');
    return (
      <div key={a.id} className="sg-arts__row" role="row">
        <button type="button" className="sg-arts__open" role="cell" onClick={() => !a.trashed_at && navigate(`/artifacts/${a.id}`)}>
          <span className="sg-arts__tile">
            <Icon name={KIND_ICON[a.kind] ?? 'file'} size={16} />
          </span>
          <span className="sg-arts__text">
            <span className="sg-arts__titleline">
              <span className="sg-arts__title truncate">{a.title}</span>
              {v && <Tag {...versionTag(v)} />}
            </span>
            <span className="sg-arts__sub truncate">{sub}</span>
          </span>
        </button>
        <span role="cell">
          <ShareCell artifact={a} />
        </span>
        <span className="sg-arts__time" role="cell">{timeAgo(a.trashed_at ?? a.updated_at)}</span>
        <span className={`sg-arts__actions${menuFor === a.id ? ' is-open' : ''}`} role="cell">
          {v && !a.trashed_at && (
            <a className="sg-iconbtn-link" href={downloadUrl(a.id, v.number)} download aria-label={`下载 ${a.title}`}>
              <IconButton icon="download" size={25} variant="ghost" label={`下载 ${a.title}`} tabIndex={-1} />
            </a>
          )}
          <IconButton icon="ellipsis" size={25} variant="ghost" label={`${a.title} 的更多操作`} onClick={() => setMenuFor(menuFor === a.id ? null : a.id)} />
        </span>
        {menuFor === a.id && (
          <div className="sg-arts__menuanchor">
            <ArtifactMenu artifact={a} targets={targets} navigate={navigate} onChanged={reload} onNotice={setNotice} onClose={() => setMenuFor(null)} />
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="sg-arts">
      <nav className="sg-arts__nav" aria-label="成果分类">
        <div className="sg-arts__navhead">
          <span>成果</span>
          <IconButton icon="search" size={25} variant="ghost" label="搜索成果" onClick={() => document.querySelector<HTMLInputElement>('.sg-arts__main input')?.focus()} />
        </div>
        <NavItem icon="layers" label="全部" count={counts?.all} active={isAll} onClick={() => select({})} />
        {KINDS.map((k) => (
          <NavItem key={k} icon={KIND_ICON[k]} label={KIND_LABEL[k]} count={counts?.kinds[k] ?? (counts ? 0 : undefined)} active={kind === k && !trashed} onClick={() => select({ kind: k })} />
        ))}
        <div className="sg-arts__group">按工作区</div>
        {counts?.workspaces.map((w) => (
          <NavItem
            key={w.id ?? 'none'}
            icon="folder"
            label={w.id ? w.name : '未关联工作区'}
            count={w.count}
            active={!trashed && workspace === (w.id ?? 'none')}
            onClick={() => select({ workspace: w.id ?? 'none' })}
          />
        ))}
        {recentItems.length > 0 && <div className="sg-arts__group">最近打开</div>}
        {recentItems.map((a) => (
          <NavItem key={a.id} icon={KIND_ICON[a.kind] ?? 'file'} label={a.title} active={false} onClick={() => navigate(`/artifacts/${a.id}`)} />
        ))}
        <div className="sg-arts__navfoot">
          <NavItem icon="archive" label="回收站" count={counts?.trash} active={trashed} onClick={() => select({ trashed: true })} />
        </div>
      </nav>

      <div className="sg-arts__main">
        <PageHeader
          title={title}
          description={description}
          actions={<SearchField size={32} width={220} placeholder="搜索成果名称或内容" value={search} clearable label="搜索成果" onValueChange={setSearch} onSubmit={(v) => go({ q: v.trim() })} />}
        />
        <div className="sg-arts__bar">
          <div className="sg-tabs" role="tablist" aria-label="成果范围">
            {SCOPES.map((s) => (
              <Tab key={s.key} size={37} label={s.label} selected={s.key === scope} onSelect={() => go({ scope: s.key })} />
            ))}
          </div>
        </div>
        <div role="table" aria-label="成果">
          <div className="sg-arts__row is-head" role="row">
            <span role="columnheader">成果</span>
            <span role="columnheader">共享对象</span>
            <span role="columnheader" className="sg-arts__time">{trashed ? '删除时间' : '更新时间'}</span>
            <span />
          </div>
          {list.status === 'error' && <ErrorBanner message={errorMessage(list.error, '成果暂时无法读取')} onRetry={list.reload} />}
          {list.status === 'loading' && !list.data && <LoadingState rows={5} />}
          {list.data && list.data.items.length === 0 && (
            <EmptyState
              icon={trashed ? 'archive' : 'layers'}
              title={q ? '没有找到相关成果' : trashed ? '回收站是空的' : '还没有成果'}
              {...(q
                ? { description: '换个关键词试试' }
                : !trashed
                  ? { description: '专家在任务中产出的文档、表格、网页会出现在这里', action: { label: '新建任务', onClick: () => navigate('/tasks/new') } }
                  : {})}
            />
          )}
          {list.data?.items.map(row)}
        </div>
      </div>
      {notice && (
        <Toast
          tone={notice.tone}
          message={notice.text}
          {...(notice.undo
            ? {
                actionLabel: '撤销',
                onAction: () => {
                  notice.undo?.();
                  setNotice(null);
                },
              }
            : {})}
          onClose={() => setNotice(null)}
        />
      )}
    </div>
  );
}
