import { useEffect, useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { BackLink } from '../../components/BackLink/BackLink';
import { Button } from '../../components/Button/Button';
import { IconTile } from '../../components/IconTile/IconTile';
import { EmptyState, ErrorBanner, ForbiddenState, LoadingState, errorMessage } from '../../components/States/States';
import { downloadBillingPdf, getBillingAccount, getBillingDisplay, getOrders, type BillingDisplayOrder, type OrderListItem } from '../../features/billing/api';
import { fmtMoney, fmtMonthDay } from '../../features/billing/format';
import { channelLabel, invoiceStatusLabel, ledgerLabel, orderItemLabel, orderStatus, periodLabel } from '../../features/billing/labels';
import { SITE_LINKS, openSite } from '../../features/billing/site';
import { activeAccount, useSession } from '../../features/settings/common';
import '../../features/billing/billing.css';

/** 64 账单列表 (764:2). */
export default function Screen({ navigate }: ScreenProps) {
  const session = useSession();
  const key = activeAccount(session)?.id ?? '';
  const accountQ = useApiQuery((s) => getBillingAccount(s), [key]);
  const first = useApiQuery((s) => getOrders(0, s), [key]);
  const displayQ = useApiQuery((s) => getBillingDisplay(s), [key]);
  const shown = new Map((displayQ.data?.orders ?? []).map((o) => [o.order_id, o]));
  const channel = displayQ.data?.payment_channel ?? null;
  const [more, setMore] = useState<OrderListItem[]>([]);
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
      const page = await getOrders(nextOffset);
      setMore((m) => [...m, ...page.items]);
      setNextOffset(page.next_offset);
    } catch (err) {
      setMoreError(errorMessage(err, '加载失败，请重试'));
    } finally {
      setLoadingMore(false);
    }
  };

  const ledger = accountQ.data?.account ?? activeAccount(session);
  const invoicesOpen = !!accountQ.data?.capabilities.invoices;
  const forbidden = first.status === 'error' && (first.error.status === 403 || first.error.code === 'FORBIDDEN');
  const rows = [...(first.data?.items ?? []), ...more];
  const anyReview = rows.some((r) => r.needs_review);

  return (
    <div className="sg-bill">
      <div className="sg-bill__crumb">
        <BackLink size={30} label="用量与账单" onBack={() => navigate('/billing')} />
      </div>
      <header className="sg-bill__head">
        <div className="sg-bill__headtext">
          <h1>账单</h1>
          <p>{ledgerLabel(ledger)} · 按月份倒序</p>
        </div>
        <Button variant="secondary" size={32} label="更换套餐 ↗" onClick={() => openSite(SITE_LINKS.plans)} />
      </header>
      <div className="sg-bill__paycard">
        <IconTile icon="globe" size={28} tone="neutral" shape="rounded" />
        <div className="sg-bill__paytext">
          <b>{channel ? `付款方式 · ${channelLabel(channel)}` : '付款方式'}</b>
          <span>由 Sage 官网支付服务处理，Sage App 不保存支付账号</span>
        </div>
        <button type="button" className="sg-bill__link sg-bill__paylink" onClick={() => openSite(SITE_LINKS.paymentMethods)}>
          在官网管理 ↗
        </button>
      </div>
      {forbidden ? (
        <div className="sg-bill__state">
          <ForbiddenState title="只有账户所有者和账务管理员可以查看账单" action={{ label: '返回用量与账单', onClick: () => navigate('/billing') }} />
        </div>
      ) : !first.data && first.status === 'loading' ? (
        <LoadingState rows={4} />
      ) : !first.data && first.status === 'error' ? (
        <ErrorBanner message={errorMessage(first.error, '账单暂时无法读取')} onRetry={first.reload} />
      ) : rows.length === 0 ? (
        <EmptyState icon="file" title="还没有账单" description="在官网购买套餐或加购 Credits 后，账单会按月份列在这里" />
      ) : (
        <>
          {first.status === 'error' && <ErrorBanner message="刷新失败，显示的是上次读取的账单" onRetry={first.reload} />}
          <div className="sg-bill__orders" role="table" aria-label="账单">
            <div className="sg-bill__orow sg-bill__orow--head" role="row">
              <span role="columnheader">账单号</span>
              <span role="columnheader">周期</span>
              <span role="columnheader">项目</span>
              <span role="columnheader" className="is-num">金额</span>
              <span role="columnheader">状态</span>
              <span role="columnheader">发票</span>
            </div>
            {rows.map((order) => {
              const status = orderStatus(order);
              const open = () => navigate(`/billing/invoices/${order.id}`);
              return (
                <div key={order.id} className="sg-bill__orow is-link" role="row" tabIndex={0} onClick={open} onKeyDown={(e) => e.key === 'Enter' && open()}>
                  <span role="cell" className="truncate">{order.display_number ?? '—'}</span>
                  <span role="cell">{order.service_period ? periodLabel(order.service_period.start, order.service_period.end) : fmtMonthDay(order.paid_at ?? order.created_at)}</span>
                  <span role="cell" className="truncate">{orderItemLabel(order)}</span>
                  <span role="cell" className="is-num">{fmtMoney(order.amount_minor, order.currency)}</span>
                  <span role="cell" className={`sg-bill__status--${status.tone}`}>{status.label}</span>
                  <span role="cell" className="truncate">
                    <InvoiceCell order={order} shown={shown.get(order.id)} invoicesOpen={invoicesOpen} statusLabel={status.label} />
                  </span>
                </div>
              );
            })}
          </div>
          {moreError && <ErrorBanner message={moreError} onRetry={() => void loadMore()} />}
          {nextOffset !== null && (
            <div className="sg-bill__more">
              <Button variant="ghost" size={32} label="加载更多" busy={loadingMore} onClick={() => void loadMore()} />
            </div>
          )}
          {anyReview && <div className="sg-bill__footnote">“正在核对”表示支付结果尚未确认，系统会自动与支付方核对，不会重复扣款。</div>}
        </>
      )}
    </div>
  );
}

/** Q-C-12 A mapping; an issued invoice file is downloadable in the App (D29), otherwise the request goes to the website. */
function InvoiceCell({ order, shown, invoicesOpen, statusLabel }: { order: OrderListItem; shown: BillingDisplayOrder | undefined; invoicesOpen: boolean; statusLabel: string }) {
  if (shown?.invoice_file === 'after_review') return <span className="sg-bill__muted">核对完成后可下载</span>;
  if (order.needs_review) return <span className="sg-bill__muted">核对完成后可申请</span>;
  if (shown?.invoice_file === 'available') {
    return (
      <button
        type="button"
        className="sg-bill__link"
        onClick={(e) => {
          e.stopPropagation();
          void downloadBillingPdf(order.id, 'invoice', `发票-${order.display_number ?? order.id}.pdf`);
        }}
      >
        下载发票
      </button>
    );
  }
  if (order.invoice_state) return <span className="sg-bill__muted">{invoiceStatusLabel(order.invoice_state)}</span>;
  const paid = order.status === 'fulfilled' || order.status === 'paid_pending_entitlement';
  if (!paid || statusLabel === '本期进行中' || !invoicesOpen) return <span>—</span>;
  return (
    <button
      type="button"
      className="sg-bill__link"
      onClick={(e) => {
        e.stopPropagation();
        openSite(SITE_LINKS.invoice(order.id));
      }}
    >
      申请发票 ↗
    </button>
  );
}
