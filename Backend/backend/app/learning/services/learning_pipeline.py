from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, TypeVar

from pydantic import ValidationError

from ..contracts import (
    CapabilityGraph,
    ContentSelection,
    EvidenceGraph,
    KnowledgeGraph,
    LearningArtifact,
    LearningContentPlan,
    LearningQualityReport,
    LearningScope,
)
from ..evidence.providers import VideoEvidenceProvider, VideoSourceProviderError
from ..evidence.services import EvidenceGenerationPipeline, EvidencePipelineError
from ..generators import (
    CapabilityGenerator,
    KnowledgeGenerator,
    LearningGenerationError,
    LearningModelOutputError,
    LearningOutcomeGenerator,
    LearningSemanticModel,
    RouterLearningModel,
)
from ..quality import LearningQualityEngine
from ..selection.services import ContentSelector, PlanComposer
from ..selection.validators import ContentSelectionValidator
from ..validators import LearningArtifactValidationError, LearningArtifactValidator


ResultT = TypeVar("ResultT")
PipelineProgressStatus = Literal["started", "artifact", "completed"]
PipelineProgressCallback = Callable[
    [str, PipelineProgressStatus, LearningArtifact | None],
    None,
]


class LearningPipelineError(RuntimeError):
    def __init__(
        self,
        *,
        stage: str,
        artifact_type: str,
        validator_rule: str,
        field_path: str,
        message: str,
    ):
        self.stage = stage
        self.artifact_type = artifact_type
        self.validator_rule = validator_rule
        self.field_path = field_path
        self.message = message
        super().__init__(
            f"{stage} ({artifact_type}) {validator_rule} [{field_path}]: {message}"
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "stage": self.stage,
            "artifact_type": self.artifact_type,
            "validator_rule": self.validator_rule,
            "field_path": self.field_path,
            "message": self.message,
        }


@dataclass(frozen=True)
class LearningPipelineResult:
    scope: LearningScope
    capability_graph: CapabilityGraph
    knowledge_graph: KnowledgeGraph
    evidence_graph: EvidenceGraph
    content_selection: ContentSelection
    learning_content_plan: LearningContentPlan
    quality_report: LearningQualityReport
    model_usage: dict[str, dict[str, Any]]


class LearningPipeline:
    """Isolated Scope -> trusted LearningContentPlan orchestration service."""

    def __init__(
        self,
        *,
        provider: VideoEvidenceProvider,
        model: LearningSemanticModel | None = None,
        artifact_validator: LearningArtifactValidator | None = None,
        selection_validator: ContentSelectionValidator | None = None,
        quality_engine: LearningQualityEngine | None = None,
    ):
        semantic_model = model or RouterLearningModel()
        self.artifact_validator = artifact_validator or LearningArtifactValidator()
        self.selection_validator = selection_validator or ContentSelectionValidator(
            artifact_validator=self.artifact_validator
        )
        self.outcome_generator = LearningOutcomeGenerator(semantic_model)
        self.capability_generator = CapabilityGenerator(semantic_model)
        self.knowledge_generator = KnowledgeGenerator(semantic_model)
        self.evidence_pipeline = EvidenceGenerationPipeline(provider, semantic_model)
        self.content_selector = ContentSelector()
        self.plan_composer = PlanComposer()
        self.quality_engine = quality_engine or LearningQualityEngine(
            artifact_validator=self.artifact_validator
        )

    def run(
        self,
        scope: LearningScope,
        *,
        progress_callback: PipelineProgressCallback | None = None,
    ) -> LearningPipelineResult:
        self._notify(progress_callback, "knowledge_generating", "started")
        outcome_result = self._stage(
            "learning_outcomes",
            "learning_outcome",
            lambda: self.outcome_generator.generate(scope),
        )
        self._stage(
            "learning_outcomes_validation",
            "learning_outcome",
            lambda: self.artifact_validator.validate_outcomes(
                scope,
                outcome_result.outcomes,
            ),
        )
        capability_result = self._stage(
            "learning_capabilities",
            "capability_graph",
            lambda: self.capability_generator.generate(scope, outcome_result.outcomes),
        )
        capability_graph = capability_result.capability_graph
        self._stage(
            "learning_capabilities_validation",
            "capability_graph",
            lambda: self.artifact_validator.validate_capability_graph(
                scope,
                capability_graph,
            ),
        )
        self._notify(
            progress_callback,
            "knowledge_generating",
            "artifact",
            capability_graph,
        )

        knowledge_result = self._stage(
            "learning_knowledge",
            "knowledge_graph",
            lambda: self.knowledge_generator.generate(scope, capability_graph),
        )
        knowledge_graph = knowledge_result.knowledge_graph
        self._stage(
            "learning_knowledge_validation",
            "knowledge_graph",
            lambda: self.artifact_validator.validate_knowledge_graph(
                scope,
                capability_graph,
                knowledge_graph,
            ),
        )
        self._notify(
            progress_callback,
            "knowledge_generating",
            "completed",
            knowledge_graph,
        )

        self._notify(progress_callback, "evidence_generating", "started")
        evidence_result = self._stage(
            "learning_evidence",
            "evidence_graph",
            lambda: self.evidence_pipeline.generate(knowledge_graph),
        )
        evidence_graph = evidence_result.evidence_graph
        self._stage(
            "learning_evidence_validation",
            "evidence_graph",
            lambda: self.artifact_validator.validate_evidence_graph(
                knowledge_graph,
                evidence_graph,
            ),
        )
        self._notify(
            progress_callback,
            "evidence_generating",
            "completed",
            evidence_graph,
        )

        self._notify(progress_callback, "content_selecting", "started")
        selection_result = self._stage(
            "learning_selection",
            "content_selection",
            lambda: self.content_selector.select(knowledge_graph, evidence_graph),
        )
        validated_selection = self._stage(
            "learning_selection_validation",
            "content_selection",
            lambda: self.selection_validator.validate_selection(
                scope,
                knowledge_graph,
                evidence_graph,
                selection_result.content_selection,
            ),
        )
        if not validated_selection.report.passed:
            issue = next(
                (
                    item
                    for item in validated_selection.report.issues
                    if item.severity in {"blocker", "major"}
                ),
                validated_selection.report.issues[0]
                if validated_selection.report.issues
                else None,
            )
            raise LearningPipelineError(
                stage="learning_selection_validation",
                artifact_type="content_selection",
                validator_rule=issue.rule if issue else "selection_validation",
                field_path="contentSelection",
                message=issue.message if issue else "selection quality gate failed",
            )
        content_selection = validated_selection.content_selection
        self._notify(
            progress_callback,
            "content_selecting",
            "artifact",
            content_selection,
        )

        draft_plan = self._stage(
            "learning_plan",
            "learning_content_plan",
            lambda: self.plan_composer.compose(
                scope,
                knowledge_graph,
                evidence_graph,
                content_selection,
            ),
        )
        learning_content_plan = self._stage(
            "learning_plan_validation",
            "learning_content_plan",
            lambda: self.selection_validator.validate_plan(
                scope,
                knowledge_graph,
                evidence_graph,
                content_selection,
                draft_plan,
            ),
        )
        self._notify(
            progress_callback,
            "content_selecting",
            "completed",
            learning_content_plan,
        )

        self._notify(progress_callback, "quality_checking", "started")
        quality_report = self._stage(
            "learning_quality",
            "learning_quality_report",
            lambda: self.quality_engine.evaluate(
                scope=scope,
                capability_graph=capability_graph,
                knowledge_graph=knowledge_graph,
                evidence_graph=evidence_graph,
                content_selection=content_selection,
                learning_content_plan=learning_content_plan,
            ),
        )
        self._notify(
            progress_callback,
            "quality_checking",
            "artifact",
            quality_report,
        )
        if not quality_report.passed:
            issue = next(
                (
                    item
                    for item in quality_report.issues
                    if item.severity in {"blocker", "major"}
                ),
                quality_report.issues[0] if quality_report.issues else None,
            )
            raise LearningPipelineError(
                stage="learning_quality",
                artifact_type="learning_quality_report",
                validator_rule=issue.rule if issue else "quality_gate",
                field_path=issue.target_type if issue else "learningQualityReport",
                message=issue.description if issue else "Learning quality gate failed",
            )
        self._notify(progress_callback, "quality_checking", "completed")
        return LearningPipelineResult(
            scope=scope,
            capability_graph=capability_graph,
            knowledge_graph=knowledge_graph,
            evidence_graph=evidence_graph,
            content_selection=content_selection,
            learning_content_plan=learning_content_plan,
            quality_report=quality_report,
            model_usage={
                "outcomes": outcome_result.model_usage,
                "capabilities": capability_result.model_usage,
                "knowledge": knowledge_result.model_usage,
                "evidence": evidence_result.model_usage,
            },
        )

    generate = run

    @staticmethod
    def _notify(
        callback: PipelineProgressCallback | None,
        stage: str,
        status: PipelineProgressStatus,
        artifact: LearningArtifact | None = None,
    ) -> None:
        if callback is not None:
            callback(stage, status, artifact)

    @staticmethod
    def _stage(
        stage: str,
        artifact_type: str,
        operation: Callable[[], ResultT],
    ) -> ResultT:
        try:
            return operation()
        except LearningPipelineError:
            raise
        except LearningArtifactValidationError as exc:
            raise LearningPipelineError(
                stage=stage,
                artifact_type=artifact_type,
                validator_rule=exc.rule,
                field_path=exc.path,
                message=exc.message,
            ) from exc
        except EvidencePipelineError as exc:
            raise LearningPipelineError(
                stage=stage,
                artifact_type=artifact_type,
                validator_rule=exc.validator_rule,
                field_path=exc.field_path,
                message=str(exc),
            ) from exc
        except (
            LearningGenerationError,
            LearningModelOutputError,
            VideoSourceProviderError,
            ValidationError,
            ValueError,
        ) as exc:
            raise LearningPipelineError(
                stage=stage,
                artifact_type=artifact_type,
                validator_rule="generation_contract",
                field_path=str(getattr(exc, "stage", stage)),
                message=str(getattr(exc, "message", str(exc))),
            ) from exc


__all__ = [
    "LearningPipeline",
    "LearningPipelineError",
    "LearningPipelineResult",
    "PipelineProgressCallback",
    "PipelineProgressStatus",
]
