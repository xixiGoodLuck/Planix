from __future__ import annotations

from typing import Literal

from pydantic import Field

from ...contracts import EvidenceGraph, KnowledgeGraph, LearningContract
from .coverage_analyzer import CoverageAnalyzer


RedundancyClassification = Literal["KEEP", "REDUNDANT", "CONTEXT_REQUIRED"]


class SegmentRedundancy(LearningContract):
    segment_id: str = Field(min_length=1)
    classification: RedundancyClassification
    knowledge_refs: list[str] = Field(default_factory=list)
    duplicate_of: str | None = None
    reason: str = Field(min_length=1)


class RedundancyReport(LearningContract):
    segments: list[SegmentRedundancy]


class RedundancyAnalyzer:
    _EVIDENCE_RANK = {
        "provider_metadata": 0,
        "chapter_marker": 1,
        "manual_verified": 2,
        "caption_span": 3,
        "transcript_span": 4,
    }

    def analyze(
        self,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
    ) -> RedundancyReport:
        knowledge_ids = {item.id for item in knowledge_graph.nodes}
        valid_edges = [
            item
            for item in CoverageAnalyzer.valid_coverage_edges(evidence_graph)
            if item.knowledge_id in knowledge_ids
        ]
        evidence = {item.id: item for item in evidence_graph.evidence}
        coverage_by_segment = {
            segment.id: {
                edge.knowledge_id for edge in valid_edges if edge.segment_id == segment.id
            }
            for segment in evidence_graph.segments
        }
        evidence_rank = {
            segment.id: max(
                (
                    self._EVIDENCE_RANK[evidence[item_id].kind]
                    for item_id in segment.evidence_refs
                    if item_id in evidence
                    and evidence[item_id].verification_status == "verified"
                ),
                default=-1,
            )
            for segment in evidence_graph.segments
        }
        duration = {
            segment.id: segment.end_seconds - segment.start_seconds
            for segment in evidence_graph.segments
        }
        context_required = {
            context_id
            for segment in evidence_graph.segments
            for context_id in segment.context_segment_refs
        }
        decisions: list[SegmentRedundancy] = []
        for segment in evidence_graph.segments:
            knowledge_refs = coverage_by_segment[segment.id]
            if segment.id in context_required:
                decisions.append(
                    SegmentRedundancy(
                        segmentId=segment.id,
                        classification="CONTEXT_REQUIRED",
                        knowledgeRefs=sorted(knowledge_refs),
                        reason="another selected candidate declares this segment as required context",
                    )
                )
                continue
            alternatives = []
            for candidate in evidence_graph.segments:
                if candidate.id == segment.id or not knowledge_refs:
                    continue
                candidate_coverage = coverage_by_segment[candidate.id]
                if not knowledge_refs <= candidate_coverage:
                    continue
                stronger = evidence_rank[candidate.id] > evidence_rank[segment.id]
                equally_strong_and_shorter = (
                    evidence_rank[candidate.id] == evidence_rank[segment.id]
                    and (
                        duration[candidate.id] < duration[segment.id]
                        or (
                            duration[candidate.id] == duration[segment.id]
                            and candidate.id < segment.id
                        )
                    )
                )
                if stronger or equally_strong_and_shorter:
                    alternatives.append(candidate)
            if alternatives:
                preferred = sorted(
                    alternatives,
                    key=lambda item: (
                        -evidence_rank[item.id],
                        duration[item.id],
                        item.id,
                    ),
                )[0]
                decisions.append(
                    SegmentRedundancy(
                        segmentId=segment.id,
                        classification="REDUNDANT",
                        knowledgeRefs=sorted(knowledge_refs),
                        duplicateOf=preferred.id,
                        reason="another segment covers the same knowledge with stronger evidence or less time",
                    )
                )
            else:
                decisions.append(
                    SegmentRedundancy(
                        segmentId=segment.id,
                        classification="KEEP",
                        knowledgeRefs=sorted(knowledge_refs),
                        reason="segment contributes non-redundant verified knowledge coverage",
                    )
                )
        return RedundancyReport(segments=decisions)


__all__ = [
    "RedundancyAnalyzer",
    "RedundancyReport",
    "SegmentRedundancy",
]
