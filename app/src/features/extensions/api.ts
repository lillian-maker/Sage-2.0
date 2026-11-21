import { request } from '../../api/client';
import type { Page } from '../experts/api';

/** Local shapes for 35–36、43–46 屏; installed_version is a string label ("v2.1") in the live API. */

export type ExtensionKind = 'plugin' | 'skill' | 'connector' | 'agent';
export type InstallState = 'not_installed' | 'pending_review' | 'installed' | 'disabled';

export interface Connection {
  id: string;
  extension_id: string;
  state: string;
  connected_at: string | null;
  last_error_code: string | null;
}

export interface ExtensionItem {
  id: string;
  kind: ExtensionKind;
  name: string;
  description: string;
  publisher_tier: string;
  access: string;
  billing_note: string | null;
  installed_version: string | null;
  latest_version: string | null;
  install_state: InstallState;
  enabled: boolean;
  used_by_expert_count: number;
  used_by_inbox: boolean;
  global_usage_count: number | null;
  connection: Connection | null;
  required_connector: { id: string; name: string; connection_state: string; connection?: Connection | null } | null;
  missing_connection: boolean;
}

export interface ExtensionList extends Page<ExtensionItem> {
  tab: 'installed' | 'market';
  counts: { installed: number };
  kind_counts: Record<ExtensionKind, number>;
}

export interface ExtensionDetail extends ExtensionItem {
  permissions: Array<{ name: string; access: 'read_only' | 'write_unavailable' | string }>;
  prerequisite_inputs: string[];
  outputs: string[];
  acceptance_template: string[];
  common_next: Array<{ extension_id: string; name: string; ratio: number; needs_connection: boolean }>;
  used_by_experts: Array<{ id: string; name: string; avatar_ref: string | null }>;
  install: { id: string; installed_version: string; enabled: boolean; install_state: InstallState; installed_at: string } | null;
}

export interface VersionRow {
  version: string;
  released_at: string;
  notes: string | null;
  permission_changes: unknown[];
}

const id = encodeURIComponent;

export const extensionsApi = {
  list: (q: { tab: 'installed' | 'market'; kind?: string | undefined; q?: string | undefined }, signal?: AbortSignal) =>
    request<ExtensionList>('GET', 'app/extensions', { query: q, signal }),
  detail: (extId: string, signal?: AbortSignal) => request<ExtensionDetail>('GET', `app/extensions/${id(extId)}`, { signal }),
  versions: (extId: string, signal?: AbortSignal) => request<Page<VersionRow>>('GET', `app/extensions/${id(extId)}/versions`, { signal }),
  install: (extId: string) => request<{ install: unknown }>('POST', `app/extensions/${id(extId)}/install`, { body: {} }),
  enable: (extId: string) => request<{ install: unknown }>('POST', `app/extensions/${id(extId)}/enable`, { body: {} }),
  disable: (extId: string) => request<{ install: unknown }>('POST', `app/extensions/${id(extId)}/disable`, { body: {} }),
  importManifest: (manifest: unknown) => request<{ extension: ExtensionDetail }>('POST', 'app/extensions/import', { body: { manifest } }),
  connect: (connectionId: string) => request<unknown>('POST', `app/connections/${id(connectionId)}/connect`, { body: {} }),
  disconnect: (connectionId: string) => request<unknown>('POST', `app/connections/${id(connectionId)}/disconnect`, { body: {} }),
};

export const KIND_LABEL: Record<ExtensionKind, string> = { plugin: '插件', skill: '技能', connector: '连接器', agent: '智能体' };

export const TIER_LABEL: Record<string, string> = {
  official: '官方',
  community_reviewed: '社区 · 已审核',
  third_party: '第三方',
  enterprise: '企业',
  local_import: '本地导入',
};
