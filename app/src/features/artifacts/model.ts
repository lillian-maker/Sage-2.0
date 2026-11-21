import { newClientRequestId, request, type Query } from '../../api/client';
import type { IconName } from '../../components/Icon/Icon';
import type { TagProps } from '../../components/Tag/Tag';

const enc = encodeURIComponent;

export type ArtifactKind = 'document' | 'spreadsheet' | 'presentation' | 'webpage' | 'image';
export type VersionState = 'draft' | 'accepted' | 'published';
export type Audience = 'only_me' | 'workspace_members' | 'public_link' | 'encrypted_link';

export interface Author {
  kind: string;
  name: string;
  avatar_ref?: string | null;
}

export interface CurrentVersion {
  number: number;
  state: VersionState;
  label: string;
  previous_number: number | null;
  author: Author | null;
  created_at: string;
  citation_count?: number;
  change_summary?: string | null;
}

export interface Share {
  id: string;
  audience: Audience;
  url: string;
  expires_at: string | null;
}

export interface Artifact {
  id: string;
  title: string;
  kind: ArtifactKind;
  workspace: { id: string; name: string } | null;
  source_task: { id: string; title: string } | null;
  current_version: CurrentVersion | null;
  share: Share | null;
  updated_at: string;
  trashed_at: string | null;
  last_opened_at: string | null;
}

export interface ArtifactDetail extends Artifact {
  delivery: { id: string; decision: string; version: number } | null;
  can_edit: boolean;
}

export interface ArtifactCounts {
  all: number;
  kinds: Partial<Record<ArtifactKind, number>>;
  trash: number;
  workspaces: Array<{ id: string | null; name: string; count: number }>;
}

export interface ArtifactList {
  items: Artifact[];
  counts: ArtifactCounts;
  summary?: { created: number; modified: number };
}

export interface ArtifactQuery {
  scope?: 'mine' | 'shared' | undefined;
  kind?: ArtifactKind | undefined;
  workspace?: string | undefined;
  task?: string | undefined;
  trashed?: 1 | undefined;
  q?: string | undefined;
  sort?: 'updated' | 'opened' | undefined;
}

export interface VersionItem {
  number: number;
  state: VersionState;
  label: string;
  author: Author | null;
  created_at: string;
  change_summary: string | null;
  restored_from: number | null;
  size_bytes: number | null;
}

export type Block =
  | { type: 'heading'; level?: number; text: string }
  | { type: 'paragraph'; text: string }
  | { type: 'table'; columns: string[]; rows: string[][] };

export interface VersionContent {
  number: number;
  kind: ArtifactKind;
  body: { format: string; blocks?: Block[] };
}

export interface Citation {
  id: string;
  ref_index: number;
  locator: string | null;
  document: { id: string; title: string } | null;
  cited_version: number | null;
  label: string;
}

export interface Hunk {
  context?: string;
  deleted?: string;
  inserted?: string;
}

export interface VersionDiff {
  base: { number: number; state: VersionState; label: string; created_at: string };
  target: { number: number; state: VersionState; label: string; author: Author | null; created_at: string; change_summary: string | null };
  hunks: Hunk[];
  changed: boolean;
  restore_preview: { next_number: number } | null;
  can_restore: boolean;
}

export const artifactApi = {
  list: (query: ArtifactQuery, signal?: AbortSignal) => request<ArtifactList>('GET', 'app/artifacts', { query: query as Query, signal }),
  get: (id: string, signal?: AbortSignal) => request<ArtifactDetail>('GET', `app/artifacts/${enc(id)}`, { signal }),
  update: (id: string, body: { title?: string; workspace_id?: string | null }) => request<unknown>('PATCH', `app/artifacts/${enc(id)}`, { body }),
  trash: (id: string) => request<unknown>('POST', `app/artifacts/${enc(id)}/trash`),
  untrash: (id: string) => request<unknown>('DELETE', `app/artifacts/${enc(id)}/trash`),
  share: (id: string, body: { audience: Exclude<Audience, 'only_me'>; expires_in_days?: number; secret?: string }) =>
    request<{ share?: Share }>('POST', `app/artifacts/${enc(id)}/shares`, { body }),
  unshare: (id: string) => request<unknown>('DELETE', `app/artifacts/${enc(id)}/shares`),
  versions: (id: string, signal?: AbortSignal) => request<{ items: VersionItem[]; total: number }>('GET', `app/artifacts/${enc(id)}/versions`, { signal }),
  content: (id: string, n: number, signal?: AbortSignal) => request<VersionContent>('GET', `app/artifacts/${enc(id)}/versions/${n}/content`, { signal }),
  citations: (id: string, n: number, signal?: AbortSignal) =>
    request<{ items: Citation[]; total: number }>('GET', `app/artifacts/${enc(id)}/versions/${n}/citations`, { signal }),
  diff: (id: string, n: number, base: number, signal?: AbortSignal) =>
    request<VersionDiff>('GET', `app/artifacts/${enc(id)}/versions/${n}/diff`, { query: { base }, signal }),
  restore: (id: string, n: number) =>
    request<{ version?: { number: number } }>('POST', `app/artifacts/${enc(id)}/versions/${n}/restore`, { body: { client_request_id: newClientRequestId() } }),
  acceptDelivery: (taskId: string, deliveryId: string) =>
    request<unknown>('POST', `app/tasks/${enc(taskId)}/deliveries/${enc(deliveryId)}/accept`, { body: { client_request_id: newClientRequestId() } }),
};

/** Download is a plain GET returning the file, so it is linked instead of fetched. */
export function downloadUrl(id: string, n: number): string {
  return `/api/app/artifacts/${enc(id)}/versions/${n}/download`;
}

export const KIND_LABEL: Record<ArtifactKind, string> = {
  document: '文档',
  spreadsheet: '表格',
  presentation: '演示',
  webpage: '网页',
  image: '图像',
};

// No grid / slides / image glyphs exist in the exported icon set yet (registered in requests-C.md).
export const KIND_ICON: Record<ArtifactKind, IconName> = {
  document: 'file',
  spreadsheet: 'layers',
  presentation: 'bar-chart',
  webpage: 'globe',
  image: 'file-badge',
};

const STATE_LABEL: Record<VersionState, string> = { draft: '草稿', accepted: '已验收', published: '已发布' };

export function versionTag(v: Pick<CurrentVersion, 'number' | 'state'>): TagProps {
  const tone: TagProps['tone'] = v.state === 'accepted' ? 'success' : v.state === 'published' ? 'info' : 'neutral';
  return { label: `${STATE_LABEL[v.state] ?? ''} v${v.number}`.trim(), tone, size: 20 };
}

/** Version list rows prefer the human label (“大纲 v1”), else the state (Q-B-18). */
export function versionName(v: Pick<VersionItem, 'number' | 'state' | 'label'>): string {
  return `${v.label || STATE_LABEL[v.state] || ''} v${v.number}`.trim();
}

export function stateName(s: VersionState): string {
  return STATE_LABEL[s] ?? '';
}

export function shareText(share: Share | null, now = Date.now()): { icon: IconName; text: string } {
  if (!share || share.audience === 'only_me') return { icon: 'contact', text: '仅本人' };
  if (share.audience === 'workspace_members') return { icon: 'users', text: '工作区成员' };
  const days = share.expires_at ? Math.max(0, Math.ceil((new Date(share.expires_at).getTime() - now) / 86400000)) : null;
  if (share.audience === 'encrypted_link') return { icon: 'link', text: days !== null ? `加密链接 · ${days} 天后过期` : '加密链接' };
  return { icon: 'globe', text: days !== null ? `所有人 · 公开链接 · ${days} 天后过期` : '所有人 · 公开链接' };
}
