import { useMemo, useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { SageMark } from '../../components/Icon/SageMark';
import { PageHeader } from '../../components/PageHeader/PageHeader';
import { SearchField } from '../../components/SearchField/SearchField';
import { EmptyState, ErrorBanner, errorMessage, QueryGate } from '../../components/States/States';
import { Tab } from '../../components/Tabs/Tab';
import { Tag } from '../../components/Tag/Tag';
import { allPages, expertsApi, type ExpertItem, type ExpertList } from '../../features/experts/api';
import { DPage, ExpertCard, SectionHead, TextMenu, chatWith } from '../../features/experts/parts';

export const EXPERT_TABS = [
  { key: 'all', label: '全部' },
  { key: 'management', label: '管理身份' },
  { key: 'role', label: '岗位专家' },
  { key: 'teams', label: '团队' },
  { key: 'mine', label: '我创建的' },
  { key: 'shared', label: '共享给我' },
] as const;
type Group = 'all' | 'management' | 'role' | 'mine' | 'shared';

async function loadAll(group: Group, domain: string, q: string, signal: AbortSignal) {
  let head: ExpertList | null = null;
  const items = await allPages<ExpertItem>(async (offset) => {
    const p = await expertsApi.list({ group, domain: domain || undefined, q: q || undefined, offset: offset || undefined }, signal);
    head ??= p;
    return p;
  });
  return { ...(head as unknown as ExpertList), items };
}

export default function Screen({ query, navigate }: ScreenProps) {
  const rawGroup = query.get('group');
  const group: Group = (['management', 'role', 'mine', 'shared'] as const).find((g) => g === rawGroup) ?? 'all';
  const domain = query.get('domain') ?? '';
  const q = query.get('q') ?? '';
  const briefId = query.get('expert');
  const [draft, setDraft] = useState(q);

  const list = useApiQuery((signal) => loadAll(group, domain, q, signal), [group, domain, q]);
  const brief = useApiQuery((signal) => (briefId ? expertsApi.detail(briefId, signal) : Promise.resolve(null)), [briefId]);

  const setParams = (next: Record<string, string | null>) => {
    const p = new URLSearchParams(query);
    for (const [k, v] of Object.entries(next)) {
      if (v) p.set(k, v);
      else p.delete(k);
    }
    const s = p.toString();
    navigate('/experts' + (s ? '?' + s : ''), { replace: !('expert' in next) });
  };

  const sections = useMemo(() => {
    const items = list.data?.items ?? [];
    if (group !== 'all' || q || domain) return [{ title: '', items }];
    const out: Array<{ title: string; items: ExpertItem[] }> = [];
    const mgmt = items.filter((e) => e.group === 'management');
    if (mgmt.length) out.push({ title: '管理身份', items: mgmt });
    const rest = items.filter((e) => e.group !== 'management');
    const order = (list.data?.domain_counts ?? []).map((d) => d.domain);
    const byDomain = new Map<string, ExpertItem[]>();
    for (const e of rest) {
      const key = e.domain || '其他';
      byDomain.set(key, [...(byDomain.get(key) ?? []), e]);
    }
    const keys = [...byDomain.keys()].sort((a, b) => (order.indexOf(a) + 1 || 999) - (order.indexOf(b) + 1 || 999));
    for (const k of keys) out.push({ title: k, items: byDomain.get(k) ?? [] });
    return out;
  }, [list.data, group, q, domain]);

  if (briefId) {
    return (
      <DPage>
        <div className="sg-ebrief">
          <Button variant="secondary" size={26} label="返回" onClick={() => setParams({ expert: null })} />
          {brief.data ? (
            <ExpertCard brief expert={brief.data} onOpen={() => navigate(`/experts/${encodeURIComponent(brief.data!.id)}`)} />
          ) : (
            <QueryGate q={brief} label="专家" rows={2} />
          )}
        </div>
      </DPage>
    );
  }

  const counts = list.data?.counts;
  // The headline counts the Sage catalog (management + role); custom experts are listed under 我创建的 / 共享给我.
  const total = counts ? counts.management + counts.role : undefined;
  const assistant = list.data?.assistant;
  const domainOptions = [{ value: '', label: '全部' }, ...(list.data?.domain_counts ?? []).map((d) => ({ value: d.domain, label: d.domain, hint: String(d.count) }))];

  return (
    <DPage>
      <PageHeader
        title="专家"
        description={total !== undefined ? `${total} 位数字专家，按职责找到合适的同事交办` : '按职责找到合适的同事交办'}
        actions={
          <>
            <SearchField
              size={32}
              width={280}
              placeholder="搜索名称、职责或技能"
              value={draft}
              clearable
              onValueChange={(v) => {
                setDraft(v);
                if (!v) setParams({ q: null });
              }}
              onSubmit={(v) => setParams({ q: v.trim() || null })}
            />
            <Button variant="primary" size={32} icon="plus" label="新建专家" onClick={() => navigate('/experts/new/start')} />
          </>
        }
      />
      <div className="sg-tabs" role="tablist" aria-label="专家分组">
        {EXPERT_TABS.map((t) => (
          <Tab
            key={t.key}
            size={37}
            label={t.label}
            selected={t.key === group}
            {...(t.key !== 'teams' && counts ? { count: counts[t.key] } : {})}
            onSelect={() => (t.key === 'teams' ? navigate('/experts/teams') : setParams({ group: t.key === 'all' ? null : t.key }))}
          />
        ))}
        <span className="sg-tabs__right">
          <TextMenu prefix="领域：" value={domain} options={domainOptions} onChange={(v) => setParams({ domain: v || null })} />
        </span>
      </div>
      {group === 'all' && !q && !domain && assistant && (
        <div className="sg-assist">
          <SageMark size={28} />
          <div className="sg-assist__text">
            <span className="sg-assist__title">
              {assistant.name}
              <Tag label={assistant.title || '兜底主责'} tone="neutral" size={18} />
            </span>
            <span className="sg-assist__desc">{assistant.responsibilities}</span>
          </div>
          <Button variant="secondary" size={32} label="对话" onClick={() => chatWith(assistant.id)} />
        </div>
      )}
      {list.status === 'error' && list.data && <ErrorBanner message={errorMessage(list.error, '专家列表刷新失败')} onRetry={list.reload} />}
      {!list.data ? (
        <div className="sg-dstate">
          <QueryGate q={list} label="专家列表" rows={4} />
        </div>
      ) : list.data.items.length === 0 ? (
        <EmptyState
          icon="users"
          title={q ? `没有找到与“${q}”相关的专家` : group === 'mine' ? '你还没有创建专家' : group === 'shared' ? '还没有共享给你的专家' : '这里还没有专家'}
          {...(group === 'mine' ? { action: { label: '新建专家', onClick: () => navigate('/experts/new/start') } } : {})}
        />
      ) : (
        sections.map((s) => (
          <section key={s.title || 'flat'}>
            {s.title ? <SectionHead title={s.title} count={`${s.items.length} 位`} /> : <div style={{ height: 22 }} />}
            <div className="sg-egrid">
              {s.items.map((e) => (
                <ExpertCard
                  key={e.id}
                  expert={e}
                  onOpen={() => setParams({ expert: e.id })}
                  onChat={() => chatWith(e.id)}
                  onTemplate={() => navigate(`/experts/new/start?template=${encodeURIComponent(e.id)}`)}
                />
              ))}
            </div>
          </section>
        ))
      )}
    </DPage>
  );
}
