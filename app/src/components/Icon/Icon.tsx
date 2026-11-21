import type { CSSProperties } from 'react';
import { colorValue, type ColorValue } from '../../tokens';
import { iconIndex, type IconName } from './iconIndex';
import './Icon.css';

export type { IconName };

const raw = import.meta.glob('../../assets/icons/*.svg', { query: '?raw', import: 'default', eager: true }) as Record<
  string,
  string
>;

type Entry = { file: string; size: number; w: number; h: number; mono: boolean; variant: string };

/** Picks the exported master closest to the requested size, preferring single-color (themeable) art. */
export function pickIcon(name: IconName, size: number): Entry | undefined {
  const list = iconIndex[name] as readonly Entry[] | undefined;
  if (!list || list.length === 0) return undefined;
  const score = (e: Entry) => Math.abs(e.size - size) * 2 + (e.mono ? 0 : 1) + (e.variant ? 0.5 : 0);
  return [...list].sort((a, b) => score(a) - score(b))[0];
}

export function iconSvg(file: string): string | undefined {
  return raw['../../assets/icons/' + file];
}

export interface IconProps {
  name: IconName;
  /** Rendered width in px (= Figma master width). */
  size: number;
  /** Only for semantic color variants; defaults to the parent's text color. */
  color?: ColorValue;
  className?: string;
  /** Accessible label; omit for decorative icons. */
  label?: string;
  style?: CSSProperties;
}

export function Icon({ name, size, color, className, label, style }: IconProps) {
  const entry = pickIcon(name, size);
  const svg = entry ? iconSvg(entry.file) : undefined;
  const height = entry && entry.w > 0 ? Math.round((size * entry.h) / entry.w * 100) / 100 : size;
  return (
    <span
      className={'sg-icon' + (className ? ' ' + className : '')}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      data-icon={name}
      style={{ width: size, height, color: color ? colorValue(color) : undefined, ...style }}
      dangerouslySetInnerHTML={svg ? { __html: svg } : undefined}
    />
  );
}
