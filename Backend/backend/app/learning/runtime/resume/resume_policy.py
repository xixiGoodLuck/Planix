from __future__ import annotations

from pydantic import Field

from ...contracts import LearningArtifactRef, LearningContract
from ..contracts import LearningRunCheckpoint
from .stage_registry import LearningStageName, LearningStageRegistry, StageArtifacts
from .validators import LearningResumeValidationError, LearningResumeValidator, normalize_stage


class ResumeDecision(LearningContract):
    allowed: bool
    previous_stage: LearningStageName | None = None
    resume_stage: LearningStageName | None = None
    validated_artifacts: list[LearningArtifactRef] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class LearningResumePolicy:
    def __init__(self, validator: LearningResumeValidator | None = None):
        self.validator = validator or LearningResumeValidator()

    def decide(
        self,
        checkpoint: LearningRunCheckpoint,
        registry: LearningStageRegistry,
        artifacts: StageArtifacts,
    ) -> ResumeDecision:
        try:
            self.validator.validate_checkpoint(checkpoint, registry, artifacts)
            previous = normalize_stage(checkpoint.last_successful_stage)
            next_stage = registry.next_after(previous)
            refs = self._validated_refs(checkpoint, artifacts)
            if next_stage is None:
                return ResumeDecision(
                    allowed=False,
                    previousStage=previous,
                    resumeStage=None,
                    validatedArtifacts=refs,
                    reason="Learning run is already complete.",
                )
            self.validator.validate_stage(next_stage, artifacts)
            return ResumeDecision(
                allowed=True,
                previousStage=previous,
                resumeStage=next_stage.stage_name,
                validatedArtifacts=refs,
                reason=(
                    f"Resume {next_stage.stage_name} after validated stage "
                    f"{previous or 'none'}."
                ),
            )
        except (LearningResumeValidationError, ValueError) as exc:
            return ResumeDecision(
                allowed=False,
                previousStage=normalize_stage(checkpoint.last_successful_stage),
                resumeStage=None,
                validatedArtifacts=[],
                reason=str(exc) or type(exc).__name__,
            )

    @staticmethod
    def _validated_refs(
        checkpoint: LearningRunCheckpoint,
        artifacts: StageArtifacts,
    ) -> list[LearningArtifactRef]:
        return [
            ref
            for ref in checkpoint.artifact_refs
            if ref.artifact_type in artifacts
        ]


__all__ = ["LearningResumePolicy", "ResumeDecision"]
