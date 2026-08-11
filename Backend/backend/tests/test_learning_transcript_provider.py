from __future__ import annotations

from pathlib import Path

import pytest

from app.learning.contracts import VideoResource
from app.learning.evidence.transcript import (
    AuthorizedTranscriptPayload,
    SubtitleFileTranscriptProvider,
    TranscriptProviderError,
    TranscriptSegmentBuilder,
    TranscriptSourceAdapter,
)


def _resource(*, fingerprint: str = "sha256:phase22-video-v1") -> VideoResource:
    return VideoResource(
        id="video-phase22",
        provider="fixture",
        externalId="phase22-video",
        canonicalUrl="https://example.test/videos/phase22",
        title="FastAPI Phase 22",
        durationSeconds=120,
        contentFingerprint=fingerprint,
    )


SRT = """1
00:00:10,000 --> 00:00:15,500
FastAPI routing maps a request to one handler.

2
00:00:15,500 --> 00:00:22,000
Pydantic validates the request body.
"""


def test_valid_srt_preserves_source_fingerprint_and_builds_evidence() -> None:
    resource = _resource()
    provider = SubtitleFileTranscriptProvider()
    provider.register_upload(
        resource,
        filename="fastapi.srt",
        content=SRT,
        language="en",
    )

    document = provider.fetch_transcript(resource)
    built = TranscriptSegmentBuilder().build(resource, document)

    assert document.fingerprint == resource.content_fingerprint
    assert document.source_metadata is not None
    assert document.source_metadata.source_type == "srt"
    assert document.source_metadata.authorized is True
    assert [(item.start_seconds, item.end_seconds) for item in document.segments] == [
        (10, 22),
    ]
    assert built.segments
    assert built.evidence
    assert all(item.verification_status == "verified" for item in built.evidence)


def test_valid_vtt_file_is_read_and_parsed() -> None:
    resource = _resource()
    subtitle_path = Path(__file__).parent / "fixtures" / "learning_transcript_valid.vtt"
    provider = SubtitleFileTranscriptProvider()
    provider.register_file(resource, subtitle_path, language="en")

    document = provider.fetch_transcript(resource)

    assert document.source_metadata is not None
    assert document.source_metadata.source_type == "vtt"
    assert len(document.segments) == 2


class AuthorizedSource:
    def __init__(self, fingerprint: str):
        self.fingerprint = fingerprint

    def fetch(self, resource):
        return AuthorizedTranscriptPayload(
            resourceId=resource.id,
            fingerprint=self.fingerprint,
            sourceId="licensed:phase22",
            sourceName="Licensed Transcript API",
            language="en",
            segments=[
                {
                    "id": "authorized-routing",
                    "startSeconds": 10,
                    "endSeconds": 20,
                    "text": "Authorized transcript content.",
                }
            ],
        )

    def health_check(self):
        return True


def test_authorized_source_adapter_binds_resource_fingerprint() -> None:
    resource = _resource()
    document = TranscriptSourceAdapter(
        AuthorizedSource(resource.content_fingerprint)
    ).fetch_transcript(resource)

    assert document.fingerprint == resource.content_fingerprint
    assert document.source_metadata is not None
    assert document.source_metadata.source_type == "authorized"


def test_invalid_subtitle_timestamp_is_rejected() -> None:
    resource = _resource()
    provider = SubtitleFileTranscriptProvider()
    provider.register_upload(
        resource,
        filename="broken.srt",
        content="1\n00:00:20,000 --> 00:00:10,000\nBroken range\n",
    )

    with pytest.raises(TranscriptProviderError, match="end must be after start"):
        provider.fetch_transcript(resource)


def test_registered_subtitle_fingerprint_mismatch_is_rejected() -> None:
    original = _resource()
    provider = SubtitleFileTranscriptProvider()
    provider.register_upload(original, filename="fastapi.srt", content=SRT)

    with pytest.raises(TranscriptProviderError, match="fingerprint"):
        provider.fetch_transcript(_resource(fingerprint="sha256:phase22-video-v2"))


def test_authorized_source_fingerprint_mismatch_is_rejected() -> None:
    resource = _resource()
    provider = TranscriptSourceAdapter(AuthorizedSource("sha256:stale"))

    with pytest.raises(TranscriptProviderError, match="fingerprint"):
        provider.fetch_transcript(resource)
