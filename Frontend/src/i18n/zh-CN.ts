import type { I18nNamespace } from './index';

export const zhCN: I18nNamespace = {
  common: { appName: 'Planix Learning', unknown: '未知' },
  shell: {
    menu: '菜单', learning: '学习规划', settings: '设置', language: '语言',
    languageZh: '中文', languageEn: 'English', productTagline: '技术学习内容规划 Agent', navigation: '导航'
  },
  learning: {
    productName: 'Planix Learning', title: '技术学习内容规划', subtitle: '从目标到知识路线，再到有证据的视频片段与质量验证。',
    newRun: '新的学习目标', describeGoal: '你想掌握什么？', goal: '学习目标', goalPlaceholder: '例如：30 天学习 FastAPI，并完成一个 CRUD API',
    targetResult: '期望成果', targetResultPlaceholder: '例如：能够独立完成可运行的项目', currentLevel: '当前水平', currentLevelPlaceholder: '例如：会 Python 基础',
    contentBudget: '内容预算（分钟）', contentBudgetPlaceholder: '例如：180', constraints: '约束', constraintsPlaceholder: '用逗号分隔，例如：只看中文内容',
    generatePlan: '生成学习内容计划', generating: '正在生成…', goalUnderstanding: '目标理解', notSpecified: '未指定', minutes: '分钟',
    agentProcess: 'Agent 实时过程', progressTitle: '内容规划进度', status_idle: '待开始', status_creating: '正在创建', status_created: '已创建', status_running: '运行中', status_completed: '已完成', status_failed: '失败',
    stage_understanding: '理解目标', stage_understanding_description: '确认目标、基础、成果和内容约束', stage_knowledge: '知识分析', stage_knowledge_description: '生成学习成果、能力与知识路线',
    stage_evidence: '资源分析', stage_evidence_description: '核验视频、字幕与知识覆盖证据', stage_selection: '内容选择', stage_selection_description: '选择必要片段并控制总观看时长',
    stage_quality: '质量验证', stage_quality_description: '检查覆盖、证据、版本与时间戳', liveEvents: '实时事件', event_session_created: '学习运行已创建',
    event_stage_started: '阶段开始', event_artifact_saved: '验证结果已保存', event_stage_completed: '阶段完成', event_session_completed: '学习计划已完成', event_session_failed: '学习运行失败',
    finalPlan: '最终学习内容计划', knowledgeRoute: '知识路线', resultDescription: '每个知识点都绑定已验证的视频内容与推荐依据。', totalDuration: '总观看时长',
    whyNeeded: '为什么需要', watchRange: '推荐观看范围', watchDuration: '观看时长', evidenceLevel: '证据级别', recommendationReason: '推荐原因', evidenceMissing: '目前没有足够的已验证内容证据。',
    qualityValidation: '质量验证', qualityPassed: '质量检查通过', qualityFailed: '质量检查未通过', qualityIssues: '需要处理的问题',
    quality_knowledge_coverage: '知识覆盖', quality_evidence_validity: '证据有效性', quality_version_compatibility: '版本兼容性', quality_content_redundancy: '内容冗余', quality_unsupported_timestamp: '时间戳有效性',
    failure_backend_unavailable_title: '无法连接 Planix 后端', failure_backend_unavailable_description: '请确认 PostgreSQL 17 与 Backend 8003 已启动后重试。',
    failure_provider_unavailable_title: '模型或资源 Provider 不可用', failure_provider_unavailable_description: '当前服务暂时无法完成学习内容分析，请检查设置中的模型与 Provider 状态。',
    failure_evidence_missing_title: '缺少可验证的视频证据', failure_evidence_missing_description: '没有足够的字幕或内容证据支撑推荐，Planix 不会伪造视频时间段。',
    failure_quality_failed_title: '质量验证未通过', failure_quality_failed_description: '当前内容未达到覆盖或证据要求，因此不会标记为完成。',
    failure_run_failed_title: '学习内容规划未完成', failure_run_failed_description: '运行已安全停止，请检查服务状态后重新开始。', startAgain: '重新开始'
  },
  settings: {
    title: '设置', subtitle: '配置 Planix Learning 使用的模型与生产组件。', aiSettings: '模型服务', backendOffline: '后端离线', backendOfflineHint: '请先启动 PostgreSQL 17 和 Planix Backend。', retryBackend: '重试',
    provider: '服务商', provider_deepseek: 'DeepSeek', provider_kimi: 'Kimi', provider_zhipu_glm: '智谱 GLM', provider_openai: 'OpenAI', provider_custom: 'Custom', provider_local: 'Local / 本地模型', provider_mock: 'Mock',
    baseUrl: 'Base URL', model: '模型', modelId: 'Model ID', recommendedModel: '推荐模型', apiKey: 'API Key', apiKeyOptional: 'API Key（可选）', apiKeyPlaceholder: '粘贴新的 API Key', apiKeyOptionalPlaceholder: '本地服务不需要时可留空',
    temperature: '温度', timeout: '超时秒数', forceNonThinking: '强制非 Thinking', forceNonThinkingEnabled: '所有支持该参数的真实模型调用均强制关闭 Thinking。', forceNonThinkingDisabled: '使用各 Provider / Model 的默认 Thinking 行为。',
    saveSettings: '保存设置', testModel: '测试模型', clearConfig: '清除配置', clearKey: '清除 Key', settingsSaved: '设置已保存', modelTestSuccess: '模型测试成功', modelTestFailed: '模型测试失败', configCleared: '本地配置已清除', keyCleared: 'API Key 已清除',
    savedProviders: '已保存服务商', noSavedProviders: '尚未保存任何服务商配置', localConfigured: '已配置本地模型',
    learningRouting: 'Learning 模型路由', learningRoutingHint: '仅配置真实 Learning 语义调用', learningSemantic: 'Learning 语义生成', learningSemanticDesc: '知识、证据与内容语义分析共享同一模型路线',
    routingTask: '任务', routingPrimary: '主模型', routingFallbackOne: '备用 1', routingFallbackTwo: '备用 2', routingAuto: '自动选择', routingNone: '无备用', saveRouting: '保存路由', routingSaved: '路由已保存',
    learningRuntimeHealth: 'Learning Runtime', modelProviderHealth: 'Model Provider', videoProviderHealth: 'Video Provider', transcriptProviderHealth: 'Transcript Provider', artifactStoreHealth: 'Artifact Store'
  }
};
