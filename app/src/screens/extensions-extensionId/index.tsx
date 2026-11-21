import { useState } from 'react';
import { useShell } from '../../app/AppShell';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { IconButton } from '../../components/IconButton/IconButton';
import { IconTile } from '../../components/IconTile/IconTile';
import { StepMarker } from '../../components/StepMarker/StepMarker';
import { ErrorBanner, errorMessage, QueryGate } from '../../components/States/States';
import { extensionsApi, KIND_LABEL, TIER_LABEL, type ExtensionDetail } from '../../features/extensions/api';
import { ExtensionsBoard, KIND_ICON, extSub } from '../../features/extensions/ExtensionsBoard';
import { MutedLabel } from '../../features/experts/parts';
import { relativeDay } from '../../features/experts/api';
import { ExtensionsHeader } from '../extensions/header';
import '../../features/extensions/board.css';
import { NotificationBell } from '../../components/NotificationBell/NotificationBell';

function steps(d: ExtensionDetail) {
  const installed = d.install_state === 'installed' || d.install_state === 'disabled';
  const conn = d.kind === 'connector' ? d.connection : d.required_connector?.connection;
  const connState = d.kind === 'connector' ? d.connection?.state : d.required_connector?.connection_state;
  const needsConnection = d.kind === 'connector' || d.missing_connection || !!d.required_connector;
  const connected = connState === 'connected';
  return { installed, conn, needsConnection, connected };
}

/** “只读：读取 A 与 B。不会 C。” built from the declared permissions, so the promise matches the manifest. */
function readOnlyNote(d: ExtensionDetail) {
  const reads = d.permissions.filter((p) => p.access === 'read_only').map((p, i) => (i === 0 ? p.name : p.name.replace(/^读取/, '')));
  const writes = d.permissions.filter((p) => p.access !== 'read_only').map((p) => p.name.replace(/、/g, '或'));
  if (!reads.length) return '只读：读取数据用于回答与生成，不会修改出价、预算或任何外部内容。';
  return `只读：${reads.join('与')}。${writes.length ? `不会${writes.join('，也不会')}。` : '不会修改任何外部内容。'}`;
}

function Drawer({ d, onChanged }: { d: ExtensionDetail; onChanged: () => void }) {
  const { toast } = useShell();
  const [busy, setBusy] = useState(false);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const versions = useApiQuery((signal) => (versionsOpen ? extensionsApi.versions(d.id, signal) : Promise.resolve(null)), [d.id, versionsOpen]);
  const { installed, conn, needsConnection, connected } = steps(d);

  const act = async (fn: () => Promise<unknown>, fail: string) => {
    setBusy(true);
    try {
      await fn();
      onChanged();
    } catch (err) {
      toast({ tone: 'failure', message: errorMessage(err, fail) });
    } finally {
      setBusy(false);
    }
  };

  const connectorName = d.kind === 'connector' ? d.name : d.required_connector?.name;
  const stepStates: Array<'done' | 'current' | 'static'> = [installed ? 'done' : 'current', installed ? (connected || !needsConnection ? (needsConnection ? 'done' : 'static') : 'current') : 'static', 'static'];

  return (
    <>
      <div className="sg-xdrawer__head">
        <IconTile icon={KIND_ICON[d.kind]} size={36} tone="neutral" shape="rounded" />
        <div className="sg-xdrawer__title">
          <b>{d.name}</b>
          <span>
            {KIND_LABEL[d.kind]} · {extSub(d)}
          </span>
        </div>
      </div>
      <p className="sg-dmuted" style={{ marginTop: 12 }}>
        {TIER_LABEL[d.publisher_tier] ?? d.publisher_tier} · {d.latest_version} · {d.description}
        {d.used_by_expert_count > 0 && d.used_by_experts.length > 0 && `用于 ${d.used_by_expert_count} 位专家：${d.used_by_experts.map((e) => e.name).join('、')}。`}
      </p>
      <MutedLabel>启用步骤</MutedLabel>
      <div className="sg-xstep">
        <StepMarker index={1} state={stepStates[0]!} shape="circle" size={20} />
        <div className="sg-xstep__text">
          <b>安装</b>
          <span>{installed ? `已加入你的扩展列表${d.install ? `（${d.install.installed_version} · ${relativeDay(d.install.installed_at)}）` : ''}` : '加入你的扩展列表，安装后用开关启用'}</span>
          {!installed && <Button variant="primary" size={30} label="安装" busy={busy} onClick={() => void act(() => extensionsApi.install(d.id), '安装失败，请重试')} />}
        </div>
      </div>
      {needsConnection && (
        <div className="sg-xstep">
          <StepMarker index={2} state={stepStates[1]!} shape="circle" size={20} />
          <div className="sg-xstep__text">
            <b>连接{connectorName ? ` ${connectorName}` : ''}</b>
            <span>{readOnlyNote(d)}</span>
            {connected ? (
              <span className="sg-dok">已连接 · {conn?.connected_at ? relativeDay(conn.connected_at) : ''}</span>
            ) : conn ? (
              <>
                <Button variant="primary" size={30} label="连接（只读）" busy={busy} onClick={() => void act(() => extensionsApi.connect(conn.id), '连接授权服务暂时不可用，请稍后再试')} />
                {conn.state !== 'not_connected' && <span className="sg-dwarn">连接状态：{conn.state}，需要重新连接</span>}
              </>
            ) : (
              <span className="sg-dmuted">安装后即可连接</span>
            )}
          </div>
        </div>
      )}
      <div className="sg-xstep">
        <StepMarker index={needsConnection ? 3 : 2} state={stepStates[2]!} shape="circle" size={20} />
        <div className="sg-xstep__text">
          <b>在任务中授权</b>
          <span>专家第一次使用时，会通过确认卡请你授权本次调用。</span>
        </div>
      </div>
      <MutedLabel>权限清单</MutedLabel>
      {d.permissions.length === 0 && <p className="sg-dmuted">这个扩展声明没有额外权限</p>}
      {d.permissions.map((p) => (
        <div key={p.name} className="sg-drow">
          <span className="sg-drow__text">
            <span className="sg-drow__name">{p.name}</span>
          </span>
          <span className={`sg-drow__right ${p.access === 'read_only' ? 'sg-dok' : ''}`}>{p.access === 'read_only' ? '只读' : '写入暂未开放'}</span>
        </div>
      ))}
      <button type="button" className="sg-dlabel" style={{ width: '100%', cursor: 'pointer' }} onClick={() => setVersionsOpen((o) => !o)}>
        <span>版本历史</span>
        <span className="sg-dlabel__right">
          <Icon name={versionsOpen ? 'chevron-down' : 'chevron-right'} size={12} />
        </span>
      </button>
      {versionsOpen &&
        (versions.data === null ? null : !versions.data ? (
          <QueryGate q={versions} label="版本历史" rows={1} />
        ) : (
          versions.data.items.map((v) => (
            <div key={v.version} className="sg-drow">
              <span className="sg-drow__text">
                <span className="sg-drow__name">{v.version}</span>
                <span className="sg-drow__sub">{v.notes ?? '当前版本'}</span>
              </span>
              <span className="sg-drow__right">{relativeDay(v.released_at)}</span>
            </div>
          ))
        ))}
    </>
  );
}

export default function Screen({ params, query, navigate }: ScreenProps) {
  const extensionId = params.extensionId!;
  const [q, setQ] = useState(query.get('q') ?? '');
  const [reloadToken, setReloadToken] = useState(0);
  const detail = useApiQuery((signal) => extensionsApi.detail(extensionId, signal), [extensionId, reloadToken]);
  const d = detail.data;

  return (
    <div className="sg-dpage has-rail" style={{ gridTemplateColumns: 'minmax(0, 1fr) 440px' }}>
      <div className="sg-dpage__col">
        <div className="sg-dpage__top" />
        <div className="sg-dpage__body">
          <div className="sg-xdim">
            <ExtensionsHeader
              navigate={navigate}
              tab="installed"
              q={q}
              onSearch={setQ}
              description="安装不等于连接，连接不等于授权执行，每一步都会单独确认"
            />
            <ExtensionsBoard tab="installed" kind="" q={q.trim()} reloadToken={reloadToken} dimmed onOpen={() => undefined} />
          </div>
        </div>
      </div>
      <aside className="sg-xdrawer" aria-label="扩展详情">
        <div className="sg-dpage__top">
          <NotificationBell onClick={() => navigate('/notifications')} />
          <IconButton icon="x" size={28} variant="ghost" label="关闭" onClick={() => navigate('/extensions')} />
        </div>
        <div className="sg-xdrawer__body">
          {!d ? (
            <QueryGate q={detail} label="扩展" rows={3} />
          ) : (
            <>
              {detail.status === 'error' && <ErrorBanner message={errorMessage(detail.error, '扩展刷新失败')} onRetry={detail.reload} />}
              <Drawer d={d} onChanged={() => setReloadToken((n) => n + 1)} />
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
