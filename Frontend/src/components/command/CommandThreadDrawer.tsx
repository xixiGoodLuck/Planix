import { History, Plus, X } from 'lucide-react';
import type { CommandWorkspaceStatus, CommandWorkspaceSummary } from '../../stores/commandAgentStore';
import type { CommandThreadSummary } from '../../types';

interface CommandThreadDrawerProps {
  open: boolean;
  threads: CommandThreadSummary[];
  workspaces: CommandWorkspaceSummary[];
  activeWorkspaceId: string;
  loading: boolean;
  onOpenChange: (open: boolean) => void;
  onNewThread: () => void;
  onSelectWorkspace: (workspaceId: string) => void;
  onLoadThread: (threadId: string) => void;
  onDeleteThread: (threadId: string) => void;
  onDeleteWorkspace: (workspaceId: string) => void;
  t: (key: string) => string;
}

function workspaceStatusLabel(status: CommandWorkspaceStatus, t: (key: string) => string): string {
  const keys: Record<CommandWorkspaceStatus, string> = {
    idle: 'command.workspaceIdle',
    running: 'command.workspaceRunning',
    waiting_clarification: 'command.workspaceClarification',
    waiting_strategy_approval: 'command.workspaceStrategyApproval',
    blocked_model: 'command.workspaceModelBlocked',
    accepted: 'command.workspaceAccepted',
    unconfirmed: 'command.workspaceUnconfirmed',
    failed: 'command.workspaceFailed'
  };
  return t(keys[status]);
}

function formatThreadTime(value: string): string {
  const time = Date.parse(value);
  if (!Number.isFinite(time)) return '';
  return new Intl.DateTimeFormat(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(new Date(time));
}

export function CommandThreadDrawer(props: CommandThreadDrawerProps) {
  const {
    open,
    threads,
    workspaces,
    activeWorkspaceId,
    loading,
    onOpenChange,
    onNewThread,
    onSelectWorkspace,
    onLoadThread,
    onDeleteThread,
    onDeleteWorkspace,
    t
  } = props;

  const localThreadIds = new Set(workspaces.flatMap((workspace) => workspace.threadId ? [workspace.threadId] : []));
  const remoteThreads = threads.filter((thread) => !localThreadIds.has(thread.id));

  return (
    <>
      <div className="command-thread-hotzone" onMouseEnter={() => onOpenChange(true)} />
      <button
        type="button"
        className={`command-thread-toggle ${open ? 'active' : ''}`}
        onClick={() => onOpenChange(!open)}
        aria-label={open ? t('command.closeThreads') : t('command.openThreads')}
        title={open ? t('command.closeThreads') : t('command.openThreads')}
      >
        <History size={18} />
      </button>
      <aside
        className={`command-thread-drawer ${open ? 'open' : ''}`}
        onMouseEnter={() => onOpenChange(true)}
        onMouseLeave={() => onOpenChange(false)}
        aria-hidden={!open}
      >
        <div className="command-thread-drawer-head">
          <div>
            <span>{t('command.threadHistory')}</span>
            <strong>{t('command.title')}</strong>
          </div>
          <button
            type="button"
            className="command-thread-new"
            onClick={() => {
              onNewThread();
              onOpenChange(false);
            }}
          >
            <Plus size={16} />
            {t('command.newThread')}
          </button>
        </div>
        <div className="command-thread-list">
          {loading && <p className="command-thread-empty">{t('command.loadingThreads')}</p>}
          {!loading && workspaces.length === 0 && remoteThreads.length === 0 && (
            <p className="command-thread-empty">{t('command.emptyThreads')}</p>
          )}
          {workspaces.map((workspace) => {
            const thread = workspace.threadId
              ? threads.find((item) => item.id === workspace.threadId)
              : undefined;
            return (
            <div
              className={`command-thread-item ${workspace.id === activeWorkspaceId ? 'active' : ''}`}
              key={workspace.id}
            >
              <button type="button" className="command-thread-load" onClick={() => onSelectWorkspace(workspace.id)}>
                <span>
                  <strong>{thread?.title || workspace.title || t('command.untitledThread')}</strong>
                  <small>
                    <i className={`command-workspace-status ${workspace.status}`}>
                      {workspaceStatusLabel(workspace.status, t)}
                    </i>
                    {thread?.currentDraftTitle || `${workspace.messageCount} ${t('command.messages')}`}
                  </small>
                </span>
                <em>{formatThreadTime(new Date(workspace.updatedAt).toISOString())}</em>
              </button>
              <button
                type="button"
                className="command-thread-delete"
                aria-label={t('command.deleteThread')}
                title={t('command.deleteThread')}
                disabled={workspace.sending}
                onClick={() => onDeleteWorkspace(workspace.id)}
              >
                <X size={14} />
              </button>
            </div>
          );})}
          {!loading && remoteThreads.map((thread) => (
            <div className="command-thread-item" key={thread.id}>
              <button type="button" className="command-thread-load" onClick={() => onLoadThread(thread.id)}>
                <span>
                  <strong>{thread.title || t('command.untitledThread')}</strong>
                  <small>{thread.currentDraftTitle || `${thread.messageCount} ${t('command.messages')}`}</small>
                </span>
                <em>{formatThreadTime(thread.updatedAt)}</em>
              </button>
              <button
                type="button"
                className="command-thread-delete"
                aria-label={t('command.deleteThread')}
                title={t('command.deleteThread')}
                onClick={() => onDeleteThread(thread.id)}
              >
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
      </aside>
    </>
  );
}
