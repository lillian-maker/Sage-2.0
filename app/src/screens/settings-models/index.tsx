import type { ScreenProps } from '../../app/router';
import { QueryGate } from '../../components/States/States';
import { Switch } from '../../components/Switch/Switch';
import { SettingsLayout, SettingsRow, SettingsSection } from '../../features/settings/SettingsLayout';
import { useSession } from '../../features/settings/common';
import { useProfile } from '../../features/settings/useProfile';

const OPTIONS: Array<{ value: 'auto' | 'standard' | 'deep'; label: string; desc: string; cost: string }> = [
  { value: 'auto', label: '自动（推荐）', desc: '由 Sage 按任务难度选择，复杂任务使用更强的模型', cost: '按实际消耗计费' },
  { value: 'standard', label: 'Sage 标准', desc: '日常任务，速度快', cost: '约 1× Credits' },
  { value: 'deep', label: 'Sage 深度', desc: '长文档、复杂分析与多专家编排', cost: '约 4× Credits' },
];

/** 72 设置 · 模型 (661:2). */
export default function Screen(_props: ScreenProps) {
  const { query, profile, patch, save } = useProfile();
  const session = useSession();
  const byok = session?.capabilities?.byok === true;
  return (
    <SettingsLayout active="models" title="模型" save={{ status: save.status, message: save.message }}>
      <QueryGate q={query} hasData={!!profile} rows={4}>
        {profile && (
          <>
            <SettingsSection title="默认模型" description="新任务默认使用的模型；每个任务仍可在输入舱下方的状态行单独切换。">
              <div role="radiogroup" aria-label="默认模型" className="sg-radiolist">
                {OPTIONS.map((o) => {
                  const on = profile.default_model === o.value;
                  return (
                    <button key={o.value} type="button" role="radio" aria-checked={on} className="sg-radiorow" onClick={() => !on && void patch({ default_model: o.value })}>
                      <span className={`sg-radio${on ? ' is-on' : ''}`} aria-hidden="true" />
                      <span className="sg-radiorow__text">
                        <b>{o.label}</b>
                        <small>{o.desc}</small>
                      </span>
                      <span className="sg-radiorow__end">{o.cost}</span>
                    </button>
                  );
                })}
                <button type="button" role="radio" aria-checked={false} aria-disabled={!byok} disabled={!byok} className="sg-radiorow is-disabled">
                  <span className="sg-radio" aria-hidden="true" />
                  <span className="sg-radiorow__text">
                    <b>自带模型密钥</b>
                    <small>企业版可用，需管理员开通</small>
                  </span>
                  <span className="sg-radiorow__end">{byok ? '已开通' : '需管理员开通'}</span>
                </button>
              </div>
            </SettingsSection>
            <div className="sg-profile__gap" />
            <SettingsRow label="任务复杂度提示" description="主责会把任务判定为简单 / 一般 / 复杂；一般和复杂任务先出开工确认卡再执行">
              <Switch checked={profile.complexity_hint_enabled} label="任务复杂度提示" onChange={(v) => void patch({ complexity_hint_enabled: v })} />
            </SettingsRow>
          </>
        )}
      </QueryGate>
    </SettingsLayout>
  );
}
