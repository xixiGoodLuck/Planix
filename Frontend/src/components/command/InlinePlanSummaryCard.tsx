interface InlinePlanSummaryCardProps {
  summary: string;
  t: (key: string) => string;
}

export function InlinePlanSummaryCard({ summary, t }: InlinePlanSummaryCardProps) {
  const lines = summary.split(/\r?\n/).filter((line) => line.trim());
  const title = lines[0] || t('command.planSummary');

  return (
    <div className="command-inline-card plan-summary">
      <div className="command-card-heading">
        <strong>{t('command.planSummary')}</strong>
      </div>
      <h3>{title}</h3>
      <div className="command-summary-lines">
        {lines.slice(1).map((line, index) => (
          <p key={`${line}-${index}`}>{line}</p>
        ))}
      </div>
    </div>
  );
}
