import { ChevronsUp, Play, RotateCcw } from 'lucide-react';
import { agentFlowActions, useAgentFlow } from '../../../store/agentFlowStore';
import { AgentFlowNodeView } from './AgentFlowNodeView';

interface AgentFlowTraceProps {
  t: (key: string) => string;
}

export function AgentFlowTrace({ t }: AgentFlowTraceProps) {
  const { nodes, traceVisible, isRunning } = useAgentFlow();

  if (!traceVisible && nodes.length === 0) return null;

  if (!traceVisible) {
    return (
      <article className="agent-flow-trace agent-flow-collapsed riva-panel">
        <button type="button" onClick={() => agentFlowActions.setTraceVisible(true)}>
          <Play size={15} />
          <span>{t('agentFlow.expand')}</span>
        </button>
        <p>{t('agentFlow.collapsed')}</p>
      </article>
    );
  }

  return (
    <article className="agent-flow-trace riva-panel">
      <div className="agent-flow-header">
        <div>
          <span className="riva-eyebrow">{t('agentFlow.subtitle')}</span>
          <h2>{t('agentFlow.title')}</h2>
        </div>
        <div className="agent-flow-actions">
          <button type="button" onClick={() => void agentFlowActions.replayFlow()} disabled={isRunning || nodes.length === 0}>
            <RotateCcw size={15} />
            {t('agentFlow.replay')}
          </button>
          <button type="button" onClick={() => agentFlowActions.setTraceVisible(false)}>
            <ChevronsUp size={15} />
            {t('agentFlow.collapse')}
          </button>
        </div>
      </div>

      <div className="agent-flow-scroll" aria-label={t('agentFlow.timeline')}>
        <div className="agent-flow-timeline">
          {nodes.map((node) => (
            <AgentFlowNodeView key={node.id} node={node} t={t} />
          ))}
        </div>
      </div>
    </article>
  );
}
