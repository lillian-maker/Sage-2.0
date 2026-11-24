import { useEffect, useMemo, useState, type ReactNode } from 'react';
import type { ScreenProps } from '../../app/router';
import { useShell } from '../../app/AppShell';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Avatar } from '../../components/Avatar/Avatar';
import { Icon } from '../../components/Icon/Icon';
import { SegmentedControl } from '../../components/SegmentedControl/SegmentedControl';
import { EmptyState, ErrorBanner, LoadingState, errorMessage } from '../../components/States/States';
import { Tab } from '../../components/Tabs/Tab';
import { Tag } from '../../components/Tag/Tag';
import { inboxApi, type InboxItem, type ItemGroup, type Mention, type SourceKind } from '../../features/inbox/api';
import { DecisionDetail, DecisionRow, type DecidedSnapshot } from '../../features/inbox/DecidePanel';
import { RECOVERY_PHASES, RecoveryDemo, type RecoveryPhase } from '../../features/inbox/RecoveryDemo';
import { GROUPS, GROUP_TAG, SOURCE_LABEL, shortTime } from '../../features/inbox/format';
import { HandoverRow } from '../../features/inbox/HandoverRow';
import { InboxCard } from '../../features/inbox/InboxCard';
import { ItemDetail } from '../../features/inbox/ItemDetail';
import '../../features/inbox/inbox.css';
import { NotificationBell } from '../../components/NotificationBell/NotificationBell';

type Filter = 'all' | 'decide' | 'mentions' | 'handover';
type View = 'board' | 'list';

// 飞书 and 企微 share one rail entry (board 47); its count covers both chat sources.
const SOURCE_ENTRIES: Array<{ kinds: SourceKind[]; label: string }> = [
  { kinds: ['company_email'], label: SOURCE_LABEL.company_email },
  { kinds: ['amazon_buyer_messages'], label: SOURCE_LABEL.amazon_buyer_messages },
  { kinds: ['store_notifications'], label: SOURCE_LABEL.store_notifications },
  { kinds: ['feishu', 'wecom'], label: '飞书 / 企微' },
];

function mentionTarget(m: Mention): string {
  if (m.target?.type === 'task' && m.target.id) return `/tasks/${m.target.id}`;
  if (m.target?.type === 'member_removal') return '/inbox?filter=handover';
  return '/inbox';
}

function MentionsList({ navigate }: { navigate: (to: string) => void }) {
  const page = useApiQuery((s) => inboxApi.mentions(s), []);
  if (page.status === 'loading' && !page.data) return <LoadingState rows={3} />;
  if (page.status === 'error' && !page.data) return <ErrorBanner message={errorMessage(page.error, '@与指派暂时无法读取')} onRetry={page.reload} />;
  const items = page.data?.items ?? [];
  if (items.length === 0) return <EmptyState icon="contact" title="没有 @ 或指派给你的消息" />;
  return (
    <div className="sg-handover">
      {page.status === 'error' && <ErrorBanner message={errorMessage(page.error, '刷新失败')} onRetry={page.reload} />}
      {items.map((m) => (
        <div key={m.id} className="sg-handover__row">
          <div className="sg-handover__row-text">
            <span className="sg-handover__row-title">{m.text}</span>
            <span className="sg-handover__row-meta">{[m.context_label, shortTime(m.occurred_at)].filter(Boolean).join(' · ')}</span>
          </div>
          <button type="button" className="sg-state__action" onClick={() => navigate(mentionTarget(m))}>
            查看
          </button>
        </div>
      ))}
    </div>
  );
}

function HandoverList({ navigate, toast }: { navigate: (to: string) => void; toast: (message: string, retry?: () => void) => void }) {
  const page = useApiQuery((s) => inboxApi.handover(s), []);
  if (page.status === 'loading' && !page.data) return <LoadingState rows={3} />;
  if (page.status === 'error' && !page.data) return <ErrorBanner message={errorMessage(page.error, '交接暂时无法读取')} onRetry={page.reload} />;
  const groups = page.data?.groups ?? [];
  if (groups.length === 0) return <EmptyState icon="users" title="现在没有待接管的交接" description="同事离开时转给你的任务、审批与自动化会出现在这里" />;
  return (
    <div className="sg-handover">
      <h2 className="sg-handover__title">交接给你的事项</h2>
      {page.status === 'error' && <ErrorBanner message={errorMessage(page.error, '刷新失败')} onRetry={page.reload} />}
      {groups.map((g) => {
        const tasks = g.items.filter((item) => item.kind === 'task');
        const others = g.items.filter((item) => item.kind !== 'task');
        const removedAt = new Date(g.removed_at);
        return (
          <section key={g.removal_id} className="sg-handover__group">
            <div className="sg-indetail__section-label">
              来自 {g.from_member.name}（已离职）· {removedAt.getMonth() + 1}月{removedAt.getDate()}日
            </div>
            {tasks.length > 0 && (
              <>
                <div className="sg-handover__label">任务 · 确认后以你的身份继续，之后的消耗计入你的额度</div>
                {tasks.map((item) => (
                  <HandoverRow key={item.id} item={item} onChanged={page.reload} onNavigate={navigate} onToast={toast} />
                ))}
              </>
            )}
            {others.length > 0 && <div className="sg-handover__label">其他</div>}
            {others.map((item) => (
              <HandoverRow key={item.id} item={item} onChanged={page.reload} onNavigate={navigate} onToast={toast} />
            ))}
            {g.revoked_connection_count > 0 && (
              <div className="sg-handover__foot">
                原成员授权的 {g.revoked_connection_count} 个外部连接器已撤销；重新启用自动化时，如需连接会提示你重新授权。
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

function DecideList({
  header,
  navigate,
  onChanged,
  demo,
}: {
  header: ReactNode;
  navigate: (to: string) => void;
  onChanged: () => void;
  demo: RecoveryPhase | null;
}) {
  const page = useApiQuery((s) => inboxApi.decisions(s), []);
  const [selected, setSelected] = useState<string | null>(null);
  const [decided, setDecided] = useState<DecidedSnapshot | null>(null);
  if (demo) return <RecoveryDemo initial={demo} navigate={navigate} header={header} />;
  const items = page.data?.items ?? [];
  const showingDecided = decided !== null && selected === decided.decision.id;
  const current = showingDecided ? null : (items.find((d) => d.id === selected) ?? items[0] ?? null);

  let list;
  if (page.status === 'loading' && !page.data) list = <LoadingState rows={3} />;
  else if (page.status === 'error' && !page.data) list = <ErrorBanner message={errorMessage(page.error, '需要我决定暂时无法读取')} onRetry={page.reload} />;
  else if (items.length === 0 && !showingDecided)
    list = <EmptyState icon="check" title="没有等你决定的事" description="审批、验收、邀请与待补充会出现在这里" />;
  else
    list = (
      <>
        {page.status === 'error' && <ErrorBanner message={errorMessage(page.error, '刷新失败')} onRetry={page.reload} />}
        {items.map((d) => (
          <DecisionRow key={d.id} decision={d} selected={current?.id === d.id} onSelect={() => setSelected(d.id)} />
        ))}
      </>
    );

  const shown = showingDecided ? decided.decision : current;
  return (
    <>
      <div className="sg-inq__side">
        {header}
        <div className="sg-inq__list">{list}</div>
      </div>
      <section className="sg-inq__detail" aria-label="决定详情">
        {shown && (
          <DecisionDetail
            key={shown.id}
            decision={shown}
            decided={showingDecided ? decided : null}
            onChanged={() => {
              page.reload();
              onChanged();
            }}
            onDecided={(snapshot) => {
              setDecided(snapshot);
              setSelected(snapshot.decision.id);
            }}
            onOpenTask={(taskId) => navigate(`/tasks/${taskId}?from=approval`)}
            onOpenRun={(automationId, runId) => navigate(`/automations/${automationId}/runs?run=${runId}`)}
          />
        )}
      </section>
    </>
  );
}

export default function Screen({ query, navigate }: ScreenProps) {
  const shell = useShell();
  const filterParam = query.get('filter');
  const filter: Filter = filterParam === 'decide' || filterParam === 'handover' ? filterParam : filterParam === 'mentions' ? 'mentions' : 'all';
  const view: View = query.get('view') === 'list' ? 'list' : 'board';
  const sourceFilter = query.get('source');
  const itemParam = query.get('item');
  const [selected, setSelected] = useState<string | null>(itemParam);
  const [cursor, setCursor] = useState(0);

  const boardQuery = useMemo(
    () => ({ ...(sourceFilter ? { source: sourceFilter } : {}) }),
    [sourceFilter],
  );
  const page = useApiQuery((s) => inboxApi.items(boardQuery, s), [boardQuery]);
  const counts = page.data?.counts;

  useEffect(() => {
    setSelected(itemParam);
  }, [itemParam]);

  // Esc closes the detail panel; in the list view ↑/↓ move, Enter opens, E hands off and # ignores.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && selected) {
        e.preventDefault();
        closeDetail();
        return;
      }
      const target = e.target as HTMLElement | null;
      if (view !== 'list' || filter !== 'all' || target?.closest('input, textarea, [contenteditable="true"]')) return;
      const current = items[Math.min(cursor, items.length - 1)];
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        setCursor((c) => Math.max(0, Math.min(items.length - 1, c + (e.key === 'ArrowDown' ? 1 : -1))));
      } else if (e.key === 'Enter' && current) {
        e.preventDefault();
        openItem(current.id);
      } else if ((e.key === 'e' || e.key === 'E') && current && (current.group === 'todo' || current.group === 'pending_confirmation')) {
        e.preventDefault();
        void quick.submit(() => inboxApi.handOff(current.id, current.suggested_experts[0]?.id).then(() => navigate(`/inbox/${current.id}/task`)));
      } else if (e.key === '#' && current && (current.group === 'todo' || current.group === 'pending_confirmation')) {
        e.preventDefault();
        void quick.submit(() => inboxApi.ignore(current.id).then(() => page.reload()));
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const setFilter = (f: Filter) => {
    setSelected(null);
    navigate(f === 'all' ? '/inbox' : `/inbox?filter=${f}`, { replace: false });
  };
  const setView = (v: View) => navigate(v === 'board' ? '/inbox' : '/inbox?view=list', { replace: true });
  const closeDetail = () => {
    setSelected(null);
    if (itemParam) navigate('/inbox', { replace: true });
  };
  const openItem = (id: string) => setSelected(id);

  const quick = useSubmit(async (run: () => Promise<unknown>) => {
    try {
      await run();
      shell.refresh();
    } catch {
      toastFail('操作没有成功，请重试');
    }
  });
  const items = page.data?.items ?? [];
  const byGroup = useMemo(() => {
    const map = new Map<ItemGroup, InboxItem[]>();
    for (const g of GROUPS) map.set(g.key, []);
    for (const it of items) {
      const list = map.get(it.group as ItemGroup);
      if (list) list.push(it);
    }
    return map;
  }, [items]);

  const selectedItem = items.find((i) => i.id === selected);

  const toastFail = (message: string, retry?: () => void) =>
    shell.toast({ tone: 'failure', message, ...(retry ? { actionLabel: '重试', onAction: retry } : {}) });

  const rail = (
    <nav className="sg-inbox__rail" aria-label="收件筛选">
      <button type="button" className={`sg-inbox__nav${filter === 'all' && !sourceFilter ? ' is-selected' : ''}`} onClick={() => setFilter('all')}>
        <Icon name="archive" size={15} />
        全部
        {counts && <span className="sg-inbox__nav-count">{counts.all}</span>}
      </button>
      <button type="button" className={`sg-inbox__nav${filter === 'decide' ? ' is-selected' : ''}`} onClick={() => setFilter('decide')}>
        <Icon name="circle-pending" size={15} />
        需要我决定
        {counts && <span className="sg-inbox__nav-count">{counts.decisions}</span>}
      </button>
      <button type="button" className={`sg-inbox__nav${filter === 'mentions' ? ' is-selected' : ''}`} onClick={() => setFilter('mentions')}>
        <Icon name="contact" size={15} />
        @与指派
        {counts && <span className="sg-inbox__nav-count">{counts.mentions}</span>}
      </button>
      <button type="button" className={`sg-inbox__nav${filter === 'handover' ? ' is-selected' : ''}`} onClick={() => setFilter('handover')}>
        <Icon name="users" size={15} />
        交接
      </button>
      <div className="sg-inbox__rail-label">外部消息 · 来源</div>
      {SOURCE_ENTRIES.map(({ kinds, label }) => {
        const n = kinds.reduce((sum, kind) => sum + (counts?.sources[kind] ?? 0), 0);
        return (
          <button
            key={kinds[0]}
            type="button"
            className={`sg-inbox__nav${filter === 'all' && sourceFilter && kinds.includes(sourceFilter as SourceKind) ? ' is-selected' : ''}`}
            onClick={() => {
              setSelected(null);
              navigate(`/inbox?source=${kinds[0]}`);
            }}
          >
            <Icon name="message-square" size={15} />
            {label}
            <span className="sg-inbox__nav-count">{n}</span>
          </button>
        );
      })}
      <button type="button" className="sg-inbox__nav" onClick={() => navigate('/inbox/sources')}>
        <Icon name="settings" size={15} />
        来源设置
      </button>
    </nav>
  );

  const demoParam = query.get('demo') as RecoveryPhase | null;
  const demo = demoParam && RECOVERY_PHASES.includes(demoParam) ? demoParam : null;

  // 638:2: 需要我决定 and @与指派 use a title + horizontal tabs header over a list / detail split; the
  // demo frames (3128:43, 3311:681) show the tabs without counts.
  if (filter === 'decide' || filter === 'mentions') {
    const tab = (label: string, selectedTab: boolean, n: number | undefined, onSelect: () => void) => (
      <Tab size={37} label={label} selected={selectedTab} onSelect={onSelect} {...(!demo && n !== undefined ? { count: n } : {})} />
    );
    const header = (
      <header className="sg-inq__head">
        <h1 className="sg-inq__h1">收件</h1>
        <p className="sg-inq__subtitle">所有在等你处理的事，都在这里</p>
        <div className="sg-tabs sg-inq__tabs" role="tablist" aria-label="收件">
          {tab('全部', false, counts?.all, () => setFilter('all'))}
          {tab('需要我决定', filter === 'decide', counts?.decisions, () => setFilter('decide'))}
          {tab('@ 与指派', filter === 'mentions', counts?.mentions, () => setFilter('mentions'))}
          {tab('外部消息', false, counts?.external, () => setFilter('all'))}
        </div>
      </header>
    );
    return (
      <div className={`sg-inbox sg-inbox--tabs${filter === 'mentions' ? ' is-single' : ''}`}>
        {filter === 'decide' ? (
          <DecideList
            header={header}
            navigate={navigate}
            onChanged={() => {
              shell.refresh();
              page.reload();
            }}
            demo={demo}
          />
        ) : (
          <div className="sg-inq__side">
            {header}
            <MentionsList navigate={navigate} />
          </div>
        )}
      </div>
    );
  }

  let body;
  if (filter === 'handover') body = <HandoverList navigate={navigate} toast={toastFail} />;
  else if (page.status === 'loading' && !page.data) body = <LoadingState rows={4} />;
  else if (page.status === 'error' && !page.data)
    body = <ErrorBanner message={errorMessage(page.error, '收件暂时无法读取')} onRetry={page.reload} />;
  else if (items.length === 0)
    body = <EmptyState icon="archive" title="收件箱是空的" description={sourceFilter ? '这个来源暂时没有新消息' : '连接外部来源后，需要你处理的消息会出现在这里'} action={{ label: '管理来源', onClick: () => navigate('/inbox/sources') }} />;
  else if (view === 'list')
    body = (
      <div className="sg-inbox__list">
        {page.status === 'error' && <ErrorBanner message={errorMessage(page.error, '刷新失败')} onRetry={page.reload} />}
        <p className="sg-inbox__hint">按时间倒序；判断把握低于 50% 的进入“待确认”</p>
        <table className="sg-inbox__table">
          <thead>
            <tr>
              <th>消息</th>
              <th>状态</th>
              <th>判断把握</th>
              <th>建议交给</th>
              <th>时间</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it, i) => (
              <tr key={it.id} className={i === cursor ? 'is-cursor' : undefined} onClick={() => { setCursor(i); openItem(it.id); }}>
                <td>
                  <div className="sg-inbox__cell-title truncate">{it.title}</div>
                  <div className="sg-inbox__cell-meta">{it.sender_label}</div>
                </td>
                <td>
                  <Tag {...GROUP_TAG[it.group]} />
                </td>
                <td className="sg-inbox__cell-meta">{it.confidence !== null ? `${it.confidence}%` : '—'}</td>
                <td>
                  <span className="sg-incard__row">
                    {it.suggested_experts.slice(0, 2).map((e) => (
                      <Avatar key={e.id} size={18} alt={e.name} src={e.avatar_ref ?? null} ring />
                    ))}
                    <span className="sg-inbox__cell-meta">{it.suggested_experts.map((e) => e.name).join('、') || '—'}</span>
                  </span>
                </td>
                <td className="sg-inbox__cell-meta">{shortTime(it.received_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="sg-inbox__hint sg-inbox__keys">↑ / ↓ 移动 · Enter 打开 · E 交给专家处理 · # 忽略</p>
      </div>
    );
  else
    body = (
      <div className="sg-inbox__board">
        {GROUPS.map((g) => {
          const list = byGroup.get(g.key) ?? [];
          return (
            <section key={g.key} className="sg-inbox__col" aria-label={g.label}>
              <div className="sg-inbox__col-head">
                {g.label}
                <span className="sg-inbox__col-count">{list.length}</span>
              </div>
              <div className="sg-inbox__col-body">
                {list.map((it) => (
                  <InboxCard key={it.id} item={it} active={selected === it.id} onOpen={() => openItem(it.id)} />
                ))}
                {list.length === 0 && <div className="sg-inbox__col-head" style={{ padding: '8px 4px' }}>没有消息</div>}
              </div>
            </section>
          );
        })}
      </div>
    );

  return (
    <div className="sg-inbox">
      {rail}
      <div className="sg-inbox__main">
        <div className="sg-inbox__toolbar">
          <div className="sg-tabs" role="tablist" aria-label="收件">
            <Tab size={37} label="收件" selected />
          </div>
          <span className="sg-inbox__subtitle">
            {filter === 'all' ? '外部渠道里需要处理的消息，会自动整理成待办；回复只生成草稿，不会替你发送' : '所有在等你处理的事，都在这里'}
          </span>
          {filter === 'all' && (
            <SegmentedControl
              label="视图"
              options={[
                { value: 'board', label: '看板' },
                { value: 'list', label: '列表' },
              ]}
              value={view}
              onChange={(v) => setView(v)}
            />
          )}
          {filter === 'all' && (
            <button type="button" className="sg-state__action sg-inbox__connect" onClick={() => navigate('/inbox/setup')}>
              接入来源
            </button>
          )}
          <span className="sg-inbox__bell">
            <NotificationBell onClick={() => navigate('/notifications')} />
          </span>
        </div>
        {body}
      </div>
      {selected && (
        <aside className="sg-indetail" aria-label="消息详情">
          {selectedItem ? (
            <ItemDetail
              id={selected}
              onClose={closeDetail}
              onChanged={() => {
                page.reload();
                shell.refresh();
              }}
              onHandedOff={(itemId) => navigate(`/inbox/${itemId}/task`)}
              onOpenTask={(taskId) => navigate(`/tasks/${taskId}`)}
              onDiscuss={(it) => navigate(`/tasks/new?text=${encodeURIComponent(`关于收件消息“${it.title}”：`)}${it.workspace ? `&workspace=${it.workspace.id}` : ''}`)}
              onError={toastFail}
            />
          ) : page.status === 'ready' ? (
            <EmptyState icon="message-square" title="这条消息不在当前列表里" action={{ label: '关闭', onClick: closeDetail }} />
          ) : (
            <ItemDetail
              id={selected}
              onClose={closeDetail}
              onChanged={() => {
                page.reload();
                shell.refresh();
              }}
              onHandedOff={(itemId) => navigate(`/inbox/${itemId}/task`)}
              onOpenTask={(taskId) => navigate(`/tasks/${taskId}`)}
              onDiscuss={(it) => navigate(`/tasks/new?text=${encodeURIComponent(`关于收件消息“${it.title}”：`)}${it.workspace ? `&workspace=${it.workspace.id}` : ''}`)}
              onError={toastFail}
            />
          )}
        </aside>
      )}
    </div>
  );
}
