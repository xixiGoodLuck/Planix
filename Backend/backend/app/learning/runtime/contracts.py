from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from ..contracts import (
    LearningArtifactRef,
    LearningArtifactType,
    LearningContentPlan,
    EvidenceInterventionReport,
    LearningContract,
    LearningQualityReport,
)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


LearningSessionStage = Literal[
    "scope",
    "knowledge_generation",
    "evidence_generation",
    "coverage_analysis",
    "gap_completion",
    "selection",
    "quality",
    "completed",
    "failed",
    "waiting_evidence",
]
LearningSessionStatus = Literal["created", "running", "completed", "failed", "waiting_evidence"]
LearningProgressStatus = Literal["created", "started", "saved", "completed", "failed", "waiting_evidence"]
LearningProgressEventType = Literal[
    "session_created",
    "stage_started",
    "artifact_saved",
    "stage_completed",
    "session_completed",
    "session_failed",
    "session_waiting_evidence",
]


_LEGACY_STAGE_MAP = {
    "created": "scope",
    "understanding": "scope",
    "knowledge_generating": "knowledge_generation",
    "evidence_generating": "evidence_generation",
    "content_selecting": "selection",
    "quality_checking": "quality",
}


def canonical_stage(value):
    return _LEGACY_STAGE_MAP.get(value, value)


class LearningSessionError(LearningContract):
    stage: LearningSessionStage
    error_type: str = Field(min_length=1)
    message: str = Field(min_length=1)
    validator_rule: str = ""
    field_path: str = ""

    _normalize_stage = field_validator("stage", mode="before")(canonical_stage)


class LearningSessionState(LearningContract):
    session_id: str = Field(min_length=1)
    current_stage: LearningSessionStage = "scope"
    status: LearningSessionStatus = "created"
    completed_stages: list[LearningSessionStage] = Field(default_factory=list)
    current_artifact_ref: LearningArtifactRef | None = None
    error: LearningSessionError | None = None
    created_at: str = Field(default_factory=_utc_now)
    updated_at: str = Field(default_factory=_utc_now)

    _normalize_current_stage = field_validator("current_stage", mode="before")(canonical_stage)

    @field_validator("completed_stages", mode="before")
    @classmethod
    def normalize_completed_stages(cls, value):
        return [canonical_stage(item) for item in (value or [])]


class LearningProgressEvent(LearningContract):
    event_type: LearningProgressEventType
    stage: LearningSessionStage
    status: LearningProgressStatus
    message: str = Field(min_length=1)
    timestamp: str = Field(default_factory=_utc_now)

    _normalize_stage = field_validator("stage", mode="before")(canonical_stage)


class LearningRunResult(LearningContract):
    session: LearningSessionState
    artifacts: dict[LearningArtifactType, LearningArtifactRef]
    final_plan: LearningContentPlan
    quality_report: LearningQualityReport


class LearningWaitingEvidenceResult(LearningContract):
    session: LearningSessionState
    artifacts: dict[LearningArtifactType, LearningArtifactRef]
    intervention_report: EvidenceInterventionReport


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
    checkpoint_version: int = Field(default=1, ge=1)
    current_stage: LearningSessionStage
    status: LearningSessionStatus
    artifact_refs: list[LearningArtifactRef] = Field(default_factory=list)
    last_successful_stage: LearningSessionStage | None = None
    schema_version: Literal[1] = 1
    updated_at: str = Field(default_factory=_utc_now)

    _normalize_current_stage = field_validator("current_stage", mode="before")(canonical_stage)
    _normalize_last_stage = field_validator("last_successful_stage", mode="before")(canonical_stage)


__all__ = [
    "LearningArtifactEnvelope",
    "LearningProgressEvent",
    "LearningProgressEventType",
    "LearningProgressStatus",
    "LearningRunResult",
    "LearningWaitingEvidenceResult",
    "LearningRunCheckpoint",
    "LearningSessionError",
    "LearningSessionStage",
    "LearningSessionState",
    "LearningSessionStatus",
]
