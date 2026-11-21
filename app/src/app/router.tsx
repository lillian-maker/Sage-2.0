import { useSyncExternalStore, type AnchorHTMLAttributes, type MouseEvent } from 'react';

export interface Location {
  pathname: string;
  search: string;
  hash: string;
}

export interface ScreenProps {
  params: Record<string, string>;
  query: URLSearchParams;
  /** Hash without '#', e.g. 'acceptance' for /tasks/:taskId#acceptance. */
  hash: string;
  navigate: (to: string, opts?: { replace?: boolean }) => void;
}

const NAV_EVENT = 'sage:navigate';

function snapshot(): string {
  return window.location.pathname + window.location.search + window.location.hash;
}

function subscribe(fn: () => void) {
  window.addEventListener('popstate', fn);
  window.addEventListener('hashchange', fn);
  window.addEventListener(NAV_EVENT, fn);
  return () => {
    window.removeEventListener('popstate', fn);
    window.removeEventListener('hashchange', fn);
    window.removeEventListener(NAV_EVENT, fn);
  };
}

export function navigate(to: string, opts: { replace?: boolean } = {}) {
  if (to === snapshot()) return;
  if (opts.replace) window.history.replaceState(null, '', to);
  else window.history.pushState(null, '', to);
  window.dispatchEvent(new Event(NAV_EVENT));
}

export function useLocation(): Location {
  const href = useSyncExternalStore(subscribe, snapshot);
  const url = new URL(href, window.location.origin);
  return { pathname: url.pathname, search: url.search, hash: url.hash.replace(/^#/, '') };
}

export interface Matcher<T> {
  value: T;
  segments: string[];
}

/** Matches a pathname against route patterns like /tasks/:taskId; static segments win over params. */
export function createMatcher<T>(entries: Array<{ path: string; value: T }>) {
  const list: Matcher<T>[] = entries
    .map((e) => ({ value: e.value, segments: e.path.split('/').filter(Boolean) }))
    .sort((a, b) => staticScore(b.segments) - staticScore(a.segments));
  return (pathname: string): { value: T; params: Record<string, string> } | null => {
    const parts = pathname.split('/').filter(Boolean).map(decodeURIComponent);
    for (const m of list) {
      if (m.segments.length !== parts.length) continue;
      const params: Record<string, string> = {};
      const ok = m.segments.every((seg, i) => {
        const part = parts[i] as string;
        if (seg.startsWith(':')) {
          params[seg.slice(1)] = part;
          return true;
        }
        return seg === part;
      });
      if (ok) return { value: m.value, params };
    }
    return null;
  };
}

function staticScore(segments: string[]): number {
  // Earlier static segments weigh more, so /tasks/new beats /tasks/:taskId.
  return segments.reduce((acc, s, i) => acc + (s.startsWith(':') ? 0 : 2 ** (16 - i)), 0);
}

export function Link({ href, onClick, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  const handle = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    navigate(href);
  };
  return <a href={href} onClick={handle} {...rest} />;
}
