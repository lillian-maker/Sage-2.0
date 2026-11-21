import { useState } from 'react';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { EmptyState, ErrorBanner, errorMessage, QueryGate } from '../../components/States/States';
import { expertsApi } from '../../features/experts/api';
import { knowledgeApi, scopeShortLine } from '../../features/knowledge/api';

import { withWizard, type WizardProps } from '../../features/experts/wizard';

function Body({ draft, navigate, draftId }: WizardProps) {
  const [selected, setSelected] = useState<string[]>(draft.knowledge_bases.map((k) => k.id));
  const bases = useApiQuery((signal) => knowledgeApi.bases({ citable: true }, signal), []);

  const toggle = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  const save = useSubmit(async (patch: Record<string, unknown>, dest: string) => {
    const res = await expertsApi.update(draftId, draft.version, patch);
    navigate(dest);
    return res;
  });

  return (
    <>
      <div className="sg-dfield">
        <span className="sg-dfield__label">
          <span>知识</span>
          <span>可选，可跳过</span>
        </span>
        {!bases.data ? (
          <QueryGate q={bases} label="知识库" rows={3} />
        ) : bases.data.items.length === 0 ? (
          <EmptyState icon="book" title="没有可引用的知识库" description="在知识中心添加资料后，就能在这里授权引用" />
        ) : (
          <div className="sg-dchecklist">
            {bases.data.items.map((kb) => (
              <label key={kb.id} className="sg-dcheck">
                <input type="checkbox" checked={selected.includes(kb.id)} onChange={() => toggle(kb.id)} />
                <span className="sg-dcheck__text">
                  <span className="sg-dcheck__name">{kb.name}</span>
                  <span className="sg-dcheck__sub">
                    {scopeShortLine(kb)} · {kb.document_count} 份
                  </span>
                </span>
                <span className="sg-dmuted">可引用</span>
              </label>
            ))}
          </div>
        )}
      </div>
      <div className="sg-dnote">
        <Icon name="info" size={14} />
        <span>该专家只会在你授权的范围内引用这些资料。只列出你有引用权限的知识库。</span>
      </div>
      {save.error && <ErrorBanner message={errorMessage(save.error, '保存失败，请重试')} />}
      <div className="sg-wiz__actions">
        <Button variant="secondary" size={36} label="上一步" onClick={() => navigate(`/experts/new/tools?draft=${encodeURIComponent(draftId)}`)} />
        <Button variant="primary" size={36} label="下一步：确认创建" busy={save.submitting} onClick={() => void save.submit({ knowledge_base_ids: selected, wizard_step: 6 }, `/experts/new/confirm?draft=${encodeURIComponent(draftId)}`)} />
        <Button variant="ghost" size={36} label="保存草稿并退出" busy={save.submitting} onClick={() => void save.submit({ knowledge_base_ids: selected }, '/experts')} />
      </div>
    </>
  );
}

export default withWizard(4, Body);
