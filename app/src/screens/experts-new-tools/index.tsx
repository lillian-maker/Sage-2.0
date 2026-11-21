import { useSubmit } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { ErrorBanner, errorMessage } from '../../components/States/States';
import { expertsApi } from '../../features/experts/api';
import { sortTools, withWizard, type WizardProps } from '../../features/experts/wizard';

function Body({ draft, navigate, draftId }: WizardProps) {
  const save = useSubmit(async (patch: Record<string, unknown>, dest: string) => {
    const res = await expertsApi.update(draftId, draft.version, patch);
    navigate(dest);
    return res;
  });

  return (
    <>
      <div className="sg-dfield">
        <span className="sg-dfield__label">所选技能需要的工具与连接</span>
        {draft.tools.length === 0 && <p className="sg-dmuted">所选技能不需要连接外部工具。</p>}
        <div>
          {sortTools(draft.tools).map((t) => (
            <div key={t.extension_id} className="sg-drow">
              <Icon name="link" size={15} />
              <span className="sg-drow__text">
                <span className="sg-drow__name">{t.name}</span>
                <span className="sg-drow__sub">{[t.required_for ? `${t.required_for.name}需要` : null, '只读'].filter(Boolean).join(' · ')}</span>
              </span>
              {t.connection_state === 'connected' ? (
                <span className="sg-drow__right sg-dok">已连接</span>
              ) : t.connection_state === 'admin_required' ? (
                <span className="sg-drow__right sg-dwarn">需管理员开通</span>
              ) : (
                <span className="sg-drow__right sg-dwarn">
                  需连接 ·{' '}
                  <button type="button" className="sg-dlink" onClick={() => navigate(`/extensions/${encodeURIComponent(t.extension_id)}`)}>
                    去连接
                  </button>
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
      <div className="sg-dnote">
        <Icon name="info" size={14} />
        <span>
          这里只是声明这位专家可以使用哪些工具，不会授予执行权限。专家在任务中第一次使用时，仍会通过确认卡请你授权。未连接的工具可以稍后再连接，不影响创建。
        </span>
      </div>
      {save.error && <ErrorBanner message={errorMessage(save.error, '保存失败，请重试')} />}
      <div className="sg-wiz__actions">
        <Button variant="secondary" size={36} label="上一步" onClick={() => navigate(`/experts/new/skills?draft=${encodeURIComponent(draftId)}`)} />
        <Button variant="primary" size={36} label="下一步：知识" busy={save.submitting} onClick={() => void save.submit({ wizard_step: 5 }, `/experts/new/knowledge?draft=${encodeURIComponent(draftId)}`)} />
        <Button variant="ghost" size={36} label="保存草稿并退出" busy={save.submitting} onClick={() => void save.submit({}, '/experts')} />
      </div>
    </>
  );
}

export default withWizard(3, Body);
