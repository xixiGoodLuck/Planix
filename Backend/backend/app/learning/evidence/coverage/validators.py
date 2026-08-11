from __future__ import annotations

from ...contracts import EvidenceGraph, KnowledgeGraph
from .conflict_analyzer import ConflictAnalyzer
from .coverage_aggregator import CoverageAggregator
from .coverage_report import CoverageReport
from .gap_analyzer import GapAnalyzer


class CoverageReportValidationError(ValueError):
    def __init__(self, rule: str, path: str, message: str):
        self.rule = rule
        self.path = path
        self.message = message
        super().__init__(f"{rule} [{path}]: {message}")


class CoverageReportValidator:
    def validate_source(
        self,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
    ) -> None:
        reference = evidence_graph.knowledge_graph_ref
        if (
            reference.artifact_id != knowledge_graph.artifact_id
            or reference.version != knowledge_graph.version
        ):
            self._fail(
                "coverage_knowledge_graph_reference",
                "evidenceGraph.knowledgeGraphRef",
                "evidence graph references another knowledge graph version",
            )
        knowledge_ids = {item.id for item in knowledge_graph.nodes}
        resources = {item.id: item for item in evidence_graph.resources}
        segments = {item.id: item for item in evidence_graph.segments}
        evidence = {item.id: item for item in evidence_graph.evidence}
        self._unique_lengths(evidence_graph, resources, segments, evidence)

        for segment in evidence_graph.segments:
            resource = resources.get(segment.resource_id)
            if resource is None:
                self._fail(
                    "coverage_segment_resource",
                    f"segments.{segment.id}.resourceId",
                    "segment references missing resource",
                )
            if segment.resource_fingerprint != resource.content_fingerprint:
                self._fail(
                    "coverage_fingerprint",
                    f"segments.{segment.id}.resourceFingerprint",
                    "segment fingerprint does not match resource",
                )
            if (
                segment.start_seconds < 0
                or segment.end_seconds <= segment.start_seconds
                or segment.end_seconds > resource.duration_seconds
            ):
                self._fail(
                    "coverage_timestamp",
                    f"segments.{segment.id}",
                    "segment timestamp is outside resource duration",
                )
            for evidence_id in segment.evidence_refs:
                item = evidence.get(evidence_id)
                if item is None or item.segment_id != segment.id:
                    self._fail(
                        "coverage_evidence_reference",
                        f"segments.{segment.id}.evidenceRefs",
                        "segment references missing evidence or evidence from another segment",
                    )

        for item in evidence_graph.evidence:
            resource = resources.get(item.resource_id)
            segment = segments.get(item.segment_id)
            if resource is None or segment is None or segment.resource_id != item.resource_id:
                self._fail(
                    "coverage_evidence_reference",
                    f"evidence.{item.id}",
                    "evidence references missing or inconsistent source objects",
                )
            if item.resource_fingerprint != resource.content_fingerprint:
                self._fail(
                    "coverage_fingerprint",
                    f"evidence.{item.id}.resourceFingerprint",
                    "evidence fingerprint does not match resource",
                )

        seen_edges: set[str] = set()
        for edge in evidence_graph.coverage_edges:
            if edge.id in seen_edges:
                self._fail("duplicate_coverage_edge", "coverageEdges", "coverage edge id is duplicated")
            seen_edges.add(edge.id)
            if edge.knowledge_id not in knowledge_ids:
                self._fail(
                    "coverage_knowledge_reference",
                    f"coverageEdges.{edge.id}.knowledgeId",
                    "coverage references missing knowledge",
                )
            segment = segments.get(edge.segment_id)
            if segment is None:
                self._fail(
                    "coverage_segment_reference",
                    f"coverageEdges.{edge.id}.segmentId",
                    "coverage references missing segment",
                )
            if not edge.evidence_refs:
                self._fail(
                    "coverage_evidence_reference",
                    f"coverageEdges.{edge.id}.evidenceRefs",
                    "coverage must reference evidence",
                )
            for evidence_id in edge.evidence_refs:
                item = evidence.get(evidence_id)
                if (
                    item is None
                    or item.segment_id != edge.segment_id
                    or evidence_id not in segment.evidence_refs
                ):
                    self._fail(
                        "coverage_evidence_reference",
                        f"coverageEdges.{edge.id}.evidenceRefs",
                        "coverage references missing evidence or evidence from another segment",
                    )

    def validate_report(
        self,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        report: CoverageReport,
    ) -> None:
        self.validate_source(knowledge_graph, evidence_graph)
        if report.knowledge_graph_ref != evidence_graph.knowledge_graph_ref:
            self._fail(
                "coverage_report_reference",
                "coverageReport.knowledgeGraphRef",
                "report knowledge reference is stale",
            )
        if (
            report.evidence_graph_ref.artifact_id != evidence_graph.artifact_id
            or report.evidence_graph_ref.version != evidence_graph.version
        ):
            self._fail(
                "coverage_report_reference",
                "coverageReport.evidenceGraphRef",
                "report evidence reference is stale",
            )
        knowledge_ids = {item.id for item in knowledge_graph.nodes}
        reported_ids = [item.knowledge_id for item in report.knowledge_coverage]
        if len(reported_ids) != len(set(reported_ids)) or set(reported_ids) != knowledge_ids:
            self._fail(
                "coverage_report_knowledge",
                "coverageReport.knowledgeCoverage",
                "report must contain each knowledge node exactly once",
            )
        expected_coverage = CoverageAggregator.calculate_knowledge_coverage(
            knowledge_graph,
            evidence_graph,
        )
        if self._dump(report.knowledge_coverage) != self._dump(expected_coverage):
            self._fail(
                "coverage_strength_computation",
                "coverageReport.knowledgeCoverage",
                "coverage strength or references do not match deterministic aggregation",
            )
        coverage_by_id = {item.knowledge_id: item for item in expected_coverage}
        for gap in report.gaps:
            if gap.knowledge_id not in knowledge_ids:
                self._fail(
                    "coverage_gap_knowledge_reference",
                    "coverageReport.gaps",
                    "gap references missing knowledge",
                )
            if gap.current_strength != coverage_by_id[gap.knowledge_id].coverage_strength:
                self._fail(
                    "coverage_gap_strength",
                    "coverageReport.gaps",
                    "gap strength does not match aggregated coverage",
                )
        expected_gaps = GapAnalyzer().analyze(knowledge_graph, expected_coverage)
        if self._dump(report.gaps) != self._dump(expected_gaps):
            self._fail(
                "coverage_gap_computation",
                "coverageReport.gaps",
                "gaps do not match deterministic gap analysis",
            )
        conflict_analysis = ConflictAnalyzer().analyze(knowledge_graph, evidence_graph)
        self._validate_analysis_refs(knowledge_ids, evidence_graph, report)
        if self._dump(report.conflicts) != self._dump(conflict_analysis.conflicts):
            self._fail(
                "version_conflict_computation",
                "coverageReport.conflicts",
                "version conflicts do not match source technology versions",
            )
        if self._dump(report.redundancy) != self._dump(conflict_analysis.redundancy):
            self._fail(
                "segment_relationship_computation",
                "coverageReport.redundancy",
                "segment relationship analysis does not match source coverage",
            )

    def _validate_analysis_refs(
        self,
        knowledge_ids: set[str],
        evidence_graph: EvidenceGraph,
        report: CoverageReport,
    ) -> None:
        resource_ids = {item.id for item in evidence_graph.resources}
        segment_ids = {item.id for item in evidence_graph.segments}
        evidence_ids = {item.id for item in evidence_graph.evidence}
        for conflict in report.conflicts:
            if conflict.knowledge_id not in knowledge_ids:
                self._fail(
                    "version_conflict_knowledge_reference",
                    "coverageReport.conflicts",
                    "version conflict references missing knowledge",
                )
            for observation in conflict.observations:
                if not set(observation.resource_refs) <= resource_ids:
                    self._fail(
                        "version_conflict_resource_reference",
                        "coverageReport.conflicts",
                        "version conflict references missing resource",
                    )
                if not set(observation.segment_refs) <= segment_ids:
                    self._fail(
                        "version_conflict_segment_reference",
                        "coverageReport.conflicts",
                        "version conflict references missing segment",
                    )
        for relationship in report.redundancy:
            if relationship.knowledge_id not in knowledge_ids:
                self._fail(
                    "segment_analysis_knowledge_reference",
                    "coverageReport.redundancy",
                    "segment analysis references missing knowledge",
                )
            if not set(relationship.segment_refs) <= segment_ids:
                self._fail(
                    "segment_analysis_segment_reference",
                    "coverageReport.redundancy",
                    "segment analysis references missing segment",
                )
            if not set(relationship.evidence_refs) <= evidence_ids:
                self._fail(
                    "segment_analysis_evidence_reference",
                    "coverageReport.redundancy",
                    "segment analysis references missing evidence",
                )

    @staticmethod
    def _unique_lengths(evidence_graph, resources, segments, evidence) -> None:
        if len(resources) != len(evidence_graph.resources):
            raise CoverageReportValidationError(
                "duplicate_resource", "resources", "resource id is duplicated"
            )
        if len(segments) != len(evidence_graph.segments):
            raise CoverageReportValidationError(
                "duplicate_segment", "segments", "segment id is duplicated"
            )
        if len(evidence) != len(evidence_graph.evidence):
            raise CoverageReportValidationError(
                "duplicate_evidence", "evidence", "evidence id is duplicated"
            )

    @staticmethod
    def _dump(items) -> list[dict]:
        return [item.model_dump(mode="json", by_alias=True) for item in items]

    @staticmethod
    def _fail(rule: str, path: str, message: str) -> None:
        raise CoverageReportValidationError(rule, path, message)


__all__ = ["CoverageReportValidationError", "CoverageReportValidator"]
