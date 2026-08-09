import type { CommandThreadMessage } from '../../stores/commandAgentStore';
import { ApprovalCard } from './ApprovalCard';
import { CalendarPlanPreviewCard } from './CalendarPlanPreviewCard';
import { CalendarWriteResultCard } from './CalendarWriteResultCard';
import { AgentDecisionCard, AgentMessageCard, PlanningSessionStatusCard } from './PlanningTraceCards';
import { ModelUsageBadge } from './ModelUsageBadge';
import { PlanningOverviewCard } from './PlanningOverviewCard';
import type { PlanningControlAction } from '../../lib/api';

interface AgentThreadProps {
  messages: CommandThreadMessage[]; sending: boolean;
  onApprove: (actionId: string, decision: 'approve' | 'reject') => void;
  onSend: (value: string) => void; onControl?: (action: PlanningControlAction, label: string) => void; advancedAgentTrace?: boolean; t: (key: string) => string;
}
const planningKinds = new Set(['planning_session_started', 'planning_progress', 'agent_decision', 'agent_message', 'planning_session_status']);
const payload = (message: CommandThreadMessage) => message.payload || {};

export function AgentThread({ messages, sending, onApprove, onSend, onControl, advancedAgentTrace = false, t }: AgentThreadProps) {
  const planningMessages = messages.filter((message) => message.role === 'card' && planningKinds.has(message.kind || ''));
  const visible = messages.filter((message) => !planningKinds.has(message.kind || '') && (advancedAgentTrace || message.kind !== 'model_usage'));
  return <div className="command-thread">
    {!messages.length && <div className="command-empty-state"><h1>Planix</h1><p>{t('command.emptyDescription')}</p></div>}
    {planningMessages.length > 0 && <article className="command-message card"><PlanningOverviewCard messages={messages} sending={sending} actionsEnabled onSend={onSend} onControl={onControl} t={t} /></article>}
    {advancedAgentTrace && planningMessages.map((message) => <article className="command-message card" key={message.id}>
      {message.kind === 'planning_session_started' || message.kind === 'planning_session_status' ? <PlanningSessionStatusCard status={String(payload(message).status || message.content)} t={t} /> : null}
      {message.kind === 'agent_decision' ? <AgentDecisionCard data={payload(message).data} t={t} /> : null}
      {message.kind === 'agent_message' ? <AgentMessageCard data={payload(message).data} t={t} /> : null}
    </article>)}
    {visible.map((message) => <article className={`command-message ${message.role}`} key={message.id}>
      {message.role !== 'card' && <><span>{message.role === 'assistant' ? t('command.assistant') : ''}</span><p>{message.content || (message.streaming ? t('command.running') : '')}</p></>}
      {message.kind === 'error' && <div className="command-inline-card error"><strong>Error</strong><p>{message.content}</p></div>}
      {message.kind === 'calendar_preview' && <CalendarPlanPreviewCard title={String(payload(message).title || message.content)} plans={payload(message).plans} t={t} />}
      {message.kind === 'approval' && <ApprovalCard summary={message.content} actionId={message.actionId} risk={String(payload(message).risk || '')} target={String(payload(message).target || '')} operation={String(payload(message).operation || '')} sending={sending} onDecision={onApprove} t={t} />}
      {message.kind === 'calendar_write_result' && <CalendarWriteResultCard created={Number(payload(message).created || 0)} updated={Number(payload(message).updated || 0)} failed={Number(payload(message).failed || 0)} affectedDates={payload(message).affectedDates} errors={payload(message).errors} plans={payload(message).plans} t={t} />}
      {message.kind === 'model_usage' && <ModelUsageBadge usage={payload(message).usage} t={t} />}
      {message.kind === 'clarify_question' && <div className="command-inline-card clarify-question"><strong>{t('command.clarifyQuestion')}</strong><p>{message.content}</p></div>}
      {message.kind === 'execution_result' && <div className={`command-inline-card execution ${message.status || ''}`}><strong>{t('command.executionResult')}</strong><p>{message.content}</p></div>}
    </article>)}
    {sending && !messages.some((message) => message.streaming) && <div className="command-running-indicator">{t('command.running')}</div>}
  </div>;
}
