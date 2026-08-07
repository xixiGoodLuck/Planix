import { useSyncExternalStore } from 'react';
import {
  ApiHttpError,
  ApiNetworkError,
  CommandStreamError,
  approveCommandAction,
  deleteCommandThread,
  fetchCommandThread,
  listCommandThreads,
  runCommandChat,
  type CommandChatEvent
} from '../lib/api';
import type { CommandMessage, CommandMode, CommandPermission, CommandThreadSummary } from '../types';
import { loadAdvancedAgentTrace, saveAdvancedAgentTrace } from '../lib/storage';
import { todayISO } from '../utils/date';

export interface CommandThreadMessage {
  id: string;
  role: 'user' | 'assistant' | 'card';
  content: string;
  createdAt: number;
  kind?: 'error' | 'runtime' | 'summary' | 'plan_detail' | 'refined_tasks_result' | 'calendar_preview' | 'approval' | 'calendar_write_result' | 'command_decision' | 'plan_search_results' | 'memory_search_results' | 'note_search_results' | 'plan_patch_preview' | 'plan_patch_result' | 'memory_write_preview' | 'memory_write_result' | 'note_write_preview' | 'note_write_result' | 'planning_session_started' | 'agent_decision' | 'agent_message' | 'planning_session_status' | 'goal_understanding' | 'model_usage' | 'clarify_question' | 'execution_result';
  status?: 'running' | 'success' | 'error';
  title?: string;
  draftId?: string;
  actionId?: string;
  payload?: Record<string, unknown>;
  streaming?: boolean;
}

export type CommandWorkspaceStatus =
  | 'idle'
  | 'running'
  | 'waiting_clarification'
  | 'waiting_confirmation'
  | 'blocked_model'
  | 'accepted'
  | 'unconfirmed'
  | 'failed';

export interface CommandWorkspaceSummary {
  id: string;
  threadId?: string;
  title: string;
  messageCount: number;
  status: CommandWorkspaceStatus;
  sending: boolean;
  updatedAt: number;
  error?: string;
}

type CommandWorkspace = CommandWorkspaceSummary & {
  messages: CommandThreadMessage[];
  loading: boolean;
};

type CommandAgentState = {
  activeWorkspaceId: string;
  workspaces: Record<string, CommandWorkspace>;
  workspaceOrder: string[];
  workspaceList: CommandWorkspaceSummary[];
  threadId?: string;
  messages: CommandThreadMessage[];
  threads: CommandThreadSummary[];
  permission: CommandPermission;
  mode: CommandMode;
  advancedAgentTrace: boolean;
  sending: boolean;
  canSend: boolean;
  runningWorkspaceCount: number;
  concurrencyLimit: 1 | 2;
  drawerOpen: boolean;
  loadingThreads: boolean;
};

const listeners = new Set<() => void>();
const PLANNING_CONCURRENCY_SESSION_KEY = 'planix_planning_concurrency_limit';

function loadPlanningConcurrencyLimit(): 1 | 2 {
  try {
    return globalThis.sessionStorage?.getItem(PLANNING_CONCURRENCY_SESSION_KEY) === '1' ? 1 : 2;
  } catch {
    return 2;
  }
}

function savePlanningConcurrencyLimit(value: 1 | 2) {
  try {
    globalThis.sessionStorage?.setItem(PLANNING_CONCURRENCY_SESSION_KEY, String(value));
  } catch {
    // Storage can be unavailable in privacy-restricted WebViews. The current
    // in-memory batch still remains safely downgraded.
  }
}

function createWorkspace(id = createId('workspace')): CommandWorkspace {
  return {
    id,
    messages: [],
    title: '',
    messageCount: 0,
    status: 'idle',
    sending: false,
    loading: false,
    updatedAt: Date.now()
  };
}

const initialWorkspace = createWorkspace();

let state: CommandAgentState = projectActiveWorkspace({
  activeWorkspaceId: initialWorkspace.id,
  workspaces: { [initialWorkspace.id]: initialWorkspace },
  workspaceOrder: [initialWorkspace.id],
  workspaceList: [],
  messages: [],
  threads: [],
  permission: 'low',
  mode: 'auto',
  advancedAgentTrace: loadAdvancedAgentTrace(),
  sending: false,
  canSend: true,
  runningWorkspaceCount: 0,
  concurrencyLimit: loadPlanningConcurrencyLimit(),
  drawerOpen: false,
  loadingThreads: false
});

function emit() {
  listeners.forEach((listener) => listener());
}

function updateState(updater: (current: CommandAgentState) => CommandAgentState) {
  state = projectActiveWorkspace(updater(state));
  emit();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return state;
}

function createId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function projectActiveWorkspace(current: CommandAgentState): CommandAgentState {
  const active = current.workspaces[current.activeWorkspaceId] || createWorkspace(current.activeWorkspaceId);
  const runningWorkspaceCount = Object.values(current.workspaces).filter((workspace) => workspace.sending).length;
  const workspaceList = current.workspaceOrder
    .map((id) => current.workspaces[id])
    .filter((workspace): workspace is CommandWorkspace => Boolean(workspace))
    .map((workspace) => ({
      id: workspace.id,
      threadId: workspace.threadId,
      title: workspace.title,
      messageCount: workspace.messages.length,
      status: workspace.status,
      sending: workspace.sending,
      updatedAt: workspace.updatedAt,
      error: workspace.error
    }));
  return {
    ...current,
    threadId: active.threadId,
    messages: active.messages,
    sending: active.sending,
    canSend: !active.sending && !active.loading && runningWorkspaceCount < current.concurrencyLimit,
    runningWorkspaceCount,
    workspaceList
  };
}

function updateWorkspace(
  current: CommandAgentState,
  workspaceId: string,
  updater: (workspace: CommandWorkspace) => CommandWorkspace
): CommandAgentState {
  const workspace = current.workspaces[workspaceId];
  if (!workspace) return current;
  const next = updater(workspace);
  return {
    ...current,
    workspaces: {
      ...current.workspaces,
      [workspaceId]: {
        ...next,
        messageCount: next.messages.length,
        updatedAt: Date.now()
      }
    }
  };
}

function addWorkspaceMessage(message: Omit<CommandThreadMessage, 'id' | 'createdAt'>, workspaceId = state.activeWorkspaceId): string {
  const id = createId(message.role);
  updateState((current) => updateWorkspace(current, workspaceId, (workspace) => ({
    ...workspace,
    messages: [...workspace.messages, { ...message, id, createdAt: Date.now() }]
  })));
  return id;
}

function replaceMessage(workspaceId: string, id: string, patch: Partial<CommandThreadMessage>) {
  updateState((current) => updateWorkspace(current, workspaceId, (workspace) => ({
    ...workspace,
    messages: workspace.messages.map((message) => (
      message.id === id ? { ...message, ...patch } : message
    ))
  })));
}

function appendAssistantDelta(workspaceId: string, id: string, delta: string) {
  updateState((current) => updateWorkspace(current, workspaceId, (workspace) => ({
    ...workspace,
    messages: workspace.messages.map((message) => (
      message.id === id ? { ...message, content: `${message.content}${delta}` } : message
    ))
  })));
}

const CARD_KINDS = new Set([
  'error',
  'runtime',
  'summary',
  'plan_detail',
  'refined_tasks_result',
  'calendar_preview',
  'approval',
  'calendar_write_result',
  'command_decision',
  'plan_search_results',
  'memory_search_results',
  'note_search_results',
  'plan_patch_preview',
  'plan_patch_result',
  'memory_write_preview',
  'memory_write_result',
  'note_write_preview',
  'note_write_result',
  'planning_session_started',
  'agent_decision',
  'agent_message',
  'planning_session_status',
  'goal_understanding',
  'model_usage',
  'clarify_question',
  'execution_result'
]);

function toThreadMessage(message: CommandMessage): CommandThreadMessage {
  const role = message.role === 'card' ? 'card' : message.role === 'user' ? 'user' : 'assistant';
  const kind = CARD_KINDS.has(message.kind || '') ? message.kind as CommandThreadMessage['kind'] : undefined;
  return {
    id: message.id,
    role,
    content: message.content,
    createdAt: Number.isFinite(Date.parse(message.createdAt)) ? Date.parse(message.createdAt) : Date.now(),
    kind,
    status: kind === 'error' ? 'error' : kind ? 'success' : undefined,
    title: message.payload?.title ? String(message.payload.title) : undefined,
    draftId: message.payload?.draftId ? String(message.payload.draftId) : undefined,
    actionId: message.payload?.actionId ? String(message.payload.actionId) : undefined,
    payload: message.payload
  };
}

function setPermission(permission: CommandPermission) {
  updateState((current) => ({ ...current, permission }));
}

function setAdvancedAgentTrace(advancedAgentTrace: boolean) {
  saveAdvancedAgentTrace(advancedAgentTrace);
  updateState((current) => ({ ...current, advancedAgentTrace }));
}

function toggleChatMode() {
  updateState((current) => ({
    ...current,
    mode: current.mode === 'chat' ? 'auto' : 'chat'
  }));
}

function toggleWorkbench() {
  updateState((current) => ({
    ...current,
    mode: current.mode === 'workbench' ? 'auto' : 'workbench'
  }));
}

function setDrawerOpen(drawerOpen: boolean) {
  updateState((current) => ({ ...current, drawerOpen }));
  if (drawerOpen) {
    void refreshThreads();
  }
}

async function refreshThreads() {
  updateState((current) => ({ ...current, loadingThreads: true }));
  try {
    const threads = await listCommandThreads(50);
    updateState((current) => ({ ...current, threads, loadingThreads: false }));
  } catch {
    updateState((current) => ({ ...current, loadingThreads: false }));
  }
}

function newThread(): string {
  const workspace = createWorkspace();
  updateState((current) => ({
    ...current,
    activeWorkspaceId: workspace.id,
    workspaces: { ...current.workspaces, [workspace.id]: workspace },
    workspaceOrder: [workspace.id, ...current.workspaceOrder]
  }));
  void refreshThreads();
  return workspace.id;
}

function selectWorkspace(workspaceId: string) {
  if (!state.workspaces[workspaceId]) return;
  updateState((current) => ({ ...current, activeWorkspaceId: workspaceId, drawerOpen: false }));
}

async function loadThread(threadId: string) {
  if (!threadId) return;
  const existing = Object.values(state.workspaces).find((workspace) => workspace.threadId === threadId);
  const workspace = existing || { ...createWorkspace(), threadId };
  updateState((current) => ({
    ...current,
    activeWorkspaceId: workspace.id,
    drawerOpen: false,
    workspaces: existing
      ? current.workspaces
      : { ...current.workspaces, [workspace.id]: workspace },
    workspaceOrder: existing
      ? current.workspaceOrder
      : [workspace.id, ...current.workspaceOrder]
  }));
  if (workspace.sending || workspace.loading) return;
  updateState((current) => updateWorkspace(current, workspace.id, (item) => ({
    ...item,
    loading: true,
    error: undefined
  })));
  try {
    const thread = await fetchCommandThread(threadId);
    updateState((current) => updateWorkspace(current, workspace.id, (item) => ({
      ...item,
      threadId: thread.id,
      title: thread.title,
      messages: thread.messages.map(toThreadMessage),
      status: deriveWorkspaceStatus(thread.messages.map(toThreadMessage)),
      loading: false,
      error: undefined
    })));
    void refreshThreads();
  } catch (err) {
    addWorkspaceMessage({
      role: 'card',
      kind: 'error',
      content: commandErrorText(err)
    }, workspace.id);
    updateState((current) => updateWorkspace(current, workspace.id, (item) => ({
      ...item,
      loading: false,
      status: 'failed',
      error: commandErrorText(err)
    })));
  }
}

async function removeThread(threadId: string) {
  if (!threadId) return;
  const workspace = Object.values(state.workspaces).find((item) => item.threadId === threadId);
  if (workspace?.sending) return;
  try {
    await deleteCommandThread(threadId);
    updateState((current) => {
      const removedId = Object.values(current.workspaces).find((item) => item.threadId === threadId)?.id;
      if (!removedId) {
        return { ...current, threads: current.threads.filter((thread) => thread.id !== threadId) };
      }
      const workspaces = { ...current.workspaces };
      delete workspaces[removedId];
      const workspaceOrder = current.workspaceOrder.filter((id) => id !== removedId);
      let activeWorkspaceId = current.activeWorkspaceId;
      if (activeWorkspaceId === removedId) {
        if (workspaceOrder.length) {
          activeWorkspaceId = workspaceOrder[0];
        } else {
          const replacement = createWorkspace();
          workspaces[replacement.id] = replacement;
          workspaceOrder.push(replacement.id);
          activeWorkspaceId = replacement.id;
        }
      }
      return {
        ...current,
        activeWorkspaceId,
        workspaces,
        workspaceOrder,
        threads: current.threads.filter((thread) => thread.id !== threadId)
      };
    });
    void refreshThreads();
  } catch (err) {
    if (workspace) {
      addWorkspaceMessage({
        role: 'card',
        kind: 'error',
        content: commandErrorText(err)
      }, workspace.id);
      updateState((current) => updateWorkspace(current, workspace.id, (item) => ({
        ...item,
        status: 'failed',
        error: commandErrorText(err)
      })));
    }
  }
}

function removeWorkspace(workspaceId: string) {
  const workspace = state.workspaces[workspaceId];
  if (!workspace || workspace.sending) return;
  if (workspace.threadId) {
    void removeThread(workspace.threadId);
    return;
  }
  updateState((current) => {
    const workspaces = { ...current.workspaces };
    delete workspaces[workspaceId];
    const workspaceOrder = current.workspaceOrder.filter((id) => id !== workspaceId);
    let activeWorkspaceId = current.activeWorkspaceId;
    if (activeWorkspaceId === workspaceId) {
      const replacement = workspaceOrder[0] ? undefined : createWorkspace();
      if (replacement) {
        workspaces[replacement.id] = replacement;
        workspaceOrder.push(replacement.id);
      }
      activeWorkspaceId = workspaceOrder[0] || replacement!.id;
    }
    return { ...current, activeWorkspaceId, workspaces, workspaceOrder };
  });
}

function commandErrorText(error: unknown): string {
  if (error instanceof ApiHttpError && error.status === 404) {
    return 'Command 接口未加载，请重启后端服务';
  }
  if (error instanceof ApiNetworkError) {
    return '后端服务未启动或连接失败';
  }
  if (error instanceof CommandStreamError) {
    return error.message || 'Command 执行流中断，请重启后端服务后重试';
  }
  return error instanceof Error ? error.message : String(error);
}

function deriveWorkspaceStatus(messages: CommandThreadMessage[]): CommandWorkspaceStatus {
  for (const message of [...messages].reverse()) {
    if (message.kind === 'error') return 'failed';
    if (message.kind === 'clarify_question') return 'waiting_clarification';
    if (message.kind === 'goal_understanding') {
      const payload = message.payload || {};
      const intentState = String(payload.intentState || '').toLowerCase();
      const nextQuestion = String(payload.nextQuestion || '').trim();
      const warnings = Array.isArray(payload.consistencyWarnings) ? payload.consistencyWarnings : [];
      if (intentState === 'ambiguous_goal' || nextQuestion || warnings.length) return 'waiting_clarification';
    }
    if (message.kind !== 'planning_session_status') continue;
    const payload = message.payload || {};
    const status = String(payload.status || message.content || '').toLowerCase();
    const runtimeStatus = String(payload.runtimeStatus || '').toLowerCase();
    const data = payload.data as Record<string, unknown> | undefined;
    if (
      runtimeStatus === 'blocked_model' ||
      status === 'model_unavailable' ||
      payload.modelFailure ||
      data?.modelFailure
    ) return 'blocked_model';
    if (status === 'waiting_understanding_confirmation' || status === 'waiting_final_review') return 'waiting_confirmation';
    if (status.includes('failed') || status.includes('error')) return 'failed';
  }
  return 'idle';
}

function eventWorkspaceStatus(event: CommandChatEvent): CommandWorkspaceStatus | undefined {
  if (event.type === 'clarify_question') return 'waiting_clarification';
  if (event.type === 'goal_understanding') {
    const intentState = String(event.intentState || '').toLowerCase();
    const nextQuestion = String(event.nextQuestion || '').trim();
    const warnings = Array.isArray(event.consistencyWarnings) ? event.consistencyWarnings : [];
    if (intentState === 'ambiguous_goal' || nextQuestion || warnings.length) return 'waiting_clarification';
  }
  if (event.type === 'planning_session_status') {
    const status = String(event.status || '').toLowerCase();
    const runtimeStatus = String(event.runtimeStatus || event.data?.runtimeStatus || '').toLowerCase();
    if (runtimeStatus === 'blocked_model' || status === 'model_unavailable' || event.modelFailure || event.data?.modelFailure) {
      return 'blocked_model';
    }
    if (status === 'waiting_understanding_confirmation' || status === 'waiting_final_review') return 'waiting_confirmation';
    if (status.includes('failed') || status.includes('error')) return 'failed';
  }
  if (event.type === 'runtime_event' && event.status === 'error') return 'failed';
  return undefined;
}

function containsDeepSeekRateLimit(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false;
  const record = value as Record<string, unknown>;
  const provider = String(record.provider || '').toLowerCase();
  const errorType = String(record.errorType || record.error_type || '').toLowerCase();
  if (provider.includes('deepseek') && (errorType.includes('rate') || errorType === '429')) return true;
  return Object.values(record).some((item) => (
    Array.isArray(item)
      ? item.some(containsDeepSeekRateLimit)
      : containsDeepSeekRateLimit(item)
  ));
}

function applyStreamEventState(workspaceId: string, event: CommandChatEvent) {
  const status = eventWorkspaceStatus(event);
  const rateLimited = containsDeepSeekRateLimit(event) || (
    event.type === 'error' && /deepseek/i.test(event.error) && /(rate.?limit|429)/i.test(event.error)
  );
  if (rateLimited) savePlanningConcurrencyLimit(1);
  updateState((current) => {
    const next = updateWorkspace(current, workspaceId, (workspace) => ({
      ...workspace,
      threadId: event.type === 'thread' ? event.threadId : workspace.threadId,
      status: status || workspace.status,
      error: status === 'failed' && event.type === 'error' ? event.error : workspace.error
    }));
    return rateLimited ? { ...next, concurrencyLimit: 1 } : next;
  });
}

function addEventCard(event: CommandChatEvent, t: (key: string) => string, workspaceId: string) {
  const addMessage = (message: Omit<CommandThreadMessage, 'id' | 'createdAt'>) => (
    addWorkspaceMessage(message, workspaceId)
  );
  if (event.type === 'runtime_started') {
    addMessage({
      role: 'card',
      kind: 'runtime',
      status: 'running',
      title: t('command.execution'),
      content: event.message
    });
  }
  if (event.type === 'runtime_event') {
    addMessage({
      role: 'card',
      kind: 'runtime',
      status: event.status,
      title: event.name,
      content: event.summary || event.name
    });
  }
  if (event.type === 'draft_created') {
    addMessage({
      role: 'card',
      kind: 'runtime',
      status: 'success',
      title: t('command.hiddenDraft'),
      content: `${t('command.hiddenDraftCreated')} v${event.version}`,
      draftId: event.draftId,
      payload: { ...event }
    });
  }
  if (event.type === 'summary') {
    addMessage({
      role: 'card',
      kind: 'summary',
      status: 'success',
      title: t('command.planSummary'),
      content: event.text,
      draftId: event.draftId,
      payload: { ...event }
    });
  }
  if (event.type === 'plan_detail') {
    addMessage({
      role: 'card',
      kind: 'plan_detail',
      status: 'success',
      title: event.title,
      content: event.title,
      draftId: event.draftId,
      payload: { ...event }
    });
  }
  if (event.type === 'refinement_started') {
    addMessage({
      role: 'card',
      kind: 'runtime',
      status: 'running',
      title: t('command.refiningTasks'),
      content: `${t('command.refiningTasksCount')} ${event.total}`,
      draftId: event.draftId,
      payload: { ...event }
    });
  }
  if (event.type === 'refined_tasks_result') {
    addMessage({
      role: 'card',
      kind: 'refined_tasks_result',
      status: event.failed > 0 ? 'error' : 'success',
      title: t('command.refinedTasksResult'),
      content: `${t('command.refinedSucceeded')} ${event.succeeded} · ${t('command.failed')} ${event.failed}`,
      draftId: event.draftId,
      payload: { ...event }
    });
  }
  if (event.type === 'calendar_plan_preview') {
    addMessage({
      role: 'card',
      kind: 'calendar_preview',
      status: 'running',
      title: t('command.calendarPreviewTitle'),
      content: event.title,
      draftId: event.draftId,
      actionId: event.actionId,
      payload: { ...event }
    });
  }
  if (event.type === 'approval_required') {
    addMessage({
      role: 'card',
      kind: 'approval',
      status: 'running',
      title: t('command.approvalRequired'),
      content: event.summary,
      draftId: event.draftId,
      actionId: event.actionId,
      payload: { ...event }
    });
  }
  if (event.type === 'calendar_write_result') {
    addMessage({
      role: 'card',
      kind: 'calendar_write_result',
      status: event.failed > 0 ? 'error' : 'success',
      title: t('command.calendarWriteResult'),
      content: `${t('command.created')} ${event.created} · ${t('command.updated')} ${event.updated} · ${t('command.failed')} ${event.failed}`,
      actionId: event.actionId,
      payload: { ...event }
    });
  }
  if (event.type === 'command_decision') {
    addMessage({
      role: 'card',
      kind: 'command_decision',
      status: 'success',
      title: t('command.intentDecision'),
      content: event.decisionSummary || event.intent,
      payload: { ...event }
    });
  }
  if (event.type === 'plan_search_results') {
    addMessage({
      role: 'card',
      kind: 'plan_search_results',
      status: 'success',
      title: t('command.planSearchResults'),
      content: event.summary,
      payload: { ...event }
    });
  }
  if (event.type === 'memory_search_results') {
    addMessage({
      role: 'card',
      kind: 'memory_search_results',
      status: 'success',
      title: t('command.memorySearchResults'),
      content: event.summary,
      payload: { ...event }
    });
  }
  if (event.type === 'note_search_results') {
    addMessage({
      role: 'card',
      kind: 'note_search_results',
      status: 'success',
      title: t('command.noteSearchResults'),
      content: event.summary,
      payload: { ...event }
    });
  }
  if (event.type === 'plan_patch_preview') {
    addMessage({
      role: 'card',
      kind: 'plan_patch_preview',
      status: 'running',
      title: t('command.planPatchPreview'),
      content: event.operation,
      actionId: event.actionId,
      payload: { ...event }
    });
  }
  if (event.type === 'plan_patch_result') {
    addMessage({
      role: 'card',
      kind: 'plan_patch_result',
      status: event.status === 'success' ? 'success' : 'error',
      title: t('command.planPatchResult'),
      content: event.error || event.status,
      actionId: event.actionId,
      payload: { ...event }
    });
  }
  if (event.type === 'memory_write_preview') {
    addMessage({
      role: 'card',
      kind: 'memory_write_preview',
      status: 'running',
      title: t('command.memoryWritePreview'),
      content: event.content,
      actionId: event.actionId,
      payload: { ...event }
    });
  }
  if (event.type === 'memory_write_result') {
    addMessage({
      role: 'card',
      kind: 'memory_write_result',
      status: event.status === 'success' ? 'success' : 'error',
      title: t('command.memoryWriteResult'),
      content: event.error || event.content || event.status,
      actionId: event.actionId,
      payload: { ...event }
    });
  }
  if (event.type === 'note_write_preview') {
    addMessage({
      role: 'card',
      kind: 'note_write_preview',
      status: 'running',
      title: t('command.noteWritePreview'),
      content: event.noteText,
      actionId: event.actionId,
      payload: { ...event }
    });
  }
  if (event.type === 'note_write_result') {
    addMessage({
      role: 'card',
      kind: 'note_write_result',
      status: event.status === 'success' ? 'success' : 'error',
      title: t('command.noteWriteResult'),
      content: event.error || event.noteText || event.status,
      actionId: event.actionId,
      payload: { ...event }
    });
  }
  if (event.type === 'planning_session_started') {
    addMessage({
      role: 'card',
      kind: 'planning_session_started',
      status: 'running',
      title: t('command.planningSessionStarted'),
      content: event.status,
      payload: { ...event }
    });
  }
  if (event.type === 'goal_understanding') {
    addMessage({
      role: 'card',
      kind: 'goal_understanding',
      status: event.consistencyWarnings?.length ? 'error' : 'success',
      title: t('command.goalUnderstanding'),
      content: event.nextQuestion || String(event.understoodIntent || t('command.goalUnderstanding')),
      payload: { ...event }
    });
  }
  if (event.type === 'agent_decision') {
    const data = event.data && typeof event.data === 'object' ? event.data as Record<string, unknown> : {};
    addMessage({
      role: 'card',
      kind: 'agent_decision',
      status: data.decision === 'block' ? 'error' : 'success',
      title: t('command.agentDecision'),
      content: String(data.userVisibleSummary || data.reason || data.decision || t('command.agentDecision')),
      payload: { ...event }
    });
  }
  if (event.type === 'agent_message') {
    const data = event.data && typeof event.data === 'object' ? event.data as Record<string, unknown> : {};
    addMessage({
      role: 'card',
      kind: 'agent_message',
      status: data.messageType === 'block' ? 'error' : 'success',
      title: t('command.agentMessage'),
      content: String(data.reason || data.messageType || t('command.agentMessage')),
      payload: { ...event }
    });
  }
  if (event.type === 'planning_session_status') {
    addMessage({
      role: 'card',
      kind: 'planning_session_status',
      status: event.status === 'written_to_calendar' ? 'success' : 'running',
      title: t('command.planningSessionStatus'),
      content: event.status,
      payload: { ...event }
    });
  }
  if (event.type === 'model_usage') {
    addMessage({
      role: 'card',
      kind: 'model_usage',
      status: 'success',
      title: t('command.modelUsage'),
      content: '',
      payload: { ...event }
    });
  }
  if (event.type === 'clarify_question') {
    addMessage({
      role: 'card',
      kind: 'clarify_question',
      status: 'running',
      title: t('command.clarifyQuestion'),
      content: event.question,
      payload: { ...event }
    });
  }
  if (event.type === 'execution_result') {
    addMessage({
      role: 'card',
      kind: 'execution_result',
      status: event.status === 'success' ? 'success' : 'error',
      title: t('command.executionResult'),
      content: event.text,
      actionId: event.actionId,
      payload: { ...event }
    });
  }
}

function createStreamHandler(t: (key: string) => string, workspaceId: string) {
  let assistantId = '';
  let sawOutput = false;
  let sawDelta = false;
  return {
    get sawOutput() {
      return sawOutput;
    },
    finish() {
      if (assistantId && sawDelta) {
        replaceMessage(workspaceId, assistantId, { streaming: false });
      }
    },
    onEvent(event: CommandChatEvent) {
      applyStreamEventState(workspaceId, event);
      if (event.type === 'assistant_delta') {
        if (!assistantId) {
          assistantId = addWorkspaceMessage({
            role: 'assistant',
            content: '',
            streaming: true
          }, workspaceId);
        }
        const delta = event.text ?? event.content ?? '';
        if (delta) {
          sawOutput = true;
          sawDelta = true;
          appendAssistantDelta(workspaceId, assistantId, delta);
        }
      }
      if (
        event.type === 'runtime_started' ||
        event.type === 'runtime_event' ||
        event.type === 'draft_created' ||
        event.type === 'summary' ||
        event.type === 'plan_detail' ||
        event.type === 'refinement_started' ||
        event.type === 'refined_tasks_result' ||
        event.type === 'calendar_plan_preview' ||
        event.type === 'approval_required' ||
        event.type === 'calendar_write_result' ||
        event.type === 'command_decision' ||
        event.type === 'plan_search_results' ||
        event.type === 'memory_search_results' ||
        event.type === 'note_search_results' ||
        event.type === 'plan_patch_preview' ||
        event.type === 'plan_patch_result' ||
        event.type === 'memory_write_preview' ||
        event.type === 'memory_write_result' ||
        event.type === 'note_write_preview' ||
        event.type === 'note_write_result' ||
        event.type === 'planning_session_started' ||
        event.type === 'agent_decision' ||
        event.type === 'agent_message' ||
        event.type === 'planning_session_status' ||
        event.type === 'goal_understanding' ||
        event.type === 'model_usage' ||
        event.type === 'clarify_question' ||
        event.type === 'execution_result'
      ) {
        sawOutput = true;
        addEventCard(event, t, workspaceId);
      }
      if (event.type === 'error') {
        throw new Error(event.error);
      }
    }
  };
}

function canStartWorkspace(workspaceId: string): boolean {
  const workspace = state.workspaces[workspaceId];
  return Boolean(
    workspace &&
    !workspace.sending &&
    !workspace.loading &&
    state.runningWorkspaceCount < state.concurrencyLimit
  );
}

function sendCommand(input: string, t: (key: string) => string): false | Promise<true> {
  const trimmed = input.trim();
  const workspaceId = state.activeWorkspaceId;
  if (!trimmed || !canStartWorkspace(workspaceId)) return false;
  const workspace = state.workspaces[workspaceId];
  const history = workspace.messages
    .filter((message): message is typeof message & { role: 'user' | 'assistant' } => message.role === 'user' || message.role === 'assistant')
    .map((message) => ({ role: message.role, content: message.content }))
    .filter((message) => message.content.trim())
    .slice(-20);
  const forcePlanning = trimmed === t('command.startDeepPlanningMessage');
  const payload = {
    threadId: workspace.threadId,
    message: trimmed,
    mode: forcePlanning ? 'auto' as const : state.mode,
    permission: state.permission,
    context: { date: todayISO() },
    history
  };
  addWorkspaceMessage({ role: 'user', content: trimmed }, workspaceId);
  updateState((current) => updateWorkspace(current, workspaceId, (item) => ({
    ...item,
    title: item.title || trimmed,
    sending: true,
    status: 'running',
    error: undefined
  })));

  return (async () => {
    let stream: ReturnType<typeof createStreamHandler> | undefined;
    try {
      stream = createStreamHandler(t, workspaceId);
      await runCommandChat(payload, {
        onEvent: stream.onEvent
      });
      stream.finish();
      if (!stream.sawOutput) {
        addWorkspaceMessage({ role: 'assistant', content: t('command.emptyReply') }, workspaceId);
      }
    } catch (err) {
      stream?.finish();
      const error = commandErrorText(err);
      addWorkspaceMessage({
        role: 'card',
        kind: 'error',
        content: error
      }, workspaceId);
      updateState((current) => updateWorkspace(current, workspaceId, (item) => ({
        ...item,
        status: item.status === 'blocked_model'
          ? item.status
          : item.threadId
            ? 'failed'
            : 'unconfirmed',
        error
      })));
    } finally {
      updateState((current) => updateWorkspace(current, workspaceId, (item) => ({
        ...item,
        sending: false,
        status: item.status === 'running' ? deriveWorkspaceStatus(item.messages) : item.status
      })));
      void refreshThreads();
    }
    return true as const;
  })();
}

function approveAction(actionId: string, decision: 'approve' | 'reject', t: (key: string) => string): false | Promise<true> {
  const workspaceId = state.activeWorkspaceId;
  if (!actionId || !canStartWorkspace(workspaceId)) return false;
  const workspace = state.workspaces[workspaceId];
  const permission = state.permission;
  updateState((current) => updateWorkspace(current, workspaceId, (item) => ({
    ...item,
    sending: true,
    status: 'running',
    error: undefined
  })));
  return (async () => {
    let stream: ReturnType<typeof createStreamHandler> | undefined;
    try {
      stream = createStreamHandler(t, workspaceId);
      await approveCommandAction({
        threadId: workspace.threadId,
        actionId,
        decision,
        permission
      }, {
        onEvent: stream.onEvent
      });
      stream.finish();
      if (!stream.sawOutput) {
        addWorkspaceMessage({ role: 'assistant', content: t('command.emptyReply') }, workspaceId);
      }
    } catch (err) {
      stream?.finish();
      const error = commandErrorText(err);
      addWorkspaceMessage({
        role: 'card',
        kind: 'error',
        content: error
      }, workspaceId);
      updateState((current) => updateWorkspace(current, workspaceId, (item) => ({
        ...item,
        status: item.status === 'blocked_model' ? item.status : 'failed',
        error
      })));
    } finally {
      updateState((current) => updateWorkspace(current, workspaceId, (item) => ({
        ...item,
        sending: false,
        status: item.status === 'running' ? deriveWorkspaceStatus(item.messages) : item.status
      })));
      void refreshThreads();
    }
    return true as const;
  })();
}

export function useCommandAgent() {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export const commandAgentActions = {
  setPermission,
  setAdvancedAgentTrace,
  toggleChatMode,
  toggleWorkbench,
  setDrawerOpen,
  refreshThreads,
  newThread,
  selectWorkspace,
  loadThread,
  removeThread,
  removeWorkspace,
  sendCommand,
  approveAction
};
