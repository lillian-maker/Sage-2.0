import type { ReactNode } from 'react';
import { Button, type ButtonProps } from '../Button/Button';
import './ActionBar.css';

export interface ActionBarProps {
  /** decision: approval row (left aligned); footer: tinted card footer with hint; toolbar: right-aligned tools. */
  variant: 'decision' | 'footer' | 'toolbar';
  hint?: ReactNode;
  actions: ButtonProps[];
  /** Plain text links after the buttons (e.g. “拒绝”). */
  extraActions?: Array<{ label: string; onClick?: () => void }>;
  secondaryLink?: { label: string; onClick?: () => void };
}

export function ActionBar({ variant, hint, actions, extraActions, secondaryLink }: ActionBarProps) {
  return (
    <div className={`sg-actionbar sg-actionbar--${variant}`}>
      {hint && <div className="sg-actionbar__hint">{hint}</div>}
      {variant !== 'decision' && <span className="sg-actionbar__spacer" />}
      {actions.map((a, i) => (
        <Button key={i} {...a} />
      ))}
      {extraActions?.map((a) => (
        <Button key={a.label} variant="ghost" size={32} label={a.label} onClick={a.onClick} />
      ))}
      {secondaryLink && (
        <button type="button" className="sg-actionbar__link" onClick={secondaryLink.onClick}>
          {secondaryLink.label}
        </button>
      )}
    </div>
  );
}
