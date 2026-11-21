import { Avatar } from '../../components/Avatar/Avatar';
import { Tag } from '../../components/Tag/Tag';
import type { InboxItem } from './api';
import { SOURCE_LABEL, shortTime } from './format';

export interface InboxCardProps {
  item: InboxItem;
  active?: boolean;
  onOpen?: () => void;
}

export function InboxCard({ item, active, onOpen }: InboxCardProps) {
  const lead = item.suggested_experts[0];
  return (
    <button type="button" className={`sg-incard${active ? ' is-active' : ''}`} onClick={onOpen}>
      <span className="sg-incard__meta">
        {[item.channel_label || SOURCE_LABEL[item.source.kind] || item.source.kind, shortTime(item.received_at)].filter(Boolean).join(' · ')}
      </span>
      <span className="sg-incard__title">{item.title}</span>
      <span className="sg-incard__foot">
        {item.confidence !== null && (
          <Tag label={`判断把握 ${item.confidence}%`} tone={item.confidence < 50 ? 'warning' : 'neutral'} size={18} />
        )}
        {item.priority === 'high' && <Tag label="优先" tone="danger" size={18} />}
        {lead && (
          <span className="sg-incard__row sg-incard__lead">
            <Avatar size={18} alt={lead.name} src={lead.avatar_ref ?? null} ring />
            <span className="sg-incard__expert-name">{lead.name}</span>
            <span className="sg-incard__hint">· 建议</span>
          </span>
        )}
      </span>
    </button>
  );
}
