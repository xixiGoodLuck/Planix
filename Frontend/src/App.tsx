import { useEffect, useState } from 'react';
import { LearningWorkspace } from './features/learning/pages/LearningWorkspace';
import { loadLanguage, saveLanguage, useI18n } from './i18n';
import { SettingsPage } from './pages/SettingsPage';
import { RivaShell } from './shell/RivaShell';
import { useAppRoute } from './shell/useAppRoute';
import type { Language } from './types';

export function App() {
  const { route, setRoute } = useAppRoute();
  const [language, setLanguage] = useState<Language>(() => loadLanguage());
  const t = useI18n(language);

  useEffect(() => {
    saveLanguage(language);
    document.documentElement.lang = language;
    document.title = t('common.appName');
  }, [language, t]);

  return (
    <RivaShell
      route={route}
      onRouteChange={setRoute}
      t={t}
    >
      {route === 'learning'
        ? <LearningWorkspace language={language} t={t} />
        : <SettingsPage language={language} onLanguageChange={setLanguage} t={t} />}
    </RivaShell>
  );
}
