import { describe, expect, it } from 'vitest';
import { getMonthDays, toISO } from './date';

describe('date utilities', () => {
  it('formats dates as stable ISO strings', () => {
    expect(toISO(new Date(2026, 5, 30))).toBe('2026-06-30');
  });

  it('builds a 6-week calendar grid', () => {
    expect(getMonthDays(new Date(2026, 5, 1))).toHaveLength(42);
  });
});
