import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { ReactElement } from 'react';

const apiMocks = vi.hoisted(() => ({
  approveCommandAction: vi.fn(),
  deleteCommandThread: vi.fn(),
  fetchCommandThread: vi.fn(),
  runCommandChat: vi.fn(),
  listCommandThreads: vi.fn()
}));

vi.mock('../lib/api', () => ({
  ApiHttpError: class ApiHttpError extends Error {},
  ApiNetworkError: class ApiNetworkError extends Error {},
  CommandStreamError: class CommandStreamError extends Error {},
  approveCommandAction: apiMocks.approveCommandAction,
  deleteCommandThread: apiMocks.deleteCommandThread,
  fetchCommandThread: apiMocks.fetchCommandThread,
  listCommandThreads: apiMocks.listCommandThreads,
  runCommandChat: apiMocks.runCommandChat
}));

import { commandAgentActions, useCommandAgent } from './commandAgentStore';

function ModeProbe(): ReactElement {
  const command = useCommandAgent();
  return <span>{command.mode}</span>;
}

function renderMode(): string {
  return renderToStaticMarkup(<ModeProbe />);
}

function MessageKindsProbe(): ReactElement {
  const command = useCommandAgent();
  return <span>{command.messages.map((message) => message.kind || message.role).join(',')}</span>;
}

function renderMessageKinds(): string {
  return renderToStaticMarkup(<MessageKindsProbe />);
}

function MessagePayloadsProbe(): ReactElement {
  const command = useCommandAgent();
  return <span>{JSON.stringify(command.messages.map((message) => message.payload))}</span>;
}

function AdvancedTraceProbe(): ReactElement {
  const command = useCommandAgent();
  return <span>{String(command.advancedAgentTrace)}</span>;
}

function WorkspaceProbe(): ReactElement {
  const command = useCommandAgent();
  return (
    <div>
      <span data-active={command.activeWorkspaceId}>{command.threadId || 'unbound'}</span>
      <span data-running={command.runningWorkspaceCount} data-limit={command.concurrencyLimit} data-can-send={command.canSend} />
      {command.workspaceList.map((workspace) => (
        <span
          key={workspace.id}
          data-workspace={workspace.id}
          data-thread={workspace.threadId || 'unbound'}
          data-status={workspace.status}
          data-sending={workspace.sending}
        >
          {workspace.title}:{workspace.messageCount}
        </span>
      ))}
      <p>{command.messages.map((message) => `${message.role}:${message.content}`).join('|')}</p>
    </div>
  );
}

function renderWorkspaces(): string {
  return renderToStaticMarkup(<WorkspaceProbe />);
}

function deferred<T = void>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe('commandAgentStore workbench mode', () => {
  it('keeps advanced agent trace off by default and toggles it independently', () => {
    commandAgentActions.setAdvancedAgentTrace(false);
    expect(renderToStaticMarkup(<AdvancedTraceProbe />)).toContain('false');
    commandAgentActions.setAdvancedAgentTrace(true);
    expect(renderToStaticMarkup(<AdvancedTraceProbe />)).toContain('true');
    commandAgentActions.setAdvancedAgentTrace(false);
  });

  it('defaults to auto and only sends workbench after manual toggle', async () => {
    apiMocks.runCommandChat.mockResolvedValue(undefined);
    apiMocks.listCommandThreads.mockResolvedValue([]);

    expect(renderMode()).toContain('auto');

    commandAgentActions.toggleWorkbench();
    expect(renderMode()).toContain('workbench');

    await commandAgentActions.sendCommand('Plan my week', (key) => key);
    expect(apiMocks.runCommandChat.mock.calls[0][0]).toMatchObject({
      message: 'Plan my week',
      mode: 'workbench'
    });

    commandAgentActions.toggleWorkbench();
    expect(renderMode()).toContain('auto');

    await commandAgentActions.sendCommand('Plan my month', (key) => key);
    expect(apiMocks.runCommandChat.mock.calls[1][0]).toMatchObject({
      message: 'Plan my month',
      mode: 'auto'
    });
  });

  it('stores new command decision and usage events from the stream', async () => {
    apiMocks.runCommandChat.mockImplementationOnce(async (_payload, handlers) => {
      handlers.onEvent({
        type: 'command_decision',
        intent: 'query_plan',
        confidence: 0.86,
        targetType: 'calendar_date',
        action: 'query',
        decisionSummary: 'View today',
        source: 'llm'
      });
      handlers.onEvent({
        type: 'model_usage',
        usage: { provider: 'test', model: 'router', mode: 'llm', taskType: 'command_decision' }
      });
      handlers.onEvent({ type: 'done', threadId: 'thread-test' });
    });
    apiMocks.listCommandThreads.mockResolvedValue([]);

    commandAgentActions.newThread();
    await commandAgentActions.sendCommand('Today?', (key) => key);

    const html = renderMessageKinds();
    expect(html).toContain('command_decision');
    expect(html).toContain('model_usage');
  });

  it('stores deep planning session events from the stream for replay', async () => {
    apiMocks.runCommandChat.mockImplementationOnce(async (_payload, handlers) => {
      handlers.onEvent({ type: 'planning_session_started', sessionId: 'session-1', status: 'waiting_design_approval' });
      handlers.onEvent({
        type: 'goal_understanding',
        sessionId: 'session-1',
        intentState: 'clear_goal',
        understoodIntent: 'Learn Python',
        possibleDomains: ['learning'],
        knownFacts: { subject: 'Python' },
        uncertainties: [],
        consistencyWarnings: [],
        nextQuestion: '',
        confidence: 0.9,
        source: 'llm'
      });
      handlers.onEvent({
        type: 'goal_completion_updated',
        sessionId: 'session-1',
        businessStatus: 'strategy_pending',
        runtimeStatus: 'running',
        data: {
          complete: true,
          blockingUnknowns: [],
          optionalUnknowns: ['Preferred framework'],
          nextStage: 'strategy'
        }
      });
      handlers.onEvent({
        type: 'user_need_contract',
        sessionId: 'session-1',
        data: { interpretedGoal: 'Python plan', canMoveToDesign: true }
      });
      handlers.onEvent({
        type: 'memory_insight_brief',
        sessionId: 'session-1',
        data: { memoryHits: {}, planningInsights: {}, confidence: 0.5 }
      });
      handlers.onEvent({
        type: 'resource_brief',
        sessionId: 'session-1',
        data: { coverage: { status: 'partial', explanation: 'Some resources found.' }, resourceCandidates: [] }
      });
      handlers.onEvent({
        type: 'plan_design_proposal',
        sessionId: 'session-1',
        data: { strategyName: 'Project-driven', phases: [], status: 'waiting_user_approval' }
      });
      handlers.onEvent({
        type: 'execution_plan_draft',
        sessionId: 'session-1',
        data: { tasks: [], status: 'waiting_user_approval' }
      });
      handlers.onEvent({
        type: 'learning_update',
        sessionId: 'session-1',
        data: { feedbackType: 'resource_feedback', insight: 'Replace resource' }
      });
      handlers.onEvent({ type: 'planning_session_status', sessionId: 'session-1', status: 'waiting_execution_approval' });
      handlers.onEvent({ type: 'done', threadId: 'thread-planning' });
    });
    apiMocks.listCommandThreads.mockResolvedValue([]);

    commandAgentActions.newThread();
    await commandAgentActions.sendCommand('Plan Python', (key) => key);

    const html = renderMessageKinds();
    expect(html).toContain('planning_session_started');
    expect(html).toContain('goal_understanding');
    expect(html).toContain('goal_completion_updated');
    expect(html).toContain('user_need_contract');
    expect(html).toContain('memory_insight_brief');
    expect(html).toContain('resource_brief');
    expect(html).toContain('plan_design_proposal');
    expect(html).toContain('execution_plan_draft');
    expect(html).toContain('learning_update');
    expect(html).toContain('planning_session_status');
  });

  it('preserves recoverable planning failure and artifact state fields', async () => {
    apiMocks.runCommandChat.mockImplementationOnce(async (_payload, handlers) => {
      handlers.onEvent({
        type: 'goal_model_updated',
        sessionId: 'session-failure',
        data: {
          goalStatement: 'Learn Python',
          artifactState: 'last_confirmed'
        }
      });
      handlers.onEvent({
        type: 'goal_completion_updated',
        sessionId: 'session-failure',
        data: {
          complete: false,
          blockingUnknowns: [],
          optionalUnknowns: [],
          nextStage: 'goal_clarification',
          artifactState: 'last_confirmed'
        }
      });
      handlers.onEvent({
        type: 'planning_session_status',
        sessionId: 'session-failure',
        status: 'MODEL_UNAVAILABLE',
        businessStatus: 'goal_clarification',
        runtimeStatus: 'blocked_model',
        pendingInput: { text: 'web开发', applied: false },
        modelFailure: {
          stage: 'goal_intelligence',
          resumeNode: 'goal_intelligence',
          retryable: true,
          automaticRetryAttempted: true,
          attempts: [{ provider: 'DeepSeek', status: 'error', errorType: 'model_output_truncated' }],
          summary: { zh: '目标理解失败', en: 'Goal understanding failed' },
          action: { zh: '重试当前阶段', en: 'Retry the current stage' }
        }
      });
      handlers.onEvent({ type: 'done', threadId: 'thread-failure' });
    });
    apiMocks.listCommandThreads.mockResolvedValue([]);

    commandAgentActions.newThread();
    await commandAgentActions.sendCommand('web开发', (key) => key);

    const html = renderToStaticMarkup(<MessagePayloadsProbe />);
    expect(html).toContain('last_confirmed');
    expect(html).toContain('web开发');
    expect(html).toContain('goal_intelligence');
    expect(html).toContain('model_output_truncated');
    expect(html).toContain('automaticRetryAttempted');
  });

  it('isolates two interleaved streams while switching workspaces and binds each first thread event to its origin', async () => {
    const pending = [deferred(), deferred()];
    const handlers: Array<{ onEvent: (event: Record<string, unknown>) => void }> = [];
    apiMocks.runCommandChat.mockReset();
    apiMocks.runCommandChat.mockImplementation((_payload, streamHandlers) => {
      handlers.push(streamHandlers);
      return pending[handlers.length - 1].promise;
    });
    apiMocks.listCommandThreads.mockResolvedValue([]);

    const firstWorkspace = commandAgentActions.newThread();
    const firstSend = commandAgentActions.sendCommand('Autumn Japan trip', (key) => key);
    expect(commandAgentActions.sendCommand('Duplicate first request', (key) => key)).toBe(false);

    const secondWorkspace = commandAgentActions.newThread();
    const secondSend = commandAgentActions.sendCommand('Learn Go', (key) => key);
    expect(apiMocks.runCommandChat).toHaveBeenCalledTimes(2);
    expect(apiMocks.runCommandChat.mock.calls.map((call) => call[0].threadId)).toEqual([undefined, undefined]);

    const thirdWorkspace = commandAgentActions.newThread();
    expect(commandAgentActions.sendCommand('Third lane must not queue', (key) => key)).toBe(false);
    expect(apiMocks.runCommandChat).toHaveBeenCalledTimes(2);
    expect(renderWorkspaces()).toContain('data-running="2"');

    handlers[0].onEvent({ type: 'thread', threadId: 'thread-travel' });
    handlers[1].onEvent({ type: 'thread', threadId: 'thread-go' });
    handlers[1].onEvent({ type: 'assistant_delta', text: 'Go response' });
    handlers[0].onEvent({ type: 'assistant_delta', text: 'Travel response' });

    commandAgentActions.selectWorkspace(firstWorkspace);
    let html = renderWorkspaces();
    expect(html).toContain('thread-travel');
    expect(html).toContain('Travel response');
    expect(html).not.toContain('Go response');

    commandAgentActions.selectWorkspace(secondWorkspace);
    html = renderWorkspaces();
    expect(html).toContain('thread-go');
    expect(html).toContain('Go response');
    expect(html).not.toContain('Travel response');

    commandAgentActions.selectWorkspace(thirdWorkspace);
    expect(renderWorkspaces()).not.toContain('Third lane must not queue');

    pending[0].resolve();
    pending[1].resolve();
    await Promise.all([firstSend, secondSend]);
    expect(renderWorkspaces()).toContain('data-running="0"');
  });

  it('keeps a failed local input in its origin workspace and marks it unconfirmed before thread binding', async () => {
    apiMocks.runCommandChat.mockReset();
    apiMocks.runCommandChat.mockRejectedValueOnce(new Error('network disconnected'));
    apiMocks.listCommandThreads.mockResolvedValue([]);

    const failedWorkspace = commandAgentActions.newThread();
    await commandAgentActions.sendCommand('Keep this input', (key) => key);
    const otherWorkspace = commandAgentActions.newThread();

    commandAgentActions.selectWorkspace(failedWorkspace);
    let html = renderWorkspaces();
    expect(html).toContain('Keep this input');
    expect(html).toContain('network disconnected');
    expect(html).toContain(`data-workspace="${failedWorkspace}"`);
    expect(html).toContain('data-status="unconfirmed"');

    commandAgentActions.selectWorkspace(otherWorkspace);
    html = renderWorkspaces();
    expect(html).toContain(`data-active="${otherWorkspace}"`);
    expect(html).toContain('<p></p>');
  });

  it('routes a background workspace deletion error back to that workspace only', async () => {
    apiMocks.runCommandChat.mockReset();
    apiMocks.runCommandChat
      .mockImplementationOnce(async (_payload, handlers) => {
        handlers.onEvent({ type: 'thread', threadId: 'thread-delete-target' });
      })
      .mockImplementationOnce(async (_payload, handlers) => {
        handlers.onEvent({ type: 'thread', threadId: 'thread-delete-active' });
      });
    apiMocks.deleteCommandThread.mockReset();
    apiMocks.deleteCommandThread.mockRejectedValueOnce(new Error('delete failed'));
    apiMocks.listCommandThreads.mockResolvedValue([]);

    const targetWorkspace = commandAgentActions.newThread();
    await commandAgentActions.sendCommand('Delete target', (key) => key);
    const activeWorkspace = commandAgentActions.newThread();
    await commandAgentActions.sendCommand('Keep active clean', (key) => key);

    commandAgentActions.selectWorkspace(activeWorkspace);
    await commandAgentActions.removeThread('thread-delete-target');
    expect(renderWorkspaces()).not.toContain('delete failed');

    commandAgentActions.selectWorkspace(targetWorkspace);
    const targetHtml = renderWorkspaces();
    expect(targetHtml).toContain('delete failed');
    expect(targetHtml).toContain('data-status="failed"');
  });

  it('restores a replayed thread into its own workspace with its terminal acceptance status', async () => {
    apiMocks.fetchCommandThread.mockReset();
    apiMocks.fetchCommandThread.mockResolvedValueOnce({
      id: 'thread-replay',
      title: 'Replayed plan',
      createdAt: '2026-07-14T00:00:00Z',
      updatedAt: '2026-07-14T00:10:00Z',
      messages: [
        { id: 'user-replay', role: 'user', content: 'Replay me', createdAt: '2026-07-14T00:00:00Z' },
        {
          id: 'status-replay',
          role: 'card',
          kind: 'planning_session_status',
          content: 'waiting_execution_approval',
          payload: {
            status: 'waiting_execution_approval',
            businessStatus: 'execution_pending',
            runtimeStatus: 'idle'
          },
          createdAt: '2026-07-14T00:10:00Z'
        }
      ]
    });
    apiMocks.listCommandThreads.mockResolvedValue([]);

    await commandAgentActions.loadThread('thread-replay');
    const html = renderWorkspaces();
    expect(html).toContain('thread-replay');
    expect(html).toContain('Replay me');
    expect(html).toContain('data-status="accepted"');
  });

  it('restores model-blocked state from modelFailure when older replay lacks runtimeStatus', async () => {
    apiMocks.fetchCommandThread.mockReset();
    apiMocks.fetchCommandThread.mockResolvedValueOnce({
      id: 'thread-blocked-replay',
      title: 'Blocked plan',
      createdAt: '2026-07-14T00:00:00Z',
      updatedAt: '2026-07-14T00:10:00Z',
      messages: [{
        id: 'status-blocked-replay',
        role: 'card',
        kind: 'planning_session_status',
        content: 'strategy_pending',
        payload: {
          status: 'strategy_pending',
          modelFailure: {
            stage: 'strategy',
            resumeNode: 'strategy',
            retryable: true
          }
        },
        createdAt: '2026-07-14T00:10:00Z'
      }]
    });
    apiMocks.listCommandThreads.mockResolvedValue([]);

    await commandAgentActions.loadThread('thread-blocked-replay');
    expect(renderWorkspaces()).toContain('data-status="blocked_model"');
  });

  it('marks sparse goal understanding as waiting for clarification both live and after replay', async () => {
    apiMocks.runCommandChat.mockReset();
    apiMocks.runCommandChat.mockImplementationOnce(async (_payload, handlers) => {
      handlers.onEvent({ type: 'thread', threadId: 'thread-ambiguous-live' });
      handlers.onEvent({
        type: 'goal_understanding',
        intentState: 'ambiguous_goal',
        understoodIntent: 'Autumn Japan trip',
        consistencyWarnings: [],
        nextQuestion: 'Which city will you depart from?'
      });
    });
    apiMocks.listCommandThreads.mockResolvedValue([]);

    commandAgentActions.newThread();
    await commandAgentActions.sendCommand('I want to visit Japan this autumn', (key) => key);
    expect(renderWorkspaces()).toContain('data-status="waiting_clarification"');

    apiMocks.fetchCommandThread.mockReset();
    apiMocks.fetchCommandThread.mockResolvedValueOnce({
      id: 'thread-ambiguous-replay',
      title: 'Sparse travel goal',
      createdAt: '2026-07-14T00:00:00Z',
      updatedAt: '2026-07-14T00:01:00Z',
      messages: [{
        id: 'goal-understanding-replay',
        role: 'card',
        kind: 'goal_understanding',
        content: 'Which city will you depart from?',
        payload: {
          intentState: 'ambiguous_goal',
          nextQuestion: 'Which city will you depart from?',
          consistencyWarnings: []
        },
        createdAt: '2026-07-14T00:01:00Z'
      }]
    });
    await commandAgentActions.loadThread('thread-ambiguous-replay');
    expect(renderWorkspaces()).toContain('data-status="waiting_clarification"');
  });

  it('derives planning drawer states and reduces page concurrency after a DeepSeek rate limit', async () => {
    const sessionStorage = {
      getItem: vi.fn(),
      setItem: vi.fn(),
      removeItem: vi.fn(),
      clear: vi.fn(),
      key: vi.fn(),
      length: 0
    };
    vi.stubGlobal('sessionStorage', sessionStorage);
    apiMocks.runCommandChat.mockReset();
    apiMocks.runCommandChat.mockImplementationOnce(async (_payload, handlers) => {
      handlers.onEvent({ type: 'thread', threadId: 'thread-rate-limit' });
      handlers.onEvent({
        type: 'planning_session_status',
        sessionId: 'session-rate-limit',
        status: 'MODEL_UNAVAILABLE',
        businessStatus: 'strategy_pending',
        runtimeStatus: 'blocked_model',
        modelFailure: {
          stage: 'strategy',
          resumeNode: 'strategy',
          retryable: true,
          automaticRetryAttempted: true,
          attempts: [{ provider: 'DeepSeek', status: 'error', errorType: 'rate_limit' }],
          summary: 'Rate limited',
          action: 'Retry'
        }
      });
    });
    apiMocks.listCommandThreads.mockResolvedValue([]);

    commandAgentActions.newThread();
    await commandAgentActions.sendCommand('Trigger rate limit', (key) => key);
    const html = renderWorkspaces();
    expect(html).toContain('data-status="blocked_model"');
    expect(html).toContain('data-limit="1"');
    expect(html).toContain('data-running="0"');
    expect(sessionStorage.setItem).toHaveBeenCalledWith('planix_planning_concurrency_limit', '1');
    vi.unstubAllGlobals();
  });
});
