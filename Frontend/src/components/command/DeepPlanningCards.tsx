import type { MouseEvent } from 'react';
import { ModelUsageBadge } from './ModelUsageBadge';

type Translator = (key: string) => string;

interface CardProps {
  data?: unknown;
  status?: string;
  planningStatus?: string;
  actionsEnabled?: boolean;
  onSend?: (value: string) => void;
  t: Translator;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {};
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.trim() ? value : fallback;
}

function label(t: Translator, key: string, fallback: string): string {
  const value = t(key);
  return value === key ? fallback : value;
}

function lines(value: unknown): string[] {
  return list(value).map((item) => text(item)).filter(Boolean);
}

function fieldLines(raw: Record<string, unknown>, key: string): string[] {
  return lines(raw[key]);
}

function compact(values: Array<string | null | undefined>): string[] {
  return values.filter((value): value is string => Boolean(value && value.trim()));
}

function itemTitle(item: unknown): string {
  const raw = record(item);
  return text(raw.title, text(raw.summary, ''));
}

function ResourceLine({ value, t }: { value: unknown; t: Translator }) {
  const raw = record(value);
  if (!Object.keys(raw).length) return null;
  return (
    <li>
      <strong>{text(raw.title, t('command.resourceUntitled'))}</strong>
      <small>{text(raw.sourceType)} · {text(raw.section) || text(raw.searchKeyword)}</small>
      {text(raw.useStep) ? <p>{text(raw.useStep)}</p> : null}
      {text(raw.expectedOutput) ? <small>{t('command.expectedOutput')}: {text(raw.expectedOutput)}</small> : null}
      {text(raw.fallbackIfTooHard) ? <small>{t('command.fallbackAdjustment')}: {text(raw.fallbackIfTooHard)}</small> : null}
    </li>
  );
}

export function PlanningSessionStatusCard({ status, t }: CardProps) {
  return (
    <div className="command-inline-card planning-session-status">
      <div className="command-card-heading">
        <strong>{t('command.planningSessionStatus')}</strong>
      </div>
      <p>{status || t('command.running')}</p>
    </div>
  );
}

export function UserNeedContractCard({ data, t }: CardProps) {
  const raw = record(data);
  const slotState = record(raw.slotState);
  const learning = record(slotState.learning);
  const travel = record(slotState.travel);
  const pendingQuestion = record(raw.pendingQuestion);
  const questions = lines(raw.clarificationQuestions);
  const constraints = lines(raw.hardConstraints);
  const missing = lines(raw.missingInformation);
  const slotMissing = lines(slotState.missingSlots);
  const received = compact([
    text(slotState.domain) ? `${t('command.slotDomain')}: ${text(slotState.domain) === 'travel' ? t('command.domainTravel') : t('command.domainLearning')}` : '',
    text(learning.subject) ? `${t('command.slotSubject')}: ${text(learning.subject)}` : '',
    text(learning.currentLevelText) || text(learning.currentLevel) ? `${t('command.slotCurrentLevel')}: ${text(learning.currentLevelText, text(learning.currentLevel))}` : '',
    text(learning.targetLevel) ? `${t('command.slotTargetLevel')}: ${text(learning.targetLevel)}` : '',
    text(learning.dailyTime) ? `${t('command.slotDailyTime')}: ${text(learning.dailyTime)}` : '',
    text(learning.duration) ? `${t('command.slotDuration')}: ${text(learning.duration)}` : '',
    text(learning.purposeText) || text(learning.purpose) ? `${t('command.slotPurpose')}: ${text(learning.purposeText, text(learning.purpose))}` : '',
    text(travel.destination) ? `${t('command.slotDestination')}: ${text(travel.destination)}` : '',
    list(travel.places).length ? `${t('command.slotPlaces')}: ${list(travel.places).map((item) => text(item)).filter(Boolean).join(' / ')}` : '',
    typeof travel.durationDays === 'number' ? `${t('command.slotDurationDays')}: ${travel.durationDays}` : '',
    text(travel.month) ? `${t('command.slotMonth')}: ${text(travel.month)}` : '',
    text(travel.transport) ? `${t('command.slotTransport')}: ${text(travel.transport)}` : '',
    text(travel.budget) ? `${t('command.slotBudget')}: ${text(travel.budget)}` : '',
    text(travel.fitnessLevel) ? `${t('command.slotFitness')}: ${text(travel.fitnessLevel)}` : ''
  ]);
  const nextQuestions = lines(pendingQuestion.questions);
  return (
    <div className="command-inline-card wide user-need-contract">
      <div className="command-card-heading">
        <strong>{t('command.userNeedContract')}</strong>
        <span>{raw.canMoveToDesign ? t('command.canMoveToDesign') : t('command.needsClarification')}</span>
      </div>
      <h3>{text(raw.interpretedGoal, t('command.untitledPlan'))}</h3>
      {text(raw.desiredOutcome) ? <p>{t('command.targetOutcome')}: {text(raw.desiredOutcome)}</p> : null}
      {received.length ? (
        <>
          <strong>{t('command.slotReceived')}</strong>
          <ul className="command-compact-list">{received.map((item) => <li key={item}>{item}</li>)}</ul>
        </>
      ) : null}
      {constraints.length ? (
        <>
          <strong>{t('command.hardConstraints')}</strong>
          <ul className="command-compact-list">{constraints.map((item) => <li key={item}>{item}</li>)}</ul>
        </>
      ) : <p>{t('command.noHardConstraints')}</p>}
      {missing.length ? <p>{t('command.missingInformation')}: {missing.join(' / ')}</p> : null}
      {slotMissing.length ? <p>{t('command.slotMissing')}: {slotMissing.join(' / ')}</p> : null}
      {text(pendingQuestion.questionText) ? <p>{t('command.nextQuestion')}: {text(pendingQuestion.questionText)}</p> : null}
      {questions.length ? (
        <>
          <strong>{t('command.clarificationQuestions')}</strong>
          <ul className="command-compact-list">{questions.map((item) => <li key={item}>{item}</li>)}</ul>
        </>
      ) : null}
      {nextQuestions.length && !questions.length ? (
        <ul className="command-compact-list">{nextQuestions.map((item) => <li key={item}>{item}</li>)}</ul>
      ) : null}
    </div>
  );
}

export function MemoryInsightCard({ data, t }: CardProps) {
  const raw = record(data);
  const hits = record(raw.memoryHits);
  const insights = record(raw.planningInsights);
  const groups = [
    ['preferences', t('command.memoryKindPreference'), list(hits.preferences)],
    ['reviews', t('command.memoryKindReview'), list(hits.reviews)],
    ['planningHistory', t('command.memoryKindPlanningHistory'), list(hits.planningHistory)],
    ['materials', t('command.memoryKindMaterial'), list(hits.materials)],
    ['notes', t('command.memoryKindNote'), list(hits.notes)]
  ] as const;
  const rules = [
    ...lines(insights.userStyleRules),
    ...lines(insights.pastFailureWarnings),
    ...lines(insights.constraintsToRespect)
  ];
  return (
    <div className="command-inline-card wide memory-insight-card">
      <div className="command-card-heading">
        <strong>{t('command.memoryInsightAgent')}</strong>
        <span>{t('command.confidence')}: {typeof raw.confidence === 'number' ? Math.round(raw.confidence * 100) : 0}%</span>
      </div>
      {text(raw.missingMemoryWarning) ? <p>{text(raw.missingMemoryWarning)}</p> : null}
      <div className="command-result-grid">
        {groups.map(([key, label, items]) => (
          <section key={key} className="command-result-section">
            <strong>{label} {items.length}</strong>
            {items.length ? (
              <ul className="command-compact-list">{items.slice(0, 3).map((item, index) => <li key={`${key}-${index}`}>{itemTitle(item)}</li>)}</ul>
            ) : <p>{t('command.noMemoryHits')}</p>}
          </section>
        ))}
      </div>
      {rules.length ? (
        <>
          <strong>{t('command.memoryInfluence')}</strong>
          <ul className="command-compact-list">{rules.slice(0, 6).map((item) => <li key={item}>{item}</li>)}</ul>
        </>
      ) : null}
    </div>
  );
}

export function ResourceBriefCard({ data, t }: CardProps) {
  const raw = record(data);
  const coverage = record(raw.coverage);
  const candidates = list(raw.resourceCandidates);
  return (
    <div className="command-inline-card wide resource-brief-card">
      <div className="command-card-heading">
        <strong>{t('command.resourceIntelligenceAgent')}</strong>
        <span>{text(coverage.status, 'partial')}</span>
      </div>
      <p>{text(coverage.explanation)}</p>
      {list(coverage.missingTopics).length ? <p>{t('command.missingTopics')}: {list(coverage.missingTopics).map((item) => text(item)).join(' / ')}</p> : null}
      <ul className="command-compact-list">
        {candidates.slice(0, 6).map((item, index) => {
          const candidate = record(item);
          return (
            <li key={`${text(candidate.id)}-${index}`}>
              <strong>{text(candidate.title, t('command.resourceUntitled'))}</strong>
              <small>{text(candidate.sourceType)} · {text(candidate.domain)} · {text(candidate.difficulty)}</small>
              <p>{text(candidate.howToUse)}</p>
              {text(candidate.expectedOutput) ? <small>{t('command.expectedOutput')}: {text(candidate.expectedOutput)}</small> : null}
            </li>
          );
        })}
      </ul>
      {!candidates.length ? <p>{t('command.noResourceCandidates')}</p> : null}
    </div>
  );
}

export function PlanDesignProposalCard({ data, onSend, t, planningStatus, actionsEnabled = true }: CardProps) {
  const raw = record(data);
  const phases = list(raw.phases);
  const canAct = actionsEnabled && Boolean(onSend) && (!planningStatus || planningStatus === 'waiting_design_approval' || planningStatus === 'design_revision');
  return (
    <div className="command-inline-card wide plan-design-proposal">
      <div className="command-card-heading">
        <strong>{t('command.planDesignProposal')}</strong>
        <span>{text(raw.status)}</span>
      </div>
      <h3>{text(raw.strategyName, t('command.planDesignProposal'))}</h3>
      <p>{text(raw.designRationale)}</p>
      {fieldLines(raw, 'userBenefits').length ? (
        <ul className="command-compact-list">{fieldLines(raw, 'userBenefits').slice(0, 3).map((item) => <li key={item}>{item}</li>)}</ul>
      ) : null}
      <ul className="command-compact-list">
        {phases.map((phase, index) => {
          const item = record(phase);
          return (
            <li key={`${text(item.title)}-${index}`}>
              <strong>{index + 1}. {text(item.title)}</strong>
              <p>{text(item.purpose)}</p>
              <small>{t('command.expectedOutput')}: {text(item.expectedOutput)}</small>
            </li>
          );
        })}
      </ul>
      {canAct ? (
        <div className="command-row-actions">
          <button type="button" onClick={() => onSend?.(t('command.confirmDesignMessage'))}>{t('command.confirmDesign')}</button>
          <button type="button" onClick={() => onSend?.(t('command.reviseDesignMessage'))}>{t('command.reviseDesign')}</button>
        </div>
      ) : null}
    </div>
  );
}

export function ExecutionPlanDraftCard({ data, onSend, t, planningStatus, actionsEnabled = true }: CardProps) {
  const raw = record(data);
  const tasks = list(raw.tasks);
  const draftStatus = text(raw.status);
  const effectiveStatus = planningStatus || (draftStatus === 'approved' ? 'ready_to_write_calendar' : draftStatus || 'waiting_execution_approval');
  const qualityReport = record(raw.qualityReport);
  const qualityStatus = text(raw.qualityStatus || qualityReport.status);
  const qualityPassed = !qualityStatus || qualityStatus === 'passed';
  const qualityBlockers = lines(qualityReport.blockers);
  const qualityWarnings = lines(qualityReport.warnings);
  const qualitySuggestions = lines(qualityReport.repairSuggestions);
  const qualityScore = typeof qualityReport.score === 'number' ? Math.round(qualityReport.score) : null;
  const canAct = actionsEnabled && Boolean(onSend);
  const toggleAllTasks = (event: MouseEvent<HTMLButtonElement> | undefined, open: boolean) => {
    if (!event?.currentTarget) return;
    const root = event.currentTarget.closest('.execution-plan-draft');
    root?.querySelectorAll<HTMLDetailsElement>('.execution-task-detail').forEach((detail) => {
      detail.open = open;
    });
  };
  return (
    <div className="command-inline-card wide execution-plan-draft">
      <div className="command-card-heading">
        <strong>{t('command.executionPlanDraft')}</strong>
        <span>{tasks.length}</span>
      </div>
      <p>{text(raw.scheduleSummary)}</p>
      <p>{text(raw.resourceCoverageSummary)}</p>
      {qualityStatus ? (
        <div className={`execution-quality-report ${qualityPassed ? 'passed' : 'blocked'}`}>
          <strong>{qualityPassed ? label(t, 'command.executionQualityPassed', '\u6267\u884c\u8ba1\u5212\u8d28\u91cf\u5df2\u901a\u8fc7') : label(t, 'command.executionQualityBlocked', '\u6267\u884c\u8ba1\u5212\u9700\u8981\u5148\u4fee\u590d')}</strong>
          <small>
            {label(t, 'command.executionQualityStatus', '\u8d28\u91cf\u72b6\u6001')}: {qualityStatus}
            {qualityScore !== null ? ` · ${label(t, 'command.executionQualityScore', '\u5206\u6570')}: ${qualityScore}` : ''}
          </small>
          {qualityBlockers.length ? (
            <ul>
              {qualityBlockers.map((item, index) => <li key={`blocker-${index}`}>{item}</li>)}
            </ul>
          ) : null}
          {!qualityBlockers.length && qualityWarnings.length ? (
            <ul>
              {qualityWarnings.map((item, index) => <li key={`warning-${index}`}>{item}</li>)}
            </ul>
          ) : null}
          {qualitySuggestions.length ? <small>{label(t, 'command.executionQualityRepair', '\u4fee\u590d\u5efa\u8bae')}: {qualitySuggestions.join(' / ')}</small> : null}
        </div>
      ) : null}
      {!tasks.length ? <p>{t('command.noExecutionTasks')}</p> : null}
      {tasks.length > 1 ? (
        <div className="command-row-actions compact">
          <button type="button" onClick={(event) => toggleAllTasks(event, true)}>{t('command.expandAll')}</button>
          <button type="button" onClick={(event) => toggleAllTasks(event, false)}>{t('command.collapseAll')}</button>
        </div>
      ) : null}
      <div className="execution-task-list">
        {tasks.map((task, index) => {
          const item = record(task);
          const bundle = record(item.resourceBundle);
          const coverage = record(item.resourceCoverage);
          const criteria = lines(item.acceptanceCriteria);
          const knowledge = lines(item.knowledgePoints);
          return (
            <details className="execution-task-detail" key={`${text(item.title)}-${index}`} open={index === 0}>
              <summary>
                <span>
                  <strong>{index + 1}. {text(item.title)}</strong>
                  <small>{text(item.dueDate, t('command.noDate'))} · {String(item.estimatedMinutes || 0)} {t('command.minutes')} · {text(item.priority)}</small>
                </span>
                <span className="execution-task-compact-meta">
                  <em>{text(coverage.status, 'partial')}</em>
                  {text(item.deliverable) ? <small>{text(item.deliverable)}</small> : null}
                </span>
              </summary>
              <div className="execution-task-body">
                <p>{text(item.whyThisTaskMatters)}</p>
                <dl className="command-result-meta">
                  <div><dt>{t('command.deliverable')}</dt><dd>{text(item.deliverable)}</dd></div>
                  <div><dt>{t('command.acceptanceCriteria')}</dt><dd>{criteria.length ? criteria.join(' / ') : t('command.noAcceptanceCriteria')}</dd></div>
                  <div><dt>{t('command.fallbackAdjustment')}</dt><dd>{text(item.fallbackAdjustment)}</dd></div>
                  <div><dt>{t('command.resourceCoverage')}</dt><dd>{text(coverage.status)} {text(coverage.explanation)}</dd></div>
                </dl>
                {knowledge.length ? <small>{t('command.knowledgePoints')}: {knowledge.join(' / ')}</small> : null}
                <strong>{t('command.whereToLearn')}</strong>
                <ul className="command-compact-list">
                  <ResourceLine value={bundle.primary} t={t} />
                  <ResourceLine value={bundle.support} t={t} />
                  <ResourceLine value={bundle.practice} t={t} />
                  <ResourceLine value={bundle.fallback} t={t} />
                </ul>
              </div>
            </details>
          );
        })}
      </div>
      {effectiveStatus === 'ready_to_write_calendar' ? (
        <div className="command-row-actions">
          <p className="command-action-hint">{qualityPassed ? t('command.executionReadyToWrite') : label(t, 'command.executionQualityCannotWrite', '\u8fd9\u4e2a\u6267\u884c\u8ba1\u5212\u8fd8\u4e0d\u591f\u5177\u4f53\uff0c\u6682\u4e0d\u5efa\u8bae\u5199\u5165\u65e5\u5386\uff0c\u8bf7\u5148\u4fee\u590d\u3002')}</p>
          {canAct && qualityPassed ? <button type="button" onClick={() => onSend?.(t('command.quickWriteCalendarMessage'))}>{t('command.quickWriteCalendar')}</button> : null}
        </div>
      ) : null}
      {effectiveStatus === 'waiting_calendar_write_approval' ? (
        <div className="command-row-actions">
          <p className="command-action-hint">{t('command.waitingCalendarApproval')}</p>
        </div>
      ) : null}
      {effectiveStatus === 'written_to_calendar' ? (
        <div className="command-row-actions">
          <p className="command-action-hint">{t('command.executionWrittenToCalendar')}</p>
        </div>
      ) : null}
      {canAct && !['ready_to_write_calendar', 'waiting_calendar_write_approval', 'written_to_calendar'].includes(effectiveStatus) ? (
        <div className="command-row-actions">
          {qualityPassed && effectiveStatus === 'waiting_execution_approval' ? <button type="button" onClick={() => onSend?.(t('command.confirmExecutionMessage'))}>{t('command.confirmExecution')}</button> : null}
          <button type="button" onClick={() => onSend?.(t('command.feedbackTooHeavyMessage'))}>{t('command.feedbackTooHeavy')}</button>
          <button type="button" onClick={() => onSend?.(t('command.feedbackResourceHardMessage'))}>{t('command.feedbackResourceHard')}</button>
        </div>
      ) : null}
    </div>
  );
}

export function LearningUpdateBadge({ data, t }: CardProps) {
  const raw = record(data);
  const reflection = record(raw.reflection);
  const immediate = record(raw.immediatePatch);
  const learning = record(raw.longTermLearning);
  return (
    <div className="command-inline-card learning-update">
      <div className="command-card-heading">
        <strong>{t('command.learningUpdate')}</strong>
        <span>{text(raw.feedbackType)}</span>
      </div>
      <p>{text(raw.insight)}</p>
      {text(reflection.whatWentWrong) ? <p>{t('command.whatWentWrong')}: {text(reflection.whatWentWrong)}</p> : null}
      {text(reflection.whyItHappened) ? <p>{t('command.whyItHappened')}: {text(reflection.whyItHappened)}</p> : null}
      {text(reflection.howToAvoidNextTime) ? <p>{t('command.reflection')}: {text(reflection.howToAvoidNextTime)}</p> : null}
      {text(immediate.action) ? <p>{t('command.currentPatch')}: {text(immediate.action)} · {text(immediate.instruction)}</p> : null}
      {text(learning.newRule) ? <p>{t('command.longTermLearning')}: {text(learning.newRule)}</p> : null}
    </div>
  );
}

export function AgentDecisionCard({ data, t }: CardProps) {
  const raw = record(data);
  const outputs = lines(raw.outputArtifactIds);
  const inputs = lines(raw.inputArtifactIds);
  const usage = record(raw.modelUsage);
  return (
    <div className={`command-inline-card wide agent-decision ${text(raw.decision)}`}>
      <div className="command-card-heading">
        <strong>{text(raw.agent, t('command.agentDecision'))}</strong>
        <span>{text(raw.decision)}</span>
      </div>
      {text(raw.userVisibleSummary) ? <p>{text(raw.userVisibleSummary)}</p> : null}
      {text(raw.reason) ? <p>{t('command.agentDecisionReason')}: {text(raw.reason)}</p> : null}
      <dl className="command-result-meta">
        {inputs.length ? <div><dt>{t('command.agentInputs')}</dt><dd>{inputs.join(' / ')}</dd></div> : null}
        {outputs.length ? <div><dt>{t('command.agentOutputs')}</dt><dd>{outputs.join(' / ')}</dd></div> : null}
        {typeof raw.confidence === 'number' ? <div><dt>{t('command.confidence')}</dt><dd>{Math.round(raw.confidence * 100)}%</dd></div> : null}
      </dl>
      {Object.keys(usage).length ? <ModelUsageBadge usage={usage} t={t} /> : null}
    </div>
  );
}

export function AgentMessageCard({ data, t }: CardProps) {
  const raw = record(data);
  const payload = record(raw.payloadJson);
  const attempts = list(payload.attempts).map(record);
  return (
    <div className={`command-inline-card wide agent-message ${text(raw.messageType)}`}>
      <div className="command-card-heading">
        <strong>{t('command.agentMessage')}</strong>
        <span>{text(raw.messageType)}</span>
      </div>
      <p>
        {text(raw.fromAgent, t('common.unknown'))} → {text(raw.toAgent, t('common.unknown'))}
      </p>
      {text(raw.reason) ? <p>{t('command.agentMessageReason')}: {text(raw.reason)}</p> : null}
      <small>{t('command.agentMessageResolved')}: {raw.resolved ? t('common.yes') : t('common.no')}</small>
      {text(payload.errorType) ? <small>{t('command.errorType')}: {text(payload.errorType)}</small> : null}
      {attempts.length ? <ul className="command-compact-list">{attempts.map((attempt, index) => <li key={index}>{text(attempt.provider)} / {text(attempt.model)} · {text(attempt.status)}{text(attempt.errorType) ? ` · ${text(attempt.errorType)}` : ''}{typeof attempt.latencyMs === 'number' ? ` · ${String(attempt.latencyMs)}ms` : ''}</li>)}</ul> : null}
      {Object.keys(payload).length && !attempts.length ? <small>{t('command.agentPayload')}: {Object.keys(payload).join(' / ')}</small> : null}
    </div>
  );
}
