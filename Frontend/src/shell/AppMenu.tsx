import { BookOpenCheck, Languages, Menu, Settings, X } from 'lucide-react';
import type { AppRoute, Language } from '../types';
import { appMenuRoutes } from './useAppRoute';

interface AppMenuProps {
  route: AppRoute;
  language: Language;
  onRouteChange: (route: AppRoute) => void;
  onLanguageChange: (language: Language) => void;
  t: (key: string) => string;
}

const menuIcons = { learning: BookOpenCheck, settings: Settings } as const;

export function AppMenu({ route, language, onRouteChange, onLanguageChange, t }: AppMenuProps) {
  return (
    <aside className="app-menu" aria-label={t('shell.navigation')}>
      <input className="menu-toggle-input" type="checkbox" id="planix-menu-toggle" aria-label={t('shell.menu')} />
      <label className="menu-toggle" htmlFor="planix-menu-toggle" title={t('shell.menu')}>
        <Menu className="menu-open-icon" size={20} />
        <X className="menu-close-icon" size={20} />
      </label>
      <div className="menu-panel">
        <div className="menu-brand">
          <button className="brand-mark" onClick={() => onRouteChange('learning')} type="button" aria-label={t('shell.learning')}>P</button>
          <div><strong>{t('common.appName')}</strong><span>{t('shell.productTagline')}</span></div>
        </div>
        <div className="language-switch" aria-label={t('shell.language')}>
          <button className={language === 'zh-CN' ? 'active' : ''} onClick={() => onLanguageChange('zh-CN')}><Languages size={14} />{t('shell.languageZh')}</button>
          <button className={language === 'en-US' ? 'active' : ''} onClick={() => onLanguageChange('en-US')}><Languages size={14} />{t('shell.languageEn')}</button>
        </div>
        <nav className="menu-nav">
          {appMenuRoutes.map((itemRoute) => {
            const Icon = menuIcons[itemRoute];
            const active = route === itemRoute;
            return (
              <button key={itemRoute} className={active ? 'active' : ''} onClick={() => onRouteChange(itemRoute)} aria-current={active ? 'page' : undefined}>
                <Icon size={17} /><span>{t(`shell.${itemRoute}`)}</span>
              </button>
            );
          })}
        </nav>
      </div>
    </aside>
  );
}
