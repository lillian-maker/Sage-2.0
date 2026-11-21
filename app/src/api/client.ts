/**
 * Same-origin client for /api (Vite proxies /api to the Django backend in dev and preview).
 * Conventions follow the existing site client (src/pages/billing.js):
 * - cookies travel with credentials: 'same-origin';
 * - writes send X-CSRFToken taken from GET /api/auth/session → csrf_token, because the
 *   backend sets CSRF_COOKIE_HTTPONLY and the csrftoken cookie is not readable from JS;
 * - /api/billing/* (except accounts/select) and /api/app/* send X-Billing-Account = session.active_account_id
 *   (app/ writes without it are rejected with 409 ACCOUNT_CHANGED);
 * - errors arrive as {error:{code, message}} (backend/http.py).
 */

export type QueryValue = string | number | boolean | null | undefined;
export type Query = Record<string, QueryValue | QueryValue[]>;
export type Method = 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';

export interface AuthSession {
  authenticated: boolean;
  csrf_token: string;
  active_account_id: string | null;
  user: { id: string; display_name: string; masked_identifier: string; avatar_url?: string | null } | null;
  accounts: Array<{ id: string; name: string; kind: string; role: string }>;
  capabilities: Record<string, unknown>;
  [key: string]: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

let session: AuthSession | null = null;
let sessionPromise: Promise<AuthSession> | null = null;
const listeners = new Set<(s: AuthSession | null) => void>();

export function onSessionChange(fn: (s: AuthSession | null) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function currentSession(): AuthSession | null {
  return session;
}

function setSession(next: AuthSession | null) {
  session = next;
  listeners.forEach((fn) => fn(next));
}

function isSession(value: unknown): value is AuthSession {
  const v = value as AuthSession | null;
  return !!v && typeof v.authenticated === 'boolean' && typeof v.csrf_token === 'string' && Array.isArray(v.accounts);
}

/** Loads (or reloads) GET /api/auth/session; concurrent callers share one request. */
export function loadSession(force = false): Promise<AuthSession> {
  if (session && !force) return Promise.resolve(session);
  if (sessionPromise && !force) return sessionPromise;
  sessionPromise = send<unknown>('GET', 'auth/session', {})
    .then((data) => {
      if (!isSession(data)) throw new ApiError(0, 'BAD_RESPONSE', '会话响应格式不正确');
      setSession(data);
      return data;
    })
    .finally(() => {
      sessionPromise = null;
    });
  return sessionPromise;
}

/** Endpoints whose response replaces the session (login, logout, account switch). */
const SESSION_WRITERS = new Set(['auth/verify', 'auth/password-login', 'auth/logout', 'billing/accounts/select']);

export function newClientRequestId(): string {
  return crypto.randomUUID();
}

function buildUrl(path: string, query?: Query): string {
  const url = '/api/' + path.replace(/^\/+/, '');
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, raw] of Object.entries(query)) {
    const values = Array.isArray(raw) ? raw : [raw];
    for (const v of values) if (v !== undefined && v !== null) params.append(key, String(v));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

async function send<T>(
  method: Method,
  path: string,
  opts: { query?: Query | undefined; body?: unknown; signal?: AbortSignal | undefined; csrf?: string | undefined },
): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  const form = opts.body instanceof FormData;
  if (method !== 'GET') {
    if (!form) headers['Content-Type'] = 'application/json';
    if (opts.csrf) headers['X-CSRFToken'] = opts.csrf;
  }
  const scoped = path.startsWith('app/') || (path.startsWith('billing/') && path !== 'billing/accounts/select');
  if (scoped && session?.active_account_id) {
    headers['X-Billing-Account'] = session.active_account_id;
  }
  let response: Response;
  try {
    response = await fetch(buildUrl(path, opts.query), {
      method,
      credentials: 'same-origin',
      headers,
      body: method === 'GET' ? undefined : form ? (opts.body as FormData) : JSON.stringify(opts.body ?? {}),
      signal: opts.signal,
    });
  } catch (err) {
    if ((err as Error).name === 'AbortError') throw err;
    throw new ApiError(0, 'NETWORK_ERROR', '无法连接服务器，请检查网络后重试');
  }
  if (response.status === 204) return undefined as T;
  const type = response.headers.get('content-type') || '';
  if (!type.includes('application/json')) {
    throw new ApiError(response.status, 'BAD_RESPONSE', `服务器返回了非 JSON 响应（${response.status}）`);
  }
  const data: unknown = await response.json();
  if (!response.ok) {
    const e = (data as { error?: { code?: string; message?: string } }).error;
    throw new ApiError(response.status, e?.code || 'HTTP_' + response.status, e?.message || '请求失败');
  }
  return data as T;
}

/** GET that returns a file body; errors still arrive as the JSON error envelope. */
export async function requestBlob(path: string, opts: { query?: Query | undefined; signal?: AbortSignal | undefined } = {}): Promise<Blob> {
  const p = path.replace(/^\/+/, '');
  const headers: Record<string, string> = {};
  if (session?.active_account_id) headers['X-Billing-Account'] = session.active_account_id;
  let response: Response;
  try {
    response = await fetch(buildUrl(p, opts.query), { method: 'GET', credentials: 'same-origin', headers, signal: opts.signal });
  } catch (err) {
    if ((err as Error).name === 'AbortError') throw err;
    throw new ApiError(0, 'NETWORK_ERROR', '无法连接服务器，请检查网络后重试');
  }
  if (!response.ok) {
    let e: { code?: string; message?: string } | undefined;
    try {
      e = ((await response.json()) as { error?: { code?: string; message?: string } }).error;
    } catch {
      e = undefined;
    }
    throw new ApiError(response.status, e?.code || 'HTTP_' + response.status, e?.message || '请求失败');
  }
  return response.blob();
}

export async function request<T>(
  method: Method,
  path: string,
  opts: { query?: Query | undefined; body?: unknown; signal?: AbortSignal | undefined } = {},
): Promise<T> {
  const p = path.replace(/^\/+/, '');
  if (method === 'GET') return send<T>(method, p, opts);
  const s = await loadSession();
  try {
    const data = await send<T>(method, p, { ...opts, csrf: s.csrf_token });
    if (SESSION_WRITERS.has(p)) {
      if (isSession(data)) setSession(data);
      else await loadSession(true);
    }
    return data;
  } catch (err) {
    // Django rotates the CSRF token on login; one retry with a fresh session covers a stale token.
    if (err instanceof ApiError && err.status === 403 && /csrf/i.test(err.code + err.message)) {
      const fresh = await loadSession(true);
      return send<T>(method, p, { ...opts, csrf: fresh.csrf_token });
    }
    throw err;
  }
}
