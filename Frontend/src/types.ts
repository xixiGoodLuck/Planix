export type Language = 'zh-CN' | 'en-US';
export type AppRoute = 'learning' | 'settings';

export type AiProvider = 'mock' | 'deepseek' | 'kimi' | 'zhipu_glm' | 'openai' | 'custom' | 'local';
export type RoutingPrimaryProvider = 'auto' | Exclude<AiProvider, 'mock'>;
export type AutoModelStrategy = 'fast_low_cost' | 'structured_stable' | 'strict_json' | 'context_summary' | 'classification' | 'knowledge_reasoning' | 'balanced';
export type ModelRoutingTaskType = 'learning_semantic';

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
  forceNonThinking: boolean;
  updatedAt: string;
  savedProviders: AiSavedProvider[];
  routingRules?: AiModelRoutingRule[];
  autoModelPolicy?: AiAutoModelPolicy;
}
export interface AiSettingsInput {
  provider: AiProvider;
  baseUrl: string;
  model: string;
  apiKey?: string;
  temperature: number;
  timeoutSeconds: number;
  forceNonThinking: boolean;
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

export interface BackendHealth {
  status: string;
  name: string;
  app: string;
  pid: number;
  version: string;
  startupTime: string;
  features: Record<string, boolean>;
  database: string;
}

export interface LearningRuntimeHealth {
  status: 'ready' | 'unavailable';
  startup_status?: unknown;
  environment?: string;
  providers?: Record<string, { status?: string; name?: string; error_type?: string }>;
  artifact_store?: { status?: string; name?: string; error_type?: string };
  transcript_source_status?: { status?: string; source_type?: string; error_type?: string };
  error?: { component?: string; message?: string } | null;
}
