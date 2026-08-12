import { useSyncExternalStore } from 'react';
import {
  continueLearningIntake,
  createLearningIntake,
  fetchLearningRun,
  fetchLearningRunResult,
  isLearningBackendUnavailable,
  isLearningRuntimeUnavailable,
  registerLearningTranscript,
  resumeLearningEvidence,
  revokeLearningTranscript,
  streamLearningRunEvents,
  supplementLearningIntake,
  type LearningEventHandlers
} from '../api/learningApi';
import type {
  LearningIntakeCreateRequest,
  LearningIntakeResponse,
  LearningIntakeSupplementRequest,
  LearningProgressEvent,
  LearningResourceDraft,
  LearningRunResult,
  LearningRunState,
  LearningTranscriptRegistrationRequest,
  LearningTranscriptRevokeResponse,
  LearningTranscriptSourceSummary,
  LearningWorkspaceState
} from '../types';

export interface LearningApiPort {
  createIntake: (payload: LearningIntakeCreateRequest) => Promise<LearningIntakeResponse>;
  supplementIntake: (intakeId: string, payload: LearningIntakeSupplementRequest) => Promise<LearningIntakeResponse>;
  continueIntake: (intakeId: string) => Promise<LearningIntakeResponse>;
  registerTranscript: (payload: LearningTranscriptRegistrationRequest) => Promise<LearningTranscriptSourceSummary>;
  revokeTranscript: (sourceId: string) => Promise<LearningTranscriptRevokeResponse>;
  getRun: (runId: string) => Promise<LearningRunState>;
  getResult: (runId: string) => Promise<LearningRunResult>;
  streamEvents: (runId: string, handlers: LearningEventHandlers, after?: number) => () => void;
  resumeEvidence: (runId: string) => Promise<LearningRunState>;
  runtimeUnavailable: (error: unknown) => boolean;
  backendUnavailable: (error: unknown) => boolean;
}

const defaultApi: LearningApiPort = {
  createIntake: createLearningIntake,
  supplementIntake: supplementLearningIntake,
  continueIntake: continueLearningIntake,
  registerTranscript: registerLearningTranscript,
  revokeTranscript: revokeLearningTranscript,
  getRun: fetchLearningRun,
  getResult: fetchLearningRunResult,
  streamEvents: (runId, handlers, after) => streamLearningRunEvents(
    runId,
    handlers,
    undefined,
    after,
  ),
  resumeEvidence: resumeLearningEvidence,
  runtimeUnavailable: isLearningRuntimeUnavailable,
  backendUnavailable: isLearningBackendUnavailable
};

const emptyResourceDraft = (): LearningResourceDraft => ({
  mode: 'automatic',
  videoUrl: '',
  subtitleFormat: 'vtt',
  subtitleLanguage: 'zh-CN',
  subtitleFileName: '',
  inputSource: 'none'
});

const emptyState = (): LearningWorkspaceState => ({
  intakeId: null,
  scope: null,
  scopeReview: null,
  intakeStatus: 'idle',
  supplementDraft: '',
  runId: null,
  status: 'idle',
  currentStage: '',
  completedStages: [],
  events: [],
  plan: null,
  qualityReport: null,
  evidenceGraph: null,
  intervention: null,
  originalInput: '',
  preferredLanguage: 'zh-CN',
  scopeAnalysisFailed: false,
  failureKind: null,
  resourceDraft: emptyResourceDraft(),
  registeredTranscript: null,
  resourceStatus: 'idle',
  resourceError: null
});

function failureKindFor(status: LearningRunState | null, runtimeUnavailable = false) {
  if (runtimeUnavailable) return 'provider_unavailable' as const;
  const stage = `${status?.error?.stage || status?.current_stage || ''}`.toLowerCase();
  const type = `${status?.error?.error_type || ''}`.toLowerCase();
  const rule = `${status?.error?.validator_rule || ''}`.toLowerCase();
  if (stage.includes('quality') || type.includes('quality')) return 'quality_failed' as const;
  if (stage.includes('evidence') || type.includes('evidence') || type.includes('transcript') || type.includes('coverage') || rule.includes('coverage')) return 'evidence_missing' as const;
  if (type.includes('provider') || type.includes('model') || type.includes('runtimeunavailable')) return 'provider_unavailable' as const;
  return 'run_failed' as const;
}

export function createLearningStore(api: LearningApiPort = defaultApi) {
  const listeners = new Set<() => void>();
  let state = emptyState();
  let stopEvents: (() => void) | null = null;
  let generation = 0;

  const emit = () => listeners.forEach((listener) => listener());
  const update = (patch: Partial<LearningWorkspaceState>) => {
    state = { ...state, ...patch };
    emit();
  };
  const active = (runId: string, token: number) => state.runId === runId && generation === token;

  async function loadTerminal(runId: string, token: number, knownStatus?: LearningRunState) {
    try {
      const runStatus = knownStatus ?? await api.getRun(runId);
      if (!active(runId, token)) return;
      if (runStatus.status === 'completed') {
        const result = await api.getResult(runId);
        if (!active(runId, token)) return;
        update({
          status: result.learning_quality_report.passed ? 'completed' : 'failed',
          intakeStatus: result.learning_quality_report.passed ? 'completed' : 'failed',
          currentStage: runStatus.current_stage,
          completedStages: runStatus.completed_stages,
          plan: result.learning_content_plan,
          qualityReport: result.learning_quality_report,
          evidenceGraph: result.evidence_graph,
          failureKind: result.learning_quality_report.passed ? null : 'quality_failed'
        });
        return;
      }
      if (runStatus.status === 'failed') {
        update({
          status: 'failed',
          intakeStatus: 'failed',
          currentStage: runStatus.current_stage,
          completedStages: runStatus.completed_stages,
          failureKind: failureKindFor(runStatus)
        });
        return;
      }
      if (runStatus.status === 'waiting_evidence') {
        update({
          status: 'waiting_evidence',
          intakeStatus: 'waiting_evidence',
          currentStage: runStatus.current_stage,
          completedStages: runStatus.completed_stages,
          intervention: runStatus.intervention ?? null,
          failureKind: null,
        });
      }
    } catch (error) {
      if (active(runId, token)) {
        update({
          status: 'failed',
          intakeStatus: 'failed',
          failureKind: api.backendUnavailable(error)
            ? 'backend_unavailable'
            : api.runtimeUnavailable(error) ? 'provider_unavailable' : 'run_failed'
        });
      }
    }
  }

  function receiveEvent(runId: string, token: number, event: LearningProgressEvent) {
    if (!active(runId, token)) return;
    if (event.stream_id && state.events.some((item) => item.stream_id === event.stream_id)) return;
    const completedStages = event.event_type === 'stage_completed'
      ? [...new Set([...state.completedStages, event.stage])]
      : state.completedStages;
    const nextStatus = event.event_type === 'session_failed'
      ? 'failed'
      : event.event_type === 'session_completed'
        ? 'completed'
        : event.event_type === 'session_waiting_evidence'
          ? 'waiting_evidence'
          : 'running';
    update({
      events: [...state.events, event],
      currentStage: event.stage,
      completedStages,
      status: nextStatus,
      intakeStatus: nextStatus
    });
    if (
      event.event_type === 'session_completed'
      || event.event_type === 'session_failed'
      || event.event_type === 'session_waiting_evidence'
    ) {
      stopEvents?.();
      stopEvents = null;
      void loadTerminal(runId, token);
    }
  }

  async function connectRun(runId: string, token: number, resumingEvidence = false) {
    if (generation !== token) return;
    update({
      runId,
      status: 'running',
      intakeStatus: 'running',
      currentStage: resumingEvidence ? 'evidence_generation' : 'scope',
      intervention: null,
    });
    stopEvents?.();
    stopEvents = api.streamEvents(runId, {
      onEvent: (event) => receiveEvent(runId, token, event),
      onError: () => {
        if (!active(runId, token)) return;
        void api.getRun(runId).then((runStatus) => {
          if (!active(runId, token)) return;
          if (
            runStatus.status === 'completed'
            || runStatus.status === 'failed'
            || runStatus.status === 'waiting_evidence'
          ) {
            stopEvents?.();
            stopEvents = null;
            void loadTerminal(runId, token, runStatus);
          }
        }).catch(() => {
          stopEvents?.();
          stopEvents = null;
          update({ status: 'failed', intakeStatus: 'failed', failureKind: 'backend_unavailable' });
        });
      }
    }, state.events.length);
    try {
      const initial = await api.getRun(runId);
      if (!active(runId, token)) return;
      if (
        initial.status === 'completed'
        || initial.status === 'failed'
        || (initial.status === 'waiting_evidence' && !resumingEvidence)
      ) {
        await loadTerminal(runId, token, initial);
      } else if (!state.events.length) {
        update({
          status: 'running',
          intakeStatus: 'running',
          currentStage: initial.current_stage,
          completedStages: initial.completed_stages
        });
      }
    } catch (error) {
      if (active(runId, token) && api.backendUnavailable(error)) {
        update({ status: 'failed', intakeStatus: 'failed', failureKind: 'backend_unavailable' });
      }
    }
  }

  async function applyIntake(response: LearningIntakeResponse, token: number) {
    if (generation !== token) return null;
    update({
      intakeId: response.intakeId,
      scope: response.scope,
      scopeReview: response.review,
      intakeStatus: response.runId ? 'starting_run' : 'waiting_scope_review',
      status: response.runId ? 'starting_run' : 'waiting_scope_review',
      runId: response.runId,
      scopeAnalysisFailed: false,
      supplementDraft: ''
    });
    if (response.runId) {
      await connectRun(response.runId, token);
    }
    return response.runId;
  }

  async function startIntake(message: string, preferredLanguage: string): Promise<string | null> {
    const normalized = message.trim();
    if (!normalized || state.status === 'analyzing_scope' || state.status === 'running') return null;
    stopEvents?.();
    stopEvents = null;
    const token = ++generation;
    state = {
      ...emptyState(),
      status: 'analyzing_scope',
      intakeStatus: 'analyzing_scope',
      originalInput: normalized,
      preferredLanguage
    };
    emit();
    try {
      const response = await api.createIntake({ message: normalized, preferredLanguage });
      return await applyIntake(response, token);
    } catch {
      if (generation === token) {
        update({ status: 'failed', intakeStatus: 'failed', scopeAnalysisFailed: true });
      }
      return null;
    }
  }

  async function supplement(message = state.supplementDraft): Promise<string | null> {
    const normalized = message.trim();
    const intakeId = state.intakeId;
    if (!intakeId || !normalized || state.status === 'analyzing_scope' || state.status === 'running') return null;
    const token = generation;
    update({
      status: 'analyzing_scope',
      intakeStatus: 'analyzing_scope',
      supplementDraft: normalized,
      scopeAnalysisFailed: false
    });
    try {
      const response = await api.supplementIntake(intakeId, {
        message: normalized,
        preferredLanguage: state.preferredLanguage,
        deferAutoStart: state.resourceDraft.mode === 'specified'
      });
      return await applyIntake(response, token);
    } catch {
      if (generation === token) {
        update({
          status: 'waiting_scope_review',
          intakeStatus: 'waiting_scope_review',
          scopeAnalysisFailed: true,
          supplementDraft: normalized
        });
      }
      return null;
    }
  }

  async function continueWithCurrentScope(): Promise<string | null> {
    const intakeId = state.intakeId;
    if (
      !intakeId
      || state.status === 'starting_run'
      || state.status === 'running'
      || state.resourceStatus === 'registering'
      || state.resourceStatus === 'validating'
    ) return null;
    const token = generation;
    update({ status: 'starting_run', intakeStatus: 'starting_run', scopeAnalysisFailed: false });
    try {
      const response = await api.continueIntake(intakeId);
      return await applyIntake(response, token);
    } catch (error) {
      if (generation === token) {
        update({
          status: 'waiting_scope_review',
          intakeStatus: 'waiting_scope_review',
          failureKind: api.backendUnavailable(error)
            ? 'backend_unavailable'
            : api.runtimeUnavailable(error) ? 'provider_unavailable' : null
        });
      }
      return null;
    }
  }

  function setSupplementDraft(supplementDraft: string) {
    update({ supplementDraft });
  }

  function setResourceDraft(patch: Partial<LearningResourceDraft>) {
    update({
      resourceDraft: { ...state.resourceDraft, ...patch },
      resourceError: null
    });
  }

  function setResourceMode(mode: LearningResourceDraft['mode']) {
    if (mode === 'specified') {
      update({
        resourceDraft: { ...state.resourceDraft, mode },
        resourceStatus: state.resourceStatus === 'revoked' ? 'revoked' : 'idle',
        resourceError: null
      });
      return;
    }
    const shouldResume = Boolean(
      state.scopeReview?.readyForPlanning
      && state.intakeId
      && !state.runId
    );
    update({
      resourceDraft: { ...emptyResourceDraft(), mode: 'automatic' },
      resourceStatus: 'idle',
      resourceError: null
    });
    if (shouldResume) void continueWithCurrentScope();
  }

  async function patchResourceUrl(videoUrl: string, token: number) {
    const intakeId = state.intakeId;
    if (!intakeId) return null;
    const response = await api.supplementIntake(intakeId, {
      message: '',
      preferredLanguage: state.preferredLanguage,
      resourceUrls: [videoUrl],
      deferAutoStart: true
    });
    await applyIntake(response, token);
    return response.scope.resourcePreference.userSuppliedUrls.at(-1) ?? videoUrl;
  }

  async function bindVideoOnly(videoUrl = state.resourceDraft.videoUrl): Promise<boolean> {
    if (!state.intakeId || !videoUrl.trim() || state.resourceStatus === 'validating') return false;
    const token = generation;
    update({ resourceStatus: 'validating', resourceError: null });
    try {
      const canonicalUrl = await patchResourceUrl(videoUrl.trim(), token);
      if (!canonicalUrl || generation !== token) return false;
      update({
        resourceDraft: {
          ...state.resourceDraft,
          mode: 'specified',
          videoUrl: canonicalUrl
        },
        resourceStatus: 'video_only',
        registeredTranscript: null,
        resourceError: null
      });
      return true;
    } catch {
      if (generation === token) {
        update({ resourceStatus: 'failed', resourceError: 'binding_failed' });
      }
      return false;
    }
  }

  async function registerTranscriptForIntake(
    payload: LearningTranscriptRegistrationRequest,
  ): Promise<boolean> {
    if (!state.intakeId || state.resourceStatus === 'registering') return false;
    const token = generation;
    update({ resourceStatus: 'registering', resourceError: null });
    let summary: LearningTranscriptSourceSummary;
    try {
      summary = await api.registerTranscript(payload);
    } catch {
      if (generation === token) {
        update({ resourceStatus: 'failed', resourceError: 'registration_failed' });
      }
      return false;
    }
    if (generation !== token) return false;
    update({ registeredTranscript: summary });
    if (state.status === 'waiting_evidence') {
      update({
        resourceDraft: {
          ...state.resourceDraft,
          mode: 'specified',
          videoUrl: summary.canonical_url,
          subtitleFormat: summary.source_format,
          subtitleLanguage: summary.language,
          subtitleFileName: summary.source_name,
          inputSource: 'none'
        },
        registeredTranscript: summary,
        resourceStatus: 'registered',
        resourceError: null
      });
      return true;
    }
    try {
      const canonicalUrl = await patchResourceUrl(summary.canonical_url, token);
      if (!canonicalUrl || generation !== token) return false;
      update({
        resourceDraft: {
          ...state.resourceDraft,
          mode: 'specified',
          videoUrl: canonicalUrl,
          subtitleFormat: summary.source_format,
          subtitleLanguage: summary.language,
          subtitleFileName: summary.source_name,
          inputSource: 'none'
        },
        registeredTranscript: summary,
        resourceStatus: 'registered',
        resourceError: null
      });
      return true;
    } catch {
      if (generation === token) {
        update({ resourceStatus: 'registered', resourceError: 'binding_failed' });
      }
      return false;
    }
  }

  async function revokeTranscriptForIntake(): Promise<boolean> {
    const summary = state.registeredTranscript;
    if (!summary || state.resourceStatus === 'registering') return false;
    const token = generation;
    update({ resourceStatus: 'validating', resourceError: null });
    try {
      await api.revokeTranscript(summary.source_id);
      if (generation !== token) return false;
      update({
        registeredTranscript: null,
        resourceStatus: 'revoked',
        resourceError: null
      });
      return true;
    } catch {
      if (generation === token) {
        update({ resourceStatus: 'registered', resourceError: 'revoke_failed' });
      }
      return false;
    }
  }

  async function resumeEvidence(): Promise<boolean> {
    const runId = state.runId;
    if (!runId || state.status !== 'waiting_evidence') return false;
    const token = generation;
    update({
      status: 'running',
      intakeStatus: 'running',
      currentStage: 'evidence_generation',
      intervention: null,
      failureKind: null,
    });
    try {
      await api.resumeEvidence(runId);
      if (!active(runId, token)) return false;
      await connectRun(runId, token, true);
      return true;
    } catch (error) {
      if (active(runId, token)) {
        const current = await api.getRun(runId).catch(() => null);
        if (current?.status === 'waiting_evidence') {
          await loadTerminal(runId, token, current);
        } else {
          update({
            status: 'failed',
            intakeStatus: 'failed',
            failureKind: api.backendUnavailable(error)
              ? 'backend_unavailable'
              : api.runtimeUnavailable(error) ? 'provider_unavailable' : 'run_failed',
          });
        }
      }
      return false;
    }
  }

  function reset() {
    generation += 1;
    stopEvents?.();
    stopEvents = null;
    state = emptyState();
    emit();
  }

  return {
    getState: () => state,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    actions: {
      startIntake,
      supplement,
      continueWithCurrentScope,
      setSupplementDraft,
      setResourceDraft,
      setResourceMode,
      bindVideoOnly,
      registerTranscript: registerTranscriptForIntake,
      revokeTranscript: revokeTranscriptForIntake,
      resumeEvidence,
      reset
    }
  };
}

export const learningStore = createLearningStore();
export function useLearningStore() {
  return useSyncExternalStore(learningStore.subscribe, learningStore.getState, learningStore.getState);
}
export const learningStoreActions = learningStore.actions;
