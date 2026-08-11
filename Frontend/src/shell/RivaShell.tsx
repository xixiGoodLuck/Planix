import type { ReactNode } from 'react';
import type { AppRoute, Language } from '../types';
import { AppMenu } from './AppMenu';

interface RivaShellProps {
  route: AppRoute;
  language: Language;
  onRouteChange: (route: AppRoute) => void;
  onLanguageChange: (language: Language) => void;
  t: (key: string) => string;
  children: ReactNode;
}

export function RivaShell(props: RivaShellProps) {
  return (
    <div className={`riva-shell ${props.route === 'learning' ? 'learning-shell' : ''}`}>
      <AppMenu
        route={props.route}
        language={props.language}
        onRouteChange={props.onRouteChange}
        onLanguageChange={props.onLanguageChange}
        t={props.t}
      />
      <main className="riva-main">{props.children}</main>
    </div>
  );
}
