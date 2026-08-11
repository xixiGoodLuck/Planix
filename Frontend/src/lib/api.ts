import { invoke } from '@tauri-apps/api/core';
import type {
  AiModelRoutingInput,
  AiSettings,
  AiSettingsInput,
  AiSettingsTestResult,
  BackendHealth,
  LearningRuntimeHealth
} from '../types';

const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
const API_BASE_URL = (import.meta.env.VITE_PLANIX_API_BASE_URL || 'http://127.0.0.1:8003').replace(/\/+$/, '');

class ApiError extends Error {
  status?: number;
  detail?: unknown;
  isNetworkError?: boolean;
  constructor(message: string, options: { status?: number; detail?: unknown; isNetworkError?: boolean } = {}) {
    super(message);
    this.name = 'ApiError';
    Object.assign(this, options);
  }
}

export class ApiNetworkError extends ApiError {
  constructor(cause?: unknown) {
    super('无法连接 Planix 后端服务，请确认 PostgreSQL 17 和 Backend 8003 已启动。', { isNetworkError: true });
    this.name = 'ApiNetworkError';
    (this as Error & { cause?: unknown }).cause = cause;
  }
}

export class ApiHttpError extends ApiError {
  status: number;
  constructor(status: number, detail: unknown) {
    super(`HTTP ${status}`, { status, detail });
    this.name = 'ApiHttpError';
    this.status = status;
  }
}

function detailMessage(detail: unknown): string | undefined {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map(detailMessage).filter((value): value is string => Boolean(value));
    return messages.length ? messages.join('; ') : undefined;
  }
  if (!detail || typeof detail !== 'object') return undefined;
  const value = detail as Record<string, unknown>;
  if (typeof value.message === 'string') return value.message;
  if (typeof value.msg === 'string') return value.msg;
  return detailMessage(value.detail);
}

export function apiErrorMessage(error: unknown): string {
  if (error instanceof ApiNetworkError) return error.message;
  if (error instanceof ApiHttpError) return detailMessage(error.detail) || `HTTP ${error.status}`;
  return error instanceof Error ? error.message : String(error);
}

export function apiUrl(path: string) {
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
}

export async function callApi<T>(method: string, path: string, body?: unknown, timeoutMs = 45000): Promise<T> {
  if (isTauri) {
    const result = await invoke<{ status: number; body: string }>('proxy_api', {
      req: { method, path, body: body === undefined ? '' : JSON.stringify(body) }
    });
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
      method,
      signal: controller.signal,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    if (!response.ok) {
      let detail: unknown;
      try {
        detail = (response.headers.get('content-type') || '').includes('application/json')
          ? await response.json()
          : await response.text();
      } catch { detail = undefined; }
      throw new ApiHttpError(response.status, detail);
    }
    return response.status === 204 ? undefined as T : await response.json() as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiNetworkError(error);
  } finally {
    window.clearTimeout(timer);
  }
}

export function fetchBackendHealth() { return callApi<BackendHealth>('GET', '/health'); }
export function fetchLearningHealth() { return callApi<LearningRuntimeHealth>('GET', '/api/learning/health'); }
export function fetchAiSettings() { return callApi<AiSettings>('GET', '/api/ai/settings'); }
export function saveAiSettings(payload: AiSettingsInput) { return callApi<AiSettings>('PUT', '/api/ai/settings', payload); }
export function saveAiSettingsRouting(payload: AiModelRoutingInput) { return callApi<AiSettings>('PUT', '/api/ai/settings/routing', payload); }
export function deleteAiSettingsKey(provider: AiSettings['provider']) { return callApi<AiSettings>('DELETE', `/api/ai/settings/key/${provider}`); }
export function testAiSettings() { return callApi<AiSettingsTestResult>('POST', '/api/ai/test', { prompt: 'Say OK in one short sentence.' }); }
