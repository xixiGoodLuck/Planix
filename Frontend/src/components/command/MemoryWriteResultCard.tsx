interface MemoryWriteResultCardProps {
  status?: string;
  kind?: unknown;
  title?: unknown;
  content?: unknown;
  error?: string;
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

export function MemoryWriteResultCard({ status, kind, title, content, error, t }: MemoryWriteResultCardProps) {
  const failed = status === 'failed';

  return (
    <div className={`command-inline-card memory-write-result ${failed ? 'has-error' : ''}`}>
      <div className="command-card-heading">
        <strong>{t('command.memoryWriteResult')}</strong>
        <span>{failed ? t('command.statusError') : t('command.statusSuccess')}</span>
      </div>
      {failed ? (
        <p>{error || t('command.memoryWriteFailed')}</p>
      ) : (
        <p>{t('command.memorySaved')}: {labelFor(kind, t)} · {String(title || content || '')}</p>
      )}
    </div>
  );
}
