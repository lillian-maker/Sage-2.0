import { useState } from 'react';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { EmptyState, ErrorBanner, LoadingState, errorMessage } from '../../components/States/States';
import { inboxApi } from './api';
import { GROUP_TAG, SOURCE_LABEL, dayLabel } from './format';
import { Avatar } from '../../components/Avatar/Avatar';
import { Tag } from '../../components/Tag/Tag';

/** “Amazon.de 买家消息”: the channel joined with the source kind unless they say the same thing. */
function sourceLine(channel: string | undefined, source: string): string {
  if (!channel || channel === source) return source;
  // A marketplace name ("Amazon.de") reads with the source kind; a channel name already says what it is.
  return channel.includes('.') ? `${channel} ${source}` : channel;
}

export interface ItemDetailProps {
  id: string;
  onClose: () => void;
  onChanged: () => void;
  onHandedOff: (itemId: string) => void;
  onOpenTask: (taskId: string) => void;
  /** Starts a new task conversation about this message. */
  onDiscuss?: (item: { title: string; workspace: { id: string } | null }) => void;
  onError: (message: string, retry: () => void) => void;
}

export function ItemDetail({ id, onChanged, onHandedOff, onOpenTask, onDiscuss, onError }: ItemDetailProps) {
  const detail = useApiQuery((s) => inboxApi.item(id, s), [id]);
  const [undoIgnored, setUndoIgnored] = useState(false);

  const ignore = useSubmit(async () => {
    await inboxApi.ignore(id);
    setUndoIgnored(true);
    onChanged();
  });
  const handOff = useSubmit(async (leadExpertId?: string) => {
    const res = await inboxApi.handOff(id, leadExpertId);
    onChanged();
    onHandedOff(id);
    return res;
  });

  if (detail.status === 'loading' && !detail.data) return <LoadingState rows={4} />;
  if (detail.status === 'error' && !detail.data)
    return <ErrorBanner message={errorMessage(detail.error, '这条消息暂时无法读取')} onRetry={detail.reload} />;
  const item = detail.data?.item;
  if (!item) return <EmptyState icon="message-square" title="这条消息不存在或已被移除" />;

  const actionable = (item.group === 'pending_confirmation' || item.group === 'todo') && !undoIgnored;
  return (
    <>
      {detail.status === 'error' && <ErrorBanner message={errorMessage(detail.error, '刷新失败')} onRetry={detail.reload} />}
      <div className="sg-indetail__scroll">
        <div>
          <h2 className="sg-indetail__title">{item.title}</h2>
          <div className="sg-incard__meta" style={{ marginTop: 6 }}>
            {[sourceLine(item.channel_label, SOURCE_LABEL[item.source.kind] ?? item.source.kind), dayLabel(item.received_at)].join(' · ')}
          </div>
          <div className="sg-incard__row" style={{ marginTop: 8 }}>
            <Tag {...GROUP_TAG[item.group]} />
            {item.confidence !== null && <Tag label={`判断把握 ${item.confidence}%`} tone={item.confidence < 60 ? 'warning' : 'neutral'} size={18} />}
          </div>
        </div>
        {item.original_text && (
          <div className="sg-indetail__quote">
            <p className="sg-indetail__text sg-indetail__original">
              {`原文：${item.original_text}${item.translated_text ? `\n译：${item.translated_text}` : ''}`}
            </p>
          </div>
        )}
        {item.rationale && (
          <section>
            <div className="sg-indetail__section-label">判断依据</div>
            <p className="sg-indetail__text">{item.rationale}</p>
          </section>
        )}
        {item.suggestion && (
          <section>
            <div className="sg-indetail__section-label">建议处理</div>
            <p className="sg-indetail__text">{item.suggestion}</p>
          </section>
        )}
        {item.suggested_experts.length > 0 && (
          <section>
            <div className="sg-indetail__section-label">建议交给</div>
            <div className="sg-incard__row" style={{ marginTop: 6, gap: 12 }}>
              {item.suggested_experts.map((e) => (
                <span key={e.id} className="sg-incard__row">
                  <Avatar size={20} alt={e.name} src={e.avatar_ref ?? null} />
                  <span className="sg-incard__expert-name">{e.name}</span>
                </span>
              ))}
            </div>
          </section>
        )}
        {item.events.length > 0 && (
          <section>
            <div className="sg-indetail__section-label">进展记录</div>
            <ul className="sg-indetail__events">
              {item.events.map((e, i) => (
                <li key={i}>{e.text}</li>
              ))}
            </ul>
          </section>
        )}
      </div>
      <div className="sg-indetail__actions">
        {actionable ? (
          <>
            <Button
              variant="ghost"
              size={32}
              label="忽略"
              busy={ignore.submitting}
              onClick={() =>
                void ignore.submit().then(() => {
                  if (ignore.error) onError(errorMessage(ignore.error, '忽略失败，请重试'), () => void ignore.submit());
                })
              }
            />
            {onDiscuss && <Button variant="secondary" size={32} label="去对话" onClick={() => onDiscuss(item)} />}
            <Button
              variant="primary"
              size={32}
              label="交给专家"
              busy={handOff.submitting}
              onClick={() =>
                void handOff.submit(item.suggested_experts[0]?.id).then(() => {
                  if (handOff.error) onError(errorMessage(handOff.error, '交给专家失败，请重试'), () => void handOff.submit());
                })
              }
            />
          </>
        ) : (
          item.task && <Button variant="primary" size={32} label="打开任务" onClick={() => onOpenTask(item.task!.id)} />
        )}
      </div>
    </>
  );
}
