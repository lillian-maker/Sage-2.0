import { CountBadge } from '../CountBadge/CountBadge';
import { Icon, type IconName } from '../Icon/Icon';
import './AppSidebar.css';

export interface SidebarNavItemProps {
  icon: IconName;
  label: string;
  shortcut?: string;
  count?: number;
  active?: boolean;
  variant?: 'default' | 'new';
  /** Narrow-window (≤ 859px) icon-only state, 36×36. */
  collapsed?: boolean;
  size?: 36;
  href?: string;
  onSelect?: () => void;
}

export function SidebarNavItem({ icon, label, shortcut, count, active, variant = 'default', collapsed, href, onSelect }: SidebarNavItemProps) {
  const cls = `sg-navitem sg-navitem--${variant}${active ? ' is-active' : ''}${collapsed ? ' is-collapsed' : ''}`;
  const content = (
    <>
      <Icon name={icon} size={16} />
      {!collapsed && <span className="sg-navitem__label">{label}</span>}
      {!collapsed && (shortcut || count !== undefined) && (
        <span className="sg-navitem__end">
          {count !== undefined && count > 0 && <CountBadge count={count} tone="brand" size={18} label={`${label} ${count}`} />}
          {shortcut && <span className="sg-navitem__shortcut">{shortcut}</span>}
        </span>
      )}
      {collapsed && count !== undefined && count > 0 && <span className="sg-navitem__pip" aria-label={`${label} ${count}`} />}
    </>
  );
  const common = { className: cls, 'aria-current': active ? ('page' as const) : undefined, title: collapsed ? label : undefined, 'aria-label': collapsed ? label : undefined };
  if (href)
    return (
      <a
        {...common}
        href={href}
        onClick={(e) => {
          if (onSelect && !e.metaKey && !e.ctrlKey) {
            e.preventDefault();
            onSelect();
          }
        }}
      >
        {content}
      </a>
    );
  return (
    <button type="button" {...common} onClick={onSelect}>
      {content}
    </button>
  );
}
