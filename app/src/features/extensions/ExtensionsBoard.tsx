import { useState } from 'react';
import { useShell } from '../../app/AppShell';
import { navigate } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { IconTile } from '../../components/IconTile/IconTile';
import { EmptyState, ErrorBanner, errorMessage } from '../../components/States/States';
import { Switch } from '../../components/Switch/Switch';
import { Tag, type TagTone } from '../../components/Tag/Tag';
import { QueryGate } from '../../components/States/States';
import { extensionsApi, KIND_LABEL, TIER_LABEL, type ExtensionItem, type ExtensionKind } from './api';
import './board.css';

export const KIND_ICON: Record<ExtensionKind, 'puzzle' | 'zap' | 'globe' | 'users'> = { plugin: 'puzzle', skill: 'zap', connector: 'globe', agent: 'users' };
const KIND_TONE: Record<ExtensionKind, TagTone> = { plugin: 'success', skill: 'neutral', connector: 'info', agent: 'warning' };

const SUB_TIER_LABEL: Record<string, string> = { community_reviewed: '社区已审核' };
const AGENT_TIER_LABEL: Record<string, string> = { enterprise: '企业接入' };

/** Version and publisher; only connectors and agents reach external systems, so only they carry the read-only mark. */
export function extSub(e: ExtensionItem): string {
  const tier = (e.kind === 'agent' ? AGENT_TIER_LABEL[e.publisher_tier] : undefined) ?? SUB_TIER_LABEL[e.publisher_tier] ?? TIER_LABEL[e.publisher_tier] ?? e.publisher_tier;
  const external = e.kind === 'connector' || e.kind === 'agent';
  return [e.kind === 'agent' ? null : e.installed_version || e.latest_version, tier, external && e.access === 'read_only' ? '只读' : null]
    .filter(Boolean)
    .join(' · ');
}

export function usageLabel(n: number): string {
  return n >= 10000 ? `${(n / 10000).toFixed(1).replace(/\.0$/, '')} 万次使用` : `${n.toLocaleString()} 次使用`;
}

export function openExtension(e: Pick<ExtensionItem, 'id' | 'kind'>) {
  navigate(e.kind === 'skill' ? `/extensions/skills/${encodeURIComponent(e.id)}` : `/extensions/${encodeURIComponent(e.id)}`);
}

function connectionIssue(e: ExtensionItem): 'missing' | 'broken' | null {
  if (e.kind !== 'connector') return null;
  if (!e.connection || e.connection.state === 'connected') return null;
  return e.connection.state === 'not_connected' ? 'missing' : 'broken';
}

export function ExtensionCard({ e, selected, onOpen, onToggle, onConnect, busy }: { e: ExtensionItem; selected?: boolean; onOpen: () => void; onToggle?: (v: boolean) => void; onConnect?: () => void; busy?: boolean }) {
  const issue = connectionIssue(e);
  const installed = e.install_state === 'installed' || e.install_state === 'disabled';
  const ctrl = (
    <span className="sg-xcard__ctrl" onClick={(ev) => ev.stopPropagation()}>
      {e.kind === 'connector' ? (
        e.connection?.state === 'connected' ? (
          <span className="sg-dok" style={{ font: 'var(--type-sans-sc-12-400-lh14-4)' }}>
            ● 已连接
          </span>
        ) : (
          <Button variant="secondary" size={26} label={issue === 'broken' ? '重新连接' : '连接'} busy={busy} onClick={onConnect} />
        )
      ) : installed && onToggle ? (
        <Switch checked={e.enabled} onChange={onToggle} label={e.enabled ? `停用 ${e.name}` : `启用 ${e.name}`} />
      ) : (
        <Button variant="secondary" size={26} label="安装" busy={busy} onClick={onConnect} />
      )}
    </span>
  );
  const footer: string[] = [];
  if (e.missing_connection) footer.push('缺少连接');
  if (issue === 'broken') footer.push('连接已失效');
  if (e.kind === 'connector' && issue === 'missing') footer.push('未连接 · 只读权限');
  if (e.used_by_expert_count > 0) footer.push(`用于 ${e.used_by_expert_count} 位专家`);
  if (e.used_by_inbox) footer.push('用于收件');
  if (e.install_state === 'disabled') footer.push('已停用');
  if (!installed && e.global_usage_count != null) footer.push(usageLabel(e.global_usage_count));
  return (
    <article className={`sg-xcard${selected ? ' is-selected' : ''}`} role="button" tabIndex={0} onClick={onOpen} onKeyDown={(ev) => ev.key === 'Enter' && onOpen()}>
      <div className="sg-xcard__head">
        <IconTile icon={KIND_ICON[e.kind]} size={36} tone="neutral" shape="rounded" />
        <span className="sg-xcard__name">
          <b className="truncate">{e.name}</b>
          <span className="truncate">{extSub(e)}</span>
        </span>
        {ctrl}
      </div>
      <p className="sg-xcard__desc">{e.description}</p>
      <div className="sg-xcard__foot">
        <Tag label={KIND_LABEL[e.kind]} tone={KIND_TONE[e.kind]} size={18} />
        {footer.filter(Boolean).map((f) => (
          <span key={f} className={f === '缺少连接' || f === '连接已失效' ? 'sg-dwarn' : undefined}>
            {f === '缺少连接' || f === '连接已失效' ? `● ${f}` : f}
          </span>
        ))}
        {e.missing_connection && e.required_connector && (
          <button
            type="button"
            className="sg-xcard__connect"
            aria-label={`去连接 ${e.required_connector.name}`}
            onClick={(ev) => {
              ev.stopPropagation();
              navigate(`/extensions/${encodeURIComponent(e.required_connector!.id)}`);
            }}
          >
            去连接
          </button>
        )}
      </div>
    </article>
  );
}

export interface BoardProps {
  tab: 'installed' | 'market';
  kind: string;
  q: string;
  selectedId?: string | undefined;
  reloadToken?: number;
  dimmed?: boolean;
  onOpen?: (e: ExtensionItem) => void;
}

export function ExtensionsBoard({ tab, kind, q, selectedId, reloadToken = 0, dimmed, onOpen }: BoardProps) {
  const { toast } = useShell();
  const [busyId, setBusyId] = useState<string | null>(null);
  const list = useApiQuery((signal) => extensionsApi.list({ tab, kind: kind || undefined, q: q || undefined }, signal), [tab, kind, q, reloadToken]);

  const act = async (e: ExtensionItem, fn: () => Promise<unknown>, undo?: { message: string; redo: () => Promise<unknown> }) => {
    setBusyId(e.id);
    try {
      await fn();
      if (undo) toast({ tone: 'undo', message: undo.message, actionLabel: '撤销', onAction: () => void undo.redo().then(list.reload, list.reload) });
      list.reload();
    } catch (err) {
      toast({ tone: 'failure', message: errorMessage(err, '操作失败，请重试') });
      list.reload();
    } finally {
      setBusyId(null);
    }
  };

  if (!list.data)
    return (
      <div className="sg-dstate">
        <QueryGate q={list} label="扩展" rows={3} />
      </div>
    );
  return (
    <>
      {list.status === 'error' && <ErrorBanner message={errorMessage(list.error, '扩展列表刷新失败')} onRetry={list.reload} />}
      <div className={dimmed ? 'sg-xdim' : undefined}>
        {list.data.items.length === 0 ? (
          <EmptyState icon="puzzle" title={q ? `没有找到与“${q}”相关的扩展` : tab === 'installed' ? '还没有安装扩展' : '市场里没有匹配的扩展'} description={tab === 'installed' ? '到市场安装插件、技能或连接器' : undefined} />
        ) : (
          <div className="sg-xgrid">
            {list.data.items.map((e) => (
              <ExtensionCard
                key={e.id}
                e={e}
                selected={selectedId === e.id}
                busy={busyId === e.id}
                onOpen={() => (onOpen ? onOpen(e) : openExtension(e))}
                {...(e.install_state === 'installed' || e.install_state === 'disabled'
                  ? {
                      onToggle: (v: boolean) =>
                        void act(
                          e,
                          () => (v ? extensionsApi.enable(e.id) : extensionsApi.disable(e.id)),
                          v ? undefined : { message: `已停用“${e.name}”`, redo: () => extensionsApi.enable(e.id) },
                        ),
                    }
                  : {})}
                onConnect={() =>
                  void act(e, () => {
                    if (e.install_state === 'not_installed') return extensionsApi.install(e.id);
                    if (e.connection) return extensionsApi.connect(e.connection.id);
                    return extensionsApi.install(e.id);
                  })
                }
              />
            ))}
          </div>
        )}
      </div>
    </>
  );
}
