import type { AuthSession } from '../../api/client';

const HOME = '/tasks/new';

/** Only same-app paths are accepted as ?next=, so a crafted link cannot bounce the user off-site. */
export function safeNext(value: string | null | undefined): string {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.startsWith('/login') || value.startsWith('/onboarding')) return HOME;
  return value;
}

export function onboardingDone(session: AuthSession | null): boolean {
  const user = session?.user as { onboarding_completed_at?: string | null } | null | undefined;
  return !!user?.onboarding_completed_at;
}

/** First sign-in goes through onboarding; later ones go straight to ?next=. */
export function afterLogin(session: AuthSession, next: string): string {
  return onboardingDone(session) ? next : '/onboarding' + (next !== HOME ? '?next=' + encodeURIComponent(next) : '');
}
