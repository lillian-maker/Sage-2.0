import { iconSvg } from './Icon';
import './Icon.css';

// The business-theme master is recoloured through CSS variables (see theme.css "Sage logo"), so the
// mark follows data-theme / data-appearance without one exported SVG per theme and appearance.
const SLOTS: Record<string, string> = {
  '#396397': 'var(--sg-logo-a)',
  '#7396BC': 'var(--sg-logo-b)',
  '#AEC8E2': 'var(--sg-logo-c)',
};

const themed = (() => {
  const svg = iconSvg('sage-logo--3374-13377.svg') ?? '';
  return svg
    .replace(/stop-color="(#[0-9A-F]{6})"/gi, (m, hex: string) => (SLOTS[hex.toUpperCase()] ? `style="stop-color:${SLOTS[hex.toUpperCase()]}"` : m))
    .replace(/fill="#7396BC"/i, 'style="fill:var(--sg-logo-star)"')
    .replace(/_3374_13377/g, '_sage_mark');
})();

/** Sage logo mark (ribbon S + four-point star) in the current theme style's colours. */
export function SageMark({ size, label }: { size: number; label?: string }) {
  return (
    <span
      className="sg-icon"
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      data-icon="sage-logo"
      style={{ width: size, height: Math.round(((size * 38) / 31) * 100) / 100 }}
      dangerouslySetInnerHTML={{ __html: themed }}
    />
  );
}
