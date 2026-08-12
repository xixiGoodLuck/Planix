from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Literal, TypeVar

from pydantic import ValidationError

from ..contracts import (
    CapabilityGraph,
    ContentSelection,
    EvidenceGraph,
    EvidenceInterventionGap,
    EvidenceInterventionCoverage,
    EvidenceInterventionReport,
    KnowledgeGraph,
    LearningArtifact,
    LearningContentPlan,
    LearningQualityReport,
    LearningScope,
)
from ..evidence.coverage import CoverageAggregator, CoverageReport
from ..evidence.orchestration import (
    GapCompletionBudget,
    GapCompletionOrchestrator,
    GapCompletionResult,
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
from ..generators.base import artifact_ref, generated_id
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
    coverage_report: CoverageReport
    content_selection: ContentSelection
    learning_content_plan: LearningContentPlan
    quality_report: LearningQualityReport
    model_usage: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class LearningPipelineWaitingEvidenceResult:
    scope: LearningScope
    capability_graph: CapabilityGraph
    knowledge_graph: KnowledgeGraph
    evidence_graph: EvidenceGraph | None
    coverage_report: CoverageReport | None
    intervention_report: EvidenceInterventionReport
    model_usage: dict[str, dict[str, Any]]


LearningPipelineOutcome = LearningPipelineResult | LearningPipelineWaitingEvidenceResult


class LearningPipeline:
    """The single canonical Scope -> verified LearningContentPlan orchestration."""

    def __init__(
        self,
        *,
        provider: VideoEvidenceProvider,
        model: LearningSemanticModel | None = None,
        artifact_validator: LearningArtifactValidator | None = None,
        selection_validator: ContentSelectionValidator | None = None,
        quality_engine: LearningQualityEngine | None = None,
        coverage_aggregator: CoverageAggregator | None = None,
        gap_orchestrator: GapCompletionOrchestrator | None = None,
    ):
        semantic_model = model or RouterLearningModel()
        self.provider = provider
        self.semantic_model = semantic_model
        self.artifact_validator = artifact_validator or LearningArtifactValidator()
        self.selection_validator = selection_validator or ContentSelectionValidator(
            artifact_validator=self.artifact_validator
        )
        self.outcome_generator = LearningOutcomeGenerator(semantic_model)
        self.capability_generator = CapabilityGenerator(semantic_model)
        self.knowledge_generator = KnowledgeGenerator(semantic_model)
        self.evidence_pipeline = EvidenceGenerationPipeline(provider, semantic_model)
        self.coverage_aggregator = coverage_aggregator or CoverageAggregator()
        self.gap_orchestrator = gap_orchestrator
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
    ) -> LearningPipelineOutcome:
        self._notify(progress_callback, "knowledge_generation", "started")
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
            "knowledge_generation",
            "artifact",
            capability_graph,
        )

        knowledge_result = self._stage(
            "learning_knowledge",
            "knowledge_graph",
            lambda: self.knowledge_generator.generate_validated(
                scope,
                capability_graph,
                self.artifact_validator,
            ),
        )
        knowledge_graph = knowledge_result.knowledge_graph
        self._notify(
            progress_callback,
            "knowledge_generation",
            "completed",
            knowledge_graph,
        )
        return self._run_evidence_to_quality(
            scope,
            capability_graph,
            knowledge_graph,
            existing_evidence_graph=None,
            progress_callback=progress_callback,
            model_usage={
                "outcomes": outcome_result.model_usage,
                "capabilities": capability_result.model_usage,
                "knowledge": knowledge_result.model_usage,
            },
        )

    def resume_evidence(
        self,
        scope: LearningScope,
        capability_graph: CapabilityGraph,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph | None,
        *,
        progress_callback: PipelineProgressCallback | None = None,
    ) -> LearningPipelineOutcome:
        self.artifact_validator.validate_capability_graph(scope, capability_graph)
        self.artifact_validator.validate_knowledge_graph(
            scope,
            capability_graph,
            knowledge_graph,
        )
        if evidence_graph is not None:
            self.artifact_validator.validate_evidence_graph(
                knowledge_graph,
                evidence_graph,
            )
        return self._run_evidence_to_quality(
            scope,
            capability_graph,
            knowledge_graph,
            existing_evidence_graph=evidence_graph,
            progress_callback=progress_callback,
            model_usage={},
        )

    def _run_evidence_to_quality(
        self,
        scope: LearningScope,
        capability_graph: CapabilityGraph,
        knowledge_graph: KnowledgeGraph,
        *,
        existing_evidence_graph: EvidenceGraph | None,
        progress_callback: PipelineProgressCallback | None,
        model_usage: dict[str, dict[str, Any]],
    ) -> LearningPipelineOutcome:
        evidence_graph = existing_evidence_graph
        if evidence_graph is None:
            self._notify(progress_callback, "evidence_generation", "started")
            try:
                evidence_result = self._stage(
                    "learning_evidence",
                    "evidence_graph",
                    lambda: self.evidence_pipeline.generate(
                        knowledge_graph,
                        preferred_urls=scope.resource_preference.user_supplied_urls,
                    ),
                )
            except LearningPipelineError as exc:
                if exc.validator_rule not in {
                    "transcript_required",
                    "video_resource_required",
                }:
                    raise
                return self._waiting_without_evidence(
                    scope,
                    capability_graph,
                    knowledge_graph,
                    progress_callback,
                    model_usage,
                )
            evidence_graph = evidence_result.evidence_graph
            model_usage["evidence"] = evidence_result.model_usage
            self._notify(
                progress_callback,
                "evidence_generation",
                "completed",
                evidence_graph,
            )
        else:
            self._notify(progress_callback, "evidence_generation", "started")
            try:
                refreshed = self._stage(
                    "learning_evidence",
                    "evidence_graph",
                    lambda: self.evidence_pipeline.generate(
                        knowledge_graph,
                        preferred_urls=scope.resource_preference.user_supplied_urls,
                    ),
                )
            except LearningPipelineError as exc:
                if exc.validator_rule not in {
                    "transcript_required",
                    "video_resource_required",
                }:
                    raise
            else:
                model_usage["evidence"] = refreshed.model_usage
                candidate = refreshed.evidence_graph.model_copy(
                    deep=True,
                    update={
                        "artifact_id": evidence_graph.artifact_id,
                        "version": evidence_graph.version + 1,
                    },
                )
                if not self._same_evidence_content(evidence_graph, candidate):
                    self.artifact_validator.validate_evidence_graph(
                        knowledge_graph,
                        candidate,
                    )
                    evidence_graph = candidate
                    self._notify(
                        progress_callback,
                        "evidence_generation",
                        "artifact",
                        evidence_graph,
                    )
            self._notify(
                progress_callback,
                "evidence_generation",
                "completed",
                evidence_graph,
            )

        self._notify(progress_callback, "coverage_analysis", "started")
        coverage_report = self._stage(
            "learning_coverage_analysis",
            "coverage_report",
            lambda: self.coverage_aggregator.aggregate(
                knowledge_graph,
                evidence_graph,
            ),
        )
        self._notify(progress_callback, "coverage_analysis", "completed")

        self._notify(progress_callback, "gap_completion", "started")
        gap_result = self._complete_gaps(
            knowledge_graph,
            evidence_graph,
            coverage_report,
        )
        if gap_result.status == "FAILED":
            raise LearningPipelineError(
                stage="learning_gap_completion",
                artifact_type="evidence_graph",
                validator_rule="gap_completion_failed",
                field_path="gapCompletion",
                message=gap_result.error or "gap completion failed",
            )
        evidence_graph = gap_result.final_graph
        coverage_report = gap_result.final_report
        if evidence_graph.version != gap_result.initial_graph_ref.version:
            self._notify(
                progress_callback,
                "gap_completion",
                "artifact",
                evidence_graph,
            )
        self._notify(progress_callback, "gap_completion", "completed")

        if gap_result.status != "COMPLETED":
            intervention = self._intervention_report(
                knowledge_graph,
                evidence_graph,
                coverage_report,
                gap_result,
            )
            self._notify(
                progress_callback,
                "gap_completion",
                "artifact",
                intervention,
            )
            return LearningPipelineWaitingEvidenceResult(
                scope=scope,
                capability_graph=capability_graph,
                knowledge_graph=knowledge_graph,
                evidence_graph=evidence_graph,
                coverage_report=coverage_report,
                intervention_report=intervention,
                model_usage=model_usage,
            )

        self._notify(progress_callback, "selection", "started")
        selection_result = self._stage(
            "learning_selection",
            "content_selection",
            lambda: self.content_selector.select(
                knowledge_graph,
                evidence_graph,
                scope=scope,
            ),
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
            "selection",
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
            "selection",
            "completed",
            learning_content_plan,
        )

        self._notify(progress_callback, "quality", "started")
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
        self._notify(progress_callback, "quality", "artifact", quality_report)
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
        self._notify(progress_callback, "quality", "completed")
        return LearningPipelineResult(
            scope=scope,
            capability_graph=capability_graph,
            knowledge_graph=knowledge_graph,
            evidence_graph=evidence_graph,
            coverage_report=coverage_report,
            content_selection=content_selection,
            learning_content_plan=learning_content_plan,
            quality_report=quality_report,
            model_usage=model_usage,
        )

    def _complete_gaps(
        self,
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        coverage_report: CoverageReport,
    ) -> GapCompletionResult:
        if self.gap_orchestrator is None:
            required_full = all(
                next(
                    item.coverage_strength
                    for item in coverage_report.knowledge_coverage
                    if item.knowledge_id == node.id
                )
                == "FULL"
                for node in knowledge_graph.nodes
                if node.importance == "required"
            )
            status = "COMPLETED" if required_full else "INCOMPLETE"
            reason = "REQUIRED_COVERAGE_FULL" if required_full else "NO_EXECUTABLE_GAP"
            return GapCompletionResult(
                runId="gap-completion-unavailable",
                status=status,
                maxRounds=GapCompletionBudget().max_rounds,
                initialGraphRef=artifact_ref("evidence_graph", evidence_graph),
                initialReport=coverage_report,
                finalGraph=evidence_graph,
                finalReport=coverage_report,
                rounds=[],
                terminationReason=reason,
                budget=GapCompletionBudget(),
            )
        return self.gap_orchestrator.run(
            knowledge_graph,
            evidence_graph,
            coverage_report,
            self.provider,
        )

    def _waiting_without_evidence(
        self,
        scope: LearningScope,
        capability_graph: CapabilityGraph,
        knowledge_graph: KnowledgeGraph,
        progress_callback: PipelineProgressCallback | None,
        model_usage: dict[str, dict[str, Any]],
    ) -> LearningPipelineWaitingEvidenceResult:
        self._notify(progress_callback, "coverage_analysis", "started")
        self._notify(progress_callback, "coverage_analysis", "completed")
        self._notify(progress_callback, "gap_completion", "started")
        intervention = EvidenceInterventionReport(
            artifactId=generated_id(
                "evidence-intervention",
                knowledge_graph.artifact_id,
                0,
                "required-evidence",
            ),
            knowledgeGraphRef=artifact_ref("knowledge_graph", knowledge_graph),
            evidenceGraphRef=None,
            requiredGaps=[
                EvidenceInterventionGap(
                    knowledgeId=node.id,
                    knowledgeName=node.name,
                    gapType="missing_knowledge",
                    coverageStrength="MISSING",
                    missingOrPartialReason="No active verified transcript supports this required knowledge.",
                )
                for node in knowledge_graph.nodes
                if node.importance == "required"
            ],
            knowledgeCoverage=[
                EvidenceInterventionCoverage(
                    knowledgeId=node.id,
                    status="missing",
                    coverageStrength="MISSING",
                )
                for node in knowledge_graph.nodes
            ],
            searchedResourceRefs=scope.resource_preference.user_supplied_urls,
            transcriptUnavailableResourceRefs=scope.resource_preference.user_supplied_urls,
            recommendedAction="add_video_or_transcript",
        )
        self._notify(progress_callback, "gap_completion", "artifact", intervention)
        self._notify(progress_callback, "gap_completion", "completed")
        return LearningPipelineWaitingEvidenceResult(
            scope=scope,
            capability_graph=capability_graph,
            knowledge_graph=knowledge_graph,
            evidence_graph=None,
            coverage_report=None,
            intervention_report=intervention,
            model_usage=model_usage,
        )

    @staticmethod
    def _intervention_report(
        knowledge_graph: KnowledgeGraph,
        evidence_graph: EvidenceGraph,
        coverage_report: CoverageReport,
        gap_result: GapCompletionResult,
    ) -> EvidenceInterventionReport:
        coverage = {
            item.knowledge_id: item for item in coverage_report.knowledge_coverage
        }
        required_nodes = [
            node
            for node in knowledge_graph.nodes
            if node.importance == "required"
            and coverage[node.id].coverage_strength != "FULL"
        ]
        gap_by_key = {
            (item.knowledge_id, item.gap_type): item
            for item in coverage_report.gaps
        }
        return EvidenceInterventionReport(
            artifactId=generated_id(
                "evidence-intervention",
                knowledge_graph.artifact_id,
                0,
                "required-evidence",
            ),
            version=evidence_graph.version,
            knowledgeGraphRef=artifact_ref("knowledge_graph", knowledge_graph),
            evidenceGraphRef=artifact_ref("evidence_graph", evidence_graph),
            requiredGaps=[
                EvidenceInterventionGap(
                    knowledgeId=node.id,
                    knowledgeName=node.name,
                    gapType=(
                        "missing_knowledge"
                        if coverage[node.id].coverage_strength == "MISSING"
                        else "weak_coverage"
                        if coverage[node.id].coverage_strength == "WEAK"
                        else "unsupported_required"
                    ),
                    coverageStrength=coverage[node.id].coverage_strength,
                    missingOrPartialReason=gap_by_key[
                        (
                            node.id,
                            "missing_knowledge"
                            if coverage[node.id].coverage_strength == "MISSING"
                            else "weak_coverage"
                            if coverage[node.id].coverage_strength == "WEAK"
                            else "unsupported_required",
                        )
                    ].reason,
                )
                for node in required_nodes
            ],
            knowledgeCoverage=[
                EvidenceInterventionCoverage(
                    knowledgeId=item.knowledge_id,
                    status=item.status,
                    coverageStrength=item.coverage_strength,
                    evidenceRefs=item.evidence_refs,
                    segmentRefs=item.segment_refs,
                )
                for item in coverage_report.knowledge_coverage
            ],
            searchedQueries=gap_result.searched_queries,
            searchedResourceRefs=gap_result.searched_resource_refs,
            transcriptUnavailableResourceRefs=(
                gap_result.transcript_unavailable_resource_refs
            ),
            recommendedAction="add_video_or_transcript",
        )

    generate = run

    @staticmethod
    def _same_evidence_content(
        current: EvidenceGraph,
        candidate: EvidenceGraph,
    ) -> bool:
        def normalized(graph: EvidenceGraph) -> str:
            payload = graph.model_dump(mode="json", by_alias=True)
            payload.pop("version", None)
            payload.pop("createdAt", None)
            for resource in payload.get("resources", []):
                resource.pop("observedAt", None)
            for evidence in payload.get("evidence", []):
                evidence.pop("observedAt", None)
            return json.dumps(payload, ensure_ascii=False, sort_keys=True)

        return normalized(current) == normalized(candidate)

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
    "LearningPipelineOutcome",
    "LearningPipelineResult",
    "LearningPipelineWaitingEvidenceResult",
    "PipelineProgressCallback",
    "PipelineProgressStatus",
]
