import { useId, type ReactNode } from 'react';
import { Button } from '../Button/Button';
import { Icon } from '../Icon/Icon';
import { Modal } from '../Modal/Modal';
import './ConfirmDialog.css';

export interface ConfirmDialogProps {
  title: string;
  /** Simple text body; omit when children carry a richer form (e.g. the off-boarding panel). */
  message?: string;
  confirmLabel: string;
  cancelLabel: string;
  /** Rich body (form rows, checkboxes) rendered instead of / under the message. */
  children?: ReactNode;
  /** Small note in the footer, left of the actions. */
  footerNote?: ReactNode;
  tone?: 'warning';
  onConfirm?: () => void;
  onCancel?: () => void;
  /** Render inline (component gallery) instead of as a modal overlay. */
  inline?: boolean;
  busy?: boolean;
  /**
   * Board 82 layouts (left aligned, actions on the right). Without it the compact centred card is kept.
   * - standard: recoverable batch action; Enter confirms, Esc and the backdrop cancel.
   * - danger: irreversible; red confirm, focus starts on cancel, the backdrop does not close.
   * - external: needs authorisation; may add a secondary action such as “修改范围”.
   */
  variant?: 'standard' | 'danger' | 'external';
  secondaryLabel?: string;
  onSecondary?: () => void;
  /** Esc / backdrop; defaults to onCancel. Needed when the cancel button is itself an action (e.g. “拒绝”). */
  onDismiss?: () => void;
}

export function ConfirmDialog(props: ConfirmDialogProps) {
  const { title, message, confirmLabel, cancelLabel, tone, onConfirm, onCancel, inline, busy, variant, secondaryLabel, onSecondary, children, footerNote } = props;
  const onDismiss = props.onDismiss ?? onCancel;
  const titleId = useId();
  const backdropCloses = variant !== 'danger' && variant !== 'external';
  const frame = (className: string, body: ReactNode) => (
    <Modal
      role="alertdialog"
      labelledBy={titleId}
      className={className}
      inline={!!inline}
      initialFocus={variant === 'danger' ? '[data-role="cancel"]' : variant ? '[data-role="confirm"]' : 'button'}
      closeOnBackdrop={backdropCloses}
      {...(onDismiss ? { onClose: onDismiss } : {})}
    >
      {body}
    </Modal>
  );

  if (variant) {
    return frame(
      `sg-dialog sg-dialog--board sg-dialog--${variant}`,
      <>
        <h2 id={titleId}>{title}</h2>
        {message && <p>{message}</p>}
        {children}
        <div className="sg-dialog__actions">
          {footerNote && <span className="sg-dialog__note">{footerNote}</span>}
          <Button data-role="cancel" variant="ghost" size={32} label={cancelLabel} onClick={onCancel} disabled={busy} />
          {secondaryLabel && <Button variant="secondary" size={32} label={secondaryLabel} onClick={onSecondary} disabled={busy} />}
          <Button
            data-role="confirm"
            className="sg-dialog__confirm"
            variant={variant === 'danger' ? 'danger' : 'primary'}
            size={32}
            label={confirmLabel}
            onClick={onConfirm}
            busy={busy}
          />
        </div>
      </>,
    );
  }
  return frame(
    'sg-dialog',
    <>
        {tone === 'warning' && (
          <span className="sg-dialog__icon">
            <Icon name="info" size={20} />
          </span>
        )}
        <h2 id={titleId}>{title}</h2>
        {message && <p>{message}</p>}
        {children}
        <div className="sg-dialog__actions">
          {footerNote && <span className="sg-dialog__note">{footerNote}</span>}
          <Button variant="primary" size={32} label={confirmLabel} onClick={onConfirm} busy={busy} />
          <Button variant="ghost" size={32} label={cancelLabel} onClick={onCancel} />
        </div>
    </>,
  );
}
