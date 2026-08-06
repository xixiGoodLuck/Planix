import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { CommandThreadDrawer } from './CommandThreadDrawer';

const labels: Record<string, string> = {
  'command.workspaceRunning': 'Running',
  'command.workspaceClarification': 'Needs clarification',
  'command.workspaceStrategyApproval': 'Strategy approval',
  'command.workspaceModelBlocked': 'Model blocked',
  'command.workspaceAccepted': 'Acceptance passed',
  'command.workspaceUnconfirmed': 'Input unconfirmed',
  'command.workspaceFailed': 'Failed'
};

const t = (key: string) => labels[key] || key;
const noop = () => undefined;

describe('CommandThreadDrawer workspaces', () => {
  it('shows live workspace states, disables deletion while running, and keeps unloaded history available', () => {
    const html = renderToStaticMarkup(
      <CommandThreadDrawer
        open
        threads={[
          {
            id: 'thread-running',
            title: 'Travel plan',
            messageCount: 2,
            createdAt: '2026-07-14T00:00:00Z',
            updatedAt: '2026-07-14T00:01:00Z'
          },
          {
            id: 'thread-history',
            title: 'Historical plan',
            messageCount: 12,
            createdAt: '2026-07-13T00:00:00Z',
            updatedAt: '2026-07-13T00:01:00Z'
          }
        ]}
        workspaces={[
          {
            id: 'workspace-running',
            threadId: 'thread-running',
            title: 'Travel plan',
            messageCount: 2,
            status: 'running',
            sending: true,
            updatedAt: Date.parse('2026-07-14T00:01:00Z')
          },
          {
            id: 'workspace-clarify',
            title: 'Swimming goal',
            messageCount: 1,
            status: 'waiting_clarification',
            sending: false,
            updatedAt: Date.parse('2026-07-14T00:02:00Z')
          },
          {
            id: 'workspace-blocked',
            title: 'Budget goal',
            messageCount: 4,
            status: 'blocked_model',
            sending: false,
            updatedAt: Date.parse('2026-07-14T00:03:00Z')
          },
          {
            id: 'workspace-accepted',
            title: 'Go plan',
            messageCount: 18,
            status: 'accepted',
            sending: false,
            updatedAt: Date.parse('2026-07-14T00:04:00Z')
          }
        ]}
        activeWorkspaceId="workspace-running"
        loading={false}
        onOpenChange={noop}
        onNewThread={noop}
        onSelectWorkspace={noop}
        onLoadThread={noop}
        onDeleteThread={noop}
        onDeleteWorkspace={noop}
        t={t}
      />
    );

    expect(html).toContain('command-thread-item active');
    expect(html).toContain('Running');
    expect(html).toContain('Needs clarification');
    expect(html).toContain('Model blocked');
    expect(html).toContain('Acceptance passed');
    expect(html).toContain('Historical plan');
    expect((html.match(/disabled=""/g) || [])).toHaveLength(1);
    expect((html.match(/Travel plan/g) || [])).toHaveLength(1);
  });
});
