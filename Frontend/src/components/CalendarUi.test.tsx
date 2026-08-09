import { readFileSync } from 'node:fs';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { PlanList } from './PlanList';
import { handlePlanAddClick, handlePlanInputKeyDown } from './planListActions';
import { enUS } from '../i18n/en-US';
import { zhCN } from '../i18n/zh-CN';

const plan = { id: 'plan-1', date: '2026-08-09', time: '03:00', title: 'Build Agent demo', done: false, completion: '' };
const t = (key: string) => key.split('.').reduce<unknown>((value, part) => (value as Record<string, unknown>)?.[part], zhCN) as string ?? key;

describe('Calendar UI', () => {
  it('renders the canonical add-row and delete-button hooks', () => {
    const html = renderToStaticMarkup(
      <PlanList
        date="2026-08-09"
        lang="zh-CN"
        plans={[plan]}
        draft=""
        time="03:00"
        onDraftChange={() => undefined}
        onTimeChange={() => undefined}
        onAdd={() => undefined}
        onToggle={() => undefined}
        onDelete={() => undefined}
        onCompletionChange={() => undefined}
        t={t}
      />
    );

    expect(html).toContain('calendar-plan-add-row');
    expect(html).toContain('calendar-time-input');
    expect(html).toContain('calendar-plan-input');
    expect(html).toContain('calendar-add-button');
    expect(html).toContain('calendar-delete-button');
    expect(html).toContain('aria-label="删除计划"');
    expect(html).toContain('value="03:00"');
    expect(html).toContain('Build Agent demo');
    for (const rawKey of ['legacy.addPlan', 'legacy.add', 'legacy.monthNote', 'legacy.plans', 'legacy.completion']) {
      expect(html).not.toContain(rawKey);
    }
  });

  it('only submits the draft on Enter', () => {
    let calls = 0;
    const onAdd = () => { calls += 1; };
    handlePlanInputKeyDown('Escape', onAdd);
    expect(calls).toBe(0);
    handlePlanInputKeyDown('Enter', onAdd);
    expect(calls).toBe(1);
  });

  it('submits through the add button action', () => {
    let calls = 0;
    handlePlanAddClick(() => { calls += 1; });
    expect(calls).toBe(1);
  });

  it('keeps every live Calendar key in both dictionaries', () => {
    const keys = ['calendar', 'expandCalendar', 'collapseCalendar', 'monthNote', 'monthNotePlaceholder', 'previousMonth', 'nextMonth', 'plans', 'completion', 'done', 'pending', 'emptyPlans', 'emptyHint', 'deletePlan', 'addPlanPlaceholder', 'addPlan', 'time', 'confirmClearDayPlans', 'clearSelectedDayPlans', 'clearingDayPlans', 'clearDayPlansDone', 'clearDayPlansFailed', 'confirmClearAllPlans', 'clearAllPlans', 'clearingAllPlans', 'clearAllPlansDone', 'clearAllPlansFailed', 'deletedCount', 'failedCount'];
    for (const key of keys) {
      expect(zhCN.calendar[key], `zh-CN calendar.${key}`).toBeTruthy();
      expect(enUS.calendar[key], `en-US calendar.${key}`).toBeTruthy();
    }
  });

  it('keeps Calendar components out of the legacy namespace', () => {
    for (const file of ['./CalendarPanel.tsx', './PlanList.tsx', '../pages/CalendarPage.tsx']) {
      expect(readFileSync(new URL(file, import.meta.url), 'utf8')).not.toContain("t('legacy.");
    }
  });

  it('defines every canonical PlanList class in the stylesheet', () => {
    const css = readFileSync(new URL('../styles.css', import.meta.url), 'utf8');
    for (const className of ['calendar-plan-add-row', 'calendar-time-input', 'calendar-plan-input', 'calendar-add-button', 'calendar-delete-button']) {
      expect(css).toContain(`.${className}`);
    }
  });
});
