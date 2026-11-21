import { Fragment, useState, type MouseEvent, type ReactNode } from 'react';
import type { ColorValue } from '../../tokens';
import { Icon } from '../Icon/Icon';
import { IconButton } from '../IconButton/IconButton';
import { Tag } from '../Tag/Tag';
import { WindowControl } from '../AppShell/WindowControl';
import { SidebarAccountBar, type SidebarAccountBarProps } from './SidebarAccountBar';
import { SidebarNavItem } from './SidebarNavItem';
import { SidebarShowMore } from './SidebarShowMore';
import { SidebarTaskItem } from './SidebarTaskItem';
import { SidebarWorkspaceItem } from './SidebarWorkspaceItem';
import { SageLogo } from './SageLogo';
import { SageMark } from '../Icon/SageMark';
import './AppSidebar.css';

export type SidebarNav = 'search' | 'experts' | 'knowledge' | 'extensions' | 'inbox' | 'artifacts' | 'automations' | 'inProgress';

export interface SidebarTask {
  id: string;
  title: string;
  /** Dot shown for unread / needs-attention tasks. */
  dot?: ColorValue;
}

export interface SidebarWorkspace {
  id: string;
  name: string;
  kind: 'workspace' | 'unassigned';
  pinned: boolean;
  taskCount: number;
  tasks: SidebarTask[];
  /** Tasks loaded after “展开显示”; replaces `tasks` when present. */
  expandedTasks?: SidebarTask[];
}

export interface AppSidebarProps {
  activeNav?: SidebarNav;
  activeWorkspaceId?: string;
  activeTaskId?: string;
  counts: { inbox?: number; inProgress?: number };
  workspaces: SidebarWorkspace[];
  user: SidebarAccountBarProps['user'];
  collapsed?: boolean;
  updateAvailable?: boolean;
  /** Replaces the static “更新” tag, e.g. the desktop client's live update button (board 25 ①). */
  titleBarExtra?: ReactNode;
  /** Pointer entering (with the card anchor) or leaving a workspace row (board 24 ① hover card). */
  onWorkspaceHover?: (id: string, at: { x: number; y: number } | null) => void;
  /** Shows the phone icon in the account bar (board 25 ②). */
  onMobile?: () => void;
  /** Errors loading the workspace list render inline instead of hiding the section. */
  workspacesState?: 'ready' | 'loading' | 'error';
  onRetryWorkspaces?: () => void;
  onNavigate?: (to: string) => void;
  onToggleCollapsed?: () => void;
  onExpandWorkspace?: (id: string, expanded: boolean) => void;
  onNewWorkspace?: () => void;
  onWorkspaceMore?: (id: string) => void;
  /** Opens the global search layer instead of navigating (board 26: 侧栏“搜索”与 ⌘K). */
  onSearch?: () => void;
  /** Account menu from the avatar / name (board 25 ③). */
  onAccountMenu?: (at: { x: number; y: number }) => void;
  /** Workspace menu: 'more' from “…”, 'context' from right-click (adds 打开工作区 / 在此新建任务). */
  onWorkspaceMenu?: (id: string, at: { x: number; y: number }, mode: 'more' | 'context') => void;
  /** Task right-click menu (board 24 ⑥). */
  onTaskMenu?: (taskId: string, workspaceId: string, at: { x: number; y: number }) => void;
  onNewTaskInWorkspace?: (id: string) => void;
  /** Overrides the default /usage navigation: opens the usage popover anchored at the bar (board 62). */
  onUsage?: (at: { x: number; y: number }) => void;
  /** Usage warning dot on the quota button (see board 62 states). */
  usageDot?: SidebarAccountBarProps['usageDot'];
}

const NAV: Array<{ key: SidebarNav; icon: Parameters<typeof SidebarNavItem>[0]['icon']; label: string; to: string; shortcut?: string }> = [
  { key: 'search', icon: 'search', label: '搜索', to: '/search', shortcut: '⌘K' },
  { key: 'experts', icon: 'contact', label: '专家', to: '/experts' },
  { key: 'knowledge', icon: 'book', label: '知识中心', to: '/knowledge' },
  { key: 'extensions', icon: 'puzzle', label: '扩展', to: '/extensions' },
  { key: 'inbox', icon: 'download', label: '收件', to: '/inbox' },
  { key: 'artifacts', icon: 'layers', label: '成果', to: '/artifacts' },
  { key: 'automations', icon: 'timer', label: '自动化', to: '/automations' },
];

export function AppSidebar(props: AppSidebarProps) {
  const { activeNav, activeWorkspaceId, activeTaskId, counts, workspaces, user, collapsed, updateAvailable, workspacesState = 'ready' } = props;
  const go = (to: string) => props.onNavigate?.(to);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const pinned = workspaces.filter((w) => w.pinned);
  const rest = workspaces.filter((w) => !w.pinned);

  const renderWorkspace = (w: SidebarWorkspace) => {
    const isOpen = !!expanded[w.id];
    const tasks = isOpen && w.expandedTasks ? w.expandedTasks : w.tasks;
    const hidden = Math.max(0, w.taskCount - w.tasks.length);
    return (
      <Fragment key={w.id}>
        <div
          className="sg-sidebar__ws"
          {...(props.onWorkspaceHover && w.kind === 'workspace'
            ? {
                onMouseEnter: (e: MouseEvent<HTMLDivElement>) => {
                  const r = e.currentTarget.getBoundingClientRect();
                  props.onWorkspaceHover?.(w.id, { x: r.right + 8, y: r.top });
                },
                onMouseLeave: () => props.onWorkspaceHover?.(w.id, null),
              }
            : {})}
        >
          <SidebarWorkspaceItem
            name={w.name}
            kind={w.kind}
            active={w.id === activeWorkspaceId && !activeTaskId}
            onSelect={() => go(w.kind === 'unassigned' ? '/tasks?workspace=none' : `/workspaces/${w.id}`)}
            onMore={
              props.onWorkspaceMore || props.onWorkspaceMenu
                ? (at) => {
                    props.onWorkspaceMore?.(w.id);
                    props.onWorkspaceMenu?.(w.id, at, 'more');
                  }
                : undefined
            }
            {...(props.onWorkspaceMenu ? { onContextMenu: (at: { x: number; y: number }) => props.onWorkspaceMenu?.(w.id, at, 'context') } : {})}
            {...(props.onNewTaskInWorkspace ? { onNewTask: () => props.onNewTaskInWorkspace?.(w.id) } : {})}
          />
        </div>
        {tasks.map((t) => (
          <SidebarTaskItem
            key={t.id}
            title={t.title}
            active={t.id === activeTaskId}
            statusDot={t.dot}
            onSelect={() => go(`/tasks/${t.id}`)}
            {...(props.onTaskMenu ? { onContextMenu: (at: { x: number; y: number }) => props.onTaskMenu?.(t.id, w.id, at) } : {})}
          />
        ))}
        {hidden > 0 && (
          <SidebarShowMore
            hiddenCount={hidden}
            expanded={isOpen}
            onToggle={() => {
              setExpanded((s) => ({ ...s, [w.id]: !isOpen }));
              props.onExpandWorkspace?.(w.id, !isOpen);
            }}
          />
        )}
      </Fragment>
    );
  };

  return (
    <nav className={`sg-sidebar${collapsed ? ' is-collapsed' : ''}`} aria-label="应用导航">
      <div className="sg-sidebar__top">
        <div className="sg-sidebar__lights">
          <WindowControl kind="close" />
          <WindowControl kind="minimize" />
          <WindowControl kind="zoom" />
        </div>
        {!collapsed && (
          <div className="sg-sidebar__topactions">
            {props.titleBarExtra ?? (updateAvailable && <Tag label="更新" tone="emphasis" size={22} icon="download" />)}
            <IconButton icon="panel-left" size={28} iconSize={16} variant="ghost" label="收起侧栏" onClick={props.onToggleCollapsed} />
          </div>
        )}
      </div>
      <div className="sg-sidebar__logo">{collapsed ? <SageMark size={30} label="Sage" /> : <SageLogo />}</div>
      <SidebarNavItem icon="square-pen" label="新任务" shortcut="⌘N" variant="new" collapsed={collapsed} href="/tasks/new" onSelect={() => go('/tasks/new')} />
      <div className="sg-sidebar__nav">
        {NAV.map((n) => (
          <SidebarNavItem
            key={n.key}
            icon={n.icon}
            label={n.label}
            shortcut={n.shortcut}
            count={n.key === 'inbox' ? counts.inbox : undefined}
            active={activeNav === n.key}
            collapsed={collapsed}
            href={n.to}
            onSelect={() => (n.key === 'search' && props.onSearch ? props.onSearch() : go(n.to))}
          />
        ))}
      </div>
      <div className="sg-sidebar__divider" />
      <div className="sg-sidebar__inprogress">
        <SidebarNavItem
          icon="clock"
          label="进行中"
          count={counts.inProgress}
          active={activeNav === 'inProgress'}
          collapsed={collapsed}
          href="/tasks/active"
          onSelect={() => go('/tasks/active')}
        />
      </div>
      {!collapsed && (
        <>
          <div className="sg-sidebar__wshead">
            <span className="sg-sidebar__wstitle">工作区</span>
            <span className="sg-sidebar__wsactions">
              <button type="button" className="sg-sidebar__wslink" onClick={() => go('/tasks')}>
                全部任务
              </button>
              <button type="button" className="sg-sidebar__wsnew" aria-label="新建工作区" onClick={props.onNewWorkspace}>
                <Icon name="folder-plus" size={15} />
              </button>
            </span>
          </div>
          <div className="sg-sidebar__list">
            {workspacesState === 'loading' && <div className="sg-sidebar__note">正在加载…</div>}
            {workspacesState === 'error' && (
              <div className="sg-sidebar__note sg-sidebar__note--error" role="alert">
                <Icon name="info" size={12} />
                <span>任务列表暂时无法读取</span>
                <button type="button" onClick={props.onRetryWorkspaces}>
                  重试
                </button>
              </div>
            )}
            {workspacesState === 'ready' && workspaces.every((w) => w.taskCount === 0) && (
              <div className="sg-sidebar__empty">
                <Icon name="message-square" size={16} />
                <strong>还没有任务</strong>
                <span>试试在上方新建一个</span>
                <button type="button" onClick={() => go('/tasks/new')}>
                  新任务
                </button>
              </div>
            )}
            {pinned.length > 0 && <div className="sg-sidebar__section">置顶</div>}
            {pinned.map(renderWorkspace)}
            {pinned.length > 0 && rest.length > 0 && <div className="sg-sidebar__section">全部</div>}
            {rest.map(renderWorkspace)}
          </div>
        </>
      )}
      {collapsed && <div className="sg-sidebar__list" />}
      <div className="sg-sidebar__account">
        <SidebarAccountBar
          user={user}
          collapsed={collapsed}
          onUsage={props.onUsage ?? (() => go('/usage'))}
          {...(props.usageDot ? { usageDot: props.usageDot } : {})}
          onSettings={() => go('/settings/profile')}
          {...(props.onAccountMenu ? { onAccount: props.onAccountMenu } : {})}
          {...(props.onMobile ? { onMobile: props.onMobile } : {})}
        />
      </div>
    </nav>
  );
}
