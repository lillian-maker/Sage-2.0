import { useCallback, useEffect, useState } from 'react';
import { currentSession } from '../../api/client';
import { fmtTime } from '../billing/format';
import {
  desktop,
  desktopErrorText,
  localIndexDigest,
  shortOsVersion,
  useDesktopInfo,
  useUpdateState,
  type ConnectionState,
  type DesktopFolder,
  type DesktopInfo,
  type LocalIndexDigest,
  type UpdateState,
} from './bridge';

/** “2026-11-03” → “11月3日”. */
export function fmtReleaseDay(day: string | null): string {
  const m = day?.match(/^\d{4}-(\d{2})-(\d{2})$/);
  return m ? `${Number(m[1])}月${Number(m[2])}日` : '';
}

function updateShort(s: UpdateState | null): string {
  if (!s) return '';
  switch (s.state) {
    case 'checking':
      return '正在检查更新…';
    case 'latest':
      return s.feedConfigured ? '已是最新版本' : '未配置发布源';
    case 'available':
      return `发现新版本 ${s.latestVersion ?? ''}`;
    case 'downloading':
      return `正在下载 ${s.latestVersion ?? ''}（${s.percent}%）`;
    case 'ready':
      return `${s.latestVersion ?? '新版本'} 已下载，可在标题栏安装`;
    case 'failed':
      return `检查更新失败：${desktopErrorText(s.error ?? '', '请稍后重试')}`;
    default:
      return '尚未检查更新';
  }
}

export function desktopDiagnostics(info: DesktopInfo, conn: ConnectionState | null, index: LocalIndexDigest | null): string {
  const s = currentSession();
  const runtime = conn ? `runtime=${conn.state} latency=${conn.latencyMs ?? '-'}ms${conn.error ? ` error=${conn.error}` : ''}` : 'runtime=checking';
  const err = index?.lastError;
  return [
    `app=${info.version} build=${info.build ?? 'dev'} os=${info.osName} ${shortOsVersion(info.osVersion)} arch=${info.arch}`,
    runtime,
    `last_error=${err ? `${err.code} (${err.file}) ${fmtTime(err.at)}` : 'none'}`,
    `local_index folders=${index?.folders ?? 0} failed=${index?.failed ?? 0} last_run=${index?.lastRunAt ?? 'never'}`,
    `account=${s?.active_account_id ?? 'none'} user=${s?.user?.id ?? 'none'} electron=${info.electron}`,
    `generated_at=${new Date().toISOString()}`,
  ].join('\n');
}

export interface DesktopHelp {
  info: DesktopInfo;
  versionDescription: string;
  checkUpdates: () => void;
  connection: ConnectionState | null;
  recheck: () => void;
  index: LocalIndexDigest | null;
  /** Workspace of the newest failed file, for “部分失败 · 查看”. */
  failedWorkspaceId: string | null;
  diag: string;
}

/** 79 帮助与诊断 in the desktop client: version, service connection, local index and diagnostics from the main process. */
export function useDesktopHelp(): DesktopHelp | null {
  const info = useDesktopInfo();
  const update = useUpdateState();
  const [connection, setConnection] = useState<ConnectionState | null>(null);
  const [folders, setFolders] = useState<DesktopFolder[] | null>(null);

  const recheck = useCallback(() => {
    const d = desktop();
    if (!d) return;
    setConnection(null);
    d.checkConnection().then(setConnection, () =>
      setConnection({ state: 'disconnected', latencyMs: null, lastOkAt: null, checkedAt: null, error: 'DESKTOP_ERROR' }),
    );
  }, []);

  useEffect(() => {
    const d = desktop();
    if (!d) return;
    let live = true;
    const load = () => d.listFolders().then((f) => live && setFolders(f), () => live && setFolders([]));
    load();
    recheck();
    const off = d.onIndexProgress((p) => p.state !== 'indexing' && load());
    return () => {
      live = false;
      off();
    };
  }, [recheck]);

  if (!info) return null;
  const index = folders ? localIndexDigest(folders) : null;
  const failedFolder = index?.lastError ? folders?.find((f) => f.index?.errors?.some((e) => e.at === index.lastError?.at && e.rel === index.lastError?.rel)) : null;
  const day = fmtReleaseDay(info.releasedAt);
  const status = updateShort(update);
  return {
    info,
    versionDescription: [day && `${day} 发布`, status].filter(Boolean).join(' · '),
    checkUpdates: () => void desktop()?.checkForUpdates().catch(() => undefined),
    connection,
    recheck,
    index,
    failedWorkspaceId: failedFolder?.workspaceId ?? null,
    diag: desktopDiagnostics(info, connection, index),
  };
}
