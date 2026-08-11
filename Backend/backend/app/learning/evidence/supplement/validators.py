from __future__ import annotations

from ...contracts import EvidenceGraph, KnowledgeGraph
from ...generators.base import artifact_ref
from ..coverage import CoverageReport, CoverageReportValidator
from ..qualification import (
    CandidateQualificationValidator,
    QualifiedCandidate,
)
from ..transcript import TranscriptDocument, TranscriptValidator
from ..validators import EvidenceValidator


class EvidenceSupplementValidationError(ValueError):
    def __init__(self, rule: str, path: str, message: str):
        self.rule = rule
        self.path = path
        self.message = message
        super().__init__(f"{rule} [{path}]: {message}")


class EvidenceSupplementValidator:
    def __init__(
        self,
        *,
        qualification_validator: CandidateQualificationValidator | None = None,
        transcript_validator: TranscriptValidator | None = None,
        evidence_validator: EvidenceValidator | None = None,
        coverage_validator: CoverageReportValidator | None = None,
    ):
        self.qualification_validator = (
            qualification_validator or CandidateQualificationValidator()
        )
        self.transcript_validator = transcript_validator or TranscriptValidator()
        self.evidence_validator = evidence_validator or EvidenceValidator()
        self.coverage_validator = coverage_validator or CoverageReportValidator()

    def validate_inputs(
        self,
        knowledge_graph: KnowledgeGraph,
        existing_graph: EvidenceGraph,
        candidate: QualifiedCandidate,
        transcript: TranscriptDocument,
    ) -> None:
        self.qualification_validator.validate_boundary(candidate)
        if candidate.qualification_status == "rejected" or candidate.resource is None:
            self._fail(
                "qualified_candidate",
                "qualifiedCandidate",
                "supplementation requires a qualified or warning candidate",
            )
        self.coverage_validator.validate_source(knowledge_graph, existing_graph)
        self.transcript_validator.validate(candidate.resource, transcript)

    def validate_result(
        self,
        knowledge_graph: KnowledgeGraph,
        existing_graph: EvidenceGraph,
        candidate: QualifiedCandidate,
        result,
    ) -> None:
        if candidate.resource is None:
            self._fail(
                "qualified_candidate",
                "qualifiedCandidate.resource",
                "qualified resource is required",
            )
        supplemented = result.supplemented_graph
        if result.source_graph_ref != artifact_ref("evidence_graph", existing_graph):
            self._fail(
                "artifact_lineage",
                "sourceGraphRef",
                "supplement result does not reference the source EvidenceGraph version",
            )
        if (
            supplemented.artifact_id != existing_graph.artifact_id
            or supplemented.version != existing_graph.version + 1
            or supplemented.knowledge_graph_ref != existing_graph.knowledge_graph_ref
        ):
            self._fail(
                "artifact_lineage",
                "supplementedGraph",
                "supplemented EvidenceGraph must be the next source artifact version",
            )

        self._validate_append_only(existing_graph, supplemented, result)
        new_resource = supplemented.resources[len(existing_graph.resources) :]
        if new_resource != [candidate.resource]:
            self._fail(
                "supplement_resource",
                "supplementedGraph.resources",
                "supplement must append exactly the qualified resource",
            )
        for segment in result.new_segments:
            if (
                segment.resource_id != candidate.resource.id
                or segment.resource_fingerprint
                != candidate.resource.content_fingerprint
            ):
                self._fail(
                    "supplement_segment_resource",
                    f"newSegments.{segment.id}",
                    "new segment does not belong to the qualified resource version",
                )
        for evidence in result.new_evidence:
            if (
                evidence.resource_id != candidate.resource.id
                or evidence.resource_fingerprint
                != candidate.resource.content_fingerprint
            ):
                self._fail(
                    "supplement_evidence_resource",
                    f"newEvidence.{evidence.id}",
                    "new evidence does not belong to the qualified resource version",
                )

        self._validate_graph_and_reports(
            knowledge_graph,
            existing_graph,
            supplemented,
            result.coverage_before,
            result.coverage_after,
        )
        before_keys = {
            (item.knowledge_id, item.gap_type) for item in result.coverage_before.gaps
        }
        after_keys = {
            (item.knowledge_id, item.gap_type) for item in result.coverage_after.gaps
        }
        expected_resolved = [
            item
            for item in result.coverage_before.gaps
            if (item.knowledge_id, item.gap_type) not in after_keys
        ]
        if result.resolved_gaps != expected_resolved:
            self._fail(
                "resolved_gap",
                "resolvedGaps",
                "resolved gaps must be present before and absent after supplementation",
            )
        if result.remaining_gaps != result.coverage_after.gaps:
            self._fail(
                "remaining_gap",
                "remainingGaps",
                "remaining gaps must match the refreshed CoverageReport",
            )
        if any(
            (item.knowledge_id, item.gap_type) in after_keys
            or (item.knowledge_id, item.gap_type) not in before_keys
            for item in result.resolved_gaps
        ):
            self._fail(
                "resolved_gap",
                "resolvedGaps",
                "a reported resolved gap still exists after supplementation",
            )

    def _validate_graph_and_reports(
        self,
        knowledge_graph: KnowledgeGraph,
        existing_graph: EvidenceGraph,
        supplemented: EvidenceGraph,
        coverage_before: CoverageReport,
        coverage_after: CoverageReport,
    ) -> None:
        try:
            self.evidence_validator.validate_graph(knowledge_graph, supplemented)
            self.coverage_validator.validate_report(
                knowledge_graph,
                existing_graph,
                coverage_before,
            )
            self.coverage_validator.validate_report(
                knowledge_graph,
                supplemented,
                coverage_after,
            )
        except ValueError as exc:
            self._fail(
                "supplement_graph",
                "supplementedGraph",
                str(exc),
            )

    def _validate_append_only(self, existing, supplemented, result) -> None:
        collections = (
            ("resources", existing.resources, supplemented.resources, None),
            ("segments", existing.segments, supplemented.segments, result.new_segments),
            ("evidence", existing.evidence, supplemented.evidence, result.new_evidence),
            (
                "coverageEdges",
                existing.coverage_edges,
                supplemented.coverage_edges,
                result.new_coverage_edges,
            ),
        )
        for name, old_items, merged_items, declared_new in collections:
            if merged_items[: len(old_items)] != old_items:
                self._fail(
                    "append_only_evidence",
                    f"supplementedGraph.{name}",
                    "existing EvidenceGraph objects were modified, reordered, or deleted",
                )
            if declared_new is not None and merged_items[len(old_items) :] != declared_new:
                self._fail(
                    "supplement_declaration",
                    name,
                    "declared supplement objects do not match the appended graph objects",
                )

    @staticmethod
    def _fail(rule: str, path: str, message: str) -> None:
        raise EvidenceSupplementValidationError(rule, path, message)


__all__ = ["EvidenceSupplementValidationError", "EvidenceSupplementValidator"]
