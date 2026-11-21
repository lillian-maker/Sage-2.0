import { request } from '../../api/client';

/**
 * Local wrappers for the 27–38 屏 endpoints: the generated api/types.ts entries for these
 * responses are incomplete, so the shapes below follow the live responses.
 */

export type ExpertGroup = 'all' | 'management' | 'role' | 'mine' | 'shared' | 'teams';

export interface ExpertItem {
  id: string;
  name: string;
  title: string;
  group: 'management' | 'role';
  domain: string;
  specialty: string | null;
  responsibilities: string;
  capability_tags: string[];
  avatar_ref: string | null;
  source: 'system' | 'custom';
  status: 'draft' | 'published' | 'archived';
  assignable: boolean;
  is_mine: boolean;
}

export interface ExpertList {
  items: ExpertItem[];
  offset: number;
  total: number;
  next_offset: number | null;
  counts: { all: number; management: number; role: number; mine: number; shared: number };
  domain_counts: Array<{ domain: string; count: number }>;
  assistant: ExpertItem | null;
}

export interface PersonRef {
  id: string;
  name: string;
  title?: string;
  specialty?: string | null;
  avatar_ref: string | null;
}

export interface ExpertSkill {
  extension_id: string;
  name: string;
  publisher_tier: string;
  installed_version: string | null;
  latest_version: string | null;
  install_state: string;
  required_connector: { id: string; name: string; connection_state: string } | null;
}

export interface ExpertTool {
  extension_id: string;
  name: string;
  kind: string;
  access: string;
  required_for: { id: string; name: string } | null;
  connection_state: string | null;
  connection_id: string | null;
}

export interface ExpertDetail extends ExpertItem {
  stats: { skill_count: number; history_task_count: number };
  skills: ExpertSkill[];
  tools: ExpertTool[];
  recent_tasks: Array<{ id: string; title: string; workspace: { id: string; name: string } | null; role: string; last_activity_at: string }>;
  wizard_step: number | null;
  start_mode: 'blank' | 'describe' | 'template' | null;
  start_description: string | null;
  template_from: { id: string; name: string } | null;
  communication_style: 'concise' | 'detailed' | 'conclusion_first';
  uncertainty_policy: 'ask_first' | 'act_then_confirm';
  forbidden_rules: string[];
  knowledge_bases: Array<{ id: string; name: string }>;
  similar_experts: Array<PersonRef>;
  try_estimated_credits: number;
  catalog_code: string | null;
  version_label: string;
  share_scope: 'only_me' | 'department' | 'account';
  version: number;
  updated_at: string;
  reports_to: PersonRef | null;
  can_edit: boolean;
}

export type Relation = 'handoff' | 'upstream' | 'downstream' | 'reviews_me' | 'reports_to';

export interface ExpertNetwork {
  center: PersonRef;
  window_days: number;
  peers: Array<{
    expert: PersonRef;
    relation: Relation;
    collaboration_count: number;
    acceptance_rate: number | null;
    return_count: number;
    summary: string;
  }>;
}

export interface Team {
  id: string;
  name: string;
  description: string;
  source: 'template' | 'custom';
  visibility: 'only_me' | 'account';
  is_mine: boolean;
  lead_expert: PersonRef;
  members: PersonRef[];
  usage_count: number;
  created_at: string;
}

export interface Page<T> {
  items: T[];
  offset: number;
  total: number;
  next_offset: number | null;
}

export type ExpertPatch = Partial<{
  wizard_step: number;
  name: string;
  title: string;
  responsibilities: string;
  capability_tags: string[];
  communication_style: ExpertDetail['communication_style'];
  uncertainty_policy: ExpertDetail['uncertainty_policy'];
  forbidden_rules: string[];
  skill_ids: string[];
  knowledge_base_ids: string[];
}>;

const id = encodeURIComponent;

export const expertsApi = {
  list: (q: { group?: string | undefined; domain?: string | undefined; q?: string | undefined; offset?: number | undefined }, signal?: AbortSignal) =>
    request<ExpertList>('GET', 'app/experts', { query: q, signal }),
  detail: (expertId: string, signal?: AbortSignal) => request<ExpertDetail>('GET', `app/experts/${id(expertId)}`, { signal }),
  network: (expertId: string, signal?: AbortSignal) => request<ExpertNetwork>('GET', `app/experts/${id(expertId)}/network`, { signal }),
  create: (body: { start_mode: 'blank' | 'describe' | 'template'; start_description?: string; template_from?: string }) =>
    request<{ expert: ExpertDetail }>('POST', 'app/experts', { body }),
  update: (expertId: string, version: number, patch: ExpertPatch) =>
    request<{ expert: ExpertDetail }>('PATCH', `app/experts/${id(expertId)}`, { body: { version, ...patch } }),
  publish: (expertId: string, version: number) =>
    request<{ expert: ExpertDetail }>('POST', `app/experts/${id(expertId)}/publish`, { body: { version } }),
  avatar: (expertId: string) => request<{ avatar_ref: string }>('POST', `app/experts/${id(expertId)}/avatar`, { body: {} }),
  tryOut: (expertId: string, prompt: string) =>
    request<{ reply: string; credits_used: number }>('POST', `app/experts/${id(expertId)}/try`, { body: { prompt } }),
  teams: (signal?: AbortSignal) => request<Page<Team>>('GET', 'app/teams', { signal }),
  createTeam: (body: { name: string; description?: string; member_ids: string[]; lead_expert_id: string; visibility: 'only_me' | 'account' }) =>
    request<{ team: Team }>('POST', 'app/teams', { body }),
};

/** Loads every page of a paged list (the expert catalog is ~55 rows, pages are 50). */
export async function allPages<T>(load: (offset: number) => Promise<Page<T>>): Promise<T[]> {
  const out: T[] = [];
  let offset: number | null = 0;
  while (offset !== null) {
    const p: Page<T> = await load(offset);
    out.push(...p.items);
    offset = p.next_offset;
  }
  return out;
}

export function displayTitle(e: { title?: string | null; specialty?: string | null }): string {
  return e.title || e.specialty || '';
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

export function relativeDay(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  const now = new Date();
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 86400000);
  if (diff <= 0) return `今天 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  if (diff === 1) return '昨天';
  if (diff < 7) return `${diff} 天前`;
  return shortDate(iso);
}

export const TIER_LABEL: Record<string, string> = { official: '官方', community: '社区', local: '本地导入', reviewed: '社区 · 已审核' };
