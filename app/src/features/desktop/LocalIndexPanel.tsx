import { useEffect, useState } from 'react';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { IconButton } from '../../components/IconButton/IconButton';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { Tag } from '../../components/Tag/Tag';
import { SectionTitle } from '../workspaces/parts';
import {
  DESKTOP_ONLY,
  desktop,
  desktopErrorText,
  fmtBytes,
  fmtLocalTime,
  notifyFoldersChanged,
  useWorkspaceFolders,
  workspaceIndexStatus,
  type DesktopFolder,
  type DirListing,
  type SageDesktopBridge,
  type WorkspaceIndexStatus,
} from './bridge';
import './desktop.css';

/** 16 / 19 right rail: “本地索引” status and the collapsed “本机目录与登记” list (631:437–485). */
export function LocalIndexPanel({ workspaceId }: { workspaceId: string }) {
  const d = desktop();
  return d ? <DesktopLocalIndex bridge={d} workspaceId={workspaceId} /> : <WebLocalIndex />;
}

function RegistryToggle({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <button type="button" className={`sg-dsk-reg__toggle${open ? ' is-open' : ''}`} aria-expanded={open} onClick={onToggle}>
      <span>本机目录与登记</span>
      <Icon name="chevron-down" size={14} />
    </button>
  );
}

function WebLocalIndex() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <section>
        <SectionTitle title="本地索引" />
        <InlineNotice icon="laptop" tone="muted" text={DESKTOP_ONLY} />
      </section>
      <section className="sg-dsk-reg">
        <RegistryToggle open={open} onToggle={() => setOpen((o) => !o)} />
        {open && (
          <div className="sg-dsk-reg__body">
            <InlineNotice icon="laptop" tone="muted" text={`本机目录登记${DESKTOP_ONLY}；网页端不读取本机文件`} />
          </div>
        )}
      </section>
    </>
  );
}

function statusLines(s: WorkspaceIndexStatus): { label: string; sub: string } {
  switch (s.kind) {
    case 'empty':
      return { label: '尚未添加本机目录', sub: '文件只在本机索引，不会上传' };
    case 'indexing':
      return s.phase === 'scan' || s.total === 0
        ? { label: '正在建立', sub: `正在扫描 · 已发现 ${s.files} 个文件` }
        : { label: `正在建立 ${s.percent}%`, sub: `已处理 ${s.done} / ${s.total} 个文件` };
    case 'unavailable':
      return { label: '无法访问本机目录', sub: s.folder.displayPath };
    case 'failed':
      return { label: '索引失败', sub: '部分文件无法读取，可重试' };
    case 'built':
      return { label: '已建立', sub: s.at ? `上次更新 ${fmtLocalTime(s.at)}` : '' };
    case 'cancelled':
      return { label: '已取消', sub: s.at ? `上次更新 ${fmtLocalTime(s.at)}` : '尚未建立完成' };
    default:
      return { label: '尚未建立', sub: '' };
  }
}

function DesktopLocalIndex({ bridge, workspaceId }: { bridge: SageDesktopBridge; workspaceId: string }) {
  const { folders, error, reload } = useWorkspaceFolders(workspaceId);
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const status = folders ? workspaceIndexStatus(folders) : null;

  const run = (fn: () => Promise<unknown>, after?: () => void) => {
    setMessage(null);
    fn().then(after, (err: unknown) => setMessage(desktopErrorText(err)));
  };
  const add = () =>
    run(
      () => bridge.pickFolder(workspaceId),
      () => notifyFoldersChanged(),
    );
  const update = () => run(() => bridge.startIndex({ workspaceId }), reload);
  const cancel = () => run(() => bridge.cancelIndex({ workspaceId }));

  let action = null;
  if (status) {
    if (status.kind === 'empty') action = <Button variant="tonal" size={28} icon="plus" label="添加文件夹" onClick={add} />;
    else if (status.kind === 'indexing') action = <Button variant="tonal" size={28} label="取消" onClick={cancel} />;
    else if (status.kind === 'unavailable' || status.kind === 'failed') action = <Button variant="tonal" size={28} icon="refresh" label="重试" onClick={update} />;
    else action = <Button variant="tonal" size={28} icon="refresh" label={status.kind === 'pending' ? '建立索引' : '更新索引'} onClick={update} />;
  }
  const lines = status ? statusLines(status) : null;

  return (
    <>
      <section className="sg-dsk-index" data-index-state={status?.kind ?? 'loading'}>
        <SectionTitle title="本地索引" />
        {error && <InlineNotice icon="info" tone="error" text={error} action="重试" onAction={reload} />}
        {lines && (
          <div className="sg-dsk-index__row">
            <span className="sg-dsk-index__text">
              <span className={`sg-dsk-index__state${status?.kind === 'unavailable' || status?.kind === 'failed' ? ' is-error' : ''}`}>{lines.label}</span>
              {lines.sub && <span className="sg-dsk-index__sub truncate">{lines.sub}</span>}
            </span>
            {action}
          </div>
        )}
        {status?.kind === 'indexing' && (
          <div className="sg-dsk-progress" role="progressbar" aria-label="本地索引进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={status.percent}>
            <span style={{ width: `${status.phase === 'scan' ? 4 : Math.max(4, status.percent)}%` }} />
          </div>
        )}
        {message && <p className="sg-dsk-error" role="alert">{message}</p>}
      </section>
      <section className="sg-dsk-reg">
        <RegistryToggle open={open} onToggle={() => setOpen((o) => !o)} />
        {open && folders && (
          <div className="sg-dsk-reg__body">
            {folders.map((f) => (
              <FolderRow key={f.id} bridge={bridge} folder={f} onChanged={notifyFoldersChanged} onError={setMessage} />
            ))}
            <button type="button" className="sg-wsside__action sg-dsk-reg__add" onClick={add}>
              <Icon name="plus" size={12} />
              添加文件夹
            </button>
            <p className="sg-dsk-note">登记只保存在这台电脑上，不上传服务端；移除登记不会删除本机文件。</p>
          </div>
        )}
      </section>
    </>
  );
}

export function folderMeta(f: DesktopFolder): string {
  if (!f.available) return '无法访问本机目录';
  const i = f.index;
  if (!i) return '尚未建立索引';
  if (i.state === 'indexing') return i.phase === 'hash' && i.total ? `正在建立 ${i.percent}%` : `正在扫描 · 已发现 ${i.files} 个文件`;
  if (i.state === 'error') return i.error === 'FOLDER_UNAVAILABLE' ? '无法访问本机目录' : '索引失败';
  const types = (i.types ?? []).slice(0, 3).map(([t, n]) => `${t} ${n}`).join(' · ');
  const base = `${i.files} 个文件 · ${fmtBytes(i.bytes)}${types ? ' · ' + types : ''}${i.failed ? ` · ${i.failed} 份失败` : ''}`;
  return i.state === 'cancelled' ? `已取消 · ${base}` : base;
}

function FolderRow({
  bridge,
  folder,
  onChanged,
  onError,
}: {
  bridge: SageDesktopBridge;
  folder: DesktopFolder;
  onChanged: () => void;
  onError: (m: string | null) => void;
}) {
  const [browsing, setBrowsing] = useState(false);
  const act = (fn: () => Promise<unknown>) => {
    onError(null);
    fn().then(onChanged, (err: unknown) => onError(desktopErrorText(err)));
  };
  return (
    <div className={`sg-dsk-folder${folder.available ? '' : ' is-unavailable'}`}>
      <div className="sg-dsk-folder__head">
        <Icon name="folder" size={14} />
        <button type="button" className="sg-dsk-folder__path truncate" title={folder.path} aria-expanded={browsing} onClick={() => setBrowsing((b) => !b)}>
          {folder.displayPath}
        </button>
        {folder.primary && <Tag label="主要" tone="neutral" size={18} />}
        <IconButton icon="x" size={25} iconSize={12} variant="ghost" label={`移除登记 ${folder.displayPath}`} onClick={() => act(() => bridge.removeFolder(folder.id))} />
      </div>
      <div className="sg-dsk-folder__meta">{folderMeta(folder)}</div>
      <div className="sg-dsk-folder__actions">
        {!folder.primary && (
          <button type="button" className="sg-dsk-link" onClick={() => act(() => bridge.setPrimaryFolder(folder.id))}>
            设为主要
          </button>
        )}
        <button type="button" className="sg-dsk-link" disabled={!folder.available} onClick={() => act(() => bridge.openFolder(folder.id))}>
          在访达中打开
        </button>
      </div>
      {browsing && <DirList bridge={bridge} folderId={folder.id} />}
    </div>
  );
}

function DirList({ bridge, folderId }: { bridge: SageDesktopBridge; folderId: string }) {
  const [rel, setRel] = useState('');
  const [data, setData] = useState<DirListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setError(null);
    bridge.listDir(folderId, rel).then(
      (d) => live && setData(d),
      (err: unknown) => live && setError(desktopErrorText(err)),
    );
    return () => {
      live = false;
    };
  }, [bridge, folderId, rel]);
  if (error) return <p className="sg-dsk-error" role="alert">{error}</p>;
  if (!data) return null;
  const up = rel.split('/').slice(0, -1).join('/');
  return (
    <ul className="sg-dsk-dir" aria-label="文件夹内容">
      {rel && (
        <li>
          <button type="button" className="sg-dsk-dir__item" onClick={() => setRel(up)}>
            <Icon name="chevron-left" size={12} />
            <span>返回上级</span>
          </button>
        </li>
      )}
      {data.entries.length === 0 && <li className="sg-dsk-dir__empty">这个文件夹是空的</li>}
      {data.entries.map((e) => (
        <li key={e.name}>
          {e.kind === 'dir' ? (
            <button type="button" className="sg-dsk-dir__item" onClick={() => setRel(rel ? `${rel}/${e.name}` : e.name)}>
              <Icon name="folder" size={12} />
              <span className="truncate">{e.name}</span>
            </button>
          ) : (
            <span className="sg-dsk-dir__item">
              <Icon name="file" size={12} />
              <span className="truncate">{e.name}</span>
              <span className="sg-dsk-dir__size">{fmtBytes(e.size)}</span>
            </span>
          )}
        </li>
      ))}
      {data.truncated && <li className="sg-dsk-dir__empty">共 {data.total} 项，只列出前 {data.entries.length} 项</li>}
    </ul>
  );
}
