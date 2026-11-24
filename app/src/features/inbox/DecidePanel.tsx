import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { useSubmit } from '../../app/useApi';
import { Avatar } from '../../components/Avatar/Avatar';
import { Button } from '../../components/Button/Button';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { Icon } from '../../components/Icon/Icon';
import { errorMessage } from '../../components/States/States';
import { ApprovalCard } from '../approvals/ApprovalCard';
import { ReviseScopeDialog } from '../approvals/ReviseScopeDialog';
import { scopedPurpose } from '../approvals/purpose';
import { taskApi } from '../task/model';
import { inboxApi, type AuthorizationDetail, type Decision, type DecisionAuthorization } from './api';
import { ACTION_TEXT, DECISION_LABEL, VALIDITY_TEXT, chargeLabel, relative } from './format';

/** 提交后轮询的最大等待；超时后条目进入“结果未知”，由用户手动核对。 */
const DECIDE_TIMEOUT_MS = 8000;

type CardState =
  /** retry: the user came back from a failed or unconfirmed submission to choose again. */
  | { phase: 'idle'; retry?: boolean }
  | { phase: 'submitting'; kind: 'approve' | 'reject' | 'other'; retry?: boolean }
  | { phase: 'unknown' }
  | { phase: 'checking' }
  /** Checked after an unknown result: nothing was recorded on the server. */
  | { phase: 'not_submitted' }
  | { phase: 'failed' };

const SCOPE_ACTION: Record<string, string> = { read_query: '只读查询，不写入任何外部系统' };

function useAuthDetail(id: string) {
  const [auth, setAuth] = useState<AuthorizationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => {
    inboxApi
      .authorization(id)
      .then((a) => {
        setAuth(a);
        setError(null);
      })
      .catch((err: unknown) => setError(errorMessage(err, '授权详情暂时无法读取')));
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [id]);
  return { auth, error, reload: load, setAuth };
}

/** An approval decided in this session: the inbox keeps showing it after it leaves the pending list. */
export interface DecidedSnapshot {
  decision: Decision;
  auth: AuthorizationDetail | null;
  status: 'approved' | 'rejected';
}

interface TaskBrief {
  title: string;
  workspace: string | null;
  lead: string | null;
}

function useTaskBrief(taskId: string | null): TaskBrief | null {
  const [brief, setBrief] = useState<TaskBrief | null>(null);
  useEffect(() => {
    setBrief(null);
    if (!taskId) return;
    const ctrl = new AbortController();
    taskApi
      .detail(taskId, ctrl.signal)
      .then((t) => setBrief({ title: t.title, workspace: t.workspace?.name ?? null, lead: t.lead_expert?.name ?? null }))
      .catch(() => undefined);
    return () => ctrl.abort();
  }, [taskId]);
  return brief;
}

/** An estimate still being worked out (2732:1174 正在核对). */
function estimateChecking(state: string | undefined): boolean {
  return state === 'estimating' || state === 'pending';
}

/** revise leaves the request in checking: the new scope waits for 重新估算 (2732:797 待重新估算). */
function estimateNeeded(state: string | undefined): boolean {
  return state === 'checking' || state === 'unavailable';
}

function approvalDetails(a: DecisionAuthorization, auth: AuthorizationDetail | null, lead: string | null, decided?: 'approved' | 'rejected', estimating = false) {
  const requester = auth?.expert?.name ?? a.requested_by.expert;
  const owner = a.requested_by.task_owner;
  const state = auth?.estimate_state ?? a.estimate_state;
  const estimated = state === 'estimated' && (auth?.estimated_credits ?? a.estimated_credits) != null;
  const credits = auth?.estimated_credits ?? a.estimated_credits;
  // 2732:2305 / 2732:2682: a rejected request shows what was estimated, without the charge target.
  const cost = estimated
    ? decided === 'rejected'
      ? `约 ${credits} Credits（估算）`
      : `约 ${credits} Credits · ${chargeLabel(auth?.charge_target ?? a.charge_target)}`
    : decided === 'rejected'
      ? '未估算'
      : estimating || estimateChecking(state)
        ? '正在核对'
        : state === 'checking'
          ? '待重新估算'
          : '暂时无法估算';
  return [
    { label: '发起', value: `${requester}${lead && lead === requester ? '（主责专家）' : ''}${owner ? `${lead && lead === requester ? '' : ' '}· 任务负责人 ${owner}` : ''}` },
    { label: '动作', value: ACTION_TEXT[a.action_kind] ?? a.action_kind },
    { label: '范围', value: auth?.scope_text ?? a.scope_text },
    { label: '预计消耗', value: cost },
    { label: '有效期', value: VALIDITY_TEXT[a.validity] ?? a.validity },
  ];
}

interface EntryProps {
  decision: Decision;
  onChanged: () => void;
  onOpenTask: (taskId: string) => void;
  onOpenRun?: (automationId: string, runId: string) => void;
  onDecided?: (snapshot: DecidedSnapshot) => void;
  lead?: string | null;
}

function ApproveDecision({ decision, onChanged, onOpenTask, onOpenRun, onDecided, lead = null }: EntryProps) {
  const authId = decision.authorization!.id;
  const { auth, error, reload, setAuth } = useAuthDetail(authId);
  const [state, setState] = useState<CardState>({ phase: 'idle' });
  const [revising, setRevising] = useState(false);
  const [estimating, setEstimating] = useState(false);
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);

  const act = useSubmit(async (fn: () => Promise<unknown>, kind: 'approve' | 'reject' | 'other' = 'other') => {
    setState((s) => ({ phase: 'submitting', kind, retry: s.phase === 'idle' && !!s.retry }));
    timer.current = window.setTimeout(() => setState((s) => (s.phase === 'submitting' ? { phase: 'unknown' } : s)), DECIDE_TIMEOUT_MS);
    try {
      await fn();
    } catch (err) {
      // A decision the server answered (4xx/5xx) is known not to have taken effect; a dropped connection is not.
      const answered = err instanceof ApiError && err.status > 0;
      setState((s) =>
        s.phase !== 'submitting' && s.phase !== 'unknown' ? s : kind === 'other' ? { phase: 'idle' } : answered ? { phase: 'failed' } : { phase: 'unknown' },
      );
      throw err;
    } finally {
      if (timer.current) window.clearTimeout(timer.current);
    }
  });

  const finish = (status: 'approved' | 'rejected', latest: AuthorizationDetail | null = auth) => {
    setState({ phase: 'idle' });
    onDecided?.({ decision, auth: latest, status });
    onChanged();
  };

  const approve = () =>
    void act.submit(async () => {
      const version = auth?.version ?? 0;
      await inboxApi.approve(authId, version, crypto.randomUUID());
      finish('approved');
    }, 'approve');

  const reject = () =>
    void act.submit(async () => {
      await inboxApi.reject(authId, auth?.version ?? 0);
      finish('rejected');
    }, 'reject');

  const revise = (topN: number) =>
    void act.submit(async () => {
      await inboxApi.revise(authId, auth?.version ?? 0, topN);
      setRevising(false);
      // The request stays pending with the new scope (2732:797), so the card stays and re-reads it.
      reload();
      setState({ phase: 'idle' });
      onChanged();
    });

  const estimate = () => {
    setEstimating(true);
    void act.submit(async () => {
      await inboxApi.estimate(authId);
      reload();
      setState({ phase: 'idle' });
    });
  };
  const check = () => {
    setState({ phase: 'checking' });
    inboxApi
      .authorization(authId)
      .then((a) => {
        setAuth(a);
        if (a.decision === 'approved') finish('approved', a);
        else if (a.decision === 'rejected') finish('rejected', a);
        else setState({ phase: 'not_submitted' });
      })
      .catch(() => setState({ phase: 'unknown' }));
  };

  const a = decision.authorization!;
  const run = decision.target.automation_id && decision.target.automation_run_id && onOpenRun
    ? () => onOpenRun(decision.target.automation_id!, decision.target.automation_run_id!)
    : null;
  const reselect = () => setState({ phase: 'idle', retry: true });
  const scopeText = auth?.scope_text ?? a.scope_text;
  const details = approvalDetails(a, auth, lead, undefined, estimating);
  const estimateState = auth?.estimate_state ?? a.estimate_state;
  // 正在核对 lasts from 重新估算 until the re-read row or an error arrives.
  useEffect(() => setEstimating(false), [auth, act.error]);

  return (
    <div className="sg-decide__card">
      {error && <InlineNotice icon="info" tone="error" text={error} action="重试" onAction={reload} />}
      {act.error && state.phase !== 'failed' && <InlineNotice icon="info" tone="error" text={errorMessage(act.error, '提交失败，请重试')} />}
      {state.phase === 'submitting' && (
        <InlineNotice icon="loader" tone="info" text={state.kind === 'reject' && !state.retry ? '正在处理，请勿重复操作。' : '正在提交，请勿重复操作。'} />
      )}
      {state.phase === 'unknown' && (
        <div className="sg-decide__recover">
          <InlineNotice icon="info" tone="info" text="结果尚未确认，请先核对，避免重复提交。" action="核对状态" onAction={check} />
          {run && <Button variant="ghost" size={28} label="返回运行详情" onClick={run} />}
        </div>
      )}
      {state.phase === 'checking' && <InlineNotice icon="loader" tone="info" text="正在核对结果，请勿重复操作。" />}
      {state.phase === 'not_submitted' && (
        <div className="sg-decide__recover">
          <InlineNotice icon="info" tone="muted" text="已确认未提交，决定未生效，可重新选择。" />
          <Button variant="secondary" size={28} label="重新选择" onClick={reselect} />
          {run && <Button variant="ghost" size={28} label="返回运行详情" onClick={run} />}
        </div>
      )}
      {(state.phase === 'failed' || (state.phase === 'idle' && state.retry)) && (
        <div className="sg-decide__recover">
          <InlineNotice icon="info" tone="error" text="提交未成功，决定未生效；可重新选择。" />
          {state.phase === 'failed' ? (
            <Button variant="secondary" size={28} label="重新选择" onClick={reselect} />
          ) : (
            <Button variant="ghost" size={28} label="返回未提交" onClick={() => setState({ phase: 'not_submitted' })} />
          )}
        </div>
      )}
      <ApprovalCard
        status={state.phase === 'checking' ? 'processing' : 'pending'}
        submitting={state.phase === 'submitting' || state.phase === 'unknown' || state.phase === 'not_submitted' || state.phase === 'failed'}
        layout="inbox"
        request={{ title: scopedPurpose(auth?.purpose_text ?? a.purpose_text, auth?.scope_params ?? a.scope_params), details }}
        busy={state.phase === 'submitting'}
        estimatePending={estimating || estimateChecking(estimateState) || estimateNeeded(estimateState)}
        canEstimate={!estimating && estimateNeeded(estimateState)}
        estimateLabel={estimateState === 'checking' ? '重新估算' : '重试估算'}
        onApprove={approve}
        onReject={reject}
        onRevise={() => setRevising((v) => !v)}
        onEstimate={estimate}
        onOpenTask={() => decision.target.task_id && onOpenTask(decision.target.task_id)}
      />
      <p className="sg-decide__note">这条审批也会出现在任务详情里，两处状态同步；通知中心只提醒一次，不重复列出待处理事项。</p>
      {revising && (
        <ReviseScopeDialog
          scopeText={scopeText}
          actionText={SCOPE_ACTION[a.action_kind] ?? ACTION_TEXT[a.action_kind] ?? a.action_kind}
          options={auth?.scope_options ?? a.scope_options}
          currentTopN={(auth?.scope_params.top_n as number | undefined) ?? (a.scope_params.top_n as number | undefined)}
          estimateState={auth?.estimate_state ?? a.estimate_state}
          reestimateNote={(auth?.estimate_state ?? a.estimate_state) !== 'unavailable'}
          busy={act.submitting}
          onClose={() => setRevising(false)}
          onSubmit={revise}
        />
      )}
    </div>
  );
}

export function DecisionEntry({ decision, onChanged, onOpenTask, onOpenRun, onDecided, lead }: EntryProps) {
  if (decision.type === 'approve' && decision.authorization) {
    return (
      <ApproveDecision
        decision={decision}
        onChanged={onChanged}
        onOpenTask={onOpenTask}
        {...(onOpenRun ? { onOpenRun } : {})}
        {...(onDecided ? { onDecided } : {})}
        lead={lead ?? null}
      />
    );
  }
  const action =
    decision.type === 'accept' ? '去验收' : decision.type === 'invite' ? '查看任务' : '去补充';
  return (
    <div className="sg-decide__card">
      <section className="sg-approval sg-approval--inbox is-pending">
        <div className="sg-approval__status">
          <Icon name="info" size={14} />
          {`${DECISION_LABEL[decision.type] ?? decision.type} · ${decision.context} · ${relative(decision.created_at)}`}
        </div>
        <p className="sg-approval__title">{decision.title}</p>
        <dl className="sg-approval__grid">
          <div>
            <dt>发起</dt>
            <dd>{decision.expert?.name ?? decision.actor?.name ?? ''}</dd>
          </div>
          <div>
            <dt>时间</dt>
            <dd>{relative(decision.created_at)}</dd>
          </div>
        </dl>
        <div className="sg-approval__footer">
          <Button variant="primary" size={32} label={action} onClick={() => decision.target.task_id && onOpenTask(decision.target.task_id)} />
          {decision.target.task_id && (
            <button type="button" className="sg-approval__open" onClick={() => onOpenTask(decision.target.task_id!)}>
              在任务中打开
              <Icon name="chevron-right" size={12} />
            </button>
          )}
        </div>
      </section>
    </div>
  );
}

/** 2292:2 / 2732:2305: the decided request stays in the detail pane after it leaves the pending list. */
function DecidedCard({ snapshot, lead, onOpenTask }: { snapshot: DecidedSnapshot; lead: string | null; onOpenTask: (taskId: string) => void }) {
  const a = snapshot.decision.authorization!;
  const taskId = snapshot.decision.target.task_id;
  return (
    <div className="sg-decide__card">
      <ApprovalCard
        status={snapshot.status}
        layout="inbox"
        request={{ title: scopedPurpose(snapshot.auth?.purpose_text ?? a.purpose_text, snapshot.auth?.scope_params ?? a.scope_params), details: approvalDetails(a, snapshot.auth, lead, snapshot.status) }}
        onOpenTask={() => taskId && onOpenTask(taskId)}
      />
      <p className="sg-decide__note">{snapshot.status === 'approved' ? '已批准，已从待处理队列移除。' : '已拒绝，已从待处理队列移除。'}</p>
    </div>
  );
}

/** Right pane of 收件 · 需要我决定 (638:2): where the request comes from, its title and the decision card. */
export function DecisionDetail({
  decision,
  decided,
  onChanged,
  onDecided,
  onOpenTask,
  onOpenRun,
}: {
  decision: Decision;
  decided: DecidedSnapshot | null;
  onChanged: () => void;
  onDecided: (snapshot: DecidedSnapshot) => void;
  onOpenTask: (taskId: string) => void;
  onOpenRun?: (automationId: string, runId: string) => void;
}) {
  const brief = useTaskBrief(decision.target.task_id);
  return (
    <div className="sg-inq__pane">
      {brief && (
        <nav className="sg-inq__crumb" aria-label="所在位置">
          <Icon name="folder" size={14} />
          <span>{brief.workspace ?? '未关联工作区'}</span>
          <span className="sg-inq__crumb-sep">/</span>
          <span>{brief.title}</span>
        </nav>
      )}
      <h2 className="sg-inq__title">{decision.title}</h2>
      {decided ? (
        <DecidedCard snapshot={decided} lead={brief?.lead ?? null} onOpenTask={onOpenTask} />
      ) : (
        <DecisionEntry
          key={decision.id}
          decision={decision}
          onChanged={onChanged}
          onDecided={onDecided}
          onOpenTask={onOpenTask}
          {...(onOpenRun ? { onOpenRun } : {})}
          lead={brief?.lead ?? null}
        />
      )}
    </div>
  );
}

/** One row of the left list in 收件 · 需要我决定 (638:239). */
export function DecisionRow({ decision, selected, onSelect }: { decision: Decision; selected: boolean; onSelect: () => void }) {
  const who = decision.expert?.name ?? decision.actor?.name ?? '';
  return (
    <button type="button" className={`sg-inq__row${selected ? ' is-selected' : ''}`} aria-current={selected || undefined} onClick={onSelect}>
      <Avatar
        size={32}
        alt={who || decision.title}
        src={decision.expert?.avatar_ref ?? null}
        {...(decision.actor ? { initials: decision.actor.initials } : {})}
      />
      <span className="sg-inq__row-text">
        <span className={`sg-inq__row-tag${decision.type === 'invite' ? ' is-info' : ''}`}>{DECISION_LABEL[decision.type] ?? decision.type}</span>
        <span className="sg-inq__row-title">{decision.title}</span>
        <span className="sg-inq__row-meta">{decision.context}</span>
      </span>
      <span className="sg-inq__row-time">{relative(decision.created_at)}</span>
    </button>
  );
}
