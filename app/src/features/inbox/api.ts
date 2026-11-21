import { newClientRequestId, request, type Query } from '../../api/client';

/*
 * Response shapes the backend returns today for app/inbox/*. The generated api/types.ts entries are partial
 * (several fields are typed loosely or missing), so the inbox screens read through these.
 */

export interface ExpertBrief {
  id: string;
  name: string;
  avatar_ref?: string | null;
}

export type ItemGroup = 'pending_confirmation' | 'todo' | 'in_progress' | 'done' | 'ignored';
export type SourceKind = 'company_email' | 'amazon_buyer_messages' | 'store_notifications' | 'feishu' | 'wecom';

export interface InboxItem {
  id: string;
  title: string;
  group: ItemGroup;
  confidence: number | null;
  priority: string;
  sender_label: string;
  channel_label?: string;
  received_at: string;
  source: { id: string; kind: SourceKind };
  suggested_experts: ExpertBrief[];
  workspace: { id: string; name: string } | null;
  task: { id: string; title: string } | null;
  ignored_at: string | null;
}

export interface InboxItemDetail extends InboxItem {
  original_text: string | null;
  translated_text: string | null;
  rationale: string | null;
  suggestion: string | null;
  events: Array<{ at: string; kind: string; text: string }>;
}

export interface InboxCounts {
  all: number;
  decisions: number;
  mentions: number;
  external: number;
  groups: Record<string, number>;
  sources: Record<string, number>;
}

export interface Page<T> {
  items: T[];
  offset: number;
  total: number;
  next_offset: number | null;
  counts: InboxCounts;
}

export interface DecisionAuthorization {
  id: string;
  purpose_text: string;
  scope_text: string;
  scope_params: Record<string, unknown>;
  scope_options: Array<{ top_n: number; label: string }>;
  action_kind: string;
  validity: string;
  estimate_state: string;
  estimated_credits: number | null;
  charge_target: string;
  decision: string;
  policy_approval_required?: boolean;
  requested_by: { expert: string; task_owner: string };
}

export interface Decision {
  type: 'approve' | 'accept' | 'invite' | 'supply';
  id: string;
  title: string;
  context: string;
  created_at: string;
  expert: ExpertBrief | null;
  actor: { name: string; initials: string } | null;
  target: { type: string; id: string; task_id: string | null; automation_run_id?: string | null; automation_id?: string | null };
  authorization?: DecisionAuthorization;
}

export interface DecisionsPage extends Page<Decision> {
  badge_count: number;
}

export interface Mention {
  source: string;
  id: string;
  category: string;
  type: string;
  text: string;
  context_label: string;
  actor_expert: ExpertBrief | null;
  action: string;
  target: { type: string; id: string } | null;
  occurred_at: string;
  read_at: string | null;
}

/** GET app/authorizations/{id}; unlike the decisions list it carries version and the charge label. */
export interface AuthorizationDetail {
  id: string;
  task: { id: string; title: string } | null;
  extension: { id: string; name: string } | null;
  expert: { id: string; name: string } | null;
  purpose_text: string;
  scope_text: string;
  action_kind: string;
  scope_params: Record<string, unknown>;
  scope_options: Array<{ top_n: number; label: string }>;
  estimate_state: string;
  estimated_credits: number | null;
  validity: string;
  decision: string;
  decision_scope: string | null;
  decided_at: string | null;
  policy_approval_required: boolean;
  charge_target: { kind: string; label: string; department?: { id: string; name: string } | null } | string | null;
  allowed_scopes: string[];
  confirm_scopes?: string[];
  confirm_reasons?: Record<string, string[]>;
  created_at: string;
  version: number;
}

export type Initiative = 'report_only' | 'draft_suggestion' | 'start_task';

export interface SourceBrief {
  id: string;
  kind: SourceKind;
  connection_state: string;
  initiative: Initiative;
  disconnected_at: string | null;
  responsible_experts: ExpertBrief[];
  version: number;
}

export interface SourceDetail extends SourceBrief {
  watched_groups: string[];
  keywords: string[];
  sender_filter: string;
  connection: { id: string; state: string; extension: { id: string; name: string } };
  funnel_7d: { received: number; after_rules: number; after_expert: number; admitted: number };
}

export interface HandoverItem {
  id: string;
  kind: 'task' | 'approval' | 'automation' | 'workspace_ownership' | 'custom_expert' | string;
  state: string;
  resolved_at: string | null;
  resume_status?: string | null;
  target: {
    type: string;
    id: string;
    title: string;
    status: string;
    current_step: number | { order?: number; title?: string } | null;
    expert?: ExpertBrief;
    task_id?: string;
    shared_department?: string | null;
  };
}

export interface HandoverGroup {
  removal_id: string;
  from_member: { name: string };
  removed_at: string;
  revoked_connection_count: number;
  items: HandoverItem[];
}

const enc = encodeURIComponent;

export const inboxApi = {
  items: (query: Query, signal?: AbortSignal) => request<Page<InboxItem>>('GET', 'app/inbox/items', { query, signal }),
  mentions: (signal?: AbortSignal) => request<Page<Mention>>('GET', 'app/inbox/items', { query: { channel: 'mentions' }, signal }),
  item: (id: string, signal?: AbortSignal) => request<{ item: InboxItemDetail }>('GET', `app/inbox/items/${enc(id)}`, { signal }),
  decisions: (signal?: AbortSignal) => request<DecisionsPage>('GET', 'app/inbox/decisions', { signal }),
  handover: (signal?: AbortSignal) => request<{ groups: HandoverGroup[] }>('GET', 'app/inbox/handover', { signal }),
  sources: (signal?: AbortSignal) => request<{ items: SourceBrief[] }>('GET', 'app/inbox/sources', { signal }),
  source: (id: string, signal?: AbortSignal) => request<{ source: SourceDetail }>('GET', `app/inbox/sources/${enc(id)}`, { signal }),
  authorization: (id: string, signal?: AbortSignal) => request<AuthorizationDetail>('GET', `app/authorizations/${enc(id)}`, { signal }),

  ignore: (id: string) => request<{ item: { id: string; group: string } }>('POST', `app/inbox/items/${enc(id)}/ignore`, { body: {} }),
  handOff: (id: string, leadExpertId?: string) =>
    request<{ item: { id: string; group: string }; task: { id: string; title: string } }>('POST', `app/inbox/items/${enc(id)}/hand-off`, {
      body: { client_request_id: newClientRequestId(), ...(leadExpertId ? { lead_expert_id: leadExpertId } : {}) },
    }),
  patchSource: (id: string, body: Record<string, unknown>) => request<{ source: SourceDetail }>('PATCH', `app/inbox/sources/${enc(id)}`, { body }),
  disconnect: (id: string) => request<{ source: { id: string } }>('POST', `app/inbox/sources/${enc(id)}/disconnect`, { body: {} }),
  acceptHandover: (id: string) =>
    request<{ task: { id: string; status: string } }>('POST', `app/inbox/handover/${enc(id)}/accept`, { body: { client_request_id: newClientRequestId() } }),
  reactivateHandover: (id: string) =>
    request<{ automation: { id: string; state: string } }>('POST', `app/inbox/handover/${enc(id)}/reactivate`, {
      body: { client_request_id: newClientRequestId() },
    }),

  approve: (id: string, version: number, clientRequestId: string) =>
    request('POST', `app/authorizations/${enc(id)}/approve`, { body: { decision_scope: 'once', version, client_request_id: clientRequestId } }),
  reject: (id: string, version: number) =>
    request('POST', `app/authorizations/${enc(id)}/reject`, { body: { version, client_request_id: newClientRequestId() } }),
  revise: (id: string, version: number, topN: number) =>
    request('POST', `app/authorizations/${enc(id)}/revise`, { body: { scope_params: { top_n: topN }, version, client_request_id: newClientRequestId() } }),
  estimate: (id: string) => request('POST', `app/authorizations/${enc(id)}/estimate`, { body: { client_request_id: newClientRequestId() } }),
};
