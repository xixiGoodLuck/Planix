import { useState } from 'react';
import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp, NotebookPen, Trash2 } from 'lucide-react';
import type { AppData, Lang } from '../types';
import { getMonthDays, todayISO } from '../utils/date';
import { weekdayLabels } from '../i18n';

interface CalendarPanelProps {
  lang: Lang;
  data: AppData;
  selectedDate: string;
  viewDate: Date;
  monthNote: string;
  onViewDateChange: (date: Date) => void;
  onSelectDate: (date: string) => void;
  onMonthNoteChange: (value: string) => void;
  onClearSelectedDayPlans: (date: string) => Promise<{ deleted: number; failed: number }>;
  onClearAllPlans: () => Promise<{ deleted: number; failed: number }>;
  t: (key: string) => string;
}

export function CalendarPanel(props: CalendarPanelProps) {
  const {
    lang,
    data,
    selectedDate,
    viewDate,
    monthNote,
    onViewDateChange,
    onSelectDate,
    onMonthNoteChange,
    onClearSelectedDayPlans,
    onClearAllPlans,
    t
  } = props;
  const [isCalendarOpen, setIsCalendarOpen] = useState(true);
  const [clearingSelected, setClearingSelected] = useState(false);
  const [clearingAll, setClearingAll] = useState(false);
  const [clearStatus, setClearStatus] = useState('');
  const today = todayISO();
  const monthTitle = new Intl.DateTimeFormat(lang, {
    year: 'numeric',
    month: 'long'
  }).format(viewDate);

  function shiftMonth(delta: number) {
    onViewDateChange(new Date(viewDate.getFullYear(), viewDate.getMonth() + delta, 1));
  }

  function statusClass(iso: string) {
    const plans = data[iso]?.plans ?? [];
    if (!plans.length) return '';
    return plans.every((plan) => plan.done) ? 'done-all' : 'has-plan';
  }

  async function clearSelectedDayPlans() {
    const plans = data[selectedDate]?.plans ?? [];
    if (!plans.length || clearingSelected) return;
    const confirmed = window.confirm(t('calendar.confirmClearDayPlans'));
    if (!confirmed) return;
    setClearingSelected(true);
    setClearStatus('');
    try {
      const result = await onClearSelectedDayPlans(selectedDate);
      setClearStatus(`${t('calendar.clearDayPlansDone')}: ${t('calendar.deletedCount')} ${result.deleted}, ${t('calendar.failedCount')} ${result.failed}`);
    } catch {
      setClearStatus(t('calendar.clearDayPlansFailed'));
    } finally {
      setClearingSelected(false);
    }
  }

  async function clearAllCalendarPlans() {
    if (clearingAll) return;
    const confirmed = window.confirm(t('calendar.confirmClearAllPlans'));
    if (!confirmed) return;
    setClearingAll(true);
    setClearStatus('');
    try {
      const result = await onClearAllPlans();
      const failedPart = result.failed > 0 ? `, ${t('calendar.failedCount')} ${result.failed}` : '';
      setClearStatus(`${t('calendar.clearAllPlansDone')}: ${t('calendar.deletedCount')} ${result.deleted}${failedPart}`);
    } catch {
      setClearStatus(t('calendar.clearAllPlansFailed'));
    } finally {
      setClearingAll(false);
    }
  }

  return (
    <section className={`surface calendar-panel ${isCalendarOpen ? 'is-open' : 'is-collapsed'}`}>
      <button
        className="calendar-collapse-toggle"
        onClick={() => setIsCalendarOpen((current) => !current)}
        aria-label={isCalendarOpen ? t('calendar.collapseCalendar') : t('calendar.expandCalendar')}
        title={isCalendarOpen ? t('calendar.collapseCalendar') : t('calendar.expandCalendar')}
      >
        {isCalendarOpen ? <ChevronDown size={22} /> : <ChevronUp size={22} />}
      </button>

      <div className="calendar-collapsible" aria-hidden={!isCalendarOpen}>
        <div className="section-head">
          <div>
            <span className="eyebrow">{t('calendar.calendar')}</span>
            <h2>{monthTitle}</h2>
          </div>
          <div className="icon-row">
            <button className="icon-button" onClick={() => shiftMonth(-1)} aria-label={t('calendar.previousMonth')}>
              <ChevronLeft size={18} />
            </button>
            <button className="icon-button" onClick={() => shiftMonth(1)} aria-label={t('calendar.nextMonth')}>
              <ChevronRight size={18} />
            </button>
          </div>
        </div>
        <div className="note-action-row calendar-maintenance-row">
          <button
            type="button"
            className="section-action-button"
            onClick={clearSelectedDayPlans}
            disabled={!(data[selectedDate]?.plans ?? []).length || clearingSelected}
          >
            <Trash2 size={15} />
            {clearingSelected ? t('calendar.clearingDayPlans') : t('calendar.clearSelectedDayPlans')}
          </button>
          <button
            type="button"
            className="section-action-button danger"
            onClick={clearAllCalendarPlans}
            disabled={clearingAll}
          >
            <Trash2 size={15} />
            {clearingAll ? t('calendar.clearingAllPlans') : t('calendar.clearAllPlans')}
          </button>
        </div>
        {clearStatus && <p className="inline-status calendar-clear-status">{clearStatus}</p>}
        <div className="weekday-grid">
          {weekdayLabels(lang).map((day) => <span key={day}>{day}</span>)}
        </div>
        <div className="date-grid">
          {getMonthDays(viewDate).map((item) => (
            <button
              key={item.iso}
              className={[
                'date-cell',
                item.muted ? 'muted' : '',
                selectedDate === item.iso ? 'selected' : '',
                today === item.iso ? 'today' : '',
                statusClass(item.iso)
              ].join(' ')}
              onClick={() => onSelectDate(item.iso)}
            >
              <span>{item.day}</span>
            </button>
          ))}
        </div>
        <label className="note-box">
          <span><NotebookPen size={16} />{t('calendar.monthNote')} · {monthTitle}</span>
          <textarea value={monthNote} onChange={(event) => onMonthNoteChange(event.target.value)} placeholder={t('calendar.monthNotePlaceholder')} aria-label={t('calendar.monthNote')} />
        </label>
      </div>
    </section>
  );
}
