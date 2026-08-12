import { ChangeEvent, useRef, useState } from 'react';
import { Captions, Link2, Search, ShieldCheck, Trash2, Upload } from 'lucide-react';
import type {
  LearningResourceDraft,
  LearningResourceMode,
  LearningResourceStatus,
  LearningTranscriptFormat,
  LearningTranscriptRegistrationRequest,
  LearningTranscriptSourceSummary
} from '../types';
import {
  canonicalizeBilibiliVideoUrl,
  readLearningTranscriptFile,
  validateLearningTranscriptFile
} from './learningResourceFiles';

interface LearningResourceInputProps {
  draft: LearningResourceDraft;
  status: LearningResourceStatus;
  summary: LearningTranscriptSourceSummary | null;
  resourceError: string | null;
  busy: boolean;
  onModeChange: (mode: LearningResourceMode) => void;
  onDraftChange: (patch: Partial<LearningResourceDraft>) => void;
  onRegister: (payload: LearningTranscriptRegistrationRequest) => Promise<boolean>;
  onBindVideoOnly: (videoUrl: string) => Promise<boolean>;
  onRevoke: () => Promise<boolean>;
  t: (key: string) => string;
}

function errorKey(error: string | null) {
  return error ? `learning.resourceError_${error}` : '';
}

export function LearningResourceInput({
  draft,
  status,
  summary,
  resourceError,
  busy,
  onModeChange,
  onDraftChange,
  onRegister,
  onBindVideoOnly,
  onRevoke,
  t
}: LearningResourceInputProps) {
  const [rawTranscript, setRawTranscript] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const registering = status === 'registering' || status === 'validating';

  async function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const format = validateLearningTranscriptFile(file);
      const content = await readLearningTranscriptFile(file);
      setRawTranscript(content);
      setLocalError(null);
      onDraftChange({
        subtitleFormat: format,
        subtitleFileName: file.name,
        inputSource: 'file'
      });
    } catch (error) {
      const code = error instanceof Error ? error.message : 'invalid_encoding';
      setLocalError(code);
    }
  }

  function pasteTranscript(value: string) {
    setRawTranscript(value);
    setLocalError(null);
    onDraftChange({ subtitleFileName: '', inputSource: value ? 'paste' : 'none' });
  }

  async function register() {
    const canonicalUrl = canonicalizeBilibiliVideoUrl(draft.videoUrl);
    if (!canonicalUrl) {
      setLocalError('video_invalid');
      return;
    }
    if (!rawTranscript.trim()) {
      setLocalError('empty_file');
      return;
    }
    const succeeded = await onRegister({
      videoUrl: canonicalUrl,
      format: draft.subtitleFormat,
      language: draft.subtitleLanguage,
      content: rawTranscript,
      sourceName: draft.subtitleFileName || `transcript.${draft.subtitleFormat}`
    });
    if (succeeded) {
      setRawTranscript('');
      setLocalError(null);
      if (fileInput.current) fileInput.current.value = '';
    }
  }

  async function bindOnly() {
    const canonicalUrl = canonicalizeBilibiliVideoUrl(draft.videoUrl);
    if (!canonicalUrl) {
      setLocalError('video_invalid');
      return;
    }
    const succeeded = await onBindVideoOnly(canonicalUrl);
    if (succeeded) setLocalError(null);
  }

  const visibleError = localError || resourceError;

  return (
    <section id="learning-resource-input" className="learning-card learning-resource-card" aria-label={t('learning.resourceSection')}>
      <div className="learning-card-heading">
        <div>
          <span className="learning-eyebrow">{t('learning.optional')}</span>
          <h2>{t('learning.resourceSection')}</h2>
        </div>
      </div>

      <div className="learning-resource-modes">
        <label className={draft.mode === 'automatic' ? 'is-selected' : ''}>
          <input
            type="radio"
            name="learning-resource-mode"
            checked={draft.mode === 'automatic'}
            onChange={() => onModeChange('automatic')}
          />
          <Search size={18} aria-hidden="true" />
          <span><strong>{t('learning.resourceAutomatic')}</strong>{t('learning.resourceAutomaticHint')}</span>
        </label>
        <label className={draft.mode === 'specified' ? 'is-selected' : ''}>
          <input
            type="radio"
            name="learning-resource-mode"
            checked={draft.mode === 'specified'}
            onChange={() => onModeChange('specified')}
          />
          <Link2 size={18} aria-hidden="true" />
          <span><strong>{t('learning.resourceSpecified')}</strong>{t('learning.resourceSpecifiedHint')}</span>
        </label>
      </div>

      {draft.mode === 'specified' && (
        <div className="learning-resource-editor">
          <p className="learning-resource-boundary">{t('learning.resourceEvidenceBoundary')}</p>
          <label>
            <span>{t('learning.bilibiliVideoUrl')}</span>
            <input
              type="url"
              value={draft.videoUrl}
              onChange={(event) => onDraftChange({ videoUrl: event.target.value })}
              placeholder="https://www.bilibili.com/video/BV..."
              disabled={registering || busy}
            />
          </label>
          <div className="learning-resource-row">
            <label>
              <span>{t('learning.subtitleLanguage')}</span>
              <input
                value={draft.subtitleLanguage}
                onChange={(event) => onDraftChange({ subtitleLanguage: event.target.value })}
                disabled={registering || busy}
              />
            </label>
            <label>
              <span>{t('learning.subtitleFormat')}</span>
              <select
                value={draft.subtitleFormat}
                onChange={(event) => onDraftChange({ subtitleFormat: event.target.value as LearningTranscriptFormat })}
                disabled={registering || busy}
              >
                <option value="srt">SRT</option>
                <option value="vtt">VTT</option>
              </select>
            </label>
          </div>
          <label>
            <span>{t('learning.subtitleFile')}</span>
            <input
              ref={fileInput}
              type="file"
              accept=".srt,.vtt"
              onChange={(event) => { void chooseFile(event); }}
              disabled={registering || busy}
            />
          </label>
          <label>
            <span>{t('learning.subtitlePaste')}</span>
            <textarea
              value={draft.inputSource === 'paste' ? rawTranscript : ''}
              onChange={(event) => pasteTranscript(event.target.value)}
              placeholder={t('learning.subtitlePastePlaceholder')}
              rows={5}
              disabled={registering || busy}
            />
          </label>
          <p className="learning-resource-source" role="status">
            <Upload size={15} aria-hidden="true" />
            {draft.inputSource === 'file' && draft.subtitleFileName
              ? `${t('learning.activeSubtitleSource')}: ${draft.subtitleFileName}`
              : draft.inputSource === 'paste'
                ? `${t('learning.activeSubtitleSource')}: ${t('learning.subtitlePaste')}`
                : t('learning.noSubtitleSelected')}
          </p>
          {visibleError && (
            <p className="learning-scope-error" role="alert">{t(errorKey(visibleError))}</p>
          )}
          <div className="learning-intake-actions">
            <button type="button" disabled={registering || busy || !rawTranscript.trim()} onClick={() => { void register(); }}>
              <Captions size={16} />
              {registering ? t('learning.transcriptRegistering') : t('learning.transcriptRegister')}
            </button>
            <button type="button" disabled={registering || busy || !draft.videoUrl.trim()} onClick={() => { void bindOnly(); }}>
              <Link2 size={16} />
              {t('learning.videoOnly')}
            </button>
          </div>
        </div>
      )}

      {status === 'video_only' && (
        <p className="learning-resource-status is-warning" role="status">{t('learning.transcriptNotProvided')}</p>
      )}
      {status === 'revoked' && (
        <p className="learning-resource-status is-warning" role="status">{t('learning.transcriptRevoked')}</p>
      )}
      {status === 'registered' && summary && (
        <div className="learning-resource-summary">
          <span className="learning-resource-verified"><ShieldCheck size={17} />{t('learning.transcriptVerified')}</span>
          <dl>
            <div><dt>{t('learning.bilibiliVideoUrl')}</dt><dd>{summary.canonical_url}</dd></div>
            <div><dt>{t('learning.subtitleSourceType')}</dt><dd>{summary.source_type}</dd></div>
            <div><dt>{t('learning.subtitleLanguage')}</dt><dd>{summary.language || t('learning.notSpecified')}</dd></div>
            <div><dt>{t('learning.subtitleSegments')}</dt><dd>{summary.segment_count}</dd></div>
            <div><dt>{t('learning.subtitleStatus')}</dt><dd>{summary.status}</dd></div>
            <div><dt>{t('learning.subtitleRegisteredAt')}</dt><dd>{summary.created_at}</dd></div>
          </dl>
          <button className="learning-resource-revoke" type="button" disabled={busy} onClick={() => { void onRevoke(); }}>
            <Trash2 size={15} />{t('learning.transcriptRevoke')}
          </button>
        </div>
      )}
    </section>
  );
}
