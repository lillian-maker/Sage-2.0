import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { useApiQuery } from '../../app/useApi';
import { errorMessage } from '../../components/States/States';
import { getProfile, patchProfile, type Profile, type ProfilePatch } from './api';
import type { SaveStatus } from './SettingsLayout';

/** Tracks the header “已保存” state for pages that write one control at a time. */
export function useSaveStatus() {
  const [status, setStatus] = useState<SaveStatus>('idle');
  const [message, setMessage] = useState<string | null>(null);
  const pending = useRef(0);
  const run = useCallback(async <T,>(action: () => Promise<T>): Promise<T | undefined> => {
    pending.current += 1;
    setStatus('saving');
    setMessage(null);
    try {
      const out = await action();
      pending.current -= 1;
      if (pending.current === 0) setStatus('saved');
      return out;
    } catch (err) {
      pending.current -= 1;
      setStatus('error');
      setMessage(err instanceof ApiError ? saveError(err) : '保存失败，请重试');
      return undefined;
    }
  }, []);
  return { status, message, run, markSaved: () => setStatus('saved') };
}

function saveError(err: ApiError): string {
  if (err.code === 'VALIDATION_FAILED' || err.code === 'INVALID_REQUEST') return '内容不符合要求，未保存';
  if (err.code === 'SENSITIVE_CONFIRMATION_REQUIRED') return '需要先完成身份验证才能修改此项';
  if (err.code === 'PERMISSION_DENIED' || err.code === 'FORBIDDEN') return '你没有修改此项的权限';
  if (err.code === 'VERSION_CONFLICT') return '已被其他人修改，请刷新后重试';
  return errorMessage(err, '保存失败，请重试');
}

/** account/profile with optimistic single-key writes; a failed write restores the server value. */
export function useProfile() {
  const query = useApiQuery((signal) => getProfile(signal), []);
  const [profile, setProfile] = useState<Profile | undefined>(undefined);
  const save = useSaveStatus();
  useEffect(() => {
    if (query.data) {
      setProfile(query.data);
      save.markSaved();
    }
    // markSaved is stable enough for this one-shot sync; re-running on its identity would reset the status.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.data]);
  const patch = useCallback(
    async (changes: ProfilePatch) => {
      let before: Profile | undefined;
      setProfile((p) => {
        before = p;
        return p ? { ...p, ...changes } : p;
      });
      const saved = await save.run(() => patchProfile(changes));
      if (saved) setProfile(saved);
      else if (before) setProfile(before);
      return !!saved;
    },
    [save.run],
  );
  return { query, profile, patch, save };
}
