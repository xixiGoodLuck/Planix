import type { AppData, DayRecord, Language } from '../types';

const DATA_KEY = 'planix_data_v2';
const LEGACY_DATA_KEY = 'planix_data';
const LANG_KEY = 'planix_lang';
const PREFERENCE_KEY = 'planix_preferences';
const ADVANCED_AGENT_TRACE_KEY = 'planix_advanced_agent_trace';

export function loadData(): AppData {
  try {
    const raw = localStorage.getItem(DATA_KEY) || localStorage.getItem(LEGACY_DATA_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, { plans?: Array<Record<string, unknown>> }>;
    return Object.fromEntries(
      Object.entries(parsed ?? {}).map(([date, day]) => [
        date,
        {
          plans: (day.plans ?? []).map((plan) => ({
            id: String(plan.id ?? crypto.randomUUID()),
            time: String(plan.time ?? '09:00'),
            title: String(plan.title ?? plan.text ?? ''),
            done: Boolean(plan.done),
            completion: String(plan.completion ?? ''),
            source: (plan.source === 'ai' ? 'ai' : 'manual') as 'ai' | 'manual'
          }))
        }
      ])
    );
  } catch {
    return {};
  }
}

export function saveData(data: AppData): void {
  localStorage.setItem(DATA_KEY, JSON.stringify(data));
}

export function ensureDay(data: AppData, date: string): DayRecord {
  return data[date] ?? { plans: [] };
}

export function loadMonthNote(key: string): string {
  return localStorage.getItem(`note_${key}`) ?? '';
}

export function saveMonthNote(key: string, value: string): void {
  localStorage.setItem(`note_${key}`, value);
}

export function loadLang(): Language {
  const value = localStorage.getItem(LANG_KEY);
  if (value === 'en' || value === 'en-US') return 'en-US';
  return 'zh-CN';
}

export function saveLang(lang: Language): void {
  localStorage.setItem(LANG_KEY, lang);
}

export function loadPreferences(): string {
  return localStorage.getItem(PREFERENCE_KEY) ?? '';
}

export function savePreferences(value: string): void {
  localStorage.setItem(PREFERENCE_KEY, value);
}

export function loadAdvancedAgentTrace(): boolean {
  try {
    return typeof localStorage !== 'undefined' && localStorage.getItem(ADVANCED_AGENT_TRACE_KEY) === 'true';
  } catch {
    return false;
  }
}

export function saveAdvancedAgentTrace(value: boolean): void {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(ADVANCED_AGENT_TRACE_KEY, String(value));
    }
  } catch {
    // UI preferences should never prevent P Mode from loading.
  }
}
