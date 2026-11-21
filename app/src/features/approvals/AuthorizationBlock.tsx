import { useEffect, useState } from 'react';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { ChoiceChipGroup } from '../../components/ChoiceChip/ChoiceChipGroup';
import { ConfirmDialog } from '../../components/ConfirmDialog/ConfirmDialog';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { Menu, MenuItem } from '../../components/Menu/MenuItem';
import { Modal } from '../../components/Modal/Modal';
import { VALIDITY_TEXT } from '../inbox/format';
import { ErrorBanner, LoadingState, errorMessage } from '../../components/States/States';
import { apiMessage, taskApi, type Authorization } from '../task/model';
import { ApprovalCard } from './ApprovalCard';
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

function credits(a: Authorization): string {
  // A rejected request keeps the estimate it had when it was turned down, marked as an estimate.
  if (a.decision === 'rejected') return a.estimate_state === 'estimated' && a.estimated_credits != null ? `约 ${a.estimated_credits} Credits（估算）` : '未估算';
  if (a.estimate_state === 'unavailable') return '估算暂不可用';
  if (a.estimate_state === 'estimating' || a.estimate_state === 'pending') return '核对中…';
  return a.estimated_credits != null ? `约 ${a.estimated_credits} Credits` : '—';
}

export interface AuthorizationBlockProps {
  id: string;
  /** Open the approval-scope menu right away (deep link #scope). */
  scopeMenu?: boolean;
  onDecided?: () => void;
}

export function AuthorizationBlock({ id, scopeMenu, onDecided }: AuthorizationBlockProps) {
  const auth = useApiQuery((signal) => taskApi.authorization(id, signal), [id]);
  const [revising, setRevising] = useState(false);
  const [topN, setTopN] = useState<number | null>(null);
  const [menu, setMenu] = useState(!!scopeMenu);
  const [confirming, setConfirming] = useState<string | null>(null);
  const act = useSubmit(async (fn: () => Promise<unknown>) => {
    await fn();
    auth.reload();
    onDecided?.();
  });
  useEffect(() => setMenu(!!scopeMenu), [scopeMenu]);

  const a = auth.data;
  const checking = a?.estimate_state === 'estimating' || a?.estimate_state === 'pending';
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
    { label: '预计消耗', value: credits(a) },
    { label: '有效期', value: VALIDITY[a.validity] ?? a.validity },
    ...(a.charge_target ? [{ label: '计入', value: [a.charge_target.label, a.charge_target.department?.name].filter(Boolean).join(' · ') }] : []),
  ];
  const statusLabel =
    a.decision === 'rejected'
      ? undefined
      : a.decision === 'approved'
        ? `已批准本次数据使用${a.decision_scope && SCOPE[a.decision_scope] ? ' · ' + SCOPE[a.decision_scope]!.label : ''}`
        : checking
          ? '核对中 · 正在重新估算'
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
  // The range in effect comes first (2736:43: 前 50 名, then 前 20 名).
  const options = [...a.scope_options]
    .sort((x, y) => Number(y.top_n === a.scope_params?.top_n) - Number(x.top_n === a.scope_params?.top_n))
    .map((o) => o.label);
  const current = a.scope_options.find((o) => o.top_n === (topN ?? a.scope_params?.top_n))?.label ?? '';

  return (
    <div className="sg-authblock">
      {(
        <ApprovalCard
          status={a.decision === 'rejected' ? 'rejected' : a.decision === 'approved' ? 'approved' : checking ? 'processing' : 'pending'}
          layout="task"
          request={{ title: a.purpose_text, details, ...(statusLabel ? { statusLabel } : {}) }}
          busy={act.submitting}
          estimatePending={a.estimate_state === 'unavailable' || checking}
          onApprove={() => approve('once')}
          onReject={() => act.submit(() => taskApi.reject(a.id))}
          onRevise={() => setRevising((v) => !v)}
          onEstimate={() => act.submit(() => taskApi.estimate(a.id))}
          onCancel={() => setRevising(false)}
        />
      )}
      {!decided && a.allowed_scopes.length > 1 && (
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
                  disabled={act.submitting || checking || a.estimate_state === 'unavailable'}
                  onSelect={() => choose(s)}
                />
              ))}
            </Menu>
          )}
        </div>
      )}
      {revising && !decided && options.length > 0 && (
        <Modal className="sg-formdialog" style={{ width: 520 }} labelledBy="sg-authrevise-title" onClose={() => { setRevising(false); setTopN(null); }}>
          <h2 id="sg-authrevise-title" className="sg-formdialog__title">调整本次查询范围</h2>
          <p className="sg-formdialog__text">{`当前范围：${a.scope_text}\n动作：${ACTION[a.action_kind] ?? a.action_kind}`}</p>
          {a.estimate_state === 'unavailable' && a.scope_options.find((o) => o.top_n === a.scope_params?.top_n) && (
            <span className="sg-authblock__hint">{`当前为${a.scope_options.find((o) => o.top_n === a.scope_params?.top_n)!.label}；返回后可继续审批。`}</span>
          )}
          <span className="sg-formdialog__label">选择查询数量</span>
          <ChoiceChipGroup
            options={options}
            value={current}
            size={28}
            label="查询范围"
            onChange={(label) => setTopN(a.scope_options.find((o) => o.label === label)?.top_n ?? null)}
          />
          <p className="sg-formdialog__text">确认范围不会批准，费用将重新估算。</p>
          <div className="sg-formdialog__actions">
            <Button variant="ghost" size={28} label="取消" onClick={() => { setRevising(false); setTopN(null); }} />
            <Button
              variant="primary"
              size={28}
              label="确认范围"
              busy={act.submitting}
              disabled={topN == null || topN === a.scope_params?.top_n}
              onClick={async () => {
                if (topN == null) return;
                await act.submit(() => taskApi.revise(a.id, topN));
                setRevising(false);
                setTopN(null);
              }}
            />
          </div>
        </Modal>
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
