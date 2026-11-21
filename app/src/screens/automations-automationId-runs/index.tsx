import { useEffect, useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { BackLink } from '../../components/BackLink/BackLink';
import { Button } from '../../components/Button/Button';
import { EmptyState, ErrorBanner, LoadingState, errorMessage } from '../../components/States/States';
import { Tag } from '../../components/Tag/Tag';
import { automationsApi } from '../../features/automations/api';
import { MISSED_REASON, RUN_TAG, TRIGGER_TEXT, clockTime, dayTime, durationText, relativeDay, runResultText, scheduleText } from '../../features/automations/format';
import type { RunBrief } from '../../features/automations/api';
import '../../features/automations/automations.css';
import { PlanStepRow } from '../../features/task/PlanStepRow';
import { avatarOf, stepStatus } from '../../features/task/model';
import { RUN_DEMOS, type RunDemo } from '../../features/inbox/RecoveryDemo';
import { NotificationBell } from '../../components/NotificationBell/NotificationBell';

/** 收件恢复演示对应的运行详情状态（3128:3115、3128:3472、3311:3761），只改显示，不写数据。 */
const RUN_DEMO: Record<RunDemo, { tag: string; tone: 'warning'; step: string; inbox: string; end: boolean }> = {
  not_submitted: { tag: '未提交 · 虚构示例', tone: 'warning', step: '未提交 · 虚构示例：使用 Keepa 读取历史价格（只读）', inbox: 'not_submitted', end: true },
  unknown: { tag: '结果未知 · 虚构示例', tone: 'warning', step: '结果未知 · 虚构示例：使用 Keepa 读取历史价格（只读）', inbox: 'unknown', end: true },
  rejected: { tag: '已拒绝', tone: 'warning', step: '已拒绝 · 后续处理待确认：使用 Keepa 读取历史价格（只读）', inbox: 'rejected_processing', end: false },
};

function RunItem({ run, selected, onSelect }: { run: RunBrief; selected: boolean; onSelect: () => void }) {
  const finished = run.state === 'succeeded' || run.state === 'failed';
  return (
    <button type="button" className={`sg-autoruns__item${selected ? ' is-selected' : ''}`} onClick={onSelect}>
      <span className="sg-autoruns__title truncate">{relativeDay(run.scheduled_for ?? run.started_at)}</span>
      <span className="sg-autoruns__meta">
        {finished && run.state === 'failed' ? (
          runResultText(run)
        ) : (
          <Tag {...(RUN_TAG[run.state] ?? { label: run.state, tone: 'neutral' as const, size: 18 as const })} />
        )}
        {run.missed_reason ? ` · ${MISSED_REASON[run.missed_reason] ?? run.missed_reason}` : ''}
      </span>
    </button>
  );
}

export default function Screen({ params, query, navigate }: ScreenProps) {
  const id = params.automationId ?? '';
  const automation = useApiQuery((s) => automationsApi.detail(id, s), [id]);
  const runs = useApiQuery((s) => automationsApi.runs(id, s), [id]);
  const [selected, setSelected] = useState<string | null>(query.get('run'));
  const demoParam = query.get('demo') as RunDemo | null;
  const demo = demoParam && RUN_DEMOS.includes(demoParam) ? RUN_DEMO[demoParam] : null;

  useEffect(() => {
    if (selected || !runs.data?.items[0]) return;
    // 恢复演示围绕停在等待授权的那次运行（11月1日），不是最近一次。
    const waiting = demo ? runs.data.items.find((r) => r.state === 'awaiting_user') : undefined;
    setSelected((waiting ?? runs.data.items[0]).id);
  }, [runs.data, selected, demo]);

  const runDetail = useApiQuery((s) => (selected ? automationsApi.run(selected, s) : Promise.resolve(null)), [selected]);

  if (automation.status === 'error' && !automation.data)
    return (
      <div className="sg-taskpage__state">
        <ErrorBanner message={errorMessage(automation.error, '这条规则暂时无法读取')} onRetry={automation.reload} />
      </div>
    );

  const a = automation.data?.automation;
  const run = runDetail.data?.run ?? null;

  return (
    <div className="sg-autoruns">
      <aside className="sg-autoruns__list">
        <BackLink label="运行记录" size={30} onBack={() => navigate('/automations')} />
        {a && (
          <div style={{ padding: '8px 12px 12px' }}>
            <div className="sg-autoruns__title">{a.name}</div>
            <div className="sg-autoruns__meta">{scheduleText(a.schedule)}</div>
          </div>
        )}
        {runs.status === 'loading' && !runs.data && <LoadingState rows={3} />}
        {runs.status === 'error' && !runs.data && <ErrorBanner message={errorMessage(runs.error, '运行记录暂时无法读取')} onRetry={runs.reload} />}
        {runs.data && runs.data.items.length === 0 && <EmptyState icon="timer" title="还没有运行记录" />}
        {(runs.data?.items ?? []).map((r, index) => (
          <div key={r.id}>
            {index === 0 && <div className="sg-autoruns__group">本次运行</div>}
            {index === 1 && <div className="sg-autoruns__group">最近运行</div>}
            <RunItem run={r} selected={selected === r.id} onSelect={() => setSelected(r.id)} />
          </div>
        ))}
      </aside>
      <div className="sg-autorun">
        {!selected && runs.data && runs.data.items.length > 0 && <LoadingState rows={3} />}
        {selected && runDetail.status === 'loading' && !run && <LoadingState rows={4} />}
        {selected && runDetail.status === 'error' && !run && (
          <ErrorBanner message={errorMessage(runDetail.error, '这次运行暂时无法读取')} onRetry={runDetail.reload} />
        )}
        {run && (
          <>
            <div className="sg-incard__row" style={{ justifyContent: 'space-between' }}>
              <div>
                <h2 className="sg-indetail__title">{`${dayTime(run.scheduled_for ?? run.started_at)} 的运行`}</h2>
                <div className="sg-autoruns__meta" style={{ marginTop: 4 }}>
                  {run.title}
                </div>
              </div>
              <div className="sg-incard__row" style={{ gap: 8 }}>
                {demo && <Tag label={demo.tag} tone={demo.tone} size={20} onClick={() => navigate(`/inbox?filter=decide&demo=${demo.inbox}`)} />}
                {!demo && run.pending_authorization_count > 0 && (
                  <Button variant="primary" size={30} label={`等你确认 · ${run.pending_authorization_count} 项`} onClick={() => navigate('/inbox?filter=decide')} />
                )}
                {run.task_id && <Button variant="secondary" size={30} label="打开任务" trailingIcon="chevron-right" onClick={() => navigate(`/tasks/${run.task_id}`)} />}
                {demo?.end && <Button variant="secondary" size={30} label="结束演示" onClick={() => navigate('/inbox?filter=decide&demo=recovery')} />}
                <NotificationBell onClick={() => navigate('/notifications')} />
              </div>
            </div>
            {runDetail.status === 'error' && <ErrorBanner message={errorMessage(runDetail.error, '刷新失败')} onRetry={runDetail.reload} />}
            <div className="sg-autorun__summary">
              <div className="sg-autorun__cell">
                <span className="sg-autorun__num">{`${TRIGGER_TEXT[run.trigger] ?? run.trigger} · ${scheduleText(run.automation.schedule)}`}</span>
                <span className="sg-autorun__label">触发</span>
              </div>
              <div className="sg-autorun__cell">
                <span className="sg-autorun__num">{clockTime(run.started_at ?? run.scheduled_for)}</span>
                <span className="sg-autorun__label">开始</span>
              </div>
              <div className="sg-autorun__cell">
                <span className="sg-autorun__num">{durationText(run.elapsed_seconds)}</span>
                <span className="sg-autorun__label">已用时</span>
              </div>
              <div className="sg-autorun__cell">
                <span className="sg-autorun__num">{run.credits_used != null ? `${run.credits_used} Credits` : '—'}</span>
                <span className="sg-autorun__label">已消耗</span>
              </div>
              <div className="sg-autorun__cell">
                <span className="sg-autorun__num">完成后放入“成果 › 自动化”</span>
                <span className="sg-autorun__label">产出</span>
              </div>
              <div className="sg-autorun__cell">
                <span className="sg-autorun__num">
                  <Tag {...(RUN_TAG[run.state] ?? { label: run.state, tone: 'neutral' as const, size: 18 as const })} />
                </span>
                <span className="sg-autorun__label">状态</span>
              </div>
            </div>
            <section>
              <div className="sg-indetail__section-label">步骤</div>
              <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
                {run.steps.length === 0 && <EmptyState icon="layers" title="这次运行还没有步骤" />}
                {run.steps.map((s) => (
                  <PlanStepRow
                    key={s.id}
                    layout="table"
                    status={stepStatus(s.status)}
                    title={s.title}
                    {...(s.status === 'awaiting_user' && demo
                      ? { meta: demo.step }
                      : s.status === 'awaiting_user' && s.authorization
                        ? { meta: `等你确认：${s.authorization.title || s.authorization.scope_text || ''}` }
                        : {})}
                    {...(s.expert ? { expert: avatarOf(s.expert.name, s.expert.avatar_ref) } : {})}
                    credits={s.credits_used ? `${s.credits_used} Credits` : ''}
                    duration={durationText(s.duration_seconds)}
                  />
                ))}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
