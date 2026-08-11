import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { enUS } from '../../../i18n/en-US';
import { LearningFailureNotice } from '../components/LearningFailureNotice';
import { LearningResult } from '../components/LearningResult';
import type { LearningContentPlan, LearningEvidenceGraph, LearningQualityReport } from '../types';

const t = (key: string): string => {
  const [namespace, item] = key.split('.');
  return enUS[namespace as keyof typeof enUS]?.[item] ?? key;
};

const plan: LearningContentPlan = {
  artifactId: 'plan-1', version: 1, totalDurationSeconds: 600, evidenceGaps: [],
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
