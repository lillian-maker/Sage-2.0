import { Icon } from '../../components/Icon/Icon';
import './task.css';

export interface ChecklistItemProps {
  checked: boolean;
  label: string;
  evidence?: string;
  onEvidence?: () => void;
  /** Lets the reviewer mark the item met / unmet; without it the box is display-only. */
  onToggle?: () => void;
}

export function ChecklistItem({ checked, label, evidence, onEvidence, onToggle }: ChecklistItemProps) {
  const box = (
    <span className={`sg-checkitem__box${checked ? ' is-checked' : ''}`} role={onToggle ? undefined : 'img'} aria-label={onToggle ? undefined : checked ? '已满足' : '未满足'}>
      {checked && <Icon name="check" size={10} />}
    </span>
  );
  return (
    <div className="sg-checkitem">
      {onToggle ? (
        <button type="button" role="checkbox" aria-checked={checked} aria-label={label} className="sg-checkitem__toggle" onClick={onToggle}>
          {box}
        </button>
      ) : (
        box
      )}
      <span className="sg-checkitem__label">{label}</span>
      {evidence && (
        <button type="button" className="sg-checkitem__evidence" onClick={onEvidence}>
          {evidence}
        </button>
      )}
    </div>
  );
}
