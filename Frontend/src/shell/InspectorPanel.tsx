import { Activity, Brain, Circle, Server, TerminalSquare } from 'lucide-react';
import type { AppRoute, InspectorSnapshot } from '../types';

interface InspectorPanelProps {
  snapshot: InspectorSnapshot;
  t: (key: string) => string;
}

function formatTime(timestamp: number): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  }).format(new Date(timestamp));
}

function routeLabel(route: AppRoute, t: (key: string) => string): string {
  return t(`shell.${route}`);
}

export function InspectorPanel({ snapshot, t }: InspectorPanelProps) {
  return (
    <aside className="inspector-panel">
      <div className="inspector-head">
        <span>{t('inspector.title')}</span>
        <strong>{routeLabel(snapshot.route, t)}</strong>
      </div>

      <section className="inspector-card">
        <div className="inspector-card-title">
          <Activity size={15} />
          <span>{t('inspector.status')}</span>
        </div>
        <div className={`status-chip ${snapshot.agentStatus}`}>
          <Circle size={8} />
          {t(`inspector.${snapshot.agentStatus}`)}
        </div>
      </section>

      <section className="inspector-card">
        <div className="inspector-card-title">
          <TerminalSquare size={15} />
          <span>{t('inspector.logs')}</span>
        </div>
        <div className="log-list">
          {!snapshot.logs.length && <p>{t('inspector.noLogs')}</p>}
          {snapshot.logs.slice(0, 5).map((log) => (
            <article className={`log-item ${log.level}`} key={log.id}>
              <time>{formatTime(log.timestamp)}</time>
              <span>{log.message}</span>
            </article>
          ))}
        </div>
      </section>

      <section className="inspector-card">
        <div className="inspector-card-title">
          <Brain size={15} />
          <span>{t('inspector.memory')}</span>
        </div>
        <dl className="inspector-metrics">
          <div>
            <dt>{t('inspector.preferenceMemory')}</dt>
            <dd>{snapshot.memory.preferenceSummary}</dd>
          </div>
          <div>
            <dt>{t('inspector.materials')}</dt>
            <dd>{snapshot.memory.materialCount}</dd>
          </div>
          <div>
            <dt>{t('inspector.plans')}</dt>
            <dd>{snapshot.memory.planCount}</dd>
          </div>
        </dl>
      </section>

      <section className="inspector-card">
        <div className="inspector-card-title">
          <Server size={15} />
          <span>{t('inspector.apiState')}</span>
        </div>
        <dl className="inspector-metrics">
          <div>
            <dt>{t('inspector.apiMode')}</dt>
            <dd>{t(`inspector.${snapshot.api.mode}`)}</dd>
          </div>
          <div>
            <dt>{t('inspector.provider')}</dt>
            <dd>{snapshot.api.provider}</dd>
          </div>
          <div>
            <dt>{t('inspector.apiKey')}</dt>
            <dd>{snapshot.api.hasApiKey ? t('inspector.hasKey') : t('inspector.noKey')}</dd>
          </div>
        </dl>
      </section>
    </aside>
  );
}
