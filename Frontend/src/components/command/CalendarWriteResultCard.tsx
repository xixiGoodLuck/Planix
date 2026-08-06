interface WrittenPlan {
  title?: string;
  date?: string;
  time?: string;
  state?: string;
}

interface CalendarWriteResultCardProps {
  created?: number;
  updated?: number;
  failed?: number;
  affectedDates?: unknown;
  errors?: unknown;
  plans?: unknown;
  t: (key: string) => string;
}

function asList<T extends object>(value: unknown): T[] {
  return Array.isArray(value) ? value.filter((item): item is T => Boolean(item && typeof item === 'object')) : [];
}

function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

export function CalendarWriteResultCard(props: CalendarWriteResultCardProps) {
  const { created = 0, updated = 0, failed = 0, affectedDates, errors, plans, t } = props;
  const planItems = asList<WrittenPlan>(plans);
  const dateItems = asStrings(affectedDates);
  const errorItems = asStrings(errors);

  return (
    <div className={`command-inline-card wide calendar-result ${failed > 0 ? 'has-error' : ''}`}>
      <div className="command-card-heading">
        <strong>{t('command.calendarWriteResult')}</strong>
        <span>{failed > 0 ? t('command.statusError') : t('command.statusSuccess')}</span>
      </div>
      <div className="command-card-meta">
        <span>{t('command.created')} {created}</span>
        <span>{t('command.updated')} {updated}</span>
        <span>{t('command.failed')} {failed}</span>
      </div>
      {dateItems.length ? <small>{t('command.affectedDates')}: {dateItems.join(', ')}</small> : null}
      {planItems.length ? (
        <ul className="command-plan-list">
          {planItems.map((plan, index) => (
            <li key={`${plan.title}-${index}`}>
              <span>{plan.title || t('command.untitledPlan')}</span>
              <small>{plan.date || t('command.noDate')} {plan.time || ''} · {plan.state || ''}</small>
            </li>
          ))}
        </ul>
      ) : null}
      {errorItems.length ? (
        <div className="command-result-errors">
          {errorItems.map((error, index) => <small key={`${error}-${index}`}>{error}</small>)}
        </div>
      ) : null}
    </div>
  );
}
