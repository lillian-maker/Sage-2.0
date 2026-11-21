import { useRef } from 'react';
import { Icon } from '../Icon/Icon';
import { Kbd } from '../Kbd/Kbd';
import './SearchField.css';

export interface SearchFieldProps {
  value?: string;
  placeholder?: string;
  size: 32 | 52;
  width?: number;
  escHint?: boolean;
  clearable?: boolean;
  onValueChange?: (value: string) => void;
  onSubmit?: (value: string) => void;
  autoFocus?: boolean;
  label?: string;
}

export function SearchField({ value, placeholder, size, width, escHint, clearable, onValueChange, onSubmit, autoFocus, label }: SearchFieldProps) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <div className={`sg-search sg-search--s${size}`} style={{ width }} onClick={() => ref.current?.focus()}>
      <Icon name="search" size={size === 52 ? 18 : 14} />
      <input
        ref={ref}
        type="search"
        value={value}
        placeholder={placeholder}
        aria-label={label ?? placeholder}
        autoFocus={autoFocus}
        onChange={(e) => onValueChange?.(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') onSubmit?.(e.currentTarget.value);
        }}
      />
      {clearable && value && (
        <button type="button" className="sg-search__clear" aria-label="清除" onClick={() => onValueChange?.('')}>
          <Icon name="x" size={12} />
        </button>
      )}
      {escHint && <Kbd keys="esc" />}
    </div>
  );
}
