import { useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useShell } from '../../app/AppShell';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Avatar } from '../../components/Avatar/Avatar';
import { BackLink } from '../../components/BackLink/BackLink';
import { Button } from '../../components/Button/Button';
import { ConfirmDialog } from '../../components/ConfirmDialog/ConfirmDialog';
import { Icon } from '../../components/Icon/Icon';
import { PageHeader } from '../../components/PageHeader/PageHeader';
import { Select } from '../../components/Select/Select';
import { EmptyState, ErrorBanner, LoadingState, errorMessage } from '../../components/States/States';
import { Tag } from '../../components/Tag/Tag';
import { TextField } from '../../components/TextField/TextField';
import { inboxApi, type Initiative, type SourceDetail } from '../../features/inbox/api';
import { INITIATIVES, SENDER_FILTERS, SOURCE_ICON, SOURCE_LABEL } from '../../features/inbox/format';
import '../../features/inbox/inbox.css';

const STATE_TAG: Record<string, { label: string; tone: 'success' | 'warning' | 'neutral' }> = {
  connected: { label: '已连接', tone: 'success' },
  disconnected: { label: '已断开', tone: 'neutral' },
  revoked: { label: '已收回', tone: 'warning' },
};

function ChipList({ items, disabled, addLabel, placeholder, onChange }: { items: string[]; disabled: boolean; addLabel: string; placeholder: string; onChange: (next: string[]) => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    const v = (draft ?? '').trim();
    if (v && !items.includes(v)) onChange([...items, v]);
    setDraft(null);
  };
  return (
    <div className="sg-inchips">
      {items.map((v) => (
        <span key={v} className="sg-insource-detail__chip">
          {v}
          {!disabled && (
            <button type="button" aria-label={`移除 ${v}`} onClick={() => onChange(items.filter((x) => x !== v))}>
              <Icon name="x" size={10} />
            </button>
          )}
        </span>
      ))}
      {!disabled &&
        (draft == null ? (
          <button type="button" className="sg-insource-detail__chip is-add" onClick={() => setDraft('')}>
            <Icon name="plus" size={10} />
            {addLabel}
          </button>
        ) : (
          <span className="sg-insource-detail__chip-input">
            <TextField size={32} aria-label={addLabel} value={draft} placeholder={placeholder} onValueChange={setDraft} onKeyDown={(e) => e.key === 'Enter' && commit()} onBlur={commit} autoFocus />
          </span>
        ))}
    </div>
  );
}

function SourceEditor({ id, navigate, onSaved }: { id: string; navigate: (to: string) => void; onSaved: () => void }) {
  const detail = useApiQuery((s) => inboxApi.source(id, s), [id]);
  const [form, setForm] = useState<{ groups: string[]; keywords: string[]; sender: string; initiative: Initiative } | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);

  const save = useSubmit(async (source: SourceDetail, current: { groups: string[]; keywords: string[]; sender: string; initiative: Initiative }) => {
    await inboxApi.patchSource(id, {
      version: source.version,
      watched_groups: current.groups,
      keywords: current.keywords,
      sender_filter: current.sender,
      initiative: current.initiative,
    });
    onSaved();
    navigate('/inbox');
  });
  const disconnect = useSubmit(async () => {
    await inboxApi.disconnect(id);
    onSaved();
    navigate('/inbox');
  });

  if (detail.status === 'loading' && !detail.data) return <LoadingState rows={4} />;
  if (detail.status === 'error' && !detail.data)
    return <ErrorBanner message={errorMessage(detail.error, '来源详情暂时无法读取')} onRetry={detail.reload} />;
  const s = detail.data?.source;
  if (!s) return <EmptyState icon="settings" title="这个来源不存在" />;

  const label = SOURCE_LABEL[s.kind] ?? s.kind;
  const current = form ?? { groups: s.watched_groups, keywords: s.keywords, sender: s.sender_filter, initiative: s.initiative };
  const editable = !s.disconnected_at && s.connection_state === 'connected';
  const funnel = s.funnel_7d;

  return (
    <div className="sg-insource-detail">
      <BackLink label="来源" size={30} onBack={() => navigate('/inbox/sources')} />
      <div className="sg-insource-detail__head">
        <div className="sg-insource-detail__headtext">
          <h1 className="sg-insource-detail__title">来源设置 · {label}</h1>
          <p className="sg-insource-detail__lead">先用规则过滤，再由专家按职责判断；任何档位都不会在{label}里发言或外发，结果一律进入收件</p>
        </div>
        <Tag {...(STATE_TAG[s.connection_state] ?? STATE_TAG.disconnected!)} size={20} />
        {editable && <Button variant="ghost" size={32} label="断开" onClick={() => setConfirmDisconnect(true)} />}
        <Button variant="primary" size={32} label="保存" busy={save.submitting} disabled={!editable} onClick={() => void save.submit(s, current)} />
      </div>

      <div className="sg-infield">
        <span className="sg-infield__label">监听范围</span>
        <ChipList items={current.groups} disabled={!editable} addLabel="添加群" placeholder="#群名称" onChange={(groups) => setForm({ ...current, groups })} />
      </div>
      <div className="sg-infield">
        <span className="sg-infield__label">关键词</span>
        <ChipList items={current.keywords} disabled={!editable} addLabel="添加关键词" placeholder="关键词" onChange={(keywords) => setForm({ ...current, keywords })} />
      </div>
      <div className="sg-infield">
        <span className="sg-infield__label">发送人</span>
        <Select
          size={36}
          label="发送人"
          value={current.sender}
          options={SENDER_FILTERS}
          disabled={!editable}
          onChange={(v) => setForm({ ...current, sender: v })}
        />
      </div>
      {s.responsible_experts.length > 0 && (
        <div className="sg-infield">
          <span className="sg-infield__label">负责的专家</span>
          <div className="sg-incard__row" style={{ gap: 12 }}>
            {s.responsible_experts.map((e) => (
              <span key={e.id} className="sg-incard__row">
                <Avatar size={20} alt={e.name} src={e.avatar_ref ?? null} />
                <span className="sg-incard__expert-name">{e.name}</span>
              </span>
            ))}
          </div>
        </div>
      )}
      <div className="sg-infield">
        <span className="sg-infield__label">主动程度</span>
        <div className="sg-insource-detail__levels" role="radiogroup" aria-label="主动程度">
          {INITIATIVES.map((o) => (
            <button
              key={o.value}
              type="button"
              role="radio"
              aria-checked={current.initiative === o.value}
              className={`sg-insource-detail__level${current.initiative === o.value ? ' is-selected' : ''}`}
              disabled={!editable}
              onClick={() => setForm({ ...current, initiative: o.value })}
            >
              <strong>{o.value === 'report_only' ? `${o.label}（默认）` : o.label}</strong>
              <span>{o.hint}</span>
            </button>
          ))}
        </div>
      </div>

      <section>
        <div className="sg-indetail__section-label">过去 7 天</div>
        <div className="sg-insource-detail__funnel" style={{ marginTop: 8 }}>
          {([
            ['收到的消息', funnel.received],
            ['规则过滤', funnel.after_rules],
            ['专家判断', funnel.after_expert],
            ['进入收件', funnel.admitted],
          ] as Array<[string, number]>).map(([name, n]) => (
            <div key={name} className="sg-insource-detail__funnel-cell">
              <strong className="sg-insource-detail__funnel-num">{n.toLocaleString('en-US')}</strong>
              <span className="sg-insource-detail__funnel-label">{name}</span>
            </div>
          ))}
        </div>
      </section>

      {save.error && <ErrorBanner message={errorMessage(save.error, '保存失败，请重试')} />}
      {disconnect.error && <ErrorBanner message={errorMessage(disconnect.error, '断开失败，请重试')} />}
      {confirmDisconnect && (
        <ConfirmDialog
          title={`断开${label}？`}
          message="断开后不再拉取新消息；已经进入收件的内容会保留。"
          confirmLabel="断开连接"
          cancelLabel="取消"
          tone="warning"
          busy={disconnect.submitting}
          onCancel={() => setConfirmDisconnect(false)}
          onConfirm={() => {
            setConfirmDisconnect(false);
            void disconnect.submit();
          }}
        />
      )}
    </div>
  );
}

export default function Screen({ query, navigate }: ScreenProps) {
  const shell = useShell();
  const page = useApiQuery((s) => inboxApi.sources(s), []);
  const selected = query.get('source');

  if (selected) {
    return (
      <div style={{ height: '100%', overflow: 'auto' }}>
        <SourceEditor id={selected} navigate={navigate} onSaved={shell.refresh} />
      </div>
    );
  }

  let body;
  if (page.status === 'loading' && !page.data) body = <LoadingState rows={3} />;
  else if (page.status === 'error' && !page.data)
    body = <ErrorBanner message={errorMessage(page.error, '来源暂时无法读取')} onRetry={page.reload} />;
  else if ((page.data?.items.length ?? 0) === 0)
    body = <EmptyState icon="link" title="还没有连接任何来源" description="连接后，外部消息会按你的规则进入收件" action={{ label: '去连接', onClick: () => navigate('/extensions') }} />;
  else
    body = (
      <>
        {page.status === 'error' && <ErrorBanner message={errorMessage(page.error, '刷新失败')} onRetry={page.reload} />}
        {(page.data?.items ?? []).map((s) => {
          const state = s.disconnected_at ? 'disconnected' : s.connection_state;
          return (
            <button
              key={s.id}
              type="button"
              className="sg-insource"
              style={{ width: '100%', textAlign: 'left', cursor: 'pointer', background: 'none' }}
              onClick={() => navigate(`/inbox/sources?source=${s.id}`)}
            >
              <Icon name={SOURCE_ICON[s.kind]} size={20} />
              <span className="sg-insource__text">
                <span className="sg-insource__name">{SOURCE_LABEL[s.kind] ?? s.kind}</span>
                <span className="sg-insource__meta">
                  {INITIATIVES.find((o) => o.value === s.initiative)?.label ?? s.initiative}
                  {s.responsible_experts.length > 0 ? ` · 负责 ${s.responsible_experts.map((e) => e.name).join('、')}` : ''}
                </span>
              </span>
              <span className="sg-incard__experts">
                {s.responsible_experts.slice(0, 3).map((e) => (
                  <Avatar key={e.id} size={18} alt={e.name} src={e.avatar_ref ?? null} ring />
                ))}
              </span>
              <Tag {...(STATE_TAG[state] ?? STATE_TAG.disconnected!)} size={20} />
            </button>
          );
        })}
      </>
    );

  return (
    <div style={{ height: '100%', overflow: 'auto' }}>
      <div className="sg-insources">
        <PageHeader title="来源设置" description="决定哪些外部消息进入收件，以及 Sage 可以主动做到哪一步" />
        {body}
      </div>
    </div>
  );
}
