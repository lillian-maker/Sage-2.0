import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';

export type QueryState<T> =
  | { status: 'loading'; data: T | undefined; error: null }
  | { status: 'ready'; data: T; error: null }
  | { status: 'error'; data: T | undefined; error: ApiError };

/**
 * Runs a read endpoint and keeps the previous data on failure, because the shared state rules
 * say loaded content stays on screen when a refresh fails ("已加载的内容保留，不清空").
 */
export function useApiQuery<T>(fetcher: (signal: AbortSignal) => Promise<T>, deps: unknown[]) {
  const [state, setState] = useState<QueryState<T>>({ status: 'loading', data: undefined, error: null });
  const [nonce, setNonce] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    const controller = new AbortController();
    setState((s) => ({ status: 'loading', data: s.data, error: null }));
    fetcherRef
      .current(controller.signal)
      .then((data) => setState({ status: 'ready', data, error: null }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const error = err instanceof ApiError ? err : new ApiError(0, 'UNKNOWN', '读取失败，请重试');
        setState((s) => ({ status: 'error', data: s.data, error }));
      });
    return () => controller.abort();
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { ...state, reload };
}

/** Tracks one write at a time so buttons can show busy and the error can be surfaced. */
export function useSubmit<A extends unknown[], R>(action: (...args: A) => Promise<R>) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const actionRef = useRef(action);
  actionRef.current = action;
  const submit = useCallback(async (...args: A): Promise<R | undefined> => {
    setSubmitting(true);
    setError(null);
    try {
      return await actionRef.current(...args);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, 'UNKNOWN', '提交失败，请重试'));
      return undefined;
    } finally {
      setSubmitting(false);
    }
  }, []);
  return { submit, submitting, error, clearError: () => setError(null) };
}
