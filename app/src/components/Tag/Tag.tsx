import { Icon, type IconName } from '../Icon/Icon';
import './Tag.css';

export type TagTone = 'neutral' | 'info' | 'warning' | 'outline' | 'emphasis' | 'success' | 'danger';

export interface TagProps {
  label: string;
  tone: TagTone;
  size: 15 | 18 | 20 | 22 | 24 | 26;
  icon?: IconName;
  /** Icon placed after the label (e.g. the info glyph on “本机试用”). */
  iconAfter?: boolean;
  removable?: boolean;
  onRemove?: () => void;
  onClick?: () => void;
}

const ICON: Record<TagProps['size'], number> = { 15: 10, 18: 11, 20: 12, 22: 13, 24: 13, 26: 13 };

export function Tag({ label, tone, size, icon, iconAfter, removable, onRemove, onClick }: TagProps) {
  const glyph = icon && <Icon name={icon} size={ICON[size]} />;
  const cls = `sg-tag sg-tag--${tone} sg-tag--s${size}${onClick ? ' is-clickable' : ''}`;
  const body = (
    <>
      {!iconAfter && glyph}
      <span className="sg-tag__label">{label}</span>
      {iconAfter && glyph}
      {removable && (
        <span
          role="button"
          tabIndex={0}
          aria-label={`移除 ${label}`}
          className="sg-tag__remove"
          onClick={(e) => {
            e.stopPropagation();
            onRemove?.();
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') onRemove?.();
          }}
        >
          <Icon name="x" size={12} />
        </span>
      )}
    </>
  );
  return onClick ? (
    <button type="button" className={cls} onClick={onClick}>
      {body}
    </button>
  ) : (
    <span className={cls}>{body}</span>
  );
}
