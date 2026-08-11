from __future__ import annotations

from hashlib import sha256
import json

from ..contracts import (
    CapabilityGraph,
    ContentSelection,
    EvidenceGraph,
    KnowledgeGraph,
    LearningContentPlan,
    LearningQualityReport,
)
from ..evidence.coverage import CoverageReport
from ..generators.base import artifact_ref, generated_id
from .contracts import (
    LearningPipelineRequest,
    LearningPipelineRunResult,
    PipelineArtifactRef,
    PipelineArtifactType,
)


class LearningPipelineAssemblyValidationError(ValueError):
    def __init__(self, rule: str, path: str, message: str):
        self.rule = rule
        self.path = path
        self.message = message
        super().__init__(f"{rule} [{path}]: {message}")


def pipeline_artifact_ref(
    artifact_type: PipelineArtifactType,
    artifact,
) -> PipelineArtifactRef:
    return PipelineArtifactRef(
        artifactType=artifact_type,
        artifactId=artifact.artifact_id,
        version=artifact.version,
    )


def coverage_artifact_ref(report: CoverageReport) -> PipelineArtifactRef:
    return PipelineArtifactRef(
        artifactType="coverage_report",
        artifactId=generated_id(
            "coverage-report",
            report.evidence_graph_ref.artifact_id,
            report.evidence_graph_ref.version,
            report.knowledge_graph_ref.artifact_id,
        ),
        version=report.evidence_graph_ref.version,
    )


def learning_run_fingerprint(
    request: LearningPipelineRequest,
    pipeline_version: str,
) -> str:
    scope = request.scope.model_dump(mode="json", by_alias=True)
    scope.pop("createdAt", None)
    canonical = json.dumps(
        {
            "scope": scope,
            "providerKey": request.provider_key.strip(),
            "pipelineVersion": pipeline_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + sha256(canonical.encode("utf-8")).hexdigest()


class LearningPipelineAssemblyValidator:
    def validate_completed(
        self,
        request: LearningPipelineRequest,
        result: LearningPipelineRunResult,
        *,
        expected_fingerprint: str,
        capability_graph: CapabilityGraph,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        coverage_report: CoverageReport,
        content_selection: ContentSelection,
        final_plan: LearningContentPlan,
        quality_report: LearningQualityReport,
    ) -> None:
        if result.status != "completed" or result.error is not None:
            self._fail(
                "completed_status",
                "status",
                "completed assembly result cannot contain an error",
            )
        if result.run_fingerprint != expected_fingerprint:
            self._fail(
                "run_fingerprint",
                "runFingerprint",
                "run fingerprint does not match request, provider, and pipeline version",
            )
        if result.final_plan != final_plan or result.quality_report != quality_report:
            self._fail(
                "final_output",
                "result",
                "result does not contain the validated final plan and quality report",
            )
        if not quality_report.passed:
            self._fail(
                "quality_gate",
                "qualityReport",
                "completed assembly requires a passed quality report",
            )

        scope_ref = pipeline_artifact_ref("learning_scope", request.scope)
        capability_ref = pipeline_artifact_ref("capability_graph", capability_graph)
        knowledge_ref = pipeline_artifact_ref("knowledge_graph", knowledge_graph)
        evidence_ref = pipeline_artifact_ref("evidence_graph", evidence_graph)
        coverage_ref = coverage_artifact_ref(coverage_report)
        selection_ref = pipeline_artifact_ref(
            "content_selection",
            content_selection,
        )
        plan_ref = pipeline_artifact_ref("learning_content_plan", final_plan)
        quality_ref = pipeline_artifact_ref(
            "learning_quality_report",
            quality_report,
        )
        required_refs = {
            self._ref_key(item)
            for item in (
                scope_ref,
                capability_ref,
                knowledge_ref,
                evidence_ref,
                coverage_ref,
                selection_ref,
                plan_ref,
                quality_ref,
            )
        }
        actual_keys = [self._ref_key(item) for item in result.artifact_refs]
        if len(actual_keys) != len(set(actual_keys)):
            self._fail(
                "artifact_ref_conflict",
                "artifactRefs",
                "artifact refs contain duplicate type/id/version entries",
            )
        if not required_refs <= set(actual_keys):
            self._fail(
                "artifact_lineage",
                "artifactRefs",
                "artifact refs do not contain the complete final lineage",
            )

        scope_learning_ref = artifact_ref("learning_scope", request.scope)
        capability_learning_ref = artifact_ref(
            "capability_graph",
            capability_graph,
        )
        knowledge_learning_ref = artifact_ref("knowledge_graph", knowledge_graph)
        evidence_learning_ref = artifact_ref("evidence_graph", evidence_graph)
        selection_learning_ref = artifact_ref(
            "content_selection",
            content_selection,
        )
        plan_learning_ref = artifact_ref("learning_content_plan", final_plan)
        if capability_graph.scope_ref != scope_learning_ref:
            self._fail("artifact_lineage", "capabilityGraph.scopeRef", "scope lineage is broken")
        if (
            knowledge_graph.scope_ref != scope_learning_ref
            or knowledge_graph.capability_graph_ref != capability_learning_ref
        ):
            self._fail("artifact_lineage", "knowledgeGraph", "knowledge lineage is broken")
        if evidence_graph.knowledge_graph_ref != knowledge_learning_ref:
            self._fail("artifact_lineage", "evidenceGraph", "evidence lineage is broken")
        if (
            coverage_report.knowledge_graph_ref != knowledge_learning_ref
            or coverage_report.evidence_graph_ref != evidence_learning_ref
        ):
            self._fail("artifact_lineage", "coverageReport", "coverage lineage is broken")
        if (
            content_selection.scope_ref != scope_learning_ref
            or content_selection.knowledge_graph_ref != knowledge_learning_ref
            or content_selection.evidence_graph_ref != evidence_learning_ref
        ):
            self._fail("artifact_lineage", "contentSelection", "selection lineage is broken")
        if (
            final_plan.scope_ref != scope_learning_ref
            or final_plan.knowledge_graph_ref != knowledge_learning_ref
            or final_plan.evidence_graph_ref != evidence_learning_ref
            or final_plan.content_selection_ref != selection_learning_ref
        ):
            self._fail("artifact_lineage", "finalPlan", "final plan lineage is broken")
        if (
            quality_report.target_ref != plan_learning_ref
            or quality_report.scope_ref != scope_learning_ref
            or quality_report.capability_graph_ref != capability_learning_ref
            or quality_report.knowledge_graph_ref != knowledge_learning_ref
            or quality_report.evidence_graph_ref != evidence_learning_ref
            or quality_report.content_selection_ref != selection_learning_ref
        ):
            self._fail("artifact_lineage", "qualityReport", "quality lineage is broken")

    def validate_failed(
        self,
        result: LearningPipelineRunResult,
        *,
        expected_fingerprint: str,
    ) -> None:
        if result.status != "failed" or result.error is None:
            self._fail(
                "failed_status",
                "status",
                "failed assembly result requires a stage error",
            )
        if result.run_fingerprint != expected_fingerprint:
            self._fail(
                "run_fingerprint",
                "runFingerprint",
                "failed result fingerprint does not match the request",
            )
        keys = [self._ref_key(item) for item in result.artifact_refs]
        if len(keys) != len(set(keys)):
            self._fail(
                "artifact_ref_conflict",
                "artifactRefs",
                "failed result contains conflicting artifact refs",
            )

    @staticmethod
    def _ref_key(item: PipelineArtifactRef) -> tuple[str, str, int]:
        return item.artifact_type, item.artifact_id, item.version

    @staticmethod
    def _fail(rule: str, path: str, message: str) -> None:
        raise LearningPipelineAssemblyValidationError(rule, path, message)


__all__ = [
    "LearningPipelineAssemblyValidationError",
    "LearningPipelineAssemblyValidator",
    "coverage_artifact_ref",
    "learning_run_fingerprint",
    "pipeline_artifact_ref",
]
