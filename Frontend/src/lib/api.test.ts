import { afterEach, describe, expect, it, vi } from 'vitest';
import { deleteAiSettingsKey, saveAiSettingsRouting, toBackendPlan } from './api';
import type { Plan } from '../types';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('plan API mapping', () => {
  it('preserves AI calendar task metadata when writing to the backend', () => {
    const plan: Plan = {
      id: 'plan-1',
      time: '09:00',
      title: '练习基本站姿与平衡',
      done: false,
      completion: '在平地上模拟滑雪站姿，双腿微曲，重心居中，练习原地踏步和平衡。',
      source: 'ai',
      sourceKey: 'command-draft:draft_ski:m0:t1',
      priority: 'high',
      estimatedMinutes: 45,
      refinedTask: null
    };

    expect(toBackendPlan('2026-07-08', plan)).toMatchObject({
      date: '2026-07-08',
      time: '09:00',
      content: '练习基本站姿与平衡',
      result: '在平地上模拟滑雪站姿，双腿微曲，重心居中，练习原地踏步和平衡。',
      source: 'ai',
      sourceKey: 'command-draft:draft_ski:m0:t1',
      priority: 'high',
      estimatedMinutes: 45
    });
  });

  it('deletes a provider API key through the provider-specific settings route', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      provider: 'kimi',
      baseUrl: 'https://api.moonshot.ai/v1',
      model: 'kimi-k2.7-code',
      hasApiKey: false,
      temperature: 0.3,
      timeoutSeconds: 40,
      updatedAt: '2026-07-08 12:00:00',
      savedProviders: []
    }), { status: 200, headers: { 'content-type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('window', { setTimeout, clearTimeout });

    const result = await deleteAiSettingsKey('kimi');

    expect(result.provider).toBe('kimi');
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/ai/settings/key/kimi'),
      expect.objectContaining({ method: 'DELETE' })
    );
  });

  it('saves model routing rules through the routing settings route', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      provider: 'deepseek',
      baseUrl: 'https://api.deepseek.com',
      model: 'deepseek-v4-flash',
      hasApiKey: false,
      temperature: 0.3,
      timeoutSeconds: 40,
      updatedAt: '2026-07-08 12:00:00',
      savedProviders: [],
      routingRules: []
    }), { status: 200, headers: { 'content-type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('window', { setTimeout, clearTimeout });

    await saveAiSettingsRouting({
      routingRules: [{
        taskType: 'plan_generation',
        primaryProvider: 'kimi',
        fallbackProviders: ['deepseek'],
        localFallbackEnabled: true
      }]
    });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/ai/settings/routing'),
      expect.objectContaining({ method: 'PUT' })
    );
  });
});
