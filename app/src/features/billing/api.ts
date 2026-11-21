/**
 * Billing reads for the 用量与账单 screens. The generated types in api/types.ts only cover the
 * keys added by the design and omit the existing commerce keys, so the shapes below follow
 * commerce/billing.py, commerce/subscriptions.py and workbench/services/billing.py.
 */
import { request, requestBlob } from '../../api/client';

export interface CreditsSummary {
  available: string;
  reserved: string;
  consumed: string;
  expired: string;
  frozen: string;
  refunded: string;
}

export interface BillingAccount {
  credits: CreditsSummary;
  capabilities: { subscription: boolean; invoices: boolean; payment_methods: boolean; refunds: boolean };
  account: { id: string; name: string; kind: 'personal' | 'business' | string; role: string };
}

export interface Subscription {
  id: string;
  plan_code: string;
  unit_amount_minor: number | null;
  currency: string | null;
  billing_cycle: 'monthly' | 'annual' | string;
  status: 'active' | 'scheduled' | 'expired' | string;
  period_start: string | null;
  period_end: string | null;
  auto_renew: boolean;
  cancellation_state: string;
}

export interface OrderSnapshot {
  kind?: 'subscription' | 'credits' | string;
  code?: string;
  billing_cycle?: string | null;
  credits_quantity?: string | number | null;
  seat_quantity?: number;
  unit_amount_minor?: number;
  currency?: string;
  [key: string]: unknown;
}

export interface Order {
  id: string;
  account_id: string;
  status: 'pending_payment' | 'paid_pending_entitlement' | 'fulfilled' | 'closed' | string;
  amount_minor: number;
  currency: string;
  snapshot: OrderSnapshot;
  created_at: string;
  paid_at: string | null;
  plan_code: string | null;
  billing_cycle: string | null;
  seat_quantity: number | null;
  display_number: string | null;
}

export interface OrderListItem extends Order {
  service_period: { start: string; end: string } | null;
  needs_review: boolean;
  invoice_state: string | null;
}

export interface OrdersPage {
  account_id: string;
  items: OrderListItem[];
  offset?: number;
  total?: number;
  next_offset: number | null;
}

export interface OrderPayment {
  id: string;
  status: string;
  amount_minor: number;
  currency: string;
  paid_at: string;
  channel?: string;
  confirmed_at?: string;
  transaction_reference_masked?: string;
}

export interface OrderDetail {
  order: Order & { discount?: { amount_minor: number; label: string } | null };
  payments: OrderPayment[];
  delivery: { status: 'completed' | 'pending' | 'not_started' | string; credit_batch?: { quantity: string; granted_at: string; expires_at: string } | null };
  needs_review: boolean;
  review_reasons: string[];
}

export interface InvoiceRequest {
  id: string;
  order_id: string;
  title: string;
  recipient_email: string;
  status: string;
  created_at: string;
}

export interface UsageSummary {
  account_id: string;
  period_start: string;
  period_end: string;
  period_used: string;
  period_total: string;
  used_ratio: number | null;
  daily_average: string;
  daily: Array<{ date: string; credits: string }>;
  as_of: string;
}

export interface UsageTask {
  task_id: string | null;
  task_key: string;
  title: string | null;
  workspace: { id: string; name: string } | null;
  source: 'task' | 'automation' | 'other' | string;
  credits: string;
  last_used_at: string;
}

export interface UsageTasksPage {
  account_id: string;
  items: UsageTask[];
  offset: number;
  total: number;
  next_offset: number | null;
}

export const getBillingAccount = (signal?: AbortSignal) => request<BillingAccount>('GET', 'billing/account', { signal });
export const getSubscriptions = (signal?: AbortSignal) => request<{ items: Subscription[] }>('GET', 'billing/subscriptions', { signal });
export const getOrders = (offset: number, signal?: AbortSignal) => request<OrdersPage>('GET', 'billing/orders', { query: { offset }, signal });
export const getOrder = (id: string, signal?: AbortSignal) => request<OrderDetail>('GET', `billing/orders/${encodeURIComponent(id)}`, { signal });
export const getInvoices = (accountId: string, signal?: AbortSignal) =>
  request<{ items: InvoiceRequest[]; available?: boolean }>('GET', 'account/invoices', { query: { account_id: accountId }, signal });
export const getUsageSummary = (signal?: AbortSignal) => request<UsageSummary>('GET', 'app/usage/summary', { signal });
export const getUsageTasks = (sort: 'recent' | 'credits', offset: number, signal?: AbortSignal) =>
  request<UsageTasksPage>('GET', 'app/usage/tasks', { query: { sort, offset }, signal });

export interface BillingDisplayOrder {
  order_id: string;
  discount: { amount_minor: number; label: string } | null;
  transaction_reference_display: string | null;
  invoice_file: 'available' | 'after_review' | null;
}

export interface BillingDisplay {
  account_id: string;
  payment_channel: string | null;
  orders: BillingDisplayOrder[];
}

export const getBillingDisplay = (signal?: AbortSignal) => request<BillingDisplay>('GET', 'app/billing/display', { signal });

/** Fetches the bill or issued invoice PDF and hands it to the browser as a download. */
export async function downloadBillingPdf(orderId: string, document: 'bill' | 'invoice', filename: string): Promise<void> {
  const blob = await requestBlob(`app/billing/invoices/${encodeURIComponent(orderId)}/pdf`, { query: { document } });
  const url = URL.createObjectURL(blob);
  const a = window.document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** The subscription whose period covers now, else the next scheduled one. */
export function currentSubscription(items: Subscription[] | undefined): Subscription | null {
  if (!items?.length) return null;
  return items.find((s) => s.status === 'active') ?? items.find((s) => s.status === 'scheduled') ?? null;
}
