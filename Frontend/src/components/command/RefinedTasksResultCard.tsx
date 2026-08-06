import { RefinedTaskPreview } from '../RefinedTaskPreview';
import type { RefinedTask } from '../../types';

interface RefinedTaskResultItem {
  taskKey?: string;
  taskTitle?: string;
  milestoneTitle?: string;
  refinedTask?: RefinedTask;
}

interface RefinedTasksResultCardProps {
  total: number;
  succeeded: number;
  failed: number;
  items: unknown;
  errors?: unknown;
  t: (key: string) => string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function toItems(value: unknown): RefinedTaskResultItem[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).map((item) => ({
    taskKey: typeof item.taskKey === 'string' ? item.taskKey : undefined,
    taskTitle: typeof item.taskTitle === 'string' ? item.taskTitle : undefined,
    milestoneTitle: typeof item.milestoneTitle === 'string' ? item.milestoneTitle : undefined,
    refinedTask: isRecord(item.refinedTask) ? item.refinedTask as unknown as RefinedTask : undefined
  })).filter((item) => item.refinedTask);
}

function toErrors(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    if (isRecord(item)) {
      const title = typeof item.taskTitle === 'string' ? item.taskTitle : '';
      const error = typeof item.error === 'string' ? item.error : '';
      return [title, error].filter(Boolean).join(': ');
    }
    return String(item);
  }).filter(Boolean);
}

export function RefinedTasksResultCard({ total, succeeded, failed, items, errors, t }: RefinedTasksResultCardProps) {
  const refinedItems = toItems(items);
  const errorLines = toErrors(errors);

  return (
    <div className={`command-inline-card refined-result ${failed > 0 ? 'has-error' : ''}`}>
      <div className="command-card-heading">
        <strong>{t('command.refinedTasksResult')}</strong>
        <span>{t('command.refinedSucceeded')} {succeeded} / {total}</span>
      </div>
      <div className="command-refined-list">
        {refinedItems.map((item) => (
          <section className="command-refined-item" key={item.taskKey || item.taskTitle}>
            <div>
              <strong>{item.taskTitle || t('command.refinedTask')}</strong>
              {item.milestoneTitle && <small>{item.milestoneTitle}</small>}
            </div>
            {item.refinedTask && <RefinedTaskPreview refinedTask={item.refinedTask} t={t} />}
          </section>
        ))}
      </div>
      {failed > 0 && (
        <div className="command-result-errors">
          <strong>{t('command.refinedErrors')}</strong>
          {errorLines.map((line) => <small key={line}>{line}</small>)}
        </div>
      )}
    </div>
  );
}
