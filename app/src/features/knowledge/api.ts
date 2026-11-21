import { request } from '../../api/client';
import type { Page } from '../experts/api';

/** Local shapes for 39–42 屏; api/types.ts types index_state etc. as numbers, which the live API does not return. */

export type KbScope = 'personal' | 'workspace' | 'account';

export interface KnowledgeBase {
  id: string;
  name: string;
  scope: KbScope;
  workspace: { id: string; name: string } | null;
  cite_audience: string;
  expert_cite_enabled: boolean;
  editor_department: { id: string; name: string } | null;
  can_edit: boolean;
  document_count: number;
  processing_count: number;
  failed_count: number;
  failed_document_ids: string[];
  content_updated_at: string | null;
}

export interface KnowledgeBaseList extends Page<KnowledgeBase> {
  index_summary: { last_run_at: string | null; failed_count: number };
}

export interface KnowledgeBaseDetail extends KnowledgeBase {
  owner: { id: string; name: string } | null;
  editors: Array<{ id: string; name: string }>;
  citation_count: number;
  stale_policy: { threshold_days: number } | null;
  version: number;
  created_at: string;
}

export type IndexState = 'queued' | 'indexing' | 'indexed' | 'failed';

export interface DocumentItem {
  id: string;
  title: string;
  source_kind: 'upload' | 'web' | 'artifact' | string;
  uploaded_by: { id: string; name: string } | null;
  source_expert: { id: string; name: string } | null;
  source_url: string | null;
  refresh_interval: string | null;
  size_bytes: number | null;
  page_count: number | null;
  index_state: IndexState | string;
  index_progress: number | null;
  data_date: string | null;
  is_stale: boolean;
  citation_count: number;
  updated_at: string;
}

export interface DocumentDetail extends DocumentItem {
  knowledge_base: { id: string; name: string; scope: KbScope; workspace: { id: string; name: string } | null; expert_cite_enabled: boolean };
  created_at: string;
  can_edit: boolean;
  related_entities: Array<{ id: string; name: string; entity_type: string }>;
}

export interface PageBlock {
  kind: 'heading' | 'paragraph' | string;
  text: string;
  spans: Array<{ start: number; end: number; kind: 'entity' | 'highlight'; entity_id?: string }>;
}

export interface DocPage {
  page: number;
  page_count: number;
  blocks: PageBlock[];
}

export interface Citation {
  id: string;
  task: { id: string; title: string; lead_expert: { id: string; name: string; avatar_ref: string | null } | null } | null;
  artifact: { id: string; title: string } | null;
  locator: string | null;
  ref_index: number;
  created_at: string;
}

export type EntityType = 'market' | 'competitor' | 'regulation' | 'product' | 'channel' | 'task' | 'time';

export interface EntityItem {
  id: string;
  name: string;
  entity_type: EntityType;
  subtitle: string;
  has_conflict: boolean;
}

export interface Fact {
  id: string;
  statement: string;
  source_label: string;
  source_document_id: string | null;
  source_date: string | null;
  validity: 'current' | 'superseded';
  superseded_by: string | null;
  conflict_key: string | null;
}

export interface EntityDetail extends EntityItem {
  cited_task_count: number;
  facts_total: number;
  facts: Fact[];
  conflicts: Array<{ conflict_key: string; facts: Fact[]; preferred_fact_id: string | null }>;
  cited_tasks: Array<{ id: string; title: string; last_cited_at: string }>;
  documents: Array<{ id: string; title: string }>;
}

export interface Neighborhood {
  center_id: string;
  nodes: Array<EntityItem & { is_center: boolean }>;
  edges: Array<{ from: string; to: string; kind: string }>;
}

export interface AuditItem {
  id?: string;
  action?: string;
  actor?: { name: string } | null;
  summary?: string;
  created_at?: string;
  [key: string]: unknown;
}

const id = encodeURIComponent;

export const knowledgeApi = {
  bases: (q: { scope?: string | undefined; q?: string | undefined; citable?: boolean | undefined }, signal?: AbortSignal) =>
    request<KnowledgeBaseList>('GET', 'app/knowledge-bases', { query: { ...q, citable: q.citable ? 'true' : undefined }, signal }),
  base: (kbId: string, signal?: AbortSignal) => request<KnowledgeBaseDetail>('GET', `app/knowledge-bases/${id(kbId)}`, { signal }),
  updateBase: (kbId: string, version: number, patch: { expert_cite_enabled?: boolean }) =>
    request<{ knowledge_base: KnowledgeBaseDetail }>('PATCH', `app/knowledge-bases/${id(kbId)}`, { body: { version, ...patch } }),
  audit: (kbId: string, signal?: AbortSignal) => request<Page<AuditItem>>('GET', `app/knowledge-bases/${id(kbId)}/audit`, { signal }),
  documents: (q: { knowledge_base?: string | undefined; q?: string | undefined; sort?: string | undefined; offset?: number | undefined }, signal?: AbortSignal) =>
    request<Page<DocumentItem>>('GET', 'app/documents', { query: q, signal }),
  document: (docId: string, signal?: AbortSignal) => request<DocumentDetail>('GET', `app/documents/${id(docId)}`, { signal }),
  page: (docId: string, n: number, signal?: AbortSignal) => request<DocPage>('GET', `app/documents/${id(docId)}/pages/${n}`, { signal }),
  reindex: (docId: string) => request<{ document: DocumentDetail }>('POST', `app/documents/${id(docId)}/reindex`, { body: {} }),
  downloadUrl: (docId: string) => `/api/app/documents/${id(docId)}/download`,
  citations: (docId: string, signal?: AbortSignal) => request<Page<Citation>>('GET', 'app/citations', { query: { document: docId }, signal }),
  entities: (q: { type?: string | undefined; q?: string | undefined; include_superseded?: boolean | undefined; offset?: number | undefined }, signal?: AbortSignal) =>
    request<Page<EntityItem> & { type_counts: Record<EntityType, number> }>('GET', 'app/graph/entities', {
      query: { ...q, include_superseded: q.include_superseded ? 'true' : undefined },
      signal,
    }),
  entity: (entityId: string, includeSuperseded: boolean, signal?: AbortSignal) =>
    request<EntityDetail>('GET', `app/graph/entities/${id(entityId)}`, { query: { include_superseded: includeSuperseded ? 'true' : undefined }, signal }),
  neighborhood: (entityId: string, signal?: AbortSignal) => request<Neighborhood>('GET', `app/graph/entities/${id(entityId)}/neighborhood`, { signal }),
  resolveConflict: (entityId: string, conflictKey: string, preferredFactId: string) =>
    request<EntityDetail>('POST', `app/graph/entities/${id(entityId)}/conflicts/${id(conflictKey)}/resolve`, { body: { preferred_fact_id: preferredFactId } }),
};

export const SCOPE_LABEL: Record<string, string> = { personal: '个人', workspace: '工作区', account: '企业' };

export const AUDIENCE_LABEL: Record<string, string> = {
  owner_only: '仅本人',
  workspace_all: '工作区全体',
  account_all: '企业全员',
  department: '指定部门',
};

export function scopeLine(kb: Pick<KnowledgeBase, 'scope' | 'workspace' | 'cite_audience'>): string {
  const scope = SCOPE_LABEL[kb.scope] ?? kb.scope;
  if (kb.scope === 'workspace' && kb.workspace) return `${scope} · ${kb.workspace.name}`;
  return `${scope} · ${AUDIENCE_LABEL[kb.cite_audience] ?? kb.cite_audience}`;
}

/** Scope without the audience, as in the expert knowledge step (37): “个人”, “工作区 · 德国市场进入”, “企业”. */
export function scopeShortLine(kb: Pick<KnowledgeBase, 'scope' | 'workspace'>): string {
  const scope = SCOPE_LABEL[kb.scope] ?? kb.scope;
  return kb.scope === 'workspace' && kb.workspace ? `${scope} · ${kb.workspace.name}` : scope;
}

/** List-row scope as on 39: “个人 · 仅本人”, “工作区 · 德国市场进入 · 全体可引用”, “企业 · 全员可引用”, “企业 · 客服组可编辑”. */
export function scopeListLine(kb: Pick<KnowledgeBase, 'scope' | 'workspace' | 'cite_audience'> & { editor_department?: { name: string } | null }): string {
  if (kb.scope === 'workspace' && kb.workspace) return `${scopeLine(kb)} · ${kb.cite_audience === 'workspace_all' ? '全体可引用' : AUDIENCE_LABEL[kb.cite_audience] ?? kb.cite_audience}`;
  if (kb.scope === 'account') return `${SCOPE_LABEL.account} · ${kb.editor_department ? `${kb.editor_department.name}可编辑` : kb.cite_audience === 'account_all' ? '全员可引用' : AUDIENCE_LABEL[kb.cite_audience] ?? kb.cite_audience}`;
  return scopeLine(kb);
}

export function fileSize(bytes: number | null | undefined): string {
  if (!bytes && bytes !== 0) return '';
  if (bytes >= 10 * 1024 * 1024) return `${Math.round(bytes / 1024 / 1024)} MB`;
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

export const ENTITY_TYPES: Array<{ key: EntityType; label: string; color: string }> = [
  { key: 'market', label: '市场', color: 'var(--color-blue-590)' },
  { key: 'competitor', label: '竞品', color: 'var(--color-red-500)' },
  { key: 'regulation', label: '法规', color: 'var(--color-orange-660)' },
  { key: 'product', label: '产品', color: 'var(--color-green-660)' },
  { key: 'channel', label: '渠道', color: 'var(--color-purple-500, #7A5AF8)' },
  { key: 'task', label: '任务', color: 'var(--color-slate-540)' },
];
