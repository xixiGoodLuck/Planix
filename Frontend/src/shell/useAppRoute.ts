import { useCallback, useEffect, useState } from 'react';
import type { AppRoute } from '../types';

const routes: AppRoute[] = ['dashboard', 'calendar', 'notes', 'goals', 'settings', 'command'];
const defaultRoute: AppRoute = 'command';

function readRouteFromHash(): AppRoute {
  const candidate = window.location.hash.replace(/^#\/?/, '').split('?')[0];
  return routes.includes(candidate as AppRoute) ? (candidate as AppRoute) : defaultRoute;
}

export function useAppRoute() {
  const [route, setRouteState] = useState<AppRoute>(() => readRouteFromHash());

  useEffect(() => {
    if (!window.location.hash) {
      window.history.replaceState(null, '', `#/${defaultRoute}`);
    }

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
