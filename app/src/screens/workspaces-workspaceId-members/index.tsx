import { useEffect, useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Avatar, avatarProps } from '../../components/Avatar/Avatar';
import { BackLink } from '../../components/BackLink/BackLink';
import { Button } from '../../components/Button/Button';
import { ConfirmDialog } from '../../components/ConfirmDialog/ConfirmDialog';
import { IconButton } from '../../components/IconButton/IconButton';
import { Menu, MenuItem } from '../../components/Menu/MenuItem';
import { PageHeader } from '../../components/PageHeader/PageHeader';
import { Select } from '../../components/Select/Select';
import { ErrorBanner, ForbiddenState, LoadingState, Toast, errorMessage } from '../../components/States/States';
import { TextField } from '../../components/TextField/TextField';
import { apiMessage } from '../../features/task/model';
import { ROLE_LABEL, timeAgo, wsApi, type Member, type Role } from '../../features/workspaces/model';
import { WorkspaceTabs } from '../../features/workspaces/parts';
import '../../features/workspaces/workspaces.css';

type InviteRole = Exclude<Role, 'guest'>;
const ROLE_OPTIONS: Array<{ value: InviteRole; label: string }> = [
  { value: 'owner', label: ROLE_LABEL.owner },
  { value: 'editor', label: ROLE_LABEL.editor },
  { value: 'viewer', label: ROLE_LABEL.viewer },
];
const ROLE_HELP: Array<{ title: string; desc: string }> = [
  { title: '所有者', desc: '管理成员、规则、删除工作区' },
  { title: '可编辑', desc: '创建任务、上传资料、批准本人发起的确认卡' },
  { title: '仅查看', desc: '查看任务、成果与资料，不能交办' },
  { title: '访客', desc: '只能访问被邀请的任务，看不到其他资料' },
];
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
type JoinRole = 'editor' | 'viewer';
const JOIN_ROLE_OPTIONS: Array<{ value: JoinRole; label: string }> = [
  { value: 'viewer', label: ROLE_LABEL.viewer },
  { value: 'editor', label: ROLE_LABEL.editor },
];

function access(m: Member): string {
  if (m.role === 'guest' && m.guest_tasks.length === 1) return `仅“${m.guest_tasks[0]!.title}”一个任务`;
  if (m.role === 'guest' && m.guest_tasks.length > 1) return `仅 ${m.guest_tasks.length} 个被邀请的任务`;
  return m.access_summary || '—';
}

function lastActive(m: Member): string {
  if (m.state === 'invited') return '待接受';
  return m.last_active_at ? timeAgo(m.last_active_at) : '—';
}

export default function Screen({ params, query, navigate }: ScreenProps) {
  const id = params.workspaceId ?? '';
  const enc = encodeURIComponent(id);
  const ws = useApiQuery((s) => wsApi.get(id, s), [id]);
  const members = useApiQuery((s) => wsApi.members(id, s), [id]);
  const rules = useApiQuery((s) => wsApi.rules(id, s), [id]);

  const [inviteOpen, setInviteOpen] = useState(query.get('invite') === '1');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<InviteRole>('editor');
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Member | null>(null);
  const [toast, setToast] = useState<{ tone: 'undo' | 'failure'; text: string } | null>(null);

  const canManage = !!ws.data?.can_manage;
  const joins = useApiQuery((s) => (canManage ? wsApi.joinRequests(id, s) : Promise.resolve({ items: [], total: 0 })), [id, canManage]);
  const [joinRole, setJoinRole] = useState<Record<string, JoinRole>>({});
  const decide = useSubmit(async (fn: () => Promise<unknown>) => {
    await fn();
    joins.reload();
    members.reload();
    ws.reload();
  });

  const invite = useSubmit(async () => {
    await wsApi.invite(id, email.trim(), role);
    setToast({ tone: 'undo', text: `已邀请 ${email.trim()}` });
    setEmail('');
    setInviteOpen(false);
    members.reload();
    ws.reload();
  });
  const changeRole = useSubmit(async (m: Member, next: InviteRole) => {
    await wsApi.setRole(id, m.id, next);
    members.reload();
    ws.reload();
  });
  const remove = useSubmit(async (m: Member) => {
    await wsApi.removeMember(id, m.id);
    setRemoving(null);
    members.reload();
    ws.reload();
  });

  useEffect(() => {
    const err = changeRole.error ?? remove.error ?? decide.error;
    if (err) setToast({ tone: 'failure', text: apiMessage(err, '操作失败，请重试') });
  }, [changeRole.error, remove.error, decide.error]);

  if (members.status === 'error' && (members.error.status === 403 || members.error.status === 404)) {
    return (
      <div className="sg-members">
        <ForbiddenState title="无法查看这个工作区的成员" action={{ label: '返回全部任务', onClick: () => navigate('/tasks') }} />
      </div>
    );
  }

  const w = ws.data;
  const counts = members.data?.counts;
  const ruleVersion = rules.data?.current?.number ?? w?.current_rule_version?.number;
  const emailInvalid = !!email.trim() && !EMAIL_RE.test(email.trim());

  return (
    <div className="sg-members">
      <BackLink size={30} label={w?.name ?? '工作区'} onBack={() => navigate(`/workspaces/${enc}`)} />
      <div className="sg-members__head">
        <PageHeader
          title="成员与权限"
          {...(counts ? { description: `${counts.active + counts.invited} 位成员 · 按角色共享任务与资料` } : {})}
          actions={canManage ? <Button variant="primary" size={32} icon="plus" label="邀请成员" onClick={() => setInviteOpen((o) => !o)} /> : undefined}
        />
        {inviteOpen && canManage && (
          <div className="sg-members__invitepop" role="dialog" aria-label="邀请成员" onKeyDown={(e) => e.key === 'Escape' && setInviteOpen(false)}>
          <form
            className="sg-members__invite"
            onSubmit={(e) => {
              e.preventDefault();
              if (email.trim() && !emailInvalid) void invite.submit();
            }}
          >
            <TextField size={32} type="email" value={email} placeholder="输入同事的邮箱" invalid={emailInvalid} autoFocus aria-label="邀请邮箱" onValueChange={setEmail} />
            <Select size={32} label="邀请角色" value={role} options={ROLE_OPTIONS} onChange={(v) => setRole(v as InviteRole)} />
            <Button type="submit" variant="primary" size={32} label="发送邀请" busy={invite.submitting} disabled={!email.trim() || emailInvalid} />
            <Button variant="ghost" size={32} label="取消" onClick={() => setInviteOpen(false)} />
          </form>
          <div className="sg-members__invitehint">
            {invite.error ? apiMessage(invite.error, '邀请失败，请重试') : '只能邀请本企业账户内的成员；访客邀请暂不支持'}
          </div>
          </div>
        )}
      </div>
      <div className="sg-ws__tabs">
        <WorkspaceTabs ws={w} current="members" {...(counts ? { memberCount: counts.active + counts.invited } : {})} rulesLabel={ruleVersion ? `规则 v${ruleVersion}` : '规则'} navigate={navigate} />
      </div>


      {ws.status === 'error' && (
        <div className="sg-members__error">
          <ErrorBanner message={errorMessage(ws.error, '工作区暂时无法读取')} onRetry={ws.reload} />
        </div>
      )}
      {members.status === 'error' && (
        <div className="sg-members__error">
          <ErrorBanner message={errorMessage(members.error, '成员暂时无法读取')} onRetry={members.reload} />
        </div>
      )}

      {canManage && (joins.data?.items.length ?? 0) > 0 && (
        <div className="sg-members__requests" aria-label="加入申请">
          <span className="sg-section-label">{`加入申请 · ${joins.data!.total}`}</span>
          {joins.data!.items.map((r) => {
            const name = r.requester.display_name || r.requester.name;
            const pick = joinRole[r.id] ?? 'viewer';
            return (
              <div key={r.id} className="sg-members__request">
                <span className="sg-members__who">
                  <Avatar size={24} {...avatarProps(name, r.requester.avatar_name)} />
                  <span className="truncate">{name}</span>
                  <span className="sg-members__email truncate">{r.message || timeAgo(r.created_at)}</span>
                </span>
                <Select
                  size={26}
                  label={`${name} 加入后的角色`}
                  value={pick}
                  options={JOIN_ROLE_OPTIONS}
                  onChange={(v) => setJoinRole((m) => ({ ...m, [r.id]: v as JoinRole }))}
                />
                <Button variant="ghost" size={26} label="拒绝" disabled={decide.submitting} onClick={() => void decide.submit(() => wsApi.rejectJoin(id, r.id))} />
                <Button variant="primary" size={26} label="批准" busy={decide.submitting} onClick={() => void decide.submit(() => wsApi.approveJoin(id, r.id, pick))} />
              </div>
            );
          })}
        </div>
      )}
      <div className="sg-members__table" role="table" aria-label="成员">
        <div className="sg-members__row is-head" role="row">
          <span role="columnheader">成员</span>
          <span role="columnheader">角色</span>
          <span role="columnheader">可以访问</span>
          <span role="columnheader">最近活跃</span>
          <span />
        </div>
        {members.status === 'loading' && !members.data && <LoadingState rows={4} />}
        {members.data?.items.map((m) => {
          const name = m.user?.display_name ?? m.email;
          const invited = m.state === 'invited' ? ' · 已邀请' : '';
          const editable = canManage && m.role !== 'guest';
          const options = m.role === 'guest' ? [{ value: 'guest', label: ROLE_LABEL.guest + invited }] : ROLE_OPTIONS.map((o) => ({ ...o, label: o.label + invited }));
          return (
            <div key={m.id} className="sg-members__row" role="row">
              <span className="sg-members__who" role="cell">
                <Avatar size={24} {...avatarProps(name, m.user?.avatar_name)} />
                <span className="truncate">{name}</span>
                {m.is_me && <span className="sg-members__me">你</span>}
                {m.user && !m.is_me && <span className="sg-members__email truncate">{m.email}</span>}
              </span>
              <span role="cell">
                <Select
                  size={26}
                  label={`${name} 的角色`}
                  value={m.role}
                  options={options}
                  disabled={!editable || changeRole.submitting}
                  onChange={(v) => v !== m.role && void changeRole.submit(m, v as InviteRole)}
                />
              </span>
              <span className="truncate" role="cell">{access(m)}</span>
              <span className={m.state === 'invited' ? 'sg-members__muted' : undefined} role="cell">{lastActive(m)}</span>
              <span role="cell">
                {canManage && !m.is_me && (
                  <IconButton icon="ellipsis" size={25} variant="ghost" label={`${name} 的操作`} onClick={() => setMenuFor(menuFor === m.id ? null : m.id)} />
                )}
              </span>
              {menuFor === m.id && (
                <div className="sg-members__menu" onMouseLeave={() => setMenuFor(null)}>
                  <Menu label="成员操作">
                    <MenuItem
                      icon="x"
                      label={m.state === 'invited' ? '撤回邀请' : '移出工作区'}
                      danger
                      onSelect={() => {
                        setMenuFor(null);
                        setRemoving(m);
                      }}
                    />
                  </Menu>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="sg-members__roles">
        {ROLE_HELP.map((r) => (
          <div key={r.title} className="sg-members__role">
            <strong>{r.title}</strong>
            <span>{r.desc}</span>
          </div>
        ))}
      </div>

      {removing && (
        <ConfirmDialog
          title={removing.state === 'invited' ? '撤回这条邀请？' : `将 ${removing.user?.display_name ?? removing.email} 移出工作区？`}
          message="移出后对方将无法再访问这个工作区的任务与资料。"
          confirmLabel={removing.state === 'invited' ? '撤回邀请' : '移出'}
          cancelLabel="取消"
          tone="warning"
          busy={remove.submitting}
          onCancel={() => setRemoving(null)}
          onConfirm={() => void remove.submit(removing)}
        />
      )}
      {toast && <Toast tone={toast.tone} message={toast.text} onClose={() => setToast(null)} />}
    </div>
  );
}
