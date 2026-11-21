import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { IconTile } from '../../components/IconTile/IconTile';
import type { IconName } from '../../components/Icon/Icon';
import { ErrorBanner, LoadingState, errorMessage } from '../../components/States/States';
import { inboxApi, type SourceKind } from '../../features/inbox/api';
import '../../features/inbox/inbox.css';

const CARDS: Array<{ kinds: SourceKind[]; icon: IconName; title: string; text: string }> = [
  { kinds: ['company_email'], icon: 'message-square', title: '企业邮箱', text: '客户、供应商、平台邮件' },
  { kinds: ['amazon_buyer_messages'], icon: 'contact', title: 'Amazon 买家消息', text: '买家咨询、退货与差评提醒' },
  { kinds: ['store_notifications'], icon: 'bell', title: '店铺通知', text: '绩效、政策与账户健康' },
  { kinds: ['feishu', 'wecom'], icon: 'message-square', title: '飞书 / 企业微信', text: '群消息与 @ 提醒' },
];

/** 首次使用收件：连接来源的引导页（有来源后从 /inbox/sources 管理）。 */
export default function Screen({ navigate }: ScreenProps) {
  const sources = useApiQuery((s) => inboxApi.sources(s), []);

  if (sources.status === 'loading' && !sources.data) return <div className="sg-taskpage__state"><LoadingState rows={3} /></div>;
  if (sources.status === 'error' && !sources.data)
    return (
      <div className="sg-taskpage__state">
        <ErrorBanner message={errorMessage(sources.error, '来源暂时无法读取')} onRetry={sources.reload} />
      </div>
    );

  const connected = new Set((sources.data?.items ?? []).map((s) => s.kind));
  // 连接 opens the connector's extension page, where connecting and managing happen; the source detail names it.
  const openConnector = (kinds: SourceKind[]) => {
    const source = (sources.data?.items ?? []).find((s) => kinds.includes(s.kind));
    if (!source) return navigate('/extensions');
    void inboxApi.source(source.id).then(
      (r) => navigate(`/extensions/${encodeURIComponent(r.source.connection.extension.id)}`),
      () => navigate('/extensions'),
    );
  };

  return (
    <div className="sg-insetup">
      <IconTile icon="download" size={36} tone="neutral" shape="rounded" />
      <h2 className="sg-indetail__title">把外部消息变成可以交办的待办</h2>
      <p className="sg-indetail__text sg-insetup__lead">
        连接来源后，Sage 会从中找出需要处理的消息，标出判断依据与把握度，你可以一键交给专家。所有来源只读，回复只生成草稿，不会替你发送。
      </p>
      <div className="sg-insetup__cards">
        {CARDS.map((card) => {
          const on = card.kinds.some((k) => connected.has(k));
          return (
            <div key={card.title} className="sg-insetup__card">
              <IconTile icon={card.icon} size={28} tone="neutral" shape="rounded" />
              <div className="sg-insource__name">{card.title}</div>
              <div className="sg-insource__meta">{card.text}</div>
              <Button
                variant="secondary"
                size={24}
                label={on ? '已连接' : '连接'}
                onClick={() => openConnector(card.kinds)}
              />
            </div>
          );
        })}
      </div>
      {(sources.data?.items.length ?? 0) > 0 && (
        <Button variant="ghost" size={32} label="管理已连接的来源" icon="settings" onClick={() => navigate('/inbox/sources')} />
      )}
      <div className="sg-insource__meta">也可以稍后在“扩展 › 连接器”中管理</div>
    </div>
  );
}
