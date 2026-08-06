interface NoteWriteResultCardProps {
  status?: string;
  year?: unknown;
  month?: unknown;
  noteText?: unknown;
  error?: string;
  t: (key: string) => string;
}

export function NoteWriteResultCard({ status, year, month, noteText, error, t }: NoteWriteResultCardProps) {
  const failed = status === 'failed';

  return (
    <div className={`command-inline-card note-write-result ${failed ? 'has-error' : ''}`}>
      <div className="command-card-heading">
        <strong>{t('command.noteWriteResult')}</strong>
        <span>{failed ? t('command.statusError') : t('command.statusSuccess')}</span>
      </div>
      {failed ? (
        <p>{error || t('command.noteWriteFailed')}</p>
      ) : (
        <p>{t('command.noteSaved')}: {String(year || '')}-{String(month || '').padStart(2, '0')} {String(noteText || '')}</p>
      )}
    </div>
  );
}
