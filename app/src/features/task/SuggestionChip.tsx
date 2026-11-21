import { Icon } from '../../components/Icon/Icon';
import { Tag } from '../../components/Tag/Tag';
import './task.css';

export interface SuggestionChipProps {
  label: string;
  badge?: string;
  onSelect?: () => void;
}

export function SuggestionChip({ label, badge, onSelect }: SuggestionChipProps) {
  return (
    <button type="button" className={`sg-suggest${badge ? ' has-badge' : ''}`} onClick={onSelect}>
      <Icon name="plus" size={12} />
      <span>{label}</span>
      {badge && <Tag label={badge} tone="warning" size={18} />}
    </button>
  );
}
