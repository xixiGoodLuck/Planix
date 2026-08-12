from __future__ import annotations

from copy import deepcopy
import inspect

from app.learning.assembly import LearningPipelineRequest, LearningPipelineRunner
from app.learning.evidence.coverage import CoverageAggregator
from app.learning.evidence.orchestration import GapCompletionOrchestrator
from app.learning.evidence.transcript import (
    LearningTranscriptRegistrationService,
    LearningTranscriptRepository,
    PersistentTranscriptProvider,
)
from app.learning.runtime import (
    LearningRuntimeConfig,
    LearningRuntimeFactory,
    LearningWaitingEvidenceResult,
    PostgresLearningArtifactRepository,
)
from app.learning.services import LearningPipeline
from app.routers.learning import (
    LearningRunCreateRequest,
    LearningRunManager,
)

from learning_pipeline_fixtures import build_fastapi_learning_pipeline_fixture
from test_learning_final_backend_e2e import (
    CONTROLLED_ROUTING_SRT,
    VIDEO_URL,
    ControlledMetadataAdapter,
    ControlledSemanticAdapter,
    _broad_insufficient_responses,
)
from app.learning.evidence.transcript import MockTranscriptProvider


def test_production_factory_executes_coverage_and_gap_completion(monkeypatch) -> None:
    metadata = ControlledMetadataAdapter()
    transcript_repository = LearningTranscriptRepository()
    LearningTranscriptRegistrationService(
        metadata,
        transcript_repository,
    ).register(
        video_url=VIDEO_URL,
        source_format="srt",
        language="en",
        source_name="production-wiring.srt",
        content=CONTROLLED_ROUTING_SRT,
    )
    semantic = ControlledSemanticAdapter(_broad_insufficient_responses())
    aggregate_calls = 0
    gap_calls = 0
    original_aggregate = CoverageAggregator.aggregate
    original_gap_run = GapCompletionOrchestrator.run

    def aggregate_spy(self, *args, **kwargs):
        nonlocal aggregate_calls
        aggregate_calls += 1
        return original_aggregate(self, *args, **kwargs)

    def gap_spy(self, *args, **kwargs):
        nonlocal gap_calls
        gap_calls += 1
        return original_gap_run(self, *args, **kwargs)

    monkeypatch.setattr(CoverageAggregator, "aggregate", aggregate_spy)
    monkeypatch.setattr(GapCompletionOrchestrator, "run", gap_spy)
    runtime = LearningRuntimeFactory(
        LearningRuntimeConfig(
            video_provider=metadata,
            transcript_provider=PersistentTranscriptProvider(transcript_repository),
            artifact_store="postgres",
            model_provider=semantic,
            environment="production",
            artifact_repository=PostgresLearningArtifactRepository(),
            transcript_repository=transcript_repository,
        )
    ).create()
    scope = LearningRunManager._scope(
        LearningRunCreateRequest.model_validate(
            {
                "goal": "Build a complete persistent FastAPI CRUD API",
                "preferences": {
                    "target_result": "Build a complete persistent FastAPI CRUD API",
                    "resourcePreference": {"userSuppliedUrls": [VIDEO_URL]},
                },
                "constraints": ["Use only the supplied authorized transcript"],
            }
        )
    )

    result = runtime.run(scope)

    assert isinstance(result, LearningWaitingEvidenceResult)
    assert aggregate_calls >= 1
    assert gap_calls == 1
    assert isinstance(runtime.pipeline.coverage_aggregator, CoverageAggregator)
    assert isinstance(runtime.pipeline.gap_orchestrator, GapCompletionOrchestrator)


def test_legacy_runner_delegates_to_the_canonical_pipeline(monkeypatch) -> None:
    fixture = build_fastapi_learning_pipeline_fixture()
    canonical_calls = 0
    original_run = LearningPipeline.run

    def run_spy(self, *args, **kwargs):
        nonlocal canonical_calls
        canonical_calls += 1
        return original_run(self, *args, **kwargs)

    monkeypatch.setattr(LearningPipeline, "run", run_spy)
    runner = LearningPipelineRunner(
        MockTranscriptProvider([]),
        model=fixture.model,
    )
    result = runner.run(
        LearningPipelineRequest(
            scope=fixture.scope,
            providerKey="fixture:canonical-delegation",
        ),
        fixture.provider,
    )
    runner_source = inspect.getsource(LearningPipelineRunner.run)

    assert result.status == "completed"
    assert canonical_calls == 1
    assert "pipeline.run(" in runner_source
    assert "coverage_aggregator.aggregate(" not in runner_source
    assert "gap_orchestrator.run(" not in runner_source
    assert "content_selector.select(" not in runner_source
