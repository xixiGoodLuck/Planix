import { AIWorkspace } from '../components/AIWorkspace';
import type { Language } from '../types';

interface SettingsPageProps {
  language: Language;
  onLanguageChange: (language: Language) => void;
  t: (key: string) => string;
}

export function SettingsPage({ language, onLanguageChange, t }: SettingsPageProps) {
  return <section className="page-stack"><AIWorkspace language={language} onLanguageChange={onLanguageChange} t={t} /></section>;
}
