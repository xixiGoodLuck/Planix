import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { enUS } from '../i18n/en-US';
import { AppMenu } from './AppMenu';

const t = (key: string): string => {
  const [namespace, item] = key.split('.');
  return enUS[namespace as keyof typeof enUS]?.[item] ?? key;
};

describe('Learning-only sidebar', () => {
  it('uses P for Learning and keeps Settings as the only other route', () => {
    const html = renderToStaticMarkup(
      <AppMenu route="learning" onRouteChange={vi.fn()} t={t} />
    );

    expect(html).toContain(`aria-label="${enUS.shell.learningNav}"`);
    expect(html).toContain('aria-current="page"');
    expect(html).toContain('class="menu-icon"');
    expect(html).toContain('class="learning-p-mark"');
    expect(html).toContain(`aria-label="${enUS.shell.settings}"`);
    expect(html.toLowerCase()).not.toContain('command');
    expect(html.toLowerCase()).not.toContain('calendar');
  });

  it('does not expose language controls in the sidebar', () => {
    const html = renderToStaticMarkup(
      <AppMenu route="learning" onRouteChange={vi.fn()} t={t} />
    );

    expect(html).not.toContain(`aria-label="${enUS.shell.languageBilingual}"`);
    expect(html).not.toContain('menu-language');
  });
});
