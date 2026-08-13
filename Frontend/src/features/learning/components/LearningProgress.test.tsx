import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { LearningProgress } from './LearningProgress';

describe('LearningProgress stage states', () => {
  it('renders every stage as complete after the run completes', () => {
    const html = renderToStaticMarkup(
      <LearningProgress
        status="completed"
        currentStage="completed"
        completedStages={['scope', 'knowledge_generation', 'evidence_generation', 'coverage_analysis', 'gap_completion', 'selection', 'quality']}
        events={[]}
        runStartedAt="2026-08-13T13:00:00.000Z"
        stageStartedAt="2026-08-13T13:10:00.000Z"
        latestEventAt="2026-08-13T13:10:00.000Z"
        providerReady
        connectionMode="idle"
        recoveryExhausted={false}
        onRefresh={vi.fn()}
        onReturnToScope={vi.fn()}
        t={(key) => key}
      />
    );

    expect(html.match(/<li class="complete">/g)).toHaveLength(8);
    expect(html).not.toContain('<li class="active">');
    expect(html).not.toContain('lucide-loader-circle');
  });
});
