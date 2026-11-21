import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { inbox, tasks, workspaces } from '../api/endpoints';
import { currentSession, onSessionChange, type AuthSession } from '../api/client';
import type { GetTasksResponse, GetWorkspacesResponse } from '../api/types';
import { AppSidebar, type SidebarNav, type SidebarWorkspace } from '../components/AppSidebar/AppSidebar';
import { Popover } from '../components/Menu/MenuItem';
import { UsagePopover, useUsageSnapshot } from '../features/billing/UsagePopover';
import { ConfirmDialog } from '../components/ConfirmDialog/ConfirmDialog';
import { BellSlotProvider, NotificationBell } from '../components/NotificationBell/NotificationBell';
import { Toast, errorMessage } from '../components/States/States';
import { useDesktopInfo } from '../features/desktop/bridge';
import { EditWorkspaceDialog, MobileAppCard, UpdatePill, WorkspaceHoverCard, useHoverIntent } from '../features/desktop/ShellDesktop';
import { GlobalSearch } from '../features/search/GlobalSearch';
import { ShellMenus, type ShellMenuState, type ToastOptions } from '../features/shell/ShellMenus';
import { routes } from '../routes';
import { breakpoints } from '../tokens/tokens';
import { useApiQuery } from './useApi';
import { createMatcher, navigate } from './router';
import './AppShell.css';

const routeTitle = createMatcher(routes.map((r) => ({ path: r.path, value: r.title })));

/** Label of the search layer's back link: “返回新任务”, or the page / workspace / task it was opened from. */
function backLabelFor(pathname: string, list: SidebarWorkspace[]): string {
  const [first, second] = pathname.split('/').filter(Boolean);
  if (first === 'tasks' && second === 'new') return '返回新任务';
  if (first === 'tasks' && second) {
    const t = list.flatMap((w) => w.tasks).find((x) => x.id === second);
    if (t?.title) return t.title;
  }
  if (first === 'workspaces' && second) {
    const w = list.find((x) => x.id === second);
    if (w?.name) return w.name;
  }
  const title = routeTitle(pathname)?.value.replace(/^\d+\s*/, '').split(' · ')[0];
  return title ? `返回${title}` : '返回';
}

const NAV_BY_SEGMENT: Record<string, SidebarNav> = {
  search: 'search',
  experts: 'experts',
  knowledge: 'knowledge',
  extensions: 'extensions',
  inbox: 'inbox',
  artifacts: 'artifacts',
  automations: 'automations',
};

function activeFromPath(pathname: string): { nav?: SidebarNav; workspaceId?: string; taskId?: string } {
  const [first, second] = pathname.split('/').filter(Boolean);
  if (first === 'tasks' && second === 'active') return { nav: 'inProgress' };
  if (first === 'tasks' && second && second !== 'new') return { taskId: second };
  if (first === 'workspaces' && second && second !== 'new') return { workspaceId: second };
  const nav = first ? NAV_BY_SEGMENT[first] : undefined;
  return nav ? { nav } : {};
}

export function toSidebarWorkspaces(data: GetWorkspacesResponse | undefined): SidebarWorkspace[] {
  return (data?.items ?? []).flatMap((w) =>
    w.id
      ? [
          {
            id: w.id,
            name: w.name ?? '',
            kind: 'workspace' as const,
            pinned: !!w.pinned_at,
            taskCount: w.task_count ?? 0,
            tasks: (w.recent_tasks ?? []).flatMap((t) =>
              t.id ? [{ id: t.id, title: t.title ?? '', ...(t.unread ? { dot: 'accent' as const } : {}) }] : [],
            ),
          },
        ]
      : [],
  );
}

/** Board 24 (727:7): tasks without a workspace form their own “未关联工作区” group, 3 recent rows like a workspace. */
export function toUnassignedGroup(data: GetTasksResponse | undefined): SidebarWorkspace[] {
  const all = (data?.items ?? []).flatMap((t) =>
    t.id ? [{ id: t.id, title: t.title ?? '', ...((t as { unread?: boolean }).unread ? { dot: 'accent' as const } : {}) }] : [],
  );
  if (all.length === 0) return [];
  return [
    {
      id: 'unassigned',
      name: '未关联工作区',
      kind: 'unassigned',
      pinned: false,
      taskCount: data?.total ?? all.length,
      tasks: all.slice(0, 3),
      expandedTasks: all,
    },
  ];
}

function useNarrow(width: number): boolean {
  const query = `(max-width: ${width - 1}px)`;
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const fn = () => setNarrow(mq.matches);
    mq.addEventListener('change', fn);
    return () => mq.removeEventListener('change', fn);
  }, [query]);
  return narrow;
}

interface ShellContextValue {
  /** Re-reads sidebar counts and workspaces, e.g. after a screen creates or archives a task. */
  refresh: () => void;
  /** Bottom-right 撤销 / 失败提示条 (board 82); a new toast replaces the current one. */
  toast: (t: ToastOptions) => void;
  /** Opens the global search layer (board 26). */
  openSearch: () => void;
}
const ShellContext = createContext<ShellContextValue>({ refresh: () => undefined, toast: () => undefined, openSearch: () => undefined });
export const useShell = () => useContext(ShellContext);

export function AppShell({ pathname, children }: { pathname: string; children: ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(currentSession);
  useEffect(() => onSessionChange(setSession), []);
  const active = activeFromPath(pathname);

  const activeTasks = useApiQuery((signal) => tasks.getTasksActive(undefined, signal), []);
  const decisions = useApiQuery((signal) => inbox.getInboxDecisions(undefined, signal), []);
  const ws = useApiQuery((signal) => workspaces.getWorkspaces(undefined, signal), []);
  const unassigned = useApiQuery((signal) => tasks.getTasks({ workspace: 'none', sort: 'updated' }, signal), []);
  const { reload: r1 } = activeTasks;
  const { reload: r2 } = decisions;
  const { reload: r3 } = ws;
  const { reload: r4 } = unassigned;
  const refresh = useCallback(() => {
    r1();
    r2();
    r3();
    r4();
  }, [r1, r2, r3, r4]);

  useEffect(() => {
    window.addEventListener('focus', refresh);
    return () => window.removeEventListener('focus', refresh);
  }, [refresh]);

  const narrow = useNarrow(breakpoints.sidebarRail);
  const [userCollapsed, setUserCollapsed] = useState(false);
  const collapsed = narrow || userCollapsed;

  const user = session?.user;
  const name = user?.display_name || user?.masked_identifier || '';
  const [toastState, setToastState] = useState<(ToastOptions & { key: number }) | null>(null);
  const toast = useCallback((t: ToastOptions) => setToastState({ ...t, key: Date.now() }), []);
  const closeToast = useCallback(() => setToastState(null), []);
  const [searchOpen, setSearchOpen] = useState(false);
  const openSearch = useCallback(() => setSearchOpen(true), []);
  const mainRef = useRef<HTMLElement>(null);
  const [menu, setMenu] = useState<ShellMenuState>(null);
  const closeMenu = useCallback(() => setMenu(null), []);
  // Board 62: the quota button opens the usage popover; the dot appears below 10% of the period total.
  const usage = useUsageSnapshot(session?.active_account_id ?? '');
  const [usageAt, setUsageAt] = useState<{ x: number; y: number } | null>(null);
  const usageDot = usage.state === 'low' ? ('color.orange.660' as const) : usage.state === 'exhausted' ? ('color.red.500' as const) : undefined;
  const ctx = useMemo(() => ({ refresh, toast, openSearch }), [refresh, toast, openSearch]);
  const [pageBells, setPageBells] = useState(0);
  const registerBell = useCallback(() => {
    setPageBells((n) => n + 1);
    return () => setPageBells((n) => n - 1);
  }, []);
  const sidebarWorkspaces = useMemo(
    () => [...toSidebarWorkspaces(ws.data), ...(ws.data ? toUnassignedGroup(unassigned.data) : [])],
    [ws.data, unassigned.data],
  );
  // Board 24 ① / ③ and 25 ② (desktop title bar, hover card, edit dialog, phone card).
  const desktopInfo = useDesktopInfo();
  const hover = useHoverIntent<{ id: string; at: { x: number; y: number } }>();
  const [editId, setEditId] = useState<string | null>(null);
  const [removeTarget, setRemoveTarget] = useState<{ id: string; name: string } | null>(null);
  const [removing, setRemoving] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const removeWorkspace = async () => {
    if (!removeTarget) return;
    setRemoving(true);
    try {
      await workspaces.deleteWorkspacesById(removeTarget.id);
      if (window.location.pathname.startsWith(`/workspaces/${removeTarget.id}`)) navigate('/tasks');
      setRemoveTarget(null);
      refresh();
    } catch (err) {
      toast({ tone: 'failure', message: errorMessage(err, '移除失败，请重试') });
    } finally {
      setRemoving(false);
    }
  };

  useEffect(() => setSearchOpen(false), [pathname]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return;
      if (e.key === 'k' || e.key === 'K') {
        e.preventDefault();
        // The search layer sits inside the page area, under modal backdrops; opening it there would be invisible.
        if (document.querySelector('.sg-modal-backdrop')) return;
        setSearchOpen((o) => !o);
      } else if (e.key === ',') {
        e.preventDefault();
        navigate('/settings/profile');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <ShellContext.Provider value={ctx}>
      <BellSlotProvider value={registerBell}>
      <div className={`sg-shell${collapsed ? ' is-collapsed' : ''}`}>
        <AppSidebar
          {...(active.nav ? { activeNav: active.nav } : {})}
          {...(active.workspaceId ? { activeWorkspaceId: active.workspaceId } : {})}
          {...(active.taskId ? { activeTaskId: active.taskId } : {})}
          counts={{
            // Unknown counts render nothing rather than 0 (“读不到时不显示 0”).
            ...(decisions.data?.badge_count !== undefined ? { inbox: decisions.data.badge_count } : {}),
            ...(activeTasks.data?.counts?.all !== undefined ? { inProgress: activeTasks.data.counts.all } : {}),
          }}
          workspaces={sidebarWorkspaces}
          workspacesState={ws.status === 'error' ? 'error' : ws.status === 'loading' && !ws.data ? 'loading' : 'ready'}
          onRetryWorkspaces={ws.reload}
          user={{ name, initials: name.slice(0, 1).toUpperCase(), avatarUrl: user?.avatar_url ?? null }}
          collapsed={collapsed}
          onToggleCollapsed={() => setUserCollapsed((c) => !c)}
          onNavigate={(to) => navigate(to)}
          onNewWorkspace={() => navigate('/workspaces/new')}
          {...(searchOpen ? { activeNav: 'search' as const } : {})}
          onSearch={openSearch}
          onAccountMenu={(at) => setMenu({ kind: 'account', at })}
          onWorkspaceMenu={(id, at, mode) => setMenu({ kind: 'workspace', id, at, mode })}
          onTaskMenu={(taskId, workspaceId, at) => setMenu({ kind: 'task', taskId, workspaceId, at })}
          onNewTaskInWorkspace={(id) => navigate(`/tasks/new?workspace=${encodeURIComponent(id)}`)}
          onUsage={(at) => setUsageAt(at)}
          {...(usageDot ? { usageDot } : {})}
          {...(desktopInfo ? { titleBarExtra: <UpdatePill /> } : {})}
          {...(desktopInfo?.mobileAppUrl ? { onMobile: () => setMobileOpen(true) } : {})}
          onWorkspaceHover={(id, at) => (at ? hover.enter({ id, at }) : hover.leave())}
        />
        <main ref={mainRef} className={`sg-shell__main${searchOpen ? ' has-search' : ''}`}>
          {children}
          {searchOpen && (
            <GlobalSearch
              // The layer sits inside the scrolling main area, so it is pinned to the current scroll offset.
              top={mainRef.current?.scrollTop ?? 0}
              backLabel={backLabelFor(pathname, sidebarWorkspaces)}
              onClose={() => setSearchOpen(false)}
              onOpen={(to) => {
                setSearchOpen(false);
                navigate(to);
              }}
              onNotifications={() => navigate('/notifications')}
            />
          )}
        </main>
        {pageBells === 0 && (
          <div className="sg-shell__bell">
            <NotificationBell shell onClick={() => navigate('/notifications')} />
          </div>
        )}
      </div>
      <ShellMenus menu={menu} onClose={closeMenu} session={session} workspaces={sidebarWorkspaces} toast={toast} refresh={refresh} onEditWorkspace={setEditId} />
      {hover.value && !menu && (
        <WorkspaceHoverCard
          key={hover.value.id}
          workspaceId={hover.value.id}
          at={hover.value.at}
          onKeep={hover.keep}
          onLeave={hover.leave}
          onClose={hover.close}
          onEdit={() => setEditId(hover.value?.id ?? null)}
          navigate={navigate}
        />
      )}
      {editId && (
        <EditWorkspaceDialog
          workspaceId={editId}
          onClose={() => setEditId(null)}
          onSaved={refresh}
          onRemove={(name) => {
            setRemoveTarget({ id: editId, name });
            setEditId(null);
          }}
          navigate={navigate}
        />
      )}
      {removeTarget && (
        <ConfirmDialog
          variant="danger"
          title={`移除工作区“${removeTarget.name}”？`}
          message="只删除 Sage 中的登记与关联，不删除本机文件和已产生的任务成果；其中的任务会变为未关联工作区。"
          confirmLabel="移除工作区"
          cancelLabel="取消"
          busy={removing}
          onConfirm={() => void removeWorkspace()}
          onCancel={() => setRemoveTarget(null)}
        />
      )}
      {mobileOpen && desktopInfo?.mobileAppUrl && (
        <Popover at={{ x: 16, y: window.innerHeight - 56 }} placement="above" width={260} onClose={() => setMobileOpen(false)}>
          <MobileAppCard url={desktopInfo.mobileAppUrl} onDone={() => setMobileOpen(false)} />
        </Popover>
      )}
      {usageAt && (
        <Popover at={usageAt} placement="above" width={320} onClose={() => setUsageAt(null)}>
          <UsagePopover snapshot={usage} />
        </Popover>
      )}
      {toastState && (
        <div className="sg-shell__toast">
          <Toast
            key={toastState.key}
            tone={toastState.tone}
            message={toastState.message}
            {...(toastState.detail ? { detail: toastState.detail } : {})}
            onClose={closeToast}
            {...(toastState.actionLabel
              ? {
                  actionLabel: toastState.actionLabel,
                  onAction: () => {
                    closeToast();
                    toastState.onAction?.();
                  },
                }
              : {})}
          />
        </div>
      )}
      </BellSlotProvider>
    </ShellContext.Provider>
  );
}
