import { AlertTriangle } from 'lucide-react';
import type { LearningFailureKind } from '../types';

interface LearningFailureNoticeProps {
  kind: LearningFailureKind;
  onRetry: () => void;
  t: (key: string) => string;
}

export function LearningFailureNotice({ kind, onRetry, t }: LearningFailureNoticeProps) {
  return (
    <section className="learning-card learning-failure" role="alert">
      <AlertTriangle size={21} />
      <div>
        <h2>{t(`learning.failure_${kind}_title`)}</h2>
        <p>{t(`learning.failure_${kind}_description`)}</p>
      </div>
      <button type="button" onClick={onRetry}>{t('learning.startAgain')}</button>
    </section>
  );
}
