import { useEffect, useState } from 'react';
import { loadSession, request, type AuthSession } from '../../api/client';
import { account, auth, billing, tasks, workspaces } from '../../api/endpoints';
import { navigate } from '../../app/router';
import type { SidebarWorkspace } from '../../components/AppSidebar/AppSidebar';
import { Button } from '../../components/Button/Button';
import { ConfirmDialog } from '../../components/ConfirmDialog/ConfirmDialog';
import { Menu, MenuDivider, MenuItem, Popover } from '../../components/Menu/MenuItem';
import { errorMessage } from '../../components/States/States';
import { TextField } from '../../components/TextField/TextField';
import { checkForUpdates } from '../desktop/ShellDesktop';
import { useDesktopInfo } from '../desktop/bridge';
import { currentSubscription, getSubscriptions } from '../billing/api';
import { planName } from '../billing/labels';
import './shell.css';
import { Modal } from '../../components/Modal/Modal';

type Point = { x: number; y: number };
export type ShellMenuState =
  | { kind: 'account'; at: Point }
  | { kind: 'workspace'; id: string; at: Point; mode: 'more' | 'context' }
  | { kind: 'task'; taskId: string; workspaceId: string; at: Point }
  | null;

export interface ToastOptions {
  tone: 'undo' | 'failure';
  message: string;
  detail?: string;
  actionLabel?: string;
  onAction?: () => void;
  variant?: 'card';
}

interface Props {
  menu: ShellMenuState;
  onClose: () => void;
  session: AuthSession | null;
  workspaces: SidebarWorkspace[];
  toast: (t: ToastOptions) => void;
  refresh: () => void;
  /** Opens 编辑工作区 (board 24 ③); falls back to the workspace page. */
  onEditWorkspace?: (id: string) => void;
}

type Dialog =
  | { kind: 'archive-completed'; id: string; name: string; count?: number }
  | { kind: 'remove-workspace'; id: string; name: string }
  | { kind: 'rename'; taskId: string; title: string }
  | null;

function useAccountCard(open: boolean) {
  const [card, setCard] = useState<{ email?: string; plan?: string }>({});
  useEffect(() => {
    if (!open) return;
    const ctl = new AbortController();
    account.getAccountProfile(undefined, ctl.signal).then(
      (p) => setCard((c) => ({ ...c, ...(typeof p.email === 'string' && p.email ? { email: p.email } : {}) })),
      () => undefined,
    );
    getSubscriptions(ctl.signal).then(
      (s) => {
        const sub = currentSubscription(s.items);
        setCard((c) => ({ ...c, plan: sub ? planName(sub.plan_code) : undefined }));
      },
      () => undefined,
    );
    return () => ctl.abort();
  }, [open]);
  return card;
}

/** Sidebar menus (boards 24 ② ④ ⑥ and 25 ③) and the dialogs they open. */
export function ShellMenus({ menu, onClose, session, workspaces: list, toast, refresh, onEditWorkspace }: Props) {
  const desktopInfo = useDesktopInfo();
  const [dialog, setDialog] = useState<Dialog>(null);
  const [busy, setBusy] = useState(false);
  const [submenu, setSubmenu] = useState<'accounts' | 'move' | null>(null);
  const card = useAccountCard(menu?.kind === 'account');
  useEffect(() => setSubmenu(null), [menu]);

  const run = async (fn: () => Promise<unknown>, failure: string, after?: () => void): Promise<void> => {
    onClose();
    try {
      await fn();
      refresh();
      after?.();
    } catch (err) {
      toast({ tone: 'failure', message: errorMessage(err, failure), actionLabel: '重试', onAction: () => void run(fn, failure, after) });
    }
  };

  const archiveTask = (taskId: string) =>
    run(
      () => request('POST', `app/tasks/${encodeURIComponent(taskId)}/archive`, { body: {} }),
      '归档失败，任务保持原状',
      () =>
        toast({
          tone: 'undo',
          message: '已归档任务',
          actionLabel: '撤销',
          onAction: () =>
            void request('POST', `app/tasks/${encodeURIComponent(taskId)}/unarchive`, { body: {} }).then(refresh, (err: unknown) =>
              toast({ tone: 'failure', message: errorMessage(err, '撤销失败，请重试') }),
            ),
        }),
    );

  // The archive endpoint refuses a stale count (STATE_CONFLICT), so the dialog asks for the live number first.
  const openArchiveCompleted = (id: string, name: string) => {
    setDialog({ kind: 'archive-completed', id, name });
    request<{ counts?: Record<string, number> }>('GET', 'app/tasks', { query: { workspace: id, status_group: 'done' } }).then(
      (res) => setDialog((d) => (d?.kind === 'archive-completed' && d.id === id ? { ...d, count: res.counts?.done ?? 0 } : d)),
      (err: unknown) => {
        setDialog(null);
        toast({ tone: 'failure', message: errorMessage(err, '已完成任务暂时无法读取，请重试') });
      },
    );
  };

  const confirmDialog = async () => {
    if (!dialog) return;
    setBusy(true);
    try {
      if (dialog.kind === 'archive-completed') {
        const res = (await workspaces.postWorkspacesArchiveCompleted(dialog.id, { expected_count: dialog.count ?? 0 })) as { archived_count?: number };
        toast({ tone: 'undo', message: `已归档 ${res.archived_count ?? 0} 个已完成任务` });
      } else if (dialog.kind === 'remove-workspace') {
        await workspaces.deleteWorkspacesById(dialog.id);
        if (window.location.pathname.startsWith(`/workspaces/${dialog.id}`)) navigate('/tasks');
      } else {
        const title = dialog.title.trim();
        if (!title) return;
        await tasks.patchTasksById(dialog.taskId, { title });
      }
      setDialog(null);
      refresh();
    } catch (err) {
      toast({ tone: 'failure', message: errorMessage(err, '操作失败，请重试') });
    } finally {
      setBusy(false);
    }
  };

  let popover = null;
  if (menu?.kind === 'account') {
    const user = session?.user;
    const accounts = session?.accounts ?? [];
    popover = (
      <Popover at={menu.at} placement="above" width={240} onClose={onClose}>
        <Menu label="账号菜单">
          <div className="sg-accountcard">
            <div className="sg-accountcard__line">
              <span className="sg-accountcard__name truncate">{user?.display_name || user?.masked_identifier}</span>
              {card.plan && <span className="sg-accountcard__plan">{card.plan}</span>}
            </div>
            <div className="sg-accountcard__email truncate">{card.email || user?.masked_identifier}</div>
          </div>
          <MenuDivider />
          {accounts.length > 1 && (
            <MenuItem icon="users" label="切换账号 / 企业" submenu onSelect={() => setSubmenu((s) => (s === 'accounts' ? null : 'accounts'))} />
          )}
          {submenu === 'accounts' &&
            accounts.map((a) => (
              <MenuItem
                key={a.id}
                icon={a.kind === 'business' ? 'users' : 'contact'}
                label={a.name}
                checked={a.id === session?.active_account_id}
                onSelect={() =>
                  a.id === session?.active_account_id
                    ? onClose()
                    : void run(
                        async () => {
                          await billing.postBillingAccountsSelect({ account_id: a.id });
                          await loadSession(true);
                        },
                        '切换账号失败，请重试',
                        () => navigate('/tasks/new'),
                      )
                }
              />
            ))}
          <MenuItem icon="settings" label="设置" hint="⌘," onSelect={() => (onClose(), navigate('/settings/profile'))} />
          <MenuItem icon="info" label="帮助与反馈" onSelect={() => (onClose(), navigate('/settings/help'))} />
          {desktopInfo ? (
            <MenuItem icon="download" label="检查更新" hint={`v${desktopInfo.version}`} onSelect={() => (onClose(), void checkForUpdates(toast))} />
          ) : (
            <MenuItem icon="download" label="检查更新" hint="桌面端提供" disabled />
          )}
          <MenuDivider />
          <MenuItem
            icon="x"
            label="退出登录"
            onSelect={() =>
              void run(
                async () => {
                  await auth.postAuthLogout({});
                  await loadSession(true).catch(() => undefined);
                },
                '退出登录失败，请重试',
                () => navigate('/login', { replace: true }),
              )
            }
          />
        </Menu>
      </Popover>
    );
  } else if (menu?.kind === 'workspace') {
    const w = list.find((x) => x.id === menu.id);
    const id = menu.id;
    const name = w?.name ?? '';
    popover = (
      <Popover at={menu.at} width={240} onClose={onClose}>
        <Menu label={`工作区 ${name}`}>
          {menu.mode === 'context' && (
            <>
              <MenuItem icon="folder" label="打开工作区" onSelect={() => (onClose(), navigate(`/workspaces/${id}`))} />
              <MenuItem icon="square-pen" label="在此新建任务" hint="⌘⇧N" onSelect={() => (onClose(), navigate(`/tasks/new?workspace=${encodeURIComponent(id)}`))} />
              <MenuDivider />
            </>
          )}
          <MenuItem
            icon="pin"
            label={w?.pinned ? '取消置顶' : '置顶'}
            onSelect={() => void run(() => request(w?.pinned ? 'DELETE' : 'POST', `app/workspaces/${encodeURIComponent(id)}/pin`, { body: {} }), '置顶失败，请重试')}
          />
          <MenuItem icon="settings" label="编辑工作区" onSelect={() => (onClose(), onEditWorkspace ? onEditWorkspace(id) : navigate(`/workspaces/${id}`))} />
          <MenuItem icon="check" label="全部标为已读" onSelect={() => void run(() => workspaces.postWorkspacesReadAll(id), '标记已读失败，请重试')} />
          <MenuItem icon="archive" label="归档已完成任务" onSelect={() => (onClose(), openArchiveCompleted(id, name))} />
          <MenuDivider />
          <MenuItem icon="x" label="移除工作区" hint="不删除本机文件" danger onSelect={() => (onClose(), setDialog({ kind: 'remove-workspace', id, name }))} />
        </Menu>
      </Popover>
    );
  } else if (menu?.kind === 'task') {
    const { taskId, workspaceId } = menu;
    const ws = list.find((x) => x.id === workspaceId);
    const title = ws?.tasks.find((t) => t.id === taskId)?.title ?? ws?.expandedTasks?.find((t) => t.id === taskId)?.title ?? '';
    const targets = list.filter((x) => x.kind === 'workspace' && x.id !== workspaceId);
    popover = (
      <Popover at={menu.at} width={240} onClose={onClose}>
        <Menu label={`任务 ${title}`}>
          <MenuItem icon="square-pen" label="重命名" onSelect={() => (onClose(), setDialog({ kind: 'rename', taskId, title }))} />
          <MenuItem icon="pin" label="置顶任务" onSelect={() => void run(() => tasks.postTasksPin(taskId), '置顶失败，请重试')} />
          <MenuItem icon="folder" label="移动到工作区" submenu disabled={targets.length === 0} onSelect={() => setSubmenu((s) => (s === 'move' ? null : 'move'))} />
          {submenu === 'move' &&
            targets.map((t) => (
              <MenuItem
                key={t.id}
                icon="folder"
                label={t.name}
                onSelect={() =>
                  void run(() => tasks.patchTasksById(taskId, { workspace_id: t.id }), '移动失败，请重试', () =>
                    toast({ tone: 'undo', message: `已移动到「${t.name}」，资料权限不会随之授予` }),
                  )
                }
              />
            ))}
          <MenuItem icon="message-square" label="标为未读" onSelect={() => void run(() => tasks.deleteTasksRead(taskId), '标记未读失败，请重试')} />
          <MenuDivider />
          <MenuItem
            icon="link"
            label="复制任务链接"
            onSelect={() =>
              void run(
                () => navigator.clipboard.writeText(`${window.location.origin}/tasks/${encodeURIComponent(taskId)}`),
                '复制失败，请手动复制地址栏链接',
                () => toast({ tone: 'undo', message: '已复制任务链接' }),
              )
            }
          />
          <MenuItem icon="archive" label="归档" hint="可恢复" onSelect={() => void archiveTask(taskId)} />
        </Menu>
      </Popover>
    );
  }

  let modal = null;
  if (dialog?.kind === 'archive-completed') {
    modal = (
      <ConfirmDialog
        variant="standard"
        title={dialog.count == null ? `归档「${dialog.name}」中的已完成任务？` : `归档 ${dialog.count} 个已完成任务？`}
        message="已完成的任务会移到归档，可随时在全部任务中恢复。"
        confirmLabel="归档"
        cancelLabel="取消"
        busy={busy || dialog.count == null}
        onConfirm={() => void confirmDialog()}
        onCancel={() => setDialog(null)}
      />
    );
  } else if (dialog?.kind === 'remove-workspace') {
    modal = (
      <ConfirmDialog
        variant="danger"
        title={`移除工作区“${dialog.name}”？`}
        message="只删除 Sage 中的登记与关联，不删除本机文件和已产生的任务成果；其中的任务会变为未关联工作区。"
        confirmLabel="移除工作区"
        cancelLabel="取消"
        busy={busy}
        onConfirm={() => void confirmDialog()}
        onCancel={() => setDialog(null)}
      />
    );
  } else if (dialog?.kind === 'rename') {
    modal = (
      <Modal
        className="sg-dialog sg-dialog--board sg-renamedialog"
        label="重命名任务"
        onClose={() => setDialog(null)}
        onSubmit={(e) => {
          e.preventDefault();
          void confirmDialog();
        }}
      >
          <h2>重命名任务</h2>
          <TextField size={32} value={dialog.title} autoFocus aria-label="任务名称" onValueChange={(title) => setDialog({ ...dialog, title })} />
          <div className="sg-dialog__actions">
            <Button variant="ghost" size={32} label="取消" onClick={() => setDialog(null)} disabled={busy} />
            <Button type="submit" variant="primary" size={32} label="保存" busy={busy} disabled={!dialog.title.trim()} />
          </div>
      </Modal>
    );
  }

  return (
    <>
      {popover}
      {modal}
    </>
  );
}
