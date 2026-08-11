from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ...contracts import LearningContract, VideoResource


TranscriptSourceType = Literal["authorized", "srt_vtt"]
TranscriptFormat = Literal["srt", "vtt"]
TranscriptSourceStatus = Literal["active", "stale", "invalid", "revoked"]


class NormalizedTranscriptSegment(LearningContract):
    segment_index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)
    text_checksum: str = Field(min_length=1)

    @model_validator(mode="after")
    def ordered_range(self) -> "NormalizedTranscriptSegment":
        if self.end_ms <= self.start_ms:
            raise ValueError("transcript segment end must be after start")
        if not self.text.strip():
            raise ValueError("transcript segment text must not be blank")
        return self


class TranscriptSourceRecord(LearningContract):
    source_id: str = Field(min_length=1)
    resource: VideoResource
    source_type: TranscriptSourceType
    source_format: TranscriptFormat
    source_name: str = Field(min_length=1)
    language: str = ""
    source_checksum: str = Field(min_length=1)
    authorization_status: Literal["authorized"] = "authorized"
    status: TranscriptSourceStatus
    segments: list[NormalizedTranscriptSegment] = Field(default_factory=list)
    created_at: str
    updated_at: str


class TranscriptSourceSummary(LearningContract):
    source_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    resource_fingerprint: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    canonical_url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_type: TranscriptSourceType
    source_format: TranscriptFormat
    source_name: str = Field(min_length=1)
    language: str = ""
    checksum_prefix: str = Field(min_length=8, max_length=16)
    authorization_status: Literal["authorized"] = "authorized"
    status: TranscriptSourceStatus
    segment_count: int = Field(ge=0)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    created_at: str


__all__ = [
    "NormalizedTranscriptSegment",
    "TranscriptFormat",
    "TranscriptSourceRecord",
    "TranscriptSourceStatus",
    "TranscriptSourceSummary",
    "TranscriptSourceType",
]
