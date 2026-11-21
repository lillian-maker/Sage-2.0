import { Icon, type IconName } from '../../components/Icon/Icon';
import './tasks.css';

export interface TaskListRowProps {
  title: string;
  subtitle: string;
  state: string;
  /** Tone of the state pill: waiting-for-you (orange) or progress (brand). */
  stateTone?: 'warning' | 'info';
  time: string;
  statusIcon: IconName;
  onOpen?: () => void;
}

export function TaskListRow({ title, subtitle, state, stateTone, time, statusIcon, onOpen }: TaskListRowProps) {
  const tone = stateTone ?? (statusIcon === 'circle-pending' ? 'warning' : 'info');
  return (
    <button type="button" className="sg-tasklist" onClick={onOpen}>
      <Icon
        name={statusIcon}
        size={18}
        color={tone === 'warning' ? 'color.orange.660' : 'accent'}
        className={statusIcon === 'loader-ring' ? 'sg-spin' : undefined}
      />
      <span className="sg-tasklist__text">
        <span className="sg-tasklist__title truncate">{title}</span>
        <span className="sg-tasklist__sub truncate">{subtitle}</span>
      </span>
      <span className="sg-tasklist__end">
        <span className={`sg-tasklist__state is-${tone}`}>{state}</span>
        <span className="sg-tasklist__time">{time}</span>
      </span>
    </button>
  );
}
