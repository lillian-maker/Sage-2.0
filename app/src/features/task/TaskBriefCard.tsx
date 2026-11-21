import { ActionBar } from '../../components/ActionBar/ActionBar';
import { Card, CardHeader } from '../../components/Card/CardHeader';
import { ChoiceChipGroup } from '../../components/ChoiceChip/ChoiceChipGroup';
import { Icon } from '../../components/Icon/Icon';
import { CapabilityRow, type CapabilityRowProps } from '../extensions/CapabilityRow';
import './review.css';

export interface TaskBrief {
  subtitle: string;
  questions: Array<{ question: string; options: string[]; value: string; confirmed?: boolean }>;
  description: string;
  /** 目标 / 范围 / 交付物 / 约束 rows; when present they replace the plain description. */
  fields?: Array<{ label: string; value: string }>;
  criteria: string[];
  capabilities: CapabilityRowProps[];
  planSummary: string;
  hint?: string;
}

export interface TaskBriefCardProps {
  brief: TaskBrief;
  onStart?: () => void;
  onCancel?: () => void;
  onAnswer?: (index: number, value: string) => void;
  onAddCriterion?: () => void;
  onTogglePlan?: () => void;
  starting?: boolean;
}

export function TaskBriefCard({ brief, onStart, onCancel, onAnswer, onAddCriterion, onTogglePlan, starting }: TaskBriefCardProps) {
  const open = brief.questions.filter((q) => (q.confirmed === undefined ? !q.value : !q.confirmed)).length;
  return (
    <Card className="sg-brief">
      <CardHeader icon="check" title="任务说明" subtitle={brief.subtitle} />
      <div className="sg-brief__grid">
        <div className="sg-brief__col">
          <div className="sg-section-label">{open > 0 ? `1 · 先确认 ${open} 个问题` : '1 · 已确认的问题'}</div>
          {brief.questions.map((q, i) => (
            <div key={q.question} className="sg-brief__q">
              <span>{q.question}</span>
              <ChoiceChipGroup options={q.options} value={q.value} size={28} label={q.question} onChange={(v) => onAnswer?.(i, v)} />
            </div>
          ))}
          <div className="sg-section-label">2 · 任务说明（点击可修改）</div>
          {brief.fields?.length ? (
            <dl className="sg-brief__fields">
              {brief.fields.map((f) => (
                <div key={f.label}>
                  <dt>{f.label}</dt>
                  <dd>{f.value}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="sg-brief__desc">{brief.description}</p>
          )}
        </div>
        <div className="sg-brief__col sg-brief__col--right">
          <div className="sg-section-label">3 · 验收清单（满足才算完成，可增删）</div>
          <ol className="sg-brief__criteria">
            {brief.criteria.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ol>
          <button type="button" className="sg-brief__add" onClick={onAddCriterion}>
            <Icon name="plus" size={13} />
            添加一条
          </button>
          <div className="sg-section-label">4 · 需要补齐的能力</div>
          {brief.capabilities.map((c) => (
            <CapabilityRow key={c.name} {...c} />
          ))}
        </div>
      </div>
      <button type="button" className="sg-brief__plan" onClick={onTogglePlan}>
        <Icon name="chevron-down" size={13} />
        {brief.planSummary}
      </button>
      <ActionBar
        variant="footer"
        hint={brief.hint}
        actions={[
          { variant: 'ghost', size: 32, label: '取消', onClick: onCancel },
          { variant: 'primary', size: 32, label: '开始执行', onClick: onStart, busy: starting },
        ]}
      />
    </Card>
  );
}
