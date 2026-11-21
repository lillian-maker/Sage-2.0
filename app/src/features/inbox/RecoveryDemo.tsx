import { useEffect, useState } from 'react';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { automationsApi } from '../automations/api';
import '../approvals/approvals.css';

/**
 * 收件“确认恢复 · 运行来源”演示（Figma 3128:43 → 428 / 813 / 1196 / 1579 / 1962 / 2347，3311:681）。
 * 演示数据全部是虚构的，任何按钮都不调用接口；进入方式是 /inbox?filter=decide&demo=…，
 * 与运行详情页的 ?demo= 状态互相跳转。
 */
export type RecoveryPhase = 'recovery' | 'not_submitted' | 'decide' | 'approving' | 'rejecting' | 'unknown' | 'checking' | 'rejected_processing';

export const RECOVERY_PHASES: RecoveryPhase[] = ['recovery', 'not_submitted', 'decide', 'approving', 'rejecting', 'unknown', 'checking', 'rejected_processing'];

/** 运行详情页的演示状态（3128:3115 未提交、3128:3472 结果未知、3311:3761 已拒绝）。 */
export type RunDemo = 'not_submitted' | 'unknown' | 'rejected';
export const RUN_DEMOS: RunDemo[] = ['not_submitted', 'unknown', 'rejected'];

export const SCAN_AUTOMATION = '每月新市场机会扫描';
/** 提交中 / 核对中在演示里停留的时间，之后进入下一个状态（设计稿 AFTER_TIMEOUT）。 */
const TRANSIENT_MS = 1500;

const STATUS: Record<RecoveryPhase, string> = {
  recovery: '恢复情景 · 虚构测试示例',
  not_submitted: '未提交 · 虚构测试示例',
  decide: '待决定 · 虚构测试示例',
  approving: '批准处理中 · 虚构测试示例',
  rejecting: '拒绝处理中 · 虚构测试示例',
  unknown: '结果未知 · 虚构测试示例',
  checking: '正在核对 · 虚构测试示例',
  rejected_processing: '处理中',
};

const LIST_TAG: Record<RecoveryPhase, string> = {
  recovery: '恢复情景 · 示例',
  not_submitted: '未提交 · 示例',
  decide: '待决定 · 示例',
  approving: '处理中 · 示例',
  rejecting: '处理中 · 示例',
  unknown: '结果未知 · 示例',
  checking: '核对中 · 示例',
  rejected_processing: '处理中',
};

const NOTE: Record<RecoveryPhase, string> = {
  recovery: '仅展示未提交及结果未知的恢复路径。',
  not_submitted: '已确认未提交，决定未生效，可重新选择。',
  decide: '提交未成功，决定未生效；可重新选择。',
  approving: '正在提交，请勿重复操作。',
  rejecting: '正在提交，请勿重复操作。',
  unknown: '结果尚未确认，请先核对，避免重复提交。',
  checking: '正在核对结果，请勿重复操作。',
  rejected_processing: '正在处理，请勿重复操作。',
};

const DETAILS = [
  { label: '发起', value: '示例发起人' },
  { label: '动作', value: '读取历史价格（只读）' },
  { label: '范围', value: 'Amazon.fr · 家用小家电 · 虚构商品 A、B、C · 最近 30 天' },
  { label: '预计消耗', value: '12 Credits（虚构示例）' },
  { label: '有效期', value: '仅本次示例运行' },
];

function useScanAutomationId(): string | null {
  const [id, setId] = useState<string | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    automationsApi
      .list(ctrl.signal)
      .then((page) => setId(page.items.find((a) => a.name === SCAN_AUTOMATION)?.id ?? null))
      .catch(() => undefined);
    return () => ctrl.abort();
  }, []);
  return id;
}

export function RecoveryDemo({ initial, navigate }: { initial: RecoveryPhase; navigate: (to: string) => void }) {
  const [phase, setPhase] = useState<RecoveryPhase>(initial);
  const scanId = useScanAutomationId();
  useEffect(() => setPhase(initial), [initial]);
  useEffect(() => {
    if (phase !== 'approving' && phase !== 'rejecting' && phase !== 'checking') return;
    const t = window.setTimeout(() => setPhase(phase === 'checking' ? 'unknown' : 'not_submitted'), TRANSIENT_MS);
    return () => window.clearTimeout(t);
  }, [phase]);

  const toRun = (demo: RunDemo) => scanId && navigate(`/automations/${scanId}/runs?demo=${demo}`);
  const busy = phase === 'approving' || phase === 'rejecting' || phase === 'checking';
  const pendingDetails = phase === 'rejected_processing';

  let actions;
  if (phase === 'recovery')
    actions = (
      <>
        <Button variant="secondary" size={28} label="未提交情景" onClick={() => setPhase('not_submitted')} />
        <Button variant="secondary" size={28} label="结果未知情景" onClick={() => setPhase('unknown')} />
        <Button variant="secondary" size={28} label="结束演示" onClick={() => setPhase('decide')} />
      </>
    );
  else if (phase === 'not_submitted')
    actions = (
      <>
        <Button variant="secondary" size={28} label="重新选择" onClick={() => setPhase('decide')} />
        <Button variant="secondary" size={28} label="返回运行详情" disabled={!scanId} onClick={() => toRun('not_submitted')} />
      </>
    );
  else if (phase === 'unknown')
    actions = (
      <>
        <Button variant="secondary" size={28} label="核对状态" onClick={() => setPhase('checking')} />
        <Button variant="secondary" size={28} label="返回运行详情" disabled={!scanId} onClick={() => toRun('unknown')} />
      </>
    );
  else if (phase === 'decide')
    actions = (
      <>
        <Button variant="primary" size={28} icon="check" label="批准" onClick={() => setPhase('approving')} />
        <Button variant="ghost" size={28} label="拒绝" onClick={() => setPhase('rejecting')} />
        <Button variant="secondary" size={28} label="返回未提交" onClick={() => setPhase('not_submitted')} />
      </>
    );
  else if (phase === 'rejected_processing') actions = <Button variant="ghost" size={28} label="拒绝" disabled />;
  else
    actions = (
      <>
        <Button variant="primary" size={28} icon="check" label="批准" disabled />
        <Button variant="ghost" size={28} label="拒绝" disabled />
      </>
    );

  return (
    <div className="sg-decide__card sg-recover" data-demo-phase={phase}>
      <div className="sg-recover__head">
        <span className="sg-recover__tag">{LIST_TAG[phase]}</span>
        <h3 className="sg-recover__title">使用 Keepa 读取历史价格</h3>
        <span className="sg-recover__meta">{phase === 'rejected_processing' ? '示例 · 每月新市场机会扫描 · 法国' : '虚构测试示例 · 每月新市场机会扫描 · 法国'}</span>
      </div>
      <section className="sg-approval sg-approval--inbox is-pending" aria-busy={busy || undefined}>
        <div className="sg-approval__status">
          <Icon name="info" size={14} />
          {STATUS[phase]}
        </div>
        <p className="sg-approval__title">使用 Keepa 读取历史价格（只读）。</p>
        <dl className="sg-approval__grid">
          {DETAILS.map((d) => (
            <div key={d.label}>
              <dt>{d.label}</dt>
              <dd>{pendingDetails && d.label !== '动作' ? '待确认' : d.value}</dd>
            </div>
          ))}
        </dl>
        <div className="sg-recover__actions">{actions}</div>
      </section>
      <p className="sg-decide__note">{NOTE[phase]}</p>
    </div>
  );
}
