import { useState } from 'react';
import { useSubmit } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { Modal } from '../../components/Modal/Modal';
import { TextField } from '../../components/TextField/TextField';
import { DeliveryReviewCard } from './DeliveryReviewCard';
import { apiMessage, taskApi, type Criterion } from './model';
import './detail.css';

export interface DeliveryBlockProps {
  taskId: string;
  deliveryId: string;
  /** “德国站竞品格局报告 v2”: the delivered artifact version. */
  artifactLabel: string;
  criteria: Criterion[];
  /** Start with the return-reason field open (deep link). */
  returning?: boolean;
  /** Set once the delivery was accepted or returned: the card stays as a read-only record. */
  decided?: { decision: 'accepted' | 'returned'; reason?: string | null };
  onDone: (result: 'accepted' | 'returned', reason?: string) => void;
}

export function DeliveryBlock({ taskId, deliveryId, artifactLabel, criteria, returning, decided, onDone }: DeliveryBlockProps) {
  const [open, setOpen] = useState(!!returning);
  const [reason, setReason] = useState('');
  const [missing, setMissing] = useState(false);
  // Items the reviewer unticks while writing a return reason; sent along so the lead only reworks those.
  const [unmet, setUnmet] = useState<string[]>([]);
  const accept = useSubmit(() => taskApi.accept(taskId, deliveryId));
  const ret = useSubmit((r: string) =>
    taskApi.returnDelivery(taskId, deliveryId, r, unmet.map((id) => ({ criterion_id: id, verdict: 'unmet' as const }))),
  );
  const error = accept.error ?? ret.error;
  const met = (c: Criterion) => c.verdict === 'met' && !unmet.includes(c.id);
  const metCount = criteria.filter(met).length;
  const subtitle = [criteria.length ? `验收清单 ${metCount}/${criteria.length} 已满足` : '', artifactLabel].filter(Boolean).join(' · ');

  return (
    <div className="sg-deliveryblock">
      <DeliveryReviewCard
        subtitle={subtitle}
        readOnly={!!decided}
        checklist={criteria.map((c) => ({
          checked: met(c),
          label: c.verdict === 'unmet' || unmet.includes(c.id) ? `${c.text}（待补充）` : c.text,
          ...(c.evidence ? { evidence: c.evidence } : {}),
          ...(open && !decided && c.verdict === 'met'
            ? { onToggle: () => setUnmet((list) => (list.includes(c.id) ? list.filter((x) => x !== c.id) : [...list, c.id])) }
            : {}),
        }))}
        {...(accept.submitting ? { busy: 'accept' as const } : ret.submitting ? { busy: 'return' as const } : {})}
        onAccept={async () => {
          const r = await accept.submit();
          if (r !== undefined) onDone('accepted');
        }}
        onReturn={() => setOpen(true)}
      />
      {decided && (
        <InlineNotice
          icon="check"
          tone="muted"
          text={decided.decision === 'accepted' ? '验收通过，当前版本已锁定' : decided.reason ? `已退回：${decided.reason}` : '已退回，等待修改'}
        />
      )}
      {open && !decided && (
        <Modal
          className="sg-formdialog"
          style={{ width: 600 }}
          labelledBy="sg-return-title"
          onClose={() => { setOpen(false); setUnmet([]); setMissing(false); }}
          onSubmit={async (e) => {
            e.preventDefault();
            if (!reason.trim()) {
              setMissing(true);
              return;
            }
            const r = await ret.submit(reason.trim());
            if (r !== undefined) onDone('returned', reason.trim());
          }}
        >
          <h2 id="sg-return-title" className="sg-formdialog__title">填写退回原因</h2>
          <p className="sg-formdialog__sub">请说明需要修改的内容。</p>
          <span className="sg-formdialog__label">退回原因（必填）</span>
          <TextField
            size={32}
            value={reason}
            invalid={missing}
            placeholder="请填写退回原因"
            aria-label="退回原因（必填）"
            autoFocus
            onValueChange={(v) => {
              setReason(v);
              if (v.trim()) setMissing(false);
            }}
          />
          {missing && <InlineNotice icon="info" tone="error" text="请填写退回原因后再提交。" />}
          {ret.error && <InlineNotice icon="info" tone="error" text={apiMessage(ret.error, '提交没有成功，请重试')} />}
          <div className="sg-formdialog__actions">
            <Button variant="ghost" size={28} label="取消" onClick={() => { setOpen(false); setUnmet([]); setMissing(false); }} />
            <Button variant="primary" size={28} type="submit" label="提交退回" busy={ret.submitting} />
          </div>
        </Modal>
      )}
      {error && !open && <InlineNotice icon="info" tone="error" text={apiMessage(error, '提交没有成功，请重试')} />}
    </div>
  );
}
