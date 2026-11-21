import type { ButtonHTMLAttributes } from 'react';
import { Icon, type IconName } from '../Icon/Icon';
import { iconIndex } from '../Icon/iconIndex';
import './IconButton.css';

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  icon: IconName;
  size: 25 | 28 | 32 | 34 | 36;
  iconSize?: number;
  variant: 'ghost' | 'tonal' | 'primary' | 'secondary';
  shape?: 'rounded' | 'pill';
  /** aria-label; icon-only buttons need an accessible name. */
  label: string;
}

const DEFAULT_ICON: Record<IconButtonProps['size'], number> = { 25: 14, 28: 16, 32: 16, 34: 16, 36: 18 };

export function IconButton({ icon, size, iconSize, variant, shape = 'rounded', label, className, type = 'button', ...rest }: IconButtonProps) {
  // Some icons exist only as the full-size Figma button master (glyph inside a transparent hit area);
  // scaling that art down to the glyph size would shrink the glyph, so it is drawn at button size.
  const entries = (iconIndex[icon] ?? []) as ReadonlyArray<{ size: number }>;
  const hotspot = !entries.some((e) => e.size < size) && entries.some((e) => e.size === size);
  return (
    <button
      type={type}
      aria-label={label}
      title={label}
      className={`sg-iconbtn sg-iconbtn--${variant} sg-iconbtn--${shape} sg-iconbtn--s${size}${className ? ' ' + className : ''}`}
      style={{ width: size, height: size }}
      {...rest}
    >
      <Icon name={icon} size={hotspot ? size : iconSize ?? DEFAULT_ICON[size]} />
    </button>
  );
}
