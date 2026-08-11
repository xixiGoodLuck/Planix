from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol

from ..artifact_store import ArtifactStore, CheckpointStore
from ..contracts import LearningRunCheckpoint
from .execution import (
    ArtifactBundle,
    ResumeExecutionResult,
    ValidatedStageContext,
)
from .resume_coordinator import LearningResumeCoordinator, ResumeEvent
from .stage_registry import LearningStageRegistry
from .validators import LearningResumeCommitValidator


class ResumeCommitStore(ArtifactStore, CheckpointStore, Protocol):
    def atomic(self) -> AbstractContextManager[Any]: ...

    def save_resume_event(self, event: ResumeEvent) -> None: ...


class ResumeCommitService:
    def __init__(
        self,
        coordinator: LearningResumeCoordinator,
        store: ResumeCommitStore,
        *,
        registry: LearningStageRegistry | None = None,
        validator: LearningResumeCommitValidator | None = None,
    ):
        self.coordinator = coordinator
        self.store = store
        self.registry = registry or coordinator.registry
        self.validator = validator or LearningResumeCommitValidator()

    def execute(self, run_id: str) -> ResumeExecutionResult:
        checkpoint_before = self.store.get_checkpoint(run_id)
        decision, artifacts = self.coordinator.prepare(run_id)
        stage_name = decision.resume_stage or "resume"
        if not decision.allowed or decision.resume_stage is None:
            return self._failed(
                run_id,
                stage_name,
                checkpoint_before,
                decision.reason,
            )

        stage = self.registry.get(decision.resume_stage)
        if stage.executor is None:
            return self._failed(
                run_id,
                stage.stage_name,
                checkpoint_before,
                f"No executor registered for {stage.stage_name}.",
            )

        try:
            bundle = stage.executor(
                ValidatedStageContext(
                    run_id=run_id,
                    stage=stage.stage_name,
                    artifacts=artifacts,
                )
            )
            if not isinstance(bundle, ArtifactBundle):
                raise TypeError("stage executor must return ArtifactBundle")
            if checkpoint_before is None:
                raise ValueError("Learning checkpoint not found before commit")
            with self.store.atomic():
                current_checkpoint = self.store.get_checkpoint(run_id)
                if current_checkpoint != checkpoint_before:
                    raise ValueError("Learning checkpoint changed before commit")
                self.validator.validate_transition(
                    checkpoint_before,
                    stage,
                    self.registry,
                )
                self.validator.validate_bundle(stage, bundle, artifacts)
                new_refs = [
                    self.store.save_artifact(run_id, artifact)
                    for artifact in bundle.artifacts
                ]
                checkpoint_after = self._advanced_checkpoint(
                    checkpoint_before,
                    stage.stage_name,
                    new_refs,
                )
                self.store.save_checkpoint(checkpoint_after)
                event = ResumeEvent(
                    runId=run_id,
                    previousStage=decision.previous_stage,
                    resumeStage=stage.stage_name,
                    reason=f"Atomically committed resumed stage {stage.stage_name}.",
                    artifactRefs=checkpoint_after.artifact_refs,
                )
                self.store.save_resume_event(event)
                self.validator.validate_commit(
                    run_id=run_id,
                    stage=stage,
                    checkpoint_before=checkpoint_before,
                    checkpoint_after=checkpoint_after,
                    new_refs=new_refs,
                    audit_event=event,
                )
            return ResumeExecutionResult(
                runId=run_id,
                stage=stage.stage_name,
                status="completed",
                artifactRefs=new_refs,
                checkpointBefore=checkpoint_before,
                checkpointAfter=checkpoint_after,
                auditRef=event.event_id,
            )
        except Exception as exc:
            return self._failed(
                run_id,
                stage.stage_name,
                checkpoint_before,
                str(exc) or type(exc).__name__,
            )

    @staticmethod
    def _advanced_checkpoint(
        checkpoint: LearningRunCheckpoint,
        stage_name,
        new_refs,
    ) -> LearningRunCheckpoint:
        replacements = {ref.artifact_type: ref for ref in new_refs}
        refs = [
            replacements.pop(ref.artifact_type, ref)
            for ref in checkpoint.artifact_refs
        ]
        refs.extend(replacements.values())
        is_complete = stage_name == "quality"
        return LearningRunCheckpoint(
            runId=checkpoint.run_id,
            currentStage="completed" if is_complete else stage_name,
            status="completed" if is_complete else "running",
            artifactRefs=refs,
            lastSuccessfulStage=stage_name,
        )

    def _failed(
        self,
        run_id: str,
        stage: str,
        checkpoint_before: LearningRunCheckpoint | None,
        error: str,
    ) -> ResumeExecutionResult:
        return ResumeExecutionResult(
            runId=run_id,
            stage=stage,
            status="failed",
            artifactRefs=[],
            checkpointBefore=checkpoint_before,
            checkpointAfter=self.store.get_checkpoint(run_id),
            auditRef=None,
            error=error,
        )


__all__ = ["ResumeCommitService", "ResumeCommitStore"]
