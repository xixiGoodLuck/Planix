from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from pydantic import ValidationError

from ..contracts import CapabilityGraph, KnowledgeGraph, LearningOutcome, LearningScope
from ..generators import (
    CapabilityGenerator,
    KnowledgeGenerator,
    LearningGenerationError,
    LearningModelOutputError,
    LearningOutcomeGenerator,
    LearningSemanticModel,
    RouterLearningModel,
)
from ..validators import LearningArtifactValidationError, LearningArtifactValidator


ResultT = TypeVar("ResultT")


class KnowledgePipelineError(RuntimeError):
    def __init__(self, stage: str, message: str):
        self.stage = stage
        super().__init__(f"{stage}: {message}")


@dataclass(frozen=True)
class KnowledgeGenerationResult:
    scope: LearningScope
    outcomes: list[LearningOutcome]
    capability_graph: CapabilityGraph
    knowledge_graph: KnowledgeGraph
    model_usage: dict[str, dict[str, Any]]


class KnowledgeGenerationPipeline:
    """Isolated Scope -> Outcome -> Capability -> Knowledge generation pipeline."""

    def __init__(
        self,
        model: LearningSemanticModel | None = None,
        validator: LearningArtifactValidator | None = None,
    ):
        semantic_model = model or RouterLearningModel()
        self.validator = validator or LearningArtifactValidator()
        self.outcome_generator = LearningOutcomeGenerator(semantic_model)
        self.capability_generator = CapabilityGenerator(semantic_model)
        self.knowledge_generator = KnowledgeGenerator(semantic_model)

    def generate(self, scope: LearningScope) -> KnowledgeGenerationResult:
        outcome_result = self._stage(
            "learning_outcomes",
            lambda: self.outcome_generator.generate(scope),
        )
        self._stage(
            "learning_outcomes_validation",
            lambda: self.validator.validate_outcomes(scope, outcome_result.outcomes),
        )

        capability_result = self._stage(
            "learning_capabilities",
            lambda: self.capability_generator.generate(scope, outcome_result.outcomes),
        )
        self._stage(
            "learning_capabilities_validation",
            lambda: self.validator.validate_capability_graph(
                scope,
                capability_result.capability_graph,
            ),
        )

        knowledge_result = self._stage(
            "learning_knowledge",
            lambda: self.knowledge_generator.generate(
                scope,
                capability_result.capability_graph,
            ),
        )
        self._stage(
            "learning_knowledge_validation",
            lambda: self.validator.validate_knowledge_graph(
                scope,
                capability_result.capability_graph,
                knowledge_result.knowledge_graph,
            ),
        )
        return KnowledgeGenerationResult(
            scope=scope,
            outcomes=outcome_result.outcomes,
            capability_graph=capability_result.capability_graph,
            knowledge_graph=knowledge_result.knowledge_graph,
            model_usage={
                "outcomes": outcome_result.model_usage,
                "capabilities": capability_result.model_usage,
                "knowledge": knowledge_result.model_usage,
            },
        )

    @staticmethod
    def _stage(stage: str, operation: Callable[[], ResultT]) -> ResultT:
        try:
            return operation()
        except KnowledgePipelineError:
            raise
        except (
            LearningArtifactValidationError,
            LearningGenerationError,
            LearningModelOutputError,
            ValidationError,
        ) as exc:
            if isinstance(exc, LearningArtifactValidationError):
                message = f"{exc.rule} [{exc.path}]: {exc.message}"
            else:
                message = str(getattr(exc, "message", str(exc)))
            raise KnowledgePipelineError(
                stage,
                message,
            ) from exc


__all__ = [
    "KnowledgeGenerationPipeline",
    "KnowledgeGenerationResult",
    "KnowledgePipelineError",
]
