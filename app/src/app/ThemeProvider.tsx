import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { account } from '../api/endpoints';
import { ApiError, loadSession, onSessionChange } from '../api/client';
import { desktop } from '../features/desktop/bridge';

export type ThemeStyle = 'business_steady' | 'neo_electric' | 'fashion_editorial' | 'comfort_readable' | 'minimal_focus';
export type AppearanceMode = 'system' | 'light' | 'dark';
export type Density = 'theme' | 'comfortable' | 'compact';

export interface ThemePrefs {
  theme_style: ThemeStyle;
  appearance_mode: AppearanceMode;
  density: Density;
}

export const THEME_STYLES: ThemeStyle[] = ['business_steady', 'neo_electric', 'fashion_editorial', 'comfort_readable', 'minimal_focus'];
const APPEARANCES: AppearanceMode[] = ['system', 'light', 'dark'];
const DENSITIES: Density[] = ['theme', 'comfortable', 'compact'];
export const DEFAULT_PREFS: ThemePrefs = { theme_style: 'business_steady', appearance_mode: 'system', density: 'theme' };

// The login screen renders before any profile is readable, so the last known theme is cached locally.
const CACHE_KEY = 'sage.theme-prefs';

function pick<T extends string>(value: unknown, allowed: T[], fallback: T): T {
  return allowed.includes(value as T) ? (value as T) : fallback;
}

function normalize(raw: Partial<Record<keyof ThemePrefs, unknown>> | null | undefined, base: ThemePrefs = DEFAULT_PREFS): ThemePrefs {
  return {
    theme_style: pick(raw?.theme_style, THEME_STYLES, base.theme_style),
    appearance_mode: pick(raw?.appearance_mode, APPEARANCES, base.appearance_mode),
    density: pick(raw?.density, DENSITIES, base.density),
  };
}

function readCache(): ThemePrefs {
  try {
    return normalize(JSON.parse(localStorage.getItem(CACHE_KEY) || 'null'));
  } catch {
    return DEFAULT_PREFS;
  }
}

function writeCache(prefs: ThemePrefs) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(prefs));
  } catch {
    /* storage unavailable (private mode); theme still applies for this session */
  }
}

function usePrefersDark(): boolean {
  const query = '(prefers-color-scheme: dark)';
  const [dark, setDark] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const fn = () => setDark(mq.matches);
    mq.addEventListener('change', fn);
    return () => mq.removeEventListener('change', fn);
  }, []);
  return dark;
}

/** Writes the theme attributes that tokens.css / theme.css select on. */
export function applyThemeAttributes(el: HTMLElement, prefs: ThemePrefs, systemDark: boolean) {
  const appearance = prefs.appearance_mode === 'system' ? (systemDark ? 'dark' : 'light') : prefs.appearance_mode;
  el.dataset.theme = prefs.theme_style;
  el.dataset.appearance = appearance;
  if (prefs.density === 'theme') delete el.dataset.density;
  else el.dataset.density = prefs.density;
}

interface ThemeContextValue {
  prefs: ThemePrefs;
  /** Resolved appearance after following the system setting. */
  appearance: 'light' | 'dark';
  /** The style currently written to data-theme: the onboarding preview while one is shown, else prefs.theme_style. */
  style: ThemeStyle;
  saving: boolean;
  error: ApiError | null;
  /** Applies immediately, PATCHes account/profile, and reverts if the write fails. */
  update: (patch: Partial<ThemePrefs>) => Promise<boolean>;
  /** Shows a theme without saving it (onboarding step 3, Q-D-9); null ends the preview. */
  preview: (style: ThemeStyle | null) => void;
  /** Takes prefs from a profile the caller already saved (e.g. the onboarding PATCH) without writing again. */
  adopt: (profile: Partial<Record<keyof ThemePrefs, unknown>>) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState<ThemePrefs>(readCache);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [previewStyle, setPreviewStyle] = useState<ThemeStyle | null>(null);
  const systemDark = usePrefersDark();

  useEffect(() => {
    applyThemeAttributes(document.documentElement, previewStyle ? { ...prefs, theme_style: previewStyle } : prefs, systemDark);
  }, [prefs, previewStyle, systemDark]);

  // The App icon follows the chosen style only; the onboarding preview does not swap the Dock icon.
  useEffect(() => {
    desktop()?.setAppIcon(prefs.theme_style).catch(() => undefined);
  }, [prefs.theme_style]);

  const adopt = useCallback((profile: Partial<Record<keyof ThemePrefs, unknown>>) => {
    setPrefs((current) => {
      const next = normalize(profile, current);
      writeCache(next);
      return next;
    });
    setPreviewStyle(null);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const load = async (authenticated: boolean) => {
      if (!authenticated) return;
      try {
        const profile = await account.getAccountProfile(undefined, controller.signal);
        if (cancelled) return;
        const next = normalize(profile);
        setPrefs(next);
        writeCache(next);
      } catch (err) {
        if (!cancelled && err instanceof ApiError) setError(err);
      }
    };
    loadSession()
      .then((s) => load(s.authenticated))
      .catch(() => undefined);
    const off = onSessionChange((s) => void load(!!s?.authenticated));
    return () => {
      cancelled = true;
      controller.abort();
      off();
    };
  }, []);

  const update = useCallback(
    async (patch: Partial<ThemePrefs>) => {
      const before = prefs;
      const next = normalize(patch, prefs);
      setPrefs(next);
      setSaving(true);
      setError(null);
      try {
        const saved = await account.patchAccountProfile({ ...patch });
        const confirmed = normalize(saved, next);
        setPrefs(confirmed);
        writeCache(confirmed);
        return true;
      } catch (err) {
        setPrefs(before);
        setError(err instanceof ApiError ? err : new ApiError(0, 'UNKNOWN', '保存失败'));
        return false;
      } finally {
        setSaving(false);
      }
    },
    [prefs],
  );

  const value = useMemo<ThemeContextValue>(
    () => ({
      prefs,
      appearance: prefs.appearance_mode === 'system' ? (systemDark ? 'dark' : 'light') : prefs.appearance_mode,
      style: previewStyle ?? prefs.theme_style,
      saving,
      error,
      update,
      preview: setPreviewStyle,
      adopt,
    }),
    [prefs, previewStyle, systemDark, saving, error, update, adopt],
  );
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

/** Current data-theme style; the default style outside a ThemeProvider (component gallery, isolated renders). */
export function useThemeStyle(): ThemeStyle {
  return useContext(ThemeContext)?.style ?? DEFAULT_PREFS.theme_style;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme 必须在 ThemeProvider 内使用');
  return ctx;
}
