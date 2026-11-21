import { request } from '../../api/client';
import { currentSession } from '../../api/client';
import type { TagProps } from '../../components/Tag/Tag';
import type { IconName } from '../../components/Icon/Icon';

const enc = encodeURIComponent;

export type Role = 'owner' | 'editor' | 'viewer' | 'guest';

export interface RecentTask {
  id: string;
  title: string;
  unread?: boolean;
}

export interface WorkspaceSummary {
  id: string;
  name: string;
  description: string | null;
  role: Role;
  pinned_at: string | null;
  last_activity_at: string | null;
  task_count: number;
  unread_count: number;
  current_rule_version: { number: number } | null;
  recent_tasks: RecentTask[];
}

export interface WorkspaceDetail {
  id: string;
  name: string;
  description: string | null;
  account_kind: string;
  role: Role;
  created_from: string;
  counts: { tasks: number; documents: number; members: number };
  unread_count: number;
  current_rule_version: { number: number } | null;
  members_preview: Array<{ display_name: string; avatar_name?: string }>;
  pinned_at?: string | null;
  can_manage: boolean;
}

export interface RuleVersion {
  number: number;
  author: { name: string } | null;
  created_at: string;
  change_note: string;
  restored_from: number | null;
}

export interface RulesResponse {
  current: { number: number; rules: string[]; author: { name: string } | null; created_at: string } | null;
  versions: { items: RuleVersion[]; total: number };
  can_edit: boolean;
}

export interface Member {
  id: string;
  user: { display_name: string; avatar_name?: string } | null;
  email: string;
  role: Role;
  state: 'active' | 'invited';
  access_summary: string;
  guest_tasks: Array<{ id: string; title: string }>;
  last_active_at: string | null;
  is_me: boolean;
}

export interface MembersResponse {
  items: Member[];
  total: number;
  counts: { active: number; invited: number };
}

export type TaskStatus =
  | 'draft'
  | 'awaiting_kickoff'
  | 'running'
  | 'awaiting_user'
  | 'paused'
  | 'stuck'
  | 'delivered'
  | 'needs_revision'
  | 'accepted'
  | 'stopped';

export interface TaskItem {
  id: string;
  title: string;
  status: TaskStatus;
  workspace: { id: string; name: string } | null;
  lead_expert: { id: string; name: string; specialty: string; avatar_ref: string | null } | null;
  counts: { collaborators: number };
  owner: { id: string; display_name: string; is_me: boolean } | null;
  shared_with_me: boolean;
  progress: { done: number; total: number };
  accepted_version: { number: number } | null;
  pending_authorization_count: number;
  last_activity_at: string | null;
  archived_at: string | null;
  unread: boolean;
}

export type TaskCounts = Record<'all' | 'owned' | 'created' | 'shared' | 'archived' | 'needs_me' | 'running' | 'done', number>;

export interface TasksResponse {
  items: TaskItem[];
  counts: TaskCounts;
}

export interface TasksQuery {
  workspace?: string | undefined;
  status_group?: 'all' | 'needs_me' | 'running' | 'done' | undefined;
  scope?: 'owned' | 'created' | 'shared' | 'archived' | undefined;
  q?: string | undefined;
  sort?: 'updated' | undefined;
}

export interface DocumentItem {
  id: string;
  title: string;
  index_state: string;
  index_progress: number | null;
}

export interface Playbook {
  id: string;
  name: string;
}

export type CreatedFrom = 'blank' | 'copy_rules' | 'playbook';

export interface JoinRequest {
  id: string;
  state: 'pending' | 'approved' | 'rejected' | 'withdrawn';
  message: string;
  role: 'editor' | 'viewer' | null;
  created_at: string;
  decided_at: string | null;
  requester: { id: string; name: string; display_name: string; avatar_name: string };
  workspace: { id: string };
}

export const wsApi = {
  list: (signal?: AbortSignal) => request<{ items: WorkspaceSummary[] }>('GET', 'app/workspaces', { signal }),
  get: (id: string, signal?: AbortSignal) => request<WorkspaceDetail>('GET', `app/workspaces/${enc(id)}`, { signal }),
  remove: (id: string) => request<unknown>('DELETE', `app/workspaces/${enc(id)}`),
  readAll: (id: string) => request<unknown>('POST', `app/workspaces/${enc(id)}/read-all`),
  create: (body: { name: string; created_from: CreatedFrom; source_workspace?: string; source_playbook?: string; description?: string }) =>
    request<{ workspace: { id: string; name: string }; first_task: { id: string } | null }>('POST', 'app/workspaces', { body }),

  rules: (id: string, signal?: AbortSignal) => request<RulesResponse>('GET', `app/workspaces/${enc(id)}/rules`, { signal }),
  saveRules: (id: string, body: { rules: string[]; version: number; change_note?: string }) =>
    request<{ version: { number: number } }>('POST', `app/workspaces/${enc(id)}/rules`, { body }),
  restoreRules: (id: string, v: number, current: number) =>
    request<{ version: { number: number } }>('POST', `app/workspaces/${enc(id)}/rules/${v}/restore`, { body: { version: current } }),

  members: (id: string, signal?: AbortSignal) => request<MembersResponse>('GET', `app/workspaces/${enc(id)}/members`, { signal }),
  invite: (id: string, email: string, role: Exclude<Role, 'guest'>) =>
    request<unknown>('POST', `app/workspaces/${enc(id)}/members`, { body: { email, role } }),
  setRole: (id: string, mid: string, role: Exclude<Role, 'guest'>) =>
    request<unknown>('PATCH', `app/workspaces/${enc(id)}/members/${enc(mid)}`, { body: { role } }),
  removeMember: (id: string, mid: string) => request<unknown>('DELETE', `app/workspaces/${enc(id)}/members/${enc(mid)}`),

  myJoinRequest: (id: string, signal?: AbortSignal) =>
    request<{ join_request: JoinRequest | null }>('GET', `app/workspaces/${enc(id)}/join-requests/mine`, { signal }),
  requestJoin: (id: string, message?: string) =>
    request<{ join_request: JoinRequest }>('POST', `app/workspaces/${enc(id)}/join-requests`, { body: message ? { message } : {} }),
  withdrawJoin: (id: string, rid: string) =>
    request<{ join_request: JoinRequest }>('POST', `app/workspaces/${enc(id)}/join-requests/${enc(rid)}/withdraw`, { body: {} }),
  joinRequests: (id: string, signal?: AbortSignal) =>
    request<{ items: JoinRequest[]; total: number }>('GET', `app/workspaces/${enc(id)}/join-requests`, { signal }),
  approveJoin: (id: string, rid: string, role: 'editor' | 'viewer') =>
    request<unknown>('POST', `app/workspaces/${enc(id)}/join-requests/${enc(rid)}/approve`, { body: { role } }),
  rejectJoin: (id: string, rid: string) =>
    request<unknown>('POST', `app/workspaces/${enc(id)}/join-requests/${enc(rid)}/reject`, { body: {} }),

  tasks: (query: TasksQuery, signal?: AbortSignal) => request<TasksResponse>('GET', 'app/tasks', { query: { sort: 'updated', ...query }, signal }),
  documents: (workspace: string, signal?: AbortSignal) =>
    request<{ items: DocumentItem[]; total: number }>('GET', 'app/documents', { query: { workspace }, signal }),
  playbooks: (signal?: AbortSignal) => request<{ items: Playbook[] }>('GET', 'app/playbooks', { signal }),
};

/** “10 分钟前 / 3 小时前 / 昨天 / 10月30日” as used across 16/18/20/54. */
export function timeAgo(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return '';
  const d = new Date(iso);
  const diff = Math.max(0, now.getTime() - d.getTime());
  const min = Math.floor(diff / 60000);
  if (min < 1) return '刚刚';
  if (min < 60) return `${min} 分钟前`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === now.toDateString()) return `${Math.floor(min / 60)} 小时前`;
  if (d.toDateString() === yesterday.toDateString()) return '昨天';
  if (d.getFullYear() !== now.getFullYear()) return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

/** “今天 10:02 / 11月3日” for version history rows. */
export function dayTime(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return '';
  const d = new Date(iso);
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  if (d.toDateString() === now.toDateString()) return `今天 ${hm}`;
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export const ROLE_LABEL: Record<Role, string> = { owner: '所有者', editor: '可编辑', viewer: '仅查看', guest: '访客' };

export { avatarInitials as initials } from '../../components/Avatar/Avatar';

export interface TaskState {
  label: string;
  /** Plain grey text (Figma shows finished and draft rows without a pill). */
  tone: TagProps['tone'] | 'plain';
  icon: IconName;
  waiting: boolean;
}

export function taskState(t: Pick<TaskItem, 'status' | 'progress' | 'pending_authorization_count'>): TaskState {
  const p = t.progress;
  const done = Number(p?.done ?? 0);
  const total = Number(p?.total ?? 0);
  switch (t.status) {
    case 'awaiting_user':
      return {
        label: t.pending_authorization_count > 0 ? `待你确认 · ${t.pending_authorization_count} 个审批` : '待你确认',
        tone: 'warning',
        icon: 'circle-pending',
        waiting: true,
      };
    case 'awaiting_kickoff':
      return { label: '待开工', tone: 'warning', icon: 'circle-pending', waiting: true };
    case 'delivered':
      return { label: '待你验收', tone: 'warning', icon: 'circle-pending', waiting: true };
    case 'needs_revision':
      return { label: '待修改', tone: 'warning', icon: 'circle-pending', waiting: true };
    case 'stuck':
      return { label: '卡住', tone: 'danger', icon: 'circle-pending', waiting: true };
    case 'paused':
      return { label: '已暂停', tone: 'neutral', icon: 'circle', waiting: false };
    case 'running':
      return { label: total > 0 ? `进行中 ${done}/${total}` : '进行中', tone: 'info', icon: 'loader-ring', waiting: false };
    case 'accepted':
      return { label: '已完成', tone: 'plain', icon: 'circle-check-soft', waiting: false };
    case 'stopped':
      return { label: '已停止', tone: 'plain', icon: 'circle', waiting: false };
    default:
      return { label: '草稿', tone: 'plain', icon: 'circle', waiting: false };
  }
}

/** Account label for the “企业 · Sage Demo” tag; the workspace payload only carries account_kind. */
export function accountLabel(kind: string | undefined): string {
  const s = currentSession();
  const acc = s?.accounts.find((a) => a.id === s.active_account_id);
  const k = kind === 'personal' ? '个人' : '企业';
  const name = acc?.name.replace(/\s*(企业|账户)$/, '');
  return name ? `${k} · ${name}` : k;
}
