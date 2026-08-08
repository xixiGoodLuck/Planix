import { useCallback, useEffect, useState } from 'react';
import type { AppRoute } from '../types';

export const appRoutes: AppRoute[] = ['calendar', 'settings', 'command'];
export const appMenuRoutes = ['calendar', 'settings'] as const satisfies readonly AppRoute[];
export const defaultRoute: AppRoute = 'command';

export function normalizeAppRoute(candidate: string): AppRoute {
  return appRoutes.includes(candidate as AppRoute) ? (candidate as AppRoute) : defaultRoute;
}

function readRouteFromHash(): AppRoute {
  const candidate = window.location.hash.replace(/^#\/?/, '').split('?')[0];
  const route = normalizeAppRoute(candidate);
  if (route !== candidate) window.history.replaceState(null, '', `#/${route}`);
  return route;
}

export function useAppRoute() {
  const [route, setRouteState] = useState<AppRoute>(() => readRouteFromHash());

  useEffect(() => {
    const handleHashChange = () => setRouteState(readRouteFromHash());
    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, []);

  const setRoute = useCallback((nextRoute: AppRoute) => {
    if (readRouteFromHash() === nextRoute) {
      setRouteState(nextRoute);
      return;
    }
    window.location.hash = `/${nextRoute}`;
  }, []);

  return { route, setRoute };
}
