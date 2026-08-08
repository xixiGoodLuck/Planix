import { useEffect, useMemo, useState } from 'react';
import { CalendarPage } from './pages/CalendarPage';
import { CommandPage } from './pages/CommandPage';
import { SettingsPage } from './pages/SettingsPage';
import { RivaShell } from './shell/RivaShell';
import { useAppRoute } from './shell/useAppRoute';
import {
  clearAllPlans as clearAllRemotePlans,
  createPlan as createRemotePlan,
  deletePlan as deleteRemotePlan,
  fetchAiSettings,
  fetchMonthNote,
  fetchMonthPlans,
  fetchPlans,
  saveRemoteMonthNote,
  updatePlan as updateRemotePlan
} from './lib/api';
import { loadLanguage, saveLanguage, useI18n } from './i18n';
import { ensureDay, loadData, loadMonthNote, saveData, saveMonthNote } from './lib/storage';
import type { AiSettings, AppData, AppRoute, InspectorSnapshot, Language, Plan } from './types';
import { monthKey, todayISO } from './utils/date';

function createId(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
}

function getYearMonth(date: Date): { year: number; month: number } {
  return { year: date.getFullYear(), month: date.getMonth() + 1 };
}

function monthDateKeys(year: number, month: number): string[] {
  const prefix = `${year}-${String(month).padStart(2, '0')}`;
  return Array.from({ length: new Date(year, month, 0).getDate() }, (_, index) => `${prefix}-${String(index + 1).padStart(2, '0')}`);
}

export function App() {
  const { route, setRoute } = useAppRoute();
  const [language, setLanguage] = useState<Language>(() => loadLanguage());
  const [data, setData] = useState<AppData>(() => loadData());
  const [selectedDate, setSelectedDate] = useState(todayISO);
  const [viewDate, setViewDate] = useState(new Date());
  const [monthNote, setMonthNote] = useState(() => loadMonthNote(monthKey(new Date())));
  const [draft, setDraft] = useState('');
  const [time, setTime] = useState(() => `${String(new Date().getHours()).padStart(2, '0')}:00`);
  const [aiSettings, setAiSettings] = useState<AiSettings | null>(null);
  const [pOnlyMode, setPOnlyMode] = useState(false);
  const t = useI18n(language);
  const selectedPlans = useMemo(() => ensureDay(data, selectedDate).plans, [data, selectedDate]);

  useEffect(() => saveData(data), [data]);
  useEffect(() => {
    saveLanguage(language);
    document.documentElement.lang = language;
    document.title = t('common.appName');
  }, [language, t]);
  useEffect(() => { void fetchAiSettings().then(setAiSettings).catch(() => setAiSettings(null)); }, []);
  useEffect(() => {
    const { year, month } = getYearMonth(viewDate);
    const key = monthKey(viewDate);
    setMonthNote(loadMonthNote(key));
    void fetchMonthNote(year, month).then((value) => {
      setMonthNote(value);
      saveMonthNote(key, value);
    }).catch(() => undefined);
  }, [viewDate]);
  useEffect(() => {
    if (route !== 'calendar') return;
    const { year, month } = getYearMonth(viewDate);
    void fetchMonthPlans(year, month).then((plans) => {
      const grouped = plans.reduce<Record<string, Plan[]>>((acc, plan) => {
        const { date, ...rest } = plan;
        acc[date] = [...(acc[date] ?? []), rest];
        return acc;
      }, {});
      setData((current) => {
        const next = { ...current };
        for (const date of monthDateKeys(year, month)) next[date] = { plans: grouped[date] ?? [] };
        return next;
      });
    }).catch(() => undefined);
  }, [route, viewDate]);
  useEffect(() => {
    void fetchPlans(selectedDate).then((plans) => setData((current) => ({ ...current, [selectedDate]: { plans } }))).catch(() => undefined);
  }, [selectedDate]);

  function updateDay(date: string, updater: (plans: Plan[]) => Plan[]) {
    setData((current) => ({ ...current, [date]: { plans: updater(ensureDay(current, date).plans) } }));
  }

  function addPlan() {
    const title = draft.trim();
    if (!title) return;
    const plan: Plan = { id: createId(), time, title, done: false, completion: '', source: 'manual' };
    updateDay(selectedDate, (plans) => [...plans, plan]);
    void createRemotePlan(selectedDate, plan).then((saved) => updateDay(selectedDate, (plans) => plans.map((item) => item.id === plan.id ? saved : item))).catch(() => undefined);
    setDraft('');
  }

  async function clearDay(date: string) {
    const plans = [...ensureDay(data, date).plans];
    const results = await Promise.allSettled(plans.map((plan) => deleteRemotePlan(plan.id)));
    const deletedIds = new Set(plans.filter((_, index) => results[index].status === 'fulfilled').map((plan) => plan.id));
    updateDay(date, (current) => current.filter((plan) => !deletedIds.has(plan.id)));
    return { deleted: deletedIds.size, failed: plans.length - deletedIds.size };
  }

  async function clearAll() {
    const result = await clearAllRemotePlans();
    setData((current) => Object.fromEntries(Object.entries(current).map(([date, day]) => [date, { ...day, plans: [] }])));
    return { deleted: result.deleted, failed: 0 };
  }

  function changeRoute(next: AppRoute) {
    setRoute(next);
    if (next !== 'command') setPOnlyMode(false);
  }

  const inspector = useMemo<InspectorSnapshot>(() => ({
    route,
    agentStatus: 'idle',
    logs: [{ id: 'boot', level: 'info', message: t('inspector.bootLog'), timestamp: Date.now() }],
    planning: { planCount: selectedPlans.length },
    api: { mode: aiSettings ? 'backend' : 'unknown', hasApiKey: aiSettings?.hasApiKey ?? false, provider: aiSettings?.provider ?? 'unknown' }
  }), [aiSettings, route, selectedPlans.length, t]);

  return (
    <RivaShell
      route={route}
      language={language}
      inspector={inspector}
      onRouteChange={changeRoute}
      onLanguageChange={setLanguage}
      onToday={() => { setSelectedDate(todayISO()); setViewDate(new Date()); changeRoute('calendar'); }}
      pOnlyMode={pOnlyMode}
      onCommandToggle={() => route === 'command' ? setPOnlyMode((value) => !value) : (setRoute('command'), setPOnlyMode(true))}
      t={t}
    >
      {route === 'calendar' && <CalendarPage
        lang={language} data={data} selectedDate={selectedDate} viewDate={viewDate} monthNote={monthNote}
        selectedPlans={selectedPlans} draft={draft} time={time} onViewDateChange={setViewDate} onSelectDate={setSelectedDate}
        onMonthNoteChange={(value) => { const { year, month } = getYearMonth(viewDate); setMonthNote(value); saveMonthNote(monthKey(viewDate), value); void saveRemoteMonthNote(year, month, value); }}
        onClearSelectedDayPlans={clearDay} onClearAllPlans={clearAll} onDraftChange={setDraft} onTimeChange={setTime} onAdd={addPlan}
        onToggle={(id) => { const plan = selectedPlans.find((item) => item.id === id); if (!plan) return; const done = !plan.done; updateDay(selectedDate, (items) => items.map((item) => item.id === id ? { ...item, done } : item)); void updateRemotePlan(id, { done }); }}
        onDelete={(id) => { updateDay(selectedDate, (plans) => plans.filter((plan) => plan.id !== id)); void deleteRemotePlan(id); }}
        onCompletionChange={(id, value) => { updateDay(selectedDate, (plans) => plans.map((plan) => plan.id === id ? { ...plan, completion: value } : plan)); void updateRemotePlan(id, { completion: value }); }} t={t}
      />}
      {route === 'settings' && <SettingsPage onSettingsChange={setAiSettings} t={t} />}
      {route === 'command' && <CommandPage t={t} />}
    </RivaShell>
  );
}
