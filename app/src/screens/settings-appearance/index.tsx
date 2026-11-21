import type { ScreenProps } from '../../app/router';
import { THEME_STYLES, useTheme, type AppearanceMode, type Density, type ThemeStyle } from '../../app/ThemeProvider';
import { expertAvatarSrc } from '../../components/Avatar/Avatar';
import { ChoiceChip } from '../../components/ChoiceChip/ChoiceChip';
import { SageMark } from '../../components/Icon/SageMark';
import { SegmentedControl } from '../../components/SegmentedControl/SegmentedControl';
import { Select } from '../../components/Select/Select';
import { SettingsLayout, SettingsRow, SettingsSection, type SaveStatus } from '../../features/settings/SettingsLayout';

import { useProfile } from '../../features/settings/useProfile';
import { errorMessage, QueryGate } from '../../components/States/States';
import { useEffect, useRef, useState } from 'react';

const STYLE_INFO: Record<ThemeStyle, { name: string; desc: string }> = {
  business_steady: { name: '商务 · 稳重', desc: 'Sage 蓝 · Manrope · 写实头像' },
  neo_electric: { name: '新锐 · 电光', desc: '电光紫 + 青柠 · Space Grotesk · 等距体块头像' },
  fashion_editorial: { name: '时尚 · 编辑', desc: '墨黑 + 骨白 + 朱砂 · Syne · 编辑拼贴头像' },
  comfort_readable: { name: '舒适 · 易读', desc: '深海青 + 暖纸 · Atkinson 易读体 · 平涂插画头像' },
  minimal_focus: { name: '极简 · 专注', desc: '黑白 + 电光橙 · Geist · 线性符号头像' },
};
const MODES: Array<{ value: AppearanceMode; label: string }> = [
  { value: 'system', label: '跟随系统' },
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
];
const DENSITIES: Array<{ value: Density; label: string }> = [
  { value: 'theme', label: '跟随风格' },
  { value: 'comfortable', label: '舒适' },
  { value: 'compact', label: '紧凑' },
];

function systemLanguageLabel(): string {
  const lang = typeof navigator !== 'undefined' ? navigator.language : 'zh-CN';
  return lang.toLowerCase().startsWith('zh') ? '简体中文' : 'English';
}

/** Theme writes go through ThemeProvider so the whole App previews at once and rolls back on failure. */
function useThemeSave() {
  const theme = useTheme();
  const [status, setStatus] = useState<SaveStatus>('idle');
  const [message, setMessage] = useState<string | null>(null);
  const pending = useRef(false);
  useEffect(() => {
    if (!pending.current || theme.saving) return;
    pending.current = false;
    if (theme.error) {
      setStatus('error');
      setMessage(errorMessage(theme.error, '保存失败，已恢复原设置'));
    } else setStatus('saved');
  }, [theme.saving, theme.error]);
  const update = (patch: Parameters<typeof theme.update>[0]) => {
    pending.current = true;
    setStatus('saving');
    setMessage(null);
    void theme.update(patch);
  };
  return { prefs: theme.prefs, status, message, update };
}

/** 68 设置 · 外观 (730:2). Boards 731:2 and 732:2 are design notes and have no App surface. */
export default function Screen({ navigate }: ScreenProps) {
  const theme = useThemeSave();
  const { query, profile, patch, save } = useProfile();
  const status = theme.status === 'saving' || save.status === 'saving' ? 'saving' : theme.status === 'error' ? 'error' : save.status === 'error' ? 'error' : theme.status === 'saved' || save.status === 'saved' ? 'saved' : 'idle';
  const message = theme.status === 'error' ? theme.message : save.message;

  return (
    <SettingsLayout active="appearance" title="外观与语言" wide save={{ status, message }}>
      <SettingsSection title="主题风格" description="每种风格是一套完整的视觉语言：配色、字体与专属专家头像一一对应，不单独拆分。真人成员始终显示本人头像或姓名首字母。">
        <div className="sg-themecards" role="radiogroup" aria-label="主题风格">
          {THEME_STYLES.map((style) => {
            const selected = theme.prefs.theme_style === style;
            return (
              <button
                key={style}
                type="button"
                role="radio"
                aria-checked={selected}
                className={`sg-themecard${selected ? ' is-selected' : ''}`}
                // 730:252: the current style's card opens 03 新建任务 to see it in use; other cards switch style.
                onClick={() => (selected ? navigate('/tasks/new') : theme.update({ theme_style: style }))}
              >
                <span className="sg-themecard__preview" data-theme={style} data-appearance="light" aria-hidden="true">
                  <span className="sg-themecard__main">
                    <span className="sg-themecard__brand">
                      <SageMark size={12} />
                      <span className="sg-themecard__greet">今天想推进什么？</span>
                    </span>
                    <span className="sg-themecard__composer">
                      <span className="sg-themecard__send" />
                    </span>
                    <span className="sg-themecard__line">主责：Sage 助手 · 模型：自动</span>
                    <span className="sg-themecard__taskrow">
                      <img className="sg-themecard__avatar" src={expertAvatarSrc('AGT-007', style)} alt="" />
                      <span className="sg-themecard__chip">进行中</span>
                    </span>
                    <span className="sg-themecard__line">
                      <span>查看全部任务 →</span>
                      <span className="sg-themecard__badge">3 条新消息</span>
                    </span>
                  </span>
                </span>
                <span className="sg-themecard__foot">
                  <span className="sg-themecard__name">
                    {STYLE_INFO[style].name}
                    <span className={`sg-themecard__radio${selected ? ' is-on' : ''}`} />
                  </span>
                  <span className="sg-themecard__desc">{STYLE_INFO[style].desc}</span>
                </span>
              </button>
            );
          })}
        </div>
      </SettingsSection>
      <SettingsSection title="外观模式">
        <div className="sg-modechips" role="radiogroup" aria-label="外观模式">
          {MODES.map((m) => (
            <ChoiceChip
              key={m.value}
              role="radio"
              size={34}
              label={m.label}
              selected={theme.prefs.appearance_mode === m.value}
              onToggle={() => theme.prefs.appearance_mode !== m.value && theme.update({ appearance_mode: m.value })}
            />
          ))}
        </div>
      </SettingsSection>
      <div className="sg-profile__gap" />
      <SettingsRow label="界面密度" description="默认跟随主题风格">
        <SegmentedControl label="界面密度" value={theme.prefs.density} options={DENSITIES} onChange={(v) => theme.update({ density: v })} />
      </SettingsRow>
      <QueryGate q={query} hasData={!!profile} rows={1}>
        {profile && (
          <SettingsRow label="语言">
            <Select
              size={32}
              width={200}
              label="语言"
              value={profile.language}
              options={[
                { value: 'system', label: `跟随系统（${systemLanguageLabel()}）` },
                { value: 'zh-Hans', label: '简体中文' },
                { value: 'en', label: 'English' },
              ]}
              onChange={(v) => void patch({ language: v as 'system' | 'zh-Hans' | 'en' })}
            />
          </SettingsRow>
        )}
      </QueryGate>
    </SettingsLayout>
  );
}
