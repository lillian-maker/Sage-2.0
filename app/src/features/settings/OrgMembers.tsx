import { useEffect, useMemo, useRef, useState } from 'react';
import { currentSession, newClientRequestId } from '../../api/client';
import { navigate } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { Avatar } from '../../components/Avatar/Avatar';
import { Button } from '../../components/Button/Button';
import { Checkbox } from '../../components/Checkbox/Checkbox';
import { IconButton } from '../../components/IconButton/IconButton';
import { Menu, MenuItem, Popover } from '../../components/Menu/MenuItem';
import { Select } from '../../components/Select/Select';
import { EmptyState, ErrorBanner, errorMessage, QueryGate } from '../../components/States/States';
import { Switch } from '../../components/Switch/Switch';
import { Tag } from '../../components/Tag/Tag';
import { TextField } from '../../components/TextField/TextField';
import { fmtCredits, fmtRelative, fmtUntil, initialsOf } from '../billing/format';
import { SITE_LINKS, openSite } from '../billing/site';
import { OrgFrame, isForbidden } from './OrgFrame';
import { SeatSummaryBar } from './SeatSummaryBar';
import { ROLE_LABEL, SettingsDialog } from './common';
import {
  getDepartments,
  getInvitations,
  getOffboardingPreview,
  getOrgPeople,
  getOrganization,
  postDepartment,
  postInvitation,
  postMemberDepartment,
  postMemberRole,
  putInvitationProfile,
  removeMember,
  type Department,
  type Invitation,
  type OrgMember,
} from './orgApi';
import { useSaveStatus } from './useProfile';
import './settings.css';

const sameDay = (a: Date, b: Date) =>
  a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

// 交接清单的任务行按「今天 / 昨天」粒度展示最近活动（760:476），与成员列表的相对时间区分
const fmtDay = (iso: string | null): string => {
  if (!iso) return '—';
  const date = new Date(iso);
  const now = new Date();
  if (sameDay(date, now)) return '今天';
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (sameDay(date, yesterday)) return '昨天';
  return fmtRelative(iso);
};

const TASK_STATUS: Record<string, string> = {
  draft: '草稿',
  awaiting_kickoff: '待开始',
  running: '进行中',
  awaiting_user: '等待确认',
  paused: '已暂停',
  stuck: '卡住了',
  delivered: '待验收',
  needs_revision: '需修改',
  accepted: '已完成',
  stopped: '已停止',
};

function memberName(m: Pick<OrgMember, 'display_name' | 'masked_identifier'>): string {
  return m.display_name || m.masked_identifier || '未命名成员';
}

function roleTags(m: OrgMember) {
  const tags: Array<{ label: string; tone: 'info' | 'neutral' }> = [];
  if (m.role === 'owner') tags.push({ label: '所有者', tone: 'info' });
  if (m.role === 'billing_admin') tags.push({ label: '账务管理员', tone: 'neutral' });
  if (m.is_security_admin) tags.push({ label: '安全与策略', tone: 'neutral' });
  if (m.is_lead) tags.push({ label: '部门负责人', tone: 'neutral' });
  if (tags.length === 0) tags.push({ label: '成员', tone: 'neutral' });
  return tags;
}

type Filter = 'all' | 'none' | string;
type Dialog =
  | { kind: 'invite' }
  | { kind: 'department' }
  | { kind: 'role'; member: OrgMember }
  | { kind: 'move'; member: OrgMember };

/** 75 设置 · 企业管理 · 成员与部门 (758:2); with `offboardId` the 78 办理离职 panel (760:2) sits on top. */
export function OrgMembersPage({ offboardId }: { offboardId?: string }) {
  const [filter, setFilter] = useState<Filter>('all');
  const [nonce, setNonce] = useState(0);
  const save = useSaveStatus();
  const org = useApiQuery((s) => getOrganization({ offset: 0, department: filter === 'all' ? undefined : filter }, s), [filter, nonce]);
  const depts = useApiQuery((s) => getDepartments(s), [nonce]);
  const invites = useApiQuery((s) => getInvitations(s), [nonce]);
  const people = useApiQuery((s) => getOrgPeople(s), [nonce]);
  const [extra, setExtra] = useState<OrgMember[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [moreBusy, setMoreBusy] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ member: OrgMember; at: { x: number; y: number } } | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);

  useEffect(() => {
    setExtra([]);
    setNext(org.data?.next_offset ?? null);
  }, [org.data]);

  const members = useMemo(() => [...(org.data?.members ?? []), ...extra].filter((m) => m.active), [org.data, extra]);
  const pendingInvites = useMemo(
    () =>
      (invites.data?.items ?? []).filter(
        (i) =>
          i.status === 'pending' &&
          (filter === 'all' || (filter === 'none' ? !i.department : i.department?.id === filter)),
      ),
    [invites.data, filter],
  );
  const inviteNames = useMemo(
    () => new Map((people.data?.invitations ?? []).map((p) => [p.invitation_id, p.display_name])),
    [people.data],
  );
  const isMe = useMemo(() => {
    const p = people.data;
    const me = currentSession()?.user;
    const users = new Map((p?.members ?? []).map((row) => [row.membership_id, row.user_id]));
    return (m: OrgMember) =>
      p ? m.id === p.me.membership_id || users.get(m.id) === p.me.user_id : !!me?.display_name && m.display_name === me.display_name;
  }, [people.data]);
  const refresh = () => setNonce((n) => n + 1);

  const loadMore = async () => {
    if (next === null) return;
    setMoreBusy(true);
    setMoreError(null);
    try {
      const page = await getOrganization({ offset: next, department: filter === 'all' ? undefined : filter });
      setExtra((x) => [...x, ...page.members]);
      setNext(page.next_offset);
    } catch (err) {
      setMoreError(errorMessage(err, '加载失败，请重试'));
    } finally {
      setMoreBusy(false);
    }
  };

  const forbidden = isForbidden(org.error) && !org.data;
  const o = org.data;
  const deptList = depts.data;

  return (
    <OrgFrame tab="members" save={{ status: save.status, message: save.message }} forbidden={forbidden}>
      <div className="sg-orgmembers">
        <QueryGate q={org} hasData={!!o} rows={4}>
          {o && (
            <SeatSummaryBar
              purchased={o.seat_count}
              assigned={o.assigned_seats}
              invited={o.reserved_seats}
              locked={o.locked_seats}
              onAdjust={() => openSite(SITE_LINKS.seats)}
              onNewDepartment={() => setDialog({ kind: 'department' })}
              onInvite={() => setDialog({ kind: 'invite' })}
            />
          )}
          <div className="sg-orgmembers__body">
            <nav className="sg-depts" aria-label="部门">
              <DeptItem label="全部成员" count={deptList?.all_count ?? o?.total_members} active={filter === 'all'} onSelect={() => setFilter('all')} />
              {deptList?.items.map((d) => (
                <DeptItem key={d.id} label={d.name} count={d.member_count} active={filter === d.id} onSelect={() => setFilter(d.id)} />
              ))}
              <DeptItem label="未分部门" count={deptList?.unassigned_count} active={filter === 'none'} onSelect={() => setFilter('none')} />
              {depts.status === 'error' && <ErrorBanner message={errorMessage(depts.error)} onRetry={depts.reload} />}
            </nav>
            <div className="sg-orgtable-wrap">
              <div className="sg-orgtable sg-orgtable--members" role="table" aria-label="成员">
                <div className="sg-orgtable__row is-head" role="row">
                  <span role="columnheader">成员</span>
                  <span role="columnheader">部门</span>
                  <span role="columnheader">角色</span>
                  <span role="columnheader">名额</span>
                  <span role="columnheader">最近活跃</span>
                  <span role="columnheader" aria-label="操作" />
                </div>
                {members.map((m) => (
                  <div className="sg-orgtable__row" role="row" key={m.id}>
                    <span className="sg-orgtable__who" role="cell">
                      <Avatar size={26} initials={initialsOf(memberName(m))} alt={memberName(m)} />
                      <span className="truncate">{memberName(m)}</span>
                    </span>
                    <span role="cell">{m.department?.name ?? '未分部门'}</span>
                    <span className="sg-orgtable__tags" role="cell">
                      {roleTags(m).map((t) => (
                        <Tag key={t.label} label={t.label} tone={t.tone} size={15} />
                      ))}
                    </span>
                    <span className="is-muted" role="cell">
                      {m.seat_id ? '已分配' : '不占名额'}
                    </span>
                    <span className="is-muted" role="cell">
                      {m.last_seen_at ? fmtRelative(m.last_seen_at) : '—'}
                    </span>
                    <span role="cell">
                      <IconButton
                        icon="ellipsis"
                        size={25}
                        variant="ghost"
                        label={`${memberName(m)} 的操作`}
                        onClick={(e) => {
                          const r = e.currentTarget.getBoundingClientRect();
                          setMenu({ member: m, at: { x: r.right - 180, y: r.bottom + 4 } });
                        }}
                      />
                    </span>
                  </div>
                ))}
                {pendingInvites.map((i) => (
                  <InviteRow key={i.id} invite={i} name={inviteNames.get(i.id)} />
                ))}
              </div>
              {o && members.length === 0 && pendingInvites.length === 0 && (
                <div className="sg-settings__state">
                  <EmptyState icon="users" title="这个部门还没有成员" description="在行末“⋯”中调整成员的部门，或邀请新成员" />
                </div>
              )}
              {moreError && <ErrorBanner message={moreError} onRetry={() => void loadMore()} />}
              {next !== null && <Button variant="ghost" size={32} label="加载更多" busy={moreBusy} onClick={() => void loadMore()} />}
              <p className="sg-settings__note">
                角色可以叠加；只做管理、不使用 Sage 的成员不占名额。行末“⋯”：调整角色 · 调整部门 · 办理离职。只有所有者可以任命管理员。
              </p>
            </div>
          </div>
        </QueryGate>
      </div>

      {menu && (
        <Popover at={menu.at} width={180} onClose={() => setMenu(null)}>
          <Menu label="成员操作">
            <MenuItem
              icon="users"
              label="调整角色"
              disabled={!menu.member.can_change_role}
              onSelect={() => {
                setDialog({ kind: 'role', member: menu.member });
                setMenu(null);
              }}
            />
            <MenuItem
              icon="folder"
              label="调整部门"
              disabled={!o?.can_edit && !deptList}
              onSelect={() => {
                setDialog({ kind: 'move', member: menu.member });
                setMenu(null);
              }}
            />
            <MenuItem
              icon="archive"
              label="办理离职"
              danger
              disabled={!menu.member.can_remove}
              onSelect={() => {
                const id = menu.member.id;
                setMenu(null);
                navigate(`/settings/org/members/${id}/offboard`);
              }}
            />
          </Menu>
        </Popover>
      )}

      {dialog?.kind === 'invite' && (
        <InviteDialog departments={deptList?.items ?? []} onClose={() => setDialog(null)} onDone={refresh} />
      )}
      {dialog?.kind === 'department' && (
        <NewDepartmentDialog
          onClose={() => setDialog(null)}
          onDone={(d) => {
            refresh();
            setFilter(d.id);
          }}
        />
      )}
      {dialog?.kind === 'role' && (
        <RoleDialog
          member={dialog.member}
          onClose={() => setDialog(null)}
          onSave={async (role) => {
            const ok = await save.run(() => postMemberRole(dialog.member, role));
            if (ok !== undefined) {
              setDialog(null);
              refresh();
            }
          }}
          busy={save.status === 'saving'}
          error={save.status === 'error' ? save.message : null}
        />
      )}
      {dialog?.kind === 'move' && (
        <MoveDialog
          member={dialog.member}
          departments={deptList?.items ?? []}
          busy={save.status === 'saving'}
          error={save.status === 'error' ? save.message : null}
          onClose={() => setDialog(null)}
          onSave={async (departmentId, lead) => {
            const ok = await save.run(() => postMemberDepartment(dialog.member.id, departmentId, lead));
            if (ok !== undefined) {
              setDialog(null);
              refresh();
            }
          }}
        />
      )}

      {offboardId && o && (
        <OffboardDialog
          memberId={offboardId}
          members={o.members}
          isMe={isMe}
          onClose={() => navigate('/settings/org/members', { replace: true })}
          onDone={() => {
            navigate('/settings/org/members', { replace: true });
            save.markSaved();
            refresh();
          }}
        />
      )}
    </OrgFrame>
  );
}

function DeptItem({ label, count, active, onSelect }: { label: string; count?: number; active: boolean; onSelect: () => void }) {
  return (
    <button type="button" className={`sg-depts__item${active ? ' is-active' : ''}`} aria-pressed={active} onClick={onSelect}>
      <span className="truncate">{label}</span>
      <b>{count ?? ''}</b>
    </button>
  );
}

function InviteRow({ invite, name: profileName }: { invite: Invitation; name?: string }) {
  const name = profileName || invite.display_name || invite.masked_identifier || invite.identifier || '受邀成员';
  const until = fmtUntil(invite.expires_at);
  return (
    <div className="sg-orgtable__row" role="row">
      <span className="sg-orgtable__who" role="cell">
        <Avatar size={26} initials={initialsOf(name)} alt={name} />
        <span className="truncate">{name}</span>
      </span>
      <span role="cell">{invite.department?.name ?? '未分部门'}</span>
      <span className="sg-orgtable__tags" role="cell">
        <Tag label={ROLE_LABEL[invite.role ?? 'member'] ?? '成员'} tone="neutral" size={15} />
      </span>
      <span className="is-muted" role="cell">
        {until ? `邀请中 · ${until}失效` : '邀请已过期'}
      </span>
      <span className="is-muted" role="cell">
        —
      </span>
      <span role="cell" />
    </div>
  );
}

function FieldLabel({ children }: { children: string }) {
  return <span className="sg-formlabel">{children}</span>;
}

function InviteDialog({ departments, onClose, onDone }: { departments: Department[]; onClose: () => void; onDone: () => void }) {
  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [role, setRole] = useState('member');
  const [dept, setDept] = useState('');
  const [paid, setPaid] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const invite = await postInvitation({ method: 'email', identifier: email.trim(), role, paid, department_id: dept || undefined });
      if (displayName.trim()) await putInvitationProfile(invite.id, displayName.trim());
      onDone();
      onClose();
    } catch (err) {
      setError(errorMessage(err, '邀请没有发出，请重试'));
    } finally {
      setBusy(false);
    }
  };
  return (
    <SettingsDialog
      title="邀请成员"
      description="对方会收到邮件邀请，24 小时内有效；占用名额的邀请会先预留一个名额"
      confirmLabel="发送邀请"
      busy={busy}
      disabled={!valid}
      onConfirm={() => void submit()}
      onCancel={onClose}
      footerNote={error && <span className="sg-statustext is-error">{error}</span>}
    >
      <FieldLabel>姓名</FieldLabel>
      <TextField size={32} value={displayName} placeholder="选填，显示在成员列表" onValueChange={setDisplayName} aria-label="姓名" />
      <FieldLabel>邮箱</FieldLabel>
      <TextField size={32} type="email" value={email} placeholder="name@company.com" onValueChange={setEmail} aria-label="邮箱" />
      <FieldLabel>角色</FieldLabel>
      <Select
        size={32}
        label="角色"
        value={role}
        options={[
          { value: 'member', label: '成员' },
          { value: 'billing_admin', label: '账务管理员' },
        ]}
        onChange={setRole}
      />
      <FieldLabel>部门</FieldLabel>
      <Select
        size={32}
        label="部门"
        value={dept || 'none'}
        options={[{ value: 'none', label: '未分部门' }, ...departments.map((d) => ({ value: d.id, label: d.name }))]}
        onChange={(v) => setDept(v === 'none' ? '' : v)}
      />
      <div className="sg-formrow">
        <span>
          占用名额
          <small>只做管理、不使用 Sage 的成员可以不占名额</small>
        </span>
        <Switch checked={paid} label="占用名额" onChange={setPaid} />
      </div>
    </SettingsDialog>
  );
}

function NewDepartmentDialog({ onClose, onDone }: { onClose: () => void; onDone: (d: Department) => void }) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const d = await postDepartment(name.trim());
      onDone(d);
      onClose();
    } catch (err) {
      setError(errorMessage(err, '没有创建成功，请重试'));
    } finally {
      setBusy(false);
    }
  };
  return (
    <SettingsDialog
      title="新建部门"
      confirmLabel="创建"
      busy={busy}
      disabled={!name.trim() || name.trim().length > 40}
      onConfirm={() => void submit()}
      onCancel={onClose}
      footerNote={error && <span className="sg-statustext is-error">{error}</span>}
    >
      <FieldLabel>部门名称</FieldLabel>
      <TextField size={32} value={name} placeholder="例如：客服组" onValueChange={setName} aria-label="部门名称" maxLength={40} />
    </SettingsDialog>
  );
}

function RoleDialog({
  member,
  onClose,
  onSave,
  busy,
  error,
}: {
  member: OrgMember;
  onClose: () => void;
  onSave: (role: string) => void;
  busy: boolean;
  error: string | null;
}) {
  const [role, setRole] = useState(member.role === 'billing_admin' ? 'billing_admin' : 'member');
  return (
    <SettingsDialog
      title={`调整 ${memberName(member)} 的角色`}
      description="账务管理员可以查看账单与额度；安全与策略、部门负责人在各自页面设置"
      confirmLabel="保存"
      busy={busy}
      disabled={role === member.role}
      onConfirm={() => onSave(role)}
      onCancel={onClose}
      footerNote={error && <span className="sg-statustext is-error">{error}</span>}
    >
      <Select
        size={32}
        label="角色"
        value={role}
        options={[
          { value: 'member', label: '成员' },
          { value: 'billing_admin', label: '账务管理员' },
        ]}
        onChange={setRole}
      />
    </SettingsDialog>
  );
}

function MoveDialog({
  member,
  departments,
  onClose,
  onSave,
  busy,
  error,
}: {
  member: OrgMember;
  departments: Department[];
  onClose: () => void;
  onSave: (departmentId: string | null, lead: boolean) => void;
  busy: boolean;
  error: string | null;
}) {
  const [dept, setDept] = useState(member.department?.id ?? 'none');
  const [lead, setLead] = useState(member.is_lead);
  return (
    <SettingsDialog
      title={`调整 ${memberName(member)} 的部门`}
      description="从下一个服务月开始按新部门的额度计算"
      confirmLabel="保存"
      busy={busy}
      onConfirm={() => onSave(dept === 'none' ? null : dept, dept === 'none' ? false : lead)}
      onCancel={onClose}
      footerNote={error && <span className="sg-statustext is-error">{error}</span>}
    >
      <Select
        size={32}
        label="部门"
        value={dept}
        options={[{ value: 'none', label: '未分部门' }, ...departments.map((d) => ({ value: d.id, label: d.name }))]}
        onChange={setDept}
      />
      <div className="sg-formrow">
        <span>部门负责人</span>
        <Switch checked={lead && dept !== 'none'} disabled={dept === 'none'} label="部门负责人" onChange={setLead} />
      </div>
    </SettingsDialog>
  );
}

function OffboardDialog({
  memberId,
  members,
  isMe,
  onClose,
  onDone,
}: {
  memberId: string;
  members: OrgMember[];
  isMe: (m: OrgMember) => boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const preview = useApiQuery((s) => getOffboardingPreview(memberId, s), [memberId]);
  const [successor, setSuccessor] = useState('');
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestKey = useRef(newClientRequestId());
  const target = members.find((m) => String(m.id) === memberId);
  const candidates = members.filter((m) => m.active && String(m.id) !== memberId);
  const p = preview.data;
  const name = p?.member.display_name ?? (target ? memberName(target) : '成员');

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await removeMember(Number(memberId), Number(successor), requestKey.current);
      onDone();
    } catch (err) {
      setError(errorMessage(err, '没有办理成功，请重试'));
    } finally {
      setBusy(false);
    }
  };

  if (preview.status === 'error' && !p) {
    return (
      <SettingsDialog title="办理离职" confirmLabel="重试" onConfirm={preview.reload} onCancel={onClose} width={620}>
        {isForbidden(preview.error) || preview.error?.status === 404 ? (
          <p className="sg-sdialog__desc">找不到这位成员，或你没有办理离职的权限。</p>
        ) : (
          <ErrorBanner message={errorMessage(preview.error)} />
        )}
      </SettingsDialog>
    );
  }

  const others: Array<[string, string]> = p
    ? [
        ['待处理的审批', `${p.approvals_count} 项 · 转给接手人`],
        ['自动化', `${p.automations_count} 个 · 暂停后转给接手人，重新启用时确认授权`],
        [
          '工作区所有权',
          p.workspaces.length ? `${p.workspaces.map((w) => `“${w.name}”`).join('、')} · 转给接手人` : '无',
        ],
        ['自建专家与 Playbook', `${p.custom_assets_count} 个 · 转给接手人，已共享的继续可用`],
        ['外部连接器授权', `${p.connections_count} 个 · 自动撤销，接手人需要重新连接`],
      ]
    : [];

  return (
    <SettingsDialog
      title={`为 ${name} 办理离职`}
      description="确认后立即停用账号；成果与资料归企业，随所在工作区保留"
      confirmLabel="确认离职"
      danger
      width={620}
      className="sg-offboard-dialog"
      busy={busy}
      disabled={!p || !successor || !ack}
      onConfirm={() => void submit()}
      onCancel={onClose}
      footerNote={error ? <span className="sg-statustext is-error">{error}</span> : '接手人会在收件中收到交接清单'}
    >
      <span className="sg-formlabel">接手人（必选）</span>
      <Select
        size={36}
        width={280}
        label="接手人"
        placeholder="选择接手人"
        value={successor || undefined}
        options={candidates.map((m) => ({
          value: String(m.id),
          label: `${memberName(m)}${isMe(m) ? '（你）' : ''} · ${m.department?.name ?? '未分部门'}`,
        }))}
        onChange={setSuccessor}
      />
      {!p ? (
        <div className="sg-offboard__loading">正在读取交接清单…</div>
      ) : (
        <>
          <span className="sg-formlabel">
            任务 · {p.tasks.total} 个（只显示标题，不含内容；接手人确认后继续，之后的消耗计入接手人的额度）
          </span>
          {p.tasks.items.length > 0 ? (
            <div className="sg-offboard__list">
              {p.tasks.items.map((t) => (
                <div className="sg-offboard__task" key={t.id}>
                  <b className="truncate">{t.title}</b>
                  <span>
                    {TASK_STATUS[t.status] ?? t.status} · {fmtCredits(t.used_credits)} Credits · {fmtDay(t.last_activity_at)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="sg-offboard__empty">没有需要交接的任务</div>
          )}
          <span className="sg-formlabel">其他交接</span>
          <div className="sg-offboard__list">
            {others.map(([label, value]) => (
              <div className="sg-offboard__item" key={label}>
                <span>{label}</span>
                <span>{value}</span>
              </div>
            ))}
          </div>
          <div className="sg-offboard__ack">
            <Checkbox checked={ack} onChange={setAck} label="我已知晓：该成员的个人记忆将被永久删除，离职不可撤销" />
          </div>
        </>
      )}
    </SettingsDialog>
  );
}
