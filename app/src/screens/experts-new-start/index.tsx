import { useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useSubmit } from '../../app/useApi';
import { ApiError } from '../../api/client';
import { Button } from '../../components/Button/Button';
import { IconButton } from '../../components/IconButton/IconButton';
import { Icon } from '../../components/Icon/Icon';
import { TextField } from '../../components/TextField/TextField';
import { errorMessage } from '../../components/States/States';
import { expertsApi } from '../../features/experts/api';
import '../../features/experts/frame.css';
import { Modal } from '../../components/Modal/Modal';

type Mode = 'describe' | 'template' | 'blank';

const DESCRIBE_MAX = 500;

export default function Screen({ query, navigate }: ScreenProps) {
  const presetTemplate = query.get('template');
  const [mode, setMode] = useState<Mode>(presetTemplate ? 'template' : 'describe');
  const [description, setDescription] = useState('');
  const [error, setError] = useState<string | null>(null);

  const create = useSubmit(async () => {
    const body =
      mode === 'describe'
        ? { start_mode: 'describe' as const, start_description: description.trim() }
        : mode === 'template'
          ? { start_mode: 'template' as const, ...(presetTemplate ? { template_from: presetTemplate } : {}) }
          : { start_mode: 'blank' as const };
    return expertsApi.create(body);
  });

  const submit = async () => {
    setError(null);
    if (mode === 'describe' && !description.trim()) {
      setError('先用一句话描述这位专家要做什么');
      return;
    }
    if (mode === 'template' && !presetTemplate) {
      setError('请从专家卡片菜单选择“以此为模板新建”');
      return;
    }
    const res = await create.submit();
    if (res) navigate(`/experts/new/identity?draft=${encodeURIComponent(res.expert.id)}`);
  };

  const createError = create.error;
  const unavailable = createError instanceof ApiError && createError.code === 'SOURCE_UNAVAILABLE';
  const cta = mode === 'describe' ? '生成草稿' : mode === 'template' ? '使用模板' : '创建草稿';

  return (
    <>
      <div className="sg-dpage" aria-hidden="true">
        <div className="sg-dpage__body">
          <div className="sg-egrid" style={{ marginTop: 56 }}>
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="sg-ecard sg-ecard--skeleton">
                <span className="sg-skel sg-skel--avatar" />
                <span className="sg-skel sg-skel--line" style={{ width: '55%' }} />
                <span className="sg-skel sg-skel--line" style={{ width: '90%' }} />
                <span className="sg-skel sg-skel--line" style={{ width: '75%' }} />
              </div>
            ))}
          </div>
        </div>
      </div>
      <Modal className="sg-dmodal__card" label="新建专家" onClose={() => navigate('/experts')}>
          <div className="sg-dmodal__head">
            <h2>新建专家</h2>
            <IconButton icon="x" size={28} variant="ghost" label="关闭" onClick={() => navigate('/experts')} />
          </div>
          <p className="sg-dmodal__sub">选择一种开始方式，之后的每一步都可以修改。</p>
          <button type="button" className={`sg-doption${mode === 'describe' ? ' is-selected' : ''}`} onClick={() => setMode('describe')}>
            <span className="sg-doption__title">
              <span className="sg-doption__radio" /> 用一句话描述
            </span>
            <span className="sg-doption__desc">Sage 会根据描述生成名称、岗位、职责和建议技能</span>
            {mode === 'describe' && (
              <span className="sg-doption__body" onClick={(e) => e.stopPropagation()}>
                <TextField
                  multiline
                  rows={3}
                  value={description}
                  placeholder="例如：一位懂德语的内容专家，负责德国市场的内容本地化与合规审校"
                  onValueChange={(v) => setDescription(v.slice(0, DESCRIBE_MAX))}
                />
                <span className="sg-dmuted" style={{ display: 'block', textAlign: 'right', marginTop: 4 }}>
                  {description.length} / {DESCRIBE_MAX}
                </span>
              </span>
            )}
          </button>
          <button type="button" className={`sg-doption${mode === 'template' ? ' is-selected' : ''}`} onClick={() => setMode('template')}>
            <span className="sg-doption__title">
              <span className="sg-doption__radio" /> 从模板开始
            </span>
            <span className="sg-doption__desc">
              以一位已有专家为基础，复制其职责与技能后修改{presetTemplate ? '（已选好模板专家）' : ''}
            </span>
          </button>
          <button type="button" className={`sg-doption${mode === 'blank' ? ' is-selected' : ''}`} onClick={() => setMode('blank')}>
            <span className="sg-doption__title">
              <span className="sg-doption__radio" /> 空白创建
            </span>
            <span className="sg-doption__desc">从头逐步填写</span>
          </button>
          {(error || createError) && (
            <span className="sg-dfield__error">
              {error ??
                (unavailable ? '草稿生成服务暂时不可用（SOURCE_UNAVAILABLE），请稍后再试，或改用“空白创建 / 从模板开始”。' : errorMessage(createError, '创建失败，请重试'))}
            </span>
          )}
          <div className="sg-dmodal__foot">
            <Button variant="ghost" size={32} label="取消" onClick={() => navigate('/experts')} />
            <Button variant="primary" size={32} icon="zap" label={cta} busy={create.submitting} onClick={submit} />
          </div>
          {unavailable && (
            <p className="sg-dmuted" style={{ margin: 0 }}>
              <Icon name="info" size={12} /> 开发环境未配置 WORKBENCH_EXPERT_DRAFT_GENERATOR 适配器，按接口约定返回 503 SOURCE_UNAVAILABLE。
            </p>
          )}
      </Modal>
    </>
  );
}
