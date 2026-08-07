import type { FormEvent } from 'react';
import type { CommandThreadMessage } from '../../stores/commandAgentStore';
import { planningStageFromStatus, planningStageTranslationKey, type PlanningStage } from './deepPlanningStatus';
import { ModelUsageBadge } from './ModelUsageBadge';

type Translator = (key: string) => string;

interface PlanningOverviewCardProps {
  messages: CommandThreadMessage[];
  status?: string;
  sending?: boolean;
  actionsEnabled?: boolean;
  onSend?: (value: string) => void;
  t: Translator;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function localizedText(value: unknown, t: Translator): string {
  const direct = text(value);
  if (direct) return direct;
  const localized = record(value);
  const preferChinese = t('command.knownFacts') === '已知事实';
  return preferChinese
    ? text(localized.zh) || text(localized.en)
    : text(localized.en) || text(localized.zh);
}

function messageData(message: CommandThreadMessage | undefined): Record<string, unknown> {
  const payload = message?.payload ?? {};
  const data = record(payload.data);
  return Object.keys(data).length ? data : payload;
}

function latest(messages: CommandThreadMessage[], kind: CommandThreadMessage['kind']): Record<string, unknown> {
  return messageData([...messages].reverse().find((message) => message.kind === kind));
}

function latestKindPayloadField(
  messages: CommandThreadMessage[],
  kind: CommandThreadMessage['kind'],
  field: string
): unknown {
  const message = [...messages].reverse().find((item) => item.kind === kind);
  if (!message) return undefined;
  const payload = message.payload ?? {};
  const data = record(payload.data);
  return payload[field] !== undefined ? payload[field] : data[field];
}

function itemText(value: unknown): string {
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  const raw = record(value);
  const primary = text(raw.statement) || text(raw.description) || text(raw.warning) || text(raw.message) || text(raw.question) || text(raw.field) || text(raw.name) || text(raw.title);
  const impact = text(raw.impact) || text(raw.whyItChangesThePlan) || text(raw.whyThisQuestionMatters) || text(raw.consequence);
  return [primary, impact].filter(Boolean).join(' — ');
}

function listText(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map(itemText).filter(Boolean);
}

function factLines(value: unknown): string[] {
  if (Array.isArray(value)) return listText(value);
  return Object.entries(record(value)).flatMap(([key, fact]) => {
    if (Array.isArray(fact)) {
      const joined = fact.map(itemText).filter(Boolean).join(' / ');
      return joined ? [`${key}: ${joined}`] : [];
    }
    const rendered = itemText(fact);
    return rendered ? [`${key}: ${rendered}`] : [];
  });
}

function userFieldLabel(key: string, t: Translator): string {
  const labels: Record<string, string> = {
    location: t('command.planningFactLocation'),
    locations: t('command.planningFactLocation'),
    subject: t('command.planningFactGoal'),
    target: t('command.planningFactGoal'),
    targetSkill: t('command.planningFactSkill'),
    skill: t('command.planningFactSkill'),
    skills: t('command.planningFactSkill'),
    background: t('command.planningFactBackground'),
    experience: t('command.planningFactBackground'),
    currentKnowledge: t('command.planningFactBackground'),
    currentLevel: t('command.planningFactCurrentLevel'),
    availableTime: t('command.planningFactAvailableTime'),
    time: t('command.planningFactAvailableTime'),
    timeBudget: t('command.planningFactAvailableTime'),
    weeklyHours: t('command.planningFactAvailableTime'),
    timeCommitmentExpression: t('command.planningFactAvailableTime'),
    timeExpressions: t('command.planningFactAvailableTime'),
    durationExpression: t('command.planningFactDuration'),
    durationExpressions: t('command.planningFactDuration'),
    dateExpression: t('command.planningFactDate'),
    dateExpressions: t('command.planningFactDate'),
    budget: t('command.planningFactBudget'),
    budgetExpression: t('command.planningFactBudget'),
    constraint: t('command.planningFactConstraints'),
    constraints: t('command.planningFactConstraints'),
    hardConstraint: t('command.planningFactConstraints'),
    purpose: t('command.planningFactPurpose'),
    purposes: t('command.planningFactPurpose')
  };
  return labels[key] || '';
}

function userFactLines(value: unknown, t: Translator): string[] {
  if (Array.isArray(value)) return listText(value);
  return Object.entries(record(value)).flatMap(([key, fact]) => {
    const factLabel = userFieldLabel(key, t);
    const rendered = Array.isArray(fact)
      ? fact.map(itemText).filter(Boolean).join(' / ')
      : itemText(fact);
    if (!rendered) return [];
    return [factLabel ? `${factLabel}: ${rendered}` : rendered];
  });
}

function semanticFactLines(value: unknown, t: Translator): string[] {
  if (!Array.isArray(value)) return userFactLines(value, t);
  return value.flatMap((fact) => {
    const raw = record(fact);
    const rendered = itemText(fact);
    if (!rendered) return [];
    const label = userFieldLabel(text(raw.key), t);
    return [label ? `${label}: ${rendered}` : rendered];
  });
}

function userUncertaintyLines(value: unknown, t: Translator): string[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (typeof item === 'string') return item.trim() ? [item.trim()] : [];
    const raw = record(item);
    const label = userFieldLabel(text(raw.field), t);
    const impact = text(raw.impact) || text(raw.whyItChangesThePlan) || text(raw.whyThisQuestionMatters) || text(raw.consequence);
    const description = text(raw.description) || text(raw.message) || text(raw.question);
    const rendered = [label, impact || description].filter(Boolean).join(' — ');
    return rendered ? [rendered] : [];
  });
}

function modelFailureAttemptLines(value: unknown, t: Translator): string[] {
  if (!Array.isArray(value)) return [];
  const errorKeys: Record<string, string> = {
    model_output_truncated: 'command.planningFailureOutputTruncated',
    auth_error: 'command.planningFailureAuthError',
    authentication_error: 'command.planningFailureAuthError',
    bad_base_url: 'command.planningFailureBadBaseUrl',
    bad_model: 'command.planningFailureBadModel',
    bad_request: 'command.planningFailureBadRequest',
    insufficient_balance: 'command.planningFailureInsufficientBalance',
    invalid_key_format: 'command.planningFailureInvalidKeyFormat',
    invalid_model_output: 'command.planningFailureInvalidModelOutput',
    missing_api_key: 'command.planningFailureMissingKey',
    missing_key: 'command.planningFailureMissingKey',
    network_error: 'command.planningFailureNetworkError',
    rate_limit: 'command.planningFailureRateLimit',
    timeout: 'command.planningFailureTimeout',
    unknown: 'command.planningFailureUnknown',
    provider_unavailable: 'command.planningFailureProviderUnavailable',
    unavailable: 'command.planningFailureProviderUnavailable'
  };
  const providerLabels: Record<string, string> = {
    deepseek: 'DeepSeek',
    zhipu_glm: 'GLM',
    kimi: 'Kimi',
    openai: 'OpenAI',
    custom: 'Custom',
    mock: 'Mock'
  };
  return value.flatMap((attempt) => {
    const raw = record(attempt);
    const status = text(raw.status);
    if (status === 'success') return [];
    const rawProvider = text(raw.provider).slice(0, 80);
    if (!rawProvider) return [];
    const provider = providerLabels[rawProvider.toLowerCase()] || rawProvider;
    const errorType = text(raw.errorType) || status;
    const key = errorKeys[errorType] || 'command.planningFailureRequestFailed';
    return [`${provider}: ${t(key)}`];
  });
}

export function ClarificationChoices({
  options,
  disabled,
  onSend,
  t
}: {
  options: string[];
  disabled: boolean;
  onSend?: (value: string) => void;
  t: Translator;
}) {
  const submitOther = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const answer = String(new FormData(form).get('otherAnswer') || '').trim();
    if (!answer || disabled || !onSend) return;
    onSend(answer);
    form.reset();
    form.closest('details')?.removeAttribute('open');
  };

  return (
    <div className="planning-clarification-choices">
      <small>{t('command.clarificationChoiceHint')}</small>
      <div className="planning-clarification-buttons">
        {options.map((option, index) => (
          <button
            type="button"
            disabled={disabled}
            key={`${option}-${index}`}
            onClick={() => onSend?.(option)}
          >
            <span>{String.fromCharCode(65 + index)}</span>
            {option}
          </button>
        ))}
      </div>
      <details className="planning-clarification-details">
        <summary
          aria-disabled={disabled}
          onClick={(event) => {
            if (disabled) event.preventDefault();
          }}
        >
          {t('command.clarificationOther')}
        </summary>
        <form className="planning-clarification-other" onSubmit={submitOther}>
          <input
            name="otherAnswer"
            disabled={disabled}
            maxLength={500}
            aria-label={t('command.clarificationOtherPlaceholder')}
            placeholder={t('command.clarificationOtherPlaceholder')}
          />
          <button type="submit" disabled={disabled}>
            {t('command.clarificationSubmit')}
          </button>
        </form>
      </details>
    </div>
  );
}

function nextActionKey(stage: PlanningStage): string {
  const suffix = stage.split('_').map((part) => part[0].toUpperCase() + part.slice(1)).join('');
  return `command.planningNext${suffix}`;
}

export function PlanningOverviewCard({
  messages,
  status,
  sending = false,
  actionsEnabled = true,
  onSend,
  t
}: PlanningOverviewCardProps) {
  const understanding = latest(messages, 'goal_understanding');
  const planningPhase = text(latestKindPayloadField(messages, 'planning_session_status', 'planningPhase'));
  const planningUnderstanding = record(latestKindPayloadField(messages, 'planning_session_status', 'understandingSnapshot'));
  const planningPlan = record(latestKindPayloadField(messages, 'planning_session_status', 'planBlueprint'));
  const planningPlanQuality = record(latestKindPayloadField(messages, 'planning_session_status', 'planQualityReport'));
  const planningSchedule = record(latestKindPayloadField(messages, 'planning_session_status', 'scheduleBlueprint'));
  const planningScheduleQuality = record(latestKindPayloadField(messages, 'planning_session_status', 'scheduleQualityReport'));
  const planningCalendar = record(latestKindPayloadField(messages, 'planning_session_status', 'calendarProposal'));
  const businessStatus = text(latestKindPayloadField(messages, 'planning_session_status', 'businessStatus'));
  const runtimeStatus = text(latestKindPayloadField(messages, 'planning_session_status', 'runtimeStatus'));
  const modelFailure = record(latestKindPayloadField(messages, 'planning_session_status', 'modelFailure'));
  const pendingInput = record(latestKindPayloadField(messages, 'planning_session_status', 'pendingInput'));
  const stableStatus = status && status !== 'MODEL_UNAVAILABLE' ? status : businessStatus;
  const stage: PlanningStage = planningPhase === 'UNDERSTANDING'
    ? 'understand_goal'
    : planningPhase === 'FINAL_REVIEW'
      ? 'waiting_confirmation'
    : planningStageFromStatus(stableStatus || status, messages);
  const modelBlocked = Object.keys(modelFailure).length > 0
    || status === 'MODEL_UNAVAILABLE'
    || runtimeStatus === 'blocked_model'
    || runtimeStatus === 'blocked_model_unavailable'
    || runtimeStatus === 'retry_required';

  const understoodIntent = text(understanding.understoodIntent)
    || text(record(understanding.understoodIntent).summary)
    || text(record(understanding.understoodIntent).goal);
  const goalStatement = text(planningUnderstanding.goalSummary)
    || understoodIntent
    || t('command.planningUnderstandingPending');
  const facts = Array.from(new Set([
    ...semanticFactLines(planningUnderstanding.facts, t),
    ...listText(planningUnderstanding.constraints).map((item) => `${t('command.planningFactConstraints')}: ${item}`),
    ...listText(planningUnderstanding.preferences),
    ...listText(planningUnderstanding.successSignals),
    ...listText(planningUnderstanding.assumptions),
    ...(modelBlocked ? [] : userFactLines(understanding.knownFacts, t))
  ]));
  const warnings = listText(understanding.consistencyWarnings);
  const blockingUnknowns = listText(planningUnderstanding.unknowns).length
    ? listText(planningUnderstanding.unknowns)
    : userUncertaintyLines(understanding.uncertainties, t);
  const pendingInputText = pendingInput.applied === false ? text(pendingInput.text) : '';
  const visibleBlockingUnknowns = modelBlocked
    ? [pendingInputText
        ? `${t('command.planningPendingModelInput')}: ${pendingInputText}`
        : t('command.planningPendingModelInputFallback')]
    : blockingUnknowns;
  const clarificationOptions = listText(understanding.clarificationOptions).slice(0, 4);
  const showClarificationChoices = stage === 'understand_goal'
    && !modelBlocked
    && clarificationOptions.length >= 2;
  const clarificationDisabled = !actionsEnabled || !onSend || sending;
  const modelFailureSummary = localizedText(modelFailure.summary, t)
    || t('command.planningFailureFallbackSummary');
  const modelFailureAction = localizedText(modelFailure.action, t)
    || t('command.planningRuntimeWaitingModel');
  const modelFailureAttempts = modelFailureAttemptLines(modelFailure.attempts, t);
  const modelRetryable = typeof modelFailure.retryable === 'boolean' ? modelFailure.retryable : true;
  const showRetryControl = modelBlocked && modelRetryable && actionsEnabled;
  const retryDisabled = sending || !onSend;

  const nextQuestion = text(record(planningUnderstanding.nextQuestion).question)
    || text(planningUnderstanding.nextQuestion)
    || text(understanding.nextQuestion);
  const nextAction = modelBlocked
    ? modelFailureAction
    : status === 'waiting_understanding_confirmation'
      ? t('command.confirmUnderstanding')
      : status === 'waiting_final_review'
        ? t('command.approveFinalPlan')
        : nextQuestion || blockingUnknowns[0] || t(nextActionKey(stage));

  return (
    <div className="command-inline-card wide planning-overview-card">
      <h2 className="planning-workspace-title">{t('command.planningWorkspace')}</h2>
      <header className="planning-overview-stage">
        <span>{t('command.currentStage')}</span>
        <strong>{t(planningStageTranslationKey(stage))}</strong>
      </header>
      <section>
        <h3>{t(planningPhase === 'FINAL_REVIEW' ? 'command.confirmedGoal' : 'command.goalUnderstanding')}</h3>
        <p className="planning-overview-goal">{goalStatement}</p>
      </section>
      <section>
        <h3>{t(modelBlocked
          ? 'command.lastConfirmedKnownFacts'
          : 'command.knownFacts')}</h3>
        {facts.length
          ? <ul>{facts.slice(0, 8).map((fact, index) => <li key={`${fact}-${index}`}>{fact}</li>)}</ul>
          : <p>{t('command.noKnownFacts')}</p>}
      </section>
      <section>
        <h3>{t('command.importantUnknowns')}</h3>
        {!modelBlocked && warnings.length ? (
          <div className="planning-consistency-warning" role="alert">
            <strong>{t('command.consistencyWarning')}</strong>
            <ul>{warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul>
          </div>
        ) : null}
        {visibleBlockingUnknowns.length
          ? <ul>{visibleBlockingUnknowns.map((unknown, index) => <li key={`${unknown}-${index}`}>{unknown}</li>)}</ul>
          : <p>{t('command.noBlockingUnknowns')}</p>}
      </section>
      {planningPhase === 'FINAL_REVIEW' ? (
        <>
          <section>
            <h3>{t('command.planMilestonesAndTasks')}</h3>
            {Array.isArray(planningPlan.tasks) && planningPlan.tasks.length ? (
              <ul>{planningPlan.tasks.map((item, index) => {
                const task = record(item);
                const title = text(task.title) || text(task.id);
                const deliverable = text(task.deliverable);
                return <li key={`${text(task.id)}-${index}`}>{[title, deliverable].filter(Boolean).join(' — ')}</li>;
              })}</ul>
            ) : <p>{t('common.unknown')}</p>}
          </section>
          <section>
            <h3>{t('command.schedulePreview')}</h3>
            {Array.isArray(planningSchedule.sessions) && planningSchedule.sessions.length ? (
              <ul>{planningSchedule.sessions.slice(0, 12).map((item, index) => {
                const session = record(item);
                return <li key={`${text(session.id)}-${index}`}>{text(session.start)} · {String(session.durationMinutes ?? '')} min</li>;
              })}</ul>
            ) : <p>{t('common.unknown')}</p>}
          </section>
          <section>
            <h3>{t('command.planQuality')}</h3>
            <p>{planningPlanQuality.hardRulesPassed === true && !listText(planningPlanQuality.issues).length
              ? t('command.qualityPassed')
              : t('command.qualityNeedsAttention')}</p>
            {listText(planningPlanQuality.issues).length
              ? <ul>{listText(planningPlanQuality.issues).map((issue, index) => <li key={`${issue}-${index}`}>{issue}</li>)}</ul>
              : null}
          </section>
          <section>
            <h3>{t('command.scheduleQuality')}</h3>
            <p>{planningScheduleQuality.hardRulesPassed === true && !listText(planningScheduleQuality.issues).length
              ? t('command.qualityPassed')
              : t('command.qualityNeedsAttention')}</p>
            {listText(planningScheduleQuality.issues).length
              ? <ul>{listText(planningScheduleQuality.issues).map((issue, index) => <li key={`${issue}-${index}`}>{issue}</li>)}</ul>
              : null}
          </section>
          <section>
            <h3>{t('command.calendarPreview')}</h3>
            {Array.isArray(planningCalendar.events) && planningCalendar.events.length ? (
              <ul>{planningCalendar.events.slice(0, 12).map((item, index) => {
                const event = record(item);
                return <li key={`${text(event.sourceKey)}-${index}`}>{text(event.start)} · {text(event.title)}</li>;
              })}</ul>
            ) : <p>{t('common.unknown')}</p>}
          </section>
        </>
      ) : null}
      <section className="planning-next-action">
        <h3>{t('command.nextAction')}</h3>
        {modelBlocked ? (
          <div className="planning-model-failure" role="alert">
            <p>{modelFailureSummary}</p>
            {modelFailureAttempts.length ? (
              <ul>{modelFailureAttempts.map((attempt, index) => <li key={`${attempt}-${index}`}>{attempt}</li>)}</ul>
            ) : null}
            {modelFailure.automaticRetryAttempted === true
              ? <small>{t('command.planningAutomaticRetryAttempted')}</small>
              : null}
          </div>
        ) : null}
        <p>{nextAction}</p>
        {showClarificationChoices ? (
          <ClarificationChoices
            key={nextAction}
            options={clarificationOptions}
            disabled={clarificationDisabled}
            onSend={onSend}
            t={t}
          />
        ) : null}
        {showRetryControl ? (
          <div className="planning-retry-control">
            <button
              type="button"
              disabled={retryDisabled}
              onClick={() => onSend?.(t('command.retryDeepPlanningMessage'))}
            >
              {t('command.retryCurrentPlanningStage')}
            </button>
          </div>
        ) : null}
      </section>
    </div>
  );
}

export function GoalUnderstandingDetailCard({ data, t }: { data?: unknown; t: Translator }) {
  const raw = record(data);
  const facts = factLines(raw.knownFacts);
  const uncertainties = listText(raw.uncertainties);
  const warnings = listText(raw.consistencyWarnings);
  const error = typeof raw.error === 'string' ? raw.error : Object.keys(record(raw.error)).length ? JSON.stringify(raw.error) : '';
  return (
    <div className="command-inline-card wide goal-understanding-trace">
      <div className="command-card-heading">
        <strong>{t('command.goalUnderstanding')}</strong>
        <span>{text(raw.intentState) || t('common.unknown')}</span>
      </div>
      {text(raw.understoodIntent) ? <p>{text(raw.understoodIntent)}</p> : null}
      {facts.length ? <dl className="command-result-meta"><div><dt>{t('command.knownFacts')}</dt><dd>{facts.join(' / ')}</dd></div></dl> : null}
      {uncertainties.length ? <p>{t('command.uncertainties')}: {uncertainties.join(' / ')}</p> : null}
      {warnings.length ? <p>{t('command.consistencyWarning')}: {warnings.join(' / ')}</p> : null}
      {text(raw.nextQuestion) ? <p>{t('command.nextAction')}: {text(raw.nextQuestion)}</p> : null}
      <small>{t('command.source')}: {text(raw.source) || t('common.unknown')} · {t('command.confidence')}: {typeof raw.confidence === 'number' ? `${Math.round(raw.confidence * 100)}%` : '-'}</small>
      {error ? <small>{t('command.errorType')}: {error}</small> : null}
      {raw.modelUsage ? <ModelUsageBadge usage={raw.modelUsage} t={t} /> : null}
    </div>
  );
}
