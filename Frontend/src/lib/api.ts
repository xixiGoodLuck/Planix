import { invoke } from '@tauri-apps/api/core';
import type {
  AiModelRoutingInput, AiSettings, AiSettingsInput, AiSettingsTestResult, AppliedPlan,
  CommandPermission, CommandThread, CommandThreadSummary, ContextResetResult, ContextStats, Plan
} from '../types';

const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
const API_BASE_URL = (import.meta.env.VITE_PLANIX_API_BASE_URL || 'http://127.0.0.1:8003').replace(/\/+$/, '');

class ApiError extends Error {
  status?: number; detail?: unknown; isNetworkError?: boolean;
  constructor(message: string, options: { status?: number; detail?: unknown; isNetworkError?: boolean } = {}) {
    super(message); this.name = 'ApiError'; Object.assign(this, options);
  }
}
export class ApiNetworkError extends ApiError {
  constructor(message = '无法连接 Planix 后端服务') { super(message, { isNetworkError: true }); this.name = 'ApiNetworkError'; }
}
export class ApiHttpError extends ApiError {
  status: number;
  constructor(status: number, detail: unknown) { super(`HTTP ${status}`, { status, detail }); this.name = 'ApiHttpError'; this.status = status; }
}
export class CommandStreamError extends Error {
  constructor(message: string) { super(message); this.name = 'CommandStreamError'; }
}

function apiUrl(path: string) { return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`; }
function isJson(res: Response) { return (res.headers.get('content-type') || '').includes('application/json'); }

async function callApi<T>(method: string, path: string, body?: unknown, timeoutMs = 45000): Promise<T> {
  if (isTauri) {
    const result = await invoke<{ status: number; body: string }>('proxy_api', { req: { method, path, body: body === undefined ? '' : JSON.stringify(body) } });
    if (result.status < 200 || result.status >= 300) {
      let detail: unknown = result.body;
      try { detail = JSON.parse(result.body); } catch { /* keep text */ }
      throw new ApiHttpError(result.status, detail);
    }
    return result.status === 204 ? undefined as T : JSON.parse(result.body) as T;
  }
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(apiUrl(path), {
      method, signal: controller.signal,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    if (!response.ok) {
      let detail: unknown;
      try { detail = isJson(response) ? await response.json() : await response.text(); } catch { detail = undefined; }
      throw new ApiHttpError(response.status, detail);
    }
    return response.status === 204 ? undefined as T : await response.json() as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiNetworkError(error instanceof Error ? error.message : String(error));
  } finally { window.clearTimeout(timer); }
}

export type PlanningLocalizedText = string | { zh: string; en: string };
export type PlanningArtifactSnapshot = Record<string, unknown>;
export interface PlanningSessionResponse {
  sessionId: string; status: string; businessStatus?: string; runtimeStatus?: string;
  planningPhase?: 'UNDERSTANDING' | 'PLANNING' | 'SCHEDULING' | 'FINAL_REVIEW' | 'WRITING' | 'ACTIVE' | 'BLOCKED' | 'COMPLETED' | 'CANCELLED' | null;
  planningStep?: string | null; cognitiveMetadata?: Record<string, unknown> | null;
  understandingSnapshot?: PlanningArtifactSnapshot | null; constraintSet?: PlanningArtifactSnapshot | null;
  contextPack?: PlanningArtifactSnapshot | null; planBlueprint?: PlanningArtifactSnapshot | null;
  planQualityReport?: PlanningArtifactSnapshot | null; scheduleBlueprint?: PlanningArtifactSnapshot | null;
  scheduleQualityReport?: PlanningArtifactSnapshot | null; calendarProposal?: PlanningArtifactSnapshot | null;
  finalApprovalBundle?: PlanningArtifactSnapshot | null;
  modelFailure?: Record<string, unknown> | null; pendingInput?: { text: string; applied: false } | null;
  data?: Record<string, unknown>;
  [key: string]: unknown;
}

export type CommandChatEvent =
  | { type: 'thread'; threadId: string }
  | { type: 'message'; message: unknown }
  | { type: 'assistant_delta'; text?: string; content?: string }
  | { type: 'calendar_plan_preview'; actionId: string; draftId: string; title: string; plans: unknown[] }
  | { type: 'approval_required'; actionId: string; draftId: string; permission: CommandPermission; risk: string; summary: string; target?: string; operation?: string }
  | { type: 'calendar_write_result'; actionId?: string; created: number; updated: number; failed: number; affectedDates?: string[]; errors?: string[]; plans?: unknown[] }
  | { type: 'planning_session_started'; sessionId: string; status: string }
  | { type: 'agent_decision'; sessionId: string; data: unknown }
  | { type: 'agent_message'; sessionId: string; data: unknown }
  | ({ type: 'planning_session_status' } & PlanningSessionResponse)
  | { type: 'model_usage'; usage: unknown; feature?: string; source?: string; error?: string }
  | { type: 'clarify_question'; question: string; decision?: unknown }
  | { type: 'execution_result'; actionId?: string; status: 'success' | 'failed' | 'rejected'; text: string }
  | { type: 'done'; threadId: string }
  | { type: 'error'; error: string };

export interface CommandChatPayload { threadId?: string; message: string; permission: CommandPermission; context?: Record<string, unknown>; }
type CommandChatHandlers = { onEvent: (event: CommandChatEvent) => void; onError?: (error: Error) => void; onDone?: () => void; };

async function runCommandStream(path: string, payload: unknown, handlers: CommandChatHandlers) {
  const response = await fetch(apiUrl(path), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  if (!response.ok) { let detail: unknown; try { detail = await response.json(); } catch { detail = undefined; } throw new ApiHttpError(response.status, detail); }
  if (!response.body) throw new ApiNetworkError('Command stream is unavailable');
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read(); if (done) break;
      buffer += decoder.decode(value, { stream: true }); const lines = buffer.split(/\r?\n/); buffer = lines.pop() || '';
      for (const line of lines) if (line.trim()) { const event = JSON.parse(line) as CommandChatEvent; handlers.onEvent(event); if (event.type === 'error') throw new CommandStreamError(event.error); }
    }
    buffer += decoder.decode();
    if (buffer.trim()) { const event = JSON.parse(buffer) as CommandChatEvent; handlers.onEvent(event); if (event.type === 'error') throw new CommandStreamError(event.error); }
    handlers.onDone?.();
  } catch (error) { const normalized = error instanceof Error ? error : new CommandStreamError(String(error)); handlers.onError?.(normalized); throw normalized; }
}

export async function runCommandChat(payload: CommandChatPayload, handlers: CommandChatHandlers) { return runCommandStream('/api/command/chat', payload, handlers); }
export async function approveCommandAction(payload: { threadId?: string; actionId: string; decision: 'approve' | 'reject'; permission: CommandPermission }, handlers: CommandChatHandlers) { return runCommandStream('/api/command/approve', payload, handlers); }
export async function fetchCommandThread(threadId: string) { return callApi<CommandThread>('GET', `/api/command/thread/${encodeURIComponent(threadId)}`); }
export async function listCommandThreads(limit = 50) { return callApi<CommandThreadSummary[]>('GET', `/api/command/threads?limit=${limit}`); }
export async function deleteCommandThread(threadId: string) { return callApi<void>('DELETE', `/api/command/thread/${encodeURIComponent(threadId)}`); }

export interface BackendHealth { status: string; service: string; version: string; database: string; }
export async function fetchBackendHealth() { return callApi<BackendHealth>('GET', '/health'); }
export async function checkBackendHealth() { try { return (await fetchBackendHealth()).status === 'ok'; } catch { return false; } }
export async function fetchAiSettings() { return callApi<AiSettings>('GET', '/api/ai/settings'); }
export async function saveAiSettings(payload: AiSettingsInput) { return callApi<AiSettings>('PUT', '/api/ai/settings', payload); }
export async function saveAiSettingsRouting(payload: AiModelRoutingInput) { return callApi<AiSettings>('PUT', '/api/ai/settings/routing', payload); }
export async function deleteAiSettingsKey(provider: AiSettings['provider']) { return callApi<AiSettings>('DELETE', `/api/ai/settings/key/${provider}`); }
export async function testAiSettings() { return callApi<AiSettingsTestResult>('POST', '/api/ai/test', { prompt: 'Say OK in one short sentence.' }); }
export async function fetchContextStats() { return callApi<ContextStats>('GET', '/api/settings/context'); }
export async function resetAiContext(clearMemory = false) { return callApi<ContextResetResult>('DELETE', '/api/settings/context', { clearMemory }); }

type BackendPlan = { id: string; date: string; time: string; content: string; done: boolean; result: string; priority: 'low' | 'medium' | 'high'; estimatedMinutes: number; source: 'manual' | 'ai'; sourceKey?: string; };
function fromBackendPlan(plan: BackendPlan): Plan { return { id: plan.id, time: plan.time, title: plan.content, done: plan.done, completion: plan.result || '', priority: plan.priority, estimatedMinutes: plan.estimatedMinutes, source: plan.source, sourceKey: plan.sourceKey || '' }; }
export type PlanPatch = Partial<Pick<Plan, 'time' | 'title' | 'done' | 'completion' | 'source' | 'sourceKey' | 'priority' | 'estimatedMinutes'>>;
export function toBackendPlan(date: string, plan: Plan) { return { date, time: plan.time, content: plan.title, done: plan.done, result: plan.completion, source: plan.source || 'manual', sourceKey: plan.sourceKey || '', priority: plan.priority || 'medium', estimatedMinutes: plan.estimatedMinutes || 30 }; }
export async function fetchPlans(date: string) { return (await callApi<BackendPlan[]>('GET', `/api/plans?date=${encodeURIComponent(date)}`)).map(fromBackendPlan); }
export async function fetchMonthPlans(year: number, month: number): Promise<AppliedPlan[]> { return (await callApi<BackendPlan[]>('GET', `/api/plans/month?year=${year}&month=${month}`)).map((plan) => ({ ...fromBackendPlan(plan), date: plan.date })); }
export async function createPlan(date: string, plan: Plan) { return fromBackendPlan(await callApi<BackendPlan>('POST', '/api/plans', toBackendPlan(date, plan))); }
export async function updatePlan(id: string, patch: PlanPatch) { return fromBackendPlan(await callApi<BackendPlan>('PATCH', `/api/plans/${id}`, { time: patch.time, content: patch.title, done: patch.done, result: patch.completion, source: patch.source, sourceKey: patch.sourceKey, priority: patch.priority, estimatedMinutes: patch.estimatedMinutes })); }
export async function deletePlan(id: string) { return callApi<void>('DELETE', `/api/plans/${id}`); }
export async function clearAllPlans() { return callApi<{ deleted: number }>('DELETE', '/api/plans/all'); }
export async function fetchMonthNote(year: number, month: number) { return (await callApi<{ content: string }>('GET', `/api/month-notes?year=${year}&month=${month}`)).content; }
export async function saveRemoteMonthNote(year: number, month: number, content: string) { return callApi<void>('PUT', '/api/month-notes', { year, month, content }); }
