import type { ReactNode } from 'react';
import './PageHeader.css';

export interface PageHeaderProps {
  title: string;
  description?: string;
  actions?: ReactNode;
}

export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <header className="sg-pageheader">
      <div className="sg-pageheader__text">
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="sg-pageheader__actions">{actions}</div>}
    </header>
  );
}
