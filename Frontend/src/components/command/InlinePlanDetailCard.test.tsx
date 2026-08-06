import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { InlinePlanDetailCard } from './InlinePlanDetailCard';

const labels: Record<string, string> = {
  'command.planDetail': 'Full plan',
  'command.milestone': 'Milestone',
  'command.noDate': 'No date',
  'command.minutes': 'minutes',
  'dashboard.runtimeProposalQuality': 'Plan quality',
  'dashboard.runtimeProposalQualityPassed': 'Good',
  'dashboard.runtimeProposalQualityRepaired': 'Auto-filled',
  'dashboard.runtimeProposalQualityLocalFallback': 'Local template fallback',
  'dashboard.runtimeProposalHorizon': 'Detected horizon',
  'dashboard.runtimeProposalDays': 'days',
  'dashboard.runtimeProposalTaskCount': 'Tasks',
  'dashboard.runtimeProposalCoveredWeeks': 'Covered weeks',
  'dashboard.runtimeProposalDateSpan': 'Date span',
  'dashboard.runtimeProposalSourceType': 'Source',
  'dashboard.runtimeProposalSourceFallback': 'Local fallback',
  'dashboard.runtimeProposalNoticeFallback': 'The model output was not usable, so Planix generated an executable local template plan.'
};

function t(key: string): string {
  return labels[key] ?? key;
}

describe('InlinePlanDetailCard', () => {
  it('renders P Mode draft quality metrics when present', () => {
    const html = renderToStaticMarkup(
      <InlinePlanDetailCard
        title="Python 90 day plan"
        version={2}
        structuredPlan={{
          goalTitle: 'Python 90 day plan',
          goalDescription: 'Project-driven learning.',
          durationDays: 90,
          milestones: [
            {
              title: 'Month 1',
              description: 'Basics',
              tasks: [
                {
                  title: 'Build Python artifact',
                  description: 'Create a script.',
                  estimatedMinutes: 120,
                  dueDate: '2026-07-08',
                  priority: 'high'
                }
              ]
            }
          ],
          reviewPlan: { frequency: 'daily', questions: ['What shipped?'] }
        }}
        planHorizon={{
          rawText: 'three months',
          durationDays: 90,
          horizonType: 'quarterly',
          startDate: '2026-07-01',
          endDate: '2026-09-28',
          expectedMilestoneCount: 3,
          expectedMinTaskCount: 24,
          expectedWeekCount: 10
        }}
        qualityReport={{
          ok: true,
          score: 88,
          totalTasks: 24,
          milestoneCount: 3,
          coveredWeekCount: 10,
          dateSpanDays: 84,
          issues: [],
          metrics: {
            durationDays: 90,
            totalTasks: 24,
            coveredWeekCount: 10,
            dateSpanDays: 84,
            fallbackUsed: true,
            qualityStatus: 'local_fallback',
            sourceType: 'local_fallback',
            localRelevance: 'low'
          }
        }}
        qualityStatus="local_fallback"
        sourceType="local_fallback"
        t={t}
      />
    );

    expect(html).toContain('Full plan');
    expect(html).toContain('Plan quality');
    expect(html).toContain('Local template fallback');
    expect(html).toContain('90 days');
    expect(html).toContain('24');
    expect(html).toContain('Covered weeks');
    expect(html).toContain('Local fallback');
    expect(html).toContain('executable local template plan');
  });
});
