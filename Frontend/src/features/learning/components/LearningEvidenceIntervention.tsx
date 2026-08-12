import { Captions, RefreshCw, ShieldAlert } from 'lucide-react';
import type { LearningEvidenceIntervention } from '../types';

interface LearningEvidenceInterventionProps {
  intervention: LearningEvidenceIntervention;
  busy: boolean;
  onAddEvidence: () => void;
  onResume: () => void;
  t: (key: string) => string;
}

export function LearningEvidenceIntervention({
  intervention,
  busy,
  onAddEvidence,
  onResume,
  t,
}: LearningEvidenceInterventionProps) {
  return (
    <section className="learning-card learning-evidence-intervention" aria-label={t('learning.interventionTitle')}>
      <header>
        <span className="learning-intervention-icon"><ShieldAlert size={22} /></span>
        <div>
          <span className="learning-eyebrow">{t('learning.interventionEyebrow')}</span>
          <h2>{t('learning.interventionTitle')}</h2>
          <p>{t('learning.interventionDescription')}</p>
        </div>
      </header>

      <div className="learning-intervention-grid">
        <div>
          <h3>{t('learning.interventionRequiredGaps')}</h3>
          <ul className="learning-intervention-gaps">
            {intervention.requiredGaps.map((gap) => (
              <li key={gap.knowledgeId}>
                <div>
                  <strong>{gap.knowledgeName}</strong>
                  <span className={`coverage-${gap.coverageStrength.toLowerCase()}`}>
                    {gap.coverageStrength}
                  </span>
                </div>
                <p>{gap.missingOrPartialReason}</p>
              </li>
            ))}
          </ul>
        </div>

        <div className="learning-intervention-facts">
          <h3>{t('learning.interventionEvidenceChecked')}</h3>
          {intervention.verifiedResources.length > 0 ? (
            <ul>
              {intervention.verifiedResources.map((resource) => (
                <li key={resource.id}>{resource.title}</li>
              ))}
            </ul>
          ) : <p>{t('learning.interventionNoVerifiedResources')}</p>}
          {intervention.transcriptUnavailableResources.length > 0 && (
            <>
              <h3>{t('learning.interventionTranscriptUnavailable')}</h3>
              <ul>
                {intervention.transcriptUnavailableResources.map((resource) => (
                  <li key={resource}>{resource}</li>
                ))}
              </ul>
            </>
          )}
          <p className="learning-intervention-boundary">{t('learning.interventionTimestampBoundary')}</p>
        </div>
      </div>

      <div className="learning-intervention-actions">
        <button type="button" onClick={onAddEvidence} disabled={busy}>
          <Captions size={17} />
          {t('learning.interventionAddEvidence')}
        </button>
        <button className="learning-primary-action" type="button" onClick={onResume} disabled={busy || !intervention.canResume}>
          <RefreshCw size={17} />
          {t('learning.interventionResume')}
        </button>
      </div>
    </section>
  );
}
