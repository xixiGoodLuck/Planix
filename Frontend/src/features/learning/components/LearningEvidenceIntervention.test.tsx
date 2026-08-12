import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { enUS } from '../../../i18n/en-US';
import type { LearningEvidenceIntervention as Intervention } from '../types';
import { LearningEvidenceIntervention } from './LearningEvidenceIntervention';

const t = (key: string): string => {
  const [namespace, item] = key.split('.');
  return enUS[namespace as keyof typeof enUS]?.[item] ?? key;
};

const intervention: Intervention = {
  kind: 'additional_evidence_required',
  requiredGaps: [{
    knowledgeId: 'knowledge-database',
    knowledgeName: 'Database persistence',
    gapType: 'missing_knowledge',
    coverageStrength: 'MISSING',
    missingOrPartialReason: 'No verified transcript supports record persistence.',
  }],
  searchedResources: ['fastapi database persistence'],
  transcriptUnavailableResources: ['https://www.bilibili.com/video/BV1missing'],
  verifiedResources: [{
    id: 'video-routing', title: 'FastAPI Routing',
    canonicalUrl: 'https://www.bilibili.com/video/BV1routing', availability: 'available',
  }],
  verifiedSegments: [],
  knowledgeCoverage: [{
    id: 'knowledge-database', name: 'Database persistence',
    importance: 'required', coverageStrength: 'MISSING',
  }],
  canResume: true,
};

describe('Learning evidence intervention', () => {
  it('renders a recoverable evidence pause without a failure banner', () => {
    const html = renderToStaticMarkup(
      <LearningEvidenceIntervention
        intervention={intervention}
        busy={false}
        onAddEvidence={vi.fn()}
        onResume={vi.fn()}
        t={t}
      />
    );

    expect(html).toContain(enUS.learning.interventionTitle);
    expect(html).toContain('Database persistence');
    expect(html).toContain('MISSING');
    expect(html).toContain('FastAPI Routing');
    expect(html).toContain('BV1missing');
    expect(html).toContain(enUS.learning.interventionTimestampBoundary);
    expect(html).toContain(enUS.learning.interventionAddEvidence);
    expect(html).toContain(enUS.learning.interventionResume);
    expect(html).not.toContain(enUS.learning.failure_run_failed_title);
  });
});
