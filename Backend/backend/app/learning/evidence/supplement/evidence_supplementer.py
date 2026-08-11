from __future__ import annotations

from pydantic import Field

from ...contracts import (
    ContentSegment,
    CoverageEdge,
    EvidenceGraph,
    LearningArtifactRef,
    LearningContract,
    KnowledgeGraph,
    SegmentEvidence,
)
from ...generators.base import artifact_ref
from ..coverage import CoverageAggregator, CoverageReport, EvidenceCoverageGap
from ..mapping import CoverageMapper
from ..qualification import QualifiedCandidate
from ..transcript import TranscriptDocument
from .evidence_merger import EvidenceGraphMerger
from .segment_generator import TranscriptSegmentGenerator
from .validators import EvidenceSupplementValidator


class EvidenceSupplementResult(LearningContract):
    source_graph_ref: LearningArtifactRef
    supplemented_graph: EvidenceGraph
    new_segments: list[ContentSegment] = Field(min_length=1)
    new_evidence: list[SegmentEvidence] = Field(min_length=1)
    new_coverage_edges: list[CoverageEdge] = Field(min_length=1)
    coverage_before: CoverageReport
    coverage_after: CoverageReport
    resolved_gaps: list[EvidenceCoverageGap] = Field(default_factory=list)
    remaining_gaps: list[EvidenceCoverageGap] = Field(default_factory=list)


class EvidenceSupplementError(RuntimeError):
    def __init__(self, stage: str, message: str):
        self.stage = stage
        self.message = message
        super().__init__(f"{stage}: {message}")


class EvidenceSupplementer:
    def __init__(
        self,
        *,
        segment_generator: TranscriptSegmentGenerator | None = None,
        coverage_mapper: CoverageMapper | None = None,
        merger: EvidenceGraphMerger | None = None,
        coverage_aggregator: CoverageAggregator | None = None,
        validator: EvidenceSupplementValidator | None = None,
    ):
        self.segment_generator = segment_generator or TranscriptSegmentGenerator()
        self.coverage_mapper = coverage_mapper or CoverageMapper()
        self.merger = merger or EvidenceGraphMerger()
        self.coverage_aggregator = coverage_aggregator or CoverageAggregator()
        self.validator = validator or EvidenceSupplementValidator()

    def supplement(
        self,
        candidate: QualifiedCandidate,
        transcript: TranscriptDocument,
        knowledge_graph: KnowledgeGraph,
        existing_graph: EvidenceGraph,
    ) -> EvidenceSupplementResult:
        try:
            self.validator.validate_inputs(
                knowledge_graph,
                existing_graph,
                candidate,
                transcript,
            )
            resource = candidate.resource
            if resource is None:
                raise EvidenceSupplementError(
                    "evidence_supplement",
                    "qualified resource is required",
                )
            generated = self.segment_generator.generate_with_evidence(
                transcript,
                resource,
            )
            mapping_graph = EvidenceGraph(
                artifactId=existing_graph.artifact_id,
                version=existing_graph.version + 1,
                knowledgeGraphRef=existing_graph.knowledge_graph_ref.model_copy(deep=True),
                resources=[resource.model_copy(deep=True)],
                segments=[item.model_copy(deep=True) for item in generated.segments],
                evidence=[item.model_copy(deep=True) for item in generated.evidence],
                coverageEdges=[],
            )
            new_edges = self.coverage_mapper.map(knowledge_graph, mapping_graph)
            supplemented = self.merger.merge(
                existing_graph,
                resource=resource,
                segments=generated.segments,
                evidence=generated.evidence,
                coverage_edges=new_edges,
            )
            coverage_before = self.coverage_aggregator.aggregate(
                knowledge_graph,
                existing_graph,
            )
            coverage_after = self.coverage_aggregator.aggregate(
                knowledge_graph,
                supplemented,
            )
            after_gap_keys = {
                (item.knowledge_id, item.gap_type) for item in coverage_after.gaps
            }
            result = EvidenceSupplementResult(
                sourceGraphRef=artifact_ref("evidence_graph", existing_graph),
                supplementedGraph=supplemented,
                newSegments=generated.segments,
                newEvidence=generated.evidence,
                newCoverageEdges=new_edges,
                coverageBefore=coverage_before,
                coverageAfter=coverage_after,
                resolvedGaps=[
                    item
                    for item in coverage_before.gaps
                    if (item.knowledge_id, item.gap_type) not in after_gap_keys
                ],
                remainingGaps=coverage_after.gaps,
            )
            self.validator.validate_result(
                knowledge_graph,
                existing_graph,
                candidate,
                result,
            )
            return result
        except EvidenceSupplementError:
            raise
        except (ValueError, RuntimeError) as exc:
            raise EvidenceSupplementError("evidence_supplement", str(exc)) from exc


__all__ = [
    "EvidenceSupplementError",
    "EvidenceSupplementResult",
    "EvidenceSupplementer",
]
