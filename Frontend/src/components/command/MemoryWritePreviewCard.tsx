interface MemoryWritePreviewCardProps {
  kind?: unknown;
  title?: unknown;
  content?: unknown;
  summary?: unknown;
  t: (key: string) => string;
}

const kindLabelKeys: Record<string, string> = {
  note: 'command.memoryKindNote',
  material: 'command.memoryKindMaterial',
  planning_history: 'command.memoryKindPlanningHistory',
  preference: 'command.memoryKindPreference',
  review: 'command.memoryKindReview'
};

function labelFor(kind: unknown, t: (key: string) => string): string {
  const key = kindLabelKeys[String(kind || '')];
  return key ? t(key) : t('command.memoryLibrary');
}

export function MemoryWritePreviewCard({ kind, title, content, summary, t }: MemoryWritePreviewCardProps) {
  return (
    <div className="command-inline-card wide memory-write-preview">
      <div className="command-card-heading">
        <strong>{t('command.memoryWritePreview')}</strong>
        <span>{labelFor(kind, t)}</span>
      </div>
      <p className="note-write-target">{t('command.memoryWriteTarget').replace('{kind}', labelFor(kind, t))}</p>
      {title ? <strong>{String(title)}</strong> : null}
      <blockquote className="note-write-content">{String(content || summary || '')}</blockquote>
    </div>
  );
}
