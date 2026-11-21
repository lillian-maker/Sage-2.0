import { useMemo, useState } from 'react';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { SearchField } from '../../components/SearchField/SearchField';
import { EmptyState, ErrorBanner, errorMessage, QueryGate } from '../../components/States/States';
import { expertsApi } from '../../features/experts/api';
import type { ExpertSkill } from '../../features/experts/api';
import { extensionsApi, type ExtensionItem, type ExtensionList, type InstallState } from '../../features/extensions/api';

import { withWizard, type WizardProps } from '../../features/experts/wizard';

const TIER: Record<string, string> = { official: '官方', community_reviewed: '社区 · 已审核', third_party: '第三方', enterprise: '企业', local_import: '本地导入' };
const MAX_SKILLS = 20;

type SkillRow = Pick<ExtensionItem, 'id' | 'name' | 'publisher_tier' | 'installed_version' | 'latest_version' |
  'install_state' | 'enabled' | 'required_connector'>;

// An account-level local import the draft references but the account has not installed is in neither list.
const fromDraft = (s: ExpertSkill): SkillRow => ({
  id: s.extension_id,
  name: s.name,
  publisher_tier: s.publisher_tier,
  installed_version: s.installed_version,
  latest_version: s.latest_version,
  install_state: s.install_state as InstallState,
  enabled: s.install_state === 'installed',
  required_connector: s.required_connector,
});

function Body({ draft, navigate, draftId }: WizardProps) {
  const [selected, setSelected] = useState<string[]>(draft.skills.map((s) => s.extension_id));
  const [q, setQ] = useState('');
  const installed = useApiQuery((signal) => extensionsApi.list({ tab: 'installed', kind: 'skill' }, signal), []);
  const market = useApiQuery((signal) => extensionsApi.list({ tab: 'market', kind: 'skill' }, signal), []);

  const rows = useMemo((): SkillRow[] => {
    const inst = (installed.data as ExtensionList | undefined)?.items ?? [];
    const marketItems = (market.data as ExtensionList | undefined)?.items ?? [];
    const notInstalled = marketItems.filter((m) => m.install_state === 'not_installed');
    // Skills already chosen for the draft come first, even when the account has not installed them yet.
    const byId = new Map<string, SkillRow>([...marketItems, ...inst].map((s) => [s.id, s]));
    const chosen = draft.skills.map((s) => byId.get(s.extension_id) ?? fromDraft(s));
    const chosenIds = new Set(chosen.map((s) => s.id));
    const all = [...chosen, ...[...inst, ...notInstalled].filter((s) => !chosenIds.has(s.id))];
    const f = q.trim();
    return f ? all.filter((s) => s.name.includes(f)) : all;
  }, [installed.data, market.data, q, draft.skills]);

  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : s.length >= MAX_SKILLS ? s : [...s, id]));

  const save = useSubmit(async (patch: Record<string, unknown>, dest: string) => {
    const res = await expertsApi.update(draftId, draft.version, patch);
    navigate(dest);
    return res;
  });

  const loading = !installed.data || !market.data;

  return (
    <>
      <div className="sg-dfield">
        <span className="sg-dfield__label">
          <span>专业技能</span>
          <span>已选 {selected.length} / {MAX_SKILLS}</span>
        </span>
        <SearchField size={32} width={320} placeholder="搜索已安装的技能" value={q} clearable onValueChange={setQ} />
      </div>
      {loading ? (
        <QueryGate q={installed} label="技能" rows={3} />
      ) : rows.length === 0 ? (
        <EmptyState icon="puzzle" title={q ? `没有找到与“${q}”相关的技能` : '还没有可用技能'} description="到扩展市场安装技能后，就能在这里勾选" />
      ) : (
        <div className="sg-dchecklist">
          {rows.map((s) => {
            const canUse = s.install_state === 'installed' && s.enabled;
            const checked = selected.includes(s.id);
            const sub =
              s.install_state === 'not_installed' && !checked
                ? '未安装'
                : [
                    TIER[s.publisher_tier] ?? s.publisher_tier,
                    s.installed_version || s.latest_version,
                    s.install_state === 'disabled' ? '已停用' : null,
                    s.required_connector ? `需连接 ${s.required_connector.name}` : null,
                  ]
                    .filter(Boolean)
                    .join(' · ');
            return (
              <label key={s.id} className={`sg-dcheck${canUse ? '' : ' is-disabled'}`}>
                <input type="checkbox" checked={checked} disabled={!canUse && !checked} onChange={() => toggle(s.id)} />
                <span className="sg-dcheck__text">
                  <span className="sg-dcheck__name">{s.name}</span>
                  <span className="sg-dcheck__sub">{sub}</span>
                </span>
                {!canUse && (
                  <button type="button" className="sg-dlink" onClick={() => navigate('/extensions')}>
                    {s.install_state === 'not_installed' ? '去扩展安装' : '去扩展启用'}
                  </button>
                )}
              </label>
            );
          })}
        </div>
      )}
      {save.error && <ErrorBanner message={errorMessage(save.error, '保存失败，请重试')} />}
      <div className="sg-wiz__actions">
        <Button variant="secondary" size={36} label="上一步" onClick={() => navigate(`/experts/new/soul?draft=${encodeURIComponent(draftId)}`)} />
        <Button variant="primary" size={36} label="下一步：工具与连接" busy={save.submitting} onClick={() => void save.submit({ skill_ids: selected, wizard_step: 4 }, `/experts/new/tools?draft=${encodeURIComponent(draftId)}`)} />
        <Button variant="ghost" size={36} label="保存草稿并退出" busy={save.submitting} onClick={() => void save.submit({ skill_ids: selected }, '/experts')} />
      </div>
    </>
  );
}

export default withWizard(2, Body);
