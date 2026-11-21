import { useEffect, useState } from 'react';
import { loadSession } from '../../api/client';
import { account } from '../../api/endpoints';
import type { ScreenProps } from '../../app/router';
import { useTheme, type ThemeStyle } from '../../app/ThemeProvider';
import { Button } from '../../components/Button/Button';
import { ChoiceChip } from '../../components/ChoiceChip/ChoiceChip';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { errorMessage } from '../../components/States/States';
import { safeNext } from '../../features/auth/session';
import '../../features/auth/auth.css';

type ListKey = 'business_markets' | 'business_channels' | 'business_categories';

const GROUPS: Array<{ key: ListKey; label: string; options: string[] }> = [
  { key: 'business_markets', label: '目标市场', options: ['美国', '德国', '英国', '法国', '日本', '东南亚', '中东', '其他'] },
  { key: 'business_channels', label: '主要渠道', options: ['Amazon', '独立站', 'TikTok Shop', 'Temu', '线下零售', 'B2B 批发'] },
  { key: 'business_categories', label: '品类', options: ['家用小家电', '个护美妆', '母婴', '户外运动', '家居', '3C 配件'] },
];

const THEMES: Array<{ value: ThemeStyle; label: string }> = [
  { value: 'business_steady', label: '商务 · 稳重' },
  { value: 'neo_electric', label: '新锐 · 电光' },
  { value: 'fashion_editorial', label: '时尚 · 编辑' },
  { value: 'comfort_readable', label: '舒适 · 易读' },
  { value: 'minimal_focus', label: '极简 · 专注' },
];

type Lists = Record<ListKey, string[]>;

export default function Screen({ query, navigate }: ScreenProps) {
  const next = safeNext(query.get('next'));
  const theme = useTheme();
  const { preview, adopt } = theme;
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [lists, setLists] = useState<Lists>({ business_markets: [], business_channels: [], business_categories: [] });
  const [style, setStyle] = useState<ThemeStyle>(theme.prefs.theme_style);
  const [styleTouched, setStyleTouched] = useState(false);
  const [saving, setSaving] = useState<'skip' | 'finish' | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    loadSession()
      .then((s) => {
        if (!s.authenticated) {
          navigate('/login?next=' + encodeURIComponent('/onboarding'), { replace: true });
          return;
        }
        return account.getAccountProfile(undefined, controller.signal).then((profile) => {
          const pick = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);
          setLists({
            business_markets: pick(profile.business_markets),
            business_channels: pick(profile.business_channels),
            business_categories: pick(profile.business_categories),
          });
        });
      })
      // Prefill is optional: without it the steps simply start empty.
      .catch(() => undefined);
    return () => controller.abort();
  }, [navigate]);

  // The theme step only previews; leaving the screen without finishing restores the saved theme.
  useEffect(() => () => preview(null), [preview]);

  const toggle = (key: ListKey, option: string) =>
    setLists((l) => ({ ...l, [key]: l[key].includes(option) ? l[key].filter((o) => o !== option) : [...l[key], option] }));

  const chooseStyle = (value: ThemeStyle) => {
    setStyle(value);
    setStyleTouched(true);
    preview(value);
  };

  const submit = async (mode: 'skip' | 'finish') => {
    setSaving(mode);
    setError(null);
    try {
      const body: Record<string, string[] | string | boolean> = { onboarding_completed: true };
      for (const g of GROUPS) if (lists[g.key].length > 0) body[g.key] = lists[g.key];
      if (mode === 'finish' || styleTouched) body.theme_style = style;
      const saved = await account.patchAccountProfile(body);
      adopt(saved);
      await loadSession(true).catch(() => undefined);
      navigate(next, { replace: true });
    } catch (err) {
      setError(errorMessage(err, '保存失败，请重试'));
      setSaving(null);
    }
  };

  const skip = <Button variant="ghost" size={32} label="跳过" busy={saving === 'skip'} disabled={!!saving} onClick={() => submit('skip')} />;

  return (
    <div className="sg-authpage">
      <section className="sg-onboarding" aria-labelledby="sg-onboarding-title">
        <div className="sg-onboarding__progress" aria-hidden="true">
          {[1, 2, 3].map((n) => (
            <span key={n} className={n <= step ? 'is-done' : undefined} />
          ))}
        </div>
        <div className="sg-onboarding__step">第 {step} 步，共 3 步</div>
        {step === 1 && (
          <>
            <h1 id="sg-onboarding-title">欢迎使用 Sage</h1>
            <p>先设置业务偏好与主题风格，也可以跳过。</p>
          </>
        )}
        {step === 2 && (
          <>
            <h1 id="sg-onboarding-title">你的业务主要在哪里？</h1>
            <p>用于推荐专家和交办示例，之后可在“设置 › 个人资料”修改。可以跳过。</p>
            {GROUPS.map((g) => (
              <div className="sg-onboarding__group" key={g.key} role="group" aria-label={g.label}>
                <div className="sg-onboarding__label">{g.label}</div>
                <div className="sg-onboarding__chips">
                  {g.options.map((o) => (
                    <ChoiceChip key={o} label={o} size={34} selected={lists[g.key].includes(o)} onToggle={() => toggle(g.key, o)} />
                  ))}
                </div>
              </div>
            ))}
          </>
        )}
        {step === 3 && (
          <>
            <h1 id="sg-onboarding-title">选择主题风格</h1>
            <p>选择喜欢的主题，之后可在设置中修改。</p>
            <div className="sg-onboarding__group" role="radiogroup" aria-label="主题风格">
              <div className="sg-onboarding__label">主题风格</div>
              <div className="sg-onboarding__chips">
                {THEMES.map((t) => (
                  <ChoiceChip key={t.value} role="radio" label={t.label} size={34} selected={style === t.value} onToggle={() => chooseStyle(t.value)} />
                ))}
              </div>
            </div>
          </>
        )}
        {error && (
          <div className="sg-onboarding__error">
            <InlineNotice icon="info" tone="error" text={error} />
          </div>
        )}
        <div className="sg-onboarding__footer">
          {skip}
          <span className="sg-onboarding__spacer" />
          {step === 1 && <Button variant="primary" size={32} label="开始设置" onClick={() => setStep(2)} />}
          {step === 2 && (
            <>
              <Button variant="secondary" size={32} label="上一步" onClick={() => setStep(1)} />
              <Button variant="primary" size={32} label="下一步：选择主题风格" onClick={() => setStep(3)} />
            </>
          )}
          {step === 3 && (
            <>
              <Button variant="secondary" size={32} label="上一步" disabled={!!saving} onClick={() => setStep(2)} />
              <Button variant="primary" size={32} label="完成" busy={saving === 'finish'} disabled={!!saving} onClick={() => submit('finish')} />
            </>
          )}
        </div>
      </section>
    </div>
  );
}
