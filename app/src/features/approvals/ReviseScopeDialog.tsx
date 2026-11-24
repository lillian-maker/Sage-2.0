import { useState } from 'react';
import { Button } from '../../components/Button/Button';
import { ChoiceChipGroup } from '../../components/ChoiceChip/ChoiceChipGroup';
import { Modal } from '../../components/Modal/Modal';

export interface ReviseScopeOption {
  top_n: number;
  label: string;
}

export interface ReviseScopeDialogProps {
  /** Current range, e.g. Amazon.de · 小家电类目 · 前 50 名. */
  scopeText: string;
  /** Action line, e.g. 只读查询，不写入任何外部系统. */
  actionText: string;
  options: ReviseScopeOption[];
  currentTopN: number | undefined;
  /** unavailable estimate: the hint switches to 返回后可继续审批 (2777:1317 / 2777:423). */
  estimateState?: string;
  /** Show 确认范围不会批准，费用将重新估算。 — hidden for the unavailable estimate in the inbox. */
  reestimateNote?: boolean;
  busy?: boolean;
  onSubmit: (topN: number) => Promise<void> | void;
  onClose: () => void;
}

/** 2732:43 / 2736:43 调整本次查询范围: full-window centered modal shared by the task page and the inbox card. */
export function ReviseScopeDialog({
  scopeText,
  actionText,
  options,
  currentTopN,
  estimateState,
  reestimateNote = true,
  busy,
  onSubmit,
  onClose,
}: ReviseScopeDialogProps) {
  const [topN, setTopN] = useState<number | null>(null);
  // The range in effect comes first (前 50 名, then 前 20 名).
  const ordered = [...options].sort((x, y) => Number(y.top_n === currentTopN) - Number(x.top_n === currentTopN));
  const value = options.find((o) => o.top_n === (topN ?? currentTopN))?.label ?? '';
  const unavailable = estimateState === 'unavailable';
  const currentOption = options.find((o) => o.top_n === currentTopN);
  return (
    <Modal className="sg-formdialog" style={{ width: 520 }} labelledBy="sg-revisescope-title" onClose={onClose}>
      <h2 id="sg-revisescope-title" className="sg-formdialog__title">调整本次查询范围</h2>
      <p className="sg-formdialog__text">{`当前范围：${scopeText}\n动作：${actionText}`}</p>
      {unavailable && currentOption && <p className="sg-formdialog__text">{`当前为${currentOption.label}；返回后可继续审批。`}</p>}
      <span className="sg-formdialog__label">选择查询数量</span>
      <ChoiceChipGroup
        options={ordered.map((o) => o.label)}
        value={value}
        size={28}
        label="查询范围"
        onChange={(label) => setTopN(options.find((o) => o.label === label)?.top_n ?? null)}
      />
      {reestimateNote && <p className="sg-formdialog__text">确认范围不会批准，费用将重新估算。</p>}
      <div className="sg-formdialog__actions">
        <Button variant="ghost" size={28} label="取消" onClick={onClose} />
        <Button
          variant="primary"
          size={28}
          label="确认范围"
          busy={busy}
          disabled={topN == null || topN === currentTopN}
          onClick={async () => {
            if (topN != null) await onSubmit(topN);
          }}
        />
      </div>
    </Modal>
  );
}
