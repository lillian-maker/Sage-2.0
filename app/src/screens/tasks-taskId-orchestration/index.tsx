import { useLayoutEffect, useMemo, useRef, useState, type MutableRefObject, type RefObject } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Avatar } from '../../components/Avatar/Avatar';
import { Button } from '../../components/Button/Button';
import { IconButton } from '../../components/IconButton/IconButton';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { SegmentedControl } from '../../components/SegmentedControl/SegmentedControl';
import { EmptyState, ErrorBanner, ForbiddenState, LoadingState, errorMessage } from '../../components/States/States';
import { Tag } from '../../components/Tag/Tag';
import { AuthorizationBlock } from '../../features/approvals/AuthorizationBlock';
import { ChecklistItem } from '../../features/task/ChecklistItem';
import { StepStatusIcon } from '../../features/task/PlanStepRow';
import { STAGES, apiMessage, avatarOf, durationText, stepStatus, taskApi, type TaskStep } from '../../features/task/model';
import '../../features/task/review.css';
import './orchestration.css';

const STATUS_TEXT: Record<string, string> = {
  done: '已完成',
  running: '进行中',
  awaiting_user: '待你确认',
  queued: '排队',
  blocked: '受阻',
  failed: '失败',
  skipped: '已跳过',
  missing_capability: '缺少能力',
};
const COMPLEXITY: Record<string, string> = { simple: '简单', normal: '一般', complex: '复杂' };

function inputLabel(i: TaskStep['inputs'][number]): string {
  return typeof i === 'string' ? i : i.title ?? i.label ?? '';
}

const LIVE_STEP = new Set(['running', 'awaiting_user']);

interface Edge { from: string; to: string; d: string; current: boolean }

/** Dependency connectors between node buttons, measured from the rendered columns. */
function useEdges(items: TaskStep[], box: RefObject<HTMLDivElement | null>, nodes: MutableRefObject<Map<string, HTMLElement>>) {
  const [edges, setEdges] = useState<Edge[]>([]);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => {
      const origin = el.getBoundingClientRect();
      const byId = new Map(items.map((s) => [s.id, s]));
      const next: Edge[] = [];
      for (const s of items) {
        const to = nodes.current.get(s.id)?.getBoundingClientRect();
        for (const d of s.depends_on) {
          const from = nodes.current.get(d)?.getBoundingClientRect();
          if (!from || !to) continue;
          const sameColumn = Math.abs(from.left - to.left) < 4;
          const [x1, y1, x2, y2] = sameColumn
            ? [from.left + from.width / 2, from.bottom, to.left + to.width / 2, to.top]
            : [from.right, from.top + from.height / 2, to.left, to.top + to.height / 2];
          const ox = x1 - origin.left, oy = y1 - origin.top, tx = x2 - origin.left, ty = y2 - origin.top;
          const mid = sameColumn ? `C ${ox} ${(oy + ty) / 2}, ${tx} ${(oy + ty) / 2}` : `C ${(ox + tx) / 2} ${oy}, ${(ox + tx) / 2} ${ty}`;
          const current = LIVE_STEP.has(s.status) || LIVE_STEP.has(byId.get(d)?.status ?? '');
          next.push({ from: d, to: s.id, d: `M ${ox} ${oy} ${mid}, ${tx} ${ty}`, current });
        }
      }
      setEdges(next);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [items, box, nodes]);
  return edges;
}

export default function Screen({ params, query, navigate }: ScreenProps) {
  const id = params.taskId ?? '';
  const task = useApiQuery((s) => taskApi.detail(id, s), [id]);
  const steps = useApiQuery((s) => taskApi.steps(id, s), [id]);
  const items = steps.data?.items ?? [];
  const selectedId = query.get('step');
  const selected = items.find((s) => s.id === selectedId) ?? null;
  const criteria = useApiQuery((s) => (selected ? taskApi.criteria(id, s, selected.id) : Promise.resolve(null)), [id, selected?.id]);
  const control = useSubmit(async (fn: () => Promise<unknown>) => {
    await fn();
    task.reload();
    steps.reload();
  });
  const byId = useMemo(() => new Map(items.map((s) => [s.id, s])), [items]);
  const colsRef = useRef<HTMLDivElement | null>(null);
  const nodeRefs = useRef(new Map<string, HTMLElement>());
  const edges = useEdges(items, colsRef, nodeRefs);
  const select = (sid: string | null) => navigate(`/tasks/${id}/orchestration${sid ? `?step=${sid}` : ''}`, { replace: true });

  if (task.status === 'error' && !task.data) {
    return (
      <div className="sg-orch__state">
        {task.error.status === 404 || task.error.status === 403 ? (
          <ForbiddenState title="任务不存在或你没有访问权限" action={{ label: '回到进行中', onClick: () => navigate('/tasks/active') }} />
        ) : (
          <ErrorBanner message={errorMessage(task.error, '任务暂时无法读取')} onRetry={task.reload} />
        )}
      </div>
    );
  }
  if (!task.data) return <div className="sg-orch__state"><LoadingState rows={4} /></div>;
  const t = task.data;
  const isOwner = t.owner?.is_me ?? false;
  const paused = t.status === 'paused';
  const live = ['running', 'awaiting_user', 'stuck'].includes(t.status);

  return (
    <div className="sg-orch">
      <header className="sg-orch__head">
        <h1 className="truncate">{t.title}</h1>
        <SegmentedControl
          label="视图"
          value="orchestration"
          options={[
            { value: 'chat', label: '对话' },
            { value: 'orchestration', label: '编排' },
          ]}
          onChange={(v) => v === 'chat' && navigate(`/tasks/${id}`)}
        />
        <span className="sg-orch__stats">
          <span>进度 <b>{t.progress.done} / {t.progress.total}</b> 步</span>
          {t.complexity && <Tag label={COMPLEXITY[t.complexity] ?? t.complexity} tone="warning" size={20} />}
          <span>已用 <b>{t.used_credits}</b> Credits</span>
          <span>重新规划 <b>{t.replan_count}</b> 次</span>
          {(live || paused) && (
            <Button
              variant="secondary"
              size={30}
              label={paused ? '继续' : '暂停'}
              busy={control.submitting}
              disabled={!isOwner}
              onClick={() => control.submit(() => (paused ? taskApi.resume(id) : taskApi.pause(id)))}
            />
          )}
        </span>
      </header>
      {control.error && <div className="sg-orch__notice"><InlineNotice icon="info" tone="error" text={apiMessage(control.error, '操作没有成功，请重试')} /></div>}
      <div className="sg-orch__body">
        <div className="sg-orch__board">
          {steps.status === 'error' && <ErrorBanner message={errorMessage(steps.error, '执行图暂时无法读取')} onRetry={steps.reload} />}
          {steps.status === 'loading' && !steps.data && <LoadingState rows={4} />}
          {steps.data && items.length === 0 && <EmptyState icon="layers" title="执行图还没有生成" description="主责确认任务说明后会拆出步骤" />}
          {items.length > 0 && (
            <div className="sg-orch__cols" ref={colsRef}>
              <svg className="sg-orch__edges" aria-hidden="true">
                {edges.map((e) => (
                  <path key={`${e.from}-${e.to}`} d={e.d} className={e.current ? 'is-current' : undefined} />
                ))}
              </svg>
              {STAGES.map((stage) => (
                <section key={stage.key} className="sg-orch__col" aria-label={stage.label}>
                  <h2>{stage.label}</h2>
                  {items
                    .filter((s) => s.stage === stage.key)
                    .map((s) => {
                      const st = stepStatus(s.status);
                      const credits = s.used_credits || s.estimated_credits;
                      return (
                        <button
                          key={s.id}
                          ref={(el) => {
                            if (el) nodeRefs.current.set(s.id, el);
                            else nodeRefs.current.delete(s.id);
                          }}
                          type="button"
                          className={`sg-orch__node is-${st}${s.id === selected?.id ? ' is-selected' : ''}`}
                          aria-pressed={s.id === selected?.id}
                          onClick={() => select(s.id === selected?.id ? null : s.id)}
                        >
                          <span className="sg-orch__nodehead">
                            {s.expert && <Avatar {...avatarOf(s.expert.name, s.expert.avatar_ref)} size={20} />}
                            <span className="truncate">{s.label || s.skill?.name || s.expert?.name || ''}</span>
                            <StepStatusIcon status={st} size={16} />
                          </span>
                          <span className="sg-orch__nodetitle">{s.title}</span>
                          <span className="sg-orch__nodemeta">
                            <span>{STATUS_TEXT[s.status] ?? s.status}</span>
                            {credits ? <span>{s.used_credits ? '' : '约 '}{credits}</span> : null}
                          </span>
                        </button>
                      );
                    })}
                </section>
              ))}
            </div>
          )}
          <footer className="sg-orch__legend">
            <span><StepStatusIcon status="done" size={14} /> 已完成</span>
            <span><StepStatusIcon status="active" size={14} /> 进行中</span>
            <span><StepStatusIcon status="waiting" size={14} /> 待你确认</span>
            <span><StepStatusIcon status="queued" size={14} /> 排队</span>
            {edges.length > 0 && <span><i className="sg-orch__legendline" /> 主色连线 = 当前执行路径</span>}
          </footer>
        </div>
        {selected && (
          <aside className="sg-orch__detail" aria-label="步骤详情">
            <div className="sg-orch__detailhead">
              {selected.expert && <Avatar {...avatarOf(selected.expert.name, selected.expert.avatar_ref)} size={28} />}
              <div className="sg-orch__detailtext">
                <h2>{selected.title}</h2>
                <p>
                  {[selected.expert?.name, selected.label || selected.skill?.name, `${STAGES.find((x) => x.key === selected.stage)?.label ?? selected.stage}阶段`]
                    .filter(Boolean)
                    .join(' · ')}
                </p>
              </div>
              <IconButton icon="x" size={28} variant="ghost" label="关闭详情" onClick={() => select(null)} />
            </div>
            {selected.authorization_id && <AuthorizationBlock id={selected.authorization_id} compact onDecided={steps.reload} />}
            {selected.required_extension && (
              <InlineNotice icon="puzzle" tone="info" text={`需要扩展：${selected.required_extension.name}`} action="去安装" onAction={() => navigate(`/extensions/${selected.required_extension!.id}`)} />
            )}
            <section>
              <h3 className="sg-section-label">
                {selected.inputs.some((i) => typeof i !== 'string' && (i as { source?: string }).source === 'knowledge_graph') ? '输入依据 · 来自知识图谱' : '输入依据'}
              </h3>
              {selected.inputs.length === 0 && selected.depends_on.length === 0 ? (
                <p className="sg-orch__muted">没有额外输入</p>
              ) : (
                <div className="sg-orch__inputs">
                  {selected.inputs.map((i, n) => (
                    <Tag key={n} label={inputLabel(i)} tone="neutral" size={22} />
                  ))}
                  {selected.depends_on.map((d) => {
                    const up = byId.get(d);
                    // Only finished upstream steps have an output to feed in; running ones are listed under 依赖.
                    return up && up.status === 'done' ? (
                      <span key={d} className="sg-orch__upstream">
                        <Tag label={`${up.title} · 上一步产出`} tone="outline" size={22} icon="file" />
                        <span>{STATUS_TEXT[up.status]}</span>
                      </span>
                    ) : null;
                  })}
                </div>
              )}
            </section>
            <section>
              <h3 className="sg-section-label">验收条件</h3>
              {criteria.status === 'error' && <ErrorBanner message={errorMessage(criteria.error, '验收条件暂时无法读取')} onRetry={criteria.reload} />}
              {criteria.data && criteria.data.items.length === 0 && <p className="sg-orch__muted">这一步没有单独的验收条件</p>}
              {criteria.data?.items.map((c) => (
                <ChecklistItem key={c.id} checked={c.verdict === 'met'} label={c.text} {...(c.evidence ? { evidence: c.evidence } : {})} />
              ))}
            </section>
            <section>
              <h3 className="sg-section-label">依赖</h3>
              <dl className="sg-orch__deps">
                <div>
                  <dt>上游</dt>
                  <dd>{selected.depends_on.map((d) => byId.get(d)?.title).filter(Boolean).join('、') || '无'}</dd>
                </div>
                <div>
                  <dt>下游</dt>
                  <dd>{items.filter((s) => s.depends_on.includes(selected.id)).map((s) => s.title).join('、') || '无'}</dd>
                </div>
              </dl>
            </section>
            <section>
              <h3 className="sg-section-label">运行日志</h3>
              <dl className="sg-orch__deps">
                <div><dt>状态</dt><dd>{STATUS_TEXT[selected.status] ?? selected.status}</dd></div>
                {selected.activity && <div><dt>当前</dt><dd>{selected.activity}</dd></div>}
                {selected.duration_seconds ? <div><dt>用时</dt><dd>{durationText(selected.duration_seconds)}</dd></div> : null}
                <div><dt>消耗</dt><dd>{selected.used_credits ? `${selected.used_credits} Credits` : selected.estimated_credits ? `预计 ${selected.estimated_credits} Credits` : '—'}</dd></div>
              </dl>
            </section>
          </aside>
        )}
      </div>
    </div>
  );
}
