import type { MouseEvent } from 'react';
import { StatusDot } from '../StatusDot/StatusDot';
import type { ColorValue } from '../../tokens';
import './AppSidebar.css';

export interface SidebarTaskItemProps {
  title: string;
  active?: boolean;
  /** Unread / needs-attention dot; the title turns bold with it. */
  statusDot?: ColorValue;
  onSelect?: () => void;
  /** Right-click menu (board 24 ⑥); receives the pointer position. */
  onContextMenu?: (at: { x: number; y: number }) => void;
}

export function SidebarTaskItem({ title, active, statusDot, onSelect, onContextMenu }: SidebarTaskItemProps) {
  const context = (e: MouseEvent) => {
    if (!onContextMenu) return;
    e.preventDefault();
    onContextMenu({ x: e.clientX, y: e.clientY });
  };
  return (
    <button
      type="button"
      className={`sg-taskitem${active ? ' is-active' : ''}${statusDot ? ' has-dot' : ''}`}
      aria-current={active ? 'page' : undefined}
      title={title}
      onClick={onSelect}
      onContextMenu={context}
      onKeyDown={(e) => {
        if (onContextMenu && (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10'))) {
          e.preventDefault();
          const r = e.currentTarget.getBoundingClientRect();
          onContextMenu({ x: r.left + 24, y: r.bottom });
        }
      }}
    >
      <span className="truncate">{title}</span>
      {statusDot && <StatusDot color={statusDot} size={6} slot={14} label="有新动态" />}
    </button>
  );
}
