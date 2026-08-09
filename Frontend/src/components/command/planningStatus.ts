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

export function planningNodeTranslationKey(node: string): string {
  const keyByNode: Record<string, string> = {
    session_guard: 'command.planningNodePreparing',
    understanding: 'command.planningNodeUnderstanding',
    understanding_readiness: 'command.planningNodeUnderstandingValidation',
    wait_for_understanding: 'command.planningNodeUnderstandingValidation',
    compile_constraints: 'command.planningNodeConstraints',
    build_context: 'command.planningNodeContext',
    generate_plan: 'command.planningNodePlan',
    validate_plan: 'command.planningNodePlanValidation',
    semantic_review: 'command.planningNodeReview',
    repair_plan: 'command.planningNodeRepair',
    validate_repaired_plan: 'command.planningNodePlanValidation',
    generate_schedule: 'command.planningNodeSchedule',
    validate_schedule: 'command.planningNodeScheduleValidation',
    repair_schedule: 'command.planningNodeScheduleRepair',
    materialize_calendar: 'command.planningNodeCalendar',
    wait_for_final_review: 'command.planningNodeFinalReview',
    feedback_router: 'command.planningNodeFeedback',
    record_learning: 'command.planningNodeLearning',
    calendar_gate: 'command.planningNodeCalendar'
  };
  return keyByNode[node] || 'command.planningNodePreparing';
}
