import { SegmentedControlItem } from './SegmentedControlItem';
import './SegmentedControl.css';

export interface SegmentedControlProps<T extends string> {
  options: Array<{ value: T; label: string }>;
  value: T;
  onChange: (value: T) => void;
  label: string;
}

export function SegmentedControl<T extends string>({ options, value, onChange, label }: SegmentedControlProps<T>) {
  return (
    <div className="sg-seg" role="tablist" aria-label={label}>
      {options.map((o) => (
        <SegmentedControlItem key={o.value} label={o.label} size={26} selected={o.value === value} onSelect={() => onChange(o.value)} />
      ))}
    </div>
  );
}
