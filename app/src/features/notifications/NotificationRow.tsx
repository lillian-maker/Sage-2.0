import { Avatar } from '../../components/Avatar/Avatar';
import { Button } from '../../components/Button/Button';
import { IconTile } from '../../components/IconTile/IconTile';
import type { IconName } from '../../components/Icon/Icon';
import './notifications.css';

export interface NotificationRowProps {
  text: string;
  meta: string;
  unread: boolean;
  icon: IconName;
  actor?: { name: string; avatar?: string | null };
  actionLabel?: string;
  actionBusy?: boolean;
  actionDone?: boolean;
  onOpen?: () => void;
  onAction?: () => void;
}

/** One row of 61 通知中心; unread rows get the tinted background, bold title and the accent dot. */
export function NotificationRow({ text, meta, unread, icon, actor, actionLabel, actionBusy, actionDone, onOpen, onAction }: NotificationRowProps) {
  return (
    <li className={`sg-notif${unread ? ' is-unread' : ''}`}>
      <button type="button" className="sg-notif__main" onClick={onOpen} aria-label={`${unread ? '未读：' : ''}${text}，${meta}`}>
        {actor ? <Avatar size={32} alt={actor.name} src={actor.avatar ?? null} /> : <IconTile icon={icon} size={32} tone="neutral" shape="circle" />}
        <span className="sg-notif__text">
          <span className="sg-notif__title">{text}</span>
          <span className="sg-notif__meta">{meta}</span>
        </span>
      </button>
      {actionLabel ? (
        <Button className="sg-notif__action" variant="secondary" size={28} label={actionDone ? '已开始运行' : actionLabel} busy={actionBusy} disabled={actionDone} onClick={onAction} />
      ) : (
        unread && <span className="sg-notif__dot" aria-hidden="true" />
      )}
    </li>
  );
}
