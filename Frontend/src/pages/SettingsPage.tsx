import { AIWorkspace } from '../components/AIWorkspace';
import { commandAgentActions, useCommandAgent } from '../stores/commandAgentStore';
import type { AiSettings } from '../types';

interface SettingsPageProps {
  onSettingsChange: (settings: AiSettings) => void;
  t: (key: string) => string;
}

export function SettingsPage(props: SettingsPageProps) {
  const command = useCommandAgent();
  return (
    <section className="page-stack">
      <AIWorkspace onSettingsChange={props.onSettingsChange} t={props.t} />
      <section className="surface advanced-debug-settings" aria-labelledby="advanced-debug-title">
        <div className="settings-title">
          <span id="advanced-debug-title">{props.t('legacy.advancedDebugMode')}</span>
          <strong>{command.advancedAgentTrace ? props.t('legacy.enabled') : props.t('legacy.disabled')}</strong>
        </div>
        <p>{props.t('legacy.advancedDebugHint')}</p>
        <label className="advanced-debug-toggle">
          <input
            type="checkbox"
            checked={command.advancedAgentTrace}
            onChange={(event) => commandAgentActions.setAdvancedAgentTrace(event.target.checked)}
          />
          <span>{props.t('legacy.showAgentTrace')}</span>
        </label>
      </section>
    </section>
  );
}
