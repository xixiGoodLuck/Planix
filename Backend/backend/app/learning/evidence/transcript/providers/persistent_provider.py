from __future__ import annotations

import re

from ....contracts import VideoResource
from ...providers.base import VideoSearchHit, VideoSearchQuery
from ..repository import LearningTranscriptRepository
from ..validators import TranscriptValidator
from .base import (
    TranscriptDocument,
    TranscriptSourceMetadata,
    TranscriptUnavailableError,
)
from .subtitle_file_provider import ParsedSubtitleCue, SubtitleFileTranscriptProvider


class PersistentTranscriptProvider:
    """Production transcript provider backed only by the verified registry."""

    source_type = "srt_vtt"

    def __init__(
        self,
        repository: LearningTranscriptRepository,
        *,
        validator: TranscriptValidator | None = None,
    ):
        self.repository = repository
        self.validator = validator or TranscriptValidator()

    def health_check(self) -> bool:
        return self.repository.health_check()

    def search_registered(self, query: VideoSearchQuery) -> list[VideoSearchHit]:
        tokens = {
            token.casefold()
            for term in query.knowledge_terms
            for token in re.findall(r"[a-z0-9_+#.]{2,}|[\u4e00-\u9fff]{2,}", term)
        }
        ranked: list[tuple[int, str, VideoSearchHit]] = []
        for source in self.repository.list_active_sources():
            searchable = " ".join(
                [source.resource.title, *(segment.text for segment in source.segments)]
            ).casefold()
            score = sum(1 for token in tokens if token in searchable)
            if score <= 0:
                continue
            resource = source.resource
            ranked.append(
                (
                    score,
                    resource.external_id,
                    VideoSearchHit(
                        provider=resource.provider,
                        externalId=resource.external_id,
                        canonicalUrl=resource.canonical_url,
                        title=resource.title,
                        durationSeconds=resource.duration_seconds,
                    ),
                )
            )
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in ranked[: query.maximum_results]]

    def fetch_transcript(self, resource: VideoResource) -> TranscriptDocument:
        source = self.repository.find_active_by_resource_fingerprint(
            resource.id,
            resource.content_fingerprint,
        )
        if source is None:
            raise TranscriptUnavailableError(
                "TRANSCRIPT_UNAVAILABLE: no active transcript matches the video fingerprint"
            )
        cues = [
            ParsedSubtitleCue(
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                text=item.text,
            )
            for item in source.segments
        ]
        document = TranscriptDocument(
            resourceId=resource.id,
            fingerprint=resource.content_fingerprint,
            language=source.language,
            segments=SubtitleFileTranscriptProvider.segments_from_cues(
                cues,
                source.source_id,
            ),
            sourceMetadata=TranscriptSourceMetadata(
                sourceType=source.source_format,
                sourceId=source.source_id,
                sourceName=source.source_name,
                checksum=source.source_checksum.removeprefix("sha256:"),
                authorized=True,
            ),
        )
        self.validator.validate(resource, document)
        return document


__all__ = ["PersistentTranscriptProvider"]
