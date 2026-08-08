import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { enUS } from '../../i18n/en-US';
import { zhCN } from '../../i18n/zh-CN';
import type { CommandThreadMessage } from '../../stores/commandAgentStore';
import { AgentThread } from './AgentThread';
import { CommandComposer } from './CommandComposer';

type Dictionary = typeof zhCN;

function translator(dictionary: Dictionary) {
  return (key: string): string => {
    const [namespace, item] = key.split('.');
    const values = dictionary[namespace as keyof Dictionary];
    return values?.[item] ?? key;
  };
}

const t = translator(zhCN);
const noop = () => undefined;

function statusMessage(status: string, payload: Record<string, unknown> = {}): CommandThreadMessage {
  return {
    id: `status-${status}`,
    role: 'card',
    kind: 'planning_session_status',
    content: status,
    createdAt: 1,
    payload: {
      status,
      businessStatus: status,
      planningPhase: 'UNDERSTANDING',
      understandingSnapshot: {
        goalSummary: '三个月内准备 AI 应用开发实习',
        facts: ['会 Python、FastAPI 和 React'],
        unknowns: []
      },
      ...payload
    }
  };
}

function renderThread(messages: CommandThreadMessage[], sending = false) {
  return renderToStaticMarkup(
    <AgentThread
      messages={messages}
      sending={sending}
      onApprove={noop}
      onSend={noop}
      t={t}
    />
  );
}

describe('Pure V2 Command lifecycle UI', () => {
  it('renders a fresh thread with only the goal composer and translated empty state', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const html = renderToStaticMarkup(
      <>
        <AgentThread messages={[]} sending={false} onApprove={noop} onSend={noop} t={t} />
        <CommandComposer
          sending={false}
          permission="low"
          onSend={noop}
          onPermissionChange={noop}
          t={t}
        />
      </>
    );
    consoleError.mockRestore();

    expect(html).toContain('<h1>Planix</h1>');
    expect(html).toContain(zhCN.command.emptyDescription);
    expect(html).toContain(zhCN.command.placeholder);
    expect(html).not.toContain('开始深度规划');
    expect(html).not.toContain('更多操作');
    expect(html).not.toContain('补充目标信息');
    expect(html).not.toContain('修改计划');
    expect(html).not.toContain('写入日历');
  });

  it('keeps clarification inside the Planning Workspace', () => {
    const html = renderThread([statusMessage('needs_goal_clarification', {
      understandingSnapshot: {
        goalSummary: '准备 AI 应用开发实习',
        unknowns: ['每周可投入时间'],
        nextQuestion: {
          question: '你每周可以投入多少小时？',
          options: ['5 小时', '10 小时', '15 小时']
        }
      }
    })]);

    expect(html).toContain('你每周可以投入多少小时？');
    expect(html).toContain('10 小时');
    expect(html).not.toContain('补充目标信息');
    expect(html).not.toContain('更多操作');
  });

  it('shows only understanding confirmation actions while waiting for confirmation', () => {
    const html = renderThread([statusMessage('waiting_understanding_confirmation')]);
    expect(html).toContain('确认当前理解');
    expect(html).toContain('修正当前理解');
    expect(html).not.toContain('更多操作');
    expect(html).not.toContain('写入日历');
  });

  it('shows no lifecycle actions during automatic planning', () => {
    const html = renderThread([statusMessage('planning', { planningPhase: 'PLAN_GENERATION' })]);
    expect(html).not.toContain('确认当前理解');
    expect(html).not.toContain('修正当前理解');
    expect(html).not.toContain('确认最终计划');
    expect(html).not.toContain('修改最终计划');
    expect(html).not.toContain('重试当前阶段');
  });

  it('shows final approval and real revision actions only at final review', () => {
    const html = renderThread([statusMessage('waiting_final_review', {
      planningPhase: 'FINAL_REVIEW',
      planBlueprint: { tasks: [{ id: 'task-1', title: '完成 Agent 项目' }] },
      planQualityReport: { hardRulesPassed: true, issues: [] },
      scheduleBlueprint: { sessions: [{ id: 'session-1', start: '2026-08-10T10:00:00+08:00', durationMinutes: 60 }] },
      scheduleQualityReport: { hardRulesPassed: true, issues: [] },
      calendarProposal: { events: [{ sourceKey: 'event-1', start: '2026-08-10T10:00:00+08:00', title: '完成 Agent 项目' }] }
    })]);

    expect(html).toContain('确认最终计划');
    expect(html).toContain('修改最终计划');
    expect(html).not.toContain('批准并写入日历');
    expect(html).not.toContain('更多操作');
  });

  it('keeps a single model retry inside the Planning Workspace', () => {
    const html = renderThread([statusMessage('MODEL_UNAVAILABLE', {
      runtimeStatus: 'blocked_model',
      modelFailure: { summary: '模型暂时不可用', retryable: true }
    })]);
    expect((html.match(/重试当前阶段/g) || [])).toHaveLength(1);
    expect(html).not.toContain('重试深度规划');
  });

  it('keeps Calendar permission in ApprovalCard after final approval', () => {
    const html = renderThread([{
      id: 'approval-1',
      role: 'card',
      kind: 'approval',
      content: '确认将计划写入日历',
      actionId: 'action-1',
      createdAt: 1,
      payload: { target: 'calendar', operation: 'create_or_update_plans', risk: 'write' }
    }]);
    expect(html).toContain('确认写入');
    expect(html).toContain('取消');
  });
});

describe('Command empty-state translations', () => {
  it('defines the empty description in Chinese and English without falling back to the key', () => {
    const zh = translator(zhCN)('command.emptyDescription');
    const en = translator(enUS)('command.emptyDescription');
    expect(zh).toBe('描述你的目标，Planix 会先理解需求，再生成可执行计划。');
    expect(en).toBe('Describe your goal. Planix will understand it first, then build an executable plan.');
    expect(zh).not.toBe('command.emptyDescription');
    expect(en).not.toBe('command.emptyDescription');
  });
});
