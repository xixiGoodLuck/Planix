interface MemoryItem {
  id?: string;
  kind?: string;
  title?: string;
  summary?: string;
  content?: string;
  tags?: unknown;
  createdAt?: string;
  updatedAt?: string;
}

interface MemoryGroup {
  kind?: string;
  title?: string;
  items?: unknown;
}

interface MemorySearchResultsCardProps {
  summary?: string;
  groups?: unknown;
  results?: unknown;
  onSend?: (value: string) => void;
  t: (key: string) => string;
}

const kindLabelKeys: Record<string, string> = {
  note: 'command.memoryKindNote',
  material: 'command.memoryKindMaterial',
  planning_history: 'command.memoryKindPlanningHistory',
  preference: 'command.memoryKindPreference',
  review: 'command.memoryKindReview'
};

function listOf<T>(value: unknown): T[] {
  return Array.isArray(value) ? value.filter((item): item is T => Boolean(item && typeof item === 'object')) : [];
}

function tagsOf(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean).slice(0, 6) : [];
}

function truncate(value: string, limit = 160): string {
  const cleaned = value.replace(/\s+/g, ' ').trim();
  return cleaned.length > limit ? `${cleaned.slice(0, limit).trim()}...` : cleaned;
}

function formatIndexMessage(template: string, index: number): string {
  return template.replace('{index}', String(index + 1));
}

function labelFor(kind: string | undefined, fallback: string | undefined, t: (key: string) => string): string {
  const key = kind ? kindLabelKeys[kind] : '';
  return key ? t(key) : fallback || t('command.memoryLibrary');
}

export function MemorySearchResultsCard({ summary, groups, results, onSend, t }: MemorySearchResultsCardProps) {
  const rawGroups = listOf<MemoryGroup>(groups);
  const fallbackItems = listOf<MemoryItem>(results);
  const normalizedGroups = rawGroups.length
    ? rawGroups.map((group) => ({
        kind: group.kind,
        title: labelFor(group.kind, group.title, t),
        items: listOf<MemoryItem>(group.items)
      })).filter((group) => group.items.length)
    : [{
        kind: 'memory',
        title: t('command.memoryLibrary'),
        items: fallbackItems
      }].filter((group) => group.items.length);

  let absoluteIndex = 0;

  return (
    <div className="command-inline-card wide memory-search-results">
      <div className="command-card-heading">
        <strong>{t('command.memorySearchResults')}</strong>
        <span>{normalizedGroups.reduce((total, group) => total + group.items.length, 0)}</span>
      </div>
      {summary ? <p>{summary}</p> : null}

      {normalizedGroups.map((group) => (
        <section className="command-result-section" key={`${group.kind || group.title}`}>
          <strong>{group.title}</strong>
          <ul className="command-compact-list">
            {group.items.map((item) => {
              const index = absoluteIndex++;
              const itemTags = tagsOf(item.tags);
              const date = item.updatedAt || item.createdAt || t('command.noDate');
              return (
                <li key={`${item.id || item.title || group.title}-${index}`}>
                  <div className="command-result-title">
                    <span>{index + 1}. {item.title || t('command.untitledMemory')}</span>
                  </div>
                  <p>{truncate(item.summary || item.content || '') || t('common.empty')}</p>
                  <dl className="command-result-meta">
                    <div>
                      <dt>{t('command.memoryKind')}</dt>
                      <dd>{labelFor(item.kind || group.kind, group.title, t)}</dd>
                    </div>
                    <div>
                      <dt>{t('command.resultDate')}</dt>
                      <dd>{date}</dd>
                    </div>
                  </dl>
                  {itemTags.length ? (
                    <div className="memory-tags">
                      {itemTags.map((tag) => <span key={tag}>{tag}</span>)}
                    </div>
                  ) : null}
                  <div className="command-row-actions" aria-label={t('command.resultActions')}>
                    <button type="button" disabled={!onSend} onClick={() => onSend?.(formatIndexMessage(t('command.actionUseMemoryInPlanMessage'), index))}>{t('command.actionUseInPlan')}</button>
                    <button type="button" disabled={!onSend} onClick={() => onSend?.(formatIndexMessage(t('command.actionContinueMemoryViewMessage'), index))}>{t('command.actionContinueView')}</button>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
