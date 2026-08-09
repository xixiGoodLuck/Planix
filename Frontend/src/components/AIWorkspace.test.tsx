import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { settingsForEditor } from '../lib/aiSettingsDefaults';
import { AIWorkspace } from './AIWorkspace';

describe('AIWorkspace backend availability', () => {
  it('does not display DeepSeek while retaining a hidden mock provider value', () => {
    const editable = settingsForEditor({
      provider: 'mock', baseUrl: 'https://api.deepseek.com', model: 'mock-model', hasApiKey: false,
      temperature: 0.3, timeoutSeconds: 40, updatedAt: '', savedProviders: [], routingRules: []
    });
    expect(editable.provider).toBe('deepseek');
    expect(editable.model).not.toBe('mock-model');
  });

  it('starts offline with persistence actions disabled and a startup hint', () => {
    const html = renderToStaticMarkup(<AIWorkspace t={(key) => key} />);
    expect(html).toContain('legacy.backendOfflineHint');
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>.*legacy\.saveSettings/s);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>.*legacy\.testModel/s);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>.*legacy\.saveRouting/s);
  });
});
