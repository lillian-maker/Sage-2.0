/** Local wrappers for 设置 · 企业管理 (members, quota, policy/audit, offboarding); shapes follow the live responses. */
import { ApiError, currentSession, newClientRequestId, request } from '../../api/client';

export interface OrgMember {
  id: number;
  role: 'owner' | 'billing_admin' | 'member' | string;
  active: boolean;
  role_version: number;
  seat_id: string | null;
  display_name: string | null;
  masked_identifier: string | null;
  last_seen_at: string | null;
  department: { id: string; name: string } | null;
  is_security_admin: boolean;
  is_lead: boolean;
  can_change_role: boolean;
  can_remove: boolean;
  removed_at: string | null;
  handoff_pending: boolean;
  usage_role: string;
  shared_limit: string | null;
  shared_used: string | null;
  shared_reserved: string | null;
  shared_limit_version?: number;
}

export interface Organization {
  account_id: string;
  members: OrgMember[];
  total_members: number;
  offset: number;
  next_offset: number | null;
  seat_count: number;
  reserved_seats: number;
  available_seats: number;
  locked_seats: number;
  assigned_seats: number;
  subscription_active: boolean;
  period_start: string | null;
  period_end: string | null;
  can_edit: boolean;
  capabilities: Record<string, unknown>;
}

export const getOrganization = (query: { offset?: number; department?: string }, signal?: AbortSignal) =>
  request<Organization>('GET', 'billing/organization', { query, signal });

export interface Invitation {
  id: string;
  status: string;
  expires_at: string | null;
  role?: string;
  masked_identifier?: string;
  identifier?: string;
  display_name?: string | null;
  department?: { id: string; name: string } | null;
  paid?: boolean;
}
export const getInvitations = (signal?: AbortSignal) =>
  request<{ items: Invitation[]; total: number; offset: number; next_offset: number | null }>('GET', 'billing/organization/invitations', {
    signal,
  });
export const postInvitation = (body: { method: 'email'; identifier: string; role: string; paid: boolean; department_id?: string }) =>
  request<Invitation>('POST', 'billing/organization/invitations', { body: { ...body, request_key: newClientRequestId() } });

/** Workbench side table (D41): invitee names and member user ids that the commerce responses do not carry. */
export interface OrgPeople {
  me: { membership_id: number; user_id: number };
  invitations: Array<{ invitation_id: string; display_name: string; user_id: number | null }>;
  members: Array<{ membership_id: number; display_name: string; user_id: number | null }>;
}
export const getOrgPeople = (signal?: AbortSignal) => request<OrgPeople>('GET', 'app/org/people', { signal });
export const putInvitationProfile = (invitationId: string, display_name: string) =>
  request<OrgPeople['invitations'][number]>('PUT', `app/org/invitations/${encodeURIComponent(invitationId)}/profile`, {
    body: { display_name },
  });

export interface Department {
  id: string;
  name: string;
  member_count: number;
  monthly_quota: number | null;
  per_member_share: number | null;
  pooling_enabled: boolean;
  quota_version: number;
  used_credits: string;
}
export interface DepartmentList {
  all_count: number;
  unassigned_count: number;
  allocated_total: number;
  items: Department[];
}
export const getDepartments = (signal?: AbortSignal) => request<DepartmentList>('GET', 'app/org/departments', { signal });
export const postDepartment = (name: string) => request<Department>('POST', 'app/org/departments', { body: { name } });
export const postMemberDepartment = (mid: number, department_id: string | null, is_lead: boolean) =>
  request<{ effective_period_start: string | null }>('POST', `app/org/members/${mid}/department`, {
    body: { department_id, is_lead, request_key: newClientRequestId() },
  });
export const postMemberRole = (m: OrgMember, role: string) =>
  request<unknown>('POST', 'billing/organization/members/role', {
    body: { membership_id: m.id, role, expected_version: m.role_version, request_key: newClientRequestId(), confirm: true },
  });

export const postDepartmentQuota = (
  d: Department,
  next: { monthly_quota: number | null; per_member_share: number | null; pooling_enabled: boolean },
) =>
  request<{ quota_version: number; monthly_quota: number | null; per_member_share: number | null; pooling_enabled: boolean }>(
    'POST',
    `billing/org/departments/${encodeURIComponent(d.id)}/quota`,
    { body: { ...next, expected_version: d.quota_version, request_key: newClientRequestId() } },
  );

export const postMemberQuota = (mid: number, quantity: string | null, expected_version: number) =>
  request<{ version: number; quantity: string | null }>('POST', `billing/org/members/${mid}/quota`, {
    body: { quantity, expected_version, request_key: newClientRequestId() },
  });

export interface Policy {
  approval_required_actions: string[];
  approver_rule: string;
  expert_share_max_scope: 'only_me' | 'department' | 'account';
  retention_days: number | null;
  byok_enabled: boolean;
  extension_allowlist_kinds: string[];
  extension_allowed_tiers: string[];
  version: number;
  updated_at: string | null;
  can_edit: boolean;
}
export const getPolicy = (signal?: AbortSignal) => request<Policy>('GET', 'app/org/policy', { signal });
export const patchPolicy = (version: number, changes: Partial<Omit<Policy, 'version' | 'updated_at' | 'can_edit'>>) =>
  request<Policy>('PATCH', 'app/org/policy', { body: { ...changes, version } });

export interface AuditEvent {
  id: string;
  created_at: string;
  actor: { id: number | null; display_name: string | null } | null;
  action: string;
  summary: string;
  target_type: string | null;
  target_id: string | null;
}
export const getAudit = (offset: number, signal?: AbortSignal) =>
  request<{ items: AuditEvent[]; offset: number; total: number; next_offset: number | null }>('GET', 'app/org/audit', {
    query: { offset },
    signal,
  });
/** The export answers text/csv, which client.request rejects as non-JSON, so it is fetched here and saved as a file. */
export async function downloadAuditExport(): Promise<void> {
  const account = currentSession()?.active_account_id;
  const res = await fetch('/api/app/org/audit/export', {
    credentials: 'same-origin',
    headers: account ? { 'X-Billing-Account': account } : {},
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { error?: { code?: string; message?: string } } | null;
    throw new ApiError(res.status, body?.error?.code ?? 'UNKNOWN', body?.error?.message ?? '导出失败，请重试');
  }
  const blob = await res.blob();
  const name = /filename="?([^";]+)"?/.exec(res.headers.get('Content-Disposition') ?? '')?.[1] ?? 'sage-audit.csv';
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export interface OffboardingPreview {
  member: { id: number; display_name: string | null };
  tasks: {
    items: Array<{ id: string; title: string; status: string; used_credits: string; last_activity_at: string | null }>;
    offset: number;
    total: number;
    next_offset: number | null;
  };
  approvals_count: number;
  automations_count: number;
  workspaces: Array<{ id: string; name: string }>;
  custom_assets_count: number;
  connections_count: number;
}
export const getOffboardingPreview = (mid: string, signal?: AbortSignal) =>
  request<OffboardingPreview>('GET', `app/org/members/${encodeURIComponent(mid)}/offboarding-preview`, { signal });

export const removeMember = (membership_id: number, successor_membership_id: number, request_key: string) =>
  request<{ removal_id: string; removed: boolean; handoff_item_count?: number }>('POST', 'billing/organization/members/remove', {
    body: { membership_id, successor_membership_id, memory_purge_acknowledged: true, request_key, confirm: true },
  });
