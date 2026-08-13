import type { ReactNode } from 'react';
import type { AppRoute } from '../types';
import { AppMenu } from './AppMenu';

interface RivaShellProps {
  route: AppRoute;
  onRouteChange: (route: AppRoute) => void;
  t: (key: string) => string;
  children: ReactNode;
}

export function RivaShell(props: RivaShellProps) {
  return (
    <div className={`riva-shell ${props.route === 'learning' ? 'learning-shell' : ''}`}>
      <AppMenu
        route={props.route}
        onRouteChange={props.onRouteChange}
        t={props.t}
      />
      <main className="riva-main">{props.children}</main>
    </div>
  );
}
