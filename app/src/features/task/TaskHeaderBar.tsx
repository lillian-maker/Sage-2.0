import type { AvatarProps } from '../../components/Avatar/Avatar';
import { Button } from '../../components/Button/Button';
import { HeaderMemberActions } from '../../components/HeaderMemberActions/HeaderMemberActions';
import { NotificationBell } from '../../components/NotificationBell/NotificationBell';
import { Tag, type TagProps } from '../../components/Tag/Tag';
import './task.css';

export interface TaskHeaderBarProps {
  title: string;
  status: 'draft' | 'running' | 'review';
  statusTag?: TagProps;
  workspaceTag?: TagProps;
  members: Array<Omit<AvatarProps, 'size'>>;
  actions: Array<'invite' | 'orchestrate'>;
  onInvite?: () => void;
  onOrchestrate?: () => void;
  onMore?: () => void;
  onNotifications?: () => void;
  /** Below 1100px the side panel becomes a drawer (Figma 709/712); this button opens it. Hidden by CSS on wider layouts. */
  railToggle?: { label: string; onClick: () => void };
}

export function TaskHeaderBar({ title, statusTag, workspaceTag, members, actions, onInvite, onOrchestrate, onMore, onNotifications, railToggle }: TaskHeaderBarProps) {
  return (
    <header className="sg-taskheader">
      <h1 className="truncate">{title}</h1>
      {workspaceTag && <Tag {...workspaceTag} />}
      {statusTag && <Tag {...statusTag} />}
      <div className="sg-taskheader__end">
        <HeaderMemberActions
          members={members}
          onInvite={actions.includes('invite') ? onInvite ?? (() => undefined) : undefined}
          extra={actions.includes('orchestrate') ? <Button variant="secondary" size={32} icon="users" label="编排" onClick={onOrchestrate} /> : undefined}
          onMore={onMore ?? (() => undefined)}
        />
        {railToggle && <Button variant="secondary" size={32} icon="layers" label={railToggle.label} className="sg-taskheader__railtoggle" onClick={railToggle.onClick} />}
        <NotificationBell onClick={onNotifications ?? (() => undefined)} />
      </div>
    </header>
  );
}
