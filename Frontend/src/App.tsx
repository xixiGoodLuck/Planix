import { useEffect, useMemo, useState } from 'react';
import { CalendarPage } from './pages/CalendarPage';
import { CommandPage } from './pages/CommandPage';
import { DashboardPage } from './pages/DashboardPage';
import { GoalsPage } from './pages/GoalsPage';
import { NotesPage } from './pages/NotesPage';
import { SettingsPage } from './pages/SettingsPage';
import { RivaShell } from './shell/RivaShell';
import { useAppRoute } from './shell/useAppRoute';
import {
  ApiHttpError,
  clearAllPlans as clearAllRemotePlans,
  createPlan as createRemotePlan,
  deletePlan as deleteRemotePlan,
  deletePlanRefinedTask,
  fetchAiSettings,
  fetchMonthNote,
  fetchMonthPlans,
  fetchPlans,
  saveRemoteMonthNote,
  savePlanRefinedTask,
  updatePlan as updateRemotePlan
} from './lib/api';
import { loadLanguage, saveLanguage, useI18n } from './i18n';
import { ensureDay, loadData, loadMonthNote, loadPreferences, saveData, saveMonthNote, savePreferences } from './lib/storage';
import type {
  AiSettings,
  AppData,
  AppRoute,
  AppliedPlan,
  CalendarWriteSummary,
  GoalPlanResponse,
  GoalPlanTask,
  InspectorLog,
  InspectorSnapshot,
  Language,
  Plan,
  RefinedTask,
  RuntimePlanProposal,
  StructuredGoalPlan
} from './types';
import { monthKey, todayISO } from './utils/date';

function createId(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
}

function getYearMonth(date: Date): { year: number; month: number } {
  return { year: date.getFullYear(), month: date.getMonth() + 1 };
}

function monthDateKeys(year: number, month: number): string[] {
  const days = new Date(year, month, 0).getDate();
  const prefix = `${year}-${String(month).padStart(2, '0')}`;
  return Array.from({ length: days }, (_, index) => `${prefix}-${String(index + 1).padStart(2, '0')}`);
}

function normalizeGoalTaskDate(dueDate: string | null | undefined, fallbackDate: string): string {
  if (typeof dueDate !== 'string') return fallbackDate;
  const trimmed = dueDate.trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) return trimmed;
  const prefix = trimmed.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(prefix) ? prefix : fallbackDate;
}

function stableKeyPart(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9\u4e00-\u9fff-]/gi, '').slice(0, 48);
}

function stableHash(value: string): string {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = ((hash << 5) - hash + value.charCodeAt(index)) | 0;
  }
  return Math.abs(hash).toString(36);
}

function buildGoalTaskSourceKey(
  goalPlan: GoalPlanResponse,
  milestoneIndex: number,
  taskIndex: number,
  task: GoalPlanTask
): string {
  const base = goalPlan.id
    ? `goal-plan:${goalPlan.id}`
    : `goal-plan:${stableKeyPart(goalPlan.structuredPlan?.goalTitle || goalPlan.summary)}:${stableKeyPart(task.title)}:${task.dueDate || 'no-date'}`;
  return `${base}:m${milestoneIndex}:t${taskIndex}`;
}

function buildRuntimeTaskSourceKey(
  proposal: RuntimePlanProposal,
  milestoneIndex: number,
  taskIndex: number,
  task: GoalPlanTask
): string {
  if (proposal.runtimeRunId) {
    return `runtime-proposal:${proposal.runtimeRunId}:m${milestoneIndex}:t${taskIndex}`;
  }
  const raw = [
    proposal.goal,
    milestoneIndex,
    taskIndex,
    task.title,
    task.dueDate || 'no-date'
  ].join('|');
  return `runtime-proposal:${stableHash(raw)}`;
}

function defaultGoalTaskTime(index: number): string {
  const times = ['09:00', '14:30', '20:30'];
  return times[index % times.length];
}

export function App() {
  const { route, setRoute } = useAppRoute();
  const [language, setLanguage] = useState<Language>(() => loadLanguage());
  const [data, setData] = useState<AppData>(() => loadData());
  const [selectedDate, setSelectedDate] = useState(() => todayISO());
  const [viewDate, setViewDate] = useState(() => new Date());
  const [monthNote, setMonthNote] = useState(() => loadMonthNote(monthKey(new Date())));
  const [draft, setDraft] = useState('');
  const [time, setTime] = useState(() => `${String(new Date().getHours()).padStart(2, '0')}:00`);
  const [preferences, setPreferences] = useState(() => loadPreferences());
  const [agentStatus, setAgentStatus] = useState<InspectorSnapshot['agentStatus']>('idle');
  const [inspectorLogs, setInspectorLogs] = useState<InspectorLog[]>([]);
  const [aiSettings, setAiSettings] = useState<AiSettings | null>(null);
  const [pOnlyMode, setPOnlyMode] = useState(false);
  const t = useI18n(language);

  const selectedPlans = useMemo(() => ensureDay(data, selectedDate).plans, [data, selectedDate]);

  useEffect(() => {
    saveData(data);
  }, [data]);

  useEffect(() => {
    saveLanguage(language);
    document.documentElement.lang = language;
    document.title = t('common.appName');
  }, [language, t]);

  useEffect(() => {
    setInspectorLogs([
      {
        id: 'boot',
        level: 'info',
        message: t('inspector.bootLog'),
        timestamp: Date.now()
      }
    ]);
  }, [language, t]);

  useEffect(() => {
    const key = monthKey(viewDate);
    const localNote = loadMonthNote(key);
    const { year, month } = getYearMonth(viewDate);
    let cancelled = false;
    setMonthNote(localNote);
    fetchMonthNote(year, month)
      .then((remoteNote) => {
        if (cancelled) return;
        setMonthNote((current) => {
          if (remoteNote || !current) {
            saveMonthNote(key, remoteNote);
            return remoteNote;
          }
          return current;
        });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [viewDate]);

  useEffect(() => {
    if (route !== 'calendar') return;
    const { year, month } = getYearMonth(viewDate);
    let cancelled = false;
    fetchMonthPlans(year, month)
      .then((remotePlans) => {
        if (cancelled) return;
        const grouped = remotePlans.reduce<Record<string, Plan[]>>((acc, plan) => {
          const { date, ...rest } = plan;
          acc[date] = [...(acc[date] ?? []), rest];
          return acc;
        }, {});
        const monthDates = monthDateKeys(year, month);
        setData((current) => {
          let next = current;
          for (const date of monthDates) {
            next = {
              ...next,
              [date]: { plans: grouped[date] ?? [] }
            };
          }
          return next;
        });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [route, viewDate]);

  useEffect(() => {
    let cancelled = false;
    fetchPlans(selectedDate)
      .then((remotePlans) => {
        if (cancelled) return;
        setData((current) => {
          const localPlans = ensureDay(current, selectedDate).plans;
          if (!remotePlans.length && localPlans.length) return current;
          return { ...current, [selectedDate]: { plans: remotePlans } };
        });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [selectedDate]);

  useEffect(() => {
    savePreferences(preferences);
  }, [preferences]);

  useEffect(() => {
    let cancelled = false;
    fetchAiSettings()
      .then((settings) => {
        if (!cancelled) setAiSettings(settings);
      })
      .catch(() => {
        if (!cancelled) setAiSettings(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function addInspectorLog(log: Omit<InspectorLog, 'id' | 'timestamp'>) {
    setInspectorLogs((current) => [
      { ...log, id: createId(), timestamp: Date.now() },
      ...current.filter((item) => item.id !== 'boot')
    ].slice(0, 8));
  }

  function updateDay(date: string, updater: (plans: Plan[]) => Plan[]) {
    setData((current) => {
      const day = ensureDay(current, date);
      return { ...current, [date]: { plans: updater(day.plans) } };
    });
  }

  function replacePlan(date: string, localId: string, remotePlan: Plan) {
    updateDay(date, (plans) => plans.map((plan) => (plan.id === localId ? remotePlan : plan)));
  }

  function upsertPlan(date: string, remotePlan: Plan) {
    updateDay(date, (plans) => {
      const exists = plans.some((plan) => plan.id === remotePlan.id);
      if (exists) return plans.map((plan) => (plan.id === remotePlan.id ? remotePlan : plan));
      return [...plans, remotePlan].sort((left, right) => left.time.localeCompare(right.time));
    });
  }

  function removePlanFromDate(date: string, planId: string) {
    updateDay(date, (plans) => plans.filter((plan) => plan.id !== planId));
  }

  function findPlanById(planId: string): { date: string; plan: Plan } | null {
    for (const [date, day] of Object.entries(data)) {
      const plan = day.plans.find((item) => item.id === planId);
      if (plan) return { date, plan };
    }
    return null;
  }

  function findPlanBySourceKey(sourceKey: string): { date: string; plan: Plan } | null {
    if (!sourceKey) return null;
    for (const [date, day] of Object.entries(data)) {
      const plan = day.plans.find((item) => item.sourceKey === sourceKey);
      if (plan) return { date, plan };
    }
    return null;
  }

  function addPlan() {
    const title = draft.trim();
    if (!title) return;
    const date = selectedDate;
    const plan = { id: createId(), time, title, done: false, completion: '', source: 'manual' as const };
    updateDay(date, (plans) => [...plans, plan]);
    void createRemotePlan(date, plan).then((savedPlan) => replacePlan(date, plan.id, savedPlan)).catch(() => undefined);
    setDraft('');
  }

  async function createOrUpdateGoalPlanTask(input: {
    date: string;
    time: string;
    title: string;
    description?: string;
    estimatedMinutes?: number;
    priority?: Plan['priority'];
    sourceKey: string;
    refinedTask?: RefinedTask | null;
  }): Promise<'created' | 'updated'> {
    const bySourceKey = findPlanBySourceKey(input.sourceKey);
    const byFallback = ensureDay(data, input.date).plans.find((plan) => plan.source === 'ai' && plan.title === input.title);
    const existing = bySourceKey ?? (byFallback ? { date: input.date, plan: byFallback } : null);

    if (existing) {
      let currentPlan = existing.plan;
      const currentDate = existing.date;
      const patch: Parameters<typeof updateRemotePlan>[1] = {};
      if (input.sourceKey && currentPlan.sourceKey !== input.sourceKey) {
        patch.sourceKey = input.sourceKey;
      }
      if (currentPlan.source === 'ai') {
        if (input.description?.trim() && !currentPlan.completion.trim()) {
          patch.completion = input.description;
        }
        if (input.estimatedMinutes && currentPlan.estimatedMinutes !== input.estimatedMinutes) {
          patch.estimatedMinutes = input.estimatedMinutes;
        }
        if (input.priority && currentPlan.priority !== input.priority) {
          patch.priority = input.priority;
        }
      }
      if (Object.keys(patch).length) {
        currentPlan = await updateRemotePlan(currentPlan.id, patch);
        upsertPlan(currentDate, currentPlan);
      }
      if (input.refinedTask) {
        currentPlan = await savePlanRefinedTask(currentPlan.id, input.refinedTask);
        upsertPlan(currentDate, currentPlan);
      }
      return 'updated';
    }

    const localPlan: Plan = {
      id: createId(),
      time: input.time,
      title: input.title,
      done: false,
      completion: input.description ?? '',
      source: 'ai',
      priority: input.priority ?? 'medium',
      estimatedMinutes: input.estimatedMinutes ?? 30,
      sourceKey: input.sourceKey,
      refinedTask: input.refinedTask ?? null
    };
    updateDay(input.date, (plans) => [...plans, localPlan]);
    const saved = await createRemotePlan(input.date, localPlan);
    replacePlan(input.date, localPlan.id, saved);
    return 'created';
  }

  async function applyStructuredPlanToCalendar(input: {
    structuredPlan: StructuredGoalPlan;
    fallbackDate: string;
    buildSourceKey: (milestoneIndex: number, taskIndex: number, task: GoalPlanTask) => string;
  }): Promise<CalendarWriteSummary> {
    const { structuredPlan, fallbackDate, buildSourceKey } = input;
    let created = 0;
    let updated = 0;
    let failed = 0;
    let sequence = 0;
    const targetDates = new Set<string>();
    const errors: string[] = [];
    for (const [milestoneIndex, milestone] of structuredPlan.milestones.entries()) {
      for (const [taskIndex, task] of milestone.tasks.entries()) {
        const targetDate = normalizeGoalTaskDate(task.dueDate, fallbackDate);
        targetDates.add(targetDate);
        const sourceKey = buildSourceKey(milestoneIndex, taskIndex, task);
        try {
          const result = await createOrUpdateGoalPlanTask({
            date: targetDate,
            time: defaultGoalTaskTime(sequence),
            title: task.title,
            description: task.description,
            estimatedMinutes: task.estimatedMinutes,
            priority: task.priority,
            sourceKey
          });
          if (result === 'created') created += 1;
          else updated += 1;
        } catch (err) {
          failed += 1;
          errors.push(err instanceof Error ? err.message : String(err));
        }
        sequence += 1;
      }
    }
    return { created, updated, failed, affectedDates: Array.from(targetDates), errors };
  }

  async function applyGoalPlanToCalendar(goalPlan: GoalPlanResponse): Promise<{ created: number; updated: number; failed: number; otherDates: boolean }> {
    const structured = goalPlan.structuredPlan;
    if (!structured) return { created: 0, updated: 0, failed: 0, otherDates: false };
    const result = await applyStructuredPlanToCalendar({
      structuredPlan: structured,
      fallbackDate: selectedDate,
      buildSourceKey: (milestoneIndex, taskIndex, task) => buildGoalTaskSourceKey(goalPlan, milestoneIndex, taskIndex, task)
    });
    return {
      created: result.created,
      updated: result.updated,
      failed: result.failed,
      otherDates: result.affectedDates.some((date) => date !== selectedDate)
    };
  }

  async function applyRuntimeProposalToCalendar(proposal: RuntimePlanProposal): Promise<CalendarWriteSummary> {
    return applyStructuredPlanToCalendar({
      structuredPlan: proposal.structuredPlan,
      fallbackDate: selectedDate || todayISO(),
      buildSourceKey: (milestoneIndex, taskIndex, task) => buildRuntimeTaskSourceKey(proposal, milestoneIndex, taskIndex, task)
    });
  }

  function applyRemotePlans(plans: AppliedPlan[]) {
    const grouped = plans.reduce<Record<string, Plan[]>>((acc, plan) => {
      const { date, ...rest } = plan;
      acc[date] = [...(acc[date] ?? []), rest];
      return acc;
    }, {});
    setData((current) => {
      let next = current;
      for (const [date, datePlans] of Object.entries(grouped)) {
        const existing = ensureDay(next, date).plans;
        const existingIds = new Set(existing.map((plan) => plan.id));
        next = {
          ...next,
          [date]: {
            plans: [...existing, ...datePlans.filter((plan) => !existingIds.has(plan.id))]
          }
        };
      }
      return next;
    });
  }

  async function saveRefinedPlan(planId: string, refinedTask: RefinedTask, planDate = selectedDate): Promise<Plan> {
    const saved = await savePlanRefinedTask(planId, refinedTask);
    const actual = findPlanById(planId);
    upsertPlan(actual?.date ?? planDate, saved);
    return saved;
  }

  async function deleteRefinedPlan(planId: string, planDate = selectedDate): Promise<Plan> {
    const saved = await deletePlanRefinedTask(planId);
    const actual = findPlanById(planId);
    upsertPlan(actual?.date ?? planDate, saved);
    return saved;
  }

  async function createOrUpdateRefinedPlan(input: {
    date: string;
    title: string;
    sourceKey: string;
    refinedTask: RefinedTask;
  }): Promise<Plan> {
    const targetDate = normalizeGoalTaskDate(input.date, selectedDate);
    const bySourceKey = findPlanBySourceKey(input.sourceKey);
    const byFallback = ensureDay(data, targetDate).plans.find((plan) => plan.source === 'ai' && plan.title === input.title);
    const existing = bySourceKey ?? (byFallback ? { date: targetDate, plan: byFallback } : null);
    if (existing) {
      if (input.sourceKey && existing.plan.sourceKey !== input.sourceKey) {
        const withSourceKey = await updateRemotePlan(existing.plan.id, { sourceKey: input.sourceKey });
        upsertPlan(existing.date, withSourceKey);
      }
      const saved = await savePlanRefinedTask(existing.plan.id, input.refinedTask);
      upsertPlan(existing.date, saved);
      return saved;
    }
    const localPlan: Plan = {
      id: createId(),
      time: '09:00',
      title: input.title,
      done: false,
      completion: '',
      source: 'ai',
      sourceKey: input.sourceKey,
      refinedTask: input.refinedTask
    };
    updateDay(targetDate, (plans) => [...plans, localPlan]);
    const saved = await createRemotePlan(targetDate, localPlan);
    replacePlan(targetDate, localPlan.id, saved);
    return saved;
  }

  async function clearDayPlans(date: string): Promise<{ deleted: number; failed: number }> {
    const plans = [...ensureDay(data, date).plans];
    let deleted = 0;
    let failed = 0;
    for (const plan of plans) {
      try {
        await deleteRemotePlan(plan.id);
        removePlanFromDate(date, plan.id);
        deleted += 1;
      } catch {
        failed += 1;
      }
    }
    return { deleted, failed };
  }

  async function clearAllPlans(): Promise<{ deleted: number; failed: number }> {
    try {
      const result = await clearAllRemotePlans();
      setData((current) => {
        const next: AppData = {};
        for (const [date, day] of Object.entries(current)) {
          next[date] = { ...day, plans: [] };
        }
        return next;
      });
      return { deleted: result.deleted, failed: 0 };
    } catch (err) {
      if (!(err instanceof ApiHttpError) || err.status !== 404) {
        throw err;
      }
    }

    const uniquePlans = Array.from(
      new Map(
        Object.entries(data)
          .flatMap(([date, day]) => day.plans.map((plan) => [plan.id, { date, plan }] as const))
      ).values()
    );
    let deleted = 0;
    let failed = 0;
    for (const { date, plan } of uniquePlans) {
      try {
        await deleteRemotePlan(plan.id);
        removePlanFromDate(date, plan.id);
        deleted += 1;
      } catch {
        failed += 1;
      }
    }
    return { deleted, failed };
  }

  function selectToday() {
    const today = todayISO();
    setSelectedDate(today);
    setViewDate(new Date());
  }

  function handleToday() {
    selectToday();
    setRoute('calendar');
    setPOnlyMode(false);
  }

  function handleRouteChange(nextRoute: AppRoute) {
    setRoute(nextRoute);
    if (nextRoute !== 'command') {
      setPOnlyMode(false);
    }
  }

  function handleCommandToggle() {
    if (route !== 'command') {
      setRoute('command');
      setPOnlyMode(true);
      return;
    }
    setPOnlyMode((current) => !current);
  }

  const inspector = useMemo<InspectorSnapshot>(() => ({
    route,
    agentStatus,
    logs: inspectorLogs,
    memory: {
      preferenceSummary: preferences.trim() || t('inspector.emptyMemory'),
      materialCount: 0,
      planCount: selectedPlans.length
    },
    api: {
      mode: aiSettings ? 'backend' : 'unknown',
      hasApiKey: aiSettings?.hasApiKey ?? false,
      provider: aiSettings?.provider ?? 'unknown'
    }
  }), [agentStatus, aiSettings, inspectorLogs, preferences, route, selectedPlans.length, t]);

  const aiPageProps = {
    data,
    date: selectedDate,
    preferences,
    onPreferencesChange: setPreferences,
    onApplyGoalPlanToCalendar: applyGoalPlanToCalendar,
    onReplanApplied: applyRemotePlans,
    onCreateOrUpdateRefinedPlan: createOrUpdateRefinedPlan,
    onDeletePlanRefinedTask: deleteRefinedPlan,
    onSettingsChange: setAiSettings,
    language,
    t
  };

  return (
    <RivaShell
      route={route}
      language={language}
      inspector={inspector}
      onRouteChange={handleRouteChange}
      onLanguageChange={setLanguage}
      onToday={handleToday}
      pOnlyMode={pOnlyMode}
      onCommandToggle={handleCommandToggle}
      t={t}
    >
      {route === 'dashboard' && (
        <DashboardPage
          date={selectedDate}
          plans={selectedPlans}
          preferences={preferences}
          inspector={inspector}
          onAgentStatusChange={setAgentStatus}
          onLog={addInspectorLog}
          onApplyRuntimeProposalToCalendar={applyRuntimeProposalToCalendar}
          onViewCalendarDate={(date) => {
            setSelectedDate(date);
            setViewDate(new Date(`${date}T00:00:00`));
            setRoute('calendar');
          }}
          t={t}
        />
      )}
      {route === 'calendar' && (
        <CalendarPage
          lang={language}
          data={data}
          selectedDate={selectedDate}
          viewDate={viewDate}
          monthNote={monthNote}
          selectedPlans={selectedPlans}
          draft={draft}
          time={time}
          onViewDateChange={setViewDate}
          onSelectDate={setSelectedDate}
          onMonthNoteChange={(value) => {
            const key = monthKey(viewDate);
            const { year, month } = getYearMonth(viewDate);
            setMonthNote(value);
            saveMonthNote(key, value);
            void saveRemoteMonthNote(year, month, value).catch(() => undefined);
          }}
          onClearSelectedDayPlans={clearDayPlans}
          onClearAllPlans={clearAllPlans}
          onDraftChange={setDraft}
          onTimeChange={setTime}
          onAdd={addPlan}
          onToggle={(id) => {
            const plan = selectedPlans.find((item) => item.id === id);
            if (!plan) return;
            const done = !plan.done;
            updateDay(selectedDate, (plans) => plans.map((item) => item.id === id ? { ...item, done } : item));
            void updateRemotePlan(id, { done }).then((savedPlan) => replacePlan(selectedDate, id, savedPlan)).catch(() => undefined);
          }}
          onDelete={(id) => {
            updateDay(selectedDate, (plans) => plans.filter((plan) => plan.id !== id));
            void deleteRemotePlan(id).catch(() => undefined);
          }}
          onCompletionChange={(id, value) => {
            updateDay(selectedDate, (plans) => plans.map((plan) => plan.id === id ? { ...plan, completion: value } : plan));
            void updateRemotePlan(id, { completion: value }).catch(() => undefined);
          }}
          preferences={preferences}
          onSavePlanRefinedTask={saveRefinedPlan}
          onDeletePlanRefinedTask={deleteRefinedPlan}
          t={t}
        />
      )}
      {route === 'notes' && <NotesPage {...aiPageProps} />}
      {route === 'goals' && <GoalsPage {...aiPageProps} />}
      {route === 'settings' && <SettingsPage {...aiPageProps} />}
      {route === 'command' && (
        <CommandPage t={t} />
      )}
    </RivaShell>
  );
}
