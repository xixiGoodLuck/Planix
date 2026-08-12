import { describe, expect, it, vi } from 'vitest';
import type { LearningEventHandlers } from '../api/learningApi';
import type {
  LearningIntakeResponse,
  LearningRunResult,
  LearningRunState
} from '../types';
import { createLearningStore, type LearningApiPort } from './learningStore';

const runningState: LearningRunState = {
  status: 'running', current_stage: 'knowledge_generation', completed_stages: ['scope'], error: null
};

const completedState: LearningRunState = {
  status: 'completed', current_stage: 'completed',
  completed_stages: ['scope', 'knowledge_generation', 'evidence_generation', 'coverage_analysis', 'gap_completion', 'selection', 'quality'],
  error: null
};

const waitingState: LearningRunState = {
  status: 'waiting_evidence',
  current_stage: 'waiting_evidence',
  completed_stages: ['scope', 'knowledge_generation', 'evidence_generation', 'coverage_analysis', 'gap_completion'],
  error: null,
  intervention: {
    kind: 'additional_evidence_required',
    requiredGaps: [{
      knowledgeId: 'knowledge-database', knowledgeName: 'Database persistence',
      gapType: 'missing_knowledge', coverageStrength: 'MISSING',
      missingOrPartialReason: 'No verified transcript covers persistence.'
    }],
    searchedResources: ['database persistence'],
    transcriptUnavailableResources: ['video-without-transcript'],
    verifiedResources: [], verifiedSegments: [], knowledgeCoverage: [], canResume: true
  }
};

const transcriptSummary = {
  source_id: 'transcript-source-1', resource_id: 'video-1', resource_fingerprint: 'bilibili:BV1zV2QBtE39:1200',
  provider: 'bilibili', external_id: 'BV1zV2QBtE39', canonical_url: 'https://www.bilibili.com/video/BV1zV2QBtE39',
  title: 'FastAPI Routing', source_type: 'srt_vtt' as const, source_format: 'vtt' as const,
  source_name: 'routing.vtt', language: 'zh-CN', checksum_prefix: '12345678', authorization_status: 'authorized' as const,
  status: 'active' as const, segment_count: 2, start_ms: 10000, end_ms: 90000, created_at: '2026-08-12T08:00:00Z'
};

const result: LearningRunResult = {
  learning_content_plan: {
    artifactId: 'plan-1', version: 1, totalDurationSeconds: 600, evidenceGaps: [], deferredKnowledge: [],
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

const intake: LearningIntakeResponse = {
  intakeId: 'learning-intake-1', status: 'waiting_scope_review', runId: null,
  scope: {
    artifactId: 'learning-scope-1', version: 1, userGoal: 'FastAPI', targetResult: 'FastAPI', confirmed: false,
    currentLevel: { summary: '', knownSkills: [], knownTechnologies: [], uncertainAreas: [], sourceRefs: [] },
    contentBudget: {},
    languagePreference: { preferredLanguages: [], acceptableLanguages: [], subtitlesAcceptable: true },
    resourcePreference: { preferredPlatforms: [], excludedPlatforms: [], preferredStyles: [], freeOnly: null, userSuppliedUrls: [] },
    assumptions: [{ id: 'scope-default-level', statement: 'Current level is unspecified.', basis: 'Code default.', sourceRef: 'system:scope-readiness:current_level', impact: 'high' }],
    unknowns: [{ id: 'gap-level', question: 'What do you know?', whyItMatters: 'It changes the route.', impact: 'high', blocking: false, affectedFields: ['current_level'] }],
    sourceRefs: ['user:message:1']
  },
  review: {
    knownInformation: [{ field: 'user_goal', values: ['FastAPI'], sourceRefs: ['user:message:1'] }],
    recommendedGaps: [
      { id: 'gap-level', question: 'What do you know?', whyItMatters: 'It changes the route.', impact: 'high', blocking: false, affectedFields: ['current_level'] },
      { id: 'gap-budget', question: 'How much time?', whyItMatters: 'It controls duration.', impact: 'medium', blocking: false, affectedFields: ['content_budget'] }
    ],
    assumptions: [{ id: 'scope-default-level', statement: 'Current level is unspecified.', basis: 'Code default.', sourceRef: 'system:scope-readiness:current_level', impact: 'high' }],
    readyForPlanning: false, highImpactGapCount: 1, recommendationRound: 1, autoContinueReason: 'high_impact_gaps_remain'
  }
};

function fakeApi() {
  let handlers: LearningEventHandlers | null = null;
  let runStatus: LearningRunState = runningState;
  const supplemented: LearningIntakeResponse = {
    ...intake,
    scope: {
      ...intake.scope,
      version: 2,
      currentLevel: { summary: 'Python basics', knownSkills: ['Python'], knownTechnologies: ['Python'], uncertainAreas: [], sourceRefs: ['user:message:2'] }
    },
    review: {
      ...intake.review,
      knownInformation: [...intake.review.knownInformation, { field: 'current_level', values: ['Python basics'], sourceRefs: ['user:message:2'] }],
      recommendedGaps: intake.review.recommendedGaps.slice(1),
      highImpactGapCount: 0,
      recommendationRound: 2
    }
  };
  const runningIntake: LearningIntakeResponse = {
    ...supplemented, status: 'running', runId: 'learning-intake-1',
    review: { ...supplemented.review, readyForPlanning: true, recommendedGaps: [], autoContinueReason: 'scope_has_no_high_impact_gaps' }
  };
  const api: LearningApiPort = {
    createIntake: vi.fn().mockResolvedValue(intake),
    supplementIntake: vi.fn().mockResolvedValue(supplemented),
    continueIntake: vi.fn().mockResolvedValue(runningIntake),
    registerTranscript: vi.fn().mockResolvedValue(transcriptSummary),
    revokeTranscript: vi.fn().mockResolvedValue({ source_id: transcriptSummary.source_id, status: 'revoked' }),
    getRun: vi.fn().mockImplementation(async () => runStatus),
    getResult: vi.fn().mockResolvedValue(result),
    streamEvents: vi.fn().mockImplementation((_runId, nextHandlers) => {
      handlers = nextHandlers;
      return vi.fn();
    }),
    resumeEvidence: vi.fn().mockImplementation(async () => runStatus),
    runtimeUnavailable: vi.fn().mockReturnValue(false),
    backendUnavailable: vi.fn().mockReturnValue(false)
  };
  return {
    api,
    intake,
    supplemented,
    runningIntake,
    emit(event: Parameters<LearningEventHandlers['onEvent']>[0]) { handlers?.onEvent(event); },
    setStatus(next: LearningRunState) { runStatus = next; }
  };
}

describe('Progressive Learning Store', () => {
  it('starts with natural language intake and waits for one batch review', async () => {
    const fake = fakeApi();
    const store = createLearningStore(fake.api);

    await store.actions.startIntake('I want to learn FastAPI', 'en-US');

    expect(fake.api.createIntake).toHaveBeenCalledWith({ message: 'I want to learn FastAPI', preferredLanguage: 'en-US' });
    expect(store.getState()).toMatchObject({
      intakeId: 'learning-intake-1', status: 'waiting_scope_review', runId: null,
      originalInput: 'I want to learn FastAPI', scopeAnalysisFailed: false
    });
    expect(store.getState().scope?.confirmed).toBe(false);
    expect(store.getState().scopeReview?.recommendedGaps).toHaveLength(2);
  });

  it('supplements all at once and preserves only the remaining gaps', async () => {
    const fake = fakeApi();
    const store = createLearningStore(fake.api);
    await store.actions.startIntake('I want to learn FastAPI', 'en-US');
    store.actions.setSupplementDraft('I know Python.');

    await store.actions.supplement();

    expect(fake.api.supplementIntake).toHaveBeenCalledWith('learning-intake-1', {
      message: 'I know Python.', preferredLanguage: 'en-US', deferAutoStart: false
    });
    expect(store.getState().scope?.version).toBe(2);
    expect(store.getState().scopeReview?.knownInformation.some((item) => item.field === 'current_level')).toBe(true);
    expect(store.getState().scopeReview?.recommendedGaps).toHaveLength(1);
  });

  it('continues without supplements and connects the existing run SSE', async () => {
    const fake = fakeApi();
    const store = createLearningStore(fake.api);
    await store.actions.startIntake('I want to learn FastAPI', 'en-US');

    const runId = await store.actions.continueWithCurrentScope();

    expect(runId).toBe('learning-intake-1');
    expect(fake.api.continueIntake).toHaveBeenCalledWith('learning-intake-1');
    expect(fake.api.streamEvents).toHaveBeenCalledOnce();
    expect(store.getState()).toMatchObject({ runId, status: 'running' });
  });

  it('automatically enters progress when supplement response is ready', async () => {
    const fake = fakeApi();
    vi.mocked(fake.api.supplementIntake).mockResolvedValue(fake.runningIntake);
    const store = createLearningStore(fake.api);
    await store.actions.startIntake('I want to learn FastAPI', 'en-US');

    await store.actions.supplement('I know Python and want CRUD.');

    expect(store.getState()).toMatchObject({ runId: 'learning-intake-1', status: 'running' });
    expect(fake.api.streamEvents).toHaveBeenCalledOnce();
  });

  it('applies SSE progress and preserves existing result rendering data', async () => {
    const fake = fakeApi();
    const store = createLearningStore(fake.api);
    await store.actions.startIntake('I want to learn FastAPI', 'en-US');
    await store.actions.continueWithCurrentScope();
    fake.emit({
      event_type: 'stage_completed', stage: 'knowledge_generation', status: 'completed',
      message: 'done', timestamp: '2026-08-12T08:00:00Z'
    });
    fake.setStatus(completedState);
    fake.emit({
      event_type: 'session_completed', stage: 'completed', status: 'completed',
      message: 'complete', timestamp: '2026-08-12T08:00:01Z'
    });
    await vi.waitFor(() => expect(store.getState().plan?.artifactId).toBe('plan-1'));

    expect(store.getState().status).toBe('completed');
    expect(store.getState().qualityReport?.passed).toBe(true);
    expect(store.getState().evidenceGraph?.segments[0].startSeconds).toBe(60);
  });

  it('keeps waiting evidence recoverable and resumes the same SSE cursor', async () => {
    const fake = fakeApi();
    const store = createLearningStore(fake.api);
    await store.actions.startIntake('I want to learn FastAPI', 'en-US');
    await store.actions.continueWithCurrentScope();
    fake.setStatus(waitingState);
    fake.emit({
      event_type: 'session_waiting_evidence', stage: 'waiting_evidence', status: 'waiting_evidence',
      message: 'more evidence required', timestamp: '2026-08-12T08:00:01Z'
    });
    await vi.waitFor(() => expect(store.getState().intervention?.requiredGaps).toHaveLength(1));

    expect(store.getState().status).toBe('waiting_evidence');
    expect(store.getState().failureKind).toBeNull();
    expect(await store.actions.resumeEvidence()).toBe(true);
    expect(fake.api.resumeEvidence).toHaveBeenCalledWith('learning-intake-1');
    expect(vi.mocked(fake.api.streamEvents).mock.calls[1][2]).toBe(1);
    expect(store.getState()).toMatchObject({
      runId: 'learning-intake-1', status: 'running', currentStage: 'evidence_generation',
      intervention: null
    });

    fake.setStatus(completedState);
    fake.emit({
      event_type: 'session_completed', stage: 'completed', status: 'completed',
      message: 'complete', timestamp: '2026-08-12T08:00:02Z'
    });
    await vi.waitFor(() => expect(store.getState().status).toBe('completed'));
  });

  it('keeps original and supplement text after scope analysis failures', async () => {
    const fake = fakeApi();
    vi.mocked(fake.api.createIntake).mockRejectedValueOnce(new Error('invalid_model_output prompt token'));
    const store = createLearningStore(fake.api);

    await store.actions.startIntake('I want to learn FastAPI', 'en-US');

    expect(store.getState()).toMatchObject({
      status: 'failed', originalInput: 'I want to learn FastAPI', scopeAnalysisFailed: true
    });

    vi.mocked(fake.api.createIntake).mockResolvedValueOnce(fake.intake);
    await store.actions.startIntake('I want to learn FastAPI', 'en-US');
    vi.mocked(fake.api.supplementIntake).mockRejectedValueOnce(new Error('Pydantic stack trace'));
    await store.actions.supplement('I know some Python');

    expect(store.getState()).toMatchObject({
      status: 'waiting_scope_review', supplementDraft: 'I know some Python', scopeAnalysisFailed: true
    });
  });

  it('maps an evidence failure to a user-safe runtime state', async () => {
    const fake = fakeApi();
    const store = createLearningStore(fake.api);
    await store.actions.startIntake('I want to learn FastAPI', 'en-US');
    await store.actions.continueWithCurrentScope();
    fake.setStatus({
      status: 'failed', current_stage: 'failed', completed_stages: ['scope'],
      error: { stage: 'evidence_generation', error_type: 'EvidenceUnavailable', message: 'internal detail', validator_rule: '', field_path: '' }
    });
    fake.emit({
      event_type: 'session_failed', stage: 'failed', status: 'failed',
      message: 'failed', timestamp: '2026-08-12T08:00:01Z'
    });
    await vi.waitFor(() => expect(store.getState().failureKind).toBe('evidence_missing'));

    expect(store.getState().status).toBe('failed');
  });

  it('defaults to automatic resource search without transcript state', () => {
    const store = createLearningStore(fakeApi().api);

    expect(store.getState().resourceDraft).toMatchObject({ mode: 'automatic', inputSource: 'none' });
    expect(store.getState().registeredTranscript).toBeNull();
    expect(JSON.stringify(store.getState())).not.toContain('content');
  });

  it('pauses supplement auto-start while the user edits a specified resource', async () => {
    const fake = fakeApi();
    const store = createLearningStore(fake.api);
    await store.actions.startIntake('Learn FastAPI', 'en-US');
    store.actions.setResourceMode('specified');

    await store.actions.supplement('I know Python and want routing.');

    expect(fake.api.supplementIntake).toHaveBeenCalledWith('learning-intake-1', {
      message: 'I know Python and want routing.', preferredLanguage: 'en-US', deferAutoStart: true
    });
    expect(store.getState().runId).toBeNull();
  });

  it('cancelling specified resource editing resumes a ready scope', async () => {
    const fake = fakeApi();
    vi.mocked(fake.api.createIntake).mockResolvedValue({
      ...fake.intake,
      review: { ...fake.intake.review, readyForPlanning: true, highImpactGapCount: 0 }
    });
    const store = createLearningStore(fake.api);
    await store.actions.startIntake('Understand FastAPI routing', 'en-US');
    store.actions.setResourceMode('specified');

    store.actions.setResourceMode('automatic');
    await vi.waitFor(() => expect(fake.api.continueIntake).toHaveBeenCalledOnce());

    expect(store.getState().runId).toBe('learning-intake-1');
  });

  it('binds a video URL without calling transcript registration', async () => {
    const fake = fakeApi();
    const resourceResponse = {
      ...fake.supplemented,
      scope: {
        ...fake.supplemented.scope,
        resourcePreference: {
          ...fake.supplemented.scope.resourcePreference,
          userSuppliedUrls: [transcriptSummary.canonical_url]
        }
      }
    };
    vi.mocked(fake.api.supplementIntake).mockResolvedValue(resourceResponse);
    const store = createLearningStore(fake.api);
    await store.actions.startIntake('Learn FastAPI', 'en-US');
    store.actions.setResourceMode('specified');
    store.actions.setResourceDraft({ videoUrl: transcriptSummary.canonical_url });

    expect(await store.actions.bindVideoOnly()).toBe(true);

    expect(fake.api.registerTranscript).not.toHaveBeenCalled();
    expect(fake.api.supplementIntake).toHaveBeenCalledWith('learning-intake-1', {
      message: '', preferredLanguage: 'en-US', resourceUrls: [transcriptSummary.canonical_url], deferAutoStart: true
    });
    expect(store.getState()).toMatchObject({ resourceStatus: 'video_only', registeredTranscript: null });
  });

  it('registers a transcript, binds its canonical URL, and never stores raw text', async () => {
    const fake = fakeApi();
    vi.mocked(fake.api.supplementIntake).mockResolvedValue({
      ...fake.supplemented,
      scope: {
        ...fake.supplemented.scope,
        resourcePreference: {
          ...fake.supplemented.scope.resourcePreference,
          userSuppliedUrls: [transcriptSummary.canonical_url]
        }
      }
    });
    const store = createLearningStore(fake.api);
    await store.actions.startIntake('Learn FastAPI', 'en-US');
    store.actions.setResourceMode('specified');
    store.actions.setResourceDraft({
      videoUrl: transcriptSummary.canonical_url,
      subtitleFileName: 'routing.vtt',
      inputSource: 'file'
    });
    const content = 'WEBVTT\n\n00:01.000 --> 00:02.000\nPRIVATE ROUTING TRANSCRIPT';

    expect(await store.actions.registerTranscript({
      videoUrl: transcriptSummary.canonical_url,
      format: 'vtt', language: 'zh-CN', content, sourceName: 'routing.vtt'
    })).toBe(true);

    expect(store.getState().resourceStatus).toBe('registered');
    expect(store.getState().registeredTranscript?.segment_count).toBe(2);
    expect(store.getState().scope?.resourcePreference.userSuppliedUrls).toEqual([transcriptSummary.canonical_url]);
    expect(JSON.stringify(store.getState())).not.toContain('PRIVATE ROUTING TRANSCRIPT');
    expect(store.getState().resourceDraft.inputSource).toBe('none');
  });

  it('registers pasted subtitle text through the same safe transcript contract', async () => {
    const fake = fakeApi();
    vi.mocked(fake.api.supplementIntake).mockResolvedValue({
      ...fake.supplemented,
      scope: {
        ...fake.supplemented.scope,
        resourcePreference: {
          ...fake.supplemented.scope.resourcePreference,
          userSuppliedUrls: [transcriptSummary.canonical_url]
        }
      }
    });
    const store = createLearningStore(fake.api);
    await store.actions.startIntake('Learn FastAPI', 'en-US');
    store.actions.setResourceMode('specified');
    store.actions.setResourceDraft({
      videoUrl: transcriptSummary.canonical_url,
      inputSource: 'paste',
      subtitleFormat: 'vtt'
    });
    const pasted = 'WEBVTT\n\n00:01.000 --> 00:02.000\nPASTED PRIVATE ROUTING';

    await store.actions.registerTranscript({
      videoUrl: transcriptSummary.canonical_url,
      format: 'vtt', language: 'zh-CN', content: pasted, sourceName: 'transcript.vtt'
    });

    expect(fake.api.registerTranscript).toHaveBeenCalledWith(expect.objectContaining({
      content: pasted, sourceName: 'transcript.vtt'
    }));
    expect(store.getState().resourceStatus).toBe('registered');
    expect(JSON.stringify(store.getState())).not.toContain('PASTED PRIVATE ROUTING');
  });

  it('revokes transcript evidence while retaining the bound video URL', async () => {
    const fake = fakeApi();
    vi.mocked(fake.api.supplementIntake).mockResolvedValue({
      ...fake.supplemented,
      scope: {
        ...fake.supplemented.scope,
        resourcePreference: {
          ...fake.supplemented.scope.resourcePreference,
          userSuppliedUrls: [transcriptSummary.canonical_url]
        }
      }
    });
    const store = createLearningStore(fake.api);
    await store.actions.startIntake('Learn FastAPI', 'en-US');
    await store.actions.registerTranscript({
      videoUrl: transcriptSummary.canonical_url, format: 'vtt', language: 'zh-CN',
      content: 'WEBVTT\n\n00:01.000 --> 00:02.000\nRouting'
    });

    expect(await store.actions.revokeTranscript()).toBe(true);

    expect(store.getState().resourceStatus).toBe('revoked');
    expect(store.getState().registeredTranscript).toBeNull();
    expect(store.getState().scope?.resourcePreference.userSuppliedUrls).toEqual([transcriptSummary.canonical_url]);
  });

  it('keeps safe resource draft fields after registration failure for immediate retry', async () => {
    const fake = fakeApi();
    vi.mocked(fake.api.registerTranscript).mockRejectedValue(new Error('backend secret transcript body'));
    const store = createLearningStore(fake.api);
    await store.actions.startIntake('Learn FastAPI', 'en-US');
    store.actions.setResourceMode('specified');
    store.actions.setResourceDraft({
      videoUrl: transcriptSummary.canonical_url,
      subtitleFileName: 'routing.vtt',
      inputSource: 'file'
    });

    expect(await store.actions.registerTranscript({
      videoUrl: transcriptSummary.canonical_url, format: 'vtt', language: 'zh-CN',
      content: 'INVALID PRIVATE CONTENT', sourceName: 'routing.vtt'
    })).toBe(false);

    expect(store.getState()).toMatchObject({
      resourceStatus: 'failed', resourceError: 'registration_failed',
      resourceDraft: { videoUrl: transcriptSummary.canonical_url, subtitleFileName: 'routing.vtt' }
    });
    expect(JSON.stringify(store.getState())).not.toContain('INVALID PRIVATE CONTENT');
    expect(JSON.stringify(store.getState())).not.toContain('backend secret');
  });

  it('reset clears all safe resource state', async () => {
    const store = createLearningStore(fakeApi().api);
    await store.actions.startIntake('Learn FastAPI', 'en-US');
    store.actions.setResourceMode('specified');
    store.actions.setResourceDraft({ videoUrl: transcriptSummary.canonical_url, subtitleFileName: 'routing.vtt' });

    store.actions.reset();

    expect(store.getState()).toMatchObject({
      intakeId: null, resourceStatus: 'idle', registeredTranscript: null,
      resourceDraft: { mode: 'automatic', videoUrl: '', subtitleFileName: '', inputSource: 'none' }
    });
  });
});
