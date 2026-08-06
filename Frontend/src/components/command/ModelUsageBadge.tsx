interface ModelRouteAttempt {
  provider?: string;
  model?: string;
  status?: string;
  errorType?: string;
  latencyMs?: number;
}

interface ModelUsage {
  provider?: string;
  model?: string;
  promptTokens?: number;
  completionTokens?: number;
  totalTokens?: number;
  latencyMs?: number;
  mode?: string;
  taskType?: string;
  fallbackUsed?: boolean;
  localFallbackAllowed?: boolean;
  attempts?: ModelRouteAttempt[];
}

interface ModelUsageBadgeProps {
  usage?: unknown;
  t: (key: string) => string;
}

function asUsage(value: unknown): ModelUsage {
  return value && typeof value === 'object' ? value as ModelUsage : {};
}

function usageItems(value: unknown): ModelUsage[] {
  if (Array.isArray(value)) {
    return value.map(asUsage).filter((item) => Object.keys(item).length > 0);
  }
  const item = asUsage(value);
  return Object.keys(item).length ? [item] : [];
}

function taskLabel(taskType: string | undefined, t: (key: string) => string): string {
  switch (taskType) {
    case 'goal_understanding':
      return t('command.usageTaskGoalUnderstanding');
    case 'command_decision':
      return t('command.usageTaskDecision');
    case 'plan_generation':
      return t('command.usageTaskPlanGeneration');
    case 'task_refinement':
      return t('command.usageTaskRefinement');
    case 'calendar_patch':
      return t('command.usageTaskCalendarPatch');
    case 'memory_query':
    case 'note_query':
      return t('command.usageTaskMemoryQuery');
    case 'memory_write':
    case 'note_write':
      return t('command.usageTaskMemoryWrite');
    case 'chat':
      return t('command.usageTaskChat');
    case 'model_knowledge':
      return t('command.usageTaskModelKnowledge');
    case 'settings_test':
      return t('command.usageTaskSettingsTest');
    case 'planning_goal_model':
      return t('command.usageTaskPlanningGoal');
    case 'planning_reality':
      return t('command.usageTaskPlanningReality');
    case 'planning_evidence':
      return t('command.usageTaskPlanningEvidence');
    case 'planning_strategy':
      return t('command.usageTaskPlanningStrategy');
    case 'planning_execution':
      return t('command.usageTaskPlanningExecution');
    case 'planning_critique':
      return t('command.usageTaskPlanningCritique');
    case 'planning_learning':
      return t('command.usageTaskPlanningLearning');
    default:
      return taskType || t('common.unknown');
  }
}

function formatLatency(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

function attemptLabel(attempt: ModelRouteAttempt, t: (key: string) => string): string {
  const provider = attempt.provider || t('common.unknown');
  const model = attempt.model ? ` / ${attempt.model}` : '';
  const label = `${provider}${model}`;
  if (attempt.status === 'success') return `${label} ${t('command.routeSuccess')}`;
  if (attempt.status === 'skipped') {
    const reason = attempt.errorType === 'missing_api_key'
      ? t('command.routeMissingKey')
      : attempt.errorType || t('command.routeSkipped');
    return `${label} ${reason}`;
  }
  return `${label} ${attempt.errorType || t('command.routeFailed')}`;
}

function routeTrace(item: ModelUsage, t: (key: string) => string): string {
  const attempts = Array.isArray(item.attempts) ? item.attempts : [];
  if (!attempts.length) return '';
  const chain = attempts.map((attempt) => attemptLabel(attempt, t)).join(' -> ');
  const suffix = item.mode === 'local_fallback' ? ` -> ${t('command.routeLocalFallback')}` : '';
  return `${t('command.routeTrace')}: ${chain}${suffix}`;
}

function tokenSummary(item: ModelUsage, t: (key: string) => string): string {
  if (typeof item.totalTokens === 'number') {
    return `${item.totalTokens} ${t('command.tokens')}`;
  }
  return t('command.noTokenStatsShort');
}

function detailLine(item: ModelUsage, t: (key: string) => string): string {
  const parts = [
    `${t('command.usageTask')}: ${taskLabel(item.taskType, t)}`,
    `${t('command.model')}: ${item.provider || t('common.unknown')} / ${item.model || t('common.unknown')}`,
  ];
  if (typeof item.promptTokens === 'number' || typeof item.completionTokens === 'number' || typeof item.totalTokens === 'number') {
    parts.push(`${t('command.tokens')}: ${t('command.promptTokens')} ${item.promptTokens ?? '-'}, ${t('command.completionTokens')} ${item.completionTokens ?? '-'}, ${t('command.totalTokens')} ${item.totalTokens ?? '-'}`);
  } else {
    parts.push(t('command.noTokenStats'));
  }
  if (typeof item.latencyMs === 'number') {
    parts.push(`${t('command.latency')}: ${formatLatency(item.latencyMs)}`);
  }
  if (typeof item.fallbackUsed === 'boolean') {
    parts.push(`${t('command.fallbackUsed')}: ${item.fallbackUsed ? t('common.yes') : t('common.no')}`);
  }
  return parts.join(' · ');
}

export function ModelUsageBadge({ usage, t }: ModelUsageBadgeProps) {
  const items = usageItems(usage);
  if (!items.length) return null;
  const localOnly = items.every((item) => item.mode === 'local_fallback');
  const routeLines = items.map((item) => routeTrace(item, t)).filter(Boolean);

  return (
    <div className={`command-inline-card model-usage ${localOnly ? 'local' : ''}`}>
      {localOnly ? (
        <>
          <p>{t('command.localFallbackNoTokens')}</p>
          {routeLines.map((line, index) => <small key={`route-local-${index}`}>{line}</small>)}
        </>
      ) : (
        <>
          <p>
            <strong>{t('command.modelUsage')}:</strong>{' '}
            {items.map((item, index) => `${index ? ' · ' : ''}${taskLabel(item.taskType, t)} ${tokenSummary(item, t)}`).join('')}
          </p>
          {items.map((item, index) => (
            <small key={`${item.taskType || 'usage'}-${index}`}>{detailLine(item, t)}</small>
          ))}
          {routeLines.map((line, index) => <small key={`route-${index}`}>{line}</small>)}
        </>
      )}
    </div>
  );
}
