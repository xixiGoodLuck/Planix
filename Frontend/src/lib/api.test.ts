import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiHttpError, ApiNetworkError, apiErrorMessage, fetchAiSettings } from './api';

describe('API error normalization', () => {
  beforeEach(() => { vi.stubGlobal('window', globalThis); });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('replaces a raw fetch failure with the backend unavailable message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    const error = await fetchAiSettings().catch((value) => value);
    expect(error).toBeInstanceOf(ApiNetworkError);
    expect(error.message).toBe('无法连接 Planix 后端服务，请确认 PostgreSQL 17 和 Backend 8003 已启动。');
    expect(error.message).not.toBe('Failed to fetch');
  });

  it('unwraps nested FastAPI details and validation messages', () => {
    expect(apiErrorMessage(new ApiHttpError(400, { detail: { message: 'Safe backend message' } }))).toBe('Safe backend message');
    expect(apiErrorMessage(new ApiHttpError(422, { detail: [{ msg: 'Field is invalid' }] }))).toBe('Field is invalid');
  });
});
