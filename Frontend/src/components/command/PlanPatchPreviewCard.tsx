interface PatchPlan {
  date?: string;
  time?: string;
  title?: string;
  content?: string;
  completion?: string;
  estimatedMinutes?: number;
}

interface PlanPatchPreviewCardProps {
  operation?: string;
  before?: unknown;
  after?: unknown;
  changes?: unknown;
  t: (key: string) => string;
}

function asPlan(value: unknown): PatchPlan {
  return value && typeof value === 'object' ? value as PatchPlan : {};
}

function asChanges(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {};
}

function planLine(plan: PatchPlan, t: (key: string) => string): string {
  return `${plan.date || t('command.noDate')} ${plan.time || '09:00'} - ${plan.content || plan.title || t('command.untitledPlan')} - ${plan.estimatedMinutes || 30} ${t('command.minutes')}`;
}

export function PlanPatchPreviewCard({ operation, before, after, changes, t }: PlanPatchPreviewCardProps) {
  const beforePlan = asPlan(before);
  const afterPlan = asPlan(after);
  const changeMap = asChanges(changes);
  const changeKeys = Object.keys(changeMap);
  const isDelete = operation === 'delete';

  return (
    <div className={`command-inline-card wide plan-patch-preview ${isDelete ? 'delete' : 'update'}`}>
      <div className="command-card-heading">
        <strong>{t('command.planPatchPreview')}</strong>
        <span>{isDelete ? t('command.deleteOperation') : t('command.updateOperation')}</span>
      </div>
      <div className="command-patch-lines">
        <p>
          <small>{t('command.before')}</small>
          <span>{planLine(beforePlan, t)}</span>
        </p>
        {!isDelete ? (
          <p>
            <small>{t('command.after')}</small>
            <span>{planLine(afterPlan, t)}</span>
          </p>
        ) : null}
      </div>
      {changeKeys.length ? (
        <ul className="command-compact-list">
          {changeKeys.map((key) => (
            <li key={key}>
              <span>{key}</span>
              <small>{String(changeMap[key] ?? '')}</small>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
