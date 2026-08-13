import { useEffect, useState } from 'react';
import { Check, Circle, LoaderCircle, RefreshCw, Undo2 } from 'lucide-react';
import type { LearningConnectionMode, LearningProgressEvent, LearningWorkspaceStatus } from '../types';

interface LearningProgressProps {
  status: LearningWorkspaceStatus;
  currentStage: string;
  completedStages: string[];
  events: LearningProgressEvent[];
  runStartedAt: string | null;
  stageStartedAt: string | null;
  latestEventAt: string | null;
  providerReady: boolean | null;
  connectionMode: LearningConnectionMode;
  recoveryExhausted: boolean;
  onRefresh: () => void;
  onReturnToScope: () => void;
  t: (key: string) => string;
}

const steps: ReadonlyArray<{ key: string; stages: readonly string[] }> = [
  { key: 'understanding', stages: ['scope'] },
  { key: 'knowledge', stages: ['knowledge_generation'] },
  { key: 'resources', stages: ['evidence_generation'] },
  { key: 'coverage', stages: ['coverage_analysis'] },
  { key: 'supplementEvidence', stages: ['gap_completion', 'waiting_evidence'] },
  { key: 'selection', stages: ['selection'] },
  { key: 'quality', stages: ['quality'] },
  { key: 'complete', stages: ['completed'] }
];

function formatDuration(start: string | null, now: number) {
  if (!start) return '--';
  const seconds = Math.max(0, Math.floor((now - Date.parse(start)) / 1000));
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
}

function eventCompleted(stepStages: readonly string[], events: LearningProgressEvent[]) {
  return events.some((event) => event.event_type === 'stage_completed' && stepStages.includes(event.stage));
}

export function LearningProgress(props: LearningProgressProps) {
  const {
    status, currentStage, completedStages, events, runStartedAt, stageStartedAt,
    latestEventAt, providerReady, connectionMode, recoveryExhausted, onRefresh,
    onReturnToScope, t,
  } = props;
  const completed = new Set(completedStages);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (status !== 'running') return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [status]);
  const lastSignalAt = latestEventAt || stageStartedAt || runStartedAt;
  const takingLonger = status === 'running' && Boolean(lastSignalAt) && now - Date.parse(lastSignalAt!) > 40_000;
  const visibleStatus = status === 'waiting_evidence' || status === 'completed' || status === 'failed'
    ? status
    : status === 'analyzing_scope' || status === 'waiting_scope_review'
      ? 'understanding'
      : 'running';

  return (
    <section className="learning-card learning-progress-card" aria-label={t('learning.progressTitle')}>
      <div className="learning-card-heading">
        <div>
          <span className="learning-eyebrow">{t('learning.agentProcess')}</span>
          <h2>{t('learning.progressTitle')}</h2>
        </div>
        <span className={`learning-run-status ${visibleStatus}`}>{t(`learning.status_${visibleStatus}`)}</span>
      </div>

      <dl className="learning-runtime-feedback">
        <div><dt>{t('learning.currentBusinessStage')}</dt><dd>{t(`learning.stage_${steps.find((step) => step.stages.includes(currentStage))?.key || 'understanding'}`)}</dd></div>
        <div><dt>{t('learning.stageStartedAt')}</dt><dd>{stageStartedAt ? new Date(stageStartedAt).toLocaleTimeString() : '--'}</dd></div>
        <div><dt>{t('learning.elapsedTime')}</dt><dd>{formatDuration(runStartedAt, now)}</dd></div>
        <div><dt>{t('learning.latestEventAt')}</dt><dd>{latestEventAt ? new Date(latestEventAt).toLocaleTimeString() : t('learning.waitingForFirstEvent')}</dd></div>
        <div><dt>{t('learning.providerStatus')}</dt><dd>{providerReady === true ? t('learning.providerReady') : providerReady === false ? t('learning.providerUnavailable') : t('learning.providerChecking')}</dd></div>
        <div><dt>{t('learning.connectionStatus')}</dt><dd>{t(`learning.connection_${connectionMode}`)}</dd></div>
      </dl>

      {(takingLonger || recoveryExhausted) && (
        <div className="learning-stall-notice" role="status">
          <p>{t('learning.stageTakingLonger')}</p>
          <div>
            <button type="button" onClick={onRefresh}><RefreshCw size={15} />{t('learning.refreshStatus')}</button>
            <button type="button" onClick={onReturnToScope}><Undo2 size={15} />{t('learning.returnScopeReview')}</button>
          </div>
        </div>
      )}

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
