import './SegmentedControl.css';

export interface SegmentedControlItemProps {
  label: string;
  selected: boolean;
  size: 26;
  onSelect?: () => void;
}

export function SegmentedControlItem({ label, selected, size, onSelect }: SegmentedControlItemProps) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={selected}
      className={`sg-seg__item sg-seg__item--s${size}${selected ? ' is-selected' : ''}`}
      onClick={onSelect}
    >
      {label}
    </button>
  );
}
