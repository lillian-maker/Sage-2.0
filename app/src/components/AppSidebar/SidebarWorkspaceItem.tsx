import type { MouseEvent } from 'react';
import { Icon } from '../Icon/Icon';
import { IconButton } from '../IconButton/IconButton';
import './AppSidebar.css';

export interface SidebarWorkspaceItemProps {
  name: string;
  kind: 'workspace' | 'unassigned';
  active?: boolean;
  showActions?: boolean;
  onSelect?: () => void;
  /** “…” menu; receives the button's bottom-left corner. */
  onMore?: (at: { x: number; y: number }) => void;
  /** Right-click menu: same as “…” plus 打开工作区 / 在此新建任务. */
  onContextMenu?: (at: { x: number; y: number }) => void;
  /** Pen button next to “…”: new task in this workspace. */
  onNewTask?: () => void;
}

export function SidebarWorkspaceItem({ name, kind, active, showActions, onSelect, onMore, onContextMenu, onNewTask }: SidebarWorkspaceItemProps) {
  const more = (e: MouseEvent<HTMLButtonElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    onMore?.({ x: r.left, y: r.bottom + 4 });
  };
  const context = (e: MouseEvent) => {
    if (!onContextMenu || kind !== 'workspace') return;
    e.preventDefault();
    onContextMenu({ x: e.clientX, y: e.clientY });
  };
  return (
    <div className={`sg-wsitem${active ? ' is-active' : ''}${showActions ? ' show-actions' : ''}${kind === 'workspace' && onNewTask ? ' has-pen' : ''}`} onContextMenu={context}>
      <button type="button" className="sg-wsitem__main" onClick={onSelect} aria-current={active ? 'page' : undefined}>
        <Icon name={kind === 'unassigned' ? 'file' : 'folder'} size={15} />
        <span className="truncate">{name}</span>
      </button>
      {kind === 'workspace' && onMore && (
        <IconButton className="sg-wsitem__more" icon="ellipsis" size={25} iconSize={14} variant="ghost" label={`${name} 更多操作`} onClick={more} />
      )}
      {kind === 'workspace' && onNewTask && (
        <IconButton className="sg-wsitem__more sg-wsitem__pen" icon="square-pen" size={25} iconSize={14} variant="ghost" label={`在${name}中新建任务`} onClick={onNewTask} />
      )}
    </div>
  );
}
