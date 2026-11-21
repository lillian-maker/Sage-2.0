import { useState } from 'react';
import { useSubmit } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { IconButton } from '../../components/IconButton/IconButton';
import { TextField } from '../../components/TextField/TextField';
import { ErrorBanner, errorMessage } from '../../components/States/States';
import { expertsApi, type ExpertDetail } from '../../features/experts/api';
import { withWizard, type WizardProps } from '../../features/experts/wizard';

const STYLES: Array<{ key: ExpertDetail['communication_style']; title: string; desc: string }> = [
  { key: 'concise', title: '简洁', desc: '只给结论和关键依据' },
  { key: 'detailed', title: '详细', desc: '完整展开过程与数据' },
  { key: 'conclusion_first', title: '先结论后依据', desc: '默认' },
];
const POLICIES: Array<{ key: ExpertDetail['uncertainty_policy']; label: string; hint: string }> = [
  { key: 'ask_first', label: '先问再做', hint: '默认' },
  { key: 'act_then_confirm', label: '先做再确认', hint: '适合重复性高的工作' },
];
const MAX_RULES = 10;
const RULE_LEN = 50;

function Body({ draft, navigate, draftId }: WizardProps) {
  const [style, setStyle] = useState(draft.communication_style);
  const [policy, setPolicy] = useState(draft.uncertainty_policy);
  const [rules, setRules] = useState<string[]>(draft.forbidden_rules);
  const [newRule, setNewRule] = useState('');

  const save = useSubmit(async (patch: Record<string, unknown>, dest: string) => {
    const res = await expertsApi.update(draftId, draft.version, patch);
    navigate(dest);
    return res;
  });
  const payload = { communication_style: style, uncertainty_policy: policy, forbidden_rules: rules };

  const addRule = () => {
    const v = newRule.trim().slice(0, RULE_LEN);
    if (!v || rules.length >= MAX_RULES) return;
    setRules((r) => [...r, v]);
    setNewRule('');
  };

  return (
    <>
      <div className="sg-dfield">
        <span className="sg-dfield__label">沟通风格</span>
        <div className="sg-wiz__styles">
          {STYLES.map((s) => (
            <button key={s.key} type="button" className={`sg-doption${style === s.key ? ' is-selected' : ''}`} onClick={() => setStyle(s.key)}>
              <span className="sg-doption__title">{s.title}</span>
              <span className="sg-doption__desc">{s.desc}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="sg-dfield">
        <span className="sg-dfield__label">遇到不确定时</span>
        <div className="sg-wiz__radios">
          {POLICIES.map((p) => (
            <label key={p.key} className="sg-wiz__radio">
              <input type="radio" name="uncertainty" checked={policy === p.key} onChange={() => setPolicy(p.key)} />
              {p.label} <small>{p.hint}</small>
            </label>
          ))}
        </div>
      </div>
      <div className="sg-dfield">
        <span className="sg-dfield__label">
          <span>禁区</span>
          <span>可选，最多 {MAX_RULES} 条，每条 {RULE_LEN} 字以内</span>
        </span>
        <div className="sg-wiz__rules">
          {rules.map((r, i) => (
            <div key={`${i}-${r}`} className="sg-wiz__rule">
              <span>{r}</span>
              <IconButton icon="x" size={25} iconSize={10} variant="ghost" label="删除这条禁区" onClick={() => setRules((list) => list.filter((_, j) => j !== i))} />
            </div>
          ))}
        </div>
        {rules.length < MAX_RULES && (
          <div style={{ display: 'flex', gap: 8 }}>
            <TextField size={32} value={newRule} placeholder="例如：不代发任何对外邮件" onValueChange={setNewRule} onKeyDown={(e) => e.key === 'Enter' && addRule()} />
            <Button variant="secondary" size={32} icon="plus" label="添加禁区" onClick={addRule} disabled={!newRule.trim()} />
          </div>
        )}
      </div>
      {save.error && <ErrorBanner message={errorMessage(save.error, '保存失败，请重试')} />}
      <div className="sg-wiz__actions">
        <Button variant="secondary" size={36} label="上一步" onClick={() => navigate(`/experts/new/identity?draft=${encodeURIComponent(draftId)}`)} />
        <Button variant="primary" size={36} label="下一步：专业技能" busy={save.submitting} onClick={() => void save.submit({ ...payload, wizard_step: 3 }, `/experts/new/skills?draft=${encodeURIComponent(draftId)}`)} />
        <Button variant="ghost" size={36} label="保存草稿并退出" busy={save.submitting} onClick={() => void save.submit(payload, '/experts')} />
      </div>
    </>
  );
}

export default withWizard(1, Body);
