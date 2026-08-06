import { useEffect, useState, type ReactNode } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import type { CommandThreadMessage } from '../../stores/commandAgentStore';
import type { PlanHorizon, PlanQualityReport, PlanQualityStatus, PlanSourceType } from '../../types';
import { ApprovalCard } from './ApprovalCard';
import { CalendarPlanPreviewCard } from './CalendarPlanPreviewCard';
import { CalendarWriteResultCard } from './CalendarWriteResultCard';
import { CommandDecisionCard } from './CommandDecisionCard';
import {
  CritiqueReportCard,
  EvidencePackCard,
  ExecutionBlueprintCard,
  GoalModelCard,
  ModelUnavailableCard,
  PlanningLearningUpdateCard,
  RealityAssessmentCard,
  StrategyPortfolioCard
} from './CognitivePlanningCards';
import {
  AgentDecisionCard,
  AgentMessageCard,
  ExecutionPlanDraftCard,
  LearningUpdateBadge,
  MemoryInsightCard,
  PlanDesignProposalCard,
  PlanningSessionStatusCard,
  ResourceBriefCard,
  UserNeedContractCard
} from './DeepPlanningCards';
import { ExecutionMiniCard } from './ExecutionMiniCard';
import { InlinePlanDetailCard } from './InlinePlanDetailCard';
import { InlinePlanSummaryCard } from './InlinePlanSummaryCard';
import { MemorySearchResultsCard } from './MemorySearchResultsCard';
import { MemoryWritePreviewCard } from './MemoryWritePreviewCard';
import { MemoryWriteResultCard } from './MemoryWriteResultCard';
import { ModelUsageBadge } from './ModelUsageBadge';
import { NoteSearchResultsCard } from './NoteSearchResultsCard';
import { NoteWritePreviewCard } from './NoteWritePreviewCard';
import { NoteWriteResultCard } from './NoteWriteResultCard';
import { PlanPatchPreviewCard } from './PlanPatchPreviewCard';
import { PlanPatchResultCard } from './PlanPatchResultCard';
import { PlanSearchResultsCard } from './PlanSearchResultsCard';
import { RefinedTasksResultCard } from './RefinedTasksResultCard';
import { GoalCompletionDetailCard, GoalUnderstandingDetailCard, PlanningOverviewCard } from './PlanningOverviewCard';

interface AgentThreadProps {
  messages: CommandThreadMessage[];
  sending: boolean;
  onApprove: (actionId: string, decision: 'approve' | 'reject') => void;
  onSend: (value: string) => void;
  advancedAgentTrace?: boolean;
  t: (key: string) => string;
}

function payloadOf(message: CommandThreadMessage): Record<string, unknown> {
  return message.payload ?? {};
}

const planningCardKinds = new Set<CommandThreadMessage['kind']>([
  'planning_session_started',
  'user_need_contract',
  'memory_insight_brief',
  'resource_brief',
  'plan_design_proposal',
  'execution_plan_draft',
  'learning_update',
  'agent_decision',
  'agent_message',
  'planning_session_status',
  'goal_understanding',
  'goal_completion_updated',
  'goal_model_updated',
  'reality_assessment_ready',
  'evidence_pack_ready',
  'strategy_portfolio_ready',
  'execution_blueprint_ready',
  'critique_report_ready',
  'planning_learning_updated'
]);

const cognitiveWorkspaceKinds = new Set<CommandThreadMessage['kind']>([
  'goal_understanding',
  'goal_completion_updated',
  'goal_model_updated',
  'reality_assessment_ready',
  'evidence_pack_ready',
  'strategy_portfolio_ready',
  'execution_blueprint_ready',
  'critique_report_ready',
  'planning_learning_updated'
]);

const technicalPlanningKinds = new Set<CommandThreadMessage['kind']>([
  'planning_session_started',
  'planning_session_status',
  'agent_decision',
  'agent_message'
]);

const planningWorkspaceDetailKinds: CommandThreadMessage['kind'][] = [
  'strategy_portfolio_ready',
  'execution_blueprint_ready',
  'critique_report_ready'
];

type RenderItem =
  | { type: 'message'; message: CommandThreadMessage }
  | { type: 'execution_group'; id: string; messages: CommandThreadMessage[] }
  | { type: 'usage_group'; id: string; messages: CommandThreadMessage[] }
  | { type: 'planning_group'; id: string; messages: CommandThreadMessage[] };

function groupMessages(messages: CommandThreadMessage[]): RenderItem[] {
  const items: RenderItem[] = [];
  let executionGroup: CommandThreadMessage[] = [];
  let usageGroup: CommandThreadMessage[] = [];
  let planningGroup: CommandThreadMessage[] = [];

  const flushExecutionGroup = () => {
    if (!executionGroup.length) return;
    items.push({
      type: 'execution_group',
      id: executionGroup[0].id,
      messages: executionGroup
    });
    executionGroup = [];
  };
  const flushUsageGroup = () => {
    if (!usageGroup.length) return;
    items.push({
      type: 'usage_group',
      id: usageGroup[0].id,
      messages: usageGroup
    });
    usageGroup = [];
  };
  const flushPlanningGroup = () => {
    if (!planningGroup.length) return;
    items.push({
      type: 'planning_group',
      id: planningGroup[0].id,
      messages: planningGroup
    });
    planningGroup = [];
  };

  for (const message of messages) {
    if (message.role === 'card' && message.kind === 'runtime') {
      flushUsageGroup();
      flushPlanningGroup();
      executionGroup.push(message);
      continue;
    }
    flushExecutionGroup();
    if (message.role === 'card' && message.kind === 'model_usage') {
      flushPlanningGroup();
      usageGroup.push(message);
      continue;
    }
    flushUsageGroup();
    if (message.role === 'card' && planningCardKinds.has(message.kind)) {
      planningGroup.push(message);
      continue;
    }
    flushPlanningGroup();
    items.push({ type: 'message', message });
  }
  flushExecutionGroup();
  flushUsageGroup();
  flushPlanningGroup();
  return items;
}

function planningSessionId(message: CommandThreadMessage): string {
  const value = payloadOf(message).sessionId;
  return typeof value === 'string' ? value : '';
}

function livePlanningMessages(messages: CommandThreadMessage[]): CommandThreadMessage[] {
  const planningMessages = messages.filter((message) => message.role === 'card' && planningCardKinds.has(message.kind));
  if (!planningMessages.length) return [];

  const latestSessionId = [...planningMessages].reverse().map(planningSessionId).find(Boolean) || '';
  if (!latestSessionId) return planningMessages;

  const scoped = planningMessages.filter((message) => planningSessionId(message) === latestSessionId);
  const firstScopedIndex = messages.findIndex((message) => planningSessionId(message) === latestSessionId && planningCardKinds.has(message.kind));
  if (firstScopedIndex < 0) return scoped;

  for (let index = firstScopedIndex - 1; index >= 0; index -= 1) {
    const candidate = messages[index];
    const candidateSessionId = planningSessionId(candidate);
    if (candidateSessionId && candidateSessionId !== latestSessionId && planningCardKinds.has(candidate.kind)) break;
    if (candidate.kind === 'goal_understanding' && !candidateSessionId) {
      return [candidate, ...scoped];
    }
  }
  return scoped;
}

function ExecutionGroupCard({ messages, t }: { messages: CommandThreadMessage[]; t: (key: string) => string }) {
  const last = messages[messages.length - 1];
  const status = messages.some((message) => message.status === 'error')
    ? 'error'
    : last?.status === 'running'
      ? 'running'
      : 'success';
  const [expanded, setExpanded] = useState(status === 'running');

  useEffect(() => {
    if (status !== 'running') {
      setExpanded(false);
    } else {
      setExpanded(true);
    }
  }, [status]);

  return (
    <div className={`command-inline-card execution-group ${status}`}>
      <button
        type="button"
        className="command-execution-toggle"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        title={expanded ? t('command.collapseExecution') : t('command.expandExecution')}
      >
        <span className="command-execution-title">
          <strong>{t('command.execution')}</strong>
          <small>{messages.length} {t('command.executionSteps')}</small>
        </span>
        <span className="command-execution-arrow" aria-hidden="true">
          {expanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
        </span>
        <em>
          <i aria-hidden="true" />
          {status === 'success' ? t('command.statusSuccess') : status === 'error' ? t('command.statusError') : t('command.statusRunning')}
        </em>
      </button>
      {!expanded && last && <p>{last.title ? `${last.title}: ${last.content}` : last.content}</p>}
      {expanded && (
        <div className="command-execution-items">
          {messages.map((message) => (
            <ExecutionMiniCard
              key={message.id}
              title={message.title}
              content={message.content}
              status={message.status}
              t={t}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {};
}

function planningKindLabel(kind: CommandThreadMessage['kind'], t: (key: string) => string): string {
  if (kind === 'goal_understanding') return t('command.goalUnderstanding');
  if (kind === 'goal_completion_updated') return t('command.goalCompletion');
  if (kind === 'goal_model_updated') return t('command.cognitiveGoalModel');
  if (kind === 'reality_assessment_ready') return t('command.cognitiveReality');
  if (kind === 'evidence_pack_ready') return t('command.cognitiveEvidence');
  if (kind === 'strategy_portfolio_ready') return t('command.cognitiveStrategyPortfolio');
  if (kind === 'execution_blueprint_ready') return t('command.cognitiveExecutionBlueprint');
  if (kind === 'critique_report_ready') return t('command.cognitiveCritique');
  if (kind === 'planning_learning_updated') return t('command.cognitiveLearning');
  if (kind === 'user_need_contract') return t('command.userNeedContract');
  if (kind === 'memory_insight_brief') return t('command.memoryInsightAgent');
  if (kind === 'resource_brief') return t('command.resourceIntelligenceAgent');
  if (kind === 'plan_design_proposal') return t('command.planDesignProposal');
  if (kind === 'execution_plan_draft') return t('command.executionPlanDraft');
  if (kind === 'learning_update') return t('command.learningUpdate');
  if (kind === 'agent_decision') return t('command.agentDecision');
  if (kind === 'agent_message') return t('command.agentMessage');
  return t('command.planningSessionStatus');
}

function planningCardSummary(message: CommandThreadMessage, t: (key: string) => string): string {
  const payload = payloadOf(message);
  const data = recordOf(payload.data);
  if (message.kind === 'goal_understanding') return String(payload.understoodIntent || payload.nextQuestion || message.content || '');
  if (message.kind === 'goal_completion_updated') return String(data.nextStage || message.content || '');
  if (message.kind === 'goal_model_updated') return String(data.goalStatement || message.content || '');
  if (message.kind === 'reality_assessment_ready') return String(data.feasibilitySummary || message.content || '');
  if (message.kind === 'evidence_pack_ready') return String(data.synthesis || message.content || '');
  if (message.kind === 'strategy_portfolio_ready') return String(data.recommendationReason || message.content || '');
  if (message.kind === 'execution_blueprint_ready') {
    const tasks = Array.isArray(data.tasks) ? data.tasks.length : 0;
    return `${tasks} ${t('command.tasks')} · ${String(data.resourceCoverage || '')}`;
  }
  if (message.kind === 'critique_report_ready') return `${String(data.status || '')} · ${String(data.score || 0)}`;
  if (message.kind === 'planning_learning_updated') return String(data.originalFeedback || message.content || '');
  if (message.kind === 'user_need_contract') {
    return String(data.interpretedGoal || message.content || '');
  }
  if (message.kind === 'memory_insight_brief') {
    const hits = recordOf(data.memoryHits);
    const total = ['preferences', 'reviews', 'planningHistory', 'materials', 'notes']
      .reduce((sum, key) => sum + (Array.isArray(hits[key]) ? (hits[key] as unknown[]).length : 0), 0);
    return `${t('command.planningCardMemorySummary')} ${total}`;
  }
  if (message.kind === 'resource_brief') {
    const coverage = recordOf(data.coverage);
    const candidates = Array.isArray(data.resourceCandidates) ? data.resourceCandidates.length : 0;
    return `${String(coverage.status || 'partial')} · ${candidates} ${t('command.resources')}`;
  }
  if (message.kind === 'plan_design_proposal') {
    return String(data.strategyName || data.status || message.content || '');
  }
  if (message.kind === 'execution_plan_draft') {
    const tasks = Array.isArray(data.tasks) ? data.tasks.length : 0;
    return `${tasks} ${t('command.tasks')} · ${String(data.status || '')}`;
  }
  if (message.kind === 'learning_update') {
    return String(data.insight || data.feedbackType || message.content || '');
  }
  if (message.kind === 'agent_decision') {
    return `${String(data.agent || t('command.agentDecision'))} · ${String(data.decision || '')}`;
  }
  if (message.kind === 'agent_message') {
    return `${String(data.fromAgent || '')} → ${String(data.toAgent || '')}`;
  }
  return String(payload.status || message.content || '');
}

function latestPlanningStatus(messages: CommandThreadMessage[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    const payload = payloadOf(message);
    const data = recordOf(payload.data);
    if (message.kind === 'planning_session_status' || message.kind === 'planning_session_started') {
      return String(payload.status || message.content || '');
    }
    if (message.kind === 'execution_plan_draft') {
      const status = String(data.status || '');
      if (status === 'approved') return 'ready_to_write_calendar';
      if (status) return 'waiting_execution_approval';
    }
    if (message.kind === 'plan_design_proposal') {
      return 'waiting_design_approval';
    }
  }
  return '';
}

function isNonPlanningGoalUnderstandingGroup(messages: CommandThreadMessage[]): boolean {
  const understanding = [...messages].reverse().find((message) => message.kind === 'goal_understanding');
  if (!understanding) return false;
  const payload = payloadOf(understanding);
  const data = recordOf(payload.data);
  const intentState = String(data.intentState || payload.intentState || '');
  const hasPlanningArtifact = messages.some((message) => message.kind !== 'goal_understanding');
  return !hasPlanningArtifact && (intentState === 'normal_chat' || intentState === 'command');
}

function latestPlanningWorkspaceDetails(messages: CommandThreadMessage[]): CommandThreadMessage[] {
  return planningWorkspaceDetailKinds.flatMap((kind) => {
    const message = [...messages].reverse().find((item) => item.kind === kind);
    return message ? [message] : [];
  });
}

function shouldExpandPlanningCard(message: CommandThreadMessage, groupMessages: CommandThreadMessage[], isLatestGroup: boolean): boolean {
  if (!isLatestGroup) return false;
  const status = latestPlanningStatus(groupMessages);
  if (status === 'needs_goal_clarification') return message.kind === 'goal_model_updated' || (!groupMessages.some((item) => item.kind === 'goal_model_updated') && message.kind === 'user_need_contract');
  if (status === 'waiting_design_approval' || status === 'design_revision') return message.kind === 'strategy_portfolio_ready' || (!groupMessages.some((item) => item.kind === 'strategy_portfolio_ready') && message.kind === 'plan_design_proposal');
  if (status === 'waiting_execution_approval' || status === 'execution_revision' || status === 'learning_from_feedback') {
    return message.kind === 'execution_blueprint_ready' || message.kind === 'critique_report_ready' || message.kind === 'planning_learning_updated';
  }
  if (status === 'ready_to_write_calendar' || status === 'written_to_calendar') {
    return message.kind === 'execution_blueprint_ready' || message.kind === 'critique_report_ready' || message.kind === 'planning_session_status';
  }
  return message.kind === 'plan_design_proposal' || message.kind === 'execution_plan_draft' || message.kind === 'user_need_contract';
}

function CollapsiblePanel({
  title,
  summary,
  defaultExpanded,
  children,
  t,
  className = ''
}: {
  title: string;
  summary?: string;
  defaultExpanded: boolean;
  children: ReactNode;
  t: (key: string) => string;
  className?: string;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (!touched) {
      setExpanded(defaultExpanded);
    }
  }, [defaultExpanded, touched]);

  return (
    <div className={`command-collapsible ${expanded ? 'expanded' : 'collapsed'} ${className}`}>
      <button
        type="button"
        className="command-collapsible-toggle"
        aria-expanded={expanded}
        onClick={() => {
          setTouched(true);
          setExpanded((value) => !value);
        }}
      >
        <span>
          <strong>{title}</strong>
          {summary ? <small>{summary}</small> : null}
        </span>
        <em>{expanded ? t('command.collapse') : t('command.expand')}</em>
      </button>
      {expanded ? <div className="command-collapsible-body">{children}</div> : null}
    </div>
  );
}

function PlanningCardContent({
  message,
  onSend,
  t,
  planningStatus,
  actionsEnabled
}: {
  message: CommandThreadMessage;
  onSend: (value: string) => void;
  t: (key: string) => string;
  planningStatus: string;
  actionsEnabled: boolean;
}) {
  const payload = payloadOf(message);
  if (message.kind === 'goal_understanding') {
    const nested = recordOf(payload.data);
    return <GoalUnderstandingDetailCard data={Object.keys(nested).length ? nested : payload} t={t} />;
  }
  if (message.kind === 'goal_completion_updated') {
    return <GoalCompletionDetailCard data={{ ...recordOf(payload.data), businessStatus: payload.businessStatus, runtimeStatus: payload.runtimeStatus }} t={t} />;
  }
  if (message.kind === 'goal_model_updated') return <GoalModelCard data={payload.data} t={t} />;
  if (message.kind === 'reality_assessment_ready') return <RealityAssessmentCard data={payload.data} t={t} />;
  if (message.kind === 'evidence_pack_ready') return <EvidencePackCard data={payload.data} t={t} />;
  if (message.kind === 'strategy_portfolio_ready') return <StrategyPortfolioCard data={payload.data} t={t} />;
  if (message.kind === 'execution_blueprint_ready') return <ExecutionBlueprintCard data={payload.data} t={t} />;
  if (message.kind === 'critique_report_ready') return <CritiqueReportCard data={payload.data} t={t} />;
  if (message.kind === 'planning_learning_updated') return <PlanningLearningUpdateCard data={payload.data} t={t} />;
  if (message.kind === 'planning_session_started' || message.kind === 'planning_session_status') {
    const nested = recordOf(payload.data);
    const businessStatus = String(payload.businessStatus || nested.businessStatus || '');
    const runtimeStatus = String(payload.runtimeStatus || nested.runtimeStatus || '');
    return (
      <>
        <PlanningSessionStatusCard status={String(payload.status || message.content || '')} t={t} />
        {businessStatus || runtimeStatus ? (
          <dl className="command-result-meta planning-status-trace">
            {businessStatus ? <div><dt>{t('command.planningBusinessStatus')}</dt><dd>{businessStatus}</dd></div> : null}
            {runtimeStatus ? <div><dt>{t('command.planningRuntimeStatus')}</dt><dd>{runtimeStatus}</dd></div> : null}
          </dl>
        ) : null}
      </>
    );
  }
  if (message.kind === 'user_need_contract') {
    return <UserNeedContractCard data={payload.data} t={t} />;
  }
  if (message.kind === 'memory_insight_brief') {
    return <MemoryInsightCard data={payload.data} t={t} />;
  }
  if (message.kind === 'resource_brief') {
    return <ResourceBriefCard data={payload.data} t={t} />;
  }
  if (message.kind === 'plan_design_proposal') {
    return <PlanDesignProposalCard data={payload.data} onSend={onSend} t={t} planningStatus={planningStatus} actionsEnabled={actionsEnabled} />;
  }
  if (message.kind === 'execution_plan_draft') {
    return <ExecutionPlanDraftCard data={payload.data} onSend={onSend} t={t} planningStatus={planningStatus} actionsEnabled={actionsEnabled} />;
  }
  if (message.kind === 'learning_update') {
    return <LearningUpdateBadge data={payload.data} t={t} />;
  }
  if (message.kind === 'agent_decision') {
    return <AgentDecisionCard data={payload.data} t={t} />;
  }
  if (message.kind === 'agent_message') {
    return <AgentMessageCard data={payload.data} t={t} />;
  }
  return null;
}

function DeepPlanningCardGroup({
  messages,
  isLatest,
  advancedAgentTrace,
  sending,
  onSend,
  t
}: {
  messages: CommandThreadMessage[];
  isLatest: boolean;
  advancedAgentTrace: boolean;
  sending: boolean;
  onSend: (value: string) => void;
  t: (key: string) => string;
}) {
  const labels = Array.from(new Set(messages.map((message) => planningKindLabel(message.kind, t)))).slice(0, 5);
  const status = latestPlanningStatus(messages);
  const isCognitiveWorkspace = status === 'MODEL_UNAVAILABLE' || messages.some((message) => cognitiveWorkspaceKinds.has(message.kind));
  const visibleMessages = advancedAgentTrace
    ? messages
    : messages.filter((message) => (
      isCognitiveWorkspace ? cognitiveWorkspaceKinds.has(message.kind) : !technicalPlanningKinds.has(message.kind)
    ));
  const workspaceTitle = status === 'MODEL_UNAVAILABLE'
    ? t('command.cognitiveModelUnavailable')
    : status === 'waiting_design_approval' || status === 'design_revision'
      ? t('command.cognitiveWorkspaceStrategy')
      : status === 'waiting_execution_approval' || status === 'execution_revision' || status === 'learning_from_feedback'
        ? t('command.cognitiveWorkspaceExecution')
        : status === 'ready_to_write_calendar' || status === 'waiting_calendar_write_approval' || status === 'written_to_calendar'
          ? t('command.cognitiveWorkspaceReady')
          : t('command.cognitiveWorkspaceUnderstanding');
  const title = isLatest ? t('command.latestPlanningStep') : t('command.planningWorkspace');
  const summary = [status, labels.join(' / ')].filter(Boolean).join(' · ');

  if (!advancedAgentTrace) {
    const workspaceDetails = latestPlanningWorkspaceDetails(messages);
    return (
      <div className="planning-workspace-surface">
        <PlanningOverviewCard
          messages={messages}
          status={status}
          sending={sending}
          actionsEnabled={isLatest}
          onSend={onSend}
          t={t}
        />
        {workspaceDetails.length ? (
          <div className="planning-workspace-detail-stack">
            {workspaceDetails.map((message) => (
              <PlanningCardContent
                key={message.id}
                message={message}
                onSend={onSend}
                t={t}
                planningStatus={status}
                actionsEnabled={isLatest}
              />
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  if (isCognitiveWorkspace) {
    return (
      <CollapsiblePanel
        title={workspaceTitle}
        summary={isLatest ? undefined : t('command.planningWorkspace')}
        defaultExpanded={isLatest}
        t={t}
        className={`deep-planning-group cognitive-workspace ${isLatest ? 'latest' : 'historical'}`}
      >
        <div className="deep-planning-card-list cognitive-workspace-list">
          {status === 'MODEL_UNAVAILABLE' ? <ModelUnavailableCard t={t} /> : null}
          {visibleMessages.map((message) => (
            <PlanningCardContent
              key={message.id}
              message={message}
              onSend={onSend}
              t={t}
              planningStatus={status}
              actionsEnabled={isLatest}
            />
          ))}
        </div>
      </CollapsiblePanel>
    );
  }

  return (
    <CollapsiblePanel
      title={title}
      summary={summary}
      defaultExpanded={isLatest}
      t={t}
      className={`deep-planning-group ${isLatest ? 'latest' : 'historical'}`}
    >
      <div className="deep-planning-card-list">
        {visibleMessages.map((message) => (
          <CollapsiblePanel
            key={message.id}
            title={planningKindLabel(message.kind, t)}
            summary={planningCardSummary(message, t)}
            defaultExpanded={shouldExpandPlanningCard(message, messages, isLatest)}
            t={t}
            className={`deep-planning-card-item ${message.kind || ''}`}
          >
            <PlanningCardContent
              message={message}
              onSend={onSend}
              t={t}
              planningStatus={status}
              actionsEnabled={isLatest}
            />
          </CollapsiblePanel>
        ))}
      </div>
    </CollapsiblePanel>
  );
}

export function AgentThread({ messages, sending, onApprove, onSend, advancedAgentTrace = false, t }: AgentThreadProps) {
  if (!messages.length) {
    const examples = [
      t('command.examplePlan'),
      t('command.exampleQuery'),
      t('command.examplePatch'),
      t('command.exampleRefine'),
      t('command.exampleNote')
    ];
    return (
      <div className="agent-thread empty">
        <div className="command-empty-state">
          <h1>{t('command.title')}</h1>
          <p>{t('command.empty')}</p>
          <ul className="command-examples">
            {examples.map((example) => (
              <li key={example}>{example}</li>
            ))}
          </ul>
        </div>
      </div>
    );
  }

  const renderItems = groupMessages(messages);
  const latestPlanningGroupId = [...renderItems].reverse().find((item) => item.type === 'planning_group')?.id;
  const workspaceMessages = livePlanningMessages(messages);
  const latestWorkspaceMessageId = workspaceMessages[workspaceMessages.length - 1]?.id;
  const livePlanningGroup = [...renderItems].reverse().find((item) => (
    item.type === 'planning_group' && item.messages.some((message) => message.id === latestWorkspaceMessageId)
  ));
  const livePlanningGroupId = livePlanningGroup?.type === 'planning_group' ? livePlanningGroup.id : undefined;

  return (
    <div className="agent-thread">
      {renderItems.map((item) => {
        if (item.type === 'execution_group') {
          return (
            <article className="command-message card" key={`execution-${item.id}`}>
              <ExecutionGroupCard messages={item.messages} t={t} />
            </article>
          );
        }
        if (item.type === 'usage_group') {
          if (!advancedAgentTrace) return null;
          return (
            <article className="command-message card" key={`usage-${item.id}`}>
              <ModelUsageBadge usage={item.messages.map((message) => payloadOf(message).usage)} t={t} />
              {item.messages.map((message) => {
                const payload = payloadOf(message);
                const error = String(payload.error || '');
                if (!error) return null;
                return (
                  <small className="advanced-debug-error" key={`${message.id}-error`}>
                    {String(payload.feature || t('command.modelUsage'))} · {t('command.source')}: {String(payload.source || t('common.unknown'))} · {t('command.errorType')}: {error}
                  </small>
                );
              })}
            </article>
          );
        }
        if (item.type === 'planning_group') {
          if (!advancedAgentTrace && item.id !== livePlanningGroupId) return null;
          const planningMessages = advancedAgentTrace ? item.messages : workspaceMessages;
          if (!advancedAgentTrace && isNonPlanningGoalUnderstandingGroup(planningMessages)) return null;
          return (
            <article className="command-message card" key={advancedAgentTrace ? `planning-${item.id}` : 'planning-workspace-live'}>
              <DeepPlanningCardGroup
                messages={planningMessages}
                isLatest={!advancedAgentTrace || item.id === latestPlanningGroupId}
                advancedAgentTrace={advancedAgentTrace}
                sending={sending}
                onSend={onSend}
                t={t}
              />
            </article>
          );
        }
        const { message } = item;
        if (!advancedAgentTrace && message.role === 'card' && (message.kind === 'command_decision' || message.kind === 'model_usage')) {
          return null;
        }
        return (
        <article className={`command-message ${message.role}`} key={message.id}>
          {message.role !== 'card' && (
            <>
              {message.role === 'assistant' ? <span>{t('command.assistant')}</span> : null}
              <p>{message.content || (message.streaming ? t('command.running') : '')}</p>
            </>
          )}

          {message.role === 'card' && message.kind === 'error' && (
            <div className="command-inline-card error">
              <strong>Error</strong>
              <p>{message.content}</p>
            </div>
          )}

          {message.role === 'card' && message.kind === 'runtime' && (
            <ExecutionMiniCard title={message.title} content={message.content} status={message.status} t={t} />
          )}

          {message.role === 'card' && message.kind === 'summary' && (
            <InlinePlanSummaryCard summary={message.content} t={t} />
          )}

          {message.role === 'card' && message.kind === 'plan_detail' && (
            <CollapsiblePanel
              title={t('command.hiddenDraftCollapsed')}
              summary={String(payloadOf(message).title || message.title || '')}
              defaultExpanded={false}
              t={t}
              className="legacy-plan-detail"
            >
              <InlinePlanDetailCard
                title={String(payloadOf(message).title || message.title || '')}
                version={typeof payloadOf(message).version === 'number' ? payloadOf(message).version as number : undefined}
                structuredPlan={payloadOf(message).structuredPlan}
                planHorizon={payloadOf(message).planHorizon as PlanHorizon | null | undefined}
                qualityReport={payloadOf(message).qualityReport as PlanQualityReport | null | undefined}
                qualityStatus={payloadOf(message).qualityStatus as PlanQualityStatus | null | undefined}
                sourceType={payloadOf(message).sourceType as PlanSourceType | null | undefined}
                t={t}
              />
            </CollapsiblePanel>
          )}

          {message.role === 'card' && message.kind === 'refined_tasks_result' && (
            <RefinedTasksResultCard
              total={Number(payloadOf(message).total || 0)}
              succeeded={Number(payloadOf(message).succeeded || 0)}
              failed={Number(payloadOf(message).failed || 0)}
              items={payloadOf(message).items}
              errors={payloadOf(message).errors}
              t={t}
            />
          )}

          {message.role === 'card' && message.kind === 'calendar_preview' && (
            <CalendarPlanPreviewCard
              title={String(payloadOf(message).title || message.title || '')}
              plans={payloadOf(message).plans}
              t={t}
            />
          )}

          {message.role === 'card' && message.kind === 'approval' && (
            <ApprovalCard
              summary={message.content}
              actionId={message.actionId}
              risk={String(payloadOf(message).risk || '')}
              target={String(payloadOf(message).target || '')}
              operation={String(payloadOf(message).operation || '')}
              sending={sending}
              onDecision={onApprove}
              t={t}
            />
          )}

          {message.role === 'card' && message.kind === 'calendar_write_result' && (
            <CalendarWriteResultCard
              created={Number(payloadOf(message).created || 0)}
              updated={Number(payloadOf(message).updated || 0)}
              failed={Number(payloadOf(message).failed || 0)}
              affectedDates={payloadOf(message).affectedDates}
              errors={payloadOf(message).errors}
              plans={payloadOf(message).plans}
              t={t}
            />
          )}

          {message.role === 'card' && message.kind === 'command_decision' && (
            <CommandDecisionCard
              intent={String(payloadOf(message).intent || '')}
              confidence={payloadOf(message).confidence}
              targetType={String(payloadOf(message).targetType || '')}
              action={String(payloadOf(message).action || '')}
              decisionSummary={String(payloadOf(message).decisionSummary || message.content || '')}
              source={String(payloadOf(message).source || '')}
              t={t}
            />
          )}

          {message.role === 'card' && message.kind === 'plan_search_results' && (
            <PlanSearchResultsCard
              summary={String(payloadOf(message).summary || message.content || '')}
              calendarPlans={payloadOf(message).calendarPlans}
              materials={payloadOf(message).materials}
              goalHistory={payloadOf(message).goalHistory}
              monthNotes={payloadOf(message).monthNotes}
              onSend={onSend}
              t={t}
            />
          )}

          {message.role === 'card' && message.kind === 'note_search_results' && (
            <NoteSearchResultsCard
              summary={String(payloadOf(message).summary || message.content || '')}
              materials={payloadOf(message).materials}
              goalHistory={payloadOf(message).goalHistory}
              monthNotes={payloadOf(message).monthNotes}
              onSend={onSend}
              t={t}
            />
          )}

          {message.role === 'card' && message.kind === 'memory_search_results' && (
            <MemorySearchResultsCard
              summary={String(payloadOf(message).summary || message.content || '')}
              groups={payloadOf(message).groups}
              results={payloadOf(message).results}
              onSend={onSend}
              t={t}
            />
          )}

          {message.role === 'card' && message.kind === 'plan_patch_preview' && (
            <PlanPatchPreviewCard
              operation={String(payloadOf(message).operation || '')}
              before={payloadOf(message).before}
              after={payloadOf(message).after}
              changes={payloadOf(message).changes}
              t={t}
            />
          )}

          {message.role === 'card' && message.kind === 'plan_patch_result' && (
            <PlanPatchResultCard
              operation={String(payloadOf(message).operation || '')}
              status={String(payloadOf(message).status || '')}
              after={payloadOf(message).after}
              error={typeof payloadOf(message).error === 'string' ? payloadOf(message).error as string : undefined}
              t={t}
            />
          )}

          {message.role === 'card' && message.kind === 'note_write_preview' && (
            <NoteWritePreviewCard
              year={payloadOf(message).year}
              month={payloadOf(message).month}
              date={payloadOf(message).date}
              noteText={payloadOf(message).noteText}
              before={payloadOf(message).before}
              after={payloadOf(message).after}
              t={t}
            />
          )}

          {message.role === 'card' && message.kind === 'memory_write_preview' && (
            <MemoryWritePreviewCard
              kind={payloadOf(message).kind}
              title={payloadOf(message).title}
              content={payloadOf(message).content}
              summary={payloadOf(message).summary}
              t={t}
            />
          )}

          {message.role === 'card' && message.kind === 'note_write_result' && (
            <NoteWriteResultCard
              status={String(payloadOf(message).status || '')}
              year={payloadOf(message).year}
              month={payloadOf(message).month}
              noteText={payloadOf(message).noteText}
              error={typeof payloadOf(message).error === 'string' ? payloadOf(message).error as string : undefined}
              t={t}
            />
          )}

          {message.role === 'card' && message.kind === 'memory_write_result' && (
            <MemoryWriteResultCard
              status={String(payloadOf(message).status || '')}
              kind={payloadOf(message).kind}
              title={payloadOf(message).title}
              content={payloadOf(message).content}
              error={typeof payloadOf(message).error === 'string' ? payloadOf(message).error as string : undefined}
              t={t}
            />
          )}

          {message.role === 'card' && message.kind === 'planning_session_started' && (
            <PlanningSessionStatusCard status={String(payloadOf(message).status || message.content || '')} t={t} />
          )}

          {message.role === 'card' && message.kind === 'user_need_contract' && (
            <UserNeedContractCard data={payloadOf(message).data} t={t} />
          )}

          {message.role === 'card' && message.kind === 'memory_insight_brief' && (
            <MemoryInsightCard data={payloadOf(message).data} t={t} />
          )}

          {message.role === 'card' && message.kind === 'resource_brief' && (
            <ResourceBriefCard data={payloadOf(message).data} t={t} />
          )}

          {message.role === 'card' && message.kind === 'plan_design_proposal' && (
            <PlanDesignProposalCard data={payloadOf(message).data} onSend={onSend} t={t} />
          )}

          {message.role === 'card' && message.kind === 'execution_plan_draft' && (
            <ExecutionPlanDraftCard data={payloadOf(message).data} onSend={onSend} t={t} />
          )}

          {message.role === 'card' && message.kind === 'learning_update' && (
            <LearningUpdateBadge data={payloadOf(message).data} t={t} />
          )}

          {message.role === 'card' && message.kind === 'agent_decision' && (
            <AgentDecisionCard data={payloadOf(message).data} t={t} />
          )}

          {message.role === 'card' && message.kind === 'agent_message' && (
            <AgentMessageCard data={payloadOf(message).data} t={t} />
          )}

          {message.role === 'card' && message.kind === 'planning_session_status' && (
            <PlanningSessionStatusCard status={String(payloadOf(message).status || message.content || '')} t={t} />
          )}

          {message.role === 'card' && message.kind === 'goal_understanding' && (
            <GoalUnderstandingDetailCard data={payloadOf(message)} t={t} />
          )}

          {message.role === 'card' && message.kind === 'model_usage' && (
            <ModelUsageBadge usage={payloadOf(message).usage} t={t} />
          )}

          {message.role === 'card' && message.kind === 'clarify_question' && (
            <div className="command-inline-card clarify-question">
              <div className="command-card-heading">
                <strong>{t('command.clarifyQuestion')}</strong>
              </div>
              <p>{message.content}</p>
            </div>
          )}

          {message.role === 'card' && message.kind === 'execution_result' && (
            <div className={`command-inline-card execution ${message.status || 'success'}`}>
              <div className="command-card-heading">
                <strong>{t('command.executionResult')}</strong>
              </div>
              <p>{message.content}</p>
            </div>
          )}
        </article>
        );
      })}
    </div>
  );
}
