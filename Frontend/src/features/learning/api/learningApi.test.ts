import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  continueLearningIntake,
  createLearningIntake,
  createLearningRun,
  fetchLearningTranscriptMetadata,
  registerLearningTranscript,
  resumeLearningEvidence,
  revokeLearningTranscript,
  supplementLearningIntake,
  streamLearningRunEvents,
  type LearningEventStream
} from './learningApi';

class FakeEventStream implements LearningEventStream {
  onerror: ((event: Event) => void) | null = null;
  closed = false;
  private listeners = new Set<EventListenerOrEventListenerObject>();

  addEventListener(_type: string, listener: EventListenerOrEventListenerObject) {
    this.listeners.add(listener);
  }

  removeEventListener(_type: string, listener: EventListenerOrEventListenerObject) {
    this.listeners.delete(listener);
  }

  close() {
    this.closed = true;
  }

  progress(data: object) {
    const event = { data: JSON.stringify(data) } as MessageEvent<string>;
    this.listeners.forEach((listener) => {
      if (typeof listener === 'function') listener(event);
      else listener.handleEvent(event);
    });
  }
}

describe('Learning API client', () => {
  beforeEach(() => {
    vi.stubGlobal('window', globalThis);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('creates a Learning run through the formal API', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ run_id: 'learning-session-1' }),
      { status: 202, headers: { 'Content-Type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', fetchMock);

    const result = await createLearningRun({
      goal: 'Learn FastAPI',
      preferences: {
        target_result: 'Build CRUD',
        current_level: { summary: 'Python basics', knownSkills: [], knownTechnologies: [], uncertainAreas: [], sourceRefs: [] },
        content_budget: { targetTotalMinutes: 120 },
        language_preference: { preferredLanguages: ['en-US'], acceptableLanguages: [], subtitlesAcceptable: true },
        resourcePreference: { preferredPlatforms: ['bilibili'], excludedPlatforms: [], preferredStyles: ['hands_on'], freeOnly: true, userSuppliedUrls: [] },
        confirmed: true
      },
      constraints: []
    });

    expect(result.run_id).toBe('learning-session-1');
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:8003/api/learning/runs');
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({ goal: 'Learn FastAPI' });
  });

  it('uses typed intake, supplement, and continue endpoints', async () => {
    const payload = { intakeId: 'learning-intake-1', status: 'waiting_scope_review', scope: {}, review: {}, runId: null };
    const fetchMock = vi.fn().mockImplementation(async () => new Response(
      JSON.stringify(payload),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', fetchMock);

    await createLearningIntake({ message: 'Learn FastAPI', preferredLanguage: 'en-US' });
    await supplementLearningIntake('learning-intake-1', {
      message: '', preferredLanguage: 'en-US',
      resourceUrls: ['https://www.bilibili.com/video/BV1zV2QBtE39'], deferAutoStart: true
    });
    await continueLearningIntake('learning-intake-1');

    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:8003/api/learning/intakes');
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ message: 'Learn FastAPI', preferredLanguage: 'en-US' });
    expect(fetchMock.mock.calls[1][0]).toContain('/api/learning/intakes/learning-intake-1/supplements');
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      message: '', preferredLanguage: 'en-US',
      resourceUrls: ['https://www.bilibili.com/video/BV1zV2QBtE39'], deferAutoStart: true
    });
    expect(fetchMock.mock.calls[2][0]).toContain('/api/learning/intakes/learning-intake-1/continue');
    expect(fetchMock.mock.calls[2][1]?.body).toBeUndefined();
  });

  it('registers, fetches, and revokes transcript sources through the existing registry API', async () => {
    const summary = {
      source_id: 'source-1', resource_id: 'video-1', provider: 'bilibili', external_id: 'BV1zV2QBtE39',
      canonical_url: 'https://www.bilibili.com/video/BV1zV2QBtE39', source_type: 'srt_vtt',
      source_format: 'vtt', source_name: 'routing.vtt', language: 'zh-CN', segment_count: 2,
      status: 'active', created_at: '2026-08-12T08:00:00Z'
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(summary), { status: 201, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(summary), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ source_id: 'source-1', status: 'revoked' }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    await registerLearningTranscript({
      videoUrl: summary.canonical_url, format: 'vtt', language: 'zh-CN',
      content: 'WEBVTT\n\n00:01.000 --> 00:02.000\nRouting', sourceName: 'routing.vtt'
    });
    await fetchLearningTranscriptMetadata('source-1');
    await revokeLearningTranscript('source-1');

    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:8003/api/learning/transcripts');
    expect(fetchMock.mock.calls[1][0]).toContain('/api/learning/transcripts/source-1');
    expect(fetchMock.mock.calls[2][1]?.method).toBe('DELETE');
  });

  it('resumes evidence through the typed run action', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({
        status: 'running', current_stage: 'evidence_generation',
        completed_stages: ['scope', 'knowledge_generation'], error: null
      }),
      { status: 202, headers: { 'Content-Type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', fetchMock);

    await resumeLearningEvidence('learning-session-1');

    expect(fetchMock.mock.calls[0][0]).toContain(
      '/api/learning/runs/learning-session-1/resume-evidence'
    );
    expect(fetchMock.mock.calls[0][1]?.method).toBe('POST');
  });

  it('parses canonical SSE events, uses a cursor, and closes on waiting', () => {
    const source = new FakeEventStream();
    const onEvent = vi.fn();
    const onError = vi.fn();
    const stop = streamLearningRunEvents(
      'learning-session-1',
      { onEvent, onError },
      (url) => {
        expect(url).toContain('/api/learning/runs/learning-session-1/events');
        expect(url).toContain('after=4');
        return source;
      },
      4,
    );

    source.progress({
      event_type: 'stage_started', stage: 'knowledge_generation', status: 'started',
      message: 'started', timestamp: '2026-08-12T08:00:00Z'
    });
    source.progress({
      event_type: 'session_waiting_evidence', stage: 'waiting_evidence', status: 'waiting_evidence',
      message: 'waiting', timestamp: '2026-08-12T08:00:01Z'
    });

    expect(onEvent).toHaveBeenCalledTimes(2);
    expect(onEvent.mock.calls[0][0].stage).toBe('knowledge_generation');
    expect(onError).not.toHaveBeenCalled();
    expect(source.closed).toBe(true);
    stop();
  });
});
