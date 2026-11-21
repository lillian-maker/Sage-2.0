import { useEffect, useState } from 'react';
import { useShell } from '../../app/AppShell';
import { Link, type ScreenProps } from '../../app/router';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Avatar, avatarProps } from '../../components/Avatar/Avatar';
import { Composer } from '../../components/Composer/Composer';
import { ConfirmDialog } from '../../components/ConfirmDialog/ConfirmDialog';
import { Button } from '../../components/Button/Button';
import { HeaderMemberActions } from '../../components/HeaderMemberActions/HeaderMemberActions';
import { Icon } from '../../components/Icon/Icon';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { Menu, MenuItem } from '../../components/Menu/MenuItem';
import { Select } from '../../components/Select/Select';
import { EmptyState, ErrorBanner, LoadingState, Toast, errorMessage } from '../../components/States/States';
import { Tab } from '../../components/Tabs/Tab';
import { Tag } from '../../components/Tag/Tag';
import { LocalIndexPanel } from '../../features/desktop/LocalIndexPanel';
import { knowledgeApi } from '../../features/knowledge/api';
import { apiMessage, taskApi } from '../../features/task/model';
import { WorkspaceJoinState } from '../../features/workspaces/JoinState';
import { SectionTitle, WorkspaceTaskRow } from '../../features/workspaces/parts';
import '../../features/workspaces/workspaces.css';
import { accountLabel, wsApi, type Role, type TaskCounts } from '../../features/workspaces/model';

type Group = 'all' | 'needs_me' | 'running' | 'done';
const GROUPS: Array<{ key: Group; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'needs_me', label: '待我处理' },
  { key: 'running', label: '进行中' },
  { key: 'done', label: '已完成' },
];
const SIDE_ROLE: Record<Role, string> = { owner: '负责人', editor: '可编辑', viewer: '可查看', guest: '访客' };

function useDraft(key: string): [string, (v: string) => void] {
  const [value, setValue] = useState(() => localStorage.getItem(key) ?? '');
  useEffect(() => setValue(localStorage.getItem(key) ?? ''), [key]);
  const set = (v: string) => {
    setValue(v);
    if (v) localStorage.setItem(key, v);
    else localStorage.removeItem(key);
  };
  return [value, set];
}

export default function Screen({ params, query, navigate }: ScreenProps) {
  const id = params.workspaceId ?? '';
  const shell = useShell();
  const raw = query.get('group') as Group | null;
  const group: Group = raw && GROUPS.some((g) => g.key === raw) ? raw : 'all';

  const ws = useApiQuery((s) => wsApi.get(id, s), [id]);
  const tasks = useApiQuery((s) => wsApi.tasks({ workspace: id, status_group: group }, s), [id, group]);
  const docs = useApiQuery((s) => wsApi.documents(id, s), [id]);
  const members = useApiQuery((s) => wsApi.members(id, s), [id]);
  const bases = useApiQuery((s) => knowledgeApi.bases({ scope: 'workspace' }, s), []);

  const [draft, setDraft] = useDraft(`sage-workspace-draft:${id}`);
  const [menuOpen, setMenuOpen] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const send = useSubmit(async (body: string) => {
    const r = await taskApi.create({ body, attached_document_ids: [], mentioned_expert_ids: [], requested_skill_ids: [], model_mode: 'auto', workspace_id: id });
    setDraft('');
    shell.refresh();
    if (r.task.complexity === 'simple' || r.task.status !== 'awaiting_kickoff') navigate(`/tasks/${r.task.id}`);
    else navigate(`/tasks/new?task=${r.task.id}#confirm`);
  });
  const readAll = useSubmit(async () => {
    await wsApi.readAll(id);
    shell.refresh();
    ws.reload();
    tasks.reload();
  });
  const remove = useSubmit(async () => {
    await wsApi.remove(id);
    shell.refresh();
    navigate('/tasks', { replace: true });
  });

  useEffect(() => {
    const err = send.error ?? readAll.error ?? remove.error;
    if (err) setToast(apiMessage(err, '操作失败，请重试'));
  }, [send.error, readAll.error, remove.error]);

  if (ws.status === 'error' && (ws.error.status === 403 || ws.error.status === 404)) {
    return (
      <div className="sg-ws">
        <div className="sg-ws__main">
          <WorkspaceJoinState id={id} onBack={() => navigate('/tasks')} />
        </div>
      </div>
    );
  }

  const w = ws.data;
  const counts: TaskCounts | undefined = tasks.data?.counts;
  const groupCount = (g: Group) => (counts ? (g === 'all' ? counts.all : counts[g]) : undefined);
  const enc = encodeURIComponent(id);
  const activeMembers = members.data?.items.filter((m) => m.state === 'active') ?? [];
  // 查看全部 opens the workspace's main knowledge base (the one holding most of its documents).
  const mainBase = (bases.data?.items ?? [])
    .filter((b) => b.workspace?.id === id)
    .sort((a, b) => b.document_count - a.document_count)[0];
  const allDocsHref = mainBase ? `/knowledge/${encodeURIComponent(mainBase.id)}` : `/knowledge?workspace=${enc}`;
  // The workspace document list does not carry the knowledge base, so the row resolves it from the document detail.
  const openDoc = (docId: string) =>
    void knowledgeApi.document(docId).then(
      (d) => navigate(`/knowledge/${encodeURIComponent(d.knowledge_base.id)}/docs/${encodeURIComponent(docId)}`),
      () => setToast('资料暂时无法打开，请重试'),
    );

  return (
    <div className="sg-ws">
      <div className="sg-ws__main">
        {ws.status === 'error' && <ErrorBanner message={errorMessage(ws.error, '工作区暂时无法读取')} onRetry={ws.reload} />}
        <div className="sg-ws__eyebrow">工作区</div>
        <div className="sg-ws__head">
          <h1>{w?.name ?? ''}</h1>
          {w && <Tag label={accountLabel(w.account_kind)} tone="neutral" size={20} />}
          <div className="sg-ws__headend">
            <Button variant="secondary" size={32} icon="panel-left" label="资料与成员" className="sg-ws__railtoggle" onClick={() => setRailOpen(true)} />
            {w && (
              <HeaderMemberActions
                members={w.members_preview.map((m) => avatarProps(m.display_name, m.avatar_name))}
                onInvite={() => navigate(`/workspaces/${enc}/members?invite=1`)}
                onMore={() => setMenuOpen((o) => !o)}
              />
            )}
            {menuOpen && w && (
              <div className="sg-ws__popover" onMouseLeave={() => setMenuOpen(false)}>
                <Menu label="工作区操作">
                  <MenuItem
                    icon="check"
                    label="全部标为已读"
                    disabled={w.unread_count === 0 || readAll.submitting}
                    onSelect={() => {
                      setMenuOpen(false);
                      void readAll.submit();
                    }}
                  />
                  <MenuItem icon="book" label="工作区规则" onSelect={() => navigate(`/workspaces/${enc}/rules`)} />
                  <MenuItem icon="users" label="成员与权限" onSelect={() => navigate(`/workspaces/${enc}/members`)} />
                  <MenuItem icon="folder-plus" label="新建工作区" onSelect={() => navigate(`/workspaces/new?from=${enc}`)} />
                  {w.can_manage && (
                    <MenuItem
                      icon="x"
                      label="删除工作区"
                      danger
                      onSelect={() => {
                        setMenuOpen(false);
                        setConfirmDelete(true);
                      }}
                    />
                  )}
                </Menu>
              </div>
            )}
          </div>
        </div>
        {w?.description && <div className="sg-ws__desc">{w.description}</div>}

        <div className="sg-ws__composer">
          <Composer
            placeholder={w ? `在“${w.name}”里交办一件事…` : '交办一件事…'}
            value={draft}
            tools={['materials', 'experts', 'skills']}
            canSend={!!draft.trim() && !send.submitting}
            busy={send.submitting}
            {...(w ? { contextTag: { label: w.name, tone: 'neutral' as const, size: 24 as const, icon: 'folder' as const } } : {})}
            onValueChange={setDraft}
            onSend={(v) => v.trim() && void send.submit(v.trim())}
            onTool={(tool) => navigate(`/tasks/new?workspace=${enc}&picker=${tool}`)}
          />
        </div>

        <div className="sg-ws__bar">
          <div className="sg-tabs" role="tablist" aria-label="任务分组">
            {GROUPS.map((g) => {
              const n = groupCount(g.key);
              return (
                <Tab
                  key={g.key}
                  size={37}
                  label={n === undefined ? g.label : `${g.label} ${n}`}
                  selected={g.key === group}
                  onSelect={() => navigate(g.key === 'all' ? `/workspaces/${enc}` : `/workspaces/${enc}?group=${g.key}`, { replace: true })}
                />
              );
            })}
          </div>
          <Select size={26} label="排序" value="updated" options={[{ value: 'updated', label: '排序：最近更新' }]} />
        </div>
        <div className="sg-ws__list">
          {tasks.status === 'error' && (
            <div className="sg-ws__state">
              <ErrorBanner message={errorMessage(tasks.error, '任务暂时无法读取')} onRetry={tasks.reload} />
            </div>
          )}
          {tasks.status === 'loading' && !tasks.data && <LoadingState rows={4} />}
          {tasks.data && tasks.data.items.length === 0 && (
            <EmptyState
              icon="square-check"
              title={group === 'all' ? '这个工作区还没有任务' : '这个分组没有任务'}
              {...(group === 'all'
                ? {
                    description: '在上方输入框里交办第一件事',
                    action: { label: '聚焦输入框', onClick: () => document.querySelector<HTMLTextAreaElement>('.sg-ws__composer textarea')?.focus() },
                  }
                : {})}
            />
          )}
          {tasks.data?.items.map((t) => <WorkspaceTaskRow key={t.id} task={t} onOpen={() => navigate(`/tasks/${t.id}`)} />)}
        </div>
      </div>

      <aside className={`sg-ws__side${railOpen ? ' is-open' : ''}`} aria-label="工作区资料与成员">
        <section>
          <SectionTitle
            title={docs.data ? `资料 · ${docs.data.total}` : '资料'}
            action={{ label: '添加资料', icon: 'plus', href: `/knowledge?workspace=${enc}` }}
          />
          {docs.status === 'error' && <InlineNotice icon="info" tone="error" text="资料暂时无法读取" action="重试" onAction={docs.reload} />}
          {docs.data && docs.data.items.length === 0 && <InlineNotice icon="info" tone="muted" text="还没有资料" />}
          {docs.data?.items.slice(0, 4).map((d) => {
            const ready = d.index_state === 'indexed' || d.index_state === 'ready';
            return (
              <button key={d.id} type="button" className="sg-wsside__row" onClick={() => openDoc(d.id)}>
                <Icon name="file" size={14} />
                <span className="truncate">{d.title}</span>
                <span className={`sg-wsside__meta${ready ? ' is-ready' : ''}`}>{ready ? '就绪' : d.index_state === 'failed' ? '索引失败' : '索引中'}</span>
              </button>
            );
          })}
          {docs.data && docs.data.total > 4 && (
            <Link className="sg-wsside__more" href={allDocsHref}>
              查看全部 {docs.data.total} 项
            </Link>
          )}
        </section>

        <LocalIndexPanel workspaceId={id} />

        <section>
          <SectionTitle
            title={members.data ? `成员与权限 · ${members.data.counts.active}` : '成员与权限'}
            action={{ label: '管理', href: `/workspaces/${enc}/members` }}
          />
          {members.status === 'error' && <InlineNotice icon="info" tone="error" text="成员暂时无法读取" action="重试" onAction={members.reload} />}
          {activeMembers.map((m) => {
            const name = m.user?.display_name ?? m.email;
            return (
              <div key={m.id} className="sg-wsside__row">
                <span className="sg-wsside__avatar">
                  <Avatar size={24} {...avatarProps(name, m.user?.avatar_name)} />
                </span>
                <span className="truncate">{name}</span>
                <span className="sg-wsside__meta">{SIDE_ROLE[m.role]}</span>
              </div>
            );
          })}
        </section>
      </aside>

      {railOpen && <div className="sg-railbackdrop" onClick={() => setRailOpen(false)} />}
      {confirmDelete && w && (
        <ConfirmDialog
          title={`删除“${w.name}”？`}
          message="工作区内的任务会变为独立任务，资料与规则会一并删除；此操作无法撤销。"
          confirmLabel="删除工作区"
          cancelLabel="取消"
          tone="warning"
          busy={remove.submitting}
          onCancel={() => setConfirmDelete(false)}
          onConfirm={() => void remove.submit()}
        />
      )}
      {toast && <Toast tone="failure" message={toast} onClose={() => setToast(null)} />}
    </div>
  );
}


