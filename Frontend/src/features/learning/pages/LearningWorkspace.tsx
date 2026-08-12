import { FormEvent, useEffect, useState } from 'react';
import { ArrowRight, BookOpenCheck, CircleHelp, Sparkles } from 'lucide-react';
import { LearningFailureNotice } from '../components/LearningFailureNotice';
import { LearningEvidenceIntervention } from '../components/LearningEvidenceIntervention';
import { LearningProgress } from '../components/LearningProgress';
import { LearningResourceInput } from '../components/LearningResourceInput';
import { LearningResult } from '../components/LearningResult';
import { learningStoreActions, useLearningStore } from '../stores/learningStore';
import type { LearningScopeReview as ScopeReview } from '../types';

interface LearningWorkspaceProps {
  language: 'zh-CN' | 'en-US';
  t: (key: string) => string;
}

interface ScopeReviewPanelProps {
  review: ScopeReview;
  supplementDraft: string;
  busy: boolean;
  analysisFailed: boolean;
  onDraftChange: (value: string) => void;
  onSupplement: () => void;
  onContinue: () => void;
  t: (key: string) => string;
}

function knownLabel(field: string, t: (key: string) => string) {
  const labels: Record<string, string> = {
    user_goal: t('learning.knownTopic'),
    target_result: t('learning.knownTargetResult'),
    current_level: t('learning.knownCurrentLevel'),
    content_budget: t('learning.knownContentBudget'),
    language_preference: t('learning.knownLanguagePreference'),
    resource_preference: t('learning.knownResourcePreference'),
    user_supplied_urls: t('learning.knownUserVideos')
  };
  return labels[field] || field;
}

export function LearningScopeReviewPanel({
  review,
  supplementDraft,
  busy,
  analysisFailed,
  onDraftChange,
  onSupplement,
  onContinue,
  t
}: ScopeReviewPanelProps) {
  function submit(event: FormEvent) {
    event.preventDefault();
    onSupplement();
  }

  return (
    <section className="learning-card learning-scope-review" aria-label={t('learning.scopeReview')}>
      <div className="learning-review-section">
        <span className="learning-eyebrow">{t('learning.knownInformation')}</span>
        <h2>{t('learning.understoodSoFar')}</h2>
        <dl className="learning-known-list">
          {review.knownInformation.map((item) => (
            <div key={item.field}>
              <dt>{knownLabel(item.field, t)}</dt>
              <dd>{item.values.join(' · ')}</dd>
            </div>
          ))}
        </dl>
      </div>

      {review.recommendedGaps.length > 0 && (
        <div className="learning-review-section learning-gap-section">
          <div className="learning-section-title">
            <div>
              <span className="learning-eyebrow">{t('learning.batchClarification')}</span>
              <h2>{t('learning.recommendedSupplement')}</h2>
            </div>
            <span className="learning-optional-badge">{t('learning.optional')}</span>
          </div>
          <ul className="learning-gap-list">
            {review.recommendedGaps.map((gap) => (
              <li key={gap.id}>
                <CircleHelp size={18} aria-hidden="true" />
                <div>
                  <strong>{gap.question}</strong>
                  <p>{gap.whyItMatters}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {review.assumptions.length > 0 && (
        <div className="learning-review-section learning-assumption-section">
          <span className="learning-eyebrow">{t('learning.currentAssumptions')}</span>
          <ul>
            {review.assumptions.map((assumption) => (
              <li key={assumption.id}>{assumption.statement}</li>
            ))}
          </ul>
        </div>
      )}

      <form className="learning-supplement-form" onSubmit={submit}>
        <label>
          <span>{t('learning.supplementAllAtOnce')}</span>
          <textarea
            value={supplementDraft}
            onChange={(event) => onDraftChange(event.target.value)}
            placeholder={t('learning.supplementPlaceholder')}
            rows={4}
          />
        </label>
        {analysisFailed && (
          <p className="learning-scope-error" role="alert">{t('learning.scopeAnalysisFailed')}</p>
        )}
        <div className="learning-intake-actions">
          <button type="submit" disabled={busy || !supplementDraft.trim()}>
            <Sparkles size={16} />
            {t('learning.supplementAndAnalyze')}
          </button>
          <button className="learning-primary-action" type="button" disabled={busy} onClick={onContinue}>
            <ArrowRight size={17} />
            {t('learning.continueCurrentScope')}
          </button>
        </div>
      </form>
    </section>
  );
}

export function LearningWorkspace({ language, t }: LearningWorkspaceProps) {
  const learning = useLearningStore();
  const [goal, setGoal] = useState('');
  const busy = learning.status === 'analyzing_scope' || learning.status === 'starting_run';
  const resourceBusy = learning.resourceStatus === 'registering' || learning.resourceStatus === 'validating';

  useEffect(() => {
    void learningStoreActions.restoreActiveRun();
  }, []);

  function submit(event: FormEvent) {
    event.preventDefault();
    void learningStoreActions.startIntake(goal, language);
  }

  function openEvidenceInput() {
    learningStoreActions.setResourceMode('specified');
    document.getElementById('learning-resource-input')?.scrollIntoView({
      behavior: 'smooth',
      block: 'start',
    });
  }

  return (
    <section className="learning-workspace" id="learning-workspace" aria-label={t('learning.title')}>
      <header className="learning-hero">
        <div className="learning-hero-mark"><BookOpenCheck size={28} /></div>
        <div>
          <span className="learning-eyebrow">{t('learning.productName')}</span>
          <h1>{t('learning.title')}</h1>
          <p>{t('learning.subtitle')}</p>
        </div>
      </header>

      <div className="learning-workspace-grid">
        <form className="learning-card learning-input-card" onSubmit={submit}>
          <div className="learning-card-heading">
            <div>
              <span className="learning-eyebrow">{t('learning.naturalLanguageFirst')}</span>
              <h2>{t('learning.tellPlanix')}</h2>
            </div>
          </div>
          <label>
            <span>{t('learning.learningIntent')}</span>
            <textarea
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              placeholder={t('learning.progressiveGoalPlaceholder')}
              rows={6}
              required
              disabled={Boolean(learning.intakeId)}
            />
          </label>
          {!learning.intakeId && (
            <button className="learning-primary-action" type="submit" disabled={busy || !goal.trim()}>
              <Sparkles size={17} />
              {busy ? t('learning.analyzingScope') : t('learning.analyzeGoal')}
            </button>
          )}
          {learning.scopeAnalysisFailed && !learning.intakeId && (
            <p className="learning-scope-error" role="alert">{t('learning.scopeAnalysisFailed')}</p>
          )}
        </form>

        {learning.scopeReview && (
          <>
            {(learning.status !== 'waiting_evidence' || learning.scopeReviewExpanded) && (
              <LearningScopeReviewPanel
                review={learning.scopeReview}
                supplementDraft={learning.supplementDraft}
                busy={busy || resourceBusy || Boolean(learning.runId)}
                analysisFailed={learning.scopeAnalysisFailed}
                onDraftChange={learningStoreActions.setSupplementDraft}
                onSupplement={() => { void learningStoreActions.supplement(); }}
                onContinue={() => { void learningStoreActions.continueWithCurrentScope(); }}
                t={t}
              />
            )}
            <LearningResourceInput
              draft={learning.resourceDraft}
              status={learning.resourceStatus}
              summary={learning.registeredTranscript}
              resourceError={learning.resourceError}
              busy={busy || learning.status === 'running'}
              onModeChange={learningStoreActions.setResourceMode}
              onDraftChange={learningStoreActions.setResourceDraft}
              onRegister={learningStoreActions.registerTranscript}
              onBindVideoOnly={learningStoreActions.bindVideoOnly}
              onRevoke={learningStoreActions.revokeTranscript}
              t={t}
            />
          </>
        )}
      </div>

      {learning.status === 'starting_run' && (
        <p className="learning-auto-continue" role="status">{t('learning.autoStartingKnowledge')}</p>
      )}

      {learning.runId && (
        <LearningProgress
          status={learning.status}
          currentStage={learning.currentStage}
          completedStages={learning.completedStages}
          events={learning.events}
          runStartedAt={learning.runStartedAt}
          stageStartedAt={learning.stageStartedAt}
          latestEventAt={learning.latestEventAt}
          providerReady={learning.providerReady}
          connectionMode={learning.connectionMode}
          recoveryExhausted={learning.recoveryExhausted}
          onRefresh={() => { void learningStoreActions.refreshStatus(); }}
          onReturnToScope={() => {
            learningStoreActions.returnToScopeReview();
            document.querySelector('.learning-scope-review')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
          }}
          t={t}
        />
      )}

      {learning.failureKind && (
        <LearningFailureNotice kind={learning.failureKind} onRetry={learningStoreActions.reset} t={t} />
      )}

      {learning.status === 'waiting_evidence' && learning.intervention && (
        <LearningEvidenceIntervention
          intervention={learning.intervention}
          busy={resourceBusy}
          onAddEvidence={openEvidenceInput}
          onResume={() => { void learningStoreActions.resumeEvidence(); }}
          onReturnToScope={() => {
            learningStoreActions.returnToScopeReview();
            document.querySelector('.learning-scope-review')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
          }}
          t={t}
        />
      )}

      {learning.plan && learning.qualityReport && learning.evidenceGraph && (
        <LearningResult
          plan={learning.plan}
          qualityReport={learning.qualityReport}
          evidenceGraph={learning.evidenceGraph}
          t={t}
        />
      )}
    </section>
  );
}
