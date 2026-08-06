import type { ReactNode } from 'react';
import { AlertTriangle, CheckCircle2, Circle, Loader2 } from 'lucide-react';
import type { AgentFlowNode } from '../../../types';

interface AgentStepCardProps {
  node: AgentFlowNode;
  label: string;
  title?: string;
  statusLabel: string;
  children: ReactNode;
}

export function AgentStepCard({ node, label, title, statusLabel, children }: AgentStepCardProps) {
  const StatusIcon = getStatusIcon(node.status);
  const heading = title ?? node.title;
  const showKicker = label.trim() && label.trim() !== heading.trim();

  return (
    <article className={`agent-step-card ${node.status}`}>
      <div className={`agent-step-dot ${node.status}`} aria-hidden="true">
        <StatusIcon size={15} />
      </div>
      <div className="agent-step-head">
        <div>
          {showKicker ? <span className="agent-step-kicker">{label}</span> : null}
          <h3>{heading}</h3>
        </div>
        <span className={`agent-step-status ${node.status}`}>{statusLabel}</span>
      </div>
      {children}
    </article>
  );
}

function getStatusIcon(status: AgentFlowNode['status']) {
  if (status === 'running') return Loader2;
  if (status === 'done') return CheckCircle2;
  if (status === 'error') return AlertTriangle;
  return Circle;
}
