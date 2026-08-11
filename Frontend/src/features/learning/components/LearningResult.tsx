import { Check, CheckCircle2, Clock3, ExternalLink, ShieldCheck, Video } from 'lucide-react';
import type {
  LearningContentPlan,
  LearningEvidenceGraph,
  LearningQualityReport
} from '../types';

interface LearningResultProps {
  plan: LearningContentPlan;
  evidenceGraph: LearningEvidenceGraph;
  qualityReport: LearningQualityReport;
  t: (key: string) => string;
}

function formatSeconds(value: number) {
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const seconds = value % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
    : `${minutes}:${String(seconds).padStart(2, '0')}`;
}

export function LearningResult({ plan, evidenceGraph, qualityReport, t }: LearningResultProps) {
  const resources = new Map(evidenceGraph.resources.map((resource) => [resource.id, resource]));
  const segments = new Map(evidenceGraph.segments.map((segment) => [segment.id, segment]));

  return (
    <div className="learning-result-stack">
      <section className="learning-card learning-result-header">
        <div>
          <span className="learning-eyebrow">{t('learning.finalPlan')}</span>
          <h2>{t('learning.knowledgeRoute')}</h2>
          <p>{t('learning.resultDescription')}</p>
        </div>
        <div className="learning-duration-total">
          <Clock3 size={18} />
          <span>{t('learning.totalDuration')}</span>
          <strong>{formatSeconds(plan.totalDurationSeconds)}</strong>
        </div>
      </section>

      <section className="learning-knowledge-route" aria-label={t('learning.knowledgeRoute')}>
        {plan.items.map((item, index) => (
          <article className="learning-knowledge-card" key={item.knowledgeId}>
            <header>
              <span className="learning-knowledge-index">{String(index + 1).padStart(2, '0')}</span>
              <div>
                <h3>{item.knowledgeName}</h3>
                <p>{item.knowledgeExplanation}</p>
              </div>
            </header>
            <div className="learning-why">
              <strong>{t('learning.whyNeeded')}</strong>
              <p>{item.whyRequired}</p>
            </div>

            {item.recommendedContent.length > 0 ? (
              <div className="learning-resource-list">
                {item.recommendedContent.map((content) => {
                  const resource = resources.get(content.resourceId);
                  const segment = segments.get(content.segmentId);
                  return (
                    <article className="learning-resource-card" key={content.selectionId}>
                      <div className="learning-resource-heading">
                        <Video size={17} />
                        {resource?.canonicalUrl ? (
                          <a href={resource.canonicalUrl} target="_blank" rel="noreferrer">
                            {content.videoTitle}<ExternalLink size={13} />
                          </a>
                        ) : <strong>{content.videoTitle}</strong>}
                      </div>
                      <p>{content.segmentSummary}</p>
                      <dl>
                        <div>
                          <dt>{t('learning.watchRange')}</dt>
                          <dd>{segment ? `${formatSeconds(segment.startSeconds)} – ${formatSeconds(segment.endSeconds)}` : t('common.unknown')}</dd>
                        </div>
                        <div>
                          <dt>{t('learning.watchDuration')}</dt>
                          <dd>{formatSeconds(content.durationSeconds)}</dd>
                        </div>
                        <div>
                          <dt>{t('learning.evidenceLevel')}</dt>
                          <dd>{content.selectionFacts?.evidenceLevel || t('common.unknown')}</dd>
                        </div>
                      </dl>
                      <div className="learning-recommendation-reason">
                        <strong>{t('learning.recommendationReason')}</strong>
                        <span>{content.recommendationReason}</span>
                      </div>
                    </article>
                  );
                })}
              </div>
            ) : (
              <div className="learning-evidence-gap">{item.uncoveredReason || t('learning.evidenceMissing')}</div>
            )}
          </article>
        ))}
      </section>

      <section className={`learning-card learning-quality-card ${qualityReport.passed ? 'passed' : 'failed'}`}>
        <div className="learning-card-heading">
          <div className="learning-quality-title">
            {qualityReport.passed ? <CheckCircle2 size={20} /> : <ShieldCheck size={20} />}
            <div>
              <span className="learning-eyebrow">{t('learning.qualityValidation')}</span>
              <h2>{qualityReport.passed ? t('learning.qualityPassed') : t('learning.qualityFailed')}</h2>
            </div>
          </div>
          {qualityReport.score != null && <strong className="learning-quality-score">{Math.round(qualityReport.score)}</strong>}
        </div>
        <div className="learning-quality-grid">
          {qualityReport.qualityChecks.map((check) => (
            <div className={check.passed ? 'passed' : 'failed'} key={check.rule}>
              <span>{check.passed ? <Check size={14} /> : '!'}</span>
              <strong>{t(`learning.quality_${check.rule}`)}</strong>
            </div>
          ))}
        </div>
        {qualityReport.issues.length > 0 && (
          <div className="learning-quality-issues">
            <strong>{t('learning.qualityIssues')}</strong>
            <ul>{qualityReport.issues.map((issue) => <li key={issue.issueId}>{t(`learning.quality_${issue.rule}`)}</li>)}</ul>
          </div>
        )}
      </section>
    </div>
  );
}
