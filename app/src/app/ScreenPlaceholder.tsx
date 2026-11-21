import { routes } from '../routes';
import type { ScreenProps } from './router';
import './ScreenPlaceholder.css';

/** Stand-in page until the screen is implemented; lists the Figma boards that belong to the route. */
export function ScreenPlaceholder({ routeName, params, query, hash }: ScreenProps & { routeName: string }) {
  const route = routes.find((r) => r.name === routeName);
  if (!route) return null;
  const extra = [
    ...Object.entries(params).map(([k, v]) => `${k}=${v}`),
    query.toString(),
    hash && `#${hash}`,
  ].filter(Boolean);
  return (
    <div className="sg-placeholder">
      <h1>{route.title}</h1>
      <p className="sg-placeholder__path">
        {route.path}
        {extra.length > 0 && ` · ${extra.join(' · ')}`}
      </p>
      <ul>
        {route.screens.map((s) => (
          <li key={s.id}>
            <code>{s.id}</code> {s.name}
            {s.state && <span className="sg-placeholder__meta"> · {s.state}</span>}
            {s.variant && <span className="sg-placeholder__meta"> · {s.variant}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
