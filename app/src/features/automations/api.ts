import { newClientRequestId, request, type Query } from '../../api/client';

/* Response shapes the backend returns today for app/automations*; generated types.ts entries are partial. */

export interface ExpertBrief {
  id: string;
  name: string;
  avatar_ref?: string | null;
  specialty?: string | null;
}

export interface Schedule {
  kind: 'daily' | 'weekly' | 'monthly';
  time: string;
  weekday: number | null;
  monthday: number | null;
  timezone: string;
}

export interface RunBrief {
  id: string;
  automation?: { id: string; name: string };
  title: string;
  trigger: string;
  scheduled_for: string | null;
  started_at: string | null;
  finished_at: string | null;
  state: string;
  failure_code: string | null;
  missed_reason: string | null;
  pending_authorization_count: number;
}

export interface AutomationBrief {
  id: string;
  name: string;
  content_kind: 'prompt' | 'playbook';
  state: 'enabled' | 'paused' | 'draft';
  version: number;
  lead_expert: ExpertBrief | null;
  schedule: Schedule;
  next_run_at: string | null;
  workspace: { id: string; name: string } | null;
  last_run: { id: string; state: string; scheduled_for: string | null; finished_at: string | null; missed_reason: string | null } | null;
}

export interface AutomationPermission {
  id: string;
  kind: string;
  extension: { id: string; name: string } | null;
  label: string;
  state: 'granted' | 'needs_grant' | 'needs_connection';
}

export interface AutomationDetail extends AutomationBrief {
  prompt: string | null;
  playbook: { id: string; name: string; variables?: Array<{ name: string; default?: string }> } | null;
  playbook_variables: Record<string, string> | null;
  estimated_duration_minutes: number | null;
  estimated_credits: number | null;
  expected_output: string | null;
  created_from_task: { id: string; title: string } | null;
  permissions: AutomationPermission[];
}

export interface RunStep {
  id: string;
  title: string;
  skill_name: string | null;
  citation_count: number;
  expert: ExpertBrief | null;
  status: string;
  authorization: { id: string; title?: string; scope_text?: string; decision?: string } | null;
  credits_used: number | null;
  duration_seconds: number | null;
}

export interface RunDetail extends RunBrief {
  automation: { id: string; name: string; schedule: Schedule };
  variables: Record<string, string> | null;
  task_id: string | null;
  elapsed_seconds: number | null;
  credits_used: number;
  steps: RunStep[];
}

export interface PlaybookBrief {
  id: string;
  name: string;
  visibility: string;
  variables: Array<{ name: string; default?: string }>;
  step_count: number;
  expert_count: number;
  created_by: { id: string; name: string } | null;
}

export interface Page<T> {
  items: T[];
  offset: number;
  total: number;
  next_offset: number | null;
}

const enc = encodeURIComponent;

export interface AutomationPayload {
  name: string;
  content_kind: 'prompt' | 'playbook';
  schedule_kind: 'daily' | 'weekly' | 'monthly';
  schedule_time: string;
  timezone: string;
  workspace: string;
  lead_expert?: string | null;
  prompt?: string | null;
  playbook?: string | null;
  playbook_variables?: Record<string, string> | null;
  schedule_weekday?: number | null;
  schedule_monthday?: number | null;
}

export const automationsApi = {
  list: (signal?: AbortSignal) => request<Page<AutomationBrief>>('GET', 'app/automations', { signal }),
  detail: (id: string, signal?: AbortSignal) => request<{ automation: AutomationDetail }>('GET', `app/automations/${enc(id)}`, { signal }),
  create: (body: AutomationPayload) => request<{ automation: AutomationDetail }>('POST', 'app/automations', { body }),
  patch: (id: string, body: Partial<AutomationPayload> & { version: number }) =>
    request<{ automation: AutomationDetail }>('PATCH', `app/automations/${enc(id)}`, { body }),
  enable: (id: string) => request<{ automation: AutomationDetail }>('POST', `app/automations/${enc(id)}/enable`, { body: {} }),
  pause: (id: string) => request<{ automation: AutomationDetail }>('DELETE', `app/automations/${enc(id)}/enable`, { body: {} }),
  runNow: (id: string) =>
    request<{ run: { id: string; state: string } }>('POST', `app/automations/${enc(id)}/run-now`, { body: { client_request_id: newClientRequestId() } }),
  runs: (id: string, signal?: AbortSignal) => request<Page<RunBrief>>('GET', `app/automations/${enc(id)}/runs`, { signal }),
  allRuns: (query: Query, signal?: AbortSignal) => request<Page<RunBrief>>('GET', 'app/automation-runs', { query, signal }),
  run: (runId: string, signal?: AbortSignal) => request<{ run: RunDetail }>('GET', `app/automation-runs/${enc(runId)}`, { signal }),
  playbooks: (signal?: AbortSignal) => request<Page<PlaybookBrief>>('GET', 'app/playbooks', { signal }),
};
