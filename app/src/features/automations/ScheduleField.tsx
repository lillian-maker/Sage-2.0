import { Select } from '../../components/Select/Select';
import './automations.css';

export interface ScheduleFieldProps {
  frequency: string;
  /** Day (weekly/monthly) — hidden for daily schedules. */
  day?: string;
  time: string;
  onChange?: (next: { frequency: string; day?: string; time: string }) => void;
}

const FREQ = ['每天', '每周', '每月'].map((v) => ({ value: v, label: v }));
const DAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'].map((v) => ({ value: v, label: v }));
const TIMES = Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, '0')}:00`).map((v) => ({ value: v, label: v }));

export function ScheduleField({ frequency, day, time, onChange }: ScheduleFieldProps) {
  return (
    <div className="sg-schedule">
      <Select size={36} label="频率" value={frequency} options={FREQ} onChange={(v) => onChange?.({ frequency: v, day, time })} />
      {frequency !== '每天' && <Select size={36} label="日期" value={day} options={DAYS} onChange={(v) => onChange?.({ frequency, day: v, time })} />}
      <Select size={36} label="时间" value={time} options={TIMES} onChange={(v) => onChange?.({ frequency, day, time: v })} />
    </div>
  );
}
