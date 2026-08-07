import type { CommandThreadMessage } from '../../stores/commandAgentStore';

export type PlanningStatus =
  | 'needs_goal_clarification'
  | 'waiting_understanding_confirmation'
  | 'planning'
  | 'final_revision'
  | 'waiting_final_review'
  | 'waiting_calendar_write_approval'
  | 'written_to_calendar'
  | 'learning_from_feedback'
  | 'MODEL_UNAVAILABLE'
  | 'ARCHIVED';

export type PlanningStage =
  | 'understand_goal'
  | 'confirm_direction'
  | 'design_plan'
  | 'optimize_plan'
  | 'waiting_confirmation'
  | 'write_calendar'
  | 'review_learning';

export const ACTIVE_PLANNING_STATUSES = new Set<PlanningStatus>([
  'needs_goal_clarification',
  'waiting_understanding_confirmation',
  'planning',
  'final_revision',
  'waiting_final_review',
  'waiting_calendar_write_approval',
  'learning_from_feedback',
  'MODEL_UNAVAILABLE'
]);

function validStatus(value: unknown): PlanningStatus | undefined {
  if (typeof value !== 'string') return undefined;
  const status = value as PlanningStatus;
  return ACTIVE_PLANNING_STATUSES.has(status) || status === 'written_to_calendar' || status === 'ARCHIVED'
    ? status
    : undefined;
}

export function deriveDeepPlanningStatus(messages: CommandThreadMessage[]): PlanningStatus | undefined {
  let currentSessionId = '';
  let status: PlanningStatus | undefined;
  for (const message of messages) {
    if (message.kind !== 'planning_session_started' && message.kind !== 'planning_session_status') continue;
    const payload = message.payload ?? {};
    const sessionId = typeof payload.sessionId === 'string' ? payload.sessionId : '';
    if (sessionId && sessionId !== currentSessionId) {
      currentSessionId = sessionId;
      status = undefined;
    }
    status = validStatus(payload.status) ?? validStatus(message.content) ?? status;
  }
  return status;
}

export function planningStageFromStatus(status: string | undefined, messages: CommandThreadMessage[] = []): PlanningStage {
  void messages;
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
