import type { ReactNode } from 'react';
import { ApiError } from '../../api/client';
import { navigate } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { ForbiddenState } from '../../components/States/States';
import { Tab } from '../../components/Tabs/Tab';
import { currentSubscription, getSubscriptions } from '../billing/api';
import { planName } from '../billing/labels';
import { SettingsLayout, type SaveStatus } from './SettingsLayout';
import { activeAccount, useSession } from './common';
import './settings.css';

export type OrgTab = 'members' | 'quota' | 'policy';

const TABS: Array<{ key: OrgTab; label: string; path: string }> = [
  { key: 'members', label: '成员与部门', path: '/settings/org/members' },
  { key: 'quota', label: '额度', path: '/settings/org/quota' },
  { key: 'policy', label: '策略与审计', path: '/settings/org/policy' },
];

export function isForbidden(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 403 || err.code === 'FORBIDDEN' || err.code === 'PERMISSION_DENIED');
}

/** Shared 企业管理 header: scope line, the three tabs, and the gate for personal accounts / non-admins. */
export function OrgFrame({
  tab,
  save,
  forbidden,
  children,
}: {
  tab: OrgTab;
  save?: { status: SaveStatus; message?: string | null };
  forbidden?: boolean;
  children: ReactNode;
}) {
  const session = useSession();
  const acct = activeAccount(session);
  const subs = useApiQuery((s) => getSubscriptions(s), [acct?.id]);
  const sub = currentSubscription(subs.data?.items);
  const personal = !!acct && acct.kind !== 'business';
  const subtitle = (
    <span>
      只有企业管理员可以看到此页 · {acct?.name ?? '—'} · {sub ? planName(sub.plan_code) : subs.status === 'loading' ? '…' : '未订阅'}
    </span>
  );
  return (
    <SettingsLayout active="org" title="企业管理" save={save} subtitle={subtitle}>
      <div className="sg-tabs sg-orgtabs" role="tablist" aria-label="企业管理">
        {TABS.map((t) => (
          <Tab key={t.key} label={t.label} size={37} selected={t.key === tab} onSelect={() => t.key !== tab && navigate(t.path)} />
        ))}
      </div>
      {personal ? (
        <div className="sg-settings__state">
          <ForbiddenState title="企业管理只在企业账户中可用，请先在左下角切换到企业账户" />
        </div>
      ) : forbidden ? (
        <div className="sg-settings__state">
          <ForbiddenState title="只有企业管理员可以查看此页" />
        </div>
      ) : (
        children
      )}
    </SettingsLayout>
  );
}

/** Thin usage bar for quota tables; turns orange from 80% so near-exhausted rows stand out. */
export function UsageBar({ used, limit }: { used: number; limit: number | null }) {
  const ratio = limit && limit > 0 ? Math.min(1, used / limit) : 0;
  return (
    <span className="sg-orgbar" aria-hidden="true">
      <span className={ratio >= 0.8 ? 'is-warning' : ''} style={{ width: `${ratio * 100}%` }} />
    </span>
  );
}
