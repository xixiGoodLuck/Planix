import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { enUS } from '../../../i18n/en-US';
import type { LearningEvidenceIntervention as Intervention, LearningProgressEvent } from '../types';
import { LearningEvidenceIntervention } from './LearningEvidenceIntervention';
import { LearningProgress } from './LearningProgress';

const t = (key: string): string => {
  const [namespace, item] = key.split('.');
  return enUS[namespace as keyof typeof enUS]?.[item] ?? key;
};

const events: LearningProgressEvent[] = [{
  stream_id: '1', event_type: 'stage_started', stage: 'evidence_generation', status: 'started',
  message: 'Safe progress event', timestamp: '2026-08-12T08:00:00.000Z'
}];

const intervention: Intervention = {
  kind: 'additional_evidence_required',
  requiredGaps: [{
    knowledgeId: 'persistence', knowledgeName: 'Database persistence',
    gapType: 'missing_knowledge', coverageStrength: 'MISSING',
    missingOrPartialReason: 'No verified transcript supports database persistence.'
  }],
  searchedResources: ['routing-only-controlled-resource'],
  transcriptUnavailableResources: ['persistence-video-without-transcript'],
  verifiedResources: [], verifiedSegments: [],
  knowledgeCoverage: [{
    id: 'persistence', name: 'Database persistence', importance: 'required', coverageStrength: 'MISSING'
  }],
  canResume: true
};

describe('Release browser states', () => {
  it('renders only user-facing stages plus runtime feedback', () => {
    const html = renderToStaticMarkup(<LearningProgress
      status="running" currentStage="evidence_generation" completedStages={['scope', 'knowledge_generation']}
      events={events} runStartedAt="2026-08-12T07:59:00.000Z" stageStartedAt="2026-08-12T08:00:00.000Z"
      latestEventAt="2026-08-12T08:00:00.000Z" providerReady connectionMode="sse" recoveryExhausted={false}
      onRefresh={vi.fn()} onReturnToScope={vi.fn()} t={t}
    />);
    expect(html).toContain(enUS.learning.stage_resources);
    expect(html).toContain(enUS.learning.providerReady);
    expect(html).not.toContain('knowledge_generation');
    expect(html).not.toContain('gap_completion');
  });

  it('renders the full waiting and same-run resume action set', () => {
    const html = renderToStaticMarkup(<LearningEvidenceIntervention
      intervention={intervention} busy={false} onAddEvidence={vi.fn()} onResume={vi.fn()}
      onReturnToScope={vi.fn()} t={t}
    />);
    expect(html).toContain(enUS.learning.interventionTitle);
    expect(html).toContain('Database persistence');
    expect(html).toContain('MISSING');
    expect(html).toContain('routing-only-controlled-resource');
    expect(html).toContain('persistence-video-without-transcript');
    expect(html).toContain(enUS.learning.interventionAddVideo);
    expect(html).toContain(enUS.learning.interventionAddTranscript);
    expect(html).toContain(enUS.learning.interventionResume);
    expect(html).toContain(enUS.learning.returnAndAdjustGoal);
  });
});
