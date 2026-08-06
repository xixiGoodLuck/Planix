import { useState } from 'react';
import { BrainCircuit, Boxes, CalendarPlus, CheckCircle2, Database, ExternalLink, Send, Sparkles, Target } from 'lucide-react';
import { AgentFlowTrace } from '../components/agent/flow/AgentFlowTrace';
import { agentFlowActions, useAgentFlow } from '../store/agentFlowStore';
import type { CalendarWriteSummary, GoalPlanTask, InspectorLog, InspectorSnapshot, Plan, RuntimePlanProposal } from '../types';

interface DashboardPageProps {
  date: string;
  plans: Plan[];
  preferences: string;
  inspector: InspectorSnapshot;
  onAgentStatusChange: (status: InspectorSnapshot['agentStatus']) => void;
  onLog: (log: Omit<InspectorLog, 'id' | 'timestamp'>) => void;
  onApplyRuntimeProposalToCalendar: (proposal: RuntimePlanProposal) => Promise<CalendarWriteSummary>;
  onViewCalendarDate: (date: string) => void;
  t: (key: string) => string;
}

export function DashboardPage(props: DashboardPageProps) {
  const { date, plans, preferences, inspector, onAgentStatusChange, onLog, onApplyRuntimeProposalToCalendar, onViewCalendarDate, t } = props;
  const [prompt, setPrompt] = useState('');
  const [output, setOutput] = useState(t('dashboard.outputReady'));
  const [forceModelKnowledge, setForceModelKnowledge] = useState(false);
  const [calendarWriteStatus, setCalendarWriteStatus] = useState('');
  const [calendarWriteLoading, setCalendarWriteLoading] = useState(false);
  const [firstWrittenDate, setFirstWrittenDate] = useState('');
  const { latestProposal, isRunning } = useAgentFlow();
  const doneCount = plans.filter((plan) => plan.done).length;
  const pendingCount = plans.length - doneCount;

  function runAgent() {
    const value = prompt.trim();
    if (!value) return;
    onAgentStatusChange('running');
    onLog({ level: 'info', message: t('dashboard.outputRunning') });
    setOutput(t('dashboard.outputRunning'));
    setCalendarWriteStatus('');
    setFirstWrittenDate('');
    void agentFlowActions.runRuntimeFlow(
      {
        input: value,
        date,
        preferences,
        options: {
          forceModelKnowledge
        },
        data: {
          [date]: { plans }
        }
      },
      {
        onFinal: (content) => setOutput(content || t('dashboard.outputDone')),
        onError: () => {
          onLog({ level: 'warning', message: t('dashboard.outputFallback') });
          setOutput(t('dashboard.outputFallback'));
        },
        onDone: () => {
          onAgentStatusChange('done');
          onLog({ level: 'success', message: t('dashboard.outputDone') });
        }
      }
    );
  }

  async function writeProposalToCalendar() {
    if (!latestProposal || calendarWriteLoading) return;
    setCalendarWriteLoading(true);
    setCalendarWriteStatus(t('dashboard.writingToCalendar'));
    setFirstWrittenDate('');
    try {
      const result = await onApplyRuntimeProposalToCalendar(latestProposal);
      const successful = result.created + result.updated;
      let status = '';
      if (result.failed === 0) {
        status = `${t('dashboard.calendarWriteSuccess')}: ${t('legacy.createdCount')} ${result.created}, ${t('legacy.updatedCount')} ${result.updated}, ${t('legacy.failedCount')} ${result.failed}`;
      } else if (successful > 0) {
        status = `${t('dashboard.calendarWritePartial')}, ${t('legacy.failedCount')} ${result.failed}`;
      } else {
        status = t('dashboard.calendarWriteFailed');
      }
      if (result.affectedDates.some((item) => item !== date) && status !== t('dashboard.calendarWriteFailed')) {
        status = `${status}。${t('legacy.goalTasksWrittenToOtherDates')}`;
      }
      setFirstWrittenDate(result.affectedDates[0] || date);
      setCalendarWriteStatus(status);
    } catch {
      setCalendarWriteStatus(t('dashboard.calendarWriteFailed'));
    } finally {
      setCalendarWriteLoading(false);
    }
  }

  return (
    <section className="dashboard-page">
      <div className="dashboard-hero">
        <span className="riva-eyebrow">
          <Sparkles size={16} />
          {t('dashboard.eyebrow')}
        </span>
        <h1>{t('dashboard.title')}</h1>
        <p>{t('dashboard.subtitle')}</p>
      </div>

      <div className="dashboard-grid">
        <article className="agent-workspace riva-panel">
          <div className="panel-head">
            <div>
              <span className="riva-eyebrow">{t('agent.title')}</span>
              <h2>{t('dashboard.promptLabel')}</h2>
            </div>
            <span className={`agent-state ${inspector.agentStatus}`}>{t(`agent.${inspector.agentStatus}`)}</span>
          </div>
          <div className="prompt-console">
            <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder={t('dashboard.promptPlaceholder')} />
            <div className="prompt-actions">
              <button
                type="button"
                className={`model-knowledge-toggle ${forceModelKnowledge ? 'active' : ''}`}
                onClick={() => setForceModelKnowledge((value) => !value)}
                aria-pressed={forceModelKnowledge}
              >
                <BrainCircuit size={17} />
                {t('dashboard.forceModelKnowledge')}
              </button>
              <button onClick={runAgent} disabled={!prompt.trim()}>
                <Send size={17} />
                {t('dashboard.runAgent')}
              </button>
            </div>
          </div>
          <div className="agent-output">
            <span>{t('dashboard.outputTitle')}</span>
            <p>{output || t('dashboard.outputEmpty')}</p>
          </div>
          <RuntimeProposalPreview
            proposal={latestProposal}
            isRunning={isRunning}
            status={calendarWriteStatus}
            writing={calendarWriteLoading}
            firstWrittenDate={firstWrittenDate}
            onWrite={writeProposalToCalendar}
            onViewCalendar={onViewCalendarDate}
            t={t}
          />
        </article>

        <article className="workspace-summary riva-panel">
          <div className="panel-head">
            <div>
              <span className="riva-eyebrow">{t('dashboard.cardsTitle')}</span>
              <h2>{t('dashboard.workspaceSummary')}</h2>
            </div>
          </div>
          <p>{t('dashboard.summaryBody')}</p>
          <div className="summary-memory">
            <BrainCircuit size={17} />
            <span>{preferences || t('inspector.emptyMemory')}</span>
          </div>
        </article>

        <AgentFlowTrace t={t} />

        <div className="ai-card-grid">
          <DashboardCard icon={Target} label={t('dashboard.activePlans')} value={plans.length} hint={t('dashboard.cardHintPlans')} />
          <DashboardCard icon={CheckCircle2} label={t('dashboard.completedPlans')} value={doneCount} hint={t('dashboard.cardHintPlans')} />
          <DashboardCard icon={Boxes} label={t('dashboard.pendingPlans')} value={pendingCount} hint={t('dashboard.cardHintGoals')} />
          <DashboardCard icon={Database} label={t('dashboard.knowledgeBase')} value={inspector.memory.materialCount} hint={t('dashboard.cardHintKnowledge')} />
        </div>

        <article className="tool-placeholder riva-panel">
          <span className="riva-eyebrow">{t('agent.toolCalls')}</span>
          <h2>{t('dashboard.toolPlaceholderTitle')}</h2>
          <p>{t('dashboard.toolPlaceholderBody')}</p>
        </article>
      </div>
    </section>
  );
}

export function RuntimeProposalPreview(props: {
  proposal?: RuntimePlanProposal;
  isRunning: boolean;
  status: string;
  writing: boolean;
  firstWrittenDate: string;
  onWrite: () => void;
  onViewCalendar: (date: string) => void;
  t: (key: string) => string;
}) {
  const { proposal, isRunning, status, writing, firstWrittenDate, onWrite, onViewCalendar, t } = props;
  const tasks = proposal ? proposal.structuredPlan.milestones.flatMap((milestone, milestoneIndex) => (
    milestone.tasks.map((task, taskIndex) => ({ milestone, milestoneIndex, task, taskIndex }))
  )) : [];
  const modeLabel = proposal?.mode === 'local_fallback'
    ? t('dashboard.runtimeProposalModeLocalFallback')
    : t('dashboard.runtimeProposalModeLlm');
  const qualityLabel = proposal ? runtimeProposalQualityLabel(proposal, t) : '';
  const metrics = proposal?.qualityReport?.metrics;
  const horizonDays = proposal?.planHorizon?.durationDays ?? proposal?.structuredPlan.durationDays;
  const coverageRange = proposal ? runtimeProposalCoverageRange(proposal) : '';
  const sourceLabel = proposal ? runtimeProposalSourceLabel(proposal.sourceType, t) : '';
  const notice = proposal ? runtimeProposalNotice(proposal, t) : '';

  return (
    <div className="runtime-proposal-preview">
      <div className="runtime-proposal-head">
        <div>
          <span className="riva-eyebrow">{t('dashboard.runtimeProposalEyebrow')}</span>
          <h3>{t('dashboard.runtimeProposalTitle')}</h3>
        </div>
        {proposal ? <em>{t('dashboard.runtimeProposalTaskCount')}: {tasks.length}</em> : null}
      </div>
      {!proposal ? (
        <p className="runtime-proposal-empty">
          {isRunning ? t('dashboard.runtimeProposalWaiting') : t('dashboard.runtimeProposalEmpty')}
        </p>
      ) : (
        <>
          <p className="runtime-proposal-summary">{proposal.structuredPlan.goalDescription || proposal.goal}</p>
          <div className="runtime-proposal-list">
            {tasks.map(({ milestone, milestoneIndex, task, taskIndex }) => (
              <RuntimeProposalTask
                key={`${milestoneIndex}-${taskIndex}-${task.title}`}
                milestoneTitle={milestone.title}
                task={task}
                t={t}
              />
            ))}
          </div>
          <div className="runtime-proposal-meta">
            <span>{t('dashboard.runtimeProposalSources')}: {proposal.sources.length}</span>
            <span>{t('dashboard.runtimeProposalMode')}: {modeLabel}</span>
            <span>{t('dashboard.runtimeProposalQuality')}: {qualityLabel}</span>
            {horizonDays ? <span>{t('dashboard.runtimeProposalHorizon')}: {horizonDays} {t('dashboard.runtimeProposalDays')}</span> : null}
            <span>{t('dashboard.runtimeProposalTaskCount')}: {metrics?.totalTasks ?? proposal.qualityReport?.totalTasks ?? tasks.length}</span>
            {(metrics?.coveredWeekCount ?? proposal.qualityReport?.coveredWeekCount) !== undefined ? (
              <span>{t('dashboard.runtimeProposalCoveredWeeks')}: {metrics?.coveredWeekCount ?? proposal.qualityReport?.coveredWeekCount}</span>
            ) : null}
            {(metrics?.dateSpanDays ?? proposal.qualityReport?.dateSpanDays) !== undefined ? (
              <span>{t('dashboard.runtimeProposalDateSpan')}: {metrics?.dateSpanDays ?? proposal.qualityReport?.dateSpanDays} {t('dashboard.runtimeProposalDays')}</span>
            ) : null}
            {sourceLabel ? <span>{t('dashboard.runtimeProposalSourceType')}: {sourceLabel}</span> : null}
            {coverageRange ? <span>{t('dashboard.runtimeProposalCoverage')}: {coverageRange}</span> : null}
          </div>
          {notice ? <p className="runtime-proposal-summary">{notice}</p> : null}
        </>
      )}
      {status && <p className={`inline-status ${writing ? 'calendar-write-status' : ''}`}>{status}</p>}
      <div className="runtime-proposal-actions">
        <button
          type="button"
          className={`apply-button ${writing ? 'is-writing' : ''}`}
          onClick={onWrite}
          disabled={!proposal || isRunning || writing}
        >
          <CalendarPlus size={17} />
          {writing ? t('dashboard.writingToCalendar') : t('legacy.writeToCalendar')}
        </button>
        {firstWrittenDate ? (
          <button type="button" className="section-action-button" onClick={() => onViewCalendar(firstWrittenDate)}>
            <ExternalLink size={15} />
            {t('dashboard.viewCalendar')}
          </button>
        ) : null}
      </div>
    </div>
  );
}

function runtimeProposalQualityLabel(proposal: RuntimePlanProposal, t: (key: string) => string): string {
  if (proposal.qualityStatus === 'repaired') return t('dashboard.runtimeProposalQualityRepaired');
  if (proposal.qualityStatus === 'local_fallback' || proposal.mode === 'local_fallback') return t('dashboard.runtimeProposalQualityLocalFallback');
  return t('dashboard.runtimeProposalQualityPassed');
}

function runtimeProposalSourceLabel(sourceType: RuntimePlanProposal['sourceType'], t: (key: string) => string): string {
  if (sourceType === 'local_context') return t('dashboard.runtimeProposalSourceLocal');
  if (sourceType === 'local_fallback') return t('dashboard.runtimeProposalSourceFallback');
  if (sourceType === 'insufficient_context') return t('dashboard.runtimeProposalSourceInsufficient');
  if (sourceType === 'model_knowledge') return t('dashboard.runtimeProposalSourceModel');
  return '';
}

function runtimeProposalNotice(proposal: RuntimePlanProposal, t: (key: string) => string): string {
  if (proposal.sourceType === 'insufficient_context') return t('dashboard.runtimeProposalNoticeInsufficient');
  if (proposal.qualityStatus === 'repaired') return t('dashboard.runtimeProposalNoticeRepaired');
  if (proposal.qualityStatus === 'local_fallback' || proposal.mode === 'local_fallback') return t('dashboard.runtimeProposalNoticeFallback');
  return '';
}

function runtimeProposalCoverageRange(proposal: RuntimePlanProposal): string {
  if (proposal.planHorizon?.startDate && proposal.planHorizon.endDate) {
    return `${proposal.planHorizon.startDate} - ${proposal.planHorizon.endDate}`;
  }
  const dates = proposal.structuredPlan.milestones
    .flatMap((milestone) => milestone.tasks.map((task) => task.dueDate))
    .filter((value): value is string => Boolean(value));
  if (!dates.length) return '';
  const sorted = [...dates].sort();
  return `${sorted[0]} - ${sorted[sorted.length - 1]}`;
}

function RuntimeProposalTask(props: { milestoneTitle: string; task: GoalPlanTask; t: (key: string) => string }) {
  const { milestoneTitle, task, t } = props;
  return (
    <article className="runtime-proposal-task">
      <div>
        <span>{milestoneTitle}</span>
        <strong>{task.title}</strong>
        {task.description ? <p>{task.description}</p> : null}
      </div>
      <time>{task.dueDate || t('dashboard.noDueDate')}</time>
      <em>{task.estimatedMinutes || 0} min</em>
    </article>
  );
}

function DashboardCard(props: { icon: typeof Target; label: string; value: number; hint: string }) {
  const Icon = props.icon;
  return (
    <article className="ai-card">
      <Icon size={18} />
      <strong>{props.value}</strong>
      <span>{props.label}</span>
      <p>{props.hint}</p>
    </article>
  );
}
