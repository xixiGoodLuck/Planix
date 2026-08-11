from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field, model_validator

from ....contracts import LearningContract, VideoResource


class TranscriptProviderError(RuntimeError):
    pass


class TranscriptSegment(LearningContract):
    """One raw transcript cue. It contains no inferred topic or knowledge data."""

    id: str = Field(min_length=1)
    start_seconds: int = Field(ge=0)
    end_seconds: int = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def ordered_range(self) -> "TranscriptSegment":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("transcript segment end must be after start")
        return self


class TranscriptSourceMetadata(LearningContract):
    source_type: Literal["authorized", "srt", "vtt"]
    source_id: str = Field(min_length=1)
    source_name: str = ""
    checksum: str = ""
    authorized: bool = True


class TranscriptDocument(LearningContract):
    resource_id: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)
    language: str = ""
    segments: list[TranscriptSegment] = Field(min_length=1)
    source_metadata: TranscriptSourceMetadata | None = None


class TranscriptProvider(Protocol):
    source_type: str

    def fetch_transcript(self, resource: VideoResource) -> TranscriptDocument: ...

    def health_check(self) -> bool: ...


__all__ = [
    "TranscriptDocument",
    "TranscriptProvider",
    "TranscriptProviderError",
    "TranscriptSegment",
    "TranscriptSourceMetadata",
]
