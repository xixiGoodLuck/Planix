import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { commandAgentActions } from '../stores/commandAgentStore';
import { SettingsPage } from './SettingsPage';

const labels: Record<string, string> = {
  'legacy.goalPlaceholder': 'Learn Python',
  'legacy.advancedDebugMode': 'Advanced debug mode',
  'legacy.advancedDebugHint': 'Show technical planning diagnostics.',
  'legacy.showAgentTrace': 'Show advanced Agent Trace',
  'legacy.enabled': 'Enabled',
  'legacy.disabled': 'Disabled'
};

function t(key: string): string {
  return labels[key] ?? key;
}

describe('SettingsPage advanced diagnostics', () => {
  it('shows the persisted advanced trace toggle off by default', () => {
    commandAgentActions.setAdvancedAgentTrace(false);
    const html = renderToStaticMarkup(
      <SettingsPage
        data={{}}
        date="2026-07-10"
        preferences=""
        onPreferencesChange={() => undefined}
        onApplyGoalPlanToCalendar={async () => ({ created: 0, updated: 0, failed: 0, otherDates: false })}
        onReplanApplied={() => undefined}
        onCreateOrUpdateRefinedPlan={async () => { throw new Error('not used'); }}
        onDeletePlanRefinedTask={async () => { throw new Error('not used'); }}
        language="en-US"
        t={t}
      />
    );
    expect(html).toContain('Advanced debug mode');
    expect(html).toContain('Show advanced Agent Trace');
    expect(html).toContain('Disabled');
    expect(html).toContain('<label class="advanced-debug-toggle"><input type="checkbox"/>');
  });
});
