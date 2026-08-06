import { Brain, CalendarDays } from 'lucide-react';
import type { Lang } from '../types';

interface HeaderProps {
  lang: Lang;
  onLangChange: (lang: Lang) => void;
  onToday: () => void;
  t: (key: string) => string;
}

export function Header({ lang, onLangChange, onToday, t }: HeaderProps) {
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark"><Brain size={20} /></span>
        <div>
          <h1>{t('common.appName')}</h1>
          <p>{t('legacy.subtitle')}</p>
        </div>
      </div>
      <div className="topbar-actions">
        <button className="ghost-button" onClick={onToday}>
          <CalendarDays size={16} />
          {t('common.today')}
        </button>
        <div className="segmented" aria-label={t('shell.language')}>
          <button className={lang === 'zh-CN' ? 'active' : ''} onClick={() => onLangChange('zh-CN')}>{t('shell.languageZh')}</button>
          <button className={lang === 'en-US' ? 'active' : ''} onClick={() => onLangChange('en-US')}>{t('shell.languageEn')}</button>
        </div>
      </div>
    </header>
  );
}
