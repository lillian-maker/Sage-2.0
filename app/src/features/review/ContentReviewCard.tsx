import { useState } from 'react';
import { Button } from '../../components/Button/Button';
import type { ButtonProps } from '../../components/Button/Button';
import { Card, CardHeader } from '../../components/Card/CardHeader';
import { Tag } from '../../components/Tag/Tag';
import '../task/review.css';

export interface ReviewIssue {
  id: string;
  severity: '高' | '中' | '低';
  title: string;
  original: string;
  suggestion: string;
  basis: string;
  /** applied / ignored once decided; pending (default) shows the actions. */
  action?: string;
  /** A generated replacement asset (主图修改版) instead of a text rewrite: the row starts collapsed. */
  candidate?: { label: string; onOpen?: () => void };
}

export interface ContentReviewCardProps {
  subtitle: string;
  issueCount: number;
  dimensions: Array<{ name: string; issues: number }>;
  issues: ReviewIssue[];
  /** Card title; defaults to 内容审核结果 (改写版本 v3 · 核对 while checking a rewrite). */
  title?: string;
  /** Replaces the default 应用全部改写 header action. */
  primaryAction?: Pick<ButtonProps, 'label' | 'onClick' | 'disabled' | 'busy'>;
  onApplyAll?: () => void;
  onExport?: () => void;
  onApply?: (id: string) => void;
  onCopy?: (id: string) => void;
  onIgnore?: (id: string) => void;
}

export function ContentReviewCard({ subtitle, issueCount, dimensions, issues, title, primaryAction, onApplyAll, onExport, onApply, onCopy, onIgnore }: ContentReviewCardProps) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  return (
    <Card className="sg-review">
      <CardHeader
        icon="check"
        title={title ?? '内容审核结果'}
        subtitle={subtitle}
        tag={{ label: `需修改 · ${issueCount} 处`, tone: 'warning', size: 24 }}
        actions={[
          { variant: 'secondary', size: 28, label: '导出报告', onClick: onExport },
          { variant: 'primary', size: 28, label: '应用全部改写', onClick: onApplyAll, ...primaryAction },
        ]}
      />
      <div className="sg-review__dims">
        {dimensions.map((d) => (
          <span key={d.name} className="sg-review__dim">
            {d.name}
            <b className={d.issues > 0 ? 'has-issues' : undefined}>{d.issues}</b>
          </span>
        ))}
      </div>
      {issues.map((it) => {
        const collapsed = !!it.candidate && !open[it.id];
        return (
          <div key={it.id} className="sg-review__issue">
            <div className="sg-review__issuehead">
              <Tag label={it.severity} tone={it.severity === '高' ? 'danger' : 'warning'} size={18} />
              <span className="sg-review__issuetitle">{it.title}</span>
            </div>
            {it.candidate && (
              <button type="button" className="sg-review__expand" aria-expanded={!collapsed} onClick={() => setOpen((o) => ({ ...o, [it.id]: !o[it.id] }))}>
                {collapsed ? `展开 · 已生成${it.candidate.label}` : '收起'}
              </button>
            )}
            {!collapsed && (
              <>
                {it.original && <div className="sg-review__diff">{it.original}</div>}
                {it.suggestion ? (
                  <div className="sg-review__suggest">
                    <span>改为</span> {it.suggestion}
                  </div>
                ) : (
                  it.candidate && (
                    <div className="sg-review__suggest">
                      <span>改为</span>{' '}
                      <button type="button" className="sg-review__link" onClick={it.candidate.onOpen}>{`查看${it.candidate.label}`}</button>
                    </div>
                  )
                )}
                <div className="sg-review__basis">依据：{it.basis}</div>
                <div className="sg-review__actions">
                  {it.action === 'applied' ? (
                    <span className="sg-review__done">已应用</span>
                  ) : it.action === 'ignored' ? (
                    <span className="sg-review__done">已忽略</span>
                  ) : (
                    <Button variant="ghost" size={28} label="应用改写" className="is-link" onClick={() => onApply?.(it.id)} />
                  )}
                  <Button variant="ghost" size={28} label="复制" className="is-link" disabled={!it.suggestion} onClick={() => onCopy?.(it.id)} />
                  <Button variant="ghost" size={28} label="忽略" disabled={!!it.action && it.action !== 'pending'} onClick={() => onIgnore?.(it.id)} />
                </div>
              </>
            )}
          </div>
        );
      })}
    </Card>
  );
}
