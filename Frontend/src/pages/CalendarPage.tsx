import { CalendarPanel } from '../components/CalendarPanel';
import { PlanList } from '../components/PlanList';
import type { AppData, Language, Plan } from '../types';

interface CalendarPageProps {
  lang: Language; data: AppData; selectedDate: string; viewDate: Date; monthNote: string; selectedPlans: Plan[]; draft: string; time: string;
  onViewDateChange: (date: Date) => void; onSelectDate: (date: string) => void; onMonthNoteChange: (value: string) => void;
  onClearSelectedDayPlans: (date: string) => Promise<{ deleted: number; failed: number }>;
  onClearAllPlans: () => Promise<{ deleted: number; failed: number }>;
  onDraftChange: (value: string) => void; onTimeChange: (value: string) => void; onAdd: () => void;
  onToggle: (id: string) => void; onDelete: (id: string) => void; onCompletionChange: (id: string, value: string) => void;
  t: (key: string) => string;
}

export function CalendarPage(props: CalendarPageProps) {
  return <section className="page-stack calendar-page">
    <CalendarPanel {...props} />
    <PlanList date={props.selectedDate} lang={props.lang} plans={props.selectedPlans} draft={props.draft} time={props.time}
      onDraftChange={props.onDraftChange} onTimeChange={props.onTimeChange} onAdd={props.onAdd} onToggle={props.onToggle}
      onDelete={props.onDelete} onCompletionChange={props.onCompletionChange} t={props.t} />
  </section>;
}
