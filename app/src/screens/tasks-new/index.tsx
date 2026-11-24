import { useState } from 'react';
import { account, workspaces } from '../../api/endpoints';
import type { ScreenProps } from '../../app/router';
import { useShell } from '../../app/AppShell';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Composer, type ComposerTool } from '../../components/Composer/Composer';
import { ComposerContextBar } from '../../components/Composer/ComposerContextBar';
import { Icon, type IconName } from '../../components/Icon/Icon';
import { NotificationBell } from '../../components/NotificationBell/NotificationBell';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { EmptyState, ErrorBanner, errorMessage } from '../../components/States/States';
import { PickerDialog, type PickedItem, type PickerKind } from '../../features/task/PickerDialog';
import { apiMessage, relativeTime, taskApi } from '../../features/task/model';
import { TaskListRow } from '../../features/tasks/TaskListRow';
import TaskDetailScreen from '../tasks-taskId';
import './new.css';

const STARTERS: Array<{ icon: IconName; text: string }> = [
  { icon: 'globe', text: '评估一个新市场的进入机会' },
  { icon: 'message-square', text: '归纳竞品近 3 个月的差评' },
  { icon: 'bar-chart', text: '生成本周 Amazon 经营周报' },
  { icon: 'megaphone', text: '为新品写一份达人合作 brief' },
];

function greeting(d = new Date()): string {
  const h = d.getHours();
  if (h < 5) return '夜深了';
  if (h < 11) return '上午好';
  if (h < 13) return '中午好';
  if (h < 18) return '下午好';
  return '晚上好';
}

const KINDS: PickerKind[] = ['materials', 'experts', 'skills'];

export default function Screen({ query, hash, navigate }: ScreenProps) {
  const shell = useShell();
  const taskId = query.get('task');
  const forkFrom = query.get('forkFrom');
  const workspaceId = query.get('workspace');
  const pickerParam = query.get('picker') as PickerKind | null;
  const [picker, setPicker] = useState<PickerKind | null>(pickerParam && KINDS.includes(pickerParam) ? pickerParam : null);
  const [text, setText] = useState(query.get('text') ?? '');
  const [picked, setPicked] = useState<Record<PickerKind, PickedItem[]>>({ materials: [], experts: [], skills: [] });

  const profile = useApiQuery((s) => account.getAccountProfile(undefined, s), []);
  const active = useApiQuery((s) => taskApi.active('all', 0, s), []);
  const ws = useApiQuery((s) => (workspaceId ? workspaces.getWorkspacesById(workspaceId, undefined, s) : Promise.resolve(null)), [workspaceId]);

  const create = useSubmit(async (body: string) => {
    const r = await taskApi.create({
      body,
      attached_document_ids: picked.materials.filter((p) => !p.id.startsWith('kb:')).map((p) => p.id),
      attached_knowledge_base_ids: picked.materials.filter((p) => p.id.startsWith('kb:')).map((p) => p.id.slice(3)),
      mentioned_expert_ids: picked.experts.map((p) => p.id),
      requested_skill_ids: picked.skills.map((p) => p.id),
      model_mode: 'auto',
      ...(workspaceId ? { workspace_id: workspaceId } : {}),
      ...(forkFrom ? { forked_from_message_id: forkFrom } : {}),
    });
    shell.refresh();
    setText('');
    setPicked({ materials: [], experts: [], skills: [] });
    if (r.task.complexity === 'simple' || r.task.status !== 'awaiting_kickoff') navigate(`/tasks/${r.task.id}`);
    else navigate(`/tasks/new?task=${r.task.id}#confirm`);
  });

  const name = (profile.data?.preferred_name as string | undefined) || profile.data?.display_name || '';
  const assistant = profile.data?.assistant_name || 'Sage 助手';
  const lead = picked.experts[0]?.title ?? `${assistant}（自动）`;
  const chips = KINDS.flatMap((k) => picked[k].map((p) => ({ kind: k, item: p })));
  const wsName = (ws.data as { name?: string } | null)?.name;

  if (hash === 'confirm') {
    if (!taskId) {
      return (
        <div className="sg-newtask sg-newtask--confirm">
          <EmptyState icon="square-pen" title="还没有待开工的任务" description="先描述要交办的事，发送后会在这里确认任务说明" action={{ label: '去描述', onClick: () => navigate('/tasks/new') }} />
        </div>
      );
    }
    // The kickoff confirmation is the task page itself (04 开工确认卡): header, conversation, kickoff card and side panel.
    return <TaskDetailScreen params={{ taskId }} query={query} hash="confirm" navigate={navigate} />;
  }

  return (
    <div className="sg-newtask">
      <div className="sg-newtask__top">
        <NotificationBell onClick={() => navigate('/notifications')} />
      </div>
      <div className="sg-newtask__center">
        <h1 className="sg-newtask__hello">
          {greeting()}
          {name ? `，${name}` : ''}
        </h1>
        <p className="sg-newtask__sub">今天先推进最重要的一件事。{assistant}已准备好，随时可以开始。</p>
        {forkFrom && <InlineNotice icon="info" tone="info" text="将从所选消息分叉出一个新任务，原任务不受影响" action="取消分叉" onAction={() => navigate('/tasks/new')} />}
        <Composer
          chips={[
            ...(wsName ? [{ label: wsName, tone: 'outline' as const, size: 24 as const, icon: 'folder' as const, removable: true, onRemove: () => navigate('/tasks/new') }] : []),
            ...chips.map(({ kind, item }) => ({
              label: item.title,
              tone: 'outline' as const,
              size: 24 as const,
              ...(item.state ? { meta: item.state, metaTone: item.state === '就绪' ? ('success' as const) : ('muted' as const) } : {}),
              icon: kind === 'materials' ? ('file' as const) : kind === 'experts' ? ('contact' as const) : ('zap' as const),
              removable: true,
              onRemove: () => setPicked((p) => ({ ...p, [kind]: p[kind].filter((x) => x.id !== item.id) })),
            })),
          ]}
          placeholder="描述你要交办的事，Sage 会安排合适的专家"
          tools={['materials', 'experts', 'skills']}
          value={text}
          canSend={!!text.trim()}
          busy={create.submitting}
          onValueChange={setText}
          onSend={(v) => create.submit(v.trim())}
          onTool={(t: ComposerTool) => setPicker(t)}
          width={720}
        />
        <ComposerContextBar lead={lead} model="自动" />
        {create.error && <InlineNotice icon="info" tone="error" text={apiMessage(create.error, '任务没有创建成功，请重试')} />}

        <section className="sg-newtask__section">
          <h2 className="sg-newtask__label">试试这些</h2>
          <div className="sg-newtask__starters">
            {STARTERS.map((s) => (
              <button key={s.text} type="button" className="sg-newtask__starter" onClick={() => setText(s.text)}>
                <Icon name={s.icon} size={14} />
                {s.text}
              </button>
            ))}
          </div>
        </section>

        <section className="sg-newtask__section">
          <div className="sg-newtask__labelrow">
            <h2 className="sg-newtask__label">进行中{active.data ? ` · ${active.data.counts.all}` : ''}</h2>
            <button type="button" className="sg-newtask__all" onClick={() => navigate('/tasks/active')}>
              查看全部
            </button>
          </div>
          {active.status === 'error' && <ErrorBanner message={errorMessage(active.error, '进行中的任务暂时无法读取')} onRetry={active.reload} />}
          {active.data && active.data.items.length === 0 && <p className="sg-newtask__empty">没有进行中的任务</p>}
          <div className="sg-newtask__active">
            {active.data?.items.slice(0, 2).map((t) => {
              const waiting = t.status === 'awaiting_user' || t.status === 'stuck';
              return (
                <TaskListRow
                  key={t.id}
                  title={t.title}
                  subtitle={t.current_step?.activity ?? t.current_step?.title ?? t.workspace?.name ?? ''}
                  state={waiting ? '等你处理' : t.progress.total ? `${t.progress.done}/${t.progress.total}` : '进行中'}
                  statusIcon={waiting ? 'circle-pending' : 'loader-ring'}
                  time={relativeTime(t.last_activity_at)}
                  onOpen={() => navigate(`/tasks/${t.id}`)}
                />
              );
            })}
          </div>
        </section>
      </div>
      {picker && (
        <PickerDialog
          kind={picker}
          selected={picked[picker]}
          onCancel={() => setPicker(null)}
          onConfirm={(items) => {
            setPicked((p) => ({ ...p, [picker]: items }));
            setPicker(null);
          }}
        />
      )}
    </div>
  );
}
