import { Button } from '../../components/Button/Button';
import { Icon, type IconName } from '../../components/Icon/Icon';
import { IconTile } from '../../components/IconTile/IconTile';
import './extensions.css';

export interface CapabilityRowProps {
  icon: IconName;
  iconTile?: boolean;
  name: string;
  kind?: string;
  action?: string;
  onAction?: () => void;
  busy?: boolean;
  disabled?: boolean;
}

export function CapabilityRow({ icon, iconTile, name, kind, action, onAction, busy, disabled }: CapabilityRowProps) {
  const connector = kind?.startsWith('连接器');
  return (
    <div className="sg-cap">
      {iconTile ? <IconTile icon={icon} size={28} tone="neutral" shape="rounded" /> : <Icon name={icon} size={15} />}
      <span className="sg-cap__name truncate">{name}</span>
      {kind && <span className={`sg-cap__kind${connector ? ' is-connector' : ''}`}>{kind}</span>}
      <span className="sg-cap__spacer" />
      {action && <Button variant="secondary" size={28} label={action} onClick={onAction} busy={busy} disabled={disabled} />}
    </div>
  );
}
