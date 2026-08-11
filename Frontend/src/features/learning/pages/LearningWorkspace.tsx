import { FormEvent, useState } from 'react';
import { BookOpenCheck, Sparkles } from 'lucide-react';
import { LearningFailureNotice } from '../components/LearningFailureNotice';
import { LearningProgress } from '../components/LearningProgress';
import { LearningResult } from '../components/LearningResult';
import { learningStoreActions, useLearningStore } from '../stores/learningStore';

interface LearningWorkspaceProps {
  language: 'zh-CN' | 'en-US';
  t: (key: string) => string;
}

export function LearningWorkspace({ language, t }: LearningWorkspaceProps) {
  const learning = useLearningStore();
  const [goal, setGoal] = useState('');
  const [targetResult, setTargetResult] = useState('');
  const [currentLevel, setCurrentLevel] = useState('');
  const [targetMinutes, setTargetMinutes] = useState('');
  const [constraints, setConstraints] = useState('');
  const busy = learning.status === 'creating' || learning.status === 'created' || learning.status === 'running';

  function submit(event: FormEvent) {
    event.preventDefault();
    const minutes = Number.parseInt(targetMinutes, 10);
    void learningStoreActions.start({
      goal,
      targetResult,
      currentLevel,
      targetMinutes: Number.isFinite(minutes) && minutes > 0 ? minutes : undefined,
      preferredLanguage: language,
      constraints: constraints.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean)
    });
  }

  return (
    <section className="learning-workspace" aria-label={t('learning.title')}>
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
              <span className="learning-eyebrow">{t('learning.newRun')}</span>
              <h2>{t('learning.describeGoal')}</h2>
            </div>
          </div>
          <label>
            <span>{t('learning.goal')}</span>
            <textarea value={goal} onChange={(event) => setGoal(event.target.value)} placeholder={t('learning.goalPlaceholder')} rows={5} required />
          </label>
          <div className="learning-form-grid">
            <label>
              <span>{t('learning.targetResult')}</span>
              <input value={targetResult} onChange={(event) => setTargetResult(event.target.value)} placeholder={t('learning.targetResultPlaceholder')} />
            </label>
            <label>
              <span>{t('learning.currentLevel')}</span>
              <input value={currentLevel} onChange={(event) => setCurrentLevel(event.target.value)} placeholder={t('learning.currentLevelPlaceholder')} />
            </label>
            <label>
              <span>{t('learning.contentBudget')}</span>
              <input type="number" min="1" value={targetMinutes} onChange={(event) => setTargetMinutes(event.target.value)} placeholder={t('learning.contentBudgetPlaceholder')} />
            </label>
            <label>
              <span>{t('learning.constraints')}</span>
              <input value={constraints} onChange={(event) => setConstraints(event.target.value)} placeholder={t('learning.constraintsPlaceholder')} />
            </label>
          </div>
          <button className="learning-primary-action" type="submit" disabled={busy || !goal.trim()}>
            <Sparkles size={17} />
            {busy ? t('learning.generating') : t('learning.generatePlan')}
          </button>
        </form>

        {learning.submittedInput && (
          <section className="learning-card learning-understanding-card">
            <span className="learning-eyebrow">{t('learning.goalUnderstanding')}</span>
            <h2>{learning.submittedInput.goal}</h2>
            <dl>
              <div><dt>{t('learning.targetResult')}</dt><dd>{learning.submittedInput.targetResult || learning.submittedInput.goal}</dd></div>
              <div><dt>{t('learning.currentLevel')}</dt><dd>{learning.submittedInput.currentLevel || t('learning.notSpecified')}</dd></div>
              <div><dt>{t('learning.contentBudget')}</dt><dd>{learning.submittedInput.targetMinutes ? `${learning.submittedInput.targetMinutes} ${t('learning.minutes')}` : t('learning.notSpecified')}</dd></div>
            </dl>
          </section>
        )}
      </div>

      {learning.status !== 'idle' && (
        <LearningProgress
          status={learning.status}
          currentStage={learning.currentStage}
          completedStages={learning.completedStages}
          events={learning.events}
          t={t}
        />
      )}

      {learning.failureKind && (
        <LearningFailureNotice kind={learning.failureKind} onRetry={learningStoreActions.reset} t={t} />
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
