import type { PlanHorizon, PlanQualityReport, PlanQualityStatus, PlanSourceType, StructuredGoalPlan } from '../../types';

interface InlinePlanDetailCardProps {
  title?: string;
  structuredPlan?: unknown;
  version?: number;
  planHorizon?: PlanHorizon | null;
  qualityReport?: PlanQualityReport | null;
  qualityStatus?: PlanQualityStatus | null;
  sourceType?: PlanSourceType | null;
  t: (key: string) => string;
}

function asPlan(value: unknown): StructuredGoalPlan | null {
  if (!value || typeof value !== 'object') return null;
  const plan = value as StructuredGoalPlan;
  return Array.isArray(plan.milestones) ? plan : null;
}

function taskCount(plan: StructuredGoalPlan): number {
  return plan.milestones.reduce((count, milestone) => count + (milestone.tasks?.length || 0), 0);
}

function qualityLabel(status: PlanQualityStatus | null | undefined, t: (key: string) => string): string {
  if (status === 'repaired') return t('dashboard.runtimeProposalQualityRepaired');
  if (status === 'local_fallback') return t('dashboard.runtimeProposalQualityLocalFallback');
  return t('dashboard.runtimeProposalQualityPassed');
}

function sourceLabel(sourceType: PlanSourceType | null | undefined, t: (key: string) => string): string {
  if (sourceType === 'local_context') return t('dashboard.runtimeProposalSourceLocal');
  if (sourceType === 'local_fallback') return t('dashboard.runtimeProposalSourceFallback');
  if (sourceType === 'insufficient_context') return t('dashboard.runtimeProposalSourceInsufficient');
  if (sourceType === 'model_knowledge') return t('dashboard.runtimeProposalSourceModel');
  return '';
}

function qualityNotice(status: PlanQualityStatus | null | undefined, sourceType: PlanSourceType | null | undefined, t: (key: string) => string): string {
  if (sourceType === 'insufficient_context') return t('dashboard.runtimeProposalNoticeInsufficient');
  if (status === 'repaired') return t('dashboard.runtimeProposalNoticeRepaired');
  if (status === 'local_fallback') return t('dashboard.runtimeProposalNoticeFallback');
  return '';
}

export function InlinePlanDetailCard({
  title,
  structuredPlan,
  version,
  planHorizon,
  qualityReport,
  qualityStatus,
  sourceType,
  t
}: InlinePlanDetailCardProps) {
  const plan = asPlan(structuredPlan);

  if (!plan) {
    return (
      <div className="command-inline-card error">
        <strong>{t('command.planDetail')}</strong>
        <p>{t('command.invalidPlanDetail')}</p>
      </div>
    );
  }

  const metrics = qualityReport?.metrics;
  const durationDays = metrics?.durationDays ?? planHorizon?.durationDays ?? plan.durationDays;
  const coveredWeekCount = metrics?.coveredWeekCount ?? qualityReport?.coveredWeekCount;
  const dateSpanDays = metrics?.dateSpanDays ?? qualityReport?.dateSpanDays;
  const totalTasks = metrics?.totalTasks ?? qualityReport?.totalTasks ?? taskCount(plan);
  const source = sourceLabel(sourceType, t);
  const notice = qualityNotice(qualityStatus, sourceType, t);
  const hasQualityMeta = Boolean(qualityReport || qualityStatus || planHorizon || sourceType);

  return (
    <div className="command-inline-card wide plan-detail">
      <div className="command-card-heading">
        <strong>{t('command.planDetail')}</strong>
        {version ? <span>v{version}</span> : null}
      </div>
      <div className="inline-plan-detail">
        <h3>{title || plan.goalTitle}</h3>
        {plan.goalDescription ? <p>{plan.goalDescription}</p> : null}
        {hasQualityMeta ? (
          <div className="runtime-proposal-meta command-quality-meta">
            <span>{t('dashboard.runtimeProposalQuality')}: {qualityLabel(qualityStatus, t)}</span>
            {durationDays ? <span>{t('dashboard.runtimeProposalHorizon')}: {durationDays} {t('dashboard.runtimeProposalDays')}</span> : null}
            <span>{t('dashboard.runtimeProposalTaskCount')}: {totalTasks}</span>
            {coveredWeekCount !== undefined ? <span>{t('dashboard.runtimeProposalCoveredWeeks')}: {coveredWeekCount}</span> : null}
            {dateSpanDays !== undefined ? <span>{t('dashboard.runtimeProposalDateSpan')}: {dateSpanDays} {t('dashboard.runtimeProposalDays')}</span> : null}
            {source ? <span>{t('dashboard.runtimeProposalSourceType')}: {source}</span> : null}
          </div>
        ) : null}
        {notice ? <p className="runtime-proposal-summary">{notice}</p> : null}
        {plan.milestones.map((milestone, milestoneIndex) => (
          <section className="inline-milestone" key={`${milestone.title}-${milestoneIndex}`}>
            <strong>{milestone.title || `${t('command.milestone')} ${milestoneIndex + 1}`}</strong>
            {milestone.description ? <small>{milestone.description}</small> : null}
            <ul>
              {(milestone.tasks || []).map((task, taskIndex) => (
                <li key={`${task.title}-${taskIndex}`}>
                  <span>{task.title}</span>
                  <small>
                    {task.dueDate || t('command.noDate')} · {task.estimatedMinutes || 60} {t('command.minutes')} · {task.priority || 'medium'}
                  </small>
                  {task.description ? <small>{task.description}</small> : null}
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
