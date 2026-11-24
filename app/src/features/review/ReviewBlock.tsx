import { useEffect, useState } from 'react';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { ErrorBanner, LoadingState, errorMessage } from '../../components/States/States';
import { apiMessage, taskApi } from '../task/model';
import { ContentReviewCard, type ReviewIssue } from './ContentReviewCard';

const DIMENSION: Record<string, string> = {
  platform_policy: '平台政策',
  market_law: '目标市场法规',
  brand: '品牌规范',
  facts: '事实与数据',
};
const SEVERITY: Record<string, ReviewIssue['severity']> = { high: '高', medium: '中', low: '低', 高: '高', 中: '中', 低: '低' };
const COUNT = ['', '一', '两', '三', '四', '五', '六', '七', '八', '九', '十'];

export interface ReviewBlockProps {
  id: string;
  onOpenArtifact?: (artifactId: string) => void;
  /** After a single suggestion is applied (23 → 56 成果变更与版本差异). */
  onOpenVersions?: (artifactId: string) => void;
}

export function ReviewBlock({ id, onOpenArtifact, onOpenVersions }: ReviewBlockProps) {
  const review = useApiQuery((signal) => taskApi.review(id, signal), [id]);
  const [copied, setCopied] = useState(false);
  // 核对 mode: the rewritten version is open next to the findings it came from.
  const [checking, setChecking] = useState(false);
  const act = useSubmit(async (fn: () => Promise<unknown>) => {
    await fn();
    review.reload();
  });
  // 2294:1361: show “正在应用全部改写” from the moment the request leaves until fresh review data arrives,
  // because the backend may finish the rewrite inside the same request and never report state 'rewriting'.
  const [staleAfterApply, setStaleAfterApply] = useState<unknown>(null);
  const applyAll = useSubmit(async (reviewId: string) => {
    const before = review.data;
    await taskApi.applyAll(reviewId);
    setStaleAfterApply(before ?? null);
    review.reload();
  });
  const r = review.data;
  const applyingAll = applyAll.submitting || (staleAfterApply !== null && r === staleAfterApply);
  const rewriting = r?.state === 'rewriting' || applyingAll;
  useEffect(() => {
    if (r?.state !== 'rewriting') return;
    const t = window.setInterval(review.reload, 2000);
    return () => window.clearInterval(t);
  }, [r?.state, review.reload]);

  if (!r) {
    return review.status === 'error' ? (
      <ErrorBanner message={errorMessage(review.error, '审核结果暂时无法读取')} onRetry={review.reload} />
    ) : (
      <LoadingState rows={3} />
    );
  }
  const subtitle = [r.targets?.map((t) => t.title).filter(Boolean).join(' · '), r.target_platform, r.target_market].filter(Boolean).join(' · ');
  const result = r.result_version;
  const rewritten = r.state === 'rewritten';
  const names = r.reviewers.map((e) => e.name);
  const lead = rewriting
    ? '正在应用全部改写，完成后将生成新版本。'
    : checking && result
      ? `核对 v${result.number} 的文字改写建议；${r.issues.some((i) => i.suggested_artifact_version) ? '主图修改版请单独核对。' : ''}`
      : rewritten
        ? '已生成改写版本，请核对修改内容后再上架。'
        : names.length
          ? `已由${names.join('、')}${COUNT[names.length] ?? names.length}位专家并行审核，${r.verifier_expert ? `${r.verifier_expert.name}复核。` : ''}结论：${r.conclusion ?? ''}`
          : r.conclusion;
  const primaryAction = rewriting
    ? { label: '正在应用…', disabled: true }
    : checking
      ? { label: '返回审核', onClick: () => setChecking(false) }
      : rewritten
        ? { label: '查看改写版本', onClick: () => setChecking(true) }
        : r.can_edit && r.state === 'reviewed'
          ? { label: '应用全部改写', onClick: () => void applyAll.submit(r.id) }
          : { label: '应用全部改写', disabled: true };
  return (
    <div className="sg-reviewblock">
      {lead && <p className="sg-reviewblock__lead">{lead}</p>}
      <ContentReviewCard
        {...(checking && result ? { title: `改写版本 v${result.number} · 核对` } : {})}
        subtitle={subtitle}
        issueCount={r.counts.issues}
        dimensions={r.dimensions.map((d) => ({ name: DIMENSION[d] ?? d, issues: r.counts.by_dimension[d] ?? 0 }))}
        primaryAction={primaryAction}
        issues={r.issues.map((i) => ({
          id: i.id,
          severity: SEVERITY[i.severity] ?? '中',
          title: i.title ?? '',
          original: [i.location, i.original_text].filter(Boolean).join('：'),
          suggestion: i.suggested_text,
          basis: i.basis,
          action: i.action,
          ...(i.suggested_artifact_version
            ? { candidate: { label: i.suggested_artifact_version.label || '修改版', onOpen: () => onOpenArtifact?.(i.suggested_artifact_version!.artifact_id) } }
            : {}),
        }))}
        onApply={(iid) =>
          act.submit(async () => {
            await taskApi.issueAction(r.id, iid, 'applied');
            const target = r.targets?.find((x) => x.kind !== 'image') ?? r.targets?.[0];
            if (target && onOpenVersions) onOpenVersions(target.artifact_id);
          })
        }
        onIgnore={(iid) => act.submit(() => taskApi.issueAction(r.id, iid, 'ignored'))}
        onCopy={(iid) => {
          const text = r.issues.find((i) => i.id === iid)?.suggested_text ?? '';
          void navigator.clipboard?.writeText(text).then(() => setCopied(true));
        }}
      />
      {rewritten && result && (
        <button type="button" className="sg-reviewblock__result" onClick={() => setChecking(true)}>
          <span>{`${result.title} · v${result.number}`}</span>
          <span className="sg-reviewblock__state">改写已生成 · 待核对</span>
        </button>
      )}
      {copied && <InlineNotice icon="check" tone="muted" text="已复制改写建议" />}
      {(act.error ?? applyAll.error) && (
        <InlineNotice icon="info" tone="error" text={apiMessage(act.error ?? applyAll.error, '操作没有成功，请重试')} />
      )}
    </div>
  );
}
