from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ....contracts import LearningContract
from ..providers import TranscriptDocument


TranscriptAcquisitionStatus = Literal["ACQUIRED", "TRANSCRIPT_UNAVAILABLE"]


class TranscriptAcquisitionResult(LearningContract):
    status: TranscriptAcquisitionStatus
    candidate_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    transcript: TranscriptDocument | None = None
    error: str | None = None

    @model_validator(mode="after")
    def coherent_result(self) -> "TranscriptAcquisitionResult":
        if self.status == "ACQUIRED":
            if self.transcript is None or self.error is not None:
                raise ValueError("ACQUIRED transcript result must contain only a transcript")
        elif self.transcript is not None or not self.error:
            raise ValueError("TRANSCRIPT_UNAVAILABLE result must contain only an error")
        return self


__all__ = ["TranscriptAcquisitionResult", "TranscriptAcquisitionStatus"]
