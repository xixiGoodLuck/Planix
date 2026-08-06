interface CalendarPlanResult {
  id?: string;
  date?: string;
  time?: string;
  title?: string;
  done?: boolean;
  priority?: string;
  estimatedMinutes?: number;
  source?: string;
}

interface MaterialResult {
  title?: string;
  chunk?: string;
  score?: number;
}

interface GoalHistoryResult {
  title?: string;
  goal?: string;
  summary?: string;
  createdAt?: string;
}

interface MonthNoteResult {
  year?: number;
  month?: number;
  content?: string;
}

interface PlanSearchResultsCardProps {
  summary?: string;
  calendarPlans?: unknown;
  materials?: unknown;
  goalHistory?: unknown;
  monthNotes?: unknown;
  onSend?: (value: string) => void;
  t: (key: string) => string;
}

function listOf<T>(value: unknown): T[] {
  return Array.isArray(value) ? value.filter((item): item is T => Boolean(item && typeof item === 'object')) : [];
}

function formatIndexMessage(template: string, index: number): string {
  return template.replace('{index}', String(index + 1));
}

export function PlanSearchResultsCard({
  summary,
  calendarPlans,
  materials,
  goalHistory,
  monthNotes,
  onSend,
  t
}: PlanSearchResultsCardProps) {
  const plans = listOf<CalendarPlanResult>(calendarPlans);
  const materialItems = listOf<MaterialResult>(materials);
  const historyItems = listOf<GoalHistoryResult>(goalHistory);
  const noteItems = listOf<MonthNoteResult>(monthNotes);
  const total = plans.length + materialItems.length + historyItems.length + noteItems.length;

  return (
    <div className="command-inline-card wide plan-search-results">
      <div className="command-card-heading">
        <strong>{t('command.planSearchResults')}</strong>
        <span>{total}</span>
      </div>
      {summary ? <p>{summary}</p> : null}

      {plans.length ? (
        <section className="command-result-section">
          <strong>{t('command.calendarPlans')}</strong>
          <ul className="command-plan-list">
            {plans.map((plan, index) => (
              <li key={plan.id || `${plan.title}-${index}`}>
                <div className="command-result-title">
                  <span>{index + 1}. {plan.title || t('command.untitledPlan')}</span>
                  <em>{plan.done ? t('common.done') : t('common.pending')}</em>
                </div>
                <dl className="command-result-meta">
                  <div>
                    <dt>{t('command.resultDate')}</dt>
                    <dd>{plan.date || t('command.noDate')}</dd>
                  </div>
                  <div>
                    <dt>{t('command.resultTime')}</dt>
                    <dd>{plan.time || '09:00'}</dd>
                  </div>
                  <div>
                    <dt>{t('command.resultDuration')}</dt>
                    <dd>{plan.estimatedMinutes || 30} {t('command.minutes')}</dd>
                  </div>
                </dl>
                <div className="command-row-actions" aria-label={t('command.resultActions')}>
                  <button type="button" disabled={!onSend} onClick={() => onSend?.(formatIndexMessage(t('command.actionRefinePlanMessage'), index))}>{t('command.actionRefine')}</button>
                  <button type="button" disabled={!onSend} onClick={() => onSend?.(formatIndexMessage(t('command.actionModifyPlanMessage'), index))}>{t('command.actionModify')}</button>
                  <button type="button" disabled={!onSend} onClick={() => onSend?.(formatIndexMessage(t('command.actionDeletePlanMessage'), index))}>{t('command.actionDelete')}</button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {historyItems.length ? (
        <section className="command-result-section">
          <strong>{t('command.goalHistory')}</strong>
          <ul className="command-compact-list">
            {historyItems.map((item, index) => (
              <li key={`${item.title}-${index}`}>
                <span>{item.title || item.goal || t('command.untitledPlan')}</span>
                {item.summary ? <small>{item.summary}</small> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {materialItems.length ? (
        <section className="command-result-section">
          <strong>{t('command.materialResults')}</strong>
          <ul className="command-compact-list">
            {materialItems.map((item, index) => (
              <li key={`${item.title}-${index}`}>
                <span>{item.title || t('command.material')}</span>
                {item.chunk ? <small>{item.chunk}</small> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {noteItems.length ? (
        <section className="command-result-section">
          <strong>{t('command.monthNotes')}</strong>
          <ul className="command-compact-list">
            {noteItems.map((item, index) => (
              <li key={`${item.year}-${item.month}-${index}`}>
                <span>{item.year}-{String(item.month || 1).padStart(2, '0')}</span>
                {item.content ? <small>{item.content}</small> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
