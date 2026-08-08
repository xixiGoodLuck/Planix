import { describe, expect, it } from 'vitest';
import { routingTaskTypes } from './lib/aiRouting';
import { appRoutes, defaultRoute } from './shell/useAppRoute';

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
    expect(appRoutes).toEqual(['calendar', 'notes', 'settings', 'command']);
  });
});
