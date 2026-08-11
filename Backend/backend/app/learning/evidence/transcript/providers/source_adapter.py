from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field

from ....contracts import LearningContract, VideoResource
from ..validators import TranscriptValidator
from .base import (
    TranscriptDocument,
    TranscriptProviderError,
    TranscriptSegment,
    TranscriptSourceMetadata,
)


class AuthorizedTranscriptPayload(LearningContract):
    resource_id: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_name: str = ""
    language: str = ""
    authorized: Literal[True] = True
    segments: list[TranscriptSegment] = Field(min_length=1)


class AuthorizedTranscriptSource(Protocol):
    def fetch(self, resource: VideoResource) -> AuthorizedTranscriptPayload: ...


class TranscriptSourceAdapter:
    """Adapts one authorized source without inferring timestamps or identity."""

    source_type = "authorized"

    def __init__(
        self,
        source: AuthorizedTranscriptSource,
        *,
        validator: TranscriptValidator | None = None,
    ):
        self.source = source
        self.validator = validator or TranscriptValidator()

    def fetch_transcript(self, resource: VideoResource) -> TranscriptDocument:
        try:
            payload = self.source.fetch(resource)
        except TranscriptProviderError:
            raise
        except Exception as exc:
            raise TranscriptProviderError("authorized transcript source failed") from exc
        if payload.resource_id != resource.id:
            raise TranscriptProviderError(
                "authorized transcript references a different video resource"
            )
        if payload.fingerprint != resource.content_fingerprint:
            raise TranscriptProviderError(
                "authorized transcript fingerprint does not match the video resource"
            )
        document = TranscriptDocument(
            resourceId=payload.resource_id,
            fingerprint=payload.fingerprint,
            language=payload.language,
            segments=payload.segments,
            sourceMetadata=TranscriptSourceMetadata(
                sourceType="authorized",
                sourceId=payload.source_id,
                sourceName=payload.source_name,
                authorized=True,
            ),
        )
        self.validator.validate(resource, document)
        return document

    def health_check(self) -> bool:
        check = getattr(self.source, "health_check", None)
        return check() is not False if callable(check) else True


__all__ = [
    "AuthorizedTranscriptPayload",
    "AuthorizedTranscriptSource",
    "TranscriptSourceAdapter",
]
