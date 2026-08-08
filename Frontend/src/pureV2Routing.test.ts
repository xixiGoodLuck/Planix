import { describe, expect, it } from 'vitest';
import { routingTaskTypes } from './lib/aiRouting';
import { appMenuRoutes, appRoutes, defaultRoute, normalizeAppRoute } from './shell/useAppRoute';

describe('Pure Planix V2 frontend routing', () => {
  it('shows exactly the four formal model routes', () => {
    expect(routingTaskTypes).toEqual([
      'planning_understanding',
      'planning_plan',
      'planning_review',
      'planning_learning'
    ]);
  });

  it('keeps Command as default and exposes no retired runtime pages', () => {
    expect(defaultRoute).toBe('command');
    expect(appRoutes).toEqual(['calendar', 'settings', 'command']);
    expect(appMenuRoutes).toEqual(['calendar', 'settings']);
    expect(normalizeAppRoute('notes')).toBe('command');
  });
});
