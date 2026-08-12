from __future__ import annotations

from collections.abc import Callable

from ..contracts import LearningArtifact, LearningContentPlan, LearningQualityReport
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
from ..services import (
    KnowledgeGenerationPipeline,
    LearningPipeline,
    LearningPipelineResult,
    LearningPipelineWaitingEvidenceResult,
)
from .contracts import (
    LearningPipelineRequest,
    LearningPipelineRunResult,
    LearningPipelineStageError,
    PipelineArtifactRef,
)
from .validators import (
    LearningPipelineAssemblyValidator,
    coverage_artifact_ref,
    learning_run_fingerprint,
    pipeline_artifact_ref,
)


class LearningPipelineRunner:
    """Deprecated offline adapter over the single canonical LearningPipeline."""

    PIPELINE_VERSION = "30.0.0"

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
        self.semantic_model = model or RouterLearningModel()
        self.transcript_provider = transcript_provider
        self.knowledge_pipeline = knowledge_pipeline
        self.evidence_pipeline_factory = evidence_pipeline_factory
        self.coverage_aggregator = coverage_aggregator or CoverageAggregator()
        self.gap_orchestrator = gap_orchestrator or GapCompletionOrchestrator(
            transcript_provider,
            evidence_supplementer=EvidenceSupplementer(
                coverage_mapper=CoverageMapper(model=self.semantic_model)
            ),
        )
        self.content_selector = content_selector
        self.selection_validator = selection_validator
        self.plan_composer = plan_composer
        self.quality_engine = quality_engine
        self.validator = validator or LearningPipelineAssemblyValidator()

    def run(
        self,
        request: LearningPipelineRequest,
        provider: VideoEvidenceProvider,
    ) -> LearningPipelineRunResult:
        fingerprint = learning_run_fingerprint(request, self.PIPELINE_VERSION)
        run_id = f"learning-pipeline-{fingerprint[7:27]}"
        refs = [pipeline_artifact_ref("learning_scope", request.scope)]
        current_stage = "scope"
        captured: dict[str, LearningArtifact] = {}

        pipeline = LearningPipeline(
            provider=provider,
            model=self.semantic_model,
            selection_validator=self.selection_validator,
            quality_engine=self.quality_engine,
            coverage_aggregator=self.coverage_aggregator,
            gap_orchestrator=self.gap_orchestrator,
        )
        if self.knowledge_pipeline is not None:
            pipeline.outcome_generator = self.knowledge_pipeline.outcome_generator
            pipeline.capability_generator = self.knowledge_pipeline.capability_generator
            pipeline.knowledge_generator = self.knowledge_pipeline.knowledge_generator
            pipeline.artifact_validator = self.knowledge_pipeline.validator
        if self.evidence_pipeline_factory is not None:
            pipeline.evidence_pipeline = self.evidence_pipeline_factory(provider)
        if self.content_selector is not None:
            pipeline.content_selector = self.content_selector
        if self.plan_composer is not None:
            pipeline.plan_composer = self.plan_composer

        def progress(stage: str, _status: str, artifact: LearningArtifact | None) -> None:
            nonlocal current_stage
            current_stage = stage
            if artifact is not None:
                captured[artifact_type(artifact)] = artifact
                self._append_ref(
                    refs,
                    pipeline_artifact_ref(artifact_type=artifact_type(artifact), artifact=artifact),
                )

        try:
            outcome = pipeline.run(request.scope, progress_callback=progress)
            if isinstance(outcome, LearningPipelineWaitingEvidenceResult):
                if outcome.coverage_report is not None:
                    self._append_ref(refs, coverage_artifact_ref(outcome.coverage_report))
                return LearningPipelineRunResult(
                    runId=run_id,
                    runFingerprint=fingerprint,
                    pipelineVersion=self.PIPELINE_VERSION,
                    status="waiting_evidence",
                    artifactRefs=refs,
                    interventionReport=outcome.intervention_report,
                )

            self._append_ref(refs, coverage_artifact_ref(outcome.coverage_report))
            result = LearningPipelineRunResult(
                runId=run_id,
                runFingerprint=fingerprint,
                pipelineVersion=self.PIPELINE_VERSION,
                status="completed",
                artifactRefs=refs,
                qualityReport=outcome.quality_report,
                finalPlan=outcome.learning_content_plan,
            )
            self._validate_completed(request, result, fingerprint, outcome)
            return result
        except Exception as exc:
            result = LearningPipelineRunResult(
                runId=run_id,
                runFingerprint=fingerprint,
                pipelineVersion=self.PIPELINE_VERSION,
                status="failed",
                artifactRefs=refs,
                finalPlan=(
                    captured.get("learning_content_plan")
                    if isinstance(
                        captured.get("learning_content_plan"),
                        LearningContentPlan,
                    )
                    else None
                ),
                qualityReport=(
                    captured.get("learning_quality_report")
                    if isinstance(
                        captured.get("learning_quality_report"),
                        LearningQualityReport,
                    )
                    else None
                ),
                error=LearningPipelineStageError(
                    stage=current_stage,
                    errorType=type(exc).__name__,
                    message=str(exc) or type(exc).__name__,
                ),
            )
            self.validator.validate_failed(result, expected_fingerprint=fingerprint)
            return result

    def _validate_completed(
        self,
        request: LearningPipelineRequest,
        result: LearningPipelineRunResult,
        fingerprint: str,
        outcome: LearningPipelineResult,
    ) -> None:
        self.validator.validate_completed(
            request,
            result,
            expected_fingerprint=fingerprint,
            capability_graph=outcome.capability_graph,
            knowledge_graph=outcome.knowledge_graph,
            evidence_graph=outcome.evidence_graph,
            coverage_report=outcome.coverage_report,
            content_selection=outcome.content_selection,
            final_plan=outcome.learning_content_plan,
            quality_report=outcome.quality_report,
        )

    @staticmethod
    def _append_ref(
        refs: list[PipelineArtifactRef],
        reference: PipelineArtifactRef,
    ) -> None:
        key = (reference.artifact_type, reference.artifact_id, reference.version)
        if key not in {
            (item.artifact_type, item.artifact_id, item.version) for item in refs
        }:
            refs.append(reference)


def artifact_type(artifact: LearningArtifact):
    from ..contracts import (
        CapabilityGraph,
        ContentSelection,
        EvidenceGraph,
        EvidenceInterventionReport,
        KnowledgeGraph,
        LearningContentPlan,
        LearningQualityReport,
    )

    mapping = {
        CapabilityGraph: "capability_graph",
        KnowledgeGraph: "knowledge_graph",
        EvidenceGraph: "evidence_graph",
        EvidenceInterventionReport: "evidence_intervention_report",
        ContentSelection: "content_selection",
        LearningContentPlan: "learning_content_plan",
        LearningQualityReport: "learning_quality_report",
    }
    return mapping[type(artifact)]


__all__ = ["LearningPipelineRunner"]
