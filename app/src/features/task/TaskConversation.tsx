import { useEffect, useState, type ReactNode } from 'react';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { IconButton } from '../../components/IconButton/IconButton';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { Menu, MenuDivider, MenuItem, Popover } from '../../components/Menu/MenuItem';
import { TextField } from '../../components/TextField/TextField';
import { MessageItem } from './MessageItem';
import { ParallelWorkCard } from './ParallelWorkCard';
import { RunSummary } from './RunSummary';
import { avatarOf, clockTime, durationText, type TaskMessage } from './model';
import './detail.css';

function metricsText(m: NonNullable<TaskMessage['run_metrics']>): string {
  if (m.steps_total) return `查看过程 · ${m.steps_done ?? 0}/${m.steps_total} 步已完成`;
  const parts = [
    m.artifacts_created ? `生成 ${m.artifacts_created} 个成果` : '',
    m.documents_read ? `查阅 ${m.documents_read} 份资料` : '',
    m.tool_calls ? `调用工具 ${m.tool_calls} 次` : '',
  ].filter(Boolean);
  const head = parts.length ? parts.join('，') : '已完成';
  return m.elapsed_seconds ? `${head} · ${durationText(m.elapsed_seconds)}` : head;
}

export interface TaskConversationProps {
  messages: TaskMessage[];
  /** Id of the user message being edited, if any. */
  editingId?: string | null;
  editBusy?: boolean;
  onEdit?: (id: string) => void;
  onCancelEdit?: () => void;
  onResend?: (id: string, body: string) => void;
  summaryOpen?: boolean;
  onSideChat?: (messageId: string) => void;
  onBranch?: (index: number) => void;
  renderCard?: (m: TaskMessage) => ReactNode;
  /** Parallel-work rows open the matching orchestration node. */
  onOpenStep?: (stepId: string) => void;
  sideThreads?: Record<string, { id: string; count: number; expert: string }>;
  /** Before the plan produces anything, the opening request collapses into a one-line context pin. */
  pinFirstUser?: boolean;
  /** Expert-message “更多” menu (board 10): 标为重要 and 从这里分叉新任务. */
  onImportant?: (messageId: string, important: boolean) => void;
  onFork?: (messageId: string) => void;
}

const INDEX_LABEL: Record<string, string> = { indexed: '就绪', indexing: '索引中', queued: '排队中', failed: '索引失败' };

export function TaskConversation({
  messages,
  editingId,
  editBusy,
  onEdit,
  onCancelEdit,
  onResend,
  summaryOpen,
  onSideChat,
  onBranch,
  renderCard,
  onOpenStep,
  sideThreads,
  pinFirstUser,
  onImportant,
  onFork,
}: TaskConversationProps) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [menu, setMenu] = useState<{ id: string; at: { x: number; y: number } } | null>(null);
  const menuMessage = menu ? messages.find((m) => m.id === menu.id) : undefined;
  const [pinOpen, setPinOpen] = useState(false);
  const firstUser = messages.find((m) => m.author_kind === 'user')?.id;
  const [draft, setDraft] = useState('');
  const lastUser = [...messages].reverse().find((m) => m.author_kind === 'user')?.id;
  const editingBody = messages.find((m) => m.id === editingId)?.body;
  useEffect(() => {
    if (editingId) setDraft(editingBody ?? '');
  }, [editingId, editingBody]);

  return (
    <div className="sg-conv">
      {messages.map((m) => {
        if (m.author_kind === 'system') {
          if (m.kind === 'quota_notice') return null;
          return <InlineNotice key={m.id} icon="info" tone="muted" text={m.body} />;
        }
        const card = renderCard?.(m);
        if (m.author_kind === 'user' && pinFirstUser && m.id === firstUser) {
          const name = m.author_user?.display_name ?? '';
          const clause = m.body.split(/[，。,\n]/)[0] ?? m.body;
          const linked = m.attachments.map((a) => `${a.title}${INDEX_LABEL[String(a.index_state)] ? `（${INDEX_LABEL[String(a.index_state)]}）` : ''}`);
          return (
            <button key={m.id} type="button" className="sg-conv__pin" aria-expanded={pinOpen} onClick={() => setPinOpen((v) => !v)}>
              <span className="sg-conv__pin-line">{pinOpen ? `↑ ${m.body}` : `↑ ${name}：${clause}${clause.length < m.body.length ? '…' : ''}`}</span>
              {pinOpen && linked.length > 0 && <span className="sg-conv__pin-line">已关联：{linked.join(' · ')}</span>}
            </button>
          );
        }
        if (m.author_kind === 'user') {
          const name = m.author_user?.display_name ?? '';
          const editing = m.id === editingId;
          return (
            <MessageItem
              key={m.id}
              role="user"
              author={name}
              avatar={avatarOf(name)}
              time={clockTime(m.sent_at)}
              body={editing ? '' : m.body}
              attachments={m.attachments.map((a) => a.title)}
            >
              {editing && (
                <div className="sg-conv__edit">
                  <TextField multiline rows={3} value={draft} onValueChange={setDraft} aria-label="修改消息" />
                  <div className="sg-conv__editbar">
                    <span className="sg-conv__edithint">
                      <Icon name="info" size={12} />
                      原分支已执行的操作不会撤销
                    </span>
                    <Button variant="ghost" size={28} label="取消" onClick={onCancelEdit} />
                    <Button variant="primary" size={28} label="重新发送" busy={editBusy} disabled={!draft.trim()} onClick={() => onResend?.(m.id, draft.trim())} />
                  </div>
                </div>
              )}
              <div className="sg-conv__tools">
                {m.branch && m.branch.count > 1 && (
                  <span className="sg-conv__branch">
                    <button type="button" aria-label="上一个分支" disabled={m.branch.index <= 1} onClick={() => onBranch?.(m.branch!.index - 1)}>
                      <Icon name="chevron-left" size={12} />
                    </button>
                    分支 {m.branch.index}/{m.branch.count}
                    <button type="button" aria-label="下一个分支" disabled={m.branch.index >= m.branch.count} onClick={() => onBranch?.(m.branch!.index + 1)}>
                      <Icon name="chevron-right" size={12} />
                    </button>
                  </span>
                )}
                {m.id === lastUser && !editing && onEdit && (
                  <button
                    type="button"
                    className="sg-conv__link"
                    onClick={() => onEdit(m.id)}
                  >
                    修改
                  </button>
                )}
              </div>
              {card}
            </MessageItem>
          );
        }
        const name = m.author_expert?.name ?? '';
        const metrics = m.run_metrics;
        const expanded = open[m.id] ?? !!summaryOpen;
        const side = sideThreads?.[m.id];
        const tag = m.author_role === 'lead' ? undefined : m.author_role === 'assist' ? '协作' : undefined;
        return (
          <MessageItem
            key={m.id}
            role="expert"
            author={name}
            avatar={avatarOf(name, m.author_expert?.avatar_ref)}
            time={clockTime(m.sent_at).replace(/^今天 /, '')}
            body={m.card?.type === 'delivery' && m.card.decision === 'accepted' ? `${m.body} · 当前版本已锁定` : m.body}
            leadTag={m.author_role === 'lead'}
            {...(tag || m.author_expert?.specialty ? { subtitle: tag ?? m.author_expert?.specialty ?? '' } : {})}
            attachments={m.attachments.map((a) => a.title)}
          >
            {m.card?.type === 'delivery' && m.card.summary && m.card.summary !== m.body && (
              <p className="sg-msg__body sg-conv__delivery-summary">{m.card.summary}</p>
            )}
            {metrics?.parallel && <ParallelWorkCard metrics={metrics} {...(onOpenStep ? { onOpenStep } : {})} />}
            {metrics && !metrics.parallel && (
              <div className="sg-conv__metrics">
                <RunSummary status="done" summary={metricsText(metrics)} expanded={expanded} onToggle={() => setOpen((o) => ({ ...o, [m.id]: !expanded }))} />
                {expanded && (
                  <dl className="sg-conv__metricgrid">
                    <div><dt>成果</dt><dd>{metrics.artifacts_created ?? 0}</dd></div>
                    <div><dt>资料</dt><dd>{metrics.documents_read ?? 0}</dd></div>
                    <div><dt>工具调用</dt><dd>{metrics.tool_calls ?? 0}</dd></div>
                    <div><dt>用时</dt><dd>{durationText(metrics.elapsed_seconds) || '—'}</dd></div>
                  </dl>
                )}
              </div>
            )}
            {m.citations.length > 0 && (
              <div className="sg-conv__cites">
                <span>来源</span>
                {m.citations.map((c) => (
                  <span key={c.id} className="sg-conv__cite">{c.label}</span>
                ))}
              </div>
            )}
            {m.kind === 'summary' && m.card?.type === 'side_thread' && <span className="sg-conv__chip">由侧聊带回主线</span>}
            {onSideChat && m.kind !== 'summary' && (
              <button type="button" className="sg-conv__chip" onClick={() => onSideChat(m.id)}>
                <Icon name="message-square" size={12} />
                {side ? `侧聊 ${side.count} 条 · ${side.expert}` : '侧聊'}
              </button>
            )}
            {m.kind !== 'summary' && (
              <IconButton
                icon="ellipsis"
                size={25}
                iconSize={14}
                variant="ghost"
                label="消息操作"
                className="sg-conv__more"
                onClick={(e) => {
                  const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                  setMenu({ id: m.id, at: { x: r.left, y: r.bottom + 4 } });
                }}
              />
            )}
            {card}
          </MessageItem>
        );
      })}
      {menu && menuMessage && (
        <Popover at={menu.at} placement="below" width={260} onClose={() => setMenu(null)}>
          <Menu label="消息操作">
            <MenuItem
              icon="link"
              label="复制"
              onSelect={() => {
                setMenu(null);
                void navigator.clipboard?.writeText(menuMessage.body).catch(() => undefined);
              }}
            />
            {onSideChat && (
              <MenuItem
                icon="message-square"
                label="在侧聊中讨论"
                onSelect={() => {
                  setMenu(null);
                  onSideChat(menuMessage.id);
                }}
              />
            )}
            {onImportant && (
              <MenuItem
                icon="pin"
                label={menuMessage.is_important ? '取消标为重要' : '标为重要'}
                onSelect={() => {
                  setMenu(null);
                  onImportant(menuMessage.id, !menuMessage.is_important);
                }}
              />
            )}
            {onFork && (
              <>
                <MenuDivider />
                <MenuItem
                  icon="square-pen"
                  label="从这里分叉新任务"
                  hint="⌘⇧F"
                  onSelect={() => {
                    setMenu(null);
                    onFork(menuMessage.id);
                  }}
                />
                <p className="sg-conv__menunote">新任务继承这条消息之前的上下文与资料，原任务不受影响</p>
              </>
            )}
          </Menu>
        </Popover>
      )}
    </div>
  );
}
