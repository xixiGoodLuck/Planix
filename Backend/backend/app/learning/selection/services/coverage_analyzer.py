from __future__ import annotations

from typing import Literal

from pydantic import Field

from ...contracts import (
    CoverageEdge,
    EvidenceGraph,
    KnowledgeGraph,
    LearningArtifactRef,
    LearningContract,
)
from ...generators.base import artifact_ref
from ...selection_semantics import verified_coverage_edges


CoverageStrength = Literal["full", "partial", "supplementary"]
_STRENGTH_RANK: dict[CoverageStrength, int] = {
    "supplementary": 1,
    "partial": 2,
    "full": 3,
}
class KnowledgeCoverage(LearningContract):
    knowledge_id: str = Field(min_length=1)
    covered: bool
    sufficient: bool
    best_strength: CoverageStrength | None = None
    segment_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    verified_transcript_evidence: bool = False


class KnowledgeCoverageReport(LearningContract):
    knowledge_graph_ref: LearningArtifactRef
    evidence_graph_ref: LearningArtifactRef
    knowledge: list[KnowledgeCoverage]


class CoverageAnalyzer:
    def analyze(
        self,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
    ) -> KnowledgeCoverageReport:
        valid_edges = self.valid_coverage_edges(evidence_graph)
        evidence = {item.id: item for item in evidence_graph.evidence}
        coverage: list[KnowledgeCoverage] = []
        for node in knowledge_graph.nodes:
            edges = [item for item in valid_edges if item.knowledge_id == node.id]
            best_strength = max(
                (item.coverage_strength for item in edges),
                key=lambda value: _STRENGTH_RANK[value],
                default=None,
            )
            evidence_refs = list(
                dict.fromkeys(
                    evidence_id for edge in edges for evidence_id in edge.evidence_refs
                )
            )
            coverage.append(
                KnowledgeCoverage(
                    knowledgeId=node.id,
                    covered=bool(edges),
                    sufficient=best_strength == "full",
                    bestStrength=best_strength,
                    segmentRefs=list(dict.fromkeys(edge.segment_id for edge in edges)),
                    evidenceRefs=evidence_refs,
                    verifiedTranscriptEvidence=any(
                        evidence[evidence_id].kind == "transcript_span"
                        for evidence_id in evidence_refs
                    ),
                )
            )
        return KnowledgeCoverageReport(
            knowledgeGraphRef=artifact_ref("knowledge_graph", knowledge_graph),
            evidenceGraphRef=artifact_ref("evidence_graph", evidence_graph),
            knowledge=coverage,
        )

    @staticmethod
    def valid_coverage_edges(evidence_graph: EvidenceGraph) -> list[CoverageEdge]:
        return list(verified_coverage_edges(evidence_graph))


__all__ = [
    "CoverageAnalyzer",
    "KnowledgeCoverage",
    "KnowledgeCoverageReport",
]
