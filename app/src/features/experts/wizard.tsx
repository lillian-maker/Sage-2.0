import type { ReactNode } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { BackLink } from '../../components/BackLink/BackLink';
import { PageHeader } from '../../components/PageHeader/PageHeader';
import { Icon } from '../../components/Icon/Icon';
import { StepMarker } from '../../components/StepMarker/StepMarker';
import { ErrorBanner, errorMessage, QueryGate } from '../../components/States/States';
import { expertsApi, type ExpertDetail, type ExpertTool } from '../../features/experts/api';
import { ExpertPreviewCard } from '../../features/experts/ExpertPreviewCard';
import { DPage } from '../../features/experts/parts';
import '../../features/experts/frame.css';

export const WIZARD_STEPS = [
  { path: '/experts/new/identity', label: '身份与职责' },
  { path: '/experts/new/soul', label: '灵魂' },
  { path: '/experts/new/skills', label: '专业技能' },
  { path: '/experts/new/tools', label: '工具与连接' },
  { path: '/experts/new/knowledge', label: '知识' },
  { path: '/experts/new/confirm', label: '确认创建' },
] as const;

const TOOL_STATE_ORDER: Record<string, number> = { connected: 0, admin_required: 2 };
/** Ready tools first, then the ones the user can connect, then the ones waiting on an admin. */
export const sortTools = (tools: ExpertTool[]) =>
  [...tools].sort((a, b) => (TOOL_STATE_ORDER[a.connection_state ?? ''] ?? 1) - (TOOL_STATE_ORDER[b.connection_state ?? ''] ?? 1));

const START_MODE_LABEL: Record<string, string> = { describe: '根据描述生成', template: '从模板开始', blank: '空白创建' };

export interface WizardProps {
  draft: ExpertDetail;
  reload: () => void;
  navigate: ScreenProps['navigate'];
  draftId: string;
}

export function withWizard(stepIndex: number, Body: (props: WizardProps) => ReactNode) {
  return function WizardScreen({ query, navigate }: ScreenProps) {
    const draftId = query.get('draft');
    const detail = useApiQuery(
      (signal) => (draftId ? expertsApi.detail(draftId, signal) : Promise.reject(new Error('missing draft'))),
      [draftId],
    );
    if (!draftId)
      return (
        <DPage>
          <BackLink label="专家" size={30} onBack={() => navigate('/experts')} />
          <ErrorBanner message="缺少草稿参数：请从“新建专家”开始" onRetry={() => navigate('/experts/new/start')} />
        </DPage>
      );
    const draft = detail.data;
    if (!draft)
      return (
        <DPage>
          <BackLink label="专家" size={30} onBack={() => navigate('/experts')} />
          <div className="sg-dstate">
            <QueryGate q={detail} label="草稿" rows={3} />
          </div>
        </DPage>
      );
    const startChip =
      draft.start_mode === 'describe' && draft.start_description
        ? `起点：根据描述生成「${draft.start_description.slice(0, 24)}${draft.start_description.length > 24 ? '…' : ''}」`
        : draft.start_mode === 'template' && draft.template_from
          ? `起点：从模板「${draft.template_from.name}」开始`
          : draft.start_mode
            ? `起点：${START_MODE_LABEL[draft.start_mode] ?? draft.start_mode}`
            : null;
    return (
      <DPage className="sg-wiz">
        <BackLink label="专家" size={30} onBack={() => navigate('/experts')} />
        <div style={{ marginTop: 12 }}>
          <PageHeader title="新建专家" description="草稿已自动保存 · 创建后可以分享" />
        </div>
        {startChip && (
          <span className="sg-wiz__chip">
            <Icon name="zap" size={12} /> {startChip}
          </span>
        )}
        <div className="sg-wiz__steps" role="tablist" aria-label="创建步骤">
          {WIZARD_STEPS.map((s, i) => {
            const state = i < stepIndex ? 'done' : i === stepIndex ? 'current' : 'static';
            return (
              <button
                key={s.path}
                type="button"
                className={`sg-wiz__step${i === stepIndex ? ' is-current' : ''}${i < stepIndex ? ' is-done' : ''}`}
                disabled={i > stepIndex}
                onClick={() => i < stepIndex && navigate(`${s.path}?draft=${encodeURIComponent(draftId)}`)}
              >
                <StepMarker index={i + 1} state={state} shape="circle" size={20} />
                {s.label}
              </button>
            );
          })}
        </div>
        {detail.status === 'error' && <ErrorBanner message={errorMessage(detail.error, '草稿刷新失败')} onRetry={detail.reload} />}
        <div className="sg-wiz__main">
          <div className="sg-wiz__form">
            <Body draft={draft} reload={detail.reload} navigate={navigate} draftId={draftId} />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <ExpertPreviewCard
              expert={{ name: draft.name ?? '', specialty: draft.title || draft.specialty || '', avatarRef: draft.avatar_ref, tags: draft.capability_tags }}
            />
            {stepIndex === 0 && <span className="sg-wiz__preview-note">头像按当前主题风格自动生成，可重新生成或上传替换。</span>}
          </div>
        </div>
      </DPage>
    );
  };
}

