/** “32 分钟前 / 1 小时前 / 昨天 / 11月3日”, the relative format used across list rows. */
export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const diff = Math.max(0, now - t);
  const min = Math.floor(diff / 60_000);
  if (min < 1) return '刚刚';
  if (min < 60) return `${min} 分钟前`;
  const hours = Math.floor(min / 60);
  const d = new Date(t);
  const today = new Date(now);
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  if (t >= startOfToday) return `${hours} 小时前`;
  if (t >= startOfToday - 86_400_000) return '昨天';
  const md = `${d.getMonth() + 1}月${d.getDate()}日`;
  return d.getFullYear() === today.getFullYear() ? md : `${d.getFullYear()}年${md}`;
}
