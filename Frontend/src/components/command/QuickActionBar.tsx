import { CalendarPlus, FileSearch, ListChecks, NotebookPen, PencilLine, Wand2 } from 'lucide-react';

interface QuickActionBarProps {
  disabled?: boolean;
  onSend: (value: string) => void;
  t: (key: string) => string;
}

const actions = [
  { key: 'viewPlans', textKey: 'command.quickViewPlans', messageKey: 'command.quickViewPlansMessage', icon: ListChecks },
  { key: 'searchMemory', textKey: 'command.quickSearchMemory', messageKey: 'command.quickSearchMemoryMessage', icon: FileSearch },
  { key: 'recordMemory', textKey: 'command.quickRecordMemory', messageKey: 'command.quickRecordMemoryMessage', icon: NotebookPen },
  { key: 'refinePlan', textKey: 'command.quickRefinePlan', messageKey: 'command.quickRefinePlanMessage', icon: Wand2 },
  { key: 'modifyPlan', textKey: 'command.quickModifyPlan', messageKey: 'command.quickModifyPlanMessage', icon: PencilLine },
  { key: 'writeCalendar', textKey: 'command.quickWriteCalendar', messageKey: 'command.quickWriteCalendarMessage', icon: CalendarPlus }
];

export function QuickActionBar({ disabled, onSend, t }: QuickActionBarProps) {
  return (
    <div className="command-quick-actions" aria-label={t('command.quickActions')}>
      {actions.map((action) => {
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
  );
}
