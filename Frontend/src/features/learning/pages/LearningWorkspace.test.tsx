import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { enUS } from '../../../i18n/en-US';
import { LearningFailureNotice } from '../components/LearningFailureNotice';
import { LearningResult } from '../components/LearningResult';
import { LearningLanding, LearningScopeReviewPanel, LearningWorkspace } from './LearningWorkspace';
import { resetCompletedLearningView } from './resetCompletedLearningView';
import type { LearningContentPlan, LearningEvidenceGraph, LearningQualityReport, LearningScopeReview } from '../types';

const t = (key: string): string => {
  const [namespace, item] = key.split('.');
  return enUS[namespace as keyof typeof enUS]?.[item] ?? key;
};

const plan: LearningContentPlan = {
  artifactId: 'plan-1', version: 1, totalDurationSeconds: 600, evidenceGaps: [], deferredKnowledge: [],
  items: [{
    knowledgeId: 'routing', knowledgeName: 'FastAPI Routing', knowledgeExplanation: 'Map HTTP requests to handlers.',
    whyRequired: 'CRUD endpoints require explicit routes.', uncoveredReason: null,
    recommendedContent: [{
      selectionId: 'selection-1', resourceId: 'video-1', segmentId: 'segment-1', videoTitle: 'FastAPI CRUD Tutorial',
      segmentSummary: 'The video demonstrates GET and POST route definitions.', durationSeconds: 600,
      recommendationReason: 'Verified transcript directly demonstrates routing.',
      selectionFacts: { knowledgeCovered: ['routing'], evidenceLevel: 'transcript', savedMinutes: 10, versionCompatible: true, selectionRuleRefs: [] }
    }]
  }]
};

const evidenceGraph: LearningEvidenceGraph = {
  artifactId: 'evidence-1', version: 1,
  resources: [{
    id: 'video-1', provider: 'bilibili', externalId: 'BV1', canonicalUrl: 'https://www.bilibili.com/video/BV1',
    title: 'FastAPI CRUD Tutorial', author: 'Teacher', language: 'en', durationSeconds: 1200, availability: 'available'
  }],
  segments: [{
    id: 'segment-1', resourceId: 'video-1', startSeconds: 60, endSeconds: 660,
    contentSummary: 'GET and POST routes', topics: ['routing'], evidenceRefs: ['evidence-1']
  }],
  evidence: [{
    id: 'evidence-1', resourceId: 'video-1', segmentId: 'segment-1', kind: 'transcript_span',
    supportedClaim: 'Routing demonstration', verificationStatus: 'verified'
  }]
};

const quality: LearningQualityReport = {
  artifactId: 'quality-1', version: 1, hardRulesPassed: true, passed: true, score: 100,
  qualityChecks: [
    { rule: 'knowledge_coverage', passed: true, evidence: ['coverage-1'] },
    { rule: 'evidence_validity', passed: true, evidence: ['evidence-1'] }
  ], issues: [], remainingGaps: []
};

describe('Learning Workspace results', () => {
  it('renders the idle landing with a centered Planix title and one composer only', () => {
    const html = renderToStaticMarkup(<LearningWorkspace language="en-US" t={t} />);

    expect(html).toContain(`<h1>${enUS.learning.landingTitle}</h1>`);
    expect(html).toContain(enUS.learning.landingSubtitle);
    expect(html).toContain(enUS.learning.landingPlaceholder);
    expect(html.match(/<textarea/g)).toHaveLength(1);
    expect(html).not.toContain(enUS.learning.scopeReview);
    expect(html).not.toContain(enUS.learning.resourceSection);
    expect(html).not.toContain(enUS.learning.progressTitle);
    expect(html).not.toContain(enUS.learning.finalPlan);
    expect(html).not.toContain(enUS.learning.targetResultPlaceholder);
    expect(html).not.toContain(enUS.learning.currentLevelPlaceholder);
    expect(html).not.toContain('type="number"');
  });

  it('keeps the landing send action disabled until a goal exists', () => {
    const empty = renderToStaticMarkup(
      <LearningLanding goal="" busy={false} analysisFailed={false} onGoalChange={vi.fn()} onSubmit={vi.fn()} t={t} />
    );
    const ready = renderToStaticMarkup(
      <LearningLanding goal="I want to learn FastAPI" busy={false} analysisFailed={false} onGoalChange={vi.fn()} onSubmit={vi.fn()} t={t} />
    );
    const analyzing = renderToStaticMarkup(
      <LearningLanding goal="I want to learn FastAPI" busy analysisFailed={false} onGoalChange={vi.fn()} onSubmit={vi.fn()} t={t} />
    );

    expect(empty).toMatch(/<button[^>]*disabled=""/);
    expect(ready).not.toMatch(/<button[^>]*disabled=""/);
    expect(analyzing).toContain(enUS.learning.analyzingScope);
    expect(analyzing).toMatch(/<button[^>]*disabled=""/);
  });

  it('renders known facts, multiple optional gaps, assumptions, and an always-present continue action', () => {
    const review: LearningScopeReview = {
      knownInformation: [{ field: 'user_goal', values: ['FastAPI'], sourceRefs: ['user:message:1'] }],
      recommendedGaps: [
        { id: 'gap-level', question: 'What do you know?', whyItMatters: 'It changes the starting point.', impact: 'high', blocking: false, affectedFields: ['current_level'] },
        { id: 'gap-budget', question: 'How much time?', whyItMatters: 'It controls duration.', impact: 'medium', blocking: false, affectedFields: ['content_budget'] }
      ],
      assumptions: [{ id: 'assumption-level', statement: 'Current level is unspecified.', basis: 'Code default.', sourceRef: 'system:scope-readiness:current_level', impact: 'high' }],
      readyForPlanning: false,
      highImpactGapCount: 1,
      recommendationRound: 1,
      autoContinueReason: 'high_impact_gaps_remain'
    };

    const html = renderToStaticMarkup(
      <LearningScopeReviewPanel
        review={review}
        supplementDraft=""
        busy={false}
        analysisFailed={false}
        onDraftChange={vi.fn()}
        onSupplement={vi.fn()}
        onContinue={vi.fn()}
        t={t}
      />
    );

    expect(html).toContain(enUS.learning.knownInformation);
    expect(html).toContain('FastAPI');
    expect(html).toContain('What do you know?');
    expect(html).toContain('How much time?');
    expect(html).toContain(enUS.learning.optional);
    expect(html).toContain(enUS.learning.currentAssumptions);
    expect(html).toContain('Current level is unspecified.');
    expect(html).toContain(enUS.learning.continueCurrentScope);
  });

  it('renders the safe scope failure message without backend internals', () => {
    const review: LearningScopeReview = {
      knownInformation: [{ field: 'user_goal', values: ['FastAPI'], sourceRefs: [] }],
      recommendedGaps: [], assumptions: [], readyForPlanning: false,
      highImpactGapCount: 1, recommendationRound: 1, autoContinueReason: 'high_impact_gaps_remain'
    };
    const html = renderToStaticMarkup(
      <LearningScopeReviewPanel
        review={review}
        supplementDraft="I know Python"
        busy={false}
        analysisFailed
        onDraftChange={vi.fn()}
        onSupplement={vi.fn()}
        onContinue={vi.fn()}
        t={t}
      />
    );

    expect(html).toContain(enUS.learning.scopeAnalysisFailed);
    expect(html).not.toContain('Pydantic');
    expect(html).not.toContain('invalid_model_output');
    expect(html).not.toContain('stack trace');
  });

  it('renders knowledge, verified video evidence, timestamps, duration, and quality', () => {
    const html = renderToStaticMarkup(<LearningResult plan={plan} evidenceGraph={evidenceGraph} qualityReport={quality} t={t} />);

    expect(html).toContain('FastAPI Routing');
    expect(html).toContain('CRUD endpoints require explicit routes.');
    expect(html).toContain('https://www.bilibili.com/video/BV1');
    expect(html).toContain('1:00 – 11:00');
    expect(html).toContain('10:00');
    expect(html).toContain(enUS.learning.qualityPassed);
    expect(html).toContain(enUS.learning.quality_knowledge_coverage);
  });

  it('offers a new learning goal action for a completed result', () => {
    const html = renderToStaticMarkup(
      <LearningResult plan={plan} evidenceGraph={evidenceGraph} qualityReport={quality} onNewGoal={vi.fn()} t={t} />
    );

    expect(html).toContain(`+ ${enUS.learning.startNewLearningGoal}`);
  });

  it('resets completed UI state, clears the goal, and scrolls to the Landing', () => {
    let learningStatus = 'completed';
    let goal = 'Learn FastAPI';
    const scrollTo = vi.fn();

    resetCompletedLearningView(
      () => { learningStatus = 'idle'; },
      (value) => { goal = value; },
      scrollTo,
    );

    expect(learningStatus).toBe('idle');
    expect(goal).toBe('');
    expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: 'smooth' });

    const landing = renderToStaticMarkup(
      <LearningLanding goal={goal} busy={false} analysisFailed={false} onGoalChange={vi.fn()} onSubmit={vi.fn()} t={t} />
    );
    expect(landing).toContain(enUS.learning.landingComposer);
    expect(landing).toContain('<textarea');
    expect(landing).not.toContain(enUS.learning.finalPlan);
  });

  it('distinguishes deferred verified knowledge from missing evidence', () => {
    const deferredPlan: LearningContentPlan = {
      ...plan,
      items: [{
        knowledgeId: 'advanced-topic',
        knowledgeName: 'Advanced topic',
        knowledgeExplanation: 'An additional verified topic.',
        whyRequired: 'Useful after the required path.',
        uncoveredReason: null,
        recommendedContent: []
      }],
      deferredKnowledge: [{
        knowledgeId: 'advanced-topic',
        importance: 'important',
        reason: 'lower_priority',
        candidateSegmentRefs: ['segment-2'],
        marginalDurationSeconds: 120,
        policyRuleRefs: ['minimum_sufficient_selection'],
        description: 'Verified but deferred.'
      }]
    };

    const html = renderToStaticMarkup(
      <LearningResult plan={deferredPlan} evidenceGraph={evidenceGraph} qualityReport={quality} t={t} />
    );

    expect(html).toContain(enUS.learning.selectionOmitted);
    expect(html).not.toContain(enUS.learning.evidenceMissing);
  });

  it('renders a quality failure without internal exception text', () => {
    const failedQuality = { ...quality, hardRulesPassed: false, passed: false, issues: [{
      issueId: 'issue-1', rule: 'evidence_validity', severity: 'major' as const,
      description: 'Internal transcript validator stack'
    }] };
    const html = renderToStaticMarkup(<LearningResult plan={plan} evidenceGraph={evidenceGraph} qualityReport={failedQuality} t={t} />);

    expect(html).toContain(enUS.learning.qualityFailed);
    expect(html).not.toContain('Internal transcript validator stack');
  });

  it('renders a safe Provider unavailable state', () => {
    const html = renderToStaticMarkup(<LearningFailureNotice kind="provider_unavailable" onRetry={vi.fn()} t={t} />);

    expect(html).toContain(enUS.learning.failure_provider_unavailable_title);
    expect(html).not.toContain('Traceback');
    expect(html).not.toContain('prompt');
    expect(html).not.toContain('token');
  });
});
