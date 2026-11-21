import { Fragment } from 'react';
import { Button } from '../../components/Button/Button';
import './settings.css';

export interface SeatSummaryBarProps {
  purchased: number;
  assigned: number;
  invited: number;
  locked: number;
  onInvite?: () => void;
  onAdjust?: () => void;
  onNewDepartment?: () => void;
}

export function SeatSummaryBar({ purchased, assigned, invited, locked, onInvite, onAdjust, onNewDepartment }: SeatSummaryBarProps) {
  const available = Math.max(0, purchased - assigned - invited - locked);
  const cells: Array<[number, string]> = [
    [purchased, '已购名额'],
    [assigned, '已分配'],
    [invited, '邀请预留'],
    [locked, '本月锁定'],
    [available, '可分配'],
  ];
  return (
    <div className="sg-seats">
      {cells.map(([n, label], i) => (
        <Fragment key={label}>
          {i > 0 && <span className="sg-seats__op">{i === 1 ? '=' : '+'}</span>}
          <span className="sg-seats__cell">
            <b>{n}</b>
            <span>{label}</span>
          </span>
        </Fragment>
      ))}
      <span className="sg-seats__actions">
        <Button variant="secondary" size={32} label="增减名额 ↗" onClick={onAdjust} />
        <Button variant="secondary" size={32} icon="plus" label="新建部门" onClick={onNewDepartment} />
        <Button variant="primary" size={32} icon="plus" label="邀请成员" onClick={onInvite} disabled={available === 0} />
      </span>
    </div>
  );
}
