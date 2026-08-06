import { afterEach, describe, expect, it, vi } from 'vitest';
import { agentFlowActions, getAgentFlowSnapshot } from './agentFlowStore';

describe('agentFlowStore', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    agentFlowActions.resetFlow();
  });

  it('applies runtime events without old demo tool names', () => {
    agentFlowActions.resetFlow();

    agentFlowActions.applyRuntimeEvent({
      runId: 'runtime-1',
      sequence: 1,
      type: 'node',
      nodeId: 'input',
      nodeType: 'input',
      title: 'Input',
      content: '帮我规划一个 python 学习计划',
      status: 'done'
    });
    agentFlowActions.applyRuntimeEvent({
      runId: 'runtime-1',
      sequence: 2,
      type: 'tool',
      nodeId: 'tool-materials',
      nodeType: 'tool',
      title: 'search_materials',
      status: 'done',
      toolCall: {
        name: 'search_materials',
        input: { query: 'python 学习计划' },
        output: [{ title: 'Python notes' }],
        latencyMs: 12,
        writeMode: 'readonly'
      }
    });

    const serialized = JSON.stringify(getAgentFlowSnapshot().nodes);
    expect(serialized).toContain('search_materials');
    expect(serialized).not.toContain('plan_context_lookup');
    expect(serialized).not.toContain('ui-mock');
  });

  it('formats memory and proposal runtime tool outputs for trace inspection', () => {
    agentFlowActions.resetFlow();

    agentFlowActions.applyRuntimeEvent({
      runId: 'runtime-2',
      sequence: 1,
      type: 'tool',
      nodeId: 'tool-memory',
      nodeType: 'tool',
      title: 'get_memory',
      status: 'done',
      toolCall: {
        name: 'get_memory',
        input: { date: '2026-07-03' },
        output: {
          preferenceMemory: { learningStyle: '项目驱动', dailyAvailableMinutes: 60 },
          historyMemory: { recentProgress: ['Built runtime trace'] }
        },
        latencyMs: 8,
        writeMode: 'readonly'
      }
    });
    agentFlowActions.applyRuntimeEvent({
      runId: 'runtime-2',
      sequence: 2,
      type: 'tool',
      nodeId: 'tool-propose',
      nodeType: 'tool',
      title: 'propose_tasks',
      status: 'done',
      toolCall: {
        name: 'propose_tasks',
        input: { goal: 'Python plan' },
        output: {
          mode: 'local_fallback',
          memoryContextSummary: '偏好：项目驱动，每天约 60 分钟。',
          structuredPlan: { goalTitle: 'Python plan' },
          tasks: [{ title: 'Build CLI' }],
          sources: [],
          fallbackReason: 'missing_api_key'
        },
        latencyMs: 20,
        writeMode: 'preview'
      }
    });

    const serialized = JSON.stringify(getAgentFlowSnapshot().nodes);
    expect(serialized).toContain('偏好记忆 / Preference Memory');
    expect(serialized).toContain('历史记忆 / History Memory');
    expect(serialized).toContain('memoryContextSummary');
    expect(serialized).toContain('structuredPlan');
    expect(getAgentFlowSnapshot().latestProposal).toBeUndefined();
  });

  it('stores the latest real runtime proposal for Dashboard calendar writing', () => {
    agentFlowActions.resetFlow();

    agentFlowActions.applyRuntimeEvent({
      runId: 'runtime-proposal-1',
      sequence: 1,
      type: 'tool',
      nodeId: 'tool-propose',
      nodeType: 'tool',
      title: 'propose_tasks',
      status: 'done',
      toolCall: {
        name: 'propose_tasks',
        input: { goal: 'Python plan' },
        output: {
          mode: 'llm',
          structuredPlan: {
            goalTitle: 'Python plan',
            goalDescription: 'Build a practical Python learning path',
            durationDays: 7,
            milestones: [
              {
                title: 'Basics',
                description: 'Learn syntax',
                tasks: [
                  {
                    title: 'Practice variables',
                    description: 'Write examples',
                    estimatedMinutes: 45,
                    dueDate: '2026-07-04',
                    priority: 'medium'
                  }
                ]
              }
            ],
            reviewPlan: { frequency: 'daily', questions: ['What worked?'] }
          },
          tasks: [{ title: 'Practice variables' }],
          sources: [],
          planHorizon: {
            rawText: 'Python plan',
            durationDays: 30,
            horizonType: 'monthly',
            startDate: '2026-07-01',
            endDate: '2026-07-31',
            expectedMilestoneCount: 4,
            expectedMinTaskCount: 12,
            expectedWeekCount: 4
          },
          qualityReport: {
            ok: true,
            score: 94,
            totalTasks: 12,
            milestoneCount: 4,
            coveredWeekCount: 4,
            dateSpanDays: 28,
            issues: [],
            metrics: {
              durationDays: 30,
              totalTasks: 12,
              milestoneCount: 4,
              coveredWeekCount: 4,
              dateSpanDays: 28,
              weakTaskCount: 0,
              missingDueDateCount: 0,
              outOfRangeDueDateCount: 0,
              repairAttempted: true,
              fallbackUsed: false,
              qualityStatus: 'repaired',
              sourceType: 'model_knowledge',
              localRelevance: 'low'
            }
          },
          qualityStatus: 'repaired',
          sourceType: 'model_knowledge',
          localRelevance: 'low'
        },
        latencyMs: 20,
        writeMode: 'preview'
      }
    });

    const proposal = getAgentFlowSnapshot().latestProposal;
    expect(proposal?.runtimeRunId).toBe('runtime-proposal-1');
    expect(proposal?.goal).toBe('Python plan');
    expect(proposal?.structuredPlan.milestones[0].tasks[0].title).toBe('Practice variables');
    expect(proposal?.planHorizon?.durationDays).toBe(30);
    expect(proposal?.qualityReport?.totalTasks).toBe(12);
    expect(proposal?.qualityReport?.metrics?.durationDays).toBe(30);
    expect(proposal?.qualityReport?.metrics?.repairAttempted).toBe(true);
    expect(proposal?.qualityReport?.metrics?.fallbackUsed).toBe(false);
    expect(proposal?.qualityReport?.metrics?.sourceType).toBe('model_knowledge');
    expect(proposal?.qualityStatus).toBe('repaired');
    expect(proposal?.sourceType).toBe('model_knowledge');
    expect(proposal?.localRelevance).toBe('low');
  });

  it('stores valid local fallback runtime proposals for Dashboard calendar writing', () => {
    agentFlowActions.resetFlow();

    agentFlowActions.applyRuntimeEvent({
      runId: 'runtime-proposal-fallback',
      sequence: 1,
      type: 'tool',
      nodeId: 'tool-propose',
      nodeType: 'tool',
      title: 'propose_tasks',
      status: 'done',
      toolCall: {
        name: 'propose_tasks',
        input: { goal: 'Fallback plan' },
        output: {
          mode: 'local_fallback',
          structuredPlan: {
            goalTitle: 'Fallback plan',
            milestones: [{ title: 'Phase', tasks: [{ title: 'Task' }] }]
          }
        },
        latencyMs: 10,
        writeMode: 'preview'
      }
    });

    const proposal = getAgentFlowSnapshot().latestProposal;
    expect(proposal?.runtimeRunId).toBe('runtime-proposal-fallback');
    expect(proposal?.mode).toBe('local_fallback');
    expect(proposal?.structuredPlan.milestones[0].tasks[0].title).toBe('Task');
  });

  it('does not store malformed runtime proposals', () => {
    agentFlowActions.resetFlow();

    agentFlowActions.applyRuntimeEvent({
      runId: 'runtime-proposal-malformed',
      sequence: 1,
      type: 'tool',
      nodeId: 'tool-propose',
      nodeType: 'tool',
      title: 'propose_tasks',
      status: 'done',
      toolCall: {
        name: 'propose_tasks',
        input: { goal: 'Malformed plan' },
        output: {
          mode: 'local_fallback',
          structuredPlan: {
            goalTitle: 'Malformed plan',
            milestones: [{ title: 'Phase', tasks: [{ title: '' }] }]
          }
        },
        latencyMs: 10,
        writeMode: 'preview'
      }
    });

    expect(getAgentFlowSnapshot().latestProposal).toBeUndefined();
  });

  it('keeps model knowledge decision metadata from search_materials tool calls', () => {
    agentFlowActions.resetFlow();

    agentFlowActions.applyRuntimeEvent({
      runId: 'runtime-model-decision',
      sequence: 1,
      type: 'tool',
      nodeId: 'tool-materials',
      nodeType: 'tool',
      title: 'search_materials',
      status: 'done',
      toolCall: {
        name: 'search_materials',
        input: {
          query: '我去新疆旅游',
          modelKnowledgeDecision: {
            shouldEnrich: true,
            triggerReason: 'keyword_mismatch',
            localSourceCount: 3,
            relevantSourceCount: 0,
            matchedKeywords: [],
            missingKeywords: ['新疆', '旅游']
          }
        },
        output: [{ title: 'Python notes' }],
        latencyMs: 18,
        writeMode: 'readonly'
      }
    });

    const node = getAgentFlowSnapshot().nodes[0];
    expect(node.toolCall?.modelKnowledgeDecision?.shouldEnrich).toBe(true);
    expect(node.toolCall?.modelKnowledgeDecision?.triggerReason).toBe('keyword_mismatch');
    expect(node.toolCall?.modelKnowledgeDecision?.missingKeywords).toEqual(['新疆', '旅游']);
  });

  it('keeps not-triggered model knowledge decisions without requiring enrichment nodes', () => {
    agentFlowActions.resetFlow();

    agentFlowActions.applyRuntimeEvent({
      runId: 'runtime-model-skip',
      sequence: 1,
      type: 'tool',
      nodeId: 'tool-materials',
      nodeType: 'tool',
      title: 'search_materials',
      status: 'done',
      toolCall: {
        name: 'search_materials',
        input: { query: '新疆旅游' },
        output: {
          modelKnowledgeDecision: {
            shouldEnrich: false,
            triggerReason: null,
            localSourceCount: 2,
            relevantSourceCount: 2,
            matchedKeywords: ['新疆', '旅游'],
            missingKeywords: []
          }
        },
        latencyMs: 13,
        writeMode: 'readonly'
      }
    });

    const snapshot = getAgentFlowSnapshot();
    expect(snapshot.nodes).toHaveLength(1);
    expect(snapshot.nodes[0].toolCall?.modelKnowledgeDecision?.shouldEnrich).toBe(false);
    expect(snapshot.nodes[0].toolCall?.modelKnowledgeDecision?.matchedKeywords).toEqual(['新疆', '旅游']);
  });

  it('keeps model knowledge call source type for degraded trace status', () => {
    agentFlowActions.resetFlow();

    agentFlowActions.applyRuntimeEvent({
      runId: 'runtime-model-called',
      sequence: 1,
      type: 'tool',
      nodeId: 'tool-model-knowledge',
      nodeType: 'tool',
      title: '大模型知识补全',
      status: 'done',
      toolCall: {
        name: 'enrich_with_model_knowledge',
        input: { triggerReason: 'insufficient_local_sources' },
        output: {
          title: '游泳入门知识',
          sourceType: 'local_knowledge_template'
        },
        latencyMs: 24,
        writeMode: 'readonly'
      }
    });

    const node = getAgentFlowSnapshot().nodes[0];
    expect(node.toolCall?.raw?.output).toMatchObject({ sourceType: 'local_knowledge_template' });
  });

  it('shows explicit fallback notice in local demo mode', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('document', { documentElement: { lang: 'zh-CN' } });

    const run = agentFlowActions.runDemoFlow('帮我规划一个 python 学习计划', { fallback: true });
    await vi.runAllTimersAsync();
    await run;

    const serialized = JSON.stringify(getAgentFlowSnapshot().nodes);
    expect(serialized).toContain('当前使用本地规划模板生成，后端 Runtime 未连接');
    expect(serialized).toContain('search_materials');
    expect(serialized).not.toContain('plan_context_lookup');
    expect(serialized).not.toContain('ui-mock');
  });
});
