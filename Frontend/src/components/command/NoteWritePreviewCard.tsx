interface NoteWritePreviewCardProps {
  year?: unknown;
  month?: unknown;
  date?: unknown;
  noteText?: unknown;
  before?: unknown;
  after?: unknown;
  t: (key: string) => string;
}

export function NoteWritePreviewCard({ year, month, date, noteText, before, after, t }: NoteWritePreviewCardProps) {
  return (
    <div className="command-inline-card wide note-write-preview">
      <div className="command-card-heading">
        <strong>{t('command.noteWritePreview')}</strong>
        <span>{String(year || '')}-{String(month || '').padStart(2, '0')}</span>
      </div>
      <p className="note-write-target">
        {t('command.noteWriteTarget')
          .replace('{year}', String(year || ''))
          .replace('{month}', String(month || ''))}
      </p>
      <blockquote className="note-write-content">{String(noteText || '')}</blockquote>
      {date ? <small>{String(date)}</small> : null}
      <div className="command-patch-lines">
        <p>
          <small>{t('command.after')}</small>
          <span>{String(after || before || '')}</span>
        </p>
      </div>
    </div>
  );
}
