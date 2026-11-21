import { Icon } from '../Icon/Icon';
import './BackLink.css';

export interface BackLinkProps {
  label: string;
  size: 30 | 44;
  onBack: () => void;
}

export function BackLink({ label, size, onBack }: BackLinkProps) {
  return (
    <button type="button" className={`sg-backlink sg-backlink--s${size}`} onClick={onBack}>
      <Icon name="chevron-left" size={12} />
      <span>{label}</span>
    </button>
  );
}
