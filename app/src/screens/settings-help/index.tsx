import { useCallback, useEffect, useState } from 'react';
import { currentSession, loadSession } from '../../api/client';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { ConfirmDialog } from '../../components/ConfirmDialog/ConfirmDialog';
import { errorMessage } from '../../components/States/States';
import { fmtTime } from '../../features/billing/format';
import { SettingsLayout, SettingsRow, SettingsSection } from '../../features/settings/SettingsLayout';
import { useDesktopHelp } from '../../features/desktop/DesktopHelp';
import { getKnowledgeIndexSummary, logout } from '../../features/settings/api';
import { useSaveStatus } from '../../features/settings/useProfile';
import { version as APP_VERSION } from '../../../package.json';

type Ping = { state: 'checking' } | { state: 'ok'; at: Date } | { state: 'error'; message: string; lastOk: Date | null };

function diagnostics(ping: Ping, index: string): string {
  const s = currentSession();
  const lines = [
    `app=${APP_VERSION} mode=${import.meta.env.MODE} client=web`,
    `ua=${navigator.userAgent}`,
    `lang=${navigator.language} tz=${Intl.DateTimeFormat().resolvedOptions().timeZone} screen=${window.screen.width}x${window.screen.height}`,
    `account=${s?.active_account_id ?? 'none'} user=${s?.user?.id ?? 'none'}`,
    `service=${ping.state}${ping.state === 'ok' ? ' at=' + ping.at.toISOString() : ping.state === 'error' ? ' error=' + ping.message : ''}`,
    `knowledge_index=${index}`,
    `generated_at=${new Date().toISOString()}`,
  ];
  return lines.join('\n');
}

/** 79 设置 · 帮助与诊断 (662:2). This build runs in the browser, so desktop-only items say so. */
export default function Screen({ navigate }: ScreenProps) {
  const save = useSaveStatus();
  const [ping, setPing] = useState<Ping>({ state: 'checking' });
  const [lastOk, setLastOk] = useState<Date | null>(null);
  const [clearOpen, setClearOpen] = useState(false);
  const [clearBusy, setClearBusy] = useState(false);
  const [clearError, setClearError] = useState<string | null>(null);
  const indexQ = useApiQuery((s) => getKnowledgeIndexSummary(s), []);

  const check = useCallback(async () => {
    setPing({ state: 'checking' });
    try {
      await loadSession(true);
      const at = new Date();
      setLastOk(at);
      setPing({ state: 'ok', at });
    } catch (err) {
      setPing({ state: 'error', message: errorMessage(err, '无法连接'), lastOk });
    }
  }, [lastOk]);
  useEffect(() => {
    void check();
    // Only the first mount pings automatically; later checks are user initiated.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const summary = indexQ.data?.index_summary;
  const indexText = summary ? `last_run=${summary.last_run_at ?? 'never'} failed=${summary.failed_count}` : indexQ.status === 'error' ? 'unavailable' : 'loading';
  // The desktop client reports version, connection and local index from its main process.
  const dsk = useDesktopHelp();
  const local = dsk?.index && dsk.index.folders > 0 ? dsk.index : null;
  const conn = dsk?.connection;
  const diag = dsk ? dsk.diag : diagnostics(ping, indexText);

  const copy = () => void save.run(() => navigator.clipboard.writeText(diag));
  const exportLog = () => {
    const url = URL.createObjectURL(new Blob([diag + '\n'], { type: 'text/plain' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `sage-diagnostics-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };
  const clearCache = async () => {
    setClearBusy(true);
    setClearError(null);
    try {
      localStorage.clear();
      sessionStorage.clear();
      await logout();
      await loadSession(true).catch(() => undefined);
      navigate('/login', { replace: true });
    } catch (err) {
      setClearError(errorMessage(err, '清除后退出登录失败，请重试'));
      setClearBusy(false);
    }
  };

  return (
    <SettingsLayout active="help" title="帮助与诊断" save={{ status: save.status, message: save.status === 'saved' ? null : save.message }}>
      <SettingsSection title="版本">
        <SettingsRow
          label={`Sage ${dsk?.info.version ?? APP_VERSION}（内测）`}
          description={dsk ? dsk.versionDescription : '网页版随服务端发布自动更新，刷新页面即为最新版本'}
        >
          <Button variant="secondary" size={32} label="检查更新" onClick={dsk ? dsk.checkUpdates : () => window.location.reload()} />
        </SettingsRow>
      </SettingsSection>
      <SettingsSection title="连接状态">
        {dsk ? (
          <SettingsRow
            label="Sage 服务"
            description={conn ? (conn.lastOkAt ? `上次成功连接 ${fmtTime(conn.lastOkAt)}` : '本次打开后尚未连接成功') : '正在检查…'}
          >
            <span className={`sg-statustext is-${conn ? (conn.state === 'connected' ? 'ok' : 'error') : 'checking'}`}>
              {conn ? (conn.state === 'connected' ? '正常' : '无法连接') : '检查中'}
            </span>
            {conn?.state === 'disconnected' && <Button variant="ghost" size={28} label="重试" onClick={dsk.recheck} />}
          </SettingsRow>
        ) : (
        <SettingsRow
          label="Sage 服务"
          description={
            ping.state === 'ok'
              ? `上次成功连接 ${fmtTime(ping.at.toISOString())}`
              : ping.state === 'error'
                ? ping.lastOk
                  ? `上次成功连接 ${fmtTime(ping.lastOk.toISOString())}`
                  : '本次打开后尚未连接成功'
                : '正在检查…'
          }
        >
          <span className={`sg-statustext is-${ping.state}`}>{ping.state === 'ok' ? '正常' : ping.state === 'error' ? '无法连接' : '检查中'}</span>
          {ping.state === 'error' && <Button variant="ghost" size={28} label="重试" onClick={() => void check()} />}
        </SettingsRow>
        )}
        <SettingsRow label="本机运行时" description="自动化与本机文件夹索引依赖此项">
          {dsk ? <span className="sg-statustext is-ok">运行中</span> : <span className="sg-statustext is-muted">网页版不可用</span>}
        </SettingsRow>
        {local ? (
          <SettingsRow
            label="知识索引"
            description={`${local.lastRunAt ? `最近一次 ${fmtTime(local.lastRunAt)}` : '尚未运行'}${local.failed ? ` · ${local.failed} 份失败` : ''}`}
          >
            {local.failed > 0 ? (
              <button
                type="button"
                className="sg-textaction is-warning"
                onClick={() => navigate(dsk?.failedWorkspaceId ? `/workspaces/${encodeURIComponent(dsk.failedWorkspaceId)}` : '/knowledge')}
              >
                部分失败 · 查看
              </button>
            ) : (
              <span className="sg-statustext is-ok">正常</span>
            )}
          </SettingsRow>
        ) : (
        <SettingsRow
          label="知识索引"
          description={
            summary
              ? `${summary.last_run_at ? `最近一次 ${fmtTime(summary.last_run_at)}` : '尚未运行'}${summary.failed_count ? ` · ${summary.failed_count} 份失败` : ''}`
              : indexQ.status === 'error'
                ? '暂时无法读取'
                : '正在读取…'
          }
        >
          {summary && summary.failed_count > 0 ? (
            <button type="button" className="sg-textaction is-warning" onClick={() => navigate('/knowledge')}>
              部分失败 · 查看
            </button>
          ) : summary ? (
            <span className="sg-statustext is-ok">正常</span>
          ) : indexQ.status === 'error' ? (
            <Button variant="ghost" size={28} label="重试" onClick={indexQ.reload} />
          ) : null}
        </SettingsRow>
        )}
      </SettingsSection>
      <SettingsSection title="诊断信息" description="错误码与运行细节只在这里显示，主界面不展示。联系支持时请复制后发送。">
        <pre className="sg-diag">{diag}</pre>
        <div className="sg-diag__actions">
          <Button variant="secondary" size={32} label="复制诊断信息" onClick={copy} />
          <Button variant="secondary" size={32} label="导出日志" onClick={exportLog} />
          <span className="sg-settings__desc">联系支持的渠道尚未开通</span>
        </div>
      </SettingsSection>
      <div className="sg-profile__gap" />
      <SettingsRow label="清除本机缓存" description="不会删除任务、成果与知识库；需要重新登录">
        <Button variant="secondary" size={32} label="清除…" onClick={() => setClearOpen(true)} />
      </SettingsRow>
      {clearError && <p className="sg-commitfield__error">{clearError}</p>}
      {clearOpen && (
        <ConfirmDialog
          title="清除本机缓存？"
          message="将清除本机保存的主题与界面偏好并退出登录。任务、成果与知识库不受影响。"
          confirmLabel="清除并退出"
          cancelLabel="取消"
          tone="warning"
          busy={clearBusy}
          onConfirm={() => void clearCache()}
          onCancel={() => setClearOpen(false)}
        />
      )}
    </SettingsLayout>
  );
}
