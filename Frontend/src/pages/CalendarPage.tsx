import { CalendarPanel } from '../components/CalendarPanel';
import { PlanList } from '../components/PlanList';
import type { AppData, Language, Plan, RefinedTask } from '../types';

interface CalendarPageProps {
  lang: Language;
  data: AppData;
  selectedDate: string;
  viewDate: Date;
  monthNote: string;
  selectedPlans: Plan[];
  draft: string;
  time: string;
  onViewDateChange: (date: Date) => void;
  onSelectDate: (date: string) => void;
  onMonthNoteChange: (value: string) => void;
  onClearSelectedDayPlans: (date: string) => Promise<{ deleted: number; failed: number }>;
  onClearAllPlans: () => Promise<{ deleted: number; failed: number }>;
  onDraftChange: (value: string) => void;
  onTimeChange: (value: string) => void;
  onAdd: () => void;
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
  onCompletionChange: (id: string, value: string) => void;
  preferences: string;
  onSavePlanRefinedTask: (planId: string, refinedTask: RefinedTask) => Promise<Plan>;
  onDeletePlanRefinedTask: (planId: string) => Promise<Plan>;
  t: (key: string) => string;
}

export function CalendarPage(props: CalendarPageProps) {
  const {
    lang,
    data,
    selectedDate,
    viewDate,
    monthNote,
    selectedPlans,
    draft,
    time,
    onViewDateChange,
    onSelectDate,
    onMonthNoteChange,
    onClearSelectedDayPlans,
    onClearAllPlans,
    onDraftChange,
    onTimeChange,
    onAdd,
    onToggle,
    onDelete,
    onCompletionChange,
    preferences,
    onSavePlanRefinedTask,
    onDeletePlanRefinedTask,
    t
  } = props;

  return (
    <section className="page-stack calendar-page">
      <CalendarPanel
        lang={lang}
        data={data}
        selectedDate={selectedDate}
        viewDate={viewDate}
        monthNote={monthNote}
        onViewDateChange={onViewDateChange}
        onSelectDate={onSelectDate}
        onMonthNoteChange={onMonthNoteChange}
        onClearSelectedDayPlans={onClearSelectedDayPlans}
        onClearAllPlans={onClearAllPlans}
        t={t}
      />
      <PlanList
        date={selectedDate}
        lang={lang}
        plans={selectedPlans}
        draft={draft}
        time={time}
        onDraftChange={onDraftChange}
        onTimeChange={onTimeChange}
        onAdd={onAdd}
        onToggle={onToggle}
        onDelete={onDelete}
        onCompletionChange={onCompletionChange}
        preferences={preferences}
        onSavePlanRefinedTask={onSavePlanRefinedTask}
        onDeletePlanRefinedTask={onDeletePlanRefinedTask}
        t={t}
      />
    </section>
  );
}
