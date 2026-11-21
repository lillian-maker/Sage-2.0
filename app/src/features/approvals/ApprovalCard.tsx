import { useState } from 'react';
import { ActionBar } from '../../components/ActionBar/ActionBar';
import { Button } from '../../components/Button/Button';
import type { ButtonProps } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { Menu, MenuItem, Popover } from '../../components/Menu/MenuItem';
import './approvals.css';

export interface ApprovalRequest {
  title: string;
  /** Rows of the detail grid (发起 / 动作 / 范围 / 预计消耗 / 有效期). */
  details: Array<{ label: string; value: string }>;
  statusLabel?: string;
}

export interface ApprovalScope {
  value: string;
  label: string;
  hint?: string;
}

export interface ApprovalCardProps {
  status: 'pending' | 'approved' | 'processing' | 'rejected';
  layout: 'task' | 'inbox';
  request: ApprovalRequest;
  onApprove?: () => void;
  onRevise?: () => void;
  onReject?: () => void;
  onEstimate?: () => void;
  onRequestInfo?: () => void;
  onCancel?: () => void;
  onOpenTask?: () => void;
  busy?: boolean;
  /** Submitting the decision: keeps the pending buttons, marks the whole card busy (inbox submit). */
  submitting?: boolean;
  /** Approval-scope choices (Figma 672:2 #scope); adds a “批准范围” dropdown next to 批准. */
  scopes?: ApprovalScope[];
  onApproveScope?: (value: string) => void;
  /** The scope changed and credits must be re-estimated before approving. */
  estimatePending?: boolean;
}

const LABEL = { pending: '需要你确认', approved: '已批准', processing: '处理中', rejected: '已拒绝' } as const;
/** A decided request inside the task names what was decided: the data use, not the request in general. */
const TASK_DECIDED = { approved: '已批准本次数据使用', rejected: '已拒绝本次数据使用' } as const;

export function ApprovalCard({
  status,
  layout,
  request,
  onApprove,
  onRevise,
  onReject,
  onEstimate,
  onRequestInfo,
  onCancel,
  onOpenTask,
  busy,
  submitting,
  scopes,
  onApproveScope,
  estimatePending,
}: ApprovalCardProps) {
  const [scopeAt, setScopeAt] = useState<{ x: number; y: number } | null>(null);
  const decided = status === 'approved' || status === 'rejected';
  const lock = !!busy || !!submitting;

  let actions: ButtonProps[];
  let extra: Array<{ label: string; onClick?: () => void }> | undefined;
  if (status === 'processing') {
    actions = [
      { variant: 'primary', size: 32, icon: 'check', label: '批准', onClick: onApprove, busy },
      { variant: 'ghost', size: 32, label: '拒绝', onClick: onReject },
      { variant: 'primary', size: 32, label: '请求补充信息', onClick: onRequestInfo },
      { variant: 'secondary', size: 32, label: '取消', onClick: onCancel },
    ];
  } else {
    actions = [
      { variant: 'primary', size: 32, icon: 'check', label: '批准', onClick: onApprove, disabled: decided || !!estimatePending || !!submitting, busy: busy || submitting },
      { variant: 'secondary', size: 32, label: '修改范围', onClick: onRevise, disabled: decided || !!submitting },
    ];
    if (status === 'pending') {
      extra = [{ label: '拒绝', onClick: onReject }];
      if (estimatePending) actions.push({ variant: 'secondary', size: 32, label: '重试估算', onClick: onEstimate, disabled: !!submitting });
    }
  }
  const scopeMenu = !decided && scopes && scopes.length > 0 && onApproveScope;
  return (
    <section className={`sg-approval sg-approval--${layout} is-${status}${submitting ? ' is-submitting' : ''}`} aria-busy={submitting || busy || undefined}>
      <div className="sg-approval__status">
        <Icon name="info" size={14} />
        {request.statusLabel ?? (layout === 'task' && (status === 'approved' || status === 'rejected') ? TASK_DECIDED[status] : LABEL[status])}
      </div>
      <p className="sg-approval__title">{request.title}</p>
      <dl className="sg-approval__grid">
        {request.details.map((d) => (
          <div key={d.label}>
            <dt>{d.label}</dt>
            <dd>{d.value}</dd>
          </div>
        ))}
      </dl>
      <div className="sg-approval__footer">
        <ActionBar variant="decision" actions={actions} extraActions={extra} />
        {scopeMenu && (
          <Button
            variant="ghost"
            size={32}
            label="批准范围"
            trailingIcon="chevron-down"
            aria-expanded={!!scopeAt}
            disabled={lock || !!estimatePending}
            onClick={(e) => {
              const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
              setScopeAt({ x: r.left, y: r.bottom + 4 });
            }}
          />
        )}
        {layout === 'inbox' && (
          <button type="button" className="sg-approval__open" onClick={onOpenTask}>
            在任务中打开
            <Icon name="chevron-right" size={12} />
          </button>
        )}
      </div>
      {scopeMenu && scopeAt && (
        <Popover at={scopeAt} placement="below" width={280} onClose={() => setScopeAt(null)}>
          <Menu label="批准范围">
            {scopes.map((s, i) => (
              <MenuItem
                key={s.value}
                icon={i === 0 ? 'check' : 'layers'}
                label={s.label}
                hint={s.hint}
                onSelect={() => {
                  setScopeAt(null);
                  onApproveScope(s.value);
                }}
              />
            ))}
          </Menu>
        </Popover>
      )}
      {status === 'approved' && layout === 'task' && <div className="sg-approval__note">已批准，已从待处理队列移除。</div>}
      {status === 'rejected' && layout === 'task' && <div className="sg-approval__note">已拒绝，已从待处理队列移除。</div>}
    </section>
  );
}
