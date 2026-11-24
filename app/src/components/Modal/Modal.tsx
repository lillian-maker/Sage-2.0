import { useEffect, useRef, type CSSProperties, type FormEvent, type ReactNode } from 'react';
import './Modal.css';

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Nested modals (e.g. a confirm opened from a picker) share one Esc key: only the topmost one closes.
const stack: object[] = [];

export interface ModalProps {
  /** Esc and backdrop clicks. Omit for a modal that can only be left through its own buttons. */
  onClose?: () => void;
  closeOnBackdrop?: boolean;
  closeOnEsc?: boolean;
  role?: 'dialog' | 'alertdialog';
  label?: string;
  labelledBy?: string;
  /** Classes of the dialog box itself; the backdrop is always the shared `sg-modal-backdrop`. */
  className?: string;
  style?: CSSProperties;
  /** Render the box as a <form> so Enter submits. */
  onSubmit?: (e: FormEvent<HTMLFormElement>) => void;
  /** CSS selector of the element focused on open; defaults to an autoFocus child, then the first focusable element. */
  initialFocus?: string;
  /** Component gallery: render the box in place, without backdrop, focus handling or Esc. */
  inline?: boolean;
  /**
   * `content`: the backdrop covers only the main area below the 52px top bar, leaving the sidebar and the
   * bell usable (645:2 新建专家). Only for routes whose page does not scroll, since it is positioned in main.
   */
  scope?: 'window' | 'content';
  children: ReactNode;
}

export function Modal({
  onClose,
  closeOnBackdrop = true,
  closeOnEsc = true,
  role = 'dialog',
  label,
  labelledBy,
  className,
  style,
  onSubmit,
  initialFocus,
  inline,
  scope = 'window',
  children,
}: ModalProps) {
  const boxRef = useRef<HTMLElement | null>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const escRef = useRef(closeOnEsc);
  escRef.current = closeOnEsc;

  useEffect(() => {
    if (inline) return;
    const token = {};
    stack.push(token);
    const box = boxRef.current;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (box && !box.contains(document.activeElement)) {
      const first =
        (initialFocus && box.querySelector<HTMLElement>(initialFocus)) ||
        box.querySelector<HTMLElement>('[autofocus], [data-autofocus]') ||
        box.querySelector<HTMLElement>(FOCUSABLE) ||
        box;
      first.focus();
    }
    const onKey = (e: KeyboardEvent) => {
      if (stack[stack.length - 1] !== token) return;
      if (e.key === 'Escape') {
        if (e.defaultPrevented || e.isComposing || !escRef.current || !closeRef.current) return;
        e.preventDefault();
        closeRef.current();
      } else if (e.key === 'Tab' && boxRef.current) {
        const items = Array.from(boxRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetParent !== null);
        if (!items.length) {
          e.preventDefault();
          return;
        }
        const firstEl = items[0]!;
        const lastEl = items[items.length - 1]!;
        const active = document.activeElement;
        if (e.shiftKey && (active === firstEl || !boxRef.current.contains(active))) {
          e.preventDefault();
          lastEl.focus();
        } else if (!e.shiftKey && (active === lastEl || !boxRef.current.contains(active))) {
          e.preventDefault();
          firstEl.focus();
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      const i = stack.indexOf(token);
      if (i >= 0) stack.splice(i, 1);
      if (opener && opener.isConnected) opener.focus();
    };
    // Focus is taken once on open; later prop changes must not steal it back.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inline]);

  const common = {
    className: ['sg-modal', className].filter(Boolean).join(' '),
    role,
    'aria-modal': inline ? undefined : true,
    'aria-label': label,
    'aria-labelledby': labelledBy,
    tabIndex: -1,
    style,
  } as const;
  const box = onSubmit ? (
    <form ref={(el) => void (boxRef.current = el)} {...common} onSubmit={onSubmit}>
      {children}
    </form>
  ) : (
    <div ref={(el) => void (boxRef.current = el)} {...common}>
      {children}
    </div>
  );
  if (inline) return box;
  return (
    <div
      className={scope === 'content' ? 'sg-modal-backdrop sg-modal-backdrop--content' : 'sg-modal-backdrop'}
      role="presentation"
      onMouseDown={(e) => {
        if (closeOnBackdrop && e.target === e.currentTarget) onClose?.();
      }}
    >
      {box}
    </div>
  );
}
