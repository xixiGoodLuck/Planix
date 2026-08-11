import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { enUS } from '../../../i18n/en-US';
import type { LearningResourceDraft, LearningTranscriptSourceSummary } from '../types';
import {
  LearningResourceInput
} from './LearningResourceInput';
import {
  LEARNING_TRANSCRIPT_HARD_MAX_BYTES,
  canonicalizeBilibiliVideoUrl,
  validateLearningTranscriptFile
} from './learningResourceFiles';

const t = (key: string): string => {
  const [namespace, item] = key.split('.');
  return enUS[namespace as keyof typeof enUS]?.[item] ?? key;
};

const automaticDraft: LearningResourceDraft = {
  mode: 'automatic', videoUrl: '', subtitleFormat: 'vtt', subtitleLanguage: 'zh-CN',
  subtitleFileName: '', inputSource: 'none'
};

const summary: LearningTranscriptSourceSummary = {
  source_id: 'PRIVATE-SOURCE-ID', resource_id: 'video-1', resource_fingerprint: 'PRIVATE-FINGERPRINT',
  provider: 'bilibili', external_id: 'BV1zV2QBtE39', canonical_url: 'https://www.bilibili.com/video/BV1zV2QBtE39',
  title: 'FastAPI Routing', source_type: 'srt_vtt', source_format: 'vtt', source_name: 'routing.vtt',
  language: 'zh-CN', checksum_prefix: 'PRIVATE-CHECKSUM', authorization_status: 'authorized', status: 'active',
  segment_count: 2, start_ms: 10000, end_ms: 90000, created_at: '2026-08-12T08:00:00Z'
};

function renderResource(
  draft: LearningResourceDraft = automaticDraft,
  updates: Partial<Parameters<typeof LearningResourceInput>[0]> = {},
) {
  return renderToStaticMarkup(
    <LearningResourceInput
      draft={draft}
      status="idle"
      summary={null}
      resourceError={null}
      busy={false}
      onModeChange={vi.fn()}
      onDraftChange={vi.fn()}
      onRegister={vi.fn().mockResolvedValue(true)}
      onBindVideoOnly={vi.fn().mockResolvedValue(true)}
      onRevoke={vi.fn().mockResolvedValue(true)}
      t={t}
      {...updates}
    />
  );
}

describe('Learning Resource Input', () => {
  it('defaults to automatic search and keeps specified video optional', () => {
    const html = renderResource();

    expect(html).toContain(enUS.learning.resourceAutomatic);
    expect(html).toContain(enUS.learning.resourceSpecified);
    expect(html).toContain('checked=""');
    expect(html).not.toContain('type="url"');
  });

  it('shows Bilibili URL, native file input, paste input, language, and format in specified mode', () => {
    const html = renderResource({ ...automaticDraft, mode: 'specified' });

    expect(html).toContain('type="url"');
    expect(html).toContain('accept=".srt,.vtt"');
    expect(html).toContain(enUS.learning.subtitlePaste);
    expect(html).toContain(enUS.learning.subtitleLanguage);
    expect(html).toContain('<option value="srt">SRT</option>');
    expect(html).toContain('<option value="vtt" selected="">VTT</option>');
  });

  it('canonicalizes valid Bilibili URLs and rejects other hosts', () => {
    expect(canonicalizeBilibiliVideoUrl('http://www.bilibili.com/video/BV1zV2QBtE39?share=1'))
      .toBe('https://www.bilibili.com/video/BV1zV2QBtE39');
    expect(canonicalizeBilibiliVideoUrl('https://example.com/video/BV1zV2QBtE39')).toBeNull();
    expect(canonicalizeBilibiliVideoUrl('not a URL')).toBeNull();
  });

  it('accepts SRT and VTT files by extension', () => {
    expect(validateLearningTranscriptFile({ name: 'routing.srt', size: 20 })).toBe('srt');
    expect(validateLearningTranscriptFile({ name: 'routing.VTT', size: 20 })).toBe('vtt');
  });

  it('rejects empty, unsupported, and oversized files before reading', () => {
    expect(() => validateLearningTranscriptFile({ name: 'routing.vtt', size: 0 })).toThrow('empty_file');
    expect(() => validateLearningTranscriptFile({ name: 'routing.txt', size: 20 })).toThrow('unsupported_file');
    expect(() => validateLearningTranscriptFile({
      name: 'routing.srt', size: LEARNING_TRANSCRIPT_HARD_MAX_BYTES + 1
    })).toThrow('file_too_large');
  });

  it('renders only safe registered metadata and never transcript internals', () => {
    const html = renderResource(
      { ...automaticDraft, mode: 'specified', videoUrl: summary.canonical_url },
      { status: 'registered', summary },
    );

    expect(html).toContain(summary.canonical_url);
    expect(html).toContain('zh-CN');
    expect(html).toContain('>2<');
    expect(html).toContain(enUS.learning.transcriptVerified);
    expect(html).not.toContain('PRIVATE-SOURCE-ID');
    expect(html).not.toContain('PRIVATE-FINGERPRINT');
    expect(html).not.toContain('PRIVATE-CHECKSUM');
  });

  it('renders explicit video-only and revoked evidence states', () => {
    expect(renderResource({ ...automaticDraft, mode: 'specified' }, { status: 'video_only' }))
      .toContain(enUS.learning.transcriptNotProvided);
    expect(renderResource({ ...automaticDraft, mode: 'specified' }, { status: 'revoked' }))
      .toContain(enUS.learning.transcriptRevoked);
  });

  it('maps registration failures to safe copy without backend details or raw subtitle text', () => {
    const html = renderResource(
      { ...automaticDraft, mode: 'specified', videoUrl: summary.canonical_url },
      { status: 'failed', resourceError: 'registration_failed' },
    );

    expect(html).toContain(enUS.learning.resourceError_registration_failed);
    expect(html).not.toContain('Pydantic');
    expect(html).not.toContain('WEBVTT');
  });
});
