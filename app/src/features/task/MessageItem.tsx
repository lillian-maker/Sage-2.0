import { Avatar, type AvatarProps } from '../../components/Avatar/Avatar';
import { Icon } from '../../components/Icon/Icon';
import { Tag } from '../../components/Tag/Tag';
import './task.css';

export interface MessageItemProps {
  role: 'user' | 'expert';
  author: string;
  avatar: Omit<AvatarProps, 'size'>;
  time: string;
  body: string;
  leadTag?: boolean;
  subtitle?: string;
  attachments?: string[];
  children?: React.ReactNode;
}

const ITEM = /^(\d+)\.\s+(.*)$/;

/** Plain text with “1. …” lines shown as a numbered list (2736:43 结论列表); everything else stays pre-wrapped text. */
function renderBody(body: string) {
  const lines = body.split('\n');
  if (!lines.some((l) => ITEM.test(l))) return body;
  const out: React.ReactNode[] = [];
  let text: string[] = [];
  let items: string[] = [];
  const flushText = () => {
    if (text.length) out.push(<span key={out.length}>{text.join('\n')}</span>);
    text = [];
  };
  const flushItems = () => {
    if (items.length)
      out.push(
        <ol key={out.length} className="sg-msg__list">
          {items.map((t, i) => <li key={i}>{t}</li>)}
        </ol>,
      );
    items = [];
  };
  for (const line of lines) {
    const m = ITEM.exec(line);
    if (m) {
      flushText();
      items.push(m[2]!);
    } else {
      flushItems();
      text.push(line);
    }
  }
  flushText();
  flushItems();
  return out;
}

export function MessageItem({ role, author, avatar, time, body, leadTag, subtitle, attachments, children }: MessageItemProps) {
  return (
    <article className={`sg-msg sg-msg--${role}`}>
      <Avatar {...avatar} size={28} />
      <div className="sg-msg__main">
        <div className="sg-msg__head">
          <span className="sg-msg__author">{author}</span>
          {leadTag && <Tag label="主责" tone="info" size={18} />}
          {subtitle ? (
            <span className="sg-msg__meta">{`${subtitle} · ${time}`}</span>
          ) : (
            <time className="sg-msg__meta">{time}</time>
          )}
        </div>
        <div className="sg-msg__body">{renderBody(body)}</div>
        {attachments && attachments.length > 0 && (
          <div className="sg-msg__files">
            {attachments.map((a) => (
              <span key={a} className="sg-msg__file">
                <Icon name="file" size={14} />
                {a}
              </span>
            ))}
          </div>
        )}
        {children}
      </div>
    </article>
  );
}
