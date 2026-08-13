import { useEffect, useState } from 'react';
import { KeyRound, PlugZap, RotateCcw, Save, Settings, Trash2, X } from 'lucide-react';
import type {
  AiAutoModelPolicy,
  AiModelRoutingRule,
  AiProvider,
  AiSettings,
  AiSettingsInput,
  Language,
  LearningRuntimeHealth,
  RoutingPrimaryProvider
} from '../types';
import {
  ApiNetworkError,
  apiErrorMessage,
  deleteAiSettingsKey,
  fetchAiSettings,
  fetchBackendHealth,
  fetchLearningHealth,
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

interface AIWorkspaceProps {
  language: Language;
  onLanguageChange: (language: Language) => void;
  t: (key: string) => string;
}
type RoutedProvider = Exclude<AiProvider, 'mock'>;

const providers: RoutedProvider[] = ['deepseek', 'kimi', 'zhipu_glm', 'openai', 'custom', 'local'];
const defaultOrder: RoutedProvider[] = ['deepseek', 'zhipu_glm', 'kimi', 'openai', 'custom', 'local'];
const defaultRule: AiModelRoutingRule = {
  taskType: 'learning_semantic',
  primaryProvider: 'auto',
  fallbackProviders: ['deepseek'],
  localFallbackEnabled: false
};
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
  routingRules: [defaultRule],
  autoModelPolicy: { autoProviderOrder: defaultOrder, taskStrategy: { learning_semantic: 'knowledge_reasoning' } }
};

function providerLabel(provider: AiProvider, t: (key: string) => string): string {
  return t(`settings.provider_${provider}`);
}

function normalizeRule(settings: AiSettings): AiModelRoutingRule {
  const existing = settings.routingRules?.find((rule) => rule.taskType === 'learning_semantic');
  if (!existing) return { ...defaultRule };
  const primary = existing.primaryProvider === 'auto' || providers.includes(existing.primaryProvider)
    ? existing.primaryProvider
    : 'auto';
  return {
    ...existing,
    taskType: 'learning_semantic',
    primaryProvider: primary,
    fallbackProviders: existing.fallbackProviders
      .filter((provider): provider is RoutedProvider => provider !== 'mock' && providers.includes(provider as RoutedProvider))
      .filter((provider, index, values) => provider !== primary && values.indexOf(provider) === index)
      .slice(0, 2),
    localFallbackEnabled: false
  };
}

function normalizePolicy(settings: AiSettings): AiAutoModelPolicy {
  const source = settings.autoModelPolicy?.autoProviderOrder?.length
    ? settings.autoModelPolicy.autoProviderOrder
    : defaultOrder;
  return {
    autoProviderOrder: [...source, ...defaultOrder]
      .filter((provider): provider is RoutedProvider => providers.includes(provider as RoutedProvider))
      .filter((provider, index, values) => values.indexOf(provider) === index),
    taskStrategy: { learning_semantic: 'knowledge_reasoning' }
  };
}

function healthStatus(health: LearningRuntimeHealth | null, key: 'model' | 'video' | 'transcript'): string {
  if (!health) return 'unavailable';
  if (key === 'transcript' && health.transcript_source_status?.status) return health.transcript_source_status.status;
  const candidates = key === 'model' ? ['model', 'model_provider'] : key === 'video' ? ['video', 'video_provider'] : ['transcript', 'transcript_provider'];
  for (const candidate of candidates) {
    const status = health.providers?.[candidate]?.status;
    if (status) return status;
  }
  return health.status;
}

export function AIWorkspace({ language, onLanguageChange, t }: AIWorkspaceProps) {
  const [settings, setSettings] = useState(defaultSettings);
  const [apiKey, setApiKey] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState<'save' | 'test' | 'clear' | 'routing' | ''>('');
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [backendVersion, setBackendVersion] = useState('');
  const [backendOnline, setBackendOnline] = useState(false);
  const [learningHealth, setLearningHealth] = useState<LearningRuntimeHealth | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([fetchAiSettings(), fetchBackendHealth(), fetchLearningHealth()]).then(([settingsResult, backendResult, learningResult]) => {
      if (cancelled) return;
      if (settingsResult.status === 'fulfilled') setSettings(settingsForEditor(settingsResult.value));
      else setStatus(apiErrorMessage(settingsResult.reason));
      if (backendResult.status === 'fulfilled' && backendResult.value.status === 'ok') {
        setBackendOnline(true);
        setBackendVersion(backendResult.value.version || 'online');
        if (settingsResult.status === 'fulfilled') setStatus('');
      } else {
        setBackendOnline(false);
        setBackendVersion('');
        if (settingsResult.status === 'fulfilled') setStatus(apiErrorMessage(backendResult.status === 'rejected' ? backendResult.reason : new ApiNetworkError()));
      }
      setLearningHealth(learningResult.status === 'fulfilled' ? learningResult.value : null);
    });
    return () => { cancelled = true; };
  }, [reloadToken]);

  const savedProviders = settings.savedProviders || [];
  const rule = normalizeRule(settings);
  const policy = normalizePolicy(settings);
  const configured = settings.provider === 'local'
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
      const currentIsDefault = normalizeBaseUrlForCompare(current.baseUrl) === normalizeBaseUrlForCompare(providerDefaultBaseUrls[current.provider] || '');
      const modelIsDefault = Object.values(providerDefaultModels).includes(current.model.trim());
      return {
        ...current,
        provider,
        baseUrl: saved?.baseUrl || (currentIsDefault || !current.baseUrl.trim() ? providerDefaultBaseUrls[provider] : current.baseUrl),
        model: saved?.model || (modelIsDefault || !current.model.trim() ? providerDefaultModels[provider] : current.model),
        hasApiKey: Boolean(saved?.hasApiKey),
        keyStatus: saved?.keyStatus || 'unchecked',
        keyErrorType: saved?.keyErrorType || ''
      };
    });
  }

  function payload(clearKey = false): AiSettingsInput {
    const value: AiSettingsInput = {
      provider: settings.provider,
      baseUrl: settings.baseUrl.trim(),
      model: settings.model.trim(),
      temperature: settings.temperature,
      timeoutSeconds: settings.timeoutSeconds,
      forceNonThinking: settings.forceNonThinking
    };
    if (clearKey) value.apiKey = '';
    else if (apiKey.trim()) value.apiKey = apiKey.trim();
    return value;
  }

  async function persist(clearKey = false) {
    try {
      const saved = settingsForEditor(await saveAiSettings(payload(clearKey)));
      setSettings(saved);
      setApiKey('');
      return saved;
    } catch (error) {
      setStatus(apiErrorMessage(error));
      return null;
    }
  }

  async function saveProvider() {
    setBusy('save'); setStatus('');
    if (await persist()) setStatus(t('settings.settingsSaved'));
    setBusy('');
  }

  async function testProvider() {
    setBusy('test'); setStatus('');
    if (await persist()) {
      try {
        const result = await testAiSettings();
        setStatus(result.message || (result.ok ? t('settings.modelTestSuccess') : t('settings.modelTestFailed')));
      } catch (error) { setStatus(apiErrorMessage(error)); }
      try { setSettings(settingsForEditor(await fetchAiSettings())); } catch { /* retry on refresh */ }
    }
    setBusy('');
  }

  async function clearProvider(provider: AiProvider) {
    setBusy('clear'); setStatus('');
    try {
      setSettings(settingsForEditor(await deleteAiSettingsKey(provider)));
      setApiKey('');
      setStatus(provider === 'local' ? t('settings.configCleared') : t('settings.keyCleared'));
    } catch (error) { setStatus(apiErrorMessage(error)); }
    setBusy('');
  }

  function setFallback(index: number, provider: RoutedProvider | '') {
    const fallbacks = [...rule.fallbackProviders];
    if (provider) fallbacks[index] = provider;
    else fallbacks.splice(index, 1);
    updateSettings((current) => ({
      ...current,
      routingRules: [{ ...rule, fallbackProviders: fallbacks.filter((item, itemIndex, values) => item !== rule.primaryProvider && values.indexOf(item) === itemIndex).slice(0, 2) }]
    }));
  }

  async function saveRouting() {
    setBusy('routing'); setStatus('');
    try {
      setSettings(settingsForEditor(await saveAiSettingsRouting({ routingRules: [rule], autoModelPolicy: policy })));
      setStatus(t('settings.routingSaved'));
    } catch (error) { setStatus(apiErrorMessage(error)); }
    setBusy('');
  }

  const recommendedModels = providerModelRecommendations[settings.provider] || [];
  return (
    <section className="surface ai-panel">
      <div className="section-head">
        <div>
          <h1>{t('settings.title')}</h1>
          <p className="section-hint">{t('settings.subtitle')}</p>
          <div className="settings-language-row">
            <span>{t('shell.language')}</span>
            <div className="settings-language-switch" role="group" aria-label={t('shell.languageBilingual')}>
              <button
                className={language === 'zh-CN' ? 'active' : ''}
                type="button"
                aria-pressed={language === 'zh-CN'}
                onClick={() => onLanguageChange('zh-CN')}
              >
                {t('shell.languageZh')}
              </button>
              <button
                className={language === 'en-US' ? 'active' : ''}
                type="button"
                aria-pressed={language === 'en-US'}
                onClick={() => onLanguageChange('en-US')}
              >
                {t('shell.languageEn')}
              </button>
            </div>
          </div>
        </div>
      </div>
      <div className="model-settings">
        <div className="settings-title"><span><Settings size={15} />{t('settings.aiSettings')}</span><strong>{backendVersion || t('settings.backendOffline')}</strong></div>
        {!backendOnline && <div className="settings-actions"><span>{t('settings.backendOfflineHint')}</span><button type="button" onClick={() => setReloadToken((value) => value + 1)}>{t('settings.retryBackend')}</button></div>}
        <div className="settings-grid">
          <label><span>{t('settings.provider')}</span><select value={settings.provider} onChange={(event) => switchProvider(event.target.value as AiProvider)}>{providers.map((provider) => <option key={provider} value={provider}>{providerLabel(provider, t)}</option>)}</select></label>
          <label><span>{t('settings.baseUrl')}</span><input value={settings.baseUrl} onChange={(event) => updateSettings((current) => ({ ...current, baseUrl: event.target.value }))} /></label>
          <label><span>{settings.provider === 'local' ? t('settings.modelId') : t('settings.model')}</span><div className="model-picker"><input value={settings.model} onChange={(event) => updateSettings((current) => ({ ...current, model: event.target.value }))} />{recommendedModels.length > 0 && <button type="button" className="model-picker-toggle" aria-label={t('settings.recommendedModel')} onClick={() => setModelMenuOpen((value) => !value)} />}{modelMenuOpen && <div className="model-picker-menu">{recommendedModels.map((model) => <button key={model} type="button" onClick={() => { updateSettings((current) => ({ ...current, model })); setModelMenuOpen(false); }}>{model}</button>)}</div>}</div></label>
          <label><span><KeyRound size={13} />{settings.provider === 'local' ? t('settings.apiKeyOptional') : t('settings.apiKey')}</span><input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={settings.provider === 'local' ? t('settings.apiKeyOptionalPlaceholder') : t('settings.apiKeyPlaceholder')} /></label>
          <label><span>{t('settings.temperature')}</span><input type="number" min={0} max={2} step={0.1} value={settings.temperature} onChange={(event) => updateSettings((current) => ({ ...current, temperature: Number(event.target.value) }))} /></label>
          <label><span>{t('settings.timeout')}</span><input type="number" min={5} max={120} value={settings.timeoutSeconds} onChange={(event) => updateSettings((current) => ({ ...current, timeoutSeconds: Number(event.target.value) }))} /></label>
          <label className="routing-toggle"><input type="checkbox" checked={settings.forceNonThinking} onChange={(event) => updateSettings((current) => ({ ...current, forceNonThinking: event.target.checked }))} /><span>{t('settings.forceNonThinking')}</span></label>
        </div>
        <p className="section-hint">{settings.forceNonThinking ? t('settings.forceNonThinkingEnabled') : t('settings.forceNonThinkingDisabled')}</p>
        <div className="settings-actions"><button onClick={saveProvider} disabled={!backendOnline || Boolean(busy)}><Save size={16} />{t('settings.saveSettings')}</button><button onClick={testProvider} disabled={!backendOnline || Boolean(busy)}><PlugZap size={16} />{t('settings.testModel')}</button><button onClick={() => clearProvider(settings.provider)} disabled={!backendOnline || Boolean(busy) || !configured}><Trash2 size={16} />{settings.provider === 'local' ? t('settings.clearConfig') : t('settings.clearKey')}</button>{status && <span role="status">{status}</span>}</div>
        <div className="saved-provider-keys"><span>{t('settings.savedProviders')}</span><div>{savedProviders.length ? savedProviders.map((item) => <button key={item.provider} type="button" className="saved-provider-key" onClick={() => clearProvider(item.provider)} disabled={!backendOnline || Boolean(busy)}><span>{providerLabel(item.provider, t)}{item.provider === 'local' ? ` · ${t('settings.localConfigured')}` : ''}</span><X size={13} /></button>) : <em>{t('settings.noSavedProviders')}</em>}</div></div>
        <div className="model-routing-settings">
          <div className="settings-title"><span><RotateCcw size={15} />{t('settings.learningRouting')}</span><strong>{t('settings.learningRoutingHint')}</strong></div>
          <div className="routing-grid"><div className="routing-row routing-head"><span>{t('settings.routingTask')}</span><span>{t('settings.routingPrimary')}</span><span>{t('settings.routingFallbackOne')}</span><span>{t('settings.routingFallbackTwo')}</span></div><div className="routing-row"><span className="routing-task-copy"><strong>{t('settings.learningSemantic')}</strong><small>{t('settings.learningSemanticDesc')}</small></span><select value={rule.primaryProvider} onChange={(event) => updateSettings((current) => ({ ...current, routingRules: [{ ...rule, primaryProvider: event.target.value as RoutingPrimaryProvider }] }))}><option value="auto">{t('settings.routingAuto')}</option>{providers.map((provider) => <option key={provider} value={provider}>{providerLabel(provider, t)}</option>)}</select>{[0, 1].map((index) => <select key={index} value={rule.fallbackProviders[index] || ''} onChange={(event) => setFallback(index, event.target.value as RoutedProvider | '')}><option value="">{t('settings.routingNone')}</option>{providers.map((provider) => <option key={provider} value={provider}>{providerLabel(provider, t)}</option>)}</select>)}</div></div>
          <div className="settings-actions"><button type="button" onClick={saveRouting} disabled={!backendOnline || Boolean(busy)}><Save size={16} />{t('settings.saveRouting')}</button></div>
        </div>
        <div className="runtime-health-grid">
          <div><span>{t('settings.learningRuntimeHealth')}</span><strong>{learningHealth?.status || 'unavailable'}</strong></div>
          <div><span>{t('settings.modelProviderHealth')}</span><strong>{healthStatus(learningHealth, 'model')}</strong></div>
          <div><span>{t('settings.videoProviderHealth')}</span><strong>{healthStatus(learningHealth, 'video')}</strong></div>
          <div><span>{t('settings.transcriptProviderHealth')}</span><strong>{healthStatus(learningHealth, 'transcript')}</strong></div>
          <div><span>{t('settings.artifactStoreHealth')}</span><strong>{learningHealth?.artifact_store?.status || 'unavailable'}</strong></div>
        </div>
      </div>
    </section>
  );
}
