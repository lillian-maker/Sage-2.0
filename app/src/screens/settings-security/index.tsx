import { useEffect, useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { ConfirmDialog } from '../../components/ConfirmDialog/ConfirmDialog';
import { IconTile } from '../../components/IconTile/IconTile';
import { Select } from '../../components/Select/Select';
import { EmptyState, ErrorBanner, errorMessage, QueryGate } from '../../components/States/States';
import { Switch } from '../../components/Switch/Switch';
import { Tag } from '../../components/Tag/Tag';
import { fmtDay, fmtRelative } from '../../features/billing/format';
import { SettingsLayout, SettingsRow, SettingsSection } from '../../features/settings/SettingsLayout';
import { getGrants, getSessions, revokeGrant, revokeSession, type DeviceSession, type Grant } from '../../features/settings/api';

import { useProfile } from '../../features/settings/useProfile';

const ACTION_LABEL: Record<string, string> = { read_query: '只读查询' };
const SCOPE_LABEL: Record<string, string> = { workspace: '本工作区内总是允许', task: '本任务内总是允许' };

// The service has no IP geolocation for local or private addresses, so this device falls back to its time zone's city.
const ZONE_CITY: Record<string, string> = {
  'Asia/Shanghai': '上海', 'Asia/Chongqing': '重庆', 'Asia/Urumqi': '乌鲁木齐', 'Asia/Hong_Kong': '香港', 'Asia/Macau': '澳门',
  'Asia/Taipei': '台北', 'Asia/Tokyo': '东京', 'Asia/Seoul': '首尔', 'Asia/Singapore': '新加坡', 'Europe/Berlin': '柏林',
  'Europe/London': '伦敦', 'Europe/Paris': '巴黎', 'America/New_York': '纽约', 'America/Los_Angeles': '洛杉矶',
};
function sessionPlace(s: DeviceSession): string {
  if (s.location && s.location !== '未知') return s.location;
  const city = s.is_current ? ZONE_CITY[Intl.DateTimeFormat().resolvedOptions().timeZone] : undefined;
  return city ?? (s.ip || '位置未知');
}

function deviceTitle(s: DeviceSession): string {
  const name = s.device_name ?? s.client;
  return s.app_version ? `${name} · Sage ${s.app_version}` : name;
}

function usePaged<T>(fetcher: (offset: number, signal?: AbortSignal) => Promise<{ items: T[]; next_offset: number | null }>) {
  const first = useApiQuery((s) => fetcher(0, s), []);
  const [items, setItems] = useState<T[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (first.data) {
      setItems(first.data.items);
      setNext(first.data.next_offset);
    }
  }, [first.data]);
  const more = async () => {
    if (next === null) return;
    setLoading(true);
    setError(null);
    try {
      const page = await fetcher(next);
      setItems((x) => [...x, ...page.items]);
      setNext(page.next_offset);
    } catch (err) {
      setError(errorMessage(err, '加载失败，请重试'));
    } finally {
      setLoading(false);
    }
  };
  return { first, items, setItems, next, loading, error, more };
}

/** 74 设置 · 安全 (677:2). The current session is never offered for revocation. */
export default function Screen(_props: ScreenProps) {
  const { query, profile, patch, save } = useProfile();
  const sessions = usePaged<DeviceSession>(getSessions);
  const grants = usePaged<Grant>(getGrants);
  const [pending, setPending] = useState<{ kind: 'session'; item: DeviceSession } | { kind: 'grant'; item: Grant } | null>(null);
  const [busy, setBusy] = useState(false);

  const confirm = async () => {
    if (!pending) return;
    setBusy(true);
    if (pending.kind === 'session') {
      const id = pending.item.id;
      const ok = await save.run(() => revokeSession(id));
      if (ok) sessions.setItems((x) => x.filter((s) => s.id !== id));
    } else {
      const id = pending.item.id;
      const ok = await save.run(() => revokeGrant(id).then(() => true));
      if (ok) grants.setItems((x) => x.filter((g) => g.id !== id));
    }
    setBusy(false);
    setPending(null);
  };

  return (
    <SettingsLayout active="security" title="安全" save={{ status: save.status, message: save.message }}>
      <QueryGate q={query} hasData={!!profile} rows={2}>
        {profile && (
          <SettingsSection title="登录">
            <SettingsRow label="两步验证" description="登录新设备时需要验证码">
              <Switch checked={profile.two_step_on_new_device} label="两步验证" onChange={(v) => void patch({ two_step_on_new_device: v })} />
            </SettingsRow>
            <SettingsRow label="闲置自动锁定" description="锁定后需要重新验证，正在运行的任务不受影响">
              <Select
                size={32}
                width={104}
                label="闲置自动锁定"
                value={profile.idle_lock_minutes === null ? 'never' : String(profile.idle_lock_minutes)}
                options={[
                  { value: '15', label: '15 分钟' },
                  { value: '30', label: '30 分钟' },
                  { value: '60', label: '60 分钟' },
                  { value: 'never', label: '从不' },
                ]}
                onChange={(v) => void patch({ idle_lock_minutes: v === 'never' ? null : Number(v) })}
              />
            </SettingsRow>
          </SettingsSection>
        )}
      </QueryGate>
      <SettingsSection title="已登录的设备">
        <QueryGate q={sessions.first} rows={2}>
          {sessions.items.map((s) => (
            <div className="sg-listrow" key={s.id}>
              <IconTile icon={/ios|android|mobile/i.test(s.client) ? 'smartphone' : 'laptop'} size={28} tone="neutral" shape="rounded" />
              <div className="sg-listrow__text">
                <b>
                  {deviceTitle(s)}
                  {s.is_current && <Tag label="当前设备" tone="success" size={18} />}
                </b>
                <span>
                  {sessionPlace(s)} · {s.is_current ? '刚刚' : fmtRelative(s.last_seen_at ?? s.created_at)}
                </span>
              </div>
              {!s.is_current && (
                <button type="button" className="sg-textaction is-danger" onClick={() => setPending({ kind: 'session', item: s })}>
                  退出该设备
                </button>
              )}
            </div>
          ))}
          {sessions.error && <ErrorBanner message={sessions.error} onRetry={() => void sessions.more()} />}
          {sessions.next !== null && <Button variant="ghost" size={32} label="加载更多" busy={sessions.loading} onClick={() => void sessions.more()} />}
        </QueryGate>
      </SettingsSection>
      <SettingsSection title="已授权的操作" description="在确认卡中选择“总是允许”的操作会列在这里；不同工作区之间不继承">
        <QueryGate q={grants.first} rows={2}>
          {grants.first.data && grants.items.length === 0 && (
            <div className="sg-settings__state">
              <EmptyState icon="puzzle" title="没有已授权的操作" description="在确认卡中选择“总是允许”后，会列在这里" />
            </div>
          )}
          {grants.items.map((g) => (
            <div className="sg-listrow" key={g.id}>
              <IconTile icon="puzzle" size={28} tone="neutral" shape="rounded" />
              <div className="sg-listrow__text">
                <b>
                  {g.extension.name} · {g.scope_text || (ACTION_LABEL[g.action_kind] ?? g.action_kind)}
                </b>
                <span>
                  {SCOPE_LABEL[g.scope] ?? g.scope} · {g.workspace?.name ?? g.task?.title ?? '—'} · {fmtDay(g.created_at)}授予
                </span>
              </div>
              <button type="button" className="sg-textaction" onClick={() => setPending({ kind: 'grant', item: g })}>
                撤销
              </button>
            </div>
          ))}
          {grants.error && <ErrorBanner message={grants.error} onRetry={() => void grants.more()} />}
          {grants.next !== null && <Button variant="ghost" size={32} label="加载更多" busy={grants.loading} onClick={() => void grants.more()} />}
        </QueryGate>
      </SettingsSection>
      {pending && (
        <ConfirmDialog
          title={pending.kind === 'session' ? '退出该设备？' : '撤销这项授权？'}
          message={
            pending.kind === 'session'
              ? `“${deviceTitle(pending.item)}”将退出登录，再次使用需要重新登录。`
              : `之后 ${pending.item.extension.name} 的同类操作会重新出确认卡。`
          }
          confirmLabel={pending.kind === 'session' ? '退出该设备' : '撤销'}
          cancelLabel="取消"
          tone="warning"
          busy={busy}
          onConfirm={() => void confirm()}
          onCancel={() => setPending(null)}
        />
      )}
    </SettingsLayout>
  );
}
