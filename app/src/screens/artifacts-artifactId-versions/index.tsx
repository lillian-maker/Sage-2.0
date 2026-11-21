import { useEffect, useMemo, useState } from 'react';
import { Link, type ScreenProps } from '../../app/router';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { Composer } from '../../components/Composer/Composer';
import { ComposerContextBar } from '../../components/Composer/ComposerContextBar';
import { AuthorizationBlock } from '../../features/approvals/AuthorizationBlock';
import { Icon } from '../../components/Icon/Icon';
import { IconButton } from '../../components/IconButton/IconButton';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { EmptyState, ErrorBanner, ForbiddenState, LoadingState, Toast, errorMessage } from '../../components/States/States';
import { Tag } from '../../components/Tag/Tag';
import { MessageItem } from '../../features/task/MessageItem';
import { apiMessage, avatarOf, taskApi, taskStatusTag } from '../../features/task/model';
import { KIND_ICON, artifactApi, type Artifact, type VersionDiff } from '../../features/artifacts/model';
import { dayTime } from '../../features/workspaces/model';
import '../../features/artifacts/artifacts.css';
import { NotificationBell } from '../../components/NotificationBell/NotificationBell';

function timeShort(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return '';
  const d = new Date(iso);
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  if (d.toDateString() === now.toDateString()) return hm;
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

function changedCount(diff: VersionDiff): number {
  return diff.hunks.filter((h) => h.deleted !== undefined || h.inserted !== undefined).length;
}

function DiffCard({
  artifact,
  diff,
  canClose,
  onClose,
  onRestored,
  onError,
}: {
  artifact: Artifact;
  diff: VersionDiff;
  canClose: boolean;
  onClose: () => void;
  onRestored: (text: string) => void;
  onError: (text: string) => void;
}) {
  const restore = useSubmit(async () => {
    await artifactApi.restore(artifact.id, diff.base.number);
    onRestored(`已恢复到 v${diff.base.number}，生成 v${diff.restore_preview?.next_number ?? ''} 草稿`);
  });
  useEffect(() => {
    if (restore.error) onError(apiMessage(restore.error, '恢复失败，请重试'));
  }, [restore.error]);
  const author = diff.target.author?.name ?? '系统';
  const next = diff.restore_preview?.next_number;
  return (
    <div className="sg-artdiff__card">
      <div className="sg-artdiff__cardhead">
        <Icon name={KIND_ICON[artifact.kind] ?? 'file'} size={14} />
        <span className="sg-artdiff__cardtitle truncate">{artifact.title}</span>
        <span className="sg-artdiff__cardmeta">
          v{diff.base.number} → v{diff.target.number} · {author}修改了 {changedCount(diff)} 处
        </span>
        <div className="sg-artdiff__cardend">
          <Button
            variant="secondary"
            size={26}
            icon="refresh"
            label={`恢复到 v${diff.base.number}`}
            busy={restore.submitting}
            disabled={!diff.can_restore}
            onClick={() => void restore.submit()}
          />
          {canClose && <IconButton icon="x" size={25} variant="ghost" label="关闭差异" onClick={onClose} />}
        </div>
      </div>
      <div className="sg-artdiff__hunks">
        {diff.hunks.map((h, i) => (
          <p key={i} className="sg-artdiff__hunk">
            {h.context !== undefined && <span className="sg-artdiff__hunk is-context">{h.context}</span>}
            {h.deleted !== undefined && <span className="sg-artdiff__del">{h.deleted}</span>}
            {h.deleted !== undefined && h.inserted !== undefined && ' '}
            {h.inserted !== undefined && <span className="sg-artdiff__ins">{h.inserted}</span>}
          </p>
        ))}
        {diff.hunks.length === 0 && <InlineNotice icon="info" tone="muted" text="两个版本的文字内容一致" />}
      </div>
      <div className="sg-artdiff__foot">
        恢复会生成{next ? ` v${next}` : '一个新版本'}，不会删除 v{diff.target.number}。只读成员可以查看差异，不能恢复。
      </div>
    </div>
  );
}

export default function Screen({ params, navigate }: ScreenProps) {
  const id = params.artifactId ?? '';
  const detail = useApiQuery((s) => artifactApi.get(id, s), [id]);
  const a = detail.data;
  const taskId = a?.source_task?.id ?? null;

  const task = useApiQuery((s) => (taskId ? taskApi.detail(taskId, s) : Promise.resolve(null)), [taskId]);
  const messages = useApiQuery((s) => (taskId ? taskApi.messages(taskId, 'main', s) : Promise.resolve(null)), [taskId]);
  const siblings = useApiQuery((s) => (taskId ? artifactApi.list({ task: taskId, sort: 'updated' }, s) : Promise.resolve(null)), [taskId]);
  const steps = useApiQuery((s) => (taskId ? taskApi.steps(taskId, s) : Promise.resolve(null)), [taskId]);
  const [reply, setReply] = useState('');
  const send = useSubmit(async (body: string) => {
    if (!taskId) return;
    await taskApi.send(taskId, body);
    setReply('');
    messages.reload();
  });

  const [diffFor, setDiffFor] = useState<string | null>(null);
  const [toast, setToast] = useState<{ tone: 'undo' | 'failure'; text: string } | null>(null);

  const shown = useMemo(() => {
    const items = siblings.data?.items ?? [];
    return items.find((x) => x.id === (diffFor ?? id)) ?? (a && (diffFor === null || diffFor === id) ? a : null) ?? null;
  }, [siblings.data, a, diffFor, id]);
  const v = shown?.current_version ?? null;
  const base = v?.previous_number ?? (v && v.number > 1 ? v.number - 1 : null);
  const diff = useApiQuery(
    (s) => (shown && v && base !== null ? artifactApi.diff(shown.id, v.number, base, s) : Promise.resolve(null)),
    [shown?.id, v?.number, base],
  );

  // Figma 56 opens with the current artifact's diff visible over the conversation.
  const [dismissed, setDismissed] = useState(false);
  useEffect(() => setDismissed(false), [shown?.id]);
  const diffOpen = !dismissed && diffFor !== '__closed' && !!shown && base !== null;

  if (detail.status === 'error' && (detail.error.status === 403 || detail.error.status === 404)) {
    return (
      <div className="sg-arts__main">
        <ForbiddenState title="无法打开这个成果" action={{ label: '返回成果', onClick: () => navigate('/artifacts') }} />
      </div>
    );
  }

  const t = task.data;
  const statusTag = t ? taskStatusTag(t.status) : undefined;
  const summary = siblings.data?.summary;
  const closeDiff = () => {
    setDismissed(true);
    setDiffFor('__closed');
  };
  const reloadAll = () => {
    detail.reload();
    siblings.reload();
    diff.reload();
  };

  return (
    <div className="sg-artview">
      <div className="sg-artview__top">
        <IconButton
          icon="chevron-left"
          size={28}
          variant="ghost"
          label="返回"
          onClick={() => navigate(taskId ? `/tasks/${taskId}` : `/artifacts/${id}`)}
        />
        <span className="sg-artview__name truncate">{t?.title ?? a?.title ?? ''}</span>
        {t?.workspace && <Tag label={t.workspace.name} tone="neutral" size={20} />}
        {statusTag && <Tag {...statusTag} />}
        <div className="sg-artview__end">
          <Button variant="ghost" size={30} label="打开成果" onClick={() => navigate(`/artifacts/${id}`)} />
          {taskId && <Button variant="secondary" size={30} icon="users" label="编排" onClick={() => navigate(`/tasks/${taskId}/orchestration`)} />}
          {taskId && <Button variant="secondary" size={30} label="在任务中查看" onClick={() => navigate(`/tasks/${taskId}`)} />}
          <NotificationBell onClick={() => navigate('/notifications')} />
        </div>
      </div>

      <div className="sg-artdiff__body">
        <div className="sg-artdiff__conv">
          <div className="sg-artdiff__msgs">
            {detail.status === 'error' && <ErrorBanner message={errorMessage(detail.error, '成果暂时无法读取')} onRetry={detail.reload} />}
            {taskId && messages.status === 'error' && (
              <ErrorBanner message={errorMessage(messages.error, '对话暂时无法读取')} onRetry={messages.reload} />
            )}
            {taskId && messages.status === 'loading' && !messages.data && <LoadingState rows={3} />}
            {messages.data?.items
              .filter((m) => m.author_kind !== 'system' && m.body)
              .map((m) => {
                const expert = m.author_expert;
                const name = expert?.name ?? m.author_user?.display_name ?? '你';
                return (
                  <MessageItem
                    key={m.id}
                    role={m.author_kind === 'user' ? 'user' : 'expert'}
                    author={name}
                    avatar={avatarOf(name, expert?.avatar_ref)}
                    time={timeShort(m.sent_at)}
                    body={m.body}
                    {...(m.author_role === 'lead' ? { leadTag: true } : {})}
                    {...(expert?.specialty ? { subtitle: expert.specialty } : {})}
                    {...(m.attachments.length ? { attachments: m.attachments.map((x) => x.title) } : {})}
                  />
                );
              })}
            {(steps.data?.items ?? [])
              .flatMap((s) => (s.authorization_id && s.status === 'awaiting_user' ? [s.authorization_id] : []))
              .map((aid) => (
                <AuthorizationBlock key={aid} id={aid} onDecided={() => { steps.reload(); messages.reload(); task.reload(); }} />
              ))}
            {!taskId && <EmptyState icon="file" title="独立成果" description="这个成果不属于任何任务，只显示版本差异" />}
            {diffOpen && shown && (
              <div className="sg-artdiff__overlay">
                {diff.status === 'error' && <ErrorBanner message={errorMessage(diff.error, '差异暂时无法读取')} onRetry={diff.reload} />}
                {diff.status === 'loading' && !diff.data && <LoadingState rows={3} label="正在加载差异" />}
                {diff.data && (
                  <DiffCard
                    artifact={shown}
                    diff={diff.data}
                    canClose={!!taskId}
                    onClose={closeDiff}
                    onRestored={(text) => {
                      setToast({ tone: 'undo', text });
                      reloadAll();
                    }}
                    onError={(text) => setToast({ tone: 'failure', text })}
                  />
                )}
              </div>
            )}
          </div>
          {taskId && t && (
            <div className="sg-artdiff__convfoot">
              {t.access === 'edit' && t.status !== 'stopped' && t.status !== 'accepted' ? (
                <>
                  <Composer
                    placeholder={t.lead_expert ? `回复${t.lead_expert.name}，或补充要求…` : '补充要求…'}
                    tools={['materials', 'experts', 'skills']}
                    value={reply}
                    canSend={!!reply.trim()}
                    busy={send.submitting}
                    onValueChange={setReply}
                    onSend={(v) => send.submit(v.trim())}
                    onTool={() => navigate(`/tasks/${taskId}`)}
                    width="100%"
                  />
                  <ComposerContextBar lead={t.lead_expert?.name || '未指定'} model={t.model_mode === 'auto' ? '自动' : t.model_mode} />
                  {send.error && <InlineNotice icon="info" tone="error" text={apiMessage(send.error, '发送没有成功，请重试')} />}
                </>
              ) : (
                <InlineNotice
                  icon="message-square"
                  tone="muted"
                  text="你对这个任务只有查看权限"
                  action="打开任务"
                  onAction={() => navigate(`/tasks/${taskId}`)}
                />
              )}
            </div>
          )}
        </div>

        {taskId && (
          <aside className="sg-artdiff__rail" aria-label="本任务成果">
            <div className="sg-artdiff__railtabs">
              <Link className="sg-artdiff__railtab" href={`/tasks/${taskId}`}>
                进度
              </Link>
              <span className="sg-artdiff__railtab is-active">成果 {t?.counts.artifacts ?? siblings.data?.items.length ?? ''}</span>
              <Link className="sg-artdiff__railtab" href={`/tasks/${taskId}`}>
                资料 {t?.counts.materials ?? ''}
              </Link>
            </div>
            {summary && (
              <div className="sg-artdiff__summary">
                本任务新增 {summary.created} 个、修改 {summary.modified} 个成果
              </div>
            )}
            {siblings.status === 'error' && <InlineNotice icon="info" tone="error" text="成果列表暂时无法读取" action="重试" onAction={siblings.reload} />}
            {siblings.status === 'loading' && !siblings.data && <LoadingState rows={3} />}
            {siblings.data?.items.map((x) => {
              const xv = x.current_version;
              const modified = !!xv?.previous_number;
              const range = modified ? `v${xv!.previous_number} → v${xv!.number}` : `v${xv?.number ?? 1}`;
              const active = shown?.id === x.id;
              return (
                <div key={x.id} className={`sg-artdiff__change${active ? ' is-active' : ''}`}>
                  <Icon name={KIND_ICON[x.kind] ?? 'file'} size={14} />
                  <div>
                    <div className="sg-artdiff__changetitle">
                      <span className="truncate">{x.title}</span>
                      <Tag label={modified ? '修改' : '新增'} tone={modified ? 'warning' : 'success'} size={18} />
                    </div>
                    <div className="sg-artdiff__changemeta">
                      {range} · {xv?.author?.name ?? '系统'} · {timeShort(xv?.created_at ?? x.updated_at)}
                    </div>
                  </div>
                  {modified ? (
                    <button
                      type="button"
                      className="sg-artdiff__link"
                      onClick={() => {
                        setDismissed(false);
                        setDiffFor(x.id);
                      }}
                    >
                      查看差异
                    </button>
                  ) : (
                    <Link className="sg-artdiff__link" href={`/artifacts/${x.id}`}>
                      打开
                    </Link>
                  )}
                </div>
              );
            })}
            {siblings.data && (
              <div className="sg-artdiff__foot" style={{ padding: '12px 8px 0' }}>
                {a && dayTime(a.updated_at) ? '' : ''}
              </div>
            )}
          </aside>
        )}
      </div>
      {toast && <Toast tone={toast.tone} message={toast.text} onClose={() => setToast(null)} />}
    </div>
  );
}
