from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field

from ..contracts import (
    LearningArtifactRef,
    LearningArtifactType,
    LearningContentPlan,
    LearningContract,
    LearningQualityReport,
)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


LearningSessionStage = Literal[
    "created",
    "scope",
    "understanding",
    "knowledge_generation",
    "knowledge_generating",
    "evidence_generation",
    "evidence_generating",
    "coverage_analysis",
    "gap_completion",
    "selection",
    "content_selecting",
    "quality",
    "quality_checking",
    "completed",
    "failed",
]
LearningSessionStatus = Literal["created", "running", "completed", "failed"]
LearningProgressStatus = Literal["created", "started", "saved", "completed", "failed"]
LearningProgressEventType = Literal[
    "session_created",
    "stage_started",
    "artifact_saved",
    "stage_completed",
    "session_completed",
    "session_failed",
]


class LearningSessionError(LearningContract):
    stage: LearningSessionStage
    error_type: str = Field(min_length=1)
    message: str = Field(min_length=1)
    validator_rule: str = ""
    field_path: str = ""


class LearningSessionState(LearningContract):
    session_id: str = Field(min_length=1)
    current_stage: LearningSessionStage = "created"
    status: LearningSessionStatus = "created"
    completed_stages: list[LearningSessionStage] = Field(default_factory=list)
    current_artifact_ref: LearningArtifactRef | None = None
    error: LearningSessionError | None = None
    created_at: str = Field(default_factory=_utc_now)
    updated_at: str = Field(default_factory=_utc_now)


class LearningProgressEvent(LearningContract):
    event_type: LearningProgressEventType
    stage: LearningSessionStage
    status: LearningProgressStatus
    message: str = Field(min_length=1)
    timestamp: str = Field(default_factory=_utc_now)


class LearningRunResult(LearningContract):
    session: LearningSessionState
    artifacts: dict[LearningArtifactType, LearningArtifactRef]
    final_plan: LearningContentPlan
    quality_report: LearningQualityReport


class LearningArtifactEnvelope(LearningContract):
    artifact_type: LearningArtifactType
    artifact_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    session_id: str = Field(min_length=1)
    schema_version: int = Field(ge=1)
    created_at: str = Field(default_factory=_utc_now)
    content: dict[str, Any]


class LearningRunCheckpoint(LearningContract):
    run_id: str = Field(min_length=1)
    current_stage: LearningSessionStage
    status: LearningSessionStatus
    artifact_refs: list[LearningArtifactRef] = Field(default_factory=list)
    last_successful_stage: LearningSessionStage | None = None
    schema_version: Literal[1] = 1
    updated_at: str = Field(default_factory=_utc_now)


__all__ = [
    "LearningArtifactEnvelope",
    "LearningProgressEvent",
    "LearningProgressEventType",
    "LearningProgressStatus",
    "LearningRunResult",
    "LearningRunCheckpoint",
    "LearningSessionError",
    "LearningSessionStage",
    "LearningSessionState",
    "LearningSessionStatus",
]
