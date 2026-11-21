import { useCallback, useEffect, useState } from 'react';

/** Text shown wherever a desktop-only capability is reached from the web build. */
export const DESKTOP_ONLY = '仅桌面客户端可用';

export type IndexState = 'indexing' | 'done' | 'error' | 'cancelled';

export interface IndexSummary {
  state: IndexState;
  phase: 'scan' | 'hash' | null;
  done: number;
  total: number;
  percent: number;
  files: number;
  bytes: number;
  types?: Array<[string, number]>;
  added?: number;
  changed?: number;
  removed?: number;
  unchanged?: number;
  failed?: number;
  /** Files that could not be read or parsed (KB_INDEX_READ_FAILED / KB_INDEX_PARSE_FAILED), newest run only. */
  errors?: IndexFileError[];
  startedAt: string | null;
  finishedAt: string | null;
  lastSuccessAt: string | null;
  error: string | null;
}

export interface IndexFileError {
  code: string;
  file: string;
  rel: string;
  at: string;
}

export interface IndexProgress extends IndexSummary {
  folderId: string;
  workspaceId: string;
}

export interface DesktopFolder {
  id: string;
  workspaceId: string;
  path: string;
  /** Home-relative form such as “~/Documents/德国市场”. */
  displayPath: string;
  name: string;
  primary: boolean;
  addedAt: string;
  available: boolean;
  index: IndexSummary | null;
}

export interface DirEntry {
  name: string;
  kind: 'dir' | 'file';
  size: number;
  mtimeMs: number;
}

export interface DirListing {
  rel: string;
  entries: DirEntry[];
  total: number;
  truncated: boolean;
}

/** 'available': a newer version was found and the renderer has not started its download yet. */
export type UpdatePhase = 'idle' | 'checking' | 'latest' | 'available' | 'downloading' | 'ready' | 'failed';

export interface UpdateState {
  state: UpdatePhase;
  currentVersion: string;
  latestVersion: string | null;
  percent: number;
  error: string | null;
  checkedAt: string | null;
  feedConfigured: boolean;
}

export interface DesktopInfo {
  version: string;
  build: string | null;
  /** Release date of the running build (YYYY-MM-DD). */
  releasedAt: string | null;
  /** Model name such as “MacBook Pro”, also sent to the service as X-Sage-Client. */
  deviceName: string;
  osName: string;
  platform: string;
  arch: string;
  osVersion: string;
  electron: string;
  mobileAppUrl: string | null;
  updateFeedConfigured: boolean;
  /** Theme style of the icon last applied to the Dock / window, null before any was applied. */
  appIconStyle: string | null;
}

export interface ConnectionState {
  state: 'unknown' | 'connected' | 'disconnected';
  latencyMs: number | null;
  lastOkAt: string | null;
  checkedAt: string | null;
  error: string | null;
}

export type IndexTarget = { folderId: string } | { workspaceId: string };

/** window.sageDesktop, exposed by app/electron/preload.cjs through contextBridge. */
export interface SageDesktopBridge {
  getInfo(): Promise<DesktopInfo>;
  pickFolder(workspaceId: string): Promise<DesktopFolder | null>;
  listFolders(workspaceId?: string): Promise<DesktopFolder[]>;
  removeFolder(folderId: string): Promise<boolean>;
  setPrimaryFolder(folderId: string): Promise<boolean>;
  openFolder(folderId: string): Promise<boolean>;
  listDir(folderId: string, relPath?: string): Promise<DirListing>;
  startIndex(target: IndexTarget): Promise<DesktopFolder[]>;
  cancelIndex(target: IndexTarget): Promise<boolean>;
  onIndexProgress(callback: (p: IndexProgress) => void): () => void;
  checkConnection(): Promise<ConnectionState>;
  checkForUpdates(): Promise<UpdateState>;
  getUpdateState(): Promise<UpdateState>;
  downloadUpdate(): Promise<UpdateState>;
  installUpdate(): Promise<boolean>;
  onUpdateState(callback: (s: UpdateState) => void): () => void;
  openExternal(url: string): Promise<boolean>;
  /** Swaps the Dock (macOS) or window icon for the given theme style's App icon; resolves with the applied style. */
  setAppIcon(style: string): Promise<string>;
}

declare global {
  interface Window {
    sageDesktop?: SageDesktopBridge;
  }
}

/** The bridge when running inside the Electron client, otherwise null (web build). */
export function desktop(): SageDesktopBridge | null {
  return typeof window !== 'undefined' && window.sageDesktop ? window.sageDesktop : null;
}

const ERRORS: Record<string, string> = {
  FOLDER_UNAVAILABLE: '无法访问本机目录',
  FOLDER_NOT_FOUND: '这个文件夹已不在登记中',
  FOLDER_TOO_BROAD: '不能添加整个磁盘，请选择具体的文件夹',
  PATH_OUTSIDE_FOLDER: '只能浏览已添加的文件夹',
  INDEX_FAILED: '索引失败，请重试',
  UPDATE_FEED_INVALID: '发布信息无法识别',
  UPDATE_CHECKSUM_MISMATCH: '安装包校验未通过',
  UPDATE_TIMEOUT: '连接发布服务器超时',
  UPDATE_HTTP_ERROR: '发布服务器暂时不可用',
  UPDATE_URL_REJECTED: '发布地址不安全，已拒绝',
  UPDATE_NOT_READY: '更新尚未下载完成',
  UPDATE_NOT_AVAILABLE: '没有可下载的新版本',
  UPDATE_SIGNATURE_INVALID: '安装包签名校验未通过',
  UPDATE_FAILED: '更新没有完成，请稍后重试',
};

export function desktopErrorText(err: unknown, fallback = '操作失败，请重试'): string {
  const code = err instanceof Error ? err.message.replace(/^.*?([A-Z_]{4,})$/, '$1') : typeof err === 'string' ? err : '';
  return ERRORS[code] ?? fallback;
}

// Folder add/remove from one surface (rail, hover card, edit dialog) refreshes the others.
const folderListeners = new Set<() => void>();
export function notifyFoldersChanged(): void {
  folderListeners.forEach((fn) => fn());
}

export function useDesktopInfo(): DesktopInfo | null {
  const [info, setInfo] = useState<DesktopInfo | null>(null);
  useEffect(() => {
    const d = desktop();
    if (!d) return;
    let live = true;
    d.getInfo().then((i) => live && setInfo(i), () => undefined);
    return () => {
      live = false;
    };
  }, []);
  return info;
}

export interface FoldersState {
  folders: DesktopFolder[] | null;
  error: string | null;
  reload: () => void;
}

export function useWorkspaceFolders(workspaceId: string | null): FoldersState {
  const [folders, setFolders] = useState<DesktopFolder[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    folderListeners.add(reload);
    return () => {
      folderListeners.delete(reload);
    };
  }, [reload]);

  useEffect(() => {
    const d = desktop();
    if (!d || !workspaceId) return;
    let live = true;
    d.listFolders(workspaceId).then(
      (list) => {
        if (!live) return;
        setFolders(list);
        setError(null);
      },
      (err: unknown) => live && setError(desktopErrorText(err, '本机目录暂时无法读取')),
    );
    return () => {
      live = false;
    };
  }, [workspaceId, tick]);

  useEffect(() => {
    const d = desktop();
    if (!d || !workspaceId) return;
    return d.onIndexProgress((p) => {
      if (p.workspaceId !== workspaceId) return;
      const { folderId: id, workspaceId: _w, ...summary } = p;
      setFolders((list) => list?.map((f) => (f.id === id ? { ...f, index: summary, available: p.error === 'FOLDER_UNAVAILABLE' ? false : f.available } : f)) ?? list);
      if (p.state !== 'indexing') reload();
    });
  }, [workspaceId, reload]);

  return { folders, error, reload };
}

export function useUpdateState(): UpdateState | null {
  const [state, setState] = useState<UpdateState | null>(null);
  useEffect(() => {
    const d = desktop();
    if (!d) return;
    let live = true;
    d.getUpdateState().then((s) => live && setState(s), () => undefined);
    const off = d.onUpdateState((s) => live && setState(s));
    return () => {
      live = false;
      off();
    };
  }, []);
  return state;
}

export type WorkspaceIndexStatus =
  | { kind: 'empty' }
  | { kind: 'indexing'; percent: number; done: number; total: number; files: number; phase: 'scan' | 'hash' | null }
  | { kind: 'unavailable'; folder: DesktopFolder }
  | { kind: 'failed' }
  | { kind: 'built'; at: string | null }
  | { kind: 'cancelled'; at: string | null }
  | { kind: 'pending' };

/** One status line for all local folders of a workspace (16 “本地索引”). */
export function workspaceIndexStatus(folders: DesktopFolder[]): WorkspaceIndexStatus {
  if (folders.length === 0) return { kind: 'empty' };
  const running = folders.filter((f) => f.index?.state === 'indexing');
  if (running.length) {
    const done = running.reduce((n, f) => n + (f.index?.done ?? 0), 0);
    const total = running.reduce((n, f) => n + (f.index?.total ?? 0), 0);
    const files = running.reduce((n, f) => n + (f.index?.files ?? 0), 0);
    const phase = running.some((f) => f.index?.phase === 'scan') ? 'scan' : 'hash';
    return { kind: 'indexing', done, total, files, phase, percent: total ? Math.floor((done / total) * 100) : 0 };
  }
  const missing = folders.find((f) => !f.available || f.index?.error === 'FOLDER_UNAVAILABLE');
  if (missing) return { kind: 'unavailable', folder: missing };
  if (folders.some((f) => f.index?.state === 'error')) return { kind: 'failed' };
  const latest = folders
    .map((f) => f.index?.lastSuccessAt ?? null)
    .filter((x): x is string => !!x)
    .sort()
    .pop() ?? null;
  if (folders.every((f) => f.index?.state === 'done')) return { kind: 'built', at: latest };
  if (folders.some((f) => f.index?.state === 'cancelled')) return { kind: 'cancelled', at: latest };
  return { kind: 'pending' };
}

/** “今天 09:42 / 昨天 18:03 / 11月4日 14:27”. */
export function fmtLocalTime(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return '';
  const d = new Date(iso);
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 86400000);
  if (diff === 0) return `今天 ${hm}`;
  if (diff === 1) return `昨天 ${hm}`;
  return `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export interface LocalIndexDigest {
  folders: number;
  lastRunAt: string | null;
  failed: number;
  lastError: IndexFileError | null;
}

/** All local folders together, for 帮助与诊断 → 知识索引. */
export function localIndexDigest(folders: DesktopFolder[]): LocalIndexDigest {
  const runs = folders.map((f) => f.index).filter((i): i is IndexSummary => !!i);
  const lastRunAt = runs.map((i) => i.finishedAt).filter((x): x is string => !!x).sort().pop() ?? null;
  const errors = runs.flatMap((i) => i.errors ?? []).sort((a, b) => a.at.localeCompare(b.at));
  return { folders: folders.length, lastRunAt, failed: runs.reduce((n, i) => n + (i.failed ?? 0), 0), lastError: errors.pop() ?? null };
}

/** “26.6.2” → “26.6”. */
export function shortOsVersion(v: string): string {
  return v.split('.').slice(0, 2).join('.');
}

/** “本机文件夹 2 个（主要：德国市场）” (24 ①). */
export function foldersSummary(folders: DesktopFolder[]): string {
  const primary = folders.find((f) => f.primary) ?? folders[0];
  return primary ? `本机文件夹 ${folders.length} 个（主要：${primary.name}）` : '还没有添加本机文件夹';
}
