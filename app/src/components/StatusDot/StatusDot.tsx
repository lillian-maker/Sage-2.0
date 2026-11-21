import { colorValue, type ColorValue } from '../../tokens';

export interface StatusDotProps {
  color: ColorValue;
  size: 6 | 7 | 8 | 10 | 12;
  /** Inline slot width; the dot is right-aligned inside it (Figma “dot 14” icon). */
  slot?: number;
  label?: string;
}

export function StatusDot({ color, size, slot, label }: StatusDotProps) {
  const dot = (
    <span
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      style={{ display: 'inline-block', flex: 'none', width: size, height: size, borderRadius: '50%', background: colorValue(color) }}
    />
  );
  if (!slot) return dot;
  return <span style={{ display: 'inline-flex', justifyContent: 'flex-end', alignItems: 'center', width: slot, height: size, flex: 'none' }}>{dot}</span>;
}
