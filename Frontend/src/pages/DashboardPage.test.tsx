import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { RuntimeProposalPreview } from './DashboardPage';
import type { RuntimePlanProposal } from '../types';

const labels: Record<string, string> = {
  'dashboard.runtimeProposalEyebrow': 'Runtime proposal',
  'dashboard.runtimeProposalTitle': 'Task proposal Preview',
  'dashboard.runtimeProposalTaskCount': 'Tasks',
  'dashboard.runtimeProposalSources': 'Sources',
  'dashboard.runtimeProposalMode': 'Mode',
  'dashboard.runtimeProposalModeLlm': 'Model plan',
  'dashboard.runtimeProposalModeLocalFallback': 'Local template plan',
  'dashboard.runtimeProposalQuality': 'Plan quality',
  'dashboard.runtimeProposalQualityPassed': 'Good',
  'dashboard.runtimeProposalQualityRepaired': 'Auto-filled',
  'dashboard.runtimeProposalQualityLocalFallback': 'Local template fallback',
  'dashboard.runtimeProposalHorizon': 'Detected horizon',
  'dashboard.runtimeProposalDays': 'days',
  'dashboard.runtimeProposalCoveredWeeks': 'Covered weeks',
  'dashboard.runtimeProposalDateSpan': 'Date span',
  'dashboard.runtimeProposalSourceType': 'Source',
  'dashboard.runtimeProposalSourceModel': 'Model knowledge',
  'dashboard.runtimeProposalNoticeRepaired': 'The original plan was too sparse and has been automatically completed.',
  'dashboard.runtimeProposalCoverage': 'Coverage',
  'dashboard.noDueDate': 'No date',
  'dashboard.writingToCalendar': 'Writing',
  'legacy.writeToCalendar': 'Write to calendar'
};

function t(key: string): string {
  return labels[key] ?? key;
}

describe('RuntimeProposalPreview', () => {
  it('renders concise demo quality metrics', () => {
    const proposal: RuntimePlanProposal = {
      runtimeRunId: 'run-1',
      goal: 'Python 90 day plan',
      mode: 'llm',
      tasks: [],
      sources: [],
      structuredPlan: {
        goalTitle: 'Python 90 day plan',
        goalDescription: 'Project-driven Python learning.',
        durationDays: 90,
        milestones: [
          {
            title: 'Month 1',
            description: 'Basics',
            tasks: [
              {
                title: 'Build Python artifact 1',
                description: 'Create a small script.',
                estimatedMinutes: 120,
                dueDate: '2026-07-08',
                priority: 'high'
              }
            ]
          }
        ],
        reviewPlan: { frequency: 'daily', questions: ['What shipped?'] }
      },
      planHorizon: {
        rawText: 'three months',
        durationDays: 90,
        horizonType: 'quarterly',
        startDate: '2026-07-01',
        endDate: '2026-09-28',
        expectedMilestoneCount: 3,
        expectedMinTaskCount: 24,
        expectedWeekCount: 10
      },
      qualityReport: {
        ok: true,
        score: 94,
        totalTasks: 24,
        milestoneCount: 3,
        coveredWeekCount: 10,
        dateSpanDays: 84,
        issues: [],
        metrics: {
          durationDays: 90,
          totalTasks: 24,
          milestoneCount: 3,
          coveredWeekCount: 10,
          dateSpanDays: 84,
          repairAttempted: true,
          fallbackUsed: false,
          qualityStatus: 'repaired',
          sourceType: 'model_knowledge',
          localRelevance: 'low'
        }
      },
      qualityStatus: 'repaired',
      sourceType: 'model_knowledge',
      localRelevance: 'low'
    };

    const html = renderToStaticMarkup(
      <RuntimeProposalPreview
        proposal={proposal}
        isRunning={false}
        status=""
        writing={false}
        firstWrittenDate=""
        onWrite={() => undefined}
        onViewCalendar={() => undefined}
        t={t}
      />
    );

    expect(html).toContain('Plan quality');
    expect(html).toContain('Auto-filled');
    expect(html).toContain('Detected horizon');
    expect(html).toContain('90 days');
    expect(html).toContain('Tasks');
    expect(html).toContain('24');
    expect(html).toContain('Covered weeks');
    expect(html).toContain('10');
    expect(html).toContain('Date span');
    expect(html).toContain('84 days');
    expect(html).toContain('Source');
    expect(html).toContain('Model knowledge');
    expect(html).toContain('automatically completed');
  });
});
