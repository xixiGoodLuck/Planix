import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { settingsForEditor } from '../lib/aiSettingsDefaults';
import { AIWorkspace } from './AIWorkspace';

describe('AIWorkspace backend availability', () => {
  it('does not expose the mock provider as an editable production provider', () => {
    const editable = settingsForEditor({
      provider: 'mock', baseUrl: 'https://api.deepseek.com', model: 'mock-model', hasApiKey: false,
      temperature: 0.3, timeoutSeconds: 40, forceNonThinking: false, updatedAt: '', savedProviders: [], routingRules: []
    });
    expect(editable.provider).toBe('deepseek');
    expect(editable.model).not.toBe('mock-model');
  });

  it('starts offline with persistence actions disabled', () => {
    const html = renderToStaticMarkup(<AIWorkspace language="zh-CN" onLanguageChange={() => undefined} t={(key) => key} />);
    expect(html).toContain('settings.backendOfflineHint');
    expect(html).toContain('settings.forceNonThinkingDisabled');
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>.*settings\.saveSettings/s);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>.*settings\.testModel/s);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>.*settings\.saveRouting/s);
  });

  it('offers the language switch in Settings with the active language exposed', () => {
    const html = renderToStaticMarkup(<AIWorkspace language="zh-CN" onLanguageChange={() => undefined} t={(key) => key} />);

    expect(html).toContain('aria-label="shell.languageBilingual"');
    expect(html).toContain('shell.languageZh');
    expect(html).toContain('shell.languageEn');
    expect(html).toMatch(/class="active"[^>]*aria-pressed="true"[^>]*>\s*shell\.languageZh/);
  });
});
