import { AIWorkspace } from '../components/AIWorkspace';
import type { AppliedPlan, AppData, GoalPlanResponse, Language, Plan, RefinedTask } from '../types';

interface GoalsPageProps {
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

export function GoalsPage(props: GoalsPageProps) {
  return (
    <section className="page-stack">
      <AIWorkspace {...props} section="goals" />
    </section>
  );
}
