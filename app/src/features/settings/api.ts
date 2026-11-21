/**
 * Local wrappers for the settings screens. The generated endpoint types in api/types.ts do not
 * match the live responses for these routes (several keys are missing or mistyped), and a few
 * routes have no generated wrapper (GET app/settings/notifications, GET app/org/policy,
 * POST account/avatar); the shapes below follow the backend responses.
 */
import { ApiError, loadSession, request } from '../../api/client';

export interface Profile {
  display_name: string;
  email: string;
  email_read_only: boolean;
  avatar_url: string | null;
  assistant_name: string;
  preferred_name: string;
  business_markets: string[];
  business_channels: string[];
  business_categories: string[];
  theme_style: string;
  appearance_mode: string;
  density: string;
  language: 'system' | 'zh-Hans' | 'en';
  memory_model: 'standard' | 'deep';
  default_model: 'auto' | 'standard' | 'deep';
  dnd_enabled: boolean;
  dnd_start: string | null;
  dnd_end: string | null;
  badge_enabled: boolean;
  memory_auto_enabled: boolean;
  complexity_hint_enabled: boolean;
  ext_auto_invoke: boolean;
  ext_auto_update: boolean;
  ext_show_needs_connection: boolean;
  two_step_on_new_device: boolean;
  idle_lock_minutes: number | null;
}

export type ProfilePatch = Partial<Omit<Profile, 'email' | 'email_read_only' | 'avatar_url'>>;

export const getProfile = (signal?: AbortSignal) => request<Profile>('GET', 'account/profile', { signal });
export const patchProfile = (body: ProfilePatch) => request<Profile>('PATCH', 'account/profile', { body });

/** POST account/avatar is multipart; client.request only sends JSON, so this uses fetch with the same CSRF rules. */
export async function uploadAvatar(file: File): Promise<{ avatar_url: string | null }> {
  const s = await loadSession();
  const form = new FormData();
  form.append('avatar', file);
  const res = await fetch('/api/account/avatar', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { Accept: 'application/json', 'X-CSRFToken': s.csrf_token },
    body: form,
  });
  const data = (await res.json().catch(() => ({}))) as { error?: { code?: string; message?: string }; avatar_url?: string | null };
  if (!res.ok) throw new ApiError(res.status, data.error?.code || 'HTTP_' + res.status, data.error?.message || '上传失败');
  return { avatar_url: data.avatar_url ?? null };
}

export const logout = () => request<unknown>('POST', 'auth/logout', { body: {} });
export const selectAccount = (account_id: string) => request<unknown>('POST', 'billing/accounts/select', { body: { account_id } });

export type NotificationType = 'needs_action' | 'task_completed' | 'mention_assign' | 'billing';
export type NotificationChannel = 'in_app' | 'push' | 'email';
export interface NotificationPref {
  type: NotificationType;
  channel: NotificationChannel;
  enabled: boolean;
  is_default?: boolean;
}
export const getNotificationPrefs = (signal?: AbortSignal) =>
  request<{ items: NotificationPref[] }>('GET', 'app/settings/notifications', { signal });
export const putNotificationPrefs = (items: NotificationPref[]) =>
  request<{ items: NotificationPref[] }>('PUT', 'app/settings/notifications', {
    body: { items: items.map(({ type, channel, enabled }) => ({ type, channel, enabled })) },
  });

export type MemoryCategory = 'about_me' | 'recent_projects' | 'lessons';
export interface Memory {
  id: string;
  category: MemoryCategory;
  content: string;
  source_kind: 'onboarding' | 'task' | 'conversation' | string;
  source_task: { id: string; title: string } | null;
  created_at: string;
  edited_at: string | null;
}
export interface MemoriesPage {
  items: Memory[];
  category_counts: Record<MemoryCategory, number>;
  offset: number;
  total: number;
  next_offset: number | null;
}
export const getMemories = (offset: number, signal?: AbortSignal) =>
  request<MemoriesPage>('GET', 'app/memories', { query: { offset }, signal });
export const patchMemory = (id: string, content: string) =>
  request<Memory>('PATCH', `app/memories/${encodeURIComponent(id)}`, { body: { content } });
export const deleteMemory = (id: string) => request<unknown>('DELETE', `app/memories/${encodeURIComponent(id)}`, { body: {} });

export interface DeviceSession {
  id: string;
  client: string;
  location: string;
  ip: string;
  created_at: string;
  is_current: boolean;
  device_name: string | null;
  app_version: string | null;
  last_seen_at: string | null;
}
export const getSessions = (offset: number, signal?: AbortSignal) =>
  request<{ items: DeviceSession[]; offset: number; total: number; next_offset: number | null }>('GET', 'account/sessions', {
    query: { offset },
    signal,
  });
export const revokeSession = (session_id: string) =>
  request<{ revoked_count: number }>('POST', 'account/sessions/revoke', { body: { session_id } });

export interface Grant {
  id: string;
  extension: { id: string; name: string };
  action_kind: string;
  /** The approved request's wording, e.g. “读取订单”. */
  scope_text?: string;
  scope: 'task' | 'workspace' | string;
  task: { id: string; title: string } | null;
  workspace: { id: string; name: string } | null;
  created_at: string;
}
export const getGrants = (offset: number, signal?: AbortSignal) =>
  request<{ items: Grant[]; offset: number; total: number; next_offset: number | null }>('GET', 'app/grants', { query: { offset }, signal });
export const revokeGrant = (id: string) => request<unknown>('DELETE', `app/grants/${encodeURIComponent(id)}`, { body: {} });

export interface InstalledExtension {
  id: string;
  kind: 'plugin' | 'skill' | 'connector' | 'agent' | string;
  name: string;
}
export const getInstalledExtensions = (signal?: AbortSignal) =>
  request<{ items: InstalledExtension[]; total?: number; kind_counts?: Record<string, number> }>('GET', 'app/extensions', {
    query: { scope: 'installed' },
    signal,
  });

export interface KnowledgeIndexSummary {
  index_summary?: { last_run_at: string | null; failed_count: number };
}
export const getKnowledgeIndexSummary = (signal?: AbortSignal) =>
  request<KnowledgeIndexSummary>('GET', 'app/knowledge-bases', { signal });
