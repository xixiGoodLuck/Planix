export type Language = 'zh-CN' | 'en-US';
export type Lang = Language;
export type AppRoute = 'calendar' | 'settings' | 'command';
export type CommandPermission = 'low' | 'medium' | 'high';

export interface CommandMessage {
  id: string; role: 'user' | 'assistant' | 'system' | 'card'; content: string; kind?: string;
  payload?: Record<string, unknown>; createdAt: string;
}
export interface CommandThread { id: string; title: string; messages: CommandMessage[]; createdAt: string; updatedAt: string; }
export interface CommandThreadSummary { id: string; title: string; messageCount: number; createdAt: string; updatedAt: string; }

export type GoalPriority = 'low' | 'medium' | 'high';
export interface Plan {
  id: string; time: string; title: string; done: boolean; completion: string; priority?: GoalPriority;
  estimatedMinutes?: number; source?: 'manual' | 'ai'; sourceKey?: string;
}
export interface AppliedPlan extends Plan { date: string; }
export interface DayRecord { plans: Plan[]; }
export type AppData = Record<string, DayRecord>;

export interface InspectorLog { id: string; level: 'info' | 'success' | 'warning' | 'error'; message: string; timestamp: number; }
export interface InspectorSnapshot {
  route: AppRoute; agentStatus: 'idle' | 'running' | 'done' | 'error'; logs: InspectorLog[];
  planning: { planCount: number; };
  api: { mode: 'local' | 'backend' | 'unknown'; hasApiKey: boolean; provider: string; };
}

export type AiProvider = 'mock' | 'deepseek' | 'kimi' | 'zhipu_glm' | 'openai' | 'custom' | 'local';
export type RoutingPrimaryProvider = 'auto' | Exclude<AiProvider, 'mock'>;
export type AutoModelStrategy = 'fast_low_cost' | 'structured_stable' | 'strict_json' | 'context_summary' | 'classification' | 'knowledge_reasoning' | 'balanced';
export type ModelRoutingTaskType = 'planning_understanding' | 'planning_plan' | 'planning_review' | 'planning_learning';
export interface ModelRouteAttempt { provider: string; model?: string; status: 'success' | 'error' | 'skipped'; errorType?: string; latencyMs?: number; }
export interface AiModelRoutingRule { taskType: ModelRoutingTaskType; primaryProvider: RoutingPrimaryProvider; fallbackProviders: AiProvider[]; localFallbackEnabled: boolean; updatedAt?: string; }
export interface AiAutoModelPolicy { autoProviderOrder: Exclude<AiProvider, 'mock'>[]; taskStrategy: Partial<Record<ModelRoutingTaskType, AutoModelStrategy>>; }
export interface AiSavedProvider { provider: AiProvider; baseUrl: string; model: string; hasApiKey: boolean; keyStatus?: 'unchecked' | 'valid' | 'invalid'; keyErrorType?: string; lastValidatedAt?: string; updatedAt: string; }
export interface AiSettings {
  provider: AiProvider; baseUrl: string; model: string; hasApiKey: boolean; keyStatus?: 'unchecked' | 'valid' | 'invalid'; keyErrorType?: string;
  temperature: number; timeoutSeconds: number; forceNonThinking: boolean; updatedAt: string; savedProviders: AiSavedProvider[];
  routingRules?: AiModelRoutingRule[]; autoModelPolicy?: AiAutoModelPolicy;
}
export interface AiSettingsInput { provider: AiProvider; baseUrl: string; model: string; apiKey?: string; temperature: number; timeoutSeconds: number; forceNonThinking: boolean; }
export interface AiModelRoutingInput { routingRules: AiModelRoutingRule[]; autoModelPolicy?: AiAutoModelPolicy; }
export interface AiSettingsTestResult { ok: boolean; mode: 'mock' | 'llm' | 'error'; message: string; provider?: string; model?: string; errorType?: string; statusCode?: number; detail?: string; }
export interface ContextStats { conversations: number; planningSessions: number; artifacts: number; memories: number; }
export interface ContextResetResult { deletedThreads: number; deletedSessions: number; deletedArtifacts: number; deletedEvents: number; deletedMemories: number; }
