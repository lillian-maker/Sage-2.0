import { ApiError, newClientRequestId, request } from '../../api/client';
import { avatarProps } from '../../components/Avatar/Avatar';
import type { TagProps } from '../../components/Tag/Tag';
import type { StepStatus } from './PlanStepRow';

/*
 * Response shapes as the backend returns them today. The generated api/types.ts entries for these
 * endpoints are partial (e.g. current_step is typed as string), so the task screens read through these.
 */

export interface ExpertRef {
  id: string;
  name: string;
  specialty?: string | null;
  avatar_ref?: string | null;
}
export interface UserRef {
  id: string;
  display_name: string;
  is_me?: boolean;
}

export interface ActiveTask {
  id: string;
  title: string;
  status: string;
  workspace: { id: string; name: string } | null;
  progress: { done: number; total: number };
  current_step: { id: string; order: number; title: string; activity: string | null; status: string } | null;
  stuck_rounds: number;
  last_activity_at: string | null;
  experts: ExpertRef[];
  source_automation: { id: string; name: string } | null;
}
export interface ActiveTasksPage {
  counts: { all: number; stuck: number; awaiting_user: number };
  items: ActiveTask[];
  offset: number;
  total: number;
  next_offset: number | null;
}

export interface Participant {
  id: string;
  kind: 'human' | 'expert';
  role: string;
  state: string;
  expert: ExpertRef | null;
  user: UserRef | null;
}

export interface TaskDetail {
  id: string;
  title: string;
  status: string;
  complexity: string | null;
  model_mode: string;
  workspace: { id: string; name: string } | null;
  owner: UserRef | null;
  lead_expert: ExpertRef | null;
  access: string;
  counts: { artifacts: number; materials: number; participants: number };
  participants: Participant[];
  progress: { done: number; total: number };
  replan_count: number;
  used_credits: number;
  pause_reason: string | null;
  pause: {
    reason: string;
    paused_at: string | null;
    department_name: string | null;
    member_share: number | null;
    completed_steps: number;
    resume_step: number | null;
    resume_available?: boolean;
  } | null;
  rule_version: { number: number } | null;
  scope_text?: string | null;
  authorizations?: Array<{ id: string; decision: string; estimate_state: string; estimated_credits: number | null }>;
  source_automation_run: { automation_name: string; scheduled_for: string | null; variables: Record<string, unknown> | null } | null;
  /** Null when the task is delivered without a formal acceptance request (drafts waiting for confirmation). */
  pending_delivery?: { id: string } | null;
  /** Suggested defaults for the save-as-playbook dialog; `variable` marks the step that variable parameterizes. */
  playbook_template?: {
    name: string;
    variables: Array<{ name: string; default: string }>;
    steps: Array<{ step: string; variable: string | null }>;
  } | null;
  source_inbox_item?: { id: string; source_label: string; sender_label: string; received_at: string; original_text: string; translated_text: string | null } | null;
  suggestions: Array<{ id: string; text: string; requires_authorization?: boolean }>;
  forked_from_task: { id: string; title: string } | null;
  accepted_at: string | null;
  last_activity_at: string | null;
}

export interface TaskMessage {
  id: string;
  thread: string;
  kind: string;
  body: string;
  sent_at: string | null;
  author_kind: 'user' | 'expert' | 'system';
  author_role: string | null;
  author_user: UserRef | null;
  author_expert: ExpertRef | null;
  attachments: Array<{ id: string; title: string; index_state?: string | number }>;
  citations: Array<{ id: string; label: string }>;
  run_metrics: {
    artifacts_created?: number;
    documents_read?: number;
    elapsed_seconds?: number;
    tool_calls?: number;
    /** Plan progress at the time of a progress-report message. */
    steps_done?: number;
    steps_total?: number;
    /** Parallel-work card (kind parallel_card): one row per expert working on the fanned-out stage. */
    parallel?: {
      rows: Array<{
        expert: { name: string; avatar_ref?: string | null };
        step_id: string;
        title: string;
        activity: string | null;
        elapsed_seconds: number | null;
        in_background: boolean;
      }>;
    };
  } | null;
  branch: { index: number; count: number } | null;
  card: {
    type: string;
    id: string;
    summary?: string;
    decision?: string;
    return_reason?: string | null;
    criteria_met?: number;
    criteria_total?: number;
    artifact_version?: { id: string; artifact_id: string; title: string; number: number; state: string };
  } | null;
  is_important: boolean;
}
export interface MessagesPage {
  items: TaskMessage[];
  total: number;
  next_offset: number | null;
}

export interface TaskStep {
  id: string;
  order: number;
  stage: string;
  title: string;
  /** Short capability name for the orchestration node header; empty falls back to skill, then expert. */
  label?: string;
  expert: ExpertRef | null;
  skill: { id: string; name: string; kind: string } | null;
  status: string;
  required_extension: { id: string; name: string } | null;
  estimated_credits: number | null;
  used_credits: number;
  duration_seconds: number | null;
  started_at: string | null;
  finished_at: string | null;
  activity: string | null;
  inputs: Array<string | { id?: string; title?: string; label?: string }>;
  depends_on: string[];
  in_background: boolean;
  assist_experts: ExpertRef[];
  authorization_id: string | null;
  criteria_total: number;
  criteria_met: number;
}
export interface StepsPage {
  items: TaskStep[];
  total: number;
  summary: { running: number; in_background: number; awaiting_user: number; done: number };
  plan_revision: number;
}

export interface Kickoff {
  task_id: string;
  status: string;
  complexity: string | null;
  estimated_expert_count: number | null;
  estimated_duration_minutes: number | null;
  /** An answer without answered_at is the lead's suggested default, still waiting for the user's confirmation. */
  questions: Array<{ id: string; order: number; question: string; options: string[]; answer: string | null; answered_at?: string | null }>;
  brief: { goal: string | null; scope_text: string | null; deliverables: string | null; constraints: string | null; rule_version: { number: number } | null };
  criteria: Criterion[];
  missing_capabilities: Array<{ step_id: string; title: string; extension: { id: string; name: string; kind: string } | null }>;
  plan: { step_count: number; expert_count: number; steps: Array<Pick<TaskStep, 'id' | 'order' | 'stage' | 'title' | 'expert' | 'skill' | 'status'>> };
}

export interface Criterion {
  id: string;
  text: string;
  evidence: string | null;
  verdict: string | null;
  origin: string;
  order: number;
  step_id: string | null;
}

export interface Authorization {
  id: string;
  task: { id: string; title: string } | null;
  extension: { id: string; name: string } | null;
  expert: { id: string; name: string } | null;
  purpose_text: string;
  scope_text: string;
  action_kind: string;
  scope_params: { top_n?: number } | null;
  scope_options: Array<{ top_n: number; label: string }>;
  estimate_state: 'estimated' | 'pending' | 'estimating' | 'unavailable' | string;
  estimated_credits: number | null;
  validity: string;
  decision: 'pending' | 'approved' | 'rejected' | string;
  decision_scope: string | null;
  decided_at: string | null;
  policy_approval_required: boolean;
  charge_target: { kind: string; label: string; department?: { id: string; name: string } | null } | null;
  allowed_scopes: string[];
  /** Scopes that need the second confirmation “批准使用 X？” (82 ③) and why: broad_scope / paid / external_send. */
  confirm_scopes?: string[];
  confirm_reasons?: Record<string, string[]>;
}

export interface InboxDecision {
  type: string;
  id: string;
  title: string;
  context: string;
  created_at: string | null;
  target: { type: string; id: string; task_id: string | null };
}

export interface ThreadItem {
  id: string;
  expert: ExpertRef | null;
  message_count: number;
  kind: string;
  anchor: { message_id: string; author_name: string; excerpt: string; sent_at: string | null } | null;
}

export interface Review {
  id: string;
  state: string;
  conclusion: string | null;
  dimensions: string[];
  task: { id: string; title: string };
  reviewers: ExpertRef[];
  verifier_expert: ExpertRef | null;
  targets: Array<{ artifact_id: string; title?: string; version: number; kind: string }>;
  target_platform: string | null;
  target_market: string | null;
  counts: { issues: number; by_dimension: Record<string, number | null> };
  issues: Array<{
    id: string;
    dimension: string;
    severity: string;
    title?: string;
    location?: string;
    original_text: string;
    suggested_text: string;
    basis: string;
    action: string;
    suggested_artifact_version?: { artifact_id: string; title: string; number: number; label?: string | null } | null;
  }>;
  result_version?: { artifact_id: string; title: string; number: number } | null;
  can_edit: boolean;
}

export interface ArtifactItem {
  id: string;
  title: string;
  kind: string;
  current_version: { number: number; state: string; created_at: string | null } | null;
  updated_at: string | null;
}

const enc = encodeURIComponent;
const get = <T>(path: string, query?: Record<string, string | number | undefined>, signal?: AbortSignal) =>
  request<T>('GET', path, { query, signal });
const post = <T>(path: string, body: Record<string, unknown> = {}) =>
  request<T>('POST', path, { body: { client_request_id: newClientRequestId(), ...body } });

export const taskApi = {
  active: (filter: string, offset: number, signal?: AbortSignal) =>
    get<ActiveTasksPage>('app/tasks/active', { filter: filter === 'all' ? undefined : filter, offset: offset || undefined }, signal),
  detail: (id: string, signal?: AbortSignal) => get<TaskDetail>(`app/tasks/${enc(id)}`, undefined, signal),
  messages: (id: string, thread: string, signal?: AbortSignal, branch?: number) =>
    get<MessagesPage>(`app/tasks/${enc(id)}/messages`, { thread, branch }, signal),
  steps: (id: string, signal?: AbortSignal) => get<StepsPage>(`app/tasks/${enc(id)}/steps`, undefined, signal),
  kickoff: (id: string, signal?: AbortSignal) => get<Kickoff>(`app/tasks/${enc(id)}/kickoff`, undefined, signal),
  criteria: (id: string, signal?: AbortSignal, step?: string) =>
    get<{ items: Criterion[] }>(`app/tasks/${enc(id)}/criteria`, step ? { step } : undefined, signal),
  threads: (id: string, signal?: AbortSignal) => get<{ items: ThreadItem[] }>(`app/tasks/${enc(id)}/threads`, undefined, signal),
  artifacts: (id: string, signal?: AbortSignal) => get<{ items: ArtifactItem[] }>('app/artifacts', { task: id }, signal),
  decisions: (signal?: AbortSignal) => get<{ items: InboxDecision[] }>('app/inbox/decisions', undefined, signal),
  authorization: (id: string, signal?: AbortSignal) => get<Authorization>(`app/authorizations/${enc(id)}`, undefined, signal),
  review: (id: string, signal?: AbortSignal) => get<Review>(`app/reviews/${enc(id)}`, undefined, signal),

  create: (body: Record<string, unknown>) =>
    request<{ task: { id: string; status: string; complexity: string | null }; message: { id: string } }>('POST', 'app/tasks', { body }),
  start: (id: string, answers?: Array<{ question_id: string; answer: string }>) =>
    post<{ task: { id: string; status: string } }>(`app/tasks/${enc(id)}/start`, answers && answers.length ? { answers } : {}),
  send: (id: string, body: string, thread?: string) =>
    request<{ message: { id: string } }>('POST', `app/tasks/${enc(id)}/messages`, { body: { body, ...(thread ? { thread } : {}) } }),
  edit: (id: string, mid: string, body: string) => post(`app/tasks/${enc(id)}/messages/${enc(mid)}/edit`, { body }),
  important: (id: string, mid: string, isImportant: boolean) =>
    request('PATCH', `app/tasks/${enc(id)}/messages/${enc(mid)}`, { body: { is_important: isImportant } }),
  fork: (id: string, messageId: string, body: string) =>
    request<{ task: { id: string } }>('POST', `app/tasks/${enc(id)}/fork`, { body: { message_id: messageId, body } }),
  pause: (id: string) => post(`app/tasks/${enc(id)}/pause`),
  stop: (id: string) => post(`app/tasks/${enc(id)}/stop`),
  answer: (id: string, qid: string, answer: string) =>
    request('PATCH', `app/tasks/${enc(id)}/kickoff/questions/${enc(qid)}`, { body: { answer } }),
  addCriterion: (id: string, text: string) => request<{ criterion: Criterion }>('POST', `app/tasks/${enc(id)}/criteria`, { body: { text } }),
  resume: (id: string) => post(`app/tasks/${enc(id)}/resume`),
  remindQuotaAdmins: (id: string) =>
    request<{ reminded: number; created: boolean }>('POST', `app/tasks/${enc(id)}/quota-reminders`, { body: {} }),
  accept: (id: string, did: string) => post(`app/tasks/${enc(id)}/deliveries/${enc(did)}/accept`),
  returnDelivery: (id: string, did: string, reason: string, verdicts?: Array<{ criterion_id: string; verdict: 'met' | 'unmet' | 'pending' }>) =>
    post(`app/tasks/${enc(id)}/deliveries/${enc(did)}/return`, verdicts && verdicts.length ? { reason, verdicts } : { reason }),
  openThread: (id: string, anchorMessageId: string) =>
    request<{ thread: { id: string; expert: ExpertRef | null } }>('POST', `app/tasks/${enc(id)}/threads`, { body: { anchor_message_id: anchorMessageId } }),
  bringBack: (id: string, tid: string) => post(`app/tasks/${enc(id)}/threads/${enc(tid)}/bring-back`),

  approve: (aid: string, scope = 'once') => post(`app/authorizations/${enc(aid)}/approve`, { decision_scope: scope }),
  reject: (aid: string) => post(`app/authorizations/${enc(aid)}/reject`),
  revise: (aid: string, topN: number) => post(`app/authorizations/${enc(aid)}/revise`, { scope_params: { top_n: topN } }),
  estimate: (aid: string) => post(`app/authorizations/${enc(aid)}/estimate`),

  applyAll: (rid: string) => post(`app/reviews/${enc(rid)}/apply-all`),
  issueAction: (rid: string, iid: string, action: 'applied' | 'ignored') =>
    request('PATCH', `app/reviews/${enc(rid)}/issues/${enc(iid)}`, { body: { action } }),

  savePlaybook: (body: Record<string, unknown>) => request<{ playbook?: { id: string }; id?: string }>('POST', 'app/playbooks', { body }),
};

export const avatarOf = avatarProps;

export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '';
  const diff = Math.max(0, now - new Date(iso).getTime());
  const min = Math.floor(diff / 60000);
  if (min < 1) return '刚刚';
  if (min < 60) return `${min} 分钟`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} 小时`;
  const d = Math.floor(h / 24);
  return `${d} 天`;
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  const today = new Date();
  if (d.toDateString() === today.toDateString()) return `今天 ${hm}`;
  return `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
}

export function durationText(seconds: number | null | undefined): string {
  if (!seconds) return '';
  const m = Math.round(seconds / 60);
  return m < 1 ? `${seconds} 秒` : `${m} 分钟`;
}

export function stepStatus(status: string): StepStatus {
  if (status === 'done' || status === 'skipped') return 'done';
  if (status === 'awaiting_user' || status === 'blocked' || status === 'failed') return 'waiting';
  if (status === 'running') return 'active';
  return 'queued';
}

export const STAGES: Array<{ key: string; label: string }> = [
  { key: 'understand', label: '理解' },
  { key: 'research', label: '研究' },
  { key: 'analyze', label: '分析' },
  { key: 'review', label: '复核' },
  { key: 'deliver', label: '交付' },
];

export function taskStatusTag(status: string): TagProps | undefined {
  const map: Record<string, TagProps> = {
    draft: { label: '草稿', tone: 'neutral', size: 22 },
    awaiting_kickoff: { label: '待开工', tone: 'warning', size: 22 },
    running: { label: '进行中', tone: 'info', size: 22 },
    awaiting_user: { label: '待你确认', tone: 'warning', size: 22 },
    stuck: { label: '卡住', tone: 'danger', size: 22 },
    paused: { label: '已暂停', tone: 'warning', size: 22 },
    delivered: { label: '待验收', tone: 'warning', size: 22 },
    accepted: { label: '已完成', tone: 'success', size: 22 },
    stopped: { label: '已停止', tone: 'neutral', size: 22 },
    rejected: { label: '已拒绝', tone: 'neutral', size: 22 },
  };
  return map[status];
}

const CODE_MESSAGE: Record<string, string> = {
  INSUFFICIENT_CREDITS: '额度不足，暂时无法继续；可以查看用量或请管理员调整额度',
  SOURCE_UNAVAILABLE: '外部数据源暂时不可用，请稍后重试',
  AUTHORIZATION_EXPIRED: '这条授权请求已过期',
  POLICY_APPROVAL_REQUIRED: '按企业策略需要管理员审批',
  STATE_CONFLICT: '状态已变化，请刷新后再试',
  RATE_LIMITED: '操作太频繁，请稍后再试',
  PERMISSION_DENIED: '你没有执行这个操作的权限',
};

export function apiMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError && CODE_MESSAGE[err.code]) return CODE_MESSAGE[err.code]!;
  if (err instanceof ApiError && err.message && err.status > 0 && err.status < 500) return err.message;
  return fallback;
}
