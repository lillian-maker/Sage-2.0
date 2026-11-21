import type { AvatarProps } from '../../components/Avatar/Avatar';
import { Avatar } from '../../components/Avatar/Avatar';
import { Icon } from '../../components/Icon/Icon';
import { Tag, type TagProps } from '../../components/Tag/Tag';
import './tasks.css';

export interface ActiveTaskRowProps {
  title: string;
  subtitle: string;
  /** 0–1; omitted when the plan has no steps yet. */
  progress?: number;
  state: TagProps;
  waiting: boolean;
  experts: Array<Omit<AvatarProps, 'size'>>;
  time: string;
  onOpen?: () => void;
  onState?: () => void;
}

export function ActiveTaskRow({ title, subtitle, progress, state, waiting, experts, time, onOpen, onState }: ActiveTaskRowProps) {
  return (
    <div className="sg-activerow" role="link" tabIndex={0} onClick={onOpen} onKeyDown={(e) => e.key === 'Enter' && onOpen?.()}>
      {waiting ? (
        <Icon name="circle-pending" size={18} color="color.orange.660" label="等你处理" />
      ) : (
        <Icon name="loader-ring" size={18} color="accent" className="sg-spin" label="进行中" />
      )}
      <span className="sg-activerow__text">
        <span className="sg-activerow__title truncate">{title}</span>
        <span className="sg-activerow__sub truncate">{subtitle}</span>
      </span>
      <span className="sg-activerow__end">
        {progress !== undefined && (
          <span className="sg-activerow__bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress * 100)}>
            <span style={{ width: `${Math.round(progress * 100)}%` }} />
          </span>
        )}
        <span
          onClick={(e) => {
            if (!onState) return;
            e.stopPropagation();
            onState();
          }}
        >
          <Tag {...state} />
        </span>
        <span className="sg-activerow__avatars">
          {experts.slice(0, 3).map((a, i) => (
            <Avatar key={i} {...a} size={20} ring />
          ))}
        </span>
        <span className="sg-activerow__time">{time}</span>
      </span>
    </div>
  );
}
