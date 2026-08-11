import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createLearningRun,
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

  it('parses named SSE progress events and closes on completion', () => {
    const source = new FakeEventStream();
    const onEvent = vi.fn();
    const onError = vi.fn();
    const stop = streamLearningRunEvents(
      'learning-session-1',
      { onEvent, onError },
      (url) => {
        expect(url).toContain('/api/learning/runs/learning-session-1/events');
        return source;
      },
    );

    source.progress({
      event_type: 'stage_started', stage: 'knowledge_generating', status: 'started',
      message: 'started', timestamp: '2026-08-12T08:00:00Z'
    });
    source.progress({
      event_type: 'session_completed', stage: 'completed', status: 'completed',
      message: 'done', timestamp: '2026-08-12T08:00:01Z'
    });

    expect(onEvent).toHaveBeenCalledTimes(2);
    expect(onEvent.mock.calls[0][0].stage).toBe('knowledge_generating');
    expect(onError).not.toHaveBeenCalled();
    expect(source.closed).toBe(true);
    stop();
  });
});
