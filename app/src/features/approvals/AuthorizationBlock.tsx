import { useEffect, useState } from 'react';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { ConfirmDialog } from '../../components/ConfirmDialog/ConfirmDialog';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { Menu, MenuItem } from '../../components/Menu/MenuItem';
import { ReviseScopeDialog } from './ReviseScopeDialog';
import { VALIDITY_TEXT } from '../inbox/format';
import { ErrorBanner, LoadingState, errorMessage } from '../../components/States/States';
import { apiMessage, taskApi, type Authorization } from '../task/model';
import { ApprovalCard } from './ApprovalCard';
import { scopedPurpose } from './purpose';
import './approvals.css';

const ACTION: Record<string, string> = {
  read_query: '只读查询，不写入任何外部系统',
  write: '写入外部系统',
  external_write: '写入外部系统',
  send: '对外发送',
};
const VALIDITY: Record<string, string> = { ...VALIDITY_TEXT, once: '仅本次' };
const SCOPE: Record<string, { label: string; hint: (extension: string | undefined) => string }> = {
  once: { label: '仅本次', hint: () => '默认。只对这一次查询生效' },
  task: {
    label: '本任务内总是允许',
    hint: (ext) => (ext ? `本任务再次用 ${ext} 只读查询时不再询问` : '本任务再次用到同一扩展时不再询问'),
  },
  workspace: { label: '本工作区内总是允许', hint: () => '仅限只读；写入外部系统的动作不提供此项。可在“设置 › 安全”撤销' },
};

const SCOPE_EFFECT: Record<string, string> = {
  once: '仅本次有效',
  task: '本任务内再次使用时不再询问',
  workspace: '本工作区内再次使用时不再询问，可在“设置 › 安全”撤销',
};
const CONFIRM_REASON: Record<string, string> = {
  paid: '该扩展按次计费，之后的调用会直接扣除 Credits。',
  external_send: '此动作会向外部系统发送数据。',
};

function confirmMessage(a: Authorization, scope: string): string {
  const reasons = (a.confirm_reasons?.[scope] ?? []).map((r) => CONFIRM_REASON[r]).filter(Boolean).join('');
  const verb = a.action_kind === 'read_query' ? '只读读取' : '使用';
  return `${a.expert?.name ?? '专家'}将${verb} ${a.scope_text}，${credits(a)}，${SCOPE_EFFECT[scope] ?? ''}。${reasons}`;
}

function validityPhrase(validity: string): string {
  const v = VALIDITY[validity] ?? validity;
  return v.endsWith('有效') ? v : `${v}有效`;
}

function credits(a: Authorization, estimating = false): string {
  // A rejected request keeps the estimate it had when it was turned down, marked as an estimate.
  if (a.decision === 'rejected') return a.estimate_state === 'estimated' && a.estimated_credits != null ? `约 ${a.estimated_credits} Credits（估算）` : '未估算';
  if (estimating || isChecking(a.estimate_state)) return '正在核对';
  if (a.estimate_state === 'unavailable') return '暂时无法估算';
  if (a.estimate_state === 'checking') return '待重新估算';
  return a.estimated_credits != null ? `约 ${a.estimated_credits} Credits` : '—';
}

/** An estimate still being worked out (2736:1495 正在核对). */
function isChecking(state: string | undefined): boolean {
  return state === 'estimating' || state === 'pending';
}

/** revise leaves the request in checking: the new scope waits for 重新估算 (2736:1011 待重新估算). */
function needsEstimate(state: string | undefined): boolean {
  return state === 'checking' || state === 'unavailable';
}

export interface AuthorizationBlockProps {
  id: string;
  /** Open the approval-scope menu right away (deep link #scope). */
  scopeMenu?: boolean;
  /** One-line banner with 批准 / 修改范围 / 拒绝 for a pending request (658:2 编排视图节点详情). */
  compact?: boolean;
  onDecided?: () => void;
}

export function AuthorizationBlock({ id, scopeMenu, compact, onDecided }: AuthorizationBlockProps) {
  const auth = useApiQuery((signal) => taskApi.authorization(id, signal), [id]);
  const [revising, setRevising] = useState(false);
  const [menu, setMenu] = useState(!!scopeMenu);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [estimating, setEstimating] = useState(false);
  const act = useSubmit(async (fn: () => Promise<unknown>) => {
    await fn();
    auth.reload();
    onDecided?.();
  });
  useEffect(() => setMenu(!!scopeMenu), [scopeMenu]);

  const a = auth.data;
  const checking = isChecking(a?.estimate_state);
  const blocked = estimating || checking || needsEstimate(a?.estimate_state);
  // The 正在核对 shown while 重新估算 runs lasts until the re-read row or an error arrives.
  useEffect(() => setEstimating(false), [a, act.error]);
  useEffect(() => {
    if (!checking) return;
    const t = window.setInterval(auth.reload, 3000);
    return () => window.clearInterval(t);
  }, [checking, auth.reload]);

  if (!a) {
    return auth.status === 'error' ? (
      <ErrorBanner message={errorMessage(auth.error, '授权请求暂时无法读取')} onRetry={auth.reload} />
    ) : (
      <LoadingState rows={2} />
    );
  }

  const decided = a.decision !== 'pending';
  const details = [
    { label: '动作', value: ACTION[a.action_kind] ?? a.action_kind },
    { label: '范围', value: a.scope_text },
    { label: '预计消耗', value: credits(a, estimating) },
    { label: '有效期', value: VALIDITY[a.validity] ?? a.validity },
    ...(a.charge_target ? [{ label: '计入', value: [a.charge_target.label, a.charge_target.department?.name].filter(Boolean).join(' · ') }] : []),
  ];
  const statusLabel =
    a.decision === 'rejected'
      ? undefined
      : a.decision === 'approved'
        ? `已批准本次数据使用${a.decision_scope && SCOPE[a.decision_scope] ? ' · ' + SCOPE[a.decision_scope]!.label : ''}`
        : a.estimate_state === 'unavailable'
            ? '需要你确认 · 估算暂不可用'
            : undefined;

  const approve = (scope: string) => act.submit(() => taskApi.approve(a.id, scope));
  // Standing, pay-per-call or outbound permissions get a second confirmation (82 ③); a one-off read stays one click.
  const choose = (scope: string) => {
    setMenu(false);
    if (a.confirm_scopes?.includes(scope)) setConfirming(scope);
    else void approve(scope);
  };

  // Checking / unavailable estimates keep the full card: they need its estimate actions.
  const banner = compact && !decided && !blocked;

  return (
    <div className="sg-authblock">
      {banner ? (
        <div className="sg-authbanner" role="group" aria-label="需要你授权">
          <p className="sg-authbanner__text">
            <strong>需要你授权：</strong>
            {`${scopedPurpose(a.purpose_text, a.scope_params)}${credits(a)}，${validityPhrase(a.validity)}。`}
          </p>
          <div className="sg-authbanner__actions">
            <Button variant="primary" size={28} label="批准" busy={act.submitting} onClick={() => choose('once')} />
            <Button variant="secondary" size={28} label="修改范围" disabled={act.submitting} onClick={() => setRevising(true)} />
            <Button variant="ghost" size={28} label="拒绝" disabled={act.submitting} onClick={() => act.submit(() => taskApi.reject(a.id))} />
          </div>
        </div>
      ) : (
        <ApprovalCard
          status={a.decision === 'rejected' ? 'rejected' : a.decision === 'approved' ? 'approved' : 'pending'}
          layout="task"
          request={{ title: scopedPurpose(a.purpose_text, a.scope_params), details, ...(statusLabel ? { statusLabel } : {}) }}
          busy={act.submitting}
          estimatePending={blocked}
          canEstimate={!estimating && needsEstimate(a.estimate_state)}
          estimateLabel={a.estimate_state === 'checking' ? '重新估算' : '重试估算'}
          onApprove={() => approve('once')}
          onReject={() => act.submit(() => taskApi.reject(a.id))}
          onRevise={() => setRevising((v) => !v)}
          onEstimate={() => {
            setEstimating(true);
            void act.submit(() => taskApi.estimate(a.id));
          }}
          onCancel={() => setRevising(false)}
        />
      )}
      {!banner && !decided && a.allowed_scopes.length > 1 && (
        <div className="sg-authblock__scope">
          <Button variant="ghost" size={26} label={`批准 · ${SCOPE.once!.label}`} trailingIcon="chevron-down" onClick={() => setMenu((m) => !m)} aria-expanded={menu} />
          {menu && (
            <Menu label="批准范围">
              {a.allowed_scopes.map((s) => (
                <MenuItem
                  key={s}
                  icon={s === 'once' ? 'check' : 'layers'}
                  label={SCOPE[s]?.label ?? s}
                  hint={SCOPE[s]?.hint(a.extension?.name)}
                  disabled={act.submitting || blocked}
                  onSelect={() => choose(s)}
                />
              ))}
            </Menu>
          )}
        </div>
      )}
      {revising && !decided && a.scope_options.length > 0 && (
        <ReviseScopeDialog
          scopeText={a.scope_text}
          actionText={ACTION[a.action_kind] ?? a.action_kind}
          options={a.scope_options}
          currentTopN={a.scope_params?.top_n}
          estimateState={a.estimate_state}
          busy={act.submitting}
          onClose={() => setRevising(false)}
          onSubmit={async (next) => {
            await act.submit(() => taskApi.revise(a.id, next));
            setRevising(false);
          }}
        />
      )}
      {confirming && !decided && (
        <ConfirmDialog
          variant="external"
          title={`批准使用 ${a.extension?.name ?? '该扩展'}？`}
          message={confirmMessage(a, confirming)}
          cancelLabel="拒绝"
          secondaryLabel="修改范围"
          confirmLabel="批准"
          busy={act.submitting}
          onDismiss={() => setConfirming(null)}
          onCancel={async () => {
            setConfirming(null);
            await act.submit(() => taskApi.reject(a.id));
          }}
          onSecondary={() => {
            setConfirming(null);
            setRevising(true);
          }}
          onConfirm={async () => {
            const scope = confirming;
            setConfirming(null);
            await approve(scope);
          }}
        />
      )}
      {act.error && <InlineNotice icon="info" tone="error" text={apiMessage(act.error, '操作没有成功，请重试')} />}
    </div>
  );
}
