interface CalendarPreviewPlan {
  title?: string;
  date?: string;
  time?: string;
  estimatedMinutes?: number;
}

interface CalendarPlanPreviewCardProps {
  title?: string;
  plans?: unknown;
  t: (key: string) => string;
}

function asPlans(value: unknown): CalendarPreviewPlan[] {
  return Array.isArray(value) ? value.filter((item): item is CalendarPreviewPlan => Boolean(item && typeof item === 'object')) : [];
}

export function CalendarPlanPreviewCard({ title, plans, t }: CalendarPlanPreviewCardProps) {
  const items = asPlans(plans);

  return (
    <div className="command-inline-card wide calendar-preview">
      <div className="command-card-heading">
        <strong>{t('command.calendarPreviewTitle')}</strong>
        <span>{items.length}</span>
      </div>
      {title ? <p>{title}</p> : null}
      <ul className="command-plan-list">
        {items.map((plan, index) => (
          <li key={`${plan.title}-${index}`}>
            <span>{plan.title || t('command.untitledPlan')}</span>
            <small>{plan.date || t('command.noDate')} {plan.time || '09:00'} · {plan.estimatedMinutes || 60} {t('command.minutes')}</small>
          </li>
        ))}
      </ul>
    </div>
  );
}
