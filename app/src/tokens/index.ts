import {
  colorTokens,
  effectTokens,
  gradientTokens,
  typeTokens,
  type ColorToken,
  type EffectToken,
  type GradientToken,
  type TypeToken,
} from './tokens';

export * from './tokens';

export const color = (t: ColorToken): string => `var(${colorTokens[t]})`;
export const effect = (t: EffectToken): string => `var(${effectTokens[t]})`;
/** Gradient tokens store only the color stops; the angle belongs to the component. */
export const gradient = (t: GradientToken, angle = 180): string =>
  `linear-gradient(${angle}deg, var(${gradientTokens[t]}))`;
export const font = (t: TypeToken): { font: string; letterSpacing: string } => ({
  font: `var(${typeTokens[t]})`,
  letterSpacing: `var(${typeTokens[t]}-ls)`,
});

/** Follows the active theme's accent (the Figma default theme accent is color.blue.590). */
export type ColorValue = ColorToken | 'accent' | 'currentColor';
export const colorValue = (t: ColorValue): string =>
  t === 'accent' ? color('color.blue.590') : t === 'currentColor' ? 'currentColor' : color(t);
