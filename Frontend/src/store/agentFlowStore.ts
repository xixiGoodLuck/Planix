import { useSyncExternalStore } from 'react';
import { runAgentRuntime } from '../lib/api';
import type {
  AgentFlowNode,
  AgentRunRequest,
  AgentRuntimeEvent,
  AgentRuntimeToolCall,
  ModelKnowledgeDecision,
  PlanHorizon,
  PlanQualityStatus,
  RuntimePlanProposal,
  StructuredGoalPlan
} from '../types';

type AgentFlowState = {
  nodes: AgentFlowNode[];
  traceVisible: boolean;
  isRunning: boolean;
  runId: number;
  runtimeRunId: string;
  lastPrompt: string;
  latestProposal?: RuntimePlanProposal;
};

const listeners = new Set<() => void>();

let state: AgentFlowState = {
  nodes: [],
  traceVisible: false,
  isRunning: false,
  runId: 0,
  runtimeRunId: '',
  lastPrompt: '',
  latestProposal: undefined
};

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, ms));
}

function emit() {
  listeners.forEach((listener) => listener());
}

function updateState(updater: (current: AgentFlowState) => AgentFlowState) {
  state = updater(state);
  emit();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return state;
}

function now() {
  return Date.now();
}

function getDemoCopy() {
  const isChinese = typeof document !== 'undefined' && document.documentElement.lang === 'zh-CN';
  return isChinese
    ? {
        fallbackNotice: '当前使用本地规划模板生成，后端 Runtime 未连接。',
        planChunks: [
          '读取目标，并拆成安全的规划步骤。\n',
          '优先使用只读工具读取资料、日程和偏好记忆。\n',
          '生成任务预览，不自动写入用户日历。'
        ],
        toolPreview: '本地规划模板工具预览。',
        observation: '本地模式只展示安全工具路由，不会修改真实数据。',
        outputChunks: [
          '已生成一个本地结构化规划：先明确目标和基础水平，再安排每日学习任务、练习项目和复盘节奏。',
          '后端 Runtime 连接恢复后，这里会切换为真实 NDJSON 事件流。'
        ],
        matches: ['资料库', '今日计划', '偏好记忆', '任务预览']
      }
    : {
        fallbackNotice: 'Local planning template is active; backend Runtime is not connected.',
        planChunks: [
          'Read the goal and split it into safe planning steps.\n',
          'Use read-only tools for materials, plans, and preference memory.\n',
          'Generate task previews without writing to the calendar.'
        ],
        toolPreview: 'Local planning template tool preview.',
        observation: 'Local mode shows safe tool routing without modifying real data.',
        outputChunks: [
          'A local structured plan is ready: clarify the target and baseline, then schedule daily study, practice projects, and review checkpoints. ',
          'When backend Runtime reconnects, this panel will switch back to real NDJSON events.'
        ],
        matches: ['materials', 'today plans', 'preference memory', 'task preview']
      };
}

function createNode(partial: Omit<AgentFlowNode, 'timestamp'>): AgentFlowNode {
  return {
    ...partial,
    timestamp: now()
  };
}

function pushNode(node: AgentFlowNode) {
  updateState((current) => ({
    ...current,
    nodes: [...current.nodes, node]
  }));
}

function updateNode(id: string, patch: Partial<AgentFlowNode>) {
  updateState((current) => ({
    ...current,
    nodes: current.nodes.map((node) => (node.id === id ? { ...node, ...patch } : node))
  }));
}

function appendNodeContent(id: string, text: string) {
  updateState((current) => ({
    ...current,
    nodes: current.nodes.map((node) => {
      if (node.id !== id) return node;
      const previous = node.content;
      const currentContent = `${previous}${text}`;
      return {
        ...node,
        content: currentContent,
        diff: {
          previous,
          current: currentContent,
          changedAt: now()
        }
      };
    })
  }));
}

function setExpanded(id: string, expanded: boolean) {
  updateState((current) => ({
    ...current,
    nodes: current.nodes.map((node) => {
      if (node.id !== id || !node.toolCall) return node;
      return {
        ...node,
        toolCall: {
          ...node.toolCall,
          expanded
        }
      };
    })
  }));
}

function setTraceVisible(traceVisible: boolean) {
  updateState((current) => ({ ...current, traceVisible }));
}

function resetFlow() {
  updateState((current) => ({
    ...current,
    nodes: [],
    traceVisible: false,
    isRunning: false,
    runId: current.runId + 1,
    runtimeRunId: '',
    latestProposal: undefined
  }));
}

async function runDemoFlow(prompt: string, options: { fallback?: boolean } = {}) {
  const trimmedPrompt = prompt.trim();
  const runId = state.runId + 1;
  const copy = getDemoCopy();

  updateState((current) => ({
    ...current,
    nodes: [],
    traceVisible: true,
    isRunning: true,
    runId,
    runtimeRunId: '',
    lastPrompt: trimmedPrompt,
    latestProposal: undefined
  }));

  const isCurrentRun = () => state.runId === runId;

  const inputId = `input-${runId}`;
  pushNode(createNode({
    id: inputId,
    type: 'input',
    title: 'Input',
    content: trimmedPrompt,
    status: 'done'
  }));

  if (options.fallback) {
    pushNode(createNode({
      id: `fallback-${runId}`,
      type: 'observation',
      title: 'Local Planning Template',
      content: copy.fallbackNotice,
      status: 'done'
    }));
  }

  await delay(160);
  if (!isCurrentRun()) return;

  const planId = `plan-${runId}`;
  pushNode(createNode({
    id: planId,
    type: 'reasoning',
    title: 'Execution Plan',
    content: '',
    status: 'running'
  }));

  for (const chunk of copy.planChunks) {
    await delay(260);
    if (!isCurrentRun()) return;
    appendNodeContent(planId, chunk);
  }
  updateNode(planId, { status: 'done' });

  await delay(180);
  if (!isCurrentRun()) return;

  const toolId = `tool-${runId}`;
  pushNode(createNode({
    id: toolId,
    type: 'tool',
    title: 'search_materials',
    content: copy.toolPreview,
    status: 'running',
    toolCall: {
      name: 'search_materials',
      input: JSON.stringify({ query: trimmedPrompt.slice(0, 96), topK: 3 }, null, 2),
      output: '',
      latencyMs: 0,
      expanded: true,
      writeMode: 'readonly'
    }
  }));

  await delay(360);
  if (!isCurrentRun()) return;
  updateNode(toolId, {
    status: 'done',
    toolCall: {
      name: 'search_materials',
      input: JSON.stringify({ query: trimmedPrompt.slice(0, 96), topK: 3 }, null, 2),
      output: JSON.stringify({
        mode: 'local-template',
        matches: copy.matches
      }, null, 2),
      latencyMs: 128,
      expanded: true,
      writeMode: 'readonly'
    }
  });

  await delay(180);
  if (!isCurrentRun()) return;

  const observationId = `observation-${runId}`;
  pushNode(createNode({
    id: observationId,
    type: 'observation',
    title: 'Observation',
    content: '',
    status: 'running'
  }));
  await delay(220);
  if (!isCurrentRun()) return;
  appendNodeContent(observationId, copy.observation);
  updateNode(observationId, { status: 'done' });

  await delay(180);
  if (!isCurrentRun()) return;

  const outputId = `output-${runId}`;
  pushNode(createNode({
    id: outputId,
    type: 'output',
    title: 'Output',
    content: '',
    status: 'running'
  }));

  for (const chunk of copy.outputChunks) {
    await delay(280);
    if (!isCurrentRun()) return;
    appendNodeContent(outputId, chunk);
  }

  updateNode(outputId, { status: 'done' });
  updateState((current) => (current.runId === runId ? { ...current, isRunning: false } : current));
}

function replayFlow() {
  const prompt = state.lastPrompt || 'Planix demo run';
  return runDemoFlow(prompt);
}

function toolValueToText(value: unknown): string {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 2);
}

function runtimeToolToFlowTool(toolCall: AgentRuntimeToolCall) {
  return {
    name: toolCall.name,
    input: toolValueToText(toolCall.input),
    output: runtimeToolOutputToText(toolCall),
    latencyMs: toolCall.latencyMs ?? 0,
    expanded: true,
    writeMode: toolCall.writeMode,
    modelKnowledgeDecision: extractModelKnowledgeDecision(toolCall),
    raw: toolCall
  };
}

function runtimeToolOutputToText(toolCall: AgentRuntimeToolCall): string {
  if (toolCall.name === 'get_memory') {
    return formatMemoryToolOutput(toolCall.output);
  }
  if (toolCall.name === 'propose_tasks') {
    return formatProposalToolOutput(toolCall.output);
  }
  return toolValueToText(toolCall.output);
}

function formatMemoryToolOutput(output: unknown): string {
  if (!isRecord(output)) return toolValueToText(output);
  return [
    '偏好记忆 / Preference Memory',
    toolValueToText(output.preferenceMemory ?? {}),
    '',
    '历史记忆 / History Memory',
    toolValueToText(output.historyMemory ?? {})
  ].join('\n');
}

function formatProposalToolOutput(output: unknown): string {
  if (!isRecord(output)) return toolValueToText(output);
  const sections = [
    `mode: ${String(output.mode ?? 'local_fallback')}`,
    '',
    'memoryContextSummary',
    String(output.memoryContextSummary ?? ''),
    '',
    'structuredPlan',
    toolValueToText(output.structuredPlan ?? {}),
    '',
    'tasks preview',
    toolValueToText(output.tasks ?? []),
    '',
    'sources',
    toolValueToText(output.sources ?? [])
  ];
  if (output.fallbackReason || output.errorType || output.baseUrlHost) {
    sections.push('', 'diagnostics', toolValueToText({
      fallbackReason: output.fallbackReason,
      errorType: output.errorType,
      baseUrlHost: output.baseUrlHost
    }));
  }
  return sections.join('\n');
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function nonEmptyString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function positiveNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : undefined;
}

function normalizePlanHorizon(value: unknown): PlanHorizon | undefined {
  if (!isRecord(value)) return undefined;
  const durationDays = positiveNumber(value.durationDays);
  const expectedMilestoneCount = positiveNumber(value.expectedMilestoneCount);
  const expectedMinTaskCount = positiveNumber(value.expectedMinTaskCount);
  const expectedWeekCount = positiveNumber(value.expectedWeekCount);
  const horizonType = value.horizonType;
  if (
    durationDays === undefined
    || expectedMilestoneCount === undefined
    || expectedMinTaskCount === undefined
    || expectedWeekCount === undefined
    || !['daily', 'weekly', 'monthly', 'quarterly', 'long_term'].includes(String(horizonType))
  ) {
    return undefined;
  }
  return {
    rawText: nonEmptyString(value.rawText),
    durationDays,
    horizonType: horizonType as PlanHorizon['horizonType'],
    startDate: nonEmptyString(value.startDate),
    endDate: nonEmptyString(value.endDate),
    expectedMilestoneCount,
    expectedMinTaskCount,
    expectedWeekCount
  };
}

function normalizeQualityReport(value: unknown): RuntimePlanProposal['qualityReport'] {
  if (!isRecord(value) || typeof value.ok !== 'boolean') return undefined;
  const score = positiveNumber(value.score);
  const totalTasks = positiveNumber(value.totalTasks);
  const milestoneCount = positiveNumber(value.milestoneCount);
  const coveredWeekCount = positiveNumber(value.coveredWeekCount);
  const dateSpanDays = positiveNumber(value.dateSpanDays);
  if (
    score === undefined
    || totalTasks === undefined
    || milestoneCount === undefined
    || coveredWeekCount === undefined
    || dateSpanDays === undefined
  ) {
    return undefined;
  }
  const issues = Array.isArray(value.issues)
    ? value.issues
      .filter((issue): issue is Record<string, unknown> => isRecord(issue))
      .map((issue) => ({
        code: nonEmptyString(issue.code),
        message: nonEmptyString(issue.message),
        severity: issue.severity === 'error' ? 'error' as const : 'warning' as const
      }))
      .filter((issue) => issue.code || issue.message)
    : [];
  const rawMetrics = isRecord(value.metrics) ? value.metrics : undefined;
  const qualityStatus = ['passed', 'repaired', 'local_fallback'].includes(String(rawMetrics?.qualityStatus))
    ? rawMetrics?.qualityStatus as PlanQualityStatus
    : undefined;
  const sourceType = ['local_context', 'model_knowledge', 'local_fallback', 'insufficient_context'].includes(String(rawMetrics?.sourceType))
    ? rawMetrics?.sourceType as RuntimePlanProposal['sourceType']
    : undefined;
  const localRelevance = ['high', 'medium', 'low'].includes(String(rawMetrics?.localRelevance))
    ? rawMetrics?.localRelevance as RuntimePlanProposal['localRelevance']
    : undefined;
  const metrics = rawMetrics ? {
    durationDays: positiveNumber(rawMetrics.durationDays),
    totalTasks: positiveNumber(rawMetrics.totalTasks),
    milestoneCount: positiveNumber(rawMetrics.milestoneCount),
    coveredWeekCount: positiveNumber(rawMetrics.coveredWeekCount),
    dateSpanDays: positiveNumber(rawMetrics.dateSpanDays),
    weakTaskCount: positiveNumber(rawMetrics.weakTaskCount),
    missingDueDateCount: positiveNumber(rawMetrics.missingDueDateCount),
    outOfRangeDueDateCount: positiveNumber(rawMetrics.outOfRangeDueDateCount),
    repairAttempted: typeof rawMetrics.repairAttempted === 'boolean' ? rawMetrics.repairAttempted : undefined,
    fallbackUsed: typeof rawMetrics.fallbackUsed === 'boolean' ? rawMetrics.fallbackUsed : undefined,
    qualityStatus,
    sourceType,
    localRelevance
  } : undefined;
  return {
    ok: value.ok,
    score,
    totalTasks,
    milestoneCount,
    coveredWeekCount,
    dateSpanDays,
    issues,
    metrics
  };
}

function isValidStructuredPlan(value: unknown): value is Record<string, unknown> {
  if (!isRecord(value) || !Array.isArray(value.milestones)) return false;
  return value.milestones.some((milestone) => (
    isRecord(milestone)
    && Array.isArray(milestone.tasks)
    && milestone.tasks.some((task) => isRecord(task) && typeof task.title === 'string' && task.title.trim())
  ));
}

function normalizeStructuredPlan(value: unknown, fallbackGoal: string): StructuredGoalPlan | undefined {
  if (!isValidStructuredPlan(value)) return undefined;
  const rawMilestones = Array.isArray(value.milestones) ? value.milestones : [];
  const milestones: StructuredGoalPlan['milestones'] = rawMilestones
    .map((milestone, milestoneIndex) => {
      if (!isRecord(milestone) || !Array.isArray(milestone.tasks)) return undefined;
      const tasks: StructuredGoalPlan['milestones'][number]['tasks'] = milestone.tasks
        .filter((task): task is Record<string, unknown> => isRecord(task) && Boolean(nonEmptyString(task.title)))
        .map((task) => {
          const priority = task.priority === 'low' || task.priority === 'medium' || task.priority === 'high'
            ? task.priority
            : 'medium';
          const estimatedMinutes = typeof task.estimatedMinutes === 'number' && Number.isFinite(task.estimatedMinutes) && task.estimatedMinutes > 0
            ? task.estimatedMinutes
            : 60;
          return {
            title: nonEmptyString(task.title),
            description: nonEmptyString(task.description),
            estimatedMinutes,
            dueDate: typeof task.dueDate === 'string' && task.dueDate.trim() ? task.dueDate.trim() : null,
            priority
          };
        });
      if (!tasks.length) return undefined;
      return {
        title: nonEmptyString(milestone.title) || `Milestone ${milestoneIndex + 1}`,
        description: nonEmptyString(milestone.description),
        tasks
      };
    })
    .filter((milestone): milestone is StructuredGoalPlan['milestones'][number] => Boolean(milestone));
  if (!milestones.length) return undefined;
  const reviewPlan = isRecord(value.reviewPlan) ? value.reviewPlan : {};
  const frequency = reviewPlan.frequency === 'weekly' ? 'weekly' : 'daily';
  const questions = Array.isArray(reviewPlan.questions)
    ? reviewPlan.questions.filter((item): item is string => typeof item === 'string')
    : [];
  return {
    goalTitle: nonEmptyString(value.goalTitle) || fallbackGoal || 'Runtime proposal',
    goalDescription: nonEmptyString(value.goalDescription),
    durationDays: typeof value.durationDays === 'number' && Number.isFinite(value.durationDays) && value.durationDays > 0
      ? value.durationDays
      : milestones.length,
    milestones,
    reviewPlan: { frequency, questions }
  };
}

function extractRuntimePlanProposal(toolCall: AgentRuntimeToolCall, runtimeRunId: string): RuntimePlanProposal | undefined {
  if (toolCall.name !== 'propose_tasks' || !isRecord(toolCall.output)) return undefined;
  const input = isRecord(toolCall.input) ? toolCall.input : {};
  const goal = typeof input.goal === 'string' && input.goal.trim()
    ? input.goal.trim()
    : 'Runtime proposal';
  const structuredPlan = normalizeStructuredPlan(toolCall.output.structuredPlan, goal);
  if (!structuredPlan) return undefined;
  const mode = toolCall.output.mode === 'llm' ? 'llm' : 'local_fallback';
  const qualityStatus = ['passed', 'repaired', 'local_fallback'].includes(String(toolCall.output.qualityStatus))
    ? toolCall.output.qualityStatus as PlanQualityStatus
    : undefined;
  const sourceType = ['local_context', 'model_knowledge', 'local_fallback', 'insufficient_context'].includes(String(toolCall.output.sourceType))
    ? toolCall.output.sourceType as RuntimePlanProposal['sourceType']
    : undefined;
  const localRelevance = ['high', 'medium', 'low'].includes(String(toolCall.output.localRelevance))
    ? toolCall.output.localRelevance as RuntimePlanProposal['localRelevance']
    : undefined;
  return {
    runtimeRunId,
    goal: goal === 'Runtime proposal' ? structuredPlan.goalTitle : goal,
    structuredPlan,
    tasks: Array.isArray(toolCall.output.tasks) ? toolCall.output.tasks : [],
    sources: Array.isArray(toolCall.output.sources) ? toolCall.output.sources : [],
    mode,
    fallbackReason: typeof toolCall.output.fallbackReason === 'string' ? toolCall.output.fallbackReason : undefined,
    errorType: typeof toolCall.output.errorType === 'string' ? toolCall.output.errorType : undefined,
    baseUrlHost: typeof toolCall.output.baseUrlHost === 'string' ? toolCall.output.baseUrlHost : undefined,
    planHorizon: normalizePlanHorizon(toolCall.output.planHorizon),
    qualityReport: normalizeQualityReport(toolCall.output.qualityReport),
    qualityStatus,
    sourceType,
    localRelevance
  };
}

function extractModelKnowledgeDecision(toolCall: AgentRuntimeToolCall): ModelKnowledgeDecision | undefined {
  const candidates = [
    toolCall.modelKnowledgeDecision,
    isRecord(toolCall.output) ? toolCall.output.modelKnowledgeDecision : undefined,
    isRecord(toolCall.input) ? toolCall.input.modelKnowledgeDecision : undefined,
    isRecord(toolCall.raw?.output) ? toolCall.raw.output.modelKnowledgeDecision : undefined,
    isRecord(toolCall.raw?.input) ? toolCall.raw.input.modelKnowledgeDecision : undefined
  ];
  for (const candidate of candidates) {
    if (!isRecord(candidate) || typeof candidate.shouldEnrich !== 'boolean') continue;
    return {
      shouldEnrich: candidate.shouldEnrich,
      triggerReason: typeof candidate.triggerReason === 'string'
        ? candidate.triggerReason as ModelKnowledgeDecision['triggerReason']
        : null,
      localSourceCount: numberOrUndefined(candidate.localSourceCount),
      relevantSourceCount: numberOrUndefined(candidate.relevantSourceCount),
      matchedKeywords: stringArrayOrUndefined(candidate.matchedKeywords),
      missingKeywords: stringArrayOrUndefined(candidate.missingKeywords)
    };
  }
  return undefined;
}

function numberOrUndefined(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function stringArrayOrUndefined(value: unknown): string[] | undefined {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : undefined;
}

function ensureRuntimeRun(event: AgentRuntimeEvent): boolean {
  if (!state.runtimeRunId) {
    updateState((current) => ({ ...current, runtimeRunId: event.runId }));
    return true;
  }
  return state.runtimeRunId === event.runId;
}

function upsertRuntimeNode(event: AgentRuntimeEvent) {
  const nodeId = event.nodeId || `${event.nodeType || 'output'}-${event.sequence}`;
  const nodeType = event.nodeType || 'output';
  updateState((current) => {
    const existing = current.nodes.find((node) => node.id === nodeId);
    const nextNode: AgentFlowNode = {
      id: nodeId,
      type: nodeType,
      title: event.title || nodeType,
      content: event.content || '',
      status: event.status || 'pending',
      timestamp: now()
    };
    return {
      ...current,
      nodes: existing
        ? current.nodes.map((node) => (
            node.id === nodeId
              ? {
                  ...node,
                  type: nodeType,
                  title: event.title || node.title,
                  content: event.content ?? node.content,
                  status: event.status || node.status
                }
              : node
          ))
        : [...current.nodes, nextNode]
    };
  });
}

function applyRuntimeEvent(event: AgentRuntimeEvent) {
  if (!ensureRuntimeRun(event)) return;
  if (event.type === 'node') {
    upsertRuntimeNode(event);
    return;
  }
  if (event.type === 'delta' && event.nodeId && event.delta) {
    appendNodeContent(event.nodeId, event.delta);
    return;
  }
  if (event.type === 'tool' && event.nodeId && event.toolCall) {
    upsertRuntimeNode({ ...event, type: 'node', nodeType: 'tool', status: event.status || 'done' });
    updateNode(event.nodeId, {
      status: event.status || 'done',
      toolCall: runtimeToolToFlowTool(event.toolCall)
    });
    const proposal = extractRuntimePlanProposal(event.toolCall, event.runId);
    updateState((current) => ({
      ...current,
      latestProposal: proposal ?? (event.toolCall?.name === 'propose_tasks' ? undefined : current.latestProposal)
    }));
    return;
  }
  if (event.type === 'status' && event.nodeId && event.status) {
    updateNode(event.nodeId, { status: event.status });
    return;
  }
  if (event.type === 'final') {
    updateState((current) => ({ ...current, isRunning: false }));
    if (event.nodeId && event.content) {
      updateNode(event.nodeId, { status: 'done' });
    }
    return;
  }
  if (event.type === 'error') {
    updateState((current) => ({ ...current, isRunning: false }));
    const nodeId = event.nodeId || `error-${event.sequence}`;
    upsertRuntimeNode({
      ...event,
      type: 'node',
      nodeId,
      nodeType: 'output',
      title: 'Error',
      content: event.error || 'Runtime error',
      status: 'error'
    });
  }
}

async function runRuntimeFlow(
  payload: AgentRunRequest,
  callbacks: {
    onFinal?: (content: string) => void;
    onDone?: () => void;
    onError?: (error: Error) => void;
  } = {}
) {
  const runId = state.runId + 1;
  updateState((current) => ({
    ...current,
    nodes: [],
    traceVisible: true,
    isRunning: true,
    runId,
    runtimeRunId: '',
    lastPrompt: payload.input,
    latestProposal: undefined
  }));

  try {
    await runAgentRuntime(payload, {
      onEvent: (event) => {
        if (state.runId !== runId) return;
        applyRuntimeEvent(event);
        if (event.type === 'final' && event.content) {
          callbacks.onFinal?.(event.content);
        }
      },
      onDone: () => {
        if (state.runId === runId) callbacks.onDone?.();
      }
    });
  } catch (err) {
    if (state.runId !== runId) return;
    const error = err instanceof Error ? err : new Error(String(err));
    callbacks.onError?.(error);
    await runDemoFlow(payload.input, { fallback: true });
    callbacks.onFinal?.(getDemoCopy().fallbackNotice);
    callbacks.onDone?.();
  } finally {
    updateState((current) => ({ ...current, isRunning: false }));
  }
}

export function useAgentFlow() {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export function getAgentFlowSnapshot() {
  return getSnapshot();
}

export const agentFlowActions = {
  resetFlow,
  pushNode,
  updateNode,
  appendNodeContent,
  setExpanded,
  setTraceVisible,
  applyRuntimeEvent,
  runRuntimeFlow,
  runDemoFlow,
  replayFlow
};
