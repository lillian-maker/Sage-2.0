import { useEffect, useState, type ReactNode } from 'react';
import { currentSession, loadSession, onSessionChange, type AuthSession } from '../../api/client';
import { Button } from '../../components/Button/Button';
import './settings.css';
import { Modal } from '../../components/Modal/Modal';

export function useSession(): AuthSession | null {
  const [s, setS] = useState<AuthSession | null>(currentSession);
  useEffect(() => {
    loadSession().then(setS, () => undefined);
    return onSessionChange(setS);
  }, []);
  return s;
}

export function activeAccount(s: AuthSession | null) {
  return s?.accounts.find((a) => a.id === s.active_account_id) ?? null;
}

export const ROLE_LABEL: Record<string, string> = { owner: '所有者', billing_admin: '账务管理员', member: '成员' };

/** Modal frame for the small forms on the settings pages (ConfirmDialog only carries a message). */
export function SettingsDialog({
  title,
  description,
  children,
  confirmLabel,
  onConfirm,
  onCancel,
  busy,
  disabled,
  footerNote,
  width = 440,
  danger,
  className,
}: {
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
  disabled?: boolean;
  footerNote?: ReactNode;
  width?: number;
  danger?: boolean;
  className?: string;
}) {
  return (
    <Modal className={className ? `sg-sdialog ${className}` : 'sg-sdialog'} label={title} style={{ width }} onClose={onCancel}>
        <h2>{title}</h2>
        {description && <p className="sg-sdialog__desc">{description}</p>}
        <div className="sg-sdialog__body">{children}</div>
        <div className="sg-sdialog__footer">
          <span className="sg-sdialog__note">{footerNote}</span>
          <Button variant="ghost" size={32} label="取消" onClick={onCancel} />
          <Button
            variant={danger ? 'danger' : 'primary'}
            size={32}
            label={confirmLabel}
            onClick={onConfirm}
            busy={busy}
            disabled={disabled}
          />
        </div>
    </Modal>
  );
}
