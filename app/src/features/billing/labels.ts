import { fmtCredits, fmtMonthDay } from './format';
import type { Order, OrderListItem, Subscription } from './api';

type AccountLike = { kind: string; name: string } | null | undefined;

/** 个人账本 / 企业账本 for page subtitles. */
export function ledgerLabel(account: AccountLike): string {
  return account?.kind === 'business' ? '企业账本' : '个人账本';
}

/** Account switch and purchase dialog label: 个人 / 企业 · Sage Demo. */
export function accountShortLabel(account: AccountLike): string {
  if (!account) return '—';
  if (account.kind !== 'business') return '个人';
  return `企业 · ${account.name.replace(/\s*企业$/, '')}`;
}

export function planName(code: string | null | undefined): string {
  if (!code) return '—';
  return code.charAt(0).toUpperCase() + code.slice(1);
}

export function cycleUnit(cycle: string | null | undefined): string {
  return cycle === 'annual' ? '年' : '月';
}

export function orderItemLabel(order: Pick<Order, 'snapshot' | 'plan_code' | 'billing_cycle'>): string {
  const s = order.snapshot ?? {};
  if (s.kind === 'credits') return `加购 ${fmtCredits(s.credits_quantity ?? null)} Credits`;
  const code = order.plan_code ?? s.code ?? null;
  const cycle = order.billing_cycle ?? s.billing_cycle ?? null;
  return `${planName(code)} ${cycle === 'annual' ? '年度' : '月度'}`;
}

/** Service periods end exclusively at midnight; show the last covered day. */
export function periodLabel(start: string, end: string): string {
  const last = new Date(new Date(end).getTime() - 1).toISOString();
  return `${fmtMonthDay(start)} – ${fmtMonthDay(last)}`;
}

export type StatusTone = 'success' | 'muted' | 'warning' | 'danger' | 'neutral';

/** Q-C-12 A: 本期进行中 = service period not over; 正在核对 = needs_review. */
export function orderStatus(order: OrderListItem | (Order & { needs_review?: boolean; service_period?: OrderListItem['service_period'] }), now = new Date()): { label: string; tone: StatusTone } {
  if (order.needs_review) return { label: '正在核对', tone: 'warning' };
  const period = order.service_period;
  if (period && new Date(period.start) <= now && now < new Date(period.end) && order.status !== 'closed')
    return { label: '本期进行中', tone: 'muted' };
  switch (order.status) {
    case 'fulfilled':
      return { label: '已支付', tone: 'success' };
    case 'paid_pending_entitlement':
      return { label: '已支付 · 到账中', tone: 'success' };
    case 'pending_payment':
      return { label: '待支付', tone: 'neutral' };
    case 'closed':
      return { label: '已关闭', tone: 'muted' };
    default:
      return { label: order.status, tone: 'neutral' };
  }
}

export function channelLabel(channel: string | null | undefined): string {
  if (!channel) return '—';
  const map: Record<string, string> = { alipay: '支付宝', wechat: '微信支付', wechat_pay: '微信支付', wechatpay: '微信支付', google_pay: 'Google Pay', card: '银行卡' };
  return map[channel] ?? channel;
}

export function invoiceStatusLabel(status: string): string {
  const map: Record<string, string> = {
    requested: '已申请，处理中',
    accepted: '已受理，开具中',
    needs_information: '需补充开票信息',
    issued: '已开具',
    sending: '正在发送',
    sent: '已发送到邮箱',
    failed: '开具失败，请在官网查看',
  };
  return map[status] ?? '状态未知，请在官网查看';
}

export function isSubscriptionLive(sub: Subscription | null): boolean {
  return !!sub && sub.status === 'active';
}
