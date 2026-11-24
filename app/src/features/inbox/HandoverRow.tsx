import { useSubmit } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { Tag } from '../../components/Tag/Tag';
import { errorMessage } from '../../components/States/States';
import { inboxApi, type HandoverItem } from './api';

const KIND_LABEL: Record<string, string> = {
  task: '任务',
  approval: '审批',
  automation: '自动化',
  workspace_ownership: '工作区',
  custom_expert: '自建专家',
};

// 762:2: non-task rows carry a kind column; the approval row reads “待处理的审批”.
const ROW_KIND: Record<string, string> = { ...KIND_LABEL, approval: '待处理的审批' };

const STATUS_ZH: Record<string, string> = {
  running: '进行中',
  paused: '已暂停',
  queued: '排队中',
  awaiting_user: '待补充',
  accepted: '已完成',
  failed: '已失败',
  cancelled: '已取消',
  enabled: '已启用',
};

export function HandoverRow({
  item,
  onChanged,
  onNavigate,
  onToast,
}: {
  item: HandoverItem;
  onChanged: () => void;
  onNavigate: (to: string) => void;
  onToast: (message: string, retry?: () => void) => void;
}) {
  const act = useSubmit(async (fn: () => Promise<unknown>) => {
    await fn();
    onChanged();
  });

  const accept = () =>
    void act.submit(async () => {
      const res = await inboxApi.acceptHandover(item.id);
      onNavigate(`/tasks/${res.task.id}`);
    }).then(() => {
      if (act.error) onToast(errorMessage(act.error, '接管家失败，请重试'), accept);
    });

  const reactivate = () =>
    void act.submit(async () => {
      await inboxApi.reactivateHandover(item.id);
      onNavigate('/automations');
    }).then(() => {
      if (act.error) onToast(errorMessage(act.error, '恢复自动化失败，请重试'), reactivate);
    });

  if (item.state !== 'pending') {
    return (
      <div className="sg-handover__row">
        <div className="sg-handover__row-text">
          <span className="sg-handover__row-title">{item.target.title}</span>
          <span className="sg-handover__row-meta">{KIND_LABEL[item.kind] ?? item.kind} · 已处理</span>
        </div>
        <Tag label={item.state === 'accepted' ? '已接管' : item.state === 'reactivated' ? '已恢复' : '已处理'} tone="success" size={20} />
      </div>
    );
  }

  const stepOrder =
    typeof item.target.current_step === 'number' ? item.target.current_step : item.target.current_step?.order;
  let meta = '';
  if (item.kind === 'task') {
    if (item.resume_status) meta = `${STATUS_ZH[item.resume_status] ?? item.resume_status} · 确认前保持暂停`;
    else if (item.target.status === 'paused' && stepOrder) meta = `已暂停 · 停在第 ${stepOrder} 步`;
    else meta = STATUS_ZH[item.target.status] ?? item.target.status;
  } else if (item.kind === 'automation') {
    meta = item.target.status === 'paused' ? '已暂停' : (STATUS_ZH[item.target.status] ?? item.target.status);
  } else if (item.kind === 'workspace_ownership') {
    meta = '你已成为所有者';
  } else if (item.kind === 'custom_expert') {
    meta = item.target.shared_department ? `已共享给${item.target.shared_department}` : '已共享给整个团队';
  } else if (item.kind === 'approval') {
    meta = item.target.expert?.name ?? '';
  }
  // 任务行：标题与状态分两行（53）；其他行：标题 · 状态一行
  const oneLine = item.kind !== 'task' && meta;
  return (
    <div className="sg-handover__row">
      {item.kind !== 'task' && <span className="sg-handover__row-kind">{ROW_KIND[item.kind] ?? item.kind}</span>}
      <div className="sg-handover__row-text">
        <span className="sg-handover__row-title">{oneLine ? `${item.target.title} · ${meta}` : item.target.title}</span>
        {!oneLine && meta && <span className="sg-handover__row-meta">{meta}</span>}
      </div>
      {item.kind === 'task' && <Button variant="primary" size={30} label="确认并继续" busy={act.submitting} onClick={accept} />}
      {item.kind === 'automation' && <Button variant="secondary" size={30} label="重新启用" busy={act.submitting} onClick={reactivate} />}
      {item.kind === 'approval' && <Button variant="secondary" size={30} label="去处理" onClick={() => onNavigate('/inbox?filter=decide')} />}
      {(item.kind === 'custom_expert' || item.kind === 'workspace_ownership') && (
        <Button
          variant="secondary"
          size={30}
          label={item.kind === 'custom_expert' ? '查看' : '打开'}
          onClick={() => onNavigate(item.kind === 'custom_expert' ? `/experts/${item.target.id}` : `/workspaces/${item.target.id}`)}
        />
      )}
      {act.error && <InlineNotice icon="info" tone="error" text={errorMessage(act.error, '操作失败，请重试')} />}
    </div>
  );
}
