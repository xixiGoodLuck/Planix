import { Menu, Settings, X } from 'lucide-react';
import type { AppRoute } from '../types';
import { appMenuRoutes } from './useAppRoute';

interface AppMenuProps {
  route: AppRoute;
  onRouteChange: (route: AppRoute) => void;
  t: (key: string) => string;
}

export function AppMenu({ route, onRouteChange, t }: AppMenuProps) {
  return (
    <aside className="app-menu" aria-label={t('shell.navigation')}>
      <input className="menu-toggle-input" type="checkbox" id="planix-menu-toggle" aria-label={t('shell.menu')} />
      <label className="menu-toggle" htmlFor="planix-menu-toggle" title={t('shell.menu')}>
        <Menu className="menu-open-icon" size={20} />
        <X className="menu-close-icon" size={20} />
      </label>
      <div className="menu-panel">
        <div className="menu-brand">
          <button className="brand-mark" onClick={() => onRouteChange('learning')} type="button" aria-label={t('common.appName')} title={t('common.appName')}>P</button>
          <div><strong>{t('common.appName')}</strong><span>{t('shell.productTagline')}</span></div>
        </div>
        <nav className="menu-nav">
          {appMenuRoutes.map((itemRoute) => {
            const active = route === itemRoute;
            const label = itemRoute === 'learning' ? t('shell.learningNav') : t('shell.settings');
            return (
              <button key={itemRoute} className={active ? 'active' : ''} onClick={() => onRouteChange(itemRoute)} aria-current={active ? 'page' : undefined} aria-label={label} title={label}>
                <span className="menu-icon" aria-hidden="true">
                  {itemRoute === 'learning' ? <span className="learning-p-mark">P</span> : <Settings size={18} />}
                </span>
                <span className="menu-label">{label}</span>
              </button>
            );
          })}
        </nav>
      </div>
    </aside>
  );
}
