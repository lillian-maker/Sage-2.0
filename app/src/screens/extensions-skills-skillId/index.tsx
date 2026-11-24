import { useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Avatar } from '../../components/Avatar/Avatar';
import { Icon } from '../../components/Icon/Icon';
import { IconButton } from '../../components/IconButton/IconButton';
import { IconTile } from '../../components/IconTile/IconTile';
import { ErrorBanner, errorMessage, QueryGate } from '../../components/States/States';
import { extensionsApi } from '../../features/extensions/api';
import { ExtensionsBoard, KIND_ICON, extSub } from '../../features/extensions/ExtensionsBoard';
import { KvRow, MutedLabel } from '../../features/experts/parts';
import { ExtensionsHeader, KindTabs } from '../extensions/header';
import '../../features/extensions/board.css';

export default function Screen({ params, query, navigate }: ScreenProps) {
  const skillId = params.skillId!;
  const [q, setQ] = useState(query.get('q') ?? '');
  const detail = useApiQuery((signal) => extensionsApi.detail(skillId, signal), [skillId]);
  const d = detail.data;
  const counts = useApiQuery((signal) => extensionsApi.list({ tab: 'installed' }, signal), []);

  return (
    <div className="sg-dpage has-rail" style={{ gridTemplateColumns: 'minmax(0, 1fr) 400px' }}>
      <div className="sg-dpage__col">
        <div className="sg-dpage__top" />
        <div className="sg-dpage__body">
          <ExtensionsHeader
            navigate={navigate}
            tab="installed"
            installedCount={counts.data?.counts.installed}
            q={q}
            onSearch={setQ}
            description="插件给专家增加工具，技能教专家方法，连接器接入外部数据，智能体接入外部 Agent；连接与授权都会单独确认"
          />
          <KindTabs kind="skill" onChange={(k) => navigate(k ? `/extensions?kind=${k}` : '/extensions')} />
          <ExtensionsBoard tab="installed" kind="skill" q={q.trim()} selectedId={skillId} onOpen={(e) => navigate(`/extensions/skills/${encodeURIComponent(e.id)}`, { replace: true })} />
        </div>
      </div>
      <aside className="sg-xdrawer" aria-label="技能详情">
        <div className="sg-dpage__top">
          <IconButton icon="x" size={28} variant="ghost" label="关闭" onClick={() => navigate('/extensions?kind=skill')} />
        </div>
        <div className="sg-xdrawer__body">
          {!d ? (
            <QueryGate q={detail} label="技能" rows={3} />
          ) : (
            <>
              {detail.status === 'error' && <ErrorBanner message={errorMessage(detail.error, '技能刷新失败')} onRetry={detail.reload} />}
              <div className="sg-xdrawer__head">
                <IconTile icon={KIND_ICON[d.kind]} size={36} tone="neutral" shape="rounded" />
                <div className="sg-xdrawer__title">
                  <b>{d.name}</b>
                  <span>
                    技能 · {extSub(d)}
                  </span>
                </div>
              </div>
              <MutedLabel>在技能图谱中的位置</MutedLabel>
              <div className="sg-xchips">
                {d.prerequisite_inputs.map((p) => (
                  <span key={p} className="sg-xchip" style={{ cursor: 'default' }}>
                    <Icon name="file" size={12} /> {p}
                  </span>
                ))}
                {d.prerequisite_inputs.length > 0 && <Icon name="arrow-right" size={12} />}
                <span className="sg-xchip is-current">
                  <Icon name="zap" size={12} /> {d.name}
                </span>
              </div>
              {d.common_next.length > 0 && (
                <div className="sg-xchips" style={{ marginTop: 8 }}>
                  {d.common_next.map((n) => (
                    <button key={n.extension_id} type="button" className="sg-xchip" onClick={() => navigate(`/extensions/skills/${encodeURIComponent(n.extension_id)}`, { replace: true })}>
                      <Icon name="zap" size={12} /> {n.name}
                    </button>
                  ))}
                </div>
              )}
              <MutedLabel>前置条件</MutedLabel>
              <KvRow k="需要的资料">{d.prerequisite_inputs.length ? d.prerequisite_inputs.join('；') : '无'}</KvRow>
              <KvRow k="需要的连接">{d.required_connector ? d.required_connector.name : '无（只读公开页面）'}</KvRow>
              <MutedLabel>产出</MutedLabel>
              <KvRow k="成果">{d.outputs.length ? d.outputs.join('；') : '—'}</KvRow>
              <KvRow k="验收条件">{d.acceptance_template.length ? d.acceptance_template.join(' · ') : '—'}</KvRow>
              {d.common_next.length > 0 && (
                <>
                  <MutedLabel>常见后续技能</MutedLabel>
                  {d.common_next.map((n) => (
                    <button key={n.extension_id} type="button" className="sg-drow" onClick={() => navigate(`/extensions/skills/${encodeURIComponent(n.extension_id)}`, { replace: true })}>
                      <Icon name="zap" size={15} />
                      <span className="sg-drow__text">
                        <span className="sg-drow__name">{n.name}</span>
                      </span>
                      <span className="sg-drow__right">
                        {Math.round(n.ratio * 100)}%{n.needs_connection ? ' · 需连接数据源' : ' 的任务接着使用'}
                      </span>
                    </button>
                  ))}
                </>
              )}
              <MutedLabel>使用的专家</MutedLabel>
              {d.used_by_experts.length === 0 ? (
                <p className="sg-dmuted">还没有专家使用这个技能</p>
              ) : (
                <div style={{ display: 'flex', gap: 4 }}>
                  {d.used_by_experts.map((e) => (
                    <button key={e.id} type="button" title={e.name} onClick={() => navigate(`/experts/${encodeURIComponent(e.id)}`)}>
                      <Avatar size={24} src={e.avatar_ref} alt={e.name} ring />
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
