from __future__ import annotations

from ....contracts import VideoResource
from ..providers import TranscriptDocument


class TranscriptValidationError(ValueError):
    def __init__(self, rule: str, path: str, message: str):
        self.rule = rule
        self.path = path
        self.message = message
        super().__init__(f"{rule} [{path}]: {message}")


class TranscriptValidator:
    def validate(
        self,
        resource: VideoResource | None,
        document: TranscriptDocument,
    ) -> None:
        if resource is None or not isinstance(resource, VideoResource):
            self._fail("transcript_resource", "resource", "transcript resource does not exist")
        if not isinstance(document, TranscriptDocument):
            self._fail(
                "transcript_document",
                "transcript",
                "verified TranscriptDocument is required; metadata is not transcript evidence",
            )
        if document.resource_id != resource.id:
            self._fail(
                "transcript_resource",
                "transcript.resourceId",
                "transcript references a different video resource",
            )
        if document.fingerprint != resource.content_fingerprint:
            self._fail(
                "transcript_fingerprint",
                "transcript.fingerprint",
                "transcript fingerprint does not match the current video resource",
            )
        if (
            document.source_metadata is not None
            and not document.source_metadata.authorized
        ):
            self._fail(
                "transcript_source",
                "transcript.sourceMetadata.authorized",
                "transcript source is not authorized",
            )
        if not document.segments:
            self._fail("transcript_empty", "transcript.segments", "transcript has no segments")

        seen: set[str] = set()
        previous_start = -1
        previous_end = -1
        for index, segment in enumerate(document.segments):
            path = f"transcript.segments.{index}"
            if segment.id in seen:
                self._fail("transcript_segment_id", f"{path}.id", "segment id is duplicated")
            seen.add(segment.id)
            if not segment.text.strip():
                self._fail("transcript_text", f"{path}.text", "transcript text is empty")
            if (
                segment.start_seconds < 0
                or segment.end_seconds <= segment.start_seconds
                or segment.end_seconds > resource.duration_seconds
            ):
                self._fail(
                    "transcript_timestamp",
                    path,
                    "transcript range is outside the video duration",
                )
            if segment.start_seconds < previous_start:
                self._fail(
                    "transcript_order",
                    path,
                    "transcript segments are not in chronological order",
                )
            if segment.start_seconds < previous_end:
                self._fail("transcript_overlap", path, "transcript segments overlap")
            previous_start = segment.start_seconds
            previous_end = segment.end_seconds

    @staticmethod
    def _fail(rule: str, path: str, message: str) -> None:
        raise TranscriptValidationError(rule, path, message)


__all__ = ["TranscriptValidationError", "TranscriptValidator"]
