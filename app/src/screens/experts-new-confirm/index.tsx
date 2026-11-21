import { useState } from 'react';
import { useShell } from '../../app/AppShell';
import { useSubmit } from '../../app/useApi';
import { ApiError } from '../../api/client';
import { Button } from '../../components/Button/Button';
import { TextField } from '../../components/TextField/TextField';
import { ErrorBanner, errorMessage } from '../../components/States/States';
import { expertsApi } from '../../features/experts/api';
import { sortTools, withWizard, type WizardProps } from '../../features/experts/wizard';

const STYLE_LABEL: Record<string, string> = { concise: '简洁', detailed: '详细', conclusion_first: '先结论后依据' };
const POLICY_LABEL: Record<string, string> = { ask_first: '先问再做', act_then_confirm: '先做再确认' };

function Body({ draft, navigate, draftId }: WizardProps) {
  const { toast } = useShell();
  const [prompt, setPrompt] = useState('');
  const [reply, setReply] = useState<string | null>(null);
  const [tryError, setTryError] = useState<string | null>(null);
  const [trying, setTrying] = useState(false);

  const edit = (path: string) => navigate(`${path}?draft=${encodeURIComponent(draftId)}`);

  const publish = useSubmit(async () => {
    const res = await expertsApi.publish(draftId, draft.version);
    toast({ tone: 'undo', message: `已创建专家“${res.expert.name}”` });
    navigate(`/experts/${encodeURIComponent(draftId)}`);
    return res;
  });

  const tryOut = async () => {
    if (!prompt.trim()) return;
    setTrying(true);
    setTryError(null);
    setReply(null);
    try {
      const res = await expertsApi.tryOut(draftId, prompt.trim());
      setReply(res.reply);
    } catch (err) {
      setTryError(
        err instanceof ApiError && err.code === 'SOURCE_UNAVAILABLE'
          ? '“试一下”服务暂时不可用（SOURCE_UNAVAILABLE）：开发环境未配置运行适配器。这不影响创建专家。'
          : errorMessage(err, '试一下失败，请重试'),
      );
    } finally {
      setTrying(false);
    }
  };

  const summaries = [
    {
      title: '身份与职责',
      path: '/experts/new/identity',
      line: [draft.name, draft.title || draft.specialty].filter(Boolean).join(' · ') || '未填写',
      extra: draft.responsibilities,
    },
    {
      title: '灵魂',
      path: '/experts/new/soul',
      line: `${STYLE_LABEL[draft.communication_style] ?? draft.communication_style} · ${POLICY_LABEL[draft.uncertainty_policy] ?? draft.uncertainty_policy}${
        draft.forbidden_rules.length ? ` · 禁区 ${draft.forbidden_rules.length} 条` : ''
      }`,
    },
    {
      title: '专业技能',
      path: '/experts/new/skills',
      line: draft.skills.length ? draft.skills.map((s) => s.name).join('、') : '未选择技能',
    },
    {
      title: '工具与连接',
      path: '/experts/new/tools',
      line: draft.tools.length
        ? sortTools(draft.tools).map((t) => `${t.name}（${t.connection_state === 'connected' ? '已连接' : t.connection_state === 'admin_required' ? '需管理员开通' : '需连接'}）`).join('· ')
        : '不需要外部工具',
    },
    {
      title: '知识',
      path: '/experts/new/knowledge',
      line: draft.knowledge_bases.length ? draft.knowledge_bases.map((k) => k.name).join('、') : '未授权知识库',
    },
  ];

  return (
    <>
      {summaries.map((s) => (
        <div key={s.title} className="sg-wiz__summary">
          <span className="sg-wiz__summary-head">
            {s.title}
            <button type="button" className="sg-dlink" onClick={() => edit(s.path)}>
              修改
            </button>
          </span>
          <p>{s.line}</p>
          {'extra' in s && s.extra ? <p className="sg-dmuted">{s.extra}</p> : null}
        </div>
      ))}
      <div className="sg-wiz__try">
        <span className="sg-wiz__try-head">
          试一下<small>（可选） · 示例回复不计入任务，约消耗 {draft.try_estimated_credits} Credits</small>
        </span>
        <div className="sg-wiz__try-row">
          <TextField size={32} value={prompt} placeholder="例如：帮我看看这份德国站文案有没有合规风险" onValueChange={setPrompt} onKeyDown={(e) => e.key === 'Enter' && void tryOut()} />
          <Button variant="secondary" size={32} label="试一下" busy={trying} disabled={!prompt.trim()} onClick={() => void tryOut()} />
        </div>
        {reply && <p className="sg-wiz__reply">{reply}</p>}
        {tryError && <ErrorBanner message={tryError} onRetry={() => void tryOut()} />}
      </div>
      {publish.error && <ErrorBanner message={errorMessage(publish.error, '创建失败，请重试')} />}
      <div className="sg-wiz__actions">
        <Button variant="secondary" size={36} label="上一步" onClick={() => edit('/experts/new/knowledge')} />
        <Button variant="primary" size={36} label="创建专家" busy={publish.submitting} onClick={() => void publish.submit()} />
        <Button variant="ghost" size={36} label="保存草稿并退出" onClick={() => navigate('/experts')} />
      </div>
    </>
  );
}

export default withWizard(5, Body);
