import { useEffect, useState, type ReactNode } from 'react';
import { ApiError } from '../../api/client';
import { Icon, type IconName } from '../Icon/Icon';
import { IconTile } from '../IconTile/IconTile';
import './States.css';

interface ActionLink {
  label: string;
  onClick: () => void;
}

function LinkAction({ action }: { action?: ActionLink | undefined }) {
  if (!action) return null;
  return (
    <button type="button" className="sg-state__action" onClick={action.onClick}>
      {action.label}
    </button>
  );
}

/** “空”：data loaded and there is nothing yet. Never use it when the read failed (“空 ≠ 读不到”). */
export function EmptyState({ icon, title, description, action }: { icon: IconName; title: string; description?: string; action?: ActionLink }) {
  return (
    <div className="sg-state" role="status">
      <IconTile icon={icon} size={44} tone="neutral" shape="circle" />
      <div className="sg-state__title">{title}</div>
      {description && <div className="sg-state__desc">{description}</div>}
      <LinkAction action={action} />
    </div>
  );
}

/** “无权限”：never names the object or reveals whether it exists. */
export function ForbiddenState({ title, action }: { title: string; action?: ActionLink }) {
  return (
    <div className="sg-state" role="status">
      <IconTile icon="info" size={44} tone="warning" shape="circle" />
      <div className="sg-state__title">{title}</div>
      <div className="sg-state__desc">不显示对象名称与内容，不泄露对象是否存在</div>
      <LinkAction action={action} />
    </div>
  );
}

const SKELETON_DELAY_MS = 300;
const SLOW_AFTER_MS = 10_000;

/** “加载”：skeleton only after 300 ms; after 10 s shows “仍在加载…” with cancel. */
export function LoadingState({ rows = 3, onCancel, label = '正在加载' }: { rows?: number; onCancel?: () => void; label?: string }) {
  const [phase, setPhase] = useState<'hidden' | 'skeleton' | 'slow'>('hidden');
  useEffect(() => {
    const a = window.setTimeout(() => setPhase('skeleton'), SKELETON_DELAY_MS);
    const b = window.setTimeout(() => setPhase('slow'), SLOW_AFTER_MS);
    return () => {
      window.clearTimeout(a);
      window.clearTimeout(b);
    };
  }, []);
  if (phase === 'hidden') return <div className="sg-skeleton" aria-busy="true" aria-label={label} />;
  return (
    <div className="sg-skeleton" aria-busy="true" aria-label={label}>
      {Array.from({ length: rows }, (_, i) => (
        <div className="sg-skeleton__row" key={i}>
          <span className="sg-skeleton__dot" />
          <span className="sg-skeleton__lines">
            <span className="sg-skeleton__line" />
            <span className="sg-skeleton__line sg-skeleton__line--short" />
          </span>
        </div>
      ))}
      {phase === 'slow' && (
        <div className="sg-skeleton__slow">
          仍在加载…
          {onCancel && (
            <button type="button" className="sg-state__action" onClick={onCancel}>
              取消
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * “错误 / 不可用”：a banner above content that is already loaded (content is kept, not cleared).
 * The error code is deliberately not shown; it belongs to 帮助与诊断.
 */
export function ErrorBanner({ message, onRetry, children }: { message: string; onRetry?: () => void; children?: ReactNode }) {
  return (
    <div className="sg-errorblock">
      <div className="sg-errorbanner" role="alert">
        <Icon name="info" size={14} />
        <span className="sg-errorbanner__text">{message}</span>
        {onRetry && (
          <button type="button" className="sg-state__action" onClick={onRetry}>
            重试
          </button>
        )}
      </div>
      {children}
    </div>
  );
}

/** Message for a failed read: server text when it is user facing, otherwise a generic one. */
export function errorMessage(err: unknown, fallback = '暂时无法读取'): string {
  if (err instanceof ApiError && err.status > 0 && err.status < 500 && err.message) return err.message;
  return fallback;
}

export type ToastTone = 'undo' | 'failure';

/** 撤销提示条 / 失败提示条 (board 82). Undo toasts stay 5 s and pause on hover; failures never auto-dismiss. */
export function Toast({
  tone,
  message,
  detail,
  actionLabel,
  onAction,
  onClose,
}: {
  tone: ToastTone;
  message: string;
  detail?: string;
  actionLabel?: string;
  onAction?: () => void;
  onClose: () => void;
}) {
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (tone !== 'undo' || paused) return;
    const t = window.setTimeout(onClose, 5000);
    return () => window.clearTimeout(t);
  }, [tone, paused, onClose]);
  return (
    <div
      className={`sg-toast sg-toast--${tone}`}
      role={tone === 'failure' ? 'alert' : 'status'}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <span className="sg-toast__text">
        {message}
        {detail && <span className="sg-toast__detail">{detail}</span>}
      </span>
      {actionLabel && (
        <button type="button" className="sg-toast__action" onClick={onAction}>
          {actionLabel}
        </button>
      )}
      {tone === 'undo' && <span className={`sg-toast__timer${paused ? ' is-paused' : ''}`} aria-hidden="true" />}
      <button type="button" className="sg-toast__close" aria-label="关闭" onClick={onClose}>
        <Icon name="x" size={12} />
      </button>
    </div>
  );
}

/** “提交中”：overlays a region while a write is in flight; buttons themselves use Button busy. */
export function SubmittingOverlay({ active, label = '正在提交…', children }: { active: boolean; label?: string; children: ReactNode }) {
  return (
    <div className="sg-submitting" aria-busy={active || undefined}>
      {children}
      {active && (
        <div className="sg-submitting__veil" role="status">
          <Icon name="loader" size={16} className="sg-spin" />
          <span>{label}</span>
        </div>
      )}
    </div>
  );
}

/**
 * Loading / error gate for one read (useApiQuery result). First load shows the skeleton; a failed read with
 * nothing loaded shows the banner. With children, they render once data exists (plus a refresh-failed banner
 * over stale data); without children the gate renders nothing once data exists.
 */
export function QueryGate({
  q,
  hasData = q.data !== undefined && q.data !== null,
  label,
  rows = 3,
  children,
}: {
  q: { status: 'loading' | 'ready' | 'error'; error: unknown; data?: unknown; reload: () => void };
  hasData?: boolean;
  /** Names the object in the error text: “{label}暂时无法读取”. */
  label?: string;
  rows?: number;
  children?: ReactNode;
}) {
  if (!hasData && q.status === 'error') return <ErrorBanner message={errorMessage(q.error, label ? `${label}暂时无法读取` : undefined)} onRetry={q.reload} />;
  if (!hasData) return <LoadingState rows={rows} />;
  if (children === undefined) return null;
  return (
    <>
      {q.status === 'error' && <ErrorBanner message={errorMessage(q.error, '刷新失败，显示的是上次读取的内容')} onRetry={q.reload} />}
      {children}
    </>
  );
}
