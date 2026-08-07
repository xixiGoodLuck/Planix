export type Language = 'zh-CN' | 'en-US';
export type Lang = Language;

export type AppRoute = 'dashboard' | 'calendar' | 'notes' | 'goals' | 'settings' | 'command';

export type AgentFlowNodeType = 'input' | 'reasoning' | 'tool' | 'observation' | 'output';
export type AgentFlowStatus = 'pending' | 'running' | 'done' | 'error';

export type ModelKnowledgeTriggerReason =
  | 'forced_by_user'
  | 'insufficient_local_sources'
  | 'keyword_mismatch'
  | 'low_local_relevance';

export interface ModelKnowledgeDecision {
  shouldEnrich: boolean;
  triggerReason?: ModelKnowledgeTriggerReason | null;
  localSourceCount?: number;
  relevantSourceCount?: number;
  matchedKeywords?: string[];
  missingKeywords?: string[];
}

export interface AgentToolCall {
  name: string;
  input: string;
  output: string;
  latencyMs: number;
  expanded: boolean;
  writeMode?: 'readonly' | 'preview';
  modelKnowledgeDecision?: ModelKnowledgeDecision;
  raw?: AgentRuntimeToolCall;
}

export interface AgentFlowDiff {
  previous: string;
  current: string;
  changedAt: number;
}

export interface AgentFlowNode {
  id: string;
  type: AgentFlowNodeType;
  title: string;
  content: string;
  status: AgentFlowStatus;
  timestamp: number;
  toolCall?: AgentToolCall;
  diff?: AgentFlowDiff;
}

export type PlanHorizonType = 'daily' | 'weekly' | 'monthly' | 'quarterly' | 'long_term';
export type PlanQualityStatus = 'passed' | 'repaired' | 'local_fallback';
export type PlanSourceType = 'local_context' | 'model_knowledge' | 'local_fallback' | 'insufficient_context';
export type LocalRelevance = 'high' | 'medium' | 'low';

export interface PlanHorizon {
  rawText: string;
  durationDays: number;
  horizonType: PlanHorizonType;
  startDate: string;
  endDate: string;
  expectedMilestoneCount: number;
  expectedMinTaskCount: number;
  expectedWeekCount: number;
}

export interface PlanQualityIssue {
  code: string;
  message: string;
  severity: 'warning' | 'error';
}

export interface PlanQualityMetrics {
  durationDays?: number;
  totalTasks?: number;
  milestoneCount?: number;
  coveredWeekCount?: number;
  dateSpanDays?: number;
  weakTaskCount?: number;
  missingDueDateCount?: number;
  outOfRangeDueDateCount?: number;
  repairAttempted?: boolean;
  fallbackUsed?: boolean;
  qualityStatus?: PlanQualityStatus;
  sourceType?: PlanSourceType;
  localRelevance?: LocalRelevance;
}

export interface PlanQualityReport {
  ok: boolean;
  score: number;
  totalTasks: number;
  milestoneCount: number;
  coveredWeekCount: number;
  dateSpanDays: number;
  issues: PlanQualityIssue[];
  metrics?: PlanQualityMetrics;
}

export interface RuntimePlanProposal {
  runtimeRunId: string;
  goal: string;
  structuredPlan: StructuredGoalPlan;
  tasks: unknown[];
  sources: unknown[];
  mode: 'llm' | 'local_fallback';
  fallbackReason?: string;
  errorType?: string;
  baseUrlHost?: string;
  planHorizon?: PlanHorizon;
  qualityReport?: PlanQualityReport;
  qualityStatus?: PlanQualityStatus;
  sourceType?: PlanSourceType;
  localRelevance?: LocalRelevance;
}

export interface CalendarWriteSummary {
  created: number;
  updated: number;
  failed: number;
  affectedDates: string[];
  errors: string[];
}

export type CommandMode = 'auto' | 'chat' | 'workbench';
export type CommandPermission = 'low' | 'medium' | 'high';
export type PWorkspaceDraftKind = 'calendar_plan';
export type PWorkspaceDraftStatus = 'current' | 'superseded' | 'written' | 'dismissed';

export interface PWorkspaceDraft {
  id: string;
  threadId: string;
  kind: PWorkspaceDraftKind;
  version: number;
  status: PWorkspaceDraftStatus;
  title: string;
  summary: string;
  payload: Record<string, unknown>;
  sourceRunId?: string;
  createdAt: string;
  updatedAt: string;
}

export interface CommandMessage {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'card';
  content: string;
  kind?: string;
  payload?: Record<string, unknown>;
  createdAt: string;
}

export interface CommandThread {
  id: string;
  title: string;
  messages: CommandMessage[];
  currentDraft?: PWorkspaceDraft | null;
  createdAt: string;
  updatedAt: string;
}

export interface CommandThreadSummary {
  id: string;
  title: string;
  messageCount: number;
  currentDraftTitle?: string;
  createdAt: string;
  updatedAt: string;
}

export interface AgentRunRequest {
  input: string;
  date: string;
  preferences?: string | Record<string, unknown>;
  materials?: string;
  data?: Record<string, unknown>;
  options?: {
    forceModelKnowledge?: boolean;
  };
}

export interface AgentRuntimeToolCall {
  name: string;
  input: unknown;
  output?: unknown;
  latencyMs?: number;
  writeMode: 'readonly' | 'preview';
  modelKnowledgeDecision?: ModelKnowledgeDecision;
  raw?: {
    input?: unknown;
    output?: unknown;
  };
}

export interface AgentRuntimeEvent {
  runId: string;
  sequence: number;
  type: 'node' | 'delta' | 'tool' | 'status' | 'final' | 'error';
  nodeId?: string;
  nodeType?: AgentFlowNodeType;
  status?: AgentFlowStatus;
  title?: string;
  content?: string;
  delta?: string;
  toolCall?: AgentRuntimeToolCall;
  error?: string;
}

export interface InspectorLog {
  id: string;
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
  timestamp: number;
}

export interface InspectorSnapshot {
  route: AppRoute;
  agentStatus: 'idle' | 'running' | 'done' | 'error';
  logs: InspectorLog[];
  memory: {
    preferenceSummary: string;
    materialCount: number;
    planCount: number;
  };
  api: {
    mode: 'local' | 'backend' | 'unknown';
    hasApiKey: boolean;
    provider: string;
  };
}

export interface Plan {
  id: string;
  time: string;
  title: string;
  done: boolean;
  completion: string;
  priority?: GoalPriority;
  estimatedMinutes?: number;
  source?: 'manual' | 'ai';
  sourceKey?: string;
  refinedTask?: RefinedTask | null;
  refinedTaskUpdatedAt?: string | null;
}

export interface AppliedPlan extends Plan {
  date: string;
}

export interface DayRecord {
  plans: Plan[];
}

export type AppData = Record<string, DayRecord>;

export interface PlannerTask {
  time: string;
  title: string;
  reason: string;
}

export type GoalPriority = 'low' | 'medium' | 'high';

export interface GoalPlanTask {
  title: string;
  description: string;
  estimatedMinutes: number;
  dueDate: string | null;
  priority: GoalPriority;
  learningResources?: TaskLearningResource[];
  resourceBundle?: TaskResourceBundle | null;
}

export interface GoalMilestone {
  title: string;
  description: string;
  tasks: GoalPlanTask[];
}

export interface ReviewPlan {
  frequency: 'daily' | 'weekly';
  questions: string[];
}

export interface StructuredGoalPlan {
  goalTitle: string;
  goalDescription: string;
  durationDays: number;
  milestones: GoalMilestone[];
  reviewPlan: ReviewPlan;
}

export interface PhaseItem {
  title: string;
  detail: string;
}

export interface ReplanTask extends PlannerTask {
  targetDate: string;
  sourcePlanId?: string;
}

export interface RagSource {
  documentId: string;
  title: string;
  chunk: string;
  score: number;
  chunkIndex: number;
}

export interface RagDocument {
  id: string;
  title: string;
  sourceType: string;
  summary: string;
  chunks: number;
  createdAt: string;
}

export interface RagDocumentInput {
  title: string;
  content: string;
  sourceType?: string;
}

export type MemoryKind = 'note' | 'material' | 'planning_history' | 'preference' | 'review';
export type MemorySource = 'user' | 'ai' | 'system';

export interface MemoryItem {
  id: string;
  kind: MemoryKind;
  title: string;
  content: string;
  summary: string;
  tags: string[];
  source: MemorySource;
  sourceId?: string;
  sourceKey?: string;
  metadata?: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface MemoryInput {
  kind: MemoryKind;
  title?: string;
  content: string;
  summary?: string;
  tags?: string[];
  source?: MemorySource;
  sourceId?: string;
  sourceKey?: string;
  metadata?: Record<string, unknown>;
}

export interface MemorySearchGroup {
  kind: MemoryKind;
  title: string;
  items: MemoryItem[];
}

export interface MemorySearchResult {
  query: string;
  summary: string;
  groups: MemorySearchGroup[];
  results: MemoryItem[];
}

export interface AiMaterialDraftRequest {
  query: string;
  outputLanguage?: 'zh' | 'en';
}

export interface AiMaterialDraft {
  title: string;
  content: string;
  summary: string;
  sourceType: 'model_knowledge' | 'local_knowledge_template';
  caveat?: string;
}

export interface GoalPlanResponse {
  id: string;
  mode: 'mock' | 'llm';
  summary: string;
  phases: PhaseItem[];
  tasks: PlannerTask[];
  sources?: RagSource[];
  structuredPlan?: StructuredGoalPlan;
  provider?: string;
  model?: string;
  fallbackReason?: 'llm_error' | 'mock_provider' | 'missing_api_key';
  errorType?: string;
  errorMessage?: string;
  baseUrlHost?: string;
  planHorizon?: PlanHorizon;
  qualityReport?: PlanQualityReport;
  qualityStatus?: PlanQualityStatus;
  sourceType?: PlanSourceType;
  localRelevance?: LocalRelevance;
}

export interface RefineTaskRequest {
  goal: string;
  taskTitle: string;
  taskDescription?: string;
  date?: string;
  availableMinutes?: number;
  planContext?: RefinePlanContext;
  sourceKey?: string;
  planId?: string;
  userConstraints?: string[];
  retrievedSources?: RagSource[];
  outputLanguage?: 'zh' | 'en';
  refinementInstruction?: string;
}

export interface RefinePlanContext {
  planTitle?: string;
  planSummary?: string;
  durationDays?: number;
  qualityStatus?: string;
  dailyLearningMinutes?: number;
  currentMilestone?: Record<string, unknown>;
  currentTask?: Record<string, unknown>;
  previousTask?: Record<string, unknown> | null;
  nextTask?: Record<string, unknown> | null;
  sameMilestoneTasks?: string[];
  sources?: Array<Record<string, unknown>>;
}

export interface TimeBlock {
  title: string;
  durationMinutes: number;
  action: string;
  expectedOutput?: string | null;
}

export interface LearningResource {
  title: string;
  type: 'official_doc' | 'library_doc' | 'search_keyword' | 'local_source';
  url?: string | null;
  searchKeyword?: string | null;
  reason?: string | null;
}

export type PlanningResourceSourceType =
  | 'user_material'
  | 'memory_note'
  | 'official_doc'
  | 'built_in_catalog'
  | 'project_template'
  | 'practice_bank'
  | 'web_search'
  | 'github'
  | 'video'
  | 'book'
  | 'ai_generated'
  | 'search_keyword';

export interface TaskLearningResource {
  title: string;
  sourceType: PlanningResourceSourceType;
  url?: string | null;
  section?: string | null;
  searchKeyword?: string | null;
  useStep: string;
  estimatedMinutes: number;
  whyThisResource: string;
  expectedOutput: string;
  fallbackIfTooHard: string;
}

export interface TaskResourceBundle {
  primary?: TaskLearningResource | null;
  support?: TaskLearningResource | null;
  practice?: TaskLearningResource | null;
  fallback?: TaskLearningResource | null;
}

export type ExecutionPlanQualityStatus = 'passed' | 'needs_repair' | 'needs_user_confirmation' | 'blocked';

export interface ExecutionPlanQualityChecks {
  goalAlignment: boolean;
  timeFit: boolean;
  taskSpecificity: boolean;
  resourceDiversity: boolean;
  deliverableQuality: boolean;
  internshipFit?: boolean | null;
  calendarWritable: boolean;
}

export interface ExecutionPlanQualityReport {
  status: ExecutionPlanQualityStatus;
  score: number;
  blockers: string[];
  warnings: string[];
  repairSuggestions: string[];
  checks: ExecutionPlanQualityChecks;
}

export interface PlanFitCheck {
  fitsCurrentMilestone: boolean;
  advancesOverallGoal: boolean;
  hasCheckableOutput: boolean;
  note: string;
}

export interface RefinedTask {
  title: string;
  objective: string;
  estimatedMinutes: number;
  steps: string[];
  checklist: string[];
  acceptanceCriteria: string[];
  deliverable: string;
  risks: string[];
  fallbackTips: string[];
  mode: 'llm' | 'local_fallback';
  fallbackReason?: string;
  errorType?: string;
  timeBlocks?: TimeBlock[];
  learningResources?: LearningResource[];
  budgetExplanation?: string | null;
  planFitCheck?: PlanFitCheck | null;
}

export interface DailyReviewResponse {
  id: string;
  mode: 'mock' | 'llm' | 'saved';
  date: string;
  summary: string;
  suggestions: string[];
  doneCount: number;
  totalCount: number;
  targetDate: string;
  replanTasks: ReplanTask[];
  provider?: string;
  model?: string;
  updatedAt?: string;
}

export interface ReplanApplyPayload {
  tasks: ReplanTask[];
}

export interface PlannerResponse {
  mode?: 'api' | 'mock' | 'llm';
  summary?: string;
  phases?: PhaseItem[];
  tasks?: PlannerTask[];
  suggestions?: string[];
  answer?: string;
  sources?: RagSource[];
  keywords?: string[];
  score?: number;
  provider?: string;
  model?: string;
  criteria?: string[];
  results?: Array<{ case: string; score: number; reason: string }>;
}

export type AiProvider = 'mock' | 'deepseek' | 'kimi' | 'zhipu_glm' | 'openai' | 'custom' | 'local';
export type RoutingPrimaryProvider = 'auto' | Exclude<AiProvider, 'mock'>;
export type AutoModelStrategy =
  | 'fast_low_cost'
  | 'structured_stable'
  | 'strict_json'
  | 'context_summary'
  | 'classification'
  | 'knowledge_reasoning'
  | 'balanced';
export type ModelRoutingTaskType =
  | 'goal_understanding'
  | 'command_decision'
  | 'plan_generation'
  | 'task_refinement'
  | 'calendar_patch'
  | 'memory_query'
  | 'memory_write'
  | 'note_query'
  | 'note_write'
  | 'chat'
  | 'model_knowledge'
  | 'planning_understanding'
  | 'planning_plan'
  | 'planning_review'
  | 'planning_learning';

export interface ModelRouteAttempt {
  provider: string;
  model?: string;
  status: 'success' | 'error' | 'skipped';
  errorType?: string;
  latencyMs?: number;
}

export interface AiModelRoutingRule {
  taskType: ModelRoutingTaskType;
  primaryProvider: RoutingPrimaryProvider;
  fallbackProviders: AiProvider[];
  localFallbackEnabled: boolean;
  updatedAt?: string;
}

export interface AiAutoModelPolicy {
  autoProviderOrder: Exclude<AiProvider, 'mock'>[];
  taskStrategy: Partial<Record<ModelRoutingTaskType, AutoModelStrategy>>;
}

export interface AiSavedProvider {
  provider: AiProvider;
  baseUrl: string;
  model: string;
  hasApiKey: boolean;
  keyStatus?: 'unchecked' | 'valid' | 'invalid';
  keyErrorType?: string;
  lastValidatedAt?: string;
  updatedAt: string;
}

export interface AiSettings {
  provider: AiProvider;
  baseUrl: string;
  model: string;
  hasApiKey: boolean;
  keyStatus?: 'unchecked' | 'valid' | 'invalid';
  keyErrorType?: string;
  temperature: number;
  timeoutSeconds: number;
  updatedAt: string;
  savedProviders: AiSavedProvider[];
  routingRules?: AiModelRoutingRule[];
  autoModelPolicy?: AiAutoModelPolicy;
}

export interface AiSettingsInput {
  provider: AiSettings['provider'];
  baseUrl: string;
  model: string;
  apiKey?: string;
  temperature: number;
  timeoutSeconds: number;
}

export interface AiModelRoutingInput {
  routingRules: AiModelRoutingRule[];
  autoModelPolicy?: AiAutoModelPolicy;
}

export interface AiSettingsTestResult {
  ok: boolean;
  mode: 'mock' | 'llm' | 'error';
  message: string;
  provider?: string;
  model?: string;
  errorType?: string;
  statusCode?: number;
  detail?: string;
}

export interface MemoryCacheStats {
  preferenceMemory: number;
  historySummaries: number;
  agentRuns: number;
  agentEvents: number;
  planningGoals: number;
  plans: number;
}

export interface MemoryResetResult {
  ok: boolean;
  before: MemoryCacheStats;
  after: MemoryCacheStats;
  deleted: Record<string, number>;
  steps?: Record<string, Record<string, number>>;
  preserved: {
    plans: boolean;
    goals: boolean;
    calendar: boolean;
    notes: boolean;
    documents: boolean;
    aiSettings: boolean;
  };
  message: string;
}
