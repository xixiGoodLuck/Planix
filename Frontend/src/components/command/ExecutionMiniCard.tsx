interface ExecutionMiniCardProps {
  title?: string;
  content: string;
  status?: 'running' | 'success' | 'error';
  t: (key: string) => string;
}

export function ExecutionMiniCard({ title, content, status = 'running', t }: ExecutionMiniCardProps) {
  const label = status === 'success'
    ? t('command.statusSuccess')
    : status === 'error'
      ? t('command.statusError')
      : t('command.statusRunning');

  return (
    <div className={`command-inline-card execution ${status}`}>
      <div className="command-card-heading">
        <strong>{title || t('command.execution')}</strong>
        <span>{label}</span>
      </div>
      <p>{content}</p>
    </div>
  );
}
