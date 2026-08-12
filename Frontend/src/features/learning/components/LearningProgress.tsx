import { Check, Circle, LoaderCircle } from 'lucide-react';
import type { LearningProgressEvent, LearningWorkspaceStatus } from '../types';

interface LearningProgressProps {
  status: LearningWorkspaceStatus;
  currentStage: string;
  completedStages: string[];
  events: LearningProgressEvent[];
  t: (key: string) => string;
}

const steps: ReadonlyArray<{ key: string; stages: readonly string[] }> = [
  { key: 'understanding', stages: ['scope'] },
  { key: 'knowledge', stages: ['knowledge_generation'] },
  { key: 'evidence', stages: ['evidence_generation', 'coverage_analysis', 'gap_completion', 'waiting_evidence'] },
  { key: 'selection', stages: ['selection'] },
  { key: 'quality', stages: ['quality', 'completed'] }
];

function eventCompleted(stepStages: readonly string[], events: LearningProgressEvent[]) {
  return events.some((event) => event.event_type === 'stage_completed' && stepStages.includes(event.stage));
}

export function LearningProgress(props: LearningProgressProps) {
  const { status, currentStage, completedStages, events, t } = props;
  const completed = new Set(completedStages);

  return (
    <section className="learning-card learning-progress-card" aria-label={t('learning.progressTitle')}>
      <div className="learning-card-heading">
        <div>
          <span className="learning-eyebrow">{t('learning.agentProcess')}</span>
          <h2>{t('learning.progressTitle')}</h2>
        </div>
        <span className={`learning-run-status ${status}`}>{t(`learning.status_${status}`)}</span>
      </div>

      <ol className="learning-stage-list">
        {steps.map((step) => {
          const isComplete = status === 'completed'
            || step.stages.some((stage) => completed.has(stage))
            || eventCompleted(step.stages, events);
          const isActive = !isComplete && step.stages.includes(currentStage);
          return (
            <li className={isComplete ? 'complete' : isActive ? 'active' : 'pending'} key={step.key}>
              <span className="learning-stage-icon" aria-hidden="true">
                {isComplete ? <Check size={15} /> : isActive ? <LoaderCircle size={15} /> : <Circle size={10} />}
              </span>
              <div>
                <strong>{t(`learning.stage_${step.key}`)}</strong>
                <span>{t(`learning.stage_${step.key}_description`)}</span>
              </div>
            </li>
          );
        })}
      </ol>

      {events.length > 0 && (
        <div className="learning-event-feed" aria-live="polite">
          <h3>{t('learning.liveEvents')}</h3>
          {events.slice(-8).map((event, index) => (
            <div className="learning-event" key={event.stream_id || `${event.timestamp}-${event.event_type}-${index}`}>
              <time>{new Date(event.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</time>
              <span>{t(`learning.event_${event.event_type}`)}</span>
              <small>{t(`learning.stage_${steps.find((step) => step.stages.includes(event.stage))?.key || 'understanding'}`)}</small>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
