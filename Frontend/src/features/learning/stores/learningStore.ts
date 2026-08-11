import { useSyncExternalStore } from 'react';
import {
  createLearningRun,
  fetchLearningRun,
  fetchLearningRunResult,
  isLearningBackendUnavailable,
  isLearningRuntimeUnavailable,
  streamLearningRunEvents,
  type LearningEventHandlers
} from '../api/learningApi';
import type {
  LearningFailureKind,
  LearningProgressEvent,
  LearningRunCreateRequest,
  LearningRunResult,
  LearningRunState,
  LearningWorkspaceInput,
  LearningWorkspaceState
} from '../types';

export interface LearningApiPort {
  createRun(payload: LearningRunCreateRequest): Promise<{ run_id: string }>;
  getRun(runId: string): Promise<LearningRunState>;
  getResult(runId: string): Promise<LearningRunResult>;
  streamEvents(runId: string, handlers: LearningEventHandlers): () => void;
  runtimeUnavailable(error: unknown): boolean;
  backendUnavailable(error: unknown): boolean;
}

const defaultApi: LearningApiPort = {
  createRun: createLearningRun,
  getRun: fetchLearningRun,
  getResult: fetchLearningRunResult,
  streamEvents: streamLearningRunEvents,
  runtimeUnavailable: isLearningRuntimeUnavailable,
  backendUnavailable: isLearningBackendUnavailable
};

const emptyState = (): LearningWorkspaceState => ({
  runId: null,
  status: 'idle',
  currentStage: 'created',
  completedStages: [],
  events: [],
  plan: null,
  qualityReport: null,
  evidenceGraph: null,
  submittedInput: null,
  failureKind: null
});

export function learningRequest(input: LearningWorkspaceInput): LearningRunCreateRequest {
  return {
    goal: input.goal.trim(),
    preferences: {
      target_result: input.targetResult.trim(),
      current_level: {
        summary: input.currentLevel.trim(),
        knownSkills: [],
        knownTechnologies: [],
        uncertainAreas: [],
        sourceRefs: ['learning-workspace:current-level']
      },
      content_budget: input.targetMinutes ? { targetTotalMinutes: input.targetMinutes } : {},
      language_preference: {
        preferredLanguages: [input.preferredLanguage],
        acceptableLanguages: [],
        subtitlesAcceptable: true
      },
      resourcePreference: {
        preferredPlatforms: ['bilibili'],
        excludedPlatforms: [],
        preferredStyles: ['hands_on', 'project_based'],
        freeOnly: true,
        userSuppliedUrls: []
      },
      confirmed: true
    },
    constraints: input.constraints.map((item) => item.trim()).filter(Boolean)
  };
}

function failureKindFor(status: LearningRunState | null, runtimeUnavailable = false): LearningFailureKind {
  if (runtimeUnavailable) return 'provider_unavailable';
  const stage = `${status?.error?.stage || status?.current_stage || ''}`.toLowerCase();
  const type = `${status?.error?.error_type || ''}`.toLowerCase();
  const rule = `${status?.error?.validator_rule || ''}`.toLowerCase();
  if (stage.includes('quality') || type.includes('quality')) return 'quality_failed';
  if (stage.includes('evidence') || type.includes('evidence') || type.includes('transcript') || type.includes('coverage') || rule.includes('coverage')) return 'evidence_missing';
  if (type.includes('provider') || type.includes('model') || type.includes('runtimeunavailable')) return 'provider_unavailable';
  return 'run_failed';
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
      const status = knownStatus ?? await api.getRun(runId);
      if (!active(runId, token)) return;
      if (status.status === 'completed') {
        const result = await api.getResult(runId);
        if (!active(runId, token)) return;
        update({
          status: result.learning_quality_report.passed ? 'completed' : 'failed',
          currentStage: status.current_stage,
          completedStages: status.completed_stages,
          plan: result.learning_content_plan,
          qualityReport: result.learning_quality_report,
          evidenceGraph: result.evidence_graph,
          failureKind: result.learning_quality_report.passed ? null : 'quality_failed'
        });
        return;
      }
      if (status.status === 'failed') {
        update({
          status: 'failed',
          currentStage: status.current_stage,
          completedStages: status.completed_stages,
          failureKind: failureKindFor(status)
        });
      }
    } catch (error) {
      if (active(runId, token)) {
        update({
          status: 'failed',
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
    update({
      events: [...state.events, event],
      currentStage: event.stage,
      completedStages,
      status: event.event_type === 'session_failed' ? 'failed' : event.event_type === 'session_completed' ? 'completed' : 'running'
    });
    if (event.event_type === 'session_completed' || event.event_type === 'session_failed') {
      stopEvents?.();
      stopEvents = null;
      void loadTerminal(runId, token);
    }
  }

  async function start(input: LearningWorkspaceInput): Promise<string | null> {
    const goal = input.goal.trim();
    if (!goal || state.status === 'creating' || state.status === 'running') return null;
    stopEvents?.();
    stopEvents = null;
    const token = ++generation;
    state = { ...emptyState(), status: 'creating', submittedInput: { ...input, goal } };
    emit();
    try {
      const response = await api.createRun(learningRequest({ ...input, goal }));
      if (generation !== token) return null;
      const runId = response.run_id;
      update({ runId, status: 'running' });
      stopEvents = api.streamEvents(runId, {
        onEvent: (event) => receiveEvent(runId, token, event),
        onError: () => {
          if (!active(runId, token)) return;
          void api.getRun(runId).then((status) => {
            if (!active(runId, token)) return;
            if (status.status === 'completed' || status.status === 'failed') {
              stopEvents?.();
              stopEvents = null;
              void loadTerminal(runId, token, status);
            }
          }).catch(() => {
            stopEvents?.();
            stopEvents = null;
            update({ status: 'failed', failureKind: 'backend_unavailable' });
          });
        }
      });
      const initial = await api.getRun(runId);
      if (!active(runId, token)) return runId;
      if (initial.status === 'completed' || initial.status === 'failed') {
        await loadTerminal(runId, token, initial);
      } else if (!state.events.length) {
        update({
          status: initial.status === 'created' ? 'running' : initial.status,
          currentStage: initial.current_stage,
          completedStages: initial.completed_stages
        });
      }
      return runId;
    } catch (error) {
      if (generation === token) {
        update({
          status: 'failed',
          failureKind: api.backendUnavailable(error)
            ? 'backend_unavailable'
            : api.runtimeUnavailable(error) ? 'provider_unavailable' : 'run_failed'
        });
      }
      return null;
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
    actions: { start, reset }
  };
}

export const learningStore = createLearningStore();
export function useLearningStore() {
  return useSyncExternalStore(learningStore.subscribe, learningStore.getState, learningStore.getState);
}
export const learningStoreActions = learningStore.actions;
