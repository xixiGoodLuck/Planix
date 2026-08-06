import {
  CalendarPlus,
  CheckCircle2,
  ChevronDown,
  FileSearch,
  ListChecks,
  NotebookPen,
  PencilLine,
  RotateCcw,
  Sparkles,
  Wand2
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { CommandThreadMessage } from '../../stores/commandAgentStore';
import { deriveDeepPlanningStatus, type PlanningStatus } from './deepPlanningStatus';

type Translator = (key: string) => string;

interface DeepPlanningActionBarProps {
  disabled?: boolean;
  messages: CommandThreadMessage[];
  onSend: (value: string) => void;
  t: Translator;
}

type PlanningAction = {
  key: string;
  label: string;
  message: string;
  icon: LucideIcon;
  disabled?: boolean;
};

const legacyActions = [
  { key: 'viewPlans', textKey: 'command.quickViewPlans', messageKey: 'command.quickViewPlansMessage', icon: ListChecks },
  { key: 'searchMemory', textKey: 'command.quickSearchMemory', messageKey: 'command.quickSearchMemoryMessage', icon: FileSearch },
  { key: 'recordMemory', textKey: 'command.quickRecordMemory', messageKey: 'command.quickRecordMemoryMessage', icon: NotebookPen },
  { key: 'refinePlan', textKey: 'command.quickRefinePlan', messageKey: 'command.quickRefinePlanMessage', icon: Wand2 },
  { key: 'modifyPlan', textKey: 'command.quickModifyPlan', messageKey: 'command.quickModifyPlanMessage', icon: PencilLine },
  { key: 'writeCalendar', textKey: 'command.quickWriteCalendar', messageKey: 'command.quickWriteCalendarMessage', icon: CalendarPlus }
];

function primaryActions(status: PlanningStatus | undefined, t: Translator): PlanningAction[] {
  if (!status) {
    return [
      { key: 'startDeepPlanning', label: t('command.startDeepPlanning'), message: t('command.startDeepPlanningMessage'), icon: Sparkles }
    ];
  }
  if (status === 'MODEL_UNAVAILABLE') {
    return [
      { key: 'retryDeepPlanning', label: t('command.retryDeepPlanning'), message: t('command.retryDeepPlanningMessage'), icon: RotateCcw }
    ];
  }
  if (status === 'needs_goal_clarification') {
    return [
      { key: 'supplementGoal', label: t('command.supplementGoal'), message: t('command.supplementGoalMessage'), icon: PencilLine }
    ];
  }
  if (status === 'waiting_design_approval' || status === 'design_revision') {
    return [
      { key: 'confirmDesign', label: t('command.confirmDesign'), message: t('command.confirmDesignMessage'), icon: CheckCircle2 },
      { key: 'reviseDesign', label: t('command.reviseDesign'), message: t('command.reviseDesignMessage'), icon: PencilLine }
    ];
  }
  if (status === 'waiting_execution_approval') {
    return [
      { key: 'confirmExecution', label: t('command.confirmExecution'), message: t('command.confirmExecutionMessage'), icon: CheckCircle2 },
      { key: 'feedbackTooHeavy', label: t('command.feedbackTooHeavy'), message: t('command.feedbackTooHeavyMessage'), icon: RotateCcw },
      { key: 'feedbackResourceHard', label: t('command.feedbackResourceHard'), message: t('command.feedbackResourceHardMessage'), icon: FileSearch }
    ];
  }
  if (status === 'execution_revision' || status === 'learning_from_feedback') {
    return [
      { key: 'feedbackTooHeavy', label: t('command.feedbackTooHeavy'), message: t('command.feedbackTooHeavyMessage'), icon: RotateCcw },
      { key: 'feedbackResourceHard', label: t('command.feedbackResourceHard'), message: t('command.feedbackResourceHardMessage'), icon: FileSearch }
    ];
  }
  if (status === 'ready_to_write_calendar') {
    return [
      { key: 'writeCalendar', label: t('command.quickWriteCalendar'), message: t('command.quickWriteCalendarMessage'), icon: CalendarPlus }
    ];
  }
  return [
    { key: 'waitingCalendarApproval', label: t('command.waitingCalendarApproval'), message: '', icon: CheckCircle2, disabled: true }
  ];
}

export function DeepPlanningActionBar({ disabled, messages, onSend, t }: DeepPlanningActionBarProps) {
  const status = deriveDeepPlanningStatus(messages);
  const actions = primaryActions(status, t);
  return (
    <div className="command-deep-actions" aria-label={t('command.deepPlanningActions')}>
      <div className="command-primary-actions">
        {actions.map((action) => {
          const Icon = action.icon;
          return (
            <button
              key={action.key}
              type="button"
              disabled={disabled || action.disabled || !action.message}
              title={action.label}
              onClick={() => action.message && onSend(action.message)}
            >
              <Icon size={15} />
              <span>{action.label}</span>
            </button>
          );
        })}
      </div>
      <details className="command-more-actions">
        <summary>
          <ChevronDown size={15} />
          <span>{t('command.moreActions')}</span>
        </summary>
        <div className="command-more-action-grid">
          {legacyActions.map((action) => {
            const Icon = action.icon;
            return (
              <button
                key={action.key}
                type="button"
                disabled={disabled}
                title={t(action.textKey)}
                onClick={() => onSend(t(action.messageKey))}
              >
                <Icon size={15} />
                <span>{t(action.textKey)}</span>
              </button>
            );
          })}
        </div>
      </details>
    </div>
  );
}
