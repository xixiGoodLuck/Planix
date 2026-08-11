import { BookOpenCheck, CalendarDays, CheckSquare, Languages, Menu, Settings, X } from 'lucide-react';
import type { AppRoute, Language } from '../types';
import { appMenuRoutes } from './useAppRoute';

interface AppMenuProps {
  route: AppRoute;
  language: Language;
  onRouteChange: (route: AppRoute) => void;
  onLanguageChange: (language: Language) => void;
  onToday: () => void;
  pOnlyMode?: boolean;
  onCommandToggle: () => void;
  t: (key: string) => string;
}

const menuIcons: Record<(typeof appMenuRoutes)[number], typeof CalendarDays> = {
  learning: BookOpenCheck,
  calendar: CalendarDays,
  settings: Settings
};

export function AppMenu(props: AppMenuProps) {
  const { route, language, onRouteChange, onLanguageChange, onToday, pOnlyMode = false, onCommandToggle, t } = props;
  const isZh = language === 'zh-CN';
  const commandActive = route === 'command';

  if (pOnlyMode) {
    return (
      <aside className="app-menu p-only" aria-label={t('shell.navigation')}>
        <button
          className="command-nav-button active p-only-button"
          onClick={onCommandToggle}
          title={t('command.title')}
          aria-label={t('command.title')}
        >
          <span className="command-letter-icon">P</span>
        </button>
      </aside>
    );
  }

  return (
    <aside className="app-menu" aria-label={t('shell.navigation')}>
      <input className="menu-toggle-input" type="checkbox" id="planix-menu-toggle" aria-label={t('shell.menu')} />
      <label className="menu-toggle" htmlFor="planix-menu-toggle" title={t('shell.menu')}>
        <Menu className="menu-open-icon" size={20} />
        <X className="menu-close-icon" size={20} />
      </label>

      <div className="menu-panel">
        <div className="menu-brand">
          <button
            className="brand-mark command-brand-button"
            onClick={onCommandToggle}
            title={t('command.title')}
            aria-label={t('command.title')}
            type="button"
          >
            P
          </button>
          <div>
            <strong>{t('common.appName')}</strong>
            <span>{t('shell.productTagline')}</span>
          </div>
        </div>

        <div className="language-switch" aria-label={t('shell.language')}>
          <button className={isZh ? 'active' : ''} onClick={() => onLanguageChange('zh-CN')}>
            <Languages size={14} />
            <span className="language-label">{t('shell.languageZh')}</span>
          </button>
          <button className={!isZh ? 'active' : ''} onClick={() => onLanguageChange('en-US')}>
            <Languages size={14} />
            <span className="language-label">{t('shell.languageEn')}</span>
          </button>
        </div>

        <nav className="menu-nav">
          <button
            className={`command-nav-button ${commandActive ? 'active' : ''}`}
            onClick={onCommandToggle}
            aria-current={commandActive ? 'page' : undefined}
          >
            <span className="command-letter-icon">P</span>
            <span>{t('command.title')}</span>
          </button>
          {appMenuRoutes.map((itemRoute) => {
            const Icon = menuIcons[itemRoute];
            const active = route === itemRoute;
            return (
              <button
                className={active ? 'active' : ''}
                key={itemRoute}
                onClick={() => onRouteChange(itemRoute)}
                aria-current={active ? 'page' : undefined}
              >
                <Icon size={17} />
                <span>{t(`shell.${itemRoute}`)}</span>
              </button>
            );
          })}
        </nav>

        <button className="menu-today" onClick={onToday}>
          <CheckSquare size={16} />
          <span>{t('shell.goToday')}</span>
        </button>
      </div>
    </aside>
  );
}
