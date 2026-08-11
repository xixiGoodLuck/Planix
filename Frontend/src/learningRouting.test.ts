import { describe, expect, it } from 'vitest';
import { appMenuRoutes, appRoutes, defaultRoute, normalizeAppRoute } from './shell/useAppRoute';

describe('Learning-only routing', () => {
  it('uses Learning as the only product home', () => {
    expect(defaultRoute).toBe('learning');
    expect(appRoutes).toEqual(['learning', 'settings']);
    expect(appMenuRoutes).toEqual(['learning', 'settings']);
  });

  it('normalizes retired hashes to Learning', () => {
    expect(normalizeAppRoute('comm' + 'and')).toBe('learning');
    expect(normalizeAppRoute('calen' + 'dar')).toBe('learning');
  });
});
