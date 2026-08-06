interface MaterialResult {
  title?: string;
  chunk?: string;
  updatedAt?: string;
}

interface GoalHistoryResult {
  title?: string;
  goal?: string;
  summary?: string;
  createdAt?: string;
  updatedAt?: string;
}

interface MonthNoteResult {
  year?: number;
  month?: number;
  content?: string;
  updatedAt?: string;
}

interface NoteSearchResultsCardProps {
  summary?: string;
  materials?: unknown;
  goalHistory?: unknown;
  monthNotes?: unknown;
  onSend?: (value: string) => void;
  t: (key: string) => string;
}

function listOf<T>(value: unknown): T[] {
  return Array.isArray(value) ? value.filter((item): item is T => Boolean(item && typeof item === 'object')) : [];
}

function formatIndexMessage(template: string, index: number): string {
  return template.replace('{index}', String(index + 1));
}

interface NoteResultItem {
  title: string;
  summary: string;
  source: string;
  date: string;
}

function truncate(value: string, limit = 140): string {
  const cleaned = value.replace(/\s+/g, ' ').trim();
  return cleaned.length > limit ? `${cleaned.slice(0, limit).trim()}...` : cleaned;
}

export function NoteSearchResultsCard({ summary, materials, goalHistory, monthNotes, onSend, t }: NoteSearchResultsCardProps) {
  const materialItems = listOf<MaterialResult>(materials);
  const historyItems = listOf<GoalHistoryResult>(goalHistory);
  const noteItems = listOf<MonthNoteResult>(monthNotes);
  const results: NoteResultItem[] = [
    ...noteItems.map((item) => ({
      title: `${item.year || ''}-${String(item.month || 1).padStart(2, '0')} ${t('command.monthNotes')}`,
      summary: truncate(item.content || ''),
      source: t('command.monthNotes'),
      date: item.updatedAt || `${item.year || ''}-${String(item.month || 1).padStart(2, '0')}`
    })),
    ...materialItems.map((item) => ({
      title: item.title || t('command.material'),
      summary: truncate(item.chunk || ''),
      source: t('command.materialResults'),
      date: item.updatedAt || t('command.noDate')
    })),
    ...historyItems.map((item) => ({
      title: item.title || item.goal || t('command.untitledPlan'),
      summary: truncate(item.summary || item.goal || ''),
      source: t('command.goalHistory'),
      date: item.updatedAt || item.createdAt || t('command.noDate')
    }))
  ];

  return (
    <div className="command-inline-card wide note-search-results">
      <div className="command-card-heading">
        <strong>{t('command.noteSearchResults')}</strong>
        <span>{results.length}</span>
      </div>
      {summary ? <p>{summary}</p> : null}

      {results.length ? (
        <section className="command-result-section">
          <ul className="command-compact-list">
            {results.map((item, index) => (
              <li key={`${item.title}-${item.source}-${index}`}>
                <div className="command-result-title">
                  <span>{index + 1}. {item.title}</span>
                </div>
                <p>{item.summary || t('common.empty')}</p>
                <dl className="command-result-meta">
                  <div>
                    <dt>{t('command.resultSource')}</dt>
                    <dd>{item.source}</dd>
                  </div>
                  <div>
                    <dt>{t('command.resultDate')}</dt>
                    <dd>{item.date}</dd>
                  </div>
                </dl>
                <div className="command-row-actions" aria-label={t('command.resultActions')}>
                  <button type="button" disabled={!onSend} onClick={() => onSend?.(formatIndexMessage(t('command.actionUseInPlanMessage'), index))}>{t('command.actionUseInPlan')}</button>
                  <button type="button" disabled={!onSend} onClick={() => onSend?.(formatIndexMessage(t('command.actionContinueViewMessage'), index))}>{t('command.actionContinueView')}</button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
