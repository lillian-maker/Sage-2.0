import { colorValue, type ColorValue } from '../../tokens';
import './ChoiceChip.css';

export interface ChoiceChipProps {
  label: string;
  selected: boolean;
  size: 28 | 30 | 32 | 34;
  dotColor?: ColorValue;
  onToggle?: () => void;
  disabled?: boolean;
  /** 'radio' inside single-choice groups, 'checkbox' for multi-select. */
  role?: 'radio' | 'checkbox';
}

export function ChoiceChip({ label, selected, size, dotColor, onToggle, disabled, role = 'checkbox' }: ChoiceChipProps) {
  return (
    <button
      type="button"
      role={role}
      aria-checked={selected}
      disabled={disabled}
      className={`sg-chip sg-chip--s${size}${selected ? ' is-selected' : ''}`}
      onClick={onToggle}
    >
      {dotColor && <span className="sg-chip__dot" style={{ background: colorValue(dotColor) }} />}
      {label}
    </button>
  );
}
