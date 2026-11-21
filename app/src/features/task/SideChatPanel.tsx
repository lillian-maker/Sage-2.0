import { useState } from 'react';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Avatar } from '../../components/Avatar/Avatar';
import { Button } from '../../components/Button/Button';
import { Composer } from '../../components/Composer/Composer';
import { Icon } from '../../components/Icon/Icon';
import { IconButton } from '../../components/IconButton/IconButton';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { EmptyState, ErrorBanner, LoadingState, errorMessage } from '../../components/States/States';
import { apiMessage, avatarOf, clockTime, taskApi, type ThreadItem } from './model';
import './detail.css';

export interface SideChatPanelProps {
  taskId: string;
  thread: ThreadItem;
  onClose: () => void;
  onBroughtBack: () => void;
  onSent?: () => void;
}

export function SideChatPanel({ taskId, thread, onClose, onBroughtBack, onSent }: SideChatPanelProps) {
  const msgs = useApiQuery((signal) => taskApi.messages(taskId, thread.id, signal), [taskId, thread.id]);
  const [text, setText] = useState('');
  const send = useSubmit(async (body: string) => {
    await taskApi.send(taskId, body, thread.id);
    setText('');
    msgs.reload();
    onSent?.();
  });
  const back = useSubmit(async () => {
    await taskApi.bringBack(taskId, thread.id);
    onBroughtBack();
  });
  const expert = thread.expert?.name ?? '';
  const submit = () => {
    if (text.trim() && !send.submitting) void send.submit(text.trim());
  };

  return (
    <aside className="sg-sidechat" aria-label="侧聊">
      <header className="sg-sidechat__head">
        <Icon name="message-square" size={14} />
        <span>侧聊</span>
        <Avatar {...avatarOf(expert, thread.expert?.avatar_ref)} size={20} />
        <span className="sg-sidechat__who truncate">{expert}</span>
        <Button variant="secondary" size={28} label="带回主线" busy={back.submitting} disabled={!msgs.data?.items.length} onClick={() => back.submit()} />
        <IconButton icon="x" size={28} variant="ghost" label="关闭侧聊" onClick={onClose} />
      </header>
      {thread.anchor && (
        <p className="sg-sidechat__anchor">
          针对：“{thread.anchor.excerpt}”（{thread.anchor.author_name} {clockTime(thread.anchor.sent_at).replace(/^今天 /, '')}）
        </p>
      )}
      <div className="sg-sidechat__list">
        {msgs.status === 'error' && <ErrorBanner message={errorMessage(msgs.error, '侧聊暂时无法读取')} onRetry={msgs.reload} />}
        {msgs.status === 'loading' && !msgs.data && <LoadingState rows={2} />}
        {msgs.data?.items.length === 0 && <EmptyState icon="message-square" title="还没有侧聊消息" description={`在下面问${expert}，不会打断主线`} />}
        {msgs.data?.items.map((m) => {
          const name = m.author_expert?.name ?? m.author_user?.display_name ?? '';
          return (
            <div key={m.id} className="sg-sidechat__msg">
              <Avatar {...avatarOf(name, m.author_expert?.avatar_ref)} size={20} />
              <div>
                <div className="sg-sidechat__meta">
                  <b>{name}</b> {clockTime(m.sent_at).replace(/^今天 /, '')}
                </div>
                <div className="sg-sidechat__body">{m.body}</div>
              </div>
            </div>
          );
        })}
      </div>
      {(send.error || back.error) && <InlineNotice icon="info" tone="error" text={apiMessage(send.error ?? back.error, '发送没有成功，请重试')} />}
      <div className="sg-sidechat__composer">
        <Composer
          placeholder={`在侧聊中回复${expert}…`}
          tools={[]}
          value={text}
          canSend={!!text.trim()}
          busy={send.submitting}
          onValueChange={setText}
          onSend={submit}
          width={316}
        />
      </div>
      <p className="sg-sidechat__foot">侧聊不会打断主线；点“带回主线”可把结论发回主对话。</p>
    </aside>
  );
}
