import type { TagProps } from '../../components/Tag/Tag';
import type { Schedule } from './api';

const WEEKDAY = ['', '周一', '周二', '周三', '周四', '周五', '周六', '周日'];

export function scheduleText(s: Schedule): string {
  const day = s.kind === 'weekly' ? `${WEEKDAY[s.weekday ?? 1]} ` : s.kind === 'monthly' ? `每月 ${s.monthday ?? 1} 日 ` : '每天 ';
  const kind = s.kind === 'daily' ? '' : s.kind === 'weekly' ? '每周' : '';
  const text = s.kind === 'daily' ? `每天 ${s.time}` : s.kind === 'weekly' ? `每${WEEKDAY[s.weekday ?? 1]} ${s.time}` : `每月 ${s.monthday ?? 1} 日 ${s.time}`;
  return text || `${kind}${day}${s.time}`;
}

export const STATE_TAG: Record<string, TagProps> = {
  enabled: { label: '已启用', tone: 'success', size: 18 },
  paused: { label: '已暂停', tone: 'neutral', size: 18 },
  draft: { label: '草稿', tone: 'warning', size: 18 },
};

export const RUN_TAG: Record<string, TagProps> = {
  running: { label: '运行中', tone: 'info', size: 18 },
  succeeded: { label: '成功', tone: 'success', size: 18 },
  done: { label: '已完成', tone: 'success', size: 18 },
  failed: { label: '失败', tone: 'danger', size: 18 },
  missed: { label: '已错过', tone: 'warning', size: 18 },
  awaiting_user: { label: '等你确认', tone: 'warning', size: 18 },
  queued: { label: '排队中', tone: 'neutral', size: 18 },
};

export const MISSED_REASON: Record<string, string> = {
  mac_asleep: 'Mac 当时在休眠',
  app_not_running: 'Sage 当时没有在运行',
};

export const TRIGGER_TEXT: Record<string, string> = {
  schedule: '按计划',
  manual: '手动',
  missed_makeup: '补跑',
};

export function dayTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export const FAILURE_TEXT: Record<string, string> = {
  connection_invalid: '连接失效',
  connection_required: '缺少连接',
  authorization_required: '缺少权限',
};

const pad2 = (n: number) => String(n).padStart(2, '0');

function sameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** 今天 / 昨天 / 明天, otherwise M月D日. */
export function relativeDay(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return '—';
  const d = new Date(iso);
  const shift = (days: number) => new Date(now.getFullYear(), now.getMonth(), now.getDate() + days);
  if (sameDay(d, now)) return '今天';
  if (sameDay(d, shift(-1))) return '昨天';
  if (sameDay(d, shift(1))) return '明天';
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export function relativeDayTime(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return `${relativeDay(iso, now)} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

/** "成功 · 昨天" / "失败 · 连接失效" for the latest finished run. */
export function runResultText(run: { state: string; scheduled_for?: string | null; finished_at?: string | null; failure_code?: string | null }): string {
  const label = RUN_TAG[run.state]?.label ?? run.state;
  if (run.state === 'failed' && run.failure_code) return `${label} · ${FAILURE_TEXT[run.failure_code] ?? run.failure_code}`;
  return `${label} · ${relativeDay(run.finished_at ?? run.scheduled_for)}`;
}

/** Next local occurrence of a schedule, as "11月10日 周一 09:00"; null when the schedule is incomplete. */
export function nextRunText(kind: string, time: string, weekday: number | null, monthday: number | null, now = new Date()): string | null {
  if (!kind || !time) return null;
  const [h, m] = time.split(':').map(Number);
  for (let offset = 0; offset < 400; offset += 1) {
    const day = new Date(now.getFullYear(), now.getMonth(), now.getDate() + offset, h, m);
    const iso = day.getDay() === 0 ? 7 : day.getDay();
    if (kind === 'weekly' && iso !== weekday) continue;
    if (kind === 'monthly') {
      const last = new Date(day.getFullYear(), day.getMonth() + 1, 0).getDate();
      if (day.getDate() !== Math.min(monthday ?? 1, last)) continue;
    }
    if (day <= now) continue;
    return `${day.getMonth() + 1}月${day.getDate()}日 ${WEEKDAY[iso]} ${time}`;
  }
  return null;
}

/** "指令、工作区、计划和主责专家" */
export function joinFields(fields: string[]): string {
  if (fields.length <= 1) return fields.join('');
  return `${fields.slice(0, -1).join('、')}和${fields[fields.length - 1]}`;
}

export function durationText(seconds: number | null | undefined): string {
  if (seconds == null) return '—';
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m} 分钟`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h} 小时${m % 60 ? ` ${m % 60} 分钟` : ''}` : `${Math.floor(h / 24)} 天${h % 24 ? ` ${h % 24} 小时` : ''}`;
}
