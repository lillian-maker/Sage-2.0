import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { BackLink } from '../../components/BackLink/BackLink';
import { Button } from '../../components/Button/Button';
import { Tag, type TagTone } from '../../components/Tag/Tag';
import { ErrorBanner, ForbiddenState, LoadingState, errorMessage } from '../../components/States/States';
import { downloadBillingPdf, getBillingAccount, getBillingDisplay, getInvoices, getOrder, type OrderDetail } from '../../features/billing/api';
import { fmtCredits, fmtDateTime, fmtMoney, fmtMonthDay, fmtTime } from '../../features/billing/format';
import { channelLabel, invoiceStatusLabel, ledgerLabel, orderItemLabel, orderStatus, type StatusTone } from '../../features/billing/labels';
import { SITE_LINKS, openSite } from '../../features/billing/site';
import { getProfile } from '../../features/settings/api';
import { activeAccount, useSession } from '../../features/settings/common';
import '../../features/billing/billing.css';

const TAG_TONE: Record<StatusTone, TagTone> = { success: 'success', muted: 'neutral', warning: 'warning', danger: 'danger', neutral: 'outline' };

function monthsBetween(a: string, b: string): number {
  const x = new Date(a);
  const y = new Date(b);
  return Math.max(0, Math.round((y.getFullYear() - x.getFullYear()) * 12 + (y.getMonth() - x.getMonth()) + (y.getDate() - x.getDate()) / 30));
}

/** Second line under the order item: Credits delivery for top-ups, billing cycle for plans. */
function deliveryLine(detail: OrderDetail): string | null {
  const batch = detail.delivery.credit_batch;
  // Credits granted within minutes of payment read as instant delivery.
  const instant = !!detail.order.paid_at && Math.abs(new Date(batch?.granted_at ?? 0).getTime() - new Date(detail.order.paid_at).getTime()) < 5 * 60_000;
  if (batch) return `${fmtCredits(batch.quantity)} Credits · ${fmtMonthDay(batch.granted_at)} · ${instant ? '立即到账' : '已到账'}，有效期 ${monthsBetween(batch.granted_at, batch.expires_at)} 个月`;
  if (detail.delivery.status === 'pending') return '已支付，正在到账';
  if (detail.delivery.status === 'not_started') return '支付完成后到账';
  return null;
}

/** 66 账单详情 (757:2). The route parameter is the Order id. */
export default function Screen({ params, navigate }: ScreenProps) {
  const id = params.invoiceId ?? '';
  const session = useSession();
  const key = activeAccount(session)?.id ?? '';
  const orderQ = useApiQuery((s) => getOrder(id, s), [id, key]);
  const accountQ = useApiQuery((s) => getBillingAccount(s), [key]);
  const invoicesQ = useApiQuery((s) => (key ? getInvoices(key, s) : Promise.resolve({ items: [] })), [key]);
  const profileQ = useApiQuery((s) => getProfile(s), []);
  const displayQ = useApiQuery((s) => getBillingDisplay(s), [key]);

  const back = (
    <div className="sg-bill__crumb">
      <BackLink size={30} label="账单" onBack={() => navigate('/billing/invoices')} />
    </div>
  );
  if (!orderQ.data) {
    if (orderQ.status === 'error') {
      const hidden = orderQ.error.status === 404 || orderQ.error.status === 403;
      return (
        <div className="sg-bill">
          {back}
          {hidden ? (
            <div className="sg-bill__state">
              <ForbiddenState title="无法查看这张账单" action={{ label: '返回账单列表', onClick: () => navigate('/billing/invoices') }} />
            </div>
          ) : (
            <ErrorBanner message={errorMessage(orderQ.error, '账单暂时无法读取')} onRetry={orderQ.reload} />
          )}
        </div>
      );
    }
    return (
      <div className="sg-bill">
        {back}
        <LoadingState rows={4} />
      </div>
    );
  }

  const detail = orderQ.data;
  const order = detail.order;
  const status = orderStatus({ ...order, needs_review: detail.needs_review });
  const payment = detail.payments.find((p) => p.status === 'accepted') ?? detail.payments[0] ?? null;
  const qty = order.seat_quantity ?? order.snapshot.seat_quantity ?? 1;
  const unit = order.snapshot.unit_amount_minor ?? null;
  const subtotal = unit !== null ? unit * qty : order.amount_minor;
  const shown = displayQ.data?.orders.find((o) => o.order_id === order.id);
  const discount = shown?.discount ?? order.discount ?? null;
  const invoice = invoicesQ.data?.items.filter((i) => i.order_id === order.id).sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null;
  const paid = order.status === 'fulfilled' || order.status === 'paid_pending_entitlement';
  const canRequestInvoice = paid && !invoice && !detail.needs_review && !!accountQ.data?.capabilities.invoices;
  const ledger = accountQ.data?.account ?? activeAccount(session);
  const subtitle = [ledgerLabel(ledger), payment?.channel ? channelLabel(payment.channel) : null, order.paid_at ? fmtDateTime(order.paid_at) : '未支付']
    .filter(Boolean)
    .join(' · ');
  const line2 = deliveryLine(detail);
  const email = profileQ.data?.email;

  return (
    <div className="sg-bill">
      {back}
      <header className="sg-bill__head">
        <div className="sg-bill__headtext">
          <h1 className="is-strong">账单 {order.display_number ?? '（未分配账单号）'}</h1>
          <p>{subtitle}</p>
        </div>
        <Tag label={status.label} tone={TAG_TONE[status.tone]} size={18} />
        {paid && (
          <Button variant="secondary" size={32} label="下载 PDF" onClick={() => void downloadBillingPdf(order.id, 'bill', `账单-${order.display_number ?? order.id}.pdf`)} />
        )}
        {canRequestInvoice && <Button variant="secondary" size={32} label="申请发票 ↗" onClick={() => openSite(SITE_LINKS.invoice(order.id))} />}
      </header>
      {orderQ.status === 'error' && <ErrorBanner message="刷新失败，显示的是上次读取的账单" onRetry={orderQ.reload} />}
      <div className="sg-bill__detail">
        <div className="sg-bill__lines" role="table" aria-label="账单项目">
          <div className="sg-bill__lrow sg-bill__lrow--head" role="row">
            <span role="columnheader">项目</span>
            <span role="columnheader" className="is-num">数量</span>
            <span role="columnheader" className="is-num">金额</span>
          </div>
          <div className="sg-bill__lrow" role="row">
            <span role="cell">
              {order.snapshot.kind === 'credits' ? '加购 Credits' : orderItemLabel(order)}
              {line2 && <small>{line2}</small>}
            </span>
            <span role="cell" className="is-num">{qty}</span>
            <span role="cell" className="is-num">{fmtMoney(subtotal, order.currency)}</span>
          </div>
          {discount && (
            <div className="sg-bill__lrow" role="row">
              <span role="cell">
                优惠
                {discount.label && <small>{discount.label}</small>}
              </span>
              <span role="cell" />
              <span role="cell" className="is-num">{fmtMoney(-Math.abs(discount.amount_minor), order.currency)}</span>
            </div>
          )}
          <div className="sg-bill__totals">
            <div>
              <span>小计</span>
              <span>{fmtMoney(subtotal, order.currency)}</span>
            </div>
            {discount && (
              <div>
                <span>优惠</span>
                <span>{fmtMoney(-Math.abs(discount.amount_minor), order.currency)}</span>
              </div>
            )}
            <div className="is-total">
              <span>合计</span>
              <b>{fmtMoney(order.amount_minor, order.currency)}</b>
            </div>
          </div>
        </div>
        <aside className="sg-bill__side">
          <h3>支付信息</h3>
          {payment ? (
            <div>
              <div className="sg-bill__kv">
                <span>支付方式</span>
                <b>{channelLabel(payment.channel)}</b>
              </div>
              <div className="sg-bill__kv">
                <span>交易号</span>
                <b>{shown?.transaction_reference_display ?? payment.transaction_reference_masked ?? '—'}</b>
              </div>
              <div className="sg-bill__kv">
                <span>回执</span>
                <b>{payment.confirmed_at ? `账务服务已确认 · ${fmtTime(payment.confirmed_at, true)}` : '等待账务服务确认'}</b>
              </div>
            </div>
          ) : (
            <p className="sg-bill__sidenote">{order.status === 'closed' ? '订单已关闭，没有支付记录。' : '尚未收到支付回执。'}</p>
          )}
          {detail.needs_review && <p className="sg-bill__sidenote">支付结果尚未确认，系统会自动与支付方核对，不会重复扣款。</p>}
          <h3>发票</h3>
          {invoicesQ.status === 'error' && !invoicesQ.data ? (
            <p className="sg-bill__sidenote">发票状态暂时无法读取。</p>
          ) : invoice ? (
            <p className="sg-bill__sidenote">
              {invoiceStatusLabel(invoice.status)}
              {invoice.recipient_email ? `，接收邮箱 ${invoice.recipient_email}` : ''}
            </p>
          ) : paid ? (
            <p className="sg-bill__sidenote">尚未申请。在官网申请后，电子普票 1 个工作日内发送到 {email ?? '账号邮箱'}</p>
          ) : (
            <p className="sg-bill__sidenote">支付完成后可在官网申请发票。</p>
          )}
        </aside>
      </div>
    </div>
  );
}
