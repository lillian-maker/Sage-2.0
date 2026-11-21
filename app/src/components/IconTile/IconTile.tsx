import { Icon, type IconName } from '../Icon/Icon';
import './IconTile.css';

export interface IconTileProps {
  icon: IconName;
  size: 28 | 32 | 36 | 44;
  tone: 'neutral' | 'warning';
  shape: 'rounded' | 'circle';
}

const GLYPH: Record<IconTileProps['size'], number> = { 28: 15, 32: 16, 36: 18, 44: 20 };

export function IconTile({ icon, size, tone, shape }: IconTileProps) {
  return (
    <span className={`sg-icontile sg-icontile--${tone} sg-icontile--${shape} sg-icontile--s${size}`} style={{ width: size, height: size }} aria-hidden>
      <Icon name={icon} size={GLYPH[size]} />
    </span>
  );
}
