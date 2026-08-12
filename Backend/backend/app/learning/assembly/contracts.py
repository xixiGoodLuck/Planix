from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from ..contracts import (
    EvidenceInterventionReport,
    LearningContentPlan,
    LearningContract,
    LearningQualityReport,
    LearningScope,
)


LearningPipelineStatus = Literal["running", "completed", "failed", "waiting_evidence"]
PipelineArtifactType = Literal[
    "learning_scope",
    "capability_graph",
    "knowledge_graph",
    "evidence_graph",
    "evidence_intervention_report",
    "coverage_report",
    "content_selection",
    "learning_content_plan",
    "learning_quality_report",
]


class LearningPipelineRequest(LearningContract):
    scope: LearningScope
    provider_key: str = Field(min_length=1)

    @field_validator("provider_key")
    @classmethod
    def valid_provider_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("provider key must not be blank")
        return normalized


class PipelineArtifactRef(LearningContract):
    artifact_type: PipelineArtifactType
    artifact_id: str = Field(min_length=1)
    version: int = Field(ge=1)


class LearningPipelineStageError(LearningContract):
    stage: str = Field(min_length=1)
    error_type: str = Field(min_length=1)
    message: str = Field(min_length=1)


class LearningPipelineRunResult(LearningContract):
    run_id: str = Field(min_length=1)
    run_fingerprint: str = Field(min_length=1)
    pipeline_version: str = Field(min_length=1)
    status: LearningPipelineStatus
    artifact_refs: list[PipelineArtifactRef] = Field(default_factory=list)
    quality_report: LearningQualityReport | None = None
    final_plan: LearningContentPlan | None = None
    intervention_report: EvidenceInterventionReport | None = None
    error: LearningPipelineStageError | None = None


__all__ = [
    "LearningPipelineRequest",
    "LearningPipelineRunResult",
    "LearningPipelineStageError",
    "LearningPipelineStatus",
    "PipelineArtifactRef",
    "PipelineArtifactType",
]
