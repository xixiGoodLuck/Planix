export type PlanningStage =
  | 'understand_goal'
  | 'confirm_direction'
  | 'design_plan'
  | 'optimize_plan'
  | 'waiting_confirmation'
  | 'write_calendar'
  | 'review_learning';

export function planningStageFromStatus(status: string | undefined): PlanningStage {
  if (status === 'goal_clarification' || status === 'needs_goal_clarification') return 'understand_goal';
  if (status === 'waiting_understanding_confirmation') return 'confirm_direction';
  if (status === 'goal_understood' || status === 'planning') return 'design_plan';
  if (status === 'final_revision') return 'optimize_plan';
  if (status === 'waiting_final_review') return 'waiting_confirmation';
  if (status === 'calendar_pending' || status === 'completed' || status === 'waiting_calendar_write_approval' || status === 'written_to_calendar') return 'write_calendar';
  if (status === 'learning_from_feedback' || status === 'ARCHIVED') return 'review_learning';
  return 'understand_goal';
}

export function planningStageTranslationKey(stage: PlanningStage): string {
  return `command.planningStage${stage.split('_').map((part) => part[0].toUpperCase() + part.slice(1)).join('')}`;
}
