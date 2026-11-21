import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { request } from '../../api/client';
import { Link } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { ConfirmDialog } from '../../components/ConfirmDialog/ConfirmDialog';
import { Icon } from '../../components/Icon/Icon';
import { IconButton } from '../../components/IconButton/IconButton';
import { Tag } from '../../components/Tag/Tag';
import { TextField } from '../../components/TextField/TextField';
import { apiMessage } from '../task/model';
import { wsApi, type WorkspaceDetail } from '../workspaces/model';
import {
  DESKTOP_ONLY,
  desktop,
  desktopErrorText,
  fmtLocalTime,
  foldersSummary,
  notifyFoldersChanged,
  useUpdateState,
  useWorkspaceFolders,
  type UpdateState,
} from './bridge';
import './desktop.css';
import { Modal } from '../../components/Modal/Modal';

type Point = { x: number; y: number };

interface Notice {
  tone: 'undo' | 'failure';
  message: string;
  detail?: string;
}

/** Open after a delay, close after a short grace period so the pointer can travel into the card (24 ①: 400ms). */
export function useHoverIntent<T>(openDelay = 400, closeDelay = 200) {
  const [value, setValue] = useState<T | null>(null);
  const openTimer = useRef<number | undefined>(undefined);
  const closeTimer = useRef<number | undefined>(undefined);
  const clear = () => {
    window.clearTimeout(openTimer.current);
    window.clearTimeout(closeTimer.current);
  };
  const enter = useCallback(
    (v: T, immediate = false) => {
      clear();
      if (immediate) setValue(v);
      else openTimer.current = window.setTimeout(() => setValue(v), openDelay);
    },
    [openDelay],
  );
  const leave = useCallback(() => {
    clear();
    closeTimer.current = window.setTimeout(() => setValue(null), closeDelay);
  }, [closeDelay]);
  const keep = useCallback(() => window.clearTimeout(closeTimer.current), []);
  const close = useCallback(() => {
    clear();
    setValue(null);
  }, []);
  useEffect(() => clear, []);
  return { value, enter, leave, keep, close };
}

interface KbItem {
  id: string;
  name: string;
  scope: string;
  workspace: { id: string; name: string } | null;
}

function useWorkspaceSources(workspaceId: string) {
  const detail = useApiQuery((s) => wsApi.get(workspaceId, s), [workspaceId]);
  const kbs = useApiQuery((s) => request<{ items: KbItem[] }>('GET', 'app/knowledge-bases', { query: { scope: 'workspace' }, signal: s }), []);
  const own = (kbs.data?.items ?? []).filter((k) => k.workspace?.id === workspaceId);
  return { detail, kbs: own, kbsLoaded: !!kbs.data };
}

const kindLabel = (w: WorkspaceDetail) => (w.account_kind === 'personal' ? '个人' : '企业');

/** 24 ① read-only details card shown while hovering a workspace name in the sidebar. */
export function WorkspaceHoverCard({
  workspaceId,
  at,
  onKeep,
  onLeave,
  onClose,
  onEdit,
  navigate,
}: {
  workspaceId: string;
  at: Point;
  onKeep: () => void;
  onLeave: () => void;
  onClose: () => void;
  onEdit: () => void;
  navigate: (to: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<Point | null>(null);
  const { detail, kbs } = useWorkspaceSources(workspaceId);
  const d = desktop();
  const { folders } = useWorkspaceFolders(d ? workspaceId : null);
  const w = detail.data;

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setPos({ x: Math.max(8, Math.min(at.x, window.innerWidth - el.offsetWidth - 8)), y: Math.max(8, Math.min(at.y, window.innerHeight - el.offsetHeight - 8)) });
  }, [at.x, at.y, w, folders, kbs.length]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const indexAt = folders
    ?.map((f) => f.index?.lastSuccessAt ?? null)
    .filter((x): x is string => !!x)
    .sort()
    .pop();
  const primary = folders?.find((f) => f.primary) ?? folders?.[0];
  const go = (to: string) => {
    onClose();
    navigate(to);
  };

  return (
    <div
      ref={ref}
      className="sg-dsk-card"
      role="group"
      aria-label={w ? `${w.name} 详情` : '工作区详情'}
      style={{ left: pos?.x ?? at.x, top: pos?.y ?? at.y, visibility: pos ? 'visible' : 'hidden' }}
      onMouseEnter={onKeep}
      onMouseLeave={onLeave}
    >
      <div className="sg-dsk-card__head">
        <Icon name="folder" size={15} />
        <span className="truncate">{w?.name ?? ''}</span>
      </div>
      {w && (
        <div className="sg-dsk-card__sub">
          <Icon name="message-square" size={12} />
          <span>{`${w.counts.tasks} 个任务 · ${w.unread_count} 条未读 · ${kindLabel(w)} · ${w.counts.members} 位成员`}</span>
        </div>
      )}
      <div className="sg-dsk-card__divider" />
      {d ? (
        folders && (
          <button type="button" className="sg-dsk-card__row" disabled={!primary} onClick={() => primary && void d.openFolder(primary.id).catch(() => undefined)}>
            <Icon name="folder" size={14} />
            <span className="truncate">{foldersSummary(folders)}</span>
          </button>
        )
      ) : (
        <div className="sg-dsk-card__row is-muted">
          <Icon name="laptop" size={14} />
          <span className="truncate">{`本机文件夹${DESKTOP_ONLY}`}</span>
        </div>
      )}
      {kbs.map((k) => (
        <button key={k.id} type="button" className="sg-dsk-card__row" onClick={() => go(`/knowledge/${encodeURIComponent(k.id)}`)}>
          <Icon name="book" size={14} />
          <span className="truncate">{`知识库：${k.name}`}</span>
        </button>
      ))}
      {w && (
        <button type="button" className="sg-dsk-card__row" onClick={() => go(`/knowledge?workspace=${encodeURIComponent(workspaceId)}`)}>
          <Icon name="file" size={14} />
          <span className="truncate">{`资料 ${w.counts.documents} 份${indexAt ? ` · 本地索引 ${fmtLocalTime(indexAt)}` : ''}`}</span>
        </button>
      )}
      <div className="sg-dsk-card__divider" />
      <button
        type="button"
        className="sg-dsk-card__row"
        onClick={() => {
          onClose();
          onEdit();
        }}
      >
        <Icon name="settings" size={14} />
        <span>编辑工作区</span>
      </button>
    </div>
  );
}

/** 24 ③ 编辑工作区: name, sources (local folders + workspace knowledge bases) and members. */
export function EditWorkspaceDialog({
  workspaceId,
  onClose,
  onSaved,
  onRemove,
  navigate,
}: {
  workspaceId: string;
  onClose: () => void;
  onSaved: () => void;
  onRemove: (name: string) => void;
  navigate: (to: string) => void;
}) {
  const { detail, kbs } = useWorkspaceSources(workspaceId);
  const d = desktop();
  const { folders } = useWorkspaceFolders(d ? workspaceId : null);
  const w = detail.data;
  const [name, setName] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const value = name ?? w?.name ?? '';
  const trimmed = value.trim();

  const save = async () => {
    if (!w) return;
    if (!trimmed || trimmed.length > 40) {
      setError(!trimmed ? '请填写名称' : '名称不超过 40 个字');
      return;
    }
    if (trimmed === w.name) {
      onClose();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await request('PATCH', `app/workspaces/${encodeURIComponent(workspaceId)}`, { body: { name: trimmed } });
      onSaved();
      onClose();
    } catch (err) {
      setError(apiMessage(err, '保存失败，请重试'));
    } finally {
      setSaving(false);
    }
  };
  const folderAct = (fn: () => Promise<unknown>) => {
    setError(null);
    fn().then(notifyFoldersChanged, (err: unknown) => setError(desktopErrorText(err)));
  };
  const go = (to: string) => {
    onClose();
    navigate(to);
  };

  return (
    <Modal
      className="sg-wsdialog sg-dsk-edit"
      labelledBy="sg-dsk-edit-title"
      onClose={onClose}
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
        <div className="sg-dsk-edit__head">
          <h2 id="sg-dsk-edit-title">编辑工作区</h2>
          <IconButton icon="x" size={28} iconSize={14} variant="ghost" label="关闭" onClick={onClose} />
        </div>
        <label className="sg-wsdialog__field">
          <span className="sg-wsdialog__label">名称</span>
          <TextField size={32} value={value} maxLength={40} autoFocus disabled={!w || !w.can_manage} onValueChange={setName} />
        </label>
        <div className="sg-wsdialog__field">
          <span className="sg-wsdialog__label">资料来源</span>
          <div className="sg-dsk-sources">
            {d ? (
              folders?.map((f) => (
                <div key={f.id} className={`sg-dsk-sources__row${f.available ? '' : ' is-unavailable'}`}>
                  <Icon name="folder" size={14} />
                  <span className="truncate" title={f.path}>
                    {f.displayPath}
                  </span>
                  {!f.available && <span className="sg-dsk-sources__warn">无法访问本机目录</span>}
                  {f.primary && <Tag label="主要" tone="neutral" size={18} />}
                  <IconButton icon="x" size={25} iconSize={12} variant="ghost" label={`移除 ${f.displayPath}`} onClick={() => folderAct(() => d.removeFolder(f.id))} />
                </div>
              ))
            ) : (
              <div className="sg-dsk-sources__row is-muted">
                <Icon name="laptop" size={14} />
                <span className="truncate">{`本机文件夹${DESKTOP_ONLY}`}</span>
              </div>
            )}
            {kbs.map((k) => (
              <div key={k.id} className="sg-dsk-sources__row">
                <Icon name="book" size={14} />
                <button type="button" className="sg-dsk-sources__link truncate" onClick={() => go(`/knowledge/${encodeURIComponent(k.id)}`)}>
                  {`知识库：${k.name}`}
                </button>
              </div>
            ))}
            <div className="sg-dsk-sources__row">
              <Icon name="plus" size={14} />
              <button type="button" className="sg-dsk-sources__link" aria-expanded={adding} onClick={() => setAdding((a) => !a)}>
                添加文件夹或知识库
              </button>
            </div>
            {adding && (
              <div className="sg-dsk-sources__choices">
                <Button
                  variant="secondary"
                  size={28}
                  icon="folder"
                  label={d ? '本机文件夹' : `本机文件夹 · ${DESKTOP_ONLY}`}
                  disabled={!d}
                  onClick={() => d && folderAct(() => d.pickFolder(workspaceId).then(() => setAdding(false)))}
                />
                <Button variant="secondary" size={28} icon="book" label="知识库" onClick={() => go(`/knowledge?workspace=${encodeURIComponent(workspaceId)}`)} />
              </div>
            )}
          </div>
        </div>
        <div className="sg-wsdialog__field">
          <span className="sg-wsdialog__label">成员与权限</span>
          <div className="sg-dsk-sources">
            <div className="sg-dsk-sources__row">
              <Icon name="users" size={14} />
              <span className="truncate">{w ? `${w.counts.members} 位成员 · ${kindLabel(w)}工作区` : ''}</span>
              <Link className="sg-dsk-sources__manage" href={`/workspaces/${encodeURIComponent(workspaceId)}/members`} onClick={onClose}>
                管理
              </Link>
            </div>
          </div>
        </div>
        {error && (
          <div className="sg-wsdialog__error" role="alert">
            {error}
          </div>
        )}
        <div className="sg-wsdialog__foot">
          {w?.can_manage && <Button variant="danger" size={32} label="移除工作区" onClick={() => onRemove(w.name)} />}
          <span className="sg-wsdialog__note" />
          <Button variant="ghost" size={32} label="取消" onClick={onClose} />
          <Button type="submit" variant="primary" size={32} label="保存" busy={saving} disabled={!w} />
        </div>
    </Modal>
  );
}

export function updateStatusText(s: UpdateState | null): string {
  if (!s) return '';
  switch (s.state) {
    case 'checking':
      return '正在检查更新…';
    case 'latest':
      return s.feedConfigured ? '已是最新版本' : '已是最新版本 · 未配置发布源';
    case 'available':
      return `发现新版本 ${s.latestVersion ?? ''}`;
    case 'downloading':
      return `下载中 ${s.percent}%`;
    case 'ready':
      return `新版本 ${s.latestVersion ?? ''} 已下载，点击标题栏“更新”安装`;
    case 'failed':
      return `更新失败：${desktopErrorText(s.error ?? '', '请稍后重试')}`;
    default:
      return '尚未检查更新';
  }
}

/** Account menu “检查更新”: runs a check and reports the outcome as a toast. */
export async function checkForUpdates(toast: (n: Notice) => void): Promise<void> {
  const d = desktop();
  if (!d) return;
  try {
    const s = await d.checkForUpdates();
    if (s.state === 'failed') toast({ tone: 'failure', message: '检查更新失败', detail: desktopErrorText(s.error ?? '', '请稍后重试') });
    else if (s.state === 'downloading' || s.state === 'available') toast({ tone: 'undo', message: `发现新版本 ${s.latestVersion ?? ''}，正在后台下载` });
    else if (s.state === 'ready') toast({ tone: 'undo', message: `新版本 ${s.latestVersion ?? ''} 已就绪`, detail: '点击标题栏“更新”安装' });
    else toast({ tone: 'undo', message: '已是最新版本', detail: `当前 v${s.currentVersion}${s.feedConfigured ? '' : ' · 未配置发布源'}` });
  } catch (err) {
    toast({ tone: 'failure', message: '检查更新失败', detail: desktopErrorText(err, '请稍后重试') });
  }
}

/** 25 ① title bar update button: hidden / 下载中 37% / 更新 / 更新失败. */
export function UpdatePill() {
  const d = desktop();
  const s = useUpdateState();
  const [confirm, setConfirm] = useState<'install' | 'failed' | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The main process does not download on its own; the client starts it as soon as a newer version is reported.
  useEffect(() => {
    if (d && s?.state === 'available') void d.downloadUpdate().catch(() => undefined);
  }, [d, s?.state]);
  if (!d || !s) return null;
  let pill = null;
  if (s.state === 'downloading') pill = <Tag label={`下载中 ${s.percent}%`} tone="neutral" size={22} icon="download" />;
  else if (s.state === 'ready') pill = <Tag label="更新" tone="emphasis" size={22} icon="download" onClick={() => setConfirm('install')} />;
  else if (s.state === 'failed') pill = <Tag label="更新失败" tone="warning" size={22} icon="info" onClick={() => setConfirm('failed')} />;
  if (!pill) return null;
  const install = async () => {
    setBusy(true);
    setError(null);
    try {
      await d.installUpdate();
    } catch (err) {
      setError(desktopErrorText(err));
      setBusy(false);
    }
  };
  return (
    <>
      <span className="sg-dsk-update" data-update-state={s.state}>
        {pill}
      </span>
      {confirm === 'install' && (
        <ConfirmDialog
          variant="standard"
          title="安装更新？"
          message={`Sage 将退出并安装 ${s.latestVersion ?? '新版本'}，完成后自动重新打开；任务在服务端继续运行，重新打开后可以接着处理。`}
          confirmLabel="安装并退出"
          cancelLabel="稍后"
          busy={busy}
          footerNote={error ?? undefined}
          onConfirm={() => void install()}
          onCancel={() => setConfirm(null)}
        />
      )}
      {confirm === 'failed' && (
        <ConfirmDialog
          variant="standard"
          title="更新失败"
          message={desktopErrorText(s.error ?? '', '下载或校验没有完成，请重试')}
          confirmLabel="重试"
          cancelLabel="关闭"
          onConfirm={() => {
            setConfirm(null);
            void d.checkForUpdates();
          }}
          onCancel={() => setConfirm(null)}
        />
      )}
    </>
  );
}

/** 25 ② “下载 Sage 手机 App” card; only offered when a download page is configured for this client. */
export function MobileAppCard({ url, onDone }: { url: string; onDone: () => void }) {
  const d = desktop();
  let host = url;
  try {
    host = new URL(url).host;
  } catch {
    /* keep raw */
  }
  return (
    <div className="sg-dsk-mobile">
      <div className="sg-dsk-mobile__title">下载 Sage 手机 App</div>
      <div className="sg-dsk-mobile__sub">随时查看任务进度、处理审批</div>
      <Button
        variant="secondary"
        size={32}
        icon="smartphone"
        label="打开下载页"
        disabled={!d}
        onClick={() => {
          void d?.openExternal(url).catch(() => undefined);
          onDone();
        }}
      />
      <div className="sg-dsk-mobile__note">{host}</div>
    </div>
  );
}
