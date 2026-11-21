import './Tabs.css';

export interface TabProps {
  label: string;
  selected: boolean;
  size: 37;
  onSelect?: () => void;
  /** Bold count after the label (e.g. “未读 2”); omitted when unknown. */
  count?: number | string;
}

export function Tab({ label, selected, onSelect, count }: TabProps) {
  return (
    <button type="button" role="tab" aria-selected={selected} className={`sg-tab${selected ? ' is-selected' : ''}`} onClick={onSelect}>
      {label}
      {count !== undefined && <b className="sg-tab__count">{count}</b>}
    </button>
  );
}
