interface PatchResultPlan {
  date?: string;
  time?: string;
  title?: string;
  content?: string;
}

interface PlanPatchResultCardProps {
  operation?: string;
  status?: string;
  after?: unknown;
  error?: string;
  t: (key: string) => string;
}

function asPlan(value: unknown): PatchResultPlan {
  return value && typeof value === 'object' ? value as PatchResultPlan : {};
}

export function PlanPatchResultCard({ operation, status, after, error, t }: PlanPatchResultCardProps) {
  const plan = asPlan(after);
  const failed = status === 'failed';
  const deleted = operation === 'delete';

  return (
    <div className={`command-inline-card plan-patch-result ${failed ? 'has-error' : ''}`}>
      <div className="command-card-heading">
        <strong>{t('command.planPatchResult')}</strong>
        <span>{failed ? t('command.statusError') : t('command.statusSuccess')}</span>
      </div>
      {failed ? (
        <p>{error || t('command.planPatchFailed')}</p>
      ) : deleted ? (
        <p>{t('command.planDeleted')}</p>
      ) : (
        <p>{t('command.planUpdated')}: {plan.date || t('command.noDate')} {plan.time || '09:00'} - {plan.content || plan.title || t('command.untitledPlan')}</p>
      )}
    </div>
  );
}
