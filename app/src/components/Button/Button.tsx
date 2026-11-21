import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Icon, type IconName } from '../Icon/Icon';
import './Button.css';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'tonal' | 'danger';
export type ButtonSize = 24 | 26 | 28 | 30 | 32 | 34 | 36 | 42;

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  variant: ButtonVariant;
  size: ButtonSize;
  label: ReactNode;
  icon?: IconName;
  /** Icon after the label (e.g. chevron, external arrow). */
  trailingIcon?: IconName;
  /** Stretch to the container width (Figma "fill" buttons such as the 328 wide login button). */
  block?: boolean;
  busy?: boolean;
}

const ICON_SIZE: Record<ButtonSize, number> = { 24: 12, 26: 12, 28: 13, 30: 13, 32: 14, 34: 15, 36: 16, 42: 16 };

export function Button({
  variant,
  size,
  label,
  icon,
  trailingIcon,
  block,
  busy,
  className,
  disabled,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={`sg-btn sg-btn--${variant} sg-btn--s${size}${block ? ' sg-btn--block' : ''}${className ? ' ' + className : ''}`}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {busy ? <Icon name="loader" size={ICON_SIZE[size]} className="sg-spin" /> : icon && <Icon name={icon} size={ICON_SIZE[size]} />}
      <span className="sg-btn__label">{label}</span>
      {trailingIcon && <Icon name={trailingIcon} size={ICON_SIZE[size] - 2} />}
    </button>
  );
}
