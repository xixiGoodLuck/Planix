import { Check, Clock3, Plus, Trash2 } from 'lucide-react';
import type { Language, Plan } from '../types';
import { formatReadable } from '../utils/date';

interface PlanListProps {
  date: string; lang: Language; plans: Plan[]; draft: string; time: string;
  onDraftChange: (value: string) => void; onTimeChange: (value: string) => void; onAdd: () => void;
  onToggle: (id: string) => void; onDelete: (id: string) => void; onCompletionChange: (id: string, value: string) => void;
  t: (key: string) => string;
}

export function PlanList({ date, lang, plans, draft, time, onDraftChange, onTimeChange, onAdd, onToggle, onDelete, onCompletionChange, t }: PlanListProps) {
  return <section className="surface plan-panel">
    <div className="section-head"><div><span className="eyebrow">{formatReadable(date, lang)}</span><h2>{t('legacy.plans')} · {plans.length}</h2></div></div>
    <div className="plan-list">
      {!plans.length && <div className="empty-state"><strong>{t('legacy.emptyPlans')}</strong><p>{t('legacy.emptyHint')}</p></div>}
      {plans.map((plan, index) => <article className={`plan-card ${plan.done ? 'is-done' : ''}`} key={plan.id}>
        <button className="check-button" onClick={() => onToggle(plan.id)} aria-label={plan.done ? t('legacy.done') : t('legacy.pending')}>{plan.done && <Check size={15} />}</button>
        <div className="plan-main"><div className="plan-line"><span className="plan-index">{String(index + 1).padStart(2, '0')}</span><span className="time-pill"><Clock3 size={14} />{plan.time}</span></div>
          <p className="plan-title">{plan.title}</p><input value={plan.completion} onChange={(event) => onCompletionChange(plan.id, event.target.value)} placeholder={t('legacy.completion')} /></div>
        <button className="delete-button" onClick={() => onDelete(plan.id)} aria-label={t('legacy.delete')}><Trash2 size={16} /></button>
      </article>)}
    </div>
    <div className="plan-add-row"><input type="time" value={time} onChange={(event) => onTimeChange(event.target.value)} /><input value={draft} onChange={(event) => onDraftChange(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') onAdd(); }} placeholder={t('legacy.addPlan')} /><button onClick={onAdd}><Plus size={16} />{t('legacy.add')}</button></div>
  </section>;
}
