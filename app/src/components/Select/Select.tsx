import { Avatar, type AvatarProps } from '../Avatar/Avatar';
import { Icon } from '../Icon/Icon';
import './Select.css';

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectProps {
  value?: string;
  placeholder?: string;
  size: 26 | 32 | 34 | 36;
  width?: number;
  leadingAvatar?: AvatarProps;
  options?: SelectOption[];
  onChange?: (value: string) => void;
  label?: string;
  disabled?: boolean;
  /** Trigger-only prefix (e.g. “排序：”); options keep their plain labels. */
  prefix?: string;
}

/** Styled trigger over a transparent native <select>, so keyboard and screen-reader behavior stay native. */
export function Select({ value, placeholder, size, width, leadingAvatar, options, onChange, label, disabled, prefix }: SelectProps) {
  const opts = options ?? (value ? [{ value, label: value }] : []);
  const current = opts.find((o) => o.value === value);
  const text = current?.label ?? value ?? placeholder ?? '';
  return (
    <label className={`sg-select sg-select--s${size}${!current && !value ? ' is-placeholder' : ''}${disabled ? ' is-disabled' : ''}`} style={{ width }}>
      {leadingAvatar && <Avatar {...leadingAvatar} />}
      {prefix && <span className="sg-select__prefix">{prefix}</span>}
      <span className="sg-select__value truncate">{text}</span>
      <Icon name="chevron-down" size={size === 26 ? 11 : 12} />
      <select
        aria-label={label ?? placeholder}
        value={value ?? ''}
        disabled={disabled}
        onChange={(e) => onChange?.(e.target.value)}
      >
        {!value && <option value="">{placeholder ?? ''}</option>}
        {opts.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
