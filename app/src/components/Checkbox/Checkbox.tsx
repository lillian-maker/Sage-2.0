import type { ReactNode } from 'react';
import { Icon } from '../Icon/Icon';
import './Checkbox.css';

export interface CheckboxProps {
  checked: boolean;
  onChange?: (checked: boolean) => void;
  disabled?: boolean;
  /** Label content next to the box (Figma 760:554 知晓勾选). */
  label?: ReactNode;
  name?: string;
}

/** 16×16 checkbox: checked fills blue-590 (#396397) with a white tick, corner radius 4. */
export function Checkbox({ checked, onChange, disabled, label, name }: CheckboxProps) {
  return (
    <label className={`sg-checkbox${disabled ? ' is-disabled' : ''}`}>
      <input
        type="checkbox"
        name={name}
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange?.(e.target.checked)}
      />
      <span className="sg-checkbox__box" aria-hidden="true">
        {checked && <Icon name="check" size={11} />}
      </span>
      {label !== undefined && <span className="sg-checkbox__label">{label}</span>}
    </label>
  );
}
