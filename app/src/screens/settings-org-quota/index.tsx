import { useEffect, useMemo, useState } from 'react';
import { ApiError } from '../../api/client';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Button } from '../../components/Button/Button';
import { EmptyState, errorMessage, QueryGate } from '../../components/States/States';
import { Switch } from '../../components/Switch/Switch';
import { TextField } from '../../components/TextField/TextField';
import { getBillingAccount } from '../../features/billing/api';
import { fmtCredits, fmtMonthDay, toNumber } from '../../features/billing/format';
import { SITE_LINKS, openSite } from '../../features/billing/site';
import { OrgFrame, UsageBar, isForbidden } from '../../features/settings/OrgFrame';
import { SettingsDialog } from '../../features/settings/common';
import {
  getDepartments,
  getOrganization,
  postDepartmentQuota,
  postMemberQuota,
  type Department,
  type OrgMember,
} from '../../features/settings/orgApi';
import { useSaveStatus } from '../../features/settings/useProfile';

const QUOTA_ERRORS: Record<string, string> = {
  QUOTA_BELOW_USED: '额度不能低于本月已用量',
  VERSION_CONFLICT: '已被其他人修改，请刷新后重试',
  LIMIT_CHANGED: '已被其他人修改，请刷新后重试',
  INVALID_LIMIT_VERSION: '已被其他人修改，请刷新后重试',
  SUBSCRIPTION_EXPIRED: '套餐已到期，暂时不能调整额度',
};

function quotaError(err: unknown): string {
  const known = err instanceof ApiError ? QUOTA_ERRORS[err.code] : undefined;
  if (known) return known;
  return errorMessage(err, '没有保存成功，请重试');
}

/** Whole non-negative Credits, or empty for “不设”. */
function parseQuota(text: string): number | null | 'invalid' {
  const t = text.replace(/,/g, '').trim();
  if (!t) return null;
  return /^\d{1,9}$/.test(t) ? Number(t) : 'invalid';
}

function memberName(m: OrgMember) {
  return m.display_name || m.masked_identifier || '未命名成员';
}

/** 76 设置 · 企业管理 · 额度 (745:2). */
export default function Screen(_props: ScreenProps) {
  const [nonce, setNonce] = useState(0);
  const save = useSaveStatus();
  const account = useApiQuery((s) => getBillingAccount(s), [nonce]);
  const depts = useApiQuery((s) => getDepartments(s), [nonce]);
  const org = useApiQuery((s) => getOrganization({ offset: 0 }, s), []);
  const [selected, setSelected] = useState<string | null>(null);
  const [items, setItems] = useState<Department[]>([]);
  const [editDept, setEditDept] = useState<Department | null>(null);
  const [editMember, setEditMember] = useState<{ member: OrgMember; dept: Department } | null>(null);

  useEffect(() => {
    if (!depts.data) return;
    setItems(depts.data.items);
    setSelected((s) => (s && depts.data!.items.some((d) => d.id === s) ? s : depts.data!.items[0]?.id ?? null));
  }, [depts.data]);

  const dept = items.find((d) => d.id === selected) ?? null;
  const reload = () => setNonce((n) => n + 1);

  const togglePooling = async (d: Department, pooling: boolean) => {
    setItems((xs) => xs.map((x) => (x.id === d.id ? { ...x, pooling_enabled: pooling } : x)));
    const out = await save.run(() =>
      postDepartmentQuota(d, { monthly_quota: d.monthly_quota, per_member_share: d.per_member_share, pooling_enabled: pooling }),
    );
    setItems((xs) =>
      xs.map((x) => (x.id === d.id ? (out ? { ...x, ...out } : { ...x, pooling_enabled: d.pooling_enabled }) : x)),
    );
  };

  const forbidden = (isForbidden(depts.error) && !depts.data) || (isForbidden(org.error) && !org.data);
  const allocated = depts.data?.allocated_total;

  return (
    <OrgFrame tab="quota" save={{ status: save.status, message: save.message }} forbidden={forbidden}>
      <QueryGate q={depts} rows={4}>
        <div className="sg-quotasum">
          <span className="sg-seats__cell">
            <b>{account.data ? fmtCredits(account.data.credits.available) : '—'}</b>
            <span>企业可用 Credits</span>
          </span>
          <span className="sg-seats__cell">
            <b>{allocated === undefined ? '—' : fmtCredits(allocated)}</b>
            <span>已分给部门 / 个人</span>
          </span>
          <span className="sg-seats__cell">
            <b>{org.data?.period_end ? fmtMonthDay(org.data.period_end) : '—'}</b>
            <span>本服务月结束</span>
          </span>
          <span className="sg-seats__actions">
            <Button variant="secondary" size={32} label="加购 ↗" onClick={() => openSite(SITE_LINKS.topup)} />
            <Button variant="primary" size={32} label="设置部门额度" disabled={!dept} onClick={() => dept && setEditDept(dept)} />
          </span>
        </div>
        {items.length === 0 ? (
          <div className="sg-settings__state">
            <EmptyState icon="users" title="还没有部门" description="先在“成员与部门”中新建部门，再为部门设置额度" />
          </div>
        ) : (
          <div className="sg-orgtable sg-orgtable--depts" role="table" aria-label="部门额度">
            <div className="sg-orgtable__row is-head" role="row">
              <span role="columnheader">部门</span>
              <span role="columnheader" className="is-num">人数</span>
              <span role="columnheader" className="is-num">本月额度</span>
              <span role="columnheader">已用</span>
              <span role="columnheader" className="is-num">每人份额</span>
              <span role="columnheader" className="is-num">部门内调剂</span>
            </div>
            {items.map((d) => {
              const used = toNumber(d.used_credits) ?? 0;
              return (
                <div
                  key={d.id}
                  role="row"
                  aria-selected={d.id === selected}
                  className={`sg-orgtable__row is-clickable${d.id === selected ? ' is-selected' : ''}`}
                  onClick={() => setSelected(d.id)}
                >
                  <span role="cell">{d.name}</span>
                  <span role="cell" className="is-num">{d.member_count}</span>
                  <span role="cell" className="is-num">{d.monthly_quota === null ? '不限' : fmtCredits(d.monthly_quota)}</span>
                  <span role="cell" className="sg-orgtable__usage">
                    <UsageBar used={used} limit={d.monthly_quota} />
                    {fmtCredits(used)}
                  </span>
                  <span role="cell" className="is-num">{d.per_member_share === null ? '—' : fmtCredits(d.per_member_share)}</span>
                  <span role="cell" className="is-num" onClick={(e) => e.stopPropagation()}>
                    <Switch checked={d.pooling_enabled} label={`${d.name} 部门内调剂`} onChange={(v) => void togglePooling(d, v)} />
                  </span>
                </div>
              );
            })}
          </div>
        )}
        {dept && <MemberQuotas key={`${dept.id}:${nonce}`} dept={dept} onEdit={(member) => setEditMember({ member, dept })} />}
      </QueryGate>

      {editDept && (
        <DeptQuotaDialog
          dept={editDept}
          onClose={() => setEditDept(null)}
          onSave={async (next) => {
            const out = await save.run(() => postDepartmentQuota(editDept, next));
            if (!out) return null;
            setEditDept(null);
            reload();
            return null;
          }}
          saveMessage={save.status === 'error' ? save.message : null}
          busy={save.status === 'saving'}
        />
      )}
      {editMember && (
        <MemberQuotaDialog
          member={editMember.member}
          dept={editMember.dept}
          onClose={() => setEditMember(null)}
          onDone={() => {
            setEditMember(null);
            save.markSaved();
            reload();
          }}
        />
      )}
    </OrgFrame>
  );
}

function MemberQuotas({ dept, onEdit }: { dept: Department; onEdit: (m: OrgMember) => void }) {
  const q = useApiQuery((s) => getOrganization({ offset: 0, department: dept.id }, s), [dept.id]);
  const [extra, setExtra] = useState<OrgMember[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setExtra([]);
    setNext(q.data?.next_offset ?? null);
  }, [q.data]);
  const members = useMemo(() => [...(q.data?.members ?? []), ...extra].filter((m) => m.active), [q.data, extra]);
  const share = dept.per_member_share;
  const half = share === null ? null : Math.floor(share / 2);
  const more = async () => {
    if (next === null) return;
    setBusy(true);
    try {
      const page = await getOrganization({ offset: next, department: dept.id });
      setExtra((x) => [...x, ...page.members]);
      setNext(page.next_offset);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="sg-quotapanel">
      <div className="sg-quotapanel__head">
        <h2>{dept.name} · 成员额度</h2>
        <span>
          {dept.pooling_enabled
            ? '调剂已打开：每人份额的一半只留给本人，另一半进入部门调剂池'
            : '调剂已关闭：每人份额只给本人使用'}
        </span>
      </div>
      <QueryGate q={q} rows={3}>
        {members.length === 0 ? (
          <div className="sg-offboard__empty">这个部门还没有成员</div>
        ) : (
          <div className="sg-orgtable sg-orgtable--memberquota" role="table" aria-label={`${dept.name} 成员额度`}>
            <div className="sg-orgtable__row is-head" role="row">
              <span role="columnheader">成员</span>
              <span role="columnheader">本月额度</span>
              <span role="columnheader">保底 · 可调剂</span>
              <span role="columnheader">已用</span>
              <span role="columnheader" aria-label="操作" />
            </div>
            {members.map((m) => {
              const own = toNumber(m.shared_limit);
              const limit = own ?? share;
              const used = toNumber(m.shared_used) ?? 0;
              return (
                <div className="sg-orgtable__row" role="row" key={m.id}>
                  <span role="cell" className="truncate">{memberName(m)}</span>
                  <span role="cell">
                    {own !== null ? `${fmtCredits(own)}（单独设定）` : share !== null ? `${fmtCredits(share)}（部门人均）` : '不限'}
                  </span>
                  <span role="cell" className="is-muted">
                    {own === null && dept.pooling_enabled && half !== null ? `${fmtCredits(half)} · ${fmtCredits(share! - half)}` : '—'}
                  </span>
                  <span role="cell" className="sg-orgtable__usage">
                    <UsageBar used={used} limit={limit} />
                    {fmtCredits(used)}
                  </span>
                  <span role="cell" className="is-end">
                    <button type="button" className="sg-textaction" onClick={() => onEdit(m)}>
                      单独设定
                    </button>
                  </span>
                </div>
              );
            })}
          </div>
        )}
        {next !== null && <Button variant="ghost" size={32} label="加载更多" busy={busy} onClick={() => void more()} />}
      </QueryGate>
      <p className="sg-settings__note">
        额度不能低于本月已用量。不设额度的成员不限，只受企业可用余额约束；员工私人点数不计入、也不会被扣。
      </p>
    </section>
  );
}

function DeptQuotaDialog({
  dept,
  onClose,
  onSave,
  saveMessage,
  busy,
}: {
  dept: Department;
  onClose: () => void;
  onSave: (next: { monthly_quota: number | null; per_member_share: number | null; pooling_enabled: boolean }) => Promise<string | null>;
  saveMessage: string | null;
  busy: boolean;
}) {
  const [quota, setQuota] = useState(dept.monthly_quota === null ? '' : String(dept.monthly_quota));
  const [share, setShare] = useState(dept.per_member_share === null ? '' : String(dept.per_member_share));
  const [pooling, setPooling] = useState(dept.pooling_enabled);
  const q = parseQuota(quota);
  const s = parseQuota(share);
  const used = toNumber(dept.used_credits) ?? 0;
  const invalid =
    q === 'invalid' ? '请输入整数' : s === 'invalid' ? '请输入整数' : q !== null && q < used ? '额度不能低于本月已用量' : q !== null && s !== null && s > q ? '每人份额不能高于部门额度' : null;
  return (
    <SettingsDialog
      title={`设置 ${dept.name} 的额度`}
      description={`本服务月已用 ${fmtCredits(used)} Credits；留空表示不设上限`}
      confirmLabel="保存"
      busy={busy}
      disabled={!!invalid}
      onConfirm={() =>
        void onSave({ monthly_quota: q as number | null, per_member_share: s as number | null, pooling_enabled: pooling })
      }
      onCancel={onClose}
      footerNote={(invalid || saveMessage) && <span className="sg-statustext is-error">{invalid || saveMessage}</span>}
    >
      <span className="sg-formlabel">本月额度（Credits）</span>
      <TextField size={32} inputMode="numeric" value={quota} placeholder="不限" invalid={!!invalid && q !== null} onValueChange={setQuota} aria-label="本月额度" />
      <span className="sg-formlabel">每人份额（Credits）</span>
      <TextField size={32} inputMode="numeric" value={share} placeholder="不设" onValueChange={setShare} aria-label="每人份额" />
      <div className="sg-formrow">
        <span>
          部门内调剂
          <small>打开后每人份额的一半只留给本人，另一半进入部门调剂池</small>
        </span>
        <Switch checked={pooling} label="部门内调剂" onChange={setPooling} />
      </div>
    </SettingsDialog>
  );
}

function MemberQuotaDialog({ member, dept, onClose, onDone }: { member: OrgMember; dept: Department; onClose: () => void; onDone: () => void }) {
  const [text, setText] = useState(member.shared_limit ? String(toNumber(member.shared_limit) ?? '') : '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const used = toNumber(member.shared_used) ?? 0;
  const v = parseQuota(text);
  const invalid = v === 'invalid' ? '请输入整数' : v !== null && v < used ? '额度不能低于本月已用量' : null;
  const submit = async (quantity: string | null) => {
    setBusy(true);
    setError(null);
    try {
      await postMemberQuota(member.id, quantity, member.shared_limit_version ?? 0);
      onDone();
    } catch (err) {
      setError(quotaError(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <SettingsDialog
      title={`单独设定 ${memberName(member)} 的额度`}
      description={`本月已用 ${fmtCredits(used)} Credits；留空表示恢复${dept.per_member_share === null ? '不限' : '部门人均'}`}
      confirmLabel="保存"
      busy={busy}
      disabled={!!invalid}
      onConfirm={() => void submit(v === null ? null : String(v))}
      onCancel={onClose}
      footerNote={(invalid || error) && <span className="sg-statustext is-error">{invalid || error}</span>}
    >
      <span className="sg-formlabel">本月额度（Credits）</span>
      <TextField size={32} inputMode="numeric" value={text} placeholder={dept.per_member_share === null ? '不限' : `${fmtCredits(dept.per_member_share)}（部门人均）`} invalid={!!invalid} onValueChange={setText} aria-label="本月额度" />
    </SettingsDialog>
  );
}
