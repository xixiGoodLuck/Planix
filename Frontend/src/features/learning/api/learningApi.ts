import { ApiHttpError, ApiNetworkError, apiUrl, callApi } from '../../../lib/api';
import type {
  LearningIntakeCreateRequest,
  LearningIntakeResponse,
  LearningIntakeSupplementRequest,
  LearningProgressEvent,
  LearningRunCreateRequest,
  LearningRunCreateResponse,
  LearningRunResult,
  LearningRunState,
  LearningTranscriptRegistrationRequest,
  LearningTranscriptRevokeResponse,
  LearningTranscriptSourceSummary
} from '../types';

export interface LearningEventHandlers {
  onEvent: (event: LearningProgressEvent) => void;
  onError: (error: Error) => void;
}

export interface LearningEventStream {
  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void;
  removeEventListener(type: string, listener: EventListenerOrEventListenerObject): void;
  close(): void;
  onerror: ((event: Event) => void) | null;
}

export type LearningEventStreamFactory = (url: string) => LearningEventStream;

export function createLearningIntake(payload: LearningIntakeCreateRequest) {
  return callApi<LearningIntakeResponse>('POST', '/api/learning/intakes', payload);
}

export function supplementLearningIntake(intakeId: string, payload: LearningIntakeSupplementRequest) {
  return callApi<LearningIntakeResponse>(
    'POST',
    `/api/learning/intakes/${encodeURIComponent(intakeId)}/supplements`,
    payload,
  );
}

export function continueLearningIntake(intakeId: string) {
  return callApi<LearningIntakeResponse>(
    'POST',
    `/api/learning/intakes/${encodeURIComponent(intakeId)}/continue`,
  );
}

export function fetchLearningIntake(intakeId: string) {
  return callApi<LearningIntakeResponse>(
    'GET',
    `/api/learning/intakes/${encodeURIComponent(intakeId)}`,
  );
}

export function registerLearningTranscript(payload: LearningTranscriptRegistrationRequest) {
  return callApi<LearningTranscriptSourceSummary>('POST', '/api/learning/transcripts', payload);
}

export function fetchLearningTranscriptMetadata(sourceId: string) {
  return callApi<LearningTranscriptSourceSummary>(
    'GET',
    `/api/learning/transcripts/${encodeURIComponent(sourceId)}`,
  );
}

export function revokeLearningTranscript(sourceId: string) {
  return callApi<LearningTranscriptRevokeResponse>(
    'DELETE',
    `/api/learning/transcripts/${encodeURIComponent(sourceId)}`,
  );
}

export function createLearningRun(payload: LearningRunCreateRequest) {
  return callApi<LearningRunCreateResponse>('POST', '/api/learning/runs', payload);
}

export function fetchLearningRun(runId: string) {
  return callApi<LearningRunState>('GET', `/api/learning/runs/${encodeURIComponent(runId)}`);
}

export function fetchLearningRunResult(runId: string) {
  return callApi<LearningRunResult>('GET', `/api/learning/runs/${encodeURIComponent(runId)}/result`);
}

export function resumeLearningEvidence(runId: string) {
  return callApi<LearningRunState>(
    'POST',
    `/api/learning/runs/${encodeURIComponent(runId)}/resume-evidence`,
  );
}

export function streamLearningRunEvents(
  runId: string,
  handlers: LearningEventHandlers,
  createStream: LearningEventStreamFactory = (url) => new EventSource(url),
  after = 0,
): () => void {
  const suffix = after > 0 ? `?after=${after}` : '';
  const source = createStream(apiUrl(`/api/learning/runs/${encodeURIComponent(runId)}/events${suffix}`));
  let terminal = false;
  const onProgress: EventListener = (rawEvent) => {
    try {
      const messageEvent = rawEvent as MessageEvent<string>;
      const event = {
        ...(JSON.parse(messageEvent.data) as LearningProgressEvent),
        stream_id: messageEvent.lastEventId || undefined
      };
      handlers.onEvent(event);
      terminal = event.event_type === 'session_completed'
        || event.event_type === 'session_failed'
        || event.event_type === 'session_waiting_evidence';
      if (terminal) source.close();
    } catch (error) {
      source.close();
      handlers.onError(error instanceof Error ? error : new Error(String(error)));
    }
  };
  source.addEventListener('progress', onProgress);
  source.onerror = () => {
    if (terminal) return;
    handlers.onError(new ApiNetworkError('Learning progress stream is unavailable'));
  };
  return () => {
    source.removeEventListener('progress', onProgress);
    source.close();
  };
}

export function isLearningRuntimeUnavailable(error: unknown): boolean {
  return error instanceof ApiHttpError && error.status === 503;
}

export function isLearningBackendUnavailable(error: unknown): boolean {
  return error instanceof ApiNetworkError;
}
