import { useCallback, useEffect, useState } from 'react';
import { Trash2 } from 'lucide-react';
import { AIWorkspace } from '../components/AIWorkspace';
import { fetchContextStats, resetAiContext } from '../lib/api';
import { commandAgentActions, useCommandAgent } from '../stores/commandAgentStore';
import type { AiSettings, ContextStats } from '../types';

interface SettingsPageProps {
  onSettingsChange: (settings: AiSettings) => void;
  t: (key: string) => string;
}

export function SettingsPage(props: SettingsPageProps) {
  const { t } = props;
  const command = useCommandAgent();
  const [stats, setStats] = useState<ContextStats | null>(null);
  const [showConfirmation, setShowConfirmation] = useState(false);
  const [clearMemory, setClearMemory] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [status, setStatus] = useState('');
  const loadStats = useCallback(async () => {
    try { setStats(await fetchContextStats()); }
    catch { setStatus(t('legacy.contextStatsFailed')); }
  }, [t]);

  useEffect(() => { void loadStats(); }, [loadStats]);

  const closeConfirmation = () => {
    if (resetting) return;
    setShowConfirmation(false);
    setClearMemory(false);
  };

  const resetContext = async () => {
    setResetting(true);
    setStatus('');
    try {
      await resetAiContext(clearMemory);
      commandAgentActions.clearContext();
      await loadStats();
      setShowConfirmation(false);
      setClearMemory(false);
      setStatus(t('legacy.contextResetDone'));
    } catch {
      setStatus(t('legacy.contextResetFailed'));
    } finally {
      setResetting(false);
    }
  };

  return (
    <section className="page-stack">
      <AIWorkspace onSettingsChange={props.onSettingsChange} t={t} />
      <section className="surface context-management-card" aria-labelledby="context-management-title">
        <div className="settings-title">
          <span id="context-management-title">{t('legacy.aiContextManagement')}</span>
          <strong>{t('legacy.aiContextManagementHint')}</strong>
        </div>
        <div className="context-stats" aria-label={t('legacy.currentContextStats')}>
          <div><span>{t('legacy.conversationCount')}</span><strong>{stats?.conversations ?? '—'}</strong></div>
          <div><span>{t('legacy.planningSessionCount')}</span><strong>{stats?.planningSessions ?? '—'}</strong></div>
          <div><span>{t('legacy.artifactCount')}</span><strong>{stats?.artifacts ?? '—'}</strong></div>
          <div><span>{t('legacy.memoryCount')}</span><strong>{stats?.memories ?? '—'}</strong></div>
        </div>
        <div className="context-management-actions">
          <button className="section-action-button danger" type="button" onClick={() => { setStatus(''); setShowConfirmation(true); }} disabled={command.runningWorkspaceCount > 0}>
            <Trash2 size={15} />
            {t('legacy.clearAiContext')}
          </button>
          {command.runningWorkspaceCount > 0 && <span>{t('legacy.contextResetRunningDisabled')}</span>}
          {status && <span role="status">{status}</span>}
        </div>
      </section>
      <section className="surface advanced-debug-settings" aria-labelledby="advanced-debug-title">
        <div className="settings-title">
          <span id="advanced-debug-title">{t('legacy.advancedDebugMode')}</span>
          <strong>{command.advancedAgentTrace ? t('legacy.enabled') : t('legacy.disabled')}</strong>
        </div>
        <p>{t('legacy.advancedDebugHint')}</p>
        <label className="advanced-debug-toggle">
          <input
            type="checkbox"
            checked={command.advancedAgentTrace}
            onChange={(event) => commandAgentActions.setAdvancedAgentTrace(event.target.checked)}
          />
          <span>{t('legacy.showAgentTrace')}</span>
        </label>
      </section>
      {showConfirmation && (
        <div className="context-reset-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeConfirmation(); }}>
          <section className="context-reset-dialog" role="dialog" aria-modal="true" aria-labelledby="context-reset-title">
            <h2 id="context-reset-title">{t('legacy.confirmClearAiContext')}</h2>
            <p>{t('legacy.contextResetDeletes')}</p>
            <ul>
              <li>{t('legacy.contextResetConversation')}</li>
              <li>{t('legacy.contextResetPlanning')}</li>
              <li>{t('legacy.contextResetArtifacts')}</li>
              <li>{t('legacy.contextResetCheckpoint')}</li>
            </ul>
            <p>{t('legacy.contextResetKeeps')}</p>
            <ul>
              <li>{t('legacy.contextResetApiKey')}</li>
              <li>{t('legacy.contextResetModelSettings')}</li>
              <li>{t('legacy.contextResetSavedPlans')}</li>
            </ul>
            <label className="advanced-debug-toggle">
              <input type="checkbox" checked={clearMemory} onChange={(event) => setClearMemory(event.target.checked)} />
              <span>{t('legacy.clearLongTermMemory')}</span>
            </label>
            <div className="context-reset-dialog-actions">
              <button type="button" className="section-action-button" onClick={closeConfirmation} disabled={resetting}>{t('common.cancel')}</button>
              <button type="button" className="section-action-button danger" onClick={() => void resetContext()} disabled={resetting}>
                {resetting ? t('legacy.clearingAiContext') : t('legacy.clearAiContext')}
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
