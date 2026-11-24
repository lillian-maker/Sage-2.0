import { useEffect, useMemo, useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useShell } from '../../app/AppShell';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { BackLink } from '../../components/BackLink/BackLink';
import { Composer } from '../../components/Composer/Composer';
import { ComposerContextBar } from '../../components/Composer/ComposerContextBar';
import { ConfirmDialog } from '../../components/ConfirmDialog/ConfirmDialog';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { Menu, MenuItem } from '../../components/Menu/MenuItem';
import { EmptyState, ErrorBanner, ForbiddenState, LoadingState, errorMessage } from '../../components/States/States';
import { AuthorizationBlock } from '../../features/approvals/AuthorizationBlock';
import { ReviewBlock } from '../../features/review/ReviewBlock';
import { DeliveryBlock } from '../../features/task/DeliveryBlock';
import { KickoffPanel } from '../../features/task/KickoffPanel';
import { ArtifactPanelPreview, OrchestrationPanel } from '../../features/task/PanelPreview';
import { PlanStepRow } from '../../features/task/PlanStepRow';
import { QuotaCard } from '../../features/task/QuotaCard';
import { SavePlaybookDialog } from '../../features/task/SavePlaybookDialog';
import { SideChatPanel } from '../../features/task/SideChatPanel';
import { SuggestionChip } from '../../features/task/SuggestionChip';
import { TaskConversation } from '../../features/task/TaskConversation';
import { TaskHeaderBar } from '../../features/task/TaskHeaderBar';
import { TaskSidePanel, type SideTab } from '../../features/task/TaskSidePanel';
import { dayLabel } from '../../features/inbox/format';
import { apiMessage, avatarOf, stepStatus, taskApi, taskStatusTag, type TaskDetail } from '../../features/task/model';
import './detail.css';

const LIVE = new Set(['running', 'awaiting_user', 'stuck', 'awaiting_kickoff']);
const MODEL: Record<string, string> = { auto: '自动', fast: '快速', deep: '深度' };
// The right panel keeps 进度 / 成果 / 资料 and at most three more panels, so the tab bar never scrolls past six.
const MAX_PANEL_TABS = 6;
const FIXED_PANEL_TABS = 3;
type PanelTab = { key: string; artifactId?: string; title?: string };
const STATUS_NOTE: Record<string, string> = { delivered: '已交付，等待验收', accepted: '已验收，当前版本已锁定', needs_revision: '已退回，等待修改' };
const versionLabel = (v?: { title: string; number: number } | null) => (v ? `${v.title} v${v.number}` : '');

function headerStatus(status: string): 'draft' | 'running' | 'review' {
  if (status === 'draft' || status === 'awaiting_kickoff') return 'draft';
  if (status === 'delivered') return 'review';
  return 'running';
}

export default function Screen({ params, query, hash, navigate }: ScreenProps) {
  const id = params.taskId ?? '';
  const shell = useShell();
  const [branch, setBranch] = useState<number | null>(null);
  const task = useApiQuery((s) => taskApi.detail(id, s), [id]);
  const msgs = useApiQuery((s) => taskApi.messages(id, 'main', s), [id]);
  const branchMsgs = useApiQuery(
    (s) => (branch == null ? Promise.resolve(null) : taskApi.messages(id, 'main', s, branch)),
    [id, branch],
  );
  const steps = useApiQuery((s) => taskApi.steps(id, s), [id]);
  const artifacts = useApiQuery((s) => taskApi.artifacts(id, s), [id]);
  const decisions = useApiQuery((s) => taskApi.decisions(s), [id]);
  const threads = useApiQuery((s) => taskApi.threads(id, s), [id]);
  const status = task.data?.status;
  // 04 开工确认卡 · 启动后: until the first step moves, the confirmed brief stays on the page read-only.
  const justStarted = status === 'running' && !!steps.data
    && steps.data.items.every((s) => s.status === 'queued' || s.status === 'missing_capability')
    && (msgs.data?.items ?? []).some((m) => m.kind === 'kickoff_card');
  const kickoff = useApiQuery(
    (s) => (status === 'awaiting_kickoff' || justStarted ? taskApi.kickoff(id, s) : Promise.resolve(null)),
    [id, status, justStarted],
  );
  const mine = (decisions.data?.items ?? []).filter((d) => d.target.task_id === id);
  const delivery = mine.find((d) => d.type === 'accept' && d.target.type === 'delivery');
  // While the task sits in the state a delivery decision put it in, that delivery stays on the page read-only.
  const decided = task.data?.status === 'accepted' || task.data?.status === 'needs_revision';
  const decidedCard = delivery || !decided
    ? null
    : [...(msgs.data?.items ?? [])].reverse().find((m) => m.card?.type === 'delivery' && (m.card.decision === 'accepted' || m.card.decision === 'returned'))?.card ?? null;
  const criteria = useApiQuery(
    (s) => (delivery || decidedCard ? taskApi.criteria(id, s) : Promise.resolve(null)),
    [id, delivery?.target.id, decidedCard?.id, decidedCard?.decision],
  );

  const reloadAll = () => {
    task.reload();
    msgs.reload();
    steps.reload();
    decisions.reload();
    artifacts.reload();
    threads.reload();
    shell.refresh();
  };
  useEffect(() => {
    if (!status || !LIVE.has(status)) return;
    const t = window.setInterval(() => {
      msgs.reload();
      steps.reload();
      task.reload();
    }, 10_000);
    return () => window.clearInterval(t);
  }, [status, msgs.reload, steps.reload, task.reload]);

  useEffect(() => {
    if (!hash) return;
    const el = document.getElementById(`task-${hash}`);
    el?.scrollIntoView({ block: 'center' });
  }, [hash, task.status, msgs.status, decisions.status, criteria.status, steps.status]);

  const [text, setText] = useState('');
  const [tab, setTab] = useState<SideTab>('progress');
  const [editing, setEditing] = useState<string | null>(null);
  const [more, setMore] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const [extraTabs, setExtraTabs] = useState<PanelTab[]>([]);
  const [activeExtra, setActiveExtra] = useState<string | null>(null);
  const [menuState, setMenuState] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);

  const send = useSubmit(async (body: string) => {
    await taskApi.send(id, body);
    setText('');
    msgs.reload();
  });
  const edit = useSubmit(async (mid: string, body: string) => {
    await taskApi.edit(id, mid, body);
    setEditing(null);
    if (hash === 'editing') navigate(`/tasks/${id}`, { replace: true });
    msgs.reload();
  });
  const control = useSubmit(async (fn: () => Promise<unknown>) => {
    await fn();
    reloadAll();
  });
  const openThread = useSubmit(async (mid: string) => {
    const r = await taskApi.openThread(id, mid);
    threads.reload();
    navigate(`/tasks/${id}?panel=side-chat&thread=${r.thread.id}`);
  });

  const messages = ((branch != null ? branchMsgs.data?.items : null) ?? msgs.data?.items ?? []).map((m) =>
    justStarted && m.kind === 'kickoff_card' ? { ...m, body: '任务已开始，进度将在右侧更新。' } : m,
  );
  const lastUser = [...messages].reverse().find((m) => m.author_kind === 'user');
  useEffect(() => {
    if (hash === 'editing' && lastUser) setEditing(lastUser.id);
  }, [hash, lastUser?.id]);

  const sideThreads = useMemo(() => {
    const map: Record<string, { id: string; count: number; expert: string }> = {};
    for (const t of threads.data?.items ?? []) {
      if (t.anchor?.message_id) map[t.anchor.message_id] = { id: t.id, count: t.message_count ?? 0, expert: t.expert?.name ?? '' };
    }
    return map;
  }, [threads.data]);

  if (task.status === 'error' && !task.data) {
    if (task.error.status === 404 || task.error.status === 403) {
      return (
        <div className="sg-taskpage__state">
          <ForbiddenState title="任务不存在或你没有访问权限" action={{ label: '回到进行中', onClick: () => navigate('/tasks/active') }} />
        </div>
      );
    }
    return (
      <div className="sg-taskpage__state">
        <ErrorBanner message={errorMessage(task.error, '任务暂时无法读取')} onRetry={task.reload} />
      </div>
    );
  }
  if (!task.data) return <div className="sg-taskpage__state"><LoadingState rows={5} /></div>;
  const t: TaskDetail = task.data;

  const lead = t.lead_expert?.name ?? '';
  const members = t.participants.map((p) => avatarOf(p.expert?.name ?? p.user?.display_name ?? '', p.expert?.avatar_ref));
  const canEdit = t.access === 'edit';
  const isOwner = t.owner?.is_me ?? false;
  const panel = query.get('panel');
  const threadId = query.get('thread');
  const thread = (threads.data?.items ?? []).find((x) => x.id === threadId) ?? (panel === 'side-chat' ? threads.data?.items[0] : undefined);
  const authIds = Array.from(
    new Set([
      ...(steps.data?.items ?? []).flatMap((s) => (s.authorization_id && s.status === 'awaiting_user' ? [s.authorization_id] : [])),
      ...mine.filter((d) => d.target.type === 'authorization').map((d) => d.target.id),
    ]),
  );
  const parallel = (steps.data?.items ?? []).filter((s) => s.status === 'running' || s.in_background);
  const deliveryMsg = messages.find((m) => m.card?.type === 'delivery' && m.card.id === delivery?.target.id);
  const materials = messages.flatMap((m) => m.attachments);
  const reviewId = query.get('review') ?? [...messages].reverse().find((m) => m.card?.type === 'review')?.card?.id ?? null;
  const paused = t.status === 'paused';
  const untouched = (steps.data?.items ?? []).every((s) => s.status === 'queued' || s.status === 'missing_capability');
  const beforeProgress = t.status === 'awaiting_kickoff' || (t.status === 'running' && !!steps.data && untouched);
  const pendingNote = t.status === 'awaiting_kickoff' ? '确认后显示任务进度' : beforeProgress ? '等待进度更新' : undefined;
  const menuOpen = menuState || panel === 'tabs';
  const closeMenu = () => {
    setMenuState(false);
    if (panel === 'tabs') {
      const q = new URLSearchParams(query);
      q.delete('panel');
      navigate(`/tasks/${id}${q.toString() ? '?' + q.toString() : ''}`, { replace: true });
    }
  };
  const full = FIXED_PANEL_TABS + extraTabs.length >= MAX_PANEL_TABS;
  const activeTab = extraTabs.find((x) => x.key === activeExtra) ?? null;
  const openExtra = (tab: PanelTab) => {
    if (!extraTabs.some((x) => x.key === tab.key)) {
      if (full) return;
      setExtraTabs((list) => [...list, tab]);
    }
    setActiveExtra(tab.key);
    closeMenu();
  };
  const closeHash = () => navigate(`/tasks/${id}${query.toString() ? '?' + query.toString() : ''}`, { replace: true });

  const sidePanel =
    panel === 'side-chat' ? (
      thread ? (
        <SideChatPanel
          key={thread.id}
          taskId={id}
          thread={thread}
          onClose={() => navigate(`/tasks/${id}`)}
          onSent={threads.reload}
          onBroughtBack={() => {
            msgs.reload();
            threads.reload();
            navigate(`/tasks/${id}`);
          }}
        />
      ) : (
        <aside className="sg-sidechat">
          <EmptyState icon="message-square" title="还没有侧聊" description="在专家消息下点“侧聊”，单独追问不会打断主线" />
        </aside>
      )
    ) : (
      <div className={`sg-taskpage__sidewrap${railOpen ? ' is-open' : ''}`}>
        {menuOpen && (
          <Menu label="打开面板">
            <MenuItem
              icon="layers"
              label="编排"
              {...(extraTabs.some((x) => x.key === 'orchestration') ? { hint: '已打开' } : {})}
              disabled={full && !extraTabs.some((x) => x.key === 'orchestration')}
              onSelect={() => openExtra({ key: 'orchestration' })}
            />
            <MenuItem icon="message-square" label="侧聊" onSelect={() => navigate(`/tasks/${id}?panel=side-chat`)} />
            {(artifacts.data?.items ?? []).map((a) => {
              const opened = extraTabs.some((x) => x.key === `preview:${a.id}`);
              return (
                <MenuItem
                  key={a.id}
                  icon="file"
                  label={`成果预览 · ${a.title}`}
                  {...(opened ? { hint: `${a.current_version ? `v${a.current_version.number} · ` : ''}预览中` } : {})}
                  disabled={full && !opened}
                  onSelect={() => openExtra({ key: `preview:${a.id}`, artifactId: a.id, title: a.title })}
                />
              );
            })}
            <MenuItem
              icon="book"
              label="资料预览"
              onSelect={() => {
                setActiveExtra(null);
                setTab('materials');
                closeMenu();
              }}
            />
            <p className="sg-menu__note">最多同时打开 {MAX_PANEL_TABS} 个标签</p>
          </Menu>
        )}
        <TaskSidePanel
          tab={tab}
          onTab={(next) => {
            setActiveExtra(null);
            setTab(next);
          }}
          steps={steps.data?.items}
          {...(pendingNote ? { pendingNote } : {})}
          {...(STATUS_NOTE[t.status] ? { statusNote: STATUS_NOTE[t.status]! } : {})}
          artifacts={artifacts.data?.items}
          materials={materials}
          participants={t.participants}
          counts={{ artifacts: t.counts.artifacts, materials: t.counts.materials }}
          onOpenArtifact={(aid) => {
            setRailOpen(false);
            navigate(`/artifacts/${aid}`);
          }}
          extraTabs={extraTabs.map((x) => ({
            key: x.key,
            label: x.key === 'orchestration' ? '编排' : extraTabs.filter((y) => y.artifactId).length > 1 ? `预览 · ${x.title}` : '预览',
          }))}
          activeExtra={activeExtra}
          onExtra={setActiveExtra}
          onCloseExtra={(key) => {
            setExtraTabs((list) => list.filter((x) => x.key !== key));
            setActiveExtra(null);
          }}
          onOpenMenu={() => (menuOpen ? closeMenu() : setMenuState(true))}
          extraContent={
            activeTab?.artifactId ? (
              <ArtifactPanelPreview artifactId={activeTab.artifactId} onOpen={() => navigate(`/artifacts/${activeTab.artifactId}`)} />
            ) : activeTab ? (
              <OrchestrationPanel steps={steps.data?.items} onOpen={() => navigate(`/tasks/${id}/orchestration`)} />
            ) : null
          }
        />
      </div>
    );

  return (
    <div className="sg-taskpage">
      <div className="sg-taskpage__main">
        <div className="sg-taskpage__header">
          <TaskHeaderBar
            title={t.title}
            status={headerStatus(t.status)}
            {...(t.status === 'delivered' && t.pending_delivery === null
              ? { statusTag: { label: '待你确认草稿', tone: 'warning' as const, size: 22 as const } }
              : taskStatusTag(t.status) ? { statusTag: taskStatusTag(t.status)! } : {})}
            {...(t.workspace ? { workspaceTag: { label: t.workspace.name, tone: 'neutral', size: 22, icon: 'folder' } } : {})}
            members={members}
            actions={['orchestrate']}
            onOrchestrate={() => navigate(`/tasks/${id}/orchestration`)}
            onMore={() => setMore((m) => !m)}
            onNotifications={() => navigate('/notifications')}
            railToggle={{ label: '进度与成果', onClick: () => setRailOpen(true) }}
          />
          {more && (
            <div className="sg-taskpage__more">
              <Menu label="任务操作">
                {paused ? (
                  <MenuItem icon="refresh" label="继续任务" disabled={!isOwner} onSelect={() => { setMore(false); void control.submit(() => taskApi.resume(id)); }} />
                ) : (
                  <MenuItem icon="clock" label="暂停任务" disabled={!isOwner || !LIVE.has(t.status)} onSelect={() => { setMore(false); void control.submit(() => taskApi.pause(id)); }} />
                )}
                <MenuItem icon="layers" label="存为 Playbook" disabled={t.status !== 'accepted'} onSelect={() => { setMore(false); navigate(`/tasks/${id}#save-playbook`); }} />
                <MenuItem icon="message-square" label="侧聊" onSelect={() => { setMore(false); navigate(`/tasks/${id}?panel=side-chat`); }} />
                <MenuItem icon="x" label="停止任务" danger disabled={!isOwner || t.status === 'stopped' || t.status === 'accepted'} onSelect={() => { setMore(false); setConfirmStop(true); }} />
              </Menu>
            </div>
          )}
        </div>
        <div className="sg-taskpage__scroll">
          <div className="sg-taskpage__column">
            {query.get('from') === 'approval' && (
              <BackLink label="返回审批" size={30} onBack={() => navigate('/inbox?filter=decide')} />
            )}
            {query.get('from') === 'search' && <BackLink label="返回搜索" size={30} onBack={shell.openSearch} />}
            {(msgs.status === 'error' || steps.status === 'error' || decisions.status === 'error') && (
              <ErrorBanner
                message={errorMessage(msgs.error ?? steps.error ?? decisions.error, '部分内容暂时无法读取')}
                onRetry={() => {
                  msgs.reload();
                  steps.reload();
                  decisions.reload();
                }}
              />
            )}
            {control.error && <InlineNotice icon="info" tone="error" text={apiMessage(control.error, '操作没有成功，请重试')} />}
            {t.source_automation_run && (
              <InlineNotice icon="clock" tone="muted" text={`由自动化“${t.source_automation_run.automation_name}”发起`} />
            )}
            {t.status === 'rejected' && (
              <section className="sg-taskpage__rejected">
                <span className="sg-section-label">任务概览</span>
                {t.source_automation_run && (
                  <div className="sg-taskpage__rejected-source">
                    <div>{`来源：${t.source_automation_run.automation_name} · ${dayLabel(t.source_automation_run.scheduled_for)}`}</div>
                    {t.scope_text && <div>{t.scope_text}</div>}
                  </div>
                )}
                {(steps.data?.items ?? []).length > 0 && (
                  <div className="sg-taskpage__rejected-steps">
                    {(steps.data?.items ?? []).map((s) => (
                      <div key={s.id}>{`${s.status === 'done' ? '已完成' : s.status === 'queued' ? '排队中' : '已拒绝'} · ${s.title}`}</div>
                    ))}
                  </div>
                )}
                {(() => {
                  const auth = (t.authorizations ?? []).find((a) => a.decision === 'rejected');
                  const estimated = auth && auth.estimate_state === 'estimated' && auth.estimated_credits != null;
                  return (
                    <div className="sg-taskpage__rejected-estimate">
                      {estimated ? `约 ${auth.estimated_credits} Credits（估算）` : '拒绝时范围、预计消耗和有效期尚未补充'}
                    </div>
                  );
                })()}
                <button type="button" className="sg-conv__link" onClick={() => navigate(`/tasks/${id}/orchestration`)}>查看处理结果</button>
              </section>
            )}
            {t.source_inbox_item && (
              <section className="sg-taskpage__origin">
                <div className="sg-taskpage__origin-row">
                  <span>{`来自收件 · ${t.source_inbox_item.source_label} · ${dayLabel(t.source_inbox_item.received_at)}`}</span>
                  <button type="button" className="sg-conv__link" onClick={() => navigate(`/inbox?item=${encodeURIComponent(t.source_inbox_item!.id)}`)}>
                    查看原消息
                  </button>
                </div>
                <p className="sg-taskpage__origin-text">
                  {t.source_inbox_item.original_text}
                  {t.source_inbox_item.translated_text && <><br />译：{t.source_inbox_item.translated_text}</>}
                </p>
              </section>
            )}
            {msgs.status === 'loading' && !msgs.data && <LoadingState rows={3} />}
            {msgs.data && messages.length === 0 && t.status !== 'awaiting_kickoff' && (
              t.status === 'rejected'
                ? <EmptyState icon="message-square" title="任务对话内容待补充。" />
                : <EmptyState icon="message-square" title="还没有对话" description="主责专家开工后，进展会显示在这里" />
            )}
            <TaskConversation
              messages={messages}
              editingId={editing}
              editBusy={edit.submitting}
              {...(canEdit && t.status !== 'accepted' && t.status !== 'stopped' ? { onEdit: setEditing } : {})}
              onCancelEdit={() => {
                setEditing(null);
                if (hash === 'editing') closeHash();
              }}
              onResend={(mid, body) => edit.submit(mid, body)}
              summaryOpen={hash === 'summary'}
              {...(canEdit ? { onSideChat: (mid: string) => (sideThreads[mid] ? navigate(`/tasks/${id}?panel=side-chat&thread=${sideThreads[mid].id}`) : openThread.submit(mid)) } : {})}
              onBranch={(i) => setBranch(i)}
              {...(canEdit
                ? {
                    onImportant: (mid: string, v: boolean) => void taskApi.important(id, mid, v).then(msgs.reload, msgs.reload),
                    onFork: (mid: string) => navigate(`/tasks/new?forkFrom=${encodeURIComponent(mid)}`),
                  }
                : {})}
              onOpenStep={(sid) => navigate(`/tasks/${id}/orchestration?step=${sid}`)}
              sideThreads={sideThreads}
              pinFirstUser={beforeProgress}
            />
            {edit.error && <InlineNotice icon="info" tone="error" text={apiMessage(edit.error, '重新发送没有成功')} />}
            {openThread.error && <InlineNotice icon="info" tone="error" text={apiMessage(openThread.error, '侧聊暂时无法打开')} />}

            {(t.status === 'awaiting_kickoff' || justStarted) && kickoff.data && (
              <div id="task-confirm">
                <KickoffPanel
                  kickoff={kickoff.data}
                  onStarted={
                    // Opened from 新建任务 (/tasks/new?task=…#confirm): starting lands on the task page, cancelling returns to 新建任务.
                    hash === 'confirm'
                      ? () => {
                          shell.refresh();
                          navigate(`/tasks/${id}`);
                        }
                      : reloadAll
                  }
                  onCancel={() => navigate(hash === 'confirm' ? '/tasks/new' : '/tasks/active')}
                  onChanged={kickoff.reload}
                  onInstall={(eid) => navigate(`/extensions/${eid}`)}
                />
              </div>
            )}
            {kickoff.status === 'error' && <ErrorBanner message={errorMessage(kickoff.error, '开工确认暂时无法读取')} onRetry={kickoff.reload} />}

            {hash === 'parallel' && parallel.length > 0 && (
              <section id="task-parallel" className="sg-taskpage__parallel">
                <h3 className="sg-section-label">同时进行 · {parallel.length}</h3>
                {parallel.map((s) => (
                  <PlanStepRow
                    key={s.id}
                    layout="table"
                    status={stepStatus(s.status)}
                    title={s.title}
                    {...(s.activity ? { meta: s.activity } : {})}
                    {...(s.expert ? { expert: avatarOf(s.expert.name, s.expert.avatar_ref) } : {})}
                    {...(s.in_background ? { variable: { label: '后台', tone: 'neutral' as const, size: 18 as const } } : {})}
                  />
                ))}
              </section>
            )}

            {authIds.map((aid) => (
              <div key={aid} id={hash === 'scope' ? 'task-scope' : undefined}>
                <AuthorizationBlock id={aid} scopeMenu={hash === 'scope'} onDecided={() => { steps.reload(); decisions.reload(); shell.refresh(); }} />
              </div>
            ))}

            {delivery && (
              <div id="task-acceptance">
                <DeliveryBlock
                  key={delivery.target.id}
                  taskId={id}
                  deliveryId={delivery.target.id}
                  artifactLabel={versionLabel(deliveryMsg?.card?.artifact_version) || delivery.title}
                  criteria={criteria.data?.items ?? []}
                  onDone={reloadAll}
                />
              </div>
            )}
            {!delivery && decidedCard && (
              <div id="task-acceptance">
                <DeliveryBlock
                  key={decidedCard.id}
                  taskId={id}
                  deliveryId={decidedCard.id}
                  artifactLabel={versionLabel(decidedCard.artifact_version)}
                  criteria={criteria.data?.items ?? []}
                  decided={{ decision: decidedCard.decision as 'accepted' | 'returned', reason: decidedCard.return_reason ?? null }}
                  onDone={reloadAll}
                />
              </div>
            )}
            {!delivery && !decidedCard && hash === 'acceptance' && <InlineNotice icon="info" tone="muted" text="当前没有待你验收的交付" />}

            {t.pause_reason === 'quota_exhausted' && (
              <div id="task-quota-exhausted">
                <QuotaCard task={t} onResumed={reloadAll} onUsage={() => navigate('/usage')} />
              </div>
            )}

            {reviewId ? (
              <div id="task-review-card">
                <ReviewBlock id={reviewId} onOpenArtifact={(aid) => navigate(`/artifacts/${aid}`)} onOpenVersions={(aid) => navigate(`/artifacts/${aid}/versions`)} />
              </div>
            ) : (
              hash === 'review-card' && <InlineNotice icon="info" tone="muted" text="这个任务暂时没有可读取的内容审核结果" />
            )}
          </div>
        </div>
        <div className="sg-taskpage__composer">
          {t.suggestions.length > 0 && (
            <div className="sg-taskpage__suggest">
              {t.suggestions.map((s) => (
                <SuggestionChip key={s.id} label={s.text} {...(s.requires_authorization ? { badge: '需授权' } : {})} onSelect={() => setText(s.text)} />
              ))}
            </div>
          )}
          {canEdit ? (
            <>
              <Composer
                placeholder={panel === 'side-chat' && thread ? '回复主线…' : lead ? `回复${lead}，或补充要求…` : '补充要求…'}
                tools={['materials', 'experts', 'skills']}
                value={text}
                canSend={!!text.trim() && t.status !== 'stopped'}
                busy={send.submitting}
                onValueChange={setText}
                onSend={(v) => send.submit(v.trim())}
                width={720}
              />
              <ComposerContextBar lead={lead || '未指定'} model={MODEL[t.model_mode] ?? t.model_mode} />
              {send.error && <InlineNotice icon="info" tone="error" text={apiMessage(send.error, '发送没有成功，请重试')} />}
            </>
          ) : (
            <InlineNotice icon="info" tone="muted" text="你对这个任务只有查看权限" />
          )}
        </div>
      </div>
      {sidePanel}
      {railOpen && <div className="sg-railbackdrop" onClick={() => setRailOpen(false)} />}
      {hash === 'save-playbook' && <SavePlaybookDialog task={t} steps={steps.data?.items ?? []} onClose={closeHash} />}
      {confirmStop && (
        <ConfirmDialog
          title="停止这个任务？"
          message="停止后专家不再继续执行，已生成的成果会保留。"
          confirmLabel="停止任务"
          cancelLabel="取消"
          tone="warning"
          busy={control.submitting}
          onCancel={() => setConfirmStop(false)}
          onConfirm={async () => {
            await control.submit(() => taskApi.stop(id));
            setConfirmStop(false);
          }}
        />
      )}
    </div>
  );
}

