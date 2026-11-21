import { useEffect, useMemo, useState } from 'react';
import { newClientRequestId } from '../../api/client';
import { automations, notifications } from '../../api/endpoints';
import { useShell } from '../../app/AppShell';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import type { IconName } from '../../components/Icon/Icon';
import { PageHeader } from '../../components/PageHeader/PageHeader';
import { EmptyState, ErrorBanner, LoadingState, errorMessage } from '../../components/States/States';
import { Tab } from '../../components/Tabs/Tab';
import { relativeTime } from '../../features/notifications/format';
import { NotificationRow } from '../../features/notifications/NotificationRow';
import '../../features/notifications/notifications.css';

/** Actual shape of GET app/notifications (types.ts only has a placeholder). */
interface NotificationItem {
  source: 'app' | 'billing';
  id: string;
  category: string;
  type: string;
  text: string;
  context_label: string;
  actor_expert: { id: string; name: string; avatar_ref?: string | null } | null;
  action: 'open' | 'run_now' | 'go_inbox' | string;
  target: { type: string; id: string } | null;
  occurred_at: string;
  read_at: string | null;
}
interface NotificationPage {
  items: NotificationItem[];
  offset: number;
  total: number;
  next_offset: number | null;
  unread_count: number;
}

type TabKey = 'all' | 'unread' | 'task_progress' | 'billing';
const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'unread', label: '未读' },
  { key: 'task_progress', label: '任务进展' },
  { key: 'billing', label: '账务' },
];

const ICONS: Record<string, IconName> = { task_progress: 'message-square', automation: 'timer', billing: 'bar-chart', workspace: 'layers' };

function targetPath(n: NotificationItem): string | null {
  if (n.source === 'billing') return '/usage';
  const t = n.target;
  if (n.action === 'go_inbox') return '/inbox';
  // Some seeded items carry an empty target id; those rows only mark read.
  if (!t || !t.id) return null;
  const id = encodeURIComponent(t.id);
  switch (t.type) {
    case 'task':
      return `/tasks/${id}`;
    case 'workspace':
      return `/workspaces/${id}`;
    case 'workspace_members':
      return `/workspaces/${id}/members`;
    case 'automation':
      return `/automations/${id}/runs`;
    case 'artifact':
      return `/artifacts/${id}`;
    case 'expert':
      return `/experts/${id}`;
    case 'org_quota':
      return '/settings/org/quota';
    default:
      return null;
  }
}

// context_label for scheduled-run items already carries the planned time (“11月1日 10:00 · …”).
const HAS_DATE = /\d+月\d+日/;

export default function Screen({ query, navigate }: ScreenProps) {
  const { toast, refresh } = useShell();
  const initial = (query.get('tab') as TabKey | null) ?? 'all';
  const [tab, setTab] = useState<TabKey>(TABS.some((t) => t.key === initial) ? initial : 'all');
  const [more, setMore] = useState<NotificationItem[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [readIds, setReadIds] = useState<Set<string>>(new Set());
  const [running, setRunning] = useState<Record<string, 'busy' | 'done'>>({});
  const [markingAll, setMarkingAll] = useState(false);

  const filter = useMemo(
    () => (tab === 'unread' ? { unread: 1 } : tab === 'all' ? {} : { category: tab }),
    [tab],
  );
  const page = useApiQuery((signal) => notifications.getNotifications(filter, signal) as Promise<NotificationPage>, [filter]);
  useEffect(() => {
    setMore([]);
    setNextOffset(page.data?.next_offset ?? null);
  }, [page.data]);

  const items = [...(page.data?.items ?? []), ...more];
  const unreadCount = page.data ? Math.max(0, page.data.unread_count - items.filter((n) => !n.read_at && readIds.has(n.id)).length) : undefined;
  const isUnread = (n: NotificationItem) => !n.read_at && !readIds.has(n.id);

  const markRead = (n: NotificationItem) => {
    if (!isUnread(n)) return;
    setReadIds((s) => new Set(s).add(n.id));
    const body = n.source === 'billing' ? { billing_ids: [n.id] } : { app_ids: [n.id] };
    notifications
      .postNotificationsRead(body)
      .then(refresh)
      .catch((err: unknown) => {
        setReadIds((s) => {
          const copy = new Set(s);
          copy.delete(n.id);
          return copy;
        });
        toast({ tone: 'failure', message: errorMessage(err, '标记已读失败，请重试') });
      });
  };

  const open = (n: NotificationItem) => {
    markRead(n);
    const to = targetPath(n);
    if (to) navigate(to);
  };

  const runNow = async (n: NotificationItem) => {
    if (!n.target?.id) return;
    markRead(n);
    setRunning((r) => ({ ...r, [n.id]: 'busy' }));
    try {
      await automations.postAutomationsRunNow(n.target.id, { client_request_id: newClientRequestId() });
      setRunning((r) => ({ ...r, [n.id]: 'done' }));
    } catch (err) {
      setRunning(({ [n.id]: _drop, ...r }) => r);
      toast({ tone: 'failure', message: errorMessage(err, '立即运行失败，请重试'), actionLabel: '重试', onAction: () => void runNow(n) });
    }
  };

  const markAll = async () => {
    setMarkingAll(true);
    try {
      await notifications.postNotificationsRead({ all: true });
      setReadIds(new Set(items.map((n) => n.id)));
      page.reload();
      refresh();
    } catch (err) {
      toast({ tone: 'failure', message: errorMessage(err, '全部标为已读失败，请重试'), actionLabel: '重试', onAction: () => void markAll() });
    } finally {
      setMarkingAll(false);
    }
  };

  const loadMore = async () => {
    if (nextOffset === null) return;
    setLoadingMore(true);
    try {
      const res = (await notifications.getNotifications({ ...filter, offset: nextOffset })) as NotificationPage;
      setMore((m) => [...m, ...res.items]);
      setNextOffset(res.next_offset);
    } catch (err) {
      toast({ tone: 'failure', message: errorMessage(err, '更多通知读取失败'), actionLabel: '重试', onAction: () => void loadMore() });
    } finally {
      setLoadingMore(false);
    }
  };

  const meta = (n: NotificationItem) => {
    const when = HAS_DATE.test(n.context_label) ? '' : relativeTime(n.occurred_at);
    return [n.context_label, when].filter(Boolean).join(' · ');
  };

  let body;
  if (page.status === 'loading' && !page.data) body = <LoadingState rows={4} />;
  else if (page.status === 'error' && !page.data)
    body = (
      <div className="sg-notifpage__state">
        <ErrorBanner message={errorMessage(page.error, '通知暂时无法读取')} onRetry={page.reload} />
      </div>
    );
  else if (items.length === 0)
    body = <EmptyState icon="bell" title={tab === 'unread' ? '没有未读通知' : '还没有通知'} description="任务进展、自动化与账务提醒会出现在这里" />;
  else
    body = (
      <>
        {page.status === 'error' && <ErrorBanner message={errorMessage(page.error, '通知刷新失败')} onRetry={page.reload} />}
        <ul className="sg-notiflist">
          {items.map((n) => {
            const action = n.action === 'run_now' ? '立即运行' : n.action === 'go_inbox' ? '去收件' : undefined;
            const run = running[n.id];
            return (
              <NotificationRow
                key={`${n.source}:${n.id}`}
                text={n.text}
                meta={meta(n)}
                unread={isUnread(n)}
                icon={ICONS[n.category] ?? 'bell'}
                {...(n.actor_expert ? { actor: { name: n.actor_expert.name, avatar: n.actor_expert.avatar_ref ?? null } } : {})}
                {...(action ? { actionLabel: action } : {})}
                actionBusy={run === 'busy'}
                actionDone={run === 'done'}
                onOpen={() => open(n)}
                onAction={() => (n.action === 'run_now' ? void runNow(n) : open(n))}
              />
            );
          })}
        </ul>
        {nextOffset !== null && (
          <div className="sg-notifpage__more">
            <Button variant="ghost" size={32} label="加载更多" busy={loadingMore} onClick={loadMore} />
          </div>
        )}
      </>
    );

  return (
    <div className="sg-notifpage">
      <PageHeader
        title="通知中心"
        description="只放被动提醒；需要你决定的事项都在收件中。提醒方式在 设置 › 通知 中调整"
        actions={
          <>
            <Button variant="ghost" size={32} label="全部标为已读" busy={markingAll} disabled={!unreadCount} onClick={markAll} />
            <Button variant="secondary" size={32} icon="settings" label="通知设置" onClick={() => navigate('/settings/notifications')} />
          </>
        }
      />
      <div className="sg-tabs" role="tablist" aria-label="通知分类">
        {TABS.map((t) => (
          <Tab
            key={t.key}
            size={37}
            label={t.label}
            selected={tab === t.key}
            {...(t.key === 'unread' && unreadCount !== undefined ? { count: unreadCount } : {})}
            onSelect={() => {
              setTab(t.key);
              navigate(t.key === 'all' ? '/notifications' : `/notifications?tab=${t.key}`, { replace: true });
            }}
          />
        ))}
      </div>
      {body}
    </div>
  );
}
