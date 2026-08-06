interface CommandDecisionCardProps {
  intent?: string;
  confidence?: unknown;
  targetType?: string;
  action?: string;
  decisionSummary?: string;
  source?: string;
  t: (key: string) => string;
}

export function CommandDecisionCard({
  intent,
  targetType,
  action,
  decisionSummary,
  source,
  t
}: CommandDecisionCardProps) {
  const intentLabel = (() => {
    switch (intent) {
      case 'create_plan':
        return t('command.decisionIntentCreatePlan');
      case 'save_plan_to_calendar':
        return t('command.decisionIntentWriteCalendar');
      case 'query_plan':
        return t('command.decisionIntentQueryPlan');
      case 'query_memory':
        return t('command.decisionIntentQueryMemory');
      case 'query_notes':
        return t('command.decisionIntentQueryNotes');
      case 'patch_calendar_plan':
        return t('command.decisionIntentPatchPlan');
      case 'refine_plan':
      case 'refine_task':
        return t('command.decisionIntentRefinePlan');
      case 'save_note':
        return t('command.decisionIntentSaveNote');
      case 'save_memory':
        return t('command.decisionIntentSaveMemory');
      case 'modify_current_draft':
        return t('command.decisionIntentModifyDraft');
      case 'clarify':
        return t('command.decisionIntentClarify');
      default:
        return decisionSummary || t('command.decisionIntentChat');
    }
  })();

  const executionLabel = (() => {
    if (intent === 'patch_calendar_plan') {
      if (action === 'delete') return t('command.decisionExecuteDeletePlan');
      return t('command.decisionExecutePatchPlan');
    }
    if (intent === 'save_plan_to_calendar') return t('command.decisionExecuteWriteCalendar');
    if (intent === 'query_plan') return t('command.decisionExecuteQueryCalendar');
    if (intent === 'query_memory') return t('command.decisionExecuteQueryMemory');
    if (intent === 'query_notes') return t('command.decisionExecuteQueryNotes');
    if (intent === 'save_memory') return t('command.decisionExecuteSaveMemory');
    if (intent === 'save_note') return t('command.decisionExecuteSaveNote');
    if (intent === 'create_plan') return t('command.decisionExecuteCreatePlan');
    if (intent === 'refine_plan' || intent === 'refine_task') return t('command.decisionExecuteRefinePlan');
    if (intent === 'clarify') return t('command.decisionExecuteClarify');
    return targetType ? `${t('command.decisionExecutePrefix')} ${targetType}` : t('command.decisionExecuteChat');
  })();

  return (
    <div className="command-inline-card command-decision">
      <div className="command-card-heading">
        <strong>{t('command.intentDecision')}</strong>
        <span>{source === 'local_fallback' ? t('command.localFallbackRule') : t('command.llmDecision')}</span>
      </div>
      <div className="command-decision-copy">
        <p><strong>{t('command.decisionUnderstand')}</strong>{intentLabel}</p>
        <p><strong>{t('command.decisionExecute')}</strong>{executionLabel}</p>
      </div>
    </div>
  );
}
