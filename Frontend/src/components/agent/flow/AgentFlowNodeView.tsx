import { ChevronDown, ChevronRight } from 'lucide-react';
import type { AgentFlowNode, ModelKnowledgeDecision, ModelKnowledgeTriggerReason } from '../../../types';
import { agentFlowActions } from '../../../store/agentFlowStore';
import { AgentDiffViewer } from './AgentDiffViewer';
import { AgentStepCard } from './AgentStepCard';

interface AgentFlowNodeViewProps {
  node: AgentFlowNode;
  t: (key: string) => string;
}

export function AgentFlowNodeView({ node, t }: AgentFlowNodeViewProps) {
  const label = getNodeLabel(node, t);
  const displayTitle = node.type === 'tool' ? node.title : label;

  return (
    <AgentStepCard node={node} label={label} title={displayTitle} statusLabel={t(`agentFlow.${node.status}`)}>
      {node.type === 'tool' && node.toolCall ? (
        <ToolCallView node={node} t={t} />
      ) : (
        <AgentDiffViewer content={node.content} diff={node.diff} isStreaming={node.status === 'running'} />
      )}
    </AgentStepCard>
  );
}

function ToolCallView({ node, t }: AgentFlowNodeViewProps) {
  const toolCall = node.toolCall;
  if (!toolCall) return null;

  return (
    <div className="tool-call-box">
      <button
        className="tool-call-toggle"
        type="button"
        onClick={() => agentFlowActions.setExpanded(node.id, !toolCall.expanded)}
      >
        {toolCall.expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        <span>{toolCall.name}</span>
        <em>{t(`agentFlow.${toolCall.writeMode || 'preview'}`)}</em>
      </button>
      {toolCall.expanded ? (
        <div className="tool-call-detail">
          <ModelKnowledgeStatus node={node} t={t} />
          <div>
            <span>{t('agentFlow.toolInput')}</span>
            <pre>{toolCall.input}</pre>
          </div>
          <div>
            <span>{t('agentFlow.toolOutput')}</span>
            <pre>{toolCall.output || node.content}</pre>
          </div>
          <div className="tool-latency">
            <span>{t('agentFlow.latency')}</span>
            <strong>{toolCall.latencyMs ? `${toolCall.latencyMs}ms` : '-'}</strong>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ModelKnowledgeStatus({ node, t }: AgentFlowNodeViewProps) {
  const toolCall = node.toolCall;
  if (!toolCall) return null;

  if (toolCall.name === 'search_materials') {
    const decision = toolCall.modelKnowledgeDecision;
    if (!decision) return null;
    return (
      <div className={`model-knowledge-status ${decision.shouldEnrich ? 'triggered' : 'idle'}`}>
        <div className="model-knowledge-status-head">
          <span>{t('agentFlow.modelKnowledge')}</span>
          <strong>{t(decision.shouldEnrich ? 'agentFlow.modelKnowledgeTriggered' : 'agentFlow.modelKnowledgeNotTriggered')}</strong>
        </div>
        <ModelKnowledgeDecisionMeta decision={decision} t={t} />
      </div>
    );
  }

  if (toolCall.name === 'enrich_with_model_knowledge') {
    const callStatus = getModelKnowledgeCallStatus(node);
    return (
      <div className={`model-knowledge-status called ${callStatus || ''}`}>
        <div className="model-knowledge-status-head">
          <span>{t('agentFlow.modelKnowledge')}</span>
          <strong>{t('agentFlow.modelKnowledgeCalled')}</strong>
        </div>
        {callStatus ? (
          <div className="model-knowledge-status-grid">
            <span>{t('agentFlow.modelKnowledgeStatus')}</span>
            <b>{t(`agentFlow.modelKnowledge${capitalize(callStatus)}`)}</b>
          </div>
        ) : null}
        {toolCall.modelKnowledgeDecision ? <ModelKnowledgeDecisionMeta decision={toolCall.modelKnowledgeDecision} t={t} /> : null}
      </div>
    );
  }

  return null;
}

function ModelKnowledgeDecisionMeta({ decision, t }: { decision: ModelKnowledgeDecision; t: (key: string) => string }) {
  return (
    <div className="model-knowledge-status-grid">
      {decision.triggerReason ? (
        <>
          <span>{t('agentFlow.modelKnowledgeReason')}</span>
          <b>{modelKnowledgeReasonLabel(decision.triggerReason, t)}</b>
        </>
      ) : null}
      {typeof decision.localSourceCount === 'number' ? (
        <>
          <span>{t('agentFlow.localSourceCount')}</span>
          <b>{decision.localSourceCount}</b>
        </>
      ) : null}
      {typeof decision.relevantSourceCount === 'number' ? (
        <>
          <span>{t('agentFlow.relevantSourceCount')}</span>
          <b>{decision.relevantSourceCount}</b>
        </>
      ) : null}
      {decision.matchedKeywords?.length ? (
        <>
          <span>{t('agentFlow.matchedKeywords')}</span>
          <b>{decision.matchedKeywords.join(' / ')}</b>
        </>
      ) : null}
      {decision.missingKeywords?.length ? (
        <>
          <span>{t('agentFlow.missingKeywords')}</span>
          <b>{decision.missingKeywords.join(' / ')}</b>
        </>
      ) : null}
    </div>
  );
}

function modelKnowledgeReasonLabel(reason: ModelKnowledgeTriggerReason, t: (key: string) => string) {
  const keyByReason: Record<ModelKnowledgeTriggerReason, string> = {
    forced_by_user: 'modelKnowledgeForcedByUser',
    insufficient_local_sources: 'modelKnowledgeInsufficientLocalSources',
    keyword_mismatch: 'modelKnowledgeKeywordMismatch',
    low_local_relevance: 'modelKnowledgeLowLocalRelevance'
  };
  return t(`agentFlow.${keyByReason[reason]}`);
}

function getModelKnowledgeCallStatus(node: AgentFlowNode): 'success' | 'failed' | 'degraded' | '' {
  const rawOutput = node.toolCall?.raw?.output;
  if (node.status === 'error' || (isRecord(rawOutput) && rawOutput.error)) return 'failed';
  if (isRecord(rawOutput) && rawOutput.sourceType === 'local_knowledge_template') return 'degraded';
  if (isRecord(rawOutput) && rawOutput.sourceType === 'model_knowledge') return 'success';
  return '';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function capitalize(value: string) {
  return value ? `${value[0].toUpperCase()}${value.slice(1)}` : value;
}

function getNodeLabel(node: AgentFlowNode, t: (key: string) => string) {
  const keyByType: Record<AgentFlowNode['type'], string> = {
    input: 'input',
    reasoning: 'plan',
    tool: 'tool',
    observation: 'observation',
    output: 'output'
  };

  return t(`agentFlow.${keyByType[node.type]}`);
}
