import { Avatar, type AvatarProps } from '../../components/Avatar/Avatar';
import { Icon } from '../../components/Icon/Icon';
import { Tag, type TagProps } from '../../components/Tag/Tag';
import './task.css';

export type StepStatus = 'done' | 'active' | 'waiting' | 'queued';

export interface PlanStepRowProps {
  layout: 'rail' | 'table' | 'template';
  status: StepStatus;
  title: string;
  meta?: string;
  expert?: Omit<AvatarProps, 'size'>;
  credits?: string;
  duration?: string;
  variable?: TagProps;
}

export function StepStatusIcon({ status, size = 18 }: { status: StepStatus; size?: number }) {
  if (status === 'done') return <Icon name="circle-check-soft" size={size} color="color.green.660" label="已完成" />;
  if (status === 'waiting') return <Icon name="circle-pending" size={size} color="color.orange.660" label="等你处理" />;
  if (status === 'active') return <Icon name="circle-progress" size={size} color="accent" label="进行中" />;
  return <Icon name="circle" size={size} color="color.slate.230" label="排队中" />;
}

export function PlanStepRow({ layout, status, title, meta, expert, credits, duration, variable }: PlanStepRowProps) {
  if (layout === 'rail') {
    return (
      <div className={`sg-step-rail is-${status}`}>
        <span className="sg-step-rail__icon">
          <StepStatusIcon status={status} />
        </span>
        <div className="sg-step__text">
          <span className="sg-step__title">{title}</span>
          {meta && <span className="sg-step__meta">{meta}</span>}
        </div>
      </div>
    );
  }
  return (
    <div className={`sg-step-row sg-step-row--${layout} is-${status}`}>
      <StepStatusIcon status={status} />
      {expert && (
        <span className="sg-step-row__avatar">
          <Avatar {...expert} size={24} />
        </span>
      )}
      <div className="sg-step__text">
        <span className="sg-step__title">{title}</span>
        {meta && <span className="sg-step__meta">{meta}</span>}
      </div>
      <span className="sg-step-row__spacer" />
      {variable && <Tag {...variable} />}
      {credits !== undefined && <span className="sg-step-row__num">{credits}</span>}
      {duration !== undefined && <span className="sg-step-row__num">{duration}</span>}
    </div>
  );
}
