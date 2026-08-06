import type { ReactNode } from 'react';
import type { AppRoute, InspectorSnapshot, Language } from '../types';
import { AppMenu } from './AppMenu';
import { InspectorPanel } from './InspectorPanel';

interface RivaShellProps {
  route: AppRoute;
  language: Language;
  inspector: InspectorSnapshot;
  onRouteChange: (route: AppRoute) => void;
  onLanguageChange: (language: Language) => void;
  onToday: () => void;
  pOnlyMode?: boolean;
  onCommandToggle: () => void;
  t: (key: string) => string;
  children: ReactNode;
}

export function RivaShell(props: RivaShellProps) {
  const {
    route,
    language,
    inspector,
    onRouteChange,
    onLanguageChange,
    onToday,
    pOnlyMode = false,
    onCommandToggle,
    t,
    children
  } = props;

  return (
    <div className={`riva-shell ${route === 'command' ? 'command-shell' : ''} ${pOnlyMode ? 'p-only-shell' : ''}`}>
      <AppMenu
        route={route}
        language={language}
        onRouteChange={onRouteChange}
        onLanguageChange={onLanguageChange}
        onToday={onToday}
        pOnlyMode={pOnlyMode}
        onCommandToggle={onCommandToggle}
        t={t}
      />
      <main className="riva-main">{children}</main>
      {route !== 'command' && <InspectorPanel snapshot={inspector} t={t} />}
    </div>
  );
}
