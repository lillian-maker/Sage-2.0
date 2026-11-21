import { useEffect, useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Avatar } from '../../components/Avatar/Avatar';
import { BackLink } from '../../components/BackLink/BackLink';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { ErrorBanner, errorMessage, QueryGate } from '../../components/States/States';
import { Tab } from '../../components/Tabs/Tab';
import { expertsApi, type ExpertNetwork, type Relation } from '../../features/experts/api';
import { DPage, MutedLabel, chatWith } from '../../features/experts/parts';
import '../../features/experts/network.css';
import '../../features/experts/profile.css';

const REL_META: Record<Relation, { label: string; cls: string; dashed?: boolean }> = {
  handoff: { label: '常交接', cls: 'handoff' },
  reviews_me: { label: '复核我', cls: 'reviews', dashed: true },
  upstream: { label: '上游', cls: 'stream' },
  downstream: { label: '下游', cls: 'stream' },
  reports_to: { label: '汇报', cls: 'reports' },
};

function edgeText(p: ExpertNetwork['peers'][number]): string {
  if (p.relation === 'handoff')
    return p.acceptance_rate !== null ? `常交接 · ${p.collaboration_count} 次 · ${p.acceptance_rate}%` : `常交接 · ${p.collaboration_count} 次`;
  if (p.relation === 'reviews_me') return `复核我 · ${p.collaboration_count} 次`;
  return REL_META[p.relation].label;
}

function railRight(p: ExpertNetwork['peers'][number]): string {
  if (p.relation === 'reviews_me' && p.return_count > 0) return `${p.collaboration_count} 次 · 退回 ${p.return_count} 次`;
  if (p.relation === 'handoff' && p.acceptance_rate !== null) return `${p.collaboration_count} 次 · 验收通过 ${p.acceptance_rate}%`;
  return `${p.collaboration_count} 次`;
}

function NetworkCanvas({ data, onOpen }: { data: ExpertNetwork; onOpen: (id: string) => void }) {
  const peers = data.peers.slice(0, 8);
  const W = 640;
  const H = 420;
  const cx = W / 2;
  const cy = H / 2;
  const R = 165;
  return (
    <div className="sg-net__canvas" style={{ aspectRatio: `${W} / ${H}` }}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="协作网络图">
        {peers.map((p, i) => {
          const a = (i / peers.length) * Math.PI * 2 - Math.PI / 2;
          const x = cx + Math.cos(a) * R;
          const y = cy + Math.sin(a) * (R * 0.62);
          const meta = REL_META[p.relation];
          return (
            <g key={p.expert.id}>
              <line x1={cx} y1={cy} x2={x} y2={y} className={`sg-net__edge is-${meta.cls}`} strokeDasharray={meta.dashed ? '5 4' : undefined} />
              <text x={(cx + x) / 2} y={(cy + y) / 2 - 6} className="sg-net__edgelabel" textAnchor="middle">
                {edgeText(p)}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="sg-net__center">
        <Avatar size={48} src={data.center.avatar_ref} alt={data.center.name} ring />
        <b>{data.center.name}</b>
      </div>
      {peers.map((p, i) => {
        const a = (i / peers.length) * Math.PI * 2 - Math.PI / 2;
        const x = 50 + Math.cos(a) * (R / W) * 100;
        const y = 50 + Math.sin(a) * (R * 0.62 / H) * 100;
        return (
          <button key={p.expert.id} type="button" className="sg-net__peer" style={{ left: `${x}%`, top: `${y}%` }} onClick={() => onOpen(p.expert.id)}>
            <Avatar size={34} src={p.expert.avatar_ref} alt={p.expert.name} />
            <b>{p.expert.name}</b>
            <span>{p.expert.specialty || p.expert.title}</span>
          </button>
        );
      })}
    </div>
  );
}

export default function Screen({ query, navigate }: ScreenProps) {
  const requested = query.get('expert') ?? '';
  const list = useApiQuery((signal) => (requested ? Promise.resolve(null) : expertsApi.list({}, signal)), [requested]);
  // 不带 expert 参数时，默认显示近 90 天有协作记录的专家（目录第一位可能没有）。
  const [defaultId, setDefaultId] = useState('');
  useEffect(() => {
    if (requested || !list.data || defaultId) return;
    let cancelled = false;
    const candidates = list.data.items.slice(0, 16);
    void Promise.all(
      candidates.map((e) =>
        expertsApi.network(e.id).then(
          (n) => ({ id: e.id, peers: n.peers.length }),
          () => ({ id: e.id, peers: -1 }),
        ),
      ),
    ).then((rows) => {
      if (cancelled) return;
      setDefaultId((rows.find((r) => r.peers > 0) ?? rows[0])?.id ?? '');
    });
    return () => {
      cancelled = true;
    };
  }, [requested, list.data, defaultId]);
  const expertId = requested || defaultId;
  const net = useApiQuery((signal) => (expertId ? expertsApi.network(expertId, signal) : Promise.resolve(null)), [expertId]);
  const detail = useApiQuery((signal) => (expertId ? expertsApi.detail(expertId, signal) : Promise.resolve(null)), [expertId]);
  const stats = detail.data?.stats;

  const data = net.data;
  return (
    <DPage
      railWidth={320}
      rail={
        data ? (
          <>
            <MutedLabel>常见协作 · 近 {data.window_days} 天</MutedLabel>
            {data.peers.length === 0 && <p className="sg-dmuted">近 {data.window_days} 天还没有协作记录</p>}
            {data.peers.map((p) => (
              <button key={p.expert.id} type="button" className="sg-drow" onClick={() => navigate(`/experts/${encodeURIComponent(p.expert.id)}`)}>
                <Avatar size={24} src={p.expert.avatar_ref} alt={p.expert.name} />
                <span className="sg-drow__text">
                  <span className="sg-drow__name">{p.expert.name}</span>
                  <span className="sg-drow__sub">{p.summary}</span>
                </span>
                <span className="sg-drow__right">{railRight(p)}</span>
              </button>
            ))}
            <div className="sg-dnote" style={{ marginTop: 16 }}>
              <Icon name="info" size={14} />
              <span>协作关系由验收结果自动更新；你也可以在编排视图里改派专家，不受这里的推荐限制。</span>
            </div>
          </>
        ) : undefined
      }
    >
      <BackLink label="专家" size={30} onBack={() => navigate(expertId ? `/experts/${encodeURIComponent(expertId)}` : '/experts')} />
      {net.status === 'error' && <ErrorBanner message={errorMessage(net.error, '协作网络暂时无法读取')} onRetry={net.reload} />}
      {data ? (
        <>
          <header className="sg-eprof">
            <Avatar size={48} src={data.center.avatar_ref} alt={data.center.name} />
            <div className="sg-eprof__name">
              <h1 style={{ font: 'var(--type-sans-sc-20-500-lh22-5, var(--type-sans-sc-24-500-lh28-8))' }}>{data.center.name}</h1>
              <p>{data.center.specialty || data.center.title} · 系统专家</p>
            </div>
            <div className="sg-eprof__actions">
              <Button variant="primary" size={32} icon="message-square" label="对话" onClick={() => chatWith(data.center.id)} />
            </div>
          </header>
          <div className="sg-tabs" role="tablist" aria-label="专家资料">
            <Tab size={37} label="资料" selected={false} onSelect={() => navigate(`/experts/${encodeURIComponent(expertId)}`)} />
            <Tab size={37} label="技能" {...(stats ? { count: stats.skill_count } : {})} selected={false} onSelect={() => navigate(`/experts/${encodeURIComponent(expertId)}?tab=skills`)} />
            <Tab size={37} label="协作网络" selected onSelect={() => undefined} />
            <Tab size={37} label="历史任务" {...(stats ? { count: stats.history_task_count } : {})} selected={false} onSelect={() => navigate(`/experts/${encodeURIComponent(expertId)}?tab=tasks`)} />
          </div>
          <div style={{ marginTop: 24 }}>
            <NetworkCanvas data={data} onOpen={(id) => navigate(`/experts/${encodeURIComponent(id)}`)} />
            <div className="sg-net__legend">
              <span className="sg-net__swatch is-handoff" /> 常交接
              <span className="sg-net__swatch is-reviews" /> 复核关系
              <span className="sg-net__swatch is-stream" /> 上下游
              <span className="sg-net__swatch is-reports" /> 汇报
              <span className="sg-net__note">中心为当前专家；依据近 {data.window_days} 天的任务记录，编排时用于推荐协作专家</span>
            </div>
          </div>
        </>
      ) : (
        <div className="sg-dstate">
          <QueryGate q={net} label="协作网络" rows={3} />
        </div>
      )}
    </DPage>
  );
}
