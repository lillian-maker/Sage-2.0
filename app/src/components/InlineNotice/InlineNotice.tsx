import { Icon, type IconName } from '../Icon/Icon';
import './InlineNotice.css';

export interface InlineNoticeProps {
  icon: IconName;
  text: string;
  tone: 'info' | 'muted' | 'error';
  action?: string;
  onAction?: () => void;
}

export function InlineNotice({ icon, text, tone, action, onAction }: InlineNoticeProps) {
  return (
    <div className={`sg-notice sg-notice--${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <Icon name={icon} size={tone === 'info' ? 13 : 14} className={icon === 'loader' ? 'sg-spin' : undefined} />
      <span className="sg-notice__text">{text}</span>
      {action && (
        <button type="button" className="sg-notice__action" onClick={onAction}>
          {action}
        </button>
      )}
    </div>
  );
}
