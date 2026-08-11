from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from pydantic import ValidationError

from ...contracts import EvidenceGraph, KnowledgeGraph, SegmentEvidence, VideoResource
from ...generators import LearningSemanticModel
from ...generators.base import artifact_ref, generated_id
from ...validators import LearningArtifactValidationError
from ..mapping import CoverageMapper, CoverageMappingError
from ..validators import EvidenceValidator
from .builders import TranscriptBuildError, TranscriptSegmentBuilder
from .providers import TranscriptProvider, TranscriptProviderError
from .validators import TranscriptValidationError, TranscriptValidator


ResultT = TypeVar("ResultT")


class TranscriptEvidencePipelineError(RuntimeError):
    def __init__(self, stage: str, message: str):
        self.stage = stage
        self.message = message
        super().__init__(f"{stage}: {message}")


@dataclass(frozen=True)
class TranscriptEvidenceGenerationResult:
    knowledge_graph: KnowledgeGraph
    evidence_graph: EvidenceGraph
    model_usage: dict[str, Any]


class TranscriptEvidencePipeline:
    """Existing VideoResource -> verified transcript -> mapped EvidenceGraph boundary."""

    def __init__(
        self,
        provider: TranscriptProvider,
        model: LearningSemanticModel | None = None,
        *,
        transcript_validator: TranscriptValidator | None = None,
        segment_builder: TranscriptSegmentBuilder | None = None,
        evidence_validator: EvidenceValidator | None = None,
        coverage_mapper: CoverageMapper | None = None,
    ):
        self.provider = provider
        self.transcript_validator = transcript_validator or TranscriptValidator()
        self.segment_builder = segment_builder or TranscriptSegmentBuilder(
            validator=self.transcript_validator
        )
        self.evidence_validator = evidence_validator or EvidenceValidator()
        self.coverage_mapper = coverage_mapper or CoverageMapper(
            model=model,
            evidence_validator=self.evidence_validator,
        )

    def generate(
        self,
        knowledge_graph: KnowledgeGraph,
        resources: list[VideoResource],
    ) -> TranscriptEvidenceGenerationResult:
        self._stage(
            "video_resource_validation",
            lambda: self.evidence_validator.validate_resources(resources),
        )
        segments = []
        evidence: list[SegmentEvidence] = []
        for index, resource in enumerate(resources):
            document = self._stage(
                "transcript_fetch",
                lambda resource=resource: self.provider.fetch_transcript(resource),
            )
            self._stage(
                "transcript_validation",
                lambda resource=resource, document=document: self.transcript_validator.validate(
                    resource, document
                ),
            )
            build_result = self._stage(
                "content_segment_build",
                lambda resource=resource, document=document: self.segment_builder.build(
                    resource, document
                ),
            )
            if not build_result.segments:
                raise TranscriptEvidencePipelineError(
                    "content_segment_build",
                    f"resource {index} produced no content segments",
                )
            segments.extend(build_result.segments)
            evidence.extend(build_result.evidence)

        self._stage(
            "content_evidence_validation",
            lambda: self._validate_content(resources, segments, evidence),
        )
        graph = EvidenceGraph(
            artifactId=generated_id(
                "evidence-graph",
                knowledge_graph.artifact_id,
                knowledge_graph.version,
                "|".join(item.content_fingerprint for item in resources),
            ),
            knowledgeGraphRef=artifact_ref("knowledge_graph", knowledge_graph),
            resources=resources,
            segments=segments,
            evidence=evidence,
            coverageEdges=[],
        )
        coverage = self._stage(
            "coverage_mapping",
            lambda: self.coverage_mapper.map(knowledge_graph, graph),
        )
        graph = graph.model_copy(update={"coverage_edges": coverage})
        self._stage(
            "evidence_graph_validation",
            lambda: self.evidence_validator.validate_graph(knowledge_graph, graph),
        )
        return TranscriptEvidenceGenerationResult(
            knowledge_graph=knowledge_graph,
            evidence_graph=graph,
            model_usage=self.coverage_mapper.model_usage,
        )

    def _validate_content(self, resources, segments, evidence) -> None:
        self.evidence_validator.validate_segments(resources, segments)
        self.evidence_validator.validate_evidence(resources, segments, evidence)

    @staticmethod
    def _stage(stage: str, operation: Callable[[], ResultT]) -> ResultT:
        try:
            return operation()
        except TranscriptEvidencePipelineError:
            raise
        except (
            CoverageMappingError,
            LearningArtifactValidationError,
            TranscriptBuildError,
            TranscriptProviderError,
            TranscriptValidationError,
            ValidationError,
            ValueError,
        ) as exc:
            raise TranscriptEvidencePipelineError(stage, str(exc)) from exc


__all__ = [
    "TranscriptEvidenceGenerationResult",
    "TranscriptEvidencePipeline",
    "TranscriptEvidencePipelineError",
]
