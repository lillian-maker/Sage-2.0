import type { ReactNode } from 'react';
import { Avatar } from '../../components/Avatar/Avatar';
import { IconButton } from '../../components/IconButton/IconButton';
import { Select } from '../../components/Select/Select';
import { Tab } from '../../components/Tabs/Tab';
import { EmptyState } from '../../components/States/States';
import { ArtifactRow } from './ArtifactRow';
import { PlanStepRow } from './PlanStepRow';
import { avatarOf, durationText, relativeTime, stepStatus, type ArtifactItem, type Participant, type TaskMessage, type TaskStep } from './model';
import './detail.css';

export type SideTab = 'progress' | 'artifacts' | 'materials';

const ROLE: Record<string, string> = { lead: '主责', assist: '协作', owner: '负责人', member: '参与者', viewer: '只读' };

function stepMeta(s: TaskStep): string {
  if (s.status === 'awaiting_user') return s.activity ?? '等你确认';
  if (s.status === 'running') return [s.expert?.name, s.activity].filter(Boolean).join(' · ');
  if (s.status === 'done') return [s.expert?.name, durationText(s.duration_seconds)].filter(Boolean).join(' · ');
  if (s.status === 'failed' || s.status === 'blocked') return s.activity ?? '遇到问题';
  return s.expert?.name ?? '';
}

function roleOf(p: Participant): string {
  if (p.kind === 'expert') return p.role === 'lead' ? '主责专家' : '协作专家';
  return p.role === 'lead' ? '负责人' : ROLE[p.role] ?? '参与者';
}

export interface TaskSidePanelProps {
  tab: SideTab;
  onTab: (tab: SideTab) => void;
  steps: TaskStep[] | undefined;
  artifacts: ArtifactItem[] | undefined;
  materials: TaskMessage['attachments'];
  participants: Participant[];
  counts: { artifacts: number; materials: number };
  onOpenArtifact: (id: string) => void;
  /** Shown in place of the step list before progress exists (“确认后显示任务进度” / “等待进度更新”). */
  pendingNote?: string;
  /** One-line task state shown above the steps, e.g. “已交付，等待验收”. */
  statusNote?: string;
  /** Panels opened from “打开面板” (artifact preview, orchestration) after the three fixed tabs. */
  extraTabs?: Array<{ key: string; label: string }>;
  /** Key of the selected extra tab; its content replaces the fixed tab body. */
  activeExtra?: string | null;
  extraContent?: ReactNode;
  onExtra?: (key: string) => void;
  onCloseExtra?: (key: string) => void;
  onOpenMenu?: () => void;
}

export function TaskSidePanel({
  tab, onTab, steps, artifacts, materials, participants, counts, onOpenArtifact, pendingNote, statusNote,
  extraTabs = [], activeExtra = null, extraContent, onExtra, onCloseExtra, onOpenMenu,
}: TaskSidePanelProps) {
  const fixed = activeExtra === null;
  const artifactList = (artifacts ?? []).map((a) => (
    <ArtifactRow
      key={a.id}
      kind={a.kind === 'document' ? 'report' : 'file'}
      title={a.title}
      meta={a.current_version ? `${a.current_version.state === 'draft' ? '草稿 ' : ''}v${a.current_version.number}${a.updated_at ? ' · ' + relativeTime(a.updated_at) : ''}` : ''}
      onOpen={() => onOpenArtifact(a.id)}
    />
  ));
  return (
    <aside className="sg-taskside">
      {/* Narrow windows (Figma 709/712): the drawer swaps the tab bar for a dropdown. */}
      <div className="sg-taskside__tabselect">
        <Select
          size={32}
          label="切换面板"
          value={fixed ? tab : `x:${activeExtra}`}
          options={[
            { value: 'progress', label: '进度' },
            { value: 'artifacts', label: `成果 ${counts.artifacts}` },
            { value: 'materials', label: `资料 ${counts.materials}` },
            ...extraTabs.map((x) => ({ value: `x:${x.key}`, label: x.label })),
          ]}
          onChange={(v) => (v.startsWith('x:') ? onExtra?.(v.slice(2)) : onTab(v as SideTab))}
        />
      </div>
      <div className="sg-taskside__tabs" role="tablist">
        <Tab size={37} label="进度" selected={fixed && tab === 'progress'} onSelect={() => onTab('progress')} />
        <Tab size={37} label={`成果 ${counts.artifacts}`} selected={fixed && tab === 'artifacts'} onSelect={() => onTab('artifacts')} />
        <Tab size={37} label={`资料 ${counts.materials}`} selected={fixed && tab === 'materials'} onSelect={() => onTab('materials')} />
        {extraTabs.map((x) => (
          <span key={x.key} className="sg-taskside__extratab">
            <Tab size={37} label={x.label} selected={activeExtra === x.key} onSelect={() => onExtra?.(x.key)} />
            {onCloseExtra && activeExtra === x.key && (
              <IconButton icon="x" size={25} iconSize={10} variant="ghost" label={`关闭 ${x.label}`} onClick={() => onCloseExtra(x.key)} />
            )}
          </span>
        ))}
        {onOpenMenu && <IconButton icon="plus" size={28} iconSize={14} variant="ghost" label="打开面板" className="sg-taskside__add" onClick={onOpenMenu} />}
      </div>
      <div className="sg-taskside__body">
        {!fixed && extraContent}
        {fixed && tab === 'progress' && (
          <>
            <div className="sg-taskside__steps">
              {statusNote && <p className="sg-taskside__status">{statusNote}</p>}
              {pendingNote ? (
                <p className="sg-taskside__muted">{pendingNote}</p>
              ) : (
                steps && steps.length === 0 && <p className="sg-taskside__muted">计划还没有生成步骤</p>
              )}
              {!pendingNote && steps?.map((s) => (
                <PlanStepRow key={s.id} layout="rail" status={stepStatus(s.status)} title={s.title} meta={stepMeta(s)} />
              ))}
            </div>
            {artifacts && artifacts.length > 0 && (
              <section className="sg-taskside__section">
                <h3 className="sg-section-label">成果</h3>
                {artifactList}
              </section>
            )}
            <section className="sg-taskside__section">
              <h3 className="sg-section-label">参与者 · {participants.length}</h3>
              {participants.map((p) => {
                const name = p.expert?.name ?? p.user?.display_name ?? '';
                return (
                  <div key={p.id} className="sg-taskside__person">
                    <Avatar {...avatarOf(name, p.expert?.avatar_ref)} size={20} />
                    <span className="truncate">{name}</span>
                    <span className="sg-taskside__role">{roleOf(p)}</span>
                  </div>
                );
              })}
            </section>
          </>
        )}
        {fixed && tab === 'artifacts' && (artifactList.length ? artifactList : <EmptyState icon="file" title="还没有成果" description="专家交付的文件会出现在这里" />)}
        {fixed && tab === 'materials' &&
          (materials.length ? (
            materials.map((m) => <ArtifactRow key={m.id} kind="file" title={m.title} meta="" />)
          ) : (
            <EmptyState icon="paperclip" title="没有附加资料" description="在输入框点“资料”可以把知识中心的文件加进任务" />
          ))}
      </div>
    </aside>
  );
}
