import type { CommandThreadMessage } from '../../stores/commandAgentStore';

export type PlanningStatus =
  | 'needs_goal_clarification'
  | 'waiting_design_approval'
  | 'design_revision'
  | 'waiting_execution_approval'
  | 'execution_revision'
  | 'ready_to_write_calendar'
  | 'waiting_calendar_write_approval'
  | 'written_to_calendar'
  | 'learning_from_feedback'
  | 'MODEL_UNAVAILABLE';

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
  'waiting_design_approval',
  'design_revision',
  'waiting_execution_approval',
  'execution_revision',
  'ready_to_write_calendar',
  'waiting_calendar_write_approval',
  'learning_from_feedback',
  'MODEL_UNAVAILABLE'
]);

function payloadOf(message: CommandThreadMessage): Record<string, unknown> {
  return message.payload ?? {};
}

function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {};
}

function validStatus(value: unknown): PlanningStatus | undefined {
  if (typeof value !== 'string') return undefined;
  return ACTIVE_PLANNING_STATUSES.has(value as PlanningStatus) || value === 'written_to_calendar'
    ? value as PlanningStatus
    : undefined;
}

function designStatus(value: unknown): PlanningStatus | undefined {
  if (value === 'waiting_user_approval') return 'waiting_design_approval';
  if (value === 'revision_needed') return 'design_revision';
  return undefined;
}

function executionStatus(value: unknown): PlanningStatus | undefined {
  if (value === 'waiting_user_approval') return 'waiting_execution_approval';
  if (value === 'revision_needed') return 'execution_revision';
  if (value === 'approved') return 'ready_to_write_calendar';
  return undefined;
}

export function deriveDeepPlanningStatus(messages: CommandThreadMessage[]): PlanningStatus | undefined {
  let currentSessionId = '';
  let started: PlanningStatus | undefined;
  let design: PlanningStatus | undefined;
  let execution: PlanningStatus | undefined;
  let explicit: PlanningStatus | undefined;

  for (const message of messages) {
    const payload = payloadOf(message);
    const sessionId = typeof payload.sessionId === 'string' ? payload.sessionId : '';
    if (sessionId && sessionId !== currentSessionId && message.kind?.startsWith('planning_session')) {
      currentSessionId = sessionId;
      started = undefined;
      design = undefined;
      execution = undefined;
      explicit = undefined;
    }
    if (message.kind === 'planning_session_started') {
      started = validStatus(payload.status) ?? validStatus(message.content) ?? started;
    }
    if (message.kind === 'planning_session_status') {
      explicit = validStatus(payload.status) ?? validStatus(message.content) ?? explicit;
    }
    if (message.kind === 'plan_design_proposal') {
      const data = recordOf(payload.data);
      design = designStatus(data.status) ?? 'waiting_design_approval';
    }
    if (message.kind === 'execution_plan_draft') {
      const data = recordOf(payload.data);
      execution = executionStatus(data.status) ?? 'waiting_execution_approval';
    }
    if (message.kind === 'learning_update' && !execution) {
      execution = 'waiting_execution_approval';
    }
  }
  const latest = explicit ?? execution ?? design ?? started;
  return latest && ACTIVE_PLANNING_STATUSES.has(latest) ? latest : undefined;
}

export function planningStageFromStatus(status: string | undefined, messages: CommandThreadMessage[] = []): PlanningStage {
  const kinds = new Set(messages.map((message) => message.kind));
  if (status === 'goal_understood' || status === 'evidence_pending' || status === 'strategy_pending') return 'design_plan';
  if (status === 'execution_pending') return 'waiting_confirmation';
  if (status === 'calendar_pending' || status === 'completed') return 'write_calendar';
  if (status === 'goal_clarification') return 'understand_goal';
  if (status === 'MODEL_UNAVAILABLE') {
    if (kinds.has('execution_blueprint_ready') || kinds.has('execution_plan_draft') || kinds.has('critique_report_ready')) return 'optimize_plan';
    if (kinds.has('strategy_portfolio_ready') || kinds.has('plan_design_proposal')) return 'design_plan';
    if (kinds.has('evidence_pack_ready') || kinds.has('resource_brief') || kinds.has('reality_assessment_ready')) return 'design_plan';
    return 'understand_goal';
  }
  if (status === 'waiting_design_approval') return 'confirm_direction';
  if (status === 'design_revision' || status === 'execution_revision') return 'optimize_plan';
  if (status === 'waiting_execution_approval') return 'waiting_confirmation';
  if (status === 'ready_to_write_calendar' || status === 'waiting_calendar_write_approval' || status === 'written_to_calendar') return 'write_calendar';
  if (status === 'learning_from_feedback') return 'review_learning';
  if (status === 'needs_goal_clarification') return 'understand_goal';

  if (kinds.has('planning_learning_updated') || kinds.has('learning_update')) return 'review_learning';
  if (kinds.has('execution_blueprint_ready') || kinds.has('execution_plan_draft') || kinds.has('critique_report_ready')) return 'waiting_confirmation';
  if (kinds.has('strategy_portfolio_ready') || kinds.has('plan_design_proposal')) return 'confirm_direction';
  if (kinds.has('evidence_pack_ready') || kinds.has('resource_brief')) return 'design_plan';
  return 'understand_goal';
}

export function planningStageTranslationKey(stage: PlanningStage): string {
  return `command.planningStage${stage.split('_').map((part) => part[0].toUpperCase() + part.slice(1)).join('')}`;
}
