import type { MouseEvent } from 'react';
import type { ColorValue } from '../../tokens';
import { Avatar } from '../Avatar/Avatar';
import { IconButton } from '../IconButton/IconButton';
import { StatusDot } from '../StatusDot/StatusDot';
import './AppSidebar.css';

export interface SidebarAccountBarProps {
  user: { name: string; initials?: string; avatarUrl?: string | null };
  /** Receives the bar's top-left corner so callers can anchor the usage popover (board 62). */
  onUsage?: (at: { x: number; y: number }) => void;
  /** Usage warning dot on the quota button (orange below 10%, red when exhausted). */
  usageDot?: ColorValue;
  /** The phone icon only renders with a handler: it stays hidden until the mobile app ships (board 25). */
  onMobile?: () => void;
  onSettings?: () => void;
  /** Avatar / name opens the account menu; receives the bar's top-left corner. */
  onAccount?: (at: { x: number; y: number }) => void;
  collapsed?: boolean;
}

export function SidebarAccountBar({ user, onUsage, usageDot, onMobile, onSettings, onAccount, collapsed }: SidebarAccountBarProps) {
  const barRect = (e: MouseEvent<HTMLElement>) => {
    const bar = (e.currentTarget.closest('.sg-accountbar') ?? e.currentTarget).getBoundingClientRect();
    return { x: bar.left, y: bar.top - 8 };
  };
  const openAccount = (e: MouseEvent<HTMLElement>) => onAccount?.(barRect(e));
  const openUsage = (e: MouseEvent<HTMLElement>) => onUsage?.(barRect(e));
  const who = (
    <>
      <Avatar size={28} alt={user.name} initials={user.initials} src={user.avatarUrl ?? undefined} />
      {!collapsed && <span className="sg-accountbar__name truncate">{user.name}</span>}
    </>
  );
  return (
    <div className={`sg-accountbar${collapsed ? ' is-collapsed' : ''}`}>
      {onAccount ? (
        <button type="button" className="sg-accountbar__who" aria-haspopup="menu" aria-label={`${user.name} 账号菜单`} onClick={openAccount}>
          {who}
        </button>
      ) : (
        who
      )}
      {!collapsed && (
        <span className="sg-accountbar__usage">
          <IconButton icon="bar-chart" size={28} variant="ghost" label="用量" onClick={openUsage} />
          {usageDot && <StatusDot color={usageDot} size={8} label="用量偏低" />}
        </span>
      )}
      {!collapsed && onMobile && <IconButton icon="smartphone" size={28} variant="ghost" label="手机端" onClick={onMobile} />}
      <IconButton icon="settings" size={28} variant="ghost" label="设置" onClick={onSettings} />
    </div>
  );
}
