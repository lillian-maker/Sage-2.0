import type { IconName } from '../../components/Icon/Icon';
import type { TagProps } from '../../components/Tag/Tag';
import type { Initiative, ItemGroup, SourceKind } from './api';

export const GROUPS: Array<{ key: Exclude<ItemGroup, 'ignored'>; label: string }> = [
  { key: 'pending_confirmation', label: '待确认' },
  { key: 'todo', label: '待办' },
  { key: 'in_progress', label: '处理中' },
  { key: 'done', label: '已完成' },
];

export const GROUP_TAG: Record<ItemGroup, TagProps> = {
  pending_confirmation: { label: '待确认', tone: 'warning', size: 18 },
  todo: { label: '待办', tone: 'info', size: 18 },
  in_progress: { label: '处理中', tone: 'neutral', size: 18 },
  done: { label: '已完成', tone: 'success', size: 18 },
  ignored: { label: '已忽略', tone: 'neutral', size: 18 },
};

export const SOURCE_LABEL: Record<SourceKind, string> = {
  company_email: '企业邮箱',
  amazon_buyer_messages: '买家消息',
  store_notifications: '店铺通知',
  feishu: '飞书',
  wecom: '企微',
};

export const SOURCE_ICON: Record<SourceKind, IconName> = {
  company_email: 'message-square',
  amazon_buyer_messages: 'contact',
  store_notifications: 'bell',
  feishu: 'message-square',
  wecom: 'message-square',
};

export const INITIATIVES: Array<{ value: Initiative; label: string; hint: string }> = [
  { value: 'report_only', label: '只汇报', hint: '发现需要处理的事，整理成收件里的一条待办' },
  { value: 'draft_suggestion', label: '起草建议', hint: '同时起草回复或方案，等你确认后再由你发送' },
  { value: 'start_task', label: '主动发起任务', hint: '直接创建任务并开始研究；外发动作仍需你确认' },
];

export const SENDER_FILTERS: Array<{ value: string; label: string }> = [
  { value: 'all_members_except_bots', label: '全部成员（排除机器人）' },
  { value: 'all', label: '所有发送者' },
  { value: 'mentions_only', label: '只看 @我 的消息' },
];

export const DECISION_LABEL: Record<string, string> = { approve: '待你审批', supply: '待你补充', accept: '待你验收', invite: '邀请' };

export const ACTION_TEXT: Record<string, string> = {
  read_query: '只读查询，不写入任何外部系统',
  write: '写入外部系统',
  send: '对外发送',
};

export const VALIDITY_TEXT: Record<string, string> = {
  this_request: '仅本次任务',
  task: '本任务内有效',
  workspace: '本工作区内有效',
};

function pad(n: number) {
  return String(n).padStart(2, '0');
}

const WEEKDAY = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

/** “10:41” today, “昨天”, a weekday within the week, otherwise a date. */
export function shortTime(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return '';
  const d = new Date(iso);
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 86_400_000);
  if (diff <= 0) return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (diff === 1) return '昨天';
  if (diff < 7) return WEEKDAY[d.getDay()]!;
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export function dayLabel(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return '';
  const d = new Date(iso);
  const same = d.toDateString() === now.toDateString();
  return same ? `今天 ${pad(d.getHours())}:${pad(d.getMinutes())}` : `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function dateLabel(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export function relative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '';
  const min = Math.floor(Math.max(0, now - new Date(iso).getTime()) / 60000);
  if (min < 1) return '刚刚';
  if (min < 60) return `${min} 分钟前`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} 小时前`;
  const d = Math.floor(h / 24);
  return d === 1 ? '昨天' : `${d} 天前`;
}

export function chargeLabel(target: unknown): string {
  if (!target) return '';
  if (typeof target === 'string') return target === 'business' ? '计入企业额度' : target === 'personal' ? '计入个人额度' : '';
  const t = target as { label?: string };
  return t.label ?? '';
}
