/** Display formatting shared by the billing and settings screens (zh-CN, local time zone). */

export function toNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

/** "3720" → "3,720"; keeps up to two decimals because Credits are decimal strings. */
export function fmtCredits(value: string | number | null | undefined): string {
  const n = toNumber(value);
  if (n === null) return '—';
  return n.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

/** Minor currency units → "¥449.00". */
export function fmtMoney(minor: number | null | undefined, currency = 'CNY'): string {
  if (minor === null || minor === undefined) return '—';
  const symbol = currency === 'CNY' ? '¥' : currency === 'USD' ? '$' : currency === 'EUR' ? '€' : currency + ' ';
  const sign = minor < 0 ? '−' : '';
  return `${sign}${symbol}${(Math.abs(minor) / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function fmtMonthDay(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? `${d.getMonth() + 1}月${d.getDate()}日` : '—';
}

/** Period ends are exclusive midnights; reset / renewal dates show the last covered day (“11月30日重置”). */
export function fmtPeriodLastDay(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? fmtMonthDay(new Date(d.getTime() - 1).toISOString()) : '—';
}

export function fmtMonth(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? `${d.getMonth() + 1}月` : '';
}

export function fmtTime(iso: string | null | undefined, seconds = false): string {
  const d = parse(iso);
  if (!d) return '—';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}${seconds ? ':' + p(d.getSeconds()) : ''}`;
}

export function fmtDateTime(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? `${fmtMonthDay(iso)} ${fmtTime(iso)}` : '—';
}

function dayDiff(d: Date, now: Date): number {
  const a = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const b = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  return Math.round((b - a) / 86_400_000);
}

/** 今天 / 昨天 / 11月3日. */
export function fmtDay(iso: string | null | undefined, now = new Date()): string {
  const d = parse(iso);
  if (!d) return '—';
  const diff = dayDiff(d, now);
  if (diff === 0) return '今天';
  if (diff === 1) return '昨天';
  return fmtMonthDay(iso);
}

/** 刚刚 / 12 分钟前 / 1 小时前 / 昨天 / 3 天前 / 11月3日. */
export function fmtRelative(iso: string | null | undefined, now = new Date()): string {
  const d = parse(iso);
  if (!d) return '—';
  const minutes = Math.floor((now.getTime() - d.getTime()) / 60_000);
  if (minutes < 2) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟前`;
  const diff = dayDiff(d, now);
  if (diff === 0) return `${Math.floor(minutes / 60)} 小时前`;
  if (diff === 1) return '昨天';
  if (diff < 30) return `${diff} 天前`;
  return fmtMonthDay(iso);
}

/** 23 小时后失效 style countdown; null when already past. */
export function fmtUntil(iso: string | null | undefined, now = new Date()): string | null {
  const d = parse(iso);
  if (!d) return null;
  const minutes = Math.floor((d.getTime() - now.getTime()) / 60_000);
  if (minutes <= 0) return null;
  if (minutes < 60) return `${minutes} 分钟后`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} 小时后`;
  return `${Math.floor(hours / 24)} 天后`;
}

export function initialsOf(name: string | null | undefined): string {
  const parts = (name ?? '').trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0]!.slice(0, 1).toUpperCase();
  return (parts[0]!.slice(0, 1) + parts[parts.length - 1]!.slice(0, 1)).toUpperCase();
}
