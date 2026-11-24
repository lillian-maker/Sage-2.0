import { useState } from 'react';
import { useSubmit } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { TextField } from '../../components/TextField/TextField';
import { PlanStepRow } from './PlanStepRow';
import { TaskBriefCard } from './TaskBriefCard';
import { apiMessage, avatarOf, stepStatus, taskApi, type Kickoff } from './model';
import './detail.css';

const COMPLEXITY: Record<string, string> = { simple: '简单任务', normal: '一般任务', complex: '复杂任务' };

export interface KickoffPanelProps {
  kickoff: Kickoff;
  onStarted: () => void;
  onCancel: () => void;
  onChanged: () => void;
  onInstall: (extensionId: string) => void;
}

export function KickoffPanel({ kickoff, onStarted, onCancel, onChanged, onInstall }: KickoffPanelProps) {
  const id = kickoff.task_id;
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [plan, setPlan] = useState(false);
  const [adding, setAdding] = useState(false);
  const [criterion, setCriterion] = useState('');
  const start = useSubmit(() =>
    taskApi.start(
      id,
      Object.entries(answers).map(([question_id, answer]) => ({ question_id, answer })),
    ),
  );
  const answer = useSubmit((qid: string, value: string) => taskApi.answer(id, qid, value));
  const add = useSubmit(async (text: string) => {
    await taskApi.addCriterion(id, text);
    setCriterion('');
    setAdding(false);
    onChanged();
  });

  const b = kickoff.brief;
  const constraints = b.constraints && (b.rule_version ? `${b.constraints}（工作区规则 v${b.rule_version.number}）` : b.constraints);
  const description = [b.goal, b.scope_text && `范围：${b.scope_text}`, b.deliverables && `交付物：${b.deliverables}`, constraints && `约束：${constraints}`]
    .filter(Boolean)
    .join('\n');
  const minutes = kickoff.estimated_duration_minutes;
  const subtitle = [
    kickoff.complexity ? COMPLEXITY[kickoff.complexity] ?? kickoff.complexity : '',
    kickoff.estimated_expert_count ? `预计 ${kickoff.estimated_expert_count} 位专家` : '',
    minutes ? `约 ${minutes} 分钟` : '',
  ]
    .filter(Boolean)
    .join(' · ');
  const error = start.error ?? answer.error ?? add.error;
  const started = kickoff.status !== 'awaiting_kickoff';

  return (
    <div className="sg-kickoff">
      <TaskBriefCard
        brief={{
          subtitle,
          questions: kickoff.questions.map((q) => ({
            question: q.question,
            options: q.options,
            value: answers[q.id] ?? q.answer ?? '',
            confirmed: !!answers[q.id] || !!q.answered_at,
          })),
          description: description || '主责还在整理任务说明',
          fields: [
            { label: '目标', value: b.goal ?? '' },
            { label: '范围', value: b.scope_text ?? '' },
            { label: '交付物', value: b.deliverables ?? '' },
            { label: '约束', value: constraints ?? '' },
          ].filter((f) => f.value),
          criteria: kickoff.criteria.map((c) => c.text),
          capabilities: kickoff.missing_capabilities.map((c) => {
            // Connectors only ever get read-only access (Extension.access has the single level read_only).
            const connector = c.extension?.kind === 'connector';
            return {
              icon: 'puzzle' as const,
              name: c.extension?.name ?? c.title,
              kind: connector ? '连接器 · 只读' : c.extension?.kind === 'skill' ? '技能' : '扩展',
              action: connector ? '连接' : '安装',
              ...(c.extension ? { onAction: () => onInstall(c.extension!.id) } : {}),
            };
          }),
          planSummary: `执行计划 · ${kickoff.plan.step_count} 步 · ${kickoff.plan.expert_count} 位专家（展开查看与调整）`,
          ...(!started && kickoff.missing_capabilities.length ? { hint: '不安装也可以开始，相关步骤会标为“缺少能力”' } : {}),
        }}
        readOnly={started}
        starting={start.submitting}
        onAnswer={(i, value) => {
          const q = kickoff.questions[i];
          if (!q) return;
          setAnswers((a) => ({ ...a, [q.id]: value }));
          void answer.submit(q.id, value);
        }}
        onAddCriterion={() => setAdding(true)}
        onTogglePlan={() => setPlan((p) => !p)}
        onCancel={onCancel}
        onStart={async () => {
          if (started) return onStarted();
          const r = await start.submit();
          if (r !== undefined) onStarted();
        }}
      />
      {adding && (
        <div className="sg-kickoff__add">
          <TextField size={32} value={criterion} placeholder="写一条可检验的验收标准" onValueChange={setCriterion} aria-label="新的验收标准" />
          <Button variant="ghost" size={28} label="取消" onClick={() => setAdding(false)} />
          <Button variant="primary" size={28} label="添加" busy={add.submitting} disabled={!criterion.trim()} onClick={() => add.submit(criterion.trim())} />
        </div>
      )}
      {plan && (
        <div className="sg-kickoff__plan">
          {kickoff.plan.steps.map((s) => (
            <PlanStepRow
              key={s.id}
              layout="table"
              status={stepStatus(s.status)}
              title={`${s.order}. ${s.title}`}
              {...(s.skill ? { meta: s.skill.name } : {})}
              {...(s.expert ? { expert: avatarOf(s.expert.name, s.expert.avatar_ref) } : {})}
            />
          ))}
        </div>
      )}
      {error && <InlineNotice icon="info" tone="error" text={apiMessage(error, '没有成功，请重试')} />}
    </div>
  );
}
