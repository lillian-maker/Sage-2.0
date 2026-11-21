import type { InputHTMLAttributes, TextareaHTMLAttributes } from 'react';
import './TextField.css';

interface Common {
  value?: string;
  placeholder?: string;
  width?: number;
  invalid?: boolean;
  onValueChange?: (value: string) => void;
}

export type TextFieldProps = Common &
  (
    | ({ multiline?: false; size: 32 | 42; rows?: never } & Omit<InputHTMLAttributes<HTMLInputElement>, 'size' | 'value' | 'width'>)
    | ({ multiline: true; rows?: number; size?: never } & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'value' | 'rows'>)
  );

export function TextField(props: TextFieldProps) {
  const { value, placeholder, width, invalid, onValueChange } = props;
  if (props.multiline) {
    const { multiline: _m, rows = 3, value: _v, placeholder: _p, width: _w, invalid: _i, onValueChange: _o, className, ...rest } = props;
    return (
      <textarea
        className={`sg-field sg-field--multi${invalid ? ' is-invalid' : ''}${className ? ' ' + className : ''}`}
        style={{ width, minHeight: 18 * Math.max(1, rows) + 18 }}
        rows={rows}
        value={value}
        placeholder={placeholder}
        aria-invalid={invalid || undefined}
        onChange={(e) => onValueChange?.(e.target.value)}
        {...rest}
      />
    );
  }
  const { multiline: _m, size, value: _v, placeholder: _p, width: _w, invalid: _i, onValueChange: _o, className, ...rest } = props;
  return (
    <input
      className={`sg-field sg-field--s${size}${invalid ? ' is-invalid' : ''}${className ? ' ' + className : ''}`}
      style={{ width }}
      value={value}
      placeholder={placeholder}
      aria-invalid={invalid || undefined}
      onChange={(e) => onValueChange?.(e.target.value)}
      {...rest}
    />
  );
}
