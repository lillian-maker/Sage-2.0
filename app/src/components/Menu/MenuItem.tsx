import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Icon, type IconName } from '../Icon/Icon';
import './Menu.css';

export interface MenuItemProps {
  icon: IconName;
  label: string;
  onSelect?: () => void;
  danger?: boolean;
  disabled?: boolean;
  /** Right-aligned secondary text: shortcut (“⌘,”), version, or a note such as “不删除本机文件”. */
  hint?: string;
  /** Shows a chevron and marks the item as opening a submenu. */
  submenu?: boolean;
  /** Checkmark for the current choice (e.g. the active account). */
  checked?: boolean;
}

export function MenuItem({ icon, label, onSelect, danger, disabled, hint, submenu, checked }: MenuItemProps) {
  return (
    <button
      type="button"
      role={checked === undefined ? 'menuitem' : 'menuitemradio'}
      aria-checked={checked}
      aria-haspopup={submenu || undefined}
      disabled={disabled}
      className={`sg-menuitem${danger ? ' is-danger' : ''}`}
      onClick={onSelect}
    >
      <Icon name={icon} size={15} />
      <span className="truncate">{label}</span>
      {(hint || submenu || checked) && (
        <span className="sg-menuitem__hint">
          {hint}
          {checked && <Icon name="check" size={13} />}
          {submenu && <Icon name="chevron-right" size={12} />}
        </span>
      )}
    </button>
  );
}

export function MenuDivider() {
  return <div className="sg-menu__divider" role="separator" />;
}

export function Menu({ children, label }: { children: React.ReactNode; label: string }) {
  return (
    <div className="sg-menu" role="menu" aria-label={label}>
      {children}
    </div>
  );
}

export interface PopoverProps {
  /** Viewport point the popover opens from (pointer position or an anchor corner). */
  at: { x: number; y: number };
  /** 'below' opens down-right from the point, 'above' grows upward from it (account bar). */
  placement?: 'below' | 'above';
  width?: number;
  onClose: () => void;
  children: ReactNode;
}

/** Fixed-position layer for menus and cards: clamps to the viewport, closes on outside click and Esc. */
export function Popover({ at, placement = 'below', width, onClose, children }: PopoverProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const { offsetWidth: w, offsetHeight: h } = el;
    const left = Math.max(8, Math.min(at.x, window.innerWidth - w - 8));
    const top = placement === 'above' ? Math.max(8, at.y - h) : Math.max(8, Math.min(at.y, window.innerHeight - h - 8));
    setPos({ left, top });
  }, [at.x, at.y, placement]);
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey, true);
    };
  }, [onClose]);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>('button:not(:disabled), [href]')?.focus();
  }, []);
  return (
    <div
      ref={ref}
      className="sg-popover"
      style={{ left: pos?.left ?? at.x, top: pos?.top ?? at.y, width, visibility: pos ? 'visible' : 'hidden' }}
      onKeyDown={(e) => {
        if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
        const items = Array.from(ref.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? []);
        const i = items.indexOf(document.activeElement as HTMLButtonElement);
        items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus();
        e.preventDefault();
      }}
    >
      {children}
    </div>
  );
}
