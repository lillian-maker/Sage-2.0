import { Suspense, lazy, useEffect, useMemo, useState, type ComponentType, type LazyExoticComponent } from 'react';
import { loadSession, onSessionChange, type AuthSession } from './api/client';
import { routes, type AppRoute } from './routes';
import { AppShell } from './app/AppShell';
import { ThemeProvider } from './app/ThemeProvider';
import { createMatcher, navigate, useLocation, type ScreenProps } from './app/router';
import { EmptyState, LoadingState } from './components/States/States';

const ComponentGallery = lazy(() => import('./gallery/ComponentGallery'));

type ScreenModule = { default: ComponentType<ScreenProps> };
const modules = import.meta.glob<ScreenModule>('./screens/*/index.tsx');

const screens = new Map<string, LazyExoticComponent<ComponentType<ScreenProps>>>();
for (const route of routes) {
  const load = modules[`./screens/${route.name}/index.tsx`];
  if (load) screens.set(route.name, lazy(load));
}

const match = createMatcher(routes.map((r) => ({ path: r.path, value: r })));

/** Routes reachable without a session. */
const PUBLIC = new Set(['login', 'onboarding']);
const HOME = '/tasks/new';

function useSession() {
  const [state, setState] = useState<{ session: AuthSession | null; failed: boolean }>({ session: null, failed: false });
  useEffect(() => {
    loadSession()
      .then((session) => setState({ session, failed: false }))
      .catch(() => setState({ session: null, failed: true }));
    // Login, logout and account switches replace the session; route guards must see the new one.
    return onSessionChange((session) => setState({ session, failed: false }));
  }, []);
  return state;
}

function Router() {
  const location = useLocation();
  const { session, failed } = useSession();
  const found = useMemo(() => match(location.pathname), [location.pathname]);
  const query = useMemo(() => new URLSearchParams(location.search), [location.search]);

  const route: AppRoute | undefined = found?.value;
  const needsLogin = !!route && !PUBLIC.has(route.name) && session !== null && !session.authenticated;

  useEffect(() => {
    if (location.pathname === '/') navigate(HOME, { replace: true });
    else if (needsLogin) {
      const next = location.pathname + location.search + (location.hash ? '#' + location.hash : '');
      navigate('/login?next=' + encodeURIComponent(next), { replace: true });
    }
  }, [location, needsLogin]);

  if (location.pathname === '/__components') {
    return (
      <Suspense fallback={<LoadingState />}>
        <ComponentGallery />
      </Suspense>
    );
  }

  const Screen = route && screens.get(route.name);
  let body;
  if (!route || !Screen) {
    body = <EmptyState icon="search" title="页面不存在" description="地址可能有误，或该页面已移除" action={{ label: '返回首页', onClick: () => navigate(HOME) }} />;
  } else if (!session && !failed && !PUBLIC.has(route.name)) {
    body = <LoadingState />;
  } else {
    body = (
      <Suspense fallback={<LoadingState />}>
        <Screen params={found?.params ?? {}} query={query} hash={location.hash} navigate={navigate} />
      </Suspense>
    );
  }
  // When the session read itself fails the shell still renders, so its own reads show inline errors.
  if (route?.shell && (session?.authenticated || failed)) return <AppShell pathname={location.pathname}>{body}</AppShell>;
  return body;
}

export function App() {
  return (
    <ThemeProvider>
      <Router />
    </ThemeProvider>
  );
}
