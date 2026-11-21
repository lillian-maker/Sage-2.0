import { Avatar } from '../../components/Avatar/Avatar';
import { Tag } from '../../components/Tag/Tag';
import { avatarOf, type TaskMessage } from './model';
import './parallel.css';

type Metrics = NonNullable<TaskMessage['run_metrics']>;

/** “6 分 12 秒” — the parallel card counts elapsed work to the second. */
export function clockDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '—';
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m ? `${m} 分 ${String(s).padStart(2, '0')} 秒` : `${s} 秒`;
}

export interface ParallelWorkCardProps {
  metrics: Metrics;
  onOpenStep?: (stepId: string) => void;
}

export function ParallelWorkCard({ metrics, onOpenStep }: ParallelWorkCardProps) {
  const rows = metrics.parallel?.rows ?? [];
  const working = rows.filter((r) => r.activity);
  const background = working.filter((r) => r.in_background).length;
  const counts = [
    metrics.tool_calls ? `调用了 ${metrics.tool_calls} 个工具` : '',
    metrics.documents_read ? `阅读了 ${metrics.documents_read} 份资料` : '',
    metrics.artifacts_created ? `生成了 ${metrics.artifacts_created} 个成果` : '',
  ].filter(Boolean);
  return (
    <section className="sg-parallel" aria-label="并行工作">
      {counts.length > 0 && <p className="sg-parallel__counts">{counts.join(' · ')}</p>}
      <div className="sg-parallel__head">
        <span>{`正在等待 ${working.length} 位专家${background ? ` · ${background} 个在后台` : ''}`}</span>
        {onOpenStep && <span className="sg-parallel__hint">点击一行查看编排节点</span>}
      </div>
      <div className="sg-parallel__table" role="table">
        <div className="sg-parallel__row is-head" role="row">
          <span role="columnheader">专家</span>
          <span role="columnheader">步骤</span>
          <span role="columnheader" />
        </div>
        {rows.map((r) => (
          <button key={r.step_id} type="button" role="row" className="sg-parallel__row" disabled={!onOpenStep} onClick={() => onOpenStep?.(r.step_id)}>
            <span className="sg-parallel__expert" role="cell">
              <Avatar {...avatarOf(r.expert.name, r.expert.avatar_ref)} size={20} />
              {r.expert.name}
            </span>
            <span className="sg-parallel__step" role="cell">
              <span className="truncate">{`${r.title} · ${r.activity ?? '等待上游完成'}`}</span>
              {r.in_background && <Tag label="后台" tone="neutral" size={18} />}
            </span>
            <span className="sg-parallel__time" role="cell">{r.activity ? clockDuration(r.elapsed_seconds) : '—'}</span>
          </button>
        ))}
      </div>
      {metrics.elapsed_seconds ? <p className="sg-parallel__foot">{`已工作 ${clockDuration(metrics.elapsed_seconds)}`}</p> : null}
    </section>
  );
}
