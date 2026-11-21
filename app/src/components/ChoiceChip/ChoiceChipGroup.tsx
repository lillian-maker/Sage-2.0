import { ChoiceChip } from './ChoiceChip';
import './ChoiceChip.css';

export interface ChoiceChipGroupProps {
  options: string[];
  value: string;
  size: 28;
  onChange?: (value: string) => void;
  label?: string;
}

export function ChoiceChipGroup({ options, value, size, onChange, label }: ChoiceChipGroupProps) {
  return (
    <div className="sg-chipgroup" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <ChoiceChip key={o} role="radio" label={o} size={size} selected={o === value} onToggle={() => onChange?.(o)} />
      ))}
    </div>
  );
}
