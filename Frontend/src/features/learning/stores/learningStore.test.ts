import { describe, expect, it, vi } from 'vitest';
import type { LearningEventHandlers } from '../api/learningApi';
import type { LearningRunResult, LearningRunState } from '../types';
import { createLearningStore, type LearningApiPort } from './learningStore';

const runningState: LearningRunState = {
  status: 'running', current_stage: 'knowledge_generating', completed_stages: ['understanding'], error: null
};

const completedState: LearningRunState = {
  status: 'completed', current_stage: 'completed',
  completed_stages: ['understanding', 'knowledge_generating', 'evidence_generating', 'content_selecting', 'quality_checking'],
  error: null
};

const result: LearningRunResult = {
  learning_content_plan: {
    artifactId: 'plan-1', version: 1, totalDurationSeconds: 600, evidenceGaps: [],
    items: [{
      knowledgeId: 'knowledge-routing', knowledgeName: 'Routing', knowledgeExplanation: 'Route requests.',
      whyRequired: 'CRUD needs routes.', uncoveredReason: null,
      recommendedContent: [{
        selectionId: 'selection-1', resourceId: 'video-1', segmentId: 'segment-1', videoTitle: 'FastAPI Routing',
        segmentSummary: 'GET and POST routes', durationSeconds: 600, recommendationReason: 'Direct evidence.'
      }]
    }]
  },
  learning_quality_report: {
    artifactId: 'quality-1', version: 1, hardRulesPassed: true, passed: true, score: 100,
    qualityChecks: [{ rule: 'knowledge_coverage', passed: true, evidence: ['coverage-1'] }], issues: [], remainingGaps: []
  },
  evidence_graph: {
    artifactId: 'evidence-1', version: 1,
    resources: [{
      id: 'video-1', provider: 'bilibili', externalId: 'BV1', canonicalUrl: 'https://www.bilibili.com/video/BV1',
      title: 'FastAPI Routing', author: 'Teacher', language: 'zh-CN', durationSeconds: 1200, availability: 'available'
    }],
    segments: [{
      id: 'segment-1', resourceId: 'video-1', startSeconds: 60, endSeconds: 660,
      contentSummary: 'GET and POST routes', topics: ['routing'], evidenceRefs: ['evidence-span-1']
    }],
    evidence: [{
      id: 'evidence-span-1', resourceId: 'video-1', segmentId: 'segment-1', kind: 'transcript_span',
      supportedClaim: 'Explains routing', verificationStatus: 'verified'
    }]
  }
};

function fakeApi() {
  let handlers: LearningEventHandlers | null = null;
  let status: LearningRunState = runningState;
  const api: LearningApiPort = {
    createRun: vi.fn().mockResolvedValue({ run_id: 'learning-session-1' }),
    getRun: vi.fn().mockImplementation(async () => status),
    getResult: vi.fn().mockResolvedValue(result),
    streamEvents: vi.fn().mockImplementation((_runId, nextHandlers) => {
      handlers = nextHandlers;
      return vi.fn();
    }),
    runtimeUnavailable: vi.fn().mockReturnValue(false),
    backendUnavailable: vi.fn().mockReturnValue(false)
  };
  return {
    api,
    emit(event: Parameters<LearningEventHandlers['onEvent']>[0]) { handlers?.onEvent(event); },
    setStatus(next: LearningRunState) { status = next; }
  };
}

const input = {
  goal: 'Learn FastAPI', targetResult: 'Build CRUD', currentLevel: 'Python basics',
  targetMinutes: 120, preferredLanguage: 'en-US', constraints: ['No deployment']
};

describe('Learning Store', () => {
  it('creates a run and stores the formal run id', async () => {
    const fake = fakeApi();
    const store = createLearningStore(fake.api);

    const runId = await store.actions.start(input);

    expect(runId).toBe('learning-session-1');
    expect(fake.api.createRun).toHaveBeenCalledOnce();
    expect(vi.mocked(fake.api.createRun).mock.calls[0][0]).toMatchObject({
      goal: 'Learn FastAPI', constraints: ['No deployment']
    });
    expect(store.getState()).toMatchObject({ runId, status: 'running', currentStage: 'knowledge_generating' });
  });

  it('applies SSE progress and loads the completed result', async () => {
    const fake = fakeApi();
    const store = createLearningStore(fake.api);
    await store.actions.start(input);

    fake.emit({
      event_type: 'stage_completed', stage: 'knowledge_generating', status: 'completed',
      message: 'done', timestamp: '2026-08-12T08:00:00Z'
    });
    fake.setStatus(completedState);
    fake.emit({
      event_type: 'session_completed', stage: 'completed', status: 'completed',
      message: 'complete', timestamp: '2026-08-12T08:00:01Z'
    });
    await vi.waitFor(() => expect(store.getState().plan?.artifactId).toBe('plan-1'));

    expect(store.getState()).toMatchObject({ status: 'completed', currentStage: 'completed', failureKind: null });
    expect(store.getState().events).toHaveLength(2);
    expect(store.getState().qualityReport?.passed).toBe(true);
    expect(store.getState().evidenceGraph?.segments[0].startSeconds).toBe(60);
  });

  it('maps an evidence failure to a user-safe failed state', async () => {
    const fake = fakeApi();
    const store = createLearningStore(fake.api);
    await store.actions.start(input);
    fake.setStatus({
      status: 'failed', current_stage: 'failed', completed_stages: ['understanding', 'knowledge_generating'],
      error: { stage: 'evidence_generating', error_type: 'EvidenceUnavailable', message: 'internal detail', validator_rule: '', field_path: '' }
    });
    fake.emit({
      event_type: 'session_failed', stage: 'failed', status: 'failed',
      message: 'failed', timestamp: '2026-08-12T08:00:01Z'
    });
    await vi.waitFor(() => expect(store.getState().failureKind).toBe('evidence_missing'));

    expect(store.getState().status).toBe('failed');
  });
});
