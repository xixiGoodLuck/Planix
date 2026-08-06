import { X } from 'lucide-react';
import type { RefinedTask } from '../types';

export function RefinedTaskPreview(props: {
  refinedTask: RefinedTask;
  onDelete?: () => void;
  deleting?: boolean;
  t: (key: string) => string;
}) {
  const { refinedTask, onDelete, deleting, t } = props;
  return (
    <div className="refined-task-panel">
      <div className="refined-task-panel-head">
        <div className="refined-task-summary">
          <span>{t('legacy.refinedObjective')}</span>
          <p>{refinedTask.objective}</p>
          <strong>{t('legacy.refinedEstimatedMinutes')}: {refinedTask.estimatedMinutes}m</strong>
        </div>
        {onDelete && (
          <button
            type="button"
            className="refined-task-delete"
            onClick={onDelete}
            disabled={deleting}
            aria-label={t('legacy.deleteRefinedTask')}
            title={t('legacy.deleteRefinedTask')}
          >
            <X size={14} />
          </button>
        )}
      </div>
      {refinedTask.budgetExplanation && (
        <div className="refined-task-section">
          <strong>{t('legacy.refinedBudgetExplanation')}</strong>
          <p>{refinedTask.budgetExplanation}</p>
        </div>
      )}
      {Boolean(refinedTask.timeBlocks?.length) && (
        <div className="refined-task-section">
          <strong>{t('legacy.refinedTimeBlocks')}</strong>
          <ul>
            {refinedTask.timeBlocks?.map((block, index) => (
              <li key={`${block.title}-${index}`}>
                <span>{block.durationMinutes}m · {block.title}</span>
                <p>{block.action}</p>
                {block.expectedOutput && (
                  <small>{t('legacy.refinedExpectedOutput')}: {block.expectedOutput}</small>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {Boolean(refinedTask.learningResources?.length) && (
        <div className="refined-task-section">
          <strong>{t('legacy.refinedLearningResources')}</strong>
          <ul>
            {refinedTask.learningResources?.map((resource, index) => (
              <li key={`${resource.title}-${index}`}>
                {resource.url ? (
                  <a href={resource.url} target="_blank" rel="noreferrer">{resource.title}</a>
                ) : (
                  <span>{resource.title}</span>
                )}
                {resource.searchKeyword && (
                  <p>{t('legacy.refinedSearchKeyword')}: {resource.searchKeyword}</p>
                )}
                {resource.reason && (
                  <small>{t('legacy.refinedResourceReason')}: {resource.reason}</small>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {refinedTask.planFitCheck && (
        <div className="refined-task-section">
          <strong>{t('legacy.refinedPlanFitCheck')}</strong>
          <p>{refinedTask.planFitCheck.note}</p>
          <ul>
            <li>{t('legacy.refinedFitsMilestone')}: {refinedTask.planFitCheck.fitsCurrentMilestone ? t('legacy.refinedYes') : t('legacy.refinedNo')}</li>
            <li>{t('legacy.refinedAdvancesGoal')}: {refinedTask.planFitCheck.advancesOverallGoal ? t('legacy.refinedYes') : t('legacy.refinedNo')}</li>
            <li>{t('legacy.refinedHasOutput')}: {refinedTask.planFitCheck.hasCheckableOutput ? t('legacy.refinedYes') : t('legacy.refinedNo')}</li>
          </ul>
        </div>
      )}
      <RefinedList title={t('legacy.refinedSteps')} items={refinedTask.steps} />
      <RefinedList title={t('legacy.refinedChecklist')} items={refinedTask.checklist} />
      <RefinedList title={t('legacy.refinedAcceptanceCriteria')} items={refinedTask.acceptanceCriteria} />
      <div className="refined-task-section">
        <strong>{t('legacy.refinedDeliverable')}</strong>
        <p>{refinedTask.deliverable}</p>
      </div>
      <RefinedList title={t('legacy.refinedRisks')} items={refinedTask.risks} emptyText={t('legacy.refinedNoRisks')} />
      <RefinedList title={t('legacy.refinedFallbackTips')} items={refinedTask.fallbackTips} emptyText={t('legacy.refinedNoFallbackTips')} />
    </div>
  );
}

function RefinedList({ title, items, emptyText }: { title: string; items: string[]; emptyText?: string }) {
  return (
    <div className="refined-task-section">
      <strong>{title}</strong>
      {items.length ? (
        <ul>
          {items.map((item) => <li key={item}>{item}</li>)}
        </ul>
      ) : (
        <p>{emptyText}</p>
      )}
    </div>
  );
}
