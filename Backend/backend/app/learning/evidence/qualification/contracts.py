from __future__ import annotations

from typing import Literal

from pydantic import Field

from ...contracts import LearningContract, VideoResource


QualificationStatus = Literal["qualified", "rejected", "warning"]


class QualificationCheck(LearningContract):
    name: str = Field(min_length=1)
    passed: bool
    blocking: bool
    reason: str = Field(min_length=1)


class QualifiedCandidate(LearningContract):
    candidate_id: str = Field(min_length=1)
    resource: VideoResource | None = None
    qualification_status: QualificationStatus
    checks: list[QualificationCheck] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)


__all__ = [
    "QualificationCheck",
    "QualificationStatus",
    "QualifiedCandidate",
]
