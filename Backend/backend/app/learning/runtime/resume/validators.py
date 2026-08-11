from __future__ import annotations

from typing import Any, cast

from ...contracts import (
    CapabilityGraph,
    ContentSelection,
    EvidenceGraph,
    KnowledgeGraph,
    LearningArtifact,
    LearningArtifactRef,
    LearningArtifactType,
    LearningContentPlan,
    LearningQualityReport,
    LearningScope,
)
from ...validators import LearningArtifactValidator
from ..artifact_store import artifact_type_for
from ..contracts import LearningRunCheckpoint
from .execution import ArtifactBundle
from .stage_registry import LearningStage, LearningStageRegistry, StageArtifacts


class LearningResumeValidationError(ValueError):
    pass


class LearningResumeValidator:
    def validate_checkpoint(
        self,
        checkpoint: LearningRunCheckpoint,
        registry: LearningStageRegistry,
        artifacts: StageArtifacts,
    ) -> None:
        refs_by_type: dict[LearningArtifactType, LearningArtifactRef] = {}
        for ref in checkpoint.artifact_refs:
            if ref.artifact_type in refs_by_type:
                raise LearningResumeValidationError(
                    f"checkpoint contains duplicate artifact type: {ref.artifact_type}"
                )
            refs_by_type[ref.artifact_type] = ref
            artifact = artifacts.get(ref.artifact_type)
            if artifact is None:
                raise LearningResumeValidationError(
                    f"checkpoint artifact was not recovered: {ref.artifact_type}"
                )
            if artifact.artifact_id != ref.artifact_id or artifact.version != ref.version:
                raise LearningResumeValidationError(
                    f"checkpoint artifact ref mismatch: {ref.artifact_type}"
                )

        previous = normalize_stage(checkpoint.last_successful_stage)
        if previous is not None:
            for stage in registry.through(previous):
                missing = set(stage.output_artifacts) - set(artifacts)
                if missing:
                    raise LearningResumeValidationError(
                        f"checkpoint claims {stage.stage_name} completed without outputs: "
                        f"{sorted(missing)}"
                    )

        current = normalize_stage(checkpoint.current_stage)
        next_stage = registry.next_after(previous)
        if checkpoint.status == "completed":
            if previous != "quality" or next_stage is not None:
                raise LearningResumeValidationError(
                    "completed checkpoint does not contain a completed quality stage"
                )
        elif checkpoint.status == "running" and current not in {
            previous,
            next_stage.stage_name if next_stage is not None else None,
        }:
            raise LearningResumeValidationError(
                "running checkpoint current stage is not adjacent to its last success"
            )

    @staticmethod
    def validate_stage(stage: LearningStage, artifacts: StageArtifacts) -> None:
        missing = set(stage.input_artifacts) - set(artifacts)
        if missing:
            raise LearningResumeValidationError(
                f"cannot resume {stage.stage_name}; missing dependencies: {sorted(missing)}"
            )
        stage.validator(artifacts)


class LearningResumeCommitValidator:
    def __init__(
        self,
        artifact_validator: LearningArtifactValidator | None = None,
    ):
        self.artifact_validator = artifact_validator or LearningArtifactValidator()

    def validate_transition(
        self,
        checkpoint: LearningRunCheckpoint,
        stage: LearningStage,
        registry: LearningStageRegistry,
    ) -> None:
        previous = normalize_stage(checkpoint.last_successful_stage)
        expected = registry.next_after(previous)
        if expected is None or expected.stage_name != stage.stage_name:
            raise LearningResumeValidationError(
                f"checkpoint cannot advance from {previous} to {stage.stage_name}"
            )

    def validate_bundle(
        self,
        stage: LearningStage,
        bundle: ArtifactBundle,
        context: StageArtifacts,
    ) -> StageArtifacts:
        produced: dict[LearningArtifactType, LearningArtifact] = {}
        for artifact in bundle.artifacts:
            artifact_type = artifact_type_for(artifact)
            if artifact_type in produced:
                raise LearningResumeValidationError(
                    f"executor produced duplicate artifact type: {artifact_type}"
                )
            produced[artifact_type] = artifact
        if set(produced) != set(stage.output_artifacts):
            raise LearningResumeValidationError(
                f"executor outputs for {stage.stage_name} must be "
                f"{sorted(stage.output_artifacts)}"
            )

        combined = dict(context)
        for artifact_type in stage.output_artifacts:
            artifact = produced[artifact_type]
            previous = context.get(artifact_type)
            expected_version = (
                previous.version + 1
                if previous is not None
                and previous.artifact_id == artifact.artifact_id
                else 1
            )
            if artifact.version != expected_version:
                raise LearningResumeValidationError(
                    f"invalid {artifact_type} version: expected {expected_version}"
                )
            combined[artifact_type] = artifact
            self._validate_lineage(artifact_type, combined)
        return combined

    def validate_commit(
        self,
        *,
        run_id: str,
        stage: LearningStage,
        checkpoint_before: LearningRunCheckpoint,
        checkpoint_after: LearningRunCheckpoint,
        new_refs: list[LearningArtifactRef],
        audit_event: Any,
    ) -> None:
        if checkpoint_before.run_id != run_id or checkpoint_after.run_id != run_id:
            raise LearningResumeValidationError("checkpoint run_id mismatch")
        if normalize_stage(checkpoint_after.last_successful_stage) != stage.stage_name:
            raise LearningResumeValidationError(
                "checkpoint did not advance to the committed stage"
            )
        if stage.stage_name == "quality":
            if checkpoint_after.status != "completed":
                raise LearningResumeValidationError(
                    "quality commit must complete the checkpoint"
                )
        elif checkpoint_after.status != "running":
            raise LearningResumeValidationError(
                "non-final stage commit must leave the checkpoint running"
            )

        after_by_type = {
            ref.artifact_type: ref for ref in checkpoint_after.artifact_refs
        }
        if len(after_by_type) != len(checkpoint_after.artifact_refs):
            raise LearningResumeValidationError(
                "checkpoint contains duplicate artifact types after commit"
            )
        for ref in new_refs:
            if after_by_type.get(ref.artifact_type) != ref:
                raise LearningResumeValidationError(
                    f"checkpoint does not reference committed {ref.artifact_type}"
                )
        if audit_event.run_id != run_id or audit_event.resume_stage != stage.stage_name:
            raise LearningResumeValidationError("resume audit stage/run mismatch")
        if audit_event.artifact_refs != checkpoint_after.artifact_refs:
            raise LearningResumeValidationError(
                "resume audit refs do not match checkpoint refs"
            )

    def _validate_lineage(
        self,
        artifact_type: LearningArtifactType,
        artifacts: StageArtifacts,
    ) -> None:
        scope = self._get(artifacts, "learning_scope", LearningScope)
        if artifact_type == "learning_scope":
            return
        if artifact_type == "capability_graph":
            self.artifact_validator.validate_capability_graph(
                scope,
                cast(CapabilityGraph, artifacts[artifact_type]),
            )
            return
        capability = self._get(artifacts, "capability_graph", CapabilityGraph)
        if artifact_type == "knowledge_graph":
            self.artifact_validator.validate_knowledge_graph(
                scope,
                capability,
                cast(KnowledgeGraph, artifacts[artifact_type]),
            )
            return
        knowledge = self._get(artifacts, "knowledge_graph", KnowledgeGraph)
        if artifact_type == "evidence_graph":
            self.artifact_validator.validate_evidence_graph(
                knowledge,
                cast(EvidenceGraph, artifacts[artifact_type]),
            )
            return
        evidence = self._get(artifacts, "evidence_graph", EvidenceGraph)
        if artifact_type == "content_selection":
            self.artifact_validator.validate_content_selection(
                scope,
                knowledge,
                evidence,
                cast(ContentSelection, artifacts[artifact_type]),
            )
            return
        selection = self._get(artifacts, "content_selection", ContentSelection)
        if artifact_type == "learning_content_plan":
            self.artifact_validator.validate_content_plan(
                scope,
                knowledge,
                evidence,
                selection,
                cast(LearningContentPlan, artifacts[artifact_type]),
            )
            return
        plan = self._get(artifacts, "learning_content_plan", LearningContentPlan)
        report = cast(LearningQualityReport, artifacts[artifact_type])
        self.artifact_validator.validate_quality_report(
            scope,
            capability,
            knowledge,
            evidence,
            selection,
            plan,
            report,
        )
        if not report.passed:
            raise LearningResumeValidationError(
                "quality artifact cannot complete a failed quality gate"
            )

    @staticmethod
    def _get(
        artifacts: StageArtifacts,
        artifact_type: LearningArtifactType,
        expected_type: type[LearningArtifact],
    ):
        artifact = artifacts.get(artifact_type)
        if artifact is None or not isinstance(artifact, expected_type):
            raise LearningResumeValidationError(
                f"commit lineage dependency missing: {artifact_type}"
            )
        return artifact


_STAGE_ALIASES = {
    "understanding": "scope",
    "knowledge_generating": "knowledge_generation",
    "evidence_generating": "evidence_generation",
    "content_selecting": "selection",
    "quality_checking": "quality",
    "completed": "quality",
}


def normalize_stage(stage: str | None):
    if stage in {None, "created", "failed"}:
        return None
    return _STAGE_ALIASES.get(stage, stage)


__all__ = [
    "LearningResumeCommitValidator",
    "LearningResumeValidationError",
    "LearningResumeValidator",
    "normalize_stage",
]
