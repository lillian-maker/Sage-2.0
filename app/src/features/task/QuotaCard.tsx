import { useState } from 'react';
import { useSubmit } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { apiMessage, taskApi, type TaskDetail } from './model';
import './detail.css';

export interface QuotaCardProps {
  task: TaskDetail;
  onResumed: () => void;
  onUsage: () => void;
}

export function QuotaCard({ task, onResumed, onUsage }: QuotaCardProps) {
  const resume = useSubmit(() => taskApi.resume(task.id));
  const remind = useSubmit(() => taskApi.remindQuotaAdmins(task.id));
  const [reminded, setReminded] = useState(false);
  const p = task.pause;
  const pausedAt = p?.paused_at ? new Date(p.paused_at) : null;
  const day = pausedAt ? `${pausedAt.getMonth() + 1}月${pausedAt.getDate()}日 · ` : '';
  const share = p?.member_share != null ? `${p.member_share.toLocaleString('en-US')} Credits` : '额度';
  const dept = p?.department_name ? `你在“${p.department_name}”的个人份额 ${share}` : `你的 ${share}`;
  const done = p?.completed_steps ? `已完成的 ${p.completed_steps} 步结果已保存；` : '';
  const step = p?.resume_step ? `从第 ${p.resume_step} 步接着做` : '接着做';
  return (
    <section className="sg-quota" role="status">
      <div className="sg-quota__title">
        <Icon name="info" size={14} />
        本月额度已用完，任务已暂停
      </div>
      <p className="sg-quota__body">
        {`${day}${dept}${p?.member_share != null ? ' ' : ''}与可用的部门调剂均已用完。${done}管理员调高额度后，点击“继续”${step}。`}
      </p>
      <div className="sg-quota__actions">
        <Button
          variant="primary"
          size={30}
          label={reminded ? '已提醒管理员' : '提醒管理员'}
          busy={remind.submitting}
          disabled={reminded}
          onClick={async () => {
            const r = await remind.submit();
            if (r !== undefined) setReminded(true);
          }}
        />
        <Button
          variant="secondary"
          size={30}
          label="继续"
          busy={resume.submitting}
          disabled={p?.resume_available === false}
          onClick={async () => {
            const r = await resume.submit();
            if (r !== undefined) onResumed();
          }}
        />
        <Button variant="ghost" size={30} label="查看我的用量" onClick={onUsage} />
      </div>
      {resume.error && <InlineNotice icon="info" tone="error" text={apiMessage(resume.error, '暂时无法继续，请稍后再试')} />}
      {remind.error && <InlineNotice icon="info" tone="error" text={apiMessage(remind.error, '没有提醒成功，请稍后再试')} />}
    </section>
  );
}
