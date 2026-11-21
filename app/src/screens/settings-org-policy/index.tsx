import { useEffect, useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { Menu, MenuItem, Popover } from '../../components/Menu/MenuItem';
import { Select } from '../../components/Select/Select';
import { EmptyState, ErrorBanner, errorMessage, QueryGate } from '../../components/States/States';
import { fmtDateTime } from '../../features/billing/format';
import { OrgFrame, isForbidden } from '../../features/settings/OrgFrame';
import { SettingsRow, SettingsSection } from '../../features/settings/SettingsLayout';

import { downloadAuditExport, getAudit, getPolicy, patchPolicy, type AuditEvent, type Policy } from '../../features/settings/orgApi';
import { useSaveStatus } from '../../features/settings/useProfile';

const APPROVAL_ACTIONS: Array<{ value: string; label: string }> = [
  { value: 'payment', label: '支付' },
  { value: 'external_publish', label: '对外发布' },
  { value: 'install_third_party_extension', label: '安装第三方扩展' },
];

/** Publisher tiers behind each 扩展白名单 choice (Q-D-18 is still open; tiers are what the backend enforces). */
const TIER_PRESETS: Array<{ value: string; label: string; tiers: string[] }> = [
  { value: 'all', label: '不限制', tiers: [] },
  { value: 'reviewed', label: '官方与已审核', tiers: ['official', 'community_reviewed', 'enterprise'] },
  { value: 'official', label: '仅官方', tiers: ['official', 'enterprise'] },
];

const RETENTION: Array<{ value: string; label: string }> = [
  { value: 'forever', label: '永久' },
  { value: '365', label: '1 年' },
  { value: '180', label: '180 天' },
  { value: '90', label: '90 天' },
];

const ACTION_LABEL: Record<string, string> = {
  'org.policy.update': '修改策略',
  'org.quota.adjust': '调整额度',
  'member.offboard': '办理离职',
  'member.remove': '办理离职',
  'member.role.change': '调整角色',
  'member.department.change': '调整部门',
  'memory.purge': '删除记忆',
  'authorization.approve': '审批',
  'authorization.reject': '审批',
  'audit.export': '导出审计',
  'business_invitation.created': '发出邀请',
  'business_invitation.accepted': '接受邀请',
  'business_invitation.revoked': '撤销邀请',
  'membership.removed': '移除成员',
  'seats.reduced': '减少席位',
  'service_refund.requested': '申请退款',
  'service_refund.approved': '批准退款',
};

function tierPreset(tiers: string[]): string {
  const key = [...tiers].sort().join(',');
  return TIER_PRESETS.find((p) => [...p.tiers].sort().join(',') === key)?.value ?? 'custom';
}

/** 77 设置 · 企业管理 · 策略与审计 (759:2). */
export default function Screen(_props: ScreenProps) {
  const save = useSaveStatus();
  const q = useApiQuery((s) => getPolicy(s), []);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [actionsMenu, setActionsMenu] = useState<{ x: number; y: number } | null>(null);
  const audit = useAudit();
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  useEffect(() => {
    if (q.data) {
      setPolicy(q.data);
      save.markSaved();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data]);

  const patch = async (changes: Partial<Policy>) => {
    if (!policy) return;
    const before = policy;
    setPolicy({ ...policy, ...changes });
    const out = await save.run(() => patchPolicy(before.version, changes));
    if (out) {
      setPolicy(out);
      audit.reload();
    } else {
      setPolicy(before);
      if (save.message?.includes('其他人修改')) q.reload();
    }
  };

  const toggleAction = (value: string) => {
    if (!policy) return;
    const has = policy.approval_required_actions.includes(value);
    const order = APPROVAL_ACTIONS.map((a) => a.value);
    const next = has
      ? policy.approval_required_actions.filter((v) => v !== value)
      : [...policy.approval_required_actions, value].sort((a, b) => order.indexOf(a) - order.indexOf(b));
    void patch({ approval_required_actions: next });
  };

  const forbidden = isForbidden(q.error) && !q.data;
  const readOnly = !policy?.can_edit;
  const actionsText = policy
    ? policy.approval_required_actions.map((v) => APPROVAL_ACTIONS.find((a) => a.value === v)?.label ?? v).join('、') || '不需要审批'
    : '';
  const preset = policy ? tierPreset(policy.extension_allowed_tiers) : 'all';

  return (
    <OrgFrame tab="policy" save={{ status: save.status, message: save.message }} forbidden={forbidden}>
      <SettingsSection title="策略" description={policy && readOnly ? '只有所有者和安全与策略管理员可以修改' : undefined}>
        <QueryGate q={q} hasData={!!policy} rows={4}>
          {policy && (
            <>
              <SettingsRow label="需要审批的操作" description="命中后会进入审批人的“收件 · 需要我决定”">
                <Button
                  variant="secondary"
                  size={36}
                  label={actionsText}
                  trailingIcon="chevron-down"
                  disabled={readOnly}
                  onClick={(e) => {
                    const r = e.currentTarget.getBoundingClientRect();
                    setActionsMenu({ x: r.right - 220, y: r.bottom + 4 });
                  }}
                />
              </SettingsRow>
              <SettingsRow label="扩展白名单" description="插件 / 技能 / 连接器 / 智能体分别设置；不在名单内的不能安装">
                <Select
                  size={36}
                  width={140}
                  label="扩展白名单"
                  disabled={readOnly}
                  value={preset}
                  options={[
                    ...TIER_PRESETS.map((p) => ({ value: p.value, label: p.label })),
                    ...(preset === 'custom' ? [{ value: 'custom', label: '自定义' }] : []),
                  ]}
                  onChange={(v) => {
                    const p = TIER_PRESETS.find((x) => x.value === v);
                    if (p) void patch({ extension_allowed_tiers: p.tiers });
                  }}
                />
              </SettingsRow>
              <SettingsRow label="自建专家的最大共享范围" description="成员都可以自建专家；这里只限制可以分享给谁">
                <Select
                  size={36}
                  width={104}
                  label="自建专家的最大共享范围"
                  disabled={readOnly}
                  value={policy.expert_share_max_scope}
                  options={[
                    { value: 'only_me', label: '仅自己' },
                    { value: 'department', label: '部门内' },
                    { value: 'account', label: '企业内' },
                  ]}
                  onChange={(v) => void patch({ expert_share_max_scope: v as Policy['expert_share_max_scope'] })}
                />
              </SettingsRow>
              <SettingsRow label="任务与成果保留时长">
                <Select
                  size={36}
                  width={92}
                  label="任务与成果保留时长"
                  disabled={readOnly}
                  value={policy.retention_days === null ? 'forever' : String(policy.retention_days)}
                  options={[
                    ...RETENTION,
                    ...(policy.retention_days !== null && !RETENTION.some((r) => r.value === String(policy.retention_days))
                      ? [{ value: String(policy.retention_days), label: `${policy.retention_days} 天` }]
                      : []),
                  ]}
                  onChange={(v) => void patch({ retention_days: v === 'forever' ? null : Number(v) })}
                />
              </SettingsRow>
            </>
          )}
        </QueryGate>
      </SettingsSection>

      <SettingsSection
        title="审计日志"
        actions={
          <Button
            variant="secondary"
            size={32}
            label="导出近 90 天"
            disabled={forbidden}
            busy={exporting}
            onClick={async () => {
              setExporting(true);
              setExportError(null);
              try {
                await downloadAuditExport();
                audit.reload();
              } catch (err) {
                setExportError(errorMessage(err, '导出失败，请重试'));
              } finally {
                setExporting(false);
              }
            }}
          />
        }
      >
        {exportError && <ErrorBanner message={exportError} />}
        <QueryGate q={{ ...audit.first, reload: audit.reload }} rows={4}>
          {audit.items.length === 0 ? (
            <div className="sg-settings__state">
              <EmptyState icon="file" title="还没有审计记录" description="调整额度、修改策略、审批与办理离职会记录在这里" />
            </div>
          ) : (
            <div className="sg-orgtable sg-orgtable--audit" role="table" aria-label="审计日志">
              <div className="sg-orgtable__row is-head" role="row">
                <span role="columnheader">时间</span>
                <span role="columnheader">操作人</span>
                <span role="columnheader">操作</span>
                <span role="columnheader">内容</span>
              </div>
              {audit.items.map((e) => (
                <div className="sg-orgtable__row" role="row" key={e.id}>
                  <span role="cell" className="is-muted">{fmtDateTime(e.created_at)}</span>
                  <span role="cell" className="truncate">{e.actor?.display_name ?? '系统'}</span>
                  <span role="cell">{ACTION_LABEL[e.action] ?? e.action}</span>
                  <span role="cell" className="is-soft truncate" title={e.summary}>{e.summary || '—'}</span>
                </div>
              ))}
            </div>
          )}
          {audit.error && <ErrorBanner message={audit.error} onRetry={() => void audit.more()} />}
          {audit.next !== null && <Button variant="ghost" size={32} label="加载更多" busy={audit.loading} onClick={() => void audit.more()} />}
        </QueryGate>
      </SettingsSection>

      {actionsMenu && policy && (
        <Popover at={actionsMenu} width={220} onClose={() => setActionsMenu(null)}>
          <Menu label="需要审批的操作">
            {APPROVAL_ACTIONS.map((a) => (
              <MenuItem
                key={a.value}
                icon="square-check"
                label={a.label}
                checked={policy.approval_required_actions.includes(a.value)}
                onSelect={() => toggleAction(a.value)}
              />
            ))}
          </Menu>
        </Popover>
      )}
    </OrgFrame>
  );
}

function useAudit() {
  const [nonce, setNonce] = useState(0);
  const first = useApiQuery((s) => getAudit(0, s), [nonce]);
  const [items, setItems] = useState<AuditEvent[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (first.data) {
      setItems(first.data.items);
      setNext(first.data.next_offset);
    }
  }, [first.data]);
  const more = async () => {
    if (next === null) return;
    setLoading(true);
    setError(null);
    try {
      const page = await getAudit(next);
      setItems((x) => [...x, ...page.items]);
      setNext(page.next_offset);
    } catch (err) {
      setError(errorMessage(err, '加载失败，请重试'));
    } finally {
      setLoading(false);
    }
  };
  return { first, items, next, loading, error, more, reload: () => setNonce((n) => n + 1) };
}
