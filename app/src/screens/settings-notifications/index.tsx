import { useEffect, useState } from 'react';
import { QueryGate } from '../../components/States/States';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { Switch } from '../../components/Switch/Switch';
import { SettingsLayout, SettingsRow, SettingsSection } from '../../features/settings/SettingsLayout';
import { getNotificationPrefs, putNotificationPrefs, type NotificationChannel, type NotificationPref, type NotificationType } from '../../features/settings/api';

import { CommitField } from '../../features/settings/fields';
import { useProfile } from '../../features/settings/useProfile';

const TYPES: Array<{ type: NotificationType; label: string; desc: string }> = [
  { type: 'needs_action', label: '需要我处理', desc: '审批、待补充、待验收、邀请' },
  { type: 'task_completed', label: '任务完成', desc: '我负责或参与的任务完成时' },
  { type: 'mention_assign', label: '@ 与指派', desc: '被 @ 或被指派时' },
  { type: 'billing', label: '账务', desc: 'Credits 偏低、续费、支付结果' },
];
const CHANNELS: Array<{ channel: NotificationChannel; label: string }> = [
  { channel: 'in_app', label: 'App 内提醒' },
  { channel: 'push', label: '系统推送' },
  { channel: 'email', label: '邮件' },
];
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

/** 69 设置 · 通知 (660:2). */
export default function Screen(_props: ScreenProps) {
  const { query: profileQ, profile, patch, save } = useProfile();
  const prefsQ = useApiQuery((s) => getNotificationPrefs(s), []);
  const [items, setItems] = useState<NotificationPref[] | undefined>(undefined);
  const [editingDnd, setEditingDnd] = useState(false);
  useEffect(() => {
    if (prefsQ.data) setItems(prefsQ.data.items);
  }, [prefsQ.data]);

  const toggle = async (type: NotificationType, channel: NotificationChannel, enabled: boolean) => {
    if (!items) return;
    const before = items;
    const next = items.map((p) => (p.type === type && p.channel === channel ? { ...p, enabled } : p));
    setItems(next);
    const saved = await save.run(() => putNotificationPrefs(next));
    setItems(saved ? saved.items : before);
  };
  const enabled = (type: NotificationType, channel: NotificationChannel) => items?.find((p) => p.type === type && p.channel === channel)?.enabled ?? false;

  return (
    <SettingsLayout active="notifications" title="通知" save={{ status: save.status, message: save.message }}>
      <SettingsSection title="通知方式" description="通知中心始终保留全部消息；这里只控制是否额外提醒你。">
        <QueryGate q={prefsQ} hasData={!!items} rows={4}>
          <div className="sg-notifgrid" role="table" aria-label="通知方式">
            <div className="sg-notifgrid__row sg-notifgrid__row--head" role="row">
              <span role="columnheader">类型</span>
              {CHANNELS.map((c) => (
                <span role="columnheader" key={c.channel}>
                  {c.label}
                </span>
              ))}
            </div>
            {TYPES.map((t) => (
              <div className="sg-notifgrid__row" role="row" key={t.type}>
                <span role="rowheader">
                  <b>{t.label}</b>
                  <small>{t.desc}</small>
                </span>
                {CHANNELS.map((c) => (
                  <span role="cell" key={c.channel}>
                    <Switch checked={enabled(t.type, c.channel)} label={`${t.label} · ${c.label}`} onChange={(v) => void toggle(t.type, c.channel, v)} />
                  </span>
                ))}
              </div>
            ))}
          </div>
        </QueryGate>
      </SettingsSection>
      <div className="sg-profile__gap" />
      <QueryGate q={profileQ} hasData={!!profile} rows={2}>
        {profile && (
          <>
            <SettingsRow label="勿扰时段" description="勿扰期间只保留“需要我处理”的系统推送">
              {!editingDnd ? (
                <button type="button" className="sg-dnd__value" aria-label="修改勿扰时段" onClick={() => setEditingDnd(true)}>
                  {`${profile.dnd_start ?? '22:00'} – ${profile.dnd_end ?? '08:00'}`}
                </button>
              ) : (
              <span className="sg-dnd">
                <CommitField
                  label="勿扰开始"
                  width={72}
                  value={profile.dnd_start ?? '22:00'}
                  validate={(v) => (HHMM.test(v) ? (v === (profile.dnd_end ?? '08:00') ? '起止不能相同' : null) : '格式 HH:MM')}
                  onCommit={(v) => patch({ dnd_start: v, dnd_end: profile.dnd_end ?? '08:00' })}
                />
                <span aria-hidden="true">–</span>
                <CommitField
                  label="勿扰结束"
                  width={72}
                  value={profile.dnd_end ?? '08:00'}
                  validate={(v) => (HHMM.test(v) ? (v === (profile.dnd_start ?? '22:00') ? '起止不能相同' : null) : '格式 HH:MM')}
                  onCommit={(v) => patch({ dnd_end: v, dnd_start: profile.dnd_start ?? '22:00' })}
                />
                <Button variant="ghost" size={28} label="完成" onClick={() => setEditingDnd(false)} />
              </span>
              )}
              <Switch
                checked={profile.dnd_enabled}
                label="勿扰时段"
                onChange={(v) =>
                  void patch(v && !profile.dnd_start ? { dnd_enabled: v, dnd_start: '22:00', dnd_end: '08:00' } : { dnd_enabled: v })
                }
              />
            </SettingsRow>
            <SettingsRow label="App 图标角标" description="显示“需要我处理”的数量">
              <Switch checked={profile.badge_enabled} label="App 图标角标" onChange={(v) => void patch({ badge_enabled: v })} />
            </SettingsRow>
          </>
        )}
      </QueryGate>
    </SettingsLayout>
  );
}
