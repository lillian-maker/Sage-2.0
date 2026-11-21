import { Avatar, type AvatarProps } from '../Avatar/Avatar';
import './PersonRow.css';

export interface PersonRowProps {
  avatar: Omit<AvatarProps, 'size'>;
  name: string;
  subtitle?: string;
  size: 24 | 48 | 56;
  /** Subtitle sits right-aligned on small rows and under the name on large ones. */
}

export function PersonRow({ avatar, name, subtitle, size }: PersonRowProps) {
  const large = size > 24;
  return (
    <div className={`sg-person${large ? ' is-large' : ''}`}>
      <Avatar {...avatar} size={size} />
      <div className="sg-person__text">
        <span className="sg-person__name truncate">{name}</span>
        {subtitle && <span className="sg-person__subtitle truncate">{subtitle}</span>}
      </div>
    </div>
  );
}
