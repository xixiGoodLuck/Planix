import { Check, Clock3, Plus, Sparkles, Trash2, X } from 'lucide-react';
import { useState } from 'react';
import { refineTask } from '../lib/api';
import { refineTaskErrorText } from '../lib/refineTaskErrors';
import type { Language, Plan, RefinedTask } from '../types';
import { formatReadable } from '../utils/date';
import { RefinedTaskPreview } from './RefinedTaskPreview';

interface PlanListProps {
  date: string;
  lang: Language;
  plans: Plan[];
  draft: string;
  time: string;
  preferences: string;
  onDraftChange: (value: string) => void;
  onTimeChange: (value: string) => void;
  onAdd: () => void;
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
  onCompletionChange: (id: string, value: string) => void;
  onSavePlanRefinedTask: (planId: string, refinedTask: RefinedTask) => Promise<Plan>;
  onDeletePlanRefinedTask: (planId: string) => Promise<Plan>;
  t: (key: string) => string;
}

export function PlanList(props: PlanListProps) {
  const {
    date,
    lang,
    plans,
    draft,
    time,
    preferences,
    onDraftChange,
    onTimeChange,
    onAdd,
    onToggle,
    onDelete,
    onCompletionChange,
    onSavePlanRefinedTask,
    onDeletePlanRefinedTask,
    t
  } = props;
  const [refinementInputs, setRefinementInputs] = useState<Record<string, string>>({});
  const [refiningPlanIds, setRefiningPlanIds] = useState<Record<string, boolean>>({});
  const [deletingPlanIds, setDeletingPlanIds] = useState<Record<string, boolean>>({});
  const [refinePlanErrors, setRefinePlanErrors] = useState<Record<string, string>>({});
  const [bulkRefining, setBulkRefining] = useState(false);

  const clearPlanError = (planId: string) => {
    setRefinePlanErrors((current) => {
      const next = { ...current };
      delete next[planId];
      return next;
    });
  };

  const setPlanError = (planId: string, message: string) => {
    setRefinePlanErrors((current) => ({ ...current, [planId]: message }));
  };

  async function refinePlan(plan: Plan, options: { skipExisting?: boolean } = {}) {
    if (!plan.title.trim() || refiningPlanIds[plan.id]) return;
    if (options.skipExisting && plan.refinedTask) return;
    setRefiningPlanIds((current) => ({ ...current, [plan.id]: true }));
    clearPlanError(plan.id);

    let refined: RefinedTask;
    try {
      refined = await refineTask({
        goal: plan.title,
        taskTitle: plan.title,
        taskDescription: plan.completion,
        date,
        availableMinutes: plan.estimatedMinutes ?? 60,
        sourceKey: plan.sourceKey,
        planId: plan.id,
        planContext: {
          planTitle: plan.title,
          planSummary: plan.completion,
          dailyLearningMinutes: plan.estimatedMinutes ?? 60,
          currentTask: {
            title: plan.title,
            description: plan.completion,
            dueDate: date,
            priority: plan.priority ?? 'medium',
            estimatedMinutes: plan.estimatedMinutes ?? 60,
            sourceKey: plan.sourceKey
          },
          sameMilestoneTasks: [plan.title]
        },
        userConstraints: [preferences].filter(Boolean),
        outputLanguage: lang === 'en-US' ? 'en' : 'zh',
        refinementInstruction: refinementInputs[plan.id] ?? ''
      });
    } catch (err) {
      setPlanError(plan.id, `${t('legacy.refineTaskGenerateFailed')}: ${refineTaskErrorText(err, t)}`);
      setRefiningPlanIds((current) => {
        const next = { ...current };
        delete next[plan.id];
        return next;
      });
      return;
    }

    try {
      await onSavePlanRefinedTask(plan.id, refined);
    } catch (err) {
      setPlanError(plan.id, `${t('legacy.refineTaskSaveFailed')}: ${refineTaskErrorText(err, t)}`);
    } finally {
      setRefiningPlanIds((current) => {
        const next = { ...current };
        delete next[plan.id];
        return next;
      });
    }
  }

  async function deleteRefinement(plan: Plan) {
    setDeletingPlanIds((current) => ({ ...current, [plan.id]: true }));
    clearPlanError(plan.id);
    try {
      await onDeletePlanRefinedTask(plan.id);
    } catch (err) {
      setPlanError(plan.id, refineTaskErrorText(err, t));
    } finally {
      setDeletingPlanIds((current) => {
        const next = { ...current };
        delete next[plan.id];
        return next;
      });
    }
  }

  async function refineAllPlans() {
    const candidates = plans.filter((plan) => plan.title.trim() && !plan.refinedTask && !refiningPlanIds[plan.id]);
    if (!candidates.length || bulkRefining) return;
    setBulkRefining(true);
    let cursor = 0;
    const worker = async () => {
      while (cursor < candidates.length) {
        const current = candidates[cursor];
        cursor += 1;
        await refinePlan(current, { skipExisting: true });
      }
    };
    await Promise.all(Array.from({ length: Math.min(2, candidates.length) }, worker));
    setBulkRefining(false);
  }

  const canRefineAll = plans.some((plan) => plan.title.trim() && !plan.refinedTask && !refiningPlanIds[plan.id]);

  return (
    <section className="surface plan-panel">
      <div className="section-head">
        <div>
          <span className="eyebrow">{formatReadable(date, lang)}</span>
          <h2>{t('legacy.plans')} · {plans.length}</h2>
        </div>
        <button className="section-action-button" onClick={refineAllPlans} disabled={!canRefineAll || bulkRefining}>
          <Sparkles size={16} />
          {bulkRefining ? t('legacy.refiningAllTasks') : t('legacy.refineAllTasks')}
        </button>
      </div>
      <div className="plan-list">
        {plans.length === 0 && (
          <div className="empty-state">
            <strong>{t('legacy.emptyPlans')}</strong>
            <p>{t('legacy.emptyHint')}</p>
          </div>
        )}
        {plans.map((plan, index) => (
          <article className={`plan-card ${plan.done ? 'is-done' : ''}`} key={plan.id}>
            <button className="check-button" onClick={() => onToggle(plan.id)} aria-label={plan.done ? t('legacy.done') : t('legacy.pending')}>
              {plan.done && <Check size={15} />}
            </button>
            <div className="plan-main">
              <div className="plan-line">
                <span className="plan-index">{String(index + 1).padStart(2, '0')}</span>
                <span className="time-pill"><Clock3 size={14} />{plan.time}</span>
                {plan.source === 'ai' && <span className="source-pill">AI</span>}
              </div>
              <p className="plan-title">{plan.title}</p>
              <div className="plan-refinement-box">
                <label>
                  <span>{t('legacy.refinementInstruction')}</span>
                  <div className="refinement-input-wrap">
                    <textarea
                      value={refinementInputs[plan.id] ?? ''}
                      onChange={(event) => setRefinementInputs((current) => ({ ...current, [plan.id]: event.target.value }))}
                      placeholder={t('legacy.refinementInstructionPlaceholder')}
                    />
                    {(refinementInputs[plan.id] ?? '').trim() && (
                      <button
                        type="button"
                        className="refinement-clear-button"
                        onClick={() => setRefinementInputs((current) => ({ ...current, [plan.id]: '' }))}
                        aria-label={t('legacy.clearRefinementInstruction')}
                        title={t('legacy.clearRefinementInstruction')}
                      >
                        <X size={14} />
                      </button>
                    )}
                  </div>
                </label>
                <button type="button" onClick={() => refinePlan(plan)} disabled={Boolean(refiningPlanIds[plan.id])}>
                  <Sparkles size={14} />
                  {refiningPlanIds[plan.id]
                    ? t('legacy.refiningTask')
                    : plan.refinedTask
                      ? t('legacy.refineAgain')
                      : t('legacy.refineTask')}
                </button>
              </div>
              {refinePlanErrors[plan.id] && <p className="inline-status error">{refinePlanErrors[plan.id]}</p>}
              {plan.refinedTask && (
                <RefinedTaskPreview
                  refinedTask={plan.refinedTask}
                  deleting={Boolean(deletingPlanIds[plan.id])}
                  onDelete={() => deleteRefinement(plan)}
                  t={t}
                />
              )}
              <label className="completion-box">
                <span>{t('legacy.completion')}</span>
                <textarea value={plan.completion} onChange={(event) => onCompletionChange(plan.id, event.target.value)} placeholder={t('legacy.completion')} />
              </label>
            </div>
            <button className="icon-button danger" onClick={() => onDelete(plan.id)} aria-label={t('common.delete')}><Trash2 size={16} /></button>
          </article>
        ))}
      </div>
      <div className="add-row">
        <input className="time-input" type="time" value={time} onChange={(event) => onTimeChange(event.target.value)} />
        <textarea
          value={draft}
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.metaKey && !event.ctrlKey) {
              event.preventDefault();
              onAdd();
            }
          }}
          placeholder={t('legacy.taskPlaceholder')}
        />
        <button className="primary-icon" onClick={onAdd} aria-label={t('legacy.addTask')}><Plus size={20} /></button>
      </div>
    </section>
  );
}
