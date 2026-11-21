import { useEffect, useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Avatar } from '../../components/Avatar/Avatar';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { PageHeader } from '../../components/PageHeader/PageHeader';
import { SearchField } from '../../components/SearchField/SearchField';
import { Select } from '../../components/Select/Select';
import { EmptyState, ErrorBanner, LoadingState, errorMessage } from '../../components/States/States';
import { Tab } from '../../components/Tabs/Tab';
import { avatarOf } from '../../features/task/model';
import { taskState, timeAgo, wsApi, type TaskItem, type TasksQuery } from '../../features/workspaces/model';
import { StateLabel } from '../../features/workspaces/parts';
import '../../features/workspaces/workspaces.css';

type Scope = 'all' | NonNullable<TasksQuery['scope']>;
const SCOPES: Array<{ key: Scope; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'owned', label: '我负责' },
  { key: 'created', label: '我发起的' },
  { key: 'shared', label: '共享给我' },
  { key: 'archived', label: '已归档' },
];

function ownerText(t: TaskItem): string {
  if (!t.owner || t.owner.is_me) return '我';
  return t.shared_with_me ? `${t.owner.display_name} · 共享给我` : t.owner.display_name;
}

export default function Screen({ query, navigate }: ScreenProps) {
  const rawScope = query.get('scope') as Scope | null;
  const scope: Scope = rawScope && SCOPES.some((s) => s.key === rawScope) ? rawScope : 'all';
  const workspace = query.get('workspace') ?? '';
  const q = query.get('q') ?? '';
  const [search, setSearch] = useState(q);
  useEffect(() => setSearch(q), [q]);

  const go = (next: { scope?: Scope; workspace?: string; q?: string }) => {
    const p = new URLSearchParams();
    const s = next.scope ?? scope;
    const w = next.workspace ?? workspace;
    const text = next.q ?? q;
    if (s !== 'all') p.set('scope', s);
    if (w) p.set('workspace', w);
    if (text) p.set('q', text);
    const qs = p.toString();
    navigate(qs ? `/tasks?${qs}` : '/tasks', { replace: true });
  };

  // Debounced so typing does not fire a request per keystroke.
  useEffect(() => {
    if (search === q) return;
    const t = window.setTimeout(() => go({ q: search.trim() }), 300);
    return () => window.clearTimeout(t);
  }, [search]);

  const list = useApiQuery(
    (s) =>
      wsApi.tasks(
        {
          ...(scope !== 'all' ? { scope } : {}),
          ...(workspace ? { workspace } : {}),
          ...(q ? { q } : {}),
          status_group: 'all',
        },
        s,
      ),
    [scope, workspace, q],
  );
  const workspaces = useApiQuery((s) => wsApi.list(s), []);
  const counts = list.data?.counts;
  const wsOptions = [
    { value: '', label: '工作区：全部' },
    ...(workspaces.data?.items ?? []).map((w) => ({ value: w.id, label: `工作区：${w.name}` })),
    { value: 'none', label: '工作区：独立任务' },
  ];

  return (
    <div className="sg-alltasks">
      <PageHeader
        title="全部任务"
        description="包含各工作区与独立任务；原“协作”中的“我发起的”“共享给我”在这里筛选"
        actions={
          <>
            <SearchField size={32} width={220} placeholder="搜索任务" value={search} clearable label="搜索任务" onValueChange={setSearch} onSubmit={(v) => go({ q: v.trim() })} />
            <Button variant="primary" size={32} icon="plus" label="新任务" onClick={() => navigate('/tasks/new')} />
          </>
        }
      />
      <div className="sg-alltasks__bar">
        <div className="sg-tabs" role="tablist" aria-label="任务范围">
          {SCOPES.map((s) => {
            const n = counts?.[s.key];
            return <Tab key={s.key} size={37} label={n === undefined ? s.label : `${s.label} ${n}`} selected={s.key === scope} onSelect={() => go({ scope: s.key })} />;
          })}
        </div>
        <Select size={26} label="按工作区筛选" value={workspace} options={wsOptions} onChange={(v) => go({ workspace: v })} />
        <Select size={26} label="排序" value="updated" options={[{ value: 'updated', label: '排序：最近更新' }]} />
      </div>

      <div role="table" aria-label="任务">
        <div className="sg-alltasks__row is-head" role="row">
          <span role="columnheader">任务</span>
          <span role="columnheader">主责</span>
          <span role="columnheader">负责人</span>
          <span role="columnheader">状态</span>
          <span role="columnheader">更新</span>
        </div>
        {list.status === 'error' && <ErrorBanner message={errorMessage(list.error, '任务暂时无法读取')} onRetry={list.reload} />}
        {list.status === 'loading' && !list.data && <LoadingState rows={6} />}
        {list.data && list.data.items.length === 0 && (
          <EmptyState
            icon="square-check"
            title={q ? '没有找到相关任务' : scope === 'archived' ? '没有已归档的任务' : '这里还没有任务'}
            {...(q ? { description: '换个关键词试试' } : {})}
          />
        )}
        {list.data?.items.map((t) => {
          const st = taskState(t);
          return (
            <button key={t.id} type="button" role="row" className="sg-alltasks__row" onClick={() => navigate(`/tasks/${t.id}`)}>
              <span className="sg-alltasks__task" role="cell">
                <Icon
                  name={st.icon}
                  size={16}
                  {...(st.waiting ? { color: 'color.orange.660' as const } : st.tone === 'info' ? { color: 'accent' as const } : {})}
                  {...(st.icon === 'loader-ring' ? { className: 'sg-spin' } : {})}
                />
                <span className="sg-alltasks__title truncate">{t.title}</span>
                <span className="sg-alltasks__where truncate">{t.workspace?.name ?? '独立任务'}</span>
              </span>
              <span className="sg-alltasks__lead" role="cell">
                {t.lead_expert ? (
                  <>
                    <Avatar size={22} {...avatarOf(t.lead_expert.name, t.lead_expert.avatar_ref)} />
                    <span className="truncate">{t.lead_expert.name}</span>
                  </>
                ) : (
                  '—'
                )}
              </span>
              <span className="truncate" role="cell">{ownerText(t)}</span>
              <span role="cell">
                <StateLabel label={st.label} tone={st.tone} />
              </span>
              <span className="sg-alltasks__time" role="cell">{timeAgo(t.last_activity_at)}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
