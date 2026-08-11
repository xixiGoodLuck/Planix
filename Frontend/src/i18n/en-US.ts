import type { I18nNamespace } from './index';

export const enUS: I18nNamespace = {
  common: { appName: 'Planix Learning', unknown: 'Unknown' },
  shell: {
    menu: 'Menu', learning: 'Learning', settings: 'Settings', language: 'Language',
    languageZh: 'Chinese', languageEn: 'English', productTagline: 'Technical learning content agent', navigation: 'Navigation'
  },
  learning: {
    productName: 'Planix Learning', title: 'Technical learning content planner', subtitle: 'Turn a goal into a knowledge route, verified video segments, and a quality report.',
    newRun: 'New learning goal', describeGoal: 'What do you want to master?', goal: 'Learning goal', goalPlaceholder: 'For example: learn FastAPI in 30 days and build a CRUD API',
    targetResult: 'Target result', targetResultPlaceholder: 'For example: independently build a working project', currentLevel: 'Current level', currentLevelPlaceholder: 'For example: comfortable with Python basics',
    contentBudget: 'Content budget (minutes)', contentBudgetPlaceholder: 'For example: 180', constraints: 'Constraints', constraintsPlaceholder: 'Comma separated, for example: Chinese content only',
    generatePlan: 'Generate learning content plan', generating: 'Generating…', goalUnderstanding: 'Goal understanding', notSpecified: 'Not specified', minutes: 'minutes',
    agentProcess: 'Live agent process', progressTitle: 'Content planning progress', status_idle: 'Ready', status_creating: 'Creating', status_created: 'Created', status_running: 'Running', status_completed: 'Completed', status_failed: 'Failed',
    stage_understanding: 'Understand goal', stage_understanding_description: 'Confirm the goal, current level, outcome, and content constraints', stage_knowledge: 'Analyze knowledge', stage_knowledge_description: 'Generate outcomes, capabilities, and the knowledge route',
    stage_evidence: 'Analyze resources', stage_evidence_description: 'Verify videos, transcripts, and knowledge coverage', stage_selection: 'Select content', stage_selection_description: 'Choose necessary segments within the viewing budget',
    stage_quality: 'Validate quality', stage_quality_description: 'Check coverage, evidence, versions, and timestamps', liveEvents: 'Live events', event_session_created: 'Learning run created',
    event_stage_started: 'Stage started', event_artifact_saved: 'Validated result saved', event_stage_completed: 'Stage completed', event_session_completed: 'Learning plan completed', event_session_failed: 'Learning run failed',
    finalPlan: 'Final learning content plan', knowledgeRoute: 'Knowledge route', resultDescription: 'Every knowledge item is tied to verified content and a recommendation rationale.', totalDuration: 'Total viewing time',
    whyNeeded: 'Why it is needed', watchRange: 'Recommended range', watchDuration: 'Viewing time', evidenceLevel: 'Evidence level', recommendationReason: 'Why recommended', evidenceMissing: 'There is not enough verified content evidence yet.',
    qualityValidation: 'Quality validation', qualityPassed: 'Quality checks passed', qualityFailed: 'Quality checks failed', qualityIssues: 'Issues to resolve',
    quality_knowledge_coverage: 'Knowledge coverage', quality_evidence_validity: 'Evidence validity', quality_version_compatibility: 'Version compatibility', quality_content_redundancy: 'Content redundancy', quality_unsupported_timestamp: 'Timestamp validity',
    failure_backend_unavailable_title: 'Cannot reach the Planix backend', failure_backend_unavailable_description: 'Start PostgreSQL 17 and Backend 8003, then try again.',
    failure_provider_unavailable_title: 'Model or evidence provider unavailable', failure_provider_unavailable_description: 'The service cannot complete learning analysis. Check model and provider status in Settings.',
    failure_evidence_missing_title: 'Verified video evidence is missing', failure_evidence_missing_description: 'There is not enough transcript evidence, so Planix will not invent video timestamps.',
    failure_quality_failed_title: 'Quality validation failed', failure_quality_failed_description: 'The content does not meet coverage or evidence requirements and will not be marked complete.',
    failure_run_failed_title: 'Learning content planning did not complete', failure_run_failed_description: 'The run stopped safely. Check service status and start again.', startAgain: 'Start again'
  },
  settings: {
    title: 'Settings', subtitle: 'Configure the models and production components used by Planix Learning.', aiSettings: 'Model provider', backendOffline: 'Backend offline', backendOfflineHint: 'Start PostgreSQL 17 and the Planix Backend first.', retryBackend: 'Retry',
    provider: 'Provider', provider_deepseek: 'DeepSeek', provider_kimi: 'Kimi', provider_zhipu_glm: 'Zhipu GLM', provider_openai: 'OpenAI', provider_custom: 'Custom', provider_local: 'Local model', provider_mock: 'Mock',
    baseUrl: 'Base URL', model: 'Model', modelId: 'Model ID', recommendedModel: 'Recommended model', apiKey: 'API Key', apiKeyOptional: 'API Key (optional)', apiKeyPlaceholder: 'Paste a new API Key', apiKeyOptionalPlaceholder: 'Leave blank if the local service does not require one',
    temperature: 'Temperature', timeout: 'Timeout seconds', forceNonThinking: 'Force non-thinking', forceNonThinkingEnabled: 'Every compatible real model call is forced into non-thinking mode.', forceNonThinkingDisabled: 'Use each provider and model default thinking behavior.',
    saveSettings: 'Save settings', testModel: 'Test model', clearConfig: 'Clear config', clearKey: 'Clear key', settingsSaved: 'Settings saved', modelTestSuccess: 'Model test passed', modelTestFailed: 'Model test failed', configCleared: 'Local config cleared', keyCleared: 'API Key cleared',
    savedProviders: 'Saved providers', noSavedProviders: 'No provider configuration saved', localConfigured: 'Local model configured',
    learningRouting: 'Learning model routing', learningRoutingHint: 'Only real Learning semantic calls are configurable', learningSemantic: 'Learning semantic generation', learningSemanticDesc: 'Knowledge, evidence, and content semantic analysis share this route',
    routingTask: 'Task', routingPrimary: 'Primary', routingFallbackOne: 'Fallback 1', routingFallbackTwo: 'Fallback 2', routingAuto: 'Auto', routingNone: 'No fallback', saveRouting: 'Save routing', routingSaved: 'Routing saved',
    learningRuntimeHealth: 'Learning Runtime', modelProviderHealth: 'Model Provider', videoProviderHealth: 'Video Provider', transcriptProviderHealth: 'Transcript Provider', artifactStoreHealth: 'Artifact Store'
  }
};
