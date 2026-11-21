import './AppSidebar.css';

export interface SidebarShowMoreProps {
  hiddenCount: number;
  expanded: boolean;
  onToggle?: () => void;
}

export function SidebarShowMore({ hiddenCount, expanded, onToggle }: SidebarShowMoreProps) {
  return (
    <button type="button" className="sg-showmore" aria-expanded={expanded} onClick={onToggle}>
      {expanded ? '收起' : `展开显示 · 另 ${hiddenCount} 个`}
    </button>
  );
}
