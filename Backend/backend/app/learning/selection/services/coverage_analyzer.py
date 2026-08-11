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


CoverageStrength = Literal["full", "partial", "supplementary"]
_STRENGTH_RANK: dict[CoverageStrength, int] = {
    "supplementary": 1,
    "partial": 2,
    "full": 3,
}
_CONTENT_EVIDENCE = {
    "transcript_span",
    "caption_span",
    "chapter_marker",
    "manual_verified",
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
        segments = {item.id for item in evidence_graph.segments}
        evidence = {item.id: item for item in evidence_graph.evidence}
        valid: list[CoverageEdge] = []
        for edge in evidence_graph.coverage_edges:
            if edge.segment_id not in segments or not edge.evidence_refs:
                continue
            if all(
                evidence_id in evidence
                and evidence[evidence_id].segment_id == edge.segment_id
                and evidence[evidence_id].verification_status == "verified"
                and evidence[evidence_id].kind in _CONTENT_EVIDENCE
                for evidence_id in edge.evidence_refs
            ):
                valid.append(edge)
        return valid


__all__ = [
    "CoverageAnalyzer",
    "KnowledgeCoverage",
    "KnowledgeCoverageReport",
]
