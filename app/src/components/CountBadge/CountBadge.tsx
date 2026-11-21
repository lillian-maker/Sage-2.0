import './CountBadge.css';

export interface CountBadgeProps {
  count: number;
  tone: 'brand' | 'accent';
  size: 18;
  label?: string;
}

export function CountBadge({ count, tone, label }: CountBadgeProps) {
  return (
    <span className={`sg-count sg-count--${tone}`} aria-label={label ?? String(count)}>
      {count > 99 ? '99+' : count}
    </span>
  );
}
