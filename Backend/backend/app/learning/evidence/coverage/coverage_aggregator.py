from __future__ import annotations

from ...contracts import EvidenceGraph, KnowledgeGraph
from ...generators.base import artifact_ref
from .conflict_analyzer import ConflictAnalyzer
from .coverage_report import CoverageReport, KnowledgeCoverageResult
from .gap_analyzer import GapAnalyzer


class CoverageAggregator:
    _PRECISE_EVIDENCE = {
        "transcript_span",
        "caption_span",
        "chapter_marker",
        "manual_verified",
    }
    _FULL_COVERAGE_TYPES = {
        "explanation",
        "demonstration",
        "implementation",
        "comparison",
        "practice",
    }

    def __init__(
        self,
        *,
        gap_analyzer: GapAnalyzer | None = None,
        conflict_analyzer: ConflictAnalyzer | None = None,
    ):
        self.gap_analyzer = gap_analyzer or GapAnalyzer()
        self.conflict_analyzer = conflict_analyzer or ConflictAnalyzer()

    def aggregate(
        self,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
    ) -> CoverageReport:
        from .validators import CoverageReportValidator

        validator = CoverageReportValidator()
        validator.validate_source(knowledge_graph, evidence_graph)
        knowledge_coverage = self.calculate_knowledge_coverage(
            knowledge_graph,
            evidence_graph,
        )
        conflicts = self.conflict_analyzer.analyze(knowledge_graph, evidence_graph)
        report = CoverageReport(
            knowledgeGraphRef=artifact_ref("knowledge_graph", knowledge_graph),
            evidenceGraphRef=artifact_ref("evidence_graph", evidence_graph),
            knowledgeCoverage=knowledge_coverage,
            gaps=self.gap_analyzer.analyze(knowledge_graph, knowledge_coverage),
            conflicts=conflicts.conflicts,
            redundancy=conflicts.redundancy,
        )
        validator.validate_report(knowledge_graph, evidence_graph, report)
        return report

    @classmethod
    def calculate_knowledge_coverage(
        cls,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
    ) -> list[KnowledgeCoverageResult]:
        evidence = {item.id: item for item in evidence_graph.evidence}
        result: list[KnowledgeCoverageResult] = []
        for node in knowledge_graph.nodes:
            edges = [item for item in evidence_graph.coverage_edges if item.knowledge_id == node.id]
            evidence_refs = list(
                dict.fromkeys(
                    evidence_id for edge in edges for evidence_id in edge.evidence_refs
                )
            )
            segment_refs = list(dict.fromkeys(item.segment_id for item in edges))
            verified_transcript_edges = [
                edge
                for edge in edges
                if any(
                    evidence[evidence_id].verification_status == "verified"
                    and evidence[evidence_id].kind == "transcript_span"
                    for evidence_id in edge.evidence_refs
                )
            ]
            requirement_ids = {item.id for item in node.coverage_requirements}
            supported_requirement_ids = {
                requirement_id
                for edge in verified_transcript_edges
                for requirement_id in edge.supported_requirement_refs
                if requirement_id in requirement_ids
            }
            full = (
                supported_requirement_ids == requirement_ids
                if requirement_ids
                else any(
                    edge.confidence >= 0.85
                    and edge.coverage_type in cls._FULL_COVERAGE_TYPES
                    for edge in verified_transcript_edges
                )
            )
            precise = any(
                evidence[evidence_id].verification_status == "verified"
                and evidence[evidence_id].kind in cls._PRECISE_EVIDENCE
                for evidence_id in evidence_refs
            )
            if full:
                strength = "FULL"
                status = "sufficient"
            elif requirement_ids and supported_requirement_ids:
                strength = "PARTIAL"
                status = "insufficient"
            elif precise:
                strength = "PARTIAL"
                status = "insufficient"
            elif evidence_refs:
                strength = "WEAK"
                status = "insufficient"
            else:
                strength = "MISSING"
                status = "missing"
            result.append(
                KnowledgeCoverageResult(
                    knowledgeId=node.id,
                    status=status,
                    coverageStrength=strength,
                    evidenceRefs=evidence_refs,
                    segmentRefs=segment_refs,
                )
            )
        return result


__all__ = ["CoverageAggregator"]
