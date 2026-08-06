import type { AgentFlowDiff } from '../../../types';

interface AgentDiffViewerProps {
  content: string;
  diff?: AgentFlowDiff;
  isStreaming: boolean;
}

export function AgentDiffViewer({ content, diff, isStreaming }: AgentDiffViewerProps) {
  if (!content) {
    return (
      <p className="agent-flow-empty-line">
        {isStreaming ? <span className="typing-cursor" aria-hidden="true" /> : null}
      </p>
    );
  }

  const hasAppendOnlyDiff = Boolean(diff && diff.current === content && content.startsWith(diff.previous));
  const stableText = hasAppendOnlyDiff ? diff?.previous ?? '' : content;
  const addedText = hasAppendOnlyDiff ? content.slice(stableText.length) : '';

  return (
    <p className="agent-flow-content">
      <span>{stableText}</span>
      {addedText ? (
        <span className="agent-flow-diff-add" key={diff?.changedAt}>
          {addedText}
        </span>
      ) : null}
      {isStreaming ? <span className="typing-cursor" aria-hidden="true" /> : null}
    </p>
  );
}
