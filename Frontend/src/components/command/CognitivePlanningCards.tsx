type Translator = (key: string) => string;

interface Props {
  data?: unknown;
  t: Translator;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {};
}

function items(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(record) : [];
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item || '')).filter(Boolean) : [];
}

function value(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.trim() ? value : fallback;
}

function label(t: Translator, key: string, fallback: string): string {
  const translated = t(key);
  return translated === key ? fallback : translated;
}

export function GoalModelCard({ data, t }: Props) {
  const raw = record(data);
  const constraints = items(raw.hardConstraints);
  const knownFacts = items(raw.knownFacts);
  const assumptions = items(raw.assumptions);
  const unknowns = items(raw.decisionRelevantUnknowns);
  const questions = items(raw.questions);
  const consistencyWarnings = strings(raw.consistencyWarnings);
  const success = record(raw.successModel);
  const feasibility = record(raw.feasibilityJudgment);
  return (
    <div className="command-inline-card wide cognitive-goal-card">
      <div className="command-card-heading">
        <strong>{label(t, 'command.cognitiveGoalModel', 'Planix 对目标的理解')}</strong>
      </div>
      <h3>{value(raw.goalStatement, label(t, 'command.untitledPlan', '未命名目标'))}</h3>
      <p>{value(raw.desiredChange)}</p>
      <dl className="command-result-meta">
        <div><dt>{label(t, 'command.cognitiveSuccess', '我理解的成功')}</dt><dd>{value(success.definition)}</dd></div>
        {value(feasibility.summary) && value(feasibility.summary) !== 'Deferred to Reality Agent' ? <div><dt>{label(t, 'command.cognitiveFeasibility', '初步判断')}</dt><dd>{value(feasibility.summary)}</dd></div> : null}
      </dl>
      {knownFacts.length ? <section><strong>{label(t, 'command.cognitiveUsedFacts', '本次使用的信息')}</strong><ul>{knownFacts.map((item, index) => <li key={index}>{value(item.statement)}<small>{value(item.sourceText)}</small></li>)}</ul></section> : null}
      {constraints.length ? <section><strong>{label(t, 'command.hardConstraints', '硬约束')}</strong><ul>{constraints.map((item, index) => <li key={index}>{value(item.statement)}</li>)}</ul></section> : null}
      {assumptions.length ? <section><strong>{label(t, 'command.cognitiveAssumptions', '当前假设')}</strong><ul>{assumptions.map((item, index) => <li key={index}>{value(item.statement)}{item.needsUserConfirmation ? ` · ${label(t, 'command.cognitiveNeedsConfirmation', '需要确认')}` : ''}</li>)}</ul></section> : null}
      {consistencyWarnings.length ? <section className="planning-consistency-warning"><strong>{label(t, 'command.consistencyWarning', '目标一致性提醒')}</strong><ul>{consistencyWarnings.map((item) => <li key={item}>{item}</li>)}</ul></section> : null}
      {unknowns.length ? <section><strong>{label(t, 'command.cognitiveUnknowns', '仍不确定的信息')}</strong><ul>{unknowns.map((item, index) => <li key={index}><b>{value(item.description)}</b><small>{value(item.whyItChangesThePlan)}</small></li>)}</ul></section> : null}
      {questions.length ? <section><strong>{label(t, 'command.clarificationQuestions', '需要确认的问题')}</strong><ol>{questions.map((item, index) => <li key={index}>{value(item.question)}<small>{value(item.whyThisQuestionMatters)}</small></li>)}</ol></section> : null}
    </div>
  );
}

export function RealityAssessmentCard({ data, t }: Props) {
  const raw = record(data);
  const risks = items(raw.hiddenRisks);
  const questions = items(raw.importantQuestions);
  return (
    <div className="command-inline-card wide cognitive-reality-card">
      <div className="command-card-heading">
        <strong>{label(t, 'command.cognitiveReality', '现实判断')}</strong>
      </div>
      <h3>{value(raw.goalRestatement)}</h3>
      <p>{value(raw.feasibilitySummary)}</p>
      <dl className="command-result-meta">
        <div><dt>{label(t, 'command.cognitiveRealityTime', '时间是否现实')}</dt><dd>{value(raw.timeAssessment)}</dd></div>
        <div><dt>{label(t, 'command.cognitiveRealityResources', '资源是否足够')}</dt><dd>{value(raw.resourceAssessment)}</dd></div>
      </dl>
      {risks.length ? <section><strong>{label(t, 'command.cognitiveRealityRisks', '需要正视的风险')}</strong><ul>{risks.map((item, index) => <li key={index}><b>{value(item.risk)}</b><small>{value(item.consequence)} · {value(item.mitigation)}</small></li>)}</ul></section> : null}
      {strings(raw.recommendedAdjustments).length ? <section><strong>{label(t, 'command.cognitiveRealityAdjustments', '建议调整')}</strong><ul>{strings(raw.recommendedAdjustments).map((item) => <li key={item}>{item}</li>)}</ul></section> : null}
      {questions.length ? <section><strong>{label(t, 'command.clarificationQuestions', '下一步最值得确认')}</strong><ol>{questions.map((item, index) => <li key={index}>{value(item.question)}<small>{value(item.whyThisQuestionMatters)}</small></li>)}</ol></section> : null}
    </div>
  );
}

export function ModelUnavailableCard({ t }: { t: Translator }) {
  return (
    <div className="command-inline-card wide cognitive-model-unavailable">
      <div className="command-card-heading">
        <strong>{label(t, 'command.cognitiveModelUnavailable', '当前无法进行深度规划')}</strong>
      </div>
      <p>{label(t, 'command.cognitiveModelUnavailableHint', '你的目标信息已经保留。Planix 不会使用模板冒充 AI 方案。')}</p>
    </div>
  );
}

export function EvidencePackCard({ data, t }: Props) {
  const raw = record(data);
  const rules = items(raw.planningRules);
  const userEvidence = items(raw.userEvidence);
  const domainEvidence = items(raw.domainEvidence);
  const candidates = items(raw.resourceCandidates);
  const gaps = items(raw.gaps);
  const calendar = record(raw.calendarReality);
  return (
    <div className="command-inline-card wide cognitive-evidence-card">
      <div className="command-card-heading">
        <strong>{label(t, 'command.cognitiveEvidence', '证据和上下文')}</strong>
      </div>
      <p>{value(raw.synthesis, label(t, 'command.cognitiveNoEvidence', '尚无足够证据摘要。'))}</p>
      {userEvidence.length ? <section><strong>{label(t, 'command.cognitiveUserEvidence', '用户与历史证据')}</strong><ul>{userEvidence.map((item, index) => <li key={index}>{value(item.statement)}<small>{value(item.whyRelevant)}</small></li>)}</ul></section> : null}
      {rules.length ? <section><strong>{label(t, 'command.cognitiveAppliedRules', '本次应用的用户规则')}</strong><ul>{rules.map((item, index) => <li key={index}>{value(item.rule)}<small>{strings(item.evidence).join(' / ')}</small></li>)}</ul></section> : null}
      {domainEvidence.length ? <section><strong>{label(t, 'command.cognitiveDomainEvidence', '影响本次规划的证据')}</strong><ul>{domainEvidence.map((item, index) => <li key={index}>{value(item.claim)}<small>{value(item.sourceType)} · {Math.round(Number(item.credibility || 0) * 100)}% · {value(item.relevance)}{value(item.sourceRef) ? ` · ${value(item.sourceRef)}` : ''}</small></li>)}</ul></section> : null}
      {candidates.length ? <section><strong>{label(t, 'command.cognitiveResourceEvidence', '可用资源证据')}</strong><ul>{candidates.slice(0, 8).map((item, index) => <li key={index}><b>{value(item.title)}</b><small>{value(item.howItHelps)} · {value(item.userFit)}</small></li>)}</ul></section> : null}
      {strings(calendar.conflicts).length || strings(calendar.loadWarnings).length ? <section><strong>{label(t, 'command.cognitiveCalendarReality', '日历现实约束')}</strong><ul>{[...strings(calendar.conflicts), ...strings(calendar.loadWarnings)].map((item) => <li key={item}>{item}</li>)}</ul></section> : null}
      {gaps.length ? <section><strong>{label(t, 'command.cognitiveEvidenceGaps', '证据缺口')}</strong><ul>{gaps.map((item, index) => <li key={index}>{value(item.description)}<small>{value(item.consequence)} · {value(item.proposedResolution)}</small></li>)}</ul></section> : null}
    </div>
  );
}

export function StrategyPortfolioCard({ data, t }: Props) {
  const raw = record(data);
  const strategies = items(raw.strategies);
  const recommended = value(raw.recommendedStrategyId);
  const decision = record(raw.userDecision);
  return (
    <div className="command-inline-card wide cognitive-strategy-card">
      <div className="command-card-heading">
        <strong>{label(t, 'command.cognitiveStrategyPortfolio', '规划方案选择')}</strong>
        <span>{strategies.length}</span>
      </div>
      <p>{value(raw.recommendationReason)}</p>
      <div className="command-result-grid">
        {strategies.map((strategy, index) => {
          const rationale = record(strategy.rationale);
          const phases = items(strategy.phases);
          const isRecommended = value(strategy.id) === recommended;
          return (
            <section className={`command-result-section ${isRecommended ? 'recommended' : ''}`} key={value(strategy.id, String(index))}>
              <strong>{value(strategy.name)}{isRecommended ? ` · ${label(t, 'command.cognitiveRecommended', '推荐')}` : ''}</strong>
              <p>{value(strategy.coreIdea)}</p>
              <small>{value(rationale.whyItFitsUser)}</small>
              {strings(strategy.expectedResults).length ? <small>{label(t, 'command.cognitiveExpectedResults', '预期结果')}: {strings(strategy.expectedResults).join(' / ')}</small> : null}
              {value(strategy.estimatedEffort) ? <small>{label(t, 'command.cognitiveEstimatedEffort', '预计投入')}: {value(strategy.estimatedEffort)}</small> : null}
              <ul>{phases.map((phase, phaseIndex) => <li key={phaseIndex}><b>{value(phase.title)}</b><small>{value(phase.outcome)}</small></li>)}</ul>
              {strings(strategy.tradeoffs).length ? <small>{label(t, 'command.cognitiveTradeoffs', '取舍')}: {strings(strategy.tradeoffs).join(' / ')}</small> : null}
              {strings(strategy.majorRisks).length ? <small>{label(t, 'command.cognitiveRisks', '风险')}: {strings(strategy.majorRisks).join(' / ')}</small> : null}
            </section>
          );
        })}
      </div>
      {value(decision.question) ? <p><strong>{value(decision.question)}</strong><br />{strings(decision.options).join(' / ')}</p> : null}
    </div>
  );
}

export function ExecutionBlueprintCard({ data, t }: Props) {
  const raw = record(data);
  const narrative = record(raw.narrative);
  const tasks = items(raw.tasks);
  return (
    <div className="command-inline-card wide cognitive-execution-card">
      <div className="command-card-heading">
        <strong>{label(t, 'command.cognitiveExecutionBlueprint', '执行蓝图')}</strong>
        <span>{tasks.length}</span>
      </div>
      <p>{value(narrative.executionLogic)}</p>
      <small>{value(narrative.workloadReasoning)} · {value(narrative.riskHandling)}</small>
      <div className="execution-task-list">
        {tasks.map((task, index) => (
          <details className="execution-task-detail" key={value(task.id, String(index))} open={index === 0}>
            <summary><span><strong>{index + 1}. {value(task.title)}</strong><small>{value(task.scheduledDate, value(task.scheduleWindow, label(t, 'command.noDate', '待排期')))} · {String(task.estimatedMinutes || 0)} {label(t, 'command.minutes', '分钟')}</small></span><em>{value(task.difficulty)}</em></summary>
            <div className="execution-task-body">
              <p>{value(task.purpose)}</p>
              <strong>{label(t, 'command.cognitiveActionSteps', '具体行动')}</strong>
              <ol>{strings(task.actionSteps).map((step) => <li key={step}>{step}</li>)}</ol>
              <dl className="command-result-meta">
                <div><dt>{label(t, 'command.deliverable', '产出物')}</dt><dd>{value(task.deliverable)}</dd></div>
                <div><dt>{label(t, 'command.acceptanceCriteria', '完成证据')}</dt><dd>{strings(task.completionEvidence).join(' / ')}</dd></div>
                <div><dt>{label(t, 'command.fallbackAdjustment', '失败时降级')}</dt><dd>{value(task.fallbackAction)}</dd></div>
                <div><dt>{label(t, 'command.cognitiveDependencies', '依赖')}</dt><dd>{strings(task.dependencies).join(' / ') || label(t, 'command.cognitiveNone', '无')}</dd></div>
              </dl>
              <strong>{label(t, 'command.whereToLearn', '资源与使用方式')}</strong>
              <ul>{items(task.resources).map((resource, resourceIndex) => <li key={resourceIndex}><b>{value(resource.title)}</b><p>{value(resource.exactUsage)}</p><small>{value(resource.expectedContribution)}</small></li>)}</ul>
            </div>
          </details>
        ))}
      </div>
    </div>
  );
}

export function CritiqueReportCard({ data, t }: Props) {
  const raw = record(data);
  const findings = items(raw.issues);
  const repairs = items(raw.repairRequests);
  const strengths = strings(raw.strengths);
  const writable = Boolean(raw.calendarWritable);
  return (
    <div className={`command-inline-card wide cognitive-critique-card ${writable ? 'passed' : 'blocked'}`}>
      <div className="command-card-heading">
        <strong>{label(t, 'command.cognitiveCritique', '独立质量审查')}</strong>
        <span>{writable ? label(t, 'command.cognitiveCalendarAllowed', '可继续') : label(t, 'command.cognitiveCalendarBlocked', '需要调整')}</span>
      </div>
      <p>{value(raw.simulationSummary)}</p>
      <strong>{writable ? label(t, 'command.cognitiveCalendarAllowed', '可进入日历确认') : label(t, 'command.cognitiveCalendarBlocked', '暂不可写入日历')}</strong>
      {strengths.length ? <section><strong>{label(t, 'command.cognitiveStrengths', '通过理由')}</strong><ul>{strengths.map((item) => <li key={item}>{item}</li>)}</ul></section> : null}
      {findings.length ? <ul>{findings.map((item, index) => <li key={index}><p>{value(item.description)}</p><small>{value(item.evidence)}</small></li>)}</ul> : null}
      {repairs.length ? <section><strong>{label(t, 'command.executionQualityRepair', '修复建议')}</strong><ul>{repairs.map((item, index) => <li key={index}>{value(item.instruction)}</li>)}</ul></section> : null}
      {strings(raw.remainingRisks).length ? <small>{label(t, 'command.cognitiveRemainingRisks', '剩余风险')}: {strings(raw.remainingRisks).join(' / ')}</small> : null}
    </div>
  );
}

export function PlanningLearningUpdateCard({ data, t }: Props) {
  const raw = record(data);
  const diagnosis = record(raw.diagnosis);
  const patch = record(raw.currentPlanPatch);
  const hypothesis = record(raw.userModelHypothesis);
  return (
    <div className="command-inline-card wide cognitive-learning-card">
      <div className="command-card-heading"><strong>{label(t, 'command.cognitiveLearning', '规划学习更新')}</strong><span>{value(diagnosis.failureStage)}</span></div>
      <p>{value(raw.originalFeedback)}</p>
      <dl className="command-result-meta">
        <div><dt>{label(t, 'command.whatWentWrong', '失败假设')}</dt><dd>{value(diagnosis.failedAssumption)}</dd></div>
        <div><dt>{label(t, 'command.whyItHappened', '根因')}</dt><dd>{value(diagnosis.rootCause)}</dd></div>
        {value(patch.instruction) ? <div><dt>{label(t, 'command.currentPatch', '当前修复')}</dt><dd>{value(patch.instruction)}</dd></div> : null}
        {value(hypothesis.rule) ? <div><dt>{label(t, 'command.longTermLearning', '长期假设')}</dt><dd>{value(hypothesis.rule)} · {Math.round(Number(hypothesis.confidence || 0) * 100)}%<small>{value(hypothesis.evidence)}{strings(hypothesis.domainScope).length ? ` · ${label(t, 'command.cognitiveDomain', '领域')}: ${strings(hypothesis.domainScope).join(' / ')}` : ''}</small></dd></div> : null}
      </dl>
    </div>
  );
}
