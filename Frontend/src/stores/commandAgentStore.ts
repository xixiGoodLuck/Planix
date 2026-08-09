import { useSyncExternalStore } from 'react';
import { approveCommandAction, CommandStreamError, deleteCommandThread, fetchCommandThread, listCommandThreads, runCommandChat, type CommandChatEvent } from '../lib/api';
import { loadAdvancedAgentTrace, saveAdvancedAgentTrace } from '../lib/storage';
import type { CommandMessage, CommandPermission, CommandThreadSummary } from '../types';
import { todayISO } from '../utils/date';

export interface CommandThreadMessage {
  id: string; role: 'user' | 'assistant' | 'card'; content: string; createdAt: number;
  kind?: 'error' | 'calendar_preview' | 'approval' | 'calendar_write_result' | 'planning_session_started' | 'agent_decision' | 'agent_message' | 'planning_session_status' | 'model_usage' | 'clarify_question' | 'execution_result';
  status?: 'running' | 'success' | 'error'; title?: string; draftId?: string; actionId?: string;
  payload?: Record<string, unknown>; streaming?: boolean;
}
export type CommandWorkspaceStatus = 'idle' | 'running' | 'waiting_clarification' | 'waiting_confirmation' | 'waiting_permission' | 'blocked_model' | 'accepted' | 'unconfirmed' | 'failed';
export interface CommandWorkspaceSummary { id: string; threadId?: string; title: string; messageCount: number; status: CommandWorkspaceStatus; sending: boolean; updatedAt: number; error?: string; }
type Workspace = CommandWorkspaceSummary & { messages: CommandThreadMessage[]; loading: boolean };
type State = {
  activeWorkspaceId: string; workspaces: Record<string, Workspace>; workspaceOrder: string[]; workspaceList: CommandWorkspaceSummary[];
  threadId?: string; messages: CommandThreadMessage[]; threads: CommandThreadSummary[]; permission: CommandPermission;
  advancedAgentTrace: boolean; sending: boolean; canSend: boolean; runningWorkspaceCount: number; concurrencyLimit: 1 | 2;
  drawerOpen: boolean; loadingThreads: boolean;
};

const listeners = new Set<() => void>();
const createId = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
const createWorkspace = (id = createId('workspace')): Workspace => ({ id, title: '', messageCount: 0, status: 'idle', sending: false, loading: false, updatedAt: Date.now(), messages: [] });
const first = createWorkspace();
let state: State = project({ activeWorkspaceId: first.id, workspaces: { [first.id]: first }, workspaceOrder: [first.id], workspaceList: [], messages: [], threads: [], permission: 'low', advancedAgentTrace: loadAdvancedAgentTrace(), sending: false, canSend: true, runningWorkspaceCount: 0, concurrencyLimit: 2, drawerOpen: false, loadingThreads: false });

function project(current: State): State {
  const active = current.workspaces[current.activeWorkspaceId] || createWorkspace(current.activeWorkspaceId);
  const runningWorkspaceCount = Object.values(current.workspaces).filter((item) => item.sending).length;
  const workspaceList = current.workspaceOrder.map((id) => current.workspaces[id]).filter(Boolean).map((workspace) => ({ id: workspace.id, threadId: workspace.threadId, title: workspace.title, messageCount: workspace.messages.length, status: workspace.status, sending: workspace.sending, updatedAt: workspace.updatedAt, error: workspace.error }));
  return { ...current, workspaceList, threadId: active.threadId, messages: active.messages, sending: active.sending, runningWorkspaceCount, canSend: !active.sending && !active.loading && runningWorkspaceCount < current.concurrencyLimit };
}
function emit() { listeners.forEach((listener) => listener()); }
function update(updater: (current: State) => State) { state = project(updater(state)); emit(); }
function updateWorkspace(current: State, id: string, updater: (workspace: Workspace) => Workspace): State {
  const workspace = current.workspaces[id]; if (!workspace) return current;
  const next = updater(workspace); return { ...current, workspaces: { ...current.workspaces, [id]: { ...next, messageCount: next.messages.length, updatedAt: Date.now() } } };
}
function addMessage(workspaceId: string, message: Omit<CommandThreadMessage, 'id' | 'createdAt'>) {
  const id = createId(message.role); update((current) => updateWorkspace(current, workspaceId, (workspace) => ({ ...workspace, messages: [...workspace.messages, { ...message, id, createdAt: Date.now() }] }))); return id;
}
function patchMessage(workspaceId: string, id: string, patch: Partial<CommandThreadMessage>) {
  update((current) => updateWorkspace(current, workspaceId, (workspace) => ({ ...workspace, messages: workspace.messages.map((message) => message.id === id ? { ...message, ...patch } : message) })));
}
function toMessage(message: CommandMessage): CommandThreadMessage {
  const allowed = new Set(['error', 'calendar_preview', 'approval', 'calendar_write_result', 'planning_session_started', 'agent_decision', 'agent_message', 'planning_session_status', 'model_usage', 'clarify_question', 'execution_result']);
  return { id: message.id, role: message.role === 'user' ? 'user' : message.role === 'card' ? 'card' : 'assistant', content: message.content, createdAt: Date.parse(message.createdAt) || Date.now(), kind: allowed.has(message.kind || '') ? message.kind as CommandThreadMessage['kind'] : undefined, payload: message.payload, actionId: message.payload?.actionId ? String(message.payload.actionId) : undefined };
}
function eventCard(event: CommandChatEvent, t: (key: string) => string): Omit<CommandThreadMessage, 'id' | 'createdAt'> | null {
  if (event.type === 'planning_session_started') return { role: 'card', kind: 'planning_session_started', status: 'running', content: event.status, payload: { ...event } };
  if (event.type === 'planning_session_status') return { role: 'card', kind: 'planning_session_status', status: event.status === 'written_to_calendar' ? 'success' : 'running', content: event.status, payload: { ...event } };
  if (event.type === 'agent_decision') return { role: 'card', kind: 'agent_decision', status: 'success', content: t('command.agentDecision'), payload: { ...event } };
  if (event.type === 'agent_message') return { role: 'card', kind: 'agent_message', status: 'success', content: t('command.agentMessage'), payload: { ...event } };
  if (event.type === 'model_usage') return { role: 'card', kind: 'model_usage', status: 'success', content: '', payload: { ...event } };
  if (event.type === 'clarify_question') return { role: 'card', kind: 'clarify_question', status: 'running', content: event.question, payload: { ...event } };
  if (event.type === 'calendar_plan_preview') return { role: 'card', kind: 'calendar_preview', status: 'success', content: event.title, actionId: event.actionId, payload: { ...event } };
  if (event.type === 'approval_required') return { role: 'card', kind: 'approval', status: 'running', content: event.summary, actionId: event.actionId, payload: { ...event } };
  if (event.type === 'calendar_write_result') return { role: 'card', kind: 'calendar_write_result', status: event.failed ? 'error' : 'success', content: '', payload: { ...event } };
  if (event.type === 'execution_result') return { role: 'card', kind: 'execution_result', status: event.status === 'success' ? 'success' : 'error', content: event.text, actionId: event.actionId, payload: { ...event } };
  return null;
}
export function workspaceStatusFor(status: string, runtimeStatus = ''): CommandWorkspaceStatus {
  if (runtimeStatus === 'blocked_model') return 'blocked_model';
  if (status === 'needs_goal_clarification' || status === 'waiting_understanding_input') return 'waiting_clarification';
  if (status === 'waiting_understanding_confirmation' || status === 'waiting_final_review') return 'waiting_confirmation';
  if (status === 'waiting_calendar_write_approval') return 'waiting_permission';
  if (status === 'written_to_calendar') return 'accepted';
  return 'running';
}
function applyEvent(workspaceId: string, event: CommandChatEvent, t: (key: string) => string) {
  if (event.type === 'thread') update((current) => updateWorkspace(current, workspaceId, (workspace) => ({ ...workspace, threadId: event.threadId })));
  const card = eventCard(event, t); if (card) addMessage(workspaceId, card);
  if (event.type === 'planning_session_status') {
    const runtime = String(event.runtimeStatus || event.data?.runtimeStatus || '');
    const status = String(event.status || '');
    update((current) => updateWorkspace(current, workspaceId, (workspace) => ({ ...workspace, status: workspaceStatusFor(status, runtime) })));
  }
}
function streamHandler(workspaceId: string, t: (key: string) => string) {
  let assistantId = ''; let sawOutput = false;
  return { get sawOutput() { return sawOutput; }, finish() { if (assistantId) patchMessage(workspaceId, assistantId, { streaming: false }); }, onEvent(event: CommandChatEvent) {
    if (event.type === 'error') throw new CommandStreamError(event.error);
    if (event.type === 'assistant_delta') { if (!assistantId) assistantId = addMessage(workspaceId, { role: 'assistant', content: '', streaming: true }); const delta = event.text || event.content || ''; if (delta) { sawOutput = true; const current = state.workspaces[workspaceId]?.messages.find((message) => message.id === assistantId); patchMessage(workspaceId, assistantId, { content: `${current?.content || ''}${delta}` }); } }
    else { applyEvent(workspaceId, event, t); if (!['thread', 'message', 'done'].includes(event.type)) sawOutput = true; }
  } };
}

async function refreshThreads() { update((current) => ({ ...current, loadingThreads: true })); try { const threads = await listCommandThreads(); update((current) => ({ ...current, threads })); } finally { update((current) => ({ ...current, loadingThreads: false })); } }
function setDrawerOpen(drawerOpen: boolean) { update((current) => ({ ...current, drawerOpen })); if (drawerOpen) void refreshThreads(); }
function setPermission(permission: CommandPermission) { update((current) => ({ ...current, permission })); }
function setAdvancedAgentTrace(value: boolean) { saveAdvancedAgentTrace(value); update((current) => ({ ...current, advancedAgentTrace: value })); }
function clearContext() { const workspace = createWorkspace(); update((current) => ({ ...current, activeWorkspaceId: workspace.id, workspaces: { [workspace.id]: workspace }, workspaceOrder: [workspace.id], threads: [], drawerOpen: false, loadingThreads: false })); }
function newThread() { const workspace = createWorkspace(); update((current) => ({ ...current, activeWorkspaceId: workspace.id, workspaces: { ...current.workspaces, [workspace.id]: workspace }, workspaceOrder: [...current.workspaceOrder, workspace.id], drawerOpen: false })); }
function selectWorkspace(id: string) { if (state.workspaces[id]) update((current) => ({ ...current, activeWorkspaceId: id, drawerOpen: false })); }
async function loadThread(threadId: string) { const found = Object.values(state.workspaces).find((item) => item.threadId === threadId); if (found) return selectWorkspace(found.id); const workspace = createWorkspace(); workspace.loading = true; update((current) => ({ ...current, activeWorkspaceId: workspace.id, workspaces: { ...current.workspaces, [workspace.id]: workspace }, workspaceOrder: [...current.workspaceOrder, workspace.id] })); try { const thread = await fetchCommandThread(threadId); update((current) => updateWorkspace(current, workspace.id, (item) => ({ ...item, threadId, title: thread.title, messages: thread.messages.map(toMessage), loading: false }))); } catch { update((current) => updateWorkspace(current, workspace.id, (item) => ({ ...item, loading: false, status: 'failed' }))); } }
async function removeThread(threadId: string) { await deleteCommandThread(threadId); const found = Object.values(state.workspaces).find((item) => item.threadId === threadId); if (found) removeWorkspace(found.id); await refreshThreads(); }
function removeWorkspace(id: string) { if (state.workspaces[id]?.sending) return; update((current) => { const workspaces = { ...current.workspaces }; delete workspaces[id]; let order = current.workspaceOrder.filter((item) => item !== id); if (!order.length) { const replacement = createWorkspace(); workspaces[replacement.id] = replacement; order = [replacement.id]; } return { ...current, workspaces, workspaceOrder: order, activeWorkspaceId: current.activeWorkspaceId === id ? order[0] : current.activeWorkspaceId }; }); }

function sendCommand(input: string, t: (key: string) => string): false | Promise<true> {
  const text = input.trim(); const workspaceId = state.activeWorkspaceId; const workspace = state.workspaces[workspaceId];
  if (!text || !state.canSend || !workspace) return false;
  addMessage(workspaceId, { role: 'user', content: text }); update((current) => updateWorkspace(current, workspaceId, (item) => ({ ...item, title: item.title || text, sending: true, status: 'running', error: undefined })));
  return (async () => { const stream = streamHandler(workspaceId, t); try { await runCommandChat({ threadId: workspace.threadId, message: text, permission: state.permission, context: { date: todayISO(), timezone: Intl.DateTimeFormat().resolvedOptions().timeZone } }, stream); stream.finish(); if (!stream.sawOutput) addMessage(workspaceId, { role: 'assistant', content: t('command.emptyReply') }); } catch (error) { stream.finish(); const message = error instanceof Error ? error.message : String(error); addMessage(workspaceId, { role: 'card', kind: 'error', status: 'error', content: message }); update((current) => updateWorkspace(current, workspaceId, (item) => ({ ...item, status: 'failed', error: message }))); } finally { update((current) => updateWorkspace(current, workspaceId, (item) => ({ ...item, sending: false }))); void refreshThreads(); } return true as const; })();
}
function approveAction(actionId: string, decision: 'approve' | 'reject', t: (key: string) => string): false | Promise<true> {
  const workspaceId = state.activeWorkspaceId; const workspace = state.workspaces[workspaceId]; if (!workspace || workspace.sending) return false;
  update((current) => updateWorkspace(current, workspaceId, (item) => ({ ...item, sending: true, status: 'running' })));
  return (async () => { const stream = streamHandler(workspaceId, t); try { await approveCommandAction({ threadId: workspace.threadId, actionId, decision, permission: state.permission }, stream); stream.finish(); } catch (error) { const message = error instanceof Error ? error.message : String(error); addMessage(workspaceId, { role: 'card', kind: 'error', status: 'error', content: message }); } finally { update((current) => updateWorkspace(current, workspaceId, (item) => ({ ...item, sending: false }))); } return true as const; })();
}

export function useCommandAgent() { return useSyncExternalStore((listener) => { listeners.add(listener); return () => listeners.delete(listener); }, () => state, () => state); }
export const commandAgentActions = { setPermission, setAdvancedAgentTrace, clearContext, setDrawerOpen, refreshThreads, newThread, selectWorkspace, loadThread, removeThread, removeWorkspace, sendCommand, approveAction };
