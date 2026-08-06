import { useMemo } from 'react';
import type { Language } from '../types';
import { enUS } from './en-US';
import { zhCN } from './zh-CN';

export type { Language } from '../types';

export type I18nNamespace = {
  common: Record<string, string>;
  shell: Record<string, string>;
  command: Record<string, string>;
  dashboard: Record<string, string>;
  agent: Record<string, string>;
  agentFlow: Record<string, string>;
  inspector: Record<string, string>;
  legacy: Record<string, string>;
};

const LANGUAGE_KEY = 'planix_lang';
const DEFAULT_LANGUAGE: Language = 'zh-CN';
const dictionaries: Record<Language, I18nNamespace> = {
  'zh-CN': zhCN,
  'en-US': enUS
};

function getByPath(dictionary: I18nNamespace, key: string): string | undefined {
  const [namespace, item] = key.split('.');
  if (!namespace || !item) return undefined;
  return dictionary[namespace as keyof I18nNamespace]?.[item];
}

export function normalizeLanguage(value?: string | null): Language {
  if (value === 'en' || value === 'en-US') return 'en-US';
  if (value === 'zh' || value === 'zh-CN') return 'zh-CN';
  return DEFAULT_LANGUAGE;
}

export function loadLanguage(): Language {
  try {
    return normalizeLanguage(localStorage.getItem(LANGUAGE_KEY));
  } catch {
    return DEFAULT_LANGUAGE;
  }
}

export function saveLanguage(language: Language): void {
  localStorage.setItem(LANGUAGE_KEY, language);
}

export function useI18n(language: Language) {
  return useMemo(() => (key: string): string => {
    const dictionary = dictionaries[language];
    const fallback = dictionaries[DEFAULT_LANGUAGE];
    return (
      getByPath(dictionary, key) ??
      getByPath(fallback, key) ??
      dictionary.legacy[key] ??
      fallback.legacy[key] ??
      dictionary.common[key] ??
      fallback.common[key] ??
      key
    );
  }, [language]);
}

export function weekdayLabels(language: Language): string[] {
  return language === 'zh-CN'
    ? ['一', '二', '三', '四', '五', '六', '日']
    : ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
}
