import type {
  AgentRunRequest,
  AgentRuntimeEvent,
  AiMaterialDraft,
  AiMaterialDraftRequest,
  AiModelRoutingInput,
  AiSettings,
  AiSettingsInput,
  AiSettingsTestResult,
  AppData,
  AppliedPlan,
  CommandMode,
  CommandPermission,
  CommandThread,
  CommandThreadSummary,
  DailyReviewResponse,
  GoalPlanResponse,
  Language,
  LocalRelevance,
  MemoryCacheStats,
  MemoryInput,
  MemoryItem,
  MemoryResetResult,
  MemorySearchResult,
  PlanHorizon,
  PlanQualityReport,
  PlanQualityStatus,
  PlanSourceType,
  Plan,
  PlannerResponse,
  PlannerTask,
  RagDocument,
  RagDocumentInput,
  RefinedTask,
  RefineTaskRequest,
  ReplanApplyPayload
} from '../types';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';

// AiPayload is defined inline in other modules, but not exported from types.ts
// Define it here for our use
interface AiPayload {
  goal: string;
  deadline: string;
  dailyHours: number;
  materials: string;
  preferences: string;
  date: string;
  outputLanguage?: Language;
  data: AppData;
}

/* ═══════════════════════════════════════════════════════════════════
   API layer for Planix desktop app.

   In the Tauri v2 MSI build, the frontend loads via a custom protocol
   (tauri://) which is treated as a secure context. WebView2 blocks
   fetch() from secure contexts to http://localhost:8003 as mixed
   content. We route ALL API calls through the Tauri IPC command
   `proxy_api`, which runs in the Rust process and is not subject
   to WebView2's mixed-content restrictions.

   In browser mode, use native fetch() against the local FastAPI backend
   directly. This keeps Vite preview/static ports such as 5198 working even
   when that frontend server does not proxy /api.
   ═══════════════════════════════════════════════════════════════════ */

const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8003';
const API_BASE_URL = (import.meta.env.VITE_PLANIX_API_BASE_URL || DEFAULT_API_BASE_URL).replace(/\/+$/, '');
const API_NOT_CONNECTED_MESSAGE = '前端服务没有连接到 Planix API，请确认后端运行在 127.0.0.1:8003';

type AgentRuntimeHandlers = {
  onEvent: (event: AgentRuntimeEvent) => void;
  onError?: (error: Error) => void;
  onDone?: () => void;
};

export type CommandChatEvent =
  | { type: 'thread'; threadId: string }
  | { type: 'message'; message: unknown }
  | { type: 'assistant_delta'; text?: string; content?: string }
  | { type: 'runtime_started'; message: string }
  | { type: 'runtime_event'; name: string; status: 'running' | 'success' | 'error'; summary?: string }
  | { type: 'draft_created'; draftId: string; kind: 'calendar_plan'; version: number }
  | { type: 'summary'; text: string; draftId?: string }
  | {
      type: 'plan_detail';
      draftId: string;
      version: number;
      title: string;
      structuredPlan: unknown;
      planHorizon?: PlanHorizon | null;
      qualityReport?: PlanQualityReport | null;
      qualityStatus?: PlanQualityStatus | null;
      sourceType?: PlanSourceType | null;
      localRelevance?: LocalRelevance | null;
    }
  | { type: 'refinement_started'; draftId: string; total: number }
  | { type: 'refined_tasks_result'; draftId: string; total: number; succeeded: number; failed: number; items: unknown[]; errors?: unknown[] }
  | { type: 'calendar_plan_preview'; actionId: string; draftId: string; title: string; plans: unknown[] }
  | { type: 'approval_required'; actionId: string; draftId: string; permission: CommandPermission; risk: string; summary: string; target?: string; operation?: string }
  | { type: 'calendar_write_result'; actionId?: string; created: number; updated: number; failed: number; affectedDates?: string[]; errors?: string[]; plans?: unknown[] }
  | { type: 'command_decision'; intent: string; confidence: number; targetType?: string; action?: string; decisionSummary?: string; source?: string; error?: string; extractedParams?: unknown; needsConfirmation?: boolean; needsClarification?: boolean; clarificationQuestion?: string }
  | { type: 'plan_search_results'; query: string; summary: string; dateRange?: unknown; calendarPlans?: unknown[]; materials?: unknown[]; goalHistory?: unknown[]; monthNotes?: unknown[] }
  | { type: 'memory_search_results'; query: string; summary: string; groups?: unknown[]; results?: unknown[] }
  | { type: 'note_search_results'; query: string; summary: string; materials?: unknown[]; goalHistory?: unknown[]; monthNotes?: unknown[] }
  | { type: 'plan_patch_preview'; actionId: string; operation: 'update' | 'delete'; risk: 'write' | 'delete'; before: unknown; after?: unknown; changes?: Record<string, unknown> }
  | { type: 'plan_patch_result'; actionId?: string; operation: 'update' | 'delete'; status: 'success' | 'failed'; before?: unknown; after?: unknown; changes?: Record<string, unknown>; error?: string }
  | { type: 'memory_write_preview'; actionId: string; operation: 'create' | 'update' | 'delete'; risk: 'write' | 'delete'; kind: string; title?: string; content: string; summary?: string; tags?: unknown[] }
  | { type: 'memory_write_result'; actionId?: string; operation?: 'create' | 'update' | 'delete'; status: 'success' | 'failed'; kind?: string; title?: string; content?: string; summary?: string; tags?: unknown[]; memory?: unknown; updatedAt?: string; error?: string }
  | { type: 'note_write_preview'; actionId: string; operation: 'create' | 'update'; risk: 'write'; year: number; month: number; date: string; noteText: string; before?: string; after?: string }
  | { type: 'note_write_result'; actionId?: string; operation?: 'create' | 'update'; status: 'success' | 'failed'; year?: number; month?: number; date?: string; noteText?: string; before?: string; after?: string; updatedAt?: string; error?: string }
  | { type: 'planning_session_started'; sessionId: string; status: string }
  | { type: 'agent_decision'; sessionId: string; data: unknown }
  | { type: 'agent_message'; sessionId: string; data: unknown }
  | ({ type: 'planning_session_status' } & PlanningSessionResponse)
  | { type: 'model_usage'; usage: unknown; feature?: string; source?: string; error?: string }
  | { type: 'clarify_question'; question: string; decision?: unknown }
  | { type: 'execution_result'; actionId?: string; status: 'success' | 'failed' | 'rejected'; text: string }
  | { type: 'done'; threadId: string }
  | { type: 'error'; error: string };

export type PlanningLocalizedText = string | {
  zh: string;
  en: string;
};

export type PlanningModelFailureAttempt = {
  provider: string;
  status: 'success' | 'error' | 'skipped';
  errorType?: string;
};

export type PlanningModelFailure = {
  stage: string;
  resumeNode: string;
  retryable: boolean;
  automaticRetryAttempted: boolean;
  attempts: PlanningModelFailureAttempt[];
  summary: PlanningLocalizedText;
  action: PlanningLocalizedText;
};

export type PlanningPendingInput = {
  text: string;
  applied: false;
};

export type PlanningArtifactSnapshot = Record<string, unknown>;

/** Shared shape used by live planning_session_status events and replayed snapshots. */
export interface PlanningSessionResponse {
  sessionId: string;
  status: string;
  businessStatus?: string;
  runtimeStatus?: string;
  modelFailure?: PlanningModelFailure | null;
  pendingInput?: PlanningPendingInput | null;
  planningPhase?: 'UNDERSTANDING' | 'PLANNING' | 'SCHEDULING' | 'FINAL_REVIEW' | 'WRITING' | 'ACTIVE' | 'BLOCKED' | 'COMPLETED' | 'CANCELLED' | null;
  planningStep?: string | null;
  cognitiveMetadata?: { engineVersion?: string; [key: string]: unknown } | null;
  understandingSnapshot?: PlanningArtifactSnapshot | null;
  constraintSet?: PlanningArtifactSnapshot | null;
  contextPack?: PlanningArtifactSnapshot | null;
  planBlueprint?: PlanningArtifactSnapshot | null;
  planQualityReport?: PlanningArtifactSnapshot | null;
  scheduleBlueprint?: PlanningArtifactSnapshot | null;
  scheduleQualityReport?: PlanningArtifactSnapshot | null;
  calendarProposal?: PlanningArtifactSnapshot | null;
  finalApprovalBundle?: PlanningArtifactSnapshot | null;
  data?: {
    businessStatus?: string;
    runtimeStatus?: string;
    modelFailure?: PlanningModelFailure | null;
    pendingInput?: PlanningPendingInput | null;
    planningPhase?: PlanningSessionResponse['planningPhase'];
    planningStep?: string | null;
    cognitiveMetadata?: PlanningSessionResponse['cognitiveMetadata'];
    understandingSnapshot?: PlanningArtifactSnapshot | null;
    constraintSet?: PlanningArtifactSnapshot | null;
    contextPack?: PlanningArtifactSnapshot | null;
    planBlueprint?: PlanningArtifactSnapshot | null;
    planQualityReport?: PlanningArtifactSnapshot | null;
    scheduleBlueprint?: PlanningArtifactSnapshot | null;
    scheduleQualityReport?: PlanningArtifactSnapshot | null;
    calendarProposal?: PlanningArtifactSnapshot | null;
    finalApprovalBundle?: PlanningArtifactSnapshot | null;
  };
}

export interface CommandChatPayload {
  threadId?: string;
  message: string;
  mode: CommandMode;
  permission: CommandPermission;
  context?: Record<string, unknown>;
  history?: Array<{ role: 'user' | 'assistant'; content: string }>;
}

type CommandChatHandlers = {
  onEvent: (event: CommandChatEvent) => void;
  onError?: (error: Error) => void;
  onDone?: () => void;
};

export class CommandStreamError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'CommandStreamError';
  }
}

async function tauriProxy<T>(method: string, path: string, body?: unknown): Promise<T> {
  let result: { status: number; body: string };
  try {
    result = await invoke<{ status: number; body: string }>('proxy_api', {
      req: {
        method,
        path,
        body: body ? JSON.stringify(body) : '',
      },
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new ApiNetworkError(message);
  }

  if (result.status >= 200 && result.status < 300) {
    if (result.status === 204) return undefined as T;
    return JSON.parse(result.body) as T;
  }

  let detail: unknown = undefined;
  try {
    detail = JSON.parse(result.body);
  } catch {
    /* ignore */
  }

  throw new ApiHttpError(result.status, detail);
}

class ApiError extends Error {
  status?: number;
  detail?: unknown;
  isNetworkError?: boolean;

  constructor(message: string, options: { status?: number; detail?: unknown; isNetworkError?: boolean } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = options.status;
    this.detail = options.detail;
    this.isNetworkError = options.isNetworkError;
  }
}

class ApiNetworkError extends ApiError {
  constructor(message = '后端服务未启动或连接失败') {
    super(message, { isNetworkError: true });
    this.name = 'ApiNetworkError';
  }
}

export { ApiNetworkError, ApiError };

class ApiHttpError extends ApiError {
  status: number;

  constructor(status: number, detail: unknown) {
    super(`HTTP ${status}`, { status, detail, isNetworkError: false });
    this.name = 'ApiHttpError';
    this.status = status;
  }
}

export { ApiHttpError };

// ═══ Common request helper ═══

function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
}

function isJsonResponse(res: Response): boolean {
  return (res.headers.get('content-type') || '').toLowerCase().includes('application/json');
}

async function parseJsonResponse<T>(res: Response): Promise<T> {
  if (!isJsonResponse(res)) {
    throw new ApiNetworkError(API_NOT_CONNECTED_MESSAGE);
  }
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiNetworkError('Planix API 返回了无效 JSON，请确认后端运行正常');
  }
}

async function callApi<T>(method: string, path: string, body?: unknown, timeoutMs = 45000): Promise<T> {
  if (isTauri) {
    return tauriProxy<T>(method, path, body);
  }
  // Dev mode: use fetch()
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const init: RequestInit = {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      signal: controller.signal,
    };
    if (body) init.body = JSON.stringify(body);
    const res = await fetch(apiUrl(path), init);
    if (!res.ok) {
      let detail: unknown = undefined;
      if (!isJsonResponse(res)) {
        throw new ApiNetworkError(API_NOT_CONNECTED_MESSAGE);
      }
      try { detail = await res.json(); } catch { /* ignore */ }
      throw new ApiHttpError(res.status, detail);
    }
    if (res.status === 204) return undefined as T;
    return parseJsonResponse<T>(res);
  } catch (err) {
    if (err instanceof ApiError) throw err;
    const message = err instanceof Error ? err.message : String(err);
    const name = err instanceof Error ? err.name : '';
    if (name === 'AbortError' || err instanceof TypeError || message.includes('Failed to fetch') || message.includes('ECONNREFUSED')) {
      throw new ApiNetworkError();
    }
    throw err;
  } finally {
    window.clearTimeout(timer);
  }
}

function parseRuntimeLine(line: string, handlers: AgentRuntimeHandlers) {
  const trimmed = line.trim();
  if (!trimmed) return;
  handlers.onEvent(JSON.parse(trimmed) as AgentRuntimeEvent);
}

function runtimeEventName(): string {
  const suffix = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `agent-runtime-${suffix}`;
}

async function runTauriAgentRuntime(payload: AgentRunRequest, handlers: AgentRuntimeHandlers): Promise<void> {
  const eventName = runtimeEventName();
  const unlisteners: Array<() => void> = [];
  let resolveDone: () => void = () => undefined;
  let rejectDone: (error: Error) => void = () => undefined;
  const completion = new Promise<void>((resolve, reject) => {
    resolveDone = resolve;
    rejectDone = reject;
  });

  const cleanup = () => {
    while (unlisteners.length) {
      unlisteners.pop()?.();
    }
  };

  try {
    unlisteners.push(await listen<string>(eventName, (event) => {
      try {
        parseRuntimeLine(event.payload, handlers);
      } catch (err) {
        const error = err instanceof Error ? err : new Error(String(err));
        cleanup();
        handlers.onError?.(error);
        rejectDone(error);
      }
    }));
    unlisteners.push(await listen<string>(`${eventName}:done`, () => {
      cleanup();
      handlers.onDone?.();
      resolveDone();
    }));
    unlisteners.push(await listen<string>(`${eventName}:error`, (event) => {
      cleanup();
      const error = new ApiNetworkError(event.payload || 'Runtime stream failed');
      handlers.onError?.(error);
      rejectDone(error);
    }));

    await invoke<void>('stream_agent_runtime', {
      req: {
        method: 'POST',
        path: '/api/runtime/run',
        body: JSON.stringify(payload),
      },
      eventName,
    });
    await completion;
  } catch (err) {
    cleanup();
    const error = err instanceof Error ? err : new ApiNetworkError(String(err));
    handlers.onError?.(error);
    throw error;
  }
}

async function runFetchAgentRuntime(payload: AgentRunRequest, handlers: AgentRuntimeHandlers): Promise<void> {
  const res = await fetch(apiUrl('/api/runtime/run'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    let detail: unknown = undefined;
    try { detail = await res.json(); } catch { /* ignore */ }
    throw new ApiHttpError(res.status, detail);
  }
  if (!res.body) {
    throw new ApiNetworkError('Runtime stream is not available');
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        parseRuntimeLine(line, handlers);
      }
    }
    buffer += decoder.decode();
    parseRuntimeLine(buffer, handlers);
    handlers.onDone?.();
  } catch (err) {
    const error = err instanceof Error ? err : new Error(String(err));
    handlers.onError?.(error);
    throw error;
  }
}

export async function runAgentRuntime(payload: AgentRunRequest, handlers: AgentRuntimeHandlers): Promise<void> {
  if (isTauri) {
    return runTauriAgentRuntime(payload, handlers);
  }
  return runFetchAgentRuntime(payload, handlers);
}

function parseCommandLine(line: string, handlers: CommandChatHandlers) {
  const trimmed = line.trim();
  if (!trimmed) return;
  const event = JSON.parse(trimmed) as CommandChatEvent;
  handlers.onEvent(event);
  if (event.type === 'error') {
    throw new CommandStreamError(event.error || 'Command 执行流中断，请重启后端服务后重试');
  }
}

async function runFetchCommandStream(path: string, payload: unknown, handlers: CommandChatHandlers): Promise<void> {
  const res = await fetch(apiUrl(path), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    let detail: unknown = undefined;
    try { detail = await res.json(); } catch { /* ignore */ }
    throw new ApiHttpError(res.status, detail);
  }
  if (!res.body) {
    throw new ApiNetworkError('Command stream is not available');
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        parseCommandLine(line, handlers);
      }
    }
    buffer += decoder.decode();
    parseCommandLine(buffer, handlers);
    handlers.onDone?.();
  } catch (err) {
    const error = err instanceof Error
      ? err
      : new CommandStreamError('Command 执行流中断，请重启后端服务后重试');
    handlers.onError?.(error);
    throw error;
  }
}

export async function runCommandChat(payload: CommandChatPayload, handlers: CommandChatHandlers): Promise<void> {
  return runFetchCommandStream('/api/command/chat', payload, handlers);
}

export async function approveCommandAction(
  payload: { threadId?: string; actionId: string; decision: 'approve' | 'reject'; permission: CommandPermission },
  handlers: CommandChatHandlers
): Promise<void> {
  return runFetchCommandStream('/api/command/approve', payload, handlers);
}

export async function fetchCommandThread(threadId: string): Promise<CommandThread> {
  return callApi<CommandThread>('GET', `/api/command/thread/${encodeURIComponent(threadId)}`);
}

export async function listCommandThreads(limit = 50): Promise<CommandThreadSummary[]> {
  return callApi<CommandThreadSummary[]>('GET', `/api/command/threads?limit=${encodeURIComponent(String(limit))}`);
}

export async function deleteCommandThread(threadId: string): Promise<void> {
  return callApi<void>('DELETE', `/api/command/thread/${encodeURIComponent(threadId)}`);
}

// ═══ Health & Settings ═══

export interface BackendHealth {
  status: string;
  app?: string;
  name?: string;
  pid?: number;
  version?: string;
  startupTime?: string;
  features?: Record<string, boolean>;
}

export async function fetchBackendHealth(): Promise<BackendHealth> {
  return callApi<BackendHealth>('GET', '/api/health');
}

export async function checkBackendHealth(): Promise<boolean> {
  try {
    const health = await fetchBackendHealth();
    return health.status === 'ok';
  } catch {
    return false;
  }
}

export async function fetchAiSettings(): Promise<AiSettings> {
  return callApi<AiSettings>('GET', '/api/ai/settings');
}

export async function saveAiSettings(payload: AiSettingsInput): Promise<AiSettings> {
  return callApi<AiSettings>('PUT', '/api/ai/settings', payload, 15000);
}

export async function saveAiSettingsRouting(payload: AiModelRoutingInput): Promise<AiSettings> {
  return callApi<AiSettings>('PUT', '/api/ai/settings/routing', payload, 15000);
}

export async function deleteAiSettingsKey(provider: AiSettings['provider']): Promise<AiSettings> {
  return callApi<AiSettings>('DELETE', `/api/ai/settings/key/${encodeURIComponent(provider)}`, undefined, 15000);
}

export async function testAiSettings(): Promise<AiSettingsTestResult> {
  return callApi<AiSettingsTestResult>('POST', '/api/ai/test', { prompt: 'Say OK in one short sentence.' }, 45000);
}

// ═══ Plans ═══

type BackendPlan = {
  id: string; date: string; time: string; content: string; done: boolean;
  result: string; priority: 'low' | 'medium' | 'high'; estimatedMinutes: number;
  source: 'manual' | 'ai'; sourceKey?: string; refinedTask?: RefinedTask | null;
  refinedTaskUpdatedAt?: string | null; createdAt: string; updatedAt: string;
};

export type PlanPatch = Partial<Pick<Plan, 'time' | 'title' | 'done' | 'completion' | 'source' | 'sourceKey' | 'priority' | 'estimatedMinutes'>>;

function fromBackendPlan(plan: BackendPlan): Plan {
  return {
    id: plan.id, time: plan.time, title: plan.content,
    done: plan.done, completion: plan.result ?? '', source: plan.source,
    sourceKey: plan.sourceKey ?? '',
    priority: plan.priority,
    estimatedMinutes: plan.estimatedMinutes,
    refinedTask: plan.refinedTask ?? null,
    refinedTaskUpdatedAt: plan.refinedTaskUpdatedAt ?? null,
  };
}

function fromAppliedBackendPlan(plan: BackendPlan): AppliedPlan {
  return { ...fromBackendPlan(plan), date: plan.date };
}

export function toBackendPlan(date: string, plan: Plan) {
  return {
    date, time: plan.time, content: plan.title, done: plan.done,
    result: plan.completion, source: plan.source ?? 'manual',
    sourceKey: plan.sourceKey ?? '',
    refinedTask: plan.refinedTask ?? undefined,
    priority: plan.priority ?? 'medium',
    estimatedMinutes: plan.estimatedMinutes ?? 30,
  };
}

export async function fetchPlans(date: string): Promise<Plan[]> {
  const plans = await callApi<BackendPlan[]>('GET', `/api/plans?date=${encodeURIComponent(date)}`);
  return plans.map(fromBackendPlan);
}

export async function fetchMonthPlans(year: number, month: number): Promise<AppliedPlan[]> {
  const plans = await callApi<BackendPlan[]>('GET', `/api/plans/month?year=${year}&month=${month}`);
  return plans.map(fromAppliedBackendPlan);
}

export async function createPlan(date: string, plan: Plan): Promise<Plan> {
  const saved = await callApi<BackendPlan>('POST', '/api/plans', toBackendPlan(date, plan));
  return fromBackendPlan(saved);
}

export async function updatePlan(id: string, patch: PlanPatch): Promise<Plan> {
  const saved = await callApi<BackendPlan>('PATCH', `/api/plans/${id}`, {
    time: patch.time, content: patch.title, done: patch.done,
    result: patch.completion, source: patch.source, sourceKey: patch.sourceKey,
    priority: patch.priority, estimatedMinutes: patch.estimatedMinutes,
  });
  return fromBackendPlan(saved);
}

export async function savePlanRefinedTask(id: string, refinedTask: RefinedTask): Promise<Plan> {
  const saved = await callApi<BackendPlan>('PATCH', `/api/plans/${id}/refined-task`, { refinedTask }, 60000);
  return fromBackendPlan(saved);
}

export async function deletePlanRefinedTask(id: string): Promise<Plan> {
  const saved = await callApi<BackendPlan>('DELETE', `/api/plans/${id}/refined-task`, undefined, 45000);
  return fromBackendPlan(saved);
}

export async function deletePlan(id: string): Promise<void> {
  await callApi<void>('DELETE', `/api/plans/${id}`);
}

export async function clearAllPlans(): Promise<{ deleted: number }> {
  return callApi<{ deleted: number }>('DELETE', '/api/plans/all', undefined, 60000);
}

// ═══ Month Notes ═══

export async function fetchMonthNote(year: number, month: number): Promise<string> {
  const note = await callApi<{ content: string }>('GET', `/api/month-notes?year=${year}&month=${month}`);
  return note.content;
}

export async function saveRemoteMonthNote(year: number, month: number, content: string): Promise<void> {
  await callApi<void>('PUT', '/api/month-notes', { year, month, content });
}

// ═══ RAG ═══

export async function fetchRagDocuments(): Promise<RagDocument[]> {
  return callApi<RagDocument[]>('GET', '/api/rag/documents', undefined, 45000);
}

export async function createRagDocument(payload: RagDocumentInput): Promise<RagDocument> {
  return callApi<RagDocument>('POST', '/api/rag/documents', payload, 45000);
}

export async function createAiMaterialDraft(payload: AiMaterialDraftRequest): Promise<AiMaterialDraft> {
  return callApi<AiMaterialDraft>('POST', '/api/materials/ai-draft', payload, 60000);
}

function validateUploadFile(file: File): void {
  const lowerName = file.name.toLowerCase();
  if (!lowerName.endsWith('.txt') && !lowerName.endsWith('.md')) {
    throw new ApiHttpError(400, { detail: 'Only .txt and .md files are supported.' });
  }
  if (file.size <= 0) {
    throw new ApiHttpError(400, { detail: 'File cannot be empty.' });
  }
  if (file.size > 5 * 1024 * 1024) {
    throw new ApiHttpError(400, { detail: 'File must be 5MB or smaller.' });
  }
}

function fallbackTitleFromFile(file: File): string {
  return file.name.replace(/\.[^/.]+$/, '') || 'Uploaded material';
}

async function readUploadText(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  for (const encoding of ['utf-8', 'gb18030']) {
    try {
      return new TextDecoder(encoding, { fatal: true }).decode(buffer);
    } catch {
      /* try the next text encoding */
    }
  }
  throw new ApiHttpError(400, { detail: 'File must be valid UTF-8 or GB18030 text.' });
}

export async function uploadRagDocument(file: File, title?: string): Promise<RagDocument> {
  validateUploadFile(file);
  if (isTauri) {
    const content = (await readUploadText(file)).trim();
    if (!content) {
      throw new ApiHttpError(400, { detail: 'File cannot be empty.' });
    }
    return createRagDocument({
      title: title?.trim() || fallbackTitleFromFile(file),
      content,
      sourceType: 'upload',
    });
  }

  const form = new FormData();
  form.append('file', file);
  if (title?.trim()) form.append('title', title.trim());
  form.append('sourceType', 'upload');
  const res = await fetch(apiUrl('/api/rag/documents/upload'), { method: 'POST', body: form });
  if (!res.ok) {
    let detail: unknown = undefined;
    if (!isJsonResponse(res)) {
      throw new ApiNetworkError(API_NOT_CONNECTED_MESSAGE);
    }
    try { detail = await res.json(); } catch { /* ignore */ }
    throw new ApiHttpError(res.status, detail);
  }
  return parseJsonResponse<RagDocument>(res);
}

export async function deleteRagDocument(id: string): Promise<void> {
  await callApi<void>('DELETE', `/api/rag/documents/${id}`, undefined, 45000);
}

export async function fetchMemories(kind?: string): Promise<MemoryItem[]> {
  const query = kind ? `?kind=${encodeURIComponent(kind)}` : '';
  return callApi<MemoryItem[]>('GET', `/api/memory${query}`, undefined, 45000);
}

export async function createMemory(payload: MemoryInput): Promise<MemoryItem> {
  return callApi<MemoryItem>('POST', '/api/memory', payload, 45000);
}

export async function searchMemories(query: string, kind?: string): Promise<MemorySearchResult> {
  const params = new URLSearchParams();
  if (query) params.set('q', query);
  if (kind) params.append('kind', kind);
  const suffix = params.toString() ? `?${params.toString()}` : '';
  return callApi<MemorySearchResult>('GET', `/api/memory/search${suffix}`, undefined, 45000);
}

export async function updateMemory(id: string, payload: Partial<MemoryInput>): Promise<MemoryItem> {
  return callApi<MemoryItem>('PATCH', `/api/memory/${encodeURIComponent(id)}`, payload, 45000);
}

export async function deleteMemory(id: string): Promise<void> {
  await callApi<void>('DELETE', `/api/memory/${encodeURIComponent(id)}`, undefined, 45000);
}

// ═══ AI Planning & Review ═══

export async function createGoalPlan(payload: Omit<AiPayload, 'data'>): Promise<GoalPlanResponse> {
  return callApi<GoalPlanResponse>('POST', '/api/planning/goal-plan', payload, 80000);
}

export async function refineTask(payload: RefineTaskRequest): Promise<RefinedTask> {
  return callApi<RefinedTask>('POST', '/api/planning/refine-task', payload, 60000);
}

export async function createDailyReview(payload: Pick<AiPayload, 'date' | 'goal' | 'preferences' | 'data'>): Promise<DailyReviewResponse> {
  return callApi<DailyReviewResponse>('POST', '/api/planning/daily-review', payload, 45000);
}

export async function fetchDailyReview(date: string): Promise<DailyReviewResponse> {
  return callApi<DailyReviewResponse>('GET', `/api/planning/daily-review?date=${encodeURIComponent(date)}`);
}

export async function applyReplanTasks(payload: ReplanApplyPayload): Promise<AppliedPlan[]> {
  const plans = await callApi<BackendPlan[]>('POST', '/api/planning/replan/apply', payload, 45000);
  return plans.map(fromAppliedBackendPlan);
}

// ═══ Agent & Evaluator ═══

function fallbackTasks(payload: AiPayload): PlannerTask[] {
  const title = payload.goal || 'AI 应用开发实习';
  return [
    { time: '09:00', title: `拆解目标：${title}`, reason: '先把长期目标转成阶段里程碑，避免计划停留在口号。' },
    { time: '14:30', title: '实现一个可展示的项目功能', reason: '每天保留工程产出，面试时可以讲清楚设计和取舍。' },
    { time: '20:30', title: '复盘完成情况并调整明日任务', reason: '形成计划、执行、反馈、重排的闭环。' },
  ];
}

export async function generatePlan(payload: AiPayload): Promise<PlannerResponse> {
  try {
    return await callApi<PlannerResponse>('POST', '/api/agent/plan', payload, 45000);
  } catch {
    return {
      mode: 'mock',
      summary: `基于每天 ${payload.dailyHours || 2} 小时，为目标生成阶段计划。`,
      phases: [
        { title: '第 1 阶段：能力对齐', detail: '补齐岗位 JD 中的核心技术栈与基础知识。' },
        { title: '第 2 阶段：项目冲刺', detail: '完成 AI/RAG/Agent 相关功能并沉淀文档。' },
        { title: '第 3 阶段：投递复盘', detail: '结合反馈优化简历、项目讲法与面试题库。' },
      ],
      tasks: fallbackTasks(payload),
    };
  }
}

export async function reviewToday(payload: AiPayload): Promise<PlannerResponse> {
  try {
    return await callApi<PlannerResponse>('POST', '/api/agent/review', payload, 45000);
  } catch {
    const plans = payload.data[payload.date]?.plans ?? [];
    const done = plans.filter((plan: Plan) => plan.done).length;
    return {
      mode: 'mock',
      summary: `今天完成 ${done}/${plans.length} 项。`,
      suggestions: ['把未完成任务拆小到 45 分钟内。', '保留一个可验证产出，例如提交、截图或笔记。', '晚上用完成情况更新明天计划。'],
    };
  }
}

export async function askMaterials(payload: AiPayload): Promise<PlannerResponse> {
  try {
    return await callApi<PlannerResponse>('POST', '/api/rag/query', payload, 45000);
  } catch {
    return {
      mode: 'mock',
      answer: '资料库服务暂不可用。你仍可以先根据当前粘贴内容提炼高频技能、项目要求和可验证产出。',
      sources: [],
    };
  }
}

export async function saveMemory(preferences: string): Promise<void> {
  try {
    await callApi<void>('POST', '/api/memory/preferences', { userId: 'local-user', preferences });
  } catch {
    return;
  }
}

export async function fetchMemoryCacheStats(): Promise<MemoryCacheStats> {
  return callApi<MemoryCacheStats>('GET', '/api/settings/ai-memory-cache/stats');
}

export async function clearPreferenceMemory(): Promise<MemoryResetResult> {
  return callApi<MemoryResetResult>('DELETE', '/api/settings/memory/preferences');
}

export async function clearHistoryMemory(): Promise<MemoryResetResult> {
  return callApi<MemoryResetResult>('DELETE', '/api/settings/memory/history');
}

export async function clearRuntimeRuns(): Promise<MemoryResetResult> {
  return callApi<MemoryResetResult>('DELETE', '/api/settings/runtime/runs');
}

export async function clearPlanningHistory(): Promise<MemoryResetResult> {
  return callApi<MemoryResetResult>('DELETE', '/api/settings/planning/history');
}

export async function clearAiMemoryCache(): Promise<MemoryResetResult> {
  return callApi<MemoryResetResult>('DELETE', '/api/settings/ai-memory-cache');
}

export async function evaluatePlanner(payload: AiPayload): Promise<PlannerResponse> {
  try {
    return await callApi<PlannerResponse>('POST', '/api/eval/planner', payload, 45000);
  } catch {
    return {
      mode: 'mock',
      score: 4.4,
      results: [
        { case: '目标明确但时间有限', score: 5, reason: '计划覆盖阶段、日任务和复盘闭环。' },
        { case: '包含岗位 JD 资料', score: 4, reason: '已能提取关键词，后续可加入引用来源。' },
        { case: '当天未完成任务', score: 4, reason: '可生成调整建议，后续继续增强自动重排。' },
      ],
    };
  }
}
