import { useState } from 'react';
import { useShell } from '../../app/AppShell';
import { useSubmit } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { TextField } from '../../components/TextField/TextField';
import { ErrorBanner, errorMessage } from '../../components/States/States';
import { expertsApi } from '../../features/experts/api';
import { withWizard, type WizardProps } from '../../features/experts/wizard';

function Body({ draft, navigate, draftId }: WizardProps) {
  const { toast } = useShell();
  const [name, setName] = useState(draft.name ?? '');
  const [title, setTitle] = useState(draft.title || draft.specialty || '');
  const [responsibilities, setResponsibilities] = useState(draft.responsibilities);
  const [error, setError] = useState<string | null>(null);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const similar = draft.similar_experts[0];

  const save = useSubmit(async (patch: Record<string, unknown>, next: number | null) => {
    const res = await expertsApi.update(draftId, draft.version, patch);
    navigate(next === null ? '/experts' : `/experts/new/soul?draft=${encodeURIComponent(draftId)}`);
    return res;
  });

  const goNext = () => {
    if (!name.trim()) {
      setError('先给专家起个名字');
      return;
    }
    setError(null);
    void save.submit({ name: name.trim(), title: title.trim(), responsibilities: responsibilities.trim(), wizard_step: 2 }, 1);
  };

  const regenerate = async () => {
    setAvatarBusy(true);
    try {
      await expertsApi.avatar(draftId);
      toast({ tone: 'undo', message: '头像已重新生成' });
    } catch (err) {
      toast({ tone: 'failure', message: errorMessage(err, '头像生成服务暂时不可用（SOURCE_UNAVAILABLE），请稍后再试') });
    } finally {
      setAvatarBusy(false);
    }
  };

  return (
    <>
      <div className="sg-dfield">
        <span className="sg-dfield__label">名称</span>
        <TextField size={42} value={name} placeholder="两个字左右，例如：德语官" onValueChange={(v) => setName(v.slice(0, 16))} />
      </div>
      <div className="sg-dfield">
        <span className="sg-dfield__label">
          <span>岗位</span>
          <span>{draft.start_mode === 'describe' ? 'AI 生成，可修改' : ''}</span>
        </span>
        <TextField size={42} value={title} placeholder="例如：德国市场内容本地化与合规审校" onValueChange={(v) => setTitle(v.slice(0, 40))} />
      </div>
      <div className="sg-dfield">
        <span className="sg-dfield__label">
          <span>职责</span>
          <span>用于说明和推荐，不会自动授予任何权限</span>
        </span>
        <TextField multiline rows={4} value={responsibilities} placeholder="这位专家负责什么、不负责什么" onValueChange={(v) => setResponsibilities(v.slice(0, 500))} />
      </div>
      {similar && (
        <div className="sg-dnote">
          <Icon name="info" size={14} />
          <span>
            发现 {draft.similar_experts.length} 位相似专家：
            {draft.similar_experts.map((e) => (e.specialty ? `${e.name}（${e.specialty}）` : e.name)).join('、')}。你可以
            <button type="button" className="sg-dlink" onClick={() => navigate(`/experts/${encodeURIComponent(similar.id)}`)}>
              直接使用{similar.name}
            </button>
            ，或
            <button type="button" className="sg-dlink" onClick={() => navigate(`/experts/new/start?template=${encodeURIComponent(similar.id)}`)}>
              以它为模板创建
            </button>
            ，也可以继续新建。
          </span>
        </div>
      )}
      <div>
        <Button variant="ghost" size={28} icon="refresh" label="重新生成头像" busy={avatarBusy} onClick={regenerate} />
      </div>
      {error && <ErrorBanner message={error} />}
      {save.error && <ErrorBanner message={errorMessage(save.error, '保存失败，请重试')} />}
      <div className="sg-wiz__actions">
        <Button variant="primary" size={36} label="下一步：灵魂" busy={save.submitting} onClick={goNext} />
        <Button variant="ghost" size={36} label="保存草稿并退出" busy={save.submitting} onClick={() => void save.submit({ name: name.trim(), title: title.trim(), responsibilities: responsibilities.trim() }, null)} />
      </div>
    </>
  );
}

export default withWizard(0, Body);
