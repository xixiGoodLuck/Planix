from __future__ import annotations

from ....contracts import VideoResource
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
