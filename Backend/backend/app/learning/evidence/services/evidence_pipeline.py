from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from pydantic import ValidationError

from ...contracts import EvidenceGraph, KnowledgeGraph
from ...generators import (
    LearningGenerationError,
    LearningModelOutputError,
    LearningSemanticModel,
)
from ...validators import LearningArtifactValidationError
from ..builders import EvidenceBuilder
from ..providers import (
    ProviderVideoDocument,
    VideoSearchQuery,
    VideoEvidenceProvider,
    VideoSourceProviderError,
)
from ..validators import EvidenceValidator


ResultT = TypeVar("ResultT")


class EvidencePipelineError(RuntimeError):
    def __init__(
        self,
        stage: str,
        message: str,
        *,
        validator_rule: str = "evidence_pipeline",
        field_path: str | None = None,
    ):
        self.stage = stage
        self.validator_rule = validator_rule
        self.field_path = field_path or stage
        super().__init__(f"{stage}: {message}")


@dataclass(frozen=True)
class EvidenceGenerationResult:
    knowledge_graph: KnowledgeGraph
    evidence_graph: EvidenceGraph
    model_usage: dict[str, Any]


class EvidenceGenerationPipeline:
    """Provider-neutral KnowledgeGraph -> EvidenceGraph pipeline."""

    def __init__(
        self,
        provider: VideoEvidenceProvider,
        model: LearningSemanticModel | None = None,
        validator: EvidenceValidator | None = None,
    ):
        self.provider = provider
        self.validator = validator or EvidenceValidator()
        self.builder = EvidenceBuilder(model)

    def generate(self, knowledge_graph: KnowledgeGraph) -> EvidenceGenerationResult:
        hits = self._stage(
            "video_search",
            lambda: self.provider.search(
                VideoSearchQuery(
                    knowledgeTerms=[node.name for node in knowledge_graph.nodes],
                )
            ),
        )
        if not hits:
            raise EvidencePipelineError(
                "video_search",
                "provider returned no video resources",
                validator_rule="video_resource_required",
                field_path="providerDocuments",
            )

        documents: list[ProviderVideoDocument] = []
        for index, hit in enumerate(hits):
            document = self._stage(
                "video_metadata",
                lambda hit=hit: self.provider.fetch_evidence(hit.external_id),
            )
            if (
                document.metadata.provider != hit.provider
                or document.metadata.external_id != hit.external_id
            ):
                raise EvidencePipelineError(
                    "video_metadata",
                    f"provider metadata does not match search hit {index}",
                    validator_rule="provider_identity",
                    field_path=f"providerDocuments.{index}",
                )
            documents.append(document)

        self._stage(
            "provider_evidence_validation",
            lambda: self.validator.validate_provider_documents(documents),
        )
        build_result = self._stage(
            "evidence_build",
            lambda: self.builder.build(knowledge_graph, documents),
        )
        graph = build_result.evidence_graph
        self._stage(
            "video_resource_validation",
            lambda: self.validator.validate_resources(graph.resources),
        )
        self._stage(
            "content_segment_validation",
            lambda: self.validator.validate_segments(graph.resources, graph.segments),
        )
        self._stage(
            "segment_evidence_validation",
            lambda: self.validator.validate_evidence(
                graph.resources,
                graph.segments,
                graph.evidence,
            ),
        )
        self._stage(
            "coverage_validation",
            lambda: self.validator.validate_coverage(
                knowledge_graph,
                graph.segments,
                graph.evidence,
                graph.coverage_edges,
            ),
        )
        self._stage(
            "evidence_graph_validation",
            lambda: self.validator.validate_graph(knowledge_graph, graph),
        )
        return EvidenceGenerationResult(
            knowledge_graph=knowledge_graph,
            evidence_graph=graph,
            model_usage=build_result.model_usage,
        )

    @staticmethod
    def _stage(stage: str, operation: Callable[[], ResultT]) -> ResultT:
        try:
            return operation()
        except EvidencePipelineError:
            raise
        except LearningArtifactValidationError as exc:
            raise EvidencePipelineError(
                stage,
                f"{exc.rule} [{exc.path}]: {exc.message}",
                validator_rule=exc.rule,
                field_path=exc.path,
            ) from exc
        except (
            LearningGenerationError,
            LearningModelOutputError,
            VideoSourceProviderError,
            ValidationError,
            ValueError,
        ) as exc:
            raise EvidencePipelineError(
                stage,
                str(getattr(exc, "message", str(exc))),
            ) from exc


__all__ = [
    "EvidenceGenerationPipeline",
    "EvidenceGenerationResult",
    "EvidencePipelineError",
]
