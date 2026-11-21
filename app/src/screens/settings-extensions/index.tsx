import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { Switch } from '../../components/Switch/Switch';
import { errorMessage, QueryGate } from '../../components/States/States';
import { SettingsLayout, SettingsRow, SettingsSection } from '../../features/settings/SettingsLayout';
import { getInstalledExtensions } from '../../features/settings/api';

import { useProfile } from '../../features/settings/useProfile';

const KINDS: Array<{ kind: string; label: string }> = [
  { kind: 'plugin', label: '插件' },
  { kind: 'skill', label: '技能' },
  { kind: 'connector', label: '连接器' },
  { kind: 'agent', label: '智能体' },
];

/** 73 设置 · 扩展 (676:2). */
export default function Screen({ navigate }: ScreenProps) {
  const { query, profile, patch, save } = useProfile();
  const installedQ = useApiQuery((s) => getInstalledExtensions(s), []);
  const items = installedQ.data?.items ?? [];
  const counts = KINDS.map((k) => ({ ...k, n: installedQ.data?.kind_counts?.[k.kind] ?? items.filter((i) => i.kind === k.kind).length })).filter((k) => k.n > 0);
  const total = installedQ.data?.total ?? items.length;
  const summary = installedQ.data
    ? total === 0
      ? '还没有安装扩展'
      : `已安装 ${total} 项${counts.length ? '：' + counts.map((c) => `${c.label} ${c.n}`).join(' · ') : ''}`
    : installedQ.status === 'error'
      ? `已安装扩展暂时无法读取（${errorMessage(installedQ.error, '请稍后重试')}）`
      : '正在读取已安装扩展…';

  return (
    <SettingsLayout active="extensions" title="扩展" save={{ status: save.status, message: save.message }}>
      <QueryGate q={query} hasData={!!profile} rows={4}>
        {profile && (
          <>
            <SettingsSection title="默认行为">
              <SettingsRow label="新安装的扩展默认只读" description="写入类权限首期不开放">
                <Switch checked disabled label="新安装的扩展默认只读" onChange={() => undefined} />
              </SettingsRow>
              <SettingsRow label="允许专家在任务中自动调用已连接的扩展" description="只读查询自动进行；涉及费用或外部影响时仍会出确认卡">
                <Switch checked={profile.ext_auto_invoke} label="允许专家自动调用扩展" onChange={(v) => void patch({ ext_auto_invoke: v })} />
              </SettingsRow>
              <SettingsRow label="自动更新扩展" description="更新后的扩展若申请了新权限，会先询问你">
                <Switch checked={profile.ext_auto_update} label="自动更新扩展" onChange={(v) => void patch({ ext_auto_update: v })} />
              </SettingsRow>
            </SettingsSection>
            <SettingsSection title="技能">
              <SettingsRow label="在输入框“技能”中显示需要连接的技能" description="关闭后只显示可以直接使用的技能">
                <Switch checked={profile.ext_show_needs_connection} label="显示需要连接的技能" onChange={(v) => void patch({ ext_show_needs_connection: v })} />
              </SettingsRow>
            </SettingsSection>
            <div className="sg-profile__gap" />
            <SettingsRow label={summary} description="在扩展页查看与管理">
              {installedQ.status === 'error' && <Button variant="ghost" size={32} label="重试" onClick={installedQ.reload} />}
              <Button variant="secondary" size={32} label="打开扩展页" onClick={() => navigate('/extensions')} />
            </SettingsRow>
          </>
        )}
      </QueryGate>
    </SettingsLayout>
  );
}
