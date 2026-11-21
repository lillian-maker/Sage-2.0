import { useEffect, useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { PageHeader } from '../../components/PageHeader/PageHeader';
import { SegmentedControl } from '../../components/SegmentedControl/SegmentedControl';
import { EmptyState, ErrorBanner, LoadingState, errorMessage } from '../../components/States/States';
import type { TagProps } from '../../components/Tag/Tag';
import { avatarOf, relativeTime, taskApi, type ActiveTask } from '../../features/task/model';
import { ActiveTaskRow } from '../../features/tasks/ActiveTaskRow';
import './active.css';

type Filter = 'all' | 'stuck' | 'awaiting_user';
const FILTERS: Filter[] = ['all', 'stuck', 'awaiting_user'];

function stateTag(t: ActiveTask): TagProps {
  const p = t.progress;
  if (t.status === 'awaiting_user') return { label: '等你处理 · 去收件', tone: 'warning', size: 20 };
  if (t.status === 'stuck') return { label: '卡住 · 需要你决定', tone: 'danger', size: 20 };
  if (t.status === 'paused') return { label: '已暂停', tone: 'warning', size: 20 };
  return { label: p.total > 0 ? `进行中 ${p.done}/${p.total}` : '进行中', tone: 'info', size: 20 };
}

function subtitle(t: ActiveTask): string {
  const where = t.workspace?.name ?? '未归入工作区';
  const step = t.current_step;
  if (t.status === 'stuck' && t.stuck_rounds > 0) return `${where} · 连续 ${t.stuck_rounds} 轮没有进展`;
  if (!step) return where;
  if (step.activity) return `${where} · ${step.status === 'running' ? '正在' + step.activity.replace(/^正在/, '') : step.activity}`;
  return `${where} · 第 ${step.order} 步 · ${step.title}`;
}

export default function Screen({ query, navigate }: ScreenProps) {
  const raw = query.get('filter') as Filter | null;
  const filter: Filter = raw && FILTERS.includes(raw) ? raw : 'all';
  const [pages, setPages] = useState(1);
  useEffect(() => setPages(1), [filter]);

  const list = useApiQuery(async (signal) => {
    const first = await taskApi.active(filter, 0, signal);
    let page = first;
    const items = [...first.items];
    for (let i = 1; i < pages && page.next_offset != null; i += 1) {
      page = await taskApi.active(filter, page.next_offset, signal);
      items.push(...page.items);
    }
    return { ...first, items, next_offset: page.next_offset };
  }, [filter, pages]);

  const counts = list.data?.counts;
  const label = (name: string, n: number | undefined) => (n === undefined ? name : `${name} ${n}`);
  const options = [
    { value: 'all' as const, label: label('全部', counts?.all) },
    { value: 'stuck' as const, label: label('卡住', counts?.stuck) },
    { value: 'awaiting_user' as const, label: label('等你处理', counts?.awaiting_user) },
  ];

  return (
    <div className="sg-active">
      <PageHeader
        title="进行中"
        description="你交办的事进展如何；需要你决定的事项在收件中"
        actions={
          <SegmentedControl
            label="筛选进行中的任务"
            options={options}
            value={filter}
            onChange={(v) => navigate(v === 'all' ? '/tasks/active' : `/tasks/active?filter=${v}`, { replace: true })}
          />
        }
      />
      <div className="sg-active__list">
        {list.status === 'error' && <ErrorBanner message={errorMessage(list.error, '进行中的任务暂时无法读取')} onRetry={list.reload} />}
        {list.status === 'loading' && !list.data && <LoadingState rows={4} />}
        {list.data && list.data.items.length === 0 && (
          <EmptyState
            icon="circle-progress"
            title={filter === 'all' ? '没有进行中的任务' : filter === 'stuck' ? '没有卡住的任务' : '没有等你处理的任务'}
            description={filter === 'all' ? '交办一件事，Sage 会在这里显示进展' : undefined}
            {...(filter === 'all' ? { action: { label: '新任务', onClick: () => navigate('/tasks/new') } } : {})}
          />
        )}
        {list.data?.items.map((t) => (
          <ActiveTaskRow
            key={t.id}
            title={t.source_automation ? `${t.title}（自动化）` : t.title}
            subtitle={subtitle(t)}
            {...(t.progress.total > 0 ? { progress: t.progress.done / t.progress.total } : {})}
            state={stateTag(t)}
            waiting={t.status === 'awaiting_user' || t.status === 'stuck'}
            experts={t.experts.map((e) => avatarOf(e.name, e.avatar_ref))}
            time={relativeTime(t.last_activity_at)}
            onOpen={() => navigate(`/tasks/${t.id}`)}
            {...(t.status === 'awaiting_user' ? { onState: () => navigate('/inbox') } : {})}
          />
        ))}
        {list.data?.next_offset != null && (
          <div className="sg-active__more">
            <Button variant="ghost" size={30} label="加载更多" busy={list.status === 'loading'} onClick={() => setPages((p) => p + 1)} />
          </div>
        )}
      </div>
    </div>
  );
}
