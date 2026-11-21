import { SageMark } from '../Icon/SageMark';

/** Logo mark + “Sage” wordmark (92×40 in the sidebar). The wordmark is lettering, not a separate Figma component. */
export function SageLogo() {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, height: 40 }} aria-label="Sage" role="img">
      <SageMark size={30} />
      <span
        aria-hidden
        style={{
          color: 'var(--color-blue-850)',
          font: 'var(--type-manrope-24-600-lh32-78)',
          letterSpacing: '-0.02em',
        }}
      >
        Sage
      </span>
    </span>
  );
}
