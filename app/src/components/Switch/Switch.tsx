import './Switch.css';

export interface SwitchProps {
  checked: boolean;
  onChange?: (checked: boolean) => void;
  size?: 'md';
  disabled?: boolean;
  label?: string;
}

export function Switch({ checked, onChange, disabled, label }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={`sg-switch${checked ? ' is-on' : ''}`}
      onClick={() => onChange?.(!checked)}
    >
      <span className="sg-switch__knob" />
    </button>
  );
}
