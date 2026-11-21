import './StepMarker.css';

export interface StepMarkerProps {
  index?: number;
  state: 'done' | 'current' | 'static';
  shape: 'circle' | 'square';
  size: 20 | 22;
}

export function StepMarker({ index, state, shape, size }: StepMarkerProps) {
  return (
    <span className={`sg-step sg-step--${state} sg-step--${shape}`} style={{ width: size, height: size }} aria-label={state === 'done' ? '已完成' : index !== undefined ? `第 ${index} 步` : undefined}>
      {state === 'done' ? '✓' : index}
    </span>
  );
}
