from __future__ import annotations

from hashlib import sha256
import os
import re
from urllib.parse import urlsplit

from ....contracts import VideoResource
from ...providers import VideoSourceProviderError
from ..providers import (
    ParsedSubtitleCue,
    SubtitleFileTranscriptProvider,
    TranscriptDocument,
    TranscriptProviderError,
    TranscriptSourceMetadata,
)
from ..registry_contracts import (
    NormalizedTranscriptSegment,
    TranscriptFormat,
    TranscriptSourceRecord,
    TranscriptSourceSummary,
)
from ..repository import LearningTranscriptRepository
from ..validators import TranscriptValidator


DEFAULT_TRANSCRIPT_MAX_BYTES = 512 * 1024
HARD_TRANSCRIPT_MAX_BYTES = 2 * 1024 * 1024
_SAFE_SOURCE_NAME = re.compile(r"^[\w .()\-]{1,128}$", re.UNICODE)


class TranscriptRegistrationError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        self.error_type = error_type
        self.message = message
        super().__init__(message)


def configured_transcript_max_bytes() -> int:
    raw = os.getenv(
        "PLANIX_TRANSCRIPT_MAX_BYTES",
        str(DEFAULT_TRANSCRIPT_MAX_BYTES),
    ).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise TranscriptRegistrationError(
            "invalid_configuration",
            "PLANIX_TRANSCRIPT_MAX_BYTES must be an integer",
        ) from exc
    if value < 1 or value > HARD_TRANSCRIPT_MAX_BYTES:
        raise TranscriptRegistrationError(
            "invalid_configuration",
            f"PLANIX_TRANSCRIPT_MAX_BYTES must be between 1 and {HARD_TRANSCRIPT_MAX_BYTES}",
        )
    return value


class LearningTranscriptRegistrationService:
    def __init__(
        self,
        video_provider: object,
        repository: LearningTranscriptRepository,
        *,
        validator: TranscriptValidator | None = None,
        maximum_bytes: int | None = None,
    ):
        self.video_provider = video_provider
        self.repository = repository
        self.validator = validator or TranscriptValidator()
        self.maximum_bytes = (
            configured_transcript_max_bytes()
            if maximum_bytes is None
            else maximum_bytes
        )
        if self.maximum_bytes < 1 or self.maximum_bytes > HARD_TRANSCRIPT_MAX_BYTES:
            raise TranscriptRegistrationError(
                "invalid_configuration",
                "transcript upload size limit is outside the safe range",
            )

    def register(
        self,
        *,
        video_url: str,
        source_format: TranscriptFormat,
        language: str,
        content: str,
        source_name: str | None = None,
    ) -> TranscriptSourceRecord:
        encoded = self._utf8_bytes(content)
        if len(encoded) > self.maximum_bytes:
            raise TranscriptRegistrationError(
                "payload_too_large",
                f"subtitle text exceeds the {self.maximum_bytes}-byte limit",
            )
        safe_name = self._source_name(source_name, source_format)
        resource = self._resolve_resource(video_url)
        try:
            normalized_text, cues = SubtitleFileTranscriptProvider.parse_upload(
                source_format,
                content,
            )
            source_checksum = "sha256:" + sha256(
                normalized_text.encode("utf-8")
            ).hexdigest()
            source_id = self.repository.source_id_for(
                resource.id,
                "srt_vtt",
                safe_name,
            )
            document = self._document(
                resource,
                cues,
                source_id=source_id,
                source_name=safe_name,
                source_format=source_format,
                source_checksum=source_checksum,
                language=language.strip(),
            )
            self.validator.validate(resource, document)
        except TranscriptProviderError as exc:
            raise TranscriptRegistrationError(
                "invalid_transcript",
                str(exc),
            ) from exc
        segments = [
            NormalizedTranscriptSegment(
                segmentIndex=index,
                startMs=cue.start_ms,
                endMs=cue.end_ms,
                text=cue.text,
                textChecksum="sha256:" + sha256(cue.text.encode("utf-8")).hexdigest(),
            )
            for index, cue in enumerate(cues)
        ]
        return self.repository.register(
            resource,
            source_type="srt_vtt",
            source_format=source_format,
            source_name=safe_name,
            language=language.strip(),
            source_checksum=source_checksum,
            segments=segments,
        )

    def get_metadata(self, source_id: str) -> TranscriptSourceSummary | None:
        return self.repository.get_source_metadata(source_id)

    def revoke(self, source_id: str) -> bool:
        return self.repository.revoke_source(source_id)

    def _resolve_resource(self, video_url: str) -> VideoResource:
        resolver = getattr(self.video_provider, "resolve_url", None)
        if not callable(resolver):
            raise TranscriptRegistrationError(
                "provider_unavailable",
                "video provider does not support verified URL resolution",
            )
        try:
            resource = resolver(video_url)
        except VideoSourceProviderError as exc:
            raise TranscriptRegistrationError(
                "invalid_video_url",
                str(exc),
            ) from exc
        if not isinstance(resource, VideoResource):
            raise TranscriptRegistrationError(
                "provider_identity_error",
                "video provider did not return verified metadata",
            )
        return resource

    @staticmethod
    def _document(
        resource: VideoResource,
        cues: list[ParsedSubtitleCue],
        *,
        source_id: str,
        source_name: str,
        source_format: TranscriptFormat,
        source_checksum: str,
        language: str,
    ) -> TranscriptDocument:
        return TranscriptDocument(
            resourceId=resource.id,
            fingerprint=resource.content_fingerprint,
            language=language,
            segments=SubtitleFileTranscriptProvider.segments_from_cues(
                cues,
                source_id,
            ),
            sourceMetadata=TranscriptSourceMetadata(
                sourceType=source_format,
                sourceId=source_id,
                sourceName=source_name,
                checksum=source_checksum.removeprefix("sha256:"),
                authorized=True,
            ),
        )

    @staticmethod
    def _utf8_bytes(content: str) -> bytes:
        try:
            return content.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise TranscriptRegistrationError(
                "invalid_encoding",
                "subtitle text must be valid UTF-8",
            ) from exc

    @staticmethod
    def _source_name(value: str | None, source_format: TranscriptFormat) -> str:
        name = (value or f"transcript.{source_format}").strip()
        parsed = urlsplit(name)
        if (
            not name
            or parsed.scheme
            or "/" in name
            or "\\" in name
            or ".." in name
            or not _SAFE_SOURCE_NAME.fullmatch(name)
        ):
            raise TranscriptRegistrationError(
                "invalid_source_name",
                "source name must be a simple local label without a path or URL",
            )
        if not name.casefold().endswith(f".{source_format}"):
            raise TranscriptRegistrationError(
                "invalid_source_name",
                f"source name must end with .{source_format}",
            )
        return name


__all__ = [
    "DEFAULT_TRANSCRIPT_MAX_BYTES",
    "HARD_TRANSCRIPT_MAX_BYTES",
    "LearningTranscriptRegistrationService",
    "TranscriptRegistrationError",
    "configured_transcript_max_bytes",
]
