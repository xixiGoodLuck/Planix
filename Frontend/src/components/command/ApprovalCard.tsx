interface ApprovalCardProps {
  summary: string;
  actionId?: string;
  risk?: string;
  target?: string;
  operation?: string;
  sending: boolean;
  onDecision: (actionId: string, decision: 'approve' | 'reject') => void;
  t: (key: string) => string;
}

function approveLabel(target: string | undefined, operation: string | undefined, risk: string | undefined, t: (key: string) => string): string {
  if (target === 'notes' || target === 'memory') return t('command.confirmRecord');
  if (target === 'calendar' && operation === 'update') return t('command.confirmModify');
  if (target === 'calendar' && (operation === 'delete' || risk === 'delete')) return t('command.confirmDelete');
  if (target === 'calendar' && operation === 'create_or_update_plans') return t('command.confirmWrite');
  return t('command.approve');
}

export function ApprovalCard({ summary, actionId, risk, target, operation, sending, onDecision, t }: ApprovalCardProps) {
  return (
    <div className="command-inline-card approval">
      <div className="command-card-heading">
        <strong>{t('command.approvalRequired')}</strong>
        <span>{target === 'notes' || target === 'memory' ? t('command.recordOperation') : risk === 'delete' ? t('command.deleteOperation') : t('command.writeRisk')}</span>
      </div>
      <p>{summary}</p>
      <div className="command-card-actions">
        <button
          type="button"
          disabled={!actionId || sending}
          onClick={() => actionId && onDecision(actionId, 'approve')}
        >
          {sending ? t('command.running') : approveLabel(target, operation, risk, t)}
        </button>
        <button
          className="ghost"
          type="button"
          disabled={!actionId || sending}
          onClick={() => actionId && onDecision(actionId, 'reject')}
        >
          {t('command.reject')}
        </button>
      </div>
    </div>
  );
}
