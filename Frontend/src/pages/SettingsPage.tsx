import { AIWorkspace } from '../components/AIWorkspace';
import { commandAgentActions, useCommandAgent } from '../stores/commandAgentStore';
import type { AppliedPlan, AppData, GoalPlanResponse, Language, Plan, RefinedTask } from '../types';

interface SettingsPageProps {
  data: AppData;
  date: string;
  preferences: string;
  onPreferencesChange: (value: string) => void;
  onApplyGoalPlanToCalendar: (plan: GoalPlanResponse) => Promise<{ created: number; updated: number; failed: number; otherDates: boolean }>;
  onReplanApplied: (plans: AppliedPlan[]) => void;
  onCreateOrUpdateRefinedPlan: (input: { date: string; title: string; sourceKey: string; refinedTask: RefinedTask }) => Promise<Plan>;
  onDeletePlanRefinedTask: (planId: string, date?: string) => Promise<Plan>;
  language: Language;
  t: (key: string) => string;
}

export function SettingsPage(props: SettingsPageProps) {
  const command = useCommandAgent();
  return (
    <section className="page-stack">
      <AIWorkspace {...props} section="settings" />
      <section className="surface advanced-debug-settings" aria-labelledby="advanced-debug-title">
        <div className="settings-title">
          <span id="advanced-debug-title">{props.t('legacy.advancedDebugMode')}</span>
          <strong>{command.advancedAgentTrace ? props.t('legacy.enabled') : props.t('legacy.disabled')}</strong>
        </div>
        <p>{props.t('legacy.advancedDebugHint')}</p>
        <label className="advanced-debug-toggle">
          <input
            type="checkbox"
            checked={command.advancedAgentTrace}
            onChange={(event) => commandAgentActions.setAdvancedAgentTrace(event.target.checked)}
          />
          <span>{props.t('legacy.showAgentTrace')}</span>
        </label>
      </section>
    </section>
  );
}
