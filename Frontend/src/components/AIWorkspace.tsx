import { useEffect, useState } from 'react';
import { ArrowDown, ArrowUp, KeyRound, PlugZap, RotateCcw, Save, Settings, Trash2, X } from 'lucide-react';
import type {
  AiAutoModelPolicy,
  AiModelRoutingRule,
  AiProvider,
  AiSettings,
  AiSettingsInput,
  AutoModelStrategy,
  ModelRoutingTaskType,
  RoutingPrimaryProvider
} from '../types';
import {
  ApiNetworkError,
  apiErrorMessage,
  deleteAiSettingsKey,
  fetchAiSettings,
  fetchBackendHealth,
  saveAiSettings,
  saveAiSettingsRouting,
  testAiSettings
} from '../lib/api';
import {
  apiKeyDraftAfterProviderSwitch,
  normalizeBaseUrlForCompare,
  providerDefaultBaseUrls,
  providerDefaultModels,
  providerModelRecommendations,
  settingsForEditor
} from '../lib/aiSettingsDefaults';
import { routingTaskTypes } from '../lib/aiRouting';

interface AIWorkspaceProps {
  onSettingsChange?: (settings: AiSettings) => void;
  t: (key: string) => string;
}

type RoutedProvider = Exclude<AiProvider, 'mock'>;

const defaultSettings: AiSettings = {
  provider: 'deepseek',
  baseUrl: providerDefaultBaseUrls.deepseek,
  model: providerDefaultModels.deepseek,
  hasApiKey: false,
  temperature: 0.3,
  timeoutSeconds: 40,
  forceNonThinking: false,
  updatedAt: '',
  savedProviders: [],
  routingRules: []
};

const routableProviders: RoutedProvider[] = ['deepseek', 'kimi', 'zhipu_glm', 'openai', 'custom', 'local'];
const defaultAutoProviderOrder: RoutedProvider[] = ['deepseek', 'zhipu_glm', 'kimi', 'openai', 'custom', 'local'];
const defaultTaskStrategies: Record<ModelRoutingTaskType, AutoModelStrategy> = {
  planning_understanding: 'knowledge_reasoning',
  planning_plan: 'structured_stable',
  planning_review: 'strict_json',
  planning_learning: 'knowledge_reasoning'
};

function providerLabel(provider: AiProvider, t: (key: string) => string): string {
  const labels: Record<AiProvider, string> = {
    deepseek: t('legacy.providerDeepSeek'),
    kimi: t('legacy.providerKimi'),
    zhipu_glm: t('legacy.providerZhipu'),
    openai: t('legacy.providerOpenAI'),
    custom: t('legacy.providerCustom'),
    local: t('legacy.providerLocal'),
    mock: t('legacy.providerMock')
  };
  return labels[provider];
}

function taskLabel(taskType: ModelRoutingTaskType, t: (key: string) => string): string {
  return {
    planning_understanding: t('legacy.routingTaskPlanningUnderstanding'),
    planning_plan: t('legacy.routingTaskPlanningPlan'),
    planning_review: t('legacy.routingTaskPlanningReview'),
    planning_learning: t('legacy.routingTaskPlanningLearning')
  }[taskType];
}

function taskDescription(taskType: ModelRoutingTaskType, t: (key: string) => string): string {
  return {
    planning_understanding: t('legacy.routingTaskPlanningUnderstandingDesc'),
    planning_plan: t('legacy.routingTaskPlanningPlanDesc'),
    planning_review: t('legacy.routingTaskPlanningReviewDesc'),
    planning_learning: t('legacy.routingTaskPlanningLearningDesc')
  }[taskType];
}

function strategyLabel(strategy: AutoModelStrategy, t: (key: string) => string): string {
  const labels: Record<AutoModelStrategy, string> = {
    fast_low_cost: t('legacy.autoStrategyFastLowCost'),
    structured_stable: t('legacy.autoStrategyStructuredStable'),
    strict_json: t('legacy.autoStrategyStrictJson'),
    context_summary: t('legacy.autoStrategyContextSummary'),
    classification: t('legacy.autoStrategyClassification'),
    knowledge_reasoning: t('legacy.autoStrategyKnowledgeReasoning'),
    balanced: t('legacy.autoStrategyBalanced')
  };
  return labels[strategy];
}

function recommendedRules(): AiModelRoutingRule[] {
  return routingTaskTypes.map((taskType) => ({
    taskType,
    primaryProvider: 'auto',
    fallbackProviders: ['deepseek'],
    localFallbackEnabled: false
  }));
}

function normalizedRules(settings: AiSettings): AiModelRoutingRule[] {
  const current = new Map((settings.routingRules || []).map((rule) => [rule.taskType, rule]));
  return recommendedRules().map((fallback) => {
    const existing = current.get(fallback.taskType);
    if (!existing) return fallback;
    const primaryProvider: RoutingPrimaryProvider = existing.primaryProvider === 'auto' || routableProviders.includes(existing.primaryProvider)
      ? existing.primaryProvider
      : 'auto';
    return {
      ...existing,
      taskType: fallback.taskType,
      primaryProvider,
      fallbackProviders: (existing.fallbackProviders || [])
        .filter((provider): provider is RoutedProvider => provider !== 'mock' && routableProviders.includes(provider as RoutedProvider))
        .filter((provider, index, providers) => provider !== primaryProvider && providers.indexOf(provider) === index)
        .slice(0, 2),
      localFallbackEnabled: false
    };
  });
}

function normalizedPolicy(settings: AiSettings): AiAutoModelPolicy {
  const configured = new Set(
    (settings.savedProviders || [])
      .filter((item) => (item.provider === 'local' || item.hasApiKey) && item.keyStatus !== 'invalid')
      .map((item) => item.provider)
  );
  const source = settings.autoModelPolicy?.autoProviderOrder?.length
    ? settings.autoModelPolicy.autoProviderOrder
    : [
        ...defaultAutoProviderOrder.filter((provider) => configured.has(provider)),
        ...defaultAutoProviderOrder.filter((provider) => !configured.has(provider))
      ];
  const autoProviderOrder = [...source, ...defaultAutoProviderOrder]
    .filter((provider): provider is RoutedProvider => routableProviders.includes(provider as RoutedProvider))
    .filter((provider, index, providers) => providers.indexOf(provider) === index);
  return {
    autoProviderOrder,
    taskStrategy: Object.fromEntries(
      routingTaskTypes.map((taskType) => [
        taskType,
        settings.autoModelPolicy?.taskStrategy?.[taskType] || defaultTaskStrategies[taskType]
      ])
    )
  };
}

export function AIWorkspace({ onSettingsChange, t }: AIWorkspaceProps) {
  const [settings, setSettings] = useState<AiSettings>(defaultSettings);
  const [apiKey, setApiKey] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState<'save' | 'test' | 'clear' | 'routing' | ''>('');
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [backendVersion, setBackendVersion] = useState('');
  const [backendOnline, setBackendOnline] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([fetchAiSettings(), fetchBackendHealth()]).then(([settingsResult, healthResult]) => {
      if (cancelled) return;
      if (settingsResult.status === 'fulfilled') {
        const normalized = settingsForEditor(settingsResult.value);
        setSettings(normalized);
        onSettingsChange?.(normalized);
      } else {
        setStatus(apiErrorMessage(settingsResult.reason));
      }
      if (healthResult.status === 'fulfilled' && healthResult.value.status === 'ok') {
        setBackendOnline(true);
        setBackendVersion(healthResult.value.version || '');
      } else {
        setBackendOnline(false);
        setBackendVersion('');
        if (settingsResult.status === 'fulfilled') {
          setStatus(apiErrorMessage(healthResult.status === 'rejected' ? healthResult.reason : new ApiNetworkError()));
        }
      }
    });
    return () => { cancelled = true; };
  }, [onSettingsChange, reloadToken]);

  const rules = normalizedRules(settings);
  const policy = normalizedPolicy(settings);
  const savedProviders = settings.savedProviders || [];
  const configuredProviders = new Map(
    savedProviders.map((item) => [item.provider, (item.provider === 'local' || item.hasApiKey) && item.keyStatus !== 'invalid'])
  );
  const activeConfigured = settings.provider === 'local'
    ? Boolean(settings.baseUrl.trim() && settings.model.trim())
    : settings.hasApiKey;

  function updateSettings(updater: (current: AiSettings) => AiSettings) {
    setStatus('');
    setSettings(updater);
  }

  function switchProvider(provider: AiProvider) {
    setApiKey(apiKeyDraftAfterProviderSwitch(apiKey));
    updateSettings((current) => {
      const saved = savedProviders.find((item) => item.provider === provider);
      const oldDefault = normalizeBaseUrlForCompare(providerDefaultBaseUrls[current.provider] || '');
      const currentBase = normalizeBaseUrlForCompare(current.baseUrl);
      const useDefaultBase = provider !== 'custom' && (!currentBase || currentBase === oldDefault);
      const knownModels = Object.values(providerDefaultModels);
      const useDefaultModel = !current.model.trim() || knownModels.includes(current.model.trim());
      return {
        ...current,
        provider,
        baseUrl: saved?.baseUrl || (useDefaultBase ? providerDefaultBaseUrls[provider] : current.baseUrl),
        model: saved?.model || (useDefaultModel ? providerDefaultModels[provider] : current.model),
        hasApiKey: Boolean(saved?.hasApiKey),
        keyStatus: saved?.keyStatus || 'unchecked',
        keyErrorType: saved?.keyErrorType || ''
      };
    });
  }

  function settingsPayload(clearKey = false): AiSettingsInput {
    const payload: AiSettingsInput = {
      provider: settings.provider,
      baseUrl: settings.baseUrl.trim(),
      model: settings.model.trim(),
      temperature: settings.temperature,
      timeoutSeconds: settings.timeoutSeconds,
      forceNonThinking: settings.forceNonThinking
    };
    if (clearKey) payload.apiKey = '';
    else if (apiKey.trim()) payload.apiKey = apiKey.trim();
    return payload;
  }

  async function persistSettings(clearKey = false): Promise<AiSettings | null> {
    try {
      const saved = settingsForEditor(await saveAiSettings(settingsPayload(clearKey)));
      setSettings(saved);
      setApiKey('');
      onSettingsChange?.(saved);
      return saved;
    } catch (error) {
      setStatus(apiErrorMessage(error));
      return null;
    }
  }

  async function saveProvider() {
    setBusy('save');
    setStatus('');
    const saved = await persistSettings();
    if (saved) setStatus(t('legacy.settingsSaved'));
    setBusy('');
  }

  async function testProvider() {
    setBusy('test');
    setStatus('');
    const saved = await persistSettings();
    if (saved) {
      try {
        const result = await testAiSettings();
        setStatus(result.message || (result.ok ? t('legacy.modelTestSuccess') : t('legacy.modelTestFailed')));
      } catch (error) {
        setStatus(apiErrorMessage(error));
      } finally {
        try {
          const refreshed = settingsForEditor(await fetchAiSettings());
          setSettings(refreshed);
          onSettingsChange?.(refreshed);
        } catch { /* preserve the test result; the next refresh will retry settings */ }
      }
    }
    setBusy('');
  }

  async function clearProvider(provider: AiProvider) {
    setBusy('clear');
    setStatus('');
    try {
      const saved = settingsForEditor(await deleteAiSettingsKey(provider));
      setSettings(saved);
      setApiKey('');
      onSettingsChange?.(saved);
      setStatus(provider === 'local' ? t('legacy.configCleared') : t('legacy.keyCleared'));
    } catch (error) {
      setStatus(apiErrorMessage(error));
    }
    setBusy('');
  }

  function updateRule(taskType: ModelRoutingTaskType, updater: (rule: AiModelRoutingRule) => AiModelRoutingRule) {
    updateSettings((current) => ({
      ...current,
      routingRules: normalizedRules(current).map((rule) => rule.taskType === taskType ? { ...updater(rule), localFallbackEnabled: false } : rule)
    }));
  }

  function setFallback(taskType: ModelRoutingTaskType, index: number, provider: RoutedProvider | '') {
    updateRule(taskType, (rule) => {
      const fallbacks = [...rule.fallbackProviders];
      if (provider) fallbacks[index] = provider;
      else fallbacks.splice(index, 1);
      return {
        ...rule,
        fallbackProviders: fallbacks
          .filter((item, itemIndex, items) => item !== rule.primaryProvider && items.indexOf(item) === itemIndex)
          .slice(0, 2)
      };
    });
  }

  function moveProvider(provider: RoutedProvider, direction: -1 | 1) {
    updateSettings((current) => {
      const currentPolicy = normalizedPolicy(current);
      const order = [...currentPolicy.autoProviderOrder];
      const index = order.indexOf(provider);
      const next = index + direction;
      if (index < 0 || next < 0 || next >= order.length) return current;
      [order[index], order[next]] = [order[next], order[index]];
      return { ...current, autoModelPolicy: { ...currentPolicy, autoProviderOrder: order } };
    });
  }

  async function saveRouting() {
    setBusy('routing');
    setStatus('');
    try {
      const saved = settingsForEditor(await saveAiSettingsRouting({ routingRules: rules, autoModelPolicy: policy }));
      setSettings(saved);
      onSettingsChange?.(saved);
      setStatus(t('legacy.routingSaved'));
    } catch (error) {
      setStatus(apiErrorMessage(error));
    }
    setBusy('');
  }

  function restoreRouting() {
    updateSettings((current) => ({
      ...current,
      routingRules: recommendedRules(),
      autoModelPolicy: {
        autoProviderOrder: defaultAutoProviderOrder,
        taskStrategy: defaultTaskStrategies
      }
    }));
  }

  const recommendedModels = providerModelRecommendations[settings.provider] || [];
  const keyLabel = settings.provider === 'local' ? t('legacy.apiKeyOptional') : t('legacy.apiKey');

  return (
    <section className="surface ai-panel">
      <div className="section-head">
        <div>
          <h2>{t('legacy.settingsTitle')}</h2>
          <p className="section-hint">{t('legacy.settingsHint')}</p>
        </div>
      </div>
      <div className="model-settings">
        <div className="settings-title">
          <span><Settings size={15} />{t('legacy.aiSettings')}</span>
          <strong>{backendVersion || t('legacy.backendOffline')}</strong>
        </div>
        {!backendOnline && <div className="settings-actions"><span>{t('legacy.backendOfflineHint')}。{t('legacy.backendConnectionFailedFriendly')}</span><button type="button" onClick={() => setReloadToken((value) => value + 1)}>{t('legacy.retryBackend')}</button></div>}
        <div className="settings-grid">
          <label>
            <span>{t('legacy.provider')}</span>
            <select value={settings.provider} onChange={(event) => switchProvider(event.target.value as AiProvider)}>
              {routableProviders.map((provider) => <option key={provider} value={provider}>{providerLabel(provider, t)}</option>)}
            </select>
          </label>
          <label>
            <span>{t('legacy.baseUrl')}</span>
            <input value={settings.baseUrl} onChange={(event) => updateSettings((current) => ({ ...current, baseUrl: event.target.value }))} />
          </label>
          <label>
            <span>{settings.provider === 'local' ? t('legacy.modelId') : t('legacy.model')}</span>
            <div className="model-picker" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setModelMenuOpen(false); }}>
              <input value={settings.model} onChange={(event) => updateSettings((current) => ({ ...current, model: event.target.value }))} />
              {recommendedModels.length > 0 && <button type="button" className="model-picker-toggle" aria-label={t('legacy.recommendedModel')} aria-expanded={modelMenuOpen} onClick={() => setModelMenuOpen((open) => !open)} />}
              {modelMenuOpen && recommendedModels.length > 0 && (
                <div className="model-picker-menu" role="listbox">
                  {recommendedModels.map((model) => <button key={model} type="button" role="option" aria-selected={settings.model === model} onClick={() => { updateSettings((current) => ({ ...current, model })); setModelMenuOpen(false); }}>{model}</button>)}
                </div>
              )}
            </div>
          </label>
          <label>
            <span><KeyRound size={13} />{keyLabel}</span>
            <input type="password" value={apiKey} onChange={(event) => { setStatus(''); setApiKey(event.target.value); }} placeholder={settings.provider === 'local' ? t('legacy.apiKeyOptionalPlaceholder') : t('legacy.apiKeyPlaceholder')} />
          </label>
          <label><span>{t('legacy.temperature')}</span><input type="number" min={0} max={2} step={0.1} value={settings.temperature} onChange={(event) => updateSettings((current) => ({ ...current, temperature: Number(event.target.value) }))} /></label>
          <label><span>{t('legacy.timeout')}</span><input type="number" min={5} max={120} value={settings.timeoutSeconds} onChange={(event) => updateSettings((current) => ({ ...current, timeoutSeconds: Number(event.target.value) }))} /></label>
          <label className="routing-toggle"><input type="checkbox" checked={settings.forceNonThinking} onChange={(event) => updateSettings((current) => ({ ...current, forceNonThinking: event.target.checked }))} /><span>{t('legacy.forceNonThinking')}</span></label>
        </div>
        <p className="section-hint">{settings.forceNonThinking ? t('legacy.forceNonThinkingEnabled') : t('legacy.forceNonThinkingDisabled')}</p>
        <div className="settings-actions">
          <button onClick={saveProvider} disabled={!backendOnline || Boolean(busy)}><Save size={16} />{t('legacy.saveSettings')}</button>
          <button onClick={testProvider} disabled={!backendOnline || Boolean(busy)}><PlugZap size={16} />{t('legacy.testModel')}</button>
          <button onClick={() => clearProvider(settings.provider)} disabled={!backendOnline || Boolean(busy) || !activeConfigured}><Trash2 size={16} />{settings.provider === 'local' ? t('legacy.clearConfig') : t('legacy.clearKey')}</button>
          {status && <span>{status}</span>}
        </div>
        <div className="saved-provider-keys">
          <span>{t('legacy.savedApiKeys')}</span>
          <div>
            {savedProviders.length ? savedProviders.map((item) => (
              <button key={item.provider} type="button" className="saved-provider-key" onClick={() => clearProvider(item.provider)} disabled={!backendOnline || Boolean(busy)}>
                <span>{providerLabel(item.provider, t)}{item.provider === 'local' ? ` · ${t('legacy.localModelConfigured')}` : ''}</span><X size={13} />
              </button>
            )) : <em>{t('legacy.noSavedApiKeys')}</em>}
          </div>
        </div>
        <div className="model-routing-settings">
          <div className="settings-title"><span><RotateCcw size={15} />{t('legacy.modelRouting')}</span><strong>{t('legacy.modelRoutingHint')}</strong></div>
          <div className="auto-provider-order">
            {policy.autoProviderOrder.map((provider, index) => (
              <div className={`auto-provider-chip ${configuredProviders.get(provider) ? '' : 'missing-key'}`} key={provider}>
                <span>{index + 1}. {providerLabel(provider, t)}</span>
                <button type="button" aria-label={t('legacy.moveProviderUp')} disabled={index === 0 || Boolean(busy)} onClick={() => moveProvider(provider, -1)}><ArrowUp size={13} /></button>
                <button type="button" aria-label={t('legacy.moveProviderDown')} disabled={index === policy.autoProviderOrder.length - 1 || Boolean(busy)} onClick={() => moveProvider(provider, 1)}><ArrowDown size={13} /></button>
              </div>
            ))}
          </div>
          <div className="auto-task-preview">
            {rules.map((rule) => <div className="auto-task-preview-row" key={`preview-${rule.taskType}`}><span>{taskLabel(rule.taskType, t)}</span><strong>{strategyLabel(policy.taskStrategy[rule.taskType] || defaultTaskStrategies[rule.taskType], t)}</strong></div>)}
          </div>
          <div className="routing-grid" role="table" aria-label={t('legacy.modelRouting')}>
            <div className="routing-row routing-head" role="row"><span>{t('legacy.routingTask')}</span><span>{t('legacy.routingPrimary')}</span><span>{t('legacy.routingFallbackOne')}</span><span>{t('legacy.routingFallbackTwo')}</span><span>{t('legacy.routingLocalFallback')}</span></div>
            {rules.map((rule) => (
              <div className="routing-row" role="row" key={rule.taskType}>
                <span className="routing-task-copy"><strong>{taskLabel(rule.taskType, t)}</strong><small>{taskDescription(rule.taskType, t)}</small></span>
                <select value={rule.primaryProvider} onChange={(event) => updateRule(rule.taskType, (current) => ({ ...current, primaryProvider: event.target.value as RoutingPrimaryProvider }))}>
                  <option value="auto">{t('legacy.routingAutoProvider')}</option>
                  {routableProviders.map((provider) => <option key={provider} value={provider}>{providerLabel(provider, t)}</option>)}
                </select>
                {[0, 1].map((index) => <select key={`${rule.taskType}-${index}`} value={rule.fallbackProviders[index] || ''} onChange={(event) => setFallback(rule.taskType, index, event.target.value as RoutedProvider | '')}><option value="">{t('legacy.routingNoFallback')}</option>{routableProviders.map((provider) => <option key={provider} value={provider}>{providerLabel(provider, t)}</option>)}</select>)}
                <label className="routing-toggle"><input type="checkbox" checked={false} disabled /><span>{t('legacy.cognitiveNoLocalFallback')}</span></label>
              </div>
            ))}
          </div>
          <div className="settings-actions">
            <button type="button" onClick={saveRouting} disabled={!backendOnline || Boolean(busy)}><Save size={16} />{t('legacy.saveRouting')}</button>
            <button type="button" onClick={restoreRouting} disabled={Boolean(busy)}><RotateCcw size={16} />{t('legacy.restoreRecommendedRouting')}</button>
          </div>
        </div>
      </div>
    </section>
  );
}
