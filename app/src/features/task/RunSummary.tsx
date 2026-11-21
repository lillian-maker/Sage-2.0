import { Icon } from '../../components/Icon/Icon';
import './task.css';

export interface RunSummaryProps {
  status: 'done';
  /** e.g. “生成 2 个成果，查阅 5 份资料 · 8 分 15 秒”; the part before the first “，” is emphasized. */
  summary: string;
  expanded: boolean;
  onToggle?: () => void;
}

export function RunSummary({ summary, expanded, onToggle }: RunSummaryProps) {
  const cut = summary.indexOf('，');
  const strong = cut >= 0 ? summary.slice(0, cut) : summary;
  const rest = cut >= 0 ? summary.slice(cut) : '';
  return (
    <button type="button" className="sg-runsummary" aria-expanded={expanded} onClick={onToggle}>
      <Icon name="check" size={12} />
      <span>完成 ·</span>
      <strong>{strong}</strong>
      <span>{rest}</span>
      <Icon name="chevron-down" size={12} style={expanded ? { transform: 'rotate(180deg)' } : undefined} />
    </button>
  );
}
