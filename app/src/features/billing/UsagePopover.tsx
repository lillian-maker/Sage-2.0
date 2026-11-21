import { navigate } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { LoadingState } from '../../components/States/States';
import { currentSubscription, getBillingAccount, getSubscriptions, getUsageSummary, getUsageTasks } from './api';
import { remainingCredits } from './BillingOverview';
import { fmtCredits, fmtPeriodLastDay, fmtTime, toNumber } from './format';
import { ledgerLabel, planName } from './labels';
import './billing.css';

export type UsageState = 'normal' | 'low' | 'exhausted' | 'unavailable';

/** Q-C-9 A: “偏低” is below 10% of the period total. */
const LOW_RATIO = 0.1;

/** Data behind the sidebar quota button and its popover (board 62). */
export function useUsageSnapshot(accountKey: string) {
  const accountQ = useApiQuery((s) => getBillingAccount(s), [accountKey]);
  const subsQ = useApiQuery((s) => getSubscriptions(s), [accountKey]);
  const summaryQ = useApiQuery((s) => getUsageSummary(s), [accountKey]);
  const topQ = useApiQuery((s) => getUsageTasks('credits', 0, s), [accountKey]);
  const acct = accountQ.data;
  const summary = summaryQ.data;
  const remaining = acct ? remainingCredits(acct.account.kind, acct.credits.available, summary?.period_used, summary?.period_total) : null;
  const total = toNumber(summary?.period_total);
  let state: UsageState = 'normal';
  if (!acct || !summary || remaining === null) state = 'unavailable';
  else if (remaining <= 0) state = 'exhausted';
  else if (total && remaining / total < LOW_RATIO) state = 'low';
  const sub = currentSubscription(subsQ.data?.items);
  // 重置日期读当前服务月终点（usage/summary 的 period_end），不是订阅那一期的结束日。
  const resetAt = summary?.period_end ?? null;
  const loading = [accountQ, summaryQ].some((q) => q.status === 'loading' && !q.data);
  const tooltip = state === 'unavailable' ? '用量暂不可用：账务服务暂时无法读取' : `剩余 ${fmtCredits(remaining)}${resetAt ? ` · ${fmtPeriodLastDay(resetAt)}重置` : ''}`;
  const reload = () => {
    accountQ.reload();
    subsQ.reload();
    summaryQ.reload();
    topQ.reload();
  };
  return { accountQ, subsQ, summaryQ, topQ, remaining, total, state, sub, resetAt, loading, tooltip, reload };
}

export function UsagePopover({ snapshot }: { snapshot: ReturnType<typeof useUsageSnapshot> }) {
  const { accountQ, summaryQ, topQ, sub, state, loading, reload } = snapshot;
  const summary = summaryQ.data;
  const ledger = accountQ.data?.account;
  const used = toNumber(summary?.period_used);
  const total = toNumber(summary?.period_total);
  const pct = used !== null && total ? Math.round((used / total) * 100) : null;
  const top = (topQ.data?.items ?? []).slice(0, 3);
  return (
    <div className="sg-usagepop" role="dialog" aria-label="本期用量">
      <div className="sg-usagepop__head">
        <b>本期用量</b>
        <span>
          {ledgerLabel(ledger)}
          {sub ? ` · ${planName(sub.plan_code)}` : ''}
        </span>
      </div>
      {loading ? (
        <LoadingState rows={2} />
      ) : state === 'unavailable' || !summary ? (
        <div className="sg-usagepop__unavailable">用量暂不可用，账务服务恢复后会自动显示。</div>
      ) : (
        <>
          <div className="sg-usagepop__meta">
            <span>
              已用 {fmtCredits(summary.period_used)} / {fmtCredits(summary.period_total)} Credits{pct !== null ? `（${pct}%）` : ''}
            </span>
            <span>日均 {fmtCredits(summary.daily_average)}</span>
          </div>
          <div className="sg-bill__meter" aria-hidden="true">
            <span style={{ width: `${Math.min(100, pct ?? 0)}%` }} />
          </div>
          <div className="sg-usagepop__label">本期消耗最多</div>
          <div className="sg-usagepop__top">
            {top.length === 0 ? (
              <div>
                <span className="sg-bill__muted">本期还没有消耗</span>
              </div>
            ) : (
              top.map((t) => (
                <div key={t.task_key}>
                  <span className="truncate">{t.title ?? '其他消耗'}</span>
                  <span>{fmtCredits(t.credits)}</span>
                </div>
              ))
            )}
          </div>
        </>
      )}
      <div className="sg-usagepop__asof">
        数据更新于 {summary ? fmtTime(summary.as_of) : '—'}
        <button type="button" aria-label="刷新用量" onClick={reload}>
          <Icon name="refresh" size={12} />
        </button>
      </div>
      <div className="sg-usagepop__actions">
        <Button variant="secondary" size={32} label="查看用量明细" icon="bar-chart" block onClick={() => navigate('/billing')} />
        <Button variant="primary" size={32} label="加购" block onClick={() => navigate('/billing/purchase')} />
      </div>
    </div>
  );
}
