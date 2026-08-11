from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field, model_validator

from ...contracts import (
    EvidenceSourceRange,
    LearningContract,
    VideoResource,
    VideoProvider,
)


class VideoSourceProviderError(RuntimeError):
    pass


class VideoSearchQuery(LearningContract):
    knowledge_terms: list[str] = Field(min_length=1)
    language: str = ""
    maximum_results: int = Field(default=5, ge=1, le=20)


class VideoSearchHit(LearningContract):
    provider: VideoProvider
    external_id: str = Field(min_length=1)
    canonical_url: str | None = None
    title: str = ""
    duration_seconds: int | None = Field(default=None, ge=1)


class ProviderVideoMetadata(LearningContract):
    provider: VideoProvider
    external_id: str = Field(min_length=1)
    canonical_url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    author: str = ""
    language: str = ""
    duration_seconds: int = Field(ge=1)
    published_at: str | None = None
    content_fingerprint: str = Field(min_length=1)
    technology_versions: dict[str, str] = Field(default_factory=dict)


class ProviderEvidenceSource(LearningContract):
    kind: Literal[
        "transcript_span",
        "caption_span",
        "chapter_marker",
        "provider_metadata",
        "manual_verified",
    ]
    supported_claim: str = Field(min_length=1)
    source_range: EvidenceSourceRange
    source_excerpt: str | None = None
    verification_status: Literal["verified", "unverified", "rejected"]


class ProviderSegmentSource(LearningContract):
    """Transient provider data; the tuple is projected into ContentSegment timestamps."""

    source_key: str = Field(min_length=1)
    time_range_seconds: tuple[int, int]
    evidence: list[ProviderEvidenceSource] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_range(self) -> "ProviderSegmentSource":
        start, end = self.time_range_seconds
        if start < 0 or end <= start:
            raise ValueError("provider segment range must satisfy 0 <= start < end")
        return self


class ProviderVideoDocument(LearningContract):
    metadata: ProviderVideoMetadata
    segments: list[ProviderSegmentSource] = Field(min_length=1)


class VideoSourceProvider(Protocol):
    def search(self, query: VideoSearchQuery) -> list[VideoSearchHit]: ...

    def fetch_metadata(self, external_id: str) -> VideoResource: ...


class VideoEvidenceProvider(VideoSourceProvider, Protocol):
    """A source provider that also owns verified segment/evidence extraction."""

    def fetch_evidence(self, external_id: str) -> ProviderVideoDocument: ...


__all__ = [
    "ProviderEvidenceSource",
    "ProviderSegmentSource",
    "ProviderVideoDocument",
    "ProviderVideoMetadata",
    "VideoSearchHit",
    "VideoSearchQuery",
    "VideoEvidenceProvider",
    "VideoSourceProvider",
    "VideoSourceProviderError",
]
