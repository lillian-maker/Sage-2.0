import { Fragment } from 'react';
import { Icon, type IconName } from '../../components/Icon/Icon';
import './search.css';

export interface SearchResultRowProps {
  kind: 'task' | 'workspace' | 'artifact' | 'document' | 'expert' | 'extension' | 'inbox' | 'automation';
  title: string;
  meta: string;
  snippet?: string;
  highlight: string;
  active?: boolean;
  onOpen?: () => void;
  /** “另有 N 处命中”: further matches inside the same object. */
  extraHits?: number;
  onHover?: () => void;
}

const ICON: Record<SearchResultRowProps['kind'], IconName> = {
  task: 'message-square',
  workspace: 'folder',
  artifact: 'file',
  document: 'book',
  expert: 'contact',
  extension: 'puzzle',
  inbox: 'download',
  automation: 'timer',
};

function mark(text: string, q: string) {
  if (!q) return text;
  const lower = text.toLowerCase();
  const needle = q.toLowerCase();
  const out: Array<string | { hit: string }> = [];
  let from = 0;
  for (let i = lower.indexOf(needle); i >= 0; i = lower.indexOf(needle, i + needle.length)) {
    if (i > from) out.push(text.slice(from, i));
    out.push({ hit: text.slice(i, i + needle.length) });
    from = i + needle.length;
  }
  if (from < text.length) out.push(text.slice(from));
  return out.map((p, i) => <Fragment key={i}>{typeof p === 'string' ? p : <mark>{p.hit}</mark>}</Fragment>);
}

export function SearchResultRow({ kind, title, meta, snippet, highlight, active, onOpen, extraHits, onHover }: SearchResultRowProps) {
  return (
    <button type="button" className={`sg-result${active ? ' is-active' : ''}`} onClick={onOpen} onMouseMove={onHover}>
      <span className="sg-result__icon">
        <Icon name={ICON[kind]} size={16} />
      </span>
      <span className="sg-result__text">
        <span className="sg-result__line">
          <span className="sg-result__title">{mark(title, highlight)}</span>
          <span className="sg-result__meta">{meta}</span>
        </span>
        {snippet && <span className="sg-result__snippet">{mark(snippet, highlight)}</span>}
        {!!extraHits && extraHits > 0 && (
          <span className="sg-result__more">
            另有 {extraHits} 处命中
            <Icon name="chevron-down" size={12} />
          </span>
        )}
      </span>
    </button>
  );
}
