from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, cast

from ..contracts import (
    CapabilityGraph,
    ContentSelection,
    EvidenceGraph,
    EvidenceInterventionReport,
    KnowledgeGraph,
    LearningArtifact,
    LearningArtifactRef,
    LearningArtifactType,
    LearningContentPlan,
    LearningQualityReport,
    LearningScope,
)
from ..validators import LearningArtifactValidator
from .artifact_store import ArtifactStore, CheckpointStore
from .contracts import LearningRunCheckpoint


LearningRecoveryStatus = Literal["recovered", "failed"]
_TYPE_ORDER: tuple[LearningArtifactType, ...] = (
    "learning_scope",
    "capability_graph",
    "knowledge_graph",
    "evidence_graph",
    "evidence_intervention_report",
    "content_selection",
    "learning_content_plan",
    "learning_quality_report",
)


@dataclass(frozen=True)
class LearningRecoveryResult:
    run_id: str
    status: LearningRecoveryStatus
    checkpoint: LearningRunCheckpoint | None
    artifacts: dict[LearningArtifactType, LearningArtifact] = field(default_factory=dict)
    error: str = ""


class LearningRecoveryService:
    def __init__(
        self,
        artifact_store: ArtifactStore,
        checkpoint_store: CheckpointStore,
        *,
        validator: LearningArtifactValidator | None = None,
    ):
        self.artifact_store = artifact_store
        self.checkpoint_store = checkpoint_store
        self.validator = validator or LearningArtifactValidator()

    def recover(self, run_id: str) -> LearningRecoveryResult:
        checkpoint = self.checkpoint_store.get_checkpoint(run_id)
        if checkpoint is None:
            return LearningRecoveryResult(
                run_id=run_id,
                status="failed",
                checkpoint=None,
                error="Learning checkpoint not found",
            )

        latest_refs = self._latest_refs(checkpoint.artifact_refs)
        artifacts: dict[LearningArtifactType, LearningArtifact] = {}
        current_ref: LearningArtifactRef | None = None
        try:
            for artifact_type in _TYPE_ORDER:
                current_ref = latest_refs.get(artifact_type)
                if current_ref is None:
                    continue
                if not self.artifact_store.exists(run_id, current_ref):
                    raise ValueError(
                        f"checkpoint references missing artifact: {artifact_type}"
                    )
                artifact = self.artifact_store.get_artifact(run_id, current_ref)
                if artifact is None:
                    raise ValueError(
                        f"checkpoint references missing artifact: {artifact_type}"
                    )
                artifacts[artifact_type] = artifact
                self._validate_artifact(artifact_type, artifacts)
            return LearningRecoveryResult(
                run_id=run_id,
                status="recovered",
                checkpoint=checkpoint,
                artifacts=artifacts,
            )
        except Exception as exc:
            if current_ref is not None:
                def validate_current(artifact: LearningArtifact) -> None:
                    candidate_artifacts = dict(artifacts)
                    candidate_artifacts[current_ref.artifact_type] = artifact
                    self._validate_artifact(
                        current_ref.artifact_type,
                        candidate_artifacts,
                    )

                self.artifact_store.delete_if_invalid(
                    run_id,
                    current_ref,
                    validate_current,
                )
                artifacts.pop(current_ref.artifact_type, None)
            valid_refs = [
                ref
                for artifact_type in _TYPE_ORDER
                if (ref := latest_refs.get(artifact_type)) is not None
                and artifact_type in artifacts
            ]
            failed_checkpoint = checkpoint.model_copy(
                update={
                    "current_stage": "failed",
                    "status": "failed",
                    "artifact_refs": valid_refs,
                }
            )
            self.checkpoint_store.save_checkpoint(failed_checkpoint)
            return LearningRecoveryResult(
                run_id=run_id,
                status="failed",
                checkpoint=failed_checkpoint,
                artifacts=artifacts,
                error=str(exc) or type(exc).__name__,
            )

    @staticmethod
    def _latest_refs(
        refs: list[LearningArtifactRef],
    ) -> dict[LearningArtifactType, LearningArtifactRef]:
        latest: dict[LearningArtifactType, LearningArtifactRef] = {}
        for ref in refs:
            existing = latest.get(ref.artifact_type)
            if existing is None or ref.version > existing.version:
                latest[ref.artifact_type] = ref
        return latest

    def _validate_artifact(
        self,
        artifact_type: LearningArtifactType,
        artifacts: dict[LearningArtifactType, LearningArtifact],
    ) -> None:
        if artifact_type == "learning_scope":
            cast(LearningScope, artifacts[artifact_type])
            return
        scope = self._require(artifacts, "learning_scope", LearningScope)
        if artifact_type == "capability_graph":
            self.validator.validate_capability_graph(
                scope,
                cast(CapabilityGraph, artifacts[artifact_type]),
            )
            return
        capability = self._require(artifacts, "capability_graph", CapabilityGraph)
        if artifact_type == "knowledge_graph":
            self.validator.validate_knowledge_graph(
                scope,
                capability,
                cast(KnowledgeGraph, artifacts[artifact_type]),
            )
            return
        knowledge = self._require(artifacts, "knowledge_graph", KnowledgeGraph)
        if artifact_type == "evidence_intervention_report":
            intervention = cast(EvidenceInterventionReport, artifacts[artifact_type])
            if intervention.knowledge_graph_ref.artifact_id != knowledge.artifact_id:
                raise ValueError("intervention references another knowledge graph")
            evidence = artifacts.get("evidence_graph")
            if intervention.evidence_graph_ref is not None:
                if not isinstance(evidence, EvidenceGraph):
                    raise ValueError("intervention evidence dependency missing")
                if (
                    intervention.evidence_graph_ref.artifact_id != evidence.artifact_id
                    or intervention.evidence_graph_ref.version != evidence.version
                ):
                    raise ValueError("intervention references another evidence graph")
            knowledge_ids = {item.id for item in knowledge.nodes}
            coverage_ids = [
                item.knowledge_id for item in intervention.knowledge_coverage
            ]
            if len(coverage_ids) != len(set(coverage_ids)) or not set(
                coverage_ids
            ) <= knowledge_ids:
                raise ValueError("intervention coverage references invalid knowledge")
            if not {
                item.knowledge_id for item in intervention.required_gaps
            } <= knowledge_ids:
                raise ValueError("intervention gaps reference invalid knowledge")
            return
        if artifact_type == "evidence_graph":
            self.validator.validate_evidence_graph(
                knowledge,
                cast(EvidenceGraph, artifacts[artifact_type]),
            )
            return
        evidence = self._require(artifacts, "evidence_graph", EvidenceGraph)
        if artifact_type == "content_selection":
            self.validator.validate_content_selection(
                scope,
                knowledge,
                evidence,
                cast(ContentSelection, artifacts[artifact_type]),
            )
            return
        selection = self._require(artifacts, "content_selection", ContentSelection)
        if artifact_type == "learning_content_plan":
            self.validator.validate_content_plan(
                scope,
                knowledge,
                evidence,
                selection,
                cast(LearningContentPlan, artifacts[artifact_type]),
            )
            return
        plan = self._require(artifacts, "learning_content_plan", LearningContentPlan)
        self.validator.validate_quality_report(
            scope,
            capability,
            knowledge,
            evidence,
            selection,
            plan,
            cast(LearningQualityReport, artifacts[artifact_type]),
        )

    @staticmethod
    def _require(
        artifacts: dict[LearningArtifactType, LearningArtifact],
        artifact_type: LearningArtifactType,
        expected_type: type[LearningArtifact],
    ):
        artifact = artifacts.get(artifact_type)
        if artifact is None or not isinstance(artifact, expected_type):
            raise ValueError(f"recovery dependency missing: {artifact_type}")
        return artifact


__all__ = [
    "LearningRecoveryResult",
    "LearningRecoveryService",
    "LearningRecoveryStatus",
]
