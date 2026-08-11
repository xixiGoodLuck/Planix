from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import Field

from ...contracts import LearningArtifactRef, LearningContract
from ..artifact_store import ArtifactStore, CheckpointStore
from ..contracts import LearningRunCheckpoint
from ..recovery import LearningRecoveryService
from .execution import ValidatedStageContext
from .resume_policy import LearningResumePolicy, ResumeDecision
from .stage_registry import LearningStageName, LearningStageRegistry, StageArtifacts
from .validators import normalize_stage


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ResumeEvent(LearningContract):
    event_id: str = Field(default_factory=lambda: f"resume-event-{uuid4()}")
    run_id: str = Field(min_length=1)
    previous_stage: LearningStageName | None = None
    resume_stage: LearningStageName | None = None
    reason: str = Field(min_length=1)
    artifact_refs: list[LearningArtifactRef] = Field(default_factory=list)
    checkpoint_before: LearningRunCheckpoint | None = None
    checkpoint_after: LearningRunCheckpoint | None = None
    timestamp: str = Field(default_factory=_utc_now)


class LearningResumeCoordinator:
    def __init__(
        self,
        artifact_store: ArtifactStore,
        checkpoint_store: CheckpointStore,
        *,
        registry: LearningStageRegistry | None = None,
        policy: LearningResumePolicy | None = None,
        recovery_service: LearningRecoveryService | None = None,
    ):
        self.registry = registry or LearningStageRegistry.default()
        self.policy = policy or LearningResumePolicy()
        self.recovery_service = recovery_service or LearningRecoveryService(
            artifact_store,
            checkpoint_store,
        )
        self._events: dict[str, list[ResumeEvent]] = defaultdict(list)

    def decide(self, run_id: str) -> ResumeDecision:
        decision, _ = self._evaluate(run_id)
        self._record(run_id, decision)
        return decision

    def resume(self, run_id: str) -> ResumeDecision:
        decision, artifacts = self._evaluate(run_id)
        if decision.allowed and decision.resume_stage is not None:
            stage = self.registry.get(decision.resume_stage)
            if stage.executor is None:
                decision = decision.model_copy(
                    update={
                        "allowed": False,
                        "resume_stage": None,
                        "reason": f"No executor registered for {stage.stage_name}.",
                    }
                )
            else:
                try:
                    stage.executor(
                        ValidatedStageContext(
                            run_id=run_id,
                            stage=stage.stage_name,
                            artifacts=artifacts,
                        )
                    )
                except Exception as exc:
                    decision = decision.model_copy(
                        update={
                            "allowed": False,
                            "resume_stage": None,
                            "reason": (
                                f"Resume executor failed for {stage.stage_name}: "
                                f"{str(exc) or type(exc).__name__}"
                            ),
                        }
                    )
        self._record(run_id, decision)
        return decision

    def get_events(self, run_id: str) -> list[ResumeEvent]:
        return [item.model_copy(deep=True) for item in self._events.get(run_id, [])]

    def prepare(self, run_id: str) -> tuple[ResumeDecision, StageArtifacts]:
        """Return a validated decision/context without executing or auditing it."""
        return self._evaluate(run_id)

    def _evaluate(self, run_id: str) -> tuple[ResumeDecision, StageArtifacts]:
        recovered = self.recovery_service.recover(run_id)
        if recovered.status != "recovered" or recovered.checkpoint is None:
            checkpoint = recovered.checkpoint
            previous = (
                normalize_stage(checkpoint.last_successful_stage)
                if checkpoint is not None
                else None
            )
            return (
                ResumeDecision(
                    allowed=False,
                    previousStage=previous,
                    resumeStage=None,
                    validatedArtifacts=[],
                    reason=f"Recovery validation failed: {recovered.error}",
                ),
                {},
            )
        artifacts = recovered.artifacts
        decision = self.policy.decide(
            recovered.checkpoint,
            self.registry,
            artifacts,
        )
        return decision, artifacts

    def _record(self, run_id: str, decision: ResumeDecision) -> None:
        self._events[run_id].append(
            ResumeEvent(
                runId=run_id,
                previousStage=decision.previous_stage,
                resumeStage=decision.resume_stage,
                reason=decision.reason,
                artifactRefs=decision.validated_artifacts,
            )
        )


__all__ = ["LearningResumeCoordinator", "ResumeEvent"]
