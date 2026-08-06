import { renderToStaticMarkup } from 'react-dom/server';
import { Children, isValidElement, type ReactElement, type ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { AgentThread } from './AgentThread';
import { ApprovalCard } from './ApprovalCard';
import { CommandDecisionCard } from './CommandDecisionCard';
import {
  CritiqueReportCard,
  EvidencePackCard,
  ExecutionBlueprintCard,
  GoalModelCard,
  ModelUnavailableCard,
  PlanningLearningUpdateCard,
  RealityAssessmentCard,
  StrategyPortfolioCard
} from './CognitivePlanningCards';
import {
  AgentDecisionCard,
  AgentMessageCard,
  ExecutionPlanDraftCard,
  LearningUpdateBadge,
  MemoryInsightCard,
  PlanDesignProposalCard,
  PlanningSessionStatusCard,
  ResourceBriefCard,
  UserNeedContractCard
} from './DeepPlanningCards';
import { DeepPlanningActionBar } from './DeepPlanningActionBar';
import { deriveDeepPlanningStatus, planningStageFromStatus } from './deepPlanningStatus';
import { MemorySearchResultsCard } from './MemorySearchResultsCard';
import { MemoryWritePreviewCard } from './MemoryWritePreviewCard';
import { MemoryWriteResultCard } from './MemoryWriteResultCard';
import { ModelUsageBadge } from './ModelUsageBadge';
import { NoteSearchResultsCard } from './NoteSearchResultsCard';
import { NoteWritePreviewCard } from './NoteWritePreviewCard';
import { NoteWriteResultCard } from './NoteWriteResultCard';
import { PlanPatchPreviewCard } from './PlanPatchPreviewCard';
import { PlanPatchResultCard } from './PlanPatchResultCard';
import { PlanSearchResultsCard } from './PlanSearchResultsCard';
import { ClarificationChoices, PlanningOverviewCard } from './PlanningOverviewCard';

const labels: Record<string, string> = {
  'command.title': 'Planix',
  'command.empty': '你可以直接说：',
  'command.examplePlan': '帮我规划 30 天学 Python',
  'command.exampleQuery': '今天有什么计划？',
  'command.examplePatch': '把明天的任务改到后天',
  'command.exampleRefine': '把这个任务拆细一点',
  'command.exampleNote': '记一下：我晚上 8 点后适合学习',
  'command.planSearchResults': 'Search results',
  'command.calendarPlans': 'Calendar plans',
  'command.goalHistory': 'Goal history',
  'command.materialResults': 'Materials',
  'command.material': 'Material',
  'command.monthNotes': 'Month notes',
  'command.untitledPlan': 'Untitled',
  'command.noDate': 'No date',
  'command.minutes': 'minutes',
  'command.planPatchPreview': 'Patch preview',
  'command.planPatchResult': 'Patch result',
  'command.updateOperation': 'Update',
  'command.deleteOperation': 'Delete',
  'command.before': 'Before',
  'command.after': 'After',
  'command.planUpdated': 'Plan updated',
  'command.planDeleted': 'Plan deleted',
  'command.planPatchFailed': 'Patch failed',
  'command.statusSuccess': 'Success',
  'command.statusError': 'Error',
  'command.intentDecision': 'Intent decision',
  'command.llmDecision': 'LLM decision',
  'command.localFallbackRule': 'Local fallback rule',
  'command.decisionUnderstand': 'I understand: ',
  'command.decisionExecute': 'I will: ',
  'command.decisionExecutePrefix': 'Handle',
  'command.decisionIntentQueryPlan': 'view plans',
  'command.decisionIntentChat': 'chat',
  'command.decisionExecuteQueryCalendar': 'query Calendar',
  'command.decisionExecuteChat': 'continue chat',
  'command.intent': 'Intent',
  'command.action': 'Action',
  'command.target': 'Target',
  'command.confidence': 'Confidence',
  'command.noteSearchResults': 'Note results',
  'command.noteWritePreview': 'Note save preview',
  'command.noteWriteResult': 'Note save result',
  'command.noteSaved': 'Saved',
  'command.noteWriteFailed': 'Failed to save note',
  'command.noteWriteTarget': 'Ready to record into {year}-{month} notes:',
  'command.modelUsage': 'Model usage',
  'command.model': 'Model',
  'command.usageTask': 'Task',
  'command.tokens': 'Tokens',
  'command.promptTokens': 'prompt',
  'command.completionTokens': 'completion',
  'command.totalTokens': 'total',
  'command.latency': 'Latency',
  'command.noTokenStats': 'No token stats',
  'command.noTokenStatsShort': 'no token stats',
  'command.localFallbackNoTokens': 'Local fallback, no tokens',
  'command.routeTrace': 'Route',
  'command.routeSuccess': 'success',
  'command.routeFailed': 'failed',
  'command.routeSkipped': 'skipped',
  'command.routeMissingKey': 'missing API key',
  'command.routeLocalFallback': 'local fallback',
  'command.fallbackUsed': 'Fallback',
  'command.approvalRequired': 'Approval required',
  'command.writeRisk': 'Write',
  'command.recordOperation': 'Record',
  'command.confirmRecord': '确认记录',
  'command.confirmModify': '确认修改',
  'command.confirmDelete': '确认删除',
  'command.confirmWrite': '确认写入',
  'command.approve': 'Confirm',
  'command.reject': 'Cancel',
  'command.running': 'Running',
  'command.assistant': 'Planix',
  'command.clarificationChoiceHint': 'Choose the option that best fits, or enter another answer.',
  'command.clarificationOther': 'Other',
  'command.clarificationOtherPlaceholder': 'Describe your situation',
  'command.clarificationSubmit': 'Send',
  'command.quickActions': 'Quick actions',
  'command.quickWriteCalendar': '写入日历',
  'command.quickViewPlans': '查看计划',
  'command.quickModifyPlan': '修改计划',
  'command.quickRefinePlan': '细化计划',
  'command.quickSearchNotes': '查笔记',
  'command.quickRecordNote': '记录笔记',
  'command.quickSearchNotesMessage': '查询我的笔记',
  'command.quickRecordNoteMessage': '记录一条笔记',
  'command.resultDate': 'Date',
  'command.resultTime': 'Time',
  'command.resultDuration': 'Duration',
  'command.resultSource': 'Source',
  'command.resultActions': 'Actions',
  'command.actionRefine': 'Refine',
  'command.actionModify': 'Edit',
  'command.actionDelete': 'Delete',
  'command.actionUseInPlan': 'Use in plan',
  'command.actionContinueView': 'Continue',
  'command.actionRefinePlanMessage': '细化第 {index} 个计划',
  'command.actionModifyPlanMessage': '修改第 {index} 个计划',
  'command.actionDeletePlanMessage': '删除第 {index} 个计划',
  'command.actionUseInPlanMessage': '把第 {index} 条笔记引用到规划',
  'command.actionContinueViewMessage': '继续查看第 {index} 条笔记',
  'command.usageTaskDecision': 'decision',
  'command.usageTaskPlanGeneration': 'plan generation',
  'command.usageTaskMemoryQuery': 'memory query',
  'command.usageTaskMemoryWrite': 'memory write',
  'command.usageTaskQueryNotes': 'note search',
  'command.usageTaskNoteWrite': 'note write',
  'command.memoryLibrary': 'Memory library',
  'command.memorySearchResults': 'Memory results',
  'command.memoryWritePreview': 'Memory preview',
  'command.memoryWriteResult': 'Memory result',
  'command.memorySaved': 'Recorded',
  'command.memoryWriteFailed': 'Memory write failed',
  'command.memoryWriteTarget': 'Ready to record into {kind}:',
  'command.memoryKind': 'Memory type',
  'command.memoryKindNote': 'Personal record',
  'command.memoryKindMaterial': 'Knowledge material',
  'command.memoryKindPlanningHistory': 'Planning archive',
  'command.memoryKindPreference': 'Preference constraint',
  'command.memoryKindReview': 'Review feedback',
  'command.untitledMemory': 'Untitled memory',
  'command.quickSearchMemory': '查记忆',
  'command.quickRecordMemory': '记录记忆',
  'command.quickWriteCalendarMessage': '写入日历',
  'command.quickViewPlansMessage': '查看我的计划',
  'command.quickModifyPlanMessage': '修改我的计划',
  'command.quickRefinePlanMessage': '细化当前计划',
  'command.quickSearchMemoryMessage': '查一下我的记忆',
  'command.quickRecordMemoryMessage': '记录一条记忆',
  'command.actionUseMemoryInPlanMessage': '把第 {index} 条记忆引用到规划',
  'command.actionContinueMemoryViewMessage': '继续查看第 {index} 条记忆',
  'command.planningSessionStarted': 'Deep planning session',
  'command.planningSessionStatus': 'Planning status',
  'command.planningWorkspace': 'Planning Workspace',
  'command.planningBusinessStatus': 'Business status',
  'command.planningRuntimeStatus': 'Runtime status',
  'command.goalCompletion': 'Goal completion',
  'command.goalComplete': 'Sufficiently understood',
  'command.goalIncomplete': 'Blocking information remains',
  'command.latestPlanningStep': 'Latest planning step',
  'command.currentStage': 'Current Stage',
  'command.currentUnderstanding': 'Current Understanding',
  'command.importantDecisions': 'Important Decisions',
  'command.importantUnknowns': 'Important Unknowns',
  'command.nextAction': 'Next Action',
  'command.skipCurrentStage': 'Skip this step',
  'command.skipCurrentStageMessage': 'Skip this step and continue with the information already provided',
  'command.skipCurrentStageHint': 'Continue with saved information.',
  'command.skipCurrentStageBlocked': 'Critical risks cannot be skipped.',
  'command.goalUnderstanding': 'Goal Understanding',
  'command.knownFacts': 'Known Facts',
  'command.lastConfirmedKnownFacts': 'Last Confirmed Facts',
  'command.optionalUnknowns': 'Optional context (does not block planning)',
  'command.noKnownFacts': 'No known facts have been saved yet.',
  'command.noBlockingUnknowns': 'No blocking unknowns. Planning can continue.',
  'command.planningPendingModelInput': 'Waiting for model processing',
  'command.planningPendingModelInputFallback': 'Model processing is blocked; the last confirmed information remains unchanged.',
  'command.uncertainties': 'Needs confirmation',
  'command.consistencyWarning': 'Goal consistency warning',
  'command.planningPossibleDirections': 'Possible directions',
  'command.planningFactLocation': 'Location',
  'command.planningFactGoal': 'Goal',
  'command.planningFactSkill': 'Target skill',
  'command.planningFactBackground': 'Background',
  'command.planningFactPurpose': 'Purpose',
  'command.planningDirectionTravel': 'Travel',
  'command.planningDirectionCareer': 'Work or career',
  'command.planningDirectionRelocation': 'Relocation',
  'command.planningDirectionOther': 'Other',
  'command.planningUnderstandingPending': 'Understanding your goal',
  'command.planningRuntimeWaitingModel': 'Your answer was saved but has not been applied as a confirmed fact.',
  'command.planningFailureFallbackSummary': 'The model could not complete this stage.',
  'command.planningFailureOutputTruncated': 'model output was truncated',
  'command.planningFailureAuthError': 'authentication failed',
  'command.planningFailureBadBaseUrl': 'service endpoint is unavailable',
  'command.planningFailureBadModel': 'configured model is unavailable',
  'command.planningFailureBadRequest': 'request configuration is unsupported',
  'command.planningFailureInsufficientBalance': 'balance or quota is insufficient',
  'command.planningFailureInvalidKeyFormat': 'API key format is invalid',
  'command.planningFailureInvalidModelOutput': 'model output does not satisfy the structured contract',
  'command.planningFailureMissingKey': 'API key is missing',
  'command.planningFailureNetworkError': 'model service cannot be reached',
  'command.planningFailureRateLimit': 'model service is rate-limited',
  'command.planningFailureTimeout': 'model service timed out',
  'command.planningFailureUnknown': 'model service did not return a usable result',
  'command.planningFailureProviderUnavailable': 'provider is unavailable',
  'command.planningFailureRequestFailed': 'model request failed',
  'command.planningAutomaticRetryAttempted': 'Planix already attempted one automatic recovery.',
  'command.retryCurrentPlanningStage': 'Retry current stage',
  'command.retryDeepPlanningMessage': 'Retry the current deep planning session',
  'command.noImportantDecisions': 'No important decisions yet.',
  'command.planningStageUnderstandGoal': 'Understand Goal',
  'command.planningStageConfirmDirection': 'Confirm Direction',
  'command.planningStageDesignPlan': 'Design Plan',
  'command.planningStageOptimizePlan': 'Optimize Plan',
  'command.planningStageWaitingConfirmation': 'Await Confirmation',
  'command.planningStageWriteCalendar': 'Write to Calendar',
  'command.planningStageReviewLearning': 'Review and Learn',
  'command.planningNextUnderstandGoal': 'Add the missing detail.',
  'command.planningNextConfirmDirection': 'Confirm the direction.',
  'command.planningNextDesignPlan': 'Wait for the design.',
  'command.planningNextOptimizePlan': 'Review the revision.',
  'command.planningNextWaitingConfirmation': 'Confirm the execution plan.',
  'command.planningNextWriteCalendar': 'Write the plan to Calendar.',
  'command.planningNextReviewLearning': 'Review the feedback.',
  'command.source': 'Source',
  'command.hiddenDraftCollapsed': 'Hidden draft · collapsed',
  'command.planningCardMemorySummary': 'Memory hits',
  'command.resources': 'resources',
  'command.tasks': 'tasks',
  'command.userNeedContract': 'Goal understanding',
  'command.memoryInsightAgent': 'Memory Insight Agent',
  'command.resourceIntelligenceAgent': 'Resource Intelligence Agent',
  'command.planDesignProposal': 'Planning direction',
  'command.executionPlanDraft': 'Execution plan draft',
  'command.learningUpdate': 'Feedback learning',
  'command.agentDecision': 'Agent decision',
  'command.agentMessage': 'Agent handoff',
  'command.agentDecisionReason': 'Decision reason',
  'command.agentMessageReason': 'Handoff reason',
  'command.agentMessageResolved': 'Resolved',
  'command.agentInputs': 'Input artifacts',
  'command.agentOutputs': 'Output artifacts',
  'command.agentPayload': 'Payload',
  'command.canMoveToDesign': 'Ready for design',
  'command.needsClarification': 'Needs clarification',
  'command.targetOutcome': 'Target outcome',
  'command.hardConstraints': 'Hard constraints',
  'command.missingInformation': 'Missing information',
  'command.clarificationQuestions': 'Clarification questions',
  'command.slotReceived': 'Captured information',
  'command.slotMissing': 'Still missing',
  'command.nextQuestion': 'Next question',
  'command.slotDomain': 'Type',
  'command.domainLearning': 'Learning plan',
  'command.domainTravel': 'Travel plan',
  'command.slotSubject': 'Subject',
  'command.slotCurrentLevel': 'Current level',
  'command.slotTargetLevel': 'Target level',
  'command.slotDailyTime': 'Available time',
  'command.slotDuration': 'Duration',
  'command.slotPurpose': 'Purpose',
  'command.slotDestination': 'Destination',
  'command.slotPlaces': 'Places',
  'command.slotDurationDays': 'Travel days',
  'command.slotMonth': 'Travel month',
  'command.slotTransport': 'Transport',
  'command.slotBudget': 'Budget',
  'command.slotFitness': 'Fitness',
  'command.memoryInfluence': 'Memory influence',
  'command.missingTopics': 'Missing topics',
  'command.resourceUntitled': 'Untitled resource',
  'command.expectedOutput': 'Expected output',
  'command.confirmDesign': 'Confirm direction',
  'command.reviseDesign': 'Adjust direction',
  'command.confirmDesignMessage': 'Confirm direction',
  'command.reviseDesignMessage': 'Adjust direction',
  'command.confirmExecution': 'Confirm execution plan',
  'command.confirmExecutionMessage': 'Confirm execution plan',
  'command.executionReadyToWrite': 'Execution plan confirmed; ready to write to Calendar',
  'command.executionQualityPassed': 'Plan quality passed',
  'command.executionQualityBlocked': 'Plan quality needs repair',
  'command.executionQualityStatus': 'Quality status',
  'command.executionQualityScore': 'Score',
  'command.executionQualityRepair': 'Repair suggestions',
  'command.executionQualityCannotWrite': 'This execution plan is not specific enough for Calendar yet. Please revise it first.',
  'command.executionWrittenToCalendar': 'Execution plan written to Calendar',
  'command.feedbackTooHeavy': 'Too heavy',
  'command.feedbackTooHeavyMessage': 'The tasks are too heavy',
  'command.feedbackResourceHard': 'Resource too hard',
  'command.feedbackResourceHardMessage': 'The resource is too hard',
  'command.deliverable': 'Deliverable',
  'command.fallbackAdjustment': 'Fallback if stuck',
  'command.whereToLearn': 'Where/how to learn',
  'command.reflection': 'Reflection',
  'command.currentPatch': 'Current plan patch',
  'command.longTermLearning': 'Long-term rule',
  'command.noHardConstraints': 'No hard constraints captured yet.',
  'command.noMemoryHits': 'No related memory',
  'command.noResourceCandidates': 'No usable resources',
  'command.noExecutionTasks': 'No execution tasks',
  'command.expand': 'Expand',
  'command.collapse': 'Collapse',
  'command.expandAll': 'Expand all',
  'command.collapseAll': 'Collapse all',
  'command.acceptanceCriteria': 'Completion standard',
  'command.noAcceptanceCriteria': 'No completion standard',
  'command.resourceCoverage': 'Resource coverage',
  'command.knowledgePoints': 'Knowledge points',
  'command.whatWentWrong': 'What went wrong',
  'command.whyItHappened': 'Why it happened',
  'command.noImmediatePatch': 'No patch needed',
  'command.deepPlanningActions': 'Deep planning actions',
  'command.startDeepPlanning': 'Start deep planning',
  'command.startDeepPlanningMessage': 'I want to do deep planning. Please ask me what information I need to add first.',
  'command.supplementGoal': 'Add goal details',
  'command.supplementGoalMessage': 'I will add more goal details',
  'command.moreActions': 'More actions',
  'command.waitingCalendarApproval': 'Waiting for Calendar approval',
  'common.done': 'Done',
  'common.pending': 'Pending',
  'common.unknown': 'Unknown',
  'common.empty': 'Empty',
  'common.yes': 'Yes',
  'common.no': 'No'
};

const labelOverrides: Record<string, string> = {
  'command.cognitiveReality': 'Reality check',
  'command.cognitiveWorkspaceUnderstanding': 'AI is understanding your goal',
  'command.cognitiveWorkspaceStrategy': 'Planning strategy',
  'command.cognitiveWorkspaceExecution': 'Execution plan',
  'command.cognitiveWorkspaceReady': 'Execution plan confirmed',
  'command.cognitiveModelUnavailable': 'Deep planning unavailable',
  'command.cognitiveModelUnavailableHint': 'The current deep planning model is unavailable; no template plan was generated.',
  'command.quickWriteCalendarMessage': '写入日历',
  'command.quickViewPlansMessage': '查看我的计划',
  'command.quickModifyPlanMessage': '修改我的计划',
  'command.quickRefinePlanMessage': '细化当前计划',
  'command.quickSearchMemory': '查记忆',
  'command.quickRecordMemory': '记录记忆',
  'command.quickSearchMemoryMessage': '查一下我的记忆',
  'command.quickRecordMemoryMessage': '记录一条记忆'
};

function t(key: string): string {
  return labelOverrides[key] ?? labels[key] ?? key;
}

function collectButtons(node: ReactNode): ReactElement[] {
  const buttons: ReactElement[] = [];
  function visit(value: ReactNode) {
    Children.forEach(value, (child) => {
      if (!isValidElement(child)) return;
      if (child.type === 'button') {
        buttons.push(child);
      }
      visit(child.props.children);
    });
  }
  visit(node);
  return buttons;
}

function collectForms(node: ReactNode): ReactElement[] {
  const forms: ReactElement[] = [];
  function visit(value: ReactNode) {
    Children.forEach(value, (child) => {
      if (!isValidElement(child)) return;
      if (child.type === 'form') forms.push(child);
      visit(child.props.children);
    });
  }
  visit(node);
  return forms;
}

describe('Plan command cards', () => {
  it('removes the user role label while retaining the Planix assistant label', () => {
    const html = renderToStaticMarkup(
      <AgentThread
        messages={[
          { id: 'user-message', role: 'user', content: '我要学 Python', createdAt: 1 },
          { id: 'assistant-message', role: 'assistant', content: '请告诉我你的学习目标。', createdAt: 2 }
        ]}
        sending={false}
        onApprove={() => undefined}
        onSend={() => undefined}
        t={t}
      />
    );

    expect(html).toContain('<article class="command-message user"><p>我要学 Python</p></article>');
    expect(html).not.toContain('<span>你</span>');
    expect(html).not.toContain('<span>You</span>');
    expect(html).toContain('<span>Planix</span>');
    expect(html).toContain('请告诉我你的学习目标。');
  });

  it('maps model-unavailable to the latest completed planning stage', () => {
    expect(planningStageFromStatus('goal_understood', [])).toBe('design_plan');
    expect(planningStageFromStatus('strategy_pending', [])).toBe('design_plan');
    expect(planningStageFromStatus('execution_pending', [])).toBe('waiting_confirmation');
    expect(planningStageFromStatus('MODEL_UNAVAILABLE', [])).toBe('understand_goal');
    expect(planningStageFromStatus('MODEL_UNAVAILABLE', [{
      id: 'strategy', role: 'card', kind: 'strategy_portfolio_ready', content: '', createdAt: 1
    }])).toBe('design_plan');
    expect(planningStageFromStatus('MODEL_UNAVAILABLE', [{
      id: 'execution', role: 'card', kind: 'execution_blueprint_ready', content: '', createdAt: 1
    }])).toBe('optimize_plan');
  });

  it('renders cognitive planning artifacts as user-readable cards', () => {
    const goalHtml = renderToStaticMarkup(<GoalModelCard data={{
      goalStatement: '安全掌握基础游泳能力',
      desiredChange: '连续游 200 米并掌握安全边界',
      domain: 'swimming',
      confidence: 0.82,
      hardConstraints: [{ statement: '必须在有救生员的泳池练习' }],
      knownFacts: [{ statement: '用户零基础，每天可练习一小时', sourceText: '零基础，每天一小时' }],
      assumptions: [{ statement: '每周可去泳池两次', needsUserConfirmation: true }],
      decisionRelevantUnknowns: [{ description: '是否能稳定使用泳池', whyItChangesThePlan: '影响可行性和排期' }],
      questions: [{ question: '你能稳定使用有救生员的泳池吗？', whyThisQuestionMatters: '决定是否可以开始水中练习' }],
      successModel: { definition: '连续游 200 米' },
      feasibilityJudgment: { summary: '需要先确认安全练习条件' }
    }} t={t} />);
    const evidenceHtml = renderToStaticMarkup(<EvidencePackCard data={{
      synthesis: '安全条件和教练资源会改变第一阶段。',
      confidence: 0.8,
      userEvidence: [{ statement: '用户只在有救生员的泳池练习', whyRelevant: '决定安全边界' }],
      domainEvidence: [{ claim: '零基础练习需要现场安全支持', relevance: '影响资源和任务顺序', sourceRef: 'local:safety-note' }],
      planningRules: [{ rule: '不安排无人监督的水中练习', evidence: ['用户零基础'] }],
      resourceCandidates: [{ title: '合格游泳教练', howItHelps: '纠正动作', userFit: '适合零基础' }],
      gaps: [{ description: '泳池开放时间未知', consequence: '无法精确排期', proposedResolution: 'ask_user' }],
      calendarReality: { conflicts: [], loadWarnings: [] }
    }} t={t} />);
    const realityHtml = renderToStaticMarkup(<RealityAssessmentCard data={{
      goalRestatement: '三个月安全连续游 200 米',
      feasibilitySummary: '在有监督训练环境下可行。',
      timeAssessment: '每天一小时足够建立稳定练习节奏。',
      resourceAssessment: '需要稳定泳池和现场安全支持。',
      hiddenRisks: [{ risk: '水上安全', consequence: '无人监督可能受伤', mitigation: '仅在有救生员时入水' }],
      recommendedAdjustments: ['先验证训练环境'],
      importantQuestions: []
    }} t={t} />);
    const strategyHtml = renderToStaticMarkup(<StrategyPortfolioCard data={{
      recommendedStrategyId: 'safe-route',
      recommendationReason: '先建立安全基础，再增加距离。',
      strategies: [{ id: 'safe-route', name: '教练带领的安全路线', coreIdea: '先安全后距离', rationale: { whyItFitsUser: '零基础且重视安全' }, phases: [{ title: '水性与呼吸', outcome: '能安全漂浮' }], tradeoffs: ['进度更稳'], majorRisks: ['教练时间'], expectedResults: ['安全连续游 200 米'], estimatedEffort: '每周两次' }],
      userDecision: { question: '采用这条路线吗？', options: ['采用', '调整'] }
    }} t={t} />);
    const executionHtml = renderToStaticMarkup(<ExecutionBlueprintCard data={{
      narrative: { executionLogic: '先确认安全条件，再做入水练习。', workloadReasoning: '每次 60 分钟', riskHandling: '无救生员则停止' },
      resourceCoverage: 'partial',
      tasks: [{ id: 't1', title: '与教练完成浅水区漂浮评估', scheduledDate: '2026-07-11', estimatedMinutes: 60, difficulty: 'medium', purpose: '建立安全起点', actionSteps: ['确认救生员在场', '完成漂浮评估'], completionEvidence: ['教练确认结果'], deliverable: '漂浮评估记录', fallbackAction: '仅完成岸上安全说明', dependencies: [], resources: [{ title: '游泳教练', exactUsage: '现场指导', expectedContribution: '纠正动作' }] }]
    }} t={t} />);
    const critiqueHtml = renderToStaticMarkup(<CritiqueReportCard data={{
      status: 'passed', score: 91, simulationSummary: '已模拟首次练习和安全失败路径。', calendarWritable: true,
      strengths: ['安全边界和停止条件明确'], dimensions: { userFit: 92, safety: 95 },
      issues: [{ responsibleAgent: 'execution_designer', severity: 'minor', description: '安全边界明确', evidence: '有救生员与停止条件' }], remainingRisks: ['泳池临时关闭']
    }} t={t} />);
    const learningHtml = renderToStaticMarkup(<PlanningLearningUpdateCard data={{
      originalFeedback: '资料太理论', diagnosis: { failureStage: 'resource', failedAssumption: '用户适合先看文档', rootCause: '缺少示范' },
      currentPlanPatch: { instruction: '替换为教练示范' }, userModelHypothesis: { rule: '先示范后阅读', confidence: 0.62 }
    }} t={t} />);

    expect(goalHtml).toContain('安全掌握基础游泳能力');
    expect(goalHtml).toContain('影响可行性和排期');
    expect(goalHtml).toContain('用户零基础，每天可练习一小时');
    expect(evidenceHtml).toContain('决定安全边界');
    expect(evidenceHtml).toContain('local:safety-note');
    expect(evidenceHtml).toContain('不安排无人监督的水中练习');
    expect(realityHtml).toContain('水上安全');
    expect(realityHtml).toContain('先验证训练环境');
    expect(strategyHtml).toContain('教练带领的安全路线');
    expect(strategyHtml).toContain('安全连续游 200 米');
    expect(executionHtml).toContain('与教练完成浅水区漂浮评估');
    expect(executionHtml).toContain('现场指导');
    expect(critiqueHtml).toContain('可进入日历确认');
    expect(critiqueHtml).toContain('安全边界和停止条件明确');
    expect(critiqueHtml).not.toContain('userFit');
    expect(learningHtml).toContain('替换为教练示范');
  });
  it('renders the P Mode empty state with concrete examples', () => {
    const html = renderToStaticMarkup(
      <AgentThread messages={[]} sending={false} onApprove={() => undefined} onSend={() => undefined} t={t} />
    );

    expect(html).toContain('你可以直接说');
    expect(html).toContain('帮我规划 30 天学 Python');
    expect(html).toContain('把明天的任务改到后天');
    expect(html).toContain('记一下：我晚上 8 点后适合学习');
  });

  it('renders calendar, material, goal history, and month note search results', () => {
    const html = renderToStaticMarkup(
      <PlanSearchResultsCard
        summary="Found 4 related items."
        calendarPlans={[
          {
            id: 'plan-1',
            date: '2026-07-08',
            time: '09:30',
            title: 'Python practice',
            estimatedMinutes: 45,
            done: false
          }
        ]}
        materials={[{ title: 'Python notes', chunk: 'Use pathlib and pytest.' }]}
        goalHistory={[{ title: 'AI internship plan', summary: 'Portfolio milestones.' }]}
        monthNotes={[{ year: 2026, month: 7, content: 'Interview prep focus.' }]}
        onSend={() => undefined}
        t={t}
      />
    );

    expect(html).toContain('Search results');
    expect(html).toContain('Calendar plans');
    expect(html).toContain('Python practice');
    expect(html).toContain('Date');
    expect(html).toContain('Duration');
    expect(html).toContain('45 minutes');
    expect(html).toContain('Refine');
    expect(html).toContain('Edit');
    expect(html).toContain('Delete');
    expect(html).toContain('Goal history');
    expect(html).toContain('AI internship plan');
    expect(html).toContain('Materials');
    expect(html).toContain('Python notes');
    expect(html).toContain('Month notes');
    expect(html).toContain('Interview prep focus.');
  });

  it('renders an update diff preview with content field changes', () => {
    const html = renderToStaticMarkup(
      <PlanPatchPreviewCard
        operation="update"
        before={{
          date: '2026-07-08',
          time: '09:30',
          title: 'Python practice',
          estimatedMinutes: 45
        }}
        after={{
          date: '2026-07-10',
          time: '10:00',
          content: 'Python project practice',
          estimatedMinutes: 30
        }}
        changes={{ date: '2026-07-10', content: 'Python project practice', estimatedMinutes: 30 }}
        t={t}
      />
    );

    expect(html).toContain('Patch preview');
    expect(html).toContain('Update');
    expect(html).toContain('Before');
    expect(html).toContain('2026-07-08 09:30 - Python practice - 45 minutes');
    expect(html).toContain('After');
    expect(html).toContain('2026-07-10 10:00 - Python project practice - 30 minutes');
    expect(html).toContain('estimatedMinutes');
  });

  it('renders successful delete and failed update results', () => {
    const deleteHtml = renderToStaticMarkup(
      <PlanPatchResultCard operation="delete" status="success" t={t} />
    );
    const failedHtml = renderToStaticMarkup(
      <PlanPatchResultCard operation="update" status="failed" error="No supported plan changes" t={t} />
    );

    expect(deleteHtml).toContain('Patch result');
    expect(deleteHtml).toContain('Success');
    expect(deleteHtml).toContain('Plan deleted');
    expect(failedHtml).toContain('Error');
    expect(failedHtml).toContain('No supported plan changes');
  });

  it('renders decision, note, usage, and quick action cards', () => {
    const decisionHtml = renderToStaticMarkup(
      <CommandDecisionCard
        intent="query_plan"
        confidence={0.86}
        targetType="calendar_date"
        action="query"
        decisionSummary="View today"
        source="llm"
        t={t}
      />
    );
    const notesHtml = renderToStaticMarkup(
      <NoteSearchResultsCard
        summary="Found notes"
        materials={[{ title: 'Python note', chunk: 'Portfolio material' }]}
        monthNotes={[{ year: 2026, month: 7, content: 'Interview notes' }]}
        onSend={() => undefined}
        t={t}
      />
    );
    const previewHtml = renderToStaticMarkup(
      <NoteWritePreviewCard year={2026} month={7} date="2026-07-05" noteText="Save this" before="" after="2026-07-05 Save this" t={t} />
    );
    const resultHtml = renderToStaticMarkup(
      <NoteWriteResultCard status="success" year={2026} month={7} noteText="Save this" t={t} />
    );
    const usageHtml = renderToStaticMarkup(
      <ModelUsageBadge usage={[
        { provider: 'deepseek', model: 'chat', promptTokens: 10, completionTokens: 5, totalTokens: 15, latencyMs: 7, mode: 'llm', taskType: 'command_decision' },
        { provider: 'deepseek', model: 'plan', promptTokens: 100, completionTokens: 50, totalTokens: 150, latencyMs: 2100, mode: 'llm', taskType: 'plan_generation' },
        { provider: 'kimi', model: 'moonshot-v1-8k', totalTokens: 22, latencyMs: 120, mode: 'llm', taskType: 'memory_query', fallbackUsed: true, attempts: [
          { provider: 'zhipu_glm', model: 'glm-4-flash', status: 'skipped', errorType: 'missing_api_key' },
          { provider: 'kimi', model: 'moonshot-v1-8k', status: 'success' }
        ] },
        { provider: 'zhipu_glm', model: 'glm-4-flash', totalTokens: 8, latencyMs: 90, mode: 'llm', taskType: 'note_write' }
      ]} t={t} />
    );
    const quickHtml = renderToStaticMarkup(<DeepPlanningActionBar messages={[]} onSend={() => undefined} t={t} />);

    expect(decisionHtml).toContain('Intent decision');
    expect(decisionHtml).toContain('I understand');
    expect(decisionHtml).toContain('query Calendar');
    expect(notesHtml).toContain('Note results');
    expect(notesHtml).toContain('Python note');
    expect(notesHtml).toContain('Use in plan');
    expect(previewHtml).toContain('Note save preview');
    expect(previewHtml).toContain('Ready to record into 2026-7 notes');
    expect(resultHtml).toContain('Saved');
    expect(usageHtml).toContain('Model usage');
    expect(usageHtml).toContain('decision 15 Tokens');
    expect(usageHtml).toContain('plan generation 150 Tokens');
    expect(usageHtml).toContain('memory query 22 Tokens');
    expect(usageHtml).toContain('memory write 8 Tokens');
    expect(usageHtml).toContain('Task: memory query');
    expect(usageHtml).toContain('Fallback: Yes');
    expect(usageHtml).toContain('Route: zhipu_glm / glm-4-flash missing API key -&gt; kimi / moonshot-v1-8k success');
    expect(quickHtml).toContain('Start deep planning');
    expect(quickHtml).toContain('More actions');
    expect(quickHtml).toContain('记录记忆');
  });

  it('renders memory search and write cards by grouped kind', () => {
    const searchHtml = renderToStaticMarkup(
      <MemorySearchResultsCard
        summary="Found memory"
        groups={[
          {
            kind: 'note',
            title: 'Personal record',
            items: [{ id: 'm1', kind: 'note', title: 'Evening learning', summary: 'Study after 8 PM', tags: ['study'], updatedAt: '2026-07-08' }]
          },
          {
            kind: 'planning_history',
            title: 'Planning archive',
            items: [{ id: 'm2', kind: 'planning_history', title: 'Python plan', summary: '30-day archive', updatedAt: '2026-07-07' }]
          }
        ]}
        onSend={() => undefined}
        t={t}
      />
    );
    const previewHtml = renderToStaticMarkup(
      <MemoryWritePreviewCard kind="preference" title="Learning time" content="I study better after 8 PM" t={t} />
    );
    const resultHtml = renderToStaticMarkup(
      <MemoryWriteResultCard status="success" kind="preference" title="Learning time" t={t} />
    );

    expect(searchHtml).toContain('Memory results');
    expect(searchHtml).toContain('Personal record');
    expect(searchHtml).toContain('Planning archive');
    expect(searchHtml).toContain('Evening learning');
    expect(searchHtml).toContain('Use in plan');
    expect(previewHtml).toContain('Memory preview');
    expect(previewHtml).toContain('Preference constraint');
    expect(resultHtml).toContain('Recorded');
  });

  it('derives deep planning actions from replayed planning status', () => {
    const messages = [{
      id: 'm-status',
      role: 'card' as const,
      kind: 'planning_session_status' as const,
      content: 'waiting_design_approval',
      createdAt: 1,
      payload: { sessionId: 'session-1', status: 'waiting_design_approval' }
    }];
    const html = renderToStaticMarkup(<DeepPlanningActionBar messages={messages} onSend={() => undefined} t={t} />);

    expect(deriveDeepPlanningStatus(messages)).toBe('waiting_design_approval');
    expect(html).toContain('Confirm direction');
    expect(html).toContain('Adjust direction');
    expect(html).not.toContain('Start deep planning');
  });

  it('derives deep planning status from replay cards by priority', () => {
    const messages = [
      {
        id: 'm-start',
        role: 'card' as const,
        kind: 'planning_session_started' as const,
        content: 'waiting_design_approval',
        createdAt: 1,
        payload: { sessionId: 'session-1', status: 'waiting_design_approval' }
      },
      {
        id: 'm-draft',
        role: 'card' as const,
        kind: 'execution_plan_draft' as const,
        content: 'draft',
        createdAt: 2,
        payload: { sessionId: 'session-1', data: { status: 'approved' } }
      }
    ];
    expect(deriveDeepPlanningStatus(messages)).toBe('ready_to_write_calendar');
    const html = renderToStaticMarkup(<DeepPlanningActionBar messages={messages} onSend={() => undefined} t={t} />);
    expect(html).toContain('写入日历');
    expect(html).not.toContain('Confirm execution plan');

    const withExplicitStatus = [
      ...messages,
      {
        id: 'm-status',
        role: 'card' as const,
        kind: 'planning_session_status' as const,
        content: 'waiting_execution_approval',
        createdAt: 3,
        payload: { sessionId: 'session-1', status: 'waiting_execution_approval' }
      }
    ];
    expect(deriveDeepPlanningStatus(withExplicitStatus)).toBe('waiting_execution_approval');

    const readyStatusOnly = [{
      id: 'm-ready',
      role: 'card' as const,
      kind: 'planning_session_status' as const,
      content: 'ready_to_write_calendar',
      createdAt: 4,
      payload: { sessionId: 'session-1', status: 'ready_to_write_calendar' }
    }];
    const readyHtml = renderToStaticMarkup(<DeepPlanningActionBar messages={readyStatusOnly} onSend={() => undefined} t={t} />);
    expect(deriveDeepPlanningStatus(readyStatusOnly)).toBe('ready_to_write_calendar');
    expect(readyHtml).toContain('写入日历');
    expect(readyHtml).not.toContain('Confirm execution plan');
  });

  it('gates execution draft actions by planning group status', () => {
    const sent: string[] = [];
    const waiting = ExecutionPlanDraftCard({
      t,
      onSend: (value) => sent.push(value),
      planningStatus: 'waiting_execution_approval',
      data: { scheduleSummary: 'Ready for review.', resourceCoverageSummary: 'Resources available.', tasks: [] }
    });
    const waitingHtml = renderToStaticMarkup(waiting);
    collectButtons(waiting).forEach((button) => button.props.onClick());
    expect(waitingHtml).toContain('Confirm execution plan');
    expect(sent).toEqual(['Confirm execution plan', 'The tasks are too heavy', 'The resource is too hard']);

    sent.length = 0;
    const revision = ExecutionPlanDraftCard({
      t,
      onSend: (value) => sent.push(value),
      planningStatus: 'execution_revision',
      data: {
        scheduleSummary: 'Independent critic blocked this draft.',
        resourceCoverageSummary: 'Repair required.',
        qualityStatus: 'needs_repair',
        tasks: []
      }
    });
    const revisionHtml = renderToStaticMarkup(revision);
    collectButtons(revision).forEach((button) => button.props.onClick());
    expect(revisionHtml).not.toContain('Confirm execution plan');
    expect(sent).toEqual(['The tasks are too heavy', 'The resource is too hard']);

    const revisionMessages = [{
      id: 'm-revision',
      role: 'card' as const,
      kind: 'planning_session_status' as const,
      content: 'execution_revision',
      createdAt: 2,
      payload: { sessionId: 'session-1', status: 'execution_revision' }
    }];
    const revisionActions = renderToStaticMarkup(<DeepPlanningActionBar messages={revisionMessages} onSend={() => undefined} t={t} />);
    expect(revisionActions).not.toContain('Confirm execution plan');
    expect(revisionActions).toContain('Too heavy');

    sent.length = 0;
    const ready = ExecutionPlanDraftCard({
      t,
      onSend: (value) => sent.push(value),
      planningStatus: 'ready_to_write_calendar',
      data: { status: 'approved', scheduleSummary: 'Ready for calendar.', resourceCoverageSummary: 'Resources available.', tasks: [] }
    });
    const readyHtml = renderToStaticMarkup(ready);
    collectButtons(ready).forEach((button) => button.props.onClick());
    expect(readyHtml).toContain('Execution plan confirmed; ready to write to Calendar');
    expect(readyHtml).toContain('\u5199\u5165\u65e5\u5386');
    expect(readyHtml).not.toContain('Confirm execution plan');
    expect(sent).toEqual(['\u5199\u5165\u65e5\u5386']);

    sent.length = 0;
    const blocked = ExecutionPlanDraftCard({
      t,
      onSend: (value) => sent.push(value),
      planningStatus: 'ready_to_write_calendar',
      data: {
        status: 'approved',
        scheduleSummary: 'Ready status but weak quality.',
        resourceCoverageSummary: 'Resources repeat too much.',
        qualityStatus: 'needs_repair',
        qualityReport: {
          status: 'needs_repair',
          score: 44,
          blockers: ['The plan is too sparse.'],
          warnings: [],
          repairSuggestions: ['Add concrete project packaging tasks.']
        },
        tasks: []
      }
    });
    const blockedHtml = renderToStaticMarkup(blocked);
    collectButtons(blocked).forEach((button) => button.props.onClick());
    expect(blockedHtml).toContain('Plan quality needs repair');
    expect(blockedHtml).toContain('The plan is too sparse.');
    expect(blockedHtml).not.toContain('\u5199\u5165\u65e5\u5386</button>');
    expect(sent).toEqual([]);

    const historical = renderToStaticMarkup(
      <ExecutionPlanDraftCard
        t={t}
        onSend={() => undefined}
        planningStatus="waiting_execution_approval"
        actionsEnabled={false}
        data={{ scheduleSummary: 'Historical draft.', resourceCoverageSummary: 'Resources available.', tasks: [] }}
      />
    );
    expect(historical).not.toContain('Confirm execution plan');
    expect(historical).not.toContain('The tasks are too heavy');
  });

  it('renders one live planning workspace for the latest session instead of per-message timelines', () => {
    const messages = [
      {
        id: 'old-contract',
        role: 'card' as const,
        kind: 'user_need_contract' as const,
        content: '',
        createdAt: 1,
        payload: { sessionId: 's-old', data: { interpretedGoal: 'Old Go plan', canMoveToDesign: true } }
      },
      {
        id: 'old-design',
        role: 'card' as const,
        kind: 'plan_design_proposal' as const,
        content: '',
        createdAt: 2,
        payload: { sessionId: 's-old', data: { strategyName: 'Old strategy', status: 'waiting_user_approval', phases: [] } }
      },
      {
        id: 'user-gap',
        role: 'user' as const,
        content: '确认方向',
        createdAt: 3
      },
      {
        id: 'new-contract',
        role: 'card' as const,
        kind: 'user_need_contract' as const,
        content: '',
        createdAt: 4,
        payload: { sessionId: 's-new', data: { interpretedGoal: 'New Python plan', canMoveToDesign: true } }
      },
      {
        id: 'new-design',
        role: 'card' as const,
        kind: 'plan_design_proposal' as const,
        content: '',
        createdAt: 5,
        payload: { sessionId: 's-new', data: { strategyName: 'New strategy', status: 'waiting_user_approval', phases: [] } }
      }
    ];

    const html = renderToStaticMarkup(
      <AgentThread messages={messages} sending={false} onApprove={() => undefined} onSend={() => undefined} t={t} />
    );

    expect((html.match(/Planning Workspace/g) || [])).toHaveLength(1);
    expect(html).toContain('Current Stage');
    expect(html).toContain('Confirm Direction');
    expect(html).toContain('New Python plan');
    expect(html).not.toContain('Planning process');
    expect(html).not.toContain('Old Go plan');
    expect(html).not.toContain('waiting_design_approval');

    const advancedHtml = renderToStaticMarkup(
      <AgentThread
        messages={messages}
        sending={false}
        onApprove={() => undefined}
        onSend={() => undefined}
        advancedAgentTrace
        t={t}
      />
    );
    expect(advancedHtml).toContain('command-collapsible collapsed deep-planning-group historical');
    expect(advancedHtml).toContain('aria-expanded="false"');
    expect(advancedHtml).not.toContain('Old Go plan');
  });

  it('updates the same workspace across follow-ups and lets a complete goal advance to strategy', () => {
    const messages = [
      {
        id: 'understanding-go',
        role: 'card' as const,
        kind: 'goal_understanding' as const,
        content: '',
        createdAt: 1,
        payload: {
          sessionId: 's-go',
          intentState: 'clear_goal',
          understoodIntent: 'Learn Go for web development',
          knownFacts: { subject: 'Go', purpose: 'Web development' },
          uncertainties: []
        }
      },
      { id: 'follow-up', role: 'user' as const, content: 'I have Python and web experience.', createdAt: 2 },
      {
        id: 'goal-model-go',
        role: 'card' as const,
        kind: 'goal_model_updated' as const,
        content: '',
        createdAt: 3,
        payload: {
          sessionId: 's-go',
          data: {
            goalStatement: 'Go Web development',
            knownFacts: [
              { key: 'background', statement: 'Python experience' },
              { key: 'background', statement: 'Web development experience' },
              { key: 'purpose', statement: 'Job search and personal projects' },
              { key: 'time', statement: '20 hours/week' }
            ]
          }
        }
      },
      { id: 'continue', role: 'user' as const, content: 'Next', createdAt: 4 },
      {
        id: 'completion-go',
        role: 'card' as const,
        kind: 'goal_completion_updated' as const,
        content: '',
        createdAt: 5,
        payload: {
          sessionId: 's-go',
          businessStatus: 'strategy_pending',
          runtimeStatus: 'running',
          data: {
            complete: true,
            blockingUnknowns: [],
            optionalUnknowns: ['Preferred Go framework'],
            nextStage: 'strategy'
          }
        }
      }
    ];

    const html = renderToStaticMarkup(
      <AgentThread messages={messages} sending={false} onApprove={() => undefined} onSend={() => undefined} t={t} />
    );

    expect((html.match(/Planning Workspace/g) || [])).toHaveLength(1);
    expect(html).toContain('Design Plan');
    expect(html).toContain('Go Web development');
    expect(html).toContain('Goal: Go');
    expect(html).toContain('Python experience');
    expect(html).toContain('Job search and personal projects');
    expect(html).toContain('20 hours/week');
    expect(html).toContain('Important Unknowns');
    expect(html).toContain('No blocking unknowns. Planning can continue.');
    expect(html).toContain('Preferred Go framework');
    expect(html).not.toContain('Planning process');
    expect(html).not.toContain('Add the missing detail.');
  });

  it('offers a safe skip control only for ordinary incomplete goal clarification', () => {
    const messages = [
      {
        id: 'goal-model-skip',
        role: 'card' as const,
        kind: 'goal_model_updated' as const,
        content: '',
        createdAt: 1,
        payload: {
          sessionId: 's-skip',
          data: {
            goalStatement: 'Learn Go',
            knownFacts: [{ key: 'goal', statement: 'Learn Go' }],
            decisionRelevantUnknowns: [{
              key: 'purpose',
              description: 'What the Go skill should support',
              impact: 'strategy',
              priority: 'blocking'
            }]
          }
        }
      },
      {
        id: 'goal-completion-skip',
        role: 'card' as const,
        kind: 'goal_completion_updated' as const,
        content: '',
        createdAt: 2,
        payload: {
          sessionId: 's-skip',
          data: {
            complete: false,
            blockingUnknowns: [{ question: 'What should Go support?', impact: 'Changes strategy' }],
            optionalUnknowns: [],
            nextStage: 'goal_clarification'
          }
        }
      },
      {
        id: 'goal-status-skip',
        role: 'card' as const,
        kind: 'planning_session_status' as const,
        content: 'needs_goal_clarification',
        createdAt: 3,
        payload: {
          sessionId: 's-skip',
          status: 'needs_goal_clarification',
          businessStatus: 'goal_clarification',
          runtimeStatus: 'idle'
        }
      }
    ];
    const sent: string[] = [];
    const card = PlanningOverviewCard({
      messages,
      status: 'needs_goal_clarification',
      onSend: (value) => sent.push(value),
      t
    });
    const skipButton = collectButtons(card).find((button) => button.props.children === 'Skip this step');
    expect(skipButton).toBeDefined();
    skipButton?.props.onClick();
    expect(sent).toEqual(['Skip this step and continue with the information already provided']);

    const sendingHtml = renderToStaticMarkup(PlanningOverviewCard({
      messages,
      status: 'needs_goal_clarification',
      sending: true,
      onSend: () => undefined,
      t
    }));
    expect(sendingHtml).toContain('Skip this step');
    expect(sendingHtml).toContain('disabled=""');

    const modelBlockedMessages = messages.map((message) => (
      message.kind === 'planning_session_status'
        ? {
            ...message,
            content: 'MODEL_UNAVAILABLE',
            payload: {
              ...message.payload,
              status: 'MODEL_UNAVAILABLE',
              businessStatus: 'goal_clarification',
              runtimeStatus: 'blocked_model'
            }
          }
        : message
    ));
    const modelBlockedHtml = renderToStaticMarkup(PlanningOverviewCard({
      messages: modelBlockedMessages,
      status: 'MODEL_UNAVAILABLE',
      onSend: () => undefined,
      t
    }));
    expect(modelBlockedHtml).not.toContain('Skip this step');
    expect(modelBlockedHtml).toContain('Retry current stage');

    const criticalMessages = messages.map((message) => (
      message.id === 'goal-model-skip'
        ? {
            ...message,
            payload: {
              ...message.payload,
              data: {
                ...message.payload.data,
                decisionRelevantUnknowns: [{
                  key: 'safety',
                  description: 'Safety boundary',
                  impact: 'safety',
                  priority: 'blocking'
                }]
              }
            }
          }
        : message
    ));
    const criticalHtml = renderToStaticMarkup(PlanningOverviewCard({
      messages: criticalMessages,
      status: 'needs_goal_clarification',
      onSend: () => undefined,
      t
    }));
    expect(criticalHtml).toContain('Critical risks cannot be skipped.');
    expect(criticalHtml).toContain('disabled=""');

    const completeMessages = messages.map((message) => (
      message.kind === 'goal_completion_updated'
        ? {
            ...message,
            payload: {
              ...message.payload,
              data: {
                complete: true,
                blockingUnknowns: [],
                optionalUnknowns: ['What should Go support?'],
                nextStage: 'strategy'
              }
            }
          }
        : message
    ));
    const completeHtml = renderToStaticMarkup(PlanningOverviewCard({
      messages: completeMessages,
      status: 'waiting_design_approval',
      onSend: () => undefined,
      t
    }));
    expect(completeHtml).not.toContain('Skip this step');

    const defaultThreadHtml = renderToStaticMarkup(
      <AgentThread messages={messages} sending={false} onApprove={() => undefined} onSend={() => undefined} t={t} />
    );
    const advancedThreadHtml = renderToStaticMarkup(
      <AgentThread messages={messages} sending={false} onApprove={() => undefined} onSend={() => undefined} advancedAgentTrace t={t} />
    );
    expect(defaultThreadHtml).toContain('Skip this step');
    expect(advancedThreadHtml).not.toContain('Skip this step');
  });

  it('renders a recoverable model failure without treating pending input as a confirmed fact', () => {
    const messages = [
      {
        id: 'blocked-goal',
        role: 'card' as const,
        kind: 'goal_model_updated' as const,
        content: '',
        createdAt: 1,
        payload: {
          sessionId: 'blocked-session',
          data: {
            goalStatement: 'Learn Python',
            knownFacts: [{ key: 'skill', statement: 'Python' }],
            decisionRelevantUnknowns: [{
              key: 'purpose',
              description: 'What should Python support?',
              impact: 'strategy',
              priority: 'blocking'
            }],
            artifactState: 'last_confirmed'
          }
        }
      },
      {
        id: 'blocked-completion',
        role: 'card' as const,
        kind: 'goal_completion_updated' as const,
        content: '',
        createdAt: 2,
        payload: {
          sessionId: 'blocked-session',
          data: {
            complete: false,
            blockingUnknowns: [{
              question: 'What should Python support?',
              impact: 'Changes strategy',
              answerOptions: ['Web development', 'Data analysis']
            }],
            optionalUnknowns: ['Preferred framework'],
            nextStage: 'goal_clarification',
            artifactState: 'last_confirmed'
          }
        }
      },
      {
        id: 'blocked-understanding',
        role: 'card' as const,
        kind: 'goal_understanding' as const,
        content: 'Web development was captured but not yet applied.',
        createdAt: 2.5,
        payload: {
          understoodIntent: 'Learn Python for web development',
          knownFacts: { skill: 'Python', purpose: 'web开发' },
          uncertainties: []
        }
      },
      {
        id: 'blocked-status',
        role: 'card' as const,
        kind: 'planning_session_status' as const,
        content: 'MODEL_UNAVAILABLE',
        createdAt: 3,
        payload: {
          sessionId: 'blocked-session',
          status: 'MODEL_UNAVAILABLE',
          businessStatus: 'goal_clarification',
          runtimeStatus: 'blocked_model',
          pendingInput: { text: 'web开发', applied: false },
          modelFailure: {
            stage: 'goal_intelligence',
            resumeNode: 'goal_intelligence',
            retryable: true,
            automaticRetryAttempted: true,
            attempts: [
              { provider: 'deepseek', status: 'error', errorType: 'model_output_truncated' },
              { provider: 'kimi', status: 'success' },
              { provider: 'zhipu_glm', status: 'error', errorType: 'auth_error' },
              { provider: 'openai', status: 'skipped', errorType: 'missing_api_key' },
              { provider: 'custom', status: 'error', errorType: 'bad_base_url' },
              { provider: 'custom', status: 'error', errorType: 'bad_model' },
              { provider: 'custom', status: 'error', errorType: 'bad_request' },
              { provider: 'custom', status: 'error', errorType: 'insufficient_balance' },
              { provider: 'custom', status: 'error', errorType: 'invalid_key_format' },
              { provider: 'custom', status: 'error', errorType: 'invalid_model_output' },
              { provider: 'custom', status: 'error', errorType: 'network_error' },
              { provider: 'custom', status: 'error', errorType: 'rate_limit' },
              { provider: 'custom', status: 'error', errorType: 'timeout' },
              { provider: 'custom', status: 'error', errorType: 'unknown' }
            ],
            summary: {
              zh: '模型路由未能完成目标理解。',
              en: 'The model route could not complete goal understanding.'
            },
            action: {
              zh: '请重试当前阶段。',
              en: 'Retry this stage after checking provider settings.'
            }
          }
        }
      }
    ];
    const sent: string[] = [];
    const card = PlanningOverviewCard({
      messages,
      status: 'MODEL_UNAVAILABLE',
      onSend: (value) => sent.push(value),
      t
    });
    const html = renderToStaticMarkup(card);

    expect(html).toContain('Last Confirmed Facts');
    expect(html).toContain('Target skill: Python');
    expect(html).toContain('Waiting for model processing: web开发');
    expect(html).not.toContain('Purpose: web开发');
    expect(html).not.toContain('What should Python support?');
    expect(html).not.toContain('Preferred framework');
    expect(html).not.toContain('Web development');
    expect(html).not.toContain('>Other<');
    expect(html).not.toContain('Skip this step');
    expect(html).toContain('The model route could not complete goal understanding.');
    expect(html).toContain('DeepSeek: model output was truncated');
    expect(html).not.toContain('Kimi: model request failed');
    expect(html).toContain('GLM: authentication failed');
    expect(html).toContain('OpenAI: API key is missing');
    expect(html).toContain('Custom: service endpoint is unavailable');
    expect(html).toContain('Custom: configured model is unavailable');
    expect(html).toContain('Custom: request configuration is unsupported');
    expect(html).toContain('Custom: balance or quota is insufficient');
    expect(html).toContain('Custom: API key format is invalid');
    expect(html).toContain('Custom: model output does not satisfy the structured contract');
    expect(html).toContain('Custom: model service cannot be reached');
    expect(html).toContain('Custom: model service is rate-limited');
    expect(html).toContain('Custom: model service timed out');
    expect(html).toContain('Custom: model service did not return a usable result');
    expect(html).toContain('Planix already attempted one automatic recovery.');
    expect(html).toContain('Retry this stage after checking provider settings.');

    const retryButton = collectButtons(card).find((button) => button.props.children === 'Retry current stage');
    retryButton?.props.onClick();
    expect(sent).toEqual(['Retry the current deep planning session']);

    const sendingHtml = renderToStaticMarkup(PlanningOverviewCard({
      messages,
      status: 'MODEL_UNAVAILABLE',
      sending: true,
      onSend: () => undefined,
      t
    }));
    expect(sendingHtml).toContain('Retry current stage');
    expect(sendingHtml).toContain('disabled=""');

    const historicalHtml = renderToStaticMarkup(PlanningOverviewCard({
      messages,
      status: 'MODEL_UNAVAILABLE',
      actionsEnabled: false,
      onSend: () => undefined,
      t
    }));
    expect(historicalHtml).not.toContain('Retry current stage');

    const recoveredHtml = renderToStaticMarkup(PlanningOverviewCard({
      messages: [
        ...messages,
        {
          id: 'recovered-goal',
          role: 'card' as const,
          kind: 'goal_model_updated' as const,
          content: '',
          createdAt: 4,
          payload: {
            sessionId: 'blocked-session',
            data: {
              goalStatement: 'Learn Python for web development',
              knownFacts: [
                { key: 'skill', statement: 'Python' },
                { key: 'purpose', statement: 'Web development' }
              ],
              artifactState: 'current'
            }
          }
        },
        {
          id: 'recovered-completion',
          role: 'card' as const,
          kind: 'goal_completion_updated' as const,
          content: '',
          createdAt: 5,
          payload: {
            sessionId: 'blocked-session',
            data: {
              complete: true,
              blockingUnknowns: [],
              optionalUnknowns: [],
              nextStage: 'strategy',
              artifactState: 'current'
            }
          }
        },
        {
          id: 'recovered-status',
          role: 'card' as const,
          kind: 'planning_session_status' as const,
          content: 'waiting_design_approval',
          createdAt: 6,
          payload: {
            sessionId: 'blocked-session',
            status: 'waiting_design_approval',
            businessStatus: 'strategy_pending',
            runtimeStatus: 'idle'
          }
        }
      ],
      status: 'waiting_design_approval',
      onSend: () => undefined,
      t
    }));
    expect(recoveredHtml).toContain('<h3>Known Facts</h3>');
    expect(recoveredHtml).toContain('Purpose: Web development');
    expect(recoveredHtml).not.toContain('Last Confirmed Facts');
    expect(recoveredHtml).not.toContain('Waiting for model processing');
    expect(recoveredHtml).not.toContain('Retry current stage');
  });

  it('renders model-authored A-D clarification choices and submits choices or other text', () => {
    const preRoutingHtml = renderToStaticMarkup(PlanningOverviewCard({
      messages: [{
        id: 'goal-understanding-options',
        role: 'card',
        kind: 'goal_understanding',
        content: '你学习 Python 最主要想实现什么？',
        createdAt: 0,
        payload: {
          intentState: 'ambiguous_goal',
          understoodIntent: '你想学习 Python，但主要用途尚未确定。',
          uncertainties: [{ field: 'purpose', impact: '用途会改变项目和知识重点。' }],
          nextQuestion: '你学习 Python 最主要想实现什么？',
          clarificationOptions: ['找工作或实习', '完成个人项目', '数据分析', '系统学习编程']
        }
      }],
      onSend: () => undefined,
      t
    }));
    expect(preRoutingHtml).toContain('A</span>找工作或实习');
    expect(preRoutingHtml).toContain('<summary aria-disabled="false">Other</summary>');

    const messages = [
      {
        id: 'goal-model-options',
        role: 'card' as const,
        kind: 'goal_model_updated' as const,
        content: '',
        createdAt: 1,
        payload: {
          sessionId: 's-options',
          data: {
            goalStatement: '学习 Python',
            knownFacts: [{ key: 'skill', statement: 'Python' }],
            decisionRelevantUnknowns: [{
              key: 'purpose',
              description: 'Python 学习用途',
              whyItChangesThePlan: '用途会改变项目和知识重点。',
              impact: 'strategy',
              priority: 'blocking'
            }]
          }
        }
      },
      {
        id: 'goal-completion-options',
        role: 'card' as const,
        kind: 'goal_completion_updated' as const,
        content: '',
        createdAt: 2,
        payload: {
          sessionId: 's-options',
          data: {
            complete: false,
            blockingUnknowns: [{
              question: '你学习 Python 最主要想实现什么？',
              impact: '用途会改变项目和知识重点。',
              answerOptions: ['找工作或实习', '完成个人项目', '数据分析', '系统学习编程']
            }],
            optionalUnknowns: [],
            nextStage: 'goal_clarification'
          }
        }
      },
      {
        id: 'goal-status-options',
        role: 'card' as const,
        kind: 'planning_session_status' as const,
        content: 'needs_goal_clarification',
        createdAt: 3,
        payload: {
          sessionId: 's-options',
          status: 'needs_goal_clarification',
          businessStatus: 'goal_clarification',
          runtimeStatus: 'idle'
        }
      }
    ];
    const sent: string[] = [];
    const card = PlanningOverviewCard({
      messages,
      status: 'needs_goal_clarification',
      onSend: (value) => sent.push(value),
      t
    });
    const html = renderToStaticMarkup(card);
    expect(html).toContain('A</span>找工作或实习');
    expect(html).toContain('B</span>完成个人项目');
    expect(html).toContain('C</span>数据分析');
    expect(html).toContain('D</span>系统学习编程');
    expect(html).toContain('<summary aria-disabled="false">Other</summary>');
    expect(html).toContain('placeholder="Describe your situation"');

    const interactiveChoices = ClarificationChoices({
      options: ['找工作或实习', '完成个人项目', '数据分析', '系统学习编程'],
      disabled: false,
      onSend: (value) => sent.push(value),
      t
    });
    const choiceButton = collectButtons(interactiveChoices).find((button) => (
      renderToStaticMarkup(button).includes('完成个人项目')
    ));
    choiceButton?.props.onClick();
    expect(sent).toEqual(['完成个人项目']);

    const reset = vi.fn();
    const removeAttribute = vi.fn();
    const preventDefault = vi.fn();
    vi.stubGlobal('FormData', class {
      get() {
        return '准备自动化办公';
      }
    });
    const form = collectForms(interactiveChoices)[0];
    form.props.onSubmit({
      preventDefault,
      currentTarget: { reset, closest: () => ({ removeAttribute }) }
    });
    vi.unstubAllGlobals();
    expect(preventDefault).toHaveBeenCalledOnce();
    expect(reset).toHaveBeenCalledOnce();
    expect(removeAttribute).toHaveBeenCalledWith('open');
    expect(sent).toEqual(['完成个人项目', '准备自动化办公']);

    const disabledHtml = renderToStaticMarkup(PlanningOverviewCard({
      messages,
      status: 'needs_goal_clarification',
      sending: true,
      actionsEnabled: false,
      onSend: () => undefined,
      t
    }));
    expect(disabledHtml).toContain('A</span>找工作或实习');
    expect(disabledHtml).toContain('disabled=""');
    expect(disabledHtml).toContain('aria-disabled="true"');
  });

  it('shows the latest strategy in the default workspace while keeping technical trace cards advanced-only', () => {
    const messages = [
      {
        id: 'decision-1',
        role: 'card' as const,
        kind: 'agent_decision' as const,
        content: '',
        createdAt: 1,
        payload: {
          sessionId: 's-cognitive',
          data: {
            agent: 'Context & Evidence Agent',
            decision: 'approve',
            reason: 'technical trace detail hidden until expanded',
            userVisibleSummary: 'Evidence is sufficient.'
          }
        }
      },
      {
        id: 'strategy-1',
        role: 'card' as const,
        kind: 'strategy_portfolio_ready' as const,
        content: '',
        createdAt: 2,
        payload: {
          sessionId: 's-cognitive',
          data: {
            recommendedStrategyId: 's1',
            recommendationReason: 'Best evidence fit.',
            strategies: [{ id: 's1', name: 'Evidence route', coreIdea: 'Use evidence', phases: [] }],
            userDecision: { question: 'Use it?', options: ['yes'] }
          }
        }
      },
      {
        id: 'status-1',
        role: 'card' as const,
        kind: 'planning_session_status' as const,
        content: 'waiting_design_approval',
        createdAt: 3,
        payload: { sessionId: 's-cognitive', status: 'waiting_design_approval' }
      }
    ];
    const html = renderToStaticMarkup(
      <AgentThread messages={messages} sending={false} onApprove={() => undefined} onSend={() => undefined} t={t} />
    );
    expect(html).toContain('Planning Workspace');
    expect(html).toContain('Evidence route');
    expect(html).not.toContain('Agent decision');
    expect(html).not.toContain('technical trace detail hidden until expanded');

    const advancedHtml = renderToStaticMarkup(
      <AgentThread messages={messages} sending={false} onApprove={() => undefined} onSend={() => undefined} advancedAgentTrace t={t} />
    );
    expect(advancedHtml).toContain('Evidence route');
    expect(advancedHtml).toContain('Context &amp; Evidence Agent');
    expect(advancedHtml).toContain('technical trace detail hidden until expanded');
    expect(advancedHtml).toContain('waiting_design_approval');
  });

  it('renders only the latest Strategy, Execution Blueprint, and Critic details in the default live workspace', () => {
    const messages = [
      {
        id: 'strategy-old',
        role: 'card' as const,
        kind: 'strategy_portfolio_ready' as const,
        content: '',
        createdAt: 1,
        payload: {
          sessionId: 's-live-details',
          data: {
            recommendedStrategyId: 'old-route',
            recommendationReason: 'Superseded strategy reason',
            strategies: [{ id: 'old-route', name: 'Superseded route', coreIdea: 'Old direction', phases: [] }]
          }
        }
      },
      {
        id: 'execution-old',
        role: 'card' as const,
        kind: 'execution_blueprint_ready' as const,
        content: '',
        createdAt: 2,
        payload: {
          sessionId: 's-live-details',
          data: {
            narrative: { executionLogic: 'Superseded execution logic' },
            tasks: [{ id: 'old-task', title: 'Superseded task', actionSteps: [] }]
          }
        }
      },
      {
        id: 'critique-old',
        role: 'card' as const,
        kind: 'critique_report_ready' as const,
        content: '',
        createdAt: 3,
        payload: {
          sessionId: 's-live-details',
          data: { simulationSummary: 'Superseded critique', calendarWritable: false }
        }
      },
      {
        id: 'strategy-latest',
        role: 'card' as const,
        kind: 'strategy_portfolio_ready' as const,
        content: '',
        createdAt: 4,
        payload: {
          sessionId: 's-live-details',
          data: {
            recommendedStrategyId: 'latest-route',
            recommendationReason: 'Latest strategy reason',
            strategies: [{
              id: 'latest-route',
              name: 'Latest evidence route',
              coreIdea: 'Ship a verified backend incrementally',
              phases: [{ title: 'Foundation', outcome: 'Working API skeleton' }]
            }]
          }
        }
      },
      {
        id: 'execution-latest',
        role: 'card' as const,
        kind: 'execution_blueprint_ready' as const,
        content: '',
        createdAt: 5,
        payload: {
          sessionId: 's-live-details',
          data: {
            narrative: {
              executionLogic: 'Build and verify one deployable slice at a time.',
              workloadReasoning: 'Ten hours per week fits the schedule.',
              riskHandling: 'Use a smaller authenticated endpoint if blocked.'
            },
            tasks: [{
              id: 'auth-api',
              title: 'Build authentication API',
              scheduleWindow: 'Week 3',
              estimatedMinutes: 600,
              difficulty: 'medium',
              purpose: 'Deliver a secure vertical slice.',
              actionSteps: ['Implement password hashing and token validation.'],
              deliverable: 'Authenticated REST endpoints',
              completionEvidence: ['Login integration test passes'],
              fallbackAction: 'Limit the first slice to access tokens.',
              dependencies: ['api-foundation'],
              resources: [{
                title: 'FastAPI security guide',
                exactUsage: 'Apply the OAuth2 password bearer example.',
                expectedContribution: 'Correct authentication flow'
              }]
            }]
          }
        }
      },
      {
        id: 'critique-latest',
        role: 'card' as const,
        kind: 'critique_report_ready' as const,
        content: '',
        createdAt: 6,
        payload: {
          sessionId: 's-live-details',
          data: {
            status: 'passed',
            score: 94,
            simulationSummary: 'Latest quality review passed.',
            calendarWritable: true,
            strengths: ['Dependencies and completion evidence are explicit.'],
            issues: [],
            repairRequests: []
          }
        }
      },
      {
        id: 'technical-decision',
        role: 'card' as const,
        kind: 'agent_decision' as const,
        content: '',
        createdAt: 7,
        payload: {
          sessionId: 's-live-details',
          data: { agent: 'Critic Agent', decision: 'approve', reason: 'Private routing trace' }
        }
      },
      {
        id: 'status-live-details',
        role: 'card' as const,
        kind: 'planning_session_status' as const,
        content: 'waiting_execution_approval',
        createdAt: 8,
        payload: {
          sessionId: 's-live-details',
          status: 'waiting_execution_approval',
          businessStatus: 'execution_pending',
          runtimeStatus: 'idle'
        }
      }
    ];

    const html = renderToStaticMarkup(
      <AgentThread messages={messages} sending={false} onApprove={() => undefined} onSend={() => undefined} t={t} />
    );

    expect((html.match(/Planning Workspace/g) || [])).toHaveLength(1);
    expect(html).toContain('Latest evidence route');
    expect(html).toContain('Latest strategy reason');
    expect(html).toContain('Build authentication API');
    expect(html).toContain('Implement password hashing and token validation.');
    expect(html).toContain('Authenticated REST endpoints');
    expect(html).toContain('FastAPI security guide');
    expect(html).toContain('Latest quality review passed.');
    expect(html).toContain('Dependencies and completion evidence are explicit.');
    expect(html).not.toContain('Superseded route');
    expect(html).not.toContain('Superseded task');
    expect(html).not.toContain('Superseded critique');
    expect(html).not.toContain('Private routing trace');

    expect(html.indexOf('Latest evidence route')).toBeLessThan(html.indexOf('Build authentication API'));
    expect(html.indexOf('Build authentication API')).toBeLessThan(html.indexOf('Latest quality review passed.'));
  });

  it('aggregates goal understanding, consistency warnings, and the next question without debug details', () => {
    const messages = [{
      id: 'goal-understanding',
      role: 'card' as const,
      kind: 'goal_understanding' as const,
      content: '',
      createdAt: 1,
      payload: {
        sessionId: 'goal-session',
        intentState: 'ambiguous_goal',
        understoodIntent: 'Go to Beijing',
        possibleDomains: ['travel', 'career', 'relocation'],
        knownFacts: { location: 'Beijing' },
        uncertainties: [{ field: 'purpose', impact: 'Changes the planning strategy' }],
        consistencyWarnings: ['The stated purpose does not match the skiing goal.'],
        nextQuestion: 'What is the main purpose of going to Beijing?',
        confidence: 0.72,
        source: 'llm',
        modelUsage: { provider: 'deepseek', model: 'deepseek-chat', taskType: 'goal_understanding' }
      }
    }];
    const html = renderToStaticMarkup(
      <AgentThread messages={messages} sending={false} onApprove={() => undefined} onSend={() => undefined} t={t} />
    );
    expect(html).toContain('Current Stage');
    expect(html).toContain('Understand Goal');
    expect(html).toContain('Go to Beijing');
    expect(html).toContain('Location: Beijing');
    expect(html).toContain('Purpose — Changes the planning strategy');
    expect(html).not.toContain('purpose — Changes the planning strategy');
    expect(html).toContain('Goal consistency warning');
    expect(html).toContain('The stated purpose does not match the skiing goal.');
    expect(html).toContain('What is the main purpose of going to Beijing?');
    expect(html).toContain('Planning Workspace');
    expect(html).not.toContain('Planning process');
    expect(html).not.toContain('ambiguous_goal');
    expect(html).not.toContain('deepseek-chat');

    const advancedHtml = renderToStaticMarkup(
      <AgentThread messages={messages} sending={false} onApprove={() => undefined} onSend={() => undefined} advancedAgentTrace t={t} />
    );
    expect(advancedHtml).toContain('ambiguous_goal');
    expect(advancedHtml).toContain('deepseek-chat');
  });

  it('hides standalone decision and model-routing diagnostics unless advanced trace is enabled', () => {
    const messages = [
      { id: 'understanding', role: 'card' as const, kind: 'goal_understanding' as const, content: '', createdAt: 0, payload: { intentState: 'command', understoodIntent: 'Operational calendar query', source: 'llm' } },
      { id: 'decision', role: 'card' as const, kind: 'command_decision' as const, content: '', createdAt: 1, payload: { intent: 'query_plan', source: 'local_fallback' } },
      { id: 'usage', role: 'card' as const, kind: 'model_usage' as const, content: '', createdAt: 2, payload: { usage: { provider: 'deepseek', model: 'deepseek-chat', attempts: [{ provider: 'kimi', status: 'error', errorType: 'timeout' }] }, feature: 'goal_understanding', source: 'model_unavailable', error: 'Goal understanding model unavailable' } }
    ];
    const defaultHtml = renderToStaticMarkup(
      <AgentThread messages={messages} sending={false} onApprove={() => undefined} onSend={() => undefined} t={t} />
    );
    expect(defaultHtml).not.toContain('deepseek-chat');
    expect(defaultHtml).not.toContain('Local fallback rule');
    expect(defaultHtml).not.toContain('Operational calendar query');

    const advancedHtml = renderToStaticMarkup(
      <AgentThread messages={messages} sending={false} onApprove={() => undefined} onSend={() => undefined} advancedAgentTrace t={t} />
    );
    expect(advancedHtml).toContain('deepseek-chat');
    expect(advancedHtml).toContain('Local fallback rule');
    expect(advancedHtml).toContain('Operational calendar query');
    expect(advancedHtml).toContain('Goal understanding model unavailable');
  });

  it('renders an honest model-unavailable state without a fake plan', () => {
    const cardHtml = renderToStaticMarkup(<ModelUnavailableCard t={t} />);
    const messages = [{
      id: 'unavailable', role: 'card' as const, kind: 'planning_session_status' as const, content: 'MODEL_UNAVAILABLE', createdAt: 1,
      payload: {
        sessionId: 's-unavailable',
        status: 'MODEL_UNAVAILABLE',
        businessStatus: 'strategy_pending',
        runtimeStatus: 'blocked_model_unavailable'
      }
    }];
    const threadHtml = renderToStaticMarkup(
      <AgentThread
        messages={messages}
        sending={false}
        onApprove={() => undefined}
        onSend={() => undefined}
        t={t}
      />
    );
    expect(cardHtml).toContain('current deep planning');
    expect(threadHtml).toContain('Planning Workspace');
    expect(threadHtml).toContain('Design Plan');
    expect(threadHtml).toContain('Your answer was saved but has not been applied as a confirmed fact.');
    expect(threadHtml).not.toContain('Deep planning unavailable');
    expect(threadHtml).not.toContain('MODEL_UNAVAILABLE');
    expect(threadHtml).not.toContain('Execution blueprint');

    const advancedHtml = renderToStaticMarkup(
      <AgentThread messages={messages} sending={false} onApprove={() => undefined} onSend={() => undefined} advancedAgentTrace t={t} />
    );
    expect(advancedHtml).toContain('strategy_pending');
    expect(advancedHtml).toContain('blocked_model_unavailable');
  });

  it('sends fixed natural language messages from deep planning, more, and row actions', () => {
    const sent: string[] = [];
    const quick = DeepPlanningActionBar({ messages: [], onSend: (value) => sent.push(value), t });
    collectButtons(quick).forEach((button) => button.props.onClick());
    expect(sent[0]).toBe('I want to do deep planning. Please ask me what information I need to add first.');
    expect(sent).toContain('查看我的计划');
    expect(sent).toContain('记录一条记忆');
    expect(sent).toHaveLength(7);

    sent.length = 0;
    const planCard = PlanSearchResultsCard({
      calendarPlans: [{ id: 'p1', date: '2026-07-08', time: '09:00', title: 'Python', estimatedMinutes: 30 }],
      onSend: (value) => sent.push(value),
      t
    });
    collectButtons(planCard).forEach((button) => button.props.onClick());
    expect(sent).toEqual(['细化第 1 个计划', '修改第 1 个计划', '删除第 1 个计划']);

    sent.length = 0;
    const noteCard = NoteSearchResultsCard({
      monthNotes: [{ year: 2026, month: 7, content: 'Evening learning' }],
      onSend: (value) => sent.push(value),
      t
    });
    collectButtons(noteCard).forEach((button) => button.props.onClick());
    expect(sent).toEqual(['把第 1 条笔记引用到规划', '继续查看第 1 条笔记']);
  });

  it('renders deep planning session cards and sends feedback actions', () => {
    const sent: string[] = [];
    const statusHtml = renderToStaticMarkup(<PlanningSessionStatusCard status="waiting_design_approval" t={t} />);
    const contractHtml = renderToStaticMarkup(
      <UserNeedContractCard
        t={t}
        data={{
          interpretedGoal: '30-day Python AI internship plan',
          desiredOutcome: 'Portfolio-ready project',
          canMoveToDesign: true,
          hardConstraints: ['30 minutes daily'],
          slotState: {
            domain: 'learning',
            learning: {
              subject: 'Python',
              currentLevel: 'Beginner',
              dailyTime: '30 minutes daily',
              purpose: 'AI internship'
            },
            missingSlots: ['duration']
          },
          pendingQuestion: {
            questionText: 'How many days should this plan cover?',
            questions: ['How many days should this plan cover?']
          }
        }}
      />
    );
    const memoryHtml = renderToStaticMarkup(
      <MemoryInsightCard
        t={t}
        data={{
          confidence: 0.82,
          memoryHits: {
            preferences: [{ title: 'Project-driven learning' }],
            reviews: [],
            planningHistory: [{ title: 'Previous Python plan' }],
            materials: [],
            notes: []
          },
          planningInsights: {
            userStyleRules: ['Prefer project-driven tasks'],
            pastFailureWarnings: ['Avoid long theory blocks'],
            constraintsToRespect: ['Keep tasks under 60 minutes']
          }
        }}
      />
    );
    const resourceHtml = renderToStaticMarkup(
      <ResourceBriefCard
        t={t}
        data={{
          coverage: { status: 'partial', explanation: 'Python basics are covered.', missingTopics: ['deployment'] },
          resourceCandidates: [{
            id: 'r1',
            title: 'FastAPI Tutorial',
            sourceType: 'official_doc',
            domain: 'FastAPI',
            difficulty: 'beginner',
            howToUse: 'Read only the first two examples.'
          }]
        }}
      />
    );
    const design = PlanDesignProposalCard({
      t,
      onSend: (value) => sent.push(value),
      data: {
        status: 'waiting_user_approval',
        strategyName: 'Portfolio-driven plan',
        designRationale: 'Use project outputs instead of pure theory.',
        phases: [{ title: 'Foundation', purpose: 'Build minimum Python fluency', expectedOutput: 'Small CLI artifact' }]
      }
    });
    const designHtml = renderToStaticMarkup(design);
    collectButtons(design).forEach((button) => button.props.onClick());
    const execution = ExecutionPlanDraftCard({
      t,
      onSend: (value) => sent.push(value),
      data: {
        scheduleSummary: '30 days, low-density progression.',
        resourceCoverageSummary: 'Core resources available.',
        tasks: [{
          title: 'Build a Python CLI checklist',
          dueDate: '2026-07-10',
          estimatedMinutes: 30,
          priority: 'high',
          whyThisTaskMatters: 'It proves practical Python basics.',
          deliverable: 'cli_checklist.py',
          fallbackAdjustment: 'Only implement one command.',
          resourceBundle: {
            primary: {
              title: 'Python control flow',
              sourceType: 'official_doc',
              section: 'Control Flow',
              useStep: 'Read one example, then code.',
              expectedOutput: 'A running script'
            },
            practice: {
              title: 'If/else exercise',
              sourceType: 'practice_bank',
              searchKeyword: 'python if else practice',
              useStep: 'Finish the smallest exercise.'
            }
          }
        }]
      }
    });
    const executionHtml = renderToStaticMarkup(execution);
    collectButtons(execution).forEach((button) => button.props.onClick());
    const learningHtml = renderToStaticMarkup(
      <LearningUpdateBadge
        t={t}
        data={{
          feedbackType: 'resource_feedback',
          insight: 'The resource was too hard.',
          reflection: { howToAvoidNextTime: 'Prefer project examples before official docs.' },
          immediatePatch: { action: 'replace_resource', instruction: 'Use practice bank first.' },
          longTermLearning: { newRule: 'Do not start beginners with pure theory.' }
        }}
      />
    );
    const decisionHtml = renderToStaticMarkup(
      <AgentDecisionCard
        t={t}
        data={{
          agent: 'Resource Intelligence Agent',
          decision: 'request_agent_revision',
          reason: 'The Go concurrency task is too broad for a concrete resource bundle.',
          userVisibleSummary: 'Resource Agent requested task splitting.',
          inputArtifactIds: ['a1'],
          outputArtifactIds: ['a2'],
          confidence: 0.87,
          modelUsage: {
            provider: 'deepseek',
            model: 'deepseek-chat',
            promptTokens: 200,
            completionTokens: 80,
            totalTokens: 280,
            latencyMs: 1200,
            taskType: 'planning_evidence',
            fallbackUsed: true,
            attempts: [
              { provider: 'kimi', model: 'kimi-k2.6', status: 'error', errorType: 'timeout', latencyMs: 800 },
              { provider: 'deepseek', model: 'deepseek-chat', status: 'success', latencyMs: 400 }
            ]
          }
        }}
      />
    );
    const messageHtml = renderToStaticMarkup(
      <AgentMessageCard
        t={t}
        data={{
          fromAgent: 'Feedback Evolution Agent',
          toAgent: 'Resource Intelligence Agent',
          messageType: 'revision_request',
          reason: 'Replace this task resource with beginner practice.',
          resolved: true,
          payloadJson: { taskId: 't3' }
        }}
      />
    );
    const failureMessageHtml = renderToStaticMarkup(
      <AgentMessageCard
        t={t}
        data={{
          fromAgent: 'Goal Modeling Agent',
          toAgent: 'Goal Modeling Agent',
          messageType: 'block',
          reason: 'No model produced a valid goal artifact.',
          resolved: false,
          payloadJson: {
            errorType: 'timeout',
            attempts: [{ provider: 'kimi', model: 'kimi-k2.6', status: 'error', errorType: 'timeout', latencyMs: 900 }]
          }
        }}
      />
    );

    expect(statusHtml).toContain('waiting_design_approval');
    expect(contractHtml).toContain('Goal understanding');
    expect(contractHtml).toContain('30-day Python AI internship plan');
    expect(contractHtml).toContain('Captured information');
    expect(contractHtml).toContain('Subject: Python');
    expect(contractHtml).toContain('Still missing');
    expect(contractHtml).toContain('Next question');
    expect(memoryHtml).toContain('Memory Insight Agent');
    expect(memoryHtml).toContain('Prefer project-driven tasks');
    expect(resourceHtml).toContain('Resource Intelligence Agent');
    expect(resourceHtml).toContain('FastAPI Tutorial');
    expect(designHtml).toContain('Portfolio-driven plan');
    expect(executionHtml).toContain('Build a Python CLI checklist');
    expect(executionHtml).toContain('Where/how to learn');
    expect(executionHtml).toContain('cli_checklist.py');
    expect(learningHtml).toContain('replace_resource');
    expect(learningHtml).toContain('Long-term rule');
    expect(decisionHtml).toContain('Resource Intelligence Agent');
    expect(decisionHtml).toContain('request_agent_revision');
    expect(decisionHtml).toContain('Decision reason');
    expect(decisionHtml).toContain('kimi-k2.6');
    expect(decisionHtml).toContain('timeout');
    expect(decisionHtml).toContain('deepseek-chat');
    expect(decisionHtml).toContain('280');
    expect(messageHtml).toContain('Feedback Evolution Agent');
    expect(messageHtml).toContain('Resource Intelligence Agent');
    expect(messageHtml).toContain('revision_request');
    expect(failureMessageHtml).toContain('timeout');
    expect(failureMessageHtml).toContain('kimi-k2.6');
    expect(failureMessageHtml).toContain('900ms');
    expect(sent).toEqual([
      'Confirm direction',
      'Adjust direction',
      'Confirm execution plan',
      'The tasks are too heavy',
      'The resource is too hard'
    ]);
  });

  it('renders execution task details collapsed except the first task by default', () => {
    const html = renderToStaticMarkup(
      <ExecutionPlanDraftCard
        t={t}
        onSend={() => undefined}
        data={{
          scheduleSummary: 'Two tasks.',
          resourceCoverageSummary: 'Resources available.',
          tasks: [
            {
              title: 'First task',
              dueDate: '2026-07-10',
              estimatedMinutes: 30,
              priority: 'high',
              whyThisTaskMatters: 'First detail',
              deliverable: 'first.py',
              fallbackAdjustment: 'Do less.',
              resourceCoverage: { status: 'partial', explanation: 'Enough.' },
              resourceBundle: { primary: { title: 'Python docs', sourceType: 'official_doc', useStep: 'Read one example.' } }
            },
            {
              title: 'Second task',
              dueDate: '2026-07-11',
              estimatedMinutes: 45,
              priority: 'medium',
              whyThisTaskMatters: 'Second detail',
              deliverable: 'second.py',
              fallbackAdjustment: 'Do less.',
              resourceCoverage: { status: 'partial', explanation: 'Enough.' },
              resourceBundle: { primary: { title: 'Practice bank', sourceType: 'practice_bank', useStep: 'Do one exercise.' } }
            }
          ]
        }}
      />
    );

    expect(html).toContain('Expand all');
    expect(html).toContain('Collapse all');
    expect((html.match(/<details class="execution-task-detail" open=""/g) || []).length).toBe(1);
    expect(html).toContain('Second task');
  });

  it('renders approval labels by action target and operation', () => {
    const noop = () => undefined;
    const note = renderToStaticMarkup(<ApprovalCard summary="Record" actionId="a1" target="notes" operation="update" risk="write" sending={false} onDecision={noop} t={t} />);
    const update = renderToStaticMarkup(<ApprovalCard summary="Update" actionId="a1" target="calendar" operation="update" risk="write" sending={false} onDecision={noop} t={t} />);
    const del = renderToStaticMarkup(<ApprovalCard summary="Delete" actionId="a1" target="calendar" operation="delete" risk="delete" sending={false} onDecision={noop} t={t} />);
    const write = renderToStaticMarkup(<ApprovalCard summary="Write" actionId="a1" target="calendar" operation="create_or_update_plans" risk="write" sending={false} onDecision={noop} t={t} />);

    expect(note).toContain('确认记录');
    expect(update).toContain('确认修改');
    expect(del).toContain('确认删除');
    expect(write).toContain('确认写入');
  });
});
