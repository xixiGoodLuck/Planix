import { ModelUsageBadge } from './ModelUsageBadge';

type Translator = (key: string) => string;

interface CardProps {
  data?: unknown;
  status?: string;
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

function lines(value: unknown): string[] {
  return list(value).map((item) => text(item)).filter(Boolean);
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
      <p>{text(raw.fromAgent, t('common.unknown'))} → {text(raw.toAgent, t('common.unknown'))}</p>
      {text(raw.reason) ? <p>{t('command.agentMessageReason')}: {text(raw.reason)}</p> : null}
      <small>{t('command.agentMessageResolved')}: {raw.resolved ? t('common.yes') : t('common.no')}</small>
      {text(payload.errorType) ? <small>{t('command.errorType')}: {text(payload.errorType)}</small> : null}
      {attempts.length ? <ul className="command-compact-list">{attempts.map((attempt, index) => <li key={index}>{text(attempt.provider)} / {text(attempt.model)} · {text(attempt.status)}{text(attempt.errorType) ? ` · ${text(attempt.errorType)}` : ''}{typeof attempt.latencyMs === 'number' ? ` · ${String(attempt.latencyMs)}ms` : ''}</li>)}</ul> : null}
      {Object.keys(payload).length && !attempts.length ? <small>{t('command.agentPayload')}: {Object.keys(payload).join(' / ')}</small> : null}
    </div>
  );
}
