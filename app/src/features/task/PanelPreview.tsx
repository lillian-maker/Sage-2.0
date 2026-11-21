import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { ErrorBanner, LoadingState, errorMessage } from '../../components/States/States';
import { artifactApi } from '../artifacts/model';
import { BlocksView } from '../artifacts/parts';
import { PlanStepRow } from './PlanStepRow';
import { avatarOf, stepStatus, type TaskStep } from './model';

/** Right-panel preview of an artifact's current version, read-only. */
export function ArtifactPanelPreview({ artifactId, onOpen }: { artifactId: string; onOpen: () => void }) {
  const detail = useApiQuery((s) => artifactApi.get(artifactId, s), [artifactId]);
  const n = detail.data?.current_version?.number;
  const content = useApiQuery((s) => (n ? artifactApi.content(artifactId, n, s) : Promise.resolve(null)), [artifactId, n]);
  if (detail.status === 'error' && !detail.data) return <ErrorBanner message={errorMessage(detail.error, '成果暂时无法读取')} onRetry={detail.reload} />;
  if (!detail.data) return <LoadingState rows={3} />;
  const blocks = content.data?.body.blocks ?? [];
  return (
    <div className="sg-taskside__preview">
      <div className="sg-taskside__preview-head">
        <span className="truncate">{detail.data.title}</span>
        {n ? <small>v{n}</small> : null}
      </div>
      {content.status === 'error' && <ErrorBanner message={errorMessage(content.error, '正文暂时无法读取')} onRetry={content.reload} />}
      {n && !content.data && content.status !== 'error' && <LoadingState rows={3} />}
      {content.data && (blocks.length ? <BlocksView blocks={blocks} /> : <p className="sg-taskside__muted">这个版本没有可预览的正文，请打开成果查看</p>)}
      <div style={{ marginTop: 12 }}>
        <Button variant="secondary" size={28} label="打开成果" onClick={onOpen} />
      </div>
    </div>
  );
}

/** Compact orchestration list for the right panel; the full graph stays on the orchestration page. */
export function OrchestrationPanel({ steps, onOpen }: { steps: TaskStep[] | undefined; onOpen: () => void }) {
  if (!steps) return <LoadingState rows={3} />;
  return (
    <div className="sg-taskside__steps">
      {steps.length === 0 && <p className="sg-taskside__muted">计划还没有生成步骤</p>}
      {steps.map((s) => (
        <PlanStepRow
          key={s.id}
          layout="rail"
          status={stepStatus(s.status)}
          title={s.title}
          meta={[s.expert?.name, s.depends_on.length ? `依赖 ${s.depends_on.length} 步` : null].filter(Boolean).join(' · ')}
          {...(s.expert ? { expert: avatarOf(s.expert.name, s.expert.avatar_ref) } : {})}
        />
      ))}
      <div style={{ marginTop: 12 }}>
        <Button variant="secondary" size={28} label="打开编排视图" onClick={onOpen} />
      </div>
    </div>
  );
}
