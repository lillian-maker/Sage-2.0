import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { loadSession } from '../../api/client';
import { navigate } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { SegmentedControl } from '../../components/SegmentedControl/SegmentedControl';
import { EmptyState, ErrorBanner, ForbiddenState, LoadingState, errorMessage } from '../../components/States/States';
import { activeAccount, useSession } from '../settings/common';
import { selectAccount } from '../settings/api';
import { currentSubscription, getBillingAccount, getSubscriptions, getUsageSummary, getUsageTasks, type UsageTask } from './api';
import { fmtCredits, fmtDay, fmtMonth, fmtMonthDay, fmtMoney, fmtPeriodLastDay, fmtRelative, toNumber } from './format';
import { accountShortLabel, cycleUnit, ledgerLabel, planName } from './labels';
import './billing.css';

/** Remaining Credits for the reader: own share on business ledgers, the ledger balance otherwise. */
export function remainingCredits(kind: string | undefined, available: string | undefined, used?: string, total?: string): number | null {
  const t = toNumber(total);
  const u = toNumber(used);
  if (kind === 'business' && t !== null && u !== null) return Math.max(0, t - u);
  return toNumber(available);
}

export function useBillingOverview() {
  const session = useSession();
  const account = activeAccount(session);
  const key = account?.id ?? '';
  const accountQ = useApiQuery((s) => getBillingAccount(s), [key]);
  const subsQ = useApiQuery((s) => getSubscriptions(s), [key]);
  const summaryQ = useApiQuery((s) => getUsageSummary(s), [key]);
  // “支付完成后，这里的额度会自动更新”: reread when the user comes back from the website.
  useEffect(() => {
    const onFocus = () => {
      accountQ.reload();
      summaryQ.reload();
    };
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [accountQ.reload, summaryQ.reload]);
  return { session, account, accountQ, subsQ, summaryQ };
}

export function BillingOverview({ overlay }: { overlay?: (data: ReturnType<typeof useBillingOverview>) => ReactNode }) {
  const data = useBillingOverview();
  const { session, account, accountQ, summaryQ } = data;
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);

  const onSwitch = useCallback(async (id: string) => {
    if (id === session?.active_account_id) return;
    setSwitching(true);
    setSwitchError(null);
    try {
      await selectAccount(id);
      await loadSession(true);
    } catch (err) {
      setSwitchError(errorMessage(err, '切换账本失败，请重试'));
    } finally {
      setSwitching(false);
    }
  }, [session?.active_account_id]);

  const ledger = accountQ.data?.account ?? account;
  const asOf = summaryQ.data?.as_of;
  const subtitle = `${ledgerLabel(ledger)} · 数据来自账务服务${asOf ? `，更新于 ${fmtRelative(asOf)}` : ''}`;
  const forbidden = accountQ.status === 'error' && (accountQ.error.status === 403 || accountQ.error.code === 'FORBIDDEN');

  return (
    <div className="sg-bill">
      <header className="sg-bill__head">
        <div className="sg-bill__headtext">
          <h1>用量与账单</h1>
          <p>{subtitle}</p>
        </div>
        {session && session.accounts.length > 1 && (
          <div className={`sg-bill__switch${switching ? ' is-busy' : ''}`}>
            <SegmentedControl
              label="选择账本"
              value={session.active_account_id ?? ''}
              options={session.accounts.map((a) => ({ value: a.id, label: accountShortLabel(a) }))}
              onChange={(id) => void onSwitch(id)}
            />
          </div>
        )}
        <Button variant="secondary" size={32} label="查看账单" onClick={() => navigate('/billing/invoices')} disabled={forbidden} />
        <Button variant="primary" size={32} label="加购" onClick={() => navigate('/billing/purchase')} disabled={forbidden} />
      </header>
      {switchError && <ErrorBanner message={switchError} />}
      {forbidden ? (
        <div className="sg-bill__state">
          <ForbiddenState title="只有账户所有者和账务管理员可以查看这个账本的用量与账单" />
        </div>
      ) : (
        <>
          <OverviewCards data={data} />
          <UsageTaskTable accountKey={account?.id ?? ''} />
        </>
      )}
      {overlay?.(data)}
    </div>
  );
}

function OverviewCards({ data }: { data: ReturnType<typeof useBillingOverview> }) {
  const { accountQ, subsQ, summaryQ } = data;
  if (!accountQ.data && accountQ.status === 'loading') return <LoadingState rows={2} />;
  if (!accountQ.data && accountQ.status === 'error')
    return <ErrorBanner message={errorMessage(accountQ.error, '账务服务暂时无法连接，当前额度未知')} onRetry={accountQ.reload} />;
  const acct = accountQ.data!;
  const summary = summaryQ.data;
  const sub = currentSubscription(subsQ.data?.items);
  const remaining = remainingCredits(acct.account.kind, acct.credits.available, summary?.period_used, summary?.period_total);
  const total = toNumber(summary?.period_total);
  const ratio = total && remaining !== null ? Math.min(1, Math.max(0, remaining / total)) : null;
  // 重置日期读当前服务月终点（usage/summary 的 period_end），不是订阅那一期的结束日。
  const resetAt = summary?.period_end ?? null;
  const daily = summary?.daily ?? [];
  const peak = Math.max(0, ...daily.map((d) => toNumber(d.credits) ?? 0));
  const lastUsedIndex = daily.reduce((idx, d, i) => ((toNumber(d.credits) ?? 0) > 0 ? i : idx), -1);

  return (
    <>
      {accountQ.status === 'error' && <ErrorBanner message={errorMessage(accountQ.error, '刷新失败，显示的是上次读取的额度')} onRetry={accountQ.reload} />}
      <div className="sg-bill__cards">
        <section className="sg-bill__card sg-bill__card--wide">
          <span className="sg-bill__cardlabel">本期剩余</span>
          <div className="sg-bill__big">
            {fmtCredits(remaining)}
            <span className="sg-bill__big-total">{total !== null ? ` / ${fmtCredits(total)}` : ''} Credits</span>
          </div>
          <div className="sg-bill__meter" role="meter" aria-label="本期剩余" aria-valuemin={0} aria-valuemax={100} aria-valuenow={ratio === null ? undefined : Math.round(ratio * 100)}>
            <span style={{ width: `${(ratio ?? 0) * 100}%` }} />
          </div>
          <div className="sg-bill__cardfoot">
            <span>{summary ? `已用 ${fmtCredits(summary.period_used)}` : summaryQ.status === 'error' ? '本期用量暂不可用' : ''}</span>
            <span>{resetAt ? `${fmtPeriodLastDay(resetAt)}重置` : ''}</span>
          </div>
        </section>
        <section className="sg-bill__card">
          <span className="sg-bill__cardlabel">当前套餐</span>
          {subsQ.status === 'error' && !subsQ.data ? (
            <div className="sg-bill__cardnote">
              套餐信息暂时无法读取
              <button type="button" className="sg-bill__link" onClick={subsQ.reload}>重试</button>
            </div>
          ) : sub ? (
            <>
              <div className="sg-bill__plan">{planName(sub.plan_code)}</div>
              <div className="sg-bill__cardfoot">
                <span>{sub.unit_amount_minor !== null ? `${fmtMoney(sub.unit_amount_minor, sub.currency ?? 'CNY').replace(/\.00$/, '')} / ${cycleUnit(sub.billing_cycle)}` : ''}</span>
                <span>
                  {sub.auto_renew && sub.cancellation_state === 'none' && sub.period_end
                    ? `下次续费 ${fmtPeriodLastDay(sub.period_end)}`
                    : sub.period_end
                      ? `${fmtMonthDay(sub.period_end)}到期，不再续费`
                      : ''}
                </span>
              </div>
            </>
          ) : subsQ.data ? (
            <>
              <div className="sg-bill__plan">未订阅</div>
              <div className="sg-bill__cardfoot">
                <span>{acct.capabilities.subscription ? '可在官网选择套餐' : '订阅暂未开放'}</span>
              </div>
            </>
          ) : (
            <LoadingState rows={1} />
          )}
        </section>
        <section className="sg-bill__card">
          <span className="sg-bill__cardlabel">每日消耗{summary ? `（${fmtMonth(summary.period_start)}）` : ''}</span>
          {summary ? (
            <div className="sg-bill__bars" aria-label="每日消耗">
              {daily.map((d, i) => {
                const v = toNumber(d.credits) ?? 0;
                return (
                  <span
                    key={d.date}
                    className={i === lastUsedIndex ? 'is-current' : undefined}
                    style={{ height: peak ? `${Math.max(v > 0 ? 4 : 0, (v / peak) * 72)}px` : 0 }}
                    title={`${fmtMonthDay(d.date + 'T00:00:00')} · ${fmtCredits(d.credits)} Credits`}
                  />
                );
              })}
              {peak === 0 && <em>本期还没有消耗</em>}
            </div>
          ) : summaryQ.status === 'error' ? (
            <div className="sg-bill__cardnote">
              用量暂不可用
              <button type="button" className="sg-bill__link" onClick={summaryQ.reload}>重试</button>
            </div>
          ) : (
            <LoadingState rows={1} />
          )}
        </section>
      </div>
    </>
  );
}

function UsageTaskTable({ accountKey }: { accountKey: string }) {
  const first = useApiQuery((s) => getUsageTasks('recent', 0, s), [accountKey]);
  const [more, setMore] = useState<UsageTask[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  useEffect(() => {
    setMore([]);
    setNextOffset(first.data?.next_offset ?? null);
  }, [first.data]);
  const loadMore = async () => {
    if (nextOffset === null) return;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const page = await getUsageTasks('recent', nextOffset);
      setMore((m) => [...m, ...page.items]);
      setNextOffset(page.next_offset);
    } catch (err) {
      setMoreError(errorMessage(err, '加载失败，请重试'));
    } finally {
      setLoadingMore(false);
    }
  };
  const rows = [...(first.data?.items ?? []), ...more];
  return (
    <section className="sg-bill__section">
      <div className="sg-bill__sectionhead">
        <h2>本期消耗明细</h2>
        <span>按任务</span>
      </div>
      {!first.data && first.status === 'loading' ? (
        <LoadingState rows={3} />
      ) : !first.data && first.status === 'error' ? (
        <ErrorBanner message={errorMessage(first.error, '消耗明细暂时无法读取')} onRetry={first.reload} />
      ) : rows.length === 0 ? (
        <EmptyState icon="bar-chart" title="本期还没有消耗" description="任务开始运行并结算 Credits 后，会按任务列在这里" />
      ) : (
        <div className="sg-bill__table" role="table" aria-label="本期消耗明细">
          <div className="sg-bill__tr sg-bill__tr--head" role="row">
            <span role="columnheader">任务</span>
            <span role="columnheader">工作区</span>
            <span role="columnheader">时间</span>
            <span role="columnheader" className="is-num">Credits</span>
          </div>
          {rows.map((row) => (
            <div
              key={row.task_key}
              className={`sg-bill__tr${row.task_id ? ' is-link' : ''}`}
              role="row"
              tabIndex={row.task_id ? 0 : undefined}
              onClick={row.task_id ? () => navigate(`/tasks/${row.task_id}`) : undefined}
              onKeyDown={row.task_id ? (e) => e.key === 'Enter' && navigate(`/tasks/${row.task_id}`) : undefined}
            >
              <span role="cell" className="truncate">
                {row.title ? (row.source === 'automation' ? `${row.title}（自动化）` : row.title) : row.source === 'other' ? '其他消耗' : '已删除的任务'}
              </span>
              <span role="cell" className="truncate">{row.workspace?.name ?? '未关联工作区'}</span>
              <span role="cell">{fmtDay(row.last_used_at)}</span>
              <span role="cell" className="is-num">{fmtCredits(row.credits)}</span>
            </div>
          ))}
        </div>
      )}
      {first.status === 'error' && first.data && <ErrorBanner message="刷新失败，显示的是上次读取的明细" onRetry={first.reload} />}
      {moreError && <ErrorBanner message={moreError} onRetry={() => void loadMore()} />}
      {nextOffset !== null && (
        <div className="sg-bill__more">
          <Button variant="ghost" size={32} label="加载更多" busy={loadingMore} onClick={() => void loadMore()} />
        </div>
      )}
    </section>
  );
}
