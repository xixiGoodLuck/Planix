import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { apiKeyDraftAfterProviderSwitch, providerModelRecommendations, upgradeLegacyKimiDefaults } from '../lib/aiSettingsDefaults';
import { AIWorkspace } from './AIWorkspace';
import type { AiSettings } from '../types';

const labels: Record<string, string> = {
  'legacy.goalPlaceholder': 'Learn Python',
  'legacy.aiSettings': 'AI settings',
  'legacy.hasKey': 'Key saved',
  'legacy.noKey': 'No key',
  'legacy.currentProvider': 'Current provider',
  'legacy.providerDeepSeek': 'DeepSeek',
  'legacy.providerKimi': 'Kimi',
  'legacy.providerZhipu': 'Zhipu GLM',
  'legacy.providerOpenAI': 'OpenAI',
  'legacy.providerCustom': 'Custom',
  'legacy.providerMock': 'Mock',
  'legacy.apiHealth': 'API health',
  'legacy.backendOffline': 'Backend offline',
  'legacy.provider': 'Provider',
  'legacy.baseUrl': 'Base URL',
  'legacy.model': 'Model',
  'legacy.recommendedModel': 'Recommended model',
  'legacy.apiKey': 'API Key',
  'legacy.deepseekApiKey': 'DeepSeek API Key',
  'legacy.kimiApiKey': 'Kimi API Key',
  'legacy.zhipuApiKey': 'Zhipu GLM API Key',
  'legacy.openaiApiKey': 'OpenAI API Key',
  'legacy.apiKeyPlaceholder': 'Paste API Key',
  'legacy.temperature': 'Temperature',
  'legacy.timeout': 'Timeout',
  'legacy.saveSettings': 'Save settings',
  'legacy.testModel': 'Test model',
  'legacy.clearKey': 'Clear key',
  'legacy.savedApiKeys': 'Saved API Keys',
  'legacy.noSavedApiKeys': 'No saved provider keys',
  'legacy.removeProviderKey': 'Remove provider API key',
  'legacy.modelRouting': 'Model Routing',
  'legacy.modelRoutingHint': 'Choose primary and fallback models per task',
  'legacy.routingTask': 'Task',
  'legacy.routingPrimary': 'Primary',
  'legacy.routingAutoProvider': 'Auto select',
  'legacy.autoModelPolicy': 'Auto selection policy',
  'legacy.autoModelPolicyHint': 'Choose by saved keys, task type, and provider priority',
  'legacy.autoStrategyFastLowCost': 'Speed first',
  'legacy.autoStrategyStructuredStable': 'Structured stable',
  'legacy.autoStrategyStrictJson': 'Strict JSON',
  'legacy.autoStrategyContextSummary': 'Long-context summary',
  'legacy.autoStrategyClassification': 'Classification',
  'legacy.autoStrategyKnowledgeReasoning': 'Knowledge reasoning',
  'legacy.autoStrategyBalanced': 'Balanced',
  'legacy.autoWillUse': 'Will prefer: {provider}',
  'legacy.autoNoSavedProvider': 'No saved-key candidate yet',
  'legacy.manualProviderSelected': 'Manual primary selected',
  'legacy.moveProviderUp': 'Move provider up',
  'legacy.moveProviderDown': 'Move provider down',
  'legacy.routingFallbackOne': 'Fallback 1',
  'legacy.routingFallbackTwo': 'Fallback 2',
  'legacy.routingNoFallback': 'No fallback',
  'legacy.routingLocalFallback': 'Local fallback',
  'legacy.cognitiveNoLocalFallback': 'No local fallback for deep planning',
  'legacy.goalUnderstandingNoLocalFallback': 'No local semantic fallback for goal understanding',
  'legacy.routingMissingKey': 'Missing key',
  'legacy.saveRouting': 'Save routing',
  'legacy.savingRouting': 'Saving routing',
  'legacy.restoreRecommendedRouting': 'Restore recommended routing',
  'legacy.enabled': 'Enabled',
  'legacy.disabled': 'Disabled',
  'legacy.routingTaskCommandDecision': 'Intent',
  'legacy.routingTaskGoalUnderstanding': 'Goal understanding',
  'legacy.routingTaskPlanGeneration': 'Plan generation',
  'legacy.routingTaskRefinement': 'Task refinement',
  'legacy.routingTaskCalendarPatch': 'Calendar patch',
  'legacy.routingTaskMemoryQuery': 'Memory query',
  'legacy.routingTaskMemoryWrite': 'Memory write',
  'legacy.routingTaskNoteQuery': 'Query notes',
  'legacy.routingTaskNoteWrite': 'Write notes',
  'legacy.routingTaskModelKnowledge': 'Knowledge',
  'legacy.routingTaskChat': 'Chat',
  'legacy.routingTaskPlanningGoal': 'Cognitive goal modeling',
  'legacy.routingTaskPlanningEvidence': 'Cognitive evidence',
  'legacy.routingTaskPlanningStrategy': 'Cognitive strategy',
  'legacy.routingTaskPlanningExecution': 'Cognitive execution',
  'legacy.routingTaskPlanningCritique': 'Cognitive critique',
  'legacy.routingTaskPlanningLearning': 'Cognitive learning',
  'legacy.routingTaskCommandDecisionDesc': 'Understands user intent',
  'legacy.routingTaskGoalUnderstandingDesc': 'Understands ambiguity and consistency before routing',
  'legacy.routingTaskPlanGenerationDesc': 'Generates structuredPlan',
  'legacy.routingTaskRefinementDesc': 'Breaks tasks down',
  'legacy.routingTaskCalendarPatchDesc': 'Extracts calendar edits',
  'legacy.routingTaskMemoryQueryDesc': 'Searches memory',
  'legacy.routingTaskMemoryWriteDesc': 'Prepares memory records',
  'legacy.routingTaskModelKnowledgeDesc': 'Adds knowledge',
  'legacy.routingTaskChatDesc': 'Normal chat'
};

function t(key: string): string {
  return labels[key] ?? key;
}

describe('AIWorkspace settings', () => {
  it('never carries an unsaved API key draft into another provider', () => {
    expect(apiKeyDraftAfterProviderSwitch('sk-provider-specific-secret')).toBe('');
  });

  it('uses the DeepSeek-specific API Key label for DeepSeek settings', () => {
    const html = renderToStaticMarkup(
      <AIWorkspace
        data={{}}
        date="2026-07-08"
        preferences=""
        section="settings"
        onPreferencesChange={() => undefined}
        onApplyGoalPlanToCalendar={async () => ({ created: 0, updated: 0, failed: 0, otherDates: false })}
        onReplanApplied={() => undefined}
        onCreateOrUpdateRefinedPlan={async () => {
          throw new Error('not used');
        }}
        onDeletePlanRefinedTask={async () => {
          throw new Error('not used');
        }}
        language="zh-CN"
        t={t}
      />
    );

    expect(html).toContain('DeepSeek API Key');
    expect(html).toContain('Model Routing');
    expect(html).toContain('Auto selection policy');
    expect(html).toContain('Goal understanding');
    expect(html).toContain('Understands ambiguity and consistency before routing');
    expect(html).toContain('No local semantic fallback for goal understanding');
    expect(html).toContain('Auto select');
    const deepSeekOrder = html.indexOf('1. DeepSeek');
    const glmOrder = html.indexOf('2. Zhipu GLM');
    const kimiOrder = html.indexOf('3. Kimi');
    expect(deepSeekOrder).toBeGreaterThan(-1);
    expect(glmOrder).toBeGreaterThan(deepSeekOrder);
    expect(kimiOrder).toBeGreaterThan(glmOrder);
    expect(html).toContain('Plan generation');
    expect(html).toContain('Memory query');
    expect(html).toContain('Searches memory');
    expect(html).toContain('Cognitive critique');
    expect(html).toContain('No local fallback for deep planning');
    expect(html).toContain('Save routing');
  });

  it('keeps Kimi API URLs and selectable models while correcting platform URLs', () => {
    const settings: AiSettings = {
      provider: 'kimi',
      baseUrl: 'https://api.moonshot.ai/v1',
      model: 'moonshot-v1-8k',
      hasApiKey: true,
      temperature: 0.3,
      timeoutSeconds: 40,
      updatedAt: '',
      savedProviders: [
        {
          provider: 'kimi',
          baseUrl: 'https://platform.kimi.ai',
          model: 'kimi-k2.5',
          hasApiKey: true,
          updatedAt: ''
        },
        {
          provider: 'kimi',
          baseUrl: 'https://api.moonshot.cn/v1',
          model: 'kimi-k2.6',
          hasApiKey: false,
          updatedAt: ''
        },
        {
          provider: 'custom',
          baseUrl: 'https://example.test/v1',
          model: 'custom-model',
          hasApiKey: true,
          updatedAt: ''
        },
        {
          provider: 'kimi',
          baseUrl: 'https://proxy.example.test/v1',
          model: 'custom-kimi-model',
          hasApiKey: true,
          updatedAt: ''
        }
      ]
    };

    const upgraded = upgradeLegacyKimiDefaults(settings);

    expect(upgraded.baseUrl).toBe('https://api.moonshot.ai/v1');
    expect(upgraded.model).toBe('moonshot-v1-8k');
    expect(upgraded.savedProviders[0]).toMatchObject({
      provider: 'kimi',
      baseUrl: 'https://api.moonshot.ai/v1',
      model: 'kimi-k2.5'
    });
    expect(upgraded.savedProviders[1]).toMatchObject({
      provider: 'kimi',
      baseUrl: 'https://api.moonshot.ai/v1',
      model: 'kimi-k2.6'
    });
    expect(upgraded.savedProviders[2]).toMatchObject({
      provider: 'custom',
      baseUrl: 'https://example.test/v1',
      model: 'custom-model'
    });
    expect(upgraded.savedProviders[3]).toMatchObject({
      provider: 'kimi',
      baseUrl: 'https://proxy.example.test/v1',
      model: 'custom-kimi-model'
    });
  });

  it('lists Kimi K2 and moonshot models as recommendations', () => {
    expect(providerModelRecommendations.kimi).toEqual([
      'kimi-k2.7-code-highspeed',
      'kimi-k2.7-code',
      'kimi-k2.6',
      'kimi-k2.5',
      'moonshot-v1-8k',
      'moonshot-v1-32k',
      'moonshot-v1-128k'
    ]);
  });

  it('maps only the generic old moonshot-v1 model back to the default model', () => {
    for (const model of ['moonshot-v1']) {
      const upgraded = upgradeLegacyKimiDefaults({
        provider: 'kimi',
        baseUrl: 'https://api.moonshot.ai/v1',
        model,
        hasApiKey: false,
        temperature: 0.3,
        timeoutSeconds: 40,
        updatedAt: '',
        savedProviders: []
      });

      expect(upgraded.model).toBe('kimi-k2.7-code');
    }
  });

  it('keeps kimi-k2.6 as a selectable Kimi model', () => {
    const upgraded = upgradeLegacyKimiDefaults({
      provider: 'kimi',
      baseUrl: 'https://api.moonshot.ai/v1',
      model: 'kimi-k2.6',
      hasApiKey: false,
      temperature: 0.3,
      timeoutSeconds: 40,
      updatedAt: '',
      savedProviders: []
    });

    expect(upgraded.model).toBe('kimi-k2.6');
  });
});
