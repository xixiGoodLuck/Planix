from __future__ import annotations

from typing import Any

from ...contracts import VideoResource
from ...evidence.providers import (
    ProviderEvidenceSource,
    ProviderSegmentSource,
    ProviderVideoDocument,
    ProviderVideoMetadata,
    VideoSearchQuery,
    VideoSourceProvider,
)
from ...evidence.transcript import (
    TranscriptProvider,
    TranscriptSegmentBuilder,
)
from ...generators import LearningSemanticModel
from .config import LearningRuntimeConfig


class RuntimeUnavailable(RuntimeError):
    def __init__(self, component: str, message: str):
        self.component = component
        self.message = message
        super().__init__(f"{component}: {message}")


def _is_mock(component: object) -> bool:
    identity = f"{type(component).__module__}.{type(component).__name__}".casefold()
    return "mock" in identity or "fixture" in identity


def _check_health(component: object, name: str) -> None:
    health_check = getattr(component, "health_check", None)
    if not callable(health_check):
        return
    try:
        result = health_check()
    except Exception as exc:
        raise RuntimeUnavailable(name, "health check failed") from exc
    if result is False:
        raise RuntimeUnavailable(name, "health check failed")


def create_video_provider(config: LearningRuntimeConfig) -> VideoSourceProvider:
    provider = config.video_provider
    if provider is None:
        raise RuntimeUnavailable("video_provider", "video provider is not configured")
    if not callable(getattr(provider, "search", None)) or not callable(
        getattr(provider, "fetch_metadata", None)
    ):
        raise RuntimeUnavailable(
            "video_provider",
            "video provider does not implement search/fetch_metadata",
        )
    if config.environment == "production" and _is_mock(provider):
        raise RuntimeUnavailable("video_provider", "Mock provider is forbidden in production")
    _check_health(provider, "video_provider")
    return provider


def create_transcript_provider(config: LearningRuntimeConfig) -> TranscriptProvider:
    provider = config.transcript_provider
    if provider is None:
        raise RuntimeUnavailable(
            "transcript_provider",
            "transcript provider is not configured",
        )
    if not callable(getattr(provider, "fetch_transcript", None)):
        raise RuntimeUnavailable(
            "transcript_provider",
            "transcript provider does not implement fetch_transcript",
        )
    if config.environment == "production" and _is_mock(provider):
        raise RuntimeUnavailable(
            "transcript_provider",
            "Mock provider is forbidden in production",
        )
    if config.environment == "production" and getattr(
        provider,
        "source_type",
        None,
    ) not in {"authorized", "srt_vtt"}:
        raise RuntimeUnavailable(
            "transcript_provider",
            "production transcript provider must use an authorized or SRT/VTT source",
        )
    _check_health(provider, "transcript_provider")
    return provider


def create_model_provider(config: LearningRuntimeConfig) -> LearningSemanticModel:
    provider = config.model_provider
    if provider is None:
        raise RuntimeUnavailable("model_provider", "model provider is not configured")
    if not callable(getattr(provider, "complete", None)):
        raise RuntimeUnavailable(
            "model_provider",
            "model provider does not implement complete",
        )
    if config.environment == "production" and _is_mock(provider):
        raise RuntimeUnavailable("model_provider", "Mock provider is forbidden in production")
    _check_health(provider, "model_provider")
    return provider


class TranscriptBackedVideoProvider:
    """Composition adapter from verified transcript cues to provider evidence."""

    def __init__(
        self,
        video_provider: VideoSourceProvider,
        transcript_provider: TranscriptProvider,
        *,
        segment_builder: TranscriptSegmentBuilder | None = None,
    ):
        self.video_provider = video_provider
        self.transcript_provider = transcript_provider
        self.segment_builder = segment_builder or TranscriptSegmentBuilder()

    def search(self, query: VideoSearchQuery):
        return self.video_provider.search(query)

    def fetch_metadata(self, external_id: str) -> VideoResource:
        return self.video_provider.fetch_metadata(external_id)

    def fetch_evidence(self, external_id: str) -> ProviderVideoDocument:
        resource = self.fetch_metadata(external_id)
        document = self.transcript_provider.fetch_transcript(resource)
        built = self.segment_builder.build(resource, document)
        evidence_by_id = {item.id: item for item in built.evidence}
        return ProviderVideoDocument(
            metadata=ProviderVideoMetadata(
                provider=resource.provider,
                externalId=resource.external_id,
                canonicalUrl=resource.canonical_url,
                title=resource.title,
                author=resource.author,
                language=resource.language,
                durationSeconds=resource.duration_seconds,
                publishedAt=resource.published_at,
                contentFingerprint=resource.content_fingerprint,
                technologyVersions=resource.technology_versions,
            ),
            segments=[
                ProviderSegmentSource(
                    sourceKey=segment.id,
                    timeRangeSeconds=(segment.start_seconds, segment.end_seconds),
                    evidence=[
                        ProviderEvidenceSource(
                            kind=evidence_by_id[evidence_id].kind,
                            supportedClaim=evidence_by_id[evidence_id].supported_claim,
                            sourceRange=evidence_by_id[evidence_id].source_range,
                            sourceExcerpt=evidence_by_id[evidence_id].source_excerpt,
                            verificationStatus=evidence_by_id[
                                evidence_id
                            ].verification_status,
                        )
                        for evidence_id in segment.evidence_refs
                    ],
                )
                for segment in built.segments
            ],
        )


def component_name(component: Any) -> str:
    return type(component).__name__


__all__ = [
    "RuntimeUnavailable",
    "TranscriptBackedVideoProvider",
    "component_name",
    "create_model_provider",
    "create_transcript_provider",
    "create_video_provider",
]
