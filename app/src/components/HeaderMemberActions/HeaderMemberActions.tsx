import { Avatar, type AvatarProps } from '../Avatar/Avatar';
import { Button } from '../Button/Button';
import { IconButton } from '../IconButton/IconButton';
import './HeaderMemberActions.css';

export interface HeaderMemberActionsProps {
  members: Array<Omit<AvatarProps, 'size'>>;
  onInvite?: () => void;
  onMore?: () => void;
  /** Extra buttons between invite and more (e.g. 编排). */
  extra?: React.ReactNode;
}

export function HeaderMemberActions({ members, onInvite, onMore, extra }: HeaderMemberActionsProps) {
  const shown = members.slice(0, 4);
  return (
    <div className="sg-hma">
      <span className="sg-hma__stack" aria-label={`${members.length} 位成员`}>
        {shown.map((m, i) => (
          <Avatar key={i} {...m} size={28} ring />
        ))}
      </span>
      {onInvite && <Button variant="secondary" size={32} icon="plus" label="邀请" onClick={onInvite} />}
      {extra}
      {onMore && <IconButton icon="ellipsis" size={28} variant="ghost" label="更多" onClick={onMore} />}
    </div>
  );
}
