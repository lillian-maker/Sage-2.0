import { useRef, useState } from 'react';
import { loadSession } from '../../api/client';
import type { ScreenProps } from '../../app/router';
import { Avatar } from '../../components/Avatar/Avatar';
import { Button } from '../../components/Button/Button';
import { IconButton } from '../../components/IconButton/IconButton';
import { Select } from '../../components/Select/Select';
import { Tag } from '../../components/Tag/Tag';
import { TextField } from '../../components/TextField/TextField';
import { errorMessage, QueryGate } from '../../components/States/States';
import { initialsOf } from '../../features/billing/format';
import { accountShortLabel } from '../../features/billing/labels';
import { SettingsLayout, SettingsRow, SettingsSection } from '../../features/settings/SettingsLayout';
import { logout, selectAccount, uploadAvatar, type Profile } from '../../features/settings/api';
import { ROLE_LABEL, SettingsDialog, activeAccount, useSession } from '../../features/settings/common';
import { CommitField } from '../../features/settings/fields';
import { useProfile } from '../../features/settings/useProfile';

type ListKey = 'business_markets' | 'business_channels' | 'business_categories';

/** 67 设置 · 个人资料 (765:2). */
export default function Screen({ navigate }: ScreenProps) {
  const { query, profile, patch, save } = useProfile();
  const session = useSession();
  const account = activeAccount(session);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [avatarUrl, setAvatarUrl] = useState<string | null | undefined>(undefined);
  const fileRef = useRef<HTMLInputElement>(null);
  const [switchOpen, setSwitchOpen] = useState(false);
  const [logoutOpen, setLogoutOpen] = useState(false);

  const onAvatar = async (file: File | undefined) => {
    if (!file) return;
    setAvatarBusy(true);
    const out = await save.run(() => uploadAvatar(file));
    if (out) {
      setAvatarUrl(out.avatar_url);
      void loadSession(true);
    }
    setAvatarBusy(false);
    if (fileRef.current) fileRef.current.value = '';
  };

  const roleLine = account ? (account.kind === 'business' ? `${account.name} · ${ROLE_LABEL[account.role] ?? account.role}` : '个人账户') : '';
  const shownAvatar = avatarUrl !== undefined ? avatarUrl : profile?.avatar_url;

  return (
    <SettingsLayout active="profile" title="个人资料" save={{ status: save.status, message: save.message }}>
      <QueryGate q={query} hasData={!!profile} rows={5}>
        {profile && (
          <>
            <div className="sg-profile__head">
              <Avatar size={56} alt={profile.display_name} initials={initialsOf(profile.display_name).slice(0, 1)} src={shownAvatar} />
              <div className="sg-profile__who">
                <b>{profile.display_name || '—'}</b>
                <span>{roleLine}</span>
              </div>
              <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={(e) => void onAvatar(e.target.files?.[0])} />
              <Button variant="secondary" size={32} label="更换头像" busy={avatarBusy} onClick={() => fileRef.current?.click()} />
            </div>
            <SettingsRow label="名称">
              <CommitField
                label="名称"
                value={profile.display_name}
                maxLength={80}
                validate={(v) => (v ? null : '名称不能为空')}
                onCommit={(v) => patch({ display_name: v })}
              />
            </SettingsRow>
            <SettingsRow label="邮箱" description={profile.email_read_only ? '由企业统一管理，不能在此修改' : '邮箱暂不支持在 App 内修改'}>
              <TextField size={32} width={240} value={profile.email} readOnly aria-label="邮箱" />
            </SettingsRow>
            <SettingsRow label="Sage 怎么称呼你" description="用于首页问候与回复">
              <CommitField
                label="Sage 怎么称呼你"
                value={profile.preferred_name ?? ''}
                placeholder={profile.display_name}
                maxLength={40}
                onCommit={(v) => patch({ preferred_name: v })}
              />
            </SettingsRow>
            <SettingsRow label="给 Sage 助手起个名字" description="2–8 个字；不填时显示“Sage 助手”">
              <CommitField
                label="助手名"
                value={profile.assistant_name ?? ''}
                placeholder="Sage 助手"
                maxLength={8}
                validate={(v) => (v === '' || (Array.from(v).length >= 2 && Array.from(v).length <= 8) ? null : '需要 2–8 个字')}
                onCommit={(v) => patch({ assistant_name: v })}
              />
            </SettingsRow>
            <SettingsSection title="业务信息" description="用于推荐专家与交办示例">
              <ListRow label="目标市场" field="business_markets" profile={profile} patch={patch} />
              <ListRow label="主要渠道" field="business_channels" profile={profile} patch={patch} />
              <ListRow label="品类" field="business_categories" profile={profile} patch={patch} />
            </SettingsSection>
            <div className="sg-profile__gap" />
            <SettingsRow label="账号" description="切换账号前，正在发送的任务会先完成">
              <Button variant="secondary" size={32} label="切换账号" disabled={!session || session.accounts.length < 2} onClick={() => setSwitchOpen(true)} />
              <Button variant="secondary" size={32} label="退出登录" onClick={() => setLogoutOpen(true)} />
            </SettingsRow>
          </>
        )}
      </QueryGate>
      {switchOpen && session && <SwitchAccountDialog current={session.active_account_id} accounts={session.accounts} onClose={() => setSwitchOpen(false)} />}
      {logoutOpen && <LogoutDialog onClose={() => setLogoutOpen(false)} onDone={() => navigate('/login', { replace: true })} />}
    </SettingsLayout>
  );
}

function ListRow({ label, field, profile, patch }: { label: string; field: ListKey; profile: Profile; patch: (c: Partial<Profile>) => Promise<boolean> }) {
  const items = profile[field] ?? [];
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState('');
  const add = async () => {
    const v = draft.trim();
    setAdding(false);
    setDraft('');
    if (!v || items.includes(v)) return;
    await patch({ [field]: [...items, v] } as Partial<Profile>);
  };
  return (
    <SettingsRow label={label}>
      <span className="sg-profile__tags">
        {items.map((item) => (
          <Tag key={item} label={item} tone="neutral" size={26} removable onRemove={() => void patch({ [field]: items.filter((x) => x !== item) } as Partial<Profile>)} />
        ))}
        {adding ? (
          <TextField
            size={32}
            width={120}
            autoFocus
            value={draft}
            aria-label={`添加${label}`}
            maxLength={40}
            onValueChange={setDraft}
            onBlur={() => void add()}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void add();
              if (e.key === 'Escape') {
                setAdding(false);
                setDraft('');
              }
            }}
          />
        ) : (
          <IconButton icon="plus" size={28} variant="tonal" shape="pill" label={`添加${label}`} onClick={() => setAdding(true)} />
        )}
      </span>
    </SettingsRow>
  );
}

function SwitchAccountDialog({ current, accounts, onClose }: { current: string | null; accounts: Array<{ id: string; name: string; kind: string; role: string }>; onClose: () => void }) {
  const [value, setValue] = useState(current ?? accounts[0]?.id ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirm = async () => {
    if (value === current) return onClose();
    setBusy(true);
    setError(null);
    try {
      await selectAccount(value);
      await loadSession(true);
      onClose();
    } catch (err) {
      setError(errorMessage(err, '切换失败，请重试'));
    } finally {
      setBusy(false);
    }
  };
  return (
    <SettingsDialog title="切换账号" description="切换后，工作区、任务与额度都会显示所选账号的内容。" confirmLabel="切换" busy={busy} onConfirm={() => void confirm()} onCancel={onClose} footerNote={error}>
      <Select
        size={32}
        label="选择账号"
        value={value}
        options={accounts.map((a) => ({ value: a.id, label: `${a.kind === 'business' ? accountShortLabel(a) : '个人账户'} · ${ROLE_LABEL[a.role] ?? a.role}` }))}
        onChange={setValue}
      />
    </SettingsDialog>
  );
}

function LogoutDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await logout();
      await loadSession(true).catch(() => undefined);
      onDone();
    } catch (err) {
      setError(errorMessage(err, '退出失败，请重试'));
      setBusy(false);
    }
  };
  return (
    <SettingsDialog title="退出登录" description="退出后需要重新登录才能查看任务与工作区。" confirmLabel="退出登录" busy={busy} onConfirm={() => void confirm()} onCancel={onClose} footerNote={error} />
  );
}
