import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { useSubmit } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { ChoiceChip } from '../../components/ChoiceChip/ChoiceChip';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { Icon } from '../../components/Icon/Icon';
import { errorMessage } from '../../components/States/States';
import { ApprovalCard } from '../approvals/ApprovalCard';
import { inboxApi, type AuthorizationDetail, type Decision } from './api';
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
  | { phase: 'failed' }
  | { phase: 'done'; label: string };

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

interface EntryProps {
  decision: Decision;
  onChanged: () => void;
  onOpenTask: (taskId: string) => void;
  onOpenRun?: (automationId: string, runId: string) => void;
}

function ApproveDecision({ decision, onChanged, onOpenTask, onOpenRun }: EntryProps) {
  const authId = decision.authorization!.id;
  const { auth, error, reload, setAuth } = useAuthDetail(authId);
  const [state, setState] = useState<CardState>({ phase: 'idle' });
  const [revising, setRevising] = useState(false);
  const [topN, setTopN] = useState<number | null>(null);
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

  const finish = (label: string) => {
    setState({ phase: 'done', label });
    onChanged();
  };

  const approve = () =>
    void act.submit(async () => {
      const version = auth?.version ?? 0;
      await inboxApi.approve(authId, version, crypto.randomUUID());
      finish('已批准，仅此一次');
    }, 'approve');

  const reject = () =>
    void act.submit(async () => {
      await inboxApi.reject(authId, auth?.version ?? 0);
      finish('已拒绝');
    }, 'reject');

  const revise = () =>
    void act.submit(async () => {
      if (topN === null) return;
      await inboxApi.revise(authId, auth?.version ?? 0, topN);
      setRevising(false);
      finish('已按新范围退回，等待专家重新估算');
    });

  const estimate = () =>
    void act.submit(async () => {
      await inboxApi.estimate(authId);
      reload();
      setState({ phase: 'idle' });
    });
  const check = () => {
    setState({ phase: 'checking' });
    inboxApi
      .authorization(authId)
      .then((a) => {
        setAuth(a);
        if (a.decision === 'approved') finish('已批准，仅此一次');
        else if (a.decision === 'rejected') finish('已拒绝');
        else setState({ phase: 'not_submitted' });
      })
      .catch(() => setState({ phase: 'unknown' }));
  };

  if (state.phase === 'done') {
    return (
      <div className="sg-decide__card">
        <InlineNotice icon="check" tone="muted" text={`${decision.title} · ${state.label}`} />
      </div>
    );
  }

  const a = decision.authorization!;
  const run = decision.target.automation_id && decision.target.automation_run_id && onOpenRun
    ? () => onOpenRun(decision.target.automation_id!, decision.target.automation_run_id!)
    : null;
  const reselect = () => setState({ phase: 'idle', retry: true });
  const scopeText = auth?.scope_text ?? a.scope_text;
  const currentOption = (auth?.scope_options ?? a.scope_options).find((o) => o.top_n === (auth?.scope_params.top_n ?? a.scope_params.top_n));
  const details = [
    { label: '发起', value: auth?.expert?.name ?? a.requested_by.expert },
    { label: '动作', value: ACTION_TEXT[a.action_kind] ?? a.action_kind },
    { label: '范围', value: auth?.scope_text ?? a.scope_text },
    {
      label: '预计消耗',
      value:
        (auth?.estimate_state ?? a.estimate_state) === 'estimated' && (auth?.estimated_credits ?? a.estimated_credits) != null
          ? `约 ${auth?.estimated_credits ?? a.estimated_credits} Credits · ${chargeLabel(auth?.charge_target ?? a.charge_target)}`
          : '暂时无法估算',
    },
    { label: '有效期', value: VALIDITY_TEXT[a.validity] ?? a.validity },
  ];

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
        request={{ title: a.purpose_text, details, statusLabel: `${DECISION_LABEL[decision.type]} · ${decision.context} · ${relative(decision.created_at)}` }}
        busy={state.phase === 'submitting'}
        estimatePending={(auth?.estimate_state ?? a.estimate_state) === 'unavailable'}
        onApprove={approve}
        onReject={reject}
        onRevise={() => {
          setRevising((v) => !v);
          setTopN((auth?.scope_params.top_n as number | undefined) ?? (a.scope_params.top_n as number | undefined) ?? null);
        }}
        onEstimate={estimate}
        onOpenTask={() => decision.target.task_id && onOpenTask(decision.target.task_id)}
      />
      <p className="sg-decide__note">这条审批也会出现在任务详情里，两处状态同步；通知中心只提醒一次，不重复列出待处理事项。</p>
      {revising && (
        <div className="sg-decide__revise">
          <p className="sg-decide__current">{`当前范围：${scopeText}\n动作：${SCOPE_ACTION[a.action_kind] ?? ACTION_TEXT[a.action_kind] ?? a.action_kind}`}</p>
          {(auth?.estimate_state ?? a.estimate_state) === 'unavailable' && currentOption && (
            <span className="sg-decide__hint">{`当前为${currentOption.label}；返回后可继续审批。`}</span>
          )}
          <div className="sg-infield__label">确认范围</div>
          <div className="sg-inchips">
            {(auth?.scope_options ?? a.scope_options).map((o) => (
              <ChoiceChip key={o.top_n} role="radio" size={30} label={o.label} selected={topN === o.top_n} onToggle={() => setTopN(o.top_n)} />
            ))}
          </div>
          <div className="sg-incard__row">
            <Button variant="primary" size={30} label="确认修改" busy={act.submitting} disabled={topN === null} onClick={revise} />
            <Button variant="ghost" size={30} label="取消" onClick={() => setRevising(false)} />
          </div>
        </div>
      )}
    </div>
  );
}

export function DecisionEntry({ decision, onChanged, onOpenTask, onOpenRun }: EntryProps) {
  if (decision.type === 'approve' && decision.authorization) {
    return <ApproveDecision decision={decision} onChanged={onChanged} onOpenTask={onOpenTask} {...(onOpenRun ? { onOpenRun } : {})} />;
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
