from __future__ import annotations

from ...contracts import CoverageEdge, EvidenceGraph, KnowledgeGraph, SegmentEvidence
from ..validators import EvidenceValidator


class CoverageMappingValidationError(ValueError):
    def __init__(self, rule: str, path: str, message: str):
        self.rule = rule
        self.path = path
        self.message = message
        super().__init__(f"{rule} [{path}]: {message}")


class CoverageMappingValidator:
    _CONFIDENCE_CAP = {
        "transcript_span": 1.0,
        "caption_span": 0.9,
        "chapter_marker": 0.75,
        "manual_verified": 1.0,
        "provider_metadata": 0.35,
    }

    def __init__(self, evidence_validator: EvidenceValidator | None = None):
        self.evidence_validator = evidence_validator or EvidenceValidator()

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
                "evidence graph does not reference the supplied knowledge graph version",
            )
        self.evidence_validator.validate_resources(evidence_graph.resources)
        self.evidence_validator.validate_segments(
            evidence_graph.resources,
            evidence_graph.segments,
        )
        self.evidence_validator.validate_evidence(
            evidence_graph.resources,
            evidence_graph.segments,
            evidence_graph.evidence,
        )

    def validate_edges(
        self,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        edges: list[CoverageEdge],
    ) -> None:
        if not edges:
            self._fail("coverage_required", "coverageEdges", "no semantic coverage was mapped")
        knowledge_ids = {item.id for item in knowledge_graph.nodes}
        segments = {item.id: item for item in evidence_graph.segments}
        evidence = {item.id: item for item in evidence_graph.evidence}
        seen: set[tuple[str, str]] = set()
        for index, edge in enumerate(edges):
            path = f"coverageEdges.{index}"
            if edge.knowledge_id not in knowledge_ids:
                self._fail(
                    "coverage_knowledge_reference",
                    f"{path}.knowledgeId",
                    "coverage references missing knowledge",
                )
            segment = segments.get(edge.segment_id)
            if segment is None:
                self._fail(
                    "coverage_segment_reference",
                    f"{path}.segmentId",
                    "coverage references missing segment",
                )
            identity = (edge.knowledge_id, edge.segment_id)
            if identity in seen:
                self._fail(
                    "duplicate_coverage",
                    path,
                    "knowledge may be mapped to a segment only once",
                )
            seen.add(identity)
            if not 0 <= edge.confidence <= 1:
                self._fail(
                    "coverage_confidence",
                    f"{path}.confidence",
                    "coverage confidence must be between zero and one",
                )
            cited = self._resolve_evidence(path, edge, segment.evidence_refs, evidence)
            confidence_cap = max(self._CONFIDENCE_CAP[item.kind] for item in cited)
            if edge.confidence > confidence_cap:
                self._fail(
                    "evidence_confidence",
                    f"{path}.confidence",
                    f"confidence {edge.confidence} exceeds evidence-level cap {confidence_cap}",
                )

    def _resolve_evidence(
        self,
        path: str,
        edge: CoverageEdge,
        segment_evidence_refs: list[str],
        evidence: dict[str, SegmentEvidence],
    ) -> list[SegmentEvidence]:
        if not edge.evidence_refs:
            self._fail(
                "coverage_evidence_reference",
                f"{path}.evidenceRefs",
                "coverage must cite evidence",
            )
        cited: list[SegmentEvidence] = []
        for evidence_id in edge.evidence_refs:
            item = evidence.get(evidence_id)
            if (
                item is None
                or item.segment_id != edge.segment_id
                or evidence_id not in segment_evidence_refs
            ):
                self._fail(
                    "coverage_evidence_reference",
                    f"{path}.evidenceRefs",
                    "coverage cites missing evidence or evidence from another segment",
                )
            if item.verification_status != "verified":
                self._fail(
                    "coverage_evidence_verification",
                    f"{path}.evidenceRefs",
                    "coverage evidence is not verified",
                )
            cited.append(item)
        return cited

    @staticmethod
    def _fail(rule: str, path: str, message: str) -> None:
        raise CoverageMappingValidationError(rule, path, message)


__all__ = ["CoverageMappingValidationError", "CoverageMappingValidator"]
