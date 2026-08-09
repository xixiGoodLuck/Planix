import type { AiProvider, AiSettings } from '../types';

export function apiKeyDraftAfterProviderSwitch(currentDraft: string): string {
  void currentDraft;
  return '';
}

export const providerDefaultBaseUrls: Record<AiProvider, string> = {
  mock: 'https://api.deepseek.com',
  deepseek: 'https://api.deepseek.com',
  kimi: 'https://api.moonshot.ai/v1',
  zhipu_glm: 'https://open.bigmodel.cn/api/paas/v4',
  openai: 'https://api.openai.com/v1',
  custom: '',
  local: ''
};

export const providerDefaultModels: Record<AiProvider, string> = {
  mock: 'deepseek-v4-flash',
  deepseek: 'deepseek-v4-flash',
  kimi: 'kimi-k2.7-code',
  zhipu_glm: 'glm-4-flash',
  openai: 'gpt-4o-mini',
  custom: 'gpt-4o-mini',
  local: ''
};

export const providerModelRecommendations: Record<AiProvider, string[]> = {
  mock: ['deepseek-v4-flash', 'deepseek-v4-pro'],
  deepseek: ['deepseek-v4-flash', 'deepseek-v4-pro'],
  kimi: [
    'kimi-k2.7-code-highspeed',
    'kimi-k2.7-code',
    'kimi-k2.6',
    'kimi-k2.5',
    'moonshot-v1-8k',
    'moonshot-v1-32k',
    'moonshot-v1-128k'
  ],
  zhipu_glm: ['glm-4-flash', 'glm-4-plus', 'glm-4-air'],
  openai: ['gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini'],
  custom: ['gpt-4o-mini', 'deepseek-v4-flash'],
  local: []
};

export function normalizeBaseUrlForCompare(value: string): string {
  return value.trim().replace(/\/+$/, '');
}

const invalidKimiPlatformHosts = new Set(['platform.kimi.ai', 'kimi.moonshot.cn', 'www.kimi.com']);
const legacyNoKeyKimiDefaultBaseUrls = new Set(['https://api.moonshot.cn/v1']);
const legacyKimiDefaultModels = new Set(['moonshot-v1']);

function isInvalidKimiPlatformUrl(value: string): boolean {
  try {
    return invalidKimiPlatformHosts.has(new URL(value).hostname);
  } catch {
    return false;
  }
}

export function upgradeLegacyKimiDefaults(settings: AiSettings): AiSettings {
  const upgradeBaseUrl = (provider: AiProvider, baseUrl: string, hasApiKey: boolean): string => {
    if (provider !== 'kimi') return baseUrl;
    const normalized = normalizeBaseUrlForCompare(baseUrl);
    const shouldUseDefault =
      isInvalidKimiPlatformUrl(normalized) ||
      (!hasApiKey && legacyNoKeyKimiDefaultBaseUrls.has(normalized));
    return shouldUseDefault
      ? providerDefaultBaseUrls.kimi
      : baseUrl;
  };
  const upgradeModel = (provider: AiProvider, model: string): string => {
    if (provider !== 'kimi') return model;
    return legacyKimiDefaultModels.has(model.trim()) ? providerDefaultModels.kimi : model;
  };
  return {
    ...settings,
    baseUrl: upgradeBaseUrl(settings.provider, settings.baseUrl, settings.hasApiKey),
    model: upgradeModel(settings.provider, settings.model),
    savedProviders: (settings.savedProviders || []).map((item) => ({
      ...item,
      baseUrl: upgradeBaseUrl(item.provider, item.baseUrl, item.hasApiKey),
      model: upgradeModel(item.provider, item.model)
    }))
  };
}

export function settingsForEditor(settings: AiSettings): AiSettings {
  const normalized = upgradeLegacyKimiDefaults(settings);
  return normalized.provider === 'mock'
    ? {
        ...normalized,
        provider: 'deepseek',
        baseUrl: providerDefaultBaseUrls.deepseek,
        model: providerDefaultModels.deepseek,
        hasApiKey: false,
        keyStatus: 'unchecked',
        keyErrorType: ''
      }
    : normalized;
}
