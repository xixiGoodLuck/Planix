from __future__ import annotations

from collections.abc import Callable

from ..evidence.coverage import CoverageAggregator
from ..evidence.mapping import CoverageMapper
from ..evidence.orchestration import GapCompletionOrchestrator
from ..evidence.providers import VideoEvidenceProvider
from ..evidence.services import EvidenceGenerationPipeline
from ..evidence.supplement import EvidenceSupplementer
from ..evidence.transcript import TranscriptProvider
from ..generators import LearningSemanticModel, RouterLearningModel
from ..quality import LearningQualityEngine
from ..selection.services import ContentSelector, PlanComposer
from ..selection.validators import ContentSelectionValidator
from ..services import KnowledgeGenerationPipeline
from .contracts import (
    LearningPipelineRequest,
    LearningPipelineRunResult,
    LearningPipelineStageError,
    PipelineArtifactRef,
    PipelineArtifactType,
)
from .validators import (
    LearningPipelineAssemblyValidator,
    coverage_artifact_ref,
    learning_run_fingerprint,
    pipeline_artifact_ref,
)


class _AssemblyStageFailure(RuntimeError):
    def __init__(self, stage: str, error_type: str, message: str):
        self.stage = stage
        self.error_type = error_type
        self.message = message
        super().__init__(f"{stage}: {message}")


class LearningPipelineRunner:
    PIPELINE_VERSION = "15.0.0"

    def __init__(
        self,
        transcript_provider: TranscriptProvider,
        *,
        model: LearningSemanticModel | None = None,
        knowledge_pipeline: KnowledgeGenerationPipeline | None = None,
        evidence_pipeline_factory: Callable[
            [VideoEvidenceProvider], EvidenceGenerationPipeline
        ]
        | None = None,
        coverage_aggregator: CoverageAggregator | None = None,
        gap_orchestrator: GapCompletionOrchestrator | None = None,
        content_selector: ContentSelector | None = None,
        selection_validator: ContentSelectionValidator | None = None,
        plan_composer: PlanComposer | None = None,
        quality_engine: LearningQualityEngine | None = None,
        validator: LearningPipelineAssemblyValidator | None = None,
    ):
        semantic_model = model or RouterLearningModel()
        self.knowledge_pipeline = knowledge_pipeline or KnowledgeGenerationPipeline(
            semantic_model
        )
        self.evidence_pipeline_factory = evidence_pipeline_factory or (
            lambda provider: EvidenceGenerationPipeline(provider, semantic_model)
        )
        self.coverage_aggregator = coverage_aggregator or CoverageAggregator()
        self.gap_orchestrator = gap_orchestrator or GapCompletionOrchestrator(
            transcript_provider,
            evidence_supplementer=EvidenceSupplementer(
                coverage_mapper=CoverageMapper(model=semantic_model)
            ),
        )
        self.content_selector = content_selector or ContentSelector()
        self.selection_validator = (
            selection_validator or ContentSelectionValidator()
        )
        self.plan_composer = plan_composer or PlanComposer()
        self.quality_engine = quality_engine or LearningQualityEngine()
        self.validator = validator or LearningPipelineAssemblyValidator()
        self._completed_by_fingerprint: dict[str, LearningPipelineRunResult] = {}

    def run(
        self,
        request: LearningPipelineRequest,
        provider: VideoEvidenceProvider,
    ) -> LearningPipelineRunResult:
        fingerprint = learning_run_fingerprint(request, self.PIPELINE_VERSION)
        cached = self._completed_by_fingerprint.get(fingerprint)
        if cached is not None:
            return cached.model_copy(deep=True)

        run_id = f"learning-pipeline-{fingerprint[7:27]}"
        refs: list[PipelineArtifactRef] = []
        self._append_ref(
            refs,
            pipeline_artifact_ref("learning_scope", request.scope),
        )
        final_plan = None
        quality_report = None
        try:
            knowledge_result = self._stage(
                "knowledge_generation",
                lambda: self.knowledge_pipeline.generate(request.scope),
            )
            capability_graph = knowledge_result.capability_graph
            knowledge_graph = knowledge_result.knowledge_graph
            self._append_ref(
                refs,
                pipeline_artifact_ref("capability_graph", capability_graph),
            )
            self._append_ref(
                refs,
                pipeline_artifact_ref("knowledge_graph", knowledge_graph),
            )

            evidence_result = self._stage(
                "evidence_generation",
                lambda: self.evidence_pipeline_factory(provider).generate(
                    knowledge_graph,
                    preferred_urls=request.scope.resource_preference.user_supplied_urls,
                ),
            )
            evidence_graph = evidence_result.evidence_graph
            coverage_report = self._stage(
                "coverage_analysis",
                lambda: self.coverage_aggregator.aggregate(
                    knowledge_graph,
                    evidence_graph,
                ),
            )
            self._append_ref(
                refs,
                pipeline_artifact_ref("evidence_graph", evidence_graph),
            )
            self._append_ref(refs, coverage_artifact_ref(coverage_report))

            gap_result = self._stage(
                "gap_completion",
                lambda: self.gap_orchestrator.run(
                    knowledge_graph,
                    evidence_graph,
                    coverage_report,
                    provider,
                ),
            )
            if gap_result.status != "COMPLETED":
                raise _AssemblyStageFailure(
                    "gap_completion",
                    "GapCompletionIncomplete",
                    gap_result.error
                    or f"gap completion stopped: {gap_result.termination_reason}",
                )
            evidence_graph = gap_result.final_graph
            coverage_report = gap_result.final_report
            self._append_ref(
                refs,
                pipeline_artifact_ref("evidence_graph", evidence_graph),
            )
            self._append_ref(refs, coverage_artifact_ref(coverage_report))

            selection_result = self._stage(
                "content_selection",
                lambda: self.content_selector.select(
                    knowledge_graph,
                    evidence_graph,
                    scope=request.scope,
                ),
            )
            validated_selection = self._stage(
                "content_selection_validation",
                lambda: self.selection_validator.validate_selection(
                    request.scope,
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
                    None,
                )
                raise _AssemblyStageFailure(
                    "content_selection_validation",
                    "SelectionQualityFailed",
                    issue.message if issue else "content selection did not pass",
                )
            content_selection = validated_selection.content_selection
            self._append_ref(
                refs,
                pipeline_artifact_ref("content_selection", content_selection),
            )

            draft_plan = self._stage(
                "learning_content_plan",
                lambda: self.plan_composer.compose(
                    request.scope,
                    knowledge_graph,
                    evidence_graph,
                    content_selection,
                ),
            )
            final_plan = self._stage(
                "learning_content_plan_validation",
                lambda: self.selection_validator.validate_plan(
                    request.scope,
                    knowledge_graph,
                    evidence_graph,
                    content_selection,
                    draft_plan,
                ),
            )
            self._append_ref(
                refs,
                pipeline_artifact_ref("learning_content_plan", final_plan),
            )

            quality_report = self._stage(
                "quality_evaluation",
                lambda: self.quality_engine.evaluate(
                    scope=request.scope,
                    capability_graph=capability_graph,
                    knowledge_graph=knowledge_graph,
                    evidence_graph=evidence_graph,
                    content_selection=content_selection,
                    learning_content_plan=final_plan,
                ),
            )
            self._append_ref(
                refs,
                pipeline_artifact_ref(
                    "learning_quality_report",
                    quality_report,
                ),
            )
            if not quality_report.passed:
                raise _AssemblyStageFailure(
                    "quality_evaluation",
                    "QualityGateFailed",
                    "Learning quality report did not pass",
                )

            result = LearningPipelineRunResult(
                runId=run_id,
                runFingerprint=fingerprint,
                pipelineVersion=self.PIPELINE_VERSION,
                status="completed",
                artifactRefs=refs,
                qualityReport=quality_report,
                finalPlan=final_plan,
            )
            self.validator.validate_completed(
                request,
                result,
                expected_fingerprint=fingerprint,
                capability_graph=capability_graph,
                knowledge_graph=knowledge_graph,
                evidence_graph=evidence_graph,
                coverage_report=coverage_report,
                content_selection=content_selection,
                final_plan=final_plan,
                quality_report=quality_report,
            )
            self._completed_by_fingerprint[fingerprint] = result.model_copy(deep=True)
            return result
        except _AssemblyStageFailure as exc:
            return self._failed_result(
                run_id,
                fingerprint,
                refs,
                exc,
                final_plan=final_plan,
                quality_report=quality_report,
            )
        except Exception as exc:
            return self._failed_result(
                run_id,
                fingerprint,
                refs,
                _AssemblyStageFailure(
                    "assembly_validation",
                    type(exc).__name__,
                    str(exc) or type(exc).__name__,
                ),
                final_plan=final_plan,
                quality_report=quality_report,
            )

    @staticmethod
    def _stage(stage: str, operation):
        try:
            return operation()
        except _AssemblyStageFailure:
            raise
        except Exception as exc:
            raise _AssemblyStageFailure(
                stage,
                type(exc).__name__,
                str(exc) or type(exc).__name__,
            ) from exc

    def _failed_result(
        self,
        run_id,
        fingerprint,
        refs,
        failure,
        *,
        final_plan,
        quality_report,
    ) -> LearningPipelineRunResult:
        result = LearningPipelineRunResult(
            runId=run_id,
            runFingerprint=fingerprint,
            pipelineVersion=self.PIPELINE_VERSION,
            status="failed",
            artifactRefs=refs,
            qualityReport=quality_report,
            finalPlan=final_plan,
            error=LearningPipelineStageError(
                stage=failure.stage,
                errorType=failure.error_type,
                message=failure.message,
            ),
        )
        self.validator.validate_failed(
            result,
            expected_fingerprint=fingerprint,
        )
        return result

    @staticmethod
    def _append_ref(
        refs: list[PipelineArtifactRef],
        reference: PipelineArtifactRef,
    ) -> None:
        key = (
            reference.artifact_type,
            reference.artifact_id,
            reference.version,
        )
        if key not in {
            (item.artifact_type, item.artifact_id, item.version) for item in refs
        }:
            refs.append(reference)


__all__ = ["LearningPipelineRunner"]
